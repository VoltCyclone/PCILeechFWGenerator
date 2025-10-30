"""Unit tests for BDF validator."""
import pytest
from src.utils.validators import BDFValidator, get_bdf_validator


class TestBDFValidator:
    """Test BDF validation functionality."""
    
    def test_valid_full_format(self):
        """Test validation of full BDF format."""
        validator = BDFValidator()
        
        valid_bdfs = [
            "0000:00:00.0",
            "0000:01:00.0", 
            "0001:02:03.4",
            "ffff:ff:ff.7",
            "FFFF:FF:FF.7",
            "1234:ab:cd.5",
            "ABCD:EF:12.3",
        ]
        
        for bdf in valid_bdfs:
            result = validator.validate(bdf)
            assert result.valid, f"Failed to validate valid BDF: {bdf}, errors: {result.errors}"
    
    def test_valid_short_format(self):
        """Test validation of short BDF format (no domain)."""
        validator = BDFValidator(strict=False)
        
        valid_bdfs = [
            "00:00.0",
            "01:00.0",
            "ff:ff.7",
            "FF:FF.7",
            "ab:cd.5",
            "AB:CD.5",
        ]
        
        for bdf in valid_bdfs:
            result = validator.validate(bdf)
            assert result.valid, f"Failed to validate valid short BDF: {bdf}, errors: {result.errors}"
    
    def test_valid_optional_domain(self):
        """Test validation with optional domain (2-4 hex digits)."""
        validator = BDFValidator(strict=False)
        
        valid_bdfs = [
            "00:01:00.0",    # 2-digit domain
            "000:01:00.0",   # 3-digit domain  
            "0000:01:00.0",  # 4-digit domain
            "ff:01:00.0",    # 2-digit domain
            "fff:01:00.0",   # 3-digit domain
            "ffff:01:00.0",  # 4-digit domain
        ]
        
        for bdf in valid_bdfs:
            result = validator.validate(bdf)
            assert result.valid, f"Failed to validate valid BDF: {bdf}, errors: {result.errors}"
    
    def test_invalid_formats(self):
        """Test rejection of invalid BDF formats."""
        validator = BDFValidator()
        
        invalid_bdfs = [
            "",                   # Empty
            "0000:00:00",        # Missing function
            "0000:00:00.",       # Missing function digit
            "0000:00:00.8",      # Function out of range (0-7)
            "0000:00:00.9",      # Function out of range
            "0000:00:00.a",      # Function out of range (hex > 7)
            "0000:00:00.00",     # Function too long
            "00000:00:00.0",     # Domain too long
            "0:00:00.0",         # Domain too short (requires at least 2)
            "0000:0:00.0",       # Bus too short
            "0000:000:00.0",     # Bus too long
            "0000:00:0.0",       # Device too short
            "0000:00:000.0",     # Device too long
            "0000:gg:00.0",      # Invalid hex in bus
            "0000:00:gg.0",      # Invalid hex in device
            "0000:00:00.g",      # Invalid hex in function
            "0000:00:00:0",      # Wrong separator
            "0000.00.00.0",      # Wrong separator
            "0000-00-00.0",      # Wrong separator
            " 0000:00:00.0",     # Leading space
            "0000:00:00.0 ",     # Trailing space
            "0000: 00:00.0",     # Space in middle
        ]
        
        for bdf in invalid_bdfs:
            result = validator.validate(bdf)
            assert not result.valid, f"Incorrectly validated invalid BDF: {bdf}"
    
    def test_strict_mode(self):
        """Test strict mode requires full format."""
        strict_validator = BDFValidator(strict=True)
        
        # These should fail in strict mode
        short_bdfs = [
            "00:00.0",
            "01:00.0", 
            "ff:ff.7",
        ]
        
        for bdf in short_bdfs:
            result = strict_validator.validate(bdf)
            assert not result.valid, f"Strict mode should reject short format: {bdf}"
            assert "XXXX:XX:XX.X" in result.errors[0]
        
        # Full format should still work
        result = strict_validator.validate("0000:01:00.0")
        assert result.valid
    
    def test_case_insensitive(self):
        """Test that hex digits are case-insensitive."""
        validator = BDFValidator()
        
        bdfs = [
            ("0000:aa:bb.c", "0000:AA:BB.C"),
            ("FFFF:EE:DD.7", "ffff:ee:dd.7"),
            ("AbCd:Ef:12.3", "abcd:ef:12.3"),
        ]
        
        for bdf1, bdf2 in bdfs:
            result1 = validator.validate(bdf1)
            result2 = validator.validate(bdf2)
            assert result1.valid == result2.valid
    
    def test_non_string_input(self):
        """Test handling of non-string inputs."""
        validator = BDFValidator()
        
        non_strings = [None, 123, [], {}, object()]
        
        for value in non_strings:
            result = validator.validate(value)
            assert not result.valid
            assert "must be a string" in result.errors[0]
    
    def test_whitespace_handling(self):
        """Test that whitespace is properly handled."""
        validator = BDFValidator()
        
        # Leading/trailing whitespace should NOT be stripped
        result = validator.validate("  0000:01:00.0  ")
        assert not result.valid
        
        # Whitespace in the middle should also fail
        result = validator.validate("0000: 01:00.0")
        assert not result.valid
    
    def test_error_messages(self):
        """Test that error messages are informative."""
        validator = BDFValidator()
        
        result = validator.validate("invalid")
        assert not result.valid
        assert "Invalid BDF format" in result.errors[0]
        assert "Expected: [XXXX:]XX:XX.X" in result.errors[0]
        
        # Strict mode should have different message
        strict_validator = BDFValidator(strict=True)
        result = strict_validator.validate("01:00.0")
        assert not result.valid
        assert "Expected: XXXX:XX:XX.X" in result.errors[0]
    
    def test_custom_field_name(self):
        """Test custom field name in error messages."""
        validator = BDFValidator(field_name="PCI address")
        
        result = validator.validate("invalid")
        assert not result.valid
        assert "Invalid PCI address format" in result.errors[0]
    
    def test_factory_function(self):
        """Test the get_bdf_validator factory function."""
        validator = get_bdf_validator()
        assert isinstance(validator, BDFValidator)
        assert not validator.strict
        
        strict_validator = get_bdf_validator(strict=True)
        assert isinstance(strict_validator, BDFValidator)
        assert strict_validator.strict