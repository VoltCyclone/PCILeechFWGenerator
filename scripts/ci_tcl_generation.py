"""
CI Script for E2E TCL Generation Testing

This script generates all TCL files for a variety of test device configurations
to validate that the complete build pipeline works end-to-end without requiring
actual hardware access.

The script:
1. Creates comprehensive test device configurations
2. Generates full template contexts using UnifiedContextBuilder
3. Builds all TCL files using TCLBuilder
4. Validates that generated TCL files meet quality standards
5. Reports on success/failure with actionable messages

Usage:
    python3 scripts/ci_tcl_generation.py
    python3 scripts/ci_tcl_generation.py --output ci_tcl_output/
    python3 scripts/ci_tcl_generation.py --verbose --board pcileech_35t325_x4
"""

from __future__ import annotations

import argparse
import logging
import sys
from pathlib import Path
from typing import Any, Dict, List, NamedTuple

# Add project root to path
project_root = Path(__file__).parent.parent
sys.path.insert(0, str(project_root))

from src.log_config import get_logger, setup_logging

from src.string_utils import (
    log_debug_safe,
    log_error_safe,
    log_info_safe,
    log_warning_safe,
    safe_format,
)

from src.templating.tcl_builder import BuildContext, TCLBuilder

from src.utils.unified_context import UnifiedContextBuilder


class TestDevice(NamedTuple):
    """Test device configuration."""

    name: str
    vendor_id: str
    device_id: str
    device_type: str
    device_class: str
    description: str
    subsystem_vendor: str | None = None
    subsystem_device: str | None = None
    revision_id: str = "00"
    class_code: str = "020000"
    bar_sizes: List[int] | None = None


# Comprehensive test device configurations covering various PCI device types
TEST_DEVICES = [
    TestDevice(
        name="xilinx_7series_fpga",
        vendor_id="10ee",
        device_id="7024",
        device_type="fpga",
        device_class="enterprise",
        description="Xilinx 7-Series FPGA (Common Development Board)",
        subsystem_vendor="10ee",
        subsystem_device="0007",
        class_code="058000",  # System peripheral
        bar_sizes=[0x4000, 0x4000, 0, 0, 0, 0],
    ),
    TestDevice(
        name="intel_i350_nic",
        vendor_id="8086",
        device_id="1521",
        device_type="network",
        device_class="enterprise",
        description="Intel I350 Gigabit Network Connection",
        subsystem_vendor="8086",
        subsystem_device="5002",
        class_code="020000",  # Ethernet controller
        bar_sizes=[0x20000, 0x4000, 0x4000, 0, 0, 0],
    ),
    TestDevice(
        name="nvidia_rtx3080",
        vendor_id="10de",
        device_id="2206",
        device_type="gpu",
        device_class="consumer",
        description="NVIDIA GeForce RTX 3080",
        subsystem_vendor="10de",
        subsystem_device="1467",
        class_code="030000",  # VGA compatible controller
        bar_sizes=[0x1000000, 0x10000000, 0x2000000, 0x80000, 0, 0],
    ),
    TestDevice(
        name="amd_rx6800",
        vendor_id="1002",
        device_id="73bf",
        device_type="gpu",
        device_class="consumer",
        description="AMD Radeon RX 6800",
        subsystem_vendor="1002",
        subsystem_device="0e3b",
        class_code="030000",  # VGA compatible controller
        bar_sizes=[0x10000000, 0x800000, 0x100000, 0x80000, 0, 0],
    ),
    TestDevice(
        name="broadcom_bcm57810",
        vendor_id="14e4",
        device_id="168e",
        device_type="network",
        device_class="enterprise",
        description="Broadcom BCM57810 10GbE NIC",
        subsystem_vendor="14e4",
        subsystem_device="168e",
        class_code="020000",  # Ethernet controller
        bar_sizes=[0x800000, 0x800000, 0x800000, 0, 0, 0],
    ),
    TestDevice(
        name="mellanox_cx4",
        vendor_id="15b3",
        device_id="1013",
        device_type="network",
        device_class="enterprise",
        description="Mellanox ConnectX-4 100GbE NIC",
        subsystem_vendor="15b3",
        subsystem_device="0050",
        class_code="020000",  # Ethernet controller
        bar_sizes=[0x100000, 0x1000000, 0, 0, 0, 0],
    ),
    TestDevice(
        name="intel_nvme_p4510",
        vendor_id="8086",
        device_id="0a54",
        device_type="storage",
        device_class="enterprise",
        description="Intel NVMe P4510 SSD",
        subsystem_vendor="8086",
        subsystem_device="3704",
        class_code="010802",  # Non-Volatile memory controller
        bar_sizes=[0x4000, 0, 0, 0, 0, 0],
    ),
    TestDevice(
        name="samsung_nvme_980pro",
        vendor_id="144d",
        device_id="a80a",
        device_type="storage",
        device_class="consumer",
        description="Samsung 980 PRO NVMe SSD",
        subsystem_vendor="144d",
        subsystem_device="a801",
        class_code="010802",  # Non-Volatile memory controller
        bar_sizes=[0x4000, 0, 0, 0, 0, 0],
    ),
]

