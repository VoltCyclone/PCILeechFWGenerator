#!/usr/bin/env python3
"""
String utilities for safe formatting operations.

This module provides utilities to handle complex string formatting
operations safely, particularly for multi-line f-strings that can
cause syntax errors when split across lines.
"""

import logging
import os
import re

from dataclasses import dataclass
from datetime import datetime, timezone
from functools import lru_cache

from typing import Any, ClassVar, Dict, Iterable, List, Optional, Tuple

# Define the project name constant locally to avoid circular imports
VIVADO_PROJECT_NAME = "pcileech_firmware"


def get_project_name() -> str:
    """
    Return the canonical project name for Vivado/PCILeech builds.
    Always use this function or VIVADO_PROJECT_NAME for project naming.
    """
    return VIVADO_PROJECT_NAME


# Short constants to keep lines within linter limits while reusing the same banner
# Note: dynamic borders are constructed in helpers to satisfy line-length rules.
SV_HEADER_BAR = "//=="
TCL_HEADER_BAR = "#=="


@dataclass
class FormatConfig:
    """Runtime configuration controlling formatting behavior."""

    timestamp_format: str = "%H:%M:%S"
    use_unicode_tables: bool = True
    max_table_width: int = 120
    log_padding_width: int = 7
    default_encoding: str = "utf-8"

    _instance: ClassVar[Optional["FormatConfig"]] = None
    @classmethod
    def get_instance(cls) -> "FormatConfig":
        if cls._instance is None:
            cls._instance = cls()
        return cls._instance


class TableFormatter:
    """Format tabular data with configurable border styles."""

    UNICODE_STYLE = {
        "top_left": "┌",
        "top_right": "┐",
        "top_join": "┬",
        "mid_left": "├",
        "mid_right": "┤",
        "mid_join": "┼",
        "bot_left": "└",
        "bot_right": "┘",
        "bot_join": "┴",
        "horizontal": "─",
        "vertical": "│",
    }

    ASCII_STYLE = {
        "top_left": "+",
        "top_right": "+",
        "top_join": "+",
        "mid_left": "+",
        "mid_right": "+",
        "mid_join": "+",
        "bot_left": "+",
        "bot_right": "+",
        "bot_join": "+",
        "horizontal": "-",
        "vertical": "|",
    }

    def __init__(self, style: str = "unicode") -> None:
        style_lower = style.lower()
        if style_lower not in {"unicode", "ascii"}:
            raise ValueError(f"Unsupported table style: {style}")
        self.style = self.UNICODE_STYLE if style_lower == "unicode" else self.ASCII_STYLE

    def _border(self, left: str, join: str, right: str, widths: Iterable[int]) -> str:
        segments = [self.style["horizontal"] * (width + 2) for width in widths]
        return self.style[left] + self.style[join].join(segments) + self.style[right]

    def format_table(self, headers: List[str], rows: List[List[str]]) -> str:
        if not headers:
            return ""

        col_widths = [len(str(header)) for header in headers]
        for row in rows:
            for idx, cell in enumerate(row):
                col_widths[idx] = max(col_widths[idx], len(str(cell)))

        top = self._border("top_left", "top_join", "top_right", col_widths)
        header_line = self.style["vertical"] + self.style["vertical"].join(
            f" {str(headers[i]):<{col_widths[i]}} " for i in range(len(headers))
        ) + self.style["vertical"]
        mid = self._border("mid_left", "mid_join", "mid_right", col_widths)
        data_lines = [
            self.style["vertical"]
            + self.style["vertical"].join(
                f" {str(row[i]):<{col_widths[i]}} " for i in range(len(headers))
            )
            + self.style["vertical"]
            for row in rows
        ]
        bottom = self._border("bot_left", "bot_join", "bot_right", col_widths)

        return "\n".join([top, header_line, mid, *data_lines, bottom])


def _build_cache_key(template: str, kwargs: Dict[str, Any]) -> Optional[Tuple[Tuple[str, Any], ...]]:
    """Return a hashable cache key for kwargs when possible."""

    frozen_items: List[Tuple[str, Any]] = []
    for key, value in kwargs.items():
        try:
            hash(value)
        except TypeError:
            return None
        frozen_items.append((key, value))

    frozen_items.sort(key=lambda item: item[0])
    return tuple(frozen_items)


