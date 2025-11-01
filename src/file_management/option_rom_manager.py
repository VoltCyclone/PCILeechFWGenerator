#!/usr/bin/env python3
"""
Option-ROM Manager

Provides functionality to extract Option-ROM from donor PCI devices
and prepare it for inclusion in the FPGA firmware.
"""

import logging
import os
import subprocess
import sys
from pathlib import Path
from typing import Dict, Optional, Tuple

from string_utils import (
    log_debug_safe,
    log_error_safe,
    log_info_safe,
    log_warning_safe,
    safe_format,
)

logger = logging.getLogger(__name__)


class OptionROMError(Exception):
    """Base exception for Option-ROM operations"""

    def __init__(
        self,
        message: str,
        rom_path: Optional[str] = None,
        device_bdf: Optional[str] = None,
    ):
        """
        Initialize Option-ROM error

        Args:
            message: Error message
            rom_path: Path to the ROM file that caused the error
            device_bdf: PCI Bus:Device.Function of the device
        """
        super().__init__(message)
        self.rom_path = rom_path
        self.device_bdf = device_bdf

    def __str__(self) -> str:
        base_msg = super().__str__()
        details = []

        if self.device_bdf:
            details.append(f"device: {self.device_bdf}")
        if self.rom_path:
            details.append(f"rom_path: {self.rom_path}")

        if details:
            return f"{base_msg} ({', '.join(details)})"
        return base_msg


class OptionROMExtractionError(OptionROMError):
    """Raised when Option-ROM extraction fails"""

    pass


class OptionROMManager:
    """Constants and utilities for Option-ROM size management"""

    # Standard Option-ROM sizes (in bytes)
    SIZE_64KB = 65536
    SIZE_128KB = 131072
    SIZE_256KB = 262144
    SIZE_512KB = 524288
    SIZE_1MB = 1048576

    # Valid Option-ROM sizes (must be powers of 2, minimum 2KB)
    VALID_SIZES = [
        2048,  # 2KB (minimum)
        4096,  # 4KB
        8192,  # 8KB
        16384,  # 16KB
        32768,  # 32KB
        SIZE_64KB,
        SIZE_128KB,
        SIZE_256KB,
        SIZE_512KB,
        SIZE_1MB,
    ]

    # Maximum Option-ROM size supported by PCI specification
    MAX_SIZE = SIZE_1MB

    # Minimum Option-ROM size
    MIN_SIZE = 2048

    @classmethod
    def validate_size(cls, size: int) -> bool:
        """
        Validate if a given size is a valid Option-ROM size

        Args:
            size: Size in bytes to validate

        Returns:
            True if size is valid for Option-ROM
        """
        return size in cls.VALID_SIZES

    @classmethod
    def get_next_valid_size(cls, size: int) -> int:
        """
        Get the next valid Option-ROM size that can accommodate the given size

        Args:
            size: Required size in bytes

        Returns:
            Next valid Option-ROM size that can fit the required size

        Raises:
            OptionROMError: If size exceeds maximum supported size
        """
        if size > cls.MAX_SIZE:
            raise OptionROMError(
                f"Size {size} exceeds maximum Option-ROM size {cls.MAX_SIZE}"
            )

        for valid_size in cls.VALID_SIZES:
            if valid_size >= size:
                return valid_size

        # Should never reach here due to MAX_SIZE check above
        raise OptionROMError(f"No valid Option-ROM size found for {size} bytes")

    @classmethod
    def get_size_description(cls, size: int) -> str:
        """
        Get a human-readable description of the Option-ROM size

        Args:
            size: Size in bytes

        Returns:
            Human-readable size description
        """
        if size >= cls.SIZE_1MB:
            return f"{size // cls.SIZE_1MB}MB"
        elif size >= 1024:
            return f"{size // 1024}KB"
        else:
            return f"{size}B"

    @classmethod
    def calculate_blocks(cls, size: int) -> int:
        """
        Calculate the number of 512-byte blocks for a given size

        Args:
            size: Size in bytes

        Returns:
            Number of 512-byte blocks
        """
        return (size + 511) // 512  # Round up to nearest block


