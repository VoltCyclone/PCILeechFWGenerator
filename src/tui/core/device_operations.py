"""
Device operations with integrated graceful degradation.

This module implements device operations with graceful degradation to ensure
the application can continue functioning even if specific operations fail.
"""

import logging
from typing import Any, Dict, List, Optional, Tuple

from pcileechfwgenerator.tui.core.protocols import DeviceManager, DeviceScanner
from pcileechfwgenerator.tui.models.device import PCIDevice
from pcileechfwgenerator.tui.utils.graceful_degradation import GracefulDegradation

# Set up logging
logger = logging.getLogger(__name__)


class DeviceOperations:
    """Handles device operations with graceful degradation."""

    def __init__(
        self,
        device_manager: DeviceManager,
        device_scanner: DeviceScanner,
        notify_callback,
    ):
        """
        Initialize the DeviceOperations.

        Args:
            device_manager: The device manager to use.
            device_scanner: The device scanner to use.
            notify_callback: Callback function for notifications.
        """
        self.device_manager = device_manager
        self.device_scanner = device_scanner
        self.notify = notify_callback

        # Initialize graceful degradation
        self.graceful = GracefulDegradation(self)

        # Track previously seen devices to detect changes
        self._previous_devices: List[PCIDevice] = []

    async def scan_devices(self) -> List[PCIDevice]:
        """
        Scan for available devices with graceful degradation.

        Returns:
            A list of discovered PCIDevice objects, or an empty list if scanning fails.
        """
        return (
            await self.graceful.try_feature("device_scanning", self._scan_devices) or []
        )

    async def _scan_devices(self) -> List[PCIDevice]:
        """
        Internal implementation of device scanning.

        Returns:
            A list of discovered PCIDevice objects.
        """
        try:
            # Scan for devices
            devices = await self.device_scanner.scan_devices()

            # Detect changes in device list
            new_devices = [
                d
                for d in devices
                if not any(p.bdf == d.bdf for p in self._previous_devices)
            ]
            removed_devices = [
                p
                for p in self._previous_devices
                if not any(d.bdf == p.bdf for d in devices)
            ]

            # Update previous device list
            self._previous_devices = devices.copy()

            # Notify about changes
            if new_devices:
                self.notify(f"Found {len(new_devices)} new device(s)", severity="info")

            if removed_devices:
                self.notify(
                    f"{len(removed_devices)} device(s) no longer available",
                    severity="warning",
                )

            return devices
        except Exception as e:
            logger.exception("Failed to scan devices")
            raise

    async def get_device_details(self, device_id: str) -> Optional[Dict[str, Any]]:
        """
        Get detailed information about a device with graceful degradation.

        Args:
            device_id: ID of the device to get information for.

        Returns:
            A dictionary of device information, or None if the operation fails.
        """
        return await self.graceful.try_feature(
            "device_details", self._get_device_details, device_id
        )

    async def _get_device_details(self, device_id: str) -> Dict[str, Any]:
        """
        Internal implementation of get_device_details.

        Args:
            device_id: ID of the device to get information for.

        Returns:
            A dictionary of device information.

        Raises:
            ValueError: If the device is not found.
        """
        # Get device info
        device_info = await self.device_manager.get_device_info(device_id)

        if not device_info:
            raise ValueError(f"Device {device_id} not found")

        return device_info

    async def select_device(self, device_id: str) -> bool:
        """
        Select a device with graceful degradation.

        Args:
            device_id: ID of the device to select.

        Returns:
            True if the device was selected successfully, False otherwise.
        """
        return (
            await self.graceful.try_feature(
                "device_selection", self._select_device, device_id
            )
            or False
        )

    async def _select_device(self, device_id: str) -> bool:
        """
        Internal implementation of select_device.

        Args:
            device_id: ID of the device to select.

        Returns:
            True if the device was selected successfully, False otherwise.
        """
        success = await self.device_manager.select_device(device_id)

        if success:
            self.notify(f"Selected device {device_id}", severity="info")
        else:
            self.notify(f"Failed to select device {device_id}", severity="error")

        return success

    async def check_device_compatibility(
        self, device: PCIDevice
    ) -> Tuple[bool, List[str]]:
        """
        Check if a device is compatible with the application with graceful degradation.

        Args:
            device: The device to check.

        Returns:
            A tuple of (is_compatible, issues) where is_compatible is a boolean
            indicating if the device is compatible, and issues is a list of
            compatibility issues.
        """
        result = await self.graceful.try_feature(
            "compatibility_check", self._check_device_compatibility, device
        )

        if result is None:
            # Return a safe default if the feature fails
            return False, ["Compatibility check failed"]

        return result

    async def _check_device_compatibility(
        self, device: PCIDevice
    ) -> Tuple[bool, List[str]]:
        """
        Internal implementation of check_device_compatibility.

        Args:
            device: The device to check.

        Returns:
            A tuple of (is_compatible, issues) where is_compatible is a boolean
            indicating if the device is compatible, and issues is a list of
            compatibility issues.
        """
        # Check if device is suitable for firmware generation
        is_compatible = device.is_suitable
        issues = list(device.compatibility_issues) if not is_compatible else []

        # Additional checks that could fail
        if not device.has_driver and not device.is_detached:
            issues.append("Device does not have a driver and is not detached")

        if not device.vfio_compatible:
            issues.append("Device is not compatible with VFIO")

        if not device.iommu_enabled:
            issues.append("IOMMU is not enabled for this device")

        return is_compatible, issues
