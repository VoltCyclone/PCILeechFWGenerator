import hashlib
import math
import secrets
import struct
import logging
from collections import Counter
from enum import Enum
from typing import TYPE_CHECKING, Dict, Optional

from src.string_utils import log_info_safe, safe_format
from src.exceptions import ConfigSpaceError

from src.log_config import get_logger

if TYPE_CHECKING:
    from src.device_clone.bar_model_loader import BarModel

logger = get_logger(__name__)

# Optional Rich support for visualization
_HAVE_RICH = True
try:
    from rich.console import Console
    from rich.text import Text
except ImportError:
    _HAVE_RICH = False
    Console = None
    Text = None


class BarContentType(Enum):
    """Types of BAR content to generate"""

    REGISTERS = "registers"
    BUFFER = "buffer"
    FIRMWARE = "firmware"
    MIXED = "mixed"
    LEARNED = "learned"  # Learned from MMIO trace


class BarContentGenerator:
    """Generates realistic, high-entropy BAR memory content"""

    def __init__(self, device_signature: Optional[str] = None):
        """
        Initialize with a device signature for deterministic but unique content
        Args:
            device_signature: Unique identifier for this device instance.
                If None, generates a random one.
        """
        self.device_signature = device_signature or secrets.token_hex(16)
        self.device_seed = self._generate_device_seed()

    def _generate_device_seed(self) -> bytes:
        """Generate deterministic seed unique to this device.

        Determinism note:
        - Same device_signature => stable content across runs; do not add
          non-deterministic entropy here.
        - If device_signature isn't provided, __init__ creates a random one,
          ensuring uniqueness without breaking determinism when provided.
        """
        hasher = hashlib.sha256()
        hasher.update(self.device_signature.encode())
        return hasher.digest()

    def _get_seeded_bytes(self, size: int, context: str = "") -> bytes:
        """Generate deterministic high-entropy bytes for this device (fast path).

        Uses SHAKE-256 (XOF) to stream exactly `size` bytes in one call
        instead of per-block SHA-256 hashing. This substantially reduces Python
        overhead for large BARs and is implemented in C.
        """
        if size <= 0:
            raise ValueError("Size must be positive")

        # One-shot XOF generation
        shake = hashlib.shake_256()
        shake.update(self.device_seed)
        if context:
            shake.update(context.encode())
        out = shake.digest(size)

        # Log once per generation (debug-gated to avoid noisy logs at scale)
        if logger.isEnabledFor(logging.DEBUG):
            log_info_safe(
                logger,
                safe_format(
                    "Generated {size} bytes for {context}",
                    size=size,
                    context=context or "<none>",
                ),
                prefix="BARS",
            )
        return out

    def _generate_register_content(self, size: int, bar_index: int) -> bytes:
        """Generate realistic register space content"""
        base_data = self._get_seeded_bytes(size, f"reg_bar{bar_index}")
        content = bytearray(base_data)
        # Overlay realistic register patterns
        for offset in range(0, size, 4):
            if offset + 4 <= size:
                raw_val = struct.unpack("<I", base_data[offset : offset + 4])[0]
                reg_offset = offset % 64
                if reg_offset == 0:  # Control register
                    val = (raw_val & 0xFFFFFFF8) | 0x1  # Enable bit set
                elif reg_offset == 4:  # Status register
                    val = (raw_val & 0xFFFFFF00) | 0x80  # Ready bit
                elif reg_offset == 8:  # ID/Version register
                    val = (raw_val & 0xFFFF0000) | 0x1234  # Fixed ID portion
                elif reg_offset == 12:  # Capabilities register
                    val = (raw_val & 0xFFFFF000) | 0x0A0  # Common cap bits
                elif reg_offset == 16:  # Interrupt register
                    val = raw_val & 0xFFFFFF00  # Usually mostly zero
                elif reg_offset == 20:  # Error register
                    val = raw_val & 0xFFFFFFFE  # Error bits, LSB usually 0
                else:  # Data/general purpose registers
                    val = raw_val
                struct.pack_into("<I", content, offset, val)
                if logger.isEnabledFor(logging.DEBUG):
                    log_info_safe(
                        logger,
                        safe_format(
                            "Reg BAR{bar} 0x{off:04X}: R=0x{raw:08X} V=0x{val:08X}",
                            bar=bar_index,
                            off=offset,
                            raw=raw_val,
                            val=val,
                        ),
                        prefix="BARS",
                    )
        return bytes(content)

    def _generate_buffer_content(self, size: int, bar_index: int) -> bytes:
        """Generate high-entropy buffer content (DMA buffers, etc.)"""
        return self._get_seeded_bytes(size, f"buf_bar{bar_index}")

    def _generate_firmware_content(self, size: int, bar_index: int) -> bytes:
        """Generate firmware-like content with headers and realistic structure"""
        base_data = self._get_seeded_bytes(size, f"fw_bar{bar_index}")
        content = bytearray(base_data)
        # Add firmware header if space allows
        if size >= 32:
            content[0:4] = b"FWIM"
            content[4:8] = struct.pack("<I", 0x00010203)
            content[8:12] = struct.pack("<I", size)
            checksum = sum(base_data[16 : min(1024, size)]) & 0xFFFFFFFF
            content[12:16] = struct.pack("<I", checksum)
            content[16:20] = struct.pack("<I", 0x100)
            content[20:24] = struct.pack("<I", 0x60A12B34)
        section_interval = max(512, size // 16)
        for i in range(64, size, section_interval):
            if i + 12 <= size:
                content[i : i + 4] = b"SECT"
                content[i + 4 : i + 8] = struct.pack("<I", i)
                content[i + 8 : i + 12] = struct.pack(
                    "<I", min(section_interval, size - i)
                )
        if logger.isEnabledFor(logging.DEBUG):
            log_info_safe(
                logger,
                safe_format(
                    "Generated firmware content for BAR{bar} with size {size}",
                    bar=bar_index,
                    size=size,
                ),
                prefix="BARS",
            )
        return bytes(content)

    def _generate_mixed_content(self, size: int, bar_index: int) -> bytes:
        """Generate mixed content (registers + buffers + firmware areas)"""
        content = bytearray(size)
        reg_size = min(4096, size // 4)
        fw_size = min(8192, size // 3)
        buf_size = size - reg_size - fw_size
        offset = 0
        if reg_size > 0:
            reg_content = self._generate_register_content(reg_size, bar_index)
            content[offset : offset + reg_size] = reg_content
            offset += reg_size
        if fw_size > 0:
            fw_content = self._generate_firmware_content(fw_size, bar_index)
            content[offset : offset + fw_size] = fw_content
            offset += fw_size
        if buf_size > 0:
            buf_content = self._generate_buffer_content(buf_size, bar_index)
            content[offset : offset + buf_size] = buf_content
        if logger.isEnabledFor(logging.DEBUG):
            log_info_safe(
                logger,
                safe_format(
                    "Generated mixed content for BAR{bar} with size {size}",
                    bar=bar_index,
                    size=size,
                ),
                prefix="BARS",
            )
        return bytes(content)

    def _generate_from_learned_model(
        self, size: int, bar_index: int, model: "BarModel"
    ) -> bytes:
        """Generate BAR content from learned register model.

        Strategy:
        1. Start with high-entropy base (existing _get_seeded_bytes)
        2. Overlay register reset values at exact offsets
        3. Preserve determinism via device_signature seed

        Args:
            size: BAR size (must match or exceed model.size)
            bar_index: BAR index for logging
            model: Loaded BarModel with register specs

        Returns:
            BAR content bytes with learned registers overlaid

        Raises:
            ValueError: If size < model.size or invalid register specs
        """
        from src.device_clone.bar_model_loader import BarModel

        if not isinstance(model, BarModel):
            raise ValueError("model parameter must be a BarModel instance")

        if size < model.size:
            raise ValueError(
                safe_format(
                    "BAR{bar} size {actual} < model size {expected}",
                    bar=bar_index,
                    actual=size,
                    expected=model.size,
                )
            )

        # Start with high-entropy base
        content = bytearray(
            self._get_seeded_bytes(size, f"learned_bar{bar_index}")
        )

        # Overlay learned register values
        for offset, reg in sorted(model.registers.items()):
            if offset + reg.width > size:
                log_info_safe(
                    logger,
                    safe_format(
                        "Skipping register at 0x{off:X} (exceeds BAR size)",
                        off=offset,
                    ),
                    prefix="BARS",
                )
                continue

            # Write reset value at offset
            if reg.width == 1:
                content[offset] = reg.reset & 0xFF
            elif reg.width == 2:
                struct.pack_into("<H", content, offset, reg.reset & 0xFFFF)
            elif reg.width == 4:
                struct.pack_into("<I", content, offset, reg.reset & 0xFFFFFFFF)
            else:
                log_info_safe(
                    logger,
                    safe_format(
                        "Unsupported register width {width} at 0x{off:X}",
                        width=reg.width,
                        off=offset,
                    ),
                    prefix="BARS",
                )

        log_info_safe(
            logger,
            safe_format(
                "Generated LEARNED content for BAR{bar}: "
                "{nregs} registers overlaid on high-entropy base",
                bar=bar_index,
                nregs=len(model.registers),
            ),
            prefix="BARS",
        )

        return bytes(content)

    def generate_bar_content(
        self,
        size: int,
        bar_index: int,
        content_type: BarContentType = BarContentType.MIXED,
        model: Optional["BarModel"] = None,
    ) -> bytes:
        """
        Generate BAR memory content
        Args:
            size: Size of BAR in bytes
            bar_index: BAR index (0-5)
            content_type: Type of content to generate
            model: Optional BarModel (required for LEARNED type)
        Returns:
            High-entropy BAR content bytes
        Raises:
            ValueError: If parameters are invalid
        """
        if size <= 0:
            raise ValueError("BAR size must be positive")
        if not (0 <= bar_index <= 5):
            raise ValueError("BAR index must be 0-5")
        if size < 32:
            return self._get_seeded_bytes(size, f"small_bar{bar_index}")
        if content_type == BarContentType.LEARNED:
            if model is None:
                raise ValueError("LEARNED content type requires model parameter")
            return self._generate_from_learned_model(size, bar_index, model)
        elif content_type == BarContentType.REGISTERS:
            return self._generate_register_content(size, bar_index)
        elif content_type == BarContentType.BUFFER:
            return self._generate_buffer_content(size, bar_index)
        elif content_type == BarContentType.FIRMWARE:
            return self._generate_firmware_content(size, bar_index)
        elif content_type == BarContentType.MIXED:
            return self._generate_mixed_content(size, bar_index)
        else:
            raise ConfigSpaceError(
                safe_format(
                    "Unknown content type: {content_type}", content_type=content_type
                ), root_cause="Invalid BAR content type"
            )

    def generate_all_bars(
        self, bar_sizes: Dict[int, int], visualize: bool = True
    ) -> Dict[int, bytes]:
        """
        Generate content for multiple BARs
        Args:
            bar_sizes: Dict mapping BAR index to size in bytes
            visualize: If True, log entropy visualizations (default: True)
        Returns:
            Dict mapping BAR index to content bytes
        """
        if not bar_sizes:
            return {}
        
        # Technical header for BAR content generation
        log_info_safe(
            logger,
            safe_format(
                "╔═════════════════════════════════════════════════════════════╗"
            ),
            prefix="BARS",
        )
        log_info_safe(
            logger,
            safe_format(
                "║  BAR CONTENT GENERATION - HIGH ENTROPY SYNTHESIS           ║"
            ),
            prefix="BARS",
        )
        log_info_safe(
            logger,
            safe_format(
                "╠═════════════════════════════════════════════════════════════╣"
            ),
            prefix="BARS",
        )
        
        result = {}
        total_generated = 0
        
        for bar_index, size in bar_sizes.items():
            if size <= 4096:
                content_type = BarContentType.REGISTERS
                type_label = "REG"
            elif size >= 1024 * 1024:
                content_type = BarContentType.MIXED
                type_label = "MIXED"
            else:
                content_type = BarContentType.BUFFER
                type_label = "BUF"
            
            content = self.generate_bar_content(size, bar_index, content_type)
            result[bar_index] = content
            total_generated += size
            
            # Calculate entropy for display
            stats = self.get_entropy_stats(content)
            entropy_pct = (stats["entropy"] / 8.0) * 100
            uniqueness_pct = stats["uniqueness"] * 100
            
            # Format size for display
            size_mb = size / (1024 * 1024)
            if size_mb >= 1:
                size_display = safe_format("{size:.2f} MB", size=size_mb)
            else:
                size_kb = size / 1024
                size_display = safe_format("{size:.2f} KB", size=size_kb)
            
            # Log detailed generation info with stats
            bar_gen_line = safe_format(
                "║ BAR{bar} [{type:>5}] {size:>12} │ "
                "ENT: {ent:>5.1f}% │ UNIQ: {uniq:>5.1f}% ║",
                bar=bar_index,
                type=type_label,
                size=size_display,
                ent=entropy_pct,
                uniq=uniqueness_pct
            )
            log_info_safe(logger, bar_gen_line, prefix="BARS")
            
            # Visualize if requested
            if visualize and logger.isEnabledFor(logging.INFO):
                self._visualize_bar_content(content, bar_index)
        
        # Summary footer
        separator = (
            "╠═════════════════════════════════════════════════════════════╣"
        )
        log_info_safe(logger, safe_format(separator), prefix="BARS")
        
        total_mb = total_generated / (1024 * 1024)
        summary_line = safe_format(
            "║ GENERATED: {count} BAR(s) │ TOTAL SIZE: {total:.2f} MB      ║",
            count=len(result),
            total=total_mb
        )
        log_info_safe(logger, summary_line, prefix="BARS")
        
        footer = (
            "╚═════════════════════════════════════════════════════════════╝"
        )
        log_info_safe(logger, safe_format(footer), prefix="BARS")
        
        return result

    def get_entropy_stats(self, data: bytes) -> Dict[str, float]:
        """Calculate entropy statistics for generated content (optimized)."""
        if not data:
            return {"entropy": 0.0, "uniqueness": 0.0}

        # collections.Counter executes the counting loop in C, faster than Python
        # loops. Avoid building a separate set by deriving unique count from keys.
        counts = Counter(data)
        total = len(data)
        entropy = 0.0
        for count in counts.values():
            prob = count / total
            entropy -= prob * math.log2(prob)

        unique_bytes = len(counts)
        uniqueness = unique_bytes / 256.0
        return {
            "entropy": entropy,
            "uniqueness": uniqueness,
            "size": total,
            "unique_bytes": unique_bytes,
        }

    def _visualize_bar_content(
        self, data: bytes, bar_index: int, max_samples: int = 4
    ) -> None:
        """Visualize BAR content with entropy mini-bars (info logging).
        
        Args:
            data: BAR content bytes
            bar_index: BAR index for labeling
            max_samples: Maximum number of entropy samples to show
        """
        if not data:
            return

        # Calculate overall stats
        stats = self.get_entropy_stats(data)
        size = len(data)
        
        # Log overall stats
        log_info_safe(
            logger,
            safe_format(
                "BAR{bar} ({size} bytes): entropy={entropy:.2f} bits/byte, "
                "unique={uniq}/{total} byte values",
                bar=bar_index,
                size=size,
                entropy=stats["entropy"],
                uniq=stats["unique_bytes"],
                total=256,
            ),
            prefix="BAR_VIZ",
        )

        # Skip detailed visualization for very small BARs
        if size < 1024:
            return

        # Sample entropy at regular intervals
        window_size = min(4096, size // 4)
        step = max(window_size, size // max_samples)
        
        samples = []
        offset = 0
        while offset < size and len(samples) < max_samples:
            end = min(offset + window_size, size)
            chunk = data[offset:end]
            if len(chunk) > 0:
                chunk_entropy = self._calculate_entropy(chunk)
                samples.append((offset, chunk_entropy))
            offset += step

        # Render entropy bars
        if _HAVE_RICH:
            self._render_rich_entropy(samples, bar_index)
        else:
            self._render_ascii_entropy(samples, bar_index)

    def _calculate_entropy(self, data: bytes) -> float:
        """Calculate Shannon entropy for a byte sequence."""
        if not data:
            return 0.0
        counts = Counter(data)
        total = len(data)
        entropy = 0.0
        for count in counts.values():
            prob = count / total
            entropy -= prob * math.log2(prob)
        return entropy

    def _render_rich_entropy(
        self, samples: list, bar_index: int, width: int = 40
    ) -> None:
        """Render entropy visualization using Rich."""
        console = Console()
        for offset, entropy in samples:
            # Color by entropy level
            if entropy >= 7.5:
                style = "bold green"
                prefix = "✓"
            elif entropy >= 6.0:
                style = "bold yellow"
                prefix = "~"
            else:
                style = "bold red"
                prefix = "!"
            
            bar_len = int((entropy / 8.0) * width)
            
            # Use different characters for visual distinction
            if entropy >= 7.5:
                bar_char = "█" * bar_len
            elif entropy >= 6.0:
                bar_char = "▓" * bar_len
            else:
                bar_char = "▒" * bar_len
            
            # Direct console output for color support
            console.print(
                f"  {prefix} 0x{offset:08X}: ",
                style=style,
                end="",
            )
            console.print(bar_char, style=style, end="")
            console.print(f" {entropy:.2f}")

    def _render_ascii_entropy(
        self, samples: list, bar_index: int, width: int = 40
    ) -> None:
        """Render entropy visualization using ASCII with intensity variation."""
        for offset, entropy in samples:
            bar_len = int((entropy / 8.0) * width)
            
            # Use different characters based on entropy level
            if entropy >= 7.5:
                bar_char = "█" * bar_len  # Solid block - high entropy
                prefix = "✓"
            elif entropy >= 6.0:
                bar_char = "▓" * bar_len  # Medium shade - medium entropy
                prefix = "~"
            else:
                bar_char = "░" * bar_len  # Light shade - low entropy
                prefix = "!"
            
            log_info_safe(
                logger,
                safe_format(
                    "  {prefix} 0x{offset:08X}: {bar} {entropy:.2f}",
                    prefix=prefix,
                    offset=offset,
                    bar=bar_char,
                    entropy=entropy,
                ),
                prefix="BAR_VIZ",
            )


def create_BARSerator(
    device_signature: Optional[str] = None,
) -> BarContentGenerator:
    """Factory function to create a BAR content generator

    Args:
        device_signature: Unique identifier for this device instance.
    """
    return BarContentGenerator(device_signature)
