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

import logging
import re
from pathlib import Path
from typing import Dict, List, Optional, Set, Tuple

from src.exceptions import FileOperationError

from src.string_utils import (
    log_debug_safe,
    log_error_safe,
    log_info_safe,
    log_warning_safe,
    safe_format,
)
from src.pci_capability.constants import (
    STANDARD_CAPABILITY_NAMES as CAPABILITY_NAMES,
    EXTENDED_CAPABILITY_NAMES,
)
from src.device_clone.constants import (
    FIXED_SECTION,
    WRITEMASK_DICT,
    WRITE_PROTECTED_BITS_MSI_64_BIT_1,
    WRITE_PROTECTED_BITS_MSI_ENABLED_0,
    WRITE_PROTECTED_BITS_MSI_MULTIPLE_MESSAGE_CAPABLE_1,
    WRITE_PROTECTED_BITS_MSI_MULTIPLE_MESSAGE_ENABLED_1,
    WRITE_PROTECTED_BITS_MSIX_3,
    WRITE_PROTECTED_BITS_MSIX_4,
    WRITE_PROTECTED_BITS_MSIX_5,
    WRITE_PROTECTED_BITS_MSIX_6,
    WRITE_PROTECTED_BITS_MSIX_7,
    WRITE_PROTECTED_BITS_MSIX_8,
)

logger = logging.getLogger(__name__)

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


