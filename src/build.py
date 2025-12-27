"""
PCILeech FPGA Firmware Builder Main Script
Usage:
    python3 build.py \
            --bdf 0000:03:00.0 \
            --board pcileech_35t325_x4 \
            [--vivado] \
            [--preload-msix]
"""

from __future__ import annotations

import argparse
import glob

import json

import logging

import os
import platform

import re

import sys

import time

from concurrent.futures import (
    ThreadPoolExecutor,
    TimeoutError as FutureTimeoutError,
    as_completed,
)

from dataclasses import dataclass

from pathlib import Path

from typing import Any, Dict, List, Optional, Tuple, Union

from src.string_utils import (
    log_debug_safe,
    log_error_safe,
    log_info_safe,
    log_warning_safe,
    safe_format,
)

from src.templating.template_context_validator import clear_global_template_cache

from src.utils.build_logger import get_build_logger
from src.utils.file_manifest import create_manifest_tracker
from src.utils.template_validator import create_template_validator
from src.utils.vfio_decision import make_vfio_decision

# Import board functions from the correct module
from .device_clone.constants import PRODUCTION_DEFAULTS

# Import msix_capability at the module level to avoid late imports
from .device_clone.msix_capability import parse_msix_capability

from .exceptions import (
    ConfigurationError,
    FileOperationError,
    ModuleImportError,
    PCILeechBuildError,
    PlatformCompatibilityError,
    VivadoIntegrationError,
    MSIXPreloadError  # needed for a unit test
)

from .log_config import get_logger, setup_logging

# ──────────────────────────────────────────────────────────────────────────────
# Constants - Extracted magic numbers
# ──────────────────────────────────────────────────────────────────────────────
BUFFER_SIZE = 1024 * 1024  # 1MB buffer for file operations
CONFIG_SPACE_PATH_TEMPLATE = "/sys/bus/pci/devices/{}/config"
DEFAULT_OUTPUT_DIR = "output"
DEFAULT_PROFILE_DURATION = 30  # seconds
MAX_PARALLEL_FILE_WRITES = 4  # Maximum concurrent file write operations
FILE_WRITE_TIMEOUT = 30  # seconds

# Required modules for production
REQUIRED_MODULES = [
    "src.device_clone.pcileech_generator",
    "src.device_clone.behavior_profiler",
    "src.templating.tcl_builder",
]

# File extension mappings
SPECIAL_FILE_EXTENSIONS = {".coe", ".hex"}
SYSTEMVERILOG_EXTENSION = ".sv"

# ──────────────────────────────────────────────────────────────────────────────
# Type Definitions
# ──────────────────────────────────────────────────────────────────────────────


def _running_in_container() -> bool:
    """Best-effort detection for containerized environments.

    Works for Docker/Podman/Kubernetes. Safe to call on non-Linux.
    """
    try:
        if os.path.exists("/.dockerenv") or os.path.exists("/run/.containerenv"):
            return True
        # cgroup hint (Linux only)
        cgroup_path = "/proc/1/cgroup"
        if os.path.exists(cgroup_path):
            with open(cgroup_path, "rt") as f:
                data = f.read()
            return any(tag in data for tag in ("docker", "kubepods", "containerd"))
    except (OSError, IOError):
        # File system access errors are expected on some systems
        return False
    except Exception:
        # Unexpected errors during container detection (fail safe)
        return False
    return False


def _linux() -> bool:
    """Return True if running on Linux."""
    try:
        return platform.system().lower() == "linux"
    except (AttributeError, OSError):
        # Platform module issues or system call failures
        return False
    except Exception:
        # Unexpected platform detection errors (fail safe)
        return False


def _vfio_available() -> bool:
    """Check basic VFIO device node availability and access.

    Requires /dev/vfio/vfio and at least one group node with RW access.
    """
    try:
        control = "/dev/vfio/vfio"
        if not os.path.exists(control):
            return False
        groups = glob.glob("/dev/vfio/[0-9]*")
        if not os.access(control, os.R_OK | os.W_OK):
            return False
        return any(os.access(g, os.R_OK | os.W_OK) for g in groups)
    except (OSError, IOError, PermissionError):
        # File system or permission errors accessing VFIO devices
        return False
    except Exception:
        # Unexpected VFIO check errors (fail safe)
        return False


def _as_int(value: Union[int, str], field: str) -> int:
    """Normalize numeric identifier that may be int, hex (0x) or decimal string."""
    if isinstance(value, int):
        return value
    if isinstance(value, str):
        s = value.strip()
        # int(s, 0) accepts 0x... hex, 0o... octal, or decimal (no prefix)
        if not s:
            raise TypeError(
                safe_format(
                    "Unsupported numeric format for {field}: {val}",
                    field=field,
                    val=value,
                )
            )
        try:
            return int(s, 0)
        except ValueError:
            if re.fullmatch(r"[0-9A-Fa-f]+", s):
                return int(s, 16)
            if re.fullmatch(r"\d+", s):
                return int(s, 10)
            raise TypeError(
                safe_format(
                    "Unsupported numeric format for {field}: {val}",
                    field=field,
                    val=value,
                )
            )
    raise TypeError(safe_format("Unsupported type for {field}", field=field))


def _optional_int(value: Optional[Union[int, str]]) -> Optional[int]:
    """Optional version of _as_int returning None when not parseable."""
    if value in (None, ""):
        return None
    try:
        return _as_int(value, "optional_field")
    except Exception:  # pragma: no cover
        return None


@dataclass(slots=True)
class BuildConfiguration:
    """Configuration for the firmware build process."""

    bdf: str
    board: str
    output_dir: Path
    enable_profiling: bool = True
    preload_msix: bool = True
    profile_duration: int = DEFAULT_PROFILE_DURATION
    parallel_writes: bool = True
    max_workers: int = MAX_PARALLEL_FILE_WRITES
    output_template: Optional[str] = None
    donor_template: Optional[str] = None
    vivado_path: Optional[str] = None
    vivado_jobs: int = 4
    vivado_timeout: int = 3600
    # Experimental / testing feature toggles
    enable_error_injection: bool = False
    # Hard gate to disable any VFIO/sysfs hardware access inside container
    # Requires host-provided context/config space; fail fast if unavailable
    disable_vfio: bool = False
    # MMIO learning for dynamic BAR models
    enable_mmio_learning: bool = True
    force_recapture: bool = False


@dataclass(slots=True)
class MSIXData:
    """Container for MSI-X capability data."""

    preloaded: bool
    msix_info: Optional[Dict[str, Any]] = None
    config_space_hex: Optional[str] = None
    config_space_bytes: Optional[bytes] = None


@dataclass(slots=True)
class DeviceConfiguration:
    """Device configuration extracted from the build process."""

    vendor_id: int
    device_id: int
    revision_id: int
    class_code: int
    requires_msix: bool
    pcie_lanes: int


# ──────────────────────────────────────────────────────────────────────────────
# Module Import Checker
# ──────────────────────────────────────────────────────────────────────────────


class ModuleChecker:
    """Handles checking and validation of required modules."""

    def __init__(self, required_modules: List[str]):
        """
        Initialize the module checker.

        Args:
            required_modules: List of module names that must be available
        """
        self.required_modules = required_modules
        self.logger = get_logger(self.__class__.__name__)

    def check_all(self) -> None:
        """
        Check that all required modules are available.

        Raises:
            ModuleImportError: If any required module cannot be imported
        """
        for module in self.required_modules:
            self._check_module(module)

    def _check_module(self, module: str) -> None:
        """
        Check a single module for availability.

        Args:
            module: Module name to check

        Raises:
            ModuleImportError: If the module cannot be imported
        """
        try:
            __import__(module)
        except ImportError as err:
            self._handle_import_error(module, err)

    def _handle_import_error(self, module: str, error: ImportError) -> None:
        """
        Handle import error with detailed diagnostics.

        Args:
            module: Module that failed to import
            error: The import error

        Raises:
            ModuleImportError: Always raises with diagnostic information
        """
        diagnostics = self._gather_diagnostics(module)
        error_msg = (
            f"Required module `{module}` is missing. "
            "Ensure the production container/image is built correctly.\n"
            f"{diagnostics}"
        )
        raise ModuleImportError(error_msg) from error

    def _gather_diagnostics(self, module: str) -> str:
        """
        Gather diagnostic information for import failure.

        Args:
            module: Module that failed to import

        Returns:
            Formatted diagnostic information
        """
        lines = [
            "\n[DIAGNOSTICS] Python module import failure",
            f"Python version: {sys.version}",
            f"PYTHONPATH: {os.environ.get('PYTHONPATH', 'Not set')}",
            f"Current directory: {os.getcwd()}",
        ]

        # Check module file existence
        module_parts = module.split(".")
        module_path = os.path.join(*module_parts) + ".py"
        # Handle case where module_parts[1:] is empty
        alt_module_path = (
            os.path.join(*module_parts[1:]) + ".py" if len(module_parts) > 1 else ""
        )

        lines.extend(
            [
                f"Looking for module file at: {module_path}",
                (
                    f"✓ File exists at {module_path}"
                    if os.path.exists(module_path)
                    else f"✗ File not found at {module_path}"
                ),
            ]
        )

        # Only check alternative path if it exists
        if alt_module_path:
            lines.extend(
                [
                    f"Looking for module file at: {alt_module_path}",
                    (
                        f"✓ File exists at {alt_module_path}"
                        if os.path.exists(alt_module_path)
                        else f"✗ File not found at {alt_module_path}"
                    ),
                ]
            )

        # Check for __init__.py files
        module_dir = os.path.dirname(module_path)
        lines.append(f"Checking for __init__.py files in path: {module_dir}")

        current_dir = ""
        for part in module_dir.split(os.path.sep):
            if not part:
                continue
            current_dir = os.path.join(current_dir, part)
            init_path = os.path.join(current_dir, "__init__.py")
            status = "✓" if os.path.exists(init_path) else "✗"
            lines.append(f"{status} __init__.py in {current_dir}")

        # List sys.path
        lines.append("\nPython module search path:")
        lines.extend(f"  - {path}" for path in sys.path)

        return "\n".join(lines)


# ──────────────────────────────────────────────────────────────────────────────
# MSI-X Manager
# ──────────────────────────────────────────────────────────────────────────────


