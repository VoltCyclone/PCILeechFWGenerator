#!/usr/bin/env python3
"""
Configuration Space Hex Formatter Module

Handles conversion of PCI configuration space data to hex format suitable for
Vivado's $readmemh initialization. Ensures proper little-endian formatting and
generates hex files compatible with FPGA initialization.
"""

import logging
from pathlib import Path
from typing import List, Optional, Union

from src.string_utils import (log_debug_safe, log_error_safe, log_info_safe,
                              safe_format)

logger = logging.getLogger(__name__)


class ConfigSpaceHexFormatter:
    """
    Formats PCI configuration space data into hex files for FPGA initialization.

    This class handles:
    - Converting configuration space bytes to little-endian 32-bit words
    - Generating properly formatted hex files for Vivado $readmemh
    - Adding debug comments with register offsets
    - Ensuring proper alignment and padding
    """

    # Standard register names for common offsets
    REGISTER_NAMES = {
        0x000: "Device/Vendor ID",
        0x004: "Status/Command",
        0x008: "Class Code/Revision ID",
        0x00C: "BIST/Header Type/Latency Timer/Cache Line Size",
        0x010: "BAR0",
        0x014: "BAR1",
        0x018: "BAR2",
        0x01C: "BAR3",
        0x020: "BAR4",
        0x024: "BAR5",
        0x028: "Cardbus CIS Pointer",
        0x02C: "Subsystem ID/Subsystem Vendor ID",
        0x030: "Expansion ROM Base Address",
        0x034: "Capabilities Pointer",
        0x038: "Reserved",
        0x03C: "Max_Lat/Min_Gnt/Interrupt Pin/Interrupt Line",
    }

    def __init__(self):
        """Initialize the hex formatter."""
        self.logger = logging.getLogger(__name__)

    def format_config_space_to_hex(
        self,
        config_space_data: bytes,
        include_comments: bool = True,
        vendor_id: Optional[str] = None,
        device_id: Optional[str] = None,
        class_code: Optional[str] = None,
        board: Optional[str] = None,
    ) -> str:
        """
        Convert configuration space data to hex format.

        Args:
            config_space_data: Raw configuration space bytes
            include_comments: Whether to include offset/register comments

        Returns:
            Formatted hex string suitable for $readmemh

        Raises:
            ValueError: If config space data is invalid
        """
        if not config_space_data:
            raise ValueError("Configuration space data cannot be empty")

        # Ensure data is aligned to 32-bit boundaries
        if len(config_space_data) % 4 != 0:
            padding_bytes = 4 - (len(config_space_data) % 4)
            log_info_safe(
                self.logger,
                safe_format(
                    "Padding config space with {padding} zero bytes for alignment",
                    padding=padding_bytes,
                ),
                prefix="HEX",
            )
            config_space_data = config_space_data + bytes(padding_bytes)

        hex_lines = []

        # Add header comment using unified helper
        if include_comments:
            from src.string_utils import generate_hex_header_comment

            header = generate_hex_header_comment(
                title=(
                    "config_space_init.hex - " "PCIe Configuration Space Initialization"
                ),
                total_bytes=len(config_space_data),
                total_dwords=len(config_space_data) // 4,
                vendor_id=vendor_id,
                device_id=device_id,
                class_code=class_code,
                board=board,
            )
            hex_lines.append(header)
            hex_lines.append("")

        # Process data in 32-bit chunks
        for offset in range(0, len(config_space_data), 4):
            # Extract 4 bytes (32-bit word)
            if offset + 4 <= len(config_space_data):
                word_bytes = config_space_data[offset : offset + 4]
            else:
                # Handle partial word at end (shouldn't happen with padding)
                word_bytes = config_space_data[offset:]
                word_bytes += bytes(4 - len(word_bytes))

            # Convert to little-endian 32-bit word
            # The bytes are already in little-endian order in memory,
            # so we just need to format them correctly
            word_value = int.from_bytes(word_bytes, byteorder="little")

            # Format as 8-character hex string (32 bits)
            hex_word = f"{word_value:08X}"

            # Add comment if enabled
            if include_comments:
                comment = self._get_register_comment(offset)
                if comment:
                    hex_lines.append(f"// Offset 0x{offset:03X} - {comment}")

            # Add the hex word
            hex_lines.append(hex_word)

            # Add spacing between major sections
            if include_comments and offset in [0x03C, 0x0FC, 0x3FC]:
                hex_lines.append("")

        return "\n".join(hex_lines)

    def _get_register_comment(self, offset: int) -> Optional[str]:
        """
        Get a descriptive comment for a register offset.

        Args:
            offset: Register offset in configuration space

        Returns:
            Register description or None if no standard register
        """
        # Check standard registers
        if offset in self.REGISTER_NAMES:
            return self.REGISTER_NAMES[offset]

        # Check capability regions
        if 0x040 <= offset < 0x100:
            return f"Capability at 0x{offset:02X}"
        elif 0x100 <= offset < 0x1000:
            return f"Extended Capability at 0x{offset:03X}"

        return None

    def write_hex_file(
        self,
        config_space_data: bytes,
        output_path: Union[str, Path],
        include_comments: bool = True,
    ) -> Path:
        """
        Write configuration space data to a hex file.

        Args:
            config_space_data: Raw configuration space bytes
            output_path: Path where hex file should be written
            include_comments: Whether to include offset/register comments

        Returns:
            Path to the written hex file

        Raises:
            IOError: If file cannot be written
        """
        output_path = Path(output_path)

        # Ensure parent directory exists
        output_path.parent.mkdir(parents=True, exist_ok=True)

        # Format the hex data
        hex_content = self.format_config_space_to_hex(
            config_space_data, include_comments=include_comments
        )

        # Write to file
        try:
            with open(output_path, "w") as f:
                f.write(hex_content)

            log_info_safe(
                self.logger,
                safe_format(
                    "Written configuration space hex file: {path}",
                    path=output_path,
                ),
                prefix="HEX",
            )

            return output_path

        except IOError as e:
            log_error_safe(
                self.logger,
                safe_format(
                    "Failed to write hex file {path}: {error}",
                    path=output_path,
                    error=str(e),
                ),
                prefix="HEX",
            )
            raise

    def validate_hex_file(self, hex_file_path: Union[str, Path]) -> bool:
        """
        Validate a hex file for proper formatting.

        Args:
            hex_file_path: Path to hex file to validate

        Returns:
            True if valid, False otherwise
        """
        hex_file_path = Path(hex_file_path)

        if not hex_file_path.exists():
            log_error_safe(
                self.logger,
                safe_format(
                    "Hex file does not exist: {path}",
                    path=hex_file_path,
                ),
                prefix="HEX",
            )
            return False

        try:
            with open(hex_file_path, "r") as f:
                lines = f.readlines()

            hex_word_count = 0
            for line in lines:
                line = line.strip()

                # Skip empty lines and comments
                if not line or line.startswith("//"):
                    continue

                # Validate hex word format (8 hex characters)
                if len(line) != 8:
                    log_error_safe(
                        self.logger,
                        safe_format(
                            "Invalid hex word length in line: {line}",
                            line=line,
                        ),
                        prefix="HEX",
                    )
                    return False

                # Validate hex characters
                try:
                    int(line, 16)
                    hex_word_count += 1
                except ValueError:
                    log_error_safe(
                        self.logger,
                        safe_format(
                            "Invalid hex characters in line: {line}",
                            line=line,
                        ),
                        prefix="HEX",
                    )
                    return False

            log_info_safe(
                self.logger,
                safe_format(
                    "Hex file validated successfully: {words} words",
                    words=hex_word_count,
                ),
                prefix="HEX",
            )

            return True

        except IOError as e:
            log_error_safe(
                self.logger,
                safe_format(
                    "Failed to read hex file {path}: {error}",
                    path=hex_file_path,
                    error=str(e),
                ),
                prefix="HEX",
            )
            return False

    def convert_to_dword_list(self, config_space_data: bytes) -> List[int]:
        """
        Convert configuration space bytes to a list of 32-bit dwords.

        Args:
            config_space_data: Raw configuration space bytes

        Returns:
            List of 32-bit integers in little-endian format
        """
        dwords = []

        # Ensure alignment
        if len(config_space_data) % 4 != 0:
            padding_bytes = 4 - (len(config_space_data) % 4)
            config_space_data = config_space_data + bytes(padding_bytes)

        # Convert to dwords
        for offset in range(0, len(config_space_data), 4):
            word_bytes = config_space_data[offset : offset + 4]
            dword = int.from_bytes(word_bytes, byteorder="little")
            dwords.append(dword)

        return dwords


def create_config_space_hex_file(
    config_space_data: bytes,
    output_path: Union[str, Path],
    include_comments: bool = True,
) -> Path:
    """
    Convenience function to create a configuration space hex file.

    Args:
        config_space_data: Raw configuration space bytes
        output_path: Path where hex file should be written
        include_comments: Whether to include offset/register comments

    Returns:
        Path to the written hex file
    """
    formatter = ConfigSpaceHexFormatter()
    return formatter.write_hex_file(config_space_data, output_path, include_comments)
