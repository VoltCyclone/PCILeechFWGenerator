#!/usr/bin/env python3
"""
Custom exceptions for the PCILeech firmware generator.

This module defines a hierarchy of custom exceptions to provide better
error handling and debugging capabilities throughout the application.
"""

from typing import Optional

from src.string_utils import safe_format


class PCILeechError(Exception):
    """Base exception for all PCILeech firmware generator errors."""

    pass


class TemplateError(PCILeechError):
    """Base exception for template-related errors."""

    def __init__(
        self, 
        message: Optional[str] = None, 
        root_cause: Optional[str] = None
        ):
        super().__init__(
            message if message else "Template error occurred"
        )
        self.root_cause = root_cause

    def __str__(self):
        base_msg = super().__str__()
        if self.root_cause and self.root_cause != base_msg:
            return safe_format(
                "{base_msg} | Root cause: {self.root_cause}",
                base_msg=base_msg, self=self
            )    
        return base_msg


class TemplateNotFoundError(TemplateError):
    """Raised when a required template file is not found."""

    def __init__(self, message: Optional[str] = None, root_cause: Optional[str] = None):
        super().__init__(message or "Template file not found", root_cause)

    def __str__(self):
        return super().__str__()


class TemplateRenderError(TemplateError):
    """Raised when template rendering fails."""

    def __init__(
        self,
        message: Optional[str] = None,
        template_name: Optional[str] = None,
        line_number: Optional[int] = None,
        original_error: Optional[Exception] = None,
        root_cause: Optional[str] = None,
    ):
        """Initialize TemplateRenderError with optional rendering context.

        Backwards-compatible with previous signature that accepted only
        (message, root_cause). New optional fields provide richer
        diagnostic information for template rendering failures.
        """
        super().__init__(message or "Template rendering failed", root_cause)
        self.template_name = template_name
        self.line_number = line_number
        self.original_error = original_error

    def __str__(self):
        # For test compatibility, return the original message
        if hasattr(self, "args") and self.args:
            return str(self.args[0])
        return "Template rendering failed"


class DeviceConfigError(PCILeechError):
    """Raised when device configuration is invalid or unavailable."""

    def __init__(self, message: Optional[str] = None, root_cause: Optional[str] = None):
        super().__init__(message or "Device configuration error", root_cause)
        self.root_cause = root_cause

    def __str__(self):
        base_msg = super().__str__()
        if self.root_cause and self.root_cause != base_msg:
            return f"{base_msg} | Root cause: {self.root_cause}"
        return base_msg


class TCLBuilderError(PCILeechError):
    """Base exception for TCL builder operations."""

    def __init__(self, message: Optional[str] = None, root_cause: Optional[str] = None):
        super().__init__(message or "TCL builder error", root_cause)
        self.root_cause = root_cause

    def __str__(self):
        base_msg = super().__str__()
        if self.root_cause and self.root_cause != base_msg:
            return f"{base_msg} | Root cause: {self.root_cause}"
        return base_msg


class XDCConstraintError(PCILeechError):
    """Raised when XDC constraint operations fail."""

    def __init__(self, message: Optional[str] = None, root_cause: Optional[str] = None):
        super().__init__(message or "XDC constraint error", root_cause)
        self.root_cause = root_cause

    def __str__(self):
        base_msg = super().__str__()
        if self.root_cause and self.root_cause != base_msg:
            return f"{base_msg} | Root cause: {self.root_cause}"
        return base_msg


class RepositoryError(PCILeechError):
    """Raised when repository operations fail."""

    def __init__(self, message: Optional[str] = None, root_cause: Optional[str] = None):
        super().__init__(message or "Repository error", root_cause)
        self.root_cause = root_cause

    def __str__(self):
        base_msg = super().__str__()
        if self.root_cause and self.root_cause != base_msg:
            return f"{base_msg} | Root cause: {self.root_cause}"
        return base_msg


class BuildError(PCILeechError):
    """Raised when build operations fail."""

    def __init__(self, message: str, root_cause: Optional[str] = None):
        super().__init__(message)
        self.root_cause = root_cause

    def __str__(self):
        # For test compatibility, return the original message
        if hasattr(self, "args") and self.args:
            return str(self.args[0])
        return super().__str__()


class PCILeechBuildError(PCILeechError):
    """Base exception for PCILeech build errors.

    This exception serves as the base for all build-related errors
    and inherits from PCILeechError to maintain hierarchy consistency.
    """

    pass


class ConfigurationError(PCILeechBuildError):
    """Raised when configuration is invalid or missing."""

    pass


class MSIXPreloadError(PCILeechBuildError):
    """Raised when MSI-X data preloading fails."""

    pass


class FileOperationError(PCILeechBuildError):
    """Raised when file operations fail."""

    pass


class VivadoIntegrationError(PCILeechBuildError):
    """Raised when Vivado integration fails."""

    pass