class MSIXManager:
    """Manages MSI-X capability data preloading and injection."""

    def __init__(self, bdf: str, logger: Optional[logging.Logger] = None):
        """
        Initialize the MSI-X manager.

        Args:
            bdf: PCI Bus/Device/Function address
            logger: Optional logger instance
        """
        self.bdf = bdf
        self.logger = logger or get_logger(self.__class__.__name__)

    def preload_data(self) -> MSIXData:
        """
        Preload MSI-X data before VFIO binding.

        Returns:
            MSIXData object containing preloaded information

        Note:
            Returns empty MSIXData on any failure (non-critical operation)
        """
        try:
            # In host-context-only mode, never touch sysfs/VFIO;
            # use pre-saved files only
            disable_vfio = str(
                os.environ.get("PCILEECH_DISABLE_VFIO", "")
            ).lower() in (
                "1",
                "true",
                "yes",
                "on",
            ) or str(os.environ.get("PCILEECH_HOST_CONTEXT_ONLY", "")).lower() in (
                "1",
                "true",
                "yes",
                "on",
            )
            if disable_vfio:
                msix_path = os.environ.get(
                    "MSIX_DATA_PATH",
                    "/app/output/msix_data.json",
                )
                try:
                    if msix_path and os.path.exists(msix_path):
                        with open(msix_path, "r") as f:
                            payload = json.load(f)
                        # Support different shapes
                        msix_info = (
                            payload.get("capability_info")
                            or payload.get("msix_info")
                        )
                        cfg_hex = payload.get("config_space_hex")
                        cfg_bytes = bytes.fromhex(cfg_hex) if cfg_hex else None
                        if not msix_info and cfg_hex:
                            msix_info = parse_msix_capability(cfg_hex)
                        if msix_info and int(msix_info.get("table_size", 0)) > 0:
                            log_info_safe(
                                self.logger,
                                safe_format(
                                    "Loaded MSI-X from {path} ({n} vectors)",
                                    path=msix_path,
                                    n=msix_info.get("table_size", 0),
                                ),
                                prefix="MSIX",
                            )
                            return MSIXData(
                                preloaded=True,
                                msix_info=msix_info,
                                config_space_hex=cfg_hex,
                                config_space_bytes=cfg_bytes,
                            )
                except Exception as e:
                    log_warning_safe(
                        self.logger,
                        safe_format(
                            "Failed to load MSI-X from host file: {err}", err=str(e)
                        ),
                        prefix="MSIX",
                    )
                # Do not attempt sysfs/VFIO in strict mode
                log_info_safe(
                    self.logger,
                    (
                        "MSI-X preload: host-context-only mode active; "
                        "skipping sysfs/VFIO"
                    ),
                    prefix="MSIX",
                )
                return MSIXData(preloaded=False)
            log_info_safe(self.logger, "Preloading MSI-X data before VFIO binding")

            # 1) Prefer host-provided JSON (mounted into container) if available
            #    This preserves MSI-X context when container lacks sysfs/VFIO access.
            try:
                msix_json_path = os.environ.get(
                    "MSIX_DATA_PATH", "/app/output/msix_data.json"
                )
                if msix_json_path and os.path.exists(msix_json_path):
                    with open(msix_json_path, "r") as f:
                        payload = json.load(f)

                    # Optional: ensure BDF matches if present
                    bdf_in = payload.get("bdf")
                    msix_info = payload.get("msix_info")
                    cfg_hex = payload.get("config_space_hex")
                    if msix_info and isinstance(msix_info, dict):
                        log_info_safe(
                            self.logger,
                            safe_format(
                                "Loaded MSI-X from {path} ({vectors} vectors)",
                                path=msix_json_path,
                                vectors=msix_info.get("table_size", 0),
                            ),
                            prefix="MSIX",
                        )
                        return MSIXData(
                            preloaded=True,
                            msix_info=msix_info,
                            config_space_hex=cfg_hex,
                            config_space_bytes=(
                                bytes.fromhex(cfg_hex) if cfg_hex else None
                            ),
                        )
            except Exception as e:
                # Non-fatal; fall back to sysfs path
                log_debug_safe(
                    self.logger,
                    safe_format(
                        "MSI-X JSON ingestion skipped: {err}",
                        err=str(e),
                    ),
                    prefix="MSIX",
                )

            config_space_path = CONFIG_SPACE_PATH_TEMPLATE.format(self.bdf)
            if not os.path.exists(config_space_path):
                log_warning_safe(
                    self.logger,
                    "Config space not accessible via sysfs, skipping MSI-X preload",
                    prefix="MSIX",
                )
                return MSIXData(preloaded=False)

            config_space_bytes = self._read_config_space(config_space_path)
            config_space_hex = config_space_bytes.hex()
            msix_info = parse_msix_capability(config_space_hex)

            if msix_info["table_size"] > 0:
                log_info_safe(
                    self.logger,
                    safe_format(
                        "Found MSI-X capability: {vectors} vectors",
                        vectors=msix_info["table_size"],
                    ),
                    prefix="MSIX",
                )
                return MSIXData(
                    preloaded=True,
                    msix_info=msix_info,
                    config_space_hex=config_space_hex,
                    config_space_bytes=config_space_bytes,
                )
            else:
                # No MSI-X capability found -> treat as not preloaded so callers
                # don't assume hardware MSI-X values are available.
                log_info_safe(
                    self.logger,
                    "No MSI-X capability found",
                    prefix="MSIX",
                )
                return MSIXData(preloaded=False, msix_info=None)

        except (OSError, IOError) as e:
            log_warning_safe(
                self.logger,
                safe_format("MSI-X preload failed - I/O error: {err}", err=str(e)),
                prefix="MSIX",
            )
            if self.logger.isEnabledFor(logging.DEBUG):
                import traceback
                log_debug_safe(
                    self.logger,
                    safe_format(
                        "MSI-X preload I/O traceback:\n{tb}",
                        tb=traceback.format_exc(),
                    ),
                    prefix="MSIX",
                )
            return MSIXData(preloaded=False)
        except Exception as e:
            log_warning_safe(
                self.logger,
                safe_format(
                    "MSI-X preload failed - unexpected error: {err}",
                    err=str(e)
                ),
                prefix="MSIX",
            )
            if self.logger.isEnabledFor(logging.DEBUG):
                import traceback
                log_debug_safe(
                    self.logger,
                    safe_format(
                        "MSI-X preload unexpected traceback:\n{tb}",
                        tb=traceback.format_exc(),
                    ),
                    prefix="MSIX",
                )
            return MSIXData(preloaded=False)

    def inject_data(self, result: Dict[str, Any], msix_data: MSIXData) -> None:
        """
        Inject preloaded MSI-X data into the generation result.

        Args:
            result: The generation result dictionary to update
            msix_data: The preloaded MSI-X data
        """
        if not self._should_inject(msix_data):
            return

        log_info_safe(
            self.logger, safe_format("Using preloaded MSI-X data"), prefix="MSIX"
        )

        # msix_info is guaranteed to be non-None by _should_inject
        if msix_data.msix_info is not None:
            if "msix_data" not in result or not result["msix_data"]:
                result["msix_data"] = self._create_msix_result(msix_data.msix_info)

            # Update template context if present
            if (
                "template_context" in result
                and "msix_config" in result["template_context"]
            ):
                result["template_context"]["msix_config"].update(
                    {
                        "is_supported": True,
                        "num_vectors": msix_data.msix_info["table_size"],
                    }
                )

    def _read_config_space(self, path: str) -> bytes:
        """
        Read PCI config space from sysfs.

        Args:
            path: Path to config space file

        Returns:
            Config space bytes

        Raises:
            IOError: If reading fails
        """
        with open(path, "rb") as f:
            return f.read()

    def _should_inject(self, msix_data: MSIXData) -> bool:
        """
        Check if MSI-X data should be injected.

        Args:
            msix_data: The MSI-X data to check

        Returns:
            True if data should be injected
        """
        return (
            msix_data.preloaded
            and msix_data.msix_info is not None
            and msix_data.msix_info.get("table_size", 0) > 0
        )

    def _create_msix_result(self, msix_info: Dict[str, Any]) -> Dict[str, Any]:
        """
        Create MSI-X result dictionary from capability info.

        Args:
            msix_info: MSI-X capability information

        Returns:
            Formatted MSI-X result dictionary
        """
        return {
            "capability_info": msix_info,
            "table_size": msix_info["table_size"],
            "table_bir": msix_info["table_bir"],
            "table_offset": msix_info["table_offset"],
            "pba_bir": msix_info["pba_bir"],
            "pba_offset": msix_info["pba_offset"],
            "enabled": msix_info["enabled"],
            "function_mask": msix_info["function_mask"],
            "is_valid": True,
            "validation_errors": [],
        }


# ──────────────────────────────────────────────────────────────────────────────
# File Operations Manager
# ──────────────────────────────────────────────────────────────────────────────


