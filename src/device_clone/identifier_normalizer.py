#!/usr/bin/env python3
"""
Centralized normalization and validation utilities for PCILeech identifiers and hex fields.
"""
from typing import Any, Optional

from src.string_utils import safe_format
from src.utils.validators import (
    HexValidator,
    get_class_code_validator,
    get_device_id_validator,
    get_vendor_id_validator,
)


class IdentifierNormalizer:
    """Utility for hex normalization and identifier validation."""

    @staticmethod
    def normalize_hex(value: Any, length: int) -> str:
        """Normalize a hex string to the specified length (zero-padded, lowercase)."""
        if value is None:
            return "0" * length
        if isinstance(value, int):
            return f"{value:0{length}x}"
        value = str(value).lower().replace("0x", "")
        try:
            return f"{int(value, 16):0{length}x}"
        except Exception:
            return "0" * length

    @staticmethod
    def validate_identifier(
        value: Any, length: int, field_name: str = "identifier"
    ) -> str:
        """Validate and normalize identifier, raising ContextError if invalid."""
        from src.exceptions import ContextError

        # Check for empty or None
        if not value or str(value).strip() == "":
            raise ContextError(
                safe_format(
                    "Missing {field_name}: {field_name} cannot be empty",
                    field_name=field_name,
                )
            )
        
        # Use HexValidator for validation
        validator = HexValidator(expected_length=length, field_name=field_name)
        result = validator.validate(str(value))
        
        if not result.is_valid:
            # Combine all errors into one message
            error_msg = "; ".join(result.errors)
            raise ContextError(error_msg)
        
        # Return normalized value
        return IdentifierNormalizer.normalize_hex(value, length)

    @staticmethod
    def normalize_subsystem(
        value: Optional[Any], main_value: str, length: int = 4
    ) -> str:
        """Normalize subsystem ID, fallback to main value if missing/invalid."""
        if value is None or str(value).lower() in ("none", "", "0000"):
            return IdentifierNormalizer.normalize_hex(main_value, length)
        return IdentifierNormalizer.normalize_hex(value, length)

    @staticmethod
    def validate_all_identifiers(identifiers: dict) -> dict:
        """Validate and normalize all required identifiers in a dict."""
        from src.exceptions import ContextError
        
        result = {}
        
        # Use specific validators for known fields
        validators = {
            "vendor_id": get_vendor_id_validator(),
            "device_id": get_device_id_validator(),
            "class_code": get_class_code_validator(),
            "revision_id": HexValidator(length=2, field_name="revision_id"),
        }
        
        for field, validator in validators.items():
            value = identifiers.get(field)
            if not value:
                raise ContextError(
                    safe_format("Missing {field}: {field} cannot be empty", field=field)
                )
            
            validation_result = validator.validate(str(value))
            if not validation_result.is_valid:
                error_msg = "; ".join(validation_result.errors)
                raise ContextError(error_msg)
            
            # Get the expected length from the validator
            expected_length = validator.length if hasattr(validator, 'length') else 4
            result[field] = IdentifierNormalizer.normalize_hex(value, expected_length)
        
        # Subsystem IDs
        result["subsystem_vendor_id"] = IdentifierNormalizer.normalize_subsystem(
            identifiers.get("subsystem_vendor_id"), result["vendor_id"], 4
        )
        result["subsystem_device_id"] = IdentifierNormalizer.normalize_subsystem(
            identifiers.get("subsystem_device_id"), result["device_id"], 4
        )
        return result
