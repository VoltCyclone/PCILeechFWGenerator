#!/usr/bin/env python3
"""Centralized error message strings for context building and validation.

Keep messages concise and actionable; format with string_utils.safe_format.
"""

from typing import Final

# Context builder / identifiers
MISSING_IDENTIFIERS = "Missing required identifier(s): {names}"
STRICT_MODE_MISSING = (
    "Strict identity mode requires donor-provided fields: {fields}. "
    "Provide these via the profiling context or disable strict mode only "
    "for testing."
)
TEMPLATE_CONTEXT_VALIDATION_FAILED = "Template context validation failed: {rc}"

# Donor artifacts
VPD_REQUIRED_MISSING = "VPD required but missing (requires_vpd=True and no vpd_data)."
OPTION_ROM_MISSING_SIZE = "Option ROM indicated but ROM_SIZE missing or invalid"
ROM_SIZE_MISMATCH = "ROM size/data mismatch: ROM_SIZE={size} rom_data_len={dlen}"

# Template context validation
TEMPLATE_CONTEXT_REQUIRED = "Template context is required for {operation}"
TEMPLATE_CONTEXT_NOT_DICT = "Template context must be a dictionary, got {type_name}"
MISSING_CRITICAL_FIELD_DEVICE_CONFIG = (
    "device_config is missing from template context. This is required "
    "for safe PCILeech firmware generation."
)
DEVICE_CONFIG_NOT_DICT = (
    "device_config must be a dictionary, got {type_name}. Cannot "
    "proceed with firmware generation."
)
MISSING_DEVICE_SIGNATURE = (
    "CRITICAL: device_signature is missing from template context. "
    "This field is required for firmware security and uniqueness."
)
EMPTY_DEVICE_SIGNATURE = (
    "CRITICAL: device_signature is None or empty. A valid device "
    "signature is required to prevent generic firmware generation."
)
TEMPLATE_VALIDATION_FAILED = (
    "Template context validation failed with {count} critical errors:\n"
    "{errors}\n\nCannot proceed with firmware generation."
)

# Device config / enums / numeric params
MISSING_DEVICE_CONFIG = (
    "Device configuration is required for safe firmware generation. "
    "Please provide a valid DeviceSpecificLogic object."
)
INVALID_DEVICE_TYPE = (
    "Invalid device_type: {value}. Must be a DeviceType enum. Please "
    "use values from DeviceType class."
)
INVALID_DEVICE_CLASS = (
    "Invalid device_class: {value}. Must be a DeviceClass enum. Please "
    "use values from DeviceClass class."
)
INVALID_NUMERIC_PARAM = "{param} = {value} is out of valid range [{min}, {max}]."

# Template/renderer misc
UNDEFINED_VAR = (
    "{context}: Missing required template variables. Ensure {object} "
    "has all required attributes. Details: {error}"
)
TEMPLATE_NOT_FOUND = (
    "{context}: Template file not found. Ensure the template exists at "
    "'{path}' or check template_dir. Details: {error}"
)

# Behavior profile
MISSING_BEHAVIOR_PROFILE = "Behavior profile is required for register extraction"

META_ERR_READ_VERSION_FILE: Final[str] = "Error reading __version__.py: {err}"
META_ERR_SETTOOLS_SCM: Final[str] = "Error getting version from setuptools_scm: {err}"
META_ERR_IMPORTLIB_METADATA: Final[str] = (
    "Error getting version from importlib.metadata: {err}"
)
