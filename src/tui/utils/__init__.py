"""
Utility modules for the PCILeech TUI application.

This package contains various utility modules that provide common functionality
used throughout the TUI application.
"""

from .debounced_search import DebouncedSearch
from .graceful_degradation import GracefulDegradation
from .input_validator import InputValidator
from .ui_helpers import (
    format_build_mode,
    format_donor_module_status,
    format_status_messages,
    safely_update_static,
)

# Module-level aliases for backward compatibility
debounced_search = DebouncedSearch
graceful_degradation = GracefulDegradation
input_validator = InputValidator

__all__ = [
    "DebouncedSearch",
    "safely_update_static",
    "format_donor_module_status",
    "format_status_messages",
    "format_build_mode",
    "GracefulDegradation",
    "InputValidator",
    "debounced_search",
    "graceful_degradation",
    "input_validator",
]
