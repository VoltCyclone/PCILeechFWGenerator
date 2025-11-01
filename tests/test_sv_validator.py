#!/usr/bin/env python3
"""
Unit tests for SystemVerilog validator module.

Tests validation of device configuration, template context, device identification,
and donor artifacts (VPD/Option ROM).
"""

import logging
import sys
from pathlib import Path
from typing import Any, Dict

import pytest

# Add src to path
sys.path.insert(0, str(Path(__file__).parent.parent))

from src.string_utils import safe_format

from src.templating.sv_validator import SVValidator
from src.templating.template_renderer import TemplateRenderError

from src.utils.unified_context import TemplateObject

logger = logging.getLogger(__name__)


def create_valid_device_config_dict() -> Dict[str, Any]:
    """Create a properly formatted device config dict for testing."""
    return {
        "vendor_id": "10de",  # No 0x prefix, 4 chars
        "device_id": "1234",  # No 0x prefix, 4 chars
        "subsystem_vendor_id": "10de",  # No 0x prefix, 4 chars
        "subsystem_device_id": "5678",  # Note: subsystem_device_id not subsystem_id
        "class_code": "020000",  # No 0x prefix, 6 chars
        "revision_id": "a1",  # No 0x prefix, 2 chars
    }


class MockDeviceConfig:
    """Mock device configuration for testing."""

    def __init__(self, **kwargs):
        self.device_type = kwargs.get("device_type", MockEnum("pcie"))
        self.device_class = kwargs.get("device_class", MockEnum("network"))
        self.max_payload_size = kwargs.get("max_payload_size", 256)
        self.max_read_request_size = kwargs.get("max_read_request_size", 512)
        self.tx_queue_depth = kwargs.get("tx_queue_depth", 256)
        self.rx_queue_depth = kwargs.get("rx_queue_depth", 256)
        self.has_option_rom = kwargs.get("has_option_rom", False)


class MockEnum:
    """Mock enum for testing."""

    def __init__(self, value: str):
        self.value = value


class TestSVValidatorDeviceConfig:
    """Test device configuration validation."""

    def test_validate_device_config_success(self):
        """Test successful device config validation."""
        validator = SVValidator(logger)
        device_config = MockDeviceConfig()

        # Should not raise
        validator.validate_device_config(device_config)

    def test_validate_device_config_none(self):
        """Test validation fails with None device config."""
        validator = SVValidator(logger)

        with pytest.raises(ValueError, match="configuration.*required|Device"):
            validator.validate_device_config(None)

    def test_validate_device_config_invalid_device_type(self):
        """Test validation fails with invalid device type."""
        validator = SVValidator(logger)
        device_config = MockDeviceConfig()
        device_config.device_type = "not_an_enum"  # String instead of enum

        with pytest.raises(ValueError, match="device.*type|invalid"):
            validator.validate_device_config(device_config)

    def test_validate_device_config_invalid_device_class(self):
        """Test validation fails with invalid device class."""
        validator = SVValidator(logger)
        device_config = MockDeviceConfig()
        device_config.device_class = 12345  # Not an enum

        with pytest.raises(ValueError, match="device.*class|invalid"):
            validator.validate_device_config(device_config)

    def test_validate_device_config_invalid_payload_size(self):
        """Test validation fails with invalid max_payload_size."""
        validator = SVValidator(logger)
        device_config = MockDeviceConfig(max_payload_size=99999)

        with pytest.raises(ValueError, match="max_payload_size"):
            validator.validate_device_config(device_config)

    def test_validate_device_config_invalid_read_request_size(self):
        """Test validation fails with invalid max_read_request_size."""
        validator = SVValidator(logger)
        device_config = MockDeviceConfig(max_read_request_size=1)

        with pytest.raises(ValueError, match="max_read_request_size"):
            validator.validate_device_config(device_config)

    def test_validate_device_config_invalid_tx_queue_depth(self):
        """Test validation fails with invalid tx_queue_depth."""
        validator = SVValidator(logger)
        device_config = MockDeviceConfig(tx_queue_depth=0)

        with pytest.raises(ValueError, match="tx_queue_depth"):
            validator.validate_device_config(device_config)

    def test_validate_device_config_invalid_rx_queue_depth(self):
        """Test validation fails with invalid rx_queue_depth."""
        validator = SVValidator(logger)
        device_config = MockDeviceConfig(rx_queue_depth=99999)

        with pytest.raises(ValueError, match="rx_queue_depth"):
            validator.validate_device_config(device_config)


