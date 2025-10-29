#!/usr/bin/env python3
"""
Device Signature Validator

This module provides validation for device signatures to ensure proper formatting
and prevent invalid values from entering SystemVerilog templates.
"""

import logging

import re

from typing import Any, Dict, Optional, Tuple

from src.string_utils import log_error_safe, safe_format

logger = logging.getLogger(__name__)

# Valid device signature formats
VALID_SIG_PATTERNS = [
    r"^32'h[0-9a-fA-F]+$",  # 32'h12345678
    r"^'h[0-9a-fA-F]+$",  # 'h12345678
    r"^0x[0-9a-fA-F]+$",  # 0x12345678
]


def validate_device_signature(device_signature: Any) -> Tuple[bool, Optional[str]]:
    """
    Validate a device signature for correct format.

    Args:
        device_signature: The device signature to validate

    Returns:
        Tuple of (is_valid, error_message)
    """
    if not device_signature:
        return False, "Device signature is empty or None"

    if not isinstance(device_signature, str):
        return (
            False,
            safe_format(
                "device_signature must be a string, got {type}",
                type=type(device_signature).__name__,
            ),
        )

    # Check if it matches any of the valid patterns
    for pattern in VALID_SIG_PATTERNS:
        if re.match(pattern, device_signature):
            return True, None

    # Special case for vendor:device format (used in some contexts)
    vendor_device_pattern = r"^[0-9a-fA-F]{4}:[0-9a-fA-F]{4}(:[0-9a-fA-F]{2})?$"
    if re.match(vendor_device_pattern, device_signature):
        return True, None

    return False, safe_format(
        "Device signature has invalid format: {sig}", sig=device_signature
    )


def ensure_valid_device_signature(context: Dict[str, Any]) -> None:
    """
    Ensure the device signature in the context is valid.

    Args:
        context: Template context dictionary

    Raises:
        ValueError: If device signature is invalid
    """
    if "device_signature" not in context:
        error_message = "device_signature key missing from template context"
        log_error_safe(
            logger,
            safe_format("CRITICAL: {error_message}", error_message=error_message),
            prefix="SECURITY",
        )
        raise ValueError(
            safe_format("CRITICAL: {error_message}", error_message=error_message)
        )

    device_signature = context["device_signature"]
    is_valid, error_message = validate_device_signature(device_signature)

    if not is_valid:
        # Log with detailed context using safe_format and then raise
        log_error_safe(
            logger,
            safe_format(
                "CRITICAL: Invalid device_signature: {error_message}",
                error_message=error_message,
            ),
            prefix="SECURITY",
        )
        # Raise to enforce strict validation in templates/tests
        raise ValueError(
            safe_format(
                "Invalid device_signature: {error_message}",
                error_message=error_message,
            )
        )