class WritemaskGenerator:
    """Generator for PCILeech configuration space writemask."""

    def __init__(self):
        """Initialize the writemask generator."""
        self.logger = logging.getLogger(__name__)

    def get_msi_writemask(self, msi_config: Dict) -> Optional[Tuple[str, ...]]:
        """
        Get appropriate MSI writemask based on configuration.

        Args:
            msi_config: MSI configuration dictionary

        Returns:
            Tuple of writemask strings or None
        """
        if not msi_config.get("enabled", False):
            return WRITE_PROTECTED_BITS_MSI_ENABLED_0

        if msi_config.get("64bit_capable", False):
            return WRITE_PROTECTED_BITS_MSI_64_BIT_1

        if msi_config.get("multiple_message_capable", False):
            return WRITE_PROTECTED_BITS_MSI_MULTIPLE_MESSAGE_CAPABLE_1

        if msi_config.get("multiple_message_enabled", False):
            return WRITE_PROTECTED_BITS_MSI_MULTIPLE_MESSAGE_ENABLED_1

        return WRITE_PROTECTED_BITS_MSI_ENABLED_0

    def get_msix_writemask(self, msix_config: Dict) -> Optional[Tuple[str, ...]]:
        """
        Get appropriate MSI-X writemask based on configuration.

        Args:
            msix_config: MSI-X configuration dictionary

        Returns:
            Tuple of writemask strings or None
        """
        table_size = msix_config.get("table_size", 0)

        # Map table size to capability length
        if table_size <= 8:
            return WRITE_PROTECTED_BITS_MSIX_3
        elif table_size <= 16:
            return WRITE_PROTECTED_BITS_MSIX_4
        elif table_size <= 32:
            return WRITE_PROTECTED_BITS_MSIX_5
        elif table_size <= 64:
            return WRITE_PROTECTED_BITS_MSIX_6
        elif table_size <= 128:
            return WRITE_PROTECTED_BITS_MSIX_7
        else:
            return WRITE_PROTECTED_BITS_MSIX_8

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

                    dword_map[index] = int(token, 16)
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
            safe_format("Read {count} dwords from {path}", 
                       count=len(dword_map), path=file_path.name),
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
        cap_ptr = dword_map.get(0x34 // 4, 0) & 0xFF
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

            # Read capability header
            dword = self._get_dword(dword_map, cap_ptr)
            byte_shift = (cap_ptr & 0x3) * 8
            cap_id = (dword >> byte_shift) & 0xFF
            next_ptr = (dword >> (byte_shift + 8)) & 0xFF

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
            next_dword_offset = (dword0 >> 20) & 0xFFF

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

            # Convert DWORD offset to byte offset (spec uses DWORD granularity)
            ext_offset = (next_dword_offset << 2) if next_dword_offset else 0

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

    def create_writemask(self, dwords: Dict[int, int]) -> List[str]:
        """
        Create initial writemask with all bits writable.

        Always creates full 1024 DWORD writemask for complete 4KB config space.

        Args:
            dwords: Configuration space dword map (unused, kept for compatibility)

        Returns:
            List of 1024 writemask strings (all "ffffffff")
        """
        return ["ffffffff"] * CFG_SPACE_DWORDS

    def update_writemask(
        self, wr_mask: List[str], protected_bits: Tuple[str, ...], start_index: int
    ) -> List[str]:
        """
        Update writemask by marking bits as read-only.

        Protected bits are cleared (0 = read-only, 1 = writable).
        Handles invalid indices and hex conversion gracefully.

        Args:
            wr_mask: Current writemask list
            protected_bits: Tuple of protected bit masks (hex strings)
            start_index: Starting dword index in writemask

        Returns:
            Updated writemask list
        """
        end_index = min(start_index + len(protected_bits), len(wr_mask))

        for i, mask in enumerate(protected_bits):
            idx = start_index + i
            if idx >= end_index:
                break

            try:
                current = int(wr_mask[idx], 16)
                protected = int(mask, 16)
                # Clear protected bits (bitwise AND with complement)
                new_mask = current & ~protected
                wr_mask[idx] = f"{new_mask:08x}"
            except (ValueError, IndexError) as e:
                log_warning_safe(
                    self.logger,
                    safe_format(
                        "Failed to update writemask at index {idx}: {err}",
                        idx=idx,
                        err=e,
                    ),
                    prefix="WRITEMASK",
                )
                continue

        return wr_mask

    def generate_writemask(
        self,
        cfg_space_path: Path,
        output_path: Path,
        device_config: Optional[Dict] = None,
        visualize: bool = False,
        visualize_rows: int = 64,
    ) -> None:
        """
        Generate writemask COE file from configuration space.

        Args:
            cfg_space_path: Path to configuration space COE file
            output_path: Path for output writemask COE file
            device_config: Optional device configuration for MSI/MSI-X
            visualize: If True, display terminal visualization (requires rich)
            visualize_rows: Number of rows to display in visualization
        """
        log_info_safe(
            self.logger,
            safe_format("Generating writemask from {path}", path=cfg_space_path),
            prefix="WRITEMASK",
        )

        # Read configuration space
        cfg_space = self.read_cfg_space(cfg_space_path)

        # Locate capabilities
        capabilities = self.locate_capabilities(cfg_space)

        # Create initial writemask (all writable)
        wr_mask = self.create_writemask(cfg_space)

        # Apply fixed section protection (vendor ID, device ID, etc.)
        wr_mask = self.update_writemask(wr_mask, FIXED_SECTION, 0)

        # Apply capability-specific protections
        for cap_id, cap_offset in capabilities.items():
            cap_start_index = cap_offset // 4

            # Handle MSI capability (0x05)
            if cap_id == "0x05":
                msi_config = (
                    device_config.get("msi_config", {}) if device_config else {}
                )
                protected_bits = self.get_msi_writemask(msi_config)
                if protected_bits:
                    wr_mask = self.update_writemask(
                        wr_mask, protected_bits, cap_start_index
                    )

            # Handle MSI-X capability (0x11)
            elif cap_id == "0x11":
                msix_config = (
                    device_config.get("msix_config", {}) if device_config else {}
                )
                protected_bits = self.get_msix_writemask(msix_config)
                if protected_bits:
                    wr_mask = self.update_writemask(
                        wr_mask, protected_bits, cap_start_index
                    )

            # Handle other capabilities from WRITEMASK_DICT
            else:
                protected_bits = WRITEMASK_DICT.get(cap_id)
                if protected_bits:
                    wr_mask = self.update_writemask(
                        wr_mask, protected_bits, cap_start_index
                    )

        # Write output COE file
        self._write_writemask_coe(wr_mask, output_path)

        log_info_safe(
            self.logger,
            safe_format(
                "Writemask generated: {path}", path=output_path.name
            ),
            prefix="WRITEMASK",
        )

        # Optional visualization
        if visualize:
            visualize_writemask_terminal(
                wr_mask, capabilities, rows=visualize_rows
            )

    def _write_writemask_coe(self, wr_mask: List[str], output_path: Path) -> None:
        """
        Write writemask to COE file with exactly 1024 DWORDs.

        Pads or truncates to ensure consistent 4KB config space size.

        Args:
            wr_mask: Writemask data
            output_path: Output file path
        """
        # Ensure exactly 1024 DWORDs
        if len(wr_mask) < CFG_SPACE_DWORDS:
            wr_mask = wr_mask + ["ffffffff"] * (CFG_SPACE_DWORDS - len(wr_mask))
        elif len(wr_mask) > CFG_SPACE_DWORDS:
            wr_mask = wr_mask[:CFG_SPACE_DWORDS]

        with output_path.open("w") as f:
            # Write header
            f.write("; PCILeech Configuration Space Writemask\n")
            f.write("; Generated by PCILeech Firmware Generator\n")
            f.write(";\n")
            f.write("; Controls which configuration space bits are writable\n")
            f.write("; 0 = read-only, 1 = writable\n")
            f.write(";\n")
            f.write("memory_initialization_radix=16;\n")
            f.write("memory_initialization_vector=\n")

            # Write data in groups of 4 dwords per line
            for i in range(0, CFG_SPACE_DWORDS, 4):
                line_data = wr_mask[i : i + 4]
                f.write(",".join(line_data))

                # Add comma except for last line
                if i + 4 < CFG_SPACE_DWORDS:
                    f.write(",\n")
                else:
                    f.write(";\n")


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
