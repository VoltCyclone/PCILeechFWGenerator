#!/usr/bin/env python3
"""VFIO Handler Module
This module provides robust VFIO device binding with improved error handling,
performance optimizations, and better maintainability.
"""

from __future__ import annotations

import ctypes
import errno
import fcntl
import json
import logging
import os
import re
import struct
import time
from contextlib import contextmanager
from dataclasses import dataclass
from enum import Enum
from pathlib import Path
from typing import Any, Dict, Generator, Optional, Tuple, Union

# Import VFIO exceptions
from src.exceptions import (VFIOBindError, VFIODeviceNotFoundError,
                            VFIOGroupError, VFIOPermissionError)

# Import the privilege manager - make it optional to avoid circular imports
try:
    from src.tui.utils.privilege_manager import PrivilegeManager

    HAS_PRIVILEGE_MANAGER = True
except ImportError:
    PrivilegeManager = None
    HAS_PRIVILEGE_MANAGER = False

# Import VFIO diagnostics - make it optional to avoid circular imports
try:
    from .vfio_diagnostics import Diagnostics

    HAS_VFIO_ASSIST = True
except ImportError:
    Diagnostics = None
    HAS_VFIO_ASSIST = False

# Import safe logging functions
from ..string_utils import (log_debug_safe, log_error_safe, log_info_safe,
                            log_warning_safe, safe_format)
# Import proper VFIO constants with kernel-compatible ioctl generation
from .vfio_constants import VfioGroupStatus  # legacy alias
from .vfio_constants import VfioRegionInfo  # legacy alias
from .vfio_constants import (VFIO_DEVICE_GET_REGION_INFO,
                             VFIO_GROUP_GET_DEVICE_FD, VFIO_GROUP_GET_STATUS,
                             VFIO_GROUP_SET_CONTAINER,
                             VFIO_REGION_INFO_FLAG_MMAP,
                             VFIO_REGION_INFO_FLAG_READ,
                             VFIO_REGION_INFO_FLAG_WRITE, VFIO_SET_IOMMU,
                             VFIO_TYPE1_IOMMU, vfio_group_status,
                             vfio_region_info)
from .vfio_helpers import get_device_fd

# Configure global logger
logger = logging.getLogger(__name__)

# Constants
VFIO_REGION_INFO_STRUCT_FORMAT = (
    "I I I I Q Q"  # argsz, flags, index, cap_offset, size, offset
)
VFIO_REGION_INFO_STRUCT_SIZE = struct.calcsize(VFIO_REGION_INFO_STRUCT_FORMAT)
VFIO_DRIVER_NAME = "vfio-pci"
VFIO_CONTAINER_PATH = "/dev/vfio/vfio"

# Timing constants
DEFAULT_BIND_WAIT_TIME = 0.5
DEFAULT_UNBIND_WAIT_TIME = 0.2
MAX_GROUP_WAIT_TIME = 10.0
INITIAL_BACKOFF_DELAY = 0.1
MAX_BACKOFF_DELAY = 3.2


class BindingState(Enum):
    """Enumeration of device binding states."""

    UNBOUND = "unbound"
    BOUND_TO_VFIO = "bound_to_vfio"
    BOUND_TO_OTHER = "bound_to_other"
    UNKNOWN = "unknown"


@dataclass(frozen=True)
class DeviceInfo:
    """Immutable device information container."""

    bdf: str
    current_driver: Optional[str]
    iommu_group: Optional[str]
    binding_state: BindingState

    @classmethod
    def from_bdf(cls, bdf: str) -> DeviceInfo:
        """Create DeviceInfo by querying system for the given BDF."""
        current_driver = _get_current_driver(bdf)
        iommu_group = _get_iommu_group_safe(bdf)

        if current_driver == VFIO_DRIVER_NAME:
            binding_state = BindingState.BOUND_TO_VFIO
        elif current_driver:
            binding_state = BindingState.BOUND_TO_OTHER
        else:
            binding_state = BindingState.UNBOUND

        return cls(
            bdf=bdf,
            current_driver=current_driver,
            iommu_group=iommu_group,
            binding_state=binding_state,
        )


