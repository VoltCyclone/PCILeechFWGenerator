"""
Device Manager

Manages PCIe device discovery, validation, and enhanced information gathering.
"""

import asyncio
import os
import re
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

from ...cli.cli import list_pci_devices
from ...cli.vfio import get_current_driver
from ...cli.vfio_helpers import check_iommu_group_binding, check_vfio_prerequisites
from ...error_utils import format_concise_error, log_error_with_root_cause
from ...log_config import get_logger
from ..models.device import PCIDevice

# Constants for device assessment
DEVICE_CLASS_SCORES = {
    "02": 0.1,  # Network controller
    "01": 0.05,  # Storage controller
    "03": -0.1,  # Display controller
}

# Constants for BAR flags
BAR_FLAG_IO = 0x1
BAR_TYPE_MEMORY = "memory"
BAR_TYPE_IO = "io"

# Constants for driver status
VFIO_DRIVERS = ["vfio-pci", "pci-stub"]

# Constants for system paths
SYSFS_PCI_DEVICES_PATH = "/sys/bus/pci/devices"
SYSFS_IOMMU_GROUPS_PATH = "/sys/kernel/iommu_groups"

# Configure logger
logger = get_logger(__name__)


class DeviceManager:
    """Manages PCIe device discovery and validation."""

    def __init__(self):
        self._device_cache: List[PCIDevice] = []

    async def scan_devices(self) -> List[PCIDevice]:
        """Enhanced device scanning with detailed information."""
        try:
            # Get raw device list from existing CLI functionality
            raw_devices = await self._get_raw_devices()

            # Enhance each device with additional information
            enhanced_devices = []
            for raw_device in raw_devices:
                try:
                    enhanced = await self._enhance_device_info(raw_device)
                    enhanced_devices.append(enhanced)
                except Exception as e:
                    # Log error but continue with other devices
                    logger.warning(
                        format_concise_error(
                            f"Failed to enhance device {raw_device.get('bdf', 'unknown')}",
                            e,
                        )
                    )
                    continue

            self._device_cache = enhanced_devices
            return enhanced_devices

        except Exception as e:
            log_error_with_root_cause(
                logger, "Failed to scan PCIe devices", e, show_full_traceback=True
            )
            raise RuntimeError(f"Failed to scan PCIe devices: {e}")

    async def _get_raw_devices(self) -> List[Dict[str, str]]:
        """Get raw device list using existing CLI functionality."""
        # Run in executor to avoid blocking
        loop = asyncio.get_event_loop()
        return await loop.run_in_executor(None, list_pci_devices)

    async def _enhance_device_info(self, raw_device: Dict[str, str]) -> PCIDevice:
        """Enhance raw device information with additional details."""
        bdf = raw_device["bdf"]
        vendor_id = raw_device["ven"]
        device_id = raw_device["dev"]
        device_class = raw_device["class"]

        # Use vendor ID directly since we don't have a vendor database
        vendor_name = f"Vendor {vendor_id}"

        # Extract device name from pretty string
        device_name = self._extract_device_name(raw_device["pretty"])

        # Get additional device information in parallel
        driver, iommu_group, power_state, link_speed, bars = await asyncio.gather(
            self._get_device_driver(bdf),
            self._get_iommu_group(bdf),
            self._get_power_state(bdf),
            self._get_link_speed(bdf),
            self._get_device_bars(bdf),
        )

        # Enhanced compatibility checks in parallel
        is_valid, (has_driver, is_detached), vfio_compatible, iommu_enabled = (
            await asyncio.gather(
                self._check_device_validity(bdf),
                self._check_driver_status(bdf, driver),
                self._check_vfio_compatibility(bdf),
                self._check_iommu_status(bdf, iommu_group),
            )
        )

        # Create detailed status information
        detailed_status = {
            "device_accessible": is_valid,
            "driver_bound": has_driver,
            "driver_detached": is_detached,
            "vfio_ready": vfio_compatible,
            "iommu_configured": iommu_enabled,
            "power_management": power_state == "D0",
            "link_active": link_speed != "unknown",
            "bars_available": len(bars) > 0,
        }

        # Calculate suitability score and compatibility issues
        suitability_score, compatibility_issues, compatibility_factors = (
            self._assess_device_suitability(
                device_class, driver, bars, is_valid, vfio_compatible, iommu_enabled
            )
        )

        return PCIDevice(
            bdf=bdf,
            vendor_id=vendor_id,
            device_id=device_id,
            vendor_name=vendor_name,
            device_name=device_name,
            device_class=device_class,
            subsystem_vendor="",  # Could be enhanced further
            subsystem_device="",  # Could be enhanced further
            driver=driver,
            iommu_group=iommu_group,
            power_state=power_state,
            link_speed=link_speed,
            bars=bars,
            suitability_score=suitability_score,
            compatibility_issues=compatibility_issues,
            compatibility_factors=compatibility_factors,
            is_valid=is_valid,
            has_driver=has_driver,
            is_detached=is_detached,
            vfio_compatible=vfio_compatible,
            iommu_enabled=iommu_enabled,
            detailed_status=detailed_status,
        )

    def _extract_device_name(self, pretty_string: str) -> str:
        """Extract device name from lspci pretty string."""
        # Remove BDF and vendor/device IDs to get clean device name
        # Example: "0000:03:00.0 Ethernet controller [0200]: Intel Corporation 82574L Gigabit Network Connection [8086:10d3]"
        match = re.search(r"]: (.+?) \[[\da-fA-F]{4}:[\da-fA-F]{4}\]", pretty_string)
        if match:
            return match.group(1).strip()

        # Fallback: extract everything after the class description
        match = re.search(r"]: (.+)", pretty_string)
        if match:
            return match.group(1).strip()

        return "Unknown Device"

    async def _get_device_driver(self, bdf: str) -> Optional[str]:
        """Get current driver for device using the VFIO module."""
        try:
            # Use the existing function from the VFIO module
            loop = asyncio.get_event_loop()
            return await loop.run_in_executor(None, get_current_driver, bdf)
        except Exception as e:
            logger.debug(f"Failed to get driver for device {bdf}: {e}")
            return None

    async def _get_iommu_group(self, bdf: str) -> str:
        """Get IOMMU group for device."""
        try:
            iommu_path = Path(f"{SYSFS_PCI_DEVICES_PATH}/{bdf}/iommu_group")
            if iommu_path.exists():
                group_num = iommu_path.resolve().name
                return group_num
            return "none"
        except Exception as e:
            logger.debug(f"Failed to get IOMMU group for device {bdf}: {e}")
            return "unknown"

    async def _get_power_state(self, bdf: str) -> str:
        """Get device power state."""
        try:
            power_path = f"{SYSFS_PCI_DEVICES_PATH}/{bdf}/power_state"
            if os.path.exists(power_path):
                with open(power_path, "r") as f:
                    return f.read().strip()
        except Exception as e:
            logger.debug(f"Failed to get power state for device {bdf}: {e}")
        return "unknown"

    async def _get_link_speed(self, bdf: str) -> str:
        """Get PCIe link speed."""
        try:
            # Try to get link speed from sysfs
            link_path = f"{SYSFS_PCI_DEVICES_PATH}/{bdf}/current_link_speed"
            if os.path.exists(link_path):
                with open(link_path, "r") as f:
                    return f.read().strip()
        except Exception as e:
            logger.debug(f"Failed to get link speed for device {bdf}: {e}")
        return "unknown"

    async def _get_device_bars(self, bdf: str) -> List[Dict[str, Any]]:
        """Get device BAR information."""
        bars = []
        try:
            # Read BAR information from main resource file
            resource_path = f"{SYSFS_PCI_DEVICES_PATH}/{bdf}/resource"
            if os.path.exists(resource_path):
                with open(resource_path, "r") as f:
                    lines = f.readlines()

                for i, line in enumerate(lines[:6]):  # Only first 6 BARs
                    line = line.strip()
                    if (
                        line
                        and line
                        != "0x0000000000000000 0x0000000000000000 0x0000000000000000"
                    ):
                        parts = line.split()
                        if len(parts) >= 3:
                            start = int(parts[0], 16)
                            end = int(parts[1], 16)
                            flags = int(parts[2], 16)

                            # Skip empty/unused BARs
                            if start != 0 or end != 0:
                                size = end - start + 1 if end > start else 0
                                bars.append(
                                    {
                                        "index": i,
                                        "start": start,
                                        "end": end,
                                        "size": size,
                                        "flags": flags,
                                        "type": (
                                            BAR_TYPE_MEMORY
                                            if flags & BAR_FLAG_IO == 0
                                            else BAR_TYPE_IO
                                        ),
                                    }
                                )
        except Exception as e:
            logger.debug(f"Failed to get BARs for device {bdf}: {e}")
        return bars

    async def _check_device_validity(self, bdf: str) -> bool:
        """Check if device is properly detected and accessible."""
        try:
            # Check if device exists in sysfs
            device_path = f"{SYSFS_PCI_DEVICES_PATH}/{bdf}"
            if not os.path.exists(device_path):
                return False

            # Check if we can read basic device information
            vendor_path = f"{device_path}/vendor"
            device_id_path = f"{device_path}/device"

            if not (os.path.exists(vendor_path) and os.path.exists(device_id_path)):
                return False

            # Try to read the files to ensure they're accessible
            try:
                with open(vendor_path, "r") as f:
                    f.read().strip()
                with open(device_id_path, "r") as f:
                    f.read().strip()
            except (OSError, IOError):
                return False

            return True
        except Exception as e:
            logger.debug(f"Device validity check failed for {bdf}: {e}")
            return False

    async def _check_driver_status(
        self, bdf: str, driver: Optional[str]
    ) -> Tuple[bool, bool]:
        """Check driver binding and detachment status."""
        try:
            has_driver = driver is not None and driver != ""
            is_detached = False

            if has_driver:
                # Check if device is detached (bound to vfio-pci or similar)
                is_detached = driver in VFIO_DRIVERS

                # Also check if driver directory exists but device is not
                # actively using it
                driver_path = f"{SYSFS_PCI_DEVICES_PATH}/{bdf}/driver"
                if os.path.islink(driver_path):
                    # Driver is bound
                    pass
                else:
                    # Driver name exists but not actually bound
                    has_driver = False

            return has_driver, is_detached
        except Exception as e:
            logger.debug(f"Driver status check failed for {bdf}: {e}")
            return False, False

    async def _check_vfio_compatibility(self, bdf: str) -> bool:
        """Check VFIO compatibility using the VFIO module."""
        try:
            # Check if VFIO modules are available
            vfio_modules = ["/sys/module/vfio", "/sys/module/vfio_pci"]
            vfio_available = any(os.path.exists(module) for module in vfio_modules)

            if not vfio_available:
                return False

            # Use the VFIO module's check_vfio_prerequisites function
            loop = asyncio.get_event_loop()
            try:
                await loop.run_in_executor(None, check_vfio_prerequisites)
            except Exception:
                return False

            # Check if device can be bound to VFIO
            device_path = f"{SYSFS_PCI_DEVICES_PATH}/{bdf}"
            if not os.path.exists(device_path):
                return False

            # Check if device class is generally VFIO-compatible
            try:
                class_path = f"{device_path}/class"
                if os.path.exists(class_path):
                    with open(class_path, "r") as f:
                        device_class = f.read().strip()

                    # Exclude some system-critical device classes
                    excluded_classes = [
                        "0x060000",  # Host bridge
                        "0x060100",  # ISA bridge
                        "0x060400",  # PCI bridge
                    ]

                    if device_class in excluded_classes:
                        return False
            except Exception:
                pass

            return True
        except Exception as e:
            logger.debug(f"VFIO compatibility check failed for {bdf}: {e}")
            return False

    async def _check_iommu_status(self, bdf: str, iommu_group: str) -> bool:
        """Check IOMMU configuration status."""
        try:
            # Check if IOMMU is enabled in the system
            if not os.path.exists(SYSFS_IOMMU_GROUPS_PATH):
                return False

            # Check if device has a valid IOMMU group
            if iommu_group in ["unknown", "none", ""]:
                return False

            # Check if the IOMMU group directory exists
            group_path = f"{SYSFS_IOMMU_GROUPS_PATH}/{iommu_group}"
            if not os.path.exists(group_path):
                return False

            # Check if device is in the IOMMU group
            devices_path = f"{group_path}/devices"
            if os.path.exists(devices_path):
                devices = os.listdir(devices_path)
                if bdf not in devices:
                    return False

            # Try to use the VFIO module's check_iommu_group_binding function
            loop = asyncio.get_event_loop()
            try:
                await loop.run_in_executor(None, check_iommu_group_binding, iommu_group)
                return True
            except Exception:
                # If the check fails, it might be because not all devices in the group
                # are bound to vfio-pci, which is fine for our compatibility check
                pass

            return True
        except Exception as e:
            logger.debug(f"IOMMU status check failed for {bdf}: {e}")
            return False

    def _assess_device_suitability(
        self,
        device_class: str,
        driver: Optional[str],
        bars: List[Dict[str, Any]],
        is_valid: bool = True,
        vfio_compatible: bool = False,
        iommu_enabled: bool = False,
    ) -> Tuple[float, List[str], List[Dict[str, Any]]]:
        """
        Assess device suitability for firmware generation with clear explanations.

        This method evaluates key device features and provides detailed explanations
        about each feature's status and importance for PCILeech firmware generation.
        """
        issues = []
        factors = []

        # Start with a binary suitability assessment
        # A device is considered suitable if it meets all critical requirements
        is_suitable = True

        # Device accessibility check
        if is_valid:
            factors.append(
                {
                    "name": "Device Status",
                    "adjustment": 0.0,
                    "description": "Device is accessible in the system",
                    "is_positive": True,
                }
            )
        else:
            is_suitable = False
            issues.append("Device is not properly accessible in the system")
            factors.append(
                {
                    "name": "Device Status",
                    "adjustment": 0.0,
                    "description": "Device cannot be accessed properly - check permissions and device presence",
                    "is_positive": False,
                }
            )

        # VFIO compatibility check
        if vfio_compatible:
            factors.append(
                {
                    "name": "VFIO Support",
                    "adjustment": 0.0,
                    "description": "Device supports VFIO passthrough which is required for PCILeech",
                    "is_positive": True,
                }
            )
        else:
            is_suitable = False
            issues.append("Device lacks VFIO passthrough support")
            factors.append(
                {
                    "name": "VFIO Support",
                    "adjustment": 0.0,
                    "description": "Device does not support VFIO passthrough - this is required for PCILeech",
                    "is_positive": False,
                }
            )

        # IOMMU status check
        if iommu_enabled:
            factors.append(
                {
                    "name": "IOMMU Status",
                    "adjustment": 0.0,
                    "description": "IOMMU is properly configured for this device",
                    "is_positive": True,
                }
            )
        else:
            is_suitable = False
            issues.append("IOMMU is not properly configured")
            factors.append(
                {
                    "name": "IOMMU Status",
                    "adjustment": 0.0,
                    "description": "IOMMU is not properly configured - required for device isolation",
                    "is_positive": False,
                }
            )

        # Device class information
        class_prefix = device_class[:2]
        class_names = {
            "01": "Storage controller",
            "02": "Network controller",
            "03": "Display controller",
        }
        class_name = class_names.get(class_prefix, f"Class {class_prefix}")

        # Add device class information
        if class_prefix == "02":  # Network controller
            factors.append(
                {
                    "name": "Device Type",
                    "adjustment": 0.0,
                    "description": f"{class_name}s typically work well with PCILeech",
                    "is_positive": True,
                }
            )
        elif class_prefix == "01":  # Storage controller
            factors.append(
                {
                    "name": "Device Type",
                    "adjustment": 0.0,
                    "description": f"{class_name}s are generally compatible with PCILeech",
                    "is_positive": True,
                }
            )
        elif class_prefix == "03":  # Display controller
            issues.append(f"{class_name}s may have driver conflicts")
            factors.append(
                {
                    "name": "Device Type",
                    "adjustment": 0.0,
                    "description": f"{class_name}s may have driver conflicts with PCILeech",
                    "is_positive": False,
                }
            )
        else:
            factors.append(
                {
                    "name": "Device Type",
                    "adjustment": 0.0,
                    "description": f"{class_name} - compatibility varies",
                    "is_positive": True,
                }
            )

        # Driver status check
        if driver:
            if driver in VFIO_DRIVERS:
                factors.append(
                    {
                        "name": "Driver Status",
                        "adjustment": 0.0,
                        "description": f"Device is properly bound to {driver} for VFIO use",
                        "is_positive": True,
                    }
                )
            else:
                issues.append(f"Device is bound to {driver} driver")
                factors.append(
                    {
                        "name": "Driver Status",
                        "adjustment": 0.0,
                        "description": f"Device is bound to {driver} driver - needs to be unbound or bound to vfio-pci",
                        "is_positive": False,
                    }
                )
                is_suitable = False
        else:
            factors.append(
                {
                    "name": "Driver Status",
                    "adjustment": 0.0,
                    "description": "Device has no driver bound - ready for VFIO binding",
                    "is_positive": True,
                }
            )

        # BAR configuration check
        if not bars:
            issues.append("No memory BARs detected")
            factors.append(
                {
                    "name": "BAR Configuration",
                    "adjustment": 0.0,
                    "description": "No memory BARs detected - required for PCILeech operation",
                    "is_positive": False,
                }
            )
            is_suitable = False
        elif len(bars) < 2:
            issues.append("Limited BAR configuration")
            factors.append(
                {
                    "name": "BAR Configuration",
                    "adjustment": 0.0,
                    "description": f"Limited BAR configuration ({len(bars)} found) - may restrict functionality",
                    "is_positive": False,
                }
            )
        else:
            factors.append(
                {
                    "name": "BAR Configuration",
                    "adjustment": 0.0,
                    "description": f"Device has {len(bars)} BARs - sufficient for operation",
                    "is_positive": True,
                }
            )

        # Calculate final score based on suitability
        # This maintains compatibility with the rest of the code
        final_score = 1.0 if is_suitable else 0.5

        return final_score, issues, factors

    def get_cached_devices(self) -> List[PCIDevice]:
        """Get cached device list."""
        return self._device_cache.copy()

    async def refresh_devices(self) -> List[PCIDevice]:
        """Refresh device list."""
        return await self.scan_devices()

    def find_device_by_bdf(self, bdf: str) -> Optional[PCIDevice]:
        """Find device by BDF address."""
        for device in self._device_cache:
            if device.bdf == bdf:
                return device
        return None

    def get_suitable_devices(self) -> List[PCIDevice]:
        """Get list of devices suitable for firmware generation."""
        return [device for device in self._device_cache if device.is_suitable]