@lru_cache(maxsize=128)


def _cached_format(template: str, frozen_items: Tuple[Tuple[str, Any], ...]) -> str:
    """Cache formatted strings for repeated template/kwargs pairs."""

    return template.format(**dict(frozen_items))


def safe_format(template: str, prefix: Optional[str] = None, **kwargs: Any) -> str:
    """
    Safely format a string template with the given keyword arguments.

    This function provides a safe alternative to f-strings when dealing
    with complex multi-line formatting that might cause syntax errors.

    Args:
        template: The string template with {variable} placeholders
        prefix: Optional prefix to add to the formatted message
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

        >>> safe_format("Processing device {bdf}", prefix="VFIO", bdf="0000:01:00.0")
        '[VFIO] Processing device 0000:01:00.0'
    """
    try:
        cache_key = _build_cache_key(template, kwargs)
        if cache_key is not None:
            formatted_message = _cached_format(template, cache_key)
        else:
            formatted_message = template.format(**kwargs)
        if prefix:
            return f"[{prefix}] {formatted_message}"
        return formatted_message
    except KeyError as e:
        # Handle missing keys gracefully
        missing_key = str(e).strip("'\"")
        logger = logging.getLogger(__name__)
        logger.warning("Missing key '%s' in string template", missing_key)
        pattern = re.compile(rf"\{{{re.escape(missing_key)}(:[^}}]+)?\}}")
        formatted_message = pattern.sub(f"<MISSING:{missing_key}>", template)
        if prefix:
            return f"[{prefix}] {formatted_message}"
        return formatted_message
    except ValueError as e:
        # Handle format specification errors
        logger = logging.getLogger(__name__)
        logger.error("Format error in string template: %s", e)
        if prefix:
            return f"[{prefix}] {template}"
        return template
    except Exception as e:
        # Handle any other unexpected errors
        logger = logging.getLogger(__name__)
        logger.error("Unexpected error in safe_format: %s", e)
        if prefix:
            return f"[{prefix}] {template}"
        return template


def safe_log_format(
    logger: logging.Logger,
    log_level: int,
    template: str,
    prefix: Optional[str] = None,
    **kwargs: Any,
) -> None:
    """
    Safely log a formatted message with padding and short timestamps.

    Args:
        logger: The logger instance to use
        log_level: The logging level (e.g., logging.INFO, logging.ERROR)
        template: The string template with {variable} placeholders
        prefix: Optional prefix to add to the log message (e.g., "VFIO", "BUILD")
        **kwargs: Keyword arguments to substitute in the template

    Example:
        >>> import logging
        >>> logger = logging.getLogger(__name__)
        >>> safe_log_format(logger, logging.INFO,
        ...                  "Processing device {bdf} with {bytes} bytes",
        ...                  prefix="VFIO", bdf="0000:00:1f.3", bytes=256)
    """
    try:
        formatted_message = safe_format(template, prefix=prefix, **kwargs)

        # Map log level to string
        level_map = {
            logging.INFO: "INFO",
            logging.WARNING: "WARNING",
            logging.DEBUG: "DEBUG",
            logging.ERROR: "ERROR",
            logging.CRITICAL: "CRITICAL",
        }
        level_str = level_map.get(log_level, "UNKNOWN")

        padded_message = format_padded_message(formatted_message, level_str)
        # Call the specific logger method so mocks like mock_logger.info are
        # invoked during tests (instead of logger.log which doesn't call them).
        if log_level == logging.INFO:
            logger.info(padded_message)
        elif log_level == logging.WARNING:
            logger.warning(padded_message)
        elif log_level == logging.DEBUG:
            logger.debug(padded_message)
        elif log_level == logging.ERROR:
            logger.error(padded_message)
        elif log_level == logging.CRITICAL:
            logger.critical(padded_message)
        else:
            logger.log(log_level, padded_message)
    except Exception as e:
        # Fallback to basic logging if formatting fails
        error_msg = f"Failed to format log message: {e}"
        padded_error = format_padded_message(error_msg, "ERROR")
        logger.error(padded_error)

        fallback_message = f"Original template: {template}"
        if prefix:
            fallback_message = f"[{prefix}] {fallback_message}"
        padded_fallback = format_padded_message(fallback_message, "ERROR")
        # Use error to ensure visibility of the fallback
        logger.error(padded_fallback)


