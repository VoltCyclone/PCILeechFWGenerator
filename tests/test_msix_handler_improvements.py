#!/usr/bin/env python3
"""
Unit tests for MSI-X handler improvements.

These tests focus on the new functionality and edge cases
added in the improved MSI-X handler.
"""

from unittest.mock import patch

import pytest

from src.pci_capability.core import ConfigSpace

from src.pci_capability.msix import MSIXCapabilityHandler

from src.pci_capability.types import (CapabilityInfo, CapabilityType,
                                      PCICapabilityID)


class TestMSIXHandlerImprovements:
    """Test improved MSI-X handler functionality."""

    @pytest.fixture
    def config_space(self):
        """Create a mock config space with MSI-X capability."""
        # Create a 256-byte configuration space as hex string
        hex_data = "00" * 256

        # Set up MSI-X capability at offset 0x60 using hex string manipulation
        hex_data_list = list(hex_data)

        # Set up PCI header with capabilities support
        values = {
            # PCI Status Register (0x06) - set capabilities list bit (bit 4)
            0x06: "10",  # Status register low byte with capabilities bit set
            0x07: "00",  # Status register high byte
            # Capabilities Pointer (0x34) - point to MSI-X capability at 0x60
            0x34: "60",  # Capabilities pointer to MSI-X
            # MSI-X capability at offset 0x60
            0x60: "11",  # MSI-X capability ID
            0x61: "00",  # Next pointer (end of list)
            0x62: "07",  # Message Control low byte (table size = 8, encoded as 7)
            0x63: "80",  # Message Control high byte (MSI-X enabled bit set)
            0x64: "00",  # Table BIR/Offset low
            0x65: "10",  # Table BIR/Offset
            0x66: "00",  # Table BIR/Offset
            0x67: "00",  # Table BIR/Offset high
            0x68: "01",  # PBA BIR/Offset low
            0x69: "20",  # PBA BIR/Offset
            0x6A: "00",  # PBA BIR/Offset
            0x6B: "00",  # PBA BIR/Offset high
        }

        # Apply the values to the hex string
        for offset, value in values.items():
            hex_data_list[offset * 2 : offset * 2 + 2] = list(value)

        hex_data = "".join(hex_data_list)
        return ConfigSpace(hex_data)

    @pytest.fixture
    def handler(self, config_space):
        """Create MSI-X handler with mock config space."""
        return MSIXCapabilityHandler(config_space)

    def test_atomic_msix_patches_validation_failure(self, handler):
        """Test atomic patch creation with validation failure."""
        operations = [
            ("disable", 0x70, None),  # Invalid offset
            ("enable", 0x60, None),  # Valid offset
        ]

        patches = handler.create_atomic_msix_patches(operations)
        assert len(patches) == 0  # Should return empty on validation failure

    def test_atomic_msix_patches_success(self, handler):
        """Test successful atomic patch creation."""
        operations = [
            ("disable", 0x60, None),
            ("set_table_size", 0x60, 16),
        ]

        patches = handler.create_atomic_msix_patches(operations)
        assert len(patches) == 2  # Should create two patches

    def test_msix_enable_patch(self, handler):
        """Test MSI-X enable patch creation."""
        # Since our fixture has MSI-X already enabled, the enable patch should return None
        patch = handler.create_msix_enable_patch(0x60)
        assert patch is None  # No patch needed when already enabled

        # Verify MSI-X is indeed enabled in our fixture
        msix_info = handler.get_msix_capability_info(0x60)
        assert msix_info["msix_enable"] is True

    def test_msix_requirements_check(self, handler):
        """Test MSI-X requirements analysis."""
        requirements = handler.check_msix_requirements()

        assert requirements["has_msix"] is True
        assert requirements["msix_count"] == 1
        assert requirements["total_vectors"] == 8  # Table size from fixture

    def test_msix_requirements_with_device_context(self, handler):
        """Test MSI-X requirements with device context."""
        device_context = {"required_msix_vectors": 16}
        requirements = handler.check_msix_requirements(device_context)

        assert len(requirements["issues"]) > 0  # Should flag insufficient vectors

    def test_capability_removal_validation(self, handler, config_space):
        """Test that capability removal validates current pointers."""
        # Mock reading capabilities pointer to return wrong value
        with patch.object(config_space, "read_byte", return_value=0x70):
            patches = handler.create_msix_removal_patches(0x60)
            assert (
                len(patches) == 0
            )  # Should not create patches due to validation failure

    def test_msix_validation_with_constants(self, handler):
        """Test MSI-X validation using new constants."""
        is_valid, errors = handler.validate_msix_capability(0x60)
        assert is_valid is True
        assert len(errors) == 0

    def test_msix_table_size_validation(self, handler):
        """Test table size validation with constants."""
        # Test invalid table size
        patch = handler.create_msix_table_size_patch(
            0x60, 3000
        )  # > MSIX_MAX_TABLE_SIZE
        assert patch is None

        # Test valid table size
        patch = handler.create_msix_table_size_patch(0x60, 64)
        assert patch is not None


if __name__ == "__main__":
    pytest.main([__file__])
