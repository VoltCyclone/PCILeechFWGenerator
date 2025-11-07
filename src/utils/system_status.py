#!/usr/bin/env python3
"""
System Status Utilities

Common utilities for checking system status, software availability,
and hardware support across CLI and TUI interfaces. This module provides
standardized system status checks that can be used by both the TUI and CLI
components of PCILeechFWGenerator.
"""

import asyncio
import os
import platform
import shutil
import subprocess
from functools import wraps
from typing import Any, Callable, Dict

try:
    import psutil
except ImportError:
    psutil = None  # Type to handle missing dependency more gracefully


def status_check(func: Callable[..., Any]) -> Callable[..., Any]:
    """
    Decorator for status check functions that handles common error handling pattern.

    Converts exceptions to standardized error response format:
    {"status": "error", "message": "FunctionName failed: {exception}"}

    Args:
        func: The function to decorate

    Returns:
        Decorated function that handles exceptions
    """

    @wraps(func)
    async def wrapper(*args, **kwargs):
        try:
            return await func(*args, **kwargs)
        except Exception as e:
            return {"status": "error", "message": f"{func.__name__} failed: {e}"}

    return wrapper


async def run_command(command: str) -> subprocess.CompletedProcess:
    """
    Run a command asynchronously.

    Args:
        command: Command to run

    Returns:
        CompletedProcess with stdout and stderr
    """
    try:
        process = await asyncio.create_subprocess_shell(
            command, stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE
        )
        stdout, stderr = await process.communicate()

        return subprocess.CompletedProcess(
            args=command,
            returncode=process.returncode or 0,
            stdout=stdout.decode(),
            stderr=stderr.decode(),
        )
    except Exception as e:
        return subprocess.CompletedProcess(
            args=command, returncode=1, stdout="", stderr=str(e)
        )


@status_check
async def check_podman_status() -> Dict[str, Any]:
    """
    Check Podman availability and status.

    Returns:
        Dictionary with status information
    """
    if not shutil.which("podman"):
        return {"status": "not_found", "message": "Podman not found in PATH"}

    # Check if Podman is running
    result = await run_command("podman version --format json")
    if result.returncode == 0:
        return {"status": "ready", "message": "Podman available"}
    else:
        return {"status": "error", "message": "Podman not responding"}


@status_check
async def check_vivado_status() -> Dict[str, Any]:
    """
    Check Vivado availability.

    Returns:
        Dictionary with Vivado status information
    """
    # Import vivado_utils from vivado_handling package
    try:
        from src.vivado_handling.vivado_utils import find_vivado_installation

        # Use the utility function to find Vivado
        vivado_info = find_vivado_installation()
        if vivado_info:
            return {
                "status": "detected",
                "version": vivado_info["version"],
                "path": vivado_info["path"],
                "executable": vivado_info["executable"],
            }
        else:
            return {"status": "not_found", "message": "Vivado not detected"}

    except ImportError:
        # Fall back to manual discovery if import fails
        # Check common installation paths
        search_paths = []
        system = platform.system().lower()

        if system == "linux":
            search_paths = [
                "/opt/Xilinx/Vivado",
                "/tools/Xilinx/Vivado",
                "/usr/local/Xilinx/Vivado",
                os.path.expanduser("~/Xilinx/Vivado"),
            ]
        elif system == "darwin":  # macOS
            search_paths = [
                "/Applications/Xilinx/Vivado",
                os.path.expanduser("~/Xilinx/Vivado"),
            ]
        else:
            # Windows support removed as per requirements
            return {"status": "not_supported", "message": "Windows not supported"}

        # Check each path for Vivado installation
        for base_path in search_paths:
            if os.path.exists(base_path) and os.path.isdir(base_path):
                try:
                    # Look for version directories (e.g., 2023.1)
                    versions = [
                        d
                        for d in os.listdir(base_path)
                        if d[0].isdigit() and os.path.isdir(os.path.join(base_path, d))
                    ]
                    if versions:
                        # Sort versions and use the latest
                        latest_version = sorted(versions)[-1]
                        vivado_dir = os.path.join(base_path, latest_version)

                        # Find bin directory and executable
                        bin_dir = os.path.join(vivado_dir, "bin")
                        if os.path.exists(bin_dir):
                            vivado_exe = os.path.join(bin_dir, "vivado")
                            if os.path.isfile(vivado_exe):
                                # Try to get version using discovered executable
                                result = await run_command(f'"{vivado_exe}" -version')
                                if result.returncode == 0:
                                    version = extract_vivado_version(result.stdout)
                                    return {
                                        "status": "detected",
                                        "version": version,
                                        "path": vivado_dir,
                                        "executable": vivado_exe,
                                    }
                                else:
                                    # Fall back to version from path
                                    return {
                                        "status": "detected",
                                        "version": latest_version,
                                        "path": vivado_dir,
                                        "executable": vivado_exe,
                                    }
                except (PermissionError, FileNotFoundError, OSError):
                    # Skip if we can't access the directory
                    continue

        # Check environment variables as last resort
        xilinx_vivado = os.environ.get("XILINX_VIVADO")
        if xilinx_vivado and os.path.exists(xilinx_vivado):
            bin_dir = os.path.join(xilinx_vivado, "bin")
            if os.path.exists(bin_dir):
                vivado_exe = os.path.join(bin_dir, "vivado")
                if os.path.isfile(vivado_exe):
                    # Try to extract version from path
                    path_parts = xilinx_vivado.split(os.path.sep)
                    version = next(
                        (p for p in path_parts if p[0].isdigit() and "." in p),
                        "unknown",
                    )
                    return {
                        "status": "detected",
                        "version": version,
                        "path": xilinx_vivado,
                        "executable": vivado_exe,
                    }

        return {"status": "not_found", "message": "Vivado not detected"}