def safe_print_format(template: str, prefix: str, **kwargs: Any) -> None:
    """
    Safely print a formatted message with padding and short timestamp.

    Args:
        template: The string template with {variable} placeholders
        prefix: Optional prefix to add to the message
        **kwargs: Keyword arguments to substitute in the template

    Example:
        >>> safe_print_format("Build completed in {time:.2f} seconds",
        ...                   prefix="BUILD", time=45.67)
        14:23:45 │  INFO  │ [BUILD] Build completed in 45.67 seconds
    """
    try:
        formatted_message = safe_format(template=template, prefix=prefix, **kwargs)
        padded_message = format_padded_message(formatted_message, "INFO")
        print(padded_message)
    except Exception as e:
        error_msg = f"Failed to format message: {e}"
        padded_error = format_padded_message(error_msg, "ERROR")
        print(padded_error)

        fallback_msg = f"Original template: {template}"
        padded_fallback = format_padded_message(fallback_msg, "ERROR")
        print(padded_fallback)


def multiline_format(template: str, prefix: str, **kwargs: Any) -> str:
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
    return safe_format(template, prefix=prefix, **kwargs)


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
        prefix="Progress",
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
        return safe_format("{size} bytes", prefix="File Size", size=size_bytes)
    elif size_bytes < 1024 * 1024:
        size_kb = size_bytes / 1024
        return safe_format(
            "{size:.1f} KB ({bytes} bytes)",
            prefix="File Size",
            size=size_kb,
            bytes=size_bytes,
        )
    else:
        size_mb = size_bytes / (1024 * 1024)
        return safe_format(
            "{size:.1f} MB ({bytes} bytes)",
            prefix="File Size",
            size=size_mb,
            bytes=size_bytes,
        )


def format_size_short(size_bytes: int) -> str:
    """
    Return a short human-readable size string using binary units.

    Examples:
        512 -> "512B"
        2048 -> "2.0KB"
        1048576 -> "1.0MB"
        1073741824 -> "1.0GB"

    This helper is intended for concise in-line logs without prefixes.
    """
    try:
        if size_bytes >= 1024 * 1024 * 1024:
            return f"{size_bytes / (1024 * 1024 * 1024):.1f}GB"
        elif size_bytes >= 1024 * 1024:
            return f"{size_bytes / (1024 * 1024):.1f}MB"
        elif size_bytes >= 1024:
            return f"{size_bytes / 1024:.1f}KB"
        else:
            return f"{size_bytes}B"
    except Exception:
        # Fallback to raw bytes on any unexpected error
        return f"{size_bytes}B"


def get_short_timestamp() -> str:
    """
    Get a short timestamp string for logging.

    Returns:
        Short timestamp in format HH:MM:SS

    Example:
        >>> get_short_timestamp()
        '14:23:45'
    """
    fmt = FormatConfig.get_instance().timestamp_format
    return datetime.now().strftime(fmt)


def utc_timestamp(
    precise: bool = False,
    env_var: str = "BUILD_TIMESTAMP",
    fallback: str = "2024-01-01T00:00:00Z",
) -> str:
    """Return a standardized UTC ISO-8601 timestamp with trailing Z.

    Args:
        precise: Include microseconds if True (default False)
        env_var: Environment variable that, if set, overrides the timestamp
        fallback: Fallback timestamp if generation fails

    Behavior:
        - If the environment variable exists, it's validated (appends 'Z' if
          missing and contains no timezone). Returned as-is otherwise.
        - Uses timezone-aware UTC datetime; strips microseconds unless
          precise=True.
        - Always normalizes '+00:00' suffix to 'Z'.
    """
    try:
        override = os.getenv(env_var)
        if override:
            # Basic normalization: ensure trailing Z if no timezone specified
            if override.endswith("Z") or override.endswith("z"):
                return override.rstrip("zZ") + "Z"
            if "+" in override or override.endswith("Z"):
                return override.replace("+00:00", "Z")
            return override + "Z"

        dt = datetime.now(timezone.utc)
        if not precise:
            dt = dt.replace(microsecond=0)
        return dt.isoformat().replace("+00:00", "Z")
    except Exception:
        return fallback


