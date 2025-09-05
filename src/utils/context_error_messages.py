#!/usr/bin/env python3
"""Centralized error message strings for context building and validation.

Keep messages concise and actionable; format with string_utils.safe_format.
"""

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
