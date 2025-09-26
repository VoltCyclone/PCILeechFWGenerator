#!/usr/bin/env python3

"""
Compatibility layer for SystemVerilog template generation.

This module provides backward compatibility for deprecated APIs and documents
migration paths to the new modular architecture. It serves as a bridge between
legacy code and the refactored templating system.

Phase 3 of the SystemVerilog refactor plan - provides clear deprecation
warnings and migration guidance for users of the old API.
"""

import logging
import warnings
from typing import Any, Dict, Optional

from src.string_utils import log_warning_safe, safe_format

# Import the new implementations
from .systemverilog_generator import MSIXHelper, SystemVerilogGenerator

# Create logger for this module
logger = logging.getLogger(__name__)


class DeprecationHelper:
    """Helper class to manage deprecation warnings and migration guidance."""

    @staticmethod
    def warn_deprecated(old_name: str, new_name: str, version: str = "2.0.0") -> None:
        """Issue a deprecation warning with migration guidance."""
        warning_msg = safe_format(
            "{old} is deprecated and will be removed in v{version}. "
            "Use {new} instead. See site/docs/REFRACTOR_PLAN.md for "
            "migration guide.",
            old=old_name,
            new=new_name,
            version=version,
        )

        warnings.warn(warning_msg, DeprecationWarning, stacklevel=3)
        log_warning_safe(logger, warning_msg, prefix="TEMPLATING")


class LegacySystemVerilogGenerator:
    """
    Legacy compatibility wrapper for SystemVerilogGenerator.

    DEPRECATED: This class exists for backward compatibility only.
    Use src.templating.systemverilog_generator.SystemVerilogGenerator
    directly.
    """

    def __init__(self, *args, **kwargs):
        DeprecationHelper.warn_deprecated(
            "LegacySystemVerilogGenerator", "SystemVerilogGenerator"
        )
        self._generator = SystemVerilogGenerator(*args, **kwargs)

    def __getattr__(self, name: str) -> Any:
        """Delegate all attribute access to the new generator."""
        return getattr(self._generator, name)


class LegacyMSIXHelper:
    """
    Legacy compatibility wrapper for MSIXHelper.

    DEPRECATED: This class exists for backward compatibility only.
    Use src.templating.systemverilog_generator.MSIXHelper directly.
    """

    def __init__(self, *args, **kwargs):
        DeprecationHelper.warn_deprecated("LegacyMSIXHelper", "MSIXHelper")
        self._helper = MSIXHelper(*args, **kwargs)

    def __getattr__(self, name: str) -> Any:
        """Delegate all attribute access to the new helper."""
        return getattr(self._helper, name)


def generate_advanced_systemverilog_legacy(
    context: Dict[str, Any], **kwargs
) -> Dict[str, str]:
    """
    Legacy function for advanced SystemVerilog generation.

    DEPRECATED: Use SystemVerilogGenerator.generate_modules() or
    AdvancedSVGenerator.generate_advanced_modules() instead.
    """
    DeprecationHelper.warn_deprecated(
        "generate_advanced_systemverilog_legacy",
        "SystemVerilogGenerator.generate_modules",
    )

    generator = SystemVerilogGenerator()
    return generator.generate_modules(context, **kwargs)


def read_actual_msix_table_legacy(
    device_path: str, bar_index: int = 0, **kwargs
) -> Optional[Dict[str, Any]]:
    """
    Legacy function for reading MSI-X tables.

    DEPRECATED: Use MSIXHelper methods or device_clone modules instead.
    """
    DeprecationHelper.warn_deprecated(
        "read_actual_msix_table_legacy",
        "MSIXHelper.read_msix_table or device_clone.msix_capability",
    )

    # Return None for legacy compatibility - actual implementation
    # should use the new device_clone infrastructure
    log_warning_safe(
        logger,
        "Legacy MSI-X table reading not implemented. "
        "Use device_clone.msix_capability for hardware access.",
        prefix="TEMPLATING",
    )
    return None


# Migration guidance constants
MIGRATION_GUIDE = {
    "SystemVerilogGenerator": {
        "old_import": "from legacy_module import SystemVerilogGenerator",
        "new_import": (
            "from src.templating.systemverilog_generator "
            "import SystemVerilogGenerator"
        ),
        "changes": [
            "Constructor parameters remain the same",
            "generate_modules() replaces generate_advanced_systemverilog()",
            "Context structure unchanged for basic usage",
        ],
    },
    "MSIXHelper": {
        "old_import": "from legacy_module import MSIXHelper",
        "new_import": (
            "from src.templating.systemverilog_generator " "import MSIXHelper"
        ),
        "changes": [
            "API methods unchanged",
            "Better error handling and validation",
            "Improved hardware integration",
        ],
    },
    "AdvancedSVGenerator": {
        "old_import": "Direct instantiation not available in legacy",
        "new_import": (
            "from src.templating.advanced_sv_generator " "import AdvancedSVGenerator"
        ),
        "changes": [
            "New modular architecture",
            "Separate advanced feature handling",
            "Better performance and error recovery",
        ],
    },
}


def print_migration_guide(component: Optional[str] = None) -> None:
    """
    Print migration guidance for moving from legacy to new APIs.

    Args:
        component: Specific component to show guidance for, or None for all
    """
    if component and component in MIGRATION_GUIDE:
        guides = {component: MIGRATION_GUIDE[component]}
    else:
        guides = MIGRATION_GUIDE

    print("\n=== SystemVerilog Template Migration Guide ===")
    print("See site/docs/REFRACTOR_PLAN.md for complete details\n")

    for name, guide in guides.items():
        print(f"## {name}")
        print(f"Old: {guide['old_import']}")
        print(f"New: {guide['new_import']}")
        print("Changes:")
        for change in guide["changes"]:
            print(f"  - {change}")
        print()


# Backward compatibility exports
__all__ = [
    "LegacySystemVerilogGenerator",
    "LegacyMSIXHelper",
    "generate_advanced_systemverilog_legacy",
    "read_actual_msix_table_legacy",
    "print_migration_guide",
    "MIGRATION_GUIDE",
    "DeprecationHelper",
]


# Module-level deprecation notice
def __getattr__(name: str) -> Any:
    """Handle legacy imports with deprecation warnings."""
    legacy_mappings = {
        "SystemVerilogGeneratorLegacy": LegacySystemVerilogGenerator,
        "MSIXHelperLegacy": LegacyMSIXHelper,
    }

    if name in legacy_mappings:
        DeprecationHelper.warn_deprecated(
            f"compat.{name}", "Direct import from templating modules"
        )
        return legacy_mappings[name]

    raise AttributeError(f"module '{__name__}' has no attribute '{name}'")


if __name__ == "__main__":
    # Print migration guide when run directly
    print_migration_guide()
