#!/usr/bin/env python3
"""
VFIO‑Assist - smarter VFIO diagnostics & auto‑fixer
"""
from __future__ import annotations

import argparse
import json
import logging
import os
import platform
import re
import shlex
import shutil
import subprocess
import sys
import textwrap
from dataclasses import dataclass
from enum import Enum, auto
from pathlib import Path
from typing import List, Optional, Tuple, Union

# Use consistent relative imports
from ..log_config import get_logger, setup_logging
from ..string_utils import (
    log_debug_safe,
    log_error_safe,
    log_info_safe,
    log_warning_safe,
    safe_format,
    safe_print_format,
)
from .vfio_handler import VFIOPathManager

# ──────────────────────────────────────────────────────────────────────────────
# Pretty terminal helpers
# ──────────────────────────────────────────────────────────────────────────────
try:
    from colorama import Fore, Style
    from colorama import init as colorama_init  # type: ignore

    colorama_init()

    def colour(txt: str, col: str) -> str:  # noqa: D401 - short lambda‑style fn
        return safe_format("{col}{txt}{reset}", col=col, txt=txt, reset=Style.RESET_ALL)

except ImportError:  # colour optional - silently degrade

    class Fore:  # type: ignore
        RED = ""
        GREEN = ""
        YELLOW = ""
        CYAN = ""
        MAGENTA = ""
        RESET = ""

    class Style:  # type: ignore - dummy placeholder
        RESET_ALL = ""

    def colour(txt: str, col: str) -> str:  # noqa: D401
        return txt


# ──────────────────────────────────────────────────────────────────────────────
# Logging setup - verbose by default, override with --quiet
# ──────────────────────────────────────────────────────────────────────────────
# Initialize with INFO level by default, will be adjusted based on CLI args
# Only setup logging if no handlers exist (avoid overriding CLI setup)
if not logging.getLogger().handlers:
    setup_logging(level=logging.INFO, log_file="vfio_diagnostics.log")
log = get_logger("vfio-assist")


# ──────────────────────────────────────────────────────────────────────────────
# Boot‑loader discovery + kernel‑arg helpers (drop‑in)
# ──────────────────────────────────────────────────────────────────────────────
class Boot(Enum):
    GRUB2_LEGACY = auto()  # /boot/grub/grub.cfg
    GRUB2_MODERN = auto()  # /boot/grub2/grub.cfg
    GRUBBY = auto()  # grubby helper (RHEL/Fedora family)
    SYSTEMD_BOOT = auto()  # /boot/loader/
    UNKNOWN = auto()


def _detect_boot() -> Boot:
    if shutil.which("grubby"):
        return Boot.GRUBBY
    if Path("/boot/loader/entries").exists():
        return Boot.SYSTEMD_BOOT
    if Path("/boot/grub2/grub.cfg").exists():
        return Boot.GRUB2_MODERN
    if Path("/boot/grub/grub.cfg").exists():
        return Boot.GRUB2_LEGACY
    return Boot.UNKNOWN


def _dedup(existing: str, new: Tuple[str, ...]) -> str:
    parts = existing.strip('"').split()
    for arg in new:
        if arg not in parts:
            parts.append(arg)
    return '"' + " ".join(parts) + '"'


def _sed_update(file: str, args: Tuple[str, ...]) -> str:
    # gnu‑sed command that inserts args exactly once
    return (
        r"""sudo sed -i -E """
        r"""'s/^(GRUB_CMDLINE_LINUX(_DEFAULT)?=)("(.*)")/\1"""
        + _dedup(r"\3", args)
        + r"/' "
        "" + shlex.quote(file)
    )


def _cmds_for_args(args: Tuple[str, ...]) -> list[str]:
    boot = _detect_boot()
    joined = " ".join(args)

    if boot == Boot.GRUBBY:
        return [
            safe_format(
                "sudo grubby --update-kernel=ALL --args {args}",
                args=shlex.quote(joined),
            ),
            "sudo reboot",
        ]

    if boot == Boot.GRUB2_MODERN:
        return [
            _sed_update("/etc/default/grub", args),
            "sudo grub2-mkconfig -o /boot/grub2/grub.cfg",
            "sudo reboot",
        ]

    if boot == Boot.GRUB2_LEGACY:
        return [
            _sed_update("/etc/default/grub", args),
            "sudo grub-mkconfig -o /boot/grub/grub.cfg",
            "sudo reboot",
        ]

    if boot == Boot.SYSTEMD_BOOT:
        kcmd = Path("/etc/kernel/cmdline")
        if kcmd.exists():
            current = kcmd.read_text().strip().split()
            add = [a for a in args if a not in current]
            if add:
                return [
                    safe_format(
                        "echo {args} | sudo tee -a /etc/kernel/cmdline",
                        args=" ".join(add),
                    ),
                    "sudo kernelstub -A || sudo bootctl update",
                    "sudo reboot",
                ]
            return [
                "# kernel args already present – reboot if you just installed a new kernel"
            ]
        return ["# Could not locate /etc/kernel/cmdline – edit manually"]

    return [
        "# Unknown boot loader; append these once then reboot:",
        safe_format("# {args}", args=joined),
    ]


