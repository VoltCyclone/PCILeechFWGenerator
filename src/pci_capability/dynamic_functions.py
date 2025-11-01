#!/usr/bin/env python3
"""
Simulated Function Capabilities

This module provides a unified interface for simulating realistic PCIe device
capabilities based on vendor/device IDs. It analyzes device identifiers to
determine the appropriate device type (network, media, storage, USB) and
generates corresponding simulated capabilities.

The module uses a data-driven approach with vendor-specific mappings and
generic heuristics to classify devices accurately. It integrates with the
existing templating and logging infrastructure for production-ready
capability simulation.
"""

import logging

from enum import Enum

from typing import Any, Dict, Optional

from ..string_utils import (log_debug_safe, log_error_safe,
                            log_warning_safe, safe_format)

from .media_functions import create_media_function_capabilities

from .network_functions import create_network_function_capabilities

from .storage_functions import create_storage_function_capabilities

from .usb_functions import create_usb_function_capabilities

logger = logging.getLogger(__name__)


class DeviceType(str, Enum):
    """Enumeration of supported device types."""

    NETWORK = "network"
    MEDIA = "media"
    STORAGE = "storage"
    USB = "usb"
    UNKNOWN = "unknown"


class VendorID(int, Enum):
    """Common PCI vendor IDs."""

    INTEL = 0x8086
    REALTEK = 0x10EC
    BROADCOM = 0x14E4
    QUALCOMM_ATHEROS = 0x17CB
    NVIDIA = 0x10DE
    AMD_ATI = 0x1002
    VIA_TECH = 0x1106
    VIA_AUDIO = 0x1412
    C_MEDIA = 0xC003
    MARVELL = 0x1B4B
    LSI_BROADCOM = 0x1000
    SAMSUNG = 0x144D
    SANDISK = 0x15B7
    MICRON = 0x1344
    NEC = 0x1033
    RENESAS = 0x1912
    ETRON = 0x15E7


class PCIClassCode:
    """PCI class code constants."""

    MASS_STORAGE = 0x01
    NETWORK = 0x02
    MULTIMEDIA = 0x04
    SERIAL_BUS = 0x0C

    # Serial bus subclasses
    USB_CONTROLLER = 0x03
    OTHER_SERIAL = 0x80


# Vendor-specific device mappings using sets for O(1) lookup
VENDOR_DEVICE_MAPPINGS = {
    VendorID.INTEL: {
        DeviceType.NETWORK: {0x15, 0x16, 0x17, 0x24, 0x25, 0x27, 0x51},
        DeviceType.MEDIA: {
            0x0C,
            0x0E,
            0x1C,
            0x1E,
            0x1F,
            0x22,
            0x29,
        },  # Removed overlaps
        DeviceType.STORAGE: {0x02, 0x06, 0x28, 0x31},  # Removed overlaps
        DeviceType.USB: {0x0F, 0x34, 0x43, 0x54},  # Kept unique USB ranges
    },
    VendorID.BROADCOM: {
        DeviceType.NETWORK: {0x16, 0x17},
    },
}

# Vendors known for specific device types
VENDOR_SPECIALIZATIONS = {
    DeviceType.NETWORK: {VendorID.QUALCOMM_ATHEROS},
    DeviceType.MEDIA: {VendorID.VIA_AUDIO, VendorID.C_MEDIA},
    DeviceType.STORAGE: {
        VendorID.MARVELL,
        VendorID.LSI_BROADCOM,
        VendorID.SAMSUNG,
        VendorID.SANDISK,
        VendorID.MICRON,
    },
    DeviceType.USB: {VendorID.NEC, VendorID.RENESAS, VendorID.ETRON},
}

# Pre-computed int conversions for performance (avoid repeated enum conversion)
_VENDOR_SPECIALIZATIONS_INT = {
    device_type: {int(v) for v in vendor_set}
    for device_type, vendor_set in VENDOR_SPECIALIZATIONS.items()
}

_VENDOR_DEVICE_MAPPINGS_INT = {
    int(k): v for k, v in VENDOR_DEVICE_MAPPINGS.items()
}

