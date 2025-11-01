"""Unit tests for BAR size validator."""
import pytest
from src.utils.validators import BARSizeValidator, get_bar_size_validator


class TestBARSizeValidator:
    """Test BAR size validation functionality."""
    
    def test_valid_memory_bar_sizes(self):
        """Test validation of valid memory BAR sizes."""
        validator = BARSizeValidator(bar_type="memory")
        
        valid_sizes = [
            0,        # Disabled BAR
            16,       # Minimum memory BAR size
            32,       # 32 bytes
            64,       # 64 bytes
            128,      # 128 bytes
            256,      # 256 bytes
            512,      # 512 bytes
            1024,     # 1KB
            4096,     # 4KB
            65536,    # 64KB
            1048576,  # 1MB
            16777216, # 16MB
            268435456,  # 256MB
            1073741824, # 1GB
        ]
        
        for size in valid_sizes:
            result = validator.validate(size)
            assert result.valid, f"Failed to validate valid memory BAR size: {size}, errors: {result.errors}"
    
    def test_valid_io_bar_sizes(self):
        """Test validation of valid I/O BAR sizes."""
        validator = BARSizeValidator(bar_type="io")
        
        valid_sizes = [
            0,    # Disabled BAR
            4,    # Minimum I/O BAR size
            8,    # 8 bytes
            16,   # 16 bytes
            32,   # 32 bytes
            64,   # 64 bytes
            128,  # 128 bytes
            256,  # Maximum I/O BAR size
        ]
        
        for size in valid_sizes:
            result = validator.validate(size)
            assert result.valid, f"Failed to validate valid I/O BAR size: {size}, errors: {result.errors}"
    
    def test_invalid_memory_bar_sizes(self):
        """Test rejection of invalid memory BAR sizes."""
        validator = BARSizeValidator(bar_type="memory")
        
        invalid_sizes = [
            -1,    # Negative
            1,     # Too small (< 16)
            2,     # Too small
            4,     # Too small
            8,     # Too small
            15,    # Not power of 2
            17,    # Not power of 2
            31,    # Not power of 2
            33,    # Not power of 2
            63,    # Not power of 2
            100,   # Not power of 2
            1000,  # Not power of 2
            1023,  # Not power of 2
        ]
        
        for size in invalid_sizes:
            result = validator.validate(size)
            assert not result.valid, f"Incorrectly validated invalid memory BAR size: {size}"
    
    def test_invalid_io_bar_sizes(self):
        """Test rejection of invalid I/O BAR sizes."""
        validator = BARSizeValidator(bar_type="io")
        
        invalid_sizes = [
            -1,    # Negative
            1,     # Too small (< 4)
            2,     # Too small and not power of 2
            3,     # Too small and not power of 2
            5,     # Not power of 2
            7,     # Not power of 2
            12,    # Not power of 2
            15,    # Not power of 2
            31,    # Not power of 2
            100,   # Not power of 2
            257,   # Too large (> 256)
            512,   # Too large
            1024,  # Too large
        ]
        
        for size in invalid_sizes:
            result = validator.validate(size)
            assert not result.valid, f"Incorrectly validated invalid I/O BAR size: {size}"
    
    def test_power_of_two_validation(self):
        """Test that only powers of two (and zero) are accepted."""
        validator = BARSizeValidator(bar_type="memory")
        
        # Test powers of two
        power = 1
        for i in range(20):  # Test 2^0 through 2^19
            if power >= 16:  # Memory BAR minimum
                result = validator.validate(power)
                assert result.valid, f"Power of two {power} should be valid"
            power *= 2
        
        # Test non-powers of two
        non_powers = [3, 5, 6, 7, 9, 10, 11, 12, 13, 14, 15, 17, 18, 19, 20]
        for num in non_powers:
            result = validator.validate(num)
            assert not result.valid, f"Non-power of two {num} should be invalid"
            assert "must be a power of two" in result.errors[0]
    
    def test_bar_type_case_insensitive(self):
        """Test that BAR type is case-insensitive."""
        validators = [
            BARSizeValidator(bar_type="memory"),
            BARSizeValidator(bar_type="MEMORY"),
            BARSizeValidator(bar_type="Memory"),
            BARSizeValidator(bar_type="MeMoRy"),
        ]
        
        for validator in validators:
            result = validator.validate(64)
            assert result.valid
        
        validators = [
            BARSizeValidator(bar_type="io"),
            BARSizeValidator(bar_type="IO"),
            BARSizeValidator(bar_type="Io"),
            BARSizeValidator(bar_type="iO"),
        ]
        
        for validator in validators:
            result = validator.validate(32)
            assert result.valid
    
    def test_non_numeric_input(self):
        """Test handling of non-numeric inputs."""
        validator = BARSizeValidator()
        
        non_numeric = [None, "abc", [], {}, object()]
        
        for value in non_numeric:
            result = validator.validate(value)
            assert not result.valid
            assert "must be an integer" in result.errors[0]
    
    def test_string_numeric_input(self):
        """Test handling of string representations of numbers."""
        validator = BARSizeValidator()
        
        # Valid string numbers
        result = validator.validate("64")
        assert result.valid
        
        result = validator.validate("0")
        assert result.valid
        
        # Invalid string numbers
        result = validator.validate("3.14")
        assert not result.valid
        
        result = validator.validate("64.0")
        assert not result.valid
    
    def test_error_messages(self):
        """Test that error messages are informative."""
        # Memory BAR too small
        validator = BARSizeValidator(bar_type="memory")
        result = validator.validate(8)
        assert not result.valid
        assert "must be at least 16 bytes" in result.errors[0]
        
        # I/O BAR too small
        validator = BARSizeValidator(bar_type="io")
        result = validator.validate(2)
        assert not result.valid
        assert "must be at least 4 bytes" in result.errors[0]
        
        # I/O BAR too large
        result = validator.validate(512)
        assert not result.valid
        assert "must be <= 256" in result.errors[0]
        
        # Not power of two
        result = validator.validate(100)
        assert not result.valid
        assert "must be a power of two" in result.errors[0]
    
    def test_custom_field_name(self):
        """Test custom field name in error messages."""
        validator = BARSizeValidator(field_name="BAR0 size")
        
        result = validator.validate("invalid")
        assert not result.valid
        assert "BAR0 size must be an integer" in result.errors[0]
        
        result = validator.validate(15)
        assert not result.valid
        assert "BAR0 size must be a power of two" in result.errors[0]
    
    def test_factory_function(self):
        """Test the get_bar_size_validator factory function."""
        validator = get_bar_size_validator()
        assert isinstance(validator, BARSizeValidator)
        assert validator.bar_type == "memory"
        
        io_validator = get_bar_size_validator(bar_type="io")
        assert isinstance(io_validator, BARSizeValidator)
        assert io_validator.bar_type == "io"
    
    def test_zero_is_valid(self):
        """Test that zero (disabled BAR) is always valid."""
        memory_validator = BARSizeValidator(bar_type="memory")
        io_validator = BARSizeValidator(bar_type="io")
        
        result = memory_validator.validate(0)
        assert result.valid, "Zero should be valid for memory BARs"
        
        result = io_validator.validate(0)
        assert result.valid, "Zero should be valid for I/O BARs"
    
    def test_large_memory_bars(self):
        """Test validation of large memory BAR sizes."""
        validator = BARSizeValidator(bar_type="memory")
        
        # Test up to 2GB
        large_sizes = [
            2**30,  # 1GB
            2**31,  # 2GB
        ]
        
        for size in large_sizes:
            result = validator.validate(size)
            assert result.valid, f"Large memory BAR size {size} should be valid"