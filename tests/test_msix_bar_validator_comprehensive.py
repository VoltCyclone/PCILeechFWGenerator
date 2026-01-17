#!/usr/bin/env python3
"""
Comprehensive test coverage for MSI-X BAR Validator.

This module provides comprehensive tests for the MSI-X BAR validator
which currently has 0% coverage.
"""

from typing import Any, Dict, List

import pytest

from pcileechfwgenerator.pci_capability.msix_bar_validator import (
    _validate_bar_configuration_for_msix, _validate_basic_bar_configuration,
    _validate_driver_compatibility, _validate_msix_capability_structure,
    _validate_msix_memory_layout, _validate_performance_considerations,
    _validate_reserved_region_conflicts, auto_fix_msix_configuration,
    print_validation_report, validate_msix_bar_configuration)


class TestMSIXBARValidatorBasic:
    """Test basic MSI-X BAR validation functionality."""

    def test_valid_configuration(self):
        """Test validation of a valid MSI-X configuration."""
        bars = [
            {"bar": 0, "type": "memory", "size": 0x10000, "prefetchable": False},
            {"bar": 1, "type": "memory", "size": 0x8000, "prefetchable": False},
        ]

        capabilities = [
            {
                "cap_id": 0x11,  # MSI-X
                "table_size": 7,  # 8 vectors (encoded as N-1)
                "table_bar": 1,
                "table_offset": 0x1000,
                "pba_bar": 1,
                "pba_offset": 0x2000,
            }
        ]

        device_info = {"vendor_id": 0x8086, "device_id": 0x1572}

        is_valid, errors, warnings = validate_msix_bar_configuration(
            bars, capabilities, device_info
        )

        assert is_valid
        assert len(errors) == 0

    def test_no_msix_capability(self):
        """Test validation when no MSI-X capability is present."""
        bars = [
            {"bar": 0, "type": "memory", "size": 0x4000, "prefetchable": False},
        ]

        capabilities = [
            {"cap_id": 0x05},  # MSI capability, not MSI-X
        ]

        is_valid, errors, warnings = validate_msix_bar_configuration(
            bars, capabilities, None
        )

        assert is_valid  # Should be valid without MSI-X
        assert len(errors) == 0

    def test_missing_device_info(self):
        """Test validation with missing device info."""
        bars = [{"bar": 0, "type": "memory", "size": 0x4000}]
        capabilities = []

        is_valid, errors, warnings = validate_msix_bar_configuration(
            bars, capabilities, None
        )

        assert is_valid
        assert len(errors) == 0


class TestMSIXCapabilityStructureValidation:
    """Test MSI-X capability structure validation."""

    def test_invalid_table_size(self):
        """Test validation of invalid table sizes."""
        errors = []
        warnings = []

        # Test table size 0 (invalid)
        msix_cap = {"table_size": -1}  # Encodes to 0 vectors
        _validate_msix_capability_structure(msix_cap, errors, warnings)
        assert any("table size 0 is invalid" in error for error in errors)

        # Test table size > 2048 (invalid)
        errors.clear()
        msix_cap = {"table_size": 2048}  # Encodes to 2049 vectors
        _validate_msix_capability_structure(msix_cap, errors, warnings)
        assert any("table size 2049 is invalid" in error for error in errors)

    def test_invalid_bir_values(self):
        """Test validation of invalid BIR values."""
        errors = []
        warnings = []

        msix_cap = {
            "table_size": 7,
            "table_bar": 6,  # Invalid BIR
            "pba_bar": 7,  # Invalid BIR
        }

        _validate_msix_capability_structure(msix_cap, errors, warnings)

        assert any("table BIR 6 is invalid" in error for error in errors)
        assert any("PBA BIR 7 is invalid" in error for error in errors)

    def test_alignment_requirements(self):
        """Test offset alignment validation."""
        errors = []
        warnings = []

        msix_cap = {
            "table_size": 7,
            "table_bar": 0,
            "pba_bar": 0,
            "table_offset": 0x1001,  # Not 8-byte aligned
            "pba_offset": 0x2003,  # Not 8-byte aligned
        }

        _validate_msix_capability_structure(msix_cap, errors, warnings)

        assert any(
            "table offset 0x1001 is not 8-byte aligned" in error for error in errors
        )
        assert any(
            "PBA offset 0x2003 is not 8-byte aligned" in error for error in errors
        )

    def test_performance_alignment_warnings(self):
        """Test performance-related alignment warnings."""
        errors = []
        warnings = []

        msix_cap = {
            "table_size": 7,
            "table_offset": 0x1008,  # 8-byte aligned but not 4KB aligned
            "pba_offset": 0x2010,  # 8-byte aligned but not 4KB aligned
        }

        _validate_msix_capability_structure(msix_cap, errors, warnings)

        assert len(errors) == 0  # Should be valid
        assert any(
            "table offset 0x1008 is not 4KB aligned" in warning for warning in warnings
        )
        assert any(
            "PBA offset 0x2010 is not 4KB aligned" in warning for warning in warnings
        )