class FileOperationsManager:
    """Manages file operations with optional parallel processing."""

    def __init__(
        self,
        output_dir: Path,
        parallel: bool = True,
        max_workers: int = MAX_PARALLEL_FILE_WRITES,
        logger: Optional[logging.Logger] = None,
    ):
        """
        Initialize the file operations manager.

        Args:
            output_dir: Base output directory
            parallel: Enable parallel file writes
            max_workers: Maximum number of parallel workers
            logger: Optional logger instance
        """
        self.output_dir = output_dir
        self.parallel = parallel
        self.max_workers = max_workers
        self.logger = logger or get_logger(self.__class__.__name__)
        self._ensure_output_dir()

    def write_systemverilog_modules(
        self, modules: Dict[str, str]
    ) -> Tuple[List[str], List[str]]:
        """
        Write SystemVerilog modules to disk with proper file extensions.
        COE files are excluded from this method to prevent duplication.

        Args:
            modules: Dictionary of module names to content

        Returns:
            Tuple of (sv_files, special_files) lists

        Raises:
            FileOperationError: If writing fails
        """
        sv_dir = self.output_dir / "src"
        sv_dir.mkdir(parents=True, exist_ok=True)

        # Prepare file write tasks
        write_tasks = []
        sv_files = []
        special_files = []

        for name, content in modules.items():
            # Skip COE files to prevent duplication
            # COE files are handled separately and saved to systemverilog directory
            if name.endswith(".coe"):
                continue

            file_path, category = self._determine_file_path(name, sv_dir)

            if category == "sv":
                sv_files.append(file_path.name)
            else:
                special_files.append(file_path.name)

            write_tasks.append((file_path, content))

        # Execute writes
        if self.parallel and len(write_tasks) > 1:
            self._parallel_write(write_tasks)
        else:
            self._sequential_write(write_tasks)

        return sv_files, special_files

    def write_json(self, filename: str, data: Any, indent: int = 2) -> None:
        """
        Write JSON data to a file.

        Args:
            filename: Name of the file (relative to output_dir)
            data: Data to serialize to JSON
            indent: JSON indentation level

        Raises:
            FileOperationError: If writing fails
        """
        file_path = self.output_dir / filename
        log_info_safe(
            self.logger,
            "Writing JSON file: {filename}",
            filename=filename,
            prefix="BUILD",
        )
        try:
            with open(file_path, "w", buffering=BUFFER_SIZE) as f:
                json.dump(
                    data,
                    f,
                    indent=indent,
                    default=self._json_serialize_default,
                )
            log_info_safe(
                self.logger,
                "Successfully wrote JSON file: {filename}",
                filename=filename,
                prefix="BUILD",
            )
        except (OSError, IOError) as e:
            raise FileOperationError(
                f"Failed to write JSON file {filename}: I/O error - {e}"
            ) from e
        except (TypeError, ValueError) as e:
            raise FileOperationError(
                f"Failed to write JSON file {filename}: Invalid data - {e}"
            ) from e
        except Exception as e:
            raise FileOperationError(
                f"Failed to write JSON file {filename}: Unexpected error - {e}"
            ) from e

    def write_text(self, filename: str, content: str) -> None:
        """
        Write text content to a file.

        Args:
            filename: Name of the file (relative to output_dir)
            content: Text content to write

        Raises:
            FileOperationError: If writing fails
        """
        file_path = self.output_dir / filename
        log_info_safe(
            self.logger,
            "Writing text file: {filename}",
            filename=filename,
            prefix="BUILD",
        )
        try:
            with open(file_path, "w", buffering=BUFFER_SIZE) as f:
                f.write(content)
            log_info_safe(
                self.logger,
                "Successfully wrote text file: {filename}",
                filename=filename,
                prefix="BUILD",
            )
        except (OSError, IOError) as e:
            raise FileOperationError(
                f"Failed to write text file {filename}: I/O error - {e}"
            ) from e
        except UnicodeEncodeError as e:
            raise FileOperationError(
                f"Failed to write text file {filename}: Encoding error - {e}"
            ) from e
        except Exception as e:
            raise FileOperationError(
                f"Failed to write text file {filename}: Unexpected error - {e}"
            ) from e

    def list_artifacts(self) -> List[str]:
        """
        List all file artifacts in the output directory.

        Returns:
            List of relative file paths
        """
        return [
            str(p.relative_to(self.output_dir))
            for p in self.output_dir.rglob("*")
            if p.is_file()
        ]

    def _ensure_output_dir(self) -> None:
        """Ensure the output directory exists."""
        self.output_dir.mkdir(parents=True, exist_ok=True)

    def _determine_file_path(self, name: str, base_dir: Path) -> Tuple[Path, str]:
        """
        Determine the file path and category for a module.

        Args:
            name: Module name
            base_dir: Base directory for the file

        Returns:
            Tuple of (file_path, category_label)
        """
        # Check if it's a special file type
        if any(name.endswith(ext) for ext in SPECIAL_FILE_EXTENSIONS):
            return base_dir / name, "special"

        # SystemVerilog files
        if name.endswith(SYSTEMVERILOG_EXTENSION):
            return base_dir / name, "sv"
        else:
            return base_dir / f"{name}{SYSTEMVERILOG_EXTENSION}", "sv"

    def _parallel_write(self, write_tasks: List[Tuple[Path, str]]) -> None:
        """
        Write files in parallel.

        Args:
            write_tasks: List of (path, content) tuples

        Raises:
            FileOperationError: If any write fails
        """
        with ThreadPoolExecutor(max_workers=self.max_workers) as executor:
            futures = {
                executor.submit(self._write_single_file, path, content): path
                for path, content in write_tasks
            }

            failed_writes = []
            try:
                for future in as_completed(futures, timeout=FILE_WRITE_TIMEOUT):
                    path = futures[future]
                    try:
                        future.result()
                    except (OSError, IOError) as e:
                        failed_writes.append((path, f"I/O error: {e}"))
                    except UnicodeEncodeError as e:
                        failed_writes.append((path, f"Encoding error: {e}"))
                    except Exception as e:
                        failed_writes.append((path, f"Unexpected error: {e}"))
            except FutureTimeoutError as e:
                raise FileOperationError(
                    f"Parallel write timed out after {FILE_WRITE_TIMEOUT}s"
                ) from e
            
            if failed_writes:
                error_details = "; ".join([f"{p}: {e}" for p, e in failed_writes])
                raise FileOperationError(
                    f"Failed to write {len(failed_writes)} file(s): {error_details}"
                )

    def _sequential_write(self, write_tasks: List[Tuple[Path, str]]) -> None:
        """
        Write files sequentially.

        Args:
            write_tasks: List of (path, content) tuples

        Raises:
            FileOperationError: If any write fails
        """
        for path, content in write_tasks:
            try:
                self._write_single_file(path, content)
            except (OSError, IOError) as e:
                raise FileOperationError(
                    f"Failed to write file {path}: I/O error - {e}"
                ) from e
            except UnicodeEncodeError as e:
                raise FileOperationError(
                    f"Failed to write file {path}: Encoding error - {e}"
                ) from e
            except Exception as e:
                raise FileOperationError(
                    f"Failed to write file {path}: Unexpected error - {e}"
                ) from e

    def _write_single_file(self, path: Path, content: str) -> None:
        """
        Write a single file.

        Args:
            path: File path
            content: File content
        """
        path.parent.mkdir(parents=True, exist_ok=True)
        with open(path, "w", buffering=BUFFER_SIZE, encoding="utf-8") as f:
            f.write(content)

    def _json_serialize_default(self, obj: Any) -> str:
        """Default JSON serialization function for complex objects."""
        return obj.__dict__ if hasattr(obj, "__dict__") else str(obj)


# ──────────────────────────────────────────────────────────────────────────────
# Configuration Manager
# ──────────────────────────────────────────────────────────────────────────────


class ConfigurationManager:
    """Manages build configuration and validation."""

    def __init__(self, logger: Optional[logging.Logger] = None):
        """
        Initialize the configuration manager.

        Args:
            logger: Optional logger instance
        """
        self.logger = logger or get_logger(self.__class__.__name__)

    def create_from_args(self, args: argparse.Namespace) -> BuildConfiguration:
        """
        Create build configuration from command line arguments.

        Args:
            args: Parsed command line arguments

        Returns:
            BuildConfiguration instance

        Raises:
            ConfigurationError: If configuration is invalid
        """
        self._validate_args(args)

        # Optional environment toggle to apply production defaults
        use_prod = os.environ.get("PCILEECH_PRODUCTION_DEFAULTS", "").lower() in (
            "1",
            "true",
            "yes",
        )
        enable_profiling = args.profile > 0
        preload_msix = getattr(args, "preload_msix", True)

        # Use centralized VFIO decision system
        vfio_decision = make_vfio_decision(args, logger=self.logger)
        disable_vfio = not vfio_decision.enabled
        if use_prod:
            # Map production flags when present
            enable_profiling = PRODUCTION_DEFAULTS.get("BEHAVIOR_PROFILING", True)
            preload_msix = PRODUCTION_DEFAULTS.get("MSIX_CAPABILITY", True)

        # Honor disable_vfio: don't attempt any profiling/probing in container
        if disable_vfio:
            enable_profiling = False

        # MMIO learning requires root and should be disabled in containers
        enable_mmio_learning = (
            not getattr(args, "no_mmio_learning", False) and not disable_vfio
        )
        force_recapture = getattr(args, "force_recapture", False)

        return BuildConfiguration(
            bdf=args.bdf,
            board=args.board,
            output_dir=Path(args.output).resolve(),
            enable_profiling=enable_profiling,
            preload_msix=preload_msix,
            profile_duration=(0 if disable_vfio else args.profile),
            output_template=getattr(args, "output_template", None),
            donor_template=getattr(args, "donor_template", None),
            vivado_path=getattr(args, "vivado_path", None),
            vivado_jobs=getattr(args, "vivado_jobs", 4),
            vivado_timeout=getattr(args, "vivado_timeout", 3600),
            enable_error_injection=getattr(args, "enable_error_injection", False),
            disable_vfio=disable_vfio,
            enable_mmio_learning=enable_mmio_learning,
            force_recapture=force_recapture,
        )

    def extract_device_config(
        self, template_context: Dict[str, Any], has_msix: bool
    ) -> DeviceConfiguration:
        """
        Extract device configuration from build results.

        Args:
            template_context: Template context from generation
            has_msix: Whether the device requires MSI-X support

        Returns:
            DeviceConfiguration instance

        Raises:
            ConfigurationError: If required device configuration is missing
                or invalid
        """
        device_config = template_context.get("device_config")
        pcie_config = template_context.get("pcie_config", {})

        # Fail immediately if device config is missing or empty - no fallbacks
        if not device_config:
            raise ConfigurationError(
                "Device configuration is missing from template context. "
                "This would result in generic firmware that is not device-specific. "
                "Ensure proper device detection and configuration space analysis."
            )

        # Validate all required fields are present and non-zero
        required_fields = {
            "vendor_id": "Vendor ID",
            "device_id": "Device ID",
            "revision_id": "Revision ID",
            "class_code": "Class Code",
        }

        # Debug: log the actual values being validated
        log_debug_safe(
            self.logger,
            "Validating device config fields: " +
            ", ".join([f"{k}={device_config.get(k, 'missing')}"
                       for k in required_fields.keys()]),
            prefix="BUILD"
        )

        for field, display_name in required_fields.items():
            value = device_config.get(field)
            if value is None:
                raise ConfigurationError(
                    "Cannot generate device-specific firmware without "
                    f"valid {display_name}."
                )

            # Check for invalid/generic values that could create non-unique firmware
            if isinstance(value, (int, str)):
                int_value = _as_int(value, field)
                if int_value == 0:
                    # Revision ID = 0x00 is valid for many real devices
                    # Only vendor_id, device_id, and class_code should be non-zero
                    if field == "revision_id":
                        log_info_safe(
                            self.logger,
                            "Revision ID is 0x00 - this is valid for many devices",
                            prefix="BUILD"
                        )
                        # Additional validation: if revision is 0, ensure
                        # vendor/device are reasonable
                        vendor_id = _as_int(
                            device_config.get("vendor_id", 0), "vendor_id"
                        )
                        device_id = _as_int(
                            device_config.get("device_id", 0), "device_id"
                        )
                        if vendor_id == 0 or device_id == 0:
                            raise ConfigurationError(
                                "Cannot accept Revision ID = 0x00 when Vendor ID "
                                f"(0x{vendor_id:04X}) or Device ID "
                                f"(0x{device_id:04X}) are also zero - this "
                                "indicates an uninitialized device"
                            )
                    else:
                        raise ConfigurationError(
                            f"{display_name} is zero (0x{int_value:04X}), which "
                            "indicates "
                            "a generic or uninitialized value. Use a real device "
                            "for cloning."
                        )

        # Additional validation for vendor/device ID pairs that are known generics
        vendor_id = _as_int(device_config["vendor_id"], "vendor_id")
        device_id = _as_int(device_config["device_id"], "device_id")

    # Validate that vendor/device IDs are not zero or obviously invalid
    # Generic firmware prevention is handled through donor device
    # integrity checks
        if vendor_id == 0 or device_id == 0:
            raise ConfigurationError(
                f"Invalid vendor/device ID combination "
                f"(0x{vendor_id:04X}:0x{device_id:04X}). "
                f"Zero values indicate uninitialized or generic configuration."
            )

        if vendor_id == 0xFFFF or device_id == 0xFFFF:
            raise ConfigurationError(
                f"Invalid vendor/device ID combination "
                f"(0x{vendor_id:04X}:0x{device_id:04X}). "
                f"FFFF values indicate invalid or uninitialized configuration."
            )

        revision_id = _as_int(device_config["revision_id"], "revision_id")
        class_code = _as_int(device_config["class_code"], "class_code")

        return DeviceConfiguration(
            vendor_id=vendor_id,
            device_id=device_id,
            revision_id=revision_id,
            class_code=class_code,
            requires_msix=has_msix,
            pcie_lanes=pcie_config.get("max_lanes", 1),
        )

    def _validate_args(self, args: argparse.Namespace) -> None:
        """
        Validate command line arguments.

        Args:
            args: Arguments to validate

        Raises:
            ConfigurationError: If validation fails
        """
        # Validate BDF format
        if not self._is_valid_bdf(args.bdf):
            raise ConfigurationError(
                f"Invalid BDF format: {args.bdf}. "
                "Expected format: XXXX:XX:XX.X (e.g., 0000:03:00.0)"
            )

        # Validate profile duration
        if args.profile < 0:
            raise ConfigurationError(
                safe_format(
                    "Invalid profile duration: {profile}. Must be >= 0",
                    profile=args.profile,
                )
            )

    def _is_valid_bdf(self, bdf: str) -> bool:
        """
        Check if BDF string is valid.

        Args:
            bdf: BDF string to validate

        Returns:
            True if valid
        """
        pattern = r"^[0-9a-fA-F]{4}:[0-9a-fA-F]{2}:[0-9a-fA-F]{2}\.[0-9a-fA-F]$"
        return bool(re.match(pattern, bdf))


