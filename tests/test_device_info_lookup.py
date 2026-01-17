#!/usr/bin/env python3
"""
Test suite for the resilient device information lookup module.
"""

import subprocess
import unittest
from pathlib import Path
from unittest.mock import MagicMock, Mock, mock_open, patch

from pcileechfwgenerator.device_clone.device_info_lookup import (DeviceInfoLookup,
                                                 lookup_device_info)


class TestDeviceInfoLookup(unittest.TestCase):
    """Test cases for DeviceInfoLookup class."""

    def setUp(self):
        """Set up test fixtures."""
        self.test_bdf = "0000:03:00.0"
        self.lookup = DeviceInfoLookup(self.test_bdf)

    def test_init(self):
        """Test initialization of DeviceInfoLookup."""
        self.assertEqual(self.lookup.bdf, self.test_bdf)
        self.assertEqual(
            self.lookup.sysfs_path, Path(f"/sys/bus/pci/devices/{self.test_bdf}")
        )
        self.assertIsNone(self.lookup._cached_info)

    def test_has_required_fields_complete(self):
        """Test checking for required fields when all are present."""
        info = {
            "vendor_id": 0x8086,
            "device_id": 0x10D3,
            "class_code": 0x020000,
            "revision_id": 0x00,
        }
        self.assertTrue(self.lookup._has_required_fields(info))

    def test_has_required_fields_missing(self):
        """Test checking for required fields when some are missing."""
        info = {
            "vendor_id": 0x8086,
            "device_id": 0x10D3,
            # Missing class_code and revision_id
        }
        self.assertFalse(self.lookup._has_required_fields(info))

    def test_has_required_fields_none_values(self):
        """Test checking for required fields when some have None values."""
        info = {
            "vendor_id": 0x8086,
            "device_id": None,
            "class_code": 0x020000,
            "revision_id": 0x00,
        }
        self.assertFalse(self.lookup._has_required_fields(info))

    @patch("pcileechfwgenerator.device_clone.device_info_lookup.Path.exists")
    @patch("pcileechfwgenerator.device_clone.device_info_lookup.Path.read_text")
    def test_get_info_from_sysfs_success(self, mock_read_text, mock_exists):
        """Test successful device info extraction from sysfs."""
        # Mock sysfs file existence and content
        mock_exists.return_value = True
        mock_read_text.side_effect = [
            "0x8086\n",  # vendor
            "0x10d3\n",  # device
            "0x020000\n",  # class
            "0x00\n",  # revision
            "0x8086\n",  # subsystem_vendor
            "0xa01f\n",  # subsystem_device
        ]

        info = self.lookup._get_info_from_sysfs()

        self.assertEqual(info["vendor_id"], 0x8086)
        self.assertEqual(info["device_id"], 0x10D3)
        self.assertEqual(info["class_code"], 0x020000)
        self.assertEqual(info["revision_id"], 0x00)
        self.assertEqual(info["subsystem_vendor_id"], 0x8086)
        self.assertEqual(info["subsystem_device_id"], 0xA01F)

    @patch("pcileechfwgenerator.device_clone.device_info_lookup.Path.exists")
    def test_get_info_from_sysfs_missing_files(self, mock_exists):
        """Test device info extraction from sysfs when files are missing."""
        mock_exists.return_value = False

        info = self.lookup._get_info_from_sysfs()

        # Should return empty dict when files don't exist
        self.assertEqual(info, {})

    @patch("pcileechfwgenerator.device_clone.device_info_lookup.subprocess.run")
    def test_get_info_from_lspci_success(self, mock_run):
        """Test successful device info extraction from lspci."""
        # Mock lspci output
        mock_result = Mock()
        mock_result.returncode = 0
        mock_result.stdout = (
            "03:00.0 Network controller [0280]: Intel Corporation [8086] "
            "Device [10d3] (rev 00)\n"
            "\tSubsystem: Intel Corporation [8086] Device [a01f]\n"
        )
        mock_run.return_value = mock_result

        info = self.lookup._get_info_from_lspci()

        self.assertEqual(info["vendor_id"], 0x8086)
        self.assertEqual(info["device_id"], 0x10D3)
        self.assertEqual(info["revision_id"], 0x00)
        self.assertEqual(info["class_code"], 0x028000)
        self.assertEqual(info["subsystem_vendor_id"], 0x8086)
        self.assertEqual(info["subsystem_device_id"], 0xA01F)

    @patch("pcileechfwgenerator.device_clone.device_info_lookup.subprocess.run")
    def test_get_info_from_lspci_failure(self, mock_run):
        """Test device info extraction from lspci when command fails."""
        mock_result = Mock()
        mock_result.returncode = 1
        mock_result.stdout = ""
        mock_run.return_value = mock_result

        info = self.lookup._get_info_from_lspci()

        # Should return empty dict on failure
        self.assertEqual(info, {})

    @patch("pcileechfwgenerator.device_clone.device_info_lookup.subprocess.run")
    def test_get_info_from_lspci_timeout(self, mock_run):
        """Test device info extraction from lspci when command times out."""
        mock_run.side_effect = subprocess.TimeoutExpired("lspci", 5)

        info = self.lookup._get_info_from_lspci()

        # Should return empty dict on timeout
        self.assertEqual(info, {})

    @patch(
        "builtins.open",
        new_callable=mock_open,
        read_data=b"\x86\x80\xd3\x10" + b"\x00" * 252,
    )
    @patch("pcileechfwgenerator.device_clone.device_info_lookup.Path.exists")
    def test_get_info_from_config_space_success(self, mock_exists, mock_file):
        """Test successful device info extraction from config space."""
        mock_exists.return_value = True

        info = self.lookup._get_info_from_config_space()

        self.assertEqual(info["vendor_id"], 0x8086)
        self.assertEqual(info["device_id"], 0x10D3)

    @patch("pcileechfwgenerator.device_clone.device_info_lookup.Path.exists")
    def test_get_info_from_config_space_no_file(self, mock_exists):
        """Test device info extraction from config space when file doesn't exist."""
        mock_exists.return_value = False

        info = self.lookup._get_info_from_config_space()

        # Should return empty dict when file doesn't exist
        self.assertEqual(info, {})

    def test_merge_device_info(self):
        """Test merging device information dictionaries."""
        base = {"vendor_id": 0x8086, "device_id": 0x10D3, "class_code": None}
        new = {
            "class_code": 0x020000,
            "revision_id": 0x00,
            "vendor_id": 0xFFFF,  # Should not override existing valid value
        }

        merged = self.lookup._merge_device_info(base, new)

        self.assertEqual(merged["vendor_id"], 0x8086)  # Original kept
        self.assertEqual(merged["device_id"], 0x10D3)
        self.assertEqual(merged["class_code"], 0x020000)  # New value added
        self.assertEqual(merged["revision_id"], 0x00)

    def test_merge_device_info_invalid_values(self):
        """Test merging device info with invalid values."""
        base = {
            "vendor_id": 0x0000,  # Invalid
            "device_id": 0xFFFF,  # Invalid
        }
        new = {
            "vendor_id": 0x8086,
            "device_id": 0x10D3,
        }

        merged = self.lookup._merge_device_info(base, new)

        # Should replace invalid values
        self.assertEqual(merged["vendor_id"], 0x8086)
        self.assertEqual(merged["device_id"], 0x10D3)

    def test_apply_intelligent_defaults_subsystem(self):
        """Test applying intelligent defaults for subsystem IDs."""
        info = {
            "vendor_id": 0x8086,
            "device_id": 0x10D3,
            # Missing subsystem IDs
        }

        result = self.lookup._apply_intelligent_defaults(info)

        # Should use main IDs as subsystem IDs
        self.assertEqual(result["subsystem_vendor_id"], 0x8086)
        self.assertEqual(result["subsystem_device_id"], 0x10D3)

    def test_apply_intelligent_defaults_missing_revision(self):
        """Test applying intelligent defaults for missing revision ID."""
        info = {
            "vendor_id": 0x8086,
            "device_id": 0x10D3,
        }

        result = self.lookup._apply_intelligent_defaults(info)

        # Should set revision to 0x00
        self.assertEqual(result["revision_id"], 0x00)

    def test_apply_intelligent_defaults_missing_class(self):
        """Test applying intelligent defaults for missing class code."""
        info = {
            "vendor_id": 0x8086,
            "device_id": 0x10D3,
        }

        result = self.lookup._apply_intelligent_defaults(info)

        # Should set generic class code
        self.assertEqual(result["class_code"], 0x088000)
