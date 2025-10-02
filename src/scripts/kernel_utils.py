#!/usr/bin/env python3
"""
Kernel utilities for driver analysis.

This module provides utilities for working with kernel source code,
including extraction of kernel source packages and driver module resolution.

Prerequisites:
    - modprobe (Linux module utilities)
    - ripgrep (rg) for fast text searching (optional but recommended)
    - Linux kernel source packages in /usr/src/

Expected kernel layout:
    /usr/src/linux-source-*.tar.* - Compressed kernel source
    /usr/src/linux-source-*/ - Extracted kernel source directory
"""

import os
import pathlib
import platform
import subprocess
import tarfile
from typing import Any, Dict, List, Optional, Union

from src.log_config import get_logger
# Project logging & safe string formatting utilities (mandatory per repo style)
from src.string_utils import (log_debug_safe, log_error_safe, log_info_safe,
                              log_warning_safe, safe_format)
from src.utils.unified_context import \
    TemplateObject  # For context compatibility

logger = get_logger(__name__)


def is_linux() -> bool:
    """Return True if current platform is Linux."""
    return platform.system().lower() == "linux"


def check_linux_requirement(operation: str) -> None:
    """Validate Linux-only operation; raise RuntimeError if not Linux."""
    if not is_linux():
        msg = safe_format(
            (
                "{op} requires Linux. Current platform: {plat}. "
                "This functionality is only available on Linux systems."
            ),
            op=operation,
            plat=platform.system(),
        )
        log_error_safe(logger, msg, prefix="KERNEL")
        raise RuntimeError(msg)


def run_command(cmd: str) -> str:
    """Run shell command and return stdout; raise RuntimeError on failure."""
    try:
        log_debug_safe(
            logger,
            safe_format("Executing command: {c}", c=cmd),
            prefix="KERNEL",
        )
        result = subprocess.run(
            cmd, shell=True, check=True, capture_output=True, text=True
        )
        return result.stdout
    except subprocess.CalledProcessError as e:
        msg = safe_format(
            "Command failed: {cmd}\nExit code: {code}\nError output: {err}",
            cmd=cmd,
            code=e.returncode,
            err=e.stderr.strip(),
        )
        log_error_safe(logger, msg, prefix="KERNEL")
        raise RuntimeError(msg) from e


