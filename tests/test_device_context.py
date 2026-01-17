#!/usr/bin/env python3
"""
Unit tests for dynamic device context generation in PCI capability processor.

These tests verify that device features in the context are generated dynamically
based on the actual capabilities found in the device configuration space.
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
from pcileechfwgenerator.pci_capability.types import CapabilityInfo, CapabilityType


class TestDynamicDeviceContext:
    """Test suite for dynamic device context generation."""

    def setup_method(self):
        """Set up test fixtures."""
        # Create a minimal configuration space with vendor/device ID
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
                0x85: "00",  # Next capability pointer: None
                0x86: "0302",  # PM Capabilities (PME from D3hot, D1 supported) (little-endian)
                0x88: "0800",  # PM Control/Status (D0 power state) (little-endian)
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

    def test_rule_engine_extract_device_context(self):
        """Test RuleEngine._extract_device_context method."""
        context = self.rule_engine._extract_device_context(self.config_space)

        # Basic device identification
        assert context["vendor_id"] == 0x1234
        assert context["device_id"] == 0x5678

        # Device class information
        assert context["class_code"] == 0x03
        assert context["subclass_code"] == 0x0C

        # Command register settings
        assert "memory_space_enabled" in context
        assert "io_space_enabled" in context
        assert "bus_master_enabled" in context

        # Status register information
        assert context["capabilities_supported"] == True

        # Default feature settings
        assert "enable_msi" in context
        assert "enable_msix" in context
        assert "enable_pcie" in context
        assert "enable_power_management" in context
        assert "enable_relaxed_ordering" in context
        assert "enable_no_snoop" in context
        assert "enable_extended_tag" in context
        assert "enable_d1_power_state" in context
        assert "enable_d2_power_state" in context
        assert "enable_d3hot_power_state" in context
        assert "enable_pme" in context
        assert "aspm_control" in context
        assert "msi_address" in context
        assert "msi_vector" in context

    def test_capability_processor_get_device_context(self):
        """Test CapabilityProcessor._get_device_context method."""
        # Mock the _update_device_features method to verify it's called
        with patch.object(self.processor, "_update_device_features") as mock_update:
            context = self.processor._get_device_context()

            # Verify _update_device_features was called
            mock_update.assert_called_once()

            # Verify the context contains basic device information
            assert context["vendor_id"] == 0x1234
            assert context["device_id"] == 0x5678

            # Verify caching works
            self.processor._get_device_context()
            # Should still be called only once
            mock_update.assert_called_once()

    def test_update_device_features_msi(self):
        """Test _update_device_features updates MSI-related features."""
        # Create a capability info for MSI
        msi_cap = CapabilityInfo(
            offset=0x50,
            cap_id=0x05,  # MSI
            cap_type=CapabilityType.STANDARD,
            next_ptr=0x60,
            name="MSI",
            version=0,
        )

        # Create a mock capabilities dictionary
        capabilities = {0x50: msi_cap}

        # Create a context to update
        context = {"vendor_id": 0x1234, "device_id": 0x5678}

        # Call _update_device_features
        self.processor._update_device_features(context, capabilities)

        # Verify MSI features were updated
        assert context["enable_msi"] == True
        assert context["msi_enabled"] == True
        assert context["msi_multiple_message_capable"] == 1
        assert context["msi_multiple_message_enabled"] == 0

    def test_update_device_features_msix(self):
        """Test _update_device_features updates MSI-X-related features."""
        # Create a capability info for MSI-X
        msix_cap = CapabilityInfo(
            offset=0x60,
            cap_id=0x11,  # MSI-X
            cap_type=CapabilityType.STANDARD,
            next_ptr=0x70,
            name="MSI-X",
            version=0,
        )

        # Create a mock capabilities dictionary
        capabilities = {0x60: msix_cap}

        # Create a context to update
        context = {"vendor_id": 0x1234, "device_id": 0x5678}

        # Call _update_device_features
        self.processor._update_device_features(context, capabilities)

        # Verify MSI-X features were updated
        assert context["enable_msix"] == True
        assert context["msix_enabled"] == True
        assert context["msix_function_mask"] == False
        assert context["msix_table_size"] == 8

    def test_update_device_features_pcie(self):
        """Test _update_device_features updates PCIe-related features."""
        # Create a capability info for PCIe
        pcie_cap = CapabilityInfo(
            offset=0x70,
            cap_id=0x10,  # PCIe
            cap_type=CapabilityType.STANDARD,
            next_ptr=0x80,
            name="PCI Express",
            version=0,
        )

        # Create a mock capabilities dictionary
        capabilities = {0x70: pcie_cap}

        # Create a context to update
        context = {"vendor_id": 0x1234, "device_id": 0x5678}

        # Call _update_device_features
        self.processor._update_device_features(context, capabilities)

        # Verify PCIe features were updated
        assert context["enable_pcie"] == True
        assert context["pcie_device_type"] == 0  # Endpoint
        assert context["pcie_relaxed_ordering_enabled"] == True
        assert context["pcie_no_snoop_enabled"] == False
        assert context["pcie_max_payload_size"] == 128
        assert context["pcie_aspm_control"] == 1  # L0s

    def test_update_device_features_power_management(self):
        """Test _update_device_features updates Power Management-related features."""
        # Create a capability info for Power Management
        pm_cap = CapabilityInfo(
            offset=0x80,
            cap_id=0x01,  # Power Management
            cap_type=CapabilityType.STANDARD,
            next_ptr=0x00,
            name="Power Management",
            version=0,
        )

        # Create a mock capabilities dictionary
        capabilities = {0x80: pm_cap}

        # Create a context to update
        context = {"vendor_id": 0x1234, "device_id": 0x5678}

        # Call _update_device_features
        self.processor._update_device_features(context, capabilities)

        # Verify Power Management features were updated
        assert context["enable_power_management"] == True
        # Skip version check as it depends on the mock data
        assert "pm_d1_support" in context
        assert "pm_d2_support" in context
        assert "pm_d3hot_support" in context
        assert "enable_d1_power_state" in context
        assert "enable_d2_power_state" in context
        assert "enable_d3hot_power_state" in context

    def test_update_device_features_all_capabilities(self):
        """Test _update_device_features with all capabilities."""
        # Use the processor's discover_all_capabilities method
        capabilities = self.processor.discover_all_capabilities()

        # Create a context to update
        context = {"vendor_id": 0x1234, "device_id": 0x5678}

        # Call _update_device_features
        self.processor._update_device_features(context, capabilities)

        # Verify all features were updated
        assert context["enable_msi"] == True
        assert context["enable_msix"] == True
        assert context["enable_pcie"] == True
        assert context["enable_power_management"] == True

        # Verify device type specific settings are present
        assert "enable_relaxed_ordering" in context
        assert "enable_no_snoop" in context
        assert "enable_extended_tag" in context

        # Verify power management settings are present
        assert "enable_d1_power_state" in context
        assert "enable_d2_power_state" in context
        assert "enable_d3hot_power_state" in context
        assert "enable_pme" in context

        # Verify ASPM settings are present
        assert "aspm_control" in context

    def test_msi_patches_use_dynamic_context(self):
        """Test that MSI patches use the dynamically generated context."""
        # Create a capability info for MSI
        msi_cap = CapabilityInfo(
            offset=0x50,
            cap_id=0x05,  # MSI
            cap_type=CapabilityType.STANDARD,
            next_ptr=0x60,
            name="MSI",
            version=0,
        )

        # Mock _get_device_context to return a controlled context
        mock_context = {
            "msi_address": 0xFEE00123,  # Custom MSI address
            "msi_vector": 0x0042,  # Custom MSI vector
        }
        with patch.object(
            self.processor, "_get_device_context", return_value=mock_context
        ):
            # Create MSI patches
            patches = self.processor._create_msi_patches(msi_cap)

            # Verify patches use the custom values from the context
            # Find MSI address patch (typically at offset 0x54)
            address_patch = next((p for p in patches if p.offset == 0x54), None)
            assert address_patch is not None
            assert int.from_bytes(address_patch.new_data, "little") == 0xFEE00123

            # Find MSI vector patch (typically at offset 0x58 or 0x5C)
            vector_patch = next((p for p in patches if p.offset in (0x58, 0x5C)), None)
            assert vector_patch is not None
            assert int.from_bytes(vector_patch.new_data, "little") == 0x0042

    def test_pcie_patches_use_dynamic_context(self):
        """Test that PCIe patches use the dynamically generated context."""
        # Create a capability info for PCIe
        pcie_cap = CapabilityInfo(
            offset=0x70,
            cap_id=0x10,  # PCIe
            cap_type=CapabilityType.STANDARD,
            next_ptr=0x80,
            name="PCI Express",
            version=0,
        )

        # Mock _get_device_context to return a controlled context
        mock_context = {
            "pcie_device_type": 4,  # Switch Upstream Port
            "enable_relaxed_ordering": False,
            "enable_no_snoop": False,
            "aspm_control": 2,  # L1
            "enable_link_training": True,
        }
        with patch.object(
            self.processor, "_get_device_context", return_value=mock_context
        ):
            # Create PCIe patches
            patches = self.processor._create_pcie_patches(pcie_cap)

            # Verify patches use the custom values from the context
            # Find PCIe device type patch (typically at offset 0x70 or 0x72)
            device_type_patch = next(
                (p for p in patches if p.offset in (0x70, 0x72)),
                None,
            )
            assert device_type_patch is not None
            assert (
                int.from_bytes(device_type_patch.new_data, "little") >> 4
            ) & 0xF == 4

            # Find PCIe link control patch (typically at offset 0x80)
            link_ctrl_patch = next(
                (p for p in patches if p.offset == 0x80),
                None,
            )
            assert link_ctrl_patch is not None
            assert (
                int.from_bytes(link_ctrl_patch.new_data, "little") & 0x3 == 2
            )  # ASPM L1
            assert (
                int.from_bytes(link_ctrl_patch.new_data, "little") & 0x20 == 0x20
            )  # Link training

    def test_integration_with_capability_processor(self):
        """Test integration with CapabilityProcessor."""
        # Process capabilities with MODIFY action
        from pcileechfwgenerator.pci_capability.types import PruningAction

        results = self.processor.process_capabilities([PruningAction.MODIFY])

        # Verify processing completed successfully
        assert results["capabilities_found"] > 0

        # Verify the summary includes the MODIFY action
        assert "MODIFY" in results["processing_summary"]
        assert "modified_capabilities" in results["processing_summary"]["MODIFY"]

    def test_capability_summary_includes_device_context(self):
        """Test that capability summary includes the device context."""
        summary = self.processor.get_capability_summary()

        # Verify the summary includes the device context
        assert "device_context" in summary

        # Verify the device context includes dynamically generated features
        context = summary["device_context"]
        assert "enable_msi" in context
        assert "enable_msix" in context
        assert "enable_pcie" in context
        assert "enable_power_management" in context


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