def _kernel_param_commands() -> list[str]:
    return _cmds_for_args(("intel_iommu=on", "amd_iommu=on", "iommu=pt"))


def _kernel_param_commands_with_acs() -> list[str]:
    return _cmds_for_args(
        (
            "intel_iommu=on",
            "amd_iommu=on",
            "iommu=pt",
            "pcie_acs_override=downstream,multifunction",
        )
    )


# ──────────────────────────────────────────────────────────────────────────────
# Data structures
# ──────────────────────────────────────────────────────────────────────────────
class Status(Enum):
    OK = "ok"
    WARNING = "warning"
    ERROR = "error"
    MISSING = "missing"


@dataclass
class Check:
    name: str
    status: Status
    message: str
    remediation: Optional[str] = None
    commands: Optional[List[str]] = None
    prefix: Optional[str] = None


@dataclass
class Report:
    overall: Status
    checks: List[Check]
    device_bdf: Optional[str] = None
    can_proceed: bool = False

    # Serialize for JSON output / CI integration
    def as_dict(self) -> dict:
        return {
            "overall": self.overall.value,
            "device_bdf": self.device_bdf,
            "can_proceed": self.can_proceed,
            "checks": [
                {
                    "name": c.name,
                    "status": c.status.value,
                    "message": c.message,
                    "remediation": c.remediation,
                    "commands": c.commands,
                }
                for c in self.checks
            ],
        }


