#!/usr/bin/env python3
"""
PCI Capability Core Abstractions

This module provides the core abstractions for efficient PCI capability
analysis, including the ConfigSpace class for bytearray-based configuration
space handling and the unified CapabilityWalker for both standard and
extended capabilities.
"""

import logging

from typing import Dict, Iterator, Optional, Set

from ..string_utils import log_info_safe, log_warning_safe, safe_format

from .constants import (EXTENDED_CAPABILITY_NAMES, PCI_CAPABILITIES_POINTER,
                        PCI_CONFIG_SPACE_MIN_HEX_CHARS,
                        PCI_CONFIG_SPACE_MIN_SIZE, PCI_EXT_CAP_ALIGNMENT,
                        PCI_EXT_CAP_ID_MASK, PCI_EXT_CAP_NEXT_PTR_MASK,
                        PCI_EXT_CAP_NEXT_PTR_SHIFT, PCI_EXT_CAP_START,
                        PCI_EXT_CAP_VERSION_MASK, PCI_EXT_CAP_VERSION_SHIFT,
                        PCI_EXT_CONFIG_SPACE_END, PCI_STATUS_CAP_LIST,
                        PCI_STATUS_REGISTER, STANDARD_CAPABILITY_NAMES,
                        TWO_BYTE_HEADER_CAPABILITIES)

from .types import CapabilityInfo, CapabilityType

logger = logging.getLogger(__name__)


