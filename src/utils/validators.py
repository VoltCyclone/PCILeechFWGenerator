"""Generic validation framework for PCILeech."""
from abc import ABC, abstractmethod
from functools import wraps
from typing import Any, Callable, List, Optional, Tuple, Union, Dict
import re
from dataclasses import dataclass


@dataclass
class ValidationResult:
    """Result of a validation operation."""
    
    valid: bool
    errors: List[str] = None
    warnings: List[str] = None
    
    def __post_init__(self):
        """Initialize lists if not provided."""
        if self.errors is None:
            self.errors = []
        if self.warnings is None:
            self.warnings = []
    
    @property
    def is_valid(self) -> bool:
        """Alias for valid property for backward compatibility."""
        return self.valid
        
    def add_error(self, error: str) -> None:
        """Add an error message."""
        self.errors.append(error)
        self.valid = False
        
    def add_warning(self, warning: str) -> None:
        """Add a warning message."""
        self.warnings.append(warning)
    
    def merge(self, other: 'ValidationResult') -> 'ValidationResult':
        """Merge another validation result into this one."""
        self.valid = self.valid and other.valid
        self.errors.extend(other.errors)
        self.warnings.extend(other.warnings)
        return self


class BaseValidator(ABC):
    """Base class for all validators."""
    
    def __init__(self, field_name: str = "value"):
        """Initialize validator with field name for error messages."""
        self.field_name = field_name
    
    @abstractmethod
    def validate(self, value: Any) -> ValidationResult:
        """Validate the input value."""
        pass
    
    def __call__(self, value: Any) -> ValidationResult:
        """Allow validator to be called directly."""
        return self.validate(value)


class RangeValidator(BaseValidator):
    """Validate numeric values are within a range."""
    
    def __init__(self, min_value: Optional[float] = None, 
                 max_value: Optional[float] = None,
                 field_name: str = "value"):
        """Initialize with min/max bounds."""
        super().__init__(field_name)
        self.min_value = min_value
        self.max_value = max_value
    
    def validate(self, value: Any) -> ValidationResult:
        """Validate value is within range."""
        errors = []
        warnings = []
        
        try:
            num_value = float(value)
        except (TypeError, ValueError):
            errors.append(f"{self.field_name} must be numeric")
            return ValidationResult(False, errors, warnings)
        
        if self.min_value is not None and num_value < self.min_value:
            errors.append(f"{self.field_name} must be >= {self.min_value}")
        
        if self.max_value is not None and num_value > self.max_value:
            errors.append(f"{self.field_name} must be <= {self.max_value}")
        
        return ValidationResult(len(errors) == 0, errors, warnings)


class HexValidator(BaseValidator):
    """Validate hexadecimal strings."""
    
    def __init__(self, length: Optional[int] = None,
                 prefix_required: bool = False,
                 field_name: str = "value",
                 expected_length: Optional[int] = None):
        """Initialize hex validator.
        
        Args:
            length: Expected length (excluding 0x prefix)
            expected_length: Alias for length for backward compatibility
            prefix_required: Whether 0x prefix is required
            field_name: Field name for error messages
        """
        super().__init__(field_name)
        # Support both 'length' and 'expected_length' parameters
        self.length = length if length is not None else expected_length
        self.prefix_required = prefix_required
    
    def validate(self, value: Any) -> ValidationResult:
        """Validate hex string."""
        errors = []
        warnings = []
        
        if not isinstance(value, str):
            errors.append(f"{self.field_name} must be a string")
            return ValidationResult(False, errors, warnings)
        
        # Check for prefix
        if value.startswith("0x") or value.startswith("0X"):
            hex_part = value[2:]
        elif self.prefix_required:
            errors.append(f"{self.field_name} must start with '0x' or '0X'")
            return ValidationResult(False, errors, warnings)
        else:
            hex_part = value
        
        # Check if valid hex
        try:
            int(hex_part, 16)
        except ValueError:
            errors.append(f"{self.field_name} contains invalid hex characters")
            return ValidationResult(False, errors, warnings)
        
        # Check length
        if self.length is not None and len(hex_part) != self.length:
            errors.append(f"{self.field_name} must be {self.length} hex digits")
        
        return ValidationResult(len(errors) == 0, errors, warnings)


class RequiredFieldsValidator(BaseValidator):
    """Validate that required fields are present in a dictionary."""
    
    def __init__(self, required_fields: List[str], field_name: str = "data"):
        """Initialize required fields validator.
        
        Args:
            required_fields: List of field names that must be present
            field_name: Field name for error messages
        """
        super().__init__(field_name)
        self.required_fields = required_fields
    
    def validate(self, value: Any) -> ValidationResult:
        """Validate required fields are present."""
        errors = []
        warnings = []
        
        if not isinstance(value, dict):
            errors.append(f"{self.field_name} must be a dictionary")
            return ValidationResult(False, errors, warnings)
        
        missing_fields = []
        none_fields = []
        
        for field in self.required_fields:
            if field not in value:
                missing_fields.append(field)
            elif value[field] is None:
                none_fields.append(field)
        
        if missing_fields:
            errors.append(f"Missing required fields: {', '.join(missing_fields)}")
        
        if none_fields:
            errors.append(f"Required fields cannot be None: {', '.join(none_fields)}")
        
        return ValidationResult(len(errors) == 0, errors, warnings)