# ──────────────────────────────────────────────────────────────────────────────
# Core diagnostic engine
# ──────────────────────────────────────────────────────────────────────────────
class Diagnostics:
    def __init__(self, device_bdf: Optional[str] = None):
        self.device_bdf = device_bdf
        self.checks: List[Check] = []

    # Public API --------------------------------------------------------------
    def run(self) -> Report:
        log_info_safe(
            log,
            "Starting VFIO diagnostics for device: {device}",
            prefix="VFIO",
            device=self.device_bdf or "system-wide",
        )
        self.checks.clear()

        try:
            # System-wide checks
            log_debug_safe(log, "Running system-wide VFIO checks", prefix="VFIO")
            self._check_linux()
            self._check_iommu_hw()
            self._check_kernel_params()
            self._check_modules()
            self._check_vfio_driver_path()

            # Device-specific checks
            if self.device_bdf:
                log_debug_safe(
                    log,
                    "Running device-specific checks for {device}",
                    prefix="VFIO",
                    device=self.device_bdf,
                )
                self._device_exists()
                self._device_iommu_group()
                self._device_driver_binding()
                self._device_node()
            else:
                log_debug_safe(
                    log,
                    "No device specified, skipping device-specific checks",
                    prefix="VFIO",
                )

            overall = self._overall()
            can_proceed = overall in (Status.OK, Status.WARNING)

            log_info_safe(
                log,
                safe_format(
                    "Diagnostics completed. Overall status: {status}, Can proceed: {can_proceed}",
                    status=overall.value,
                    can_proceed=can_proceed,
                ),
                prefix="VFIO",
            )
            return Report(overall, self.checks, self.device_bdf, can_proceed)

        except Exception as e:
            log_error_safe(
                log,
                safe_format(
                    "Unexpected error during diagnostics: {error}",
                    error=str(e),
                ),
                prefix="VFIO",
            )
            log_error_safe(
                log, "Full exception details available in logs", prefix="VFIO"
            )
            # Add an error check to indicate the diagnostic failure
            self._append(
                name="Diagnostic Engine",
                status=Status.ERROR,
                message=safe_format("Diagnostic engine failed: {error}", error=str(e)),
                remediation="Check system logs and retry",
            )
            return Report(Status.ERROR, self.checks, self.device_bdf, False)

    # Private helpers ---------------------------------------------------------
    @staticmethod
    def _path_exists(path: Union[str, Path]) -> bool:
        return Path(path).exists()

    def _append(self, **kw):  # tiny helper
        self.checks.append(Check(**kw))

    # Individual checks -------------------------------------------------------
    def _check_linux(self):
        """Check if running on Linux platform."""
        platform_name = platform.system().lower()
        log_debug_safe(
            log, "Detected platform: {platform}", prefix="VFIO", platform=platform_name
        )

        if platform_name == "linux":
            log_debug_safe(log, "Linux platform confirmed", prefix="VFIO")
            self._append(name="Platform", status=Status.OK, message="Linux detected")
        else:
            log_warning_safe(
                log,
                safe_format(
                    "Unsupported platform detected: {platform}", platform=platform_name
                ),
                prefix="VFIO",
            )
            self._append(
                name="Platform",
                status=Status.ERROR,
                message=safe_format(
                    "Unsupported OS: {os_name}", os_name=platform.system()
                ),
                remediation="Run on a Linux system with VFIO support",
            )

    def _check_iommu_hw(self):
        """Check for IOMMU hardware support in CPU."""
        log_debug_safe(log, "Checking IOMMU hardware support")

        try:
            cpuinfo_path = Path("/proc/cpuinfo")
            if not cpuinfo_path.exists():
                log_warning_safe(log, "/proc/cpuinfo not found")
                self._append(
                    name="IOMMU HW",
                    status=Status.WARNING,
                    message="/proc/cpuinfo not found - cannot verify IOMMU support",
                )
                return

            cpuinfo = cpuinfo_path.read_text()
            intel = "vmx" in cpuinfo and "ept" in cpuinfo
            amd = "svm" in cpuinfo and "npt" in cpuinfo

            log_debug_safe(
                log,
                safe_format(
                    "CPU flags check - Intel VT-d: {intel}, AMD-Vi: {amd}",
                    intel=intel,
                    amd=amd,
                ),
                prefix="VFIO",
            )

            if intel or amd:
                cpu_type = "Intel VT-d" if intel else "AMD-Vi"
                log_debug_safe(
                    log,
                    safe_format(
                        "IOMMU hardware support confirmed: {cpu_type}",
                        cpu_type=cpu_type,
                    ),
                    prefix="VFIO",
                )
                self._append(
                    name="IOMMU HW",
                    status=Status.OK,
                    message="VT‑d / AMD‑Vi supported by CPU",
                )
            else:
                log_warning_safe(log, "IOMMU hardware flags not found in CPU info")
                self._append(
                    name="IOMMU HW",
                    status=Status.WARNING,
                    message="CPU flags missing VT‑d/AMD‑Vi - maybe disabled in BIOS",
                    remediation="Enable IOMMU in firmware setup (VT‑d, AMD‑Vi) or echo 1 > /sys/module/vfio/parameters/enable_unsafe_noiommu_mode if you can't in BIOS (unsafe)",
                )
        except Exception as e:
            log_error_safe(
                log, "Failed to check IOMMU hardware support: {error}", error=str(e)
            )
            log_error_safe(log, "Full exception details available in logs")
            self._append(
                name="IOMMU HW",
                status=Status.WARNING,
                message=safe_format(
                    "Could not parse /proc/cpuinfo: {error}", error=str(e)
                ),
            )

    def _check_kernel_params(self):
        """Check kernel command line for IOMMU parameters."""
        log_debug_safe(log, "Checking kernel command line parameters")

        try:
            cmdline_path = Path("/proc/cmdline")
            if not cmdline_path.exists():
                log_error_safe(log, "/proc/cmdline not found")
                self._append(
                    name="Kernel cmdline",
                    status=Status.ERROR,
                    message="/proc/cmdline not found",
                )
                return

            cmdline = cmdline_path.read_text().strip()
            log_debug_safe(
                log,
                safe_format("Kernel cmdline: {cmdline}", cmdline=cmdline),
                prefix="VFIO",
            )

            iommu_params = ["intel_iommu=on", "amd_iommu=on", "iommu=pt", "iommu=on"]
            found_params = [param for param in iommu_params if param in cmdline]

            # Check for ACS bypass parameters
            acs_params = [
                "pcie_acs_override=downstream",
                "pcie_acs_override=multifunction",
                "pcie_acs_override=downstream,multifunction",
            ]
            found_acs = [param for param in acs_params if param in cmdline]

            log_debug_safe(
                log,
                safe_format("Found IOMMU parameters: {params}", params=found_params),
                prefix="VFIO",
            )
            log_debug_safe(
                log,
                safe_format(
                    "Found ACS override parameters: {params}", params=found_acs
                ),
                prefix="VFIO",
            )

            if found_params:
                log_debug_safe(log, "IOMMU enabled in kernel cmdline", prefix="VFIO")
                message = safe_format(
                    "IOMMU enabled in cmdline: {params}", params=", ".join(found_params)
                )
                if found_acs:
                    # Check if ACS override is actually supported
                    acs_supported = self._test_acs_override_support()
                    if acs_supported:
                        message += safe_format(
                            ", ACS override: {acs_params}",
                            acs_params=", ".join(found_acs),
                        )
                    else:
                        message += ", ACS override params present but kernel doesn't support them"

                self._append(
                    name="Kernel cmdline",
                    status=Status.OK,
                    message=message,
                )

                # Check if ACS bypass might be needed
                if not found_acs and self.device_bdf:
                    self._check_acs_bypass_need()
            else:
                log_warning_safe(
                    log, "No IOMMU parameters found in kernel cmdline", prefix="VFIO"
                )
                fix = "Enable intel_iommu=on and/or amd_iommu=on iommu=pt in grub"
                self._append(
                    name="Kernel cmdline",
                    status=Status.ERROR,
                    message="IOMMU not present in kernel parameters",
                    remediation=fix,
                    commands=_kernel_param_commands(),
                )
        except Exception as e:
            log_error_safe(
                log,
                safe_format(
                    "Failed to check kernel parameters: {error}.", error=str(e)
                ),
                prefix="VFIO",
            )
            self._append(
                name="Kernel cmdline",
                status=Status.ERROR,
                message=safe_format(
                    "Failed to read /proc/cmdline: {error}", error=str(e)
                ),
            )

    def _check_modules(self):
        """Check if required VFIO kernel modules are loaded."""
        log_debug_safe(log, "Checking VFIO kernel modules")

        required = ["vfio", "vfio_pci", "vfio_iommu_type1"]
        missing = []
        loaded = []

        for module in required:
            module_path = safe_format("/sys/module/{module}", module=module)
            if self._path_exists(module_path):
                loaded.append(module)
                log_debug_safe(
                    log,
                    safe_format("Module {module} is loaded", module=module),
                    prefix="VFIO",
                )
            else:
                missing.append(module)
                log_debug_safe(
                    log,
                    safe_format("Module {module} is missing", module=module),
                    prefix="VFIO",
                )

        log_debug_safe(
            log,
            safe_format(
                "Loaded modules: {loaded}, Missing modules: {missing}",
                loaded=loaded,
                missing=missing,
            ),
            prefix="VFIO",
        )

        if not missing:
            log_debug_safe(log, "All required VFIO modules are loaded", prefix="VFIO")
            self._append(
                name="Kernel modules",
                status=Status.OK,
                message=safe_format(
                    "All VFIO modules loaded: {modules}", modules=", ".join(loaded)
                ),
            )
        else:
            severity = Status.ERROR if len(missing) == len(required) else Status.WARNING
            log_warning_safe(
                log,
                "Missing VFIO modules: {missing} (severity: {severity})",
                missing=missing,
                severity=severity.value,
            )
            self._append(
                name="Kernel modules",
                status=severity,
                message=safe_format(
                    "Missing modules: {modules}", modules=", ".join(missing)
                ),
                remediation="Load required modules with modprobe",
                commands=[
                    safe_format("sudo modprobe {module}", module=m.replace("_", "-"))
                    for m in missing
                ],
            )

    def _check_vfio_driver_path(self):
        path = Path("/sys/bus/pci/drivers/vfio-pci")
        if path.exists():
            self._append(
                name="vfio-pci driver", status=Status.OK, message="vfio-pci registered"
            )
        else:
            self._append(
                name="vfio-pci driver",
                status=Status.ERROR,
                message="vfio-pci driver not present in sysfs",
                remediation="Ensure kernel has VFIO support compiled or module present",
                commands=["sudo modprobe vfio-pci"],
            )

    # Device‑specific ---------------------------------------------------------
    def _device_exists(self):
        try:
            path_manager = VFIOPathManager(self.device_bdf)
            if path_manager.device_path.exists():
                vendor_id, device_id = path_manager.get_vendor_device_id()
                # Add 0x prefix for display consistency with existing behavior
                vendor = f"0x{vendor_id}"
                device = f"0x{device_id}"
                self._append(
                    name="Device",
                    status=Status.OK,
                    message=safe_format(
                        "{bdf} ({vendor}:{device}) present",
                        bdf=self.device_bdf,
                        vendor=vendor,
                        device=device,
                    ),
                )
            else:
                self._append(
                    name="Device",
                    status=Status.ERROR,
                    message=safe_format("PCI device {bdf} not found", bdf=self.device_bdf),
                    remediation="Check BDF with lspci ‑D",
                )
        except Exception as e:
            self._append(
                name="Device",
                status=Status.ERROR,
                message=safe_format(
                    "Failed to read device information for {bdf}: {err}",
                    bdf=self.device_bdf,
                    err=e
                ),
                remediation="Check BDF with lspci ‑D",
            )

    def _device_iommu_group(self):
        group_link = Path(
            safe_format("/sys/bus/pci/devices/{bdf}/iommu_group", bdf=self.device_bdf)
        )
        log_debug_safe(
            log,
            "Checking IOMMU group link: {group_link}",
            group_link=group_link,
            prefix="VFIO",
        )

        if group_link.exists():
            try:
                group_target = os.readlink(group_link)
                group = os.path.basename(group_target)
                log_debug_safe(
                    log,
                    "IOMMU group link target: {group_target}, group: {group}",
                    group_target=group_target,
                    group=group,
                    prefix="VFIO",
                )

                # Check if the group directory exists
                group_dir = Path(
                    safe_format("/sys/kernel/iommu_groups/{group}", group=group)
                )
                if group_dir.exists():
                    # List devices in the group for debugging
                    devices_dir = group_dir / "devices"
                    if devices_dir.exists():
                        try:
                            devices = list(devices_dir.iterdir())
                            device_names = [d.name for d in devices]
                            log_debug_safe(
                                log,
                                safe_format(
                                    "Devices in IOMMU group {group}: {device_names}",
                                    group=group,
                                    device_names=device_names,
                                ),
                                prefix="VFIO",
                            )
                        except Exception as e:
                            log_debug_safe(
                                log,
                                safe_format(
                                    "Could not list devices in IOMMU group {group}: {error}",
                                    group=group,
                                    error=str(e),
                                ),
                                prefix="VFIO",
                            )

                self._append(
                    name="IOMMU group",
                    status=Status.OK,
                    message=safe_format("Group {group}", group=group),
                )
            except OSError as e:
                log_debug_safe(
                    log,
                    safe_format(
                        "Failed to read IOMMU group symlink: {error}",
                        error=str(e),
                    ),
                    prefix="VFIO",
                )
                self._append(
                    name="IOMMU group",
                    status=Status.ERROR,
                    message=safe_format(
                        "Failed to read IOMMU group symlink: {error}", error=str(e)
                    ),
                )
        else:
            # Check if device exists at all
            device_path = Path(
                safe_format("/sys/bus/pci/devices/{bdf}", bdf=self.device_bdf)
            )
            if device_path.exists():
                log_debug_safe(
                    log,
                    safe_format(
                        "Device {device} exists but has no IOMMU group",
                        device=self.device_bdf,
                    ),
                    prefix="VFIO",
                )
                self._append(
                    name="IOMMU group",
                    status=Status.ERROR,
                    message="Device exists but not in an IOMMU group - IOMMU disabled?",
                    prefix="VFIO",
                )
            else:
                log_debug_safe(
                    log,
                    safe_format(
                        "Device {device} does not exist in sysfs",
                        device=self.device_bdf,
                    ),
                    prefix="VFIO",
                )
                self._append(
                    name="IOMMU group",
                    status=Status.ERROR,
                    message=safe_format(
                        "Device {bdf} not found in sysfs", bdf=self.device_bdf
                    ),
                    prefix="VFIO",
                )

    def _device_driver_binding(self):
        if self.device_bdf is None:
            self._append(
                name="Driver",
                status=Status.ERROR,
                message="Cannot check driver binding without device BDF",
            )
            return

        link = Path(
            safe_format("/sys/bus/pci/devices/{bdf}/driver", bdf=self.device_bdf)
        )
        log_debug_safe(
            log,
            safe_format(
                "Checking driver binding for {device} at {link}",
                device=self.device_bdf,
                link=link,
            ),
            prefix="VFIO",
        )

        if link.exists():
            try:
                driver_target = os.readlink(link)
                driver = os.path.basename(driver_target)
                log_debug_safe(
                    log,
                    safe_format(
                        "Driver link target: {driver_target}, driver: {driver}",
                        driver_target=driver_target,
                        driver=driver,
                    ),
                    prefix="VFIO",
                )

                if driver == "vfio-pci":
                    self._append(
                        name="Driver",
                        status=Status.OK,
                        message="Already bound to vfio-pci",
                    )
                else:
                    log_debug_safe(
                        log,
                        safe_format(
                            "Device {device} bound to {driver}, needs rebinding to vfio-pci",
                            device=self.device_bdf,
                            driver=driver,
                        ),
                        prefix="VFIO",
                    )
                    self._append(
                        name="Driver",
                        status=Status.WARNING,
                        message=safe_format("Bound to {driver}", driver=driver),
                        remediation="Will need to rebind to vfio-pci",
                        commands=self._bind_commands(self.device_bdf, driver),
                    )
            except OSError as e:
                log_debug_safe(
                    log,
                    safe_format(
                        "Failed to read driver symlink for {device}: {error}",
                        device=self.device_bdf,
                        error=str(e),
                    ),
                    prefix="VFIO",
                )
                self._append(
                    name="Driver",
                    status=Status.ERROR,
                    message=safe_format(
                        "Failed to read driver symlink: {error}", error=str(e)
                    ),
                )
        else:
            log_debug_safe(
                log,
                safe_format(
                    "No driver bound to device {device}",
                    device=self.device_bdf,
                ),
                prefix="VFIO",
            )
            self._append(
                name="Driver",
                status=Status.WARNING,
                message="No driver bound",
                commands=self._bind_commands(self.device_bdf, None),
            )

    @staticmethod
    def _bind_commands(bdf: str, current: Optional[str]) -> List[str]:
        cmds: list[str] = [
            (
                safe_format(
                    "echo '{bdf}' | sudo tee /sys/bus/pci/devices/{bdf}/driver/unbind",
                    bdf=bdf,
                )
                if current
                else ""
            ),
            safe_format(
                "echo '{bdf}' | sudo tee /sys/bus/pci/drivers/vfio-pci/bind", bdf=bdf
            ),
        ]
        return [c for c in cmds if c]

    def _device_node(self):
        link = Path(
            safe_format("/sys/bus/pci/devices/{bdf}/iommu_group", bdf=self.device_bdf)
        )
        log_debug_safe(
            log,
            safe_format(
                "Checking VFIO device node for {device}",
                device=self.device_bdf,
            ),
            prefix="VFIO",
        )

        if not link.exists():
            log_debug_safe(
                log,
                safe_format(
                    "IOMMU group link does not exist for {device}",
                    device=self.device_bdf,
                ),
                prefix="VFIO",
            )
            return

        try:
            group_target = os.readlink(link)
            group = os.path.basename(group_target)
            log_debug_safe(
                log,
                safe_format(
                    "IOMMU group for {device}: {group}",
                    device=self.device_bdf,
                    group=group,
                ),
                prefix="VFIO",
            )

            node = Path(safe_format("/dev/vfio/{group}", group=group))
            log_debug_safe(log, "Checking VFIO node: {node}", node=node, prefix="VFIO")

            if node.exists():
                # Check node permissions and properties
                try:
                    stat_info = node.stat()
                    log_debug_safe(
                        log,
                        safe_format(
                            "VFIO node {node} permissions: {permissions}",
                            node=node,
                            permissions=oct(stat_info.st_mode),
                        ),
                        prefix="VFIO",
                    )
                except Exception as e:
                    log_debug_safe(
                        log,
                        safe_format(
                            "Could not stat VFIO node {node}: {error}",
                            node=node,
                            error=str(e),
                        ),
                        prefix="VFIO",
                    )

                self._append(
                    name="/dev/vfio node", status=Status.OK, message=node.as_posix()
                )
            else:
                log_debug_safe(
                    log, "VFIO node {node} does not exist", node=node, prefix="VFIO"
                )

                # Check if /dev/vfio directory exists
                vfio_dir = Path("/dev/vfio")
                if vfio_dir.exists():
                    try:
                        vfio_entries = list(vfio_dir.iterdir())
                        log_debug_safe(
                            log,
                            safe_format(
                                "Available VFIO entries: {entries}",
                                entries=[e.name for e in vfio_entries],
                            ),
                            prefix="VFIO",
                        )
                    except Exception as e:
                        log_debug_safe(
                            log,
                            safe_format(
                                "Could not list /dev/vfio entries: {error}",
                                error=str(e),
                            ),
                            prefix="VFIO",
                        )
                else:
                    log_debug_safe(
                        log, "/dev/vfio directory does not exist", prefix="VFIO"
                    )

                self._append(
                    name="/dev/vfio node",
                    status=Status.WARNING,
                    message=safe_format(
                        "{node} missing (will appear after binding)", node=str(node)
                    ),
                )
        except OSError as e:
            log_debug_safe(
                log,
                safe_format(
                    "Failed to read IOMMU group symlink for device node check: {error}",
                    error=str(e),
                ),
                prefix="VFIO",
            )
            self._append(
                name="/dev/vfio node",
                status=Status.ERROR,
                message=safe_format(
                    "Failed to determine VFIO node: {error}", error=str(e)
                ),
            )

    def _check_acs_bypass_need(self):
        """Check if ACS bypass might be needed for proper IOMMU group isolation."""
        if not self.device_bdf:
            return

        log_debug_safe(
            log,
            safe_format(
                "Checking if ACS bypass might be needed for {device}",
                device=self.device_bdf,
            ),
            prefix="VFIO",
        )

        try:
            # Get the target device's IOMMU group
            group_link = Path(f"/sys/bus/pci/devices/{self.device_bdf}/iommu_group")
            if not group_link.exists():
                return

            group_target = os.readlink(group_link)
            group = os.path.basename(group_target)

            # Check how many devices are in this IOMMU group
            group_devices_path = Path(
                safe_format("/sys/kernel/iommu_groups/{group}/devices", group=group)
            )
            if not group_devices_path.exists():
                return

            devices = list(group_devices_path.iterdir())
            device_count = len(devices)

            log_debug_safe(
                log,
                safe_format(
                    "Device {device} is in IOMMU group {group} with {count} devices",
                    device=self.device_bdf,
                    group=group,
                    count=device_count,
                ),
                prefix="VFIO",
            )

            if device_count > 1:
                # Multiple devices in the same group - ACS bypass likely needed
                device_names = [d.name for d in devices]

                # Check if this looks like PCIe bridge isolation issue
                bridges = [d for d in device_names if self._is_pci_bridge(d)]

                other_devices = [d for d in device_names if d != self.device_bdf]
                warning_msg = safe_format(
                    "Device shares IOMMU group {group} with {count} other device(s): {devices}",
                    group=group,
                    count=device_count - 1,
                    devices=", ".join(other_devices),
                )

                if bridges:
                    warning_msg += safe_format(
                        ". PCIe bridges detected: {bridges}", bridges=", ".join(bridges)
                    )

                # Check if ACS override is actually supported by testing the parameter
                acs_supported = self._test_acs_override_support()

                if acs_supported:
                    remediation = "Enable ACS bypass with pcie_acs_override=downstream,multifunction"
                    commands = _kernel_param_commands_with_acs()
                else:
                    remediation = "Device isolation requires ACS override patch (not available in this kernel)"
                    commands = [
                        "# ACS override is NOT supported in this kernel",
                        "# General solutions:",
                        "# 1. Check if your distribution provides ACS-patched kernels",
                        "# 2. Build custom kernel with ACS patch",
                        "# 3. Switch to distribution with ACS support (Proxmox, Arch AUR)",
                        "# 4. Check BIOS for ACS/PCIe override options",
                        "# 5. Consider different hardware topology",
                    ]

                self._append(
                    name="ACS bypass",
                    status=Status.WARNING,
                    message=warning_msg,
                    remediation=remediation,
                    commands=commands,
                )
            else:
                log_debug_safe(
                    log,
                    "Device {device} is isolated in its own IOMMU group - ACS bypass not needed",
                    device=self.device_bdf,
                    prefix="VFIO",
                )
                self._append(
                    name="ACS bypass",
                    status=Status.OK,
                    message=safe_format(
                        "Device isolated in IOMMU group {group}", group=group
                    ),
                )

        except Exception as e:
            log_debug_safe(
                log,
                "Failed to check ACS bypass need: {error}",
                error=str(e),
                prefix="VFIO",
            )
            self._append(
                name="ACS bypass",
                status=Status.WARNING,
                message=safe_format(
                    "Could not determine if ACS bypass is needed: {error}", error=str(e)
                ),
            )

    def _is_pci_bridge(self, bdf: str) -> bool:
        """Check if a device is a PCIe bridge by examining its class code."""
        try:
            class_path = Path(safe_format("/sys/bus/pci/devices/{bdf}/class", bdf=bdf))
            if class_path.exists():
                class_code = class_path.read_text().strip()
                # PCIe bridges typically have class code 0x060400 or 0x060401
                return class_code.startswith("0x0604")
        except Exception:
            pass
        return False

    def _get_kernel_version(self) -> Optional[str]:
        """Get the current kernel version string."""
        try:
            result = subprocess.run(
                ["uname", "-r"], capture_output=True, text=True, timeout=5
            )
            if result.returncode == 0:
                return result.stdout.strip()
        except Exception:
            pass
        return None

    def _test_acs_override_support(self) -> bool:
        """Test if the current kernel supports ACS override by checking if the parameter exists."""
        # 1. live sysfs parameter
        if Path("/sys/module/pci/parameters/pcie_acs_override").exists():
            return True

        # 2. kernel build config
        kcfg = Path(safe_format("/boot/config-{release}", release=os.uname().release))
        try:
            if kcfg.exists():
                for line in kcfg.read_text().splitlines():
                    if line.startswith("CONFIG_PCI_QUIRKS="):
                        return line.rstrip().endswith("y")
        except PermissionError:
            pass

        # 3. modular pci driver parameters
        try:
            out = subprocess.run(
                ["modinfo", "-p", "pci"],
                capture_output=True,
                text=True,
                timeout=5,
            )
            if out.returncode == 0:
                return "pcie_acs_override" in out.stdout
        except (subprocess.SubprocessError, FileNotFoundError):
            pass

        return False

    def _is_ubuntu(self) -> bool:
        """Check if we're running on Ubuntu."""
        try:
            # Check /etc/os-release
            os_release = Path("/etc/os-release")
            if os_release.exists():
                content = os_release.read_text()
                return "ID=ubuntu" in content.lower() or "ubuntu" in content.lower()

            # Fallback: check /etc/lsb-release
            lsb_release = Path("/etc/lsb-release")
            if lsb_release.exists():
                content = lsb_release.read_text()
                return "ubuntu" in content.lower()

        except Exception:
            pass
        return False

    def _overall(self) -> Status:
        if any(c.status == Status.ERROR for c in self.checks):
            return Status.ERROR
        if any(c.status == Status.WARNING for c in self.checks):
            return Status.WARNING
        return Status.OK