def format_padded_message(message: str, log_level: str) -> str:
    """
    Format a message with padding based on log level.

    Args:
        message: The message to format
        log_level: The log level (INFO, WARNING, DEBUG, ERROR)

    Returns:
        Formatted message with appropriate padding

    Example:
        >>> format_padded_message("Device found", "INFO")
        '  INFO  │ Device found'
        >>> format_padded_message("Memory issue", "WARNING")
        ' WARNING│ Memory issue'
    """
    timestamp = get_short_timestamp()
    config = FormatConfig.get_instance()

    level_defaults = {
        "INFO": " INFO  ",
        "WARNING": "WARNING",
        "DEBUG": " DEBUG ",
        "ERROR": "ERROR  ",
        "CRITICAL": "CRITCL",
    }

    level_segment = level_defaults.get(log_level, log_level)
    if len(level_segment) < config.log_padding_width:
        level_segment = level_segment.ljust(config.log_padding_width)
    else:
        level_segment = level_segment[: config.log_padding_width]

    return f"  {timestamp} │ {level_segment}│ {message}"


# Convenience functions for common logging patterns


def log_info_safe(
    logger: logging.Logger,
    template: str,
    prefix: Optional[str] = None,
    **kwargs: Any,
) -> None:
    """Convenience function for safe INFO level logging."""
    # Use the centralized safe_log_format to ensure padding/timestamp and
    # consistent behavior across log levels.
    safe_log_format(logger, logging.INFO, template, prefix=prefix, **kwargs)


def log_error_safe(
    logger: logging.Logger,
    template: str,
    prefix: Optional[str] = None,
    **kwargs: Any,
) -> None:
    """Convenience function for safe ERROR level logging."""
    safe_log_format(logger, logging.ERROR, template, prefix=prefix, **kwargs)


def log_warning_safe(
    logger: logging.Logger,
    template: str,
    prefix: Optional[str] = None,
    **kwargs: Any,
) -> None:
    """Convenience function for safe WARNING level logging."""
    safe_log_format(logger, logging.WARNING, template, prefix=prefix, **kwargs)


def log_debug_safe(
    logger: logging.Logger,
    template: str,
    prefix: Optional[str] = None,
    **kwargs: Any,
) -> None:
    """Convenience function for safe DEBUG level logging."""
    safe_log_format(logger, logging.DEBUG, template, prefix=prefix, **kwargs)


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
        '//... Device Configuration Module - Generated for 1234:5678 ...'

        >>> generate_sv_header_comment("PCIe Controller Module")
        '//... PCIe Controller Module ...'
    """
    from src.utils.validation_constants import SV_FILE_HEADER

    # Use the first line of the standardized header as a base (single-line banner)
    header_base = (
        SV_FILE_HEADER.split("\n")[0] if "\n" in SV_FILE_HEADER else SV_FILE_HEADER
    )

    # Build border line dynamically to satisfy line-length rules
    sv_border = "//" + "=" * 78
    lines = [sv_border, header_base]

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

    lines.append(sv_border)

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
    '# ... PCILeech Firmware Build Script ...'
    """
    from src.utils.validation_constants import TCL_FILE_HEADER

    # Use the standardized header as a base (single-line banner)
    header_base = (
        TCL_FILE_HEADER.split("\n")[0] if "\n" in TCL_FILE_HEADER else TCL_FILE_HEADER
    )

    # Build border line dynamically to satisfy line-length rules
    tcl_border = "#" + "=" * 78
    lines = [tcl_border, header_base]

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

    lines.append(tcl_border)

    return "\n".join(lines)


