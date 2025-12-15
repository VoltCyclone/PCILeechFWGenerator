#!/usr/bin/env python3
"""
High-level TCL builder interface for PCILeech firmware generation.

This module provides a clean, object-oriented interface for building TCL scripts
using the template system, integrating with constants and build helpers.
"""

import logging
import shutil
import sys
from dataclasses import dataclass, field
from enum import Enum
from pathlib import Path
from typing import Any, Dict, List, Optional, Protocol, Union, runtime_checkable

# Import exceptions and utilities - these should be in the package structure
from src.device_clone.fallback_manager import get_global_fallback_manager
from src.exceptions import (
    DeviceConfigError,
    TCLBuilderError,
    TemplateNotFoundError,
    XDCConstraintError,
)
from src.string_utils import (
    generate_tcl_header_comment,
    get_project_name,
    log_debug_safe,
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
    'revision': 2,
    'vendor': 4,
    'device': 4,
    'class': 6,
}


class HexFormatter:
    """Handles formatting of hexadecimal device IDs with validation."""
    
    @staticmethod
    def format_id(
        val: Union[int, str, None],
        width: int = 4,
        permissive: bool = False,
        field_name: str = "id"
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
            if not permissive:
                raise ValueError(
                    safe_format(
                        "Cannot format None value as hex ID for {field_name} "
                        "width={width}",
                        "Donor-unique device identification values are required.",
                        field_name=field_name,
                        width=width
                    )
                )
            
            # Legacy defaults for testing/development
            defaults = {
                2: DEFAULT_REVISION_ID,
                4: DEFAULT_VENDOR_ID,
                6: DEFAULT_CLASS_CODE,
            }
            val = defaults.get(width, DEFAULT_VENDOR_ID)
        
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
                        val=val
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
    field_name: str = "id"
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

    # PCILeech-specific script types
    PCILEECH_PROJECT = "pcileech_project"
    PCILEECH_BUILD = "pcileech_build"


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
    pcileech_project_script: str = "vivado_generate_project.tcl"
    pcileech_build_script: str = "vivado_build.tcl"
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
        
        if (self.pcie_max_link_speed_code and 
            self.pcie_max_link_speed_code not in PCIE_SPEED_CODES):
            raise ValueError(
                safe_format(
                    "Invalid PCIe speed code: {self.pcie_max_link_speed_code}", 
                    self=self
                )
            )
        
        if (self.pcie_max_link_width and 
            self.pcie_max_link_width not in PCIE_WIDTH_CODES):
            raise ValueError(
                safe_format(
                    "Invalid PCIe width: {self.pcie_max_link_width}", 
                    self=self
                )
            )
    
    def require_donor_values(self):
        """
        Check that all donor-derived values are present.
        
        Raises:
            ValueError: If any required donor value is missing
        """
        required_fields = {
            'vendor_id': self.vendor_id,
            'device_id': self.device_id,
            'revision_id': self.revision_id,
            'class_code': self.class_code,
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
            # CRITICAL: No fallback! Fail if class_code is missing
            log_error_safe(
                self.logger,
                "class_code missing from device_config - cannot generate firmware without device identity",
                prefix="TCL",
            )
            raise ValueError(
                "class_code is required in device_config. "
                "Cannot generate firmware without proper device class code. "
                "This value must be read from the actual hardware device."
            )
        else:
            context_metadata["explicit_values"]["class_code"] = class_code

        # Subsystem IDs: require explicit values or fallback to resolved main IDs
        subsys_vendor_id = getattr(self, "subsys_vendor_id", None)
        if subsys_vendor_id is None:
            # Use resolved vendor_id (which has defaults applied if needed)
            subsys_vendor_id = vendor_id
            context_metadata["defaults_used"]["subsys_vendor_id"] = "vendor_id"
        else:
            context_metadata["explicit_values"]["subsys_vendor_id"] = (
                subsys_vendor_id
            )

        subsys_device_id = getattr(self, "subsys_device_id", None)
        if subsys_device_id is None:
            # Use resolved device_id (which has defaults applied if needed)
            subsys_device_id = device_id
            context_metadata["defaults_used"]["subsys_device_id"] = "device_id"
        else:
            context_metadata["explicit_values"]["subsys_device_id"] = (
                subsys_device_id
            )

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
            "project_script": self.pcileech_project_script,
            "build_script": self.pcileech_build_script,
            "source_files": self.source_file_list or [],
            "ip_files": self.ip_file_list or [],
            "coefficient_files": self.coefficient_file_list or [],
            "batch_mode": self.batch_mode,
        }

        # Import TemplateObject for template compatibility
        from src.utils.unified_context import TemplateObject

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
                    "project_script": self.pcileech_project_script,
                    "build_script": self.pcileech_build_script,
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
            from src.file_management.repo_manager import (
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
            # PCILeech templates
            TCLScriptType.PCILEECH_PROJECT: "tcl/pcileech_generate_project.j2",
        }

        # PCILeech-specific template mapping for enhanced integration
        self._pcileech_template_map = {
            "project_setup": "tcl/pcileech_project_setup.j2",
            "sources": "tcl/pcileech_sources.j2",
            "constraints": "tcl/pcileech_constraints.j2",
            "implementation": "tcl/pcileech_implementation.j2",
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


@dataclass
class TCLBuilder:
    """
    High-level interface for building TCL scripts using templates.

    This class provides a clean, object-oriented interface for building TCL scripts
    with improved error handling, performance, and maintainability.
    """

    # Class constants
    DEFAULT_BUILD_JOBS = 4
    DEFAULT_BUILD_TIMEOUT = 3600
    DEFAULT_PROJECT_NAME = get_project_name()
    DEFAULT_PROJECT_DIR = "./vivado_project"

    def __init__(
        self,
        template_dir: Optional[Union[str, Path]] = None,
        output_dir: Optional[Union[str, Path]] = None,
        device_profile: Optional[str] = None,
        prefix: str = "TCL_BLDER",
    ):
        """
        Initialize the TCL builder.

        Args:
            template_dir: Directory containing template files
            output_dir: Directory for output files
            device_profile: Device configuration profile to use (optional)
        """
        self.logger = logging.getLogger(__name__)
        self.output_dir = Path(output_dir) if output_dir else Path(".")
        self.output_dir.mkdir(parents=True, exist_ok=True)
        self.prefix = prefix

        # Initialize components
        self._init_template_renderer(template_dir)
        self._init_device_config(device_profile)
        self._init_build_helpers()
        self._init_constants()
        self._init_repo_manager()

        # Initialize (shared) fallback manager
        self.fallback_manager = get_global_fallback_manager()

        # Initialize script builder
        self.script_builder = TCLScriptBuilder(self.template_renderer, self.logger)

        # Initialize PCILeech-specific template mapping
        self._pcileech_template_map = {
            "project_setup": "tcl/pcileech_project_setup.j2",
            "sources": "tcl/pcileech_sources.j2",
            "constraints": "tcl/pcileech_constraints.j2",
            "implementation": "tcl/pcileech_implementation.j2",
        }

        # Track generated files
        self.generated_files: List[str] = []

        log_debug_safe(
            self.logger,
            safe_format(
                "TCL builder initialized with output dir: {output_dir}",
                output_dir=self.output_dir,
            ),
            prefix=self.prefix,
        )

    def _init_template_renderer(self, template_dir: Optional[Union[str, Path]]):
        """Initialize template renderer with error handling."""
        try:
            from .template_renderer import TemplateRenderer

            self.template_renderer = TemplateRenderer(template_dir)
        except ImportError as e:
            raise TCLBuilderError(
                safe_format(
                    "Failed to initialize template renderer: {error}",
                    error=e
                )
            ) from e

    def _init_device_config(self, device_profile: Optional[str]):
        """Initialize device configuration with robust error handling."""
        if device_profile is None:
            log_info_safe(
                self.logger,
                "No device profile specified, using live device detection",
                prefix=self.prefix,
            )
            self.device_config = None
            return

        try:
            from ..device_clone.device_config import get_device_config

            self.device_config = get_device_config(device_profile)
        except ImportError as e:
            log_warning_safe(
                self.logger,
                safe_format("Device config module unavailable: {error}", error=e),
                prefix=self.prefix,
            )
            self.device_config = None

    def _init_build_helpers(self):
        """Initialize build helpers with fallback handling."""
        try:
            from src.build_helpers import (
                batch_write_tcl_files,
                create_fpga_strategy_selector,
                validate_fpga_part,
            )

            self.batch_write_tcl_files = batch_write_tcl_files
            self.fpga_strategy_selector = create_fpga_strategy_selector()
            self.validate_fpga_part = validate_fpga_part
        except ImportError as e:
            raise TCLBuilderError(f"Failed to initialize build helpers: {e}") from e

    def _init_constants(self):
        """Initialize constants with fallback values."""
        try:
            import src.device_clone.constants as constants

            self.BOARD_PARTS = constants.BOARD_PARTS
            self.DEFAULT_FPGA_PART = constants.DEFAULT_FPGA_PART
            self.TCL_SCRIPT_FILES = constants.PCILEECH_TCL_SCRIPT_FILES
            self.MASTER_BUILD_SCRIPT = constants.PCILEECH_BUILD_SCRIPT
            self.SYNTHESIS_STRATEGY = getattr(constants, 'SYNTHESIS_STRATEGY', 'Vivado Synthesis Defaults')
            self.IMPLEMENTATION_STRATEGY = constants.IMPLEMENTATION_STRATEGY
            self.FPGA_FAMILIES = constants.FPGA_FAMILIES
        except ImportError as e:
            log_warning_safe(
                self.logger,
                safe_format("Using fallback constants: {error}", error=e),
                prefix=self.prefix,
            )
            fallback = self._create_fallback_constants()
            for attr_name in dir(fallback):
                if not attr_name.startswith("_"):
                    setattr(self, attr_name, getattr(fallback, attr_name))

    def _create_fallback_constants(self):
        """Create fallback constants when import fails."""

        class FallbackConstants:
            BOARD_PARTS = {
                "pcileech_35t325_x4": "xc7a35tcsg324-2",
                "pcileech_50t325_x4": "xc7a50tcsg324-2",
            }
            DEFAULT_FPGA_PART = "xc7a35tcsg324-2"
            TCL_SCRIPT_FILES = [
                "01_project_setup.tcl",
                "02_ip_config.tcl",
                "03_add_sources.tcl",
                "04_constraints.tcl",
                "05_synthesis.tcl",
                "06_implementation.tcl",
                "07_bitstream.tcl",
            ]
            MASTER_BUILD_SCRIPT = "vivado_build.tcl"
            SYNTHESIS_STRATEGY = "Vivado Synthesis Defaults"
            IMPLEMENTATION_STRATEGY = "Performance_Explore"
            FPGA_FAMILIES = {
                "xc7a35tcsg324-2": "Artix-7",
                "xc7a50tcsg324-2": "Artix-7",
            }

        return FallbackConstants()

    def _init_repo_manager(self):
        """Initialize constraint manager with error handling."""
        try:
            self.constraint_manager = ConstraintManager(self.output_dir, self.logger)
        except ImportError as e:
            log_warning_safe(
                self.logger,
                safe_format("Constraint manager unavailable: {error}", error=e),
                prefix=self.prefix,
            )
            self.constraint_manager = None

    @staticmethod
    def _safe_getattr(obj: Any, attr_path: str, default: Any = None) -> Any:
        """Safely retrieve nested attributes/keys from an object or dict.
        
        Supports dot-separated paths over mixed objects and dicts.
        
        Args:
            obj: Object or dict to traverse
            attr_path: Dot-separated path (e.g., "device.vendor_id")
            default: Value to return if path not found
            
        Returns:
            Value at the path, or default if not found
        """
        if obj is None:
            return default

        current = obj
        for part in attr_path.split("."):
            if isinstance(current, dict):
                current = current.get(part)
            else:
                current = getattr(current, part, None)
            if current is None:
                return default
        return current

    def _select_board_interactively(self) -> str:
        """Interactively select a board from available options.

        Raises:
            ValueError: If stdin is not a TTY or selection fails
        """
        # Check if stdin is a TTY (interactive terminal)
        if not sys.stdin.isatty():
            available_boards = list(self.BOARD_PARTS.keys())
            raise ValueError(
                "Board selection requires interactive terminal, but stdin is not a TTY. "
                f"Please specify board explicitly using --board parameter. "
                f"Available boards: {', '.join(available_boards)}"
            )

        available_boards = list(self.BOARD_PARTS.keys())
        if not available_boards:
            raise ValueError("No board configurations available")

        print("\nAvailable boards:")
        for i, board_name in enumerate(available_boards, 1):
            print(f"  {i}. {board_name}")

        try:
            selection = input("\nSelect a board (number or name): ").strip()
            if selection.isdigit() and 1 <= int(selection) <= len(available_boards):
                return available_boards[int(selection) - 1]
            elif selection in available_boards:
                return selection
            else:
                raise ValueError(f"Invalid selection: {selection}")
        except (ValueError, IndexError) as e:
            raise ValueError(f"Board selection failed: {e}") from e
        except EOFError as e:
            raise ValueError(
                "Board selection failed: EOF while reading input. "
                "This typically occurs in non-interactive environments (containers, CI/CD). "
                f"Please specify board explicitly using --board parameter."
            ) from e

    def create_build_context(
        self,
        board: Optional[str] = None,
        fpga_part: Optional[str] = None,
        vendor_id: Optional[int] = None,
        device_id: Optional[int] = None,
        revision_id: Optional[int] = None,
        subsys_vendor_id: Optional[int] = None,
        subsys_device_id: Optional[int] = None,
        **kwargs,
    ) -> BuildContext:
        """
        Create a build context with validated parameters.

        Args:
            board: Board name
            fpga_part: FPGA part string
            Returns:
                Validated build context

        Raises:
            ValueError: If required parameters are invalid
        """
        # Normalize empty strings to None for consistent handling
        board = board.strip() if board and isinstance(board, str) else board
        board = None if board == "" else board

        # Determine board and FPGA part
        # Prefer non-interactive inference before prompting
        # 1) If board missing but fpga_part provided, reverse-map to a known board
        if not board and fpga_part:
            try:
                matches = [
                    b
                    for b, p in self.BOARD_PARTS.items()
                    if str(p).lower() == str(fpga_part).lower()
                ]
                if len(matches) == 1:
                    board = matches[0]
                    log_info_safe(
                        self.logger,
                        safe_format(
                            "Auto-selected board '{board}' from fpga_part '{part}'",
                            board=board,
                            part=fpga_part,
                        ),
                    )
                elif len(matches) > 1:
                    # Prefer canonical pcileech_* names if ambiguous
                    preferred = [m for m in matches if m.startswith("pcileech_")]
                    chosen = preferred[0] if preferred else matches[0]
                    log_warning_safe(
                        self.logger,
                        safe_format(
                            "Multiple boards match fpga_part '{part}'; choosing '{chosen}' from {matches}",
                            part=fpga_part,
                            chosen=chosen,
                            matches=matches,
                        ),
                    )
                    board = chosen
            except Exception:
                # Fall through to normal flow
                pass

        if fpga_part is None:
            if not board:
                board = self._select_board_interactively()

            if board not in self.BOARD_PARTS:
                raise ValueError(
                    safe_format(
                        "Invalid board '{board}'. Available: {boards}",
                        board=board,
                        boards=list(self.BOARD_PARTS.keys()),
                    )
                )

            fpga_part = self.BOARD_PARTS[board]

        # Validate FPGA part
        if fpga_part is None:
            raise ValueError("FPGA part cannot be None")
        if not self.validate_fpga_part(fpga_part):
            raise ValueError(f"Invalid FPGA part '{fpga_part}'")
        if board is None:
            raise ValueError("Board name cannot be None")

        # Get FPGA-specific configuration
        fpga_config = self.fpga_strategy_selector(fpga_part)

        # Validate that FPGA family is properly determined
        fpga_family = fpga_config.get("family")
        if not fpga_family:
            raise TCLBuilderError(
                safe_format(
                    "Failed to determine FPGA family for part '{fpga_part}'. "
                    "FPGA family is critical for proper synthesis and implementation. "
                    "Please ensure the FPGA part is correctly specified.",
                    fpga_part=fpga_part,
                )
            )

        # Extract device configuration values
        # device_config can be either a dict or an object with attributes
        cfg = self.device_config or {}
        if isinstance(cfg, dict):
            config_vendor_id = cfg.get("vendor_id")
            config_device_id = cfg.get("device_id")
            config_revision_id = cfg.get("revision_id")
            config_class_code = cfg.get("class_code")
            config_subsys_vendor_id = cfg.get("subsystem_vendor_id")
            config_subsys_device_id = cfg.get("subsystem_device_id")
        else:
            config_vendor_id = self._safe_getattr(cfg, "vendor_id")
            config_device_id = self._safe_getattr(cfg, "device_id")
            config_revision_id = self._safe_getattr(cfg, "revision_id")
            config_class_code = self._safe_getattr(cfg, "class_code")
            config_subsys_vendor_id = self._safe_getattr(cfg, "subsystem_vendor_id")
            config_subsys_device_id = self._safe_getattr(cfg, "subsystem_device_id")

        # Resolve final device identification values
        final_vendor_id = vendor_id or config_vendor_id
        final_device_id = device_id or config_device_id
        final_revision_id = revision_id or config_revision_id
        # CRITICAL: No fallback for class_code - must come from hardware
        final_class_code = kwargs.get("class_code") or config_class_code

        # Validate critical device identification values are present
        # These are required for donor-unique firmware generation
        missing_ids = []
        if final_vendor_id is None:
            missing_ids.append("vendor_id")
        if final_device_id is None:
            missing_ids.append("device_id")
        if final_revision_id is None:
            missing_ids.append("revision_id")

        if missing_ids:
            missing_list = ', '.join(missing_ids)
            raise ValueError(
                f"Missing required device identification values: {missing_list}. "
                "Cannot generate donor-unique firmware without complete device "
                "configuration. Ensure device profiling/detection completed "
                "successfully and all required parameters are provided.\n\n"
                "Remediation: Run device detection/profiling to extract these "
                "values from the donor device's PCI configuration space using "
                "VFIO or similar tools."
            )

        return BuildContext(
            board_name=board,
            fpga_part=fpga_part,
            fpga_family=fpga_family,
            pcie_ip_type=fpga_config.get("pcie_ip_type", "7x"),
            max_lanes=fpga_config.get("max_lanes", 1),
            supports_msi=fpga_config.get("supports_msi", False),
            supports_msix=fpga_config.get("supports_msix", False),
            vendor_id=final_vendor_id,
            device_id=final_device_id,
            revision_id=final_revision_id,
            class_code=final_class_code,
            subsys_vendor_id=subsys_vendor_id or config_subsys_vendor_id,
            subsys_device_id=subsys_device_id or config_subsys_device_id,
            # Donor-derived PCIe link fields (optional)
            pcie_max_link_speed_code=kwargs.get("pcie_max_link_speed_code"),
            pcie_max_link_width=kwargs.get("pcie_max_link_width"),
            synthesis_strategy=kwargs.get(
                "synthesis_strategy", self.SYNTHESIS_STRATEGY
            ),
            implementation_strategy=kwargs.get(
                "implementation_strategy", self.IMPLEMENTATION_STRATEGY
            ),
            build_jobs=kwargs.get("build_jobs", self.DEFAULT_BUILD_JOBS),
            build_timeout=kwargs.get("build_timeout", self.DEFAULT_BUILD_TIMEOUT),
            project_name=kwargs.get("project_name", self.DEFAULT_PROJECT_NAME),
            project_dir=kwargs.get("project_dir", self.DEFAULT_PROJECT_DIR),
            output_dir=kwargs.get("output_dir", str(self.output_dir)),
        )

    def build_constraints_tcl(
        self, context: BuildContext, constraint_files: Optional[List[str]] = None
    ) -> str:
        """
        Build constraints TCL script with XDC file management.

        Args:
            context: Build context
            constraint_files: Additional constraint files

        Returns:
            Rendered TCL content

        Raises:
            XDCConstraintError: If XDC files cannot be processed
        """
        template_context = context.to_template_context()
        template_context["constraint_files"] = constraint_files or []

        # Handle XDC file copying if repository manager is available
        if self.constraint_manager and context.board_name:
            try:
                copied_files = self.constraint_manager.copy_xdc_files(
                    context.board_name
                )
                template_context["constraint_files"].extend(copied_files)
                template_context["generated_xdc_path"] = (
                    copied_files[0] if copied_files else None
                )
                if copied_files:
                    log_info_safe(
                        self.logger,
                        safe_format(
                            "Board-specific XDC files selected for {board}: {files}",
                            board=context.board_name,
                            files=", ".join(copied_files),
                        ),
                        prefix=self.prefix,
                    )
            except XDCConstraintError as e:
                log_warning_safe(
                    self.logger,
                    safe_format(
                        "XDC file handling failed: {error}. "
                        "Proceeding without board-specific XDC files.",
                        error=e
                    ),
                    prefix=self.prefix,
                )
                # Proceed without XDC files - build can continue with
                # user-provided constraints or embedded XDC content

        # Add required variables for constraints template
        template_context.setdefault("sys_clk_freq_mhz", 100)  # Default to 100MHz
        template_context.setdefault(
            "generated_xdc_path", ""
        )  # Empty path if not generated
        template_context.setdefault(
            "board_xdc_content", ""
        )  # Empty content if not available
        embedded_xdc = template_context.get("board_xdc_content")
        if embedded_xdc:
            log_info_safe(
                self.logger,
                safe_format(
                    "Embedded board XDC content detected for {board} (chars={count})",
                    board=context.board_name,
                    count=len(embedded_xdc),
                ),
                prefix=self.prefix,
            )
        else:
            log_debug_safe(
                self.logger,
                safe_format(
                    "No embedded board XDC content provided for {board}",
                    board=context.board_name or "<unspecified>",
                ),
                prefix=self.prefix,
            )

        # Ensure header is defined
        template_context.setdefault(
            "header",
            generate_tcl_header_comment("TCL Constraints", board=context.board_name),
        )

        # COMPREHENSIVE TEMPLATE CONTEXT HANDLING
        # 1. Handle device information - extract from BuildContext if available
        if not template_context.get("device") or not isinstance(
            template_context["device"], dict
        ):
            template_context["device"] = {}

        # Extract all device properties directly from context
        if hasattr(context, "vendor_id") and context.vendor_id:
            template_context["device"]["vendor_id"] = format_hex_id(
                context.vendor_id, 4
            )
        if hasattr(context, "device_id") and context.device_id:
            template_context["device"]["device_id"] = format_hex_id(
                context.device_id, 4
            )
        if hasattr(context, "revision_id") and context.revision_id:
            template_context["device"]["revision_id"] = format_hex_id(
                context.revision_id, 2
            )
        if hasattr(context, "class_code") and context.class_code:
            template_context["device"]["class_code"] = format_hex_id(
                context.class_code, 6
            )
        if hasattr(context, "subsys_vendor_id") and context.subsys_vendor_id:
            template_context["device"]["subsys_vendor_id"] = format_hex_id(
                context.subsys_vendor_id, 4
            )
        if hasattr(context, "subsys_device_id") and context.subsys_device_id:
            template_context["device"]["subsys_device_id"] = format_hex_id(
                context.subsys_device_id, 4
            )

        # Log device info being used
        log_info_safe(
            self.logger,
            safe_format(
                "Using device information: {vid}:{did} (Class: {cls})",
                vid=template_context["device"].get("vendor_id", "N/A"),
                did=template_context["device"].get("device_id", "N/A"),
                cls=template_context["device"].get("class_code", "N/A"),
            ),
            prefix=self.prefix,
        )

        # 2. Handle board information - extract from BuildContext if available
        if not template_context.get("board") or not isinstance(
            template_context["board"], dict
        ):
            template_context["board"] = {}

        # Always ensure board name is available
        if not template_context["board"].get("name") and hasattr(context, "board_name"):
            template_context["board"]["name"] = context.board_name

        # Add other board properties if available
        if hasattr(context, "fpga_part") and context.fpga_part:
            template_context["board"]["fpga_part"] = context.fpga_part
        if hasattr(context, "fpga_family") and context.fpga_family:
            template_context["board"]["fpga_family"] = context.fpga_family
        if hasattr(context, "pcie_ip_type") and context.pcie_ip_type:
            template_context["board"]["pcie_ip_type"] = context.pcie_ip_type

        # 3. Add required variables for constraints template
        template_context.setdefault("sys_clk_freq_mhz", 100)  # Default to 100MHz
        template_context.setdefault(
            "generated_xdc_path", ""
        )  # Empty path if not generated
        template_context.setdefault(
            "board_xdc_content", ""
        )  # Empty content if not available

        # 4. Ensure header is defined
        template_context.setdefault(
            "header",
            generate_tcl_header_comment(
                "TCL Constraints",
                vendor_id=template_context["device"].get("vendor_id", "Unknown"),
                device_id=template_context["device"].get("device_id", "Unknown"),
            ),
        )

        return self.script_builder.build_script(
            TCLScriptType.CONSTRAINTS, template_context
        )

    def build_sources_tcl(
        self, context: BuildContext, source_files: Optional[List[str]] = None
    ) -> str:
        """Build sources management TCL script."""
        template_context = context.to_template_context()
        template_context["source_files"] = source_files or []
        return self.script_builder.build_script(TCLScriptType.SOURCES, template_context)

    def build_master_tcl(self, context: BuildContext) -> str:
        """Build master build TCL script."""
        template_context = context.to_template_context()
        template_context["tcl_script_files"] = self.TCL_SCRIPT_FILES
        return self.script_builder.build_script(
            TCLScriptType.MASTER_BUILD, template_context
        )

    def _ensure_pcileech_context(
        self, template_context: Dict[str, Any], context: BuildContext
    ) -> None:
        """
        Ensure PCILeech-specific context is available in the template context.

        Creates a default PCILeech context if one doesn't exist, populating it
        with necessary paths and file lists from the build context.

        Args:
            template_context: The template context dictionary to update
            context: Build context with PCILeech-specific parameters
        """
        pcileech_context = template_context.get("pcileech", {})
        if not pcileech_context:
            # Create default PCILeech context if missing
            template_context["pcileech"] = {
                "src_dir": context.pcileech_src_dir,
                "ip_dir": context.pcileech_ip_dir,
                "project_script": context.pcileech_project_script,
                "build_script": context.pcileech_build_script,
                "source_files": context.source_file_list or [],
                "ip_files": context.ip_file_list or [],
                "coefficient_files": context.coefficient_file_list or [],
            }

    def build_pcileech_project_script(self, context: BuildContext) -> str:
        """
        Build PCILeech project generation script.

        This replaces the current 7-script approach with PCILeech's unified
        project generation script that handles project setup, IP configuration,
        and source file management.

        Args:
            context: Build context with PCILeech-specific parameters

        Returns:
            Rendered PCILeech project generation TCL content
        """
        template_context = context.to_template_context()

        # Ensure PCILeech-specific context is available
        self._ensure_pcileech_context(template_context, context)

        return self.script_builder.build_script(
            TCLScriptType.PCILEECH_PROJECT, template_context
        )

    def build_pcileech_build_script(self, context: BuildContext) -> str:
        """
        Build PCILeech batch build script.

        This script handles synthesis, implementation, and bitstream generation
        in batch mode for automated builds.

        Args:
            context: Build context with PCILeech-specific parameters

        Returns:
            Rendered PCILeech build TCL content
        """
        template_context = context.to_template_context()

        # Ensure PCILeech-specific context is available
        self._ensure_pcileech_context(template_context, context)

        return self.script_builder.build_script(
            TCLScriptType.PCILEECH_BUILD, template_context
        )

    def build_all_tcl_scripts(
        self,
        board: Optional[str] = None,
        fpga_part: Optional[str] = None,
        vendor_id: Optional[int] = None,
        device_id: Optional[int] = None,
        revision_id: Optional[int] = None,
        subsys_vendor_id: Optional[int] = None,
        subsys_device_id: Optional[int] = None,
        source_files: Optional[List[str]] = None,
        constraint_files: Optional[List[str]] = None,
        use_pcileech: bool = True,
        **kwargs,
    ) -> Dict[str, bool]:
        """
        Build all TCL scripts and write them to the output directory.

        This method now supports both legacy 7-script approach and PCILeech's
        2-script approach based on the use_pcileech parameter.

        Args:
            board: Board name
            fpga_part: FPGA part string
            vendor_id: PCI vendor ID
            device_id: PCI device ID
            revision_id: PCI revision ID
            source_files: List of source files
            constraint_files: List of constraint files
            use_pcileech: Whether to use PCILeech 2-script approach (default: True)
            **kwargs: Additional build parameters

        Returns:
            Dictionary mapping script names to success status
        """
        # Create build context with PCILeech parameters
        pcileech_kwargs = {
            "source_file_list": source_files,
            "constraint_files": constraint_files,
            "subsys_vendor_id": subsys_vendor_id,
            "subsys_device_id": subsys_device_id,
            **kwargs,
        }

        context = self.create_build_context(
            board=board,
            fpga_part=fpga_part,
            vendor_id=vendor_id,
            device_id=device_id,
            revision_id=revision_id,
            **pcileech_kwargs,
        )

        if use_pcileech:
            # Use PCILeech 2-script approach
            tcl_contents = {
                context.pcileech_project_script: self.build_pcileech_project_script(
                    context
                ),
                context.pcileech_build_script: self.build_pcileech_build_script(
                    context
                ),
            }
        else:
            # Legacy 7-script approach (for backward compatibility)
            template_context = context.to_template_context()
            tcl_contents = {
                self.TCL_SCRIPT_FILES[0]: self.script_builder.build_script(
                    TCLScriptType.PROJECT_SETUP, template_context
                ),
                self.TCL_SCRIPT_FILES[1]: self.script_builder.build_script(
                    TCLScriptType.IP_CONFIG, template_context
                ),
                self.TCL_SCRIPT_FILES[2]: self.build_sources_tcl(
                    context, source_files),
                self.TCL_SCRIPT_FILES[3]: self.build_constraints_tcl(
                    context, constraint_files
                ),
                self.TCL_SCRIPT_FILES[4]: self.script_builder.build_script(
                    TCLScriptType.SYNTHESIS, template_context
                ),
                self.TCL_SCRIPT_FILES[5]: self.script_builder.build_script(
                    TCLScriptType.IMPLEMENTATION, template_context
                ),
                self.TCL_SCRIPT_FILES[6]: self.script_builder.build_script(
                    TCLScriptType.BITSTREAM, template_context
                ),
                self.MASTER_BUILD_SCRIPT: self.build_master_tcl(context),
            }

        # Validate required parameters before batch write
        if tcl_contents is None:
            raise TCLBuilderError("TCL contents cannot be None")
        if self.output_dir is None:
            raise TCLBuilderError("Output directory cannot be None")
        if self.generated_files is None:
            raise TCLBuilderError("Generated files list cannot be None")
        if self.logger is None:
            raise TCLBuilderError("Logger cannot be None")

        # Write all files in batch
        try:
            self.batch_write_tcl_files(
                tcl_contents, self.output_dir, self.generated_files, self.logger
            )
            # Return success status for all files if batch write succeeds
            return {filename: True for filename in tcl_contents.keys()}
        except Exception as e:
            log_error_safe(
                self.logger,
                safe_format("Failed to write TCL files: {error}", error=e),
                prefix=self.prefix,
            )
            # Return failure status for all files if batch write fails
            return {filename: False for filename in tcl_contents.keys()}

    def build_pcileech_scripts_only(
        self,
        board: Optional[str] = None,
        fpga_part: Optional[str] = None,
        vendor_id: Optional[int] = None,
        device_id: Optional[int] = None,
        revision_id: Optional[int] = None,
        subsys_vendor_id: Optional[int] = None,
        subsys_device_id: Optional[int] = None,
        source_files: Optional[List[str]] = None,
        constraint_files: Optional[List[str]] = None,
        **kwargs,
    ) -> Dict[str, bool]:
        """
        Build only PCILeech scripts (replacement for 7-script approach).

        This is the new primary method for PCILeech integration that completely
        replaces the 7-script approach with PCILeech's 2-script system.

        Args:
            board: Board name
            fpga_part: FPGA part string
            vendor_id: PCI vendor ID
            device_id: PCI device ID
            revision_id: PCI revision ID
            source_files: List of source files
            constraint_files: List of constraint files
            **kwargs: Additional build parameters

        Returns:
            Dictionary mapping script names to success status
        """
        return self.build_all_tcl_scripts(
            board=board,
            fpga_part=fpga_part,
            vendor_id=vendor_id,
            device_id=device_id,
            revision_id=revision_id,
            subsys_vendor_id=subsys_vendor_id,
            subsys_device_id=subsys_device_id,
            source_files=source_files,
            constraint_files=constraint_files,
            use_pcileech=True,
            **kwargs,
        )

    def build_pcileech_enhanced_scripts(self, context: BuildContext) -> Dict[str, str]:
        """
        Build enhanced PCILeech-specific TCL scripts using dedicated templates.

        This method generates PCILeech-optimized scripts that include:
        - Project setup with PCILeech-specific settings
        - Source file management for PCILeech modules
        - PCILeech-specific timing constraints
        - Implementation settings optimized for PCILeech

        Args:
            context: Build context with PCILeech-specific parameters

        Returns:
            Dictionary mapping script names to generated TCL content
        """
        scripts = {}
        template_context = context.to_template_context()

        try:
            # Generate each PCILeech-specific script
            for script_name, template_path in self._pcileech_template_map.items():
                try:
                    script_content = self.template_renderer.render_template(
                        template_path, template_context
                    )
                    scripts[script_name] = script_content
                    log_info_safe(
                        self.logger,
                        safe_format(
                            "Generated PCILeech {name} script", name=script_name
                        ),
                        prefix="TEMPLATE",
                    )

                except Exception as e:
                    log_error_safe(
                        self.logger,
                        safe_format(
                            "Failed to generate PCILeech {name} script: {error}",
                            name=script_name,
                            error=e,
                        ),
                        prefix="TEMPLATE",
                    )
                    # Continue with other scripts even if one fails

            log_info_safe(
                self.logger,
                safe_format(
                    "Generated {count} PCILeech-enhanced TCL scripts",
                    count=len(scripts),
                ),
                prefix="TEMPLATE",
            )

        except Exception as e:
            raise TCLBuilderError(
                safe_format(
                    "Failed to build PCILeech enhanced scripts: {error}", error=e
                )
            ) from e

        return scripts

    def save_pcileech_scripts(
        self, scripts: Dict[str, str], output_dir: Path
    ) -> List[str]:
        """
        Save PCILeech-specific scripts to the output directory.

        Args:
            scripts: Dictionary of script names to content
            output_dir: Directory to save scripts to

        Returns:
            List of saved file paths
        """
        saved_files = []

        try:
            output_dir.mkdir(parents=True, exist_ok=True)

            # PCILeech script filename mapping
            script_filenames = {
                "project_setup": "pcileech_project_setup.tcl",
                "sources": "pcileech_sources.tcl",
                "constraints": "pcileech_constraints.tcl",
                "implementation": "pcileech_implementation.tcl",
            }

            for script_name, script_content in scripts.items():
                filename = script_filenames.get(
                    script_name, f"pcileech_{script_name}.tcl"
                )
                script_path = output_dir / filename

                with open(script_path, "w") as f:
                    f.write(script_content)

                saved_files.append(str(script_path))
                log_info_safe(
                    self.logger,
                    safe_format("Saved PCILeech script: {filename}", filename=filename),
                    prefix="TEMPLATE",
                )

            log_info_safe(
                self.logger,
                safe_format(
                    "Saved {count} PCILeech scripts to {output_dir}",
                    count=len(saved_files),
                    output_dir=output_dir,
                ),
                prefix="TEMPLATE",
            )

        except Exception as e:
            raise TCLBuilderError(
                safe_format("Failed to save PCILeech scripts: {error}", error=e)
            ) from e

        return saved_files


# Backward compatibility aliases


def create_tcl_builder(*args, **kwargs) -> TCLBuilder:
    """Factory function for creating TCL builder instances."""
    return TCLBuilder(*args, **kwargs)


# Export main classes and functions
__all__ = [
    "TCLBuilder",
    "BuildContext",
    "TCLScriptType",
    "TCLBuilderError",
    "TemplateNotFoundError",
    "DeviceConfigError",
    "XDCConstraintError",
    "create_tcl_builder",
    "format_hex_id",
    "HexFormatter",
]