class ValidationError(PCILeechError):
    """Raised when validation fails."""

    def __init__(self, message: Optional[str] = None, root_cause: Optional[str] = None):
        super().__init__(message or "Validation error", root_cause)
        self.root_cause = root_cause

    def __str__(self):
        base_msg = super().__str__()
        if self.root_cause and self.root_cause != base_msg:
            return f"{base_msg} | Root cause: {self.root_cause}"
        return base_msg


class ContextError(PCILeechError):
    """Exception raised when context building fails."""

    def __init__(self, message: str, root_cause: Optional[str] = None):
        super().__init__(message)
        self.root_cause = root_cause

    def __str__(self):
        if self.root_cause and self.root_cause != str(super().__str__()):
            # Only show root cause if it's different from the main message
            return self.root_cause
        return super().__str__()


class PCILeechGenerationError(PCILeechError):
    """Exception raised when PCILeech generation fails."""

    def __init__(self, message: str, root_cause: Optional[str] = None):
        super().__init__(message)
        self.root_cause = root_cause

    def __str__(self):
        if self.root_cause and self.root_cause != str(super().__str__()):
            # Only show root cause if it's different from the main message
            return self.root_cause
        return super().__str__()


class ModuleImportError(PCILeechBuildError):
    """Raised when module imports fail."""

    def __init__(self, message: Optional[str] = None, root_cause: Optional[str] = None):
        super().__init__(message or "Module not found", root_cause)
        self.root_cause = root_cause

    def __str__(self):
        # For test compatibility, return the original message only
        if hasattr(self, "args") and self.args:
            return str(self.args[0])
        return "Module not found"


class PlatformCompatibilityError(PCILeechError):
    """Raised when a feature is not supported on the current platform."""

    def __init__(
        self,
        message: str,
        current_platform: Optional[str] = None,
        required_platform: Optional[str] = None,
    ):
        super().__init__(message)
        self.current_platform = current_platform
        self.required_platform = required_platform

    def __str__(self):
        base_msg = super().__str__()
        if self.current_platform and self.required_platform:
            return f"{base_msg} (Current: {self.current_platform}, Required: {self.required_platform})"
        return base_msg


class VFIOBindError(Exception):
    """Raised when VFIO binding fails."""

    pass


class VFIODeviceNotFoundError(VFIOBindError):
    """Raised when a VFIO device is not found."""

    pass


class VFIOPermissionError(VFIOBindError):
    """Raised when VFIO operations lack required permissions."""

    pass


class VFIOGroupError(VFIOBindError):
    """Raised when VFIO group operations fail."""

    pass


# Configuration Space Exceptions


class ConfigSpaceError(PCILeechError):
    """Base exception for PCI configuration space operations.

    This exception and its subclasses are used for errors related to
    reading/writing PCI configuration space, distinct from VFIO binding
    operations (which use VFIOBindError).
    """
    
    def __init__(self, message: Optional[str] = None, root_cause: Optional[str] = None):
        super().__init__(message or "PCI config space error", root_cause)
        self.root_cause = root_cause

    def __str__(self):
        base_msg = super().__str__()
        if self.root_cause and self.root_cause != base_msg:
            return f"{base_msg} | Root cause: {self.root_cause}"
        return base_msg

    pass


class VFIOConfigSpaceError(ConfigSpaceError):
    """Raised when VFIO-based config space access fails.

    Note: This is distinct from VFIOBindError which handles driver binding.
    This exception is for errors reading config space via VFIO after binding.
    """

    pass


class SysfsConfigSpaceError(ConfigSpaceError):
    """Raised when sysfs-based config space access fails."""

    pass


def is_platform_error(message: str) -> bool:
    """Return True if message indicates a known platform incompatibility.

    Centralized heuristic used across modules to detect when an error should
    be treated as a platform support issue (e.g. attempting Linux‑only
    features on macOS). Update patterns here when new guard messages are
    introduced. Keep patterns concise to avoid false positives.
    """
    patterns = (
        "requires Linux",
        "Current platform:",
        "only available on Linux",
        "platform incompatibility",
    )
    return any(p in message for p in patterns)


# Export all exception classes
__all__ = [
    "PCILeechError",
    "ConfigurationError",
    "TemplateError",
    "TemplateNotFoundError",
    "TemplateRenderError",
    "DeviceConfigError",
    "TCLBuilderError",
    "XDCConstraintError",
    "RepositoryError",
    "BuildError",
    "PCILeechBuildError",
    "MSIXPreloadError",
    "FileOperationError",
    "VivadoIntegrationError",
    "ValidationError",
    "ContextError",
    "PCILeechGenerationError",
    "ModuleImportError",
    "PlatformCompatibilityError",
    "VFIOBindError",
    "VFIODeviceNotFoundError",
    "VFIOPermissionError",
    "VFIOGroupError",
    "ConfigSpaceError",
    "VFIOConfigSpaceError",
    "SysfsConfigSpaceError",
    "is_platform_error",
]
