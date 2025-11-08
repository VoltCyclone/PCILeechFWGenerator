#!/usr/bin/env python3
"""
Unit tests for post-build validation system.

Tests the PostBuildValidator class and PostBuildValidationCheck to ensure
proper validation of firmware output for driver compatibility.
"""

import sys
from pathlib import Path
from unittest.mock import MagicMock, patch, mock_open
import struct

import pytest

# Add src to path for imports
sys.path.insert(0, str(Path(__file__).parent.parent))

from src.utils.post_build_validator import (
    PostBuildValidator,
    PostBuildValidationCheck
)


class TestPostBuildValidationCheck:
    """Test PostBuildValidationCheck result class."""

    def test_create_check_valid(self):
        """Test creating a valid check result."""
        check = PostBuildValidationCheck(
            is_valid=True,
            check_name="test_check",
            message="Test passed",
            severity="info"
        )
        
        assert check.is_valid is True
        assert check.check_name == "test_check"
        assert check.message == "Test passed"
        assert check.severity == "info"
        assert check.details == {}

    def test_create_check_with_details(self):
        """Test creating check with details."""
        details = {"value": "0x10ec", "expected": "0x10ec"}
        check = PostBuildValidationCheck(
            is_valid=True,
            check_name="vendor_id",
            message="Vendor ID valid",
            severity="info",
            details=details
        )
        
        assert check.details == details

    def test_create_check_invalid(self):
        """Test creating an invalid check result."""
        check = PostBuildValidationCheck(
            is_valid=False,
            check_name="missing_field",
            message="Required field missing",
            severity="error"
        )
        
        assert check.is_valid is False
        assert check.severity == "error"


