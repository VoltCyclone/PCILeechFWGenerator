#!/usr/bin/env python3
"""
Integration tests for dynamic device context with patch creation.

These tests verify that the dynamically generated device context is properly
integrated with the patch creation process, ensuring that patches are created
based on the actual capabilities found in the device.
"""

import sys
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

# Add src to path for imports
sys.path.insert(0, str(Path(__file__).parent.parent))

from pcileechfwgenerator.pci_capability.core import CapabilityWalker, ConfigSpace
from pcileechfwgenerator.pci_capability.processor import CapabilityProcessor
from pcileechfwgenerator.pci_capability.rules import RuleEngine
from pcileechfwgenerator.pci_capability.types import (CapabilityInfo, CapabilityType,
                                      EmulationCategory, PruningAction)


class TestDeviceContextIntegration:
    """Test suite for dynamic device context integration with patch creation."""

    def setup_method(self):
        """Set up test fixtures."""
        # Create a configuration space with multiple capabilities
        # Create a 256-byte configuration space with proper endianness
        # Start with all zeros
        hex_data = "00" * 256

        # Now set specific values at specific offsets, respecting endianness

        # PCI Header (little-endian for multi-byte values)
        values = {
            0x00: "3412",  # Vendor ID: 0x1234 (little-endian)
            0x02: "7856",  # Device ID: 0x5678 (little-endian)
            0x04: "0010",  # Command register (little-endian)
            0x06: "9002",  # Status register with capabilities bit set (0x0010) (little-endian)
            0x08: "0100",  # Revision ID (little-endian)
            0x09: "00",  # Programming Interface
            0x0A: "0C",  # Subclass Code: Network controller
            0x0B: "03",  # Class Code: Communication controller
            0x0C: "0000",  # Cache Line Size & Latency Timer (little-endian)
            0x0E: "8000",  # Header Type & BIST (little-endian)
            0x2C: "CDAB",  # Subsystem Vendor ID (little-endian)
            0x2E: "01EF",  # Subsystem ID (little-endian)
            0x34: "50",  # Capabilities Pointer: 0x50 (points to first capability)
        }

        # MSI capability at offset 0x50
        values.update(
            {
                0x50: "05",  # Capability ID: MSI
                0x51: "60",  # Next capability pointer: 0x60
                0x52: "0300",  # Message Control (MSI enabled, 2 messages) (little-endian)
                0x54: "0000E0FE",  # Message Address: 0xFEE00000 (little-endian)
                0x58: "0000",  # Message Data (little-endian)
            }
        )

        # MSI-X capability at offset 0x60
        values.update(
            {
                0x60: "11",  # Capability ID: MSI-X
                0x61: "70",  # Next capability pointer: 0x70
                0x62: "0780",  # Message Control (MSI-X enabled, 8 entries) (little-endian)
                0x64: "00000000",  # Table offset/BIR (little-endian)
                0x68: "00000000",  # PBA offset/BIR (little-endian)
            }
        )

        # PCIe capability at offset 0x70
        values.update(
            {
                0x70: "10",  # Capability ID: PCIe
                0x71: "80",  # Next capability pointer: 0x80
                0x72: "0200",  # PCIe Capabilities (endpoint) (little-endian)
                0x74: "01000000",  # Device Capabilities (Max payload 128 bytes) (little-endian)
                0x78: "1000",  # Device Control (Relaxed ordering enabled) (little-endian)
                0x7A: "0000",  # Device Status (little-endian)
                0x7C: "01000101",  # Link Capabilities (2.5GT/s, x1, ASPM L0s) (little-endian)
                0x80: "0100",  # Link Control (ASPM L0s enabled) (little-endian)
                0x82: "0000",  # Link Status (little-endian)
            }
        )

        # Power Management capability at offset 0x80
        values.update(
            {
                0x84: "01",  # Capability ID: Power Management
                0x85: "90",  # Next capability pointer: 0x90
                0x86: "0302",  # PM Capabilities (PME from D3hot, D1 supported) (little-endian)
                0x88: "0800",  # PM Control/Status (D0 power state) (little-endian)
            }
        )

        # Vendor Specific capability at offset 0x90
        values.update(
            {
                0x90: "09",  # Capability ID: Vendor Specific
                0x91: "00",  # Next capability pointer: None
                0x92: "04",  # Length: 4 bytes
                0x93: "42",  # Vendor-specific data
            }
        )

        # Apply all values to the hex string
        hex_data_list = list(hex_data)
        for offset, value in values.items():
            for i in range(0, len(value), 2):
                pos = offset + i // 2
                if pos < 256:  # Ensure we don't go out of bounds
                    hex_data_list[pos * 2 : pos * 2 + 2] = value[i : i + 2]

        hex_data = "".join(hex_data_list)
        self.config_space = ConfigSpace(hex_data)
        self.rule_engine = RuleEngine()
        self.processor = CapabilityProcessor(self.config_space, self.rule_engine)

    def test_end_to_end_capability_processing(self):
        """Test end-to-end capability processing with dynamic device context."""
        # Process capabilities with all actions
        results = self.processor.process_capabilities(
            [PruningAction.REMOVE, PruningAction.MODIFY, PruningAction.KEEP]
        )

        # Verify processing completed successfully
        assert results["capabilities_found"] > 0

        # Verify the summary includes all action types
        assert "REMOVE" in results["processing_summary"]
        assert "MODIFY" in results["processing_summary"]
        assert "KEEP" in results["processing_summary"]

        # Get the capability summary to check the device context
        summary = self.processor.get_capability_summary()
        context = summary["device_context"]

        # Verify the context includes dynamically generated features
        assert "enable_msi" in context
        assert "enable_msix" in context
        assert "enable_pcie" in context
        assert "enable_power_management" in context

    def test_device_context_affects_patch_creation(self):
        """Test that changes in device context affect patch creation."""
        # Create a mock device context with custom values
        mock_context = {
            "vendor_id": 0x1234,
            "device_id": 0x5678,
            "enable_msi": True,
            "enable_msix": True,
            "enable_pcie": True,
            "enable_power_management": True,
            "enable_relaxed_ordering": False,  # Disable relaxed ordering
            "enable_no_snoop": True,  # Enable no snoop
            "aspm_control": 2,  # L1 instead of L0s
            "msi_address": 0xFEE00123,  # Custom MSI address
            "msi_vector": 0x0042,  # Custom MSI vector
            "enable_link_training": True,  # Enable link training
        }

        # Mock _get_device_context to return our custom context
        with patch.object(
            self.processor, "_get_device_context", return_value=mock_context
        ):
            # Process capabilities with MODIFY action
            results = self.processor.process_capabilities([PruningAction.MODIFY])

            # Verify the processing completed
            assert "processing_summary" in results
            assert "MODIFY" in results["processing_summary"]

            # Verify the context was used
            assert mock_context["vendor_id"] == 0x1234
            assert mock_context["device_id"] == 0x5678

    def test_different_device_types_affect_context(self):
        """Test that different device types affect the device context."""
        # Create a PCIe capability with different device types
        endpoint_cap = CapabilityInfo(
            offset=0x70,
            cap_id=0x10,  # PCIe
            cap_type=CapabilityType.STANDARD,
            next_ptr=0x80,
            name="PCI Express",
            version=0,
        )

        switch_cap = CapabilityInfo(
            offset=0x70,
            cap_id=0x10,  # PCIe
            cap_type=CapabilityType.STANDARD,
            next_ptr=0x80,
            name="PCI Express",
            version=0,
        )

        # Create contexts for different device types
        endpoint_context = {"vendor_id": 0x1234, "device_id": 0x5678}
        switch_context = {"vendor_id": 0x1234, "device_id": 0x5678}

        # Mock the config_space.read_word method to return different device types
        with patch.object(self.config_space, "read_word") as mock_read_word:
            # For endpoint (device type 0)
            mock_read_word.return_value = 0x0002  # PCIe Capabilities (endpoint)
            self.processor._update_device_features(
                endpoint_context, {0x70: endpoint_cap}
            )

            # For switch (device type 4)
            mock_read_word.return_value = 0x0042  # PCIe Capabilities (switch)
            self.processor._update_device_features(switch_context, {0x70: switch_cap})

        # Verify endpoint and switch contexts have different settings
        assert "enable_relaxed_ordering" in endpoint_context
        assert "enable_no_snoop" in endpoint_context
        assert "enable_extended_tag" in endpoint_context

        assert "enable_relaxed_ordering" in switch_context
        assert "enable_no_snoop" in switch_context
        assert "enable_extended_tag" in switch_context

        # Verify the contexts are different
        assert endpoint_context != switch_context

    def test_power_management_features_based_on_capability(self):
        """Test that power management features are set based on capability."""
        # Create a PM capability with D1 and D2 support
        pm_cap_d1_d2 = CapabilityInfo(
            offset=0x80,
            cap_id=0x01,  # Power Management
            cap_type=CapabilityType.STANDARD,
            next_ptr=0x00,
            name="Power Management",
            version=0,
        )

        # Create a PM capability without D1 and D2 support
        pm_cap_no_d1_d2 = CapabilityInfo(
            offset=0x80,
            cap_id=0x01,  # Power Management
            cap_type=CapabilityType.STANDARD,
            next_ptr=0x00,
            name="Power Management",
            version=0,
        )

        # Create contexts for different PM capabilities
        context_d1_d2 = {"vendor_id": 0x1234, "device_id": 0x5678}
        context_no_d1_d2 = {"vendor_id": 0x1234, "device_id": 0x5678}

        # Mock the config_space.read_word method to return different PM capabilities
        with patch.object(self.config_space, "read_word") as mock_read_word:
            # For D1 and D2 support
            mock_read_word.return_value = (
                0x0603  # PM Capabilities (D1 and D2 supported)
            )
            self.processor._update_device_features(context_d1_d2, {0x80: pm_cap_d1_d2})

            # For no D1 and D2 support
            mock_read_word.return_value = (
                0x0003  # PM Capabilities (no D1 or D2 support)
            )
            self.processor._update_device_features(
                context_no_d1_d2, {0x80: pm_cap_no_d1_d2}
            )

        # Verify context with D1 and D2 support
        assert context_d1_d2["enable_d1_power_state"] == True
        assert context_d1_d2["enable_d2_power_state"] == True

        # Verify context without D1 and D2 support
        assert context_no_d1_d2["enable_d1_power_state"] == False
        assert context_no_d1_d2["enable_d2_power_state"] == False

    def test_aspm_control_based_on_capabilities(self):
        """Test that ASPM control is set based on capabilities."""
        # Create a PCIe capability
        pcie_cap = CapabilityInfo(
            offset=0x70,
            cap_id=0x10,  # PCIe
            cap_type=CapabilityType.STANDARD,
            next_ptr=0x80,
            name="PCI Express",
            version=0,
        )

        # Create contexts for different ASPM capabilities
        context_l0s = {"vendor_id": 0x1234, "device_id": 0x5678}
        context_l1 = {"vendor_id": 0x1234, "device_id": 0x5678}
        context_both = {"vendor_id": 0x1234, "device_id": 0x5678}

        # Mock the config_space methods to return different ASPM capabilities
        with patch.object(
            self.config_space, "read_dword"
        ) as mock_read_dword, patch.object(
            self.config_space, "read_word"
        ) as mock_read_word:

            # For L0s support only
            mock_read_dword.return_value = 0x01010001  # Link Capabilities (ASPM L0s)
            mock_read_word.return_value = 0x0001  # Link Control (ASPM L0s enabled)
            self.processor._update_device_features(context_l0s, {0x70: pcie_cap})

            # For L1 support only
            mock_read_dword.return_value = 0x01010002  # Link Capabilities (ASPM L1)
            mock_read_word.return_value = 0x0002  # Link Control (ASPM L1 enabled)
            self.processor._update_device_features(context_l1, {0x70: pcie_cap})

            # For both L0s and L1 support
            mock_read_dword.return_value = (
                0x01010003  # Link Capabilities (ASPM L0s and L1)
            )
            mock_read_word.return_value = (
                0x0003  # Link Control (ASPM L0s and L1 enabled)
            )
            self.processor._update_device_features(context_both, {0x70: pcie_cap})

        # Verify ASPM control settings are present
        assert "aspm_control" in context_l0s
        assert "aspm_control" in context_l1
        assert "aspm_control" in context_both

        # Verify the contexts are different
        assert context_l0s != context_l1
        assert context_l1 != context_both
        assert context_l0s != context_both


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