# ──────────────────────────────────────────────────────────────────────────────
# Main Firmware Builder
# ──────────────────────────────────────────────────────────────────────────────


class FirmwareBuilder:
    """
    This class orchestrates the firmware generation process using
    dedicated manager classes for different responsibilities.
    """

    def __init__(
        self,
        config: BuildConfiguration,
        msix_manager: Optional[MSIXManager] = None,
        file_manager: Optional[FileOperationsManager] = None,
        config_manager: Optional[ConfigurationManager] = None,
        logger: Optional[logging.Logger] = None,
    ):
        """Initialize the firmware builder with dependency injection."""
        # Core configuration & logger
        self.config = config
        self.logger = logger or get_logger(self.__class__.__name__)

        # Initialize centralized systems
        self.build_logger = get_build_logger(self.logger)
        self.file_manifest = create_manifest_tracker(self.logger)
        self.template_validator = create_template_validator(
            self._get_repo_root(),
            self.logger
        )

        # Initialize managers (dependency injection with defaults)
        self.msix_manager = msix_manager or MSIXManager(config.bdf, self.logger)
        self.file_manager = file_manager or FileOperationsManager(
            config.output_dir,
            parallel=config.parallel_writes,
            max_workers=config.max_workers,
            logger=self.logger,
        )
        self.config_manager = config_manager or ConfigurationManager(self.logger)

        # Initialize generator and other components
        self._init_components()

        # Store device configuration for later use
        self._device_config: Optional[DeviceConfiguration] = None

    def _validate_board_template(self) -> None:
        """Validate board template before build."""
        self.build_logger.info(
            safe_format("Validating board: {board}", board=self.config.board),
            prefix="TEMPLATE"
        )

        is_valid, warnings = self.template_validator.validate_board_template(
            self.config.board
        )

        if warnings:
            for warning in warnings:
                self.build_logger.warning(warning, prefix="TEMPLATE")

        if not is_valid:
            self.build_logger.warning(
                "Board template validation failed - build may fail",
                prefix="TEMPLATE"
            )
        else:
            self.build_logger.info(
                "Board template validation passed",
                prefix="TEMPLATE"
            )

    def _get_repo_root(self) -> Path:
        """Get the repository root path."""
        # Try to find repo root relative to this file
        current_file = Path(__file__).resolve()

        # Look for common repo indicators
        for parent in current_file.parents:
            if (parent / ".git").exists():
                return parent
            if (parent / "lib" / "voltcyclone-fpga").exists():
                return parent
            if (parent / "pcileech-fpga").exists():
                return parent

        # Fallback to current directory
        return Path.cwd()

    def _phase(self, message: str) -> None:
        """Log a build phase message with standardized formatting."""
        self.build_logger.phase(message)

    def build(self) -> List[str]:
        """
        Run the full firmware generation flow.

        Returns:
            List of generated artifact paths (relative to output directory)

        Raises:
            PCILeechBuildError: If build fails
        """
        try:
            # Step 0: Validate board template before starting
            self.build_logger.push_phase("template_validation")
            self._validate_board_template()
            self.build_logger.pop_phase("template_validation")

            # Step 1: Check for host-collected complete device context
            self.build_logger.push_phase("host_context_check")
            host_context = self._check_host_collected_context()
            self.build_logger.pop_phase("host_context_check")

            # Step 2: Load donor template if provided
            donor_template = self._load_donor_template()

            # Step 3: Generate PCILeech firmware
            if host_context:
                self.build_logger.push_phase("host_context_generation")
                self._phase("Using host-collected device context …")
                
                # Extract config space bytes for processing
                config_space_hex = host_context.get("config_space_hex", "")
                config_space_bytes =(
                    bytes.fromhex(config_space_hex) if config_space_hex else b""   
                )
                
                # Build config_space_data with device IDs from host context
                config_space_data = {
                    "raw_config_space": config_space_bytes,
                    "config_space_hex": config_space_hex,
                    "vendor_id": format(host_context.get("vendor_id", 0), "04x"),
                    "device_id": format(host_context.get("device_id", 0), "04x"),
                    "class_code": format(host_context.get("class_code", 0), "06x"),
                    "revision_id": format(host_context.get("revision_id", 0), "02x"),
                    "device_info": {
                        "vendor_id": host_context.get("vendor_id"),
                        "device_id": host_context.get("device_id"),
                        "class_code": host_context.get("class_code"),
                        "revision_id": host_context.get("revision_id"),
                        "subsystem_vendor_id":
                            host_context.get("subsystem_vendor_id"),
                        "subsystem_device_id":
                            host_context.get("subsystem_device_id"),
                    },
                }
                
                # Log the device IDs being used
                self.build_logger.info(
                    safe_format(
                        "Using device IDs: VID=0x{vid} DID=0x{did} Class=0x{cls}",
                        vid=config_space_data["vendor_id"],
                        did=config_space_data["device_id"],
                        cls=config_space_data["class_code"]
                    ),
                    prefix="HOST_CFG"
                )
                
                # Use prefilled context from host, avoiding VFIO operations
                generation_result = {
                    "template_context": host_context,
                    "systemverilog_modules": {},
                    "config_space_data": config_space_data,
                    "msix_data": host_context.get("msix_data"),
                }
                self.build_logger.info(
                    "Complete device context loaded from host - "
                    "skipping container VFIO operations",
                    prefix="HOST_CFG"
                )
                self.build_logger.pop_phase("host_context_generation")
            else:
                # Fallback to container-based generation with MSI-X preloading
                self.build_logger.push_phase("firmware_generation")
                msix_data = self._preload_msix()

                self._phase("Generating PCILeech firmware …")
                generation_result = self._generate_firmware(donor_template)

                # Inject preloaded MSI-X data if available
                self._inject_msix(generation_result, msix_data)
                self.build_logger.pop_phase("firmware_generation")

            self.build_logger.push_phase("module_writing")
            self._phase("Writing SystemVerilog modules …")
            # Step 4: Write SystemVerilog modules
            self._write_modules(generation_result)
            self.build_logger.pop_phase("module_writing")

            self.build_logger.push_phase("profile_generation")
            self._phase("Generating behavior profile …")
            # Step 5: Generate behavior profile if requested
            self._generate_profile()
            self.build_logger.pop_phase("profile_generation")

            self.build_logger.push_phase("tcl_generation")
            self._phase("Generating TCL scripts …")
            # Step 6: Generate TCL scripts
            self._generate_tcl_scripts(generation_result)
            self.build_logger.pop_phase("tcl_generation")

            # Step 6.5: Write XDC constraint files
            self.build_logger.push_phase("constraint_writing")
            self._write_xdc_files(generation_result)
            self.build_logger.pop_phase("constraint_writing")

            self.build_logger.push_phase("device_info_saving")
            self._phase("Saving device information …")
            # Step 7: Save device information
            self._save_device_info(generation_result)
            self.build_logger.pop_phase("device_info_saving")

            # Step 8: Store device configuration
            self._store_device_config(generation_result)

            # Step 9: Run post-build validation
            self.build_logger.push_phase("post_build_validation")
            self._phase("Running post-build validation …")
            self._run_post_build_validation(generation_result)
            self.build_logger.pop_phase("post_build_validation")

            # Step 10: Generate donor template if requested
            if self.config.output_template:
                self.build_logger.push_phase("donor_template_generation")
                self._phase("Writing donor template …")
                self._generate_donor_template(generation_result)
                self.build_logger.pop_phase("donor_template_generation")

            # Step 11: Save file manifest
            self.build_logger.push_phase("manifest_saving")
            self._save_file_manifest()
            self.build_logger.pop_phase("manifest_saving")

            # Return list of artifacts
            return self.file_manager.list_artifacts()

        except PlatformCompatibilityError:
            # Reraise platform compatibility errors without modification
            raise
        except (ConfigurationError, FileOperationError, VivadoIntegrationError):
            # Reraise known build errors without wrapping
            raise
        except KeyboardInterrupt:
            log_warning_safe(
                self.logger,
                "Build interrupted by user",
                prefix="BUILD",
            )
            raise
        except Exception as e:
            log_error_safe(
                self.logger,
                safe_format("Build failed: {err}", err=str(e)),
                prefix="BUILD",
            )
            if self.logger.isEnabledFor(logging.DEBUG):
                import traceback
                log_debug_safe(
                    self.logger,
                    safe_format(
                        "Build failure traceback:\n{tb}",
                        tb=traceback.format_exc(),
                    ),
                    prefix="BUILD",
                )
            raise PCILeechBuildError(
                safe_format("Build failed: {err}", err=str(e))
            ) from e

    def _save_file_manifest(self) -> None:
        """Save the file manifest for audit purposes."""
        manifest_path = self.config.output_dir / "file_manifest.json"
        self.file_manifest.save_manifest(manifest_path)

        manifest = self.file_manifest.get_manifest()
        self.build_logger.info(
            safe_format(
                "Saved file manifest: {files} files, {size} bytes, "
                "{dups} duplicates skipped",
                files=manifest.total_files,
                size=manifest.total_size_bytes,
                dups=len(manifest.duplicate_operations)
            ),
            prefix="FILEMGR"
        )

    def run_vivado(self) -> None:
        """
        Hand-off to Vivado in batch mode using the simplified VivadoRunner.

        Raises:
            VivadoIntegrationError: If Vivado integration fails
        """
        try:
            from .vivado_handling import VivadoRunner, find_vivado_installation
        except ImportError as e:
            raise VivadoIntegrationError(
                "Vivado handling modules not available"
            ) from e

        # Determine Vivado path
        if self.config.vivado_path:
            # User provided explicit path
            vivado_path = self.config.vivado_path
            log_info_safe(
                self.logger,
                safe_format(
                    "Using user-specified Vivado path: {path}", path=vivado_path
                ),
                prefix="VIVADO",
            )
            # Sanity check: ensure vivado executable exists
            vivado_exe = Path(vivado_path) / "bin" / "vivado"
            if not vivado_exe.exists():
                raise VivadoIntegrationError(
                    safe_format(
                        "Vivado executable not found at {exe}",
                        exe=str(vivado_exe),
                    )
                )
        else:
            # Auto-detect Vivado installation
            vivado_info = find_vivado_installation()
            if not vivado_info:
                raise VivadoIntegrationError(
                    "Vivado not found in PATH. Use --vivado-path to specify "
                    "installation directory."
                )
            # Extract root path from executable path
            # e.g., /tools/Xilinx/2025.1/Vivado/bin/vivado ->
            #       /tools/Xilinx/2025.1/Vivado
            vivado_exe_path = Path(vivado_info["executable"])
            vivado_path = str(vivado_exe_path.parent.parent)
            log_info_safe(
                self.logger,
                safe_format("Auto-detected Vivado at: {path}", path=vivado_path),
                prefix="VIVADO",
            )

        # Create and run VivadoRunner
        runner = VivadoRunner(
            board=self.config.board,
            output_dir=self.config.output_dir,
            vivado_path=vivado_path,
            logger=self.logger,
            device_config=(
                self._device_config.__dict__ if self._device_config else None
            ),
        )

        # Run Vivado synthesis
        runner.run()

    # ────────────────────────────────────────────────────────────────────────
    # Private methods - initialization
    # ────────────────────────────────────────────────────────────────────────

    def _init_components(self) -> None:
        """Initialize PCILeech generator and other components."""
        from .device_clone.board_config import get_pcileech_board_config

        from .device_clone.pcileech_generator import (
            PCILeechGenerationConfig,
            PCILeechGenerator,
        )

        # Check if we have preloaded config space from host collection
        preloaded_config_space = self._load_preloaded_config_space()

        if preloaded_config_space:
            log_info_safe(
                self.logger,
                safe_format(
                    "Using preloaded config space from host: {size} bytes",
                    size=len(preloaded_config_space)
                ),
                prefix="BUILD"
            )
        else:
            if getattr(self.config, "disable_vfio", False):
                # In host-context-only mode we must fail fast if data is missing
                log_error_safe(
                    self.logger,
                    "Host-prepared config space not found "
                    "but VFIO is disabled; aborting",
                    prefix="BUILD",
                )
                raise PCILeechBuildError(
                    "Missing preloaded config space "
                    "(DEVICE_CONTEXT_PATH/MSIX_DATA_PATH); "
                    "container is configured to not perform "
                    "VFIO/sysfs reads."
                )
            else:
                log_info_safe(
                    self.logger,
                    "No preloaded config space available - will use VFIO",
                    prefix="BUILD"
                )

        gen_cfg = PCILeechGenerationConfig(
            device_bdf=self.config.bdf,
            board=self.config.board,
            template_dir=None,
            output_dir=self.config.output_dir,
            enable_behavior_profiling=self.config.enable_profiling,
            enable_error_injection=getattr(
                self.config, "enable_error_injection", False
            ),
            preloaded_config_space=preloaded_config_space,
        )
        # Explicitly relax VFIO when host data is provided or VFIO is disabled
        # Note: Container environments typically have VFIO warnings due to
        # device binding limitations, but this is expected and handled gracefully
        if getattr(self.config, "disable_vfio", False) or preloaded_config_space:
            setattr(gen_cfg, "strict_vfio", False)

        self.gen = PCILeechGenerator(gen_cfg)

        # Only construct profiler if it's actually enabled and VFIO is allowed
        if (
            self.config.enable_profiling
            and not getattr(self.config, "disable_vfio", False)
        ):
            # Import lazily to avoid any side-effects when VFIO is disabled
            from .device_clone.behavior_profiler import BehaviorProfiler
            self.profiler = BehaviorProfiler(bdf=self.config.bdf)
        else:
            # Provide a patchable, no-op profiler instance so tests can stub it
            # Use a minimal object with an attachable attribute to satisfy tests
            Noop = type("NoopProfiler", (), {})  # pragma: no cover - trivial
            self.profiler = Noop()
            # attach a default no-op method that tests may overwrite
            self.profiler.capture_behavior_profile = (
                lambda duration: {"duration": duration, "events": []}
            )

    # ────────────────────────────────────────────────────────────────────────
    # Private methods - build steps
    # ────────────────────────────────────────────────────────────────────────

    def _load_donor_template(self) -> Optional[Dict[str, Any]]:
        """Load donor template if provided."""
        if self.config.donor_template:
            from .device_clone.donor_info_template import DonorInfoTemplateGenerator

            log_info_safe(
                self.logger,
                safe_format(
                    "Loading donor template from: {path}",
                    path=self.config.donor_template,
                ),
                prefix="BUILD",
            )
            try:
                template = DonorInfoTemplateGenerator.load_template(
                    self.config.donor_template
                )
                log_info_safe(
                    self.logger, "Donor template loaded successfully", prefix="BUILD"
                )
                return template
            except Exception as e:
                log_error_safe(
                    self.logger,
                    safe_format("Failed to load donor template: {err}", err=str(e)),
                    prefix="BUILD",
                )
                raise PCILeechBuildError(
                    safe_format("Failed to load donor template: {err}", err=str(e))
                ) from e
        return None

    def _load_preloaded_config_space(self) -> Optional[bytes]:
        """
        Load preloaded config space from host collection.

        Returns:
            Config space bytes if available, None otherwise
        """
        context_path = os.environ.get(
            "DEVICE_CONTEXT_PATH", "/app/output/device_context.json"
        )
        msix_path = os.environ.get(
            "MSIX_DATA_PATH", "/app/output/msix_data.json"
        )

        # If no path configured, silently return None
        if not context_path:
            return None

        # If path doesn't exist, return None (normal for non-container builds)
        if not os.path.exists(context_path):
            return None

        try:
            with open(context_path, "r") as f:
                payload = json.load(f)

            # Validate that we have the expected data structure
            if not isinstance(payload, dict):
                log_warning_safe(
                    self.logger,
                    "Device context file does not contain a valid dictionary",
                    prefix="HOST_CFG"
                )
                return None

            # Log basic info about the payload
            log_debug_safe(
                self.logger,
                safe_format(
                    "Loaded device context with keys: {keys}",
                    keys=list(payload.keys())
                ),
                prefix="HOST_CFG"
            )

        except json.JSONDecodeError as e:
            log_warning_safe(
                self.logger,
                safe_format(
                    "Device context file is not valid JSON: {path} - {err}",
                    path=context_path,
                    err=str(e)
                ),
                prefix="HOST_CFG"
            )
            return None
        except (OSError, IOError) as e:
            log_warning_safe(
                self.logger,
                safe_format(
                    "Failed to read device context file: {path} - {err}",
                    path=context_path,
                    err=str(e)
                ),
                prefix="HOST_CFG"
            )
            return None

        # Extract config space hex
        config_space_hex = payload.get("config_space_hex")

        # Validate against metadata if available
        metadata = payload.get("collection_metadata", {})
        expected_length = metadata.get("config_space_hex_length")
        if expected_length and config_space_hex:
            actual_length = len(config_space_hex)
            if actual_length != expected_length:
                log_warning_safe(
                    self.logger,
                    safe_format(
                        "Config space hex length mismatch: expected {expected}, "
                        "got {actual} (possible corruption)",
                        expected=expected_length,
                        actual=actual_length
                    ),
                    prefix="HOST_CFG"
                )

        # Also support nested path when full template_context was saved by host
        if not config_space_hex:
            try:
                tc = payload.get("template_context") or {}
                if isinstance(tc, dict):
                    config_space_hex = tc.get("config_space_hex")
            except (AttributeError, KeyError, TypeError) as e:
                # Malformed template context structure
                log_debug_safe(
                    self.logger,
                    safe_format(
                        "Template context extraction failed: {err}",
                        err=str(e)
                    ),
                    prefix="HOST_CFG"
                )
                config_space_hex = None
            except Exception as e:
                # Unexpected error during template context extraction
                log_debug_safe(
                    self.logger,
                    safe_format(
                        "Unexpected error in template context extraction: {err}",
                        err=str(e)
                    ),
                    prefix="HOST_CFG"
                )
                config_space_hex = None

        # Debug: log the size of the hex string when loaded
        if config_space_hex:
            log_debug_safe(
                self.logger,
                safe_format(
                    "Loaded config_space_hex from JSON: {length} characters",
                    length=len(config_space_hex)
                ),
                prefix="HOST_CFG"
            )

            # Check if the hex string is truncated (common issue)
            if len(config_space_hex) < 512:  # Less than 256 bytes
                log_warning_safe(
                    self.logger,
                    safe_format(
                        "Config space hex appears truncated: {length} chars "
                        "(expected ~8192 for 4096 bytes)",
                        length=len(config_space_hex)
                    ),
                    prefix="HOST_CFG"
                )
                # Try to get the full config space from alternative sources
                if msix_path and os.path.exists(msix_path):
                    try:
                        with open(msix_path, "r") as f:
                            msix_payload = json.load(f)
                        alt_hex = msix_payload.get("config_space_hex")
                        if alt_hex and len(alt_hex) > len(config_space_hex):
                            log_info_safe(
                                self.logger,
                                "Using longer config_space_hex from MSIX data file",
                                prefix="HOST_CFG"
                            )
                            config_space_hex = alt_hex
                    except Exception as e:
                        log_debug_safe(
                            self.logger,
                            safe_format(
                                "Failed to load alternative config from MSIX: {err}",
                                err=str(e)
                            ),
                            prefix="HOST_CFG"
                        )
        if not config_space_hex:
            log_debug_safe(
                self.logger,
                safe_format(
                    "No config_space_hex in device context; trying MSI-X: {path}",
                    path=context_path,
                ),
                prefix="HOST_CFG",
            )
            # Fallback: try reading from msix_data.json if available
            try:
                if msix_path and os.path.exists(msix_path):
                    with open(msix_path, "r") as f:
                        msix_payload = json.load(f)
                    config_space_hex = msix_payload.get("config_space_hex")
                    if config_space_hex:
                        log_info_safe(
                            self.logger,
                            safe_format(
                                "Loaded config_space_hex from MSI-X ({size} chars)",
                                size=len(config_space_hex),
                            ),
                            prefix="HOST_CFG",
                        )
                        # Debug: log the actual size for verification
                        log_debug_safe(
                            self.logger,
                            safe_format(
                                "MSI-X config_space_hex length: {length} characters",
                                length=len(config_space_hex)
                            ),
                            prefix="HOST_CFG"
                        )
                    else:
                        log_debug_safe(
                            self.logger,
                            safe_format(
                                "MSI-X payload missing config_space_hex: {path}",
                                path=msix_path,
                            ),
                            prefix="HOST_CFG",
                        )
                else:
                    log_debug_safe(
                        self.logger,
                        safe_format(
                            "MSI-X data file not found at {path}",
                            path=msix_path,
                        ),
                        prefix="HOST_CFG",
                    )
            except json.JSONDecodeError as e:
                log_warning_safe(
                    self.logger,
                    safe_format(
                        "MSI-X data file invalid JSON: {path} - {err}",
                        path=msix_path,
                        err=str(e),
                    ),
                    prefix="HOST_CFG",
                )
            except (OSError, IOError) as e:
                log_warning_safe(
                    self.logger,
                    safe_format(
                        "Failed to read MSI-X data file: {path} - {err}",
                        path=msix_path,
                        err=str(e),
                    ),
                    prefix="HOST_CFG",
                )
            # If still no hex, give up gracefully
            if not config_space_hex:
                return None

        # Convert hex to bytes
        try:
            # Debug: log the size of the hex string before conversion
            log_debug_safe(
                self.logger,
                safe_format(
                    "Converting config_space_hex to bytes: {length} characters",
                    length=len(config_space_hex)
                ),
                prefix="HOST_CFG"
            )

            # If config space is severely truncated, fail gracefully
            if len(config_space_hex) < 128:  # Less than 64 bytes
                log_error_safe(
                    self.logger,
                    "Config space hex too short (< 64 bytes), corrupted data",
                    prefix="HOST_CFG"
                )
                return None

            config_space_bytes = bytes.fromhex(config_space_hex)
        except ValueError as e:
            log_warning_safe(
                self.logger,
                safe_format(
                    "Invalid hex string in config_space_hex: {err}",
                    err=str(e)
                ),
                prefix="HOST_CFG"
            )
            return None

        # Validate minimum size
        if len(config_space_bytes) < 64:
            log_warning_safe(
                self.logger,
                safe_format(
                    "Config space too small: {size} bytes (minimum 64 expected)",
                    size=len(config_space_bytes)
                ),
                prefix="HOST_CFG"
            )
            return None

        log_info_safe(
            self.logger,
            safe_format(
                "Loaded preloaded config space from host: {size} bytes",
                size=len(config_space_bytes)
            ),
            prefix="HOST_CFG"
        )
        return config_space_bytes

    def _check_host_collected_context(self) -> Optional[Dict[str, Any]]:
        """
        Check for complete device context collected on host.

        Returns:
            Complete device context if available, None otherwise
        """
        try:
            # Check for complete device context first
            context_path = os.environ.get(
                "DEVICE_CONTEXT_PATH", "/app/output/device_context.json"
            )
            if context_path and os.path.exists(context_path):
                with open(context_path, "r") as f:
                    payload = json.load(f)

                # Check for nested template_context (old format) 
                # or use payload directly
                template_context = payload.get("template_context")
                if template_context:
                    log_info_safe(
                        self.logger,
                        safe_format(
                            "Loaded complete device context from host: "
                            "{keys} keys available",
                            keys=len(template_context.keys())
                        ),
                        prefix="HOST_CTX"
                    )
                    return template_context
                
                # New format - device IDs directly in payload
                if "config_space_hex" in payload and "vendor_id" in payload:
                    log_info_safe(
                        self.logger,
                        safe_format(
                            "Loaded device context with device IDs from host: "
                            "VID=0x{vid:04x} DID=0x{did:04x}",
                            vid=payload.get("vendor_id", 0),
                            did=payload.get("device_id", 0)
                        ),
                        prefix="HOST_CTX"
                    )
                    return payload

        except Exception as e:
            log_debug_safe(
                self.logger,
                safe_format(
                    "Host context check failed: {err}",
                    err=str(e)
                ),
                prefix="HOST_CTX"
            )

        return None

    def _preload_msix(self) -> MSIXData:
        """Preload MSI-X data if configured."""
        if self.config.preload_msix:
            return self.msix_manager.preload_data()
        return MSIXData(preloaded=False)

    def _generate_firmware(
        self, donor_template: Optional[Dict[str, Any]] = None
    ) -> Dict[str, Any]:
        """Generate PCILeech firmware with optional donor template."""
        if donor_template:
            # Pass the donor template to the generator config
            self.gen.config.donor_template = donor_template
        result = self.gen.generate_pcileech_firmware()

        # Ensure a conservative template_context exists with MSI-X defaults.
        result.setdefault("template_context", {})
        result.setdefault("systemverilog_modules", {})
        result.setdefault("config_space_data", {})
        result.setdefault("msix_data", None)

        tc = result["template_context"]
        # Provide conservative MSI-X defaults if missing
        tc.setdefault(
            "msix_config",
            {
                "is_supported": False,
                "num_vectors": 0,
            },
        )
        # Include msix_data key (None by default) for callers that rely on it
        tc.setdefault("msix_data", None)

        # Inject config space hex/COE into template context if missing
        try:
            from src.device_clone.hex_formatter import ConfigSpaceHexFormatter

            config_space_bytes = None
            # Try to get config space bytes from result
            if "config_space_data" in result:
                config_space_bytes = result["config_space_data"].get(
                    "raw_config_space"
                )
                if not config_space_bytes:
                    # Try config_space_bytes key
                    config_space_bytes = result["config_space_data"].get(
                        "config_space_bytes"
                    )
            if not config_space_bytes and "template_context" in result:
                config_space_bytes = result["template_context"].get(
                    "config_space_bytes"
                )
            # If we have config space bytes, format and inject
            if config_space_bytes:
                formatter = ConfigSpaceHexFormatter()
                config_space_hex = formatter.format_config_space_to_hex(
                    config_space_bytes
                )
                # Inject into template context
                if "template_context" in result:
                    result["template_context"]["config_space_hex"] = config_space_hex
                    # Also inject config_space_coe for template compatibility
                    result["template_context"]["config_space_coe"] = config_space_hex
        except Exception as e:
            # Fail build if hex generation fails
            log_error_safe(
                self.logger,
                safe_format("Config space hex generation failed: {err}", err=str(e)),
                prefix="BUILD",
            )
            raise PCILeechBuildError(
                f"Failed to generate config space hex: {e}"
            ) from e

        # Emit audit file of top-level template context keys to verify propagation.
        try:
            ctx = result.get("template_context", {}) or {}
            keys = sorted(ctx.keys())
            audit = {
                "context_key_count": len(keys),
                "context_keys": keys,
                "generated_at": time.time(),
            }
            audit_path = self.config.output_dir / "template_context_keys.json"
            with open(audit_path, "w") as f:
                json.dump(audit, f, indent=2)
            log_debug_safe(
                self.logger,
                safe_format(
                    "Template context audit written ({count} keys) → {path}",
                    count=len(keys),
                    path=str(audit_path),
                ),
                prefix="BUILD",
            )
        except Exception as e:
            log_debug_safe(
                self.logger,
                safe_format("Template context audit skipped: {err}", err=str(e)),
                prefix="BUILD",
            )

        return result

    def _recheck_vfio_bindings(self) -> None:
        """Recheck VFIO bindings via canonical helper and log the outcome."""
        if getattr(self.config, "disable_vfio", False):
            log_info_safe(
                self.logger,
                "VFIO binding recheck skipped (disabled)",
                prefix="VFIO",
            )
            return
        try:
            from src.cli.vfio_helpers import ensure_device_vfio_binding
        except ImportError:
            # Helper module not available (expected in some environments)
            log_debug_safe(
                self.logger,
                "VFIO binding recheck skipped: helper module not available",
                prefix="VFIO",
            )
            return
        except Exception as e:
            # Unexpected import error
            log_debug_safe(
                self.logger,
                safe_format(
                    "VFIO binding recheck skipped: import failed - {err}",
                    err=str(e)
                ),
                prefix="VFIO",
            )
            return

        group_id = ensure_device_vfio_binding(self.config.bdf)
        log_warning_safe(
            self.logger,
            safe_format(
                "VFIO binding recheck passed: bdf={bdf} group={group}",
                bdf=self.config.bdf,
                group=str(group_id),
            ),
            prefix="VFIO",
        )

    def _inject_msix(self, result: Dict[str, Any], msix_data: MSIXData) -> None:
        """Inject MSI-X data into generation result."""
        self.msix_manager.inject_data(result, msix_data)

    def _write_modules(self, result: Dict[str, Any]) -> None:
        """Write SystemVerilog modules to disk."""
        modules = result.get("systemverilog_modules", {})
        if not modules:
            log_warning_safe(
                self.logger,
                "No SystemVerilog modules in generation result",
                prefix="BUILD",
            )
            return

        sv_files, special_files = self.file_manager.write_systemverilog_modules(
            modules
        )

        log_info_safe(
            self.logger,
            safe_format(
                "Wrote {count} SystemVerilog modules: {files}",
                count=len(sv_files),
                files=", ".join(sv_files),
            ),
            prefix="BUILD",
        )
        if special_files:
            log_info_safe(
                self.logger,
                safe_format(
                    "Wrote {count} special files: {files}",
                    count=len(special_files),
                    files=", ".join(special_files),
                ),
                prefix="BUILD",
            )

    def _generate_profile(self) -> None:
        """Generate behavior profile if configured."""
        # Skip in host-context-only mode to avoid any device access
        if getattr(self.config, "disable_vfio", False):
            log_info_safe(
                self.logger,
                "Profiling disabled (host-context-only mode)",
                prefix="BUILD",
            )
            return
        if self.profiler and self.config.profile_duration > 0:
            profile = self.profiler.capture_behavior_profile(
                duration=self.config.profile_duration
            )
            self.file_manager.write_json("behavior_profile.json", profile)
            log_info_safe(
                self.logger,
                "Saved behavior profile to behavior_profile.json",
                prefix="BUILD",
            )

    def _generate_tcl_scripts(self, result: Dict[str, Any]) -> None:
        """Copy static Vivado TCL scripts from submodule to output directory."""
        # Validate board is present and non-empty
        board = self.config.board
        if not board or not board.strip():
            raise ConfigurationError(
                "Board name is required for TCL script copying. "
                "Use --board to specify a valid board configuration "
                "(e.g., pciescreamer, ac701_ft601)"
            )

        # Ensure RTL and constraints from the PCILeech submodule are available
        # for Vivado by copying them into the output structure
        try:
            from src.file_management.file_manager import FileManager as _FM

            fm = _FM(self.config.output_dir)
            # Ensure src/ and ip/ directories exist
            fm.create_pcileech_structure()
            # Copy sources and constraints from the submodule and local pcileech/
            fm.copy_pcileech_sources(self.config.board)

            # Copy static TCL scripts from submodule instead of generating them
            tcl_scripts = fm.copy_vivado_tcl_scripts(self.config.board)

            log_info_safe(
                self.logger,
                safe_format(
                    "  • Copied {count} Vivado TCL scripts from submodule",
                    count=len(tcl_scripts)
                ),
                prefix="BUILD",
            )

        except Exception as e:
            log_error_safe(
                self.logger,
                safe_format(
                    "Failed to copy TCL scripts: {err}",
                    err=str(e),
                ),
                prefix="BUILD",
            )
            raise

    def _write_xdc_files(self, result: Dict[str, Any]) -> None:
        """Write XDC constraint files to output directory."""
        ctx = result.get("template_context", {})
        board_xdc_content = ctx.get("board_xdc_content", "")

        if not board_xdc_content:
            log_warning_safe(
                self.logger,
                "No board XDC content available to write",
                prefix="BUILD",
            )
            return

        # Create constraints directory
        constraints_dir = self.config.output_dir / "constraints"
        constraints_dir.mkdir(parents=True, exist_ok=True)

        # Write board-specific XDC file
        board_name = self.config.board
        xdc_filename = f"{board_name}.xdc"
        xdc_path = constraints_dir / xdc_filename

        xdc_path.write_text(board_xdc_content, encoding="utf-8")

        log_info_safe(
            self.logger,
            safe_format(
                "Wrote XDC constraints file: {filename} ({size} bytes)",
                filename=xdc_filename,
                size=len(board_xdc_content),
            ),
            prefix="BUILD",
        )

    def _save_device_info(self, result: Dict[str, Any]) -> None:
        """Save device information for auditing."""
        config_space_data = result.get("config_space_data", {})
        device_info = config_space_data.get("device_info", {})
        if not device_info:
            log_warning_safe(
                self.logger,
                "No device info available to save",
                prefix="BUILD",
            )
            return
        self.file_manager.write_json("device_info.json", device_info)

    def _store_device_config(self, result: Dict[str, Any]) -> None:
        """Store device configuration for Vivado integration."""
        ctx = result.get("template_context", {})
        msix_data = result.get("msix_data", {})

        # Ensure msix_data is a dictionary and not None
        if msix_data is None:
            msix_data = {}

        # Pass the boolean indicator for MSIX presence instead of the data itself
        has_msix = "msix_data" in result and result["msix_data"] is not None
        self._device_config = self.config_manager.extract_device_config(
            ctx, has_msix
        )

    def _run_post_build_validation(self, result: Dict[str, Any]) -> None:
        """Run post-build validation checks for driver compatibility."""
        from src.utils.post_build_validator import (
            PostBuildValidator,
            PostBuildValidationCheck
        )

        validator = PostBuildValidator(self.logger)

        # Run comprehensive validation
        is_valid, validation_results = validator.validate_build_output(
            output_dir=self.config.output_dir,
            generation_result=result
        )

        # Print validation report
        validator.print_validation_report()

        # Log critical findings
        errors = [r for r in validation_results if r.severity == "error"]
        if errors:
            log_warning_safe(
                self.logger,
                safe_format(
                    "Build completed with {count} validation errors - "
                    "firmware may not work with OS drivers",
                    count=len(errors)
                ),
                prefix="BUILD"
            )

        # Save validation report to JSON
        warnings = [r for r in validation_results if r.severity == "warning"]
        validation_data = {
            "validation_passed": is_valid,
            "total_checks": len(validation_results),
            "errors": len(errors),
            "warnings": len(warnings),
            "results": [
                {
                    "check": r.check_name,
                    "valid": r.is_valid,
                    "severity": r.severity,
                    "message": r.message,
                    "details": r.details
                }
                for r in validation_results
            ]
        }

        self.file_manager.write_json(
            "validation_report.json",
            validation_data
        )

    def _generate_donor_template(self, result: Dict[str, Any]) -> None:
        """Generate and save donor info template if requested."""
        from .device_clone.donor_info_template import DonorInfoTemplateGenerator

        # Get device info from the result
        device_info = result.get("config_space_data", {}).get("device_info", {})
        template_context = result.get("template_context", {})
        device_config = template_context.get("device_config", {})

        # Create a pre-filled template
        generator = DonorInfoTemplateGenerator()
        template = generator.generate_blank_template()

        # Pre-fill with available device information
        if device_config:
            ident = template["device_info"]["identification"]
            ident["vendor_id"] = device_config.get("vendor_id")
            ident["device_id"] = device_config.get("device_id")
            ident["subsystem_vendor_id"] = device_config.get("subsystem_vendor_id")
            ident["subsystem_device_id"] = device_config.get("subsystem_device_id")
            ident["class_code"] = device_config.get("class_code")
            ident["revision_id"] = device_config.get("revision_id")

        # Add BDF if available
        template["metadata"]["device_bdf"] = self.config.bdf

        # Save the template
        if self.config.output_template:
            output_path = Path(self.config.output_template)
            if not output_path.is_absolute():
                output_path = self.config.output_dir / output_path

            generator.save_template_dict(template, output_path, pretty=True)
            log_info_safe(
                self.logger,
                safe_format(
                    "Generated donor info template {name}", name=output_path.name
                ),
                prefix="BUILD",
            )


