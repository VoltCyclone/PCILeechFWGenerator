#!/usr/bin/env python3
"""Tests for vendor/device name resolution after lookup removal."""

import unittest
from unittest.mock import Mock, patch

from src.device_clone.pcileech_context import PCILeechContextBuilder


class TestVendorDeviceNameResolution(unittest.TestCase):
    def setUp(self):  # noqa: D401
        self.bdf = "0000:03:00.0"

        class Cfg:
            pass

        self.cfg = Cfg()
        self.builder = PCILeechContextBuilder(self.bdf, self.cfg)

    @patch("subprocess.run")
    def test_vendor_name_lspci_parse(self, mock_run):
        mock_result = Mock()
        mock_result.returncode = 0
        mock_result.stdout = (
            '0000:03:00.0 "Net controller" "Acme Corp" "UltraNet 1000"\n'
        )
        mock_run.return_value = mock_result
        name = self.builder._get_vendor_name("8086")
        self.assertEqual(name, "Acme Corp")

    @patch("subprocess.run")
    def test_device_name_lspci_parse(self, mock_run):
        mock_result = Mock()
        mock_result.returncode = 0
        mock_result.stdout = (
            '0000:03:00.0 "Net controller" "Acme Corp" "UltraNet 1000"\n'
        )
        mock_run.return_value = mock_result
        name = self.builder._get_device_name("8086", "10d3")
        self.assertEqual(name, "UltraNet 1000")

    @patch("subprocess.run", side_effect=Exception("lspci fail"))
    def test_vendor_name_fallback(self, _):
        name = self.builder._get_vendor_name("8086")
        self.assertEqual(name, "Vendor 8086")

    @patch("subprocess.run", side_effect=Exception("lspci fail"))
    def test_device_name_fallback(self, _):
        name = self.builder._get_device_name("8086", "10d3")
        self.assertEqual(name, "Device 10d3")


if __name__ == "__main__":  # pragma: no cover
    unittest.main()
