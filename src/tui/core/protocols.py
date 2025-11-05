"""
Protocol definitions for mockable components in the PCILeech TUI application.

These protocols define the interfaces that can be implemented by both real
and mock components, enabling dependency injection and testability.
"""

import asyncio
from typing import Any, Dict, List, Optional, Protocol, runtime_checkable

# Import necessary types
from src.tui.models.device import PCIDevice


@runtime_checkable
class DeviceScanner(Protocol):
    """Protocol for components that scan for PCI devices."""

    async def scan_devices(self) -> List[PCIDevice]:
        """
        Scan for available PCI devices.

        Returns:
            A list of PCIDevice objects representing discovered devices.
        """
        ...


@runtime_checkable
class ConfigManager(Protocol):
    """Protocol for components that manage configuration."""

    async def load_config(self, config_path: str) -> Dict[str, Any]:
        """
        Load a configuration from the given path.

        Args:
            config_path: Path to the configuration file.

        Returns:
            A dictionary containing the configuration data.
        """
        ...

    async def save_config(self, config_path: str, config_data: Dict[str, Any]) -> bool:
        """
        Save a configuration to the given path.

        Args:
            config_path: Path to the configuration file.
            config_data: Configuration data to save.

        Returns:
            True if the save was successful, False otherwise.
        """
        ...


@runtime_checkable
class BuildOrchestrator(Protocol):
    """Protocol for components that orchestrate the build process."""

    async def start_build(self, config: Dict[str, Any]) -> bool:
        """
        Start a build with the given configuration.

        Args:
            config: Build configuration.

        Returns:
            True if the build was started successfully, False otherwise.
        """
        ...

    async def get_build_status(self) -> Dict[str, Any]:
        """
        Get the current build status.

        Returns:
            A dictionary containing build status information.
        """
        ...

    async def cancel_build(self) -> bool:
        """
        Cancel the current build.

        Returns:
            True if the build was cancelled successfully, False otherwise.
        """
        ...


@runtime_checkable
class DeviceManager(Protocol):
    """Protocol for components that manage PCI devices."""

    async def get_device_info(self, device_id: str) -> Optional[Dict[str, Any]]:
        """
        Get information about a specific device.

        Args:
            device_id: ID of the device.

        Returns:
            A dictionary containing device information, or None if the device is not found.
        """
        ...

    async def select_device(self, device_id: str) -> bool:
        """
        Select a device for further operations.

        Args:
            device_id: ID of the device to select.

        Returns:
            True if the device was selected successfully, False otherwise.
        """
        ...


@runtime_checkable
class NotificationManager(Protocol):
    """Protocol for components that manage notifications."""

    def notify(self, message: str, severity: str = "info") -> None:
        """
        Show a notification to the user.

        Args:
            message: Notification message.
            severity: Severity level of the notification ("info", "warning", "error").
        """
        ...


@runtime_checkable
class UICoordinator(Protocol):
    """Protocol for components that coordinate the UI."""

    async def refresh(self) -> None:
        """Refresh the UI."""
        ...

    async def show_screen(self, screen_name: str, **kwargs) -> None:
        """
        Show a specific screen.

        Args:
            screen_name: Name of the screen to show.
            **kwargs: Additional arguments to pass to the screen.
        """
        _ = screen_name
        _ = kwargs
        ...

    def exit(self) -> None:
        """Exit the application."""
        ...