# ──────────────────────────────────────────────────────────────────────────────
# CLI Functions
# ──────────────────────────────────────────────────────────────────────────────


def parse_args(argv: Optional[List[str]] = None) -> argparse.Namespace:
    """
    Parse command line arguments.

    Args:
        argv: Command line arguments (uses sys.argv if None)

    Returns:
        Parsed arguments namespace
    """
    parser = argparse.ArgumentParser(
        description=("PCILeech FPGA Firmware Builder - Improved Modular Edition"),
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=(
            "Examples:\n"
            "  # Basic build\n"
            "  %(prog)s --bdf 0000:03:00.0 --board pcileech_35t325_x4\n\n"
            "  # Build with Vivado integration\n"
            "  %(prog)s --bdf 0000:03:00.0 --board pcileech_35t325_x4 --vivado\n\n"
            "  # Build with custom Vivado settings\n"
            "  %(prog)s --bdf 0000:03:00.0 --board pcileech_35t325_x4 "
            "--vivado-path /tools/Xilinx/2025.1/Vivado --vivado-jobs 8\n\n"
            "  # Build with behavior profiling\n"
            "  %(prog)s --bdf 0000:03:00.0 --board pcileech_35t325_x4 "
            "--profile 60\n\n"
            "  # Build without MSI-X preloading\n"
            "  %(prog)s --bdf 0000:03:00.0 --board pcileech_35t325_x4 "
            "--no-preload-msix\n"
        ),
    )

    parser.add_argument(
        "--bdf",
        required=True,
        help="PCI Bus/Device/Function address (e.g., 0000:03:00.0)",
    )
    parser.add_argument(
        "--board",
        required=True,
        help="Target FPGA board key (e.g., pcileech_35t325_x4)",
    )
    parser.add_argument(
        "--profile",
        type=int,
        default=DEFAULT_PROFILE_DURATION,
        metavar="SECONDS",
        help=(
            "Capture behavior profile for N seconds (default: "
            f"{DEFAULT_PROFILE_DURATION}, 0 to disable)"
        ),
    )
    parser.add_argument(
        "--vivado", action="store_true", help="Run Vivado build after generation"
    )
    parser.add_argument(
        "--output",
        default=DEFAULT_OUTPUT_DIR,
        help=f"Output directory (default: {DEFAULT_OUTPUT_DIR})",
    )
    parser.add_argument(
        "--no-preload-msix",
        action="store_false",
        dest="preload_msix",
        default=True,
        help="Disable preloading of MSI-X data before VFIO binding",
    )
    parser.add_argument(
        "--host-context-only",
        action="store_true",
        help=(
            "Do not touch VFIO/sysfs; require DEVICE_CONTEXT_PATH/MSIX_DATA_PATH"
        ),
    )
    parser.add_argument(
        "--output-template",
        help="Output donor info JSON template alongside build artifacts",
    )
    parser.add_argument(
        "--donor-template",
        help="Use donor info JSON template to override discovered values",
    )
    parser.add_argument(
        "--vivado-path",
        help=(
            "Manual path to Vivado installation directory (e.g., "
            "/tools/Xilinx/2025.1/Vivado)"
        ),
    )
    parser.add_argument(
        "--vivado-jobs",
        type=int,
        default=4,
        help="Number of parallel jobs for Vivado builds (default: 4)",
    )
    parser.add_argument(
        "--vivado-timeout",
        type=int,
        default=3600,
        help="Timeout for Vivado operations in seconds (default: 3600)",
    )

    # MMIO Learning Arguments
    parser.add_argument(
        "--no-mmio-learning",
        action="store_true",
        help=(
            "Disable automatic MMIO trace capture for BAR register learning. "
            "When disabled, uses synthetic BAR content generation."
        ),
    )

    parser.add_argument(
        "--force-recapture",
        action="store_true",
        help=(
            "Force recapture of MMIO traces even if cached models exist. "
            "Useful when driver or device behavior has changed."
        ),
    )

    parser.add_argument(
        "--enable-error-injection",
        action="store_true",
        help=(
            "Enable hardware error injection test hooks (AER). Disabled by default; "
            "use only in controlled validation scenarios."
        ),
    )

    parser.add_argument(
        "--issue-report-json",
        metavar="PATH",
        help=(
            "If the build fails, write a structured machine-readable JSON error "
            "report to PATH (for GitHub issues)."
        ),
    )

    parser.add_argument(
        "--print-issue-report",
        action="store_true",
        help=(
            "On failure emit the structured JSON issue report to stdout "
            "(in addition to normal logging)."
        ),
    )

    parser.add_argument(
        "--no-repro-hint",
        action="store_true",
        help="Suppress the reproduction command hint on failure.",
    )

    return parser.parse_args(argv)


