#!/usr/bin/env python3
"""
PCI Capability Utility Functions

This module provides utility functions for PCI capability analysis,
categorization, and pruning operations.
"""

import logging
from typing import Dict

# Import local modules
from .constants import ( 
    EXT_CAP_SIZE_ACCESS_CONTROL_SERVICES,
    EXT_CAP_SIZE_ADVANCED_ERROR_REPORTING, EXT_CAP_SIZE_DEFAULT,
    EXT_CAP_SIZE_DOWNSTREAM_PORT_CONTAINMENT, EXT_CAP_SIZE_RESIZABLE_BAR,
    EXTENDED_CAPABILITY_NAMES, PCI_EXT_CAP_ALIGNMENT, PCI_EXT_CAP_START,
    PCI_EXT_CONFIG_SPACE_END, STANDARD_CAPABILITY_NAMES, STD_CAP_SIZE_DEFAULT,
    STD_CAP_SIZE_MSI, STD_CAP_SIZE_MSI_X, STD_CAP_SIZE_PCI_EXPRESS,
    STD_CAP_SIZE_POWER_MANAGEMENT, TWO_BYTE_HEADER_CAPABILITIES)

from .types import (CapabilityInfo, CapabilityType, EmulationCategory,
                    PCICapabilityID, PCIExtCapabilityID, PruningAction)

# Global logger for this module
module_logger = logging.getLogger(__name__)

# Project string utilities (no fallbacks – central source of truth)
from src.string_utils import log_info_safe, safe_format

logger = logging.getLogger(__name__)


def is_two_byte_header_capability(cap_id: int) -> bool:
    """
    Check if a standard capability has a 2-byte header instead of 1-byte.

    Args:
        cap_id: Standard capability ID

    Returns:
        True if capability has 2-byte header, False for 1-byte header
    """
    return cap_id in TWO_BYTE_HEADER_CAPABILITIES


def categorize_capability(cap_info: CapabilityInfo) -> EmulationCategory:
    """
    Categorize a single capability based on emulation feasibility.

    Args:
        cap_info: CapabilityInfo object to categorize

    Returns:
        EmulationCategory for the capability
    """
    cap_id = cap_info.cap_id
    cap_type = cap_info.cap_type

    # Default category
    category = EmulationCategory.UNSUPPORTED

    if cap_type == CapabilityType.STANDARD:
        # Categorize standard capabilities
        if cap_id == PCICapabilityID.POWER_MANAGEMENT.value:
            category = EmulationCategory.PARTIALLY_SUPPORTED
        elif cap_id == PCICapabilityID.MSI.value:
            category = EmulationCategory.FULLY_SUPPORTED
        elif cap_id == PCICapabilityID.MSI_X.value:
            category = EmulationCategory.FULLY_SUPPORTED
        elif cap_id == PCICapabilityID.PCI_EXPRESS.value:
            category = EmulationCategory.PARTIALLY_SUPPORTED
    elif cap_type == CapabilityType.EXTENDED:
        # Categorize extended capabilities
        if cap_id == PCIExtCapabilityID.ADVANCED_ERROR_REPORTING.value:
            category = EmulationCategory.PARTIALLY_SUPPORTED
        elif cap_id == PCIExtCapabilityID.ACCESS_CONTROL_SERVICES.value:
            # ACS - partially supported as requested
            category = EmulationCategory.PARTIALLY_SUPPORTED
        elif cap_id == PCIExtCapabilityID.DOWNSTREAM_PORT_CONTAINMENT.value:
            # DPC - partially supported as requested
            category = EmulationCategory.PARTIALLY_SUPPORTED
        elif cap_id == PCIExtCapabilityID.RESIZABLE_BAR.value:
            category = EmulationCategory.PARTIALLY_SUPPORTED

    return category


def categorize_capabilities(
    capabilities: Dict[int, CapabilityInfo],
) -> Dict[int, EmulationCategory]:
    """
    Categorize multiple capabilities based on emulation feasibility.

    Args:
        capabilities: Dictionary mapping offsets to CapabilityInfo objects

    Returns:
        Dictionary mapping capability offsets to emulation categories
    """
    categories = {}

    for offset, cap_info in capabilities.items():
        categories[offset] = categorize_capability(cap_info)

    return categories


def determine_pruning_action(category: EmulationCategory) -> PruningAction:
    """
    Determine pruning action for a capability based on its category.

    Args:
        category: EmulationCategory for the capability

    Returns:
        PruningAction to take for the capability
    """
    # Map categories to actions
    action_map = {
        EmulationCategory.FULLY_SUPPORTED: PruningAction.KEEP,
        EmulationCategory.PARTIALLY_SUPPORTED: PruningAction.MODIFY,
        EmulationCategory.UNSUPPORTED: PruningAction.REMOVE,
        EmulationCategory.CRITICAL: PruningAction.KEEP,
    }

    # Default to remove for unknown categories
    return action_map.get(category, PruningAction.REMOVE)


