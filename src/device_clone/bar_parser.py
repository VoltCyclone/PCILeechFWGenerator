"""Unified BAR parsing module for PCI configuration space.

This module provides a single, consistent implementation for parsing BAR
(Base Address Register) information from PCI configuration space data.
"""

import struct
from typing import List, Optional, Union

from src.device_clone.config_space_manager import BarInfo, ConfigSpaceConstants
from src.device_clone.constants import BAR_SIZE_CONSTANTS
from src.string_utils import (
    log_debug_safe,
    log_error_safe,
    log_warning_safe,
    safe_format,
)
from src.log_config import get_logger

logger = get_logger(__name__)


class UnifiedBarParser:
    """Unified BAR parser that handles both hex string and bytes input.
    
    This parser extracts BAR information from PCI configuration space and
    leverages the existing BAR size discovery mechanisms rather than using
    hardcoded defaults.
    """

    @classmethod
    def parse_bars(cls, config_space: Union[str, bytes]) -> List[BarInfo]:
        """Parse BAR information from PCI configuration space.
        
        Args:
            config_space: Configuration space data as hex string or bytes
            
        Returns:
            List of BarInfo objects containing parsed BAR information
        """
        # Convert hex string to bytes if needed
        if isinstance(config_space, str):
            try:
                # Remove any whitespace and convert hex to bytes
                config_bytes = bytes.fromhex(config_space.replace(" ", ""))
            except ValueError as e:
                log_error_safe(
                    logger,
                    safe_format(
                        "Invalid hex string in configuration space: {error}",
                        error=str(e)
                    ),
                    prefix="BAR",
                )
                return []
        else:
            config_bytes = config_space
            
        return cls._parse_bars_from_bytes(config_bytes)

    @classmethod
    def _parse_bars_from_bytes(cls, config_bytes: bytes) -> List[BarInfo]:
        """Parse BARs from configuration space bytes.
        
        Args:
            config_bytes: Configuration space as bytes
            
        Returns:
            List of BarInfo objects
        """
        bars = []
        
        # Validate minimum size
        min_size = ConfigSpaceConstants.BAR_BASE_OFFSET + (
            ConfigSpaceConstants.MAX_BARS * ConfigSpaceConstants.BAR_SIZE
        )
        if len(config_bytes) < min_size:
            log_warning_safe(
                logger,
                safe_format(
                    "Config space too short ({length} bytes) for BAR extraction - "
                    "need at least {min_size} bytes",
                    length=len(config_bytes),
                    min_size=min_size
                ),
                prefix="BAR",
            )
            return bars
            
        # Parse each BAR (0-5)
        i = 0
        while i < ConfigSpaceConstants.MAX_BARS:
            bar_offset = ConfigSpaceConstants.BAR_BASE_OFFSET + (
                i * ConfigSpaceConstants.BAR_SIZE
            )
            
            # Check bounds
            if bar_offset + ConfigSpaceConstants.BAR_SIZE > len(config_bytes):
                break
                
            try:
                # Read BAR value
                bar_value = struct.unpack_from("<I", config_bytes, bar_offset)[0]
                
                # Skip empty BARs
                if bar_value == 0:
                    i += 1
                    continue
                    
                # Parse BAR properties
                is_io = bool(bar_value & ConfigSpaceConstants.BAR_TYPE_MASK)
                bar_type = "io" if is_io else "memory"
                
                # Memory-specific properties
                is_64bit = False
                prefetchable = False
                if not is_io:
                    memory_type = (
                        bar_value & ConfigSpaceConstants.BAR_MEMORY_TYPE_MASK
                    )
                    is_64bit = (
                        memory_type == ConfigSpaceConstants.BAR_64BIT_TYPE
                    )
                    prefetchable = bool(
                        bar_value & ConfigSpaceConstants.BAR_PREFETCHABLE_MASK
                    )
                
                # Extract base address
                if is_io:
                    address = (
                        bar_value & ConfigSpaceConstants.BAR_IO_ADDRESS_MASK
                    )
                else:
                    address = (
                        bar_value & ConfigSpaceConstants.BAR_MEMORY_ADDRESS_MASK
                    )
                    
                # Handle 64-bit BARs
                if is_64bit and i < ConfigSpaceConstants.MAX_BARS - 1:
                    upper_offset = bar_offset + ConfigSpaceConstants.BAR_SIZE
                    if (
                        upper_offset + ConfigSpaceConstants.BAR_SIZE
                        <= len(config_bytes)
                    ):
                        upper_value = struct.unpack_from(
                            "<I", config_bytes, upper_offset
                        )[0]
                        address |= (upper_value << 32)
                        
                # Estimate size
                size = cls._estimate_bar_size(bar_value, address, is_io)
                
                # Create BarInfo object
                bar_info = BarInfo(
                    index=i,
                    bar_type=bar_type,
                    address=address,
                    size=size,
                    prefetchable=prefetchable,
                    is_64bit=is_64bit
                )
                
                bars.append(bar_info)
                log_debug_safe(
                    logger,
                    safe_format(
                        "Parsed BAR {index}: {type} @ 0x{address:016x}, "
                        "size=0x{size:x}, 64bit={is_64bit}, prefetchable={pref}",
                        index=i,
                        type=bar_type,
                        address=address,
                        size=size,
                        is_64bit=is_64bit,
                        pref=prefetchable
                    ),
                    prefix="BAR",
                )
                
                # Skip next BAR if this was 64-bit
                if is_64bit:
                    i += 2
                else:
                    i += 1
                    
            except (struct.error, IndexError) as e:
                log_warning_safe(
                    logger,
                    safe_format(
                        "Error parsing BAR {index}: {error}",
                        index=i,
                        error=str(e)
                    ),
                    prefix="BAR",
                )
                i += 1
                
        return bars

    @classmethod
    def _estimate_bar_size(cls, bar_value: int, address: int, is_io: bool) -> int:
        """Attempt to determine BAR size - returns 0 if unavailable.
        
        IMPORTANT: BAR size CANNOT be determined from the base address register
        value alone. The base address register contains the current assigned
        address, NOT the size encoding. To get the size, you need to either:
        
        1. Write all 1s to the BAR and read back (HW sizing method)
        2. Read from sysfs /sys/bus/pci/devices/{bdf}/resource
        
        This method correctly returns 0 to indicate size is unknown and must
        be discovered via sysfs or other dynamic mechanisms.
        
        Args:
            bar_value: Raw BAR register value (base address, NOT size encoding)
            address: Parsed base address
            is_io: Whether this is an I/O BAR
            
        Returns:
            0 - Size cannot be determined from base address alone
        """
        if bar_value == 0:
            return 0
            
        # BAR size cannot be determined from the base address register value
        # The value in the BAR register is the assigned base address, not the
        # size encoding (which you only get by writing all 1s and reading back)
        log_debug_safe(
            logger,
            safe_format(
                "BAR size unknown from config space - requires sysfs or HW sizing"
            ),
            prefix="BAR",
        )
        return 0


def parse_bar_info_from_config_space(
    config_space: Union[str, bytes]
) -> List[BarInfo]:
    """Parse BAR information from configuration space.
    
    This is a convenience function that uses the UnifiedBarParser.
    
    Args:
        config_space: Configuration space as hex string or bytes
        
    Returns:
        List of BarInfo objects
    """
    return UnifiedBarParser.parse_bars(config_space)


def parse_bar_info_as_dicts(config_space: Union[str, bytes]) -> List[dict]:
    """Parse BAR information and return as dictionaries for compatibility.
    
    Args:
        config_space: Configuration space as hex string or bytes
        
    Returns:
        List of dictionaries with BAR information
    """
    bars = UnifiedBarParser.parse_bars(config_space)
    
    # Convert BarInfo objects to dictionaries for compatibility
    return [
        {
            "index": bar.index,
            "bar_type": bar.bar_type,
            "address": bar.address,
            "size": bar.size,
            "is_64bit": bar.is_64bit,
            "prefetchable": bar.prefetchable,
        }
        for bar in bars
    ]