def extract_vivado_version(output: str) -> str:
    """
    Extract Vivado version from command output.

    Args:
        output: Command output string

    Returns:
        Version string
    """
    lines = output.split("\n")
    for line in lines:
        if "Vivado" in line and "v" in line:
            # Extract version like "v2022.2"
            parts = line.split()
            for part in parts:
                if part.startswith("v") and "." in part:
                    return part[1:]  # Remove 'v' prefix
    return "unknown"


@status_check
async def get_usb_device_count() -> Dict[str, Any]:
    """
    Get USB device count.

    Returns:
        Dictionary with USB device count information
    """
    from src.cli.flash import list_usb_devices

    devices = await asyncio.get_event_loop().run_in_executor(None, list_usb_devices)
    return {"count": len(devices), "devices": devices[:5]}  # Show first 5


@status_check
async def get_disk_space() -> Dict[str, Any]:
    """
    Get disk space information.

    Returns:
        Dictionary with disk space information
    """
    if psutil is None:
        return {"error": "psutil module not available", "status": "unknown"}

    disk_usage = psutil.disk_usage("/")
    free_gb = disk_usage.free / (1024**3)
    total_gb = disk_usage.total / (1024**3)
    used_percent = (disk_usage.used / disk_usage.total) * 100

    return {
        "free_gb": round(free_gb, 1),
        "total_gb": round(total_gb, 1),
        "used_percent": round(used_percent, 1),
        "status": "low" if free_gb < 10 else "ok",
    }


@status_check
async def check_root_access() -> Dict[str, Any]:
    """
    Check if running with root privileges.

    Returns:
        Dictionary with root access status
    """
    # Check for Unix-like systems (Linux, macOS)
    if hasattr(os, "geteuid"):
        has_root = os.geteuid() == 0
        return {
            "available": has_root,
            "message": (
                "Root access available" if has_root else "Root access required"
            ),
        }
    else:
        return {
            "available": False,
            "message": "Unable to determine root access on this platform",
        }


@status_check
async def check_container_image() -> Dict[str, Any]:
    """
    Check if DMA firmware container image exists.

    Returns:
        Dictionary with container image status
    """
    result = await run_command(
        "podman images pcileech-fw-generator --format '{{.Repository}}:{{.Tag}}'"
    )
    if result.returncode == 0 and "pcileech-fw-generator" in result.stdout:
        return {"available": True, "image": result.stdout.strip()}
    else:
        return {
            "available": False,
            "message": "Container image 'pcileech-fw-generator' not found",
        }