class VFIOPathManager:
    """Manages VFIO-related system paths with caching."""

    def __init__(self, bdf: str):
        self.bdf = bdf
        self._device_path = Path(f"/sys/bus/pci/devices/{bdf}")
        self._driver_link = self._device_path / "driver"
        self._driver_override_path = self._device_path / "driver_override"
        self._iommu_group_link = self._device_path / "iommu_group"

    @property
    def device_path(self) -> Path:
        """Get the device sysfs path."""
        return self._device_path

    @property
    def driver_link(self) -> Path:
        """Get the driver symlink path."""
        return self._driver_link

    @property
    def driver_override_path(self) -> Path:
        """Get the driver override path."""
        return self._driver_override_path

    @property
    def iommu_group_link(self) -> Path:
        """Get the IOMMU group symlink path."""
        return self._iommu_group_link

    def get_driver_unbind_path(self, driver_name: str) -> Path:
        """Get the unbind path for a specific driver."""
        return Path(f"/sys/bus/pci/drivers/{driver_name}/unbind")

    def get_driver_bind_path(self, driver_name: str) -> Path:
        """Get the bind path for a specific driver."""
        return Path(f"/sys/bus/pci/drivers/{driver_name}/bind")

    def get_vfio_group_path(self, group_id: str) -> Path:
        """Get the VFIO group device path."""
        return Path(f"/dev/vfio/{group_id}")


