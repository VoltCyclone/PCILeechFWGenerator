#!/usr/bin/env python3
"""
PCILeech Writemask Generator

Generates writemask COE files for PCILeech firmware to control
which configuration space bits are writable vs read-only.

Improvements:
- Fixed extended capability next-pointer handling (DWORD -> byte offset)
- Hardened capability traversal (cycle guards, alignment checks)
- Always writes 1024 DW writemask (full 4KB config space)
- Resilient COE parsing with flexible format support
- Optional terminal visualization with rich

Thanks @Simonrak
"""

import re
from pathlib import Path
from typing import Dict, List, Optional, Set, Tuple

from pcileechfwgenerator.device_clone.coe_word_order import (
    BRAM_ORDER_MARKER,
    bswap32,
    is_bram_order,
)
from pcileechfwgenerator.device_clone.writemask_constants import (
    HEADER_RW_MASK,
    MSI_ADDRESS_HIGH_RW_MASK,
    MSI_ADDRESS_LOW_RW_MASK,
    MSI_CONTROL_RW_MASK,
    MSI_DATA_RW_MASK,
    MSIX_RW_MASK,
    PCIE_DEVCTL2_LTR_RW_MASK,
    PCIE_DEVCTL2_TIMEOUT_DISABLE_RW_MASK,
    PCIE_DEVCTL2_TIMEOUT_RW_MASK,
    PCIE_RW_MASK,
    PM_RW_MASK,
)
from pcileechfwgenerator.exceptions import FileOperationError
from pcileechfwgenerator.log_config import get_logger
from pcileechfwgenerator.pci_capability.constants import (
    EXTENDED_CAPABILITY_NAMES,
)
from pcileechfwgenerator.pci_capability.constants import (
    STANDARD_CAPABILITY_NAMES as CAPABILITY_NAMES,
)
from pcileechfwgenerator.string_utils import (
    log_debug_safe,
    log_error_safe,
    log_info_safe,
    log_warning_safe,
    safe_format,
)

logger = get_logger(__name__)

# Optional rich terminal visualization
_HAVE_RICH = False
try:
    from rich.console import Console
    from rich.panel import Panel
    from rich.table import Table
    from rich.text import Text

    _HAVE_RICH = True
except ImportError:
    Console = Table = Text = Panel = None

# PCIe configuration space is 4KB (1024 DWORDs)
CFG_SPACE_DWORDS = 1024


def _to_coe_word(value: int) -> str:
    """Format a logical PCI DWORD value (config byte +0 in bits [7:0])."""
    return f"{value & 0xFFFFFFFF:08x}"


def _to_bram_word(value: int) -> str:
    """Format a logical DWORD in shadow-BRAM order (byte +0 in bits [31:24]).

    See :mod:`pcileechfwgenerator.device_clone.coe_word_order` for the RTL
    evidence. The cfgspace COE is converted the same way, so both files index
    identical bytes.
    """
    return f"{bswap32(value):08x}"