class RegexValidator(BaseValidator):
    """Validate values match a regex pattern."""
    
    def __init__(self, pattern: str, 
                 error_message: Optional[str] = None,
                 field_name: str = "value"):
        """Initialize regex validator."""
        super().__init__(field_name)
        self.pattern = re.compile(pattern)
        self.error_message = error_message
    
    def validate(self, value: Any) -> ValidationResult:
        """Validate value matches pattern."""
        errors = []
        warnings = []
        
        if not isinstance(value, str):
            value = str(value)
        
        if not self.pattern.match(value):
            msg = self.error_message or f"{self.field_name} does not match required pattern"
            errors.append(msg)
        
        return ValidationResult(len(errors) == 0, errors, warnings)


class CompositeValidator(BaseValidator):
    """Combine multiple validators."""
    
    def __init__(self, validators: List[BaseValidator], field_name: str = "value"):
        """Initialize with list of validators."""
        super().__init__(field_name)
        self.validators = validators
    
    def validate(self, value: Any) -> ValidationResult:
        """Run all validators and merge results."""
        result = ValidationResult(True, [], [])
        for validator in self.validators:
            result.merge(validator.validate(value))
        return result


# Validation decorators
def validate_input(**validators: BaseValidator) -> Callable:
    """
    Decorator to validate function inputs.
    
    Example:
        @validate_input(
            vendor_id=HexValidator(4, field_name="vendor_id"),
            bar_size=RangeValidator(min_value=0, field_name="bar_size")
        )
        def configure_device(vendor_id: str, bar_size: int):
            pass
    """
    def decorator(func: Callable) -> Callable:
        @wraps(func)
        def wrapper(*args, **kwargs):
            # Get function signature
            import inspect
            sig = inspect.signature(func)
            bound = sig.bind(*args, **kwargs)
            bound.apply_defaults()
            
            # Validate each parameter
            all_errors = []
            for param_name, validator in validators.items():
                if param_name in bound.arguments:
                    result = validator.validate(bound.arguments[param_name])
                    if not result.valid:
                        all_errors.extend(result.errors)
            
            if all_errors:
                raise ValueError(f"Validation errors: {'; '.join(all_errors)}")
            
            return func(*args, **kwargs)
        return wrapper
    return decorator


def validate_return(validator: BaseValidator) -> Callable:
    """
    Decorator to validate function return value.
    
    Example:
        @validate_return(HexValidator(4))
        def get_device_id() -> str:
            return "10de"
    """
    def decorator(func: Callable) -> Callable:
        @wraps(func)
        def wrapper(*args, **kwargs):
            result = func(*args, **kwargs)
            validation = validator.validate(result)
            if not validation.valid:
                raise ValueError(f"Return value validation failed: {'; '.join(validation.errors)}")
            return result
        return wrapper
    return decorator


# Specialized validators

class BDFValidator(BaseValidator):
    """Validate PCI Bus:Device.Function identifiers.
    
    Supports two formats:
    1. Full format: XXXX:XX:XX.X (e.g., 0000:01:00.0)
    2. Short format: XX:XX.X (e.g., 01:00.0) 
    3. With optional domain: [XXXX:]XX:XX.X
    """
    
    # Pattern that matches both formats
    FULL_PATTERN = re.compile(
        r"^([0-9A-Fa-f]{2,4}:)?[0-9A-Fa-f]{2}:[0-9A-Fa-f]{2}\.[0-7]$"
    )
    
    # Strict pattern requiring full format
    STRICT_PATTERN = re.compile(
        r"^[0-9A-Fa-f]{4}:[0-9A-Fa-f]{2}:[0-9A-Fa-f]{2}\.[0-7]$"
    )
    
    def __init__(self, strict: bool = False, field_name: str = "BDF"):
        """Initialize BDF validator.
        
        Args:
            strict: If True, require full XXXX:XX:XX.X format
            field_name: Field name for error messages
        """
        super().__init__(field_name)
        self.strict = strict
        self.pattern = self.STRICT_PATTERN if strict else self.FULL_PATTERN
    
    def validate(self, value: Any) -> ValidationResult:
        """Validate BDF format."""
        errors = []
        warnings = []
        
        if not isinstance(value, str):
            errors.append(f"{self.field_name} must be a string")
            return ValidationResult(False, errors, warnings)
        
        # Validate the exact input without stripping whitespace
        if not self.pattern.match(value):
            if self.strict:
                errors.append(
                    f"Invalid {self.field_name} format. "
                    f"Expected: XXXX:XX:XX.X (e.g., 0000:01:00.0)"
                )
            else:
                errors.append(
                    f"Invalid {self.field_name} format. "
                    f"Expected: [XXXX:]XX:XX.X (e.g., 01:00.0 or 0000:01:00.0)"
                )
        
        return ValidationResult(len(errors) == 0, errors, warnings)


