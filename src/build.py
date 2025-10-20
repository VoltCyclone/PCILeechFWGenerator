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
import json
import logging
import os
import re
import sys
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Callable, Dict, List, Optional, Tuple, Union

from src.string_utils import (
    log_debug_safe,
    log_error_safe,
    log_info_safe,
    log_warning_safe,
    safe_format,
)
from src.templating.template_context_validator import clear_global_template_cache

# Import board functions from the correct module
from .device_clone.constants import PRODUCTION_DEFAULTS

# Import msix_capability at the module level to avoid late imports
from .device_clone.msix_capability import parse_msix_capability
from .exceptions import (
    ConfigurationError,
    FileOperationError,
    ModuleImportError,
    MSIXPreloadError,
    PCILeechBuildError,
    PlatformCompatibilityError,
    VivadoIntegrationError,
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

def _as_int(value: Union[int, str], field: str) -> int:
    """Normalize numeric identifier that may be int or hex string."""
    if isinstance(value, int):
        return value
    if isinstance(value, str):
        v = value.strip().lower()
        if v.startswith("0x"):
            v = v[2:]
        return int(v, 16)
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
                log_info_safe(self.logger, "No MSI-X capability found", prefix="MSIX")
                return MSIXData(preloaded=False, msix_info=None)

        except Exception as e:
            log_warning_safe(
                self.logger,
                safe_format("MSI-X preload failed: {err}", err=str(e)),
                prefix="MSIX",
            )
            if self.logger.isEnabledFor(logging.DEBUG):
                log_debug_safe(
                    self.logger,
                    safe_format("MSI-X preload exception details: {err}", err=str(e)),
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
        sv_dir.mkdir(exist_ok=True)

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
        except Exception as e:
            raise FileOperationError(
                f"Failed to write JSON file {filename}: {e}"
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
        except Exception as e:
            raise FileOperationError(
                f"Failed to write text file {filename}: {e}"
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

            for future in as_completed(futures, timeout=FILE_WRITE_TIMEOUT):
                path = futures[future]
                try:
                    future.result()
                except Exception as e:
                    raise FileOperationError(f"Failed to write file {path}: {e}") from e

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
            except Exception as e:
                raise FileOperationError(f"Failed to write file {path}: {e}") from e

    def _write_single_file(self, path: Path, content: str) -> None:
        """
        Write a single file.

        Args:
            path: File path
            content: File content
        """
        with open(path, "w", buffering=BUFFER_SIZE) as f:
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
        use_prod = bool(os.environ.get("PCILEECH_PRODUCTION_DEFAULTS"))
        enable_profiling = args.profile > 0
        preload_msix = getattr(args, "preload_msix", True)
        if use_prod:
            # Map production flags when present
            enable_profiling = PRODUCTION_DEFAULTS.get("BEHAVIOR_PROFILING", True)
            preload_msix = PRODUCTION_DEFAULTS.get("MSIX_CAPABILITY", True)

        return BuildConfiguration(
            bdf=args.bdf,
            board=args.board,
            output_dir=Path(args.output).resolve(),
            enable_profiling=enable_profiling,
            preload_msix=preload_msix,
            profile_duration=args.profile,
            output_template=getattr(args, "output_template", None),
            donor_template=getattr(args, "donor_template", None),
            vivado_path=getattr(args, "vivado_path", None),
            vivado_jobs=getattr(args, "vivado_jobs", 4),
            vivado_timeout=getattr(args, "vivado_timeout", 3600),
            enable_error_injection=getattr(args, "enable_error_injection", False),
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

        for field, display_name in required_fields.items():
            value = device_config.get(field)
            if value is None:
                raise ConfigurationError(
                    "Cannot generate device-specific firmware without "
                    f"valid {display_name}."
                )

            # Check for invalid/generic values that could create non-unique firmware
            if isinstance(value, (int, str)):
                int_value = int(value, 16) if isinstance(value, str) else value
                if int_value == 0:
                    raise ConfigurationError(
                        f"{display_name} is zero (0x{int_value:04X}), which "
                        "indicates "
                        "a generic or uninitialized value. Use a real device for cloning."
                    )

        # Additional validation for vendor/device ID pairs that are known generics
        vendor_id = _as_int(device_config["vendor_id"], "vendor_id")
        device_id = _as_int(device_config["device_id"], "device_id")

        # Validate that vendor/device IDs are not zero or obviously invalid
        # Generic firmware prevention is handled through donor device integrity checks
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

    def _phase(self, message: str) -> None:
        """Log a build phase message with standardized formatting."""
        log_info_safe(
            self.logger,
            safe_format("➤ {msg}", msg=message),
            prefix="BUILD",
        )

    def build(self) -> List[str]:
        """
        Run the full firmware generation flow.

        Returns:
            List of generated artifact paths (relative to output directory)

        Raises:
            PCILeechBuildError: If build fails
        """
        try:
            # Step 1: Load donor template if provided
            donor_template = self._load_donor_template()

            # Step 2: Preload MSI-X data if requested
            msix_data = self._preload_msix()

            self._phase("Generating PCILeech firmware …")
            # Step 3: Generate PCILeech firmware
            generation_result = self._generate_firmware(donor_template)

            # Step 3: Inject preloaded MSI-X data if available
            self._inject_msix(generation_result, msix_data)

            self._phase("Writing SystemVerilog modules …")
            # Step 4: Write SystemVerilog modules
            self._write_modules(generation_result)

            self._phase("Generating behavior profile …")
            # Step 5: Generate behavior profile if requested
            self._generate_profile()

            self._phase("Generating TCL scripts …")
            # Step 6: Generate TCL scripts
            self._generate_tcl_scripts(generation_result)

            # Step 6.5: Write XDC constraint files
            self._write_xdc_files(generation_result)

            self._phase("Saving device information …")
            # Step 7: Save device information
            self._save_device_info(generation_result)

            # Step 8: Store device configuration
            self._store_device_config(generation_result)

            # Step 9: Generate donor template if requested
            if self.config.output_template:
                self._phase("Writing donor template …")
                self._generate_donor_template(generation_result)

            # Return list of artifacts
            return self.file_manager.list_artifacts()

        except PlatformCompatibilityError:
            # Reraise platform compatibility errors without modification
            raise
        except Exception as e:
            log_error_safe(
                self.logger,
                safe_format("Build failed: {err}", err=str(e)),
                prefix="BUILD",
            )
            raise PCILeechBuildError(
                safe_format("Build failed: {err}", err=str(e))
            ) from e

    def run_vivado(self) -> None:
        """
        Hand-off to Vivado in batch mode using the simplified VivadoRunner.

        Raises:
            VivadoIntegrationError: If Vivado integration fails
        """
        try:
            from .vivado_handling import VivadoRunner, find_vivado_installation
        except ImportError as e:
            raise VivadoIntegrationError("Vivado handling modules not available") from e

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
        from .device_clone.behavior_profiler import BehaviorProfiler
        from .device_clone.board_config import get_pcileech_board_config
        from .device_clone.pcileech_generator import (
            PCILeechGenerationConfig,
            PCILeechGenerator,
        )
        from .templating.tcl_builder import BuildContext, TCLBuilder

        self.gen = PCILeechGenerator(
            PCILeechGenerationConfig(
                device_bdf=self.config.bdf,
                board=self.config.board,
                template_dir=None,
                output_dir=self.config.output_dir,
                enable_behavior_profiling=self.config.enable_profiling,
                enable_error_injection=getattr(
                    self.config, "enable_error_injection", False
                ),
            )
        )

        self.tcl = TCLBuilder(output_dir=self.config.output_dir)
        self.profiler = BehaviorProfiler(bdf=self.config.bdf)

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
        # This prevents template generation from crashing when the generator
        # returns a minimal result.
        if "template_context" not in result or not isinstance(
            result.get("template_context"), dict
        ):
            result["template_context"] = {}

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
                config_space_bytes = result["config_space_data"].get("raw_config_space")
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
            # Log but do not fail build if hex generation fails
            log_warning_safe(
                self.logger,
                safe_format("Config space hex generation failed: {err}", err=str(e)),
                prefix="BUILD",
            )

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
        try:
            from src.cli.vfio_helpers import ensure_device_vfio_binding
        except Exception:
            # Helper not available; keep quiet in production paths
            log_info_safe(
                self.logger,
                "VFIO binding recheck skipped: helper unavailable",
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
        sv_files, special_files = self.file_manager.write_systemverilog_modules(
            result["systemverilog_modules"]
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
        if self.config.profile_duration > 0:
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
        """Generate TCL scripts for Vivado."""
        ctx = result["template_context"]
        device_config = ctx["device_config"]

        # Validate board is present and non-empty
        board = self.config.board
        if not board or not board.strip():
            raise ConfigurationError(
                "Board name is required for TCL generation. "
                "Use --board to specify a valid board configuration "
                "(e.g., pcileech_100t484_x1)"
            )

        # Extract optional subsystem IDs
        subsys_vendor_id = _optional_int(device_config.get("subsystem_vendor_id"))
        subsys_device_id = _optional_int(device_config.get("subsystem_device_id"))

        # Extract PCIe link speed and width from template context
        # These are critical for donor-unique firmware generation
        pcie_max_link_speed = ctx.get("pcie_max_link_speed")
        pcie_max_link_width = ctx.get("pcie_max_link_width")

        self.tcl.build_all_tcl_scripts(
            board=board,
            device_id=device_config["device_id"],
            class_code=device_config["class_code"],
            revision_id=device_config["revision_id"],
            vendor_id=device_config["vendor_id"],
            subsys_vendor_id=subsys_vendor_id,
            subsys_device_id=subsys_device_id,
            build_jobs=self.config.vivado_jobs,
            build_timeout=self.config.vivado_timeout,
            pcie_max_link_speed_code=pcie_max_link_speed,
            pcie_max_link_width=pcie_max_link_width,
        )

        log_info_safe(
            self.logger,
            "  • Emitted Vivado scripts → vivado_project.tcl, vivado_build.tcl",
            prefix="BUILD",
        )

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
        device_info = result["config_space_data"].get("device_info", {})
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
        self._device_config = self.config_manager.extract_device_config(ctx, has_msix)

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

    except PCILeechBuildError as e:
        # Known build errors
        log_error_safe(logger, "Build failed: {err}", err=str(e))
        if logger.isEnabledFor(logging.DEBUG):
            log_debug_safe(logger, "Full traceback while handling build error")
        _maybe_emit_issue_report(e, logger, args if "args" in locals() else None)
        return 1

    except KeyboardInterrupt:
        # User interrupted
        log_warning_safe(logger, "Build interrupted by user", prefix="BUILD")
        return 130

    except Exception as e:
        # Check if this is a platform compatibility error
        error_str = str(e)
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
                logger, "Unexpected error: {err}", err=str(e), prefix="BUILD"
            )
            log_debug_safe(
                logger, "Full traceback for unexpected error", prefix="BUILD"
            )
        _maybe_emit_issue_report(e, logger, args if "args" in locals() else None)
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
        logger, "\nGenerated artifacts in {dir}", dir=str(output_dir), prefix="SUMMARY"
    )

    # Group artifacts by type
    sv_files = [a for a in artifacts if a.endswith(".sv")]
    tcl_files = [a for a in artifacts if a.endswith(".tcl")]
    json_files = [a for a in artifacts if a.endswith(".json")]
    other_files = [a for a in artifacts if a not in sv_files + tcl_files + json_files]

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
            logger, "\n  TCL scripts ({count}):", count=len(tcl_files), prefix="SUMMARY"
        )
        for f in sorted(tcl_files):
            log_info_safe(logger, "    - {file}", file=f, prefix="SUMMARY")

    if json_files:
        log_info_safe(
            logger, "\n  JSON files ({count}):", count=len(json_files), prefix="SUMMARY"
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