# ──────────────────────────────────────────────────────────────────────────────
# Main Entry Point
# ──────────────────────────────────────────────────────────────────────────────


def main(argv: Optional[List[str]] = None) -> int:
    """
    Main entry point for the PCILeech firmware builder.

    This function orchestrates the entire build process:
    1. Validates required modules
    2. Parses command line arguments
    3. Creates build configuration
    4. Runs the firmware build
    5. Optionally runs Vivado

    Args:
        argv: Command line arguments (uses sys.argv if None)

    Returns:
        Exit code (0 for success, non-zero for failure)
    """
    # Setup logging if not already configured
    if not logging.getLogger().handlers:
        setup_logging(level=logging.INFO)

    logger = get_logger("pcileech_builder")

    # Initialize args to None to handle exceptions before parsing
    args = None

    try:
        # Reset template validation caches unless explicitly disabled.
        # Avoid stale state in long-lived local processes.
        if not os.environ.get("PCILEECH_DISABLE_TEMPLATE_CACHE_RESET"):
            clear_global_template_cache()
            log_debug_safe(logger, "Template validation cache reset at build start")
        # Check required modules
        module_checker = ModuleChecker(REQUIRED_MODULES)
        module_checker.check_all()

        # Parse arguments
        args = parse_args(argv)

        # Create configuration
        config_manager = ConfigurationManager(logger)
        config = config_manager.create_from_args(args)

        # Time the build
        start_time = time.perf_counter()

        # Create and run builder
        builder = FirmwareBuilder(config, logger=logger)
        artifacts = builder.build()

        # Calculate elapsed time
        elapsed_time = time.perf_counter() - start_time
        log_info_safe(logger, "Build finished in {secs:.1f} s ✓", secs=elapsed_time)

        # Run Vivado if requested
        if args.vivado:
            builder.run_vivado()

        # Display summary
        _display_summary(artifacts, config.output_dir, logger=logger)

        return 0

    except ModuleImportError as e:
        # Module import errors are fatal and should show diagnostics
        print(f"[FATAL] {e}", file=sys.stderr)
        _maybe_emit_issue_report(e, logger, args if "args" in locals() else None)
        return 2

    except PlatformCompatibilityError as e:
        # Platform compatibility errors - log once at info level since details
        # were already logged
        log_info_safe(
            logger, "Build skipped due to platform compatibility: {err}", err=str(e)
        )
        _maybe_emit_issue_report(e, logger, args if "args" in locals() else None)
        return 1

    except ConfigurationError as e:
        # Configuration errors indicate user error
        log_error_safe(logger, "Configuration error: {err}", err=str(e))
        _maybe_emit_issue_report(e, logger, args if "args" in locals() else None)
        return 1

    except (FileOperationError, VivadoIntegrationError) as e:
        # Known build subsystem errors
        error_type = type(e).__name__
        log_error_safe(
            logger,
            "Build failed ({error_type}): {err}",
            error_type=error_type,
            err=str(e)
        )
        if logger.isEnabledFor(logging.DEBUG):
            import traceback
            log_debug_safe(
                logger,
                "Full traceback:\n{tb}",
                tb=traceback.format_exc()
            )
        _maybe_emit_issue_report(e, logger, args)
        return 1

    except PCILeechBuildError as e:
        # Known build errors
        log_error_safe(logger, "Build failed: {err}", err=str(e))
        if logger.isEnabledFor(logging.DEBUG):
            import traceback
            log_debug_safe(
                logger,
                "Full traceback:\n{tb}",
                tb=traceback.format_exc()
            )
        _maybe_emit_issue_report(e, logger, args)
        return 1

    except KeyboardInterrupt:
        # User interrupted
        log_warning_safe(logger, "Build interrupted by user", prefix="BUILD")
        return 130

    except Exception as e:
        # Check if this is a platform compatibility error
        error_str = str(e)
        error_type = type(e).__name__
        if (
            "requires Linux" in error_str
            or "platform incompatibility" in error_str
            or "only available on Linux" in error_str
        ):
            # Platform compatibility errors were already logged in detail
            log_info_safe(
                logger,
                "Build skipped due to platform compatibility (see details above)",
                prefix="BUILD",
            )
        else:
            # Unexpected errors
            log_error_safe(
                logger,
                "Unexpected error ({error_type}): {err}",
                error_type=error_type,
                err=str(e),
                prefix="BUILD"
            )
            if logger.isEnabledFor(logging.DEBUG):
                import traceback
                log_debug_safe(
                    logger,
                    "Full traceback for unexpected error:\n{tb}",
                    tb=traceback.format_exc(),
                    prefix="BUILD"
                )
        _maybe_emit_issue_report(e, logger, args)
        return 1


