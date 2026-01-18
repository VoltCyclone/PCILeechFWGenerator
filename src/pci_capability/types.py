#!/usr/bin/env python3
"""
PCI Capability Type Definitions

This module contains all type definitions, enums, and data structures
used throughout the PCI capability analysis system.
"""

from enum import Enum, auto
from typing import NamedTuple


class PatchInfo(NamedTuple):
    """
    Information about a capability patch operation.

    Enhanced from the original to support binary format operations
    and more detailed patch tracking.
    """

    offset: int
    action: str
    before_bytes: str
    after_bytes: str


class PCICapabilityID(Enum):
    """Standard PCI Capability IDs as defined in the PCI specification."""

    POWER_MANAGEMENT = 0x01
    AGP = 0x02
    VPD = 0x03
    SLOT_ID = 0x04
    MSI = 0x05
    COMPACT_PCI_HOT_SWAP = 0x06
    PCI_X = 0x07
    HYPERTRANSPORT = 0x08
    VENDOR_SPECIFIC = 0x09
    DEBUG_PORT = 0x0A
    COMPACT_PCI_CRC = 0x0B
    PCI_HOT_PLUG = 0x0C
    PCI_BRIDGE_SUBSYSTEM_VID = 0x0D
    AGP_8X = 0x0E
    SECURE_DEVICE = 0x0F
    PCI_EXPRESS = 0x10
    MSI_X = 0x11
    SATA_DATA_INDEX_CONF = 0x12
    AF = 0x13  # Advanced Features


class PCIExtCapabilityID(Enum):
    """PCI Express Extended Capability IDs as defined in the PCIe specification."""

    ADVANCED_ERROR_REPORTING = 0x0001
    VIRTUAL_CHANNEL = 0x0002
    DEVICE_SERIAL_NUMBER = 0x0003
    POWER_BUDGETING = 0x0004
    ROOT_COMPLEX_LINK_DECLARATION = 0x0005
    ROOT_COMPLEX_INTERNAL_LINK_CONTROL = 0x0006
    ROOT_COMPLEX_EVENT_COLLECTOR_ENDPOINT_ASSOCIATION = 0x0007
    MULTI_FUNCTION_VIRTUAL_CHANNEL = 0x0008
    VIRTUAL_CHANNEL_MFVC = 0x0009
    ROOT_COMPLEX_REGISTER_BLOCK = 0x000A
    VENDOR_SPECIFIC_EXTENDED = 0x000B
    CONFIG_ACCESS_CORRELATION = 0x000C
    ACCESS_CONTROL_SERVICES = 0x000D
    ALTERNATIVE_ROUTING_ID_INTERPRETATION = 0x000E
    ADDRESS_TRANSLATION_SERVICES = 0x000F
    SINGLE_ROOT_IO_VIRTUALIZATION = 0x0010
    MULTI_ROOT_IO_VIRTUALIZATION = 0x0011
    MULTICAST = 0x0012
    PAGE_REQUEST = 0x0013
    RESERVED_FOR_AMD = 0x0014
    RESIZABLE_BAR = 0x0015
    DYNAMIC_POWER_ALLOCATION = 0x0016
    TPH_REQUESTER = 0x0017
    LATENCY_TOLERANCE_REPORTING = 0x0018
    SECONDARY_PCI_EXPRESS = 0x0019
    PROTOCOL_MULTIPLEXING = 0x001A
    PROCESS_ADDRESS_SPACE_ID = 0x001B
    LN_REQUESTER = 0x001C
    DOWNSTREAM_PORT_CONTAINMENT = 0x001D
    L1_PM_SUBSTATES = 0x001E
    PRECISION_TIME_MEASUREMENT = 0x001F
    PCI_EXPRESS_OVER_MPHY = 0x0020
    FRS_QUEUEING = 0x0021
    READINESS_TIME_REPORTING = 0x0022
    DESIGNATED_VENDOR_SPECIFIC = 0x0023
    VF_RESIZABLE_BAR = 0x0024
    DATA_LINK_FEATURE = 0x0025
    PHYSICAL_LAYER_16_0_GT_S = 0x0026
    LANE_MARGINING_AT_RECEIVER = 0x0027
    HIERARCHY_ID = 0x0028
    NATIVE_PCIE_ENCLOSURE_MANAGEMENT = 0x0029


class EmulationCategory(Enum):
    """Categories for capability emulation feasibility."""

    FULLY_SUPPORTED = auto()  # Can be fully emulated
    PARTIALLY_SUPPORTED = auto()  # Can be partially emulated with modifications
    UNSUPPORTED = auto()  # Cannot be emulated, should be removed
    CRITICAL = auto()  # Critical for operation, must be preserved


class PruningAction(Enum):
    """Actions to take when pruning capabilities."""

    KEEP = auto()  # Keep the capability as-is
    MODIFY = auto()  # Modify specific fields in the capability
    REMOVE = auto()  # Remove the capability entirely


class CapabilityType(Enum):
    """Type of PCI capability."""

    STANDARD = "standard"
    EXTENDED = "extended"


class CapabilityInfo(NamedTuple):
    """
    Information about a discovered capability.

    This provides a standardized way to represent capability information
    regardless of whether it's a standard or extended capability.
    """

    offset: int
    cap_id: int
    cap_type: CapabilityType
    next_ptr: int
    name: str
    version: int = 0  # Only used for extended capabilities