class TestBARConfigurationValidation:
    """Test BAR configuration validation for MSI-X."""

    def test_missing_bars(self):
        """Test validation when required BARs are missing."""
        bars = [
            {"bar": 0, "type": "memory", "size": 0x4000},
        ]

        msix_cap = {
            "table_bar": 1,  # Missing BAR
            "pba_bar": 2,  # Missing BAR
        }

        errors = []
        warnings = []

        _validate_bar_configuration_for_msix(bars, msix_cap, errors, warnings)

        assert any("MSI-X table BAR 1 is not configured" in error for error in errors)
        assert any("MSI-X PBA BAR 2 is not configured" in error for error in errors)

    def test_wrong_bar_type(self):
        """Test validation of wrong BAR types."""
        bars = [
            {"bar": 0, "type": "io", "size": 0x4000},  # Wrong type
            {"bar": 1, "type": "memory", "size": 0x4000},
        ]

        msix_cap = {
            "table_bar": 0,
            "pba_bar": 1,
        }

        errors = []
        warnings = []

        _validate_bar_configuration_for_msix(bars, msix_cap, errors, warnings)

        assert any(
            "table BAR 0 must be memory type, got io" in error for error in errors
        )

    def test_prefetchable_warnings(self):
        """Test warnings for prefetchable BARs."""
        bars = [
            {"bar": 0, "type": "memory", "size": 0x4000, "prefetchable": True},
            {"bar": 1, "type": "memory", "size": 0x4000, "prefetchable": True},
        ]

        msix_cap = {
            "table_bar": 0,
            "pba_bar": 1,
        }

        errors = []
        warnings = []

        _validate_bar_configuration_for_msix(bars, msix_cap, errors, warnings)

        assert len(errors) == 0
        assert any("table BAR 0 is prefetchable" in warning for warning in warnings)
        assert any("PBA BAR 1 is prefetchable" in warning for warning in warnings)


class TestMemoryLayoutValidation:
    """Test MSI-X memory layout validation."""

    def test_table_exceeds_bar_size(self):
        """Test validation when table exceeds BAR size."""
        bars = [
            {"bar": 0, "type": "memory", "size": 0x1000},  # 4KB BAR
        ]

        msix_cap = {
            "table_size": 255,  # 256 vectors * 16 bytes = 4KB
            "table_bar": 0,
            "table_offset": 0x100,  # Table would extend beyond BAR
            "pba_bar": 0,
            "pba_offset": 0x2000,
        }

        errors = []
        warnings = []

        _validate_msix_memory_layout(bars, msix_cap, errors, warnings)

        assert any(
            "MSI-X table" in error and "exceeds BAR 0 size" in error for error in errors
        )

    def test_pba_exceeds_bar_size(self):
        """Test validation when PBA exceeds BAR size."""
        bars = [
            {"bar": 0, "type": "memory", "size": 0x1000},  # 4KB BAR
        ]

        msix_cap = {
            "table_size": 7,  # 8 vectors
            "table_bar": 0,
            "table_offset": 0x000,
            "pba_bar": 0,
            "pba_offset": 0xFFF,  # PBA would extend beyond BAR
        }

        errors = []
        warnings = []

        _validate_msix_memory_layout(bars, msix_cap, errors, warnings)

        assert any(
            "MSI-X PBA" in error and "exceeds BAR 0 size" in error for error in errors
        )

    def test_table_pba_overlap_same_bar(self):
        """Test detection of table/PBA overlap in same BAR."""
        bars = [
            {"bar": 0, "type": "memory", "size": 0x10000},
        ]

        msix_cap = {
            "table_size": 7,  # 8 vectors * 16 bytes = 128 bytes
            "table_bar": 0,
            "table_offset": 0x1000,
            "pba_bar": 0,
            "pba_offset": 0x1040,  # Overlaps with table (table ends at 0x1080)
        }

        errors = []
        warnings = []

        _validate_msix_memory_layout(bars, msix_cap, errors, warnings)

        assert any(
            "table" in error and "PBA" in error and "overlap" in error
            for error in errors
        )

    def test_no_overlap_different_bars(self):
        """Test no overlap detection when structures are in different BARs."""
        bars = [
            {"bar": 0, "type": "memory", "size": 0x4000},
            {"bar": 1, "type": "memory", "size": 0x4000},
        ]

        msix_cap = {
            "table_size": 15,  # 16 vectors
            "table_bar": 0,
            "table_offset": 0x0000,
            "pba_bar": 1,
            "pba_offset": 0x0000,
        }

        errors = []
        warnings = []

        _validate_msix_memory_layout(bars, msix_cap, errors, warnings)

        # Should not report overlap since they're in different BARs
        assert not any("overlap" in error for error in errors)


