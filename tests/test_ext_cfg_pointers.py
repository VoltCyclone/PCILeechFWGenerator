#!/usr/bin/env python3
"""
Unit tests for Extended Configuration Space Pointer Control feature.
"""

import sys
from pathlib import Path

import pytest

# Add src to path for imports
sys.path.insert(0, str(Path(__file__).parent.parent))

from pcileechfwgenerator.device_clone.config_space_manager import ConfigSpaceConstants
from pcileechfwgenerator.device_clone.device_config import (DeviceCapabilities, DeviceClass,
                                            DeviceConfigManager,
                                            DeviceConfiguration,
                                            DeviceIdentification, DeviceType)


class TestExtendedConfigPointers:
    """Test suite for Extended Configuration Space Pointer Control."""

    def test_device_capabilities_defaults(self):
        """Test DeviceCapabilities with default extended config pointers."""
        caps = DeviceCapabilities()
        assert caps.ext_cfg_cap_ptr == 0x100
        assert caps.ext_cfg_xp_cap_ptr == 0x100

    def test_device_capabilities_custom_values(self):
        """Test DeviceCapabilities with custom extended config pointers."""
        caps = DeviceCapabilities(ext_cfg_cap_ptr=0x200, ext_cfg_xp_cap_ptr=0x300)
        assert caps.ext_cfg_cap_ptr == 0x200
        assert caps.ext_cfg_xp_cap_ptr == 0x300

        # Should pass validation
        caps.validate()

    def test_device_capabilities_validation_low_value(self):
        """Test validation fails for pointer values below 0x100."""
        caps = DeviceCapabilities(ext_cfg_cap_ptr=0x50)

        with pytest.raises(ValueError) as exc_info:
            caps.validate()
        assert "Invalid extended config capability pointer" in str(exc_info.value)

    def test_device_capabilities_validation_high_value(self):
        """Test validation fails for pointer values above 0xFFC."""
        caps = DeviceCapabilities(ext_cfg_cap_ptr=0x1000)

        with pytest.raises(ValueError) as exc_info:
            caps.validate()
        assert "Invalid extended config capability pointer" in str(exc_info.value)

    def test_device_capabilities_validation_alignment(self):
        """Test validation fails for misaligned pointer values."""
        caps = DeviceCapabilities(ext_cfg_cap_ptr=0x201)

        with pytest.raises(ValueError) as exc_info:
            caps.validate()
        assert "must be 4-byte aligned" in str(exc_info.value)

    def test_device_configuration_serialization(self):
        """Test DeviceConfiguration serialization includes extended pointers."""
        config = DeviceConfiguration(
            name="test_device",
            device_type=DeviceType.GENERIC,
            device_class=DeviceClass.CONSUMER,
            identification=DeviceIdentification(
                vendor_id=0x1234, device_id=0x5678, class_code=0x040300
            ),
            capabilities=DeviceCapabilities(
                ext_cfg_cap_ptr=0x200, ext_cfg_xp_cap_ptr=0x300
            ),
        )

        # Test serialization
        config_dict = config.to_dict()
        caps_dict = config_dict.get("capabilities", {})

        assert caps_dict.get("ext_cfg_cap_ptr") == 0x200
        assert caps_dict.get("ext_cfg_xp_cap_ptr") == 0x300

    def test_device_config_manager_deserialization(self):
        """Test DeviceConfigManager correctly deserializes extended pointers."""
        manager = DeviceConfigManager()

        # Test data with extended pointers
        test_data = {
            "name": "test_device",
            "device_type": "generic",
            "device_class": "consumer",
            "identification": {
                "vendor_id": 0x1234,
                "device_id": 0x5678,
                "class_code": 0x040300,
            },
            "registers": {},
            "capabilities": {
                "link_width": 1,
                "link_speed": "2.5GT/s",
                "ext_cfg_cap_ptr": 0x400,
                "ext_cfg_xp_cap_ptr": 0x500,
            },
        }

        config = manager._dict_to_config(test_data)
        assert config.capabilities.ext_cfg_cap_ptr == 0x400
        assert config.capabilities.ext_cfg_xp_cap_ptr == 0x500

    def test_config_space_constants(self):
        """Test ConfigSpaceConstants includes extended pointer defaults."""
        assert hasattr(ConfigSpaceConstants, "DEFAULT_EXT_CFG_CAP_PTR")
        assert hasattr(ConfigSpaceConstants, "DEFAULT_EXT_CFG_XP_CAP_PTR")
        assert ConfigSpaceConstants.DEFAULT_EXT_CFG_CAP_PTR == 0x100
        assert ConfigSpaceConstants.DEFAULT_EXT_CFG_XP_CAP_PTR == 0x100

    def test_template_context_structure(self):
        """Test template context structure for extended pointers."""
        # This tests the expected structure that would be passed to templates
        template_context = {
            "CONFIG_SPACE_SIZE": 4096,
            "OVERLAY_ENTRIES": 32,
            "EXT_CFG_CAP_PTR": 0x200,
            "EXT_CFG_XP_CAP_PTR": 0x300,
            "OVERLAY_MAP": [],
        }

        # Verify the context has the required keys
        assert "EXT_CFG_CAP_PTR" in template_context
        assert "EXT_CFG_XP_CAP_PTR" in template_context
        assert template_context["EXT_CFG_CAP_PTR"] == 0x200
        assert template_context["EXT_CFG_XP_CAP_PTR"] == 0x300


@pytest.mark.parametrize(
    "cap_ptr,xp_ptr,expected_valid",
    [
        (0x100, 0x100, True),  # Default values
        (0x200, 0x300, True),  # Valid custom values
        (0x400, 0x400, True),  # Same value for both
        (0xFFC, 0xFFC, True),  # Maximum valid value
        (0x0FC, 0x100, False),  # cap_ptr too low
        (0x100, 0x0FC, False),  # xp_ptr too low
        (0x1000, 0x100, False),  # cap_ptr too high
        (0x100, 0x1000, False),  # xp_ptr too high
        (0x101, 0x100, False),  # cap_ptr misaligned
        (0x100, 0x102, False),  # xp_ptr misaligned
    ],
)
def test_pointer_validation_combinations(cap_ptr, xp_ptr, expected_valid):
    """Test various combinations of pointer values for validation."""
    caps = DeviceCapabilities(ext_cfg_cap_ptr=cap_ptr, ext_cfg_xp_cap_ptr=xp_ptr)

    if expected_valid:
        caps.validate()  # Should not raise
    else:
        with pytest.raises(ValueError):
            caps.validate()


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