class OptionROMManager:
    """Manager for Option-ROM extraction and handling"""

    def __init__(
        self,
        output_dir: Optional[Path] = None,
        rom_file_path: Optional[str] = None,
    ):
        """
        Initialize the Option-ROM manager

        Args:
            output_dir: Path to directory for storing extracted ROM
            rom_file_path: Path to an existing ROM file to use instead of extraction
        """
        self.output_dir = (
            Path(output_dir) if output_dir else Path(__file__).parent.parent / "output"
        )
        self.rom_file_path = rom_file_path
        self.rom_size = 0
        self.rom_data: Optional[bytes] = None

    def extract_rom_linux(self, bdf: str) -> Tuple[bool, str]:
        """
        Extract Option-ROM from a PCI device on Linux

        Args:
            bdf: PCIe Bus:Device.Function (e.g., "0000:03:00.0")

        Returns:
            Tuple of (success, rom_path)
        """
        import re

        # Validate BDF format
        bdf_pattern = re.compile(
            r"^[0-9a-fA-F]{4}:[0-9a-fA-F]{2}:[0-9a-fA-F]{2}\.[0-7]$"
        )
        if not bdf_pattern.match(bdf):
            raise OptionROMExtractionError(
                f"Invalid BDF format: {bdf}", device_bdf=bdf
            )

        try:

            # Create output directory if it doesn't exist
            self.output_dir.mkdir(exist_ok=True, parents=True)
            rom_path = self.output_dir / "donor.rom"

            # Check if device exists
            device_path = f"/sys/bus/pci/devices/{bdf}"
            if not os.path.exists(device_path):
                raise OptionROMExtractionError(
                    f"PCI device not found: {bdf}", device_bdf=bdf
                )

            # Check if ROM file exists
            rom_sysfs_path = f"{device_path}/rom"
            if not os.path.exists(rom_sysfs_path):
                raise OptionROMExtractionError(
                    f"ROM file not available for device: {bdf}", device_bdf=bdf
                )

            # Enable ROM access
            log_info_safe(
                logger,
                safe_format("Enabling ROM access for {bdf}", bdf=bdf),
                prefix="ROM",
            )
            try:
                with open(rom_sysfs_path, "w") as f:
                    f.write("1")
            except (OSError, IOError) as e:
                raise OptionROMExtractionError(
                    safe_format("Failed to enable ROM access: {err}", err=e),
                    device_bdf=bdf,
                )

            # Extract ROM content
            try:
                log_info_safe(
                    logger,
                    safe_format(
                        "Extracting ROM from {bdf} to {path}", bdf=bdf, path=rom_path
                    ),
                    prefix="ROM",
                )
                subprocess.run(
                    ["dd", f"if={rom_sysfs_path}", f"of={rom_path}", "bs=4K"],
                    check=True,
                    capture_output=True,
                    text=True,
                )
            except subprocess.CalledProcessError as e:
                raise OptionROMExtractionError(
                    safe_format("Failed to extract ROM: {err}", err=e), device_bdf=bdf
                )
            finally:
                # Disable ROM access
                try:
                    with open(rom_sysfs_path, "w") as f:
                        f.write("0")
                except (OSError, IOError) as e:
                    log_warning_safe(
                        logger,
                        safe_format("Failed to disable ROM access: {err}", err=e),
                        prefix="ROM",
                    )

            # Verify ROM file was created and has content
            if not rom_path.exists():
                raise OptionROMExtractionError(
                    "ROM extraction failed: file not created", device_bdf=bdf
                )

            file_size = rom_path.stat().st_size
            if file_size == 0:
                raise OptionROMExtractionError(
                    "ROM extraction failed: file is empty", device_bdf=bdf
                )

            # Load the ROM data
            with open(rom_path, "rb") as f:
                self.rom_data = f.read()

            self.rom_file_path = str(rom_path)
            self.rom_size = file_size
            log_info_safe(
                logger,
                safe_format("Successfully extracted ROM ({size} bytes)", size=file_size),
                prefix="ROM",
            )

            return True, str(rom_path)

        except Exception as e:
            log_error_safe(
                logger,
                safe_format("ROM extraction failed: {err}", err=e),
                prefix="ROM",
            )
            return False, ""

    def load_rom_file(self, file_path: Optional[str] = None) -> bool:
        """
        Load ROM data from a file

        Args:
            file_path: Path to ROM file (uses self.rom_file_path if None)

        Returns:
            True if ROM was loaded successfully

        Raises:
            OptionROMError: If path not specified or file not found
        """
        path = file_path or self.rom_file_path
        if not path:
            raise OptionROMError("No ROM file path specified")

        rom_path = Path(path)
        if not rom_path.exists():
            raise OptionROMError(f"ROM file not found: {rom_path}")

        try:
            # Read ROM data
            with open(rom_path, "rb") as f:
                self.rom_data = f.read()

            self.rom_size = len(self.rom_data)
            log_info_safe(
                logger,
                safe_format(
                    "Loaded ROM file: {path} ({size} bytes)",
                    path=rom_path,
                    size=self.rom_size,
                ),
                prefix="ROM",
            )
            return True

        except Exception as e:
            log_error_safe(
                logger,
                safe_format("Failed to load ROM file: {err}", err=e),
                prefix="ROM",
            )
            return False

    def save_rom_hex(self, output_path: Optional[str] = None) -> bool:
        """
        Save ROM data in a format suitable for SystemVerilog $readmemh

        Args:
            output_path: Path to save the hex file (default: output_dir/rom_init.hex)

        Returns:
            True if data was saved successfully
        """
        if self.rom_data is None:
            if not self.load_rom_file():
                raise OptionROMError("No ROM data available")

        output_path = output_path or str(self.output_dir / "rom_init.hex")
        os.makedirs(os.path.dirname(os.path.abspath(output_path)), exist_ok=True)

        try:
            with open(output_path, "w") as f:
                # Process 4 bytes at a time to create 32-bit little-endian words
                for i in range(0, len(self.rom_data), 4):
                    chunk = self.rom_data[i : i + 4]
                    # Pad with zeros if needed
                    chunk = chunk + b"\x00" * (4 - len(chunk))
                    # Convert to little-endian hex format
                    le_word = f"{chunk[3]:02x}{chunk[2]:02x}{chunk[1]:02x}{chunk[0]:02x}"
                    f.write(f"{le_word}\n")

            log_info_safe(
                logger,
                safe_format("Saved ROM hex data to {path}", path=output_path),
                prefix="ROM",
            )
            return True
        except Exception as e:
            log_error_safe(
                logger,
                safe_format("Failed to save ROM hex data: {err}", err=e),
                prefix="ROM",
            )
            return False

    def get_rom_info(self) -> Dict[str, str]:
        """
        Get information about the ROM

        Returns:
            Dictionary with ROM information
        """
        if self.rom_data is None and self.rom_file_path:
            self.load_rom_file()

        info = {
            "rom_size": str(self.rom_size),
            "rom_file": str(self.rom_file_path) if self.rom_file_path else "",
        }

        if self.rom_data and len(self.rom_data) >= 2:
            # Check for valid Option ROM signature (0x55AA)
            has_valid_sig = self.rom_data[0] == 0x55 and self.rom_data[1] == 0xAA
            info["valid_signature"] = str(has_valid_sig)

            # Extract ROM size from header (offset 2, in 512-byte blocks)
            if len(self.rom_data) >= 3:
                info["rom_size_from_header"] = str(self.rom_data[2] * 512)

        return info

    def setup_option_rom(
        self, bdf: str, use_existing_rom: bool = False
    ) -> Dict[str, str]:
        """
        Complete setup process: extract ROM, save hex file, and return info

        Args:
            bdf: PCIe Bus:Device.Function
            use_existing_rom: Use existing ROM file if available

        Returns:
            Dictionary with ROM information
        """
        # Check if we should use an existing ROM file
        if (
            use_existing_rom
            and self.rom_file_path
            and os.path.exists(self.rom_file_path)
        ):
            log_info_safe(
                logger,
                safe_format("Using existing ROM file: {path}", path=self.rom_file_path),
                prefix="ROM",
            )
            self.load_rom_file()
        else:
            # Extract ROM from device
            success, rom_path = self.extract_rom_linux(bdf)
            if not success:
                raise OptionROMError(
                    f"Failed to extract ROM from {bdf}", device_bdf=bdf
                )

        # Save ROM in hex format for SystemVerilog
        hex_path = str(self.output_dir / "rom_init.hex")
        if not self.save_rom_hex(hex_path):
            raise OptionROMError("Failed to save ROM hex file")

        return self.get_rom_info()