class VFIOBinderImpl:
    """Context manager for VFIO device binding with strict error handling."""

    # BDF validation regex: 2-4 hex digits for domain (allowing short format like 00:01:00.0)
    BDF_PATTERN = re.compile(
        r"^([0-9A-Fa-f]{2,4}:)?[0-9A-Fa-f]{2}:[0-9A-Fa-f]{2}\.[0-7]$"
    )

    def __init__(self, bdf: str, *, attach: bool = True) -> None:
        """Initialize VFIO binder for the specified BDF.

        Args:
            bdf: PCI Bus:Device.Function identifier
            attach: Whether to attach the group (open device and set IOMMU)

        Raises:
            ValueError: If BDF format is invalid
            VFIOPermissionError: If not running as root
        """
        self._validate_permissions()
        self._validate_bdf(bdf)

        self.bdf = bdf
        self.original_driver: Optional[str] = None
        self.group_id: Optional[str] = None
        self._bound = False
        self._attach = attach
        self._path_manager = VFIOPathManager(bdf)
        self._device_info: Optional[DeviceInfo] = None
        self._privilege_manager = None

        # Initialize privilege manager if available
        if HAS_PRIVILEGE_MANAGER and PrivilegeManager is not None:
            self._privilege_manager = PrivilegeManager()

    @staticmethod
    def _validate_permissions() -> None:
        """Validate that we have root privileges."""
        if os.geteuid() != 0:
            # Enforce privilege requirement at initialization time
            log_warning_safe(
                logger,
                "Not running as root. Some VFIO operations may require elevated privileges.",
                prefix="PERM",
            )
            raise VFIOPermissionError("VFIO operations require root privileges")

    def _validate_bdf(self, bdf: str) -> None:
        """Validate BDF format."""
        if not self.BDF_PATTERN.match(bdf):
            raise ValueError(f"Invalid BDF format: {bdf}")

    def _get_device_info(self, refresh: bool = False) -> DeviceInfo:
        """Get current device information with optional refresh."""
        if self._device_info is None or refresh:
            self._device_info = DeviceInfo.from_bdf(self.bdf)
        return self._device_info

    def _write_sysfs_safe(self, path: Path, value: str) -> None:
        """Write to sysfs file with comprehensive error handling."""
        try:
            if not path.exists():
                raise VFIOBindError(f"Sysfs path does not exist: {path}")
            path.write_text(value)
            log_debug_safe(
                logger,
                safe_format(
                    "Successfully wrote '{value}' to {path}",
                    value=value,
                    path=path,
                ),
                prefix="BIND",
            )
        except PermissionError as e:
            raise VFIOPermissionError(
                safe_format(
                    "Permission denied writing to {path}: {error}",
                    path=path,
                    error=e,
                )
            ) from e
        except OSError as e:
            raise VFIOBindError(
                safe_format(
                    "Failed to write '{value}' to {path}: {error}",
                    value=value,
                    path=path,
                    error=e,
                )
            ) from e

    def _wait_for_state_change(
        self, expected_driver: Optional[str], timeout: float = 2.0
    ) -> bool:
        """Wait for driver state change with timeout."""
        start_time = time.time()
        while time.time() - start_time < timeout:
            current_driver = _get_current_driver(self.bdf)
            if current_driver == expected_driver:
                return True
            time.sleep(0.1)
        return False

    def _unbind_current_driver(self, device_info: DeviceInfo) -> None:
        """Unbind device from current driver if present."""
        if not device_info.current_driver:
            return

        log_info_safe(
            logger,
            safe_format(
                "Unbinding device from current driver {current_driver}",
                current_driver=device_info.current_driver,
            ),
            prefix="BIND",
        )

        try:
            unbind_path = self._path_manager.get_driver_unbind_path(
                device_info.current_driver
            )
            if unbind_path.exists():
                self._write_sysfs_safe(unbind_path, self.bdf)

                # Wait for unbinding to complete
                if self._wait_for_state_change(None, timeout=2.0):
                    log_debug_safe(
                        logger,
                        safe_format(
                            "Successfully unbound from {current_driver}",
                            current_driver=device_info.current_driver,
                        ),
                        prefix="BIND",
                    )
                else:
                    log_warning_safe(
                        logger,
                        safe_format(
                            "Unbinding from {current_driver} may not have completed",
                            current_driver=device_info.current_driver,
                        ),
                        prefix="BIND",
                    )

                time.sleep(DEFAULT_UNBIND_WAIT_TIME)
        except Exception as e:
            log_warning_safe(
                logger,
                safe_format(
                    "Failed to unbind from {current_driver}: {error}",
                    current_driver=device_info.current_driver,
                    error=str(e),
                ),
                prefix="BIND",
            )

    def _perform_vfio_binding(self) -> None:
        """Bind device to vfio-pci using driver_override workflow."""
        log_info_safe(
            logger,
            safe_format("Binding {bdf} to vfio-pci driver", bdf=self.bdf),
            prefix="BIND",
        )

        # Wait for driver_override to be writable
        self._wait_for_override_writable(timeout=5.0)

        # Set driver override
        self._write_sysfs_safe(
            self._path_manager.driver_override_path, VFIO_DRIVER_NAME
        )

        # Bind to vfio-pci
        vfio_bind_path = self._path_manager.get_driver_bind_path(VFIO_DRIVER_NAME)
        self._write_sysfs_safe(vfio_bind_path, self.bdf)

        # Wait for binding to complete
        if self._wait_for_state_change(VFIO_DRIVER_NAME, timeout=3.0):
            log_debug_safe(
                logger, safe_format("Successfully bound to vfio-pci"), prefix="BIND"
            )
        else:
            raise VFIOBindError(
                safe_format("Binding to vfio-pci timed out for {bdf}", bdf=self.bdf)
            )

        time.sleep(DEFAULT_BIND_WAIT_TIME)

    def _bind_to_vfio(self) -> None:
        """Bind device to vfio-pci using driver_override workflow."""
        log_info_safe(
            logger,
            safe_format("Starting VFIO binding process for device {bdf}", bdf=self.bdf),
            prefix="BIND",
        )

        # Check privileges and attempt to elevate if needed
        self._ensure_privileges()

        # Get current device state
        device_info = self._get_device_info(refresh=True)

        log_info_safe(
            logger,
            safe_format(
                "Current driver for {bdf}: {current_driver}",
                bdf=self.bdf,
                current_driver=device_info.current_driver or "none",
            ),
            prefix="BIND",
        )

        # Check if already bound to vfio-pci
        if device_info.binding_state == BindingState.BOUND_TO_VFIO:
            log_info_safe(
                logger,
                safe_format("Device {bdf} already bound to vfio-pci", bdf=self.bdf),
                prefix="BIND",
            )
            self._verify_vfio_binding()
            return

        # Store original driver for cleanup
        self.original_driver = device_info.current_driver
        if self.original_driver:
            log_info_safe(
                logger,
                safe_format(
                    "Stored original driver '{original_driver}' for restoration",
                    original_driver=self.original_driver,
                ),
                prefix="BIND",
            )

        # Unbind from current driver if present
        self._unbind_current_driver(device_info)

        # Bind to vfio-pci
        self._perform_vfio_binding()

        # Verify binding
        final_device_info = self._get_device_info(refresh=True)
        if final_device_info.binding_state != BindingState.BOUND_TO_VFIO:
            raise VFIOBindError(
                safe_format("Failed to bind {bdf} to vfio-pci.", bdf=self.bdf)
            )

        # Additional verification that VFIO binding is functional
        self._verify_vfio_binding()

        self._bound = True
        log_info_safe(
            logger,
            safe_format("Successfully bound {bdf} to vfio-pci", bdf=self.bdf),
            prefix="BIND",
        )

    def _ensure_privileges(self) -> None:
        """
        Ensure we have the necessary privileges for VFIO operations.

        This method will attempt to elevate privileges if needed and available.
        If privilege elevation fails, it will raise an exception.
        """
        # If we're already root, no need to elevate
        if os.geteuid() == 0:
            return

        # If privilege manager is available, try to elevate
        if self._privilege_manager:
            try:
                # Create a synchronous version of the async call using asyncio
                import asyncio

                loop = asyncio.new_event_loop()
                has_privileges = loop.run_until_complete(
                    self._privilege_manager.request_privileges("load_kernel_modules")
                )
                loop.close()

                if not has_privileges:
                    raise VFIOPermissionError(
                        "Failed to obtain required privileges for VFIO operations"
                    )

            except Exception as e:
                log_error_safe(
                    logger,
                    safe_format("Failed to elevate privileges: {error}", error=str(e)),
                    prefix="PERM",
                )
                raise VFIOPermissionError(
                    safe_format(
                        "Failed to elevate privileges for VFIO operations: {error}",
                        error=str(e),
                    )
                )
        else:
            # No privilege manager available, fall back to standard check
            if os.geteuid() != 0:
                raise VFIOPermissionError("VFIO operations require root privileges")

    def _verify_vfio_binding(self) -> None:
        """Verify that VFIO binding is functional."""
        log_debug_safe(
            logger,
            safe_format("Verifying VFIO binding functionality for {bdf}", bdf=self.bdf),
            prefix="BIND",
        )

        try:
            # Ensure we have group ID
            if not self.group_id:
                self.group_id = _get_iommu_group(self.bdf)

            group_path = self._path_manager.get_vfio_group_path(self.group_id)

            if not group_path.exists():
                raise VFIOGroupError(
                    safe_format(
                        "VFIO group device {group_path} does not exist",
                        group_path=group_path,
                    )
                )

            # Verify the group device is accessible
            if not os.access(group_path, os.R_OK | os.W_OK):
                raise VFIOPermissionError(
                    safe_format(
                        "VFIO group device {group_path} is not accessible",
                        group_path=group_path,
                    )
                )

            log_debug_safe(
                logger,
                safe_format(
                    "VFIO group device {group_path} exists and is accessible",
                    group_path=group_path,
                ),
                prefix="BIND",
            )

        except Exception as e:
            log_error_safe(
                logger,
                safe_format("VFIO binding verification failed: {error}", error=str(e)),
                prefix="BIND",
            )
            raise VFIOBindError(
                safe_format(
                    "VFIO binding verification failed for {bdf}: {error}",
                    bdf=self.bdf,
                    error=str(e),
                )
            ) from e

        log_debug_safe(
            logger,
            safe_format("VFIO binding verification successful for {bdf}", bdf=self.bdf),
            prefix="BIND",
        )

    def _wait_for_override_writable(self, timeout: float = 5.0) -> None:
        """Wait for the driver_override file to become writable."""
        start_time = time.time()
        override_path = self._path_manager.driver_override_path
        while time.time() - start_time < timeout:
            if override_path.exists() and os.access(override_path, os.W_OK):
                log_debug_safe(
                    logger,
                    "driver_override is writable",
                    prefix="BIND",
                )
                return
            time.sleep(0.1)
        log_warning_safe(
            logger,
            "Timed out waiting for driver_override to become writable",
            prefix="BIND",
        )

    def _wait_for_group_node(self) -> Path:
        """Wait for VFIO group node with exponential backoff."""
        if not self.group_id:
            raise VFIOGroupError("No group ID available for waiting")

        group_path = self._path_manager.get_vfio_group_path(self.group_id)

        delay = INITIAL_BACKOFF_DELAY
        total_time = 0.0

        while total_time < MAX_GROUP_WAIT_TIME:
            if group_path.exists():
                return group_path

            log_debug_safe(
                logger,
                "Waiting for {group_path} ({total_time:.1f}s elapsed)",
                group_path=group_path,
                total_time=total_time,
                prefix="BIND",
            )
            time.sleep(delay)
            total_time += delay
            delay = min(delay * 2, MAX_BACKOFF_DELAY)

        raise VFIOGroupError(
            safe_format(
                "VFIO group node {group_path} did not appear within {max_wait_time}s",
                group_path=group_path,
                max_wait_time=MAX_GROUP_WAIT_TIME,
            )
        )

    def _restore_original_driver(self) -> None:
        """Restore the original driver if it existed."""
        if not self.original_driver:
            return

        bind_path = self._path_manager.get_driver_bind_path(self.original_driver)
        if bind_path.exists():
            try:
                self._write_sysfs_safe(bind_path, self.bdf)
                log_debug_safe(
                    logger,
                    safe_format(
                        "Restored {bdf} to {original_driver}",
                        bdf=self.bdf,
                        original_driver=self.original_driver,
                    ),
                    prefix="BIND",
                )
            except Exception as e:
                log_warning_safe(
                    logger,
                    safe_format(
                        "Failed to restore original driver {original_driver}: {error}",
                        original_driver=self.original_driver,
                        error=str(e),
                    ),
                    prefix="BIND",
                )

    def _cleanup(self) -> None:
        """Clean up VFIO binding and restore original driver."""
        if not self._bound:
            return

        # Check if device still exists
        if not self._path_manager.device_path.exists():
            log_debug_safe(
                logger,
                safe_format(
                    "Device {bdf} no longer exists, skipping cleanup",
                    bdf=self.bdf,
                ),
                prefix="BIND",
            )
            return

        try:
            # Get current state
            device_info = self._get_device_info(refresh=True)

            # Only unbind if still bound to vfio-pci
            if device_info.binding_state == BindingState.BOUND_TO_VFIO:
                unbind_path = self._path_manager.get_driver_unbind_path(
                    VFIO_DRIVER_NAME
                )
                self._write_sysfs_safe(unbind_path, self.bdf)
                log_debug_safe(
                    logger,
                    safe_format("Unbound {bdf} from vfio-pci", bdf=self.bdf),
                    prefix="BIND",
                )

                # Wait for unbinding
                self._wait_for_state_change(None, timeout=2.0)

                # Restore original driver
                self._restore_original_driver()

            # Clear driver override
            if self._path_manager.driver_override_path.exists():
                self._write_sysfs_safe(self._path_manager.driver_override_path, "")
                log_debug_safe(
                    logger,
                    safe_format("Cleared driver_override for {bdf}", bdf=self.bdf),
                    prefix="BIND",
                )

        except Exception as e:
            # Only log warnings if device still exists
            if self._path_manager.device_path.exists():
                log_warning_safe(
                    logger,
                    safe_format(
                        "Cleanup failed for {bdf}: {error}",
                        bdf=self.bdf,
                        error=str(e),
                    ),
                    prefix="BIND",
                )
            else:
                log_debug_safe(
                    logger,
                    safe_format(
                        "Device {bdf} removed during cleanup",
                        bdf=self.bdf,
                    ),
                    prefix="BIND",
                )

    def _open_vfio_device_fd(self) -> Tuple[int, int]:
        """
        Return a (device_fd, container_fd) tuple.

        * Host side (attach=False) - **skip** and raise to prevent double-attach.
        * Container side (attach=True, the default) - run the normal workflow.
        """
        if not self._attach:
            raise RuntimeError(
                "Device-FD opening disabled in host context (attach=False)"
            )

        if not self.group_id:
            raise VFIOGroupError("No group ID available for opening device FD")

        container_fd = None
        group_fd = None

        try:
            # Open the generic container node
            container_fd = os.open(VFIO_CONTAINER_PATH, os.O_RDWR | os.O_CLOEXEC)

            # Open this device's VFIO-group node
            group_path = self._path_manager.get_vfio_group_path(self.group_id)
            group_fd = os.open(str(group_path), os.O_RDWR | os.O_CLOEXEC)

            # Tie the group to the container
            try:
                fcntl.ioctl(
                    group_fd, VFIO_GROUP_SET_CONTAINER, ctypes.c_int(container_fd)
                )
                log_debug_safe(
                    logger, "Successfully linked group to container", prefix="VFIO"
                )
            except OSError as e:
                log_error_safe(
                    logger,
                    safe_format(
                        "Failed to link group {group} to container: {e}",
                        group=self.group_id,
                        e=str(e),
                    ),
                    prefix="VFIO",
                )
                if e.errno == errno.EINVAL:
                    log_error_safe(
                        logger,
                        "EINVAL: Invalid argument - group may already be linked or container issue",
                        prefix="VFIO",
                    )
                elif e.errno == errno.EBUSY:
                    log_error_safe(
                        logger,
                        "EBUSY: Group is busy - may be in use by another container",
                        prefix="VFIO",
                    )
                raise OSError(f"Failed to link group {self.group_id} to container: {e}")

            # Enable the Type-1 IOMMU backend
            fcntl.ioctl(container_fd, VFIO_SET_IOMMU, VFIO_TYPE1_IOMMU)

            # Ask the group for a device FD
            # Create a proper ctypes char array for the device name
            name_array = (ctypes.c_char * 40)()
            name_bytes = self.bdf.encode("utf-8")
            if len(name_bytes) >= 40:
                raise VFIOBindError(f"Device name {self.bdf} too long (max 39 chars)")

            # Copy the device name into the array (null-terminated)
            ctypes.memmove(name_array, name_bytes, len(name_bytes))
            name_array[len(name_bytes)] = 0  # Ensure null termination

            device_fd = fcntl.ioctl(group_fd, VFIO_GROUP_GET_DEVICE_FD, name_array)

            # Group FD can be closed now; the container & device FDs remain live
            if group_fd is not None:
                os.close(group_fd)
                group_fd = None

            return int(device_fd), container_fd

        except OSError as e:
            # Ensure all file descriptors are properly closed on error
            if group_fd is not None:
                try:
                    os.close(group_fd)
                except OSError:
                    pass  # Already closed or invalid
            if container_fd is not None:
                try:
                    os.close(container_fd)
                except OSError:
                    pass  # Already closed or invalid
            raise VFIOBindError(
                safe_format(
                    "Failed to open VFIO device FD for {bdf}: {e}",
                    bdf=self.bdf,
                    e=str(e),
                )
            ) from e

    def _get_vfio_region_info(self, region_index: int) -> Optional[Dict[str, Any]]:
        """Get VFIO region information for the specified region index.

        WARNING: This method is disabled in the new workflow to avoid double attach.
        Region info should be queried by the container after it sets up VFIO.

        Args:
            region_index: The region index to query

        Returns:
            None - method is disabled to avoid double attach
        """
        log_warning_safe(
            logger,
            "Region info query disabled in host-side VFIOBinder to avoid double attach",
            prefix="BIND",
        )
        log_warning_safe(
            logger, "Container should query region info after VFIO setup", prefix="BIND"
        )
        return None

    def rebind(self) -> None:
        """Manually rebind the device to vfio-pci."""
        if not self._bound:
            self._bind_to_vfio()

    def close(self) -> None:
        """Manually close and cleanup the binding."""
        self._cleanup()
        self._bound = False

    def __enter__(self) -> Path:
        """Enter context manager and return VFIO group device path."""
        # Get IOMMU group
        self.group_id = _get_iommu_group(self.bdf)

        # Bind to VFIO
        self._bind_to_vfio()

        # Optionally attach; generators in guests should do this instead
        if self._attach:
            fd = None
            cont_fd = None
            try:
                fd, cont_fd = self._open_vfio_device_fd()
            finally:
                if fd is not None:
                    try:
                        os.close(fd)
                    except OSError:
                        pass  # Already closed
                if cont_fd is not None:
                    try:
                        os.close(cont_fd)
                    except OSError:
                        pass  # Already closed

        return self._path_manager.get_vfio_group_path(self.group_id)

    def __exit__(self, exc_type: Any, exc_val: Any, exc_tb: Any) -> None:
        """Exit context manager and cleanup."""
        self._cleanup()


