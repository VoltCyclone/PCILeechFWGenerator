#!/usr/bin/env python3
"""
Template path mapping for the flattened directory structure.

This module provides mappings from the old nested template paths to the new
flatter structure, ensuring backward compatibility during the transition.
"""

# Mapping from old paths to new paths (config-only architecture)
TEMPLATE_PATH_MAPPING = {
    # Helper templates (root level)
    "_helpers.j2": "_helpers.j2",
    # SystemVerilog config-only templates
    "systemverilog/device_config.sv.j2": "sv/device_config.sv.j2",
    "systemverilog/pcileech_cfgspace.coe.j2": "sv/pcileech_cfgspace.coe.j2",
    "systemverilog/pcileech_header.svh.j2": "sv/pcileech_header.svh.j2",
    # TCL templates
    "tcl/bitstream.j2": "tcl/bitstream.j2",
    "tcl/constraints.j2": "tcl/constraints.j2",
    "tcl/device_setup.j2": "tcl/device_setup.j2",
    "tcl/implementation.j2": "tcl/implementation.j2",
    "tcl/ip_config.j2": "tcl/ip_config.j2",
    "tcl/master_build.j2": "tcl/master_build.j2",
    "tcl/pcileech_build.j2": "tcl/pcileech_build.j2",
    "tcl/pcileech_constraints.j2": "tcl/pcileech_constraints.j2",
    "tcl/pcileech_generate_project.j2": "tcl/pcileech_generate_project.j2",
    "tcl/pcileech_implementation.j2": "tcl/pcileech_implementation.j2",
    "tcl/pcileech_project_setup.j2": "tcl/pcileech_project_setup.j2",
    "tcl/pcileech_sources.j2": "tcl/pcileech_sources.j2",
    "tcl/project_setup.j2": "tcl/project_setup.j2",
    "tcl/sources.j2": "tcl/sources.j2",
    "tcl/synthesis.j2": "tcl/synthesis.j2",
    "tcl/common/header.j2": "tcl/header.j2",
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

    # Convenience fallback: if caller provided a bare SystemVerilog template
    # filename (e.g. "top_level_wrapper.sv.j2") without a directory prefix,
    # assume it lives under the "sv/" folder.
    if "/" not in mapped and mapped.endswith(".sv.j2"):
        return f"sv/{mapped}"

    return mapped
