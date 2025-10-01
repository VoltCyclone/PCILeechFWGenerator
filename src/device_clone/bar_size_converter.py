#!/usr/bin/env python3
"""
BAR Size Conversion Utility for PCILeech

This module provides utilities for converting between BAR base addresses and size encodings
according to the PCIe specification. It handles proper encoding of BAR sizes for the
shadow configuration space and validates sizes against PCIe requirements.
"""

import logging
from typing import Optional, Tuple, Union

from src.device_clone.constants import BAR_SIZE_CONSTANTS
from src.exceptions import ContextError
from src.string_utils import (log_debug_safe, log_error_safe, log_info_safe,
                              log_warning_safe, safe_format)

logger = logging.getLogger(__name__)


def extract_bar_size(bar: dict) -> int:
    """Extract BAR size and raise ContextError for invalid sizes (0 or >= 4GB)."""
    size = bar.get("size", 0)
    if size == 0 or size >= 4294967296:
        raise ContextError(
            safe_format(
                "Invalid BAR size: size={size} (BAR size cannot be zero or >= 4GB)",
                size=size,
            )
        )
    return size


class BarSizeConverter:
    """Handles conversion between BAR addresses and size encodings."""

    @staticmethod
    def address_to_size(base_address: int, bar_type: str = "memory") -> int:
        """
        Convert a BAR base address to its size in bytes.

        According to PCIe spec, the size is determined by writing all 1s to the BAR
        and reading back. The device will return 0s in the bits that are hardwired
        to 0 (representing the size) and 1s in the bits that can be programmed.

        Args:
            base_address: The BAR base address value
            bar_type: Type of BAR ("memory" or "io")

        Returns:
            Size of the BAR in bytes

        Raises:
            ValueError: If the address format is invalid
        """
        if base_address == 0:
            return 0

        if bar_type.lower() == "io":
            # I/O BARs: bits [1:0] are reserved, mask them
            mask = BAR_SIZE_CONSTANTS["IO_ADDRESS_MASK"]
            # Find the least significant bit that is 0
            size_mask = (~base_address) & mask
            if size_mask == 0:
                return 0
            # Find the position of the least significant 1 bit
            size = size_mask & -size_mask
            return size
        else:
            # Memory BARs: bits [3:0] are reserved, mask them
            mask = BAR_SIZE_CONSTANTS["MEMORY_ADDRESS_MASK"]
            # Find the least significant bit that is 0
            size_mask = (~base_address) & mask
            if size_mask == 0:
                return 0
            # Find the position of the least significant 1 bit
            size = size_mask & -size_mask
            return size

    @staticmethod
    def size_to_encoding(
        size: int,
        bar_type: str = "memory",
        is_64bit: bool = False,
        prefetchable: bool = False,
    ) -> int:
        """
        Convert a BAR size to its proper encoding for the configuration space.

        The encoding sets all bits to 1 except for the size bits which are 0,
        and the type bits in the lower nibble.

        Args:
            size: Size of the BAR in bytes
            bar_type: Type of BAR ("memory" or "io")
            is_64bit: Whether this is a 64-bit memory BAR
            prefetchable: Whether this is a prefetchable memory BAR

        Returns:
            Encoded BAR value for the configuration space

        Raises:
            ValueError: If the size is invalid according to PCIe spec
        """
        if size == 0:
            return 0

        # Validate size is power of 2
        if size & (size - 1) != 0:
            raise ValueError(
                safe_format("BAR size must be a power of 2, got {size}", size=size)
            )

        if bar_type.lower() == "io":
            # Validate I/O BAR size
            if size < BAR_SIZE_CONSTANTS["MIN_IO_SIZE"]:
                raise ValueError(
                    safe_format(
                        "I/O BAR size must be at least {min_size} bytes, got {size}",
                        min_size=BAR_SIZE_CONSTANTS["MIN_IO_SIZE"],
                        size=size,
                    )
                )
            if size > BAR_SIZE_CONSTANTS["MAX_IO_SIZE"]:
                raise ValueError(
                    safe_format(
                        "I/O BAR size cannot exceed {max_size} bytes, got {size}",
                        max_size=BAR_SIZE_CONSTANTS["MAX_IO_SIZE"],
                        size=size,
                    )
                )
            # Create size mask with lower 2 bits set for I/O type
            size_mask = ~(size - 1)
            return (size_mask & BAR_SIZE_CONSTANTS["IO_ADDRESS_MASK"]) | 0x1
        else:
            # Validate memory BAR size
            if size < BAR_SIZE_CONSTANTS["MIN_MEMORY_SIZE"]:
                raise ValueError(
                    safe_format(
                        "Memory BAR size must be at least {min_size} bytes, "
                        "got {size}",
                        min_size=BAR_SIZE_CONSTANTS["MIN_MEMORY_SIZE"],
                        size=size,
                    )
                )
            # Create size mask
            size_mask = ~(size - 1)
            encoding = size_mask & BAR_SIZE_CONSTANTS["MEMORY_ADDRESS_MASK"]

            # Set type bits
            if is_64bit:
                encoding |= BAR_SIZE_CONSTANTS["TYPE_64BIT"]
            if prefetchable:
                encoding |= BAR_SIZE_CONSTANTS["TYPE_PREFETCHABLE"]

            return encoding

    @staticmethod
    def decode_bar_register(bar_value: int) -> Tuple[str, int, bool, bool]:
        """
        Decode a BAR register value to extract type and properties.

        Args:
            bar_value: The BAR register value

        Returns:
            Tuple of (bar_type, address, is_64bit, prefetchable)
        """
        if bar_value & 0x1:
            # I/O BAR
            address = bar_value & BAR_SIZE_CONSTANTS["IO_ADDRESS_MASK"]
            return ("io", address, False, False)
        else:
            # Memory BAR
            address = bar_value & BAR_SIZE_CONSTANTS["MEMORY_ADDRESS_MASK"]
            is_64bit = bool(bar_value & BAR_SIZE_CONSTANTS["TYPE_64BIT"])
            prefetchable = bool(bar_value & BAR_SIZE_CONSTANTS["TYPE_PREFETCHABLE"])
            return ("memory", address, is_64bit, prefetchable)

    @staticmethod
    def validate_bar_size(size: int, bar_type: str = "memory") -> bool:
        """
        Validate if a BAR size meets PCIe specification requirements.

        Args:
            size: Size to validate in bytes
            bar_type: Type of BAR ("memory" or "io")

        Returns:
            True if size is valid, False otherwise
        """
        if size == 0:
            return True  # Disabled BAR is valid

        # Must be power of 2
        if size & (size - 1) != 0:
            return False

        if bar_type.lower() == "io":
            return (
                BAR_SIZE_CONSTANTS["MIN_IO_SIZE"]
                <= size
                <= BAR_SIZE_CONSTANTS["MAX_IO_SIZE"]
            )
        else:
            return size >= BAR_SIZE_CONSTANTS["MIN_MEMORY_SIZE"]

    @staticmethod
    def get_size_from_encoding(encoded_value: int, bar_type: str = "memory") -> int:
        """
        Extract the size from an encoded BAR value.

        This is the reverse of size_to_encoding - it extracts the size
        from a BAR value that has all 1s except for the size bits.

        Args:
            encoded_value: The encoded BAR value
            bar_type: Type of BAR ("memory" or "io")

        Returns:
            Size of the BAR in bytes
        """
        if encoded_value == 0:
            return 0

        if bar_type.lower() == "io":
            mask = BAR_SIZE_CONSTANTS["IO_ADDRESS_MASK"]
            size_bits = encoded_value & mask
        else:
            mask = BAR_SIZE_CONSTANTS["MEMORY_ADDRESS_MASK"]
            size_bits = encoded_value & mask

        if size_bits == 0:
            return 0

        inverted = ~size_bits & mask
        if inverted == 0:
            return 0

        # Find position of least significant 1 bit
        bar_size = inverted & -inverted
        if bar_size <= 0 or bar_size >= 2**32:
            raise ContextError(
                safe_format(
                    "Invalid BAR size: {size} (BAR size cannot be zero or >= 4GB)",
                    size=bar_size,
                )
            )
        return bar_size

    @staticmethod
    def format_size(size: int) -> str:
        """
        Format a size value for human-readable display.

        Args:
            size: Size in bytes

        Returns:
            Formatted string (e.g., "4KB", "256MB")
        """
        if size == 0:
            return "Disabled"

        units = [
            (1 << 30, "GB"),
            (1 << 20, "MB"),
            (1 << 10, "KB"),
        ]

        for unit_size, unit_name in units:
            if size >= unit_size and size % unit_size == 0:
                return f"{size // unit_size}{unit_name}"

        return f"{size} bytes"

    @classmethod
    def convert_bar_for_shadow_space(cls, bar_info: dict) -> dict:
        """
            Convert BAR information for use in shadow configuration space.

            Args:
                bar_info: Dictionary containing BAR information with keys:
                    - base_address: Current BAR base address
                    - size: BAR size in bytes
                    - bar_type: "memory" or "io"
                    - is_64bit: Whether this is a 64-bit BAR
                    - prefetchable: Whether this is prefetchable
        return bar_size  # Ensure all code paths return an int
            Returns:
                Dictionary with:
                    - encoded_value: The encoded BAR value for shadow space
                    - size: The size in bytes
                    - size_str: Human-readable size string
        """
        size = bar_info.get("size", 0)
        bar_type = bar_info.get("bar_type", "memory")
        is_64bit = bar_info.get("is_64bit", False)
        prefetchable = bar_info.get("prefetchable", False)

        try:
            # Validate the size
            if not cls.validate_bar_size(size, bar_type):
                log_warning_safe(
                    logger,
                    safe_format(
                        "Invalid BAR size {size} for {bar_type} BAR",
                        size=size,
                        bar_type=bar_type,
                    ),
                    prefix="BAR",
                )
                size = 0  # Disable invalid BARs

            # Convert to encoding
            encoded_value = cls.size_to_encoding(size, bar_type, is_64bit, prefetchable)

            return {
                "encoded_value": encoded_value,
                "size": size,
                "size_str": cls.format_size(size),
            }

        except Exception as e:
            log_error_safe(
                logger,
                safe_format("Error converting BAR for shadow space: {error}", error=e),
                prefix="BAR",
            )
            return {
                "encoded_value": 0,
                "size": 0,
                "size_str": "Disabled",
            }