# Helper functions (extracted for better testability and reuse)


def _get_current_driver(bdf: str) -> Optional[str]:
    """Get the current driver for the device."""
    driver_link = Path(f"/sys/bus/pci/devices/{bdf}/driver")
    try:
        if driver_link.exists() and driver_link.is_symlink():
            return driver_link.resolve().name
    except (OSError, RuntimeError):
        # Handle broken symlinks or permission issues
        pass
    return None


def _get_iommu_group_safe(bdf: str) -> Optional[str]:
    """Get the IOMMU group for the device, returning None if not found."""
    try:
        return _get_iommu_group(bdf)
    except VFIOBindError:
        return None


def _get_iommu_group(bdf: str) -> str:
    """Get the IOMMU group for the device."""
    group_link = Path(f"/sys/bus/pci/devices/{bdf}/iommu_group")
    try:
        if not group_link.exists():
            raise VFIODeviceNotFoundError(
                safe_format("No IOMMU group found for device {bdf}", bdf=bdf)
            )
        return group_link.resolve().name
    except (OSError, RuntimeError) as e:
        raise VFIOBindError(
            safe_format("Failed to read IOMMU group for {bdf}: {e}", bdf=bdf, e=str(e))
        ) from e


@contextmanager
def VFIOBinder(bdf: str, *, attach: bool = True) -> Generator[Path, None, None]:
    """Context manager that yields the VFIO group device path.

    Args:
        bdf: PCI Bus:Device.Function identifier
        attach: Whether to attach the group (open device and set IOMMU)

    Yields:
        Path to the VFIO group device node (/dev/vfio/<group>)

    Raises:
        ValueError: If BDF format is invalid
        VFIOPermissionError: If not running as root
        VFIOBindError: If binding fails
    """
    binder = VFIOBinderImpl(bdf, attach=attach)
    with binder as group_path:
        yield group_path