_VENDOR_CLASSIFIERS = {
    int(VendorID.INTEL): "_classify_intel_device",
    int(VendorID.REALTEK): "_classify_realtek_device",
    int(VendorID.NVIDIA): "_classify_nvidia_device",
    int(VendorID.AMD_ATI): "_classify_amd_device",
    int(VendorID.VIA_TECH): "_classify_via_device",
}

# Valid device function type strings
_VALID_FUNCTION_TYPES = {dt.value for dt in DeviceType if dt != DeviceType.UNKNOWN}


def _classify_by_class_code(class_code: int) -> Optional[DeviceType]:
    """
    Classify device type based on PCI class code.

    Args:
        class_code: PCI class code

    Returns:
        Device type or None if not determinable from class code
    """
    class_major = (class_code >> 16) & 0xFF
    class_minor = (class_code >> 8) & 0xFF

    if class_major == PCIClassCode.NETWORK:
        return DeviceType.NETWORK
    if class_major == PCIClassCode.MULTIMEDIA:
        return DeviceType.MEDIA
    if class_major == PCIClassCode.MASS_STORAGE:
        return DeviceType.STORAGE
    if class_major == PCIClassCode.SERIAL_BUS:
        if class_minor == PCIClassCode.USB_CONTROLLER:
            return DeviceType.USB
        if class_minor == PCIClassCode.OTHER_SERIAL:
            return DeviceType.NETWORK

    return None


def _classify_intel_device(device_upper: int, device_lower: int) -> DeviceType:
    """Classify Intel devices based on device ID patterns."""
    # Intel-specific heuristics for unknown devices
    # (Known device ranges are checked by _classify_by_vendor_patterns)
    if device_upper >= 0x50:
        return DeviceType.STORAGE
    if device_upper >= 0x20:
        return DeviceType.NETWORK

    return DeviceType.UNKNOWN


def _classify_realtek_device(device_upper: int, device_lower: int) -> DeviceType:
    """Classify Realtek devices based on device ID patterns."""
    if device_upper == 0x81:
        return DeviceType.NETWORK
    if device_upper == 0x52:
        return DeviceType.STORAGE
    if device_lower < 0x80:
        return DeviceType.MEDIA

    return DeviceType.UNKNOWN


def _classify_nvidia_device(device_upper: int, device_lower: int) -> DeviceType:
    """Classify NVIDIA devices based on device ID patterns."""
    if device_upper >= 0x0A:
        return DeviceType.MEDIA if device_upper >= 0x40 else DeviceType.USB
    return DeviceType.MEDIA


def _classify_amd_device(device_upper: int, device_lower: int) -> DeviceType:
    """Classify AMD/ATI devices based on device ID patterns."""
    if device_upper >= 0x43:
        return DeviceType.USB if device_lower < 0x50 else DeviceType.MEDIA
    return DeviceType.MEDIA


def _classify_via_device(device_upper: int, device_lower: int) -> DeviceType:
    """Classify VIA devices based on device ID patterns."""
    if device_upper >= 0x31:
        return DeviceType.USB
    return DeviceType.STORAGE


def _classify_by_vendor_patterns(
    vendor_id: int, device_upper: int, device_lower: int
) -> Optional[DeviceType]:
    """
    Classify device based on vendor-specific patterns.

    Args:
        vendor_id: PCI vendor ID
        device_upper: Upper byte of device ID
        device_lower: Lower byte of device ID

    Returns:
        Device type or None if vendor not recognized
    """
    # Check vendor specializations first (pre-computed int conversions)
    for device_type, vendor_set in _VENDOR_SPECIALIZATIONS_INT.items():
        if vendor_id in vendor_set:
            return device_type

    # Check mapped vendors BEFORE vendor-specific classifiers
    # (Explicit mappings are more reliable than heuristics)
    if vendor_id in _VENDOR_DEVICE_MAPPINGS_INT:
        for device_type, device_set in _VENDOR_DEVICE_MAPPINGS_INT[vendor_id].items():
            if device_upper in device_set:
                return device_type

    # Vendor-specific classification functions (fallback heuristics)
    classifier_name = _VENDOR_CLASSIFIERS.get(vendor_id)
    if classifier_name:
        # Get the classifier function from globals
        classifier = globals()[classifier_name]
        return classifier(device_upper, device_lower)

    return None


