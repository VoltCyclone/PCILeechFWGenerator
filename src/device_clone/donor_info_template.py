#!/usr/bin/env python3
"""
Donor Info Template Generator

This module provides functionality to generate comprehensive donor device
information templates with behavioral profiling and advanced feature configuration.
All values in the generated template are blank for users to fill out.
"""

import json
import logging
import subprocess
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, List, Tuple

from src.exceptions import DeviceConfigError, ValidationError
from src.string_utils import (log_error_safe, log_info_safe, safe_format,
                              safe_log_format)

logger = logging.getLogger(__name__)


class DonorInfoTemplateGenerator:
    """Generates blank donor info JSON templates for user configuration."""

    @staticmethod
    def generate_blank_template() -> Dict[str, Any]:
        """
        Generate a comprehensive blank donor info template.

        Returns:
            Dict containing the complete template structure with blank/null values
        """
        from src.utils.metadata import build_generation_metadata

        safe_log_format(
            logger, logging.INFO, "Generating blank donor info template", prefix="DONOR"
        )

        # Use centralized metadata generation
        metadata = build_generation_metadata(
            device_bdf="",  # User to fill: e.g., "0000:03:00.0"
            behavioral_data_included=True,
            profile_capture_duration=None,  # User to fill: duration in seconds
            comments="",  # User can add notes about the device
            kernel_version="",  # User to fill: e.g., "6.1.0-15-amd64"
        )

        template = {
            "metadata": metadata,
            "device_info": {
                "identification": {
                    "vendor_id": None,  # User to fill: e.g., 0x8086
                    "device_id": None,  # User to fill: e.g., 0x10D3
                    "subsystem_vendor_id": None,
                    "subsystem_device_id": None,
                    "class_code": None,  # User to fill: e.g., 0x020000
                    "revision_id": None,
                    "device_name": "",  # User to fill: human-readable name
                    "manufacturer": "",  # User to fill: e.g., "Intel"
                    "model": "",  # User to fill: e.g., "82574L"
                    "serial_number": "",  # User to fill if available
                },
                "capabilities": {
                    "pcie_version": None,  # User to fill: 1, 2, 3, 4, 5
                    "link_width": None,  # User to fill: x1, x4, x8, x16
                    "link_speed": None,  # User to fill: 2.5, 5.0, 8.0, 16.0, 32.0 GT/s
                    "max_payload_size": None,  # User to fill: 128, 256, 512, etc.
                    "max_read_request": None,  # User to fill: 128, 256, 512, etc.
                    "supports_msi": None,  # User to fill: true/false
                    "supports_msix": None,  # User to fill: true/false
                    "msix_table_size": None,  # User to fill if MSI-X supported
                    "supports_sriov": None,  # User to fill: true/false
                    "num_vfs": None,  # User to fill if SR-IOV supported
                    "supports_ats": None,  # User to fill: Address Translation Service
                    "supports_pri": None,  # User to fill: Page Request Interface
                    "supports_pasid": None,  # User to fill: Process Address Space ID
                    "pasid_width": None,  # User to fill if PASID supported
                },
                "bars": [
                    {
                        "bar_number": 0,
                        "type": "",  # User to fill: "memory" or "io"
                        "size": None,  # User to fill: size in bytes
                        "prefetchable": None,  # User to fill: true/false
                        "64bit": None,  # User to fill: true/false
                        "purpose": "",  # User to fill: e.g., "control registers"
                        "typical_access_pattern": "",  # User to fill: "sequential", "random", etc.
                    }
                    # User should duplicate this structure for each BAR
                ],
                "power_management": {
                    "supports_d1": None,  # User to fill: true/false
                    "supports_d2": None,  # User to fill: true/false
                    "supports_d3hot": None,  # User to fill: true/false
                    "supports_d3cold": None,  # User to fill: true/false
                    "pme_support": None,  # User to fill: bitmask of supported states
                    "aux_current": None,  # User to fill: auxiliary current in mA
                    "d0_power": None,  # User to fill: power consumption in mW
                    "d3_power": None,  # User to fill: power consumption in mW
                },
                "error_handling": {
                    "supports_aer": None,  # User to fill: Advanced Error Reporting
                    "supports_ecrc": None,  # User to fill: End-to-End CRC
                    "correctable_errors_mask": None,  # User to fill: hex value
                    "uncorrectable_errors_mask": None,  # User to fill: hex value
                    "fatal_errors_mask": None,  # User to fill: hex value
                },
            },
            "behavioral_profile": {
                "initialization": {
                    "reset_duration_ms": None,  # User to fill: time to complete reset
                    "init_sequence": [],  # User to fill: list of register writes
                    "stabilization_delay_ms": None,  # User to fill: delay after init
                    "requires_firmware_load": None,  # User to fill: true/false
                    "firmware_load_method": "",  # User to fill if firmware required
                    "init_timeout_ms": None,  # User to fill: max init time
                },
                "runtime_behavior": {
                    "interrupt_patterns": {
                        "type": "",  # User to fill: "msi", "msix", "intx"
                        "typical_rate_hz": None,  # User to fill: interrupts per second
                        "burst_behavior": None,  # User to fill: true/false
                        "max_burst_size": None,  # User to fill if burst_behavior is true
                        "coalescing_supported": None,  # User to fill: true/false
                        "coalescing_timeout_us": None,  # User to fill if supported
                    },
                    "memory_access_patterns": {
                        "typical_read_size": None,  # User to fill: in bytes
                        "typical_write_size": None,  # User to fill: in bytes
                        "read_write_ratio": None,  # User to fill: percentage of reads
                        "sequential_access_percent": None,  # User to fill: 0-100
                        "prefetch_friendly": None,  # User to fill: true/false
                        "cache_line_aligned": None,  # User to fill: true/false
                    },
                    "timing_characteristics": {
                        "register_read_latency_ns": None,  # User to fill
                        "register_write_latency_ns": None,  # User to fill
                        "memory_read_latency_ns": None,  # User to fill
                        "memory_write_latency_ns": None,  # User to fill
                        "command_completion_timeout_ms": None,  # User to fill
                        "watchdog_timeout_ms": None,  # User to fill if applicable
                    },
                    "state_machine": {
                        "idle_state_characteristics": {},  # User to fill
                        "active_state_characteristics": {},  # User to fill
                        "transition_triggers": [],  # User to fill: list of triggers
                        "state_change_latency_us": None,  # User to fill
                    },
                },
                "dma_behavior": {
                    "supports_dma": None,  # User to fill: true/false
                    "dma_engine_count": None,  # User to fill: number of DMA engines
                    "max_dma_transfer_size": None,  # User to fill: in bytes
                    "dma_alignment_requirement": None,  # User to fill: in bytes
                    "scatter_gather_support": None,  # User to fill: true/false
                    "max_scatter_gather_entries": None,
                    "dma_direction_patterns": {
                        "host_to_device": None,  # Percentage
                        "device_to_host": None,  # Percentage
                        "bidirectional": None,  # Percentage
                    },
                    "dma_timing_patterns": [
                        {
                            "size_bytes": None,
                            "setup_time_us": None,
                            "transfer_time_us": None,
                            "teardown_time_us": None,
                        }
                    ],
                    "dma_channels": [
                        {
                            "channel_id": None,
                            "direction": "",
                            "priority": None,
                            "typical_usage": "",
                        }
                    ],
                },
                "error_injection_response": {
                    "handles_surprise_removal": None,  # User to fill: true/false
                    "handles_bus_errors": None,  # User to fill: true/false
                    "handles_parity_errors": None,  # User to fill: true/false
                    "recovery_mechanism": "",  # User to fill: "reset", "retry", etc.
                    "error_reporting_method": "",  # User to fill
                    "max_retry_count": None,  # User to fill
                    "retry_delay_ms": None,  # User to fill
                },
                "performance_profile": {
                    "sustained_throughput_mbps": None,  # User to fill
                    "peak_throughput_mbps": None,  # User to fill
                    "typical_latency_us": None,  # User to fill
                    "worst_case_latency_us": None,  # User to fill
                    "iops_read": None,  # User to fill: IO operations per second
                    "iops_write": None,  # User to fill
                    "iops_mixed": None,  # User to fill
                    "queue_depth_impact": [],  # User to fill: performance vs queue depth
                    "thermal_throttling": {
                        "threshold_celsius": None,
                        "performance_impact_percent": None,
                    },
                },
            },
            "advanced_features": {
                "custom_protocols": {
                    "uses_vendor_specific_protocol": None,  # User to fill: true/false
                    "protocol_description": "",  # User to fill if true
                    "command_format": {},  # User to fill: command structure
                    "response_format": {},  # User to fill: response structure
                    "protocol_version": "",  # User to fill
                },
                "security_features": {
                    "supports_encryption": None,  # User to fill: true/false
                    "encryption_algorithms": [],  # User to fill: list of algorithms
                    "supports_authentication": None,  # User to fill: true/false
                    "authentication_methods": [],  # User to fill
                    "secure_boot_required": None,  # User to fill: true/false
                    "firmware_signing": None,  # User to fill: true/false
                },
                "virtualization_support": {
                    "vf_bar_layout": {},  # User to fill if SR-IOV supported
                    "vf_capabilities_differences": {},  # User to fill
                    "vf_resource_limits": {},  # User to fill
                    "pf_vf_communication": {},  # User to fill: mailbox, etc.
                    "live_migration_support": None,  # User to fill: true/false
                },
                "debug_features": {
                    "debug_registers": [],  # User to fill: list of debug registers
                    "trace_buffer_support": None,  # User to fill: true/false
                    "performance_counters": [],  # User to fill
                    "diagnostic_modes": [],  # User to fill
                    "test_patterns": [],  # User to fill: built-in test patterns
                },
                "platform_specific": {
                    "x86_specific": {},  # User to fill if applicable
                    "arm_specific": {},  # User to fill if applicable
                    "power_specific": {},  # User to fill if applicable
                    "custom_platform": {},  # User to fill for other platforms
                },
            },
            "emulation_hints": {
                "critical_features": [],  # User to fill: must-have for basic function
                "optional_features": [],  # User to fill: nice-to-have
                "performance_critical_paths": [],  # User to fill: hot paths
                "compatibility_quirks": [],  # User to fill: known issues
                "recommended_optimizations": [],  # User to fill
                "testing_recommendations": {
                    "test_cases": [],  # User to fill: specific test scenarios
                    "stress_test_parameters": {},  # User to fill
                    "validation_tools": [],  # User to fill: recommended tools
                    "known_test_failures": [],  # User to fill: expected failures
                },
            },
            "extended_behavioral_data": {
                "workload_profiles": [
                    {
                        "name": "",  # User to fill: e.g., "idle", "light", "heavy"
                        "description": "",
                        "typical_duration_ms": None,
                        "resource_usage": {
                            "cpu_percent": None,
                            "memory_mb": None,
                            "pcie_bandwidth_percent": None,
                        },
                        "io_pattern": {
                            "reads_per_second": None,
                            "writes_per_second": None,
                            "average_io_size": None,
                        },
                    }
                ],
                "state_transitions": [
                    {
                        "from_state": "",
                        "to_state": "",
                        "trigger": "",
                        "duration_us": None,
                        "side_effects": [],
                    }
                ],
                "error_recovery_sequences": [
                    {
                        "error_type": "",
                        "detection_method": "",
                        "recovery_steps": [],
                        "recovery_time_ms": None,
                        "success_rate_percent": None,
                    }
                ],
                "performance_scaling": {
                    "frequency_scaling": {},  # User to fill: performance vs frequency
                    "voltage_scaling": {},  # User to fill: performance vs voltage
                    "temperature_impact": {},  # User to fill: performance vs temp
                    "aging_impact": {},  # User to fill: degradation over time
                },
                "compatibility_matrix": {
                    "tested_platforms": [],  # User to fill: list of tested systems
                    "known_incompatibilities": [],  # User to fill
                    "driver_versions": [],  # User to fill: tested driver versions
                    "firmware_versions": [],  # User to fill: tested firmware
                },
            },
        }

        return template

    @staticmethod
    def generate_minimal_template() -> Dict[str, Any]:
        """
        Generate a minimal blank donor info template with only essential fields.

        This template contains only the most critical fields needed for basic
        device emulation, making it easier for users to get started quickly.

        Returns:
            Dict containing the minimal template structure
        """
        from src.utils.metadata import build_generation_metadata

        safe_log_format(
            logger,
            logging.INFO,
            "Generating minimal donor info template",
            prefix="DONOR",
        )

        # Use centralized metadata generation
        metadata = build_generation_metadata(
            device_bdf="",  # User to fill: e.g., "0000:03:00.0"
            template_type="minimal",
        )

        template = {
            "metadata": metadata,
            "device_info": {
                "identification": {
                    "vendor_id": None,  # User to fill: e.g., 0x8086
                    "device_id": None,  # User to fill: e.g., 0x10D3
                    "subsystem_vendor_id": None,
                    "subsystem_device_id": None,
                    "class_code": None,  # User to fill: e.g., 0x020000
                    "revision_id": None,
                },
                "capabilities": {
                    "pcie_version": None,  # User to fill: 1, 2, 3, 4, 5
                    "link_width": None,  # User to fill: x1, x4, x8, x16
                    "link_speed": None,  # User to fill: 2.5, 5.0, 8.0, 16.0, 32.0 GT/s
                },
                "bars": [
                    {
                        "bar_number": 0,
                        "type": "",  # User to fill: "memory" or "io"
                        "size": None,  # User to fill: size in bytes
                        "prefetchable": None,  # User to fill: true/false
                        "64bit": None,  # User to fill: true/false
                    }
                    # User should duplicate this structure for each BAR
                ],
            },
        }

        return template

    @staticmethod
    def save_template(filepath: Path, pretty: bool = True) -> None:
        """
        Generate and save a blank donor info template to a file.

        Args:
            filepath: Path where to save the template
            pretty: Whether to format JSON with indentation
        """
        template = DonorInfoTemplateGenerator.generate_blank_template()

        try:
            with open(filepath, "w") as f:
                if pretty:
                    json.dump(template, f, indent=2, sort_keys=False)
                else:
                    json.dump(template, f)

            safe_log_format(
                logger,
                logging.INFO,
                "Donor info template saved to: {path}",
                prefix="DONOR",
                path=filepath,
            )
        except Exception as e:
            error_msg = safe_format("Failed to save template: {error}", error=str(e))
            log_error_safe(logger, error_msg, prefix="DONOR")
            raise DeviceConfigError(error_msg) from e

    @staticmethod
    def save_template_dict(
        template: Dict[str, Any], filepath: Path, pretty: bool = True
    ) -> None:
        """
        Save a template dictionary to a file.

        Args:
            template: Template dictionary to save
            filepath: Path where to save the template
            pretty: Whether to format JSON with indentation
        """
        try:
            with open(filepath, "w") as f:
                if pretty:
                    json.dump(template, f, indent=2, sort_keys=False)
                else:
                    json.dump(template, f)

            safe_log_format(
                logger,
                logging.INFO,
                "Donor info template saved to: {path}",
                prefix="DONOR",
                path=filepath,
            )
        except Exception as e:
            error_msg = safe_format("Failed to save template: {error}", error=str(e))
            log_error_safe(logger, error_msg, prefix="DONOR")
            raise DeviceConfigError(error_msg) from e

    @staticmethod
    def generate_template_with_comments() -> str:
        """
        Generate a template with inline comments explaining each field.

        Returns:
            String containing the template with comments
        """
        # Build from the blank template to avoid drift and emit valid JSON
        base = DonorInfoTemplateGenerator.generate_blank_template()

        # Attach $comment fields to key sections. Keep it minimal to satisfy
        # documentation needs without duplicating schema.
        base["$comment"] = (
            "Template with explanatory comments provided via $comment fields. "
            "Values are blanks for users to fill; unknown keys may be ignored by tooling."
        )

        # Metadata comments
        md = base.get("metadata", {})
        if isinstance(md, dict):
            md["$comment"] = (
                "Generation metadata; device_bdf is PCIe Bus:Device.Function "
                "(e.g., 0000:03:00.0). generated_at and generator_version are auto-populated."
            )

        # Device info section comment
        di = base.get("device_info", {})
        if isinstance(di, dict):
            di["$comment"] = (
                "Static device description: identification (VID/DID/etc), "
                "capabilities (PCIe features), and BAR layout."
            )
            ident = di.get("identification", {})
            if isinstance(ident, dict):
                ident["$comment"] = (
                    "PCI identification fields. vendor_id/device_id required; "
                    "class_code is the PCI class (e.g., 0x020000)."
                )
            caps = di.get("capabilities", {})
            if isinstance(caps, dict):
                caps["$comment"] = (
                    "Key PCIe capabilities and negotiated link parameters."
                )
            bars = di.get("bars", [])
            if isinstance(bars, list) and bars:
                # Comment the first BAR object shape
                if isinstance(bars[0], dict):
                    bars[0]["$comment"] = (
                        "Describe each BAR: type=memory|io, size in bytes, "
                        "prefetchable true/false, 64bit true/false, and purpose."
                    )

        # Behavioral profile section comment
        bp = base.get("behavioral_profile", {})
        if isinstance(bp, dict):
            bp["$comment"] = (
                "Behavioral profiling hints: init timing/sequence, runtime patterns, "
                "DMA behavior, error responses, and performance."
            )
            init = bp.get("initialization", {})
            if isinstance(init, dict):
                init["$comment"] = (
                    "Initialization/reset characteristics and any firmware load steps."
                )
            runtime = bp.get("runtime_behavior", {})
            if isinstance(runtime, dict):
                runtime["$comment"] = (
                    "Interrupt and memory access patterns plus timing characteristics."
                )
            dma = bp.get("dma_behavior", {})
            if isinstance(dma, dict):
                dma["$comment"] = (
                    "DMA engines, directions, alignment, and timing patterns."
                )

        # Advanced features
        adv = base.get("advanced_features", {})
        if isinstance(adv, dict):
            adv["$comment"] = (
                "Optional/advanced capabilities: vendor protocols, security, virtualization, debug."
            )

        # Emulation hints
        eh = base.get("emulation_hints", {})
        if isinstance(eh, dict):
            eh["$comment"] = (
                "Prioritized features, quirks, and optimization/testing recommendations."
            )

        # Extended behavioral data
        ext = base.get("extended_behavioral_data", {})
        if isinstance(ext, dict):
            ext["$comment"] = (
                "Deeper behavioral datasets: workload profiles, transitions, scaling, compatibility."
            )

        # Serialize as pretty JSON
        return json.dumps(base, indent=2, sort_keys=False)

    def __init__(self):
        """Initialize the template generator."""
        self.logger = logger

    def generate_template_from_device(self, bdf: str) -> Dict[str, Any]:
        """
        Generate a template pre-filled with device information from lspci.

        Args:
            bdf: PCI device BDF (Bus:Device.Function) identifier

        Returns:
            Dict containing template with device-specific values filled

        Raises:
            DeviceConfigError: If device information cannot be read
        """
        safe_log_format(
            logger,
            logging.INFO,
            "Generating template from device {bdf}",
            prefix="DONOR",
            bdf=bdf,
        )

        # Start with blank template
        template = self.generate_blank_template()

        try:
            # Run lspci to get device info
            result = subprocess.run(
                ["lspci", "-vvvnnxxs", bdf],
                capture_output=True,
                text=True,
                check=True,
            )

            if not result.stdout:
                error_msg = safe_format(
                    "No output from lspci for device {bdf}", bdf=bdf
                )
                raise DeviceConfigError(error_msg)

            # Parse lspci output
            lines = result.stdout.strip().split("\n")
            for line in lines:
                # Extract vendor and device IDs
                if "[" in line and "]" in line and bdf in line:
                    import re

                    # Look for [vendor:device] pattern
                    match = re.search(r"\[([0-9a-fA-F]{4}):([0-9a-fA-F]{4})\]", line)
                    if match:
                        vendor_id = match.group(1)
                        device_id = match.group(2)
                        template["device_info"]["identification"][
                            "vendor_id"
                        ] = f"0x{vendor_id}"
                        template["device_info"]["identification"][
                            "device_id"
                        ] = f"0x{device_id}"

                        # Extract device name
                        name_match = re.search(r"^\S+\s+(.+?)\s+\[", line)
                        if name_match:
                            template["device_info"]["identification"]["device_name"] = (
                                name_match.group(1).strip()
                            )

                # Extract subsystem IDs
                if "Subsystem:" in line:
                    match = re.search(r"\[([0-9a-fA-F]{4}):([0-9a-fA-F]{4})\]", line)
                    if match:
                        template["device_info"]["identification"][
                            "subsystem_vendor_id"
                        ] = f"0x{match.group(1)}"
                        template["device_info"]["identification"][
                            "subsystem_device_id"
                        ] = f"0x{match.group(2)}"

                # Extract capabilities
                if "LnkCap:" in line:
                    # Extract link speed
                    speed_match = re.search(r"Speed\s+(\d+(?:\.\d+)?GT/s)", line)
                    if speed_match:
                        speed = float(speed_match.group(1).replace("GT/s", ""))
                        template["device_info"]["capabilities"]["link_speed"] = speed

                    # Extract link width
                    width_match = re.search(r"Width\s+x(\d+)", line)
                    if width_match:
                        template["device_info"]["capabilities"]["link_width"] = int(
                            width_match.group(1)
                        )

                # Check for MSI/MSI-X
                if "MSI:" in line and "Enable" in line:
                    template["device_info"]["capabilities"]["supports_msi"] = True

                if "MSI-X:" in line and "Enable" in line:
                    template["device_info"]["capabilities"]["supports_msix"] = True
                    # Try to extract table size
                    size_match = re.search(r"Count=(\d+)", line)
                    if size_match:
                        template["device_info"]["capabilities"]["msix_table_size"] = (
                            int(size_match.group(1))
                        )

            # Try to get additional info from sysfs
            sysfs_path = Path(f"/sys/bus/pci/devices/{bdf}")
            if sysfs_path.exists():
                # Read class code
                try:
                    class_file = sysfs_path / "class"
                    if class_file.exists():
                        class_code = class_file.read_text().strip()
                        template["device_info"]["identification"][
                            "class_code"
                        ] = class_code
                except Exception:
                    pass

                # Read revision
                try:
                    rev_file = sysfs_path / "revision"
                    if rev_file.exists():
                        revision = rev_file.read_text().strip()
                        template["device_info"]["identification"][
                            "revision_id"
                        ] = revision
                except Exception:
                    pass

            # Update metadata
            template["metadata"]["device_bdf"] = bdf

            safe_log_format(
                logger,
                logging.INFO,
                "Successfully generated template for device {bdf}",
                prefix="DONOR",
                bdf=bdf,
            )

        except FileNotFoundError as e:
            error_msg = safe_format(
                "lspci command not found. Please install pciutils package", 
                bdf=bdf
            )
            log_error_safe(logger, error_msg, prefix="DONOR")
            raise DeviceConfigError(error_msg) from e
        except subprocess.CalledProcessError as e:
            error_msg = safe_format(
                "Failed to run lspci for device {bdf}: {error}", bdf=bdf, error=str(e)
            )
            log_error_safe(logger, error_msg, prefix="DONOR")
            raise DeviceConfigError(error_msg) from e
        except Exception as e:
            error_msg = safe_format(
                "Error generating template from device {bdf}: {error}",
                bdf=bdf,
                error=str(e),
            )
            log_error_safe(logger, error_msg, prefix="DONOR")
            raise DeviceConfigError(error_msg) from e

        return template

    def validate_template(self, template: Dict[str, Any]) -> Tuple[bool, List[str]]:
        """
        Validate a donor info template structure.

        Args:
            template: Template dictionary to validate

        Returns:
            Tuple of (is_valid, list_of_errors)
        """
        errors = []

        # Check required top-level sections
        required_sections = ["metadata", "device_info", "behavioral_profile"]
        for section in required_sections:
            if section not in template:
                errors.append(
                    safe_format("Missing required section: {section}", section=section)
                )

        # Validate metadata
        if "metadata" in template:
            metadata = template["metadata"]
            required_metadata = ["generator_version"]
            for field in required_metadata:
                if field not in metadata:
                    errors.append(
                        safe_format(
                            "Missing required metadata field: {field}", field=field
                        )
                    )

        # Validate device_info
        if "device_info" in template:
            device_info = template["device_info"]
            if "identification" not in device_info:
                errors.append("Missing device_info.identification section")
            else:
                ident = device_info["identification"]
                # At minimum need vendor and device IDs
                if not ident.get("vendor_id"):
                    errors.append("Missing device_info.identification.vendor_id")
                if not ident.get("device_id"):
                    errors.append("Missing device_info.identification.device_id")

        # Validate behavioral_profile
        if "behavioral_profile" in template:
            profile = template["behavioral_profile"]
            # Check for at least one behavioral section
            behavioral_sections = [
                "initialization",
                "runtime_behavior",
                "dma_behavior",
                "error_injection_response",
                "performance_profile",
            ]
            has_behavioral_data = any(
                section in profile for section in behavioral_sections
            )
            if not has_behavioral_data:
                errors.append(
                    "behavioral_profile must contain at least one behavioral section"
                )

        is_valid = len(errors) == 0
        if is_valid:
            safe_log_format(
                logger, logging.INFO, "Template validation successful", prefix="DONOR"
            )
        else:
            safe_log_format(
                logger,
                logging.WARNING,
                "Template validation failed with {count} errors",
                prefix="DONOR",
                count=len(errors),
            )

        return is_valid, errors

    def validate_template_file(self, filepath: str) -> Tuple[bool, List[str]]:
        """
        Validate a donor info template file.

        Args:
            filepath: Path to the template file

        Returns:
            Tuple of (is_valid, list_of_errors)

        Raises:
            ValidationError: If file cannot be read or parsed
        """
        try:
            with open(filepath, "r") as f:
                template = json.load(f)

            return self.validate_template(template)

        except FileNotFoundError:
            error_msg = safe_format("Template file not found: {path}", path=filepath)
            log_error_safe(logger, error_msg, prefix="DONOR")
            raise ValidationError(error_msg)
        except json.JSONDecodeError as e:
            error_msg = safe_format(
                "Invalid JSON in {path}: {error}", path=filepath, error=str(e)
            )
            log_error_safe(logger, error_msg, prefix="DONOR")
            raise ValidationError(error_msg) from e

    @staticmethod
    def load_template(filepath: str) -> Dict[str, Any]:
        """
        Load a donor info template from a JSON file.

        Args:
            filepath: Path to the JSON template file

        Returns:
            Dict containing the loaded template

        Raises:
            ValidationError: If file cannot be loaded or parsed
        """
        try:
            with open(filepath, "r") as f:
                template = json.load(f)
            return template
        except FileNotFoundError:
            raise DeviceConfigError(f"Template file not found: {filepath}")
        except json.JSONDecodeError as e:
            raise DeviceConfigError(f"Invalid JSON in template file: {e}")
        except Exception as e:
            raise DeviceConfigError(f"Failed to load template: {e}")

    @staticmethod
    def merge_template_with_discovered(
        template: Dict[str, Any], discovered: Dict[str, Any]
    ) -> Dict[str, Any]:
        """
        Merge template values with discovered values.
        Template values take precedence over discovered values.
        Null values in the template are ignored.

        Args:
            template: Template dictionary with user-provided values
            discovered: Dictionary with discovered device values

        Returns:
            Merged dictionary with template values overriding discovered values
        """

        def merge_dicts(template_dict: Dict, discovered_dict: Dict) -> Dict:
            """Recursively merge two dictionaries."""
            result = discovered_dict.copy()

            for key, value in template_dict.items():
                if value is None:
                    # Skip null values in template
                    continue
                elif (
                    isinstance(value, dict)
                    and key in result
                    and isinstance(result[key], dict)
                ):
                    # Recursively merge nested dictionaries
                    result[key] = merge_dicts(value, result[key])
                elif (
                    isinstance(value, list)
                    and key in result
                    and isinstance(result[key], list)
                ):
                    # Special handling for lists (like bars)
                    # Merge lists by index if they contain dicts
                    if value and isinstance(value[0], dict):
                        # Merge list of dicts by index
                        merged_list = result[key].copy()
                        for i, item in enumerate(value):
                            if i < len(merged_list):
                                # Merge with existing item
                                if isinstance(item, dict) and isinstance(
                                    merged_list[i], dict
                                ):
                                    merged_list[i] = merge_dicts(item, merged_list[i])
                                else:
                                    merged_list[i] = item
                            else:
                                # Append new item
                                merged_list.append(item)
                        result[key] = merged_list
                    else:
                        # For simple lists, just replace
                        result[key] = value
                else:
                    # Template value overrides discovered value
                    result[key] = value

            return result

        # Simply use the generic merge function for the entire structure
        return merge_dicts(template, discovered)
