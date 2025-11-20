#!/usr/bin/env python3
"""
Centralized Build Logging System

Provides consistent logging prefixes and formatting for the build process.
"""

import logging

from typing import Optional

from src.string_utils import (
    log_debug_safe,
    log_error_safe,
    log_info_safe,
    log_warning_safe,
    safe_format,
)


class BuildLogger:
    """Centralized build logger with consistent prefixes."""

    # Standardized prefixes for different build phases
    PREFIXES = {
        "build": "BUILD",           # Main build orchestration
        "vfio": "VFIO",             # VFIO operations
        "host_cfg": "HOST_CFG",     # Host configuration loading
        "device": "DEVICE",         # Device detection/analysis
        "template": "TEMPLATE",     # Template operations
        "vivado": "VIVADO",         # Vivado integration
        "validation": "VALID",      # Validation checks
        "filemgr": "FILEMGR",       # File management
        "msix": "MSIX",             # MSI-X operations
        "bar": "BAR",               # BAR analysis
        "pcil": "PCIL",             # PCILeech generator
        "repo": "REPO",             # Repository operations
    }

    def __init__(self, logger: Optional[logging.Logger] = None):
        """Initialize with optional logger."""
        self.logger = logger or logging.getLogger(__name__)
        self._phase_stack = []

    def info(self, message: str, prefix: str = "BUILD", **kwargs) -> None:
        """Log info message with consistent prefix."""
        prefix = self.PREFIXES.get(prefix.lower(), prefix)
        log_info_safe(self.logger, message, prefix=prefix, **kwargs)

    def warning(self, message: str, prefix: str = "BUILD", **kwargs) -> None:
        """Log warning message with consistent prefix."""
        prefix = self.PREFIXES.get(prefix.lower(), prefix)
        log_warning_safe(self.logger, message, prefix=prefix, **kwargs)

    def error(self, message: str, prefix: str = "BUILD", **kwargs) -> None:
        """Log error message with consistent prefix."""
        prefix = self.PREFIXES.get(prefix.lower(), prefix)
        log_error_safe(self.logger, message, prefix=prefix, **kwargs)

    def debug(self, message: str, prefix: str = "BUILD", **kwargs) -> None:
        """Log debug message with consistent prefix."""
        prefix = self.PREFIXES.get(prefix.lower(), prefix)
        log_debug_safe(self.logger, message, prefix=prefix, **kwargs)

    def phase(self, message: str, **kwargs) -> None:
        """Log build phase with special formatting."""
        formatted = safe_format("➤ {msg}", msg=message)
        self.info(formatted, prefix="BUILD", **kwargs)

    def push_phase(self, phase_name: str) -> None:
        """Push a new build phase onto the stack."""
        self._phase_stack.append(phase_name)
        self.phase(f"Starting {phase_name}...")

    def pop_phase(self, phase_name: str) -> None:
        """Pop a build phase from the stack."""
        if self._phase_stack and self._phase_stack[-1] == phase_name:
            self._phase_stack.pop()
            self.phase(f"Completed {phase_name}")
        else:
            self.warning(f"Phase stack mismatch: expected {phase_name}")

    def current_phase(self) -> Optional[str]:
        """Get current build phase."""
        return self._phase_stack[-1] if self._phase_stack else None

    # Convenience methods for specific build components

    def vfio_info(self, message: str, **kwargs) -> None:
        """Log VFIO-related info."""
        self.info(message, prefix="VFIO", **kwargs)

    def device_info(self, message: str, **kwargs) -> None:
        """Log device-related info."""
        self.info(message, prefix="DEVICE", **kwargs)

    def template_info(self, message: str, **kwargs) -> None:
        """Log template-related info."""
        self.info(message, prefix="TEMPLATE", **kwargs)

    def vivado_info(self, message: str, **kwargs) -> None:
        """Log Vivado-related info."""
        self.info(message, prefix="VIVADO", **kwargs)

    def validation_info(self, message: str, **kwargs) -> None:
        """Log validation-related info."""
        self.info(message, prefix="VALID", **kwargs)

    def filemgr_info(self, message: str, **kwargs) -> None:
        """Log file management info."""
        self.info(message, prefix="FILEMGR", **kwargs)

    def msix_info(self, message: str, **kwargs) -> None:
        """Log MSI-X related info."""
        self.info(message, prefix="MSIX", **kwargs)

    def bar_info(self, message: str, **kwargs) -> None:
        """Log BAR analysis info."""
        self.info(message, prefix="BAR", **kwargs)

    def pcil_info(self, message: str, **kwargs) -> None:
        """Log PCILeech generator info."""
        self.info(message, prefix="PCIL", **kwargs)

    def repo_info(self, message: str, **kwargs) -> None:
        """Log repository operations info."""
        self.info(message, prefix="REPO", **kwargs)

    def host_cfg_info(self, message: str, **kwargs) -> None:
        """Log host configuration info."""
        self.info(message, prefix="HOST_CFG", **kwargs)


def get_build_logger(logger: Optional[logging.Logger] = None) -> BuildLogger:
    """Get a build logger instance."""
    return BuildLogger(logger)