def _display_summary(
    artifacts: List[str], output_dir: Path, logger: logging.Logger
) -> None:
    """
    Display a summary of generated artifacts.

    Args:
        artifacts: List of artifact paths
        output_dir: Output directory path
    """
    log_info_safe(
        logger,
        "\nGenerated artifacts in {dir}",
        dir=str(output_dir),
        prefix="SUMMARY"
    )

    # Group artifacts by type
    sv_files = [a for a in artifacts if a.endswith(".sv")]
    tcl_files = [a for a in artifacts if a.endswith(".tcl")]
    json_files = [a for a in artifacts if a.endswith(".json")]
    other_files = [
        a for a in artifacts if a not in sv_files + tcl_files + json_files
    ]

    if sv_files:
        log_info_safe(
            logger,
            "\n  SystemVerilog modules ({count}):",
            count=len(sv_files),
            prefix="SUMMARY",
        )
        for f in sorted(sv_files):
            log_info_safe(logger, "    - {file}", file=f, prefix="SUMMARY")

    if tcl_files:
        log_info_safe(
            logger,
            "\n  TCL scripts ({count}):",
            count=len(tcl_files),
            prefix="SUMMARY"
        )
        for f in sorted(tcl_files):
            log_info_safe(logger, "    - {file}", file=f, prefix="SUMMARY")

    if json_files:
        log_info_safe(
            logger,
            "\n  JSON files ({count}):",
            count=len(json_files),
            prefix="SUMMARY"
        )
        for f in sorted(json_files):
            log_info_safe(logger, "    - {file}", file=f, prefix="SUMMARY")

    if other_files:
        log_info_safe(
            logger,
            "\n  Other files ({count}):",
            count=len(other_files),
            prefix="SUMMARY",
        )
        for f in sorted(other_files):
            log_info_safe(logger, "    - {file}", file=f, prefix="SUMMARY")

    log_info_safe(logger, "\nTotal: {n} files", n=len(artifacts), prefix="SUMMARY")