class TestReservedRegionConflicts:
    """Test reserved region conflict detection."""

    def test_device_control_conflict(self):
        """Test conflict with device control registers."""
        errors = []

        _validate_reserved_region_conflicts(
            0,  # BAR 0
            0x0000,  # Start at beginning
            0x0800,  # End overlaps with device control region
            "table",
            errors,
        )

        assert any("Device Control Registers" in error for error in errors)

    def test_custom_pio_conflict(self):
        """Test conflict with custom PIO region."""
        errors = []

        _validate_reserved_region_conflicts(
            0,  # BAR 0
            0x5000,  # Start in middle of custom PIO region
            0x6000,  # End in custom PIO region
            "PBA",
            errors,
        )

        assert any("Custom PIO Region" in error for error in errors)

    def test_no_conflict_different_bar(self):
        """Test no conflict when in different BAR."""
        errors = []

        _validate_reserved_region_conflicts(
            1,  # BAR 1 (not BAR 0)
            0x0000,  # Even though overlaps, it's not BAR 0
            0x1000,
            "table",
            errors,
        )

        assert len(errors) == 0  # No conflicts should be reported


class TestBasicBARValidation:
    """Test basic BAR validation without MSI-X."""

    def test_invalid_bar_index(self):
        """Test validation of invalid BAR indices."""
        bars = [
            {"bar": -1, "type": "memory", "size": 0x4000},
            {"bar": 6, "type": "memory", "size": 0x4000},
        ]

        errors = []
        warnings = []

        _validate_basic_bar_configuration(bars, errors, warnings)

        assert any("Invalid BAR index -1" in error for error in errors)
        assert any("Invalid BAR index 6" in error for error in errors)

    def test_zero_size_warning(self):
        """Test warning for zero-sized BARs."""
        bars = [
            {"bar": 0, "type": "memory", "size": 0},
        ]

        errors = []
        warnings = []

        _validate_basic_bar_configuration(bars, errors, warnings)

        assert any("BAR 0 has size 0" in warning for warning in warnings)

    def test_non_power_of_two_warning(self):
        """Test warning for non-power-of-two BAR sizes."""
        bars = [
            {"bar": 0, "type": "memory", "size": 0x3000},  # Not power of 2
        ]

        errors = []
        warnings = []

        _validate_basic_bar_configuration(bars, errors, warnings)

        assert any("not power of 2" in warning for warning in warnings)


