"""VFIO handler for PCI device management."""

import errno
import fcntl
import logging
import os
import time
from dataclasses import dataclass
from enum import Enum
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

from pcileechfwgenerator.exceptions import (
    VFIOBindError,
    VFIODeviceNotFoundError,
    VFIOGroupError,
    VFIOPermissionError,
)
from pcileechfwgenerator.string_utils import (
    log_debug_safe,
    log_error_safe,
    log_info_safe,
    log_warning_safe,
    safe_format,
)
from pcileechfwgenerator.utils.validators import get_bdf_validator


class BindingState(Enum):
    """Represents the binding state of a PCI device."""

    UNBOUND = "unbound"
    BOUND_TO_VFIO = "bound_to_vfio"
    BOUND_TO_OTHER = "bound_to_other"


# Try to import privilege manager if available
HAS_PRIVILEGE_MANAGER = False
PrivilegeManager: Any = None
try:
    from pcileechfwgenerator.utils.privilege_manager import (
        PrivilegeManager as _PrivilegeManagerImpl,
    )

    PrivilegeManager = _PrivilegeManagerImpl
    HAS_PRIVILEGE_MANAGER = True
except ImportError:
    pass

# Configure logger
logger = logging.getLogger(__name__)


@dataclass
class DeviceInfo:
    """Information about a PCI device."""

    bdf: str
    vendor_id: str = None
    device_id: str = None
    iommu_group: Optional[str] = None
    driver: Optional[str] = None
    description: Optional[str] = None

    # Compatibility properties
    @property
    def current_driver(self) -> Optional[str]:
        """Alias for driver property for backward compatibility."""
        return self.driver

    @property
    def binding_state(self) -> BindingState:
        """Get the binding state of the device."""
        if not self.driver:
            return BindingState.UNBOUND
        elif self.driver == "vfio-pci":
            return BindingState.BOUND_TO_VFIO
        else:
            return BindingState.BOUND_TO_OTHER

    @classmethod
    def from_bdf(cls, bdf: str) -> "DeviceInfo":
        """Create DeviceInfo from BDF string.

        Args:
            bdf: PCI Bus:Device.Function identifier

        Returns:
            DeviceInfo object with device details

        Raises:
            VFIODeviceNotFoundError: If device doesn't exist
        """
        # Create path manager to get device info
        path_manager = VFIOPathManager(bdf)
        device_path = path_manager.device_path

        if not device_path.exists():
            log_warning_safe(logger, "PCI device %s not found", bdf, prefix="VFIO")
            raise VFIODeviceNotFoundError(f"PCI device {bdf} not found")

        # Get vendor and device IDs
        vendor_id = None
        device_id = None
        try:
            vendor_id, device_id = path_manager.get_vendor_device_id()
        except Exception as e:
            raise VFIODeviceNotFoundError(
                safe_format(
                    "Cannot read device information for {bdf}: {err}", bdf=bdf, err=e
                )
            )

        # Get current driver
        driver = None
        if path_manager.driver_path.exists():
            try:
                driver = path_manager.driver_path.resolve().name
                log_info_safe(
                    logger, "Current driver for %s: %s", bdf, driver, prefix="VFIO"
                )
            except Exception as e:
                log_warning_safe(
                    logger, "Failed to read driver for %s: %s", bdf, e, prefix="VFIO"
                )
                raise VFIODeviceNotFoundError(f"Failed to read driver for {bdf}: {e}")

        # Get IOMMU group
        iommu_group = None
        if path_manager.iommu_group_path:
            try:
                group_path = path_manager.iommu_group_path.resolve()
                iommu_group = group_path.name
                log_info_safe(
                    logger,
                    safe_format(
                        "IOMMU group for {bdf}: {group}", bdf=bdf, group=iommu_group
                    ),
                    prefix="VFIO",
                )
            except Exception as e:
                log_warning_safe(
                    logger,
                    safe_format(
                        "Failed to read IOMMU group for {bdf}: {err}", bdf=bdf, err=e
                    ),
                    prefix="VFIO",
                )
                raise VFIODeviceNotFoundError(
                    f"Failed to read IOMMU group for {bdf}: {e}"
                )
        log_info_safe(
            logger,
            safe_format("Successfully retrieved device info for {bdf}", bdf=bdf),
            prefix="VFIO",
        )
        return cls(
            bdf=bdf,
            vendor_id=vendor_id,
            device_id=device_id,
            iommu_group=iommu_group,
            driver=driver,
        )


