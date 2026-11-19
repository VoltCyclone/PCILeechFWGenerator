#!/usr/bin/env python3
"""
High-level TCL builder interface for PCILeech firmware generation.

This module provides a clean, object-oriented interface for building TCL scripts
using the template system, integrating with constants and build helpers.
"""

import logging
import shutil
from dataclasses import dataclass, field
from enum import Enum
from pathlib import Path
from typing import Any, Dict, List, Optional, Protocol, Union, runtime_checkable

# Import exceptions and utilities - these should be in the package structure
# Imported for its side effect: loading device_clone before the templating
# package finishes initializing avoids a circular import between
# pcileechfwgenerator.templating and pcileechfwgenerator.device_clone.
import pcileechfwgenerator.device_clone.fallback_manager  # noqa: F401
from pcileechfwgenerator.exceptions import (
    DeviceConfigError,
    TCLBuilderError,
    TemplateNotFoundError,
    XDCConstraintError,
)
from pcileechfwgenerator.string_utils import (
    generate_tcl_header_comment,
    get_project_name,
    log_error_safe,
    log_info_safe,
    log_warning_safe,
    safe_format,
)

# Module-level logger for utility functions
_logger = logging.getLogger(__name__)

# Constants
DEFAULT_VENDOR_ID = 0x10EC  # Realtek
DEFAULT_DEVICE_ID = 0x8168  # RTL8168
DEFAULT_REVISION_ID = 0x15
# CRITICAL: Default class code must be unknown (000000), NOT Ethernet!
# This value should only be used as a last resort - real class code must come from hardware
DEFAULT_CLASS_CODE = 0x000000  # Unknown device - MUST be read from actual hardware

# PCIe link parameter validation sets
PCIE_SPEED_CODES = {1, 2, 3, 4, 5}  # Gen1-Gen5 (2.5GT/s - 32GT/s)
PCIE_WIDTH_CODES = {1, 2, 4, 8, 16}  # Valid lane widths

HEX_WIDTH_MAP = {
    "revision": 2,
    "vendor": 4,
    "device": 4,
    "class": 6,
}


class HexFormatter:
    """Handles formatting of hexadecimal device IDs with validation."""

    @staticmethod
    def format_id(
        val: Union[int, str, None],
        width: int = 4,
        permissive: bool = False,
        field_name: str = "id",
    ) -> str:
        """
        Format device ID values as hex strings with optional permissive defaults.

        Args:
            val: Value to format (int, str, or None)
            width: Width of hex string (2, 4, or 6)
            permissive: If True, use legacy defaults for None values
            field_name: Name of field for error messages

        Returns:
            Formatted hex string without 0x prefix

        Raises:
            ValueError: If val is None and permissive=False, or invalid hex string
        """
        if val is None:
            # Never provide defaults in format_hex_id - all defaults must be
            # applied before calling this function
            raise ValueError(
                safe_format(
                    "Cannot format None value as hex ID for {field_name} "
                    "width={width}",
                    "Donor-unique device identification values are required.",
                    field_name=field_name,
                    width=width,
                )
            )

        # Handle Enum values
        if isinstance(val, Enum):
            val = val.value if hasattr(val, "value") else str(val)

        # Format as hex with proper zero-padding
        if isinstance(val, str):
            s = val.strip()
            if s.startswith(("0x", "0X")):
                s = s[2:]
            # Validate hex and normalize with zero-padding
            try:
                ival = int(s, 16)
            except ValueError as e:
                raise ValueError(
                    safe_format(
                        "Invalid hex string for {field_name}: {val}",
                        field_name=field_name,
                        val=val,
                    )
                ) from e
            return f"{ival:0{width}X}"

        # Integer path with zero-padding
        return f"{int(val):0{width}X}"


# Standalone function for backward compatibility


def format_hex_id(
    val: Union[int, str, None],
    width: int = 4,
    permissive: bool = False,
    field_name: str = "id",
) -> str:
    """
    Format device ID values as hex strings with optional permissive defaults.

    This is a convenience function that delegates to HexFormatter.format_id().

    Args:
        val: Value to format (int, str, or None)
        width: Width of hex string (2, 4, or 6)
        permissive: If True, use legacy defaults for None values
        field_name: Name of field for error messages

    Returns:
        Formatted hex string without 0x prefix

    Raises:
        ValueError: If val is None and permissive=False
    """
    return HexFormatter.format_id(val, width, permissive, field_name)