def determine_pruning_actions(
    capabilities: Dict[int, CapabilityInfo], categories: Dict[int, EmulationCategory]
) -> Dict[int, PruningAction]:
    """
    Determine pruning actions for multiple capabilities based on their categories.

    Args:
        capabilities: Dictionary mapping offsets to CapabilityInfo objects
        categories: Dictionary mapping capability offsets to emulation categories

    Returns:
        Dictionary mapping capability offsets to pruning actions
    """
    actions = {}

    for offset in capabilities:
        category = categories.get(offset, EmulationCategory.UNSUPPORTED)
        actions[offset] = determine_pruning_action(category)

    return actions


def get_capability_name(cap_id: int, cap_type: CapabilityType) -> str:
    """
    Get the human-readable name for a capability.

    Args:
        cap_id: Capability ID
        cap_type: Type of capability (standard or extended)

    Returns:
        Human-readable capability name
    """
    if cap_type == CapabilityType.STANDARD:
        return STANDARD_CAPABILITY_NAMES.get(
            cap_id,
            safe_format(
                "Unknown (0x{cap_id:02x})",
                cap_id=cap_id,
            ),
        )

    name = EXTENDED_CAPABILITY_NAMES.get(
        cap_id,
        safe_format(
            "Unknown Extended (0x{cap_id:04x})",
            cap_id=cap_id,
        ),
    )
    if cap_id not in EXTENDED_CAPABILITY_NAMES and cap_id <= 0x0029:
        log_info_safe(
            module_logger,
            "Unknown extended capability ID 0x{cap_id:04x} encountered",
            prefix="PCI_CAP",
            cap_id=cap_id,
        )
    return name


def validate_capability_offset(offset: int, cap_type: CapabilityType) -> bool:
    """
    Validate that a capability offset is reasonable for its type.

    Args:
        offset: Capability offset to validate
        cap_type: Type of capability (standard or extended)

    Returns:
        True if offset is valid, False otherwise
    """
    if cap_type == CapabilityType.STANDARD:
        # Standard capabilities should be in the first 256 bytes
        # and typically start after the standard header (0x40+)
        return 0x40 <= offset < 0x100

    # Extended capabilities start at 0x100 and should be DWORD aligned
    return (
        PCI_EXT_CAP_START <= offset < PCI_EXT_CONFIG_SPACE_END
        and offset & PCI_EXT_CAP_ALIGNMENT == 0
    )


def format_capability_info(cap_info: CapabilityInfo) -> str:
    """
    Format capability information for display.

    Args:
        cap_info: CapabilityInfo object to forma

    Returns:
        Formatted string representation of the capability
    """
    if cap_info.cap_type == CapabilityType.STANDARD:
        return safe_format(
            "Standard Cap @ 0x{offset:02x}: {name} (ID: 0x{cap_id:02x})",
            offset=cap_info.offset,
            name=cap_info.name,
            cap_id=cap_info.cap_id,
        )

    return safe_format(
        "Extended Cap @ 0x{offset:03x}: {name} (ID: 0x{cap_id:04x}, Ver: {version})",
        offset=cap_info.offset,
        name=cap_info.name,
        cap_id=cap_info.cap_id,
        version=cap_info.version,
    )


def get_capability_size_estimate(cap_info: CapabilityInfo) -> int:
    """
    Estimate the size of a capability structure.

    This provides rough estimates for common capabilities to help with
    bounds checking and space calculations.

    Args:
        cap_info: CapabilityInfo objec

    Returns:
        Estimated size in bytes
    """
    cap_id = cap_info.cap_id
    cap_type = cap_info.cap_type

    # Default sizes
    standard_default = STD_CAP_SIZE_DEFAULT
    extended_default = EXT_CAP_SIZE_DEFAULT

    if cap_type == CapabilityType.STANDARD:
        # Standard capability size map
        std_size_map = {
            PCICapabilityID.POWER_MANAGEMENT.value: STD_CAP_SIZE_POWER_MANAGEMENT,
            PCICapabilityID.MSI.value: STD_CAP_SIZE_MSI,
            PCICapabilityID.MSI_X.value: STD_CAP_SIZE_MSI_X,
            PCICapabilityID.PCI_EXPRESS.value: STD_CAP_SIZE_PCI_EXPRESS,
        }
        return std_size_map.get(cap_id, standard_default)

    # Extended capability size map
    ext_size_map = {
        PCIExtCapabilityID.ADVANCED_ERROR_REPORTING.value: EXT_CAP_SIZE_ADVANCED_ERROR_REPORTING,
        PCIExtCapabilityID.ACCESS_CONTROL_SERVICES.value: EXT_CAP_SIZE_ACCESS_CONTROL_SERVICES,
        PCIExtCapabilityID.DOWNSTREAM_PORT_CONTAINMENT.value: EXT_CAP_SIZE_DOWNSTREAM_PORT_CONTAINMENT,
        PCIExtCapabilityID.RESIZABLE_BAR.value: EXT_CAP_SIZE_RESIZABLE_BAR,
    }
    return ext_size_map.get(cap_id, extended_default)