# ──────────────────────────────────────────────────────────────────────────────
# Remediation script generator
# ──────────────────────────────────────────────────────────────────────────────
def remediation_script(report: Report) -> str:
    lines = [
        "#!/bin/bash",
        "# Auto‑generated VFIO remediation script - review before running!",
        "set -euo pipefail",
        "echo '>> VFIO remediation started'",
    ]
    for c in report.checks:
        if c.commands and c.status in (Status.ERROR, Status.WARNING):
            lines.append(f"# — {c.name}")
            lines += c.commands
            lines.append("")
    lines += [
        "echo '>> Remediation completed. Reboot if kernel params changed.'",
    ]
    return "\n".join(lines) + "\n"


# ──────────────────────────────────────────────────────────────────────────────
# Human‑readable renderer
# ──────────────────────────────────────────────────────────────────────────────
SYMBOLS = {
    Status.OK: colour("✔", Fore.GREEN),
    Status.WARNING: colour("⚠", Fore.YELLOW),
    Status.ERROR: colour("✖", Fore.RED),
    Status.MISSING: colour("?", Fore.MAGENTA),
}


def render(report: Report):
    print(colour("\n=== VFIO DIAGNOSTIC REPORT ===", Fore.CYAN))
    print(
        safe_format(
            "Overall: {symbol} {status}",
            symbol=SYMBOLS[report.overall],
            status=report.overall.value.upper(),
        )
    )
    if report.device_bdf:
        print(safe_format("Device : {device}", device=report.device_bdf))
    print(
        safe_format(
            "Proceed: {proceed}\n", proceed="yes" if report.can_proceed else "no"
        )
    )
    for ck in report.checks:
        sym = SYMBOLS.get(ck.status, "?")
        print(
            safe_format(
                "{symbol} {name}: {message}",
                symbol=sym,
                name=ck.name,
                message=ck.message,
            )
        )
        if ck.remediation:
            print(safe_format("   · {remediation}", remediation=ck.remediation))
    print()