class PowerOfTwoValidator(BaseValidator):
    """Validate that a value is a power of two."""
    
    def __init__(self, allow_zero: bool = True, field_name: str = "value"):
        """Initialize power of two validator.
        
        Args:
            allow_zero: Whether zero is considered valid
            field_name: Field name for error messages
        """
        super().__init__(field_name)
        self.allow_zero = allow_zero
    
    def validate(self, value: Any) -> ValidationResult:
        """Validate value is power of two."""
        errors = []
        warnings = []
        
        try:
            int_value = int(value)
        except (TypeError, ValueError):
            errors.append(f"{self.field_name} must be an integer")
            return ValidationResult(False, errors, warnings)
        
        if int_value < 0:
            errors.append(f"{self.field_name} must be non-negative")
            return ValidationResult(False, errors, warnings)
        
        if int_value == 0:
            if not self.allow_zero:
                errors.append(f"{self.field_name} cannot be zero")
            return ValidationResult(self.allow_zero, errors, warnings)
        
        # Check if power of two: n & (n-1) == 0
        if int_value & (int_value - 1) != 0:
            errors.append(f"{self.field_name} must be a power of two")
        
        return ValidationResult(len(errors) == 0, errors, warnings)


class BARSizeValidator(BaseValidator):
    """Validate BAR (Base Address Register) sizes according to PCIe spec."""
    
    # Import constants - we'll need to handle this differently to avoid circular imports
    MIN_MEMORY_SIZE = 16  # 16 bytes minimum for memory BARs
    MIN_IO_SIZE = 4       # 4 bytes minimum for I/O BARs
    MAX_IO_SIZE = 256     # 256 bytes maximum for I/O BARs
    
    def __init__(self, bar_type: str = "memory", field_name: str = "BAR size"):
        """Initialize BAR size validator.
        
        Args:
            bar_type: Type of BAR ("memory" or "io")
            field_name: Field name for error messages
        """
        super().__init__(field_name)
        self.bar_type = bar_type.lower()
        
        # Compose validators
        self.validators = [
            PowerOfTwoValidator(allow_zero=True, field_name=field_name)
        ]
        
        if self.bar_type == "io":
            self.validators.append(
                RangeValidator(
                    min_value=0,  # 0 is allowed (disabled)
                    max_value=self.MAX_IO_SIZE,
                    field_name=field_name
                )
            )
    
    def validate(self, value: Any) -> ValidationResult:
        """Validate BAR size."""
        errors = []
        warnings = []
        
        try:
            size = int(value)
        except (TypeError, ValueError):
            errors.append(f"{self.field_name} must be an integer")
            return ValidationResult(False, errors, warnings)
        
        # Run power of two validation
        result = ValidationResult(True, [], [])
        for validator in self.validators:
            result.merge(validator.validate(size))
        
        if not result.valid:
            return result
        
        # Additional BAR-specific checks
        if size > 0:  # Non-zero sizes have minimum requirements
            if self.bar_type == "io":
                if size < self.MIN_IO_SIZE:
                    errors.append(
                        f"I/O {self.field_name} must be at least {self.MIN_IO_SIZE} bytes"
                    )
            else:  # memory
                if size < self.MIN_MEMORY_SIZE:
                    errors.append(
                        f"Memory {self.field_name} must be at least {self.MIN_MEMORY_SIZE} bytes"
                    )
        
        result.errors.extend(errors)
        result.valid = result.valid and len(errors) == 0
        
        return result


# Factory functions for common validators

def get_hex_validator(length: int, field_name: str = "hex value") -> HexValidator:
    """Get a hex validator for the specified length."""
    return HexValidator(length=length, field_name=field_name)


def get_vendor_id_validator() -> HexValidator:
    """Get validator for vendor IDs."""
    return HexValidator(length=4, field_name="vendor_id")


def get_device_id_validator() -> HexValidator:
    """Get validator for device IDs."""
    return HexValidator(length=4, field_name="device_id")


def get_class_code_validator() -> HexValidator:
    """Get validator for class codes."""
    return HexValidator(length=6, field_name="class_code")


def get_bdf_validator(strict: bool = False) -> BDFValidator:
    """Get validator for BDF strings."""
    return BDFValidator(strict=strict)


def get_bar_size_validator(bar_type: str = "memory") -> BARSizeValidator:
    """Get validator for BAR sizes."""
    return BARSizeValidator(bar_type=bar_type)


# Helper function to validate common device configuration
def validate_device_config(config: Dict[str, Any]) -> ValidationResult:
    """Validate a device configuration dictionary."""
    result = ValidationResult(True, [], [])
    
    # Check required fields
    required_fields = ["vendor_id", "device_id"]
    for field in required_fields:
        if field not in config:
            result.errors.append(f"Missing required field: {field}")
            result.valid = False
    
    # Validate individual fields if present  
    if "vendor_id" in config:
        result.merge(get_vendor_id_validator().validate(config["vendor_id"]))
    
    if "device_id" in config:
        result.merge(get_device_id_validator().validate(config["device_id"]))
    
    if "device_bdf" in config:
        result.merge(get_bdf_validator().validate(config["device_bdf"]))
    
    return result