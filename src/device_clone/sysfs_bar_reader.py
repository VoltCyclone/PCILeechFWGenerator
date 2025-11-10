#!/usr/bin/env python3
"""Safe, read-only BAR memory access via sysfs.

Provides direct access to donor device BARs using Linux sysfs interface
(/sys/bus/pci/devices/<BDF>/resourceN) with mmap for efficient reading.
This module complements VFIO-based access for simpler, read-only operations.
"""

import mmap
import os
import struct

from dataclasses import dataclass
from pathlib import Path
from typing import Dict, List, Optional

from src.log_config import get_logger

from src.string_utils import (
    log_debug_safe,
    log_error_safe,
    log_info_safe,
    log_warning_safe,
    safe_format,
)

logger = get_logger(__name__)


@dataclass
class BarInfo:
    """BAR metadata from sysfs resource file."""

    index: int
    start: int  # Physical address
    end: int  # Physical address
    size: int  # Size in bytes
    flags: int  # Resource flags
    is_io: bool  # True if I/O BAR, False if MMIO
    is_64bit: bool  # True if 64-bit BAR


class SysfsBarReader:
    """Read-only BAR access via sysfs resourceN files."""

    def __init__(self, device_bdf: str):
        """Initialize BAR reader for a specific device.

        Args:
            device_bdf: PCI device BDF (e.g., "0000:03:00.0")

        Raises:
            FileNotFoundError: If device not found in sysfs
        """
        self.device_bdf = device_bdf
        self.sysfs_path = Path(f"/sys/bus/pci/devices/{device_bdf}")

        if not self.sysfs_path.exists():
            raise FileNotFoundError(
                safe_format(
                    "Device {bdf} not found in sysfs at {path}",
                    bdf=device_bdf,
                    path=self.sysfs_path,
                )
            )

        log_debug_safe(
            logger,
            safe_format("Initialized sysfs BAR reader for {bdf}", bdf=device_bdf),
            prefix="SYSFS_BAR",
        )

    def get_bar_info(self, bar_index: int) -> Optional[BarInfo]:
        """Get BAR metadata from sysfs resource file.

        Args:
            bar_index: BAR index (0-5)

        Returns:
            BarInfo if BAR exists and has non-zero size, None otherwise
        """
        resource_file = self.sysfs_path / "resource"
        if not resource_file.exists():
            log_error_safe(
                logger,
                safe_format(
                    "Resource file not found: {path}", path=resource_file
                ),
            )
            return None

        try:
            with open(resource_file, "r") as f:
                lines = f.readlines()

            if bar_index >= len(lines):
                log_debug_safe(
                    logger,
                    safe_format(
                        "BAR{idx} index out of range (only {n} BARs)",
                        idx=bar_index,
                        n=len(lines),
                    ),
                    prefix="SYSFS_BAR",
                )
                return None

            line = lines[bar_index].strip()
            if not line:
                return None

            parts = line.split()
            if len(parts) < 3:
                log_warning_safe(
                    logger,
                    safe_format(
                        "Invalid resource line for BAR{idx}: {line}",
                        idx=bar_index,
                        line=line,
                    ),
                    prefix="SYSFS_BAR",
                )
                return None

            start = int(parts[0], 16)
            end = int(parts[1], 16)
            flags = int(parts[2], 16)

            # BAR not present if start and end are both zero
            if start == 0 and end == 0:
                log_debug_safe(
                    logger,
                    safe_format("BAR{idx} not present", idx=bar_index),
                    prefix="SYSFS_BAR",
                )
                return None

            size = end - start + 1 if end >= start else 0
            is_io = bool(flags & 0x1)  # Bit 0 indicates I/O space
            is_64bit = not is_io and ((flags >> 1) & 0x3) == 0x2

            log_debug_safe(
                logger,
                safe_format(
                    "BAR{idx}: start=0x{start:X}, end=0x{end:X}, "
                    "size=0x{size:X}, flags=0x{flags:X}, io={is_io}, 64bit={is_64}",
                    idx=bar_index,
                    start=start,
                    end=end,
                    size=size,
                    flags=flags,
                    is_io=is_io,
                    is_64=is_64bit,
                ),
                prefix="SYSFS_BAR",
            )

            return BarInfo(
                index=bar_index,
                start=start,
                end=end,
                size=size,
                flags=flags,
                is_io=is_io,
                is_64bit=is_64bit,
            )

        except (IOError, ValueError) as e:
            log_error_safe(
                logger,
                safe_format(
                    "Failed to read BAR{idx} info: {err}",
                    idx=bar_index,
                    err=str(e),
                ),
            )
            return None

    def list_bars(self) -> List[BarInfo]:
        """Get info for all present BARs.

        Returns:
            List of BarInfo for all present (non-zero size) BARs
        """
        bars = []
        for idx in range(6):  # Standard PCI devices have up to 6 BARs
            bar_info = self.get_bar_info(idx)
            if bar_info and bar_info.size > 0:
                bars.append(bar_info)
        return bars

    def enable_memory_decoding(self) -> bool:
        """Enable memory decoding for the device.

        Writes '1' to sysfs enable file to ensure device can decode MMIO.
        This is safe to call multiple times.

        Returns:
            True if successful, False otherwise
        """
        enable_file = self.sysfs_path / "enable"
        if not enable_file.exists():
            log_warning_safe(
                logger,
                "Enable file not found; device may not support enable control",
                prefix="SYSFS_BAR",
            )
            return False

        try:
            with open(enable_file, "w") as f:
                f.write("1")
            log_debug_safe(
                logger,
                safe_format(
                    "Enabled memory decoding for {bdf}", bdf=self.device_bdf
                ),
                prefix="SYSFS_BAR",
            )
            return True
        except (IOError, PermissionError) as e:
            log_warning_safe(
                logger,
                safe_format(
                    "Failed to enable memory decoding: {err}", err=str(e)
                ),
                prefix="SYSFS_BAR",
            )
            return False

    def read_bar_bytes(
        self, bar_index: int, offset: int = 0, length: Optional[int] = None
    ) -> Optional[bytes]:
        """Read bytes from BAR using mmap (read-only, safe).

        Args:
            bar_index: BAR index (0-5)
            offset: Offset within BAR to start reading (default: 0)
            length: Number of bytes to read (default: entire BAR)

        Returns:
            Bytes read from BAR, or None on failure

        Raises:
            PermissionError: If insufficient privileges
            ValueError: If parameters are invalid
        """
        bar_info = self.get_bar_info(bar_index)
        if not bar_info or bar_info.size == 0:
            log_warning_safe(
                logger,
                safe_format("BAR{idx} not present or empty", idx=bar_index),
                prefix="SYSFS_BAR",
            )
            return None

        if bar_info.is_io:
            log_warning_safe(
                logger,
                safe_format(
                    "BAR{idx} is I/O space (not MMIO); use port I/O instead",
                    idx=bar_index,
                ),
                prefix="SYSFS_BAR",
            )
            return None

        # Validate parameters
        if offset < 0 or offset >= bar_info.size:
            raise ValueError(
                safe_format(
                    "Offset 0x{off:X} out of range for BAR{idx} (size=0x{size:X})",
                    off=offset,
                    idx=bar_index,
                    size=bar_info.size,
                )
            )

        if length is None:
            length = bar_info.size - offset
        elif length <= 0 or offset + length > bar_info.size:
            raise ValueError(
                safe_format(
                    "Length {len} at offset 0x{off:X} exceeds "
                    "BAR{idx} size 0x{size:X}",
                    len=length,
                    off=offset,
                    idx=bar_index,
                    size=bar_info.size,
                )
            )

        # Ensure memory decoding is enabled
        self.enable_memory_decoding()

        resource_file = self.sysfs_path / f"resource{bar_index}"
        if not resource_file.exists():
            log_error_safe(
                logger,
                safe_format(
                    "Resource file not found: {path}", path=resource_file
                ),
            )
            return None

        try:
            # Open resourceN file
            fd = os.open(resource_file, os.O_RDONLY)
            try:
                # Map the BAR (read-only)
                with mmap.mmap(
                    fd,
                    length=bar_info.size,
                    flags=mmap.MAP_SHARED,
                    prot=mmap.PROT_READ,
                ) as mm:
                    # Read requested bytes
                    data = mm[offset : offset + length]

                    log_info_safe(
                        logger,
                        safe_format(
                            "Read {len} bytes from BAR{idx} at offset 0x{off:X}",
                            len=len(data),
                            idx=bar_index,
                            off=offset,
                        ),
                        prefix="SYSFS_BAR",
                    )
                    return bytes(data)

            finally:
                os.close(fd)

        except PermissionError:
            log_error_safe(
                logger, "BAR read requires root privileges (for mmap)"
            )
            raise
        except Exception as e:
            log_error_safe(
                logger,
                safe_format(
                    "Failed to read BAR{idx}: {err}",
                    idx=bar_index,
                    err=str(e),
                ),
            )
            return None

    def read_bar_dword(self, bar_index: int, offset: int) -> Optional[int]:
        """Read a 32-bit DWORD from BAR.

        Args:
            bar_index: BAR index (0-5)
            offset: Byte offset (must be 4-byte aligned)

        Returns:
            32-bit value as int, or None on failure
        """
        if offset % 4 != 0:
            raise ValueError(
                safe_format(
                    "Offset 0x{off:X} not 4-byte aligned", off=offset
                )
            )

        data = self.read_bar_bytes(bar_index, offset, 4)
        if data is None or len(data) != 4:
            return None

        return struct.unpack("<I", data)[0]

    def sample_bar_registers(
        self, bar_index: int, offsets: Optional[List[int]] = None
    ) -> Dict[int, int]:
        """Sample register values from BAR at specified offsets.

        Args:
            bar_index: BAR index (0-5)
            offsets: List of offsets to sample (4-byte aligned).
                     If None, samples common register offsets.

        Returns:
            Dict mapping offset to 32-bit value
        """
        bar_info = self.get_bar_info(bar_index)
        if not bar_info or bar_info.size == 0:
            log_warning_safe(
                logger,
                safe_format("BAR{idx} not available for sampling", idx=bar_index),
                prefix="SYSFS_BAR",
            )
            return {}

        # Default: sample first 8 KiB at 4-byte intervals
        if offsets is None:
            max_sample = min(8192, bar_info.size)
            offsets = list(range(0, max_sample, 4))

        samples: Dict[int, int] = {}
        for offset in offsets:
            if offset % 4 != 0:
                log_warning_safe(
                    logger,
                    safe_format(
                        "Skipping unaligned offset 0x{off:X}", off=offset
                    ),
                    prefix="SYSFS_BAR",
                )
                continue

            if offset >= bar_info.size:
                break

            value = self.read_bar_dword(bar_index, offset)
            if value is not None:
                samples[offset] = value

        log_info_safe(
            logger,
            safe_format(
                "Sampled {count} registers from BAR{idx}",
                count=len(samples),
                idx=bar_index,
            ),
            prefix="SYSFS_BAR",
        )

        return samples

    def dump_bar_to_file(self, bar_index: int, output_path: Path) -> bool:
        """Dump entire BAR contents to a binary file.

        Args:
            bar_index: BAR index (0-5)
            output_path: Path to write binary dump

        Returns:
            True if successful, False otherwise
        """
        data = self.read_bar_bytes(bar_index)
        if data is None:
            return False

        try:
            with open(output_path, "wb") as f:
                f.write(data)

            log_info_safe(
                logger,
                safe_format(
                    "Dumped BAR{idx} ({size} bytes) to {path}",
                    idx=bar_index,
                    size=len(data),
                    path=output_path,
                ),
                prefix="SYSFS_BAR",
            )
            return True

        except IOError as e:
            log_error_safe(
                logger,
                safe_format("Failed to write dump file: {err}", err=str(e)),
            )
            return False