class TestPostBuildValidator:
    """Test PostBuildValidator class."""

    @pytest.fixture
    def validator(self):
        """Create a validator instance with mock logger."""
        logger = MagicMock()
        return PostBuildValidator(logger)

    @pytest.fixture
    def valid_generation_result(self):
        """Create a valid generation result."""
        # Create valid config space (256 bytes)
        config_space = bytearray(256)
        struct.pack_into("<H", config_space, 0, 0x10ec)  # Vendor ID
        struct.pack_into("<H", config_space, 2, 0x8168)  # Device ID
        struct.pack_into("<H", config_space, 0x0e, 0x0200)  # Class code
        config_space[0x34] = 0x40  # Capability pointer
        config_space[0x40] = 0x01  # PM capability
        config_space[0x41] = 0x50  # Next cap pointer
        config_space[0x50] = 0x05  # MSI capability
        config_space[0x51] = 0x00  # End of list
        
        return {
            "config_space_data": {
                "raw_config_space": bytes(config_space),
                "config_space_hex": config_space.hex(),
                "device_info": {
                    "vendor_id": 0x10ec,
                    "device_id": 0x8168,
                    "class_code": 0x020000,
                    "revision_id": 0x10,
                    "subsystem_vendor_id": 0x10ec,
                    "subsystem_device_id": 0x8168,
                    "bars": [
                        {"size": 0x1000, "is_64bit": False, "is_io": False}
                    ]
                }
            },
            "template_context": {
                "device_config": {
                    "vendor_id": "0x10ec",
                    "device_id": "0x8168",
                    "class_code": "0x020000",
                    "revision_id": "0x10",
                    "subsystem_vendor_id": "0x10ec",
                    "subsystem_device_id": "0x8168"
                },
                "bar_config": {
                    "bars": [
                        {"size": 0x1000, "is_64bit": False, "is_io": False}
                    ]
                }
            }
        }

    def test_validator_initialization(self, validator):
        """Test validator initializes correctly."""
        assert validator.logger is not None
        assert validator.results == []

    def test_validate_pci_ids_all_present(self, validator):
        """Test PCI ID validation when all IDs are present."""
        device_info = {
            "vendor_id": 0x10ec,
            "device_id": 0x8168,
            "class_code": 0x020000,
            "revision_id": 0x10,
            "subsystem_vendor_id": 0x10ec,
            "subsystem_device_id": 0x8168
        }
        template_context = {
            "device_config": {
                "vendor_id": "0x10ec",
                "device_id": "0x8168",
                "class_code": "0x020000",
                "revision_id": "0x10",
                "subsystem_vendor_id": "0x10ec",
                "subsystem_device_id": "0x8168"
            }
        }
        
        validator._validate_pci_ids(device_info, template_context)
        
        # Should have 6 successful checks (one per ID field)
        assert len(validator.results) == 6
        assert all(r.is_valid for r in validator.results)
        assert all(r.severity == "info" for r in validator.results)

    def test_validate_pci_ids_missing_field(self, validator):
        """Test PCI ID validation with missing field."""
        device_info = {
            "vendor_id": 0x10ec,
            "device_id": 0x8168
        }
        template_context = {"device_config": {}}
        
        validator._validate_pci_ids(device_info, template_context)
        
        # Should have errors for missing fields
        errors = [r for r in validator.results if r.severity == "error"]
        assert len(errors) > 0
        assert any("Missing required PCI ID field" in r.message for r in errors)

    def test_validate_config_space_structure_valid_256(self, validator):
        """Test config space validation with valid 256-byte space."""
        config_space = bytearray(256)
        struct.pack_into("<H", config_space, 0, 0x10ec)
        struct.pack_into("<H", config_space, 2, 0x8168)
        
        config_space_data = {"raw_config_space": bytes(config_space)}
        
        validator._validate_config_space_structure(config_space_data)
        
        # Should have size check and header check
        assert len(validator.results) >= 2
        size_checks = [r for r in validator.results if "256 bytes" in r.message]
        assert len(size_checks) > 0

    def test_validate_config_space_structure_valid_4096(self, validator):
        """Test config space validation with valid 4096-byte space."""
        config_space = bytearray(4096)
        struct.pack_into("<H", config_space, 0, 0x10ec)
        struct.pack_into("<H", config_space, 2, 0x8168)
        
        config_space_data = {"raw_config_space": bytes(config_space)}
        
        validator._validate_config_space_structure(config_space_data)
        
        size_checks = [r for r in validator.results if "4096 bytes" in r.message]
        assert len(size_checks) > 0

    def test_validate_config_space_missing(self, validator):
        """Test config space validation with missing data."""
        config_space_data = {}
        
        validator._validate_config_space_structure(config_space_data)
        
        errors = [r for r in validator.results if r.severity == "error"]
        assert len(errors) > 0
        assert any("Missing raw config space" in r.message for r in errors)

    def test_validate_config_space_invalid_ids(self, validator):
        """Test config space validation with invalid IDs."""
        config_space = bytearray(256)
        struct.pack_into("<H", config_space, 0, 0xFFFF)  # Invalid VID
        struct.pack_into("<H", config_space, 2, 0xFFFF)  # Invalid DID
        
        config_space_data = {"raw_config_space": bytes(config_space)}
        
        validator._validate_config_space_structure(config_space_data)
        
        errors = [r for r in validator.results if r.severity == "error"]
        assert any("invalid device IDs" in r.message for r in errors)

    def test_validate_capabilities_with_msi(self, validator):
        """Test capability validation with MSI present."""
        config_space = bytearray(256)
        config_space[0x34] = 0x40  # Cap pointer
        config_space[0x40] = 0x05  # MSI capability
        config_space[0x41] = 0x00  # End of list
        
        config_space_data = {"raw_config_space": bytes(config_space)}
        template_context = {"device_config": {}}
        
        validator._validate_capabilities(config_space_data, template_context)
        
        # Should find MSI
        info_checks = [r for r in validator.results if "MSI" in r.message]
        assert len(info_checks) > 0

    def test_validate_capabilities_with_msix(self, validator):
        """Test capability validation with MSI-X present."""
        config_space = bytearray(256)
        config_space[0x34] = 0x40  # Cap pointer
        config_space[0x40] = 0x11  # MSI-X capability
        config_space[0x41] = 0x00  # End of list
        
        config_space_data = {"raw_config_space": bytes(config_space)}
        template_context = {"device_config": {}}
        
        validator._validate_capabilities(config_space_data, template_context)
        
        info_checks = [r for r in validator.results if "MSI-X" in r.message]
        assert len(info_checks) > 0

    def test_validate_capabilities_missing_interrupt(self, validator):
        """Test capability validation with no interrupt capability."""
        config_space = bytearray(256)
        config_space[0x34] = 0x40  # Cap pointer
        config_space[0x40] = 0x01  # PM capability (no MSI/MSI-X)
        config_space[0x41] = 0x00  # End of list
        
        config_space_data = {"raw_config_space": bytes(config_space)}
        template_context = {"device_config": {}}
        
        validator._validate_capabilities(config_space_data, template_context)
        
        warnings = [r for r in validator.results if r.severity == "warning"]
        assert any("No MSI or MSI-X" in r.message for r in warnings)

    def test_validate_capabilities_no_pointer(self, validator):
        """Test capability validation with no capability pointer."""
        config_space = bytearray(256)
        config_space[0x34] = 0x00  # No capabilities
        
        config_space_data = {"raw_config_space": bytes(config_space)}
        template_context = {"device_config": {}}
        
        validator._validate_capabilities(config_space_data, template_context)
        
        warnings = [r for r in validator.results if r.severity == "warning"]
        assert any("No capability pointer" in r.message for r in warnings)

    def test_walk_capabilities_circular_reference(self, validator):
        """Test capability walking handles circular references."""
        config_space = bytearray(256)
        config_space[0x40] = 0x01  # PM
        config_space[0x41] = 0x50  # Next
        config_space[0x50] = 0x05  # MSI
        config_space[0x51] = 0x40  # Back to PM (circular!)
        
        found = validator._walk_capabilities(config_space, 0x40)
        
        # Should find both without infinite loop
        assert 0x01 in found
        assert 0x05 in found
        assert len(found) == 2

    def test_validate_bar_configuration_valid(self, validator):
        """Test BAR validation with valid BARs."""
        device_info = {
            "bars": [
                {"size": 0x1000, "is_64bit": False, "is_io": False}
            ]
        }
        template_context = {"bar_config": {"bars": device_info["bars"]}}
        
        validator._validate_bar_configuration(device_info, template_context)
        
        # Should have info about the BAR
        info_checks = [r for r in validator.results if r.severity == "info"]
        assert any("BAR0" in r.message for r in info_checks)

    def test_validate_bar_configuration_no_bars(self, validator):
        """Test BAR validation with no BARs."""
        device_info = {"bars": []}
        template_context = {}
        
        validator._validate_bar_configuration(device_info, template_context)
        
        warnings = [r for r in validator.results if r.severity == "warning"]
        assert len(warnings) > 0

    def test_validate_bar_configuration_invalid_bars(self, validator):
        """Test BAR validation with zero-size BARs."""
        device_info = {
            "bars": [
                {"size": 0, "is_64bit": False, "is_io": False}
            ]
        }
        template_context = {}
        
        validator._validate_bar_configuration(device_info, template_context)
        
        errors = [r for r in validator.results if r.severity == "error"]
        assert any("No valid BARs" in r.message for r in errors)

    def test_validate_class_code_valid(self, validator):
        """Test class code validation with valid code."""
        device_info = {"class_code": 0x020000}  # Network controller
        
        validator._validate_class_code(device_info)
        
        info_checks = [r for r in validator.results if r.severity == "info"]
        assert any("Network Controller" in r.message for r in info_checks)

    def test_validate_class_code_zero(self, validator):
        """Test class code validation with zero code."""
        device_info = {"class_code": 0}
        
        validator._validate_class_code(device_info)
        
        errors = [r for r in validator.results if r.severity == "error"]
        assert any("0x000000" in r.message for r in errors)

    def test_validate_class_code_missing(self, validator):
        """Test class code validation with missing code."""
        device_info = {}
        
        validator._validate_class_code(device_info)
        
        errors = [r for r in validator.results if r.severity == "error"]
        assert any("missing" in r.message.lower() for r in errors)

    def test_get_class_name(self, validator):
        """Test class name lookup."""
        assert "Network Controller" in validator._get_class_name(0x02)
        assert "Mass Storage" in validator._get_class_name(0x01)
        assert "Display Controller" in validator._get_class_name(0x03)
        assert "Unknown" in validator._get_class_name(0xFF)

    def test_validate_capability_order_ascending(self, validator):
        """Test capability order validation with correct order."""
        config_space = bytearray(256)
        config_space[0x34] = 0x40
        config_space[0x40] = 0x01  # PM at 0x40
        config_space[0x41] = 0x50
        config_space[0x50] = 0x05  # MSI at 0x50
        config_space[0x51] = 0x00
        
        config_space_data = {"raw_config_space": bytes(config_space)}
        
        validator._validate_capability_order(config_space_data)
        
        info_checks = [r for r in validator.results if r.severity == "info"]
        assert any("ascending offset order" in r.message for r in info_checks)

    def test_validate_capability_order_pm_first(self, validator):
        """Test capability order validation with PM first."""
        config_space = bytearray(256)
        config_space[0x34] = 0x40
        config_space[0x40] = 0x01  # PM first
        config_space[0x41] = 0x00
        
        config_space_data = {"raw_config_space": bytes(config_space)}
        
        validator._validate_capability_order(config_space_data)
        
        info_checks = [r for r in validator.results if r.severity == "info"]
        assert any("first capability" in r.message for r in info_checks)

    @patch('pathlib.Path.exists')
    def test_validate_generated_files_all_present(self, mock_exists, validator):
        """Test file validation when all files exist."""
        mock_exists.return_value = True
        output_dir = Path("/fake/output")
        
        validator._validate_generated_files(output_dir)
        
        # Should have checks for expected files
        assert len(validator.results) > 0
        assert all(r.is_valid for r in validator.results)

    @patch('pathlib.Path.exists')
    def test_validate_generated_files_missing(self, mock_exists, validator):
        """Test file validation when files are missing."""
        mock_exists.return_value = False
        output_dir = Path("/fake/output")
        
        validator._validate_generated_files(output_dir)
        
        warnings = [r for r in validator.results if r.severity == "warning"]
        assert len(warnings) > 0

    def test_validate_build_output_complete(
        self, validator, valid_generation_result
    ):
        """Test complete build validation."""
        output_dir = Path("/fake/output")
        
        with patch('pathlib.Path.exists', return_value=True):
            is_valid, results = validator.validate_build_output(
                output_dir, valid_generation_result
            )
        
        # Should pass with valid data
        assert isinstance(is_valid, bool)
        assert isinstance(results, list)
        assert len(results) > 0
        
        # Should have no errors
        errors = [r for r in results if r.severity == "error"]
        assert len(errors) == 0

    def test_validate_build_output_with_errors(self, validator):
        """Test build validation with invalid data."""
        output_dir = Path("/fake/output")
        generation_result = {
            "config_space_data": {
                "raw_config_space": b"",  # Too short
                "device_info": {}
            },
            "template_context": {"device_config": {}}
        }
        
        with patch('pathlib.Path.exists', return_value=True):
            is_valid, results = validator.validate_build_output(
                output_dir, generation_result
            )
        
        # Should fail
        assert is_valid is False
        errors = [r for r in results if r.severity == "error"]
        assert len(errors) > 0

    def test_print_validation_report_no_results(self, validator):
        """Test printing report with no results."""
        validator.results = []
        
        # Should not raise and should log that there are no results
        with patch('src.utils.post_build_validator.log_info_safe') as mock_log:
            validator.print_validation_report()
            assert mock_log.called

    def test_print_validation_report_with_errors(self, validator):
        """Test printing report with errors."""
        validator.results = [
            PostBuildValidationCheck(
                is_valid=False,
                check_name="test",
                message="Test error",
                severity="error"
            )
        ]
        
        # Should log both info (summary) and errors
        with patch('src.utils.post_build_validator.log_error_safe') as mock_error, \
             patch('src.utils.post_build_validator.log_info_safe') as mock_info:
            validator.print_validation_report()
            assert mock_error.called
            assert mock_info.called

    def test_print_validation_report_with_warnings(self, validator):
        """Test printing report with warnings."""
        validator.results = [
            PostBuildValidationCheck(
                is_valid=True,
                check_name="test",
                message="Test warning",
                severity="warning"
            )
        ]
        
        # Should log both info (summary) and warnings
        with patch('src.utils.post_build_validator.log_warning_safe') as mock_warn, \
             patch('src.utils.post_build_validator.log_info_safe') as mock_info:
            validator.print_validation_report()
            assert mock_warn.called
            assert mock_info.called

    def test_get_bar_type_io(self, validator):
        """Test BAR type detection for I/O BAR."""
        bar = {"is_io": True, "is_64bit": False, "is_prefetchable": False}
        bar_type = validator._get_bar_type(bar)
        assert bar_type == "I/O"

    def test_get_bar_type_64bit_prefetchable(self, validator):
        """Test BAR type detection for 64-bit prefetchable."""
        bar = {"is_io": False, "is_64bit": True, "is_prefetchable": True}
        bar_type = validator._get_bar_type(bar)
        assert "64-bit" in bar_type
        assert "Prefetchable" in bar_type

    def test_get_bar_type_32bit_non_prefetchable(self, validator):
        """Test BAR type detection for 32-bit non-prefetchable."""
        bar = {"is_io": False, "is_64bit": False, "is_prefetchable": False}
        bar_type = validator._get_bar_type(bar)
        assert "32-bit" in bar_type
        assert "Non-Prefetchable" in bar_type

    def test_is_valid_bar_dict_valid(self, validator):
        """Test BAR validity check with valid dict BAR."""
        bar = {"size": 0x1000}
        assert validator._is_valid_bar(bar) is True

    def test_is_valid_bar_dict_invalid(self, validator):
        """Test BAR validity check with invalid dict BAR."""
        bar = {"size": 0}
        assert validator._is_valid_bar(bar) is False

    def test_is_valid_bar_object_valid(self, validator):
        """Test BAR validity check with valid object BAR."""
        bar = MagicMock()
        bar.size = 0x1000
        assert validator._is_valid_bar(bar) is True

    def test_is_valid_bar_object_invalid(self, validator):
        """Test BAR validity check with invalid object BAR."""
        bar = MagicMock()
        bar.size = 0
        assert validator._is_valid_bar(bar) is False


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