# ──────────────────────────────────────────────────────────────────────────────
# CLI entry‑point
# ──────────────────────────────────────────────────────────────────────────────


def parse_args(argv: list[str]) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        prog="vfio-assist",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        description=textwrap.dedent(
            """\
            Smart VFIO diagnostics & remediation tool.
            --------------------------------------------------------------------
            Most commands require *root* - either run with sudo or prefix
            privileged sub‑steps with sudo when prompted.
            """,
        ),
    )

    sub = parser.add_subparsers(dest="cmd", required=True)

    common = argparse.ArgumentParser(add_help=False)
    common.add_argument(
        "-d", "--device", dest="device_bdf", help="Target PCIe BDF (0000:01:00.0)"
    )
    common.add_argument(
        "--quiet", action="store_true", help="Silence info logs (warnings still shown)"
    )

    sub.add_parser(
        "diagnose", parents=[common], help="Run diagnostics and print report"
    )
    fix_p = sub.add_parser(
        "fix", parents=[common], help="Attempt automatic remediation"
    )
    fix_p.add_argument(
        "-y", "--yes", action="store_true", help="Run fixes without confirmation"
    )

    sub.add_parser(
        "script", parents=[common], help="Output a shell script that would fix issues"
    )
    sub.add_parser(
        "json", parents=[common], help="Machine‑readable JSON report (stdout)"
    )

    return parser.parse_args(argv)


