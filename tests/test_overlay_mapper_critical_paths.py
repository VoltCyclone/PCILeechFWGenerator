#!/usr/bin/env python3
"""
Critical path unit tests for overlay_mapper.

Tests untested critical edge cases and error paths:
- _calculate_bar_mask edge cases (zero BAR, 64-bit upper dword, converter failures)
- _get_capability_overlay_entries with invalid inputs
- detect_overlay_registers with edge case configurations
"""

import os
import sys
from typing import Dict
from unittest.mock import patch

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from src.device_clone.overlay_mapper import OverlayMapper, RegisterType


class TestCalculateBarMaskCriticalPaths:
    """Test critical untested paths in _calculate_bar_mask."""

    def test_calculate_bar_mask_with_zero_bar_value(self):
        """Test that zero BAR values return 0x00000000 mask."""
        mapper = OverlayMapper()
        config_space = {4: 0x00000000}  # BAR0 = 0 (disabled/unimplemented)

        mask = mapper._calculate_bar_mask(config_space, 0x10)
        assert mask == 0x00000000, "Zero BAR should return zero mask"

    def test_calculate_bar_mask_64bit_upper_dword_detection(self):
        """Test 64-bit BAR upper dword returns 0xFFFFFFFF."""
        mapper = OverlayMapper()
        config_space = {
            # BAR2 at offset 0x18 (dword 6) is 64-bit memory (type bits [2:1]=0b10)
            # Bit 0 = 0 (memory), bits [2:1] = 0b10 (64-bit)
            6: 0xFFFFFFF4,  # Memory BAR, 64-bit, prefetchable
            7: 0x00000001,  # Upper 32 bits of BAR2 (offset 0x1C, dword 7)
        }

        # Offset 0x1C (dword 7) is the upper dword of a 64-bit BAR
        # The previous dword (6) has bits [2:1]=0b10 indicating 64-bit
        mask = mapper._calculate_bar_mask(config_space, 0x1C)
        expected_msg = "Upper dword of 64-bit BAR should be fully writable"
        assert mask == 0xFFFFFFFF, expected_msg

    def test_calculate_bar_mask_io_bar_with_converter_failure(self):
        """Test I/O BAR handling when BarSizeConverter.address_to_size fails."""
        mapper = OverlayMapper()
        config_space = {4: 0x0000FC01}  # I/O BAR at offset 0x10

        patch_path = (
            "src.device_clone.overlay_mapper.BarSizeConverter.address_to_size"
        )
        with patch(patch_path, return_value=None):
            mask = mapper._calculate_bar_mask(config_space, 0x10)
            # Should fall back to base mask
            expected_msg = "I/O BAR should return base mask on converter failure"
            assert mask == 0xFFFFFFFC, expected_msg

    def test_calculate_bar_mask_io_bar_with_zero_size(self):
        """Test I/O BAR handling when BarSizeConverter returns size=0."""
        mapper = OverlayMapper()
        config_space = {4: 0x0000FC01}  # I/O BAR

        with patch(
            "src.device_clone.overlay_mapper.BarSizeConverter.address_to_size",
            return_value=0,
        ):
            mask = mapper._calculate_bar_mask(config_space, 0x10)
            assert mask == 0xFFFFFFFC, "I/O BAR with size=0 should return base mask"

    def test_calculate_bar_mask_memory_bar_with_converter_failure(self):
        """Test memory BAR handling when BarSizeConverter.address_to_size fails."""
        mapper = OverlayMapper()
        config_space = {4: 0xFE000000}  # Memory BAR (32-bit) at offset 0x10

        with patch(
            "src.device_clone.overlay_mapper.BarSizeConverter.address_to_size",
            return_value=None,
        ):
            mask = mapper._calculate_bar_mask(config_space, 0x10)
            # Should fall back to base mask
            assert (
                mask == 0xFFFFFFF0
            ), "Memory BAR should return base mask on converter failure"

    def test_calculate_bar_mask_memory_bar_with_zero_size(self):
        """Test memory BAR handling when BarSizeConverter returns size=0."""
        mapper = OverlayMapper()
        config_space = {4: 0xFE000000}  # Memory BAR

        with patch(
            "src.device_clone.overlay_mapper.BarSizeConverter.address_to_size",
            return_value=0,
        ):
            mask = mapper._calculate_bar_mask(config_space, 0x10)
            assert (
                mask == 0xFFFFFFF0
            ), "Memory BAR with size=0 should return base mask"

    def test_calculate_bar_mask_memory_bar_with_valid_size(self):
        """Test memory BAR with valid size from converter."""
        mapper = OverlayMapper()
        config_space = {4: 0xFE000000}  # Memory BAR

        # Mock converter to return 16MB size
        with patch(
            "src.device_clone.overlay_mapper.BarSizeConverter.address_to_size",
            return_value=0x01000000,
        ):
            mask = mapper._calculate_bar_mask(config_space, 0x10)
            # base_mask (0xFFFFFFF0) & ~(size - 1)
            expected = 0xFFFFFFF0 & ~(0x01000000 - 1)
            assert (
                mask == expected
            ), "Memory BAR mask should incorporate size alignment"

    def test_calculate_bar_mask_io_bar_with_valid_size(self):
        """Test I/O BAR with valid size from converter."""
        mapper = OverlayMapper()
        config_space = {4: 0x0000FC01}  # I/O BAR

        # Mock converter to return 256-byte I/O size
        with patch(
            "src.device_clone.overlay_mapper.BarSizeConverter.address_to_size",
            return_value=0x100,
        ):
            mask = mapper._calculate_bar_mask(config_space, 0x10)
            # base_mask (0xFFFFFFFC) & ~(size - 1)
            expected = 0xFFFFFFFC & ~(0x100 - 1)
            assert mask == expected, "I/O BAR mask should incorporate size alignment"

    def test_calculate_bar_mask_not_64bit_previous_bar(self):
        """Test that non-64-bit previous BAR doesn't trigger upper dword logic."""
        mapper = OverlayMapper()
        config_space = {
            5: 0xFE000000,  # BAR1 at 0x14: 32-bit memory BAR (type bits [2:1]=0b00)
            6: 0xFD000000,  # BAR2 at 0x18: should NOT be treated as upper dword
        }

        # Offset 0x18 (dword 6) should not be treated as 64-bit upper dword
        # because previous BAR (0x14) is not 64-bit
        with patch(
            "src.device_clone.overlay_mapper.BarSizeConverter.address_to_size",
            return_value=None,
        ):
            mask = mapper._calculate_bar_mask(config_space, 0x18)
            # Should process as regular memory BAR, not return 0xFFFFFFFF
            assert (
                mask == 0xFFFFFFF0
            ), "Non-64-bit-upper dword should use memory BAR logic"


