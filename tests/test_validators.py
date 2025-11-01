"""
Unit tests for the generic validation framework.
"""

import pytest
from src.utils.validators import (
    BaseValidator,
    HexValidator,
    RangeValidator,
    ValidationResult,
    get_bar_size_validator,
    get_bdf_validator,
    get_class_code_validator,
    get_device_id_validator,
    get_vendor_id_validator,
    validate_device_config,
)


class TestValidationResult:
    """Test the ValidationResult class."""

    def test_init(self):
        """Test initialization of ValidationResult."""
        result = ValidationResult(True)
        assert result.is_valid
        assert result.errors == []
        assert result.warnings == []

    def test_add_error(self):
        """Test adding errors."""
        result = ValidationResult(True)
        result.add_error("Test error")
        assert not result.is_valid
        assert "Test error" in result.errors

    def test_add_warning(self):
        """Test adding warnings."""
        result = ValidationResult(True)
        result.add_warning("Test warning")
        assert result.is_valid  # Warnings don't invalidate
        assert "Test warning" in result.warnings

    def test_merge(self):
        """Test merging validation results."""
        result1 = ValidationResult(True)
        result1.add_warning("Warning 1")
        
        result2 = ValidationResult(False)
        result2.add_error("Error 1")
        
        result1.merge(result2)
        assert not result1.is_valid
        assert "Warning 1" in result1.warnings
        assert "Error 1" in result1.errors


class TestRangeValidator:
    """Test the RangeValidator class."""

    def test_valid_range(self):
        """Test validation with valid values."""
        validator = RangeValidator(min_value=0, max_value=100)
        
        result = validator.validate(50)
        assert result.is_valid
        
        result = validator.validate(0)
        assert result.is_valid
        
        result = validator.validate(100)
        assert result.is_valid

    def test_invalid_range(self):
        """Test validation with invalid values."""
        validator = RangeValidator(min_value=0, max_value=100)
        
        result = validator.validate(-1)
        assert not result.is_valid
        assert "must be >= 0" in result.errors[0]
        
        result = validator.validate(101)
        assert not result.is_valid
        assert "must be <= 100" in result.errors[0]

    def test_non_numeric(self):
        """Test validation with non-numeric values."""
        validator = RangeValidator(min_value=0, max_value=100)
        
        result = validator.validate("not a number")
        assert not result.is_valid
        assert "must be numeric" in result.errors[0]

    def test_min_only(self):
        """Test validation with only minimum value."""
        validator = RangeValidator(min_value=0)
        
        result = validator.validate(1000000)
        assert result.is_valid
        
        result = validator.validate(-1)
        assert not result.is_valid

    def test_max_only(self):
        """Test validation with only maximum value."""
        validator = RangeValidator(max_value=100)
        
        result = validator.validate(-1000000)
        assert result.is_valid
        
        result = validator.validate(101)
        assert not result.is_valid


class TestHexValidator:
    """Test the HexValidator class."""

    def test_valid_hex(self):
        """Test validation with valid hex values."""
        validator = HexValidator(expected_length=4)
        
        result = validator.validate("abcd")
        assert result.is_valid
        
        result = validator.validate("0x1234")
        assert result.is_valid
        
        result = validator.validate("ABCD")
        assert result.is_valid

    def test_invalid_hex(self):
        """Test validation with invalid hex values."""
        validator = HexValidator(expected_length=4)
        
        result = validator.validate("ghij")
        assert not result.is_valid
        assert "invalid hex characters" in result.errors[0]

    def test_wrong_length(self):
        """Test validation with wrong length."""
        validator = HexValidator(expected_length=4)
        
        result = validator.validate("abc")
        assert not result.is_valid
        assert "must be 4 hex digits" in result.errors[0]
        
        result = validator.validate("abcde")
        assert not result.is_valid

    def test_no_length_requirement(self):
        """Test validation without length requirement."""
        validator = HexValidator()
        
        result = validator.validate("a")
        assert result.is_valid
        
        result = validator.validate("abcdef1234567890")
        assert result.is_valid

    def test_non_string(self):
        """Test validation with non-string values."""
        validator = HexValidator()
        
        result = validator.validate(123)
        assert not result.is_valid
        assert "must be a string" in result.errors[0]


class TestSpecificValidators:
    """Test the specific validator instances."""

    def test_vendor_id_validator(self):
        """Test vendor ID validation."""
        validator = get_vendor_id_validator()
        
        result = validator.validate("8086")
        assert result.is_valid
        
        result = validator.validate("0x10de")
        assert result.is_valid
        
        result = validator.validate("123")  # Too short
        assert not result.is_valid

    def test_device_id_validator(self):
        """Test device ID validation."""
        validator = get_device_id_validator()
        
        result = validator.validate("1533")
        assert result.is_valid
        
        result = validator.validate("abcdef")  # Too long
        assert not result.is_valid

    def test_class_code_validator(self):
        """Test class code validation."""
        validator = get_class_code_validator()
        
        result = validator.validate("020000")
        assert result.is_valid
        
        result = validator.validate("0200")  # Too short
        assert not result.is_valid

    def test_bar_size_validator(self):
        """Test BAR size validation."""
        validator = get_bar_size_validator()
        
        result = validator.validate(0x1000)  # 4KB
        assert result.is_valid
        
        result = validator.validate(0x100000000)  # 4GB - valid power of 2
        assert result.is_valid
        
        result = validator.validate(-1)  # Negative
        assert not result.is_valid

    def test_bdf_validator(self):
        """Test BDF string validation."""
        validator = get_bdf_validator()
        
        result = validator.validate("0000:03:00.0")
        assert result.is_valid
        
        result = validator.validate("0000:ff:1f.7")
        assert result.is_valid
        
        result = validator.validate("0000:ff:1f.8")  # Function > 7
        assert not result.is_valid
        
        result = validator.validate("not:a:bdf")
        assert not result.is_valid


class TestDeviceConfigValidation:
    """Test device configuration validation."""

    def test_valid_config(self):
        """Test validation with valid device config."""
        config = {
            "vendor_id": "8086",
            "device_id": "1533",
            "device_bdf": "0000:03:00.0",
            "class_code": "020000"
        }
        
        result = validate_device_config(config)
        assert result.is_valid

    def test_missing_required(self):
        """Test validation with missing required fields."""
        config = {
            "vendor_id": "8086",
            # Missing device_id
        }
        
        result = validate_device_config(config)
        assert not result.is_valid
        assert any("Missing required field: device_id" in e for e in result.errors)

    def test_invalid_format(self):
        """Test validation with invalid formats."""
        config = {
            "vendor_id": "invalid",
            "device_id": "1533",
            "device_bdf": "not:a:bdf",
        }
        
        result = validate_device_config(config)
        assert not result.is_valid
        assert len(result.errors) >= 2  # Both vendor_id and device_bdf invalid

    def test_optional_fields(self):
        """Test validation with optional fields."""
        config = {
            "vendor_id": "8086",
            "device_id": "1533",
            "device_bdf": "0000:03:00.0",
            # class_code is optional
        }
        
        result = validate_device_config(config)
        assert result.is_valid
