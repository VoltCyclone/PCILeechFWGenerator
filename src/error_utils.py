#!/usr/bin/env python3
"""
Error handling utilities for cleaner exception management.

This module provides utilities to extract root causes from exception chains,
format error messages in a more user-friendly way, and categorize errors
to provide actionable feedback to users.
"""

import logging
import sys
import traceback
import re

from enum import Enum
from typing import Dict, List, Optional, Tuple, Union


class ErrorCategory(Enum):
    """
    Categorization of errors for better user guidance.
    """

    USER_INPUT = "User Input Error"  # User-provided input is invalid
    CONFIGURATION = "Configuration Error"  # Configuration problem that user can fix
    PERMISSION = "Permission Error"  # Permission denied, access issues
    RESOURCE = "Resource Error"  # Missing resources, unavailable systems
    TEMPLATE = "Template Error"  # Template syntax or rendering error
    SYSTEM = "System Error"  # Unexpected system errors
    NETWORK = "Network Error"  # Network connectivity issues
    DATA = "Data Error"  # Data parsing or format issues
    UNKNOWN = "Unknown Error"  # Uncategorized errors
    MSIX = "MSI-X Error"  # MSI-X specific parsing/validation/config issues


def extract_root_cause(exception: Exception) -> str:
    """
    Extract the root cause from an exception chain.

    Args:
        exception: The exception to extract the root cause from

    Returns:
        The root cause message as a string
    """
    root_cause = str(exception)
    current = exception

    # Walk the exception chain to find the root cause
    while hasattr(current, "__cause__") and current.__cause__:
        current = current.__cause__
        root_cause = str(current)

    return root_cause


def extract_exception_chain(exception: Exception) -> List[str]:
    """
    Extract the full exception chain for detailed error reporting.

    Args:
        exception: The exception to extract the chain from

    Returns:
        List of exception messages in the chain, from most specific to root cause
    """
    chain = [str(exception)]
    current = exception

    while hasattr(current, "__cause__") and current.__cause__:
        current = current.__cause__
        chain.append(str(current))

    return chain


def categorize_error(exception: Exception) -> Tuple[ErrorCategory, str]:
    """
    Categorize an exception to provide better user guidance.

    Args:
        exception: The exception to categorize

    Returns:
        Tuple of (ErrorCategory, suggestion) where suggestion is actionable advice
    """
    error_text = str(exception)
    root_cause = extract_root_cause(exception)

    # MSI-X specific errors (prioritize early for targeted guidance)
    lower_text = error_text.lower()
    lower_root = root_cause.lower()
    if (
        "msix" in lower_text
        or "msi-x" in lower_text
        or "msi x" in lower_text
        or "msix" in lower_root
    ):
        suggestion = _build_msix_suggestion(error_text)
        return (ErrorCategory.MSIX, suggestion)

    # File and permission related errors
    if (
        isinstance(exception, (FileNotFoundError, PermissionError))
        or "Permission denied" in root_cause
    ):
        return (
            ErrorCategory.PERMISSION,
            (
                "Check file permissions and ensure you have access to the "
                "required resources."
            ),
        )

    # Template related errors
    if (
        "Template" in error_text
        or "template" in error_text.lower()
        or "jinja" in error_text.lower()
    ):
        return (
            ErrorCategory.TEMPLATE,
            (
                "There's an issue with the template. Check the template syntax "
                "and ensure all required variables are provided."
            ),
        )

    # Configuration related errors
    if "config" in error_text.lower() or "configuration" in error_text.lower():
        return (
            ErrorCategory.CONFIGURATION,
            "Check your configuration settings and ensure they are valid.",
        )

    # Network related errors
    if (
        "network" in error_text.lower()
        or "connection" in error_text.lower()
        or "timeout" in error_text.lower()
    ):
        return (
            ErrorCategory.NETWORK,
            (
                "Check your network connection and ensure the target service "
                "is available."
            ),
        )

    # Resource related errors
    if "resource" in error_text.lower() or "not found" in error_text.lower():
        return (
            ErrorCategory.RESOURCE,
            "Ensure all required resources are available and properly configured.",
        )

    # Data related errors
    if (
        "data" in error_text.lower()
        or "parse" in error_text.lower()
        or "format" in error_text.lower()
    ):
        return (
            ErrorCategory.DATA,
            (
                "Check your data format and ensure it's valid according to the "
                "expected schema."
            ),
        )

    # Default to unknown
    return (
        ErrorCategory.UNKNOWN,
        "An unexpected error occurred. Check the logs for more details.",
    )