def _classify_by_generic_patterns(device_upper: int, device_lower: int) -> DeviceType:
    """
    Apply generic heuristics for unknown vendors.

    Args:
        device_upper: Upper byte of device ID
        device_lower: Lower byte of device ID

    Returns:
        Best guess device type based on patterns
    """
    if device_upper >= 0x80:
        # Very high device IDs often indicate storage or USB
        return DeviceType.STORAGE if device_lower >= 0xA0 else DeviceType.USB
    if device_upper >= 0x50:
        return DeviceType.STORAGE
    if device_upper >= 0x15:
        return DeviceType.NETWORK
    if device_lower < 0x50:
        return DeviceType.MEDIA

    # Default to generic
    return DeviceType.UNKNOWN


def analyze_device_function_type(
    vendor_id: int, device_id: int, class_code: Optional[int] = None
) -> str:
    """
    Analyze device function type based on vendor/device ID and optional class code.

    This function uses a hierarchical approach:
    1. First tries to classify by PCI class code (most reliable)
    2. Then tries vendor-specific patterns
    3. Finally falls back to generic heuristics

    Args:
        vendor_id: PCI vendor ID (16-bit value)
        device_id: PCI device ID (16-bit value)
        class_code: Optional PCI class code (24-bit value)

    Returns:
        Device function type string ('network', 'media', 'storage', 'usb', 'unknown')

    Raises:
        ValueError: If vendor_id or device_id are out of valid range
    """
    # Validate inputs
    if not 0 <= vendor_id <= 0xFFFF:
        raise ValueError(f"Invalid vendor_id: {vendor_id:#x} (must be 16-bit)")
    if not 0 <= device_id <= 0xFFFF:
        raise ValueError(f"Invalid device_id: {device_id:#x} (must be 16-bit)")
    if class_code is not None and not 0 <= class_code <= 0xFFFFFF:
        raise ValueError(f"Invalid class_code: {class_code:#x} (must be 24-bit)")

    # Try classification by class code first (most reliable)
    if class_code is not None:
        device_type = _classify_by_class_code(class_code)
        if device_type:
            log_debug_safe(
                logger,
                safe_format(
                    "Classified device {vendor_id:04x}:{device_id:04x} as "
                    "{device_type} by class code {class_code:06x}",
                    vendor_id=vendor_id,
                    device_id=device_id,
                    device_type=device_type,
                    class_code=class_code,
                ),
                prefix="CLASS",
            )
            return device_type.value

    # Extract device ID components
    device_upper = (device_id >> 8) & 0xFF
    device_lower = device_id & 0xFF

    # Try vendor-specific classification
    device_type = _classify_by_vendor_patterns(vendor_id, device_upper, device_lower)
    if device_type:
        log_debug_safe(
            logger,
            safe_format(
                "Classified device {vendor_id:04x}:{device_id:04x} as {device_type} by vendor patterns",
                vendor_id=vendor_id,
                device_id=device_id,
                device_type=device_type,
            ),
            prefix="CLASS",
        )
        return device_type.value

    # Fall back to generic patterns
    device_type = _classify_by_generic_patterns(device_upper, device_lower)

    if device_type == DeviceType.UNKNOWN:
        log_warning_safe(
            logger,
            safe_format(
                "Could not determine device type for {vendor_id:04x}:{device_id:04x}, returning 'unknown'",
                vendor_id=vendor_id,
                device_id=device_id,
            ),
            prefix="CLASS",
        )
    else:
        log_debug_safe(
            logger,
            safe_format(
                "Classified device {vendor_id:04x}:{device_id:04x} as {device_type} by generic patterns",
                vendor_id=vendor_id,
                device_id=device_id,
                device_type=device_type,
            ),
            prefix="CLASS",
        )

    return device_type.value