class VFIOPathManager:
    """Manages file system paths for VFIO operations."""

    def __init__(self, bdf: str):
        """Initialize path manager for a specific BDF.

        Args:
            bdf: PCI Bus:Device.Function identifier
        """
        self.bdf = bdf
        self._validate_bdf()

    def _validate_bdf(self) -> None:
        """Validate BDF format using the new validator."""
        validator = get_bdf_validator()
        result = validator.validate(self.bdf)
        if not result.valid:
            raise ValueError(
                safe_format("Invalid BDF format: {error}", error=result.errors[0])
            )

    @property
    def device_path(self) -> Path:
        """Get the device sysfs path."""
        return Path(f"/sys/bus/pci/devices/{self.bdf}")

    @property
    def driver_path(self) -> Path:
        """Get the driver symlink path."""
        return self.device_path / "driver"

    @property
    def driver_link(self) -> Path:
        """Alias for driver_path for backward compatibility."""
        return self.driver_path

    @property
    def driver_override_path(self) -> Path:
        """Alias for override_path for backward compatibility."""
        return self.override_path

    @property
    def iommu_group_link(self) -> Path:
        """Get the IOMMU group symlink path."""
        return self.device_path / "iommu_group"

    @property
    def override_path(self) -> Path:
        """Get the driver override path."""
        return self.device_path / "driver_override"

    @property
    def unbind_path(self) -> Optional[Path]:
        """Get the unbind path for the current driver."""
        if self.driver_path.exists():
            return self.driver_path / "unbind"
        return None

    @property
    def new_id_path(self) -> Path:
        """Get the new_id path for vfio-pci."""
        return Path("/sys/bus/pci/drivers/vfio-pci/new_id")

    @property
    def bind_path(self) -> Path:
        """Get the bind path for vfio-pci."""
        return Path("/sys/bus/pci/drivers/vfio-pci/bind")

    @property
    def remove_id_path(self) -> Path:
        """Get the remove_id path for vfio-pci."""
        return Path("/sys/bus/pci/drivers/vfio-pci/remove_id")

    @property
    def iommu_group_path(self) -> Optional[Path]:
        """Get the IOMMU group symlink path."""
        group_link = self.device_path / "iommu_group"
        return group_link if group_link.exists() else None

    def get_vendor_device_id(self) -> Tuple[str, str]:
        """Read vendor and device IDs from sysfs.

        Returns:
            Tuple of (vendor_id, device_id)

        Raises:
            VFIODeviceNotFoundError: If device files cannot be read
        """
        try:
            vendor_id = (self.device_path / "vendor").read_text().strip()
            device_id = (self.device_path / "device").read_text().strip()
            # Remove 0x prefix
            vendor_id = vendor_id.replace("0x", "")
            device_id = device_id.replace("0x", "")
            return vendor_id, device_id
        except (OSError, IOError) as e:
            raise VFIODeviceNotFoundError(
                safe_format(
                    "Cannot read device information for {bdf}: {err}",
                    bdf=self.bdf,
                    err=e,
                )
            )

    def get_driver_unbind_path(self, driver_name: str) -> Path:
        """Get the unbind path for a specific driver.

        Args:
            driver_name: Name of the driver

        Returns:
            Path to the driver's unbind file
        """
        return Path(
            safe_format(
                "/sys/bus/pci/drivers/{driver_name}/unbind", driver_name=driver_name
            )
        )

    def get_driver_bind_path(self, driver_name: str) -> Path:
        """Get the bind path for a specific driver.

        Args:
            driver_name: Name of the driver

        Returns:
            Path to the driver's bind file
        """
        return Path(
            safe_format(
                "/sys/bus/pci/drivers/{driver_name}/bind", driver_name=driver_name
            )
        )

    def get_vfio_group_path(self, group_id: str) -> Path:
        """Get the VFIO group device path.

        Args:
            group_id: IOMMU group ID

        Returns:
            Path to the VFIO group device
        """
        return Path(safe_format("/dev/vfio/{group_id}", group_id=group_id))


# --- per-device binding journal (shared by every driver-changing path) ------


def binding_journal_path(directory: Path, bdf: str) -> Path:
    return Path(directory) / f"binding-{bdf.replace(':', '_')}.json"


def begin_binding_journal(directory: Path, bdf: str, record: Dict[str, Any]) -> Path:
    """Durably record a device's original binding before changing it.

    Raises VFIOBindError if a journal for the device already exists: a previous
    session did not confirm restoration, or another session holds the device.
    ``os.link`` makes creation exclusive across processes.
    """
    import json
    import tempfile
    from datetime import datetime, timezone

    path = binding_journal_path(directory, bdf)
    if path.exists():
        raise VFIOBindError(
            safe_format(
                "A previous VFIO session for {bdf} did not confirm it "
                "restored the original driver binding (journal {path}). "
                "Verify the device's driver, then remove the journal.",
                bdf=bdf,
                path=str(path),
            )
        )
    path.parent.mkdir(parents=True, exist_ok=True)
    data = json.dumps(
        {
            "bdf": bdf,
            **record,
            "started_at": datetime.now(timezone.utc).isoformat(),
        }
    ).encode()
    with tempfile.NamedTemporaryFile(
        dir=path.parent, prefix=".binding-", delete=False
    ) as stream:
        stream.write(data)
        stream.flush()
        os.fsync(stream.fileno())
    try:
        os.link(stream.name, path)
    except FileExistsError:
        raise VFIOBindError(
            safe_format(
                "Another session for {bdf} holds journal {path}",
                bdf=bdf,
                path=str(path),
            )
        ) from None
    finally:
        os.unlink(stream.name)
    handle = os.open(path.parent, os.O_RDONLY)
    try:
        os.fsync(handle)
    finally:
        os.close(handle)
    return path


