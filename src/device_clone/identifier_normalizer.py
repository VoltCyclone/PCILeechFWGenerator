#!/usr/bin/env python3
"""
Centralized normalization and validation utilities for PCILeech identifiers and hex fields.
"""
from typing import Any, Optional

from src.string_utils import safe_format


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
        norm = IdentifierNormalizer.normalize_hex(value, length)
        # Check for invalid hex format
        try:
            int(str(value).lower().replace("0x", ""), 16)
        except Exception:
            raise ContextError(
                safe_format(
                    "Invalid hex format for {field_name}: '{value}'",
                    field_name=field_name,
                    value=value,
                )
            )
        if len(norm) != length:
            raise ContextError(
                safe_format(
                    "Invalid hex format for {field_name}: '{value}' (length {len(norm)} != {length})",
                    field_name=field_name,
                    value=value,
                    len=len(norm),
                    length=length,
                )
            )
        return norm

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
        specs = [
            ("vendor_id", 4),
            ("device_id", 4),
            ("class_code", 6),
            ("revision_id", 2),
        ]
        result = {}
        for field, length in specs:
            result[field] = IdentifierNormalizer.validate_identifier(
                identifiers.get(field), length, field
            )
        # Subsystem IDs
        result["subsystem_vendor_id"] = IdentifierNormalizer.normalize_subsystem(
            identifiers.get("subsystem_vendor_id"), result["vendor_id"], 4
        )
        result["subsystem_device_id"] = IdentifierNormalizer.normalize_subsystem(
            identifiers.get("subsystem_device_id"), result["device_id"], 4
        )
        return result