# Enums for better type safety


class TCLScriptType(Enum):
    """Enumeration of TCL script types."""

    PROJECT_SETUP = "project_setup"
    IP_CONFIG = "ip_config"
    SOURCES = "sources"
    CONSTRAINTS = "constraints"
    SYNTHESIS = "synthesis"
    IMPLEMENTATION = "implementation"
    BITSTREAM = "bitstream"
    MASTER_BUILD = "master_build"
    VALIDATE_PROJECT = "validate_project"


@dataclass
class BuildContext:
    """Immutable build context containing all necessary build parameters."""

    # Required fields
    board_name: str
    fpga_part: str
    fpga_family: str
    pcie_ip_type: str
    max_lanes: int
    supports_msi: bool
    supports_msix: bool

    # Optional device identifiers
    vendor_id: Optional[int] = None
    device_id: Optional[int] = None
    revision_id: Optional[int] = None
    class_code: Optional[int] = None
    subsys_vendor_id: Optional[int] = None
    subsys_device_id: Optional[int] = None

    # Project configuration
    project_name: str = field(default_factory=get_project_name)
    project_dir: str = "./vivado_project"
    output_dir: str = "."

    # Logger instance
    logger: Optional[logging.Logger] = None

    # Build strategies
    synthesis_strategy: str = "Vivado Synthesis Defaults"
    implementation_strategy: str = "Performance_Explore"
    build_jobs: int = 4
    build_timeout: int = 3600

    # PCIe capabilities
    pcie_max_link_speed_code: Optional[int] = None
    pcie_max_link_width: Optional[int] = None

    # PCILeech-specific parameters
    pcileech_src_dir: str = "src"
    pcileech_ip_dir: str = "ip"
    source_file_list: Optional[List[str]] = None
    ip_file_list: Optional[List[str]] = None
    coefficient_file_list: Optional[List[str]] = None
    batch_mode: bool = True

    def __post_init__(self):
        """Validate required fields after initialization."""
        self._validate_required_fields()
        self._validate_pcie_parameters()

    def _validate_required_fields(self):
        """Validate that required fields are present and valid."""
        if not self.fpga_part:
            raise ValueError("fpga_part is required and cannot be empty")
        if not self.board_name:
            raise ValueError("board_name is required and cannot be empty")
        if not self.fpga_family:
            raise ValueError("fpga_family is required and cannot be empty")
        if not self.pcie_ip_type:
            raise ValueError("pcie_ip_type is required and cannot be empty")

    def _validate_pcie_parameters(self):
        """Validate PCIe-specific parameters."""
        if self.max_lanes not in [1, 2, 4, 8, 16]:
            raise ValueError(f"Invalid max_lanes value: {self.max_lanes}")

        if (
            self.pcie_max_link_speed_code
            and self.pcie_max_link_speed_code not in PCIE_SPEED_CODES
        ):
            raise ValueError(
                safe_format(
                    "Invalid PCIe speed code: {self.pcie_max_link_speed_code}",
                    self=self,
                )
            )

        if (
            self.pcie_max_link_width
            and self.pcie_max_link_width not in PCIE_WIDTH_CODES
        ):
            raise ValueError(
                safe_format("Invalid PCIe width: {self.pcie_max_link_width}", self=self)
            )

    def require_donor_values(self):
        """
        Check that all donor-derived values are present.

        Raises:
            ValueError: If any required donor value is missing
        """
        required_fields = {
            "vendor_id": self.vendor_id,
            "device_id": self.device_id,
            "revision_id": self.revision_id,
            "class_code": self.class_code,
        }

        missing = [name for name, value in required_fields.items() if value is None]
        if missing:
            raise ValueError(
                f"Missing required donor-derived values: {', '.join(missing)}. "
                "Cannot generate donor-unique firmware without explicit values "
                "from donor device profiling."
            )

    def to_template_context(self, strict: bool = True) -> Dict[str, Any]:
        """Convert build context to template context dictionary with all
        required variables.

        Args:
            strict: If True (default), raises ValueError if any implicit defaults
                are used, enforcing donor-uniqueness. If False, uses legacy
                defaults for testing/development.

        Returns:
            Dictionary containing template context with metadata about
            defaults used
        """
        # Initialize context metadata to track default usage
        context_metadata = {
            "strict_mode": strict,
            "defaults_used": {},
            "explicit_values": {},
        }

        # In strict mode, enforce donor-uniqueness: require all critical device IDs
        def require_donor_value(value: Optional[int], field_name: str) -> None:
            """Validate donor-derived value is present in strict mode."""
            if strict and value is None:
                raise ValueError(
                    safe_format(
                        "Strict mode: Missing required {field}. Cannot generate "
                        "donor-unique firmware without explicit {field} from "
                        "donor device profiling.",
                        field=field_name,
                    )
                )

        require_donor_value(self.vendor_id, "vendor_id")
        require_donor_value(self.device_id, "device_id")
        require_donor_value(self.revision_id, "revision_id")
        require_donor_value(self.class_code, "class_code")

        # Apply values (with permissive defaults if not strict)
        # Legacy defaults: vendor_id=0x10EC (Realtek), device_id=0x8168 (RTL8168)
        # revision_id=0x15, class_code=0x020000 (Ethernet)
        vendor_id = self.vendor_id
        if vendor_id is None:
            vendor_id = 0x10EC  # Legacy default
            context_metadata["defaults_used"]["vendor_id"] = 0x10EC
        else:
            context_metadata["explicit_values"]["vendor_id"] = vendor_id

        device_id = self.device_id
        if device_id is None:
            device_id = 0x8168  # Legacy default
            context_metadata["defaults_used"]["device_id"] = 0x8168
        else:
            context_metadata["explicit_values"]["device_id"] = device_id

        revision_id = self.revision_id
        if revision_id is None:
            revision_id = 0x15  # Legacy default
            context_metadata["defaults_used"]["revision_id"] = 0x15
        else:
            context_metadata["explicit_values"]["revision_id"] = revision_id

        class_code = self.class_code
        if class_code is None:
            if strict:
                # CRITICAL: No fallback in strict mode! Fail if class_code is missing
                log_error_safe(
                    self.logger or _logger,
                    "class_code missing from device_config - cannot generate firmware without device identity",
                    prefix="TCL",
                )
                raise ValueError(
                    "class_code is required in device_config. "
                    "Cannot generate firmware without proper device class code. "
                    "This value must be read from the actual hardware device."
                )
            else:
                # Legacy default for non-strict mode
                class_code = DEFAULT_CLASS_CODE
                context_metadata["defaults_used"]["class_code"] = DEFAULT_CLASS_CODE
        else:
            context_metadata["explicit_values"]["class_code"] = class_code

        # Subsystem IDs: require explicit values or fallback to resolved main IDs
        subsys_vendor_id = getattr(self, "subsys_vendor_id", None)
        if subsys_vendor_id is None:
            # Use resolved vendor_id (which has defaults applied if needed)
            subsys_vendor_id = vendor_id
            context_metadata["defaults_used"]["subsys_vendor_id"] = "vendor_id"
        else:
            context_metadata["explicit_values"]["subsys_vendor_id"] = subsys_vendor_id

        subsys_device_id = getattr(self, "subsys_device_id", None)
        if subsys_device_id is None:
            # Use resolved device_id (which has defaults applied if needed)
            subsys_device_id = device_id
            context_metadata["defaults_used"]["subsys_device_id"] = "device_id"
        else:
            context_metadata["explicit_values"]["subsys_device_id"] = subsys_device_id

        # Generate device signature for security compliance
        device_signature = safe_format(
            "{vid}:{did}:{rid}",
            vid=format_hex_id(vendor_id, 4),
            did=format_hex_id(device_id, 4),
            rid=format_hex_id(revision_id, 2),
        )

        # Create comprehensive config objects required by templates
        device_config = {
            "vendor_id": format_hex_id(vendor_id, 4),
            "device_id": format_hex_id(device_id, 4),
            "class_code": format_hex_id(class_code, 6),
            "revision_id": format_hex_id(revision_id, 2),
            "subsys_vendor_id": format_hex_id(subsys_vendor_id, 4),
            "subsys_device_id": format_hex_id(subsys_device_id, 4),
            "identification": {
                "vendor_id": format_hex_id(vendor_id, 4),
                "device_id": format_hex_id(device_id, 4),
                "class_code": format_hex_id(class_code, 6),
                "subsystem_vendor_id": format_hex_id(subsys_vendor_id, 4),
                "subsystem_device_id": format_hex_id(subsys_device_id, 4),
            },
            "registers": {
                "revision_id": format_hex_id(revision_id, 2),
            },
        }

        board_config = {
            "name": self.board_name,
            "fpga_part": self.fpga_part,
            "fpga_family": self.fpga_family,
            "pcie_ip_type": self.pcie_ip_type,
            "max_lanes": self.max_lanes,
            "supports_msi": self.supports_msi,
            "supports_msix": self.supports_msix,
        }

        config_space = {
            "vendor_id": format_hex_id(vendor_id, 4),
            "device_id": format_hex_id(device_id, 4),
            "class_code": format_hex_id(class_code, 6),
            "revision_id": format_hex_id(revision_id, 2),
            "subsystem_vendor_id": format_hex_id(subsys_vendor_id, 4),
            "subsystem_device_id": format_hex_id(subsys_device_id, 4),
        }

        msix_config = {
            "enabled": self.supports_msix,
            "table_size": 32 if self.supports_msix else 0,
            "vectors": 32 if self.supports_msix else 0,
        }

        bar_config = {
            "bar0": {
                "enabled": True,
                "type": "Memory",
                "size": "1MB",
                "64bit": True,
            },
        }

        timing_config = {
            "sys_clk_freq_mhz": 100,
            "pcie_clk_freq_mhz": 250,
            "constraints": [],
        }

        pcileech_config = {
            "src_dir": self.pcileech_src_dir,
            "ip_dir": self.pcileech_ip_dir,
            "source_files": self.source_file_list or [],
            "ip_files": self.ip_file_list or [],
            "coefficient_files": self.coefficient_file_list or [],
            "batch_mode": self.batch_mode,
        }

        # Import TemplateObject for template compatibility
        from pcileechfwgenerator.utils.unified_context import TemplateObject

        # Derive and validate PCIe link speed/width enums from donor/IP context
        def _map_speed(code: Optional[int], ip_type: str, strict: bool) -> str:
            """Map PCIe speed code to canonical string representation.

            Returns canonical format without underscores (e.g., "2.5GT/s").
            """
            mapping = {
                1: "2.5GT/s",
                2: "5.0GT/s",
                3: "8.0GT/s",
                4: "16.0GT/s",
                5: "32.0GT/s",
            }
            if code is None:
                if strict:
                    raise TCLBuilderError(
                        "Missing required PCIe link speed from donor device "
                        "profiling. Cannot generate donor-unique firmware without "
                        "speed capability data."
                    )
                # Non-strict mode: provide safe default for testing
                # Track this default in context_metadata
                # Use Gen1 (2.5 GT/s) for maximum compatibility with 7-series
                context_metadata["defaults_used"]["pcie_max_link_speed_code"] = 1
                return "2.5GT/s"  # Gen1 - maximum compatibility
            if code not in mapping:
                raise TCLBuilderError(
                    safe_format(
                        "Invalid PCIe speed code {code} from donor device "
                        "(valid: 1-5)",
                        code=code,
                    )
                )

            speed = mapping[code]

            # Clamp speed to IP-specific maximum
            if ip_type in ("pcie_7x", "7x"):
                # 7-series only supports Gen1 (2.5 GT/s)
                if code > 1:
                    log_warning_safe(
                        _logger,
                        safe_format(
                            "Clamping PCIe speed from {requested} to 2.5GT/s for "
                            "7-series IP (Gen1 only)",
                            requested=speed,
                        ),
                        prefix="TCL_BLDER",
                    )
                    context_metadata["clamped_values"] = context_metadata.get(
                        "clamped_values", {}
                    )
                    context_metadata["clamped_values"]["pcie_speed"] = {
                        "requested": speed,
                        "clamped_to": "2.5GT/s",
                        "reason": "7-series IP maximum",
                    }
                    return "2.5GT/s"

            return speed

        def _map_width(
            width_val: Optional[int], default_lanes: int, strict: bool
        ) -> str:
            lanes = width_val or default_lanes
            if lanes is None or lanes == 0:
                if strict:
                    raise TCLBuilderError(
                        "Missing required PCIe link width from donor device "
                        "profiling. Cannot generate donor-unique firmware without "
                        "lane configuration data."
                    )
                # Non-strict mode: use max_lanes as fallback for testing
                lanes = default_lanes if default_lanes > 0 else 1
                context_metadata["defaults_used"]["pcie_max_link_width"] = lanes
            if lanes < 1 or lanes > 16:
                raise TCLBuilderError(
                    safe_format(
                        "Invalid PCIe link width {lanes} from donor device "
                        "(valid: 1-16)",
                        lanes=lanes,
                    )
                )
            return f"X{lanes}"

        def _validate_enums(ip_type: str, speed_enum: str, width_enum: str) -> None:
            """Validate PCIe enums against IP capabilities."""
            if ip_type in ("pcie_7x", "7x"):
                allowed_widths = {"X1", "X2", "X4", "X8"}
            elif ip_type in ("pcie_ultrascale", "ultrascale"):
                allowed_widths = {"X1", "X2", "X4", "X8", "X16"}
            else:
                return

            # Speed is already clamped in _map_speed, just validate width
            if width_enum not in allowed_widths:
                raise TCLBuilderError(
                    safe_format(
                        "Unsupported link width {width} for IP {ip}",
                        width=width_enum,
                        ip=ip_type,
                    )
                )

        derived_speed = _map_speed(
            self.pcie_max_link_speed_code, self.pcie_ip_type, strict
        )
        derived_width = _map_width(self.pcie_max_link_width, self.max_lanes, strict)

        # Validate derived enums against the IP type; fail fast on mismatch
        _validate_enums(self.pcie_ip_type, derived_speed, derived_width)

        # Normalize PCIe representations and ship all flavors for templates
        # Speed: provide canonical ("2.5GT/s") as primary, code as numeric
        speed_canonical = derived_speed  # "2.5GT/s" (canonical, no underscore)

        # Vivado 7-series expects enum tokens with an underscore (e.g., "2.5_GT/s").
        # Convert canonical form -> Vivado enum form for templates that need it.
        speed_enum = derived_speed.replace("GT/s", "_GT/s")  # "2.5_GT/s"
        speed_code = self.pcie_max_link_speed_code

        # Width: provide uppercase enum ("X4"), lowercase ("x4"), and numeric (4)
        width_enum = derived_width  # "X4"
        width_num = (
            int(derived_width[1:]) if derived_width.upper().startswith("X") else None
        )
        width_str = f"x{width_num}" if width_num else None
        width_code = self.pcie_max_link_width or width_num

        # Map board name to official Xilinx board part ID (if available)
        # This enables board-specific optimizations and constraints
        board_part_id_map = {
            # Note: Most PCILeech boards don't have official Xilinx board parts
            # They use raw FPGA parts instead. Add mappings here if using
            # official dev boards (AC701, KC705, ZC706, etc.)
            # "ac701": "xilinx.com:ac701:part0:1.4",
            # "kc705": "xilinx.com:kc705:part0:1.6",
            # "zc706": "xilinx.com:zc706:part0:1.4",
        }
        board_part_id = board_part_id_map.get(self.board_name.lower())

        return {
            # REQUIRED VARIABLES - These are critical for template validation
            "device_signature": device_signature,
            "device_config": device_config,
            "board_config": board_config,
            "config_space": config_space,
            "msix_config": msix_config,
            "bar_config": bar_config,
            "timing_config": timing_config,
            "pcileech_config": pcileech_config,
            # Nested device information (backward compatibility)
            # use TemplateObject for attribute access
            "device": TemplateObject(
                {
                    "vendor_id": format_hex_id(vendor_id, 4),
                    "device_id": format_hex_id(device_id, 4),
                    "class_code": format_hex_id(class_code, 6),
                    "revision_id": format_hex_id(revision_id, 2),
                    "subsys_vendor_id": format_hex_id(subsys_vendor_id, 4),
                    "subsys_device_id": format_hex_id(subsys_device_id, 4),
                }
            ),
            # Nested board information (backward compatibility)
            # use TemplateObject for attribute access
            "board": TemplateObject(
                {
                    "name": self.board_name,
                    "fpga_part": self.fpga_part,
                    "fpga_family": self.fpga_family,
                    "pcie_ip_type": self.pcie_ip_type,
                }
            ),
            # Nested project information - use TemplateObject for attribute access
            "project": TemplateObject(
                {
                    "name": self.project_name,
                    "dir": self.project_dir,
                    "output_dir": self.output_dir,
                }
            ),
            # Nested build information - use TemplateObject for attribute access
            "build": TemplateObject(
                {
                    "timestamp": "Generated by TCLBuilder",
                    "jobs": self.build_jobs,
                    "timeout": self.build_timeout,
                    "batch_mode": self.batch_mode,
                }
            ),
            # PCILeech-specific information - use TemplateObject for attribute access
            "pcileech": TemplateObject(
                {
                    "src_dir": self.pcileech_src_dir,
                    "ip_dir": self.pcileech_ip_dir,
                    "source_files": self.source_file_list or [],
                    "ip_files": self.ip_file_list or [],
                    "coefficient_files": self.coefficient_file_list or [],
                }
            ),
            # Flat variables for backward compatibility
            "board_name": self.board_name,
            "fpga_part": self.fpga_part,
            "pcie_ip_type": self.pcie_ip_type,
            "fpga_family": self.fpga_family,
            "max_lanes": self.max_lanes,
            "supports_msi": self.supports_msi,
            "supports_msix": self.supports_msix,
            "synthesis_strategy": self.synthesis_strategy,
            "implementation_strategy": self.implementation_strategy,
            "vendor_id": vendor_id,  # Required from donor device
            "device_id": device_id,  # Required from donor device
            "revision_id": revision_id,  # Required from donor device
            "class_code": class_code,  # Required from donor device
            "project_name": self.project_name,
            "project_dir": self.project_dir,
            "output_dir": self.output_dir,
            "header_comment": generate_tcl_header_comment(
                "PCILeech Firmware Build",
                vendor_id=format_hex_id(vendor_id, 4),
                device_id=format_hex_id(device_id, 4),
                board=self.board_name,
            ),
            "header": generate_tcl_header_comment(
                "PCILeech Firmware Build", board=self.board_name
            ),
            # PCILeech flat variables
            "pcileech_src_dir": self.pcileech_src_dir,
            "pcileech_ip_dir": self.pcileech_ip_dir,
            "batch_mode": self.batch_mode,
            "constraint_files": [],  # Add empty constraint files list
            # Board part ID for Xilinx board-specific optimizations (optional)
            "board_part_id": board_part_id,
            # Link configuration: provide all representation variants for templates
            # Speed variants
            "target_link_speed": speed_canonical,  # "2.5GT/s" (canonical)
            "target_link_speed_enum": speed_enum,  # "2.5_GT/s" (with underscore)
            "target_link_speed_code": speed_code,  # 1..5 (numeric code)
            # Width variants
            "target_link_width": width_str,  # "x4" (lowercase)
            "target_link_width_enum": width_enum,  # "X4" (uppercase)
            "target_link_width_num": width_num,  # 4 (numeric)
            "target_link_width_code": width_code,  # Original code or derived
            # Context metadata for introspection and strict mode validation
            "context_metadata": context_metadata,
        }


