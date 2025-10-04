#!/usr/bin/env python3
"""
Template constants - exposes centralized constants to templates.
"""
from src.device_clone.constants import (
    DEVICE_ID_GENERIC,
    DEVICE_ID_INTEL_ETH,
    DEVICE_ID_INTEL_NVME,
    DEVICE_ID_NVIDIA_GPU,
    VENDOR_ID_AMD,
    VENDOR_ID_INTEL,
    VENDOR_ID_NVIDIA,
    VENDOR_ID_REALTEK,
    get_fallback_device_id,
    get_fallback_vendor_id,
)


def get_template_constants():
    """Return constants that can be injected into template context."""
    return {
        # Template plumbing defaults
        "header_includes": [],
        # Vendor ID constants
        "VENDOR_ID_INTEL": VENDOR_ID_INTEL,
        "VENDOR_ID_NVIDIA": VENDOR_ID_NVIDIA,
        "VENDOR_ID_AMD": VENDOR_ID_AMD,
        "VENDOR_ID_REALTEK": VENDOR_ID_REALTEK,
        "FALLBACK_VENDOR_ID": get_fallback_vendor_id(),
        # Device ID constants
        "DEVICE_ID_GENERIC": DEVICE_ID_GENERIC,
        "DEVICE_ID_INTEL_ETH": DEVICE_ID_INTEL_ETH,
        "DEVICE_ID_INTEL_NVME": DEVICE_ID_INTEL_NVME,
        "DEVICE_ID_NVIDIA_GPU": DEVICE_ID_NVIDIA_GPU,
        "FALLBACK_DEVICE_ID": get_fallback_device_id(),
        # Hex string formats (for easy use in templates)
        "VENDOR_ID_INTEL_HEX": f"0x{VENDOR_ID_INTEL:04x}",
        "VENDOR_ID_NVIDIA_HEX": f"0x{VENDOR_ID_NVIDIA:04x}",
        "VENDOR_ID_AMD_HEX": f"0x{VENDOR_ID_AMD:04x}",
        "VENDOR_ID_REALTEK_HEX": f"0x{VENDOR_ID_REALTEK:04x}",
        "FALLBACK_VENDOR_ID_HEX": f"0x{get_fallback_vendor_id():04x}",
        "FALLBACK_DEVICE_ID_HEX": f"0x{get_fallback_device_id():04x}",
    }