# Default board configurations for testing
DEFAULT_BOARDS = [
    "pcileech_35t325_x4",
    "pcileech_75t_x1",
    "pcileech_100t_x1",
]


class TCLGenerationValidator:
    """Validates generated TCL files for quality and correctness."""

    def __init__(self, logger: logging.Logger):
        """Initialize validator."""
        self.logger = logger
        self.validations_passed = 0
        self.validations_failed = 0

    def validate_tcl_file(self, tcl_path: Path, device: TestDevice) -> bool:
        """
        Validate a single TCL file.

        Args:
            tcl_path: Path to TCL file
            device: Test device configuration

        Returns:
            True if validation passed
        """
        if not tcl_path.exists():
            log_error_safe(
                self.logger,
                safe_format("TCL file missing: {path}", path=tcl_path),
                prefix="VALIDATE",
            )
            self.validations_failed += 1
            return False

        content = tcl_path.read_text(encoding="utf-8")

        # Validation checks
        checks = [
            (
                device.vendor_id.lower() in content.lower()
                or device.vendor_id.upper() in content,
                f"Vendor ID {device.vendor_id} not found in TCL",
            ),
            (
                device.device_id.lower() in content.lower()
                or device.device_id.upper() in content,
                f"Device ID {device.device_id} not found in TCL",
            ),
            (len(content) > 100, "TCL file suspiciously small (<100 bytes)"),
            (
                "set " in content or "create_" in content,
                "TCL file missing expected Vivado commands",
            ),
        ]

        all_passed = True
        for check_passed, error_msg in checks:
            if not check_passed:
                log_error_safe(
                    self.logger,
                    safe_format(
                        "Validation failed for {path}: {msg}",
                        path=tcl_path.name,
                        msg=error_msg,
                    ),
                    prefix="VALIDATE",
                )
                all_passed = False

        if all_passed:
            self.validations_passed += 1
            log_debug_safe(
                self.logger,
                safe_format("Validated {path}", path=tcl_path.name),
                prefix="VALIDATE",
            )
        else:
            self.validations_failed += 1

        return all_passed

    def report_summary(self) -> bool:
        """
        Report validation summary.

        Returns:
            True if all validations passed
        """
        total = self.validations_passed + self.validations_failed
        success_rate = (
            (self.validations_passed / total * 100) if total > 0 else 0.0
        )

        log_info_safe(
            self.logger,
            safe_format(
                "Validation summary: {passed}/{total} passed ({rate:.1f}%)",
                passed=self.validations_passed,
                total=total,
                rate=success_rate,
            ),
            prefix="VALIDATE",
        )

        return self.validations_failed == 0