class ConfigSpace:
    """
    Efficient bytearray-based PCI configuration space representation.

    This class provides safe access to configuration space data with
    bounds checking and validation. It accepts hex strings and converts
    them to bytearray internally for efficient manipulation.
    """

    def __init__(self, hex_data: str) -> None:
        """
        Initialize configuration space from hex string.

        Args:
            hex_data: Configuration space as a hex string

        Raises:
            ValueError: If hex_data is invalid or too small
        """
        self._validate_hex_string(hex_data)
        try:
            self._data = bytearray.fromhex(hex_data)
        except ValueError as e:
            raise ValueError(
                safe_format(
                    "Invalid hex data: {e}",
                    e=e,
                )
            ) from e

    def _validate_hex_string(self, hex_data: str) -> None:
        """Validate that the hex string meets basic requirements."""
        if not hex_data:
            raise ValueError("Configuration string is empty")

        if len(hex_data) % 2 != 0:
            raise ValueError(
                safe_format(
                    "Configuration string length {length} is odd",
                    length=len(hex_data),
                )
            )
        if len(hex_data) < PCI_CONFIG_SPACE_MIN_HEX_CHARS:
            raise ValueError(
                safe_format(
                    "Configuration string length {length} is less than {min_chars} hex chars ({min_bytes} bytes)",
                    length=len(hex_data),
                    min_chars=PCI_CONFIG_SPACE_MIN_HEX_CHARS,
                    min_bytes=PCI_CONFIG_SPACE_MIN_SIZE,
                )
            )
        # Validate it's proper hex by attempting conversion
        try:
            int(hex_data, 16)
        except ValueError as e:
            raise ValueError(
                "Configuration string contains invalid hex characters"
            ) from e

    def read_byte(self, offset: int) -> int:
        """
        Read a single byte from configuration space.

        Args:
            offset: Byte offset to read from

        Returns:
            Byte value at the specified offset

        Raises:
            IndexError: If offset is out of bounds
        """
        if offset < 0 or offset >= len(self._data):
            raise IndexError(
                safe_format(
                    "Offset {offset:02x} is out of bounds (size: {size})",
                    offset=offset,
                    size=len(self._data),
                )
            )
        return self._data[offset]

    def read_word(self, offset: int) -> int:
        """
        Read a 16-bit word from configuration space (little-endian).

        Args:
            offset: Byte offset to read from

        Returns:
            16-bit word value at the specified offset

        Raises:
            IndexError: If offset+1 is out of bounds
        """
        if offset < 0 or offset + 1 >= len(self._data):
            raise IndexError(
                safe_format(
                    "Word offset {offset:02x} is out of bounds (size: {size})",
                    offset=offset,
                    size=len(self._data),
                )
            )
        return int.from_bytes(self._data[offset : offset + 2], "little")

    def read_dword(self, offset: int) -> int:
        """
        Read a 32-bit dword from configuration space (little-endian).

        Args:
            offset: Byte offset to read from

        Returns:
            32-bit dword value at the specified offset

        Raises:
            IndexError: If offset+3 is out of bounds
        """
        if offset < 0 or offset + 3 >= len(self._data):
            raise IndexError(
                safe_format(
                    "Dword offset {offset:02x} is out of bounds (size: {size})",
                    offset=offset,
                    size=len(self._data),
                )
            )
        return int.from_bytes(self._data[offset : offset + 4], "little")

    def write_byte(self, offset: int, value: int) -> None:
        """
        Write a single byte to configuration space.

        Args:
            offset: Byte offset to write to
            value: Byte value to write

        Raises:
            IndexError: If offset is out of bounds
            ValueError: If value is not a valid byte
        """
        if offset < 0 or offset >= len(self._data):
            raise IndexError(
                safe_format(
                    "Offset {offset:02x} is out of bounds (size: {size})",
                    offset=offset,
                    size=len(self._data),
                )
            )
        if not 0 <= value <= 255:
            raise ValueError(
                safe_format(
                    "Value {value} is not a valid byte (0-255)",
                    value=value,
                )
            )
        self._data[offset] = value

    def write_word(self, offset: int, value: int) -> None:
        """
        Write a 16-bit word to configuration space (little-endian).

        Args:
            offset: Byte offset to write to
            value: 16-bit word value to write

        Raises:
            IndexError: If offset+1 is out of bounds
            ValueError: If value is not a valid word
        """
        if offset < 0 or offset + 1 >= len(self._data):
            raise IndexError(
                safe_format(
                    "Word offset {offset:02x} is out of bounds (size: {size})",
                    offset=offset,
                    size=len(self._data),
                )
            )
        if not 0 <= value <= 0xFFFF:
            raise ValueError(
                safe_format(
                    "Value {value} is not a valid word (0-65535)",
                    value=value,
                )
            )
        self._data[offset : offset + 2] = value.to_bytes(2, "little")

    def write_dword(self, offset: int, value: int) -> None:
        """
        Write a 32-bit dword to configuration space (little-endian).

        Args:
            offset: Byte offset to write to
            value: 32-bit dword value to write

        Raises:
            IndexError: If offset+3 is out of bounds
            ValueError: If value is not a valid dword
        """
        if offset < 0 or offset + 3 >= len(self._data):
            raise IndexError(
                safe_format(
                    "Dword offset {offset:02x} is out of bounds (size: {size})",
                    offset=offset,
                    size=len(self._data),
                )
            )
        if not 0 <= value <= 0xFFFFFFFF:
            raise ValueError(
                safe_format(
                    "Value {value} is not a valid dword (0-4294967295)",
                    value=value,
                )
            )
        self._data[offset : offset + 4] = value.to_bytes(4, "little")

    def has_data(self, offset: int, length: int) -> bool:
        """
        Check if configuration space has enough data at the specified offset.

        Args:
            offset: Byte offset to check
            length: Number of bytes needed

        Returns:
            True if enough data is available, False otherwise
        """
        return offset >= 0 and offset + length <= len(self._data)

    def to_hex(self) -> str:
        """
        Convert configuration space back to hex string.

        Returns:
            Configuration space as a hex string
        """
        return self._data.hex()

    def __len__(self) -> int:
        """Return the size of the configuration space in bytes."""
        return len(self._data)

    def __getitem__(self, key):
        """Allow array-like access to bytes."""
        return self._data[key]

    def __setitem__(self, key, value) -> None:
        """Allow array-like assignment to bytes."""
        self._data[key] = value