def clear_binding_journal(directory: Path, bdf: str) -> None:
    binding_journal_path(directory, bdf).unlink(missing_ok=True)


class VFIOBinder:
    """Handles binding PCI devices to vfio-pci driver."""

    def __init__(
        self,
        bdf: str,
        *,
        attach: bool = True,
        journal_directory: Any = "default",
    ) -> None:
        """Initialize VFIO binder for the specified BDF.

        Args:
            bdf: PCI Bus:Device.Function identifier
            attach: Whether to attach the group (open device and set IOMMU)
            journal_directory: Where the binding journal is written before
                any driver change; it is cleared only after the original
                binding is verified, so a crash mid-session blocks the next
                bind of this device until an operator checks it. Defaults to
                the host-wide register-learning journal directory, shared by
                every binder user; None disables journaling (tests only).

        Raises:
            ValueError: If BDF format is invalid
            VFIOPermissionError: If not running as root
        """
        self._validate_permissions()
        self._validate_bdf(bdf)

        self.bdf = bdf
        self.original_driver: Optional[str] = None
        self.original_override: str = ""
        self.final_driver: Optional[str] = None
        # The device was already owned by vfio-pci before this session; it is
        # used in place and left there.
        self._preexisting_vfio = False
        if journal_directory == "default":
            from pcileechfwgenerator.host_collect.register_learning import (
                default_journal_directory,
            )

            journal_directory = default_journal_directory() / "bindings"
        self._journal_directory = (
            Path(journal_directory) if journal_directory is not None else None
        )
        self.group_id: Optional[str] = None
        self._bound = False
        self._attach = attach
        self._path_manager = VFIOPathManager(bdf)
        self._device_info: Optional[DeviceInfo] = None
        self._privilege_manager = None
        self._group_lock: Optional[Any] = None  # File lock for IOMMU group
        self._added_device_ids: List[Tuple[str, str]] = []  # Track new_id calls

        # Initialize privilege manager if available
        if HAS_PRIVILEGE_MANAGER and PrivilegeManager is not None:
            self._privilege_manager = PrivilegeManager()

        # Check security context and emit warnings
        self._check_security_context()

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
        """Validate BDF format using the new validator."""
        validator = get_bdf_validator()
        result = validator.validate(bdf)
        if not result.valid:
            raise ValueError(f"Invalid BDF format: {result.errors[0]}")

    def _check_privilege(self, operation: str) -> None:
        """Check if we have privileges for the operation.

        Args:
            operation: Description of the operation

        Raises:
            VFIOPermissionError: If privileges are insufficient
        """
        if self._privilege_manager is None:
            return

        # PrivilegeManager exposes has_root / can_sudo (no check_privilege()).
        # Privileges are sufficient if we are root or can escalate via sudo.
        has_privilege = getattr(self._privilege_manager, "has_root", False) or getattr(
            self._privilege_manager, "can_sudo", False
        )
        if not has_privilege:
            raise VFIOPermissionError(
                f"Insufficient privileges for {operation}. "
                f"Please run with appropriate permissions."
            )

    def get_device_info(self) -> DeviceInfo:
        """Get information about the PCI device.

        Returns:
            DeviceInfo object with device details

        Raises:
            VFIODeviceNotFoundError: If device doesn't exist
        """
        if self._device_info is not None:
            return self._device_info

        device_path = self._path_manager.device_path
        if not device_path.exists():
            raise VFIODeviceNotFoundError(f"PCI device {self.bdf} not found")

        try:
            # Get vendor and device IDs
            vendor_id, device_id = self._path_manager.get_vendor_device_id()

            # Get current driver
            driver = None
            if self._path_manager.driver_path.exists():
                driver = self._path_manager.driver_path.resolve().name

            # Get IOMMU group
            iommu_group = None
            if self._path_manager.iommu_group_path:
                group_path = self._path_manager.iommu_group_path.resolve()
                iommu_group = group_path.name

            # Try to get device description
            description = None
            try:
                # This would typically come from pciutils/libpci
                # For now, we'll just use the IDs
                description = f"{vendor_id}:{device_id}"
            except Exception:
                pass

            self._device_info = DeviceInfo(
                bdf=self.bdf,
                vendor_id=vendor_id,
                device_id=device_id,
                iommu_group=iommu_group,
                driver=driver,
                description=description,
            )
            return self._device_info

        except Exception as e:
            raise VFIODeviceNotFoundError(
                safe_format(
                    "Failed to get device info for {bdf}: {error}",
                    bdf=self.bdf,
                    error=e,
                )
            )

    def _check_iommu(self) -> None:
        """Check if IOMMU is enabled for the device.

        Raises:
            VFIOBindError: If IOMMU is not enabled
        """
        if not self._path_manager.iommu_group_path:
            raise VFIOBindError(
                f"No IOMMU group found for {self.bdf}. "
                f"Please ensure IOMMU is enabled in BIOS/UEFI and kernel."
            )

    def _check_security_context(self) -> None:
        """Check for SELinux/AppArmor and emit diagnostic warnings.

        This helps users understand container VFIO failures caused by
        security contexts blocking /dev/vfio access or sysfs writes.
        """
        # Check SELinux
        selinux_enforce = Path("/sys/fs/selinux/enforce")
        if selinux_enforce.exists():
            try:
                if selinux_enforce.read_text().strip() == "1":
                    log_warning_safe(
                        logger,
                        "SELinux is enforcing - container VFIO may fail. "
                        "Run container with: --security-opt label=disable",
                        prefix="SEC",
                    )
            except (OSError, IOError):
                pass

        # Check AppArmor
        apparmor_enabled = Path("/sys/module/apparmor/parameters/enabled")
        if apparmor_enabled.exists():
            try:
                if apparmor_enabled.read_text().strip().upper() in ("Y", "1"):
                    log_warning_safe(
                        logger,
                        "AppArmor is enabled - Docker containers may need: "
                        "--security-opt apparmor=unconfined",
                        prefix="SEC",
                    )
            except (OSError, IOError):
                pass

    def _wait_for_group_node(self, group_id: str, timeout: float = 3.0) -> None:
        """Wait for /dev/vfio/{group} device node to appear.

        In containers, udev may race with bind operations. This ensures
        the group device node exists before we try to access it.

        Args:
            group_id: IOMMU group ID
            timeout: Maximum wait time in seconds

        Raises:
            VFIOGroupError: If timeout waiting for group node
        """
        group_path = Path(f"/dev/vfio/{group_id}")
        start_time = time.time()

        while time.time() - start_time < timeout:
            if group_path.exists():
                log_debug_safe(
                    logger,
                    safe_format("Group device node ready: {path}", path=group_path),
                    prefix="VFIO",
                )
                return
            time.sleep(0.05)

        raise VFIOGroupError(
            safe_format(
                "Timeout waiting for {path}. "
                "Check SELinux labels (relabel_files) or udev rules.",
                path=group_path,
            )
        )

    def _acquire_group_lock(self, group_id: str) -> None:
        """Acquire inter-process lock for IOMMU group.

        Prevents multiple containers/processes from binding devices in
        the same IOMMU group concurrently.

        Args:
            group_id: IOMMU group ID

        Raises:
            VFIOBindError: If group is already locked by another process
        """
        lock_dir = Path("/var/lock")
        lock_dir.mkdir(parents=True, exist_ok=True)

        lock_path = lock_dir / f"pcileech-vfio-{group_id}.lock"

        lock_file = None
        try:
            lock_file = open(lock_path, "w")
            # Try non-blocking lock
            fcntl.flock(lock_file.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
            self._group_lock = lock_file

            log_debug_safe(
                logger,
                safe_format("Acquired group lock: {path}", path=lock_path),
                prefix="LOCK",
            )
        except (OSError, IOError) as e:
            if lock_file:
                lock_file.close()
            raise VFIOBindError(
                safe_format(
                    "IOMMU group {gid} is already in use by another process. "
                    "Lock file: {path}",
                    gid=group_id,
                    path=lock_path,
                )
            ) from e

    def _release_group_lock(self) -> None:
        """Release IOMMU group lock if held."""
        if self._group_lock:
            try:
                fcntl.flock(self._group_lock.fileno(), fcntl.LOCK_UN)
                self._group_lock.close()
                log_debug_safe(
                    logger,
                    "Released group lock",
                    prefix="LOCK",
                )
            except Exception as e:
                log_warning_safe(
                    logger,
                    safe_format("Failed to release group lock: {err}", err=e),
                    prefix="LOCK",
                )
            finally:
                self._group_lock = None

    def _cleanup_device_ids(self) -> List[str]:
        """Remove device IDs added to vfio-pci; return failures.

        A dynamic ID left behind makes vfio-pci claim this device (and any
        identical one) on the next probe, so failed removals are kept for a
        retry and reported rather than forgotten.
        """
        if not self._added_device_ids:
            return []

        remove_id_path = self._path_manager.remove_id_path
        if not remove_id_path.exists():
            return [
                safe_format(
                    "remove_id unavailable; vfio-pci still matches {ids}",
                    ids=", ".join(f"{v}:{d}" for v, d in self._added_device_ids),
                )
            ]

        failures = []
        remaining = []
        for vendor_id, device_id in self._added_device_ids:
            try:
                remove_id_path.write_text(f"{vendor_id} {device_id}\n")
            except (OSError, IOError) as e:
                remaining.append((vendor_id, device_id))
                failures.append(
                    safe_format(
                        "remove_id {vid}:{did} failed: {err}",
                        vid=vendor_id,
                        did=device_id,
                        err=e,
                    )
                )
                log_warning_safe(logger, failures[-1], prefix="VFIO")
        self._added_device_ids[:] = remaining
        return failures

    def _save_original_driver(self) -> None:
        """Save the current driver for restoration."""
        self.original_driver = None
        if self._path_manager.driver_path.exists():
            self.original_driver = self._path_manager.driver_path.resolve().name
            log_debug_safe(
                logger,
                safe_format(
                    "Original driver for {bdf}: {drv}",
                    bdf=self.bdf,
                    drv=self.original_driver,
                ),
                prefix="VFIO",
            )

    def _unbind_current_driver(self) -> None:
        """Unbind device from its current driver."""
        if not self._path_manager.driver_path.exists():
            log_debug_safe(
                logger,
                safe_format("Device {bdf} not bound to any driver", bdf=self.bdf),
                prefix="VFIO",
            )
            return

        unbind_path = self._path_manager.unbind_path
        if not unbind_path:
            return

        try:
            self._check_privilege("unbind driver")
            unbind_path.write_text(f"{self.bdf}\n")
            log_info_safe(
                logger,
                safe_format(
                    "Unbound {bdf} from {drv}",
                    bdf=self.bdf,
                    drv=self.original_driver,
                ),
                prefix="VFIO",
            )

            # Wait for unbind to complete
            timeout = 5
            start = time.time()
            while (
                self._path_manager.driver_path.exists()
                and time.time() - start < timeout
            ):
                time.sleep(0.1)

            if self._path_manager.driver_path.exists():
                raise VFIOBindError(f"Failed to unbind {self.bdf} from driver")

        except (OSError, IOError) as e:
            raise VFIOBindError(f"Failed to unbind driver: {e}")

    def _set_driver_override(self) -> None:
        """Set driver override to vfio-pci."""
        try:
            self._check_privilege("set driver override")
            self._path_manager.override_path.write_text("vfio-pci\n")
            log_debug_safe(
                logger,
                safe_format("Set driver override to vfio-pci for {bdf}", bdf=self.bdf),
                prefix="VFIO",
            )
        except (OSError, IOError) as e:
            raise VFIOBindError(f"Failed to set driver override: {e}")

    def _bind_to_vfio(self) -> None:
        """Bind device to vfio-pci driver."""
        try:
            # driver_override (set just before) is sufficient for vfio-pci to
            # bind this one device. new_id is not used: it makes vfio-pci claim
            # every unbound device with the same VID:DID, and it can attach
            # the target before the explicit bind below (which then EBUSYs).
            # Small delay to let kernel finish processing unbind/override
            time.sleep(0.2)

            # Bind to vfio-pci with retry for transient EBUSY
            self._check_privilege("bind to vfio-pci")
            max_retries = 5
            for attempt in range(max_retries):
                try:
                    self._path_manager.bind_path.write_text(f"{self.bdf}\n")
                    log_info_safe(
                        logger,
                        safe_format("Bound {bdf} to vfio-pci", bdf=self.bdf),
                        prefix="VFIO",
                    )
                    break
                except OSError as e:
                    if e.errno == errno.EBUSY and self._current_driver() == "vfio-pci":
                        break  # already attached to vfio-pci: success
                    if e.errno == errno.EBUSY and attempt < max_retries - 1:
                        delay = 0.5 * (attempt + 1)
                        log_debug_safe(
                            logger,
                            safe_format(
                                "Device {bdf} busy, retrying in {delay}s "
                                "(attempt {n}/{total})",
                                bdf=self.bdf,
                                delay=delay,
                                n=attempt + 1,
                                total=max_retries,
                            ),
                            prefix="VFIO",
                        )
                        time.sleep(delay)
                    else:
                        raise

            # Verify binding
            timeout = 5
            start = time.time()
            while time.time() - start < timeout:
                if (
                    self._path_manager.driver_path.exists()
                    and self._path_manager.driver_path.resolve().name == "vfio-pci"
                ):
                    self._bound = True
                    return
                time.sleep(0.1)

            raise VFIOBindError(f"Failed to verify vfio-pci binding for {self.bdf}")

        except (OSError, IOError) as e:
            raise VFIOBindError(f"Failed to bind to vfio-pci: {e}")

    def _get_iommu_group(self) -> str:
        """Get the IOMMU group for the device.

        Returns:
            IOMMU group number

        Raises:
            VFIOBindError: If IOMMU group cannot be determined
        """
        if not self._path_manager.iommu_group_path:
            raise VFIOBindError(f"No IOMMU group found for {self.bdf}")

        try:
            group_path = self._path_manager.iommu_group_path.resolve()
            return group_path.name
        except (OSError, IOError) as e:
            raise VFIOBindError(f"Failed to determine IOMMU group: {e}")

    def _attach_group(self) -> None:
        """Attach to the IOMMU group and configure it."""
        if not self._attach:
            log_debug_safe(
                logger,
                "Skipping group attachment (attach=False)",
                prefix="VFIO",
            )
            return

        try:
            self.group_id = self._get_iommu_group()
            group_path = Path(f"/dev/vfio/{self.group_id}")

            if not group_path.exists():
                raise VFIOGroupError(
                    f"VFIO group {self.group_id} not available. "
                    f"Ensure vfio-pci is loaded and device is bound."
                )

            # Open the group to ensure it's accessible
            self._check_privilege("access VFIO group")
            try:
                with open(group_path, "r"):
                    log_debug_safe(
                        logger,
                        safe_format(
                            "Successfully opened VFIO group {gid}",
                            gid=self.group_id,
                        ),
                        prefix="VFIO",
                    )
            except (OSError, IOError) as e:
                raise VFIOGroupError(f"Cannot access VFIO group {self.group_id}: {e}")

        except Exception as e:
            raise VFIOGroupError(f"Failed to attach IOMMU group: {e}")

    # --- sysfs primitives (one place for state reads/writes; faked in tests) --

    def _current_driver(self) -> Optional[str]:
        driver_path = self._path_manager.driver_path
        if not driver_path.is_symlink() and not driver_path.exists():
            return None
        return driver_path.resolve().name

    def _read_override(self) -> str:
        try:
            value = self._path_manager.override_path.read_text().strip()
        except (OSError, IOError):
            return ""
        return "" if value == "(null)" else value

    def _write_override(self, value: str) -> None:
        self._check_privilege("set driver override")
        self._path_manager.override_path.write_text(f"{value}\n")

    def _unbind_driver(self) -> None:
        self._check_privilege("unbind driver")
        (self._path_manager.driver_path / "unbind").write_text(f"{self.bdf}\n")

    def _bind_driver(self, driver: str) -> None:
        self._check_privilege(f"bind {driver} driver")
        Path(f"/sys/bus/pci/drivers/{driver}/bind").write_text(f"{self.bdf}\n")

    def _probe_driver(self) -> None:
        self._check_privilege("probe for driver")
        Path("/sys/bus/pci/drivers_probe").write_text(f"{self.bdf}\n")

    # --- binding journal -----------------------------------------------------

    def _journal_path(self) -> Optional[Path]:
        if self._journal_directory is None:
            return None
        return binding_journal_path(self._journal_directory, self.bdf)

    def _journal_begin(self) -> None:
        """Durably record the original binding before changing anything."""
        if self._journal_directory is None:
            return
        begin_binding_journal(
            self._journal_directory,
            self.bdf,
            {
                "original_driver": self.original_driver,
                "original_override": self.original_override,
            },
        )

    def _journal_clear(self) -> None:
        if self._journal_directory is not None:
            clear_binding_journal(self._journal_directory, self.bdf)

    # --- transactional restore ----------------------------------------------

    def _restore_original(self) -> None:
        """Return the device to the binding it had before bind() and verify it.

        Safe to call from any partial state of bind(). Raises VFIOBindError
        naming the actual final binding when it cannot be verified.
        """
        target = self.original_driver
        current = self._current_driver()
        if current not in (None, "vfio-pci", target):
            # Someone else bound another driver meanwhile; do not touch it.
            self.final_driver = current
            raise VFIOBindError(
                safe_format(
                    "{bdf} is bound to {cur}, not vfio-pci or its original "
                    "driver {orig}; left untouched",
                    bdf=self.bdf,
                    cur=current,
                    orig=target or "none",
                )
            )
        # Attempt every step; failures are collected, and the outcome is
        # always verified and reported from the actual final state.
        step_errors: List[str] = []

        def attempt(label: str, action: Any) -> None:
            try:
                action()
            except (OSError, IOError, VFIOPermissionError, VFIOBindError) as exc:
                step_errors.append(f"{label}: {exc}")

        if self._preexisting_vfio:
            if current != "vfio-pci":
                if self.original_override:
                    attempt(
                        "override",
                        lambda: self._write_override(self.original_override),
                    )
                attempt("bind vfio-pci", lambda: self._bind_driver("vfio-pci"))
        else:
            if current == "vfio-pci":
                attempt("unbind vfio-pci", self._unbind_driver)
            # Bind the original driver with no override in the way (an
            # override naming another driver makes the kernel refuse it), then
            # put the original override back.
            if self._read_override():
                attempt("clear override", lambda: self._write_override(""))
            # Stop vfio-pci from claiming the device again by ID.
            step_errors.extend(self._cleanup_device_ids() or [])
            if target and self._current_driver() is None:
                attempt("bind " + target, lambda: self._bind_driver(target))
                if self._current_driver() is None:
                    attempt("probe", self._probe_driver)
            if self._read_override() != self.original_override:
                attempt(
                    "restore override",
                    lambda: self._write_override(self.original_override),
                )
        self.final_driver = self._current_driver()
        problems = []
        if self.final_driver != target:
            problems.append(
                safe_format(
                    "driver is {cur} (expected {orig})",
                    cur=self.final_driver or "none",
                    orig=target or "none",
                )
            )
        if self._read_override() != self.original_override:
            problems.append("driver_override was not restored")
        if step_errors and not problems:
            # Binding verified, but something was left behind (e.g. a dynamic
            # vfio-pci ID): not fatal, never silent.
            log_warning_safe(
                logger,
                safe_format(
                    "Restored {bdf} with leftovers: {errs}",
                    bdf=self.bdf,
                    errs="; ".join(step_errors),
                ),
                prefix="VFIO",
            )
        if problems:
            problems.extend(step_errors)
            raise VFIOBindError(
                safe_format(
                    "Original binding of {bdf} not restored: {problems}. "
                    "Rebind the device manually before using it.",
                    bdf=self.bdf,
                    problems="; ".join(problems),
                )
            )

    def bind(self) -> None:
        """Bind the PCI device to vfio-pci driver.

        Raises:
            VFIOBindError: If binding fails
            VFIOPermissionError: If permissions are insufficient
        """
        log_info_safe(
            logger,
            safe_format("Binding {bdf} to vfio-pci", bdf=self.bdf),
            prefix="VFIO",
        )

        # Check IOMMU is enabled
        self._check_iommu()

        # Get IOMMU group for locking
        group_id = self._get_iommu_group()

        # Acquire inter-process lock to prevent concurrent binding
        self._acquire_group_lock(group_id)

        changed = False
        try:
            # Save original binding for cleanup; record it before any change.
            self._save_original_driver()
            self.original_override = self._read_override()
            self._preexisting_vfio = self.original_driver == "vfio-pci"
            self._journal_begin()
            changed = True

            if self._preexisting_vfio:
                # Already ours: use it in place, never unbind it.
                self._bound = True
            else:
                # Unbind from current driver
                self._unbind_current_driver()

                # Set driver override
                self._set_driver_override()

                # Bind to vfio-pci
                self._bind_to_vfio()

            # Wait for /dev/vfio/{group} to appear (udev race in containers)
            self._wait_for_group_node(group_id)

            # Attach to IOMMU group if requested
            if self._attach:
                try:
                    self._attach_group()
                except Exception as e:
                    log_warning_safe(
                        logger,
                        safe_format(
                            "Failed to attach IOMMU group for {bdf}: {err}",
                            bdf=self.bdf,
                            err=e,
                        ),
                        prefix="VFIO",
                    )
                    # Continue anyway - the device is bound

            log_info_safe(
                logger,
                safe_format("Successfully bound {bdf} to vfio-pci", bdf=self.bdf),
                prefix="VFIO",
            )
        except Exception as error:
            # Partial entry: roll back while still holding the group lock.
            # __exit__ will not run because __enter__ failed.
            try:
                if changed:
                    self._restore_original()
                    self._journal_clear()
            except Exception as rollback_error:
                log_error_safe(
                    logger,
                    safe_format(
                        "Rollback after failed bind of {bdf} failed: {err}",
                        bdf=self.bdf,
                        err=rollback_error,
                    ),
                    prefix="VFIO",
                )
                raise VFIOBindError(
                    safe_format(
                        "Binding {bdf} failed ({err}) and rollback failed: "
                        "{rollback}",
                        bdf=self.bdf,
                        err=error,
                        rollback=rollback_error,
                    )
                ) from error
            finally:
                self._bound = False
                self._release_group_lock()
            raise

    def unbind(self) -> None:
        """Restore the device's original binding and verify it.

        A device that was already on vfio-pci stays there. Raises
        VFIOBindError, naming the actual binding, when restoration cannot be
        verified; the binding journal is then kept so the next session stops.
        """
        log_info_safe(
            logger,
            safe_format("Restoring original binding of {bdf}", bdf=self.bdf),
            prefix="VFIO",
        )
        try:
            self._restore_original()
            self._journal_clear()
        finally:
            self._bound = False
            self._release_group_lock()
        log_info_safe(
            logger,
            safe_format(
                "Restored {bdf} to {driver}",
                bdf=self.bdf,
                driver=self.final_driver or "no driver",
            ),
            prefix="VFIO",
        )

    def __enter__(self):
        """Context manager entry."""
        self.bind()
        return self

    def __exit__(self, _exc_type, _exc_val, _exc_tb):
        """Context manager exit - ensures complete cleanup."""
        try:
            if self._bound:
                try:
                    self.unbind()
                except Exception as e:
                    log_warning_safe(
                        logger,
                        safe_format("Failed to unbind in cleanup: {err}", err=e),
                        prefix="VFIO",
                    )
        finally:
            # Always clean up device IDs and release lock, even if unbind fails
            self._cleanup_device_ids()
            self._release_group_lock()

    @property
    def is_bound(self) -> bool:
        """Check if device is currently bound to vfio-pci."""
        return (
            self._bound
            and self._path_manager.driver_path.exists()
            and self._path_manager.driver_path.resolve().name == "vfio-pci"
        )


def check_vfio_availability() -> Dict[str, Any]:
    """Check if VFIO is available on the system.

    Returns:
        Dictionary with availability information:
        - available: bool - Whether VFIO is available
        - vfio_loaded: bool - Whether vfio kernel module is loaded
        - vfio_pci_loaded: bool - Whether vfio-pci kernel module is loaded
        - iommu_enabled: bool - Whether IOMMU is enabled
        - errors: List[str] - Any error messages
    """
    result = {
        "available": False,
        "vfio_loaded": False,
        "vfio_pci_loaded": False,
        "iommu_enabled": False,
        "errors": [],
    }

    # Check kernel modules
    try:
        with open("/proc/modules", "r") as f:
            modules = f.read()
            result["vfio_loaded"] = "vfio " in modules
            result["vfio_pci_loaded"] = "vfio_pci " in modules
    except Exception as e:
        result["errors"].append(f"Cannot check kernel modules: {e}")

    # Check IOMMU
    iommu_groups = Path("/sys/kernel/iommu_groups")
    if iommu_groups.exists() and list(iommu_groups.iterdir()):
        result["iommu_enabled"] = True
    else:
        result["errors"].append("IOMMU not enabled or no IOMMU groups found")

    # Check /dev/vfio
    if not Path("/dev/vfio").exists():
        result["errors"].append("/dev/vfio not found")

    # Overall availability
    result["available"] = (
        result["vfio_loaded"]
        and result["vfio_pci_loaded"]
        and result["iommu_enabled"]
        and Path("/dev/vfio").exists()
    )

    return result


# Alias for compatibility
VFIOBinderImpl = VFIOBinder


# Helper functions
def _get_current_driver(bdf: str) -> Optional[str]:
    """Get the current driver for a device.

    Args:
        bdf: PCI Bus:Device.Function identifier

    Returns:
        Driver name or None if not bound
    """
    try:
        path_manager = VFIOPathManager(bdf)
        if path_manager.driver_path.exists():
            return path_manager.driver_path.resolve().name
    except Exception as e:
        log_warning_safe(
            logger,
            safe_format(
                "Failed to get current driver for {bdf}: {err}", bdf=bdf, err=e
            ),
            prefix="VFIO",
        )
    return None


def _get_iommu_group(bdf: str) -> Optional[str]:
    """Get the IOMMU group for a device.

    Args:
        bdf: PCI Bus:Device.Function identifier

    Returns:
        IOMMU group ID or None

    Raises:
        VFIODeviceNotFoundError: If device doesn't exist
    """
    path_manager = VFIOPathManager(bdf)
    if not path_manager.device_path.exists():
        raise VFIODeviceNotFoundError(f"PCI device {bdf} not found")

    if path_manager.iommu_group_path:
        try:
            group_path = path_manager.iommu_group_path.resolve()
            return group_path.name
        except Exception as e:
            raise VFIODeviceNotFoundError(f"Failed to get IOMMU group: {e}")
    return None


def _get_iommu_group_safe(bdf: str) -> Optional[str]:
    """Safely get the IOMMU group for a device.

    Args:
        bdf: PCI Bus:Device.Function identifier

    Returns:
        IOMMU group ID or None
    """
    try:
        return _get_iommu_group(bdf)
    except Exception:
        return None


def _wait_for_state_change(
    check_func: callable,
    expected_state: Any,
    timeout: float = 5.0,
    poll_interval: float = 0.1,
) -> bool:
    """Wait for a state change with timeout.

    Args:
        check_func: Function to check current state
        expected_state: Expected state value
        timeout: Maximum time to wait in seconds
        poll_interval: Time between checks in seconds

    Returns:
        True if state changed, False if timeout
    """
    start_time = time.time()
    while time.time() - start_time < timeout:
        if check_func() == expected_state:
            return True
        time.sleep(poll_interval)
    return False


def run_diagnostics(bdf: str) -> Dict[str, Any]:
    """Run diagnostics on a PCI device.

    Args:
        bdf: PCI Bus:Device.Function identifier

    Returns:
        Dictionary with diagnostic information
    """
    result = {
        "bdf": bdf,
        "exists": False,
        "driver": None,
        "iommu_group": None,
        "vfio_capable": False,
        "errors": [],
    }

    try:
        device_info = DeviceInfo.from_bdf(bdf)
        result["exists"] = True
        result["driver"] = device_info.driver
        result["iommu_group"] = device_info.iommu_group
        result["vfio_capable"] = bool(device_info.iommu_group)
    except VFIODeviceNotFoundError:
        result["errors"].append(f"Device {bdf} not found")
    except Exception as e:
        result["errors"].append(str(e))

    return result


def render_pretty(data: Dict[str, Any], use_color: bool = True) -> str:
    """Render data in a pretty format.

    Args:
        data: Data to render
        use_color: Whether to use ANSI colors

    Returns:
        Formatted string
    """
    lines = []

    # Simple color codes
    if use_color:
        GREEN = "\033[32m"
        RED = "\033[31m"
        RESET = "\033[0m"
    else:
        GREEN = RED = RESET = ""

    for key, value in data.items():
        if isinstance(value, bool):
            color = GREEN if value else RED
            lines.append(f"{key}: {color}{value}{RESET}")
        elif isinstance(value, list):
            if value:
                lines.append(f"{key}:")
                for item in value:
                    lines.append(f"  - {item}")
            else:
                lines.append(f"{key}: {GREEN}none{RESET}")
        else:
            lines.append(f"{key}: {value}")

    return "\n".join(lines)


# Module exports
__all__ = [
    "BindingState",
    "DeviceInfo",
    "VFIOPathManager",
    "VFIOBinder",
    "VFIOBinderImpl",
    "check_vfio_availability",
    "log_warning_safe",
    "_get_current_driver",
    "_get_iommu_group",
    "_get_iommu_group_safe",
    "_wait_for_state_change",
    "run_diagnostics",
    "render_pretty",
]
