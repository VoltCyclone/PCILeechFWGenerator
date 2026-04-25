"""
Error Handling Data Model

Error classification and guidance system for the TUI.
"""

from dataclasses import dataclass
from enum import Enum
from typing import List, Optional


class ErrorSeverity(Enum):
    """Error severity levels."""

    INFO = "info"
    WARNING = "warning"
    ERROR = "error"
    CRITICAL = "critical"


@dataclass
class TUIError:
    """TUI error with guidance information."""

    severity: ErrorSeverity
    category: str  # "device", "config", "build", "flash", "system"
    message: str
    details: Optional[str] = None
    suggested_actions: Optional[List[str]] = None
    documentation_link: Optional[str] = None
    auto_fix_available: bool = False

    def __post_init__(self):
        """Initialize default values."""
        if self.suggested_actions is None:
            self.suggested_actions = []

    @property
    def severity_icon(self) -> str:
        """Get icon for severity level."""
        icons = {
            ErrorSeverity.INFO: "ℹ️",
            ErrorSeverity.WARNING: "⚠️",
            ErrorSeverity.ERROR: "❌",
            ErrorSeverity.CRITICAL: "🚨",
        }
        return icons[self.severity]

    @property
    def severity_color(self) -> str:
        """Get color for severity level."""
        colors = {
            ErrorSeverity.INFO: "blue",
            ErrorSeverity.WARNING: "yellow",
            ErrorSeverity.ERROR: "red",
            ErrorSeverity.CRITICAL: "bright_red",
        }
        return colors[self.severity]

    @property
    def title(self) -> str:
        """Get formatted title for display."""
        return f"{self.severity_icon} {self.severity.value.title()}: {self.message}"

    def add_action(self, action: str) -> None:
        """Add a suggested action."""
        if self.suggested_actions is None:
            self.suggested_actions = []
        if action not in self.suggested_actions:
            self.suggested_actions.append(action)

    def to_dict(self) -> dict:
        """Convert to dictionary for serialization."""
        return {
            "severity": self.severity.value,
            "category": self.category,
            "message": self.message,
            "details": self.details,
            "suggested_actions": self.suggested_actions,
            "documentation_link": self.documentation_link,
            "auto_fix_available": self.auto_fix_available,
        }

    @classmethod
    def from_dict(cls, data: dict) -> "TUIError":
        """Create instance from dictionary."""
        data["severity"] = ErrorSeverity(data["severity"])
        return cls(**data)


# Common error templates
class ErrorTemplates:
    """Pre-defined error templates for common issues."""

    @staticmethod
    def vfio_binding_failed(details: Optional[str] = None) -> TUIError:
        """VFIO binding failure error."""
        return TUIError(
            severity=ErrorSeverity.ERROR,
            category="device",
            message="VFIO binding failed",
            details=details,
            suggested_actions=[
                "Check if IOMMU is enabled in BIOS",
                "Verify vfio-pci module is loaded: " "lsmod | grep vfio",
                "Ensure device is not in use by another driver",
                "Try unbinding the current driver first",
            ],
            documentation_link="https://wiki.archlinux.org/title/PCI_passthrough_via_OVMF",
            auto_fix_available=True,
        )

    @staticmethod
    def container_not_found() -> TUIError:
        """Container image not found error."""
        return TUIError(
            severity=ErrorSeverity.ERROR,
            category="system",
            message="Container image 'pcileechfwgenerator' not found and automatic build failed",
            suggested_actions=[
                "Manually build the container image: podman build -t pcileechfwgenerator:latest .",
                "Check if Podman is properly installed",
                "Verify internet connectivity for downloading base images",
                "Check for sufficient disk space",
            ],
            auto_fix_available=True,
        )

    @staticmethod
    def insufficient_permissions() -> TUIError:
        """Insufficient permissions error."""
        return TUIError(
            severity=ErrorSeverity.CRITICAL,
            category="system",
            message="Insufficient permissions",
            details="Root privileges required for device binding "
            "and container operations",
            suggested_actions=[
                "Run with sudo: sudo python3 pcileech.py tui",
                "Ensure user is in required groups (docker, vfio)",
                "Check system security policies",
            ],
        )

    @staticmethod
    def build_failed(stage: str, details: Optional[str] = None) -> TUIError:
        """Build process failure error."""
        return TUIError(
            severity=ErrorSeverity.ERROR,
            category="build",
            message=f"Build failed during {stage}",
            details=details,
            suggested_actions=[
                "Check build logs for detailed error information",
                "Verify all dependencies are installed",
                "Ensure sufficient disk space is available",
                "Try rebuilding with verbose output",
            ],
        )

    @staticmethod
    def device_not_suitable(issues: List[str]) -> TUIError:
        """Device not suitable for firmware generation."""
        return TUIError(
            severity=ErrorSeverity.WARNING,
            category="device",
            message="Selected device may not be suitable",
            details=f"Issues found: {', '.join(issues)}",
            suggested_actions=[
                "Select a different PCIe device",
                "Check device compatibility requirements",
                "Proceed with caution if you understand the risks",
            ],
        )

    @staticmethod
    def config_file_error(details: Optional[str] = None) -> TUIError:
        """Configuration file access error."""
        return TUIError(
            severity=ErrorSeverity.ERROR,
            category="config",
            message="Configuration file access error",
            details=details,
            suggested_actions=[
                "Check if ~/.pcileech/profiles/ directory exists and is accessible",
                "Ensure you have read/write permissions for your home directory",
                "Try running the application with appropriate permissions",
                "Check available disk space",
            ],
        )