@status_check
async def check_vfio_support() -> Dict[str, Any]:
    """
    Check VFIO support.

    Returns:
        Dictionary with VFIO support status
    """
    # VFIO is a Linux-specific feature
    if platform.system() != "Linux":
        return {
            "supported": False,
            "checks": {"platform_supported": False},
            "message": f"PCILeech requires Linux for hardware operations (current OS: {platform.system()})",
        }

    # For Linux, check VFIO modules and configuration
    vfio_checks = {
        "vfio_module": os.path.exists("/sys/module/vfio"),
        "vfio_pci_driver": os.path.exists("/sys/bus/pci/drivers/vfio-pci"),
        "vfio_device": os.path.exists("/dev/vfio/vfio"),
        "iommu_enabled": check_iommu_enabled(),
    }

    all_good = all(vfio_checks.values())
    return {
        "supported": all_good,
        "checks": vfio_checks,
        "message": ("VFIO fully supported" if all_good else "VFIO issues detected"),
    }


def check_iommu_enabled() -> bool:
    """
    Check if IOMMU is enabled.

    Returns:
        True if IOMMU is enabled, False otherwise
    """
    # IOMMU is a Linux-specific feature
    if platform.system() != "Linux":
        # PCILeech requires Linux, so IOMMU is not available on other platforms
        return False

    try:
        # Check kernel command line for IOMMU parameters
        with open("/proc/cmdline", "r") as f:
            cmdline = f.read()
            return (
                "iommu=on" in cmdline
                or "intel_iommu=on" in cmdline
                or "amd_iommu=on" in cmdline
            )
    except FileNotFoundError:
        # /proc/cmdline doesn't exist on non-Linux platforms
        return False
    except Exception:
        return False


@status_check
async def get_resource_usage() -> Dict[str, Any]:
    """
    Get current resource usage.

    Returns:
        Dictionary with system resource usage information
    """
    if psutil is None:
        return {
            "error": "psutil module not available",
            "status": "unknown",
            "cpu_percent": 0,
            "memory_percent": 0,
        }

    cpu_percent = psutil.cpu_percent(interval=0.1)
    memory = psutil.virtual_memory()
    load_avg = os.getloadavg() if hasattr(os, "getloadavg") else (0, 0, 0)

    return {
        "cpu_percent": round(cpu_percent, 1),
        "memory_used_gb": round(memory.used / (1024**3), 1),
        "memory_total_gb": round(memory.total / (1024**3), 1),
        "memory_percent": round(memory.percent, 1),
        "load_average": {
            "1min": round(load_avg[0], 2),
            "5min": round(load_avg[1], 2),
            "15min": round(load_avg[2], 2),
        },
    }


async def get_system_status() -> Dict[str, Any]:
    """
    Get comprehensive system status.

    Returns:
        Dictionary with complete system status information
    """
    status = {
        "podman": await check_podman_status(),
        "vivado": await check_vivado_status(),
        "usb_devices": await get_usb_device_count(),
        "disk_space": await get_disk_space(),
        "root_access": await check_root_access(),
        "container_image": await check_container_image(),
        "vfio_support": await check_vfio_support(),
        "resources": await get_resource_usage(),
    }

    return status


def get_status_summary(status: Dict[str, Any]) -> Dict[str, str]:
    """
    Get a human-readable summary of system status.

    Args:
        status: Status dictionary returned by get_system_status

    Returns:
        Dictionary with user-friendly status messages
    """
    summary = {}

    # Podman status
    podman = status.get("podman", {})
    summary["podman"] = (
        "🐳 Ready" if podman.get("status") == "ready" else "❌ Not Available"
    )

    # Vivado status
    vivado = status.get("vivado", {})
    if vivado.get("status") == "detected":
        summary["vivado"] = f"⚡ {vivado['version']} Detected"
    else:
        summary["vivado"] = "❌ Not Detected"

    # USB devices
    usb = status.get("usb_devices", {})
    count = usb.get("count", 0)
    summary["usb"] = f"🔌 {count} USB Device{'s' if count != 1 else ''} Found"

    # Disk space
    disk = status.get("disk_space", {})
    if "free_gb" in disk:
        free_gb = disk["free_gb"]
        summary["disk"] = f"💾 {free_gb} GB Free"
    else:
        summary["disk"] = "❌ Disk Info Unavailable"

    # Root access
    root = status.get("root_access", {})
    summary["root"] = (
        "🔒 Root Access Available"
        if root.get("available")
        else "❌ Root Access Required"
    )

    return summary