def setup_debugfs() -> None:
    """
    Set up debugfs mount for kernel debugging operations.

    This function ensures that debugfs is properly mounted at /sys/kernel/debug,
    which is required for kernel debugging and analysis operations.

    Raises:
        RuntimeError: If debugfs setup fails or not running on Linux
    """
    check_linux_requirement("Debugfs setup")

    try:
        # First, check if /sys/kernel exists
        try:
            result = subprocess.run(
                "ls -la /sys/kernel", shell=True, capture_output=True, text=True
            )
            if result.returncode != 0:
                raise RuntimeError(
                    safe_format(
                        (
                            "/sys/kernel directory not accessible. "
                            "Exit code: {code}, Error: {err}"
                        ),
                        code=result.returncode,
                        err=result.stderr.strip(),
                    )
                )
        except Exception as e:
            raise RuntimeError(
                safe_format("Cannot access /sys/kernel: {e}", e=e)
            ) from e

        # Check current user privileges
        try:
            result = subprocess.run("id -u", shell=True, capture_output=True, text=True)
            uid = result.stdout.strip()

            # Check if we can write to /sys/kernel/debug (privileged container check)
            can_access_sys = False
            try:
                # Test if we can access privileged paths
                test_path = "/sys/kernel/debug"
                if os.path.exists(test_path):
                    can_access_sys = os.access(test_path, os.W_OK)
                else:
                    # Try to create the directory to test privileges
                    try:
                        os.makedirs(test_path, exist_ok=True)
                        can_access_sys = True
                    except PermissionError:
                        can_access_sys = False
            except Exception:
                can_access_sys = False

            # If not root and can't access sys, try with sudo
            if uid != "0" and not can_access_sys:
                # Check if sudo is available
                sudo_available = (
                    subprocess.run(
                        "which sudo", shell=True, capture_output=True
                    ).returncode
                    == 0
                )

                if not sudo_available:
                    raise RuntimeError(
                        safe_format(
                            (
                                "Debugfs setup requires root privileges. "
                                "Current UID: {uid}. Run with sudo/root."
                            ),
                            uid=uid,
                        )
                    )
        except Exception as e:
            raise RuntimeError(
                safe_format("Cannot determine user privileges: {e}", e=e)
            ) from e

        # Check if debugfs is already mounted
        try:
            mount_output = run_command("mount | grep debugfs")
            if "/sys/kernel/debug" in mount_output:
                return  # Already mounted
        except RuntimeError:
            # grep returns non-zero if no matches found, which is expected
            # Attempt to mount debugfs directly if grep fails
            log_debug_safe(
                logger, "No debugfs mount found, attempting to mount", prefix="KERNEL"
            )
            try:
                # Try mounting debugfs without check=True to avoid failing on mount errors
                result = subprocess.run(
                    ["mount", "-t", "debugfs", "none", "/sys/kernel/debug"],
                    check=False,
                    capture_output=True,
                    text=True,
                )
                if result.returncode == 0:
                    log_info_safe(
                        logger,
                        "Successfully mounted debugfs at /sys/kernel/debug",
                        prefix="KERNEL",
                    )
                    return
                else:
                    log_warning_safe(
                        logger,
                        safe_format(
                            "Failed to mount debugfs (non-fatal): {err}",
                            err=result.stderr.strip(),
                        ),
                        prefix="KERNEL",
                    )
            except Exception as mount_e:
                log_warning_safe(
                    logger,
                    safe_format(
                        "Debugfs mount attempt failed (non-fatal): {e}", e=mount_e
                    ),
                    prefix="KERNEL",
                )
            pass

        # Check if debugfs is supported in kernel
        try:
            result = subprocess.run(
                "grep -q debugfs /proc/filesystems", shell=True, capture_output=True
            )
            if result.returncode != 0:
                raise RuntimeError(
                    (
                        "debugfs filesystem not supported by kernel. "
                        "Ensure debugfs is compiled in or available as a module."
                    )
                )
        except Exception as e:
            raise RuntimeError(
                safe_format("Cannot check debugfs kernel support: {e}", e=e)
            ) from e

        # Create the debug directory if it doesn't exist
        try:
            run_command("mkdir -p /sys/kernel/debug")
        except RuntimeError as e:
            # Provide more specific error information
            if "Permission denied" in str(e):
                raise RuntimeError(
                    safe_format(
                        (
                            "Permission denied creating /sys/kernel/debug. "
                            "Root privileges required. Original error: {e}"
                        ),
                        e=e,
                    )
                ) from e
            elif "Read-only file system" in str(e):
                raise RuntimeError(
                    safe_format(
                        (
                            "/sys filesystem is read-only. Cannot create "
                            "debugfs mount point. Original error: {e}"
                        ),
                        e=e,
                    )
                ) from e
            else:
                raise RuntimeError(
                    safe_format("Failed to create /sys/kernel/debug: {e}", e=e)
                ) from e

        # Mount debugfs
        try:
            run_command("mount -t debugfs debugfs /sys/kernel/debug")
        except RuntimeError as e:
            error_str = str(e).lower()
            if "already mounted" in error_str or "debugfs already mounted" in error_str:
                # This is actually a success case - debugfs is already available
                return
            elif "permission denied" in error_str:
                raise RuntimeError(
                    safe_format(
                        (
                            "Permission denied mounting debugfs. Root "
                            "privileges required. Original error: {e}"
                        ),
                        e=e,
                    )
                ) from e
            else:
                raise RuntimeError(
                    safe_format("Failed to mount debugfs: {e}", e=e)
                ) from e

    except Exception as e:
        raise RuntimeError(safe_format("Failed to setup debugfs: {e}", e=e)) from e