class CITCLGenerator:
    """Main CI TCL generation orchestrator."""

    def __init__(self, output_dir: Path, board: str | None = None):
        """
        Initialize CI TCL generator.

        Args:
            output_dir: Output directory for generated TCL files
            board: Optional board override (uses defaults if None)
        """
        self.output_dir = output_dir
        self.boards = [board] if board else DEFAULT_BOARDS
        self.logger = get_logger("ci_tcl_generation")
        self.context_builder = UnifiedContextBuilder()
        self.validator = TCLGenerationValidator(self.logger)
        self.successful_builds = 0
        self.failed_builds = 0

    def create_device_context(self, device: TestDevice, board: str) -> Dict[str, Any]:
        """
        Create complete template context for a test device.

        Args:
            device: Test device configuration
            board: Board name

        Returns:
            Complete template context dictionary
        """
        log_info_safe(
            self.logger,
            safe_format(
                "Building context for {name} on {board}",
                name=device.name,
                board=board,
            ),
            prefix="BUILD",
        )

        # Create complete template context with all required variables
        context_obj = self.context_builder.create_complete_template_context(
            vendor_id=device.vendor_id,
            device_id=device.device_id,
            device_type=device.device_type,
            device_class=device.device_class,
            # Additional overrides
            revision_id=device.revision_id,
            class_code=device.class_code,
            subsystem_vendor=device.subsystem_vendor or device.vendor_id,
            subsystem_device=device.subsystem_device or device.device_id,
            board=board,
            device_name=device.description,
        )

        # Convert to dict for TCLBuilder
        context = context_obj.to_dict()

        # Add BAR configuration if specified
        if device.bar_sizes:
            bars = []
            for idx, size in enumerate(device.bar_sizes):
                if size > 0:
                    bars.append(
                        {
                            "index": idx,
                            "size": size,
                            "type": "MMIO64" if idx == 0 else "MMIO32",
                            "prefetchable": idx == 0,
                        }
                    )
            context["bar_config"] = {"bars": bars, "total_bars": len(bars)}

        return context

    def generate_tcl_for_device(
        self, device: TestDevice, board: str
    ) -> tuple[bool, Path | None]:
        """
        Generate all TCL files for a single device configuration.

        Args:
            device: Test device configuration
            board: Board name

        Returns:
            Tuple of (success, output_path)
        """
        try:
            # Create output directory for this device/board combination
            device_output_dir = (
                self.output_dir / board / device.name
            )
            device_output_dir.mkdir(parents=True, exist_ok=True)

            # Build context
            context = self.create_device_context(device, board)

            # Initialize TCL builder
            tcl_builder = TCLBuilder(output_dir=device_output_dir)

            # Create BuildContext
            build_context = tcl_builder.create_build_context(
                vendor_id=device.vendor_id,
                device_id=device.device_id,
                board=board,
                device_config=context.get("device", {}),
                bar_config=context.get("bar_config", {}),
                additional_context=context,
            )

            # Generate all TCL scripts
            log_info_safe(
                self.logger,
                safe_format(
                    "Generating TCL scripts for {name} on {board}",
                    name=device.name,
                    board=board,
                ),
                prefix="BUILD",
            )

            tcl_scripts = tcl_builder.build_all_tcl_scripts(
                context=build_context,
                board=board,
            )

            # Write TCL scripts to files
            for script_name, script_content in tcl_scripts.items():
                tcl_path = device_output_dir / f"{script_name}.tcl"
                tcl_path.write_text(script_content, encoding="utf-8")
                log_debug_safe(
                    self.logger,
                    safe_format("Wrote {path}", path=tcl_path),
                    prefix="BUILD",
                )

                # Validate the generated file
                self.validator.validate_tcl_file(tcl_path, device)

            self.successful_builds += 1
            log_info_safe(
                self.logger,
                safe_format(
                    "Successfully generated TCL for {name} on {board}",
                    name=device.name,
                    board=board,
                ),
                prefix="BUILD",
            )
            return True, device_output_dir

        except Exception as e:
            self.failed_builds += 1
            log_error_safe(
                self.logger,
                safe_format(
                    "Failed to generate TCL for {name} on {board}: {error}",
                    name=device.name,
                    board=board,
                    error=str(e),
                ),
                prefix="BUILD",
            )
            return False, None

    def generate_all(self) -> bool:
        """
        Generate TCL files for all test devices and boards.

        Returns:
            True if all generations succeeded
        """
        log_info_safe(
            self.logger,
            safe_format(
                "Starting E2E TCL generation for {devices} devices on {boards} boards",
                devices=len(TEST_DEVICES),
                boards=len(self.boards),
            ),
            prefix="BUILD",
        )

        for board in self.boards:
            log_info_safe(
                self.logger,
                safe_format("Processing board: {board}", board=board),
                prefix="BUILD",
            )

            for device in TEST_DEVICES:
                self.generate_tcl_for_device(device, board)

        # Report summary
        total_builds = self.successful_builds + self.failed_builds
        success_rate = (
            (self.successful_builds / total_builds * 100) if total_builds > 0 else 0.0
        )

        log_info_safe(
            self.logger,
            safe_format(
                "Build summary: {success}/{total} successful ({rate:.1f}%)",
                success=self.successful_builds,
                total=total_builds,
                rate=success_rate,
            ),
            prefix="BUILD",
        )

        # Validation summary
        validation_passed = self.validator.report_summary()

        return self.failed_builds == 0 and validation_passed


def main() -> int:
    """Main entry point for CI TCL generation script."""
    parser = argparse.ArgumentParser(
        description="Generate all TCL files for E2E testing with test device configurations"
    )
    parser.add_argument(
        "--output",
        type=Path,
        default=Path("ci_tcl_output"),
        help="Output directory for generated TCL files (default: ci_tcl_output)",
    )
    parser.add_argument(
        "--board",
        type=str,
        help="Specific board to test (default: all supported boards)",
    )
    parser.add_argument(
        "--verbose",
        action="store_true",
        help="Enable verbose logging",
    )
    parser.add_argument(
        "--debug",
        action="store_true",
        help="Enable debug logging",
    )

    args = parser.parse_args()

    # Setup logging
    log_level = logging.DEBUG if args.debug else (logging.INFO if args.verbose else logging.WARNING)
    setup_logging(level=log_level)

    logger = get_logger("ci_tcl_generation")

    log_info_safe(
        logger,
        safe_format(
            "CI TCL Generation Script | Output: {output}",
            output=args.output,
        ),
        prefix="CI",
    )

    # Create output directory
    args.output.mkdir(parents=True, exist_ok=True)

    # Run generation
    generator = CITCLGenerator(output_dir=args.output, board=args.board)
    success = generator.generate_all()

    if success:
        log_info_safe(
            logger,
            "All TCL generation and validation passed!",
            prefix="CI",
        )
        return 0
    else:
        log_error_safe(
            logger,
            "Some TCL generation or validation failed!",
            prefix="CI",
        )
        return 1


if __name__ == "__main__":
    sys.exit(main())