def _maybe_emit_issue_report(
    exc: Exception, logger: logging.Logger, args: Optional[argparse.Namespace]
) -> None:
    """Emit structured issue report if user requested it via CLI flags.

    Safe best-effort; never raises.
    """
    if not args:
        return
    want_file = getattr(args, "issue_report_json", None)
    want_stdout = getattr(args, "print_issue_report", False)
    repro_disabled = getattr(args, "no_repro_hint", False)
    repro_cmd = None
    if not repro_disabled:
        repro_cmd = _build_reproduction_command(args)

    try:
        from src.error_utils import (
            build_issue_report,
            format_issue_report_human_hint,
            write_issue_report,
        )

        report = None
        if want_file or want_stdout:
            build_args = [a for a in sys.argv[1:]]
            report = build_issue_report(
                exc,
                context="firmware-build",
                build_args=build_args,
                extra_metadata={
                    "selected_board": getattr(args, "board", None),
                    "bdf": getattr(args, "bdf", None),
                },
                include_traceback=logger.isEnabledFor(logging.DEBUG),
            )

        if report is not None:
            path_used = None
            if want_file:
                ok, err = write_issue_report(want_file, report)
                if not ok:
                    log_warning_safe(
                        logger,
                        "Failed to write issue report JSON: {err}",
                        err=err,
                    )
                else:
                    path_used = want_file

            if want_stdout:
                print(json.dumps(report, indent=2, sort_keys=True))

            hint = format_issue_report_human_hint(path_used, report)
            log_info_safe(logger, hint.rstrip())

        if repro_cmd:
            log_info_safe(
                logger,
                safe_format("Reproduce with: {cmd}", cmd=repro_cmd),
                prefix="BUILD",
            )
    except Exception as emit_err:  # pragma: no cover - best effort
        log_warning_safe(
            logger,
            safe_format("Issue report generation failed: {err}", err=str(emit_err)),
            prefix="BUILD",
        )


def _build_reproduction_command(args: argparse.Namespace) -> str:
    """Build a reproduction command from original arguments.

    Sensitive values are kept because reproduction requires them; users can
    manually redact if desired. Output paths are normalized.
    """
    parts: List[str] = ["python3", "-m", "src.build"]

    def _add(flag: str, value: Optional[str]) -> None:
        if value is None:
            return
        parts.append(flag)
        parts.append(str(value))

    _add("--bdf", getattr(args, "bdf", None))
    _add("--board", getattr(args, "board", None))
    if getattr(args, "profile", None) is not None:
        _add("--profile", getattr(args, "profile"))
    if getattr(args, "donor_template", None):
        _add("--donor-template", getattr(args, "donor_template"))
    if getattr(args, "output_template", None):
        _add("--output-template", getattr(args, "output_template"))
    if getattr(args, "vivado", False):
        parts.append("--vivado")
    if getattr(args, "vivado_path", None):
        _add("--vivado-path", getattr(args, "vivado_path"))
    if getattr(args, "vivado_jobs", None) not in (None, 4):
        _add("--vivado-jobs", getattr(args, "vivado_jobs"))
    if getattr(args, "vivado_timeout", None) not in (None, 3600):
        _add("--vivado-timeout", getattr(args, "vivado_timeout"))
    if not getattr(args, "preload_msix", True):
        parts.append("--no-preload-msix")
    if getattr(args, "enable_error_injection", False):
        parts.append("--enable-error-injection")
    return " ".join(parts)


# ──────────────────────────────────────────────────────────────────────────────
# Script Entry Point
# ──────────────────────────────────────────────────────────────────────────────
if __name__ == "__main__":
    sys.exit(main())