def run_diagnostics(bdf: Optional[str] = None) -> Dict[str, Any]:
    """Run VFIO diagnostics and return structured results.

    Args:
        bdf: Optional BDF to check specific device

    Returns:
        Dictionary containing diagnostic results
    """
    if not HAS_VFIO_ASSIST:
        return {
            "overall": "skipped",
            "can_proceed": True,
            "checks": [],
            "message": "vfio_assist module not available - diagnostics skipped",
        }

    try:
        if Diagnostics is None:
            return {
                "overall": "skipped",
                "can_proceed": True,
                "checks": [],
                "message": "vfio_assist module not available - diagnostics skipped",
            }
        diagnostics = Diagnostics(bdf)
        result = diagnostics.run()

        # Convert to dictionary for JSON serialization
        return {
            "overall": result.overall,
            "can_proceed": result.can_proceed,
            "checks": [
                {
                    "name": check.name,
                    "status": check.status,
                    "message": check.message,
                    "details": getattr(check, "details", None),
                }
                for check in result.checks
            ],
        }
    except Exception as e:
        log_error_safe(
            logger,
            safe_format("Diagnostics failed: {error}", error=str(e)),
            prefix="DIAG",
        )
        return {"overall": "error", "can_proceed": False, "checks": [], "error": str(e)}


