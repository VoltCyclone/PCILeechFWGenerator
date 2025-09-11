#!/usr/bin/env python3
"""
Test suite for CLI Device Prioritizer

Tests the CLIDevicePrioritizer class and format_device_with_priority function.
"""

from unittest.mock import MagicMock

import pytest

from src.cli.device_prioritizer import (CLIDevicePrioritizer,
                                        format_device_with_priority)


class TestCLIDevicePrioritizer:
    """Test cases for CLIDevicePrioritizer class."""

    def setup_method(self):
        """Set up test fixtures."""
        self.prioritizer = CLIDevicePrioritizer()

    def test_init(self):
        """Test initialization of CLIDevicePrioritizer."""
        prioritizer = CLIDevicePrioritizer()
        assert hasattr(prioritizer, "priority_keywords")
        assert isinstance(prioritizer.priority_keywords, list)
        assert "network" in prioritizer.priority_keywords
        assert "ethernet" in prioritizer.priority_keywords
        assert "storage" in prioritizer.priority_keywords

    def test_is_priority_device_with_keyword_match(self):
        """Test priority detection with keyword matches."""
        # Test network device
        network_device = {"pretty": "Intel Ethernet Controller I219-V", "class": "0200"}
        assert self.prioritizer.is_priority_device(network_device)

        # Test storage device
        storage_device = {"pretty": "Samsung NVMe SSD Controller", "class": "0108"}
        assert self.prioritizer.is_priority_device(storage_device)

        # Test wireless device
        wireless_device = {
            "pretty": "Broadcom Wireless Network Adapter",
            "class": "0280",
        }
        assert self.prioritizer.is_priority_device(wireless_device)

    def test_is_priority_device_with_class_match(self):
        """Test priority detection with device class matches."""
        # Network controller class
        network_class_device = {"pretty": "Unknown Device", "class": "0200"}
        assert self.prioritizer.is_priority_device(network_class_device)

        # Storage controller class
        storage_class_device = {"pretty": "Generic Controller", "class": "0101"}
        assert self.prioritizer.is_priority_device(storage_class_device)

    def test_is_priority_device_no_match(self):
        """Test priority detection for non-priority devices."""
        # Graphics device
        graphics_device = {"pretty": "NVIDIA GeForce RTX 3080", "class": "0300"}
        assert not self.prioritizer.is_priority_device(graphics_device)

        # USB controller
        usb_device = {"pretty": "Intel USB 3.0 Controller", "class": "0c03"}
        assert not self.prioritizer.is_priority_device(usb_device)

        # PCI bridge
        bridge_device = {"pretty": "PCI Express Bridge", "class": "0604"}
        assert not self.prioritizer.is_priority_device(bridge_device)

    def test_is_priority_device_case_insensitive(self):
        """Test that keyword matching is case insensitive."""
        # Mixed case device name
        mixed_case_device = {"pretty": "Broadcom ETHERNET Controller", "class": "0200"}
        assert self.prioritizer.is_priority_device(mixed_case_device)

        # Upper case device name
        upper_case_device = {"pretty": "INTEL WIRELESS ADAPTER", "class": "0280"}
        assert self.prioritizer.is_priority_device(upper_case_device)

    def test_sort_devices_by_priority(self):
        """Test sorting devices with priority devices first."""
        devices = [
            {"pretty": "NVIDIA GeForce RTX 3080", "class": "0300"},  # Non-priority
            {"pretty": "Intel Ethernet Controller", "class": "0200"},  # Priority
            {"pretty": "Samsung NVMe SSD", "class": "0108"},  # Priority
            {"pretty": "Intel USB Controller", "class": "0c03"},  # Non-priority
        ]

        sorted_devices = self.prioritizer.sort_devices_by_priority(devices)

        # Priority devices should come first
        assert sorted_devices[0]["pretty"] == "Intel Ethernet Controller"
        assert sorted_devices[1]["pretty"] == "Samsung NVMe SSD"
        assert sorted_devices[2]["pretty"] == "Intel USB Controller"
        assert sorted_devices[3]["pretty"] == "NVIDIA GeForce RTX 3080"

    def test_sort_devices_by_priority_alphabetical(self):
        """Test that priority devices are sorted alphabetically."""
        devices = [
            {"pretty": "Broadcom Wireless", "class": "0280"},  # Priority
            {"pretty": "Intel Ethernet", "class": "0200"},  # Priority
            {"pretty": "Samsung NVMe", "class": "0108"},  # Priority
        ]

        sorted_devices = self.prioritizer.sort_devices_by_priority(devices)

        # Should be sorted alphabetically within priority group
        assert sorted_devices[0]["pretty"] == "Broadcom Wireless"
        assert sorted_devices[1]["pretty"] == "Intel Ethernet"
        assert sorted_devices[2]["pretty"] == "Samsung NVMe"

    def test_get_priority_devices_split(self):
        """Test splitting devices into priority and regular lists."""
        devices = [
            {"pretty": "Intel Ethernet Controller", "class": "0200"},  # Priority
            {"pretty": "NVIDIA GeForce RTX 3080", "class": "0300"},  # Non-priority
            {"pretty": "Samsung NVMe SSD", "class": "0108"},  # Priority
            {"pretty": "Intel USB Controller", "class": "0c03"},  # Non-priority
        ]

        priority_devices, regular_devices = self.prioritizer.get_priority_devices(
            devices
        )

        assert len(priority_devices) == 2
        assert len(regular_devices) == 2

        # Check priority devices
        priority_names = [d["pretty"] for d in priority_devices]
        assert "Intel Ethernet Controller" in priority_names
        assert "Samsung NVMe SSD" in priority_names

        # Check regular devices
        regular_names = [d["pretty"] for d in regular_devices]
        assert "NVIDIA GeForce RTX 3080" in regular_names
        assert "Intel USB Controller" in regular_names

    def test_get_priority_devices_empty_list(self):
        """Test get_priority_devices with empty device list."""
        priority_devices, regular_devices = self.prioritizer.get_priority_devices([])
        assert priority_devices == []
        assert regular_devices == []