def _build_msix_suggestion(error_text: str) -> str:
    """
    Build actionable guidance for MSI-X related errors based on message
    content.
    """
    text = error_text.lower()

    tips: List[str] = []

    # Truncated / unreadable capability
    if "truncated" in text or "failed to read" in text or "parse msix" in text:
        tips.append(
            (
                "Config space appears incomplete or unreadable; ensure 4KB PCIe "
                "config space access via VFIO (device bound to vfio-pci)."
            )
        )
        tips.append(("Confirm the device isn't in a low-power state (avoid D3cold)."))

    # Invalid table size
    if "invalid msi-x table size" in text or re.search(r"table size .*must be", text):
        tips.append(
            (
                "MSI-X table size is out of range; use the exact donor device and "
                "capture a clean config space read."
            )
        )

    # Invalid BIR
    if "invalid msi-x table bir" in text or "invalid msi-x pba bir" in text:
        tips.append(
            (
                "BIR points to an invalid BAR; verify BAR discovery under VFIO and "
                "retry with a fresh device capture."
            )
        )

    # Alignment issues
    if "not" in text and "aligned" in text:
        tips.append(
            (
                "MSI-X table/PBA offsets must be 16-byte aligned; capture a "
                "fresh device profile after a cold boot."
            )
        )

    # Generic MSI-X trouble
    if not tips:
        tips.append(
            (
                "MSI-X capability validation failed; ensure the device supports "
                "MSI-X and you're operating on the correct donor device."
            )
        )

    # Always helpful next-steps
    tips.append(
        (
            "Re-run with --verbose to capture MSI-X offsets/values; review details "
            "in generate.log."
        )
    )
    tips.append(
        (
            "Use 'pcileech.py check --device <BDF>' or run 'vfio_check.py' to "
            "validate VFIO setup (produces vfio_diagnostics.log)."
        )
    )

    return " ".join(f"- {t}" for t in tips)


def log_error_with_root_cause(
    logger: logging.Logger,
    message: str,
    exception: Exception,
    show_full_traceback: bool = False,
) -> None:
    """
    Log an error with the root cause extracted from the exception chain.

    Args:
        logger: The logger to use
        message: The base error message
        exception: The exception that occurred
        show_full_traceback: Whether to show the full traceback (default: False)
    """
    root_cause = extract_root_cause(exception)
    logger.error("%s: %s", message, root_cause)

    if show_full_traceback or logger.isEnabledFor(logging.DEBUG):
        logger.debug("Full traceback:", exc_info=True)


def format_concise_error(message: str, exception: Exception) -> str:
    """
    Format a concise error message with root cause.

    Args:
        message: The base error message
        exception: The exception that occurred

    Returns:
        A formatted error message with root cause
    """
    root_cause = extract_root_cause(exception)
    return f"{message}: {root_cause}"


def format_user_friendly_error(
    exception: Exception, context: Optional[str] = None
) -> str:
    """
    Format an exception as a user-friendly error message with actionable advice.

    Args:
        exception: The exception to format
        context: Optional context about what was happening when the error occurred

    Returns:
        A user-friendly error message with actionable advice
    """
    category, suggestion = categorize_error(exception)
    root_cause = extract_root_cause(exception)

    error_parts = []

    # Add error category as header
    error_parts.append(f"ERROR TYPE: {category.value}")

    # Add context if provided
    if context:
        error_parts.append(f"CONTEXT: {context}")

    # Add root cause
    error_parts.append(f"DETAILS: {root_cause}")

    # Add suggestion
    if category == ErrorCategory.MSIX and suggestion:
        error_parts.append("SUGGESTIONS:")
        # Suggestion may contain bullet-like items joined; display as lines
        for line in (
            suggestion.split(" - ") if " - " in suggestion else suggestion.split("- ")
        ):
            line = line.strip()
            if not line:
                continue
            if not line.startswith("-"):
                line = f"- {line}"
            error_parts.append(f"  {line}")
    else:
        error_parts.append(f"SUGGESTION: {suggestion}")

    return "\n".join(error_parts)


def format_detailed_error(
    exception: Exception,
    context: Optional[str] = None,
    include_traceback: bool = False,
) -> str:
    """
    Format a detailed error report with full exception chain and optional traceback.

    Args:
        exception: The exception to format
        context: Optional context about what was happening when the error occurred
        include_traceback: Whether to include the full traceback

    Returns:
        A detailed error report suitable for logs or debug output
    """
    category, suggestion = categorize_error(exception)
    exception_chain = extract_exception_chain(exception)

    error_parts = []

    # Add error category as header
    error_parts.append(f"ERROR CATEGORY: {category.value}")

    # Add context if provided
    if context:
        error_parts.append(f"CONTEXT: {context}")

    # Add exception chain
    error_parts.append("EXCEPTION CHAIN:")
    for i, exc in enumerate(exception_chain):
        error_parts.append(f"  {i+1}. {exc}")

    # Add suggestion(s)
    if category == ErrorCategory.MSIX and suggestion:
        error_parts.append("SUGGESTIONS:")
        for line in (
            suggestion.split(" - ") if " - " in suggestion else suggestion.split("- ")
        ):
            line = line.strip()
            if not line:
                continue
            if not line.startswith("-"):
                line = f"- {line}"
            error_parts.append(f"  {line}")
    else:
        error_parts.append(f"SUGGESTION: {suggestion}")

    # Add traceback if requested
    if include_traceback:
        tb = "".join(
            traceback.format_exception(
                type(exception), exception, exception.__traceback__
            )
        )
        error_parts.append("TRACEBACK:")
        error_parts.append(tb)

    return "\n".join(error_parts)


def is_user_fixable_error(exception: Exception) -> bool:
    """
    Determine if an error is likely fixable by the user.

    Args:
        exception: The exception to check

    Returns:
        True if the error is likely fixable by the user, False otherwise
    """
    category, _ = categorize_error(exception)

    # These categories are typically user-fixable
    user_fixable_categories = [
        ErrorCategory.USER_INPUT,
        ErrorCategory.CONFIGURATION,
        ErrorCategory.PERMISSION,
        ErrorCategory.RESOURCE,
        ErrorCategory.NETWORK,
        ErrorCategory.MSIX,
    ]

    return category in user_fixable_categories