def ensure_kernel_source() -> Optional[pathlib.Path]:
    """
    Extract /usr/src/linux-source-*.tar.* if not untarred yet.

    Returns:
        Path to extracted kernel source directory, or None if not found

    Raises:
        RuntimeError: If extraction fails
    """
    # Find the source package
    src_path = pathlib.Path("/usr/src")

    # In real execution, this will be an iterator
    # In tests, this is mocked to return a list directly
    glob_result = src_path.glob("linux-source-*.tar*")

    # Get the first source package
    # This approach works with both real iterators and mocked lists
    src_pkg = None
    for pkg in glob_result:
        src_pkg = pkg
        break

    if not src_pkg:
        return None

    untar_dir = src_pkg.with_suffix("").with_suffix("")  # strip .tar.xz
    if not (untar_dir / "drivers").exists():
        try:
            with tarfile.open(src_pkg) as t:
                # Security: validate tar members before extraction

                def is_safe_path(member: tarfile.TarInfo, target_dir: str) -> bool:
                    # Get the absolute path where the member would be extracted
                    member_path = os.path.join(target_dir, member.name)
                    real_target = os.path.realpath(target_dir)
                    real_member = os.path.realpath(member_path)
                    # Ensure the member path is within the target directory
                    return real_member.startswith(real_target + os.sep)

                safe_members = [
                    m for m in t.getmembers() if is_safe_path(m, "/usr/src")
                ]
                t.extractall("/usr/src", members=safe_members)
        except Exception as e:
            raise RuntimeError(
                safe_format("Failed to extract kernel source: {e}", e=e)
            ) from e

    return untar_dir


def resolve_driver_module(vendor_id: str, device_id: str) -> str:
    """
    Resolve driver module name from vendor and device IDs.

    Args:
        vendor_id: 4-digit hex vendor ID (e.g., "8086")
        device_id: 4-digit hex device ID (e.g., "1533")

    Returns:
        Driver module name (e.g., "snd_hda_intel")

    Raises:
        RuntimeError: If no driver module found or command fails
    """
    check_linux_requirement("Driver module resolution")

    try:
        alias_line = run_command(
            safe_format(
                "modprobe --resolve-alias pci:v0000{vid}d0000{did}*",
                vid=vendor_id,
                did=device_id,
            )
        ).splitlines()

        if not alias_line:
            raise RuntimeError(
                safe_format(
                    (
                        "No driver module found for VID:DID {vid}:{did} "
                        "in modules.alias"
                    ),
                    vid=vendor_id,
                    did=device_id,
                )
            )

        return alias_line[-1].strip()  # e.g. snd_hda_intel
    except RuntimeError:
        raise
    except Exception as e:
        raise RuntimeError(
            safe_format("Failed to resolve driver module: {e}", e=e)
        ) from e


def find_driver_sources(
    kernel_source_dir: pathlib.Path, driver_name: str
) -> List[pathlib.Path]:
    """
    Find source files for a specific driver in the kernel source tree.

    Args:
        kernel_source_dir: Path to the kernel source directory
        driver_name: Name of the driver module to find

    Returns:
        List of paths to source files related to the driver
    """
    # First, try to find files directly matching the driver name
    src_files = list(kernel_source_dir.rglob(f"{driver_name}*.c")) + list(
        kernel_source_dir.rglob(f"{driver_name}*.h")
    )

    # If no direct matches, try to find files containing the driver name in
    # their content
    if not src_files:
        # Look in drivers directory first as it's most likely location
        drivers_dir = kernel_source_dir / "drivers"
        if drivers_dir.exists():
            candidates: List[pathlib.Path] = []
            for ext in [".c", ".h"]:
                for file_path in drivers_dir.rglob(f"*{ext}"):
                    try:
                        content = file_path.read_text(errors="ignore")
                        if driver_name in content:
                            candidates.append(file_path)
                            # Limit to prevent excessive searching
                            if len(candidates) >= 20:
                                break
                    except Exception:
                        continue
            src_files = candidates

    return src_files


# ---------------------------------------------------------------------------
# Context Enrichment Helpers
# ---------------------------------------------------------------------------


# Expose enrich_context_with_driver for test and module compatibility
from src.utils.context_driver_enrichment import enrich_context_with_driver

# Retain the usage wrapper for legacy/internal use if needed


def enrich_context_with_driver_usage(*args, **kwargs):
    return enrich_context_with_driver(*args, **kwargs)