class WritemaskGenerator:
    """Generator for PCILeech configuration space writemask."""

    def __init__(self) -> None:
        """Initialize the writemask generator."""
        self.logger = get_logger(__name__)

    def _msi_rw_words(self, msi_config: Dict) -> Tuple[int, ...]:
        """Derive MSI grants from layout flags, independently of Enable state."""
        words = [MSI_CONTROL_RW_MASK, MSI_ADDRESS_LOW_RW_MASK]
        if msi_config.get("64bit_capable", False):
            words.append(MSI_ADDRESS_HIGH_RW_MASK)
        words.append(MSI_DATA_RW_MASK)
        if msi_config.get("per_vector_masking", False):
            mmc = min(max(int(msi_config.get("multiple_message_capable", 0)), 0), 5)
            words.extend(((1 << (1 << mmc)) - 1, 0))
        return tuple(words)

    def get_msi_writemask(self, msi_config: Dict) -> Tuple[str, ...]:
        """Return writable-polarity words for MSI layout flags; ignore enabled.

        Flags are 64bit_capable, per_vector_masking, and the integer
        multiple_message_capable encoding (not a message count).
        """
        return tuple(_to_coe_word(word) for word in self._msi_rw_words(msi_config))

    def get_msix_writemask(self, msix_config: Dict) -> Tuple[str, ...]:
        """Return the three writable-polarity MSI-X words, regardless of size."""
        return tuple(_to_coe_word(word) for word in MSIX_RW_MASK)

    def read_cfg_space(self, file_path: Path) -> Dict[int, int]:
        """
        Read configuration space from COE file with flexible parsing.

        Handles various COE formats:
        - Comma or whitespace separated
        - Optional 0x prefixes
        - Mixed case hex
        - Comments and metadata

        Args:
            file_path: Path to COE file

        Returns:
            Dictionary mapping dword index to value (0..1023)
        """
        dword_map: Dict[int, int] = {}
        index = 0
        in_data_section = False

        try:
            content = file_path.read_text()
            swap = is_bram_order(content)
            for raw_line in content.splitlines():
                line = raw_line.strip()

                # Skip comments and empty lines
                if not line or line.startswith(";"):
                    continue

                # Check for data section start
                if "memory_initialization_vector" in line:
                    in_data_section = True
                    continue

                if not in_data_section:
                    continue

                # Split on semicolon to handle inline comments
                line = line.split(";")[0].strip()
                if not line:
                    continue

                # Extract hex tokens (comma or whitespace separated)
                tokens = re.split(r"[,\s]+", line)
                for token in tokens:
                    if not token:
                        continue

                    # Strip optional 0x prefix
                    if token.lower().startswith("0x"):
                        token = token[2:]

                    # Validate hex format (1-8 digits)
                    if not re.fullmatch(r"[0-9a-fA-F]{1,8}", token):
                        continue

                    if index >= CFG_SPACE_DWORDS:
                        break

                    value = int(token, 16)
                    dword_map[index] = bswap32(value) if swap else value
                    index += 1

                if index >= CFG_SPACE_DWORDS:
                    break

        except Exception as e:
            log_error_safe(
                self.logger,
                safe_format("Failed to read configuration space: {error}", error=e),
                prefix="WRITEMASK",
            )
            raise FileOperationError(
                f"Failed to read configuration space from {file_path}: {e}"
            ) from e

        log_debug_safe(
            self.logger,
            safe_format(
                "Read {count} dwords from {path}",
                count=len(dword_map),
                path=file_path.name,
            ),
            prefix="WRITEMASK",
        )

        return dword_map

    def _get_dword(self, dwords: Dict[int, int], byte_offset: int) -> int:
        """
        Safely get DWORD from config space at byte offset.

        Args:
            dwords: Configuration space dword map
            byte_offset: Byte offset into config space

        Returns:
            DWORD value or 0 if invalid
        """
        idx = (byte_offset // 4) & (CFG_SPACE_DWORDS - 1)
        return dwords.get(idx, 0)

    def locate_capabilities(self, dword_map: Dict[int, int]) -> Dict[str, int]:
        """
        Locate PCI capabilities with hardened traversal.

        Improvements:
        - Cycle detection for both standard and extended caps
        - Proper extended capability pointer handling (DWORD -> byte)
        - Alignment and range validation
        - Detailed debug logging

        Args:
            dword_map: Configuration space dword map

        Returns:
            Dictionary mapping capability ID (as hex string) to byte offset
        """
        capabilities: Dict[str, int] = {}

        # ---- Standard capabilities (byte-linked list) ----
        cap_ptr = dword_map.get(0x34 // 4, 0) & 0xFC  # low 2 bits reserved
        seen_std: Set[int] = set()

        while cap_ptr and 0x40 <= cap_ptr < 0x100:
            # Cycle detection
            if cap_ptr in seen_std:
                log_error_safe(
                    self.logger,
                    safe_format(
                        "Standard capability cycle detected at 0x{ptr:02X}",
                        ptr=cap_ptr,
                    ),
                    prefix="WRITEMASK",
                )
                break
            seen_std.add(cap_ptr)

            # PCIe spec requires capability structures to be DWORD-aligned.
            # A misaligned cap_ptr means the config space is malformed; the
            # byte-shift below would silently extract the wrong bytes.
            if cap_ptr & 0x3:
                log_warning_safe(
                    self.logger,
                    safe_format(
                        "Misaligned standard capability pointer 0x{ptr:02X}; "
                        "stopping walk",
                        ptr=cap_ptr,
                    ),
                    prefix="WRITEMASK",
                )
                break

            # Read capability header
            dword = self._get_dword(dword_map, cap_ptr)
            byte_shift = (cap_ptr & 0x3) * 8
            cap_id = (dword >> byte_shift) & 0xFF
            next_ptr = (dword >> (byte_shift + 8)) & 0xFC
            if cap_id == 0xFF:  # all-ones / absent function (matches Linux)
                break

            cap_name = CAPABILITY_NAMES.get(cap_id, f"Unknown (0x{cap_id:02X})")
            log_debug_safe(
                self.logger,
                safe_format(
                    "Std cap @0x{ptr:02X}: id=0x{id:02X} ({name})",
                    ptr=cap_ptr,
                    id=cap_id,
                    name=cap_name,
                ),
                prefix="WRITEMASK",
            )

            capabilities[f"0x{cap_id:02X}"] = cap_ptr

            # Validate next pointer
            if next_ptr == cap_ptr or (next_ptr and next_ptr < 0x40):
                log_warning_safe(
                    self.logger,
                    safe_format(
                        "Invalid next std cap pointer 0x{ptr:02X}", ptr=next_ptr
                    ),
                    prefix="WRITEMASK",
                )
                break

            cap_ptr = next_ptr

        # ---- Extended capabilities (DWORD offset in header) ----
        ext_offset = 0x100
        seen_ext: Set[int] = set()

        while ext_offset and 0x100 <= ext_offset < 0x1000:
            # Cycle detection
            if ext_offset in seen_ext:
                log_error_safe(
                    self.logger,
                    safe_format(
                        "Extended capability cycle detected at 0x{off:03X}",
                        off=ext_offset,
                    ),
                    prefix="WRITEMASK",
                )
                break
            seen_ext.add(ext_offset)

            # Read extended capability header
            dword0 = self._get_dword(dword_map, ext_offset)
            ext_id = dword0 & 0xFFFF

            # Check for termination
            if ext_id in (0x0000, 0xFFFF):
                break

            ext_ver = (dword0 >> 16) & 0xF
            next_dword_offset = (dword0 >> 20) & 0xFFC

            cap_name = EXTENDED_CAPABILITY_NAMES.get(
                ext_id, f"Unknown (0x{ext_id:04X})"
            )
            log_debug_safe(
                self.logger,
                safe_format(
                    "Ext cap @0x{off:03X}: id=0x{id:04X} v{ver} ({name})",
                    off=ext_offset,
                    id=ext_id,
                    ver=ext_ver,
                    name=cap_name,
                ),
                prefix="WRITEMASK",
            )

            capabilities[f"0x{ext_id:04X}"] = ext_offset

            # Next pointer is already a byte offset per PCIe spec
            # (bits [31:20] of extended capability header encode byte offset)
            ext_offset = next_dword_offset if next_dword_offset else 0

        log_info_safe(
            self.logger,
            safe_format(
                "Found {count} capabilities ({std} std, {ext} ext)",
                count=len(capabilities),
                std=len(seen_std),
                ext=len(seen_ext),
            ),
            prefix="WRITEMASK",
        )

        return capabilities

    def build_writemask(self, dword_map: Dict[int, int]) -> List[int]:
        """Build 1024 logical DWORD masks without file IO or mutating the image.

        Start read-only and explicitly grant supported Type-0 header and
        standard-capability writes. Layout comes only from the final cfg image.
        Extended space remains read-only because shadow RAM lacks RW1C support.
        """
        mask = [0] * CFG_SPACE_DWORDS
        mask[: len(HEADER_RW_MASK)] = HEADER_RW_MASK

        slot = 0
        while slot < 6:
            index = 4 + slot
            word = dword_map.get(index, 0)
            if word & 1:
                mask[index] = word & 0xFFFFFFFC
                mask[1] |= 1  # IO Space Enable only for a served IO BAR
            elif (word >> 1) & 3 == 2:
                if slot == 5:
                    log_warning_safe(
                        self.logger,
                        "64-bit BAR in slot 5 has no upper slot; leaving read-only",
                        prefix="WRITEMASK",
                    )
                else:
                    mask[index] = word & 0xFFFFFFF0
                    mask[index + 1] = dword_map.get(index + 1, 0) & 0xFFFFFFFF
                    slot += 1
            elif (word >> 1) & 3 == 0:
                mask[index] = word & 0xFFFFFFF0
            # Reserved memory BAR types remain read-only.
            slot += 1

        rom = dword_map.get(0x30 // 4, 0)
        mask[0x30 // 4] = (rom & 0xFFFFF800) | 1 if rom else 0

        capabilities = self.locate_capabilities(dword_map)
        std_offsets = sorted(o for o in capabilities.values() if o < 0x100)
        for cap_id, offset in capabilities.items():
            if offset >= 0x100:
                continue
            start = offset // 4
            # A capability's grants never spill into the next structure.
            limit = next((o // 4 for o in std_offsets if o > offset), 0x100 // 4)
            words: Tuple[int, ...] = ()
            if cap_id == "0x01":
                words = PM_RW_MASK
            elif cap_id == "0x05":
                control = dword_map.get(start, 0) >> 16
                words = self._msi_rw_words(
                    {
                        "64bit_capable": bool(control & 0x80),
                        "per_vector_masking": bool(control & 0x100),
                        "multiple_message_capable": (control >> 1) & 7,
                    }
                )
            elif cap_id == "0x11":
                words = MSIX_RW_MASK
            elif cap_id == "0x10":
                pcie_words = list(PCIE_RW_MASK)
                version = (dword_map.get(start, 0) >> 16) & 0xF
                devcap2_index = start + 9
                devcap2 = dword_map.get(devcap2_index, 0) if devcap2_index < 64 else 0
                if version < 2:
                    # v1 ends at Root Status (+0x20); no *2 registers exist.
                    pcie_words = pcie_words[:9]
                    devcap2 = 0
                if devcap2 & 0xF:
                    pcie_words[10] |= PCIE_DEVCTL2_TIMEOUT_RW_MASK
                if devcap2 & 0x10:
                    pcie_words[10] |= PCIE_DEVCTL2_TIMEOUT_DISABLE_RW_MASK
                if devcap2 & 0x800:
                    pcie_words[10] |= PCIE_DEVCTL2_LTR_RW_MASK
                words = tuple(pcie_words)
            end = min(start + len(words), limit)
            mask[start:end] = words[: end - start]

        return mask

    def render_writemask_coe(self, dword_map: Dict[int, int]) -> str:
        """Render the final-image policy as COE text without temporary files."""
        return self._format_writemask_coe(self.build_writemask(dword_map))

    def generate_writemask(
        self,
        cfg_space_path: Path,
        output_path: Path,
        device_config: Optional[Dict] = None,
        visualize: bool = False,
        visualize_rows: int = 64,
    ) -> None:
        """Generate the mask from the final configuration COE image.

        device_config is accepted for compatibility but never determines layout.
        """
        log_info_safe(
            self.logger,
            safe_format("Generating writemask from {path}", path=cfg_space_path),
            prefix="WRITEMASK",
        )
        cfg_space = self.read_cfg_space(cfg_space_path)
        wr_mask = self.build_writemask(cfg_space)
        self._write_writemask_coe(wr_mask, output_path)
        log_info_safe(
            self.logger,
            safe_format("Writemask generated: {path}", path=output_path.name),
            prefix="WRITEMASK",
        )
        if visualize:
            visualize_writemask_terminal(
                [_to_coe_word(word) for word in wr_mask],
                self.locate_capabilities(cfg_space),
                rows=visualize_rows,
            )

    def _format_writemask_coe(self, wr_mask: List[int]) -> str:
        """Format exactly 1024 words, padding read-only or truncating as needed."""
        words = wr_mask[:CFG_SPACE_DWORDS]
        words = words + [0] * (CFG_SPACE_DWORDS - len(words))
        lines = [
            "; PCILeech Configuration Space Writemask",
            "; Generated by PCILeech Firmware Generator",
            ";",
            "; Controls which configuration space bits are writable",
            "; 1 = writable, 0 = read-only; words are in shadow-BRAM order",
            "; (config byte +0 in bits [31:24]), same as the cfgspace COE",
            ";",
            BRAM_ORDER_MARKER,
            "memory_initialization_radix=16;",
            "memory_initialization_vector=",
        ]
        for i in range(0, CFG_SPACE_DWORDS, 4):
            suffix = "," if i + 4 < CFG_SPACE_DWORDS else ";"
            lines.append(
                ",".join(_to_bram_word(word) for word in words[i : i + 4]) + suffix
            )
        return "\n".join(lines) + "\n"

    def _write_writemask_coe(self, wr_mask: List[int], output_path: Path) -> None:
        """Write integer masks with the shared logical DWORD serializer."""
        output_path.write_text(self._format_writemask_coe(wr_mask), encoding="utf-8")


# ============================================================================
# Terminal Visualization (Optional)
# ============================================================================


def _get_console():
    """Get rich Console if available."""
    if not _HAVE_RICH:
        return None
    return Console(force_terminal=True)


def _bitline(mask_hex: str) -> str:
    """Convert hex mask to 32-char bitstring (MSB..LSB)."""
    try:
        value = int(mask_hex, 16)
        return "".join("1" if (value & (1 << (31 - i))) else "0" for i in range(32))
    except ValueError:
        return "0" * 32


def _ascii_bits(mask_hex: str) -> str:
    """Convert hex mask to ASCII bitstring grouped by 4."""
    bits = _bitline(mask_hex)
    return " ".join(bits[i : i + 4] for i in range(0, 32, 4))


def visualize_writemask_terminal(
    wr_mask: List[str],
    caps: Dict[str, int],
    rows: int = 64,
) -> None:
    """
    Display writemask and capabilities in terminal.

    Uses rich library if available, falls back to plain text.

    Args:
        wr_mask: Writemask data
        caps: Capabilities dictionary (id -> offset)
        rows: Number of rows to display
    """
    console = _get_console()

    if console:
        # Rich terminal output
        _visualize_rich(console, wr_mask, caps, rows)
    else:
        # Plain text fallback
        _visualize_plain(wr_mask, caps, rows)


def _visualize_rich(
    console,
    wr_mask: List[str],
    caps: Dict[str, int],
    rows: int,
) -> None:
    """Rich terminal visualization."""
    # Capability table
    table = Table(title="Capabilities", show_lines=False)
    table.add_column("ID", style="cyan")
    table.add_column("Name", style="white")
    table.add_column("Offset", style="yellow")

    for cap_id, offset in sorted(caps.items(), key=lambda x: x[1]):
        try:
            cap_id_int = int(cap_id, 16)
            name = CAPABILITY_NAMES.get(
                cap_id_int, EXTENDED_CAPABILITY_NAMES.get(cap_id_int, "")
            )
        except ValueError:
            name = ""
        table.add_row(cap_id, name or "", f"0x{offset:03X}")

    if len(table.rows) > 0:
        console.print(table)
    else:
        console.print(Panel(Text("No capabilities detected", style="dim")))

    # Writemask bits
    console.rule("[bold]Writemask (1=writable, 0=read-only)")
    show_rows = min(rows, len(wr_mask))

    for i in range(show_rows):
        bits = _bitline(wr_mask[i])
        styled = Text()
        for j, ch in enumerate(bits):
            style = "bold green" if ch == "1" else "dim white"
            styled.append(ch, style=style)
            if (j + 1) % 4 == 0:
                styled.append(" ", style="dim")
        console.print(f"{i:04d}: ", styled)

    # Distribution statistics
    ones = sum(bin(int(mask, 16)).count("1") for mask in wr_mask)
    zeros = CFG_SPACE_DWORDS * 32 - ones

    console.rule("[bold]Mask Distribution")
    console.print(f"[green]Writable bits:[/green] {ones}")
    console.print(f"[red]Read-only bits:[/red] {zeros}")
    console.print(
        f"[yellow]Writable percentage:[/yellow] {ones / (ones + zeros) * 100:.1f}%"
    )


def _visualize_plain(
    wr_mask: List[str],
    caps: Dict[str, int],
    rows: int,
) -> None:
    """Plain text fallback visualization."""
    print("=" * 80)
    print("CAPABILITIES")
    print("=" * 80)

    for cap_id, offset in sorted(caps.items(), key=lambda x: x[1]):
        try:
            cap_id_int = int(cap_id, 16)
            name = CAPABILITY_NAMES.get(
                cap_id_int, EXTENDED_CAPABILITY_NAMES.get(cap_id_int, "")
            )
        except ValueError:
            name = ""
        print(f"{cap_id:>8} @ 0x{offset:03X}  {name or ''}")

    print("\n" + "=" * 80)
    print(f"WRITEMASK (first {rows} dwords, 1=writable 0=read-only)")
    print("=" * 80)

    for i in range(min(rows, len(wr_mask))):
        print(f"{i:04d}: {_ascii_bits(wr_mask[i])}")

    ones = sum(bin(int(mask, 16)).count("1") for mask in wr_mask)
    zeros = CFG_SPACE_DWORDS * 32 - ones

    print("\n" + "=" * 80)
    print("DISTRIBUTION")
    print("=" * 80)
    print(f"Writable bits:   {ones}")
    print(f"Read-only bits:  {zeros}")
    print(f"Writable %:      {ones / (ones + zeros) * 100:.1f}%")