def main(argv: list[str] | None = None):
    args = parse_args(argv or sys.argv[1:])
    if args.quiet:
        logging.getLogger().setLevel(logging.WARNING)

    diag = Diagnostics(args.device_bdf)
    report = diag.run()

    if args.cmd == "diagnose":
        render(report)
        sys.exit(0 if report.can_proceed else 1)

    if args.cmd == "script":
        script = remediation_script(report)
        print(script, end="")
        return

    if args.cmd == "json":
        print(json.dumps(report.as_dict(), indent=2))
        return

    if args.cmd == "fix":
        if report.overall == Status.OK:
            render(report)
            print(colour("System already VFIO‑ready - nothing to do", Fore.GREEN))
            return

        script_text = remediation_script(report)
        temp = Path("/tmp/vfio_fix.sh")
        temp.write_text(script_text)
        temp.chmod(0o755)
        render(report)
        print(
            colour(
                safe_format("Remediation script written to {path}", path=str(temp)),
                Fore.CYAN,
            )
        )

        if not args.yes:
            confirm = input("Run remediation script now? [y/N]: ").strip().lower()
            if confirm not in ("y", "yes"):
                print("Aborted.")
                return
        log_info_safe(log, "Executing remediation script (requires root)…")
        try:
            subprocess.run(["sudo", str(temp)], check=True)
        except subprocess.CalledProcessError as e:
            log_error_safe(log, "Script failed: {error}", error=str(e))
            sys.exit(1)

        # Re‑run diagnostics after remediation
        print(colour("\nRe‑running diagnostics after remediation…", Fore.CYAN))
        new_report = Diagnostics(args.device_bdf).run()
        render(new_report)
        sys.exit(0 if new_report.can_proceed else 1)


if __name__ == "__main__":  # pragma: no cover
    main()