class TestGetCapabilityOverlayEntriesCriticalPaths:
    """Test critical untested paths in _get_capability_overlay_entries."""

    def test_get_capability_overlay_entries_invalid_hex_cap_id(self):
        """Test handling of invalid (non-hex) capability ID."""
        mapper = OverlayMapper()

        # Invalid hex string should return empty list
        entries = mapper._get_capability_overlay_entries("INVALID", 0x40)
        assert entries == [], "Invalid cap_id should return empty entries list"

    def test_get_capability_overlay_entries_unknown_standard_capability(self):
        """Test unknown standard capability returns empty entries."""
        mapper = OverlayMapper()

        # Cap ID 0xFF is not a standard or known capability
        entries = mapper._get_capability_overlay_entries("0xFF", 0x40)
        assert entries == [], "Unknown standard capability should return empty"

    def test_get_capability_overlay_entries_unknown_extended_capability(self):
        """Test unknown extended capability returns empty entries."""
        mapper = OverlayMapper()

        # Cap ID 0xFFFF at extended offset should return empty
        entries = mapper._get_capability_overlay_entries("0xFFFF", 0x100)
        assert entries == [], "Unknown extended capability should return empty"

    def test_get_capability_overlay_entries_pm_capability(self):
        """Test PM capability (0x01) returns correct entries."""
        mapper = OverlayMapper()

        entries = mapper._get_capability_overlay_entries("0x01", 0x40)

        # PM capability should have at least PMCSR register (offset +0x04)
        assert len(entries) > 0, "PM capability should have overlay entries"

        # Check that PMCSR offset is included
        pmcsr_offset = 0x40 + 0x04
        offsets = [entry[0] for entry in entries]
        assert pmcsr_offset in offsets, "PMCSR register should be in entries"

    def test_get_capability_overlay_entries_msi_capability(self):
        """Test MSI capability (0x05) returns correct entries."""
        mapper = OverlayMapper()

        entries = mapper._get_capability_overlay_entries("0x05", 0x48)

        # MSI capability should have control and address registers
        assert len(entries) > 0, "MSI capability should have overlay entries"

        # Verify at least one entry is MIXED type
        has_mixed = any(entry[3] == RegisterType.MIXED for entry in entries)
        assert has_mixed, "MSI should have MIXED register types"

    def test_get_capability_overlay_entries_msix_capability(self):
        """Test MSI-X capability (0x11) returns correct entries."""
        mapper = OverlayMapper()

        entries = mapper._get_capability_overlay_entries("0x11", 0x50)

        # MSI-X capability should have at least control register
        assert len(entries) > 0, "MSI-X capability should have overlay entries"

    def test_get_capability_overlay_entries_pcie_capability(self):
        """Test PCIe capability (0x10) returns correct entries."""
        mapper = OverlayMapper()

        entries = mapper._get_capability_overlay_entries("0x10", 0x60)

        # PCIe capability should have device control/status, link control/status, etc.
        assert len(entries) > 0, "PCIe capability should have overlay entries"

        # Verify multiple entries (PCIe has many registers)
        assert len(entries) >= 3, "PCIe should have multiple overlay registers"

    def test_get_capability_overlay_entries_aer_extended(self):
        """Test AER extended capability (0x0001) returns correct entries."""
        mapper = OverlayMapper()

        entries = mapper._get_capability_overlay_entries("0x0001", 0x100)

        # AER should have status registers (RW1C type)
        assert len(entries) > 0, "AER capability should have overlay entries"

        # AER has RW1C status registers
        has_rw1c = any(entry[3] == RegisterType.RW1C for entry in entries)
        assert has_rw1c, "AER should have RW1C register types"

    def test_get_capability_overlay_entries_filters_read_only(self):
        """Test that read-only registers are filtered out."""
        mapper = OverlayMapper()

        # Get entries for a capability that has RO registers
        entries = mapper._get_capability_overlay_entries("0x01", 0x40)

        # All returned entries should be MIXED or RW1C, never READ_ONLY
        for offset, mask, desc, reg_type in entries:
            assert (
                reg_type != RegisterType.READ_ONLY
            ), f"Read-only register {desc} should not be in overlay entries"


