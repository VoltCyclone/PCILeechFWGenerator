#!/usr/bin/env python3
"""
Tests for PCIe Payload Size Configuration Module
"""

import pytest

from pcileechfwgenerator.device_clone.constants import VALID_MPS_VALUES
from pcileechfwgenerator.device_clone.payload_size_config import (
    PayloadSizeConfig, PayloadSizeError, validate_and_configure_payload_size)


class TestPayloadSizeConfig:
    """Test cases for PayloadSizeConfig class."""

    def test_valid_payload_sizes(self):
        """Test that all valid payload sizes are accepted."""
        for size in VALID_MPS_VALUES:
            config = PayloadSizeConfig(size)
            assert config.max_payload_size == size

    def test_invalid_payload_size(self):
        """Test that invalid payload sizes raise errors."""
        invalid_sizes = [64, 100, 200, 300, 500, 1500, 3000, 5000, 8192]

        for size in invalid_sizes:
            with pytest.raises(PayloadSizeError) as exc_info:
                PayloadSizeConfig(size)
            assert "Invalid maximum payload size" in str(exc_info.value)
            assert str(size) in str(exc_info.value)

    def test_mps_encoding_values(self):
        """Test that MPS encoding values are correct."""
        expected_encodings = {
            128: 0,
            256: 1,
            512: 2,
            1024: 3,
            2048: 4,
            4096: 5,
        }

        for size, expected_encoding in expected_encodings.items():
            config = PayloadSizeConfig(size)
            assert config.get_mps_encoding() == expected_encoding
            assert config.get_cfg_force_mps() == expected_encoding

    def test_tiny_pcie_algo_detection(self):
        """Test detection of tiny PCIe algorithm issues."""
        # Payload size below threshold should trigger warning
        config_128 = PayloadSizeConfig(128)
        has_issues, warning = config_128.check_tiny_pcie_algo_issues()
        assert has_issues is True
        assert warning is not None
        assert "128 bytes is below the recommended threshold" in warning
        assert "tiny PCIe algorithm" in warning

        # Payload sizes at or above threshold should not trigger warning
        for size in [256, 512, 1024, 2048, 4096]:
            config = PayloadSizeConfig(size)
            has_issues, warning = config.check_tiny_pcie_algo_issues()
            assert has_issues is False
            assert warning is None

    def test_device_capabilities_validation(self):
        """Test validation against device capabilities."""
        # Test with device that supports up to 512 bytes
        device_caps = {"max_payload_supported": 512}

        # Valid configurations
        for size in [128, 256, 512]:
            config = PayloadSizeConfig(size, device_caps)
            config.validate_against_device_capabilities()  # Should not raise

        # Invalid configuration - exceeds device capability
        config_1024 = PayloadSizeConfig(1024, device_caps)
        with pytest.raises(PayloadSizeError) as exc_info:
            config_1024.validate_against_device_capabilities()
        assert "1024 bytes exceeds device maximum" in str(exc_info.value)

    def test_pcie_generation_recommendations(self):
        """Test PCIe generation recommendations."""
        # Test PCIe Gen3 device with small payload
        device_caps = {"pcie_generation": 3}
        config = PayloadSizeConfig(256, device_caps)

        # This should log a warning but not raise an error
        config.validate_against_device_capabilities()

        # Test with recommended size
        config_512 = PayloadSizeConfig(512, device_caps)
        config_512.validate_against_device_capabilities()  # Should not warn

    def test_configuration_summary(self):
        """Test configuration summary generation."""
        config = PayloadSizeConfig(512)
        summary = config.get_configuration_summary()

        assert summary["max_payload_size"] == 512
        assert summary["mps_encoding"] == 2
        assert summary["cfg_force_mps"] == 2
        assert summary["has_tiny_pcie_issues"] is False
        assert summary["warning"] is None
        assert summary["hex_encoding"] == "0x2"

    def test_validate_and_configure_function(self):
        """Test the validate_and_configure_payload_size helper function."""
        # Test successful configuration
        result = validate_and_configure_payload_size(256)
        assert result["max_payload_size"] == 256
        assert result["cfg_force_mps"] == 1
        assert result["has_tiny_pcie_issues"] is False

        # Test with tiny PCIe warning (should not fail by default)
        result = validate_and_configure_payload_size(128)
        assert result["max_payload_size"] == 128
        assert result["cfg_force_mps"] == 0
        assert result["has_tiny_pcie_issues"] is True
        assert result["warning"] is not None

        # Test with fail_on_warning=True
        with pytest.raises(PayloadSizeError) as exc_info:
            validate_and_configure_payload_size(128, fail_on_warning=True)
        assert "tiny PCIe algorithm" in str(exc_info.value)

        # Test with invalid size
        with pytest.raises(PayloadSizeError) as exc_info:
            validate_and_configure_payload_size(999)
        assert "Invalid maximum payload size: 999" in str(exc_info.value)

    def test_edge_cases(self):
        """Test edge cases and boundary conditions."""
        # Test minimum valid size
        config_min = PayloadSizeConfig(128)
        assert config_min.get_cfg_force_mps() == 0

        # Test maximum valid size
        config_max = PayloadSizeConfig(4096)
        assert config_max.get_cfg_force_mps() == 5

        # Test with empty device capabilities
        config = PayloadSizeConfig(256, {})
        config.validate_against_device_capabilities()  # Should not raise

        # Test with None device capabilities
        config_none = PayloadSizeConfig(256, None)
        config_none.validate_against_device_capabilities()  # Should not raise


class TestIntegrationWithDeviceConfig:
    """Test integration with DeviceCapabilities class."""

    def test_device_capabilities_methods(self):
        """Test that DeviceCapabilities methods work correctly."""
        from pcileechfwgenerator.device_clone.device_config import DeviceCapabilities

        # Test with default payload size
        caps = DeviceCapabilities()
        assert caps.max_payload_size == 256
        assert caps.get_cfg_force_mps() == 1

        # Test with custom payload size
        caps_512 = DeviceCapabilities(max_payload_size=512)
        assert caps_512.get_cfg_force_mps() == 2

        # Test tiny PCIe detection
        caps_128 = DeviceCapabilities(max_payload_size=128)
        has_issues, warning = caps_128.check_tiny_pcie_issues()
        assert has_issues is True
        assert warning is not None
        assert "128 bytes" in warning

    def test_device_capabilities_validation(self):
        """Test that DeviceCapabilities validation uses PayloadSizeConfig."""
        from pcileechfwgenerator.device_clone.device_config import DeviceCapabilities

        # Valid payload size
        caps = DeviceCapabilities(max_payload_size=1024)
        caps.validate()  # Should not raise

        # Invalid payload size
        caps_invalid = DeviceCapabilities(max_payload_size=777)
        with pytest.raises(ValueError) as exc_info:
            caps_invalid.validate()
        assert "Invalid maximum payload size: 777" in str(exc_info.value)


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
