#!/usr/bin/env python3
"""
CLI Device Prioritizer

Simple prioritization system that highlights devices likely to be successful
for PCILeech firmware generation based on device names and types.
"""

from typing import Dict, List, Tuple


class CLIDevicePrioritizer:
    """Simple device prioritizer for CLI device selection."""

    def __init__(self):
        """Initialize the prioritizer with success patterns."""
        # Keywords that indicate good PCILeech candidates
        self.priority_keywords = [
            "network",
            "ethernet",
            "media",
            "storage",
            "scsi",
            "sata",
            "nvme",
            "gigabit",
            "wireless",
            "wifi",
            "lan",
            "audio",
            "sound",
        ]

    def is_priority_device(self, device: Dict[str, str]) -> bool:
        """Check if device should be prioritized (highlighted in green)."""
        device_text = device["pretty"].lower()

        # Check for priority keywords
        for keyword in self.priority_keywords:
            if keyword in device_text:
                return True

        # Check device class for network/storage controllers
        device_class = device.get("class", "").lower()
        # Network controllers (02xx) or Storage controllers (01xx)
        if device_class.startswith("02") or device_class.startswith("01"):
            return True

        return False

    def sort_devices_by_priority(
        self, devices: List[Dict[str, str]]
    ) -> List[Dict[str, str]]:
        """Sort devices with priority devices first."""
        return sorted(
            devices,
            key=lambda d: (
                -int(
                    self.is_priority_device(d)
                ),  # Priority devices first (negative for descending)
                d["pretty"],  # Then alphabetically
            ),
        )

    def get_priority_devices(
        self, devices: List[Dict[str, str]]
    ) -> Tuple[List[Dict[str, str]], List[Dict[str, str]]]:
        """Split devices into priority and regular lists."""
        priority_devices = []
        regular_devices = []

        for device in devices:
            if self.is_priority_device(device):
                priority_devices.append(device)
            else:
                regular_devices.append(device)

        return priority_devices, regular_devices


def format_device_with_priority(
    device: Dict[str, str], prioritizer: CLIDevicePrioritizer
) -> str:
    """Format device string with priority highlighting."""
    device_str = device["pretty"]

    if prioritizer.is_priority_device(device):
        # Add green highlighting for priority devices
        return f"\033[92m✓ {device_str}\033[0m"  # Green text with checkmark
    else:
        return f"  {device_str}"  # Regular text with indent