def render_pretty(diagnostic_result: Dict[str, Any]) -> str:
    """Render diagnostic results with ANSI colors for display.

    Args:
        diagnostic_result: Result from run_diagnostics()

    Returns:
        Formatted string with ANSI color codes
    """
    output = []
    overall = diagnostic_result.get("overall", "unknown")

    try:
        # Use color functions from vfio_diagnostics if available
        from .vfio_diagnostics import Fore, colour

        # Header
        if overall == "ok":
            output.append(colour("✓ VFIO Diagnostics: PASSED", Fore.GREEN))
        elif overall == "warning":
            output.append(colour("⚠ VFIO Diagnostics: WARNINGS", Fore.YELLOW))
        else:
            output.append(colour("✗ VFIO Diagnostics: FAILED", Fore.RED))

        # Individual checks
        for check in diagnostic_result.get("checks", []):
            status = check.get("status", "unknown")
            name = check.get("name", "Unknown")
            message = check.get("message", "")

            if status == "ok":
                output.append(f"  ✓ {colour(name, Fore.GREEN)}: {message}")
            elif status == "warning":
                output.append(f"  ⚠ {colour(name, Fore.YELLOW)}: {message}")
            else:
                output.append(f"  ✗ {colour(name, Fore.RED)}: {message}")

        # Error if present
        if "error" in diagnostic_result:
            output.append(colour(f"Error: {diagnostic_result['error']}", Fore.RED))

    except ImportError:
        # Fallback without colors - generate a clean text report
        header_map = {
            "ok": "✓ VFIO Diagnostics: PASSED",
            "warning": "⚠ VFIO Diagnostics: WARNINGS",
            "error": "✗ VFIO Diagnostics: FAILED",
        }
        # Ensure JSON serializer is invoked for diagnostics consumers that rely on it
        try:
            json.dumps(diagnostic_result, indent=2)
        except Exception:
            # Ignore serialization errors here; we still want a textual report
            pass

        output.append(header_map.get(overall, "✗ VFIO Diagnostics: FAILED"))

        for check in diagnostic_result.get("checks", []):
            status_map = {"ok": "✓", "warning": "⚠", "error": "✗"}
            status_icon = status_map.get(check.get("status"), "✗")
            name = check.get("name", "Unknown")
            message = check.get("message", "")
            output.append(f"  {status_icon} {name}: {message}")

        if "error" in diagnostic_result:
            output.append(f"Error: {diagnostic_result['error']}")

    return "\n".join(output)


# Expose the context manager at module level for backward compatibility
__all__ = [
    "VFIOBinder",
    "VFIOBindError",
    "VFIODeviceNotFoundError",
    "VFIOPermissionError",
    "VFIOGroupError",
    "BindingState",
    "DeviceInfo",
    "VFIOPathManager",
    "run_diagnostics",
    "render_pretty",
]
