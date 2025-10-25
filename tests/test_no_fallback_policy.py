#!/usr/bin/env python3
"""
Test suite for no-fallback security policy.

This test verifies that the firmware generator properly rejects missing or invalid
device identification parameters instead of using dangerous fallback values.
"""

import pytest

from src.build import ConfigurationError, ConfigurationManager


class TestNoFallbackPolicy:
    """Test that the system rejects missing device IDs instead of using fallbacks."""

    def setup_method(self):
        """Set up test fixtures."""
        self.config_manager = ConfigurationManager()

    def test_missing_device_config_raises_error(self):
        """Test that missing device config raises ConfigurationError."""
        template_context = {}  # Empty context

        with pytest.raises(ConfigurationError, match="Device configuration is missing"):
            self.config_manager.extract_device_config(template_context, False)

    def test_empty_device_config_raises_error(self):
        """Test that empty device config raises ConfigurationError."""
        template_context = {"device_config": {}}  # Empty device config

        with pytest.raises(ConfigurationError, match="Device configuration is missing"):
            self.config_manager.extract_device_config(template_context, False)

    def test_missing_vendor_id_raises_error(self):
        """Test that missing vendor ID raises ConfigurationError."""
        template_context = {
            "device_config": {
                "device_id": 0x1234,
                "revision_id": 0x01,
                "class_code": 0x020000,
            }
        }

        with pytest.raises(
            ConfigurationError,
            match="Cannot generate device-specific firmware without valid Vendor ID",
        ):
            self.config_manager.extract_device_config(template_context, False)

    def test_missing_device_id_raises_error(self):
        """Test that missing device ID raises ConfigurationError."""
        template_context = {
            "device_config": {
                "vendor_id": 0x8086,
                "revision_id": 0x01,
                "class_code": 0x020000,
            }
        }

        with pytest.raises(
            ConfigurationError,
            match="Cannot generate device-specific firmware without valid Device ID",
        ):
            self.config_manager.extract_device_config(template_context, False)

    def test_zero_vendor_id_raises_error(self):
        """Test that zero vendor ID raises ConfigurationError."""
        template_context = {
            "device_config": {
                "vendor_id": 0x0000,  # Invalid
                "device_id": 0x1234,
                "revision_id": 0x01,
                "class_code": 0x020000,
            }
        }

        with pytest.raises(ConfigurationError, match="Vendor ID is zero"):
            self.config_manager.extract_device_config(template_context, False)

    def test_zero_device_id_raises_error(self):
        """Test that zero device ID raises ConfigurationError."""
        template_context = {
            "device_config": {
                "vendor_id": 0x8086,
                "device_id": 0x0000,  # Invalid
                "revision_id": 0x01,
                "class_code": 0x020000,
            }
        }

        with pytest.raises(ConfigurationError, match="Device ID is zero"):
            self.config_manager.extract_device_config(template_context, False)

    def test_generic_xilinx_ids_allowed(self):
        """Test that previously hardcoded generic IDs are now allowed (following project principles)."""
        template_context = {
            "device_config": {
                "vendor_id": 0x10EE,  # Xilinx - previously hardcoded as generic
                "device_id": 0x7021,  # Generic test device - previously hardcoded as generic
                "revision_id": 0x01,
                "class_code": 0x020000,
            }
        }

        # Should not raise an exception - we no longer hardcode generic combinations
        device_config = self.config_manager.extract_device_config(
            template_context, False
        )

        assert device_config.vendor_id == 0x10EE
        assert device_config.device_id == 0x7021

    def test_generic_placeholder_ids_allowed(self):
        """Test that common placeholder IDs are now allowed (following project principles)."""
        template_context = {
            "device_config": {
                "vendor_id": 0x1234,  # Placeholder - previously hardcoded as generic
                "device_id": 0x5678,  # Placeholder - previously hardcoded as generic
                "revision_id": 0x01,
                "class_code": 0x020000,
            }
        }

        # Should not raise an exception - we no longer hardcode generic combinations
        device_config = self.config_manager.extract_device_config(
            template_context, False
        )

        assert device_config.vendor_id == 0x1234
        assert device_config.device_id == 0x5678

    def test_zero_vendor_id_rejected(self):
        """Test that zero vendor ID is rejected."""
        template_context = {
            "device_config": {
                "vendor_id": 0x0000,  # Invalid zero value
                "device_id": 0x1533,
                "revision_id": 0x01,
                "class_code": 0x020000,
            }
        }

        with pytest.raises(ConfigurationError, match="Vendor ID is zero"):
            self.config_manager.extract_device_config(template_context, False)

    def test_ffff_vendor_id_rejected(self):
        """Test that FFFF vendor ID is rejected."""
        template_context = {
            "device_config": {
                "vendor_id": 0xFFFF,  # Invalid FFFF value
                "device_id": 0x1533,
                "revision_id": 0x01,
                "class_code": 0x020000,
            }
        }

        with pytest.raises(ConfigurationError, match="FFFF values indicate invalid"):
            self.config_manager.extract_device_config(template_context, False)

    def test_zero_device_id_rejected(self):
        """Test that zero device ID is rejected."""
        template_context = {
            "device_config": {
                "vendor_id": 0x8086,
                "device_id": 0x0000,  # Invalid zero value
                "revision_id": 0x01,
                "class_code": 0x020000,
            }
        }

        with pytest.raises(ConfigurationError, match="Device ID is zero"):
            self.config_manager.extract_device_config(template_context, False)

    def test_ffff_device_id_rejected(self):
        """Test that FFFF device ID is rejected."""
        template_context = {
            "device_config": {
                "vendor_id": 0x8086,
                "device_id": 0xFFFF,  # Invalid FFFF value
                "revision_id": 0x01,
                "class_code": 0x020000,
            }
        }

        with pytest.raises(ConfigurationError, match="FFFF values indicate invalid"):
            self.config_manager.extract_device_config(template_context, False)

    def test_valid_device_config_succeeds(self):
        """Test that valid device configuration succeeds."""
        template_context = {
            "device_config": {
                "vendor_id": 0x8086,  # Intel
                "device_id": 0x1533,  # Real Intel device
                "revision_id": 0x01,
                "class_code": 0x020000,
            }
        }

        # Should not raise an exception
        device_config = self.config_manager.extract_device_config(
            template_context, False
        )

        assert device_config.vendor_id == 0x8086
        assert device_config.device_id == 0x1533
        assert device_config.revision_id == 0x01
        assert device_config.class_code == 0x020000

    def test_hex_string_ids_handled_correctly(self):
        """Test that hex string IDs are properly converted and validated."""
        template_context = {
            "device_config": {
                "vendor_id": "0x8086",  # Hex string with prefix
                "device_id": "0x1533",  # Hex string with prefix
                "revision_id": "0x01",  # Hex string with prefix
                "class_code": "0x020000",  # Hex string with prefix
            }
        }

        device_config = self.config_manager.extract_device_config(
            template_context, False
        )

        assert device_config.vendor_id == 0x8086
        assert device_config.device_id == 0x1533
        assert device_config.revision_id == 0x01
        assert device_config.class_code == 0x020000

    def test_zero_hex_string_ids_rejected(self):
        """Test that hex string zero IDs are rejected."""
        template_context = {
            "device_config": {
                "vendor_id": "0x0000",  # Zero as hex string with prefix
                "device_id": "0x1533",
                "revision_id": "0x01",
                "class_code": "0x020000",
            }
        }

        with pytest.raises(ConfigurationError, match="Vendor ID is zero"):
            self.config_manager.extract_device_config(template_context, False)