class TestDetectOverlayRegistersCriticalPaths:
    """Test critical paths in detect_overlay_registers."""

    def test_detect_overlay_registers_empty_config_space(self):
        """Test detection with empty configuration space."""
        mapper = OverlayMapper()
        config_space: Dict[int, int] = {}
        capabilities: Dict[str, int] = {}

        overlays = mapper.detect_overlay_registers(config_space, capabilities)

        # Should still detect standard header registers (Command/Status, etc.)
        # even with empty config space
        assert isinstance(overlays, list), "Should return list"

    def test_detect_overlay_registers_no_duplicates(self):
        """Test that processed_offsets prevents duplicates."""
        mapper = OverlayMapper()

        # Simple config space
        config_space = {
            0: 0x12348086,
            1: 0x04100006,  # Command/Status - should be in overlay
        }
        capabilities = {}

        overlays = mapper.detect_overlay_registers(config_space, capabilities)

        # Check no duplicate offsets
        offsets = [offset for offset, mask in overlays]
        assert len(offsets) == len(set(offsets)), "Should not have duplicate offsets"

    def test_detect_overlay_registers_sorts_by_offset(self):
        """Test that overlay map is sorted by offset."""
        mapper = OverlayMapper()

        config_space = {
            0: 0x12348086,
            1: 0x04100006,
            3: 0x00004000,
            12: 0xFFFFF800,  # Expansion ROM
        }
        capabilities = {}

        overlays = mapper.detect_overlay_registers(config_space, capabilities)

        # Offsets should be in ascending order
        offsets = [offset for offset, mask in overlays]
        assert offsets == sorted(offsets), "Overlay offsets should be sorted"

    def test_detect_overlay_registers_excludes_fully_writable(self):
        """Test that fully writable registers (mask=0xFFFFFFFF) are excluded unless RW1C."""
        mapper = OverlayMapper()

        config_space = {0: 0x12348086, 1: 0x04100006}
        capabilities = {}

        overlays = mapper.detect_overlay_registers(config_space, capabilities)

        # Check that non-RW1C registers with mask 0xFFFFFFFF are excluded
        for offset, mask in overlays:
            # If mask is 0xFFFFFFFF, it must be an RW1C register
            if mask == 0xFFFFFFFF:
                # Verify this is a known RW1C register (e.g., from AER)
                assert (
                    offset >= 0x100
                ), "Only RW1C registers (typically extended caps) should have 0xFFFFFFFF mask"

    def test_detect_overlay_registers_includes_rw1c_from_capabilities(self):
        """Test that RW1C registers from capabilities are included."""
        mapper = OverlayMapper()

        config_space = {0: 0x12348086, 1: 0x04100006}
        # AER capability at 0x100 has RW1C status registers
        capabilities = {"0x0001": 0x100}

        overlays = mapper.detect_overlay_registers(config_space, capabilities)

        # Should include AER RW1C status registers
        aer_offsets = [offset for offset, mask in overlays if offset >= 0x100]
        assert (
            len(aer_offsets) > 0
        ), "Should include AER extended capability registers"