class CapabilityWalker:
    """
    Unified capability walker for both standard and extended capabilities.

    This class eliminates duplication between standard and extended capability
    walking by providing a parameterized implementation that handles both types
    with proper loop detection and bounds checking.
    """

    def __init__(self, config_space: ConfigSpace) -> None:
        """
        Initialize capability walker with configuration space.

        Args:
            config_space: ConfigSpace instance to walk
        """
        self.config_space = config_space

    def walk_standard_capabilities(self) -> Iterator[CapabilityInfo]:
        """
        Walk standard PCI capabilities.

        Yields:
            CapabilityInfo objects for each discovered standard capability
        """
        # Check if capabilities are supported
        if not self._capabilities_supported():
            return

        # Get capabilities pointer
        cap_ptr = self._get_capabilities_pointer()
        if cap_ptr == 0:
            return

        # Walk the capabilities list
        visited: Set[int] = set()
        current_ptr = cap_ptr

        while current_ptr != 0 and current_ptr not in visited:
            if not self.config_space.has_data(current_ptr, 2):
                log_warning_safe(
                    logger,
                    safe_format(
                        "Standard capability pointer {current_ptr:02x} is out of bounds",
                        current_ptr=current_ptr,
                    ),
                )
                break

            visited.add(current_ptr)

            try:
                cap_id = self.config_space.read_byte(current_ptr)
                next_ptr = self.config_space.read_byte(current_ptr + 1)

                # Handle capabilities with 2-byte headers
                if cap_id in TWO_BYTE_HEADER_CAPABILITIES:
                    if not self.config_space.has_data(current_ptr, 3):
                        log_warning_safe(
                            logger,
                            safe_format(
                                "2-byte header capability at {current_ptr:02x} is truncated",
                                current_ptr=current_ptr,
                            ),
                        )
                        break
                    next_ptr = self.config_space.read_byte(current_ptr + 2)

                name = STANDARD_CAPABILITY_NAMES.get(
                    cap_id,
                    safe_format(
                        "Unknown (0x{cap_id:02x})",
                        cap_id=cap_id,
                    ),
                )
                yield CapabilityInfo(
                    offset=current_ptr,
                    cap_id=cap_id,
                    cap_type=CapabilityType.STANDARD,
                    next_ptr=next_ptr,
                    name=name,
                )

                current_ptr = next_ptr

            except (IndexError, ValueError) as e:
                log_warning_safe(
                    logger,
                    safe_format(
                        "Error reading standard capability at {current_ptr:02x}: {e}",
                        current_ptr=current_ptr,
                        e=e,
                    ),
                    prefix="PCI_CAP",
                )
                break

    def walk_extended_capabilities(self) -> Iterator[CapabilityInfo]:
        """
        Walk extended PCI Express capabilities.

        Yields:
            CapabilityInfo objects for each discovered extended capability
        """
        # Check if configuration space is large enough for extended capabilities
        if not self.config_space.has_data(PCI_EXT_CAP_START, 4):
            return

        # Walk the extended capabilities list
        visited: Set[int] = set()
        current_ptr = PCI_EXT_CAP_START

        while current_ptr != 0 and current_ptr not in visited:
            if not self.config_space.has_data(current_ptr, 4):
                log_warning_safe(
                    logger,
                    safe_format(
                        "Extended capability pointer {current_ptr:03x} is out of bounds",
                        current_ptr=current_ptr,
                    ),
                    prefix="PCI_CAP",
                )
                break

            # Check DWORD alignment
            if current_ptr & PCI_EXT_CAP_ALIGNMENT != 0:
                log_warning_safe(
                    logger,
                    safe_format(
                        "Extended capability pointer {current_ptr:03x} is not DWORD aligned",
                        current_ptr=current_ptr,
                    ),
                    prefix="PCI_CAP",
                )
                break

            visited.add(current_ptr)

            try:
                header = self.config_space.read_dword(current_ptr)

                # Check if the header is all zeros (capability has been removed)
                if header == 0:
                    break

                # Extract fields from header
                cap_id = header & PCI_EXT_CAP_ID_MASK
                cap_version = (
                    header >> PCI_EXT_CAP_VERSION_SHIFT
                ) & PCI_EXT_CAP_VERSION_MASK
                next_ptr = (
                    header >> PCI_EXT_CAP_NEXT_PTR_SHIFT
                ) & PCI_EXT_CAP_NEXT_PTR_MASK

                # Skip if capability ID is 0 (removed capability)
                if cap_id == 0:
                    break

                name = EXTENDED_CAPABILITY_NAMES.get(
                    cap_id,
                    safe_format(
                        "Unknown Extended (0x{cap_id:04x})",
                        cap_id=cap_id,
                    ),
                )
                if cap_id not in EXTENDED_CAPABILITY_NAMES and cap_id <= 0x0029:
                    log_info_safe(
                        logger,
                        safe_format(
                            "Unknown extended capability ID 0x{cap_id:04x} encountered",
                            cap_id=cap_id,
                        ),
                    )
                yield CapabilityInfo(
                    offset=current_ptr,
                    cap_id=cap_id,
                    cap_type=CapabilityType.EXTENDED,
                    next_ptr=next_ptr,
                    name=name,
                    version=cap_version,
                )

                # Break before adding to visited when next_ptr == 0
                if next_ptr == 0:
                    break

                current_ptr = next_ptr

            except (IndexError, ValueError) as e:
                log_warning_safe(
                    logger,
                    safe_format(
                        "Error reading extended capability at {current_ptr:03x}: {e}",
                        current_ptr=current_ptr,
                        e=e,
                    ),
                    prefix="PCI_CAP",
                )
                break

    def find_capability(
        self, cap_id: int, cap_type: CapabilityType
    ) -> Optional[CapabilityInfo]:
        """
        Find a specific capability by ID and type.

        Args:
            cap_id: Capability ID to find
            cap_type: Type of capability (standard or extended)

        Returns:
            CapabilityInfo if found, None otherwise
        """
        if cap_type == CapabilityType.STANDARD:
            walker = self.walk_standard_capabilities()
        else:
            walker = self.walk_extended_capabilities()

        for cap_info in walker:
            if cap_info.cap_id == cap_id:
                return cap_info

        return None

    def get_all_capabilities(self) -> Dict[int, CapabilityInfo]:
        """
        Get all capabilities (both standard and extended).

        Returns:
            Dictionary mapping capability offsets to CapabilityInfo objects
        """
        capabilities = {}

        # Add standard capabilities
        for cap_info in self.walk_standard_capabilities():
            capabilities[cap_info.offset] = cap_info

        # Add extended capabilities
        for cap_info in self.walk_extended_capabilities():
            capabilities[cap_info.offset] = cap_info

        return capabilities

    def _capabilities_supported(self) -> bool:
        """Check if device supports capabilities."""
        if not self.config_space.has_data(PCI_STATUS_REGISTER, 2):
            log_warning_safe(
                logger,
                "Status register not found in configuration space",
                prefix="PCI_CAP",
            )
            return False

        try:
            status = self.config_space.read_word(PCI_STATUS_REGISTER)
            return bool(status & PCI_STATUS_CAP_LIST)
        except (IndexError, ValueError):
            log_warning_safe(
                logger,
                "Failed to read status register",
                prefix="PCI_CAP",
            )
            return False

    def _get_capabilities_pointer(self) -> int:
        """Get the capabilities pointer from offset 0x34."""
        if not self.config_space.has_data(PCI_CAPABILITIES_POINTER, 1):
            log_warning_safe(
                logger,
                "Capabilities pointer not found in configuration space",
                prefix="PCI_CAP",
            )
            return 0

        try:
            return self.config_space.read_byte(PCI_CAPABILITIES_POINTER)
        except (IndexError, ValueError):
            log_warning_safe(
                logger,
                "Failed to read capabilities pointer",
                prefix="PCI_CAP",
            )
            return 0