@runtime_checkable
class DeviceConfigProvider(Protocol):
    """Protocol for device configuration providers."""

    def get_device_config(self, profile_name: str) -> Any:
        """Get device configuration for the specified profile."""
        ...


@dataclass
class ConstraintManager:
    """Manages XDC constraint file operations."""

    output_dir: Path
    logger: logging.Logger

    def __init__(
        self,
        output_dir: Path,
        logger: logging.Logger,
        prefix: str = "XDC",
    ):
        self.output_dir = output_dir
        self.logger = logger
        self.prefix = prefix

    def copy_xdc_files(self, board_name: str) -> List[str]:
        """
        Copy XDC files from repository to output directory.

        Args:
            board_name: Name of the board to get XDC files for

        Returns:
            List of copied file names

        Raises:
            XDCConstraintError: If XDC files cannot be found or copied
        """
        try:
            # Import repo_manager functions directly
            from pcileechfwgenerator.file_management.repo_manager import (
                get_xdc_files,
                is_repository_accessible,
            )

            if not is_repository_accessible(board_name):
                raise XDCConstraintError("Repository is not accessible")

            xdc_files = get_xdc_files(board_name)
            if not xdc_files:
                raise XDCConstraintError(
                    safe_format(
                        "No XDC files found for board '{board}'", board=board_name
                    )
                )

            copied_files = []
            for xdc_file in xdc_files:
                dest_path = self.output_dir / xdc_file.name
                try:
                    shutil.copy2(xdc_file, dest_path)
                    copied_files.append(dest_path.name)
                    log_info_safe(
                        self.logger,
                        safe_format(
                            "Copied XDC file: {filename}", filename=xdc_file.name
                        ),
                        prefix=self.prefix,
                    )
                except Exception as e:
                    raise XDCConstraintError(
                        safe_format(
                            "Failed to copy XDC file {filename}: {error}",
                            filename=xdc_file.name,
                            error=e,
                        )
                    ) from e

            log_info_safe(
                self.logger,
                safe_format(
                    "Successfully copied {count} XDC files", count=len(copied_files)
                ),
                prefix=self.prefix,
            )
            return copied_files

        except Exception as e:
            if isinstance(e, XDCConstraintError):
                raise
            raise XDCConstraintError(
                safe_format(
                    "Failed to copy XDC files for board '{board}': {error}",
                    board=board_name,
                    error=e,
                )
            ) from e