class TestSVValidatorTemplateContext:
    """Test template context validation."""

    def test_validate_template_context_success(self):
        """Test successful template context validation."""
        validator = SVValidator(logger)
        context = {
            "device_config": create_valid_device_config_dict(),
            "device_signature": "test_signature_12345",
        }

        # Should not raise
        validator.validate_template_context(context)

    def test_validate_template_context_none(self):
        """Test validation fails with None context."""
        validator = SVValidator(logger)

        with pytest.raises(ValueError, match="no_template_context|context"):
            validator.validate_template_context(None)

    def test_validate_template_context_not_dict(self):
        """Test validation fails with non-dict context."""
        validator = SVValidator(logger)

        with pytest.raises(ValueError, match="dictionary.*got str|must be"):
            validator.validate_template_context("not_a_dict")

    def test_validate_template_context_missing_device_config(self):
        """Test validation fails without device_config."""
        validator = SVValidator(logger)
        context = {"device_signature": "test"}

        with pytest.raises(TemplateRenderError, match="device_config|critical"):
            validator.validate_template_context(context)

    def test_validate_template_context_invalid_device_config_type(self):
        """Test validation fails with invalid device_config type."""
        validator = SVValidator(logger)
        context = {
            "device_config": "not_a_dict_or_object",
            "device_signature": "test",
        }

        with pytest.raises(TemplateRenderError, match="device_config.*dict"):
            validator.validate_template_context(context)

    def test_validate_template_context_missing_device_signature(self):
        """Test validation fails without device_signature."""
        validator = SVValidator(logger)
        context = {"device_config": {}}

        with pytest.raises(TemplateRenderError, match="device_signature|missing"):
            validator.validate_template_context(context)

    def test_validate_template_context_empty_device_signature(self):
        """Test validation fails with empty device_signature."""
        validator = SVValidator(logger)
        context = {"device_config": {}, "device_signature": ""}

        with pytest.raises(TemplateRenderError, match="device_signature|empty"):
            validator.validate_template_context(context)

    def test_validate_template_context_with_template_object(self):
        """Test validation works with TemplateObject device_config."""
        validator = SVValidator(logger)
        # TemplateObject wraps a dict
        device_config_dict = create_valid_device_config_dict()
        device_config = TemplateObject(device_config_dict)
        context = {
            "device_config": device_config,
            "device_signature": "test_signature",
        }

        # Should not raise
        validator.validate_template_context(context)


class TestSVValidatorDeviceIdentification:
    """Test device identification validation."""

    def test_validate_device_identification_success(self):
        """Test successful device identification validation."""
        validator = SVValidator(logger)
        device_config = create_valid_device_config_dict()

        # Should not raise
        validator.validate_device_identification(device_config)

    def test_validate_device_identification_missing_vendor_id(self):
        """Test validation fails with missing vendor_id."""
        validator = SVValidator(logger)
        device_config = create_valid_device_config_dict()
        del device_config["vendor_id"]

        with pytest.raises(TemplateRenderError, match="vendor_id"):
            validator.validate_device_identification(device_config)

    def test_validate_device_identification_missing_device_id(self):
        """Test validation fails with missing device_id."""
        validator = SVValidator(logger)
        device_config = create_valid_device_config_dict()
        del device_config["device_id"]

        with pytest.raises(TemplateRenderError, match="device_id"):
            validator.validate_device_identification(device_config)

    def test_validate_device_identification_invalid_vendor_id_width(self):
        """Test validation fails with invalid vendor_id width."""
        validator = SVValidator(logger)
        device_config = create_valid_device_config_dict()
        device_config["vendor_id"] = "10"  # Too short (2 chars instead of 4)

        with pytest.raises(TemplateRenderError, match="vendor_id"):
            validator.validate_device_identification(device_config)

    def test_validate_device_identification_invalid_class_code_width(self):
        """Test validation fails with invalid class_code width."""
        validator = SVValidator(logger)
        device_config = create_valid_device_config_dict()
        device_config["class_code"] = "02"  # Too short (2 chars instead of 6)

        with pytest.raises(TemplateRenderError, match="class_code"):
            validator.validate_device_identification(device_config)

    def test_validate_device_identification_missing_subsystem_vendor_id(self):
        """Test validation fails with missing subsystem_vendor_id."""
        validator = SVValidator(logger)
        device_config = create_valid_device_config_dict()
        del device_config["subsystem_vendor_id"]

        with pytest.raises(TemplateRenderError, match="subsystem_vendor_id"):
            validator.validate_device_identification(device_config)

    def test_validate_device_identification_missing_subsystem_id(self):
        """Test validation fails with missing subsystem_device_id."""
        validator = SVValidator(logger)
        device_config = create_valid_device_config_dict()
        del device_config["subsystem_device_id"]

        with pytest.raises(TemplateRenderError, match="subsystem_device_id"):
            validator.validate_device_identification(device_config)

    def test_validate_device_identification_missing_revision_id(self):
        """Test validation fails with missing revision_id."""
        validator = SVValidator(logger)
        device_config = create_valid_device_config_dict()
        del device_config["revision_id"]

        with pytest.raises(TemplateRenderError, match="revision_id"):
            validator.validate_device_identification(device_config)


