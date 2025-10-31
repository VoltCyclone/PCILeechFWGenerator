#!/usr/bin/env python3
"""
Template path mapping for the overlay-only architecture.

This module provides minimal mappings for the active templates used
in the current overlay-only architecture where only .coe configuration
files are generated.
"""

# Mapping from old paths to new paths (minimal - only active templates)
TEMPLATE_PATH_MAPPING = {
    # Active SystemVerilog template (config space overlay)
    "systemverilog/pcileech_cfgspace.coe.j2": "sv/pcileech_cfgspace.coe.j2",
    # Python templates
    "python/build_integration.py.j2": "python/build_integration.py.j2",
    "python/pcileech_build_integration.py.j2": "python/pcileech_build_integration.py.j2",
}


def get_new_template_path(old_path: str) -> str:
    """
    Get the new template path for a given old path.

    Args:
        old_path: The old nested template path

    Returns:
        The new flattened template path
    """
    # Remove leading slashes and normalize
    old_path = old_path.lstrip("/")

    # Check if we have a mapping
    if old_path in TEMPLATE_PATH_MAPPING:
        return TEMPLATE_PATH_MAPPING[old_path]

    # If no mapping exists, return the original path
    # This allows for gradual migration
    return old_path


def update_template_path(template_name: str) -> str:
    """
    Update a template name to use the new path structure.

    This function handles both old and new path formats gracefully.

    Args:
        template_name: The template name (may include path)

    Returns:
        The updated template path
    """
    # If it's already using the new structure, return as-is
    if template_name.startswith(("sv/", "tcl/", "python/")):
        return template_name

    # Otherwise, map it
    mapped = get_new_template_path(template_name)

    # Convenience fallback: if caller provided a bare .coe template
    # filename without a directory prefix, assume it lives under the "sv/" folder.
    if "/" not in mapped and mapped.endswith(".coe.j2"):
        return f"sv/{mapped}"

    return mapped