@dataclass
class TCLScriptBuilder:
    """Builds individual TCL scripts using templates."""

    template_renderer: Any
    logger: logging.Logger

    def __init__(self, template_renderer, logger: logging.Logger):
        self.template_renderer = template_renderer
        self.logger = logger

        # Template mapping for each script type
        self._template_map = {
            TCLScriptType.PROJECT_SETUP: "tcl/project_setup.j2",
            TCLScriptType.IP_CONFIG: "tcl/ip_config.j2",
            TCLScriptType.SOURCES: "tcl/sources.j2",
            TCLScriptType.CONSTRAINTS: "tcl/constraints.j2",
            TCLScriptType.SYNTHESIS: "tcl/synthesis.j2",
            TCLScriptType.IMPLEMENTATION: "tcl/implementation.j2",
            TCLScriptType.BITSTREAM: "tcl/bitstream.j2",
            TCLScriptType.MASTER_BUILD: "tcl/master_build.j2",
            TCLScriptType.VALIDATE_PROJECT: "tcl/validate_project.j2",
        }

    def build_script(self, script_type: TCLScriptType, context: Dict[str, Any]) -> str:
        """
        Build a TCL script of the specified type.

        Args:
            script_type: Type of script to build
            context: Template context variables

        Returns:
            Rendered TCL content

        Raises:
            TemplateNotFoundError: If the template file is not found
            TCLBuilderError: If template rendering fails
        """
        template_path = self._template_map.get(script_type)
        if not template_path:
            raise TemplateNotFoundError(
                safe_format(
                    "No template mapping for script type: {script_type}",
                    script_type=script_type,
                )
            )

        try:
            return self.template_renderer.render_template(template_path, context)
        except FileNotFoundError as e:
            # Renderer surfaced an actual missing file
            raise TemplateNotFoundError(
                safe_format(
                    "Template not found for {script_type_value}. "
                    "Ensure '{template_path}' exists in the template directory.",
                    script_type_value=script_type.value,
                    template_path=template_path,
                )
            ) from e
        except Exception as e:
            # Real render error: preserve signal
            raise TCLBuilderError(
                safe_format(
                    "Failed to render template '{template_path}': {error}",
                    template_path=template_path,
                    error=e,
                )
            ) from e


# Export main classes and functions
__all__ = [
    "BuildContext",
    "TCLScriptType",
    "TCLBuilderError",
    "TemplateNotFoundError",
    "DeviceConfigError",
    "XDCConstraintError",
    "format_hex_id",
    "HexFormatter",
]
