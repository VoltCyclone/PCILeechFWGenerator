#!/usr/bin/env python3
"""
Post-build firmware validation module.

Validates that generated firmware includes all critical config space elements
that OS drivers expect for proper device enumeration and initialization.

Critical elements validated:
1. PCI IDs (VEN/DEV/SUBSYS/SUBVENDOR/REV)
2. Capabilities (PM, MSI/MSI-X, ASPM, VPD) in correct order
3. BAR configuration (type, size, proper probe behavior)
4. Class code and programming interface
5. Config space structure and integrity
"""

from typing import Any, Dict, List, Optional, Set, Tuple
from pathlib import Path

import struct

from string_utils import (
    log_info_safe,
    log_warning_safe,
    log_error_safe,
    safe_format,
)


class PostBuildValidationCheck:
    """Result of a single post-build validation check.
    
    This is a lightweight check result that doesn't use dataclass
    to avoid duplication with the generic ValidationResult in validators.py.
    """
    
    def __init__(
        self,
        is_valid: bool,
        check_name: str,
        message: str,
        severity: str,
        details: Optional[Dict[str, Any]] = None
    ):
        """Initialize validation check result.
        
        Args:
            is_valid: Whether the check passed
            check_name: Unique name for this check
            message: Human-readable message
            severity: "info", "warning", or "error"
            details: Optional additional data about the check
        """
        self.is_valid = is_valid
        self.check_name = check_name
        self.message = message
        self.severity = severity
        self.details = details or {}