class TestDriverCompatibility:
    """Test driver compatibility validation."""

    def test_different_bar_warning(self):
        """Test warning when table and PBA are in different BARs."""
        bars = [
            {"bar": 0, "type": "memory", "size": 0x4000},
            {"bar": 1, "type": "memory", "size": 0x4000},
        ]

        msix_cap = {
            "table_size": 7,
            "table_bar": 0,
            "pba_bar": 1,  # Different BAR
        }

        errors = []
        warnings = []
        device_info = {"vendor_id": 0x1234, "device_id": 0x5678}

        _validate_driver_compatibility(bars, msix_cap, errors, warnings, device_info)

        assert any("different BARs" in warning for warning in warnings)

    def test_large_vector_count_warning(self):
        """Test warning for large vector counts."""
        msix_cap = {
            "table_size": 511,  # 512 vectors
            "table_bar": 0,
            "pba_bar": 0,
        }

        errors = []
        warnings = []
        device_info = {"vendor_id": 0x1234, "device_id": 0x5678}

        _validate_driver_compatibility([], msix_cap, errors, warnings, device_info)

        assert any("Large MSI-X table size (512)" in warning for warning in warnings)

    def test_intel_specific_warnings(self):
        """Test Intel-specific compatibility warnings."""
        msix_cap = {
            "table_size": 255,  # 256 vectors
            "table_bar": 0,
            "pba_bar": 0,
        }

        errors = []
        warnings = []
        device_info = {"vendor_id": 0x8086, "device_id": 0x1234}  # Intel

        _validate_driver_compatibility([], msix_cap, errors, warnings, device_info)

        assert any(
            "Intel devices with >128 MSI-X vectors" in warning for warning in warnings
        )

    def test_nvidia_specific_warnings(self):
        """Test NVIDIA-specific compatibility warnings."""
        msix_cap = {
            "table_size": 7,
            "table_bar": 1,  # Not BAR 0
            "pba_bar": 1,
        }

        errors = []
        warnings = []
        device_info = {"vendor_id": 0x10DE, "device_id": 0x1234}  # NVIDIA

        _validate_driver_compatibility([], msix_cap, errors, warnings, device_info)

        assert any(
            "NVIDIA drivers typically expect MSI-X structures in BAR 0" in warning
            for warning in warnings
        )


class TestPerformanceConsiderations:
    """Test performance-related validation."""

    def test_cache_alignment_warnings(self):
        """Test warnings for suboptimal cache alignment."""
        msix_cap = {
            "table_size": 7,
            "table_offset": 0x1008,  # Not 64-byte aligned
            "pba_offset": 0x2010,  # Not 64-byte aligned
        }

        warnings = []
        device_info = {"vendor_id": 0x1234, "device_id": 0x5678}

        _validate_performance_considerations([], msix_cap, warnings, device_info)

        assert any(
            "table offset 0x1008 is not 64-byte aligned" in warning
            for warning in warnings
        )
        assert any(
            "PBA offset 0x2010 is not 64-byte aligned" in warning
            for warning in warnings
        )

    def test_excessive_vectors_warning(self):
        """Test warning for excessive vector counts on low-end devices."""
        msix_cap = {
            "table_size": 127,  # 128 vectors
        }

        warnings = []
        device_info = {"vendor_id": 0x1234, "device_id": 0x1000}  # Low device ID

        _validate_performance_considerations([], msix_cap, warnings, device_info)

        assert any(
            "may be excessive for this device class" in warning for warning in warnings
        )

    def test_oversized_bar_warning(self):
        """Test warning for oversized BARs."""
        bars = [
            {"bar": 0, "type": "memory", "size": 0x100000},  # 1MB BAR
        ]

        msix_cap = {
            "table_size": 7,  # 8 vectors * 16 bytes = 128 bytes
            "table_bar": 0,
        }

        warnings = []
        device_info = {"vendor_id": 0x1234, "device_id": 0x5678}

        _validate_performance_considerations(bars, msix_cap, warnings, device_info)

        assert any(
            "much larger than MSI-X requirements" in warning for warning in warnings
        )