class TestSVValidatorDonorArtifacts:
    """Test VPD and Option ROM donor artifact validation."""

    def test_validate_donor_artifacts_no_requirements(self):
        """Test validation passes when no artifacts required."""
        validator = SVValidator(logger)
        context = {
            "device_config": create_valid_device_config_dict(),
            "device_signature": "test",
        }

        # Should not raise
        validator.validate_template_context(context)

    def test_validate_donor_artifacts_vpd_required_missing(self):
        """Test validation fails when VPD required but missing."""
        validator = SVValidator(logger)
        context = {
            "device_config": create_valid_device_config_dict(),
            "device_signature": "test",
            "requires_vpd": True,
        }

        with pytest.raises(TemplateRenderError, match="VPD.*required"):
            validator.validate_template_context(context)

    def test_validate_donor_artifacts_vpd_required_empty(self):
        """Test validation fails when VPD required but empty."""
        validator = SVValidator(logger)
        context = {
            "device_config": create_valid_device_config_dict(),
            "device_signature": "test",
            "requires_vpd": True,
            "vpd_data": b"",
        }

        with pytest.raises(TemplateRenderError, match="VPD.*required"):
            validator.validate_template_context(context)

    def test_validate_donor_artifacts_vpd_required_present(self):
        """Test validation passes when VPD required and present."""
        validator = SVValidator(logger)
        context = {
            "device_config": create_valid_device_config_dict(),
            "device_signature": "test",
            "requires_vpd": True,
            "vpd_data": b"\x90\x01\x02\x03",
        }

        # Should not raise
        validator.validate_template_context(context)

    def test_validate_donor_artifacts_option_rom_missing_size(self):
        """Test validation fails when Option ROM has_option_rom but no size."""
        device_config_dict = create_valid_device_config_dict()
        device_config_dict["has_option_rom"] = True
        # No option_rom_size provided, should trigger ROM_SIZE error
        
        context = {
            "device_config": device_config_dict,
            "device_signature": "test_signature"
        }
        validator = SVValidator(logger)
        
        with pytest.raises(TemplateRenderError, match="ROM_SIZE"):
            validator.validate_template_context(context)

    def test_validate_donor_artifacts_option_rom_invalid_size(self):
        """Test validation fails when option ROM size is invalid."""
        validator = SVValidator(logger)
        device_config = create_valid_device_config_dict()
        device_config["has_option_rom"] = True
        context = {
            "device_config": device_config,
            "device_signature": "test",
            "ROM_SIZE": 0,
        }

        with pytest.raises(TemplateRenderError, match="ROM_SIZE"):
            validator.validate_template_context(context)

    def test_validate_donor_artifacts_option_rom_size_mismatch(self):
        """Test validation fails when ROM data size doesn't match ROM_SIZE."""
        validator = SVValidator(logger)
        device_config = create_valid_device_config_dict()
        device_config["has_option_rom"] = True
        context = {
            "device_config": device_config,
            "device_signature": "test",
            "ROM_SIZE": 1024,
            "rom_data": b"\x55\xaa" * 256,  # 512 bytes, not 1024
        }

        with pytest.raises(TemplateRenderError, match="ROM.*size.*mismatch"):
            validator.validate_template_context(context)

    def test_validate_donor_artifacts_option_rom_valid(self):
        """Test validation passes with valid option ROM data."""
        validator = SVValidator(logger)
        rom_data = b"\x55\xaa" + b"\x00" * 510  # 512 bytes
        device_config = create_valid_device_config_dict()
        device_config["has_option_rom"] = True
        context = {
            "device_config": device_config,
            "device_signature": "test",
            "ROM_SIZE": 512,
            "rom_data": rom_data,
        }

        # Should not raise
        validator.validate_template_context(context)

    def test_validate_donor_artifacts_rom_checksum_computed(self):
        """Test ROM checksum is computed when not provided."""
        validator = SVValidator(logger)
        rom_data = b"\x55\xaa" + b"\x00" * 510
        device_config = create_valid_device_config_dict()
        device_config["has_option_rom"] = True
        context = {
            "device_config": device_config,
            "device_signature": "test",
            "ROM_SIZE": 512,
            "rom_data": rom_data,
        }

        # Should compute checksum without raising
        validator.validate_template_context(context)

    def test_compute_rom_digest_bytes(self):
        """Test ROM digest computation with bytes."""
        validator = SVValidator(logger)
        rom_data = b"\x55\xaa\x00\x00"
        digest = validator._compute_rom_digest(rom_data)

        assert digest is not None
        assert len(digest) == 64  # SHA256 hex digest length

    def test_compute_rom_digest_bytearray(self):
        """Test ROM digest computation with bytearray."""
        validator = SVValidator(logger)
        rom_data = bytearray(b"\x55\xaa\x00\x00")
        digest = validator._compute_rom_digest(rom_data)

        assert digest is not None
        assert len(digest) == 64

    def test_compute_rom_digest_hex_string(self):
        """Test ROM digest computation with hex string."""
        validator = SVValidator(logger)
        rom_data = "55aa0000"
        digest = validator._compute_rom_digest(rom_data)

        assert digest is not None
        assert len(digest) == 64

    def test_compute_rom_digest_invalid_type(self):
        """Test ROM digest computation returns None for invalid type."""
        validator = SVValidator(logger)
        digest = validator._compute_rom_digest(12345)

        assert digest is None