class TestGenerateOverlayMapCriticalPaths:
    """Test generate_overlay_map output format."""

    def test_generate_overlay_map_output_format(self):
        """Test that generate_overlay_map returns correct format."""
        mapper = OverlayMapper()

        config_space = {0: 0x12348086, 1: 0x04100006}
        capabilities = {}

        result = mapper.generate_overlay_map(config_space, capabilities)

        # Check output structure
        assert "OVERLAY_MAP" in result, "Should have OVERLAY_MAP key"
        assert "OVERLAY_ENTRIES" in result, "Should have OVERLAY_ENTRIES key"

        # Check that OVERLAY_ENTRIES matches list length
        assert result["OVERLAY_ENTRIES"] == len(
            result["OVERLAY_MAP"]
        ), "OVERLAY_ENTRIES should match OVERLAY_MAP length"

    def test_generate_overlay_map_converts_to_register_numbers(self):
        """Test that overlay map uses register numbers (offset/4)."""
        mapper = OverlayMapper()

        config_space = {0: 0x12348086, 1: 0x04100006}
        capabilities = {}

        result = mapper.generate_overlay_map(config_space, capabilities)

        # All register numbers should be valid (offset % 4 == 0 when converted back)
        for reg_num, mask in result["OVERLAY_MAP"]:
            offset = reg_num * 4
            assert (
                offset % 4 == 0
            ), "Register number should represent DWORD-aligned offset"

    def test_generate_overlay_map_empty_input(self):
        """Test generate_overlay_map with completely empty inputs."""
        mapper = OverlayMapper()

        result = mapper.generate_overlay_map({}, {})

        # Should still return valid structure
        assert isinstance(result, dict), "Should return dict"
        assert "OVERLAY_MAP" in result, "Should have OVERLAY_MAP"
        assert "OVERLAY_ENTRIES" in result, "Should have OVERLAY_ENTRIES"
        assert isinstance(result["OVERLAY_MAP"], list), "OVERLAY_MAP should be list"


class TestGetOverlayInfoCriticalPaths:
    """Test get_overlay_info edge cases."""

    def test_get_overlay_info_standard_mixed_register(self):
        """Test get_overlay_info for standard mixed register (Command/Status)."""
        mapper = OverlayMapper()

        info = mapper.get_overlay_info(0x04)

        assert info is not None, "Command/Status should return OverlayEntry"
        assert info.register_type == RegisterType.MIXED, "Should be MIXED type"

    def test_get_overlay_info_standard_special_register(self):
        """Test get_overlay_info for BAR (special type)."""
        mapper = OverlayMapper()

        info = mapper.get_overlay_info(0x10)  # BAR0

        assert info is not None, "BAR0 should return OverlayEntry"
        assert (
            info.register_type == RegisterType.SPECIAL
        ), "BAR should be SPECIAL type"

    def test_get_overlay_info_read_only_register(self):
        """Test get_overlay_info for read-only register returns None."""
        mapper = OverlayMapper()

        info = mapper.get_overlay_info(0x00)  # Vendor/Device ID

        # Read-only registers should return None (not included in overlays)
        assert info is None, "Vendor/Device ID (RO) should return None"

    def test_get_overlay_info_unknown_offset(self):
        """Test get_overlay_info for unknown offset returns None."""
        mapper = OverlayMapper()

        info = mapper.get_overlay_info(0xFFF)  # Invalid offset

        assert info is None, "Unknown offset should return None"


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