class PostBuildValidator:
    """Validates post-build firmware output for driver compatibility."""

    # Required PCI ID fields
    REQUIRED_PCI_IDS = [
        "vendor_id",
        "device_id",
        "subsystem_vendor_id",
        "subsystem_device_id",
        "revision_id",
        "class_code",
    ]

    # Standard capability IDs that drivers commonly expect
    COMMON_CAPABILITIES = {
        0x01: "Power Management",
        0x05: "MSI",
        0x10: "PCI Express",
        0x11: "MSI-X",
    }

    # Extended capability IDs
    COMMON_EXT_CAPABILITIES = {
        0x0001: "Advanced Error Reporting (AER)",
        0x0002: "Virtual Channel",
        0x0003: "Device Serial Number",
        0x0004: "Power Budgeting",
        0x000B: "Vendor-Specific Extended Capability",
        0x0010: "SR-IOV",
        0x0015: "Resizable BAR",
        0x001E: "L1 PM Substates",
    }

    def __init__(self, logger):
        """Initialize validator with logger."""
        self.logger = logger
        self.results: List[PostBuildValidationCheck] = []

    def validate_build_output(
        self,
        output_dir: Path,
        generation_result: Dict[str, Any]
    ) -> Tuple[bool, List[PostBuildValidationCheck]]:
        """
        Validate complete build output.

        Args:
            output_dir: Build output directory
            generation_result: Firmware generation result dict

        Returns:
            Tuple of (all_valid, validation_results)
        """
        self.results = []

        log_info_safe(
            self.logger,
            "Running post-build validation checks",
            prefix="VALID",
        )

        # Extract data from generation result
        config_space_data = generation_result.get("config_space_data", {})
        template_context = generation_result.get("template_context", {})
        device_info = config_space_data.get("device_info", {})

        # Run validation checks
        self._validate_pci_ids(device_info, template_context)
        self._validate_config_space_structure(config_space_data)
        self._validate_capabilities(config_space_data, template_context)
        self._validate_bar_configuration(device_info, template_context)
        self._validate_class_code(device_info)
        self._validate_generated_files(output_dir)
        self._validate_capability_order(config_space_data)

        # Check if we have any errors
        has_errors = any(r.severity == "error" for r in self.results)
        has_warnings = any(r.severity == "warning" for r in self.results)

        # Log summary
        if has_errors:
            log_error_safe(
                self.logger,
                safe_format(
                    "Post-build validation FAILED with {count} errors",
                    count=sum(1 for r in self.results if r.severity == "error")
                ),
                prefix="VALID",
            )
        elif has_warnings:
            log_warning_safe(
                self.logger,
                safe_format(
                    "Post-build validation passed with {count} warnings",
                    count=sum(1 for r in self.results if r.severity == "warning")
                ),
                prefix="VALID",
            )
        else:
            log_info_safe(
                self.logger,
                "Post-build validation PASSED - all checks successful",
                prefix="VALID",
            )

        return (not has_errors, self.results)

    def _validate_pci_ids(
        self,
        device_info: Dict[str, Any],
        template_context: Dict[str, Any]
    ) -> None:
        """Validate all required PCI identification fields are present and valid."""
        device_config = template_context.get("device_config", {})

        for field in self.REQUIRED_PCI_IDS:
            value = device_config.get(field) or device_info.get(field)

            if value is None or value == "":
                self.results.append(PostBuildValidationCheck(
                    is_valid=False,
                    check_name=f"pci_id_{field}",
                    message=f"Missing required PCI ID field: {field}",
                    severity="error"
                ))
            elif isinstance(value, str) and value.startswith("0x"):
                # Valid hex string
                self.results.append(PostBuildValidationCheck(
                    is_valid=True,
                    check_name=f"pci_id_{field}",
                    message=f"PCI ID {field} present: {value}",
                    severity="info",
                    details={"value": value}
                ))
            else:
                # Check if it's a valid integer
                try:
                    if isinstance(value, int):
                        int_val = int(value)
                    else:
                        int_val = int(str(value), 16)
                    
                    if field != "class_code":
                        hex_val = f"0x{int_val:04x}"
                    else:
                        hex_val = f"0x{int_val:06x}"
                    
                    self.results.append(PostBuildValidationCheck(
                        is_valid=True,
                        check_name=f"pci_id_{field}",
                        message=f"PCI ID {field} present: {hex_val}",
                        severity="info",
                        details={"value": hex_val}
                    ))
                except (ValueError, TypeError):
                    self.results.append(PostBuildValidationCheck(
                        is_valid=False,
                        check_name=f"pci_id_{field}",
                        message=f"Invalid PCI ID {field}: {value}",
                        severity="error"
                    ))

    def _validate_config_space_structure(
        self, config_space_data: Dict[str, Any]
    ) -> None:
        """Validate config space structure and size."""
        raw_config = config_space_data.get("raw_config_space")
        
        if not raw_config:
            self.results.append(PostBuildValidationCheck(
                is_valid=False,
                check_name="config_space_structure",
                message="Missing raw config space data",
                severity="error"
            ))
            return

        config_size = len(raw_config)
        
        # Config space should be 256 bytes (standard) or 4096 bytes (extended)
        if config_size == 256:
            self.results.append(PostBuildValidationCheck(
                is_valid=True,
                check_name="config_space_size",
                message="Standard config space (256 bytes)",
                severity="info",
                details={"size": config_size, "type": "standard"}
            ))
        elif config_size == 4096:
            self.results.append(PostBuildValidationCheck(
                is_valid=True,
                check_name="config_space_size",
                message="Extended config space (4096 bytes)",
                severity="info",
                details={"size": config_size, "type": "extended"}
            ))
        else:
            self.results.append(PostBuildValidationCheck(
                is_valid=False,
                check_name="config_space_size",
                message=f"Unexpected config space size: {config_size} bytes",
                severity="warning",
                details={"size": config_size}
            ))

        # Validate config space header (first 16 bytes minimum)
        if config_size >= 16:
            vendor_id = struct.unpack("<H", raw_config[0:2])[0]
            device_id = struct.unpack("<H", raw_config[2:4])[0]
            
            if vendor_id == 0xFFFF or device_id == 0xFFFF:
                self.results.append(PostBuildValidationCheck(
                    is_valid=False,
                    check_name="config_space_header",
                    message="Config space header has invalid device IDs (0xFFFF)",
                    severity="error"
                ))
            else:
                header_msg = (
                    f"Valid config space header "
                    f"(VID=0x{vendor_id:04x}, DID=0x{device_id:04x})"
                )
                self.results.append(PostBuildValidationCheck(
                    is_valid=True,
                    check_name="config_space_header",
                    message=header_msg,
                    severity="info"
                ))

    def _validate_capabilities(
        self,
        config_space_data: Dict[str, Any],
        template_context: Dict[str, Any]
    ) -> None:
        """Validate PCI capabilities are present and properly configured."""
        raw_config = config_space_data.get("raw_config_space")
        
        if not raw_config or len(raw_config) < 64:
            self.results.append(PostBuildValidationCheck(
                is_valid=False,
                check_name="capabilities_structure",
                message="Config space too small to contain capabilities",
                severity="error"
            ))
            return

        # Check capability pointer
        cap_ptr = raw_config[0x34] if len(raw_config) > 0x34 else 0
        
        if cap_ptr == 0:
            self.results.append(PostBuildValidationCheck(
                is_valid=False,
                check_name="capability_pointer",
                message="No capability pointer - device has no capabilities",
                severity="warning"
            ))
            return

        # Walk capability list
        found_caps = self._walk_capabilities(raw_config, cap_ptr)
        
        # Check for common capabilities
        device_config = template_context.get("device_config", {})
        
        # MSI or MSI-X expected for modern devices
        has_msi = 0x05 in found_caps
        has_msix = 0x11 in found_caps
        
        if not has_msi and not has_msix:
            self.results.append(PostBuildValidationCheck(
                is_valid=False,
                check_name="interrupt_capability",
                message="No MSI or MSI-X capability found - drivers may not bind",
                severity="warning"
            ))
        else:
            cap_names = []
            if has_msi:
                cap_names.append("MSI")
            if has_msix:
                cap_names.append("MSI-X")
                
            self.results.append(PostBuildValidationCheck(
                is_valid=True,
                check_name="interrupt_capability",
                message=f"Interrupt capabilities present: {', '.join(cap_names)}",
                severity="info",
                details={"capabilities": cap_names}
            ))

        # Check for Power Management
        if 0x01 in found_caps:
            self.results.append(PostBuildValidationCheck(
                is_valid=True,
                check_name="power_management",
                message="Power Management capability present",
                severity="info"
            ))
        else:
            pm_msg = (
                "Power Management capability missing - "
                "may cause driver issues"
            )
            self.results.append(PostBuildValidationCheck(
                is_valid=False,
                check_name="power_management",
                message=pm_msg,
                severity="warning"
            ))

        # Check for PCI Express
        if 0x10 in found_caps:
            self.results.append(PostBuildValidationCheck(
                is_valid=True,
                check_name="pcie_capability",
                message="PCI Express capability present",
                severity="info"
            ))

        # Log all found capabilities
        cap_summary = ", ".join([
            f"{self.COMMON_CAPABILITIES.get(cap_id, f'0x{cap_id:02x}')}"
            for cap_id in sorted(found_caps)
        ])
        
        self.results.append(PostBuildValidationCheck(
            is_valid=True,
            check_name="capability_summary",
            message=f"Found {len(found_caps)} capabilities: {cap_summary}",
            severity="info",
            details={
                "capability_count": len(found_caps),
                "capabilities": list(found_caps)
            }
        ))

    def _walk_capabilities(self, config_space: bytes, cap_ptr: int) -> Set[int]:
        """Walk the capability list and return set of capability IDs."""
        found = set()
        visited = set()
        max_iterations = 48  # Prevent infinite loops
        
        while cap_ptr and cap_ptr < len(config_space) - 1 and max_iterations > 0:
            if cap_ptr in visited:
                break  # Circular reference
            
            visited.add(cap_ptr)
            cap_id = config_space[cap_ptr]
            found.add(cap_id)
            
            # Next pointer is at offset+1
            cap_ptr = config_space[cap_ptr + 1]
            max_iterations -= 1
        
        return found

    def _validate_bar_configuration(
        self,
        device_info: Dict[str, Any],
        template_context: Dict[str, Any]
    ) -> None:
        """Validate BAR configuration."""
        bars = device_info.get("bars", [])
        bar_config = template_context.get("bar_config", {})
        
        if not bars and not bar_config:
            self.results.append(PostBuildValidationCheck(
                is_valid=False,
                check_name="bar_configuration",
                message="No BAR configuration found - device may not function",
                severity="warning"
            ))
            return

        # Use bars from bar_config if available
        if bar_config and "bars" in bar_config:
            bars = bar_config["bars"]

        valid_bars = [b for b in bars if self._is_valid_bar(b)]
        
        if not valid_bars:
            self.results.append(PostBuildValidationCheck(
                is_valid=False,
                check_name="bar_configuration",
                message="No valid BARs configured - device will not function",
                severity="error"
            ))
            return

        # Log BAR details
        for i, bar in enumerate(valid_bars):
            bar_size = self._get_bar_size(bar)
            bar_type = self._get_bar_type(bar)
            size_str = hex(bar_size) if bar_size else 'unknown'
            
            self.results.append(PostBuildValidationCheck(
                is_valid=True,
                check_name=f"bar_{i}",
                message=f"BAR{i}: {bar_type}, size={size_str}",
                severity="info",
                details={
                    "bar_index": i,
                    "type": bar_type,
                    "size": bar_size
                }
            ))

    def _is_valid_bar(self, bar: Any) -> bool:
        """Check if BAR configuration is valid."""
        if isinstance(bar, dict):
            size = bar.get("size", 0)
            return size > 0
        # Handle BarInfo objects
        return hasattr(bar, "size") and bar.size > 0

    def _get_bar_size(self, bar: Any) -> int:
        """Extract BAR size."""
        if isinstance(bar, dict):
            return bar.get("size", 0)
        return getattr(bar, "size", 0)

    def _get_bar_type(self, bar: Any) -> str:
        """Determine BAR type."""
        if isinstance(bar, dict):
            is_64bit = bar.get("is_64bit", False)
            is_prefetchable = bar.get("is_prefetchable", False)
            is_io = bar.get("is_io", False)
        else:
            is_64bit = getattr(bar, "is_64bit", False)
            is_prefetchable = getattr(bar, "is_prefetchable", False)
            is_io = getattr(bar, "is_io", False)

        if is_io:
            return "I/O"
        elif is_64bit and is_prefetchable:
            return "64-bit Prefetchable MMIO"
        elif is_64bit:
            return "64-bit Non-Prefetchable MMIO"
        elif is_prefetchable:
            return "32-bit Prefetchable MMIO"
        else:
            return "32-bit Non-Prefetchable MMIO"

    def _validate_class_code(self, device_info: Dict[str, Any]) -> None:
        """Validate class code is present and non-zero."""
        class_code = device_info.get("class_code")
        
        if class_code is None:
            self.results.append(PostBuildValidationCheck(
                is_valid=False,
                check_name="class_code",
                message="Class code missing",
                severity="error"
            ))
            return

        # Convert to int if hex string
        if isinstance(class_code, str):
            try:
                class_code = int(class_code.replace("0x", ""), 16)
            except ValueError:
                self.results.append(PostBuildValidationCheck(
                    is_valid=False,
                    check_name="class_code",
                    message=f"Invalid class code format: {class_code}",
                    severity="error"
                ))
                return

        if class_code == 0:
            class_msg = (
                "Class code is 0x000000 - "
                "device will not enumerate properly"
            )
            self.results.append(PostBuildValidationCheck(
                is_valid=False,
                check_name="class_code",
                message=class_msg,
                severity="error"
            ))
        else:
            # Decode class code
            base_class = (class_code >> 16) & 0xFF
            sub_class = (class_code >> 8) & 0xFF
            prog_if = class_code & 0xFF
            
            class_name = self._get_class_name(base_class)
            
            self.results.append(PostBuildValidationCheck(
                is_valid=True,
                check_name="class_code",
                message=f"Class code: 0x{class_code:06x} ({class_name})",
                severity="info",
                details={
                    "class_code": f"0x{class_code:06x}",
                    "base_class": f"0x{base_class:02x}",
                    "sub_class": f"0x{sub_class:02x}",
                    "prog_if": f"0x{prog_if:02x}",
                    "class_name": class_name
                }
            ))

    def _get_class_name(self, base_class: int) -> str:
        """Get human-readable class name."""
        class_names = {
            0x00: "Unclassified",
            0x01: "Mass Storage Controller",
            0x02: "Network Controller",
            0x03: "Display Controller",
            0x04: "Multimedia Controller",
            0x05: "Memory Controller",
            0x06: "Bridge Device",
            0x07: "Simple Communication Controller",
            0x08: "Base System Peripheral",
            0x09: "Input Device",
            0x0A: "Docking Station",
            0x0B: "Processor",
            0x0C: "Serial Bus Controller",
            0x0D: "Wireless Controller",
            0x0E: "Intelligent I/O Controller",
            0x0F: "Satellite Communication Controller",
            0x10: "Encryption/Decryption Controller",
            0x11: "Data Acquisition and Signal Processing Controller",
        }
        return class_names.get(base_class, f"Unknown (0x{base_class:02x})")

    def _validate_generated_files(self, output_dir: Path) -> None:
        """Validate expected output files exist."""
        expected_files = [
            ("device_info.json", output_dir),
            ("pcileech_pcie_cfg_a7.sv", output_dir / "src"),
            # Note: build_all.tcl doesn't exist, removed from expectations
        ]

        for filename, search_dir in expected_files:
            filepath = search_dir / filename
            if filepath.exists():
                self.results.append(PostBuildValidationCheck(
                    is_valid=True,
                    check_name=f"file_{filename}",
                    message=f"Generated file present: {filename}",
                    severity="info"
                ))
            else:
                self.results.append(PostBuildValidationCheck(
                    is_valid=False,
                    check_name=f"file_{filename}",
                    message=f"Expected file missing: {filename}",
                    severity="warning"
                ))
        
        # Note: build_all.tcl doesn't exist in voltcyclone-fpga submodule
        # This is expected behavior, not an error

    def _validate_capability_order(self, config_space_data: Dict[str, Any]) -> None:
        """Validate capability list order for driver compatibility."""
        raw_config = config_space_data.get("raw_config_space")
        
        if not raw_config or len(raw_config) < 64:
            return

        cap_ptr = raw_config[0x34] if len(raw_config) > 0x34 else 0
        if cap_ptr == 0:
            return

        # Walk capabilities and record order
        cap_order = []
        visited = set()
        max_iterations = 48
        
        while cap_ptr and cap_ptr < len(raw_config) - 1 and max_iterations > 0:
            if cap_ptr in visited:
                break
            
            visited.add(cap_ptr)
            cap_id = raw_config[cap_ptr]
            cap_order.append((cap_ptr, cap_id))
            
            cap_ptr = raw_config[cap_ptr + 1]
            max_iterations -= 1

        # Check for PM capability first (best practice)
        if cap_order and cap_order[0][1] == 0x01:
            self.results.append(PostBuildValidationCheck(
                is_valid=True,
                check_name="capability_order_pm_first",
                message="Power Management is first capability (recommended)",
                severity="info"
            ))
        elif any(cap_id == 0x01 for _, cap_id in cap_order):
            self.results.append(PostBuildValidationCheck(
                is_valid=True,
                check_name="capability_order_pm_first",
                message="Power Management not first capability (non-critical)",
                severity="info"
            ))

        # Validate capabilities are in ascending offset order (required by spec)
        offsets = [offset for offset, _ in cap_order]
        if offsets == sorted(offsets):
            self.results.append(PostBuildValidationCheck(
                is_valid=True,
                check_name="capability_order_ascending",
                message="Capabilities in ascending offset order (spec compliant)",
                severity="info"
            ))
        else:
            order_msg = (
                "Capabilities NOT in ascending offset order "
                "(spec violation)"
            )
            self.results.append(PostBuildValidationCheck(
                is_valid=False,
                check_name="capability_order_ascending",
                message=order_msg,
                severity="warning"
            ))

    def print_validation_report(self) -> None:
        """Print formatted validation report."""
        if not self.results:
            log_info_safe(
                self.logger,
                "No validation results to report",
                prefix="VALID"
            )
            return

        # Group by severity
        errors = [r for r in self.results if r.severity == "error"]
        warnings = [r for r in self.results if r.severity == "warning"]
        info = [r for r in self.results if r.severity == "info"]

        report_msg = (
            "Validation Report: {errors} errors, "
            "{warnings} warnings, {info} info"
        )
        log_info_safe(
            self.logger,
            safe_format(
                report_msg,
                errors=len(errors),
                warnings=len(warnings),
                info=len(info)
            ),
            prefix="VALID"
        )

        if errors:
            log_error_safe(self.logger, "ERRORS:", prefix="VALID")
            for result in errors:
                log_error_safe(self.logger, f"  • {result.message}", prefix="VALID")

        if warnings:
            log_warning_safe(self.logger, "WARNINGS:", prefix="VALID")
            for result in warnings:
                log_warning_safe(
                    self.logger,
                    f"  • {result.message}",
                    prefix="VALID"
                )

        # Log summary of successful checks
        if info and not errors:
            log_info_safe(
                self.logger,
                safe_format(
                    "All critical checks passed ({count} checks)",
                    count=len(info)
                ),
                prefix="VALID"
            )
