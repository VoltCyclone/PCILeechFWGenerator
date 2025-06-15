#!/usr/bin/env python3
"""
String utilities for safe formatting operations.

This module provides utilities to handle complex string formatting
operations safely, particularly for multi-line f-strings that can
cause syntax errors when split across lines.
"""

import logging
from typing import Any, Dict, Optional


def safe_format(template: str, **kwargs: Any) -> str:
    """
    Safely format a string template with the given keyword arguments.

    This function provides a safe alternative to f-strings when dealing
    with complex multi-line formatting that might cause syntax errors.

    Args:
        template: The string template with {variable} placeholders
        **kwargs: Keyword arguments to substitute in the template

    Returns:
        The formatted string with all placeholders replaced

    Example:
        >>> safe_format("Hello {name}, you have {count} messages",
        ...             name="Alice", count=5)
        'Hello Alice, you have 5 messages'

        >>> safe_format(
        ...     "Device {bdf} with VID:{vid:04x} DID:{did:04x}",
        ...     bdf="0000:00:1f.3", vid=0x8086, did=0x54c8
        ... )
        'Device 0000:00:1f.3 with VID:8086 DID:54c8'
    """
    try:
        return template.format(**kwargs)
    except KeyError as e:
        # Handle missing keys gracefully
        missing_key = str(e).strip("'\"")
        logging.warning(f"Missing key '{missing_key}' in string template")
        return template.replace(f"{{{missing_key}}}", f"<MISSING:{missing_key}>")
    except ValueError as e:
        # Handle format specification errors
        logging.error(f"Format error in string template: {e}")
        return template
    except Exception as e:
        # Handle any other unexpected errors
        logging.error(f"Unexpected error in safe_format: {e}")
        return template


def safe_log_format(
    logger: logging.Logger, level: int, template: str, **kwargs: Any
) -> None:
    """
    Safely log a formatted message.

    Args:
        logger: The logger instance to use
        level: The logging level (e.g., logging.INFO, logging.ERROR)
        template: The string template with {variable} placeholders
        **kwargs: Keyword arguments to substitute in the template

    Example:
        >>> import logging
        >>> logger = logging.getLogger(__name__)
        >>> safe_log_format(logger, logging.INFO,
        ...                  "Processing device {bdf} with {bytes} bytes",
        ...                  bdf="0000:00:1f.3", bytes=256)
    """
    try:
        formatted_message = safe_format(template, **kwargs)
        logger.log(level, formatted_message)
    except Exception as e:
        # Fallback to basic logging if formatting fails
        logger.error(f"Failed to format log message: {e}")
        logger.log(level, f"Original template: {template}")


def safe_print_format(template: str, **kwargs: Any) -> None:
    """
    Safely print a formatted message.

    Args:
        template: The string template with {variable} placeholders
        **kwargs: Keyword arguments to substitute in the template

    Example:
        >>> safe_print_format("Build completed in {time:.2f} seconds", time=45.67)
        Build completed in 45.67 seconds
    """
    try:
        formatted_message = safe_format(template, **kwargs)
        print(formatted_message)
    except Exception as e:
        print(f"Failed to format message: {e}")
        print(f"Original template: {template}")


def multiline_format(template: str, **kwargs: Any) -> str:
    """
    Format a multi-line string template safely.

    This is particularly useful for complex multi-line strings that
    would be difficult to handle with f-strings.

    Args:
        template: Multi-line string template with {variable} placeholders
        **kwargs: Keyword arguments to substitute in the template

    Returns:
        The formatted multi-line string

    Example:
        >>> template = '''
        ... Device Information:
        ...   BDF: {bdf}
        ...   Vendor ID: {vid:04x}
        ...   Device ID: {did:04x}
        ...   Driver: {driver}
        ... '''
        >>> result = multiline_format(template.strip(),
        ...                          bdf="0000:00:1f.3", vid=0x8086,
        ...                          did=0x54c8, driver="snd_hda_intel")
    """
    return safe_format(template, **kwargs)


def build_device_info_string(device_info: Dict[str, Any]) -> str:
    """
    Build a standardized device information string.

    Args:
        device_info: Dictionary containing device information

    Returns:
        Formatted device information string
    """
    template = "VID:{vendor_id:04x}, DID:{device_id:04x}"

    # Add optional fields if present
    if "class_code" in device_info:
        template += ", Class:{class_code:04x}"
    if "subsystem_vendor_id" in device_info:
        template += ", SVID:{subsystem_vendor_id:04x}"
    if "subsystem_device_id" in device_info:
        template += ", SDID:{subsystem_device_id:04x}"

    return safe_format(template, **device_info)


def build_progress_string(
    operation: str, current: int, total: int, elapsed_time: Optional[float] = None
) -> str:
    """
    Build a standardized progress string.

    Args:
        operation: Description of the current operation
        current: Current progress value
        total: Total expected value
        elapsed_time: Optional elapsed time in seconds

    Returns:
        Formatted progress string
    """
    percentage = (current / total * 100) if total > 0 else 0
    template = "{operation}: {current}/{total} ({percentage:.1f}%)"

    if elapsed_time is not None:
        template += " - {elapsed_time:.1f}s elapsed"

    return safe_format(
        template,
        operation=operation,
        current=current,
        total=total,
        percentage=percentage,
        elapsed_time=elapsed_time,
    )