class TestAutoFixFunctionality:
    """Test automatic configuration fixes."""

    def test_offset_alignment_fix(self):
        """Test automatic offset alignment fixing."""
        bars = [
            {"bar": 0, "type": "memory", "size": 0x10000},
        ]

        capabilities = [
            {
                "cap_id": 0x11,
                "table_size": 7,
                "table_bar": 0,
                "table_offset": 0x1001,  # Not aligned
                "pba_bar": 0,
                "pba_offset": 0x2003,  # Not aligned
            }
        ]

        fixed_bars, fixed_caps, fix_messages = auto_fix_msix_configuration(
            bars, capabilities
        )

        msix_cap = fixed_caps[0]
        # Offsets are aligned to 4KB and then moved out of reserved region (first 0x8000)
        assert msix_cap["table_offset"] == 0x8000
        assert msix_cap["pba_offset"] >= 0x8000

        # Fix messages may include both alignment and reserved-region moves; accept either
        assert any("Aligned MSI-X table offset" in msg for msg in fix_messages) or any(
            "Moved MSI-X table" in msg for msg in fix_messages
        )
        assert any("Aligned MSI-X PBA offset" in msg for msg in fix_messages) or any(
            "Moved MSI-X PBA" in msg for msg in fix_messages
        )

    def test_overlap_resolution(self):
        """Test automatic overlap resolution."""
        bars = [
            {"bar": 0, "type": "memory", "size": 0x10000},
        ]

        capabilities = [
            {
                "cap_id": 0x11,
                "table_size": 15,  # 16 vectors * 16 bytes = 256 bytes
                "table_bar": 0,
                "table_offset": 0x1000,
                "pba_bar": 0,
                "pba_offset": 0x1080,  # Overlaps with table
            }
        ]

        fixed_bars, fixed_caps, fix_messages = auto_fix_msix_configuration(
            bars, capabilities
        )

        msix_cap = fixed_caps[0]
        table_end = msix_cap["table_offset"] + (16 * 16)  # 256 bytes

        # PBA should be moved after table (or a message should indicate avoidance)
        assert msix_cap["pba_offset"] >= table_end
        assert any("avoid table overlap" in msg for msg in fix_messages) or any(
            "Moved MSI-X PBA" in msg for msg in fix_messages
        )

    def test_bar_size_increase(self):
        """Test automatic BAR size increase."""
        bars = [
            {"bar": 0, "type": "memory", "size": 0x1000},  # Too small
        ]

        capabilities = [
            {
                "cap_id": 0x11,
                "table_size": 255,  # 256 vectors
                "table_bar": 0,
                "table_offset": 0x1000,  # Table won't fit
                "pba_bar": 0,
                "pba_offset": 0x2000,
            }
        ]

        fixed_bars, fixed_caps, fix_messages = auto_fix_msix_configuration(
            bars, capabilities
        )

        bar = next(b for b in fixed_bars if b["bar"] == 0)
        required_size = 0x1000 + (256 * 16)  # Offset + table size

        assert bar["size"] >= required_size
        assert any("Increased BAR 0 size" in msg for msg in fix_messages)

    def test_reserved_region_avoidance(self):
        """Test moving structures away from reserved regions."""
        bars = [
            {"bar": 0, "type": "memory", "size": 0x20000},
        ]

        capabilities = [
            {
                "cap_id": 0x11,
                "table_size": 7,
                "table_bar": 0,
                "table_offset": 0x1000,  # In reserved region
                "pba_bar": 0,
                "pba_offset": 0x2000,  # In reserved region
            }
        ]

        fixed_bars, fixed_caps, fix_messages = auto_fix_msix_configuration(
            bars, capabilities
        )

        msix_cap = fixed_caps[0]
        assert msix_cap["table_offset"] >= 0x8000  # After reserved region
        assert msix_cap["pba_offset"] >= 0x8000  # After reserved region

        assert any("avoid reserved region" in msg for msg in fix_messages)

    def test_no_msix_capability(self):
        """Test auto-fix with no MSI-X capability."""
        bars = [{"bar": 0, "type": "memory", "size": 0x4000}]
        capabilities = []

        fixed_bars, fixed_caps, fix_messages = auto_fix_msix_configuration(
            bars, capabilities
        )

        assert fixed_bars == bars
        assert fixed_caps == capabilities
        assert len(fix_messages) == 0


class TestValidationReporting:
    """Test validation report printing."""

    def test_print_validation_report(self, capsys):
        """Test validation report output."""
        errors = ["Error 1", "Error 2"]
        warnings = ["Warning 1"]
        device_info = {"vendor_id": 0x8086, "device_id": 0x1234}

        print_validation_report(False, errors, warnings, device_info)

        captured = capsys.readouterr()
        output = captured.out

        assert "8086:1234" in output
        assert "INVALID" in output
        assert "Error 1" in output
        assert "Error 2" in output
        assert "Warning 1" in output

    def test_print_valid_report(self, capsys):
        """Test printing of valid configuration report."""
        print_validation_report(True, [], [], None)

        captured = capsys.readouterr()
        output = captured.out

        assert "VALID" in output
        assert "No issues found!" in output


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
