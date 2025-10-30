#!/usr/bin/env python3
"""
BAR Size Conversion Utility for PCILeech

This module provides utilities for converting between BAR base addresses and size encodings
according to the PCIe specification. It handles proper encoding of BAR sizes for the
shadow configuration space and validates sizes against PCIe requirements.
"""

import logging
from typing import Any, Dict, List, Optional, Tuple, Union

from src.device_clone.constants import BAR_SIZE_CONSTANTS
from src.utils.validators import get_bar_size_validator
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
            Size in bytes represented by the address encoding
        """
        if base_address == 0:
            return 0  # Disabled BAR

        # For I/O BARs, clear the lower 2 bits (bit 0 is I/O space indicator, bit 1 reserved)
        # For Memory BARs, clear the lower 4 bits (type and prefetchable info)
        if bar_type.lower() == "io":
            mask = ~0x3  # Clear bits 0-1
        else:
            mask = ~0xF  # Clear bits 0-3

        # Find the first set bit from the right (LSB)
        # This represents the BAR's size encoding
        masked_addr = base_address & mask

        if masked_addr == 0:
            return 0

        # The size is determined by finding the rightmost set bit
        # Using two's complement: size = masked_addr & (-masked_addr)
        size = masked_addr & (~masked_addr + 1)

        return size

    @staticmethod
    def size_to_mask(size: int, bar_type: str = "memory") -> int:
        """
        Convert a BAR size to its mask value for the configuration space.

        The mask represents which bits are hardwired to 0 (size bits) vs
        programmable (address bits).

        Args:
            size: Size in bytes
            bar_type: Type of BAR ("memory" or "io")

        Returns:
            Mask value to be written to the shadow configuration space
        """
        if size == 0:
            return 0  # Disabled BAR returns 0

        # The mask is: ~(size - 1)
        # This creates 1s for address bits and 0s for size bits
        mask = ~(size - 1) & 0xFFFFFFFF  # Ensure 32-bit value

        # Set the appropriate low-order bits based on BAR type
        if bar_type.lower() == "io":
            # Bit 0 must be 1 for I/O space indicator
            mask |= BAR_SIZE_CONSTANTS["TYPE_IO"]
        else:
            # Memory space indicator (bit 0 = 0) is already handled by the mask
            # No need to explicitly set it
            pass

        return mask

    @classmethod
    def encode_bar_with_size(cls, size: int, bar_type: str = "memory",
                             prefetchable: bool = False, is_64bit: bool = False) -> int:
        """
        Create a complete BAR encoding with the specified size and attributes.

        Args:
            size: Size in bytes (must be power of 2 or 0)
            bar_type: Type of BAR ("memory" or "io")
            prefetchable: For memory BARs, whether it's prefetchable
            is_64bit: For memory BARs, whether it's 64-bit addressable

        Returns:
            Complete BAR value with size encoding and type bits
        """
        # Validate the size using new validator
        validator = get_bar_size_validator(bar_type=bar_type)
        result = validator.validate(size)
        if not result.valid:
            log_warning_safe(
                logger,
                safe_format("Invalid BAR size: {size}, errors: {errors}",
                           size=size, errors="; ".join(result.errors))
            )
            return 0

        if size == 0:
            return 0  # Disabled BAR

        # Get the base mask for the size
        mask = cls.size_to_mask(size, bar_type)

        # Add type-specific bits
        if bar_type.lower() == "io":
            # I/O space - bit 0 is already set by size_to_mask
            return mask
        else:
            # Memory space
            if prefetchable:
                mask |= BAR_SIZE_CONSTANTS["TYPE_PREFETCHABLE"]
            if is_64bit:
                mask |= BAR_SIZE_CONSTANTS["TYPE_64BIT"]
            return mask

    @staticmethod
    def decode_bar_type(bar_value: int) -> Tuple[str, int, bool, bool]:
        """
        Decode the type and attributes from a BAR value.

        Args:
            bar_value: The BAR register value

        Returns:
            Tuple of (bar_type, address, is_64bit, prefetchable)
            - bar_type: "io" or "memory"
            - address: The base address (with type bits masked off)
            - is_64bit: True if 64-bit BAR (memory only)
            - prefetchable: True if prefetchable (memory only)
        """
        if bar_value & BAR_SIZE_CONSTANTS["TYPE_IO"]:
            # I/O space BAR
            address = bar_value & BAR_SIZE_CONSTANTS["IO_ADDRESS_MASK"]
            return ("io", address, False, False)
        else:
            # Memory space BAR
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
        validator = get_bar_size_validator(bar_type=bar_type)
        result = validator.validate(size)
        return result.valid

    @staticmethod
    def get_size_from_encoding(encoded_value: int, bar_type: str = "memory") -> int:
        """
        Extract the size from an encoded BAR value.

        This simulates what happens when software writes all 1s to a BAR
        and reads back the value to determine the size.

        Args:
            encoded_value: The encoded BAR value (as read from hardware)
            bar_type: Type of BAR ("memory" or "io")

        Returns:
            Size in bytes
        """
        if encoded_value == 0:
            return 0  # Disabled BAR

        # Mask off the type bits
        if bar_type.lower() == "io":
            mask = BAR_SIZE_CONSTANTS["IO_ADDRESS_MASK"]
        else:
            mask = BAR_SIZE_CONSTANTS["MEMORY_ADDRESS_MASK"]

        size_bits = encoded_value & mask

        if size_bits == 0:
            return 0

        # Find the size by inverting and adding 1
        # This gives us the size represented by the encoding
        size = (~size_bits + 1) & mask

        # Ensure we have a valid size
        validator = get_bar_size_validator(bar_type=bar_type)
        result = validator.validate(size)
        if not result.valid:
            log_warning_safe(
                logger,
                safe_format("Invalid size {size} extracted from BAR encoding {encoding:#x}",
                           size=size, encoding=encoded_value)
            )
            return 0

        return size

    @classmethod
    def convert_bars_to_size_encoding(cls, bars: List[Dict[str, Any]]) -> Dict[int, int]:
        """
        Convert a list of BAR definitions to their size-encoded values.

        This is used to prepare the shadow configuration space where BARs
        report their size when written with all 1s.

        Args:
            bars: List of BAR definitions with 'base', 'size', and optionally
                  'type', 'prefetchable', 'is_64bit' fields

        Returns:
            Dictionary mapping BAR offset to encoded value
        """
        result = {}

        for i, bar in enumerate(bars):
            if not bar or bar.get("size", 0) == 0:
                continue

            # Determine BAR offset (BAR0 = 0x10, BAR1 = 0x14, etc.)
            offset = 0x10 + (i * 4)

            # Get BAR properties
            size = bar.get("size", 0)
            bar_type = bar.get("type", "memory")
            prefetchable = bar.get("prefetchable", False)
            is_64bit = bar.get("is_64bit", False)

            # For 64-bit BARs, they use two consecutive BAR slots
            if is_64bit and bar_type == "memory":
                # Lower 32 bits
                encoded = cls.encode_bar_with_size(
                    size, bar_type, prefetchable, is_64bit
                )
                result[offset] = encoded & 0xFFFFFFFF
                # Upper 32 bits (if within BAR range)
                if i < 5:  # BAR5 is the last one
                    result[offset + 4] = 0xFFFFFFFF  # Upper bits modifiable
            else:
                # 32-bit BAR
                encoded = cls.encode_bar_with_size(
                    size, bar_type, prefetchable, False
                )
                result[offset] = encoded

        return result

    @staticmethod
    def format_size(size: int) -> str:
        """
        Format BAR size in human-readable format.
        
        Args:
            size: Size in bytes
            
        Returns:
            Human-readable size string
        """
        if size == 0:
            return "Disabled"
        elif size < 1024:
            return f"{size} bytes"
        elif size < 1024 * 1024:
            return f"{size // 1024}KB"
        elif size < 1024 * 1024 * 1024:
            return f"{size // (1024 * 1024)}MB"
        else:
            return f"{size // (1024 * 1024 * 1024)}GB"

    @staticmethod
    def decode_bar_register(bar_value: int) -> Tuple[str, int, bool, bool]:
        """
        Decode the type and attributes from a BAR register value.
        
        This is an alias for decode_bar_type to maintain compatibility.
        
        Args:
            bar_value: The BAR register value
            
        Returns:
            Tuple of (bar_type, address, is_64bit, prefetchable)
        """
        return BarSizeConverter.decode_bar_type(bar_value)

    @classmethod
    def convert_bar_for_shadow_space(cls, bar_info: Dict[str, Any]) -> Dict[str, Any]:
        """
        Convert BAR information for shadow configuration space.
        
        Args:
            bar_info: Dictionary containing BAR information with keys:
                - base_address: BAR base address
                - size: BAR size in bytes
                - bar_type: "memory" or "io"
                - is_64bit: Whether it's a 64-bit BAR
                - prefetchable: Whether it's prefetchable
                
        Returns:
            Dictionary with:
                - encoded_value: The encoded BAR value for shadow space
                - size_str: Human-readable size string
        """
        size = bar_info.get("size", 0)
        bar_type = bar_info.get("bar_type", "memory")
        is_64bit = bar_info.get("is_64bit", False)
        prefetchable = bar_info.get("prefetchable", False)
        
        encoded_value = cls.encode_bar_with_size(size, bar_type, prefetchable, is_64bit)
        size_str = cls.format_size(size)
        
        return {
            "encoded_value": encoded_value,
            "size_str": size_str
        }

    @classmethod
    def size_to_encoding(cls, size: int, bar_type: str = "memory",
                        is_64bit: bool = False, prefetchable: bool = False) -> int:
        """
        Convert BAR size to its encoding value.
        
        This is a compatibility wrapper for encode_bar_with_size with
        parameters in a different order.
        
        Args:
            size: Size in bytes
            bar_type: Type of BAR ("memory" or "io")
            is_64bit: Whether it's a 64-bit BAR
            prefetchable: Whether it's prefetchable
            
        Returns:
            Encoded BAR value
        """
        return cls.encode_bar_with_size(size, bar_type, prefetchable, is_64bit)


def create_bar_size_mask(bar_info: Dict[str, Any], bar_index: int = 0) -> Tuple[int, int]:
    """
    Create BAR size mask values for a specific BAR.

    This is a convenience function that creates both the lower and upper
    32-bit values for a BAR's size encoding.

    Args:
        bar_info: Dictionary with BAR information (size, type, etc.)
        bar_index: Index of the BAR (0-5)

    Returns:
        Tuple of (lower_32_bits, upper_32_bits)
        For 32-bit BARs, upper_32_bits will be 0
    """
    converter = BarSizeConverter()

    size = bar_info.get("size", 0)
    bar_type = bar_info.get("type", "memory")
    prefetchable = bar_info.get("prefetchable", False)
    is_64bit = bar_info.get("is_64bit", False)

    if size == 0:
        return (0, 0)

    # Validate the BAR size
    validator = get_bar_size_validator(bar_type=bar_type)
    result = validator.validate(size)
    if not result.valid:
        log_error_safe(
            logger,
            safe_format("Invalid BAR{idx} size: {size}, errors: {errors}",
                       idx=bar_index, size=size, errors="; ".join(result.errors))
        )
        return (0, 0)

    encoded = converter.encode_bar_with_size(size, bar_type, prefetchable, is_64bit)

    if is_64bit and bar_type == "memory":
        # 64-bit BAR uses two consecutive slots
        return (encoded & 0xFFFFFFFF, 0xFFFFFFFF)
    else:
        # 32-bit BAR
        return (encoded, 0)