def generate_hex_header_comment(
    title: str,
    total_bytes: Optional[int] = None,
    total_dwords: Optional[int] = None,
    vendor_id: Optional[str] = None,
    device_id: Optional[str] = None,
    class_code: Optional[str] = None,
    board: Optional[str] = None,
    **kwargs: Any,
) -> str:
    """
    Generate a standardized HEX file header comment block.

    This creates a consistent header used for .hex files intended for
    $readmemh initialization, aligning with other header styles.

    Args:
        title: Main title/description (e.g., "config_space_init.hex - ...")
        total_bytes: Optional total byte count included when provided
        total_dwords: Optional total dword count included when provided
        vendor_id: Optional vendor ID to display
        device_id: Optional device ID to display
        class_code: Optional class code to display
        board: Optional board identifier
        **kwargs: Additional key-value pairs to include as comments

    Returns:
        Formatted HEX header comment block
    """
    from src.utils.validation_constants import HEX_FILE_HEADER

    # First line from standardized header as base
    header_base = (
        HEX_FILE_HEADER.split("\n")[0] if "\n" in HEX_FILE_HEADER else HEX_FILE_HEADER
    )

    # Dynamic border line to respect line-length rules
    hex_border = "//" + "=" * 78
    lines = [hex_border, header_base]

    # Title
    lines.append(f"// {title}")

    # Optional device information
    if vendor_id and device_id:
        dev_line = f"// Generated for device {vendor_id}:{device_id}"
        if class_code:
            dev_line += f" (Class: {class_code})"
        lines.append(dev_line)

    # Optional board info
    if board:
        lines.append(f"// Board: {board}")

    # Totals if provided
    if total_bytes is not None and total_dwords is not None:
        lines.append(f"// Total size: {total_bytes} bytes ({total_dwords} dwords)")
    elif total_bytes is not None:
        lines.append(f"// Total size: {total_bytes} bytes")

    # Additional key/value pairs
    for key, value in kwargs.items():
        if value is not None:
            display_key = key.replace("_", " ").title()
            lines.append(f"// {display_key}: {value}")

    lines.append(hex_border)

    return "\n".join(lines)


def format_bar_table(bar_configs: List[Any], primary_bar: Any = None) -> str:
    """
    Format BAR configuration data into a nice ASCII table.

    Args:
        bar_configs: List of BarConfiguration objects
        primary_bar: Optional primary BAR to highlight

    Returns:
        Formatted ASCII table string
    """
    if not bar_configs:
        return "No BAR configurations found"

    headers = [
        "BAR",
        "Address",
        "Size",
        "Size (MB)",
        "Type",
        "Prefetch",
        "Memory",
        "Candidate",
        "Primary",
    ]

    rows: List[List[str]] = []
    for bar_info in bar_configs:
        is_candidate = (
            getattr(bar_info, "is_memory", False) and getattr(bar_info, "size", 0) > 0
        )
        is_primary = primary_bar and getattr(bar_info, "index", None) == getattr(
            primary_bar, "index", None
        )

        size_bytes = getattr(bar_info, "size", 0)
        size_mb = (size_bytes / (1024 * 1024)) if size_bytes > 0 else 0

        rows.append(
            [
                str(getattr(bar_info, "index", "unknown")),
                f"0x{getattr(bar_info, 'base_address', 0):08X}",
                f"{size_bytes:,}",
                f"{size_mb:.2f}" if size_mb > 0 else "0.00",
                "memory" if getattr(bar_info, "is_memory", False) else "io",
                "yes" if getattr(bar_info, "prefetchable", False) else "no",
                "yes" if getattr(bar_info, "is_memory", False) else "no",
                "yes" if is_candidate else "no",
                "★" if is_primary else "",
            ]
        )

    formatter = TableFormatter(
        "unicode" if FormatConfig.get_instance().use_unicode_tables else "ascii"
    )
    return formatter.format_table(headers, rows)


def format_bar_summary_table(bar_configs: List[Any], primary_bar: Any = None) -> str:
    """
    Format a compact BAR summary table showing only essential information.

    Args:
        bar_configs: List of BarConfiguration objects
        primary_bar: Optional primary BAR to highlight

    Returns:
        Formatted ASCII table string
    """
    if not bar_configs:
        return "No BAR configurations found"

    headers = ["BAR", "Address", "Size (MB)", "Type", "Status"]
    rows: List[List[str]] = []

    for bar_info in bar_configs:
        is_candidate = (
            getattr(bar_info, "is_memory", False) and getattr(bar_info, "size", 0) > 0
        )
        is_primary = primary_bar and getattr(bar_info, "index", None) == getattr(
            primary_bar, "index", None
        )

        size_bytes = getattr(bar_info, "size", 0)
        size_mb = (size_bytes / (1024 * 1024)) if size_bytes > 0 else 0

        if is_primary:
            status = "PRIMARY ★"
        elif is_candidate:
            status = "candidate"
        elif size_bytes == 0:
            status = "empty"
        elif not getattr(bar_info, "is_memory", False):
            status = "I/O port"
        else:
            status = "skipped"

        rows.append(
            [
                str(getattr(bar_info, "index", "unknown")),
                f"0x{getattr(bar_info, 'base_address', 0):08X}",
                f"{size_mb:.2f}" if size_mb > 0 else "0.00",
                "memory" if getattr(bar_info, "is_memory", False) else "io",
                status,
            ]
        )

    formatter = TableFormatter(
        "unicode" if FormatConfig.get_instance().use_unicode_tables else "ascii"
    )
    return formatter.format_table(headers, rows)