# Keep the original function signature for backward compatibility
def create_simulated_device_capabilities(
    vendor_id: int,
    device_id: int,
    class_code: Optional[int] = None,
    function_hint: Optional[str] = None,
) -> Dict[str, Any]:
    """
    Create simulated device capabilities based on vendor/device IDs.

    This is the main entry point for simulating realistic PCIe device capabilities.
    It analyzes the provided vendor/device IDs and generates appropriate simulated
    capabilities using vendor-specific patterns and generic heuristics.

    Args:
        vendor_id: PCI vendor ID (16-bit value)
        device_id: PCI device ID (16-bit value)
        class_code: Optional PCI class code (24-bit value) for classification hints
        function_hint: Optional function type hint ('network', 'media', 'storage', 'usb')

    Returns:
        Complete device configuration dictionary with simulated capabilities, BARs, and features

    Raises:
        ValueError: If vendor_id or device_id are out of valid range
    """
    try:
        # Determine function type
        if function_hint and function_hint in _VALID_FUNCTION_TYPES:
            function_type = function_hint
            log_debug_safe(
                logger,
                safe_format(
                    "Using provided function hint: {hint} for device "
                    "{vendor_id:04x}:{device_id:04x}",
                    hint=function_hint,
                    vendor_id=vendor_id,
                    device_id=device_id,
                ),
                prefix="CLASS",
            )
        else:
            function_type = analyze_device_function_type(
                vendor_id, device_id, class_code
            )
            log_debug_safe(
                logger,
                safe_format(
                    "Analyzed device {vendor_id:04x}:{device_id:04x} as function type: {function_type}",
                    vendor_id=vendor_id,
                    device_id=device_id,
                    function_type=function_type,
                ),
                prefix="CLASS",
            )

        # Generate capabilities based on function type
        if function_type == "network":
            config = create_network_function_capabilities(vendor_id, device_id)
        elif function_type == "media":
            config = create_media_function_capabilities(vendor_id, device_id)
        elif function_type == "storage":
            config = create_storage_function_capabilities(vendor_id, device_id)
        elif function_type == "usb":
            config = create_usb_function_capabilities(vendor_id, device_id)
        else:
            # Create basic generic device config for unknown types
            config = _create_generic_device_capabilities(
                vendor_id, device_id, class_code
            )
            log_warning_safe(
                logger,
                safe_format(
                    "Unknown function type for device {vendor_id:04x}:{device_id:04x}, using generic capabilities",
                    vendor_id=vendor_id,
                    device_id=device_id,
                ),
                prefix="CLASS",
            )

        # Add metadata
        config["metadata"] = {
            "vendor_id": vendor_id,
            "device_id": device_id,
            "class_code": class_code,
            "function_type": function_type,
        }

        # Ensure bar_config is present for template compatibility
        if (
            "bars" in config
            and isinstance(config["bars"], list)
            and len(config["bars"]) > 0
        ):
            config["bar_config"] = {"bars": config["bars"]}

        return config

    except Exception as e:
        log_error_safe(
            logger,
            safe_format(
                "Error creating capabilities for {vid:04x}:{did:04x}: {error}",
                vid=vendor_id,
                did=device_id,
                error=str(e),
            ),
            prefix="DEVICE_CAPABILITY",
        )
        # Return minimal safe configuration
        return _create_generic_device_capabilities(vendor_id, device_id, class_code)


def _create_generic_device_capabilities(
    vendor_id: int, device_id: int, class_code: Optional[int] = None
) -> Dict[str, Any]:
    """Create a minimal generic device configuration."""
    return {
        "vendor_id": vendor_id,
        "device_id": device_id,
        "class_code": class_code or 0xFF0000,  # Unclassified device
        "capabilities": [],
        "bars": [
            {
                "type": "memory",
                "size": 0x1000,  # Default minimal BAR size
                "prefetchable": False,
                "is_64bit": False,
            }
        ],
        "features": {
            "msi": False,
            "msix": False,
            "pcie": True,
        },
    }


# Backward compatibility alias
create_dynamic_device_capabilities = create_simulated_device_capabilities
