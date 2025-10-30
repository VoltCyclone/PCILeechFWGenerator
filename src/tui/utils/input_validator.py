"""Input validation utilities for TUI."""

from typing import Tuple, List, Optional, Dict, Any
from pathlib import Path
import re

from src.utils.validators import (
    get_bdf_validator, 
    RangeValidator,
    ValidationResult
)


class InputValidator:
    """Validation utilities for user input in TUI."""

    @staticmethod
    def validate_file_path(path: str) -> Tuple[bool, str]:
        """
        Validate that a file path exists and is readable.

        Args:
            path: The file path to validate.

        Returns:
            A tuple containing (is_valid, error_message).
            If valid, error_message will be empty.
        """
        try:
            path_obj = Path(path)
            if not path_obj.exists():
                return False, f"File does not exist: {path}"
            if not path_obj.is_file():
                return False, f"Path is not a file: {path}"
            if not path_obj.stat().st_size > 0:
                return False, f"File is empty: {path}"
            return True, ""
        except Exception as e:
            return False, f"Invalid path: {str(e)}"

    @staticmethod
    def validate_directory_path(path: str) -> Tuple[bool, str]:
        """
        Validate that a directory path exists and is writable.

        Args:
            path: The directory path to validate.

        Returns:
            A tuple containing (is_valid, error_message).
            If valid, error_message will be empty.
        """
        try:
            path_obj = Path(path)
            if not path_obj.exists():
                # Try to create it
                try:
                    path_obj.mkdir(parents=True, exist_ok=True)
                    return True, ""
                except Exception as e:
                    return False, f"Cannot create directory: {str(e)}"
            elif not path_obj.is_dir():
                return False, f"Path is not a directory: {path}"
            return True, ""
        except Exception as e:
            return False, f"Invalid path: {str(e)}"

    @staticmethod
    def validate_bdf(bdf: str) -> Tuple[bool, str]:
        """
        Validate PCI BDF format.

        Args:
            bdf: The PCI BDF identifier to validate (format: XXXX:XX:XX.X).

        Returns:
            A tuple containing (is_valid, error_message).
            If valid, error_message will be empty.
        """
        validator = get_bdf_validator()
        result = validator.validate(bdf)
        if result.valid:
            return True, ""
        else:
            # Return the first error message
            return False, result.errors[0] if result.errors else "Invalid BDF format"

    @staticmethod
    def validate_non_empty(value: str, field_name: str = "Value") -> Tuple[bool, str]:
        """
        Validate that a string is not empty.

        Args:
            value: The string to validate.
            field_name: The name of the field being validated for error messages.

        Returns:
            A tuple containing (is_valid, error_message).
            If valid, error_message will be empty.
        """
        if not value or not value.strip():
            return False, f"{field_name} cannot be empty"
        return True, ""

    @staticmethod
    def validate_numeric(value: str, field_name: str = "Value") -> Tuple[bool, str]:
        """
        Validate that a string represents a numeric value.

        Args:
            value: The string to validate.
            field_name: The name of the field being validated for error messages.

        Returns:
            A tuple containing (is_valid, error_message).
            If valid, error_message will be empty.
        """
        try:
            float(value)
            return True, ""
        except ValueError:
            return False, f"{field_name} must be a numeric value"

    @staticmethod
    def validate_integer(value: str, field_name: str = "Value") -> Tuple[bool, str]:
        """
        Validate that a string represents an integer value.

        Args:
            value: The string to validate.
            field_name: The name of the field being validated for error messages.

        Returns:
            A tuple containing (is_valid, error_message).
            If valid, error_message will be empty.
        """
        try:
            int(value)
            return True, ""
        except ValueError:
            return False, f"{field_name} must be an integer value"

    @staticmethod
    def validate_in_range(
        value: str, min_val: float, max_val: float, field_name: str = "Value"
    ) -> Tuple[bool, str]:
        """
        Validate that a numeric value is within a specified range.

        Args:
            value: The string value to validate.
            min_val: Minimum allowed value (inclusive).
            max_val: Maximum allowed value (inclusive).
            field_name: The name of the field being validated for error messages.

        Returns:
            A tuple containing (is_valid, error_message).
            If valid, error_message will be empty.
        """
        # Use the RangeValidator from our framework
        validator = RangeValidator(min_value=min_val, max_value=max_val, field_name=field_name)
        
        # First check if it's numeric
        try:
            num_value = float(value)
        except ValueError:
            return False, f"{field_name} must be a numeric value"
        
        result = validator.validate(num_value)
        if result.valid:
            return True, ""
        else:
            return False, result.errors[0] if result.errors else f"{field_name} out of range"

    @staticmethod
    def validate_in_choices(
        value: str, choices: List[str], field_name: str = "Value"
    ) -> Tuple[bool, str]:
        """
        Validate that a value is one of the allowed choices.

        Args:
            value: The value to validate.
            choices: List of allowed values.
            field_name: The name of the field being validated for error messages.

        Returns:
            A tuple containing (is_valid, error_message).
            If valid, error_message will be empty.
        """
        if value not in choices:
            return False, f"{field_name} must be one of: {', '.join(choices)}"
        return True, ""

    @staticmethod
    def validate_config(config: Dict[str, Any]) -> Tuple[bool, str]:
        """
        Validate a configuration dictionary.

        Args:
            config: The configuration dictionary to validate.

        Returns:
            A tuple containing (is_valid, error_message).
            If valid, error_message will be empty.
        """
        from src.utils.validators import validate_device_config
        
        result = validate_device_config(config)
        if result.valid:
            return True, ""
        else:
            # Combine all errors into a single message
            error_msg = "; ".join(result.errors)
            return False, error_msg

    @staticmethod
    def validate_hex(value: str, length: Optional[int] = None, field_name: str = "Value") -> Tuple[bool, str]:
        """
        Validate that a string is a valid hexadecimal value.

        Args:
            value: The string to validate.
            length: Expected length of hex string (excluding 0x prefix).
            field_name: The name of the field being validated.

        Returns:
            A tuple containing (is_valid, error_message).
            If valid, error_message will be empty.
        """
        from src.utils.validators import HexValidator
        
        validator = HexValidator(length=length, field_name=field_name)
        result = validator.validate(value)
        
        if result.valid:
            return True, ""
        else:
            return False, result.errors[0] if result.errors else f"Invalid hex value"

    @staticmethod
    def validate_email(value: str) -> Tuple[bool, str]:
        """
        Validate email address format.

        Args:
            value: The email address to validate.

        Returns:
            A tuple containing (is_valid, error_message).
            If valid, error_message will be empty.
        """
        # Basic email regex pattern
        pattern = r'^[a-zA-Z0-9._%+-]+@[a-zA-Z0-9.-]+\.[a-zA-Z]{2,}$'
        if re.match(pattern, value):
            return True, ""
        return False, "Invalid email format"

    @staticmethod
    def validate_url(value: str) -> Tuple[bool, str]:
        """
        Validate URL format.

        Args:
            value: The URL to validate.

        Returns:
            A tuple containing (is_valid, error_message).
            If valid, error_message will be empty.
        """
        # Basic URL regex pattern
        pattern = r'^https?://[^\s/$.?#].[^\s]*$'
        if re.match(pattern, value, re.IGNORECASE):
            return True, ""
        return False, "Invalid URL format (must start with http:// or https://)"

    @staticmethod
    def validate_positive_integer(value: str, field_name: str = "Value") -> Tuple[bool, str]:
        """
        Validate that a string represents a positive integer.

        Args:
            value: The string to validate.
            field_name: The name of the field being validated.

        Returns:
            A tuple containing (is_valid, error_message).
            If valid, error_message will be empty.
        """
        try:
            num = int(value)
            if num > 0:
                return True, ""
            return False, f"{field_name} must be a positive integer"
        except ValueError:
            return False, f"{field_name} must be an integer value"

    @staticmethod
    def validate_percentage(value: str, field_name: str = "Value") -> Tuple[bool, str]:
        """
        Validate that a value represents a percentage (0-100).

        Args:
            value: The string to validate.
            field_name: The name of the field being validated.

        Returns:
            A tuple containing (is_valid, error_message).
            If valid, error_message will be empty.
        """
        return InputValidator.validate_in_range(value, 0, 100, field_name)


# Import required for type hints
from typing import Dict, Any