def build_file_size_string(size_bytes: int) -> str:
    """
    Build a human-readable file size string.

    Args:
        size_bytes: Size in bytes

    Returns:
        Formatted size string (e.g., "1.5 MB", "256 KB")
    """
    if size_bytes < 1024:
        return safe_format("{size} bytes", size=size_bytes)
    elif size_bytes < 1024 * 1024:
        size_kb = size_bytes / 1024
        return safe_format(
            "{size:.1f} KB ({bytes} bytes)", size=size_kb, bytes=size_bytes
        )
    else:
        size_mb = size_bytes / (1024 * 1024)
        return safe_format(
            "{size:.1f} MB ({bytes} bytes)", size=size_mb, bytes=size_bytes
        )


# Convenience functions for common logging patterns
def log_info_safe(logger: logging.Logger, template: str, **kwargs: Any) -> None:
    """Convenience function for safe INFO level logging."""
    safe_log_format(logger, logging.INFO, template, **kwargs)


def log_error_safe(logger: logging.Logger, template: str, **kwargs: Any) -> None:
    """Convenience function for safe ERROR level logging."""
    safe_log_format(logger, logging.ERROR, template, **kwargs)


def log_warning_safe(logger: logging.Logger, template: str, **kwargs: Any) -> None:
    """Convenience function for safe WARNING level logging."""
    safe_log_format(logger, logging.WARNING, template, **kwargs)


def log_debug_safe(logger: logging.Logger, template: str, **kwargs: Any) -> None:
    """Convenience function for safe DEBUG level logging."""
    safe_log_format(logger, logging.DEBUG, template, **kwargs)


def generate_sv_header_comment(
    title: str,
    vendor_id: Optional[str] = None,
    device_id: Optional[str] = None,
    board: Optional[str] = None,
    **kwargs: Any,
) -> str:
    """
    Generate a standardized SystemVerilog header comment block.

    This function creates a consistent header format used across SystemVerilog
    modules with device-specific information.

    Args:
        title: The main title/description for the module
        vendor_id: Optional vendor ID (will be included if provided)
        device_id: Optional device ID (will be included if provided)
        board: Optional board name (will be included if provided)
        **kwargs: Additional key-value pairs to include in the header

    Returns:
        Formatted SystemVerilog header comment block

    Example:
        >>> generate_sv_header_comment(
        ...     "Device Configuration Module",
        ...     vendor_id="1234", device_id="5678", board="AC701"
        ... )
        '//==============================================================================\\n// Device Configuration Module - Generated for 1234:5678\\n// Board: AC701\\n//=============================================================================='

        >>> generate_sv_header_comment("PCIe Controller Module")
        '//==============================================================================\\n// PCIe Controller Module\\n//=============================================================================='
    """
    lines = [
        "//=============================================================================="
    ]

    # Build the main title line
    if vendor_id and device_id:
        title_line = f"// {title} - Generated for {vendor_id}:{device_id}"
    else:
        title_line = f"// {title}"
    lines.append(title_line)

    # Add board information if provided
    if board:
        lines.append(f"// Board: {board}")

    # Add any additional key-value pairs
    for key, value in kwargs.items():
        if value is not None:
            # Convert key from snake_case to Title Case for display
            display_key = key.replace("_", " ").title()
            lines.append(f"// {display_key}: {value}")

    lines.append(
        "//=============================================================================="
    )

    return "\n".join(lines)


def generate_tcl_header_comment(
    title: str,
    vendor_id: Optional[str] = None,
    device_id: Optional[str] = None,
    class_code: Optional[str] = None,
    board: Optional[str] = None,
    fpga_part: Optional[str] = None,
    **kwargs: Any,
) -> str:
    """
    Generate a standardized TCL header comment block.

    This function creates a consistent header format used across TCL build scripts
    with device-specific information.

    Args:
        title: The main title/description for the script
        vendor_id: Optional vendor ID (will be included if provided)
        device_id: Optional device ID (will be included if provided)
        class_code: Optional class code (will be included if provided)
        board: Optional board name (will be included if provided)
        fpga_part: Optional FPGA part number (will be included if provided)
        **kwargs: Additional key-value pairs to include in the header

    Returns:
        Formatted TCL header comment block

    Example:
        >>> generate_tcl_header_comment(
        ...     "PCILeech Firmware Build Script",
        ...     vendor_id="1234", device_id="5678",
        ...     class_code="0200", board="AC701"
        ... )
        '#==============================================================================\\n# PCILeech Firmware Build Script\\n# Generated for device 1234:5678 (Class: 0200)\\n# Board: AC701\\n#=============================================================================='
    """
    lines = [
        "#=============================================================================="
    ]

    # Build the main title line
    lines.append(f"# {title}")

    # Add device information if provided
    if vendor_id and device_id:
        device_line = f"# Generated for device {vendor_id}:{device_id}"
        if class_code:
            device_line += f" (Class: {class_code})"
        lines.append(device_line)

    # Add board information if provided
    if board:
        lines.append(f"# Board: {board}")

    # Add FPGA part information if provided
    if fpga_part:
        lines.append(f"# FPGA Part: {fpga_part}")

    # Add any additional key-value pairs
    for key, value in kwargs.items():
        if value is not None:
            # Convert key from snake_case to Title Case for display
            display_key = key.replace("_", " ").title()
            lines.append(f"# {display_key}: {value}")

    lines.append(
        "#=============================================================================="
    )

    return "\n".join(lines)