def format_raw_bar_table(bars: List[Any], device_bdf: str) -> str:
    """
    Format raw BAR data from config space into a nice ASCII table.

    Args:
        bars: List of raw BAR data (dict or int values)
        device_bdf: Device BDF for context

    Returns:
        Formatted ASCII table string
    """
    if not bars:
        return "No BAR data found"

    headers = ["BAR", "Type", "Address", "Size", "Prefetchable", "64-bit"]
    rows: List[List[str]] = []

    for i, bar_data in enumerate(bars[:6]):
        if isinstance(bar_data, dict):
            rows.append(
                [
                    str(i),
                    bar_data.get("type", "unknown"),
                    f"0x{bar_data.get('address', 0):08X}",
                    str(bar_data.get("size", 0)),
                    "Yes" if bar_data.get("prefetchable", False) else "No",
                    "Yes" if bar_data.get("is_64bit", False) else "No",
                ]
            )
        else:
            rows.append(
                [
                    str(i),
                    "unknown",
                    f"0x{bar_data:08X}",
                    "unknown",
                    "unknown",
                    "unknown",
                ]
            )

    formatter = TableFormatter(
        "unicode" if FormatConfig.get_instance().use_unicode_tables else "ascii"
    )
    return formatter.format_table(headers, rows)


def format_kv_table(rows: List[Tuple[str, str]], title: str) -> str:
    """
    Format a simple key/value table with a banner title.

    Args:
        rows: List of (key, value) pairs to display
        title: Title to show in the banner

    Returns:
        Box-drawn table string
    """
    if rows is None:
        rows = []

    headers = ["Field", "Value"]
    col_widths = [len(headers[0]), len(headers[1])]
    for k, v in rows:
        col_widths[0] = max(col_widths[0], len(str(k)))
        col_widths[1] = max(col_widths[1], len(str(v)))

    formatter = TableFormatter(
        "unicode" if FormatConfig.get_instance().use_unicode_tables else "ascii"
    )
    table = formatter.format_table(headers, [[str(k), str(v)] for k, v in rows])
    lines = table.split("\n")

    title_bar = f"── {title} "
    banner = "┌" + title_bar + "─" * max(0, sum(col_widths) + 5 - len(title_bar)) + "┐"
    return "\n".join([banner] + lines)


def truncate_string(
    text: str,
    max_length: int,
    suffix: str = "...",
    position: str = "end",
) -> str:
    """Truncate text with optional suffix placement."""

    if max_length <= 0:
        return ""

    if len(text) <= max_length:
        return text

    if len(suffix) >= max_length:
        return suffix[:max_length]

    remaining = max_length - len(suffix)
    position_lower = position.lower()

    if position_lower == "start":
        return suffix + text[-remaining:]
    if position_lower == "middle":
        left = remaining // 2
        right = remaining - left
        return text[:left] + suffix + text[-right:]
    return text[:remaining] + suffix


def validate_template(template: str) -> bool:
    """Return True when placeholders in template are well-formed."""

    if template.count("{") != template.count("}"):
        return False

    placeholder_pattern = re.compile(r"(?<!\{)\{([^{}]+)\}(?!\})")
    for match in placeholder_pattern.finditer(template):
        placeholder = match.group(1)
        if ":" in placeholder:
            name, _ = placeholder.split(":", 1)
            if not name.isidentifier():
                return False
        elif not placeholder.isidentifier():
            return False

    return True