class TestSVValidatorNumericRange:
    """Test numeric range validation (internal helper)."""

    def test_validate_numeric_range_valid_int(self):
        """Test numeric range validation passes for valid integer."""
        validator = SVValidator(logger)
        error = validator._validate_numeric_range("test_param", 50, 0, 100)

        assert error is None

    def test_validate_numeric_range_valid_float(self):
        """Test numeric range validation passes for valid float."""
        validator = SVValidator(logger)
        error = validator._validate_numeric_range("test_param", 50.5, 0.0, 100.0)

        assert error is None

    def test_validate_numeric_range_below_min(self):
        """Test numeric range validation fails below minimum."""
        validator = SVValidator(logger)
        error = validator._validate_numeric_range("test_param", -1, 0, 100)

        assert error is not None
        assert "test_param" in error

    def test_validate_numeric_range_above_max(self):
        """Test numeric range validation fails above maximum."""
        validator = SVValidator(logger)
        error = validator._validate_numeric_range("test_param", 101, 0, 100)

        assert error is not None
        assert "test_param" in error

    def test_validate_numeric_range_not_numeric(self):
        """Test numeric range validation fails for non-numeric value."""
        validator = SVValidator(logger)
        error = validator._validate_numeric_range(
            "test_param", "not_a_number", 0, 100
        )

        assert error is not None
        assert "test_param" in error
        assert "number" in error.lower()

    def test_validate_numeric_range_at_boundaries(self):
        """Test numeric range validation passes at exact boundaries."""
        validator = SVValidator(logger)

        # At minimum
        error = validator._validate_numeric_range("test_param", 0, 0, 100)
        assert error is None

        # At maximum
        error = validator._validate_numeric_range("test_param", 100, 0, 100)
        assert error is None


class TestSVValidatorIntegration:
    """Integration tests for SVValidator."""

    def test_full_validation_pipeline_success(self):
        """Test complete validation pipeline with valid data."""
        validator = SVValidator(logger)

        # Create valid device config
        device_config = MockDeviceConfig(
            max_payload_size=256,
            max_read_request_size=512,
            tx_queue_depth=256,
            rx_queue_depth=256,
        )

        # Create valid context
        context = {
            "device_config": create_valid_device_config_dict(),
            "device_signature": "nvidia_gpu_12345",
        }

        # Should pass all validations
        validator.validate_device_config(device_config)
        validator.validate_template_context(context)

    def test_full_validation_pipeline_with_vpd(self):
        """Test complete validation pipeline with VPD requirement."""
        validator = SVValidator(logger)

        device_config = MockDeviceConfig()
        context = {
            "device_config": create_valid_device_config_dict(),
            "device_signature": "nvidia_gpu_12345",
            "requires_vpd": True,
            "vpd_data": b"\x90\x01\x02\x03" * 64,
        }

        validator.validate_device_config(device_config)
        validator.validate_template_context(context)

    def test_full_validation_pipeline_with_option_rom(self):
        """Test complete validation pipeline with option ROM."""
        validator = SVValidator(logger)

        rom_data = b"\x55\xaa" + b"\x00" * 65534  # 64KB ROM
        device_config = MockDeviceConfig(has_option_rom=True)
        device_config_dict = create_valid_device_config_dict()
        device_config_dict["has_option_rom"] = True
        context = {
            "device_config": device_config_dict,
            "device_signature": "nvidia_gpu_12345",
            "ROM_SIZE": 65536,
            "rom_data": rom_data,
        }

        validator.validate_device_config(device_config)
        validator.validate_template_context(context)


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
