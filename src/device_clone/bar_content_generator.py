import hashlib
import math
import secrets
import struct
from enum import Enum
from typing import Any, Dict, Optional


class BarContentType(Enum):
    """Types of BAR content to generate"""

    REGISTERS = "registers"
    BUFFER = "buffer"
    FIRMWARE = "firmware"
    MIXED = "mixed"


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
        """Generate cryptographically secure seed unique to this device"""
        hasher = hashlib.sha256()
        hasher.update(self.device_signature.encode())
        hasher.update(secrets.token_bytes(32))  # Additional entropy
        return hasher.digest()

    def _get_seeded_bytes(self, size: int, context: str = "") -> bytes:
        """Generate deterministic high-entropy bytes for this device (optimized)"""
        if size <= 0:
            raise ValueError("Size must be positive")
        block_size = 32  # SHA-256 digest size
        num_blocks = (size + block_size - 1) // block_size
        out = bytearray(size)
        for block_num in range(num_blocks):
            hasher = hashlib.sha256()
            hasher.update(self.device_seed)
            hasher.update(context.encode())
            hasher.update(struct.pack("<Q", block_num))
            digest = hasher.digest()
            start = block_num * block_size
            end = min(start + block_size, size)
            out[start:end] = digest[: end - start]
        return bytes(out)

    def _generate_register_content(self, size: int, bar_index: int) -> bytes:
        """Generate realistic register space content"""
        content = bytearray(size)
        base_data = self._get_seeded_bytes(size, f"reg_bar{bar_index}")
        content[:] = base_data
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
        return bytes(content)

    def _generate_buffer_content(self, size: int, bar_index: int) -> bytes:
        """Generate high-entropy buffer content (DMA buffers, etc.)"""
        return self._get_seeded_bytes(size, f"buf_bar{bar_index}")

    def _generate_firmware_content(self, size: int, bar_index: int) -> bytes:
        """Generate firmware-like content with headers and realistic structure"""
        content = bytearray(size)
        base_data = self._get_seeded_bytes(size, f"fw_bar{bar_index}")
        content[:] = base_data
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
        return bytes(content)

    def generate_bar_content(
        self,
        size: int,
        bar_index: int,
        content_type: BarContentType = BarContentType.MIXED,
    ) -> bytes:
        """
        Generate BAR memory content
        Args:
            size: Size of BAR in bytes
            bar_index: BAR index (0-5)
            content_type: Type of content to generate
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
        if content_type == BarContentType.REGISTERS:
            return self._generate_register_content(size, bar_index)
        elif content_type == BarContentType.BUFFER:
            return self._generate_buffer_content(size, bar_index)
        elif content_type == BarContentType.FIRMWARE:
            return self._generate_firmware_content(size, bar_index)
        elif content_type == BarContentType.MIXED:
            return self._generate_mixed_content(size, bar_index)
        else:
            raise ValueError(f"Unknown content type: {content_type}")

    def generate_all_bars(self, bar_sizes: Dict[int, int]) -> Dict[int, bytes]:
        """
        Generate content for multiple BARs
        Args:
            bar_sizes: Dict mapping BAR index to size in bytes
        Returns:
            Dict mapping BAR index to content bytes
        """
        result = {}
        for bar_index, size in bar_sizes.items():
            if size <= 4096:
                content_type = BarContentType.REGISTERS
            elif size >= 1024 * 1024:
                content_type = BarContentType.MIXED
            else:
                content_type = BarContentType.BUFFER
            result[bar_index] = self.generate_bar_content(size, bar_index, content_type)
        return result

    def get_entropy_stats(self, data: bytes) -> Dict[str, float]:
        """Calculate entropy statistics for generated content"""
        if not data:
            return {"entropy": 0.0, "uniqueness": 0.0}
        byte_counts = [0] * 256
        for byte in data:
            byte_counts[byte] += 1
        entropy = 0.0
        total = len(data)
        for count in byte_counts:
            if count > 0:
                prob = count / total
                entropy -= prob * math.log2(prob)
        unique_bytes = len(set(data))
        uniqueness = unique_bytes / 256.0
        return {
            "entropy": entropy,
            "uniqueness": uniqueness,
            "size": total,
            "unique_bytes": unique_bytes,
        }


def create_bar_generator(device_signature: Optional[str] = None) -> BarContentGenerator:
    """Factory function to create a BAR content generator

    Args:
        device_signature: Unique identifier for this device instance.
    """
    return BarContentGenerator(device_signature)