def main():
    """CLI interface for Option-ROM manager"""
    import argparse

    parser = argparse.ArgumentParser(description="Option-ROM Extraction Tool")
    parser.add_argument(
        "--bd", required=True, help="PCIe Bus:Device.Function (e.g., 0000:03:00.0)"
    )
    parser.add_argument("--output-dir", help="Directory to save extracted ROM files")
    parser.add_argument(
        "--rom-file", help="Use existing ROM file instead of extraction"
    )
    parser.add_argument(
        "--verbose", "-v", action="store_true", help="Enable verbose logging"
    )

    args = parser.parse_args()

    if args.verbose:
        logging.basicConfig(level=logging.DEBUG)
    else:
        logging.basicConfig(level=logging.INFO)

    try:
        manager = OptionROMManager(
            output_dir=args.output_dir,
            rom_file_path=args.rom_file,
        )

        if args.rom_file:
            # Use existing ROM file
            if not manager.load_rom_file():
                sys.exit(1)
        else:
            # Extract ROM from device
            success, rom_path = manager.extract_rom_linux(args.bdf)
            if not success:
                sys.exit(1)

        # Save ROM in hex format for SystemVerilog
        manager.save_rom_hex()

        # Print ROM information
        rom_info = manager.get_rom_info()
        print("Option-ROM Information:")
        for key, value in rom_info.items():
            print(f"  {key}: {value}")

    except OptionROMError as e:
        print(f"Error: {e}", file=sys.stderr)
        sys.exit(1)
    except KeyboardInterrupt:
        print("\nOperation cancelled by user")
        sys.exit(1)


if __name__ == "__main__":
    main()