class TestFormatDeviceWithPriority:
    """Test cases for format_device_with_priority function."""

    def setup_method(self):
        """Set up test fixtures."""
        self.prioritizer = CLIDevicePrioritizer()

    def test_format_priority_device(self):
        """Test formatting of priority devices."""
        device = {"pretty": "Intel Ethernet Controller", "class": "0200"}
        formatted = format_device_with_priority(device, self.prioritizer)

        # Should have green color codes and checkmark
        assert "\033[92m" in formatted  # Green color code
        assert "\033[0m" in formatted  # Reset color code
        assert "✓" in formatted  # Checkmark
        assert "Intel Ethernet Controller" in formatted

    def test_format_regular_device(self):
        """Test formatting of regular (non-priority) devices."""
        device = {"pretty": "NVIDIA GeForce RTX 3080", "class": "0300"}
        formatted = format_device_with_priority(device, self.prioritizer)

        # Should have indentation but no color codes or checkmark
        assert formatted.startswith("  ")  # Two spaces for indentation
        assert "\033[92m" not in formatted  # No green color
        assert "✓" not in formatted  # No checkmark
        assert "NVIDIA GeForce RTX 3080" in formatted

    def test_format_device_missing_class(self):
        """Test formatting device without class information."""
        device = {"pretty": "Unknown Device"}
        formatted = format_device_with_priority(device, self.prioritizer)

        # Should be formatted as regular device
        assert formatted.startswith("  ")
        assert "Unknown Device" in formatted

    def test_format_device_empty_pretty(self):
        """Test formatting device with empty pretty name."""
        device = {"pretty": "", "class": "0200"}
        formatted = format_device_with_priority(device, self.prioritizer)

        # Should still format correctly
        assert "\033[92m" in formatted  # Still priority due to class
        assert "✓" in formatted

    def test_format_device_with_mock_prioritizer(self):
        """Test formatting with mocked prioritizer."""
        device = {"pretty": "Test Device", "class": "0200"}

        # Mock prioritizer to return False
        mock_prioritizer = MagicMock()
        mock_prioritizer.is_priority_device.return_value = False

        formatted = format_device_with_priority(device, mock_prioritizer)

        # Should be formatted as regular device
        assert formatted.startswith("  ")
        assert "✓" not in formatted

        # Mock prioritizer to return True
        mock_prioritizer.is_priority_device.return_value = True

        formatted = format_device_with_priority(device, mock_prioritizer)

        # Should be formatted as priority device
        assert "\033[92m" in formatted
        assert "✓" in formatted
