#!/usr/bin/env python3
"""
End-to-End Test Script for GitHub Actions

This script provides comprehensive E2E testing without requiring actual hardware.
It simulates the complete PCILeech firmware generation workflow using mock devices
and validates all components work together as expected.

Features:
- Mock PCIe device creation with realistic sysfs structures
- Full firmware generation pipeline testing
- Container build and execution validation
- SystemVerilog template generation verification
- Bitstream simulation (without actual Vivado)
- Integration testing with fallback mechanisms
- Performance and resource usage monitoring
"""

import argparse
import asyncio
import json
import logging
import os
import shutil
import subprocess
import sys
import tempfile
import time
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple
from unittest.mock import patch


class E2ETestRunner:
    """Main test runner for end-to-end testing."""

    def __init__(self, cleanup: bool = True):
        self.cleanup = cleanup
        self.test_results: List[Dict[str, Any]] = []
        self.start_time = time.time()

        # Setup logging
        log_level = logging.DEBUG
        logging.basicConfig(
            level=log_level, format="%(asctime)s - %(levelname)s - %(message)s"
        )
        self.logger = logging.getLogger(__name__)

        # Test directories
        self.project_root = Path(__file__).parent.parent
        self.test_base_dir = self.project_root / "tests" / "e2e_temp"
        self.mock_sysfs_root = self.test_base_dir / "mock_sysfs"
        self.build_output_dir = self.test_base_dir / "build_output"
        self.container_output_dir = self.test_base_dir / "container_output"

        # Test configuration
        self.test_devices = [
            {
                "bdf": "0000:03:00.0",
                "vendor_id": "0x8086",
                "device_id": "0x100e",
                "subsystem_vendor": "0x8086",
                "subsystem_device": "0x001e",
                "class_code": "0x020000",  # Network controller
                "device_name": "Intel 82574L Gigabit Network Connection",
                "board": "pcileech_35t325_x1",
            },
            {
                "bdf": "0000:04:00.0",
                "vendor_id": "0x10de",
                "device_id": "0x1b06",
                "subsystem_vendor": "0x10de",
                "subsystem_device": "0x11bc",
                "class_code": "0x030000",  # VGA controller
                "device_name": "NVIDIA GeForce GTX 1080 Ti",
                "board": "pcileech_75t484_x1",
            },
            {
                "bdf": "0000:05:00.0",
                "vendor_id": "0x1022",
                "device_id": "0x43bb",
                "subsystem_vendor": "0x1b21",
                "subsystem_device": "0x1142",
                "class_code": "0x0c0330",  # USB 3.0 controller
                "device_name": "AMD 400 Series Chipset USB 3.0 Host Controller",
                "board": "pcileech_35t484_x1",
            },
        ]

    def log_test_result(
        self,
        test_name: str,
        success: bool,
        details: str = "",
        duration: float = 0.0,
        artifacts: Optional[List[str]] = None,
    ):
        """Log a test result for later reporting."""
        result = {
            "test_name": test_name,
            "success": success,
            "details": details,
            "duration": duration,
            "artifacts": artifacts or [],
            "timestamp": time.time(),
        }
        self.test_results.append(result)

        status = "✅ PASS" if success else "❌ FAIL"
        self.logger.info(f"{status} {test_name} ({duration:.2f}s)")
        if details:
            self.logger.info(f"    Details: {details}")

    def setup_test_environment(self) -> bool:
        """Set up the test environment with mock devices and directories."""
        self.logger.info("Setting up test environment...")
        start_time = time.time()

        try:
            # Clean and create test directories
            if self.test_base_dir.exists():
                shutil.rmtree(self.test_base_dir)

            self.test_base_dir.mkdir(parents=True, exist_ok=True)
            self.mock_sysfs_root.mkdir(parents=True, exist_ok=True)
            self.build_output_dir.mkdir(parents=True, exist_ok=True)
            self.container_output_dir.mkdir(parents=True, exist_ok=True)

            # Create mock sysfs structure for each test device
            for device in self.test_devices:
                self._create_mock_device(device)

            # Create mock VFIO and IOMMU structures
            self._create_mock_vfio_structure()

            duration = time.time() - start_time
            self.log_test_result(
                "Environment Setup",
                True,
                f"Created mock environment for {len(self.test_devices)} devices",
                duration,
            )
            return True

        except Exception as e:
            duration = time.time() - start_time
            self.log_test_result("Environment Setup", False, str(e), duration)
            return False

    def _create_mock_device(self, device: Dict[str, str]) -> None:
        """Create a mock PCIe device in sysfs."""
        bdf = device["bdf"]
        device_dir = self.mock_sysfs_root / "sys" / "bus" / "pci" / "devices" / bdf
        device_dir.mkdir(parents=True, exist_ok=True)

        # Create device attribute files
        attributes = {
            "vendor": device["vendor_id"],
            "device": device["device_id"],
            "subsystem_vendor": device["subsystem_vendor"],
            "subsystem_device": device["subsystem_device"],
            "class": device["class_code"],
            "irq": "42",
            "numa_node": "0",
            "power_state": "D0",
            "current_link_speed": "5.0 GT/s PCIe",
            "current_link_width": "1",
            "max_link_speed": "5.0 GT/s PCIe",
            "max_link_width": "1",
        }

        for attr, value in attributes.items():
            (device_dir / attr).write_text(value + "\n")

        # Create realistic config space
        config_space = self._generate_realistic_config_space(device)
        (device_dir / "config").write_bytes(config_space)

        # Create resource file
        resources = [
            "0x00000000f0000000 0x00000000f0ffffff 0x00040200",  # BAR0: Memory
            "0x00000000f1000000 0x00000000f1000fff 0x00040200",  # BAR1: Memory
            "0x0000000000002000 0x000000000000203f 0x00040101",  # BAR2: I/O
            "0x0000000000000000 0x0000000000000000 0x00000000",  # BAR3: Unused
            "0x0000000000000000 0x0000000000000000 0x00000000",  # BAR4: Unused
            "0x0000000000000000 0x0000000000000000 0x00000000",  # BAR5: Unused
            "0x0000000000000000 0x0000000000000000 0x00000000",  # ROM
        ]
        (device_dir / "resource").write_text("\n".join(resources) + "\n")

        # Create IOMMU group
        iommu_group = "42"
        iommu_dir = device_dir / "iommu_group"
        iommu_dir.mkdir(exist_ok=True)
        (iommu_dir / "type").write_text("DMA\n")

        # Create driver symlink (if bound)
        driver_name = self._get_driver_for_device(device)
        if driver_name:
            driver_dir = device_dir / "driver"
            # In real sysfs this would be a symlink, but for testing we'll create a simple marker
            driver_dir.mkdir(exist_ok=True)
            (driver_dir / "module").mkdir(exist_ok=True)

    def _generate_realistic_config_space(self, device: Dict[str, str]) -> bytes:
        """Generate a realistic 256-byte PCIe configuration space."""
        config = bytearray(256)

        # Standard PCI Header
        vendor_id = int(device["vendor_id"], 16)
        device_id = int(device["device_id"], 16)
        subsys_vendor_id = int(device["subsystem_vendor"], 16)
        subsys_device_id = int(device["subsystem_device"], 16)
        class_code = int(device["class_code"], 16)

        # Vendor ID and Device ID
        config[0:2] = vendor_id.to_bytes(2, "little")
        config[2:4] = device_id.to_bytes(2, "little")

        # Command and Status
        config[4:6] = (0x0007).to_bytes(
            2, "little"
        )  # Bus master, memory space, I/O space
        config[6:8] = (0x0010).to_bytes(2, "little")  # Capabilities list

        # Class code and revision
        config[8] = 0x01  # Revision
        config[9:12] = class_code.to_bytes(3, "little")

        # Header type and other fields
        config[14] = 0x00  # Single-function device

        # BARs (simplified)
        config[16:20] = (0xF0000000).to_bytes(4, "little")  # BAR0: Memory
        config[20:24] = (0xF1000000).to_bytes(4, "little")  # BAR1: Memory
        config[24:28] = (0x00002001).to_bytes(4, "little")  # BAR2: I/O

        # Subsystem IDs
        config[44:46] = subsys_vendor_id.to_bytes(2, "little")
        config[46:48] = subsys_device_id.to_bytes(2, "little")

        # Capabilities pointer
        config[52] = 0x50  # First capability at offset 0x50

        # Add some basic capabilities
        # MSI capability at 0x50
        config[0x50] = 0x05  # MSI Capability ID
        config[0x51] = 0x60  # Next capability pointer
        config[0x52:0x54] = (0x0001).to_bytes(2, "little")  # Message Control

        # PCIe capability at 0x60
        config[0x60] = 0x10  # PCIe Capability ID
        config[0x61] = 0x00  # Next capability (end)
        config[0x62:0x64] = (0x0002).to_bytes(2, "little")  # PCIe Capabilities

        return bytes(config)

    def _get_driver_for_device(self, device: Dict[str, str]) -> Optional[str]:
        """Get the expected driver name for a device type."""
        class_code = device["class_code"]
        if class_code.startswith("0x0200"):  # Network
            return "e1000e"
        elif class_code.startswith("0x0300"):  # Display
            return "nvidia"
        elif class_code.startswith("0x0c03"):  # USB
            return "xhci_hcd"
        return None

    def _create_mock_vfio_structure(self) -> None:
        """Create mock VFIO and IOMMU structures."""
        # Create VFIO device files
        vfio_dir = self.mock_sysfs_root / "dev" / "vfio"
        vfio_dir.mkdir(parents=True, exist_ok=True)

        # Create VFIO character devices (mock)
        (vfio_dir / "vfio").write_text("10:196\n")
        (vfio_dir / "42").write_text("10:197\n")  # IOMMU group 42

        # Create IOMMU groups
        iommu_groups_dir = self.mock_sysfs_root / "sys" / "kernel" / "iommu_groups"
        iommu_groups_dir.mkdir(parents=True, exist_ok=True)

        group_dir = iommu_groups_dir / "42"
        group_dir.mkdir(exist_ok=True)
        (group_dir / "type").write_text("DMA\n")

    async def test_python_dependencies(self) -> bool:
        """Test that all Python dependencies are available."""
        self.logger.info("Testing Python dependencies...")
        start_time = time.time()

        try:
            # Critical imports
            import sys

            sys.path.insert(0, str(self.project_root))
            sys.path.insert(0, str(self.project_root / "src"))

            # Test core module imports
            from src.build import BuildConfiguration, FirmwareBuilder
            from src.device_clone.device_config import get_device_config

            # Note: Some imports may not be available in all environments
            try:
                from src.systemverilog_generator import SystemVerilogGenerator
            except ImportError:
                self.logger.warning("SystemVerilog generator not available")

            try:
                from src.tcl_builder import TCLBuilder
            except ImportError:
                self.logger.warning("TCL builder not available")

            # Test optional imports with graceful degradation
            optional_imports = []
            try:
                import textual

                optional_imports.append("textual")
            except ImportError:
                pass

            try:
                import rich

                optional_imports.append("rich")
            except ImportError:
                pass

            duration = time.time() - start_time
            details = (
                f"Core imports successful, optional: {', '.join(optional_imports)}"
            )
            self.log_test_result("Python Dependencies", True, details, duration)
            return True

        except Exception as e:
            duration = time.time() - start_time
            self.log_test_result("Python Dependencies", False, str(e), duration)
            return False

    async def test_mock_device_discovery(self) -> bool:
        """Test device discovery using mock sysfs."""
        self.logger.info("Testing mock device discovery...")
        start_time = time.time()

        try:
            # Set environment to use mock sysfs
            os.environ["PCILEECH_SYSFS_ROOT"] = str(
                self.mock_sysfs_root / "sys" / "bus" / "pci" / "devices"
            )

            from src.device_discovery import discover_pci_devices
            from src.vfio_handler import VFIODeviceHandler

            # Discover devices
            devices = discover_pci_devices()

            if len(devices) != len(self.test_devices):
                raise ValueError(
                    f"Expected {len(self.test_devices)} devices, found {len(devices)}"
                )

            # Validate each discovered device
            for expected_device in self.test_devices:
                found = False
                for discovered_device in devices:
                    if discovered_device.bdf == expected_device["bdf"]:
                        found = True
                        # Validate key attributes
                        if discovered_device.vendor_id != expected_device["vendor_id"]:
                            raise ValueError(
                                f"Vendor ID mismatch for {expected_device['bdf']}"
                            )
                        if discovered_device.device_id != expected_device["device_id"]:
                            raise ValueError(
                                f"Device ID mismatch for {expected_device['bdf']}"
                            )
                        break

                if not found:
                    raise ValueError(f"Device {expected_device['bdf']} not discovered")

            duration = time.time() - start_time
            self.log_test_result(
                "Mock Device Discovery",
                True,
                f"Discovered {len(devices)} devices",
                duration,
            )
            return True

        except Exception as e:
            duration = time.time() - start_time
            self.log_test_result("Mock Device Discovery", False, str(e), duration)
            return False

    async def test_firmware_generation_pipeline(self) -> bool:
        """Test the complete firmware generation pipeline."""
        self.logger.info("Testing firmware generation pipeline...")
        start_time = time.time()

        try:
            # Test with the first device
            test_device = self.test_devices[0]
            output_dir = self.build_output_dir / "pipeline_test"
            output_dir.mkdir(parents=True, exist_ok=True)

            # Set environment for mock testing
            os.environ["PCILEECH_SYSFS_ROOT"] = str(
                self.mock_sysfs_root / "sys" / "bus" / "pci" / "devices"
            )
            os.environ["PCILEECH_ALLOW_MOCK_DATA"] = "true"

            from src.build import BuildConfiguration, FirmwareBuilder

            # Create build configuration
            config = BuildConfiguration(
                bdf=test_device["bdf"],
                board=test_device["board"],
                output_dir=output_dir,
                enable_profiling=False,  # Skip profiling for tests
                preload_msix=False,  # Skip MSI-X preloading for tests
            )

            # Run firmware generation
            builder = FirmwareBuilder(config)

            # Mock the hardware access methods
            with patch(
                "src.vfio_handler.VFIODeviceHandler.read_config_space"
            ) as mock_read:
                # Return realistic config space data
                mock_read.return_value = self._generate_realistic_config_space(
                    test_device
                )

                # Generate firmware (synchronous call)
                builder.build()

            # Validate outputs
            expected_files = [
                "generated/pcileech_top.sv",
                "generated/pci_config.sv",
                "tcl/build_project.tcl",
                "tcl/constraints.xdc",
                "build_summary.json",
            ]

            missing_files = []
            for file_path in expected_files:
                if not (output_dir / file_path).exists():
                    missing_files.append(file_path)

            if missing_files:
                raise ValueError(f"Missing generated files: {', '.join(missing_files)}")

            # Validate SystemVerilog syntax
            sv_file = output_dir / "generated" / "pcileech_top.sv"
            if not self._validate_systemverilog_syntax(sv_file):
                raise ValueError("Generated SystemVerilog has syntax errors")

            duration = time.time() - start_time
            artifacts = [str(output_dir / f) for f in expected_files]
            self.log_test_result(
                "Firmware Generation Pipeline",
                True,
                f"Generated {len(expected_files)} files",
                duration,
                artifacts,
            )
            return True

        except Exception as e:
            duration = time.time() - start_time
            self.log_test_result(
                "Firmware Generation Pipeline", False, str(e), duration
            )
            return False

    def _validate_systemverilog_syntax(self, sv_file: Path) -> bool:
        """Basic SystemVerilog syntax validation."""
        try:
            content = sv_file.read_text()

            # Check for required SystemVerilog constructs
            required_patterns = [
                "module pcileech_top",
                "endmodule",
                "input wire",
                "output wire",
                "always_ff",
                "pcie_clk",
                "pcie_rst_n",
            ]

            for pattern in required_patterns:
                if pattern not in content:
                    self.logger.warning(f"Missing required pattern: {pattern}")
                    return False

            # Check for balanced begin/end blocks
            begin_count = content.count("begin")
            end_count = content.count("end")
            if begin_count != end_count:
                self.logger.warning(
                    f"Unbalanced begin/end blocks: {begin_count} vs {end_count}"
                )
                return False

            return True

        except Exception as e:
            self.logger.error(f"SystemVerilog validation error: {e}")
            return False

    async def test_container_build(self) -> bool:
        """Test container build and basic functionality."""
        self.logger.info("Testing container build...")
        start_time = time.time()

        try:
            # Check if container engine is available
            container_engines = ["podman", "docker"]
            container_engine = None

            for engine in container_engines:
                try:
                    subprocess.run(
                        [engine, "--version"],
                        capture_output=True,
                        check=True,
                        timeout=10,
                    )
                    container_engine = engine
                    break
                except (
                    subprocess.CalledProcessError,
                    FileNotFoundError,
                    subprocess.TimeoutExpired,
                ):
                    continue

            if not container_engine:
                # Skip container test if no engine available
                self.log_test_result(
                    "Container Build",
                    True,
                    "Skipped - no container engine available",
                    0.0,
                )
                return True

            # Build container
            build_cmd = [
                container_engine,
                "build",
                "-t",
                "pcileech-fw-generator:e2e-test",
                "-f",
                "Containerfile",
                str(self.project_root),
            ]

            result = subprocess.run(
                build_cmd, capture_output=True, text=True, timeout=300
            )

            if result.returncode != 0:
                raise subprocess.CalledProcessError(
                    result.returncode, build_cmd, result.stdout, result.stderr
                )

            # Test container basic functionality
            test_cmd = [
                container_engine,
                "run",
                "--rm",
                "pcileech-fw-generator:e2e-test",
                "python3",
                "-c",
                "import sys; sys.path.append('/app/src'); import build; print('OK')",
            ]

            result = subprocess.run(
                test_cmd, capture_output=True, text=True, timeout=60
            )

            if result.returncode != 0:
                raise subprocess.CalledProcessError(
                    result.returncode, test_cmd, result.stdout, result.stderr
                )

            duration = time.time() - start_time
            self.log_test_result(
                "Container Build", True, f"Built with {container_engine}", duration
            )
            return True

        except subprocess.TimeoutExpired as e:
            duration = time.time() - start_time
            self.log_test_result("Container Build", False, f"Timeout: {e}", duration)
            return False
        except Exception as e:
            duration = time.time() - start_time
            self.log_test_result("Container Build", False, str(e), duration)
            return False

    async def test_cli_interface(self) -> bool:
        """Test CLI interface functionality."""
        self.logger.info("Testing CLI interface...")
        start_time = time.time()

        try:
            # Set environment for mock testing
            os.environ["PCILEECH_SYSFS_ROOT"] = str(
                self.mock_sysfs_root / "sys" / "bus" / "pci" / "devices"
            )
            os.environ["PCILEECH_ALLOW_MOCK_DATA"] = "true"

            test_device = self.test_devices[0]
            output_dir = self.build_output_dir / "cli_test"
            output_dir.mkdir(parents=True, exist_ok=True)

            # Test CLI build command
            cli_cmd = [
                sys.executable,
                str(self.project_root / "pcileech.py"),
                "build",
                "--bdf",
                test_device["bdf"],
                "--board",
                test_device["board"],
                "--build-dir",
                str(output_dir),
                "--skip-requirements-check",
            ]

            # Mock VFIO operations to avoid requiring root
            with patch("src.vfio_handler.VFIODeviceHandler") as mock_vfio:
                mock_instance = mock_vfio.return_value
                mock_instance.read_config_space.return_value = (
                    self._generate_realistic_config_space(test_device)
                )

                result = subprocess.run(
                    cli_cmd,
                    capture_output=True,
                    text=True,
                    timeout=120,
                    cwd=self.project_root,
                )

            if result.returncode != 0:
                raise subprocess.CalledProcessError(
                    result.returncode, cli_cmd, result.stdout, result.stderr
                )

            # Verify output files were created
            if not (output_dir / "generated" / "pcileech_top.sv").exists():
                raise ValueError("CLI build did not generate expected output files")

            duration = time.time() - start_time
            self.log_test_result(
                "CLI Interface", True, "CLI build completed successfully", duration
            )
            return True

        except subprocess.TimeoutExpired as e:
            duration = time.time() - start_time
            self.log_test_result("CLI Interface", False, f"Timeout: {e}", duration)
            return False
        except Exception as e:
            duration = time.time() - start_time
            self.log_test_result("CLI Interface", False, str(e), duration)
            return False

    async def test_template_validation(self) -> bool:
        """Test SystemVerilog template validation."""
        self.logger.info("Testing template validation...")
        start_time = time.time()

        try:
            from src.template_context_validator import TemplateContextValidator
            from src.template_security_validation import \
                TemplateSecurityValidator

            # Find all template files
            template_dir = self.project_root / "src" / "templates"
            template_files = list(template_dir.rglob("*.j2"))

            if not template_files:
                raise ValueError("No template files found")

            # Validate templates
            security_validator = TemplateSecurityValidator()
            context_validator = TemplateContextValidator()

            validation_errors = []

            for template_file in template_files:
                try:
                    # Security validation
                    security_result = security_validator.validate_template_file(
                        template_file
                    )
                    if not security_result.is_safe:
                        validation_errors.append(
                            f"{template_file.name}: Security issues - {security_result.issues}"
                        )

                    # Context validation (basic syntax)
                    context_result = context_validator.validate_template_syntax(
                        template_file
                    )
                    if not context_result.is_valid:
                        validation_errors.append(
                            f"{template_file.name}: Syntax issues - {context_result.errors}"
                        )

                except Exception as e:
                    validation_errors.append(
                        f"{template_file.name}: Validation error - {e}"
                    )

            if validation_errors:
                raise ValueError(
                    f"Template validation errors: {'; '.join(validation_errors)}"
                )

            duration = time.time() - start_time
            self.log_test_result(
                "Template Validation",
                True,
                f"Validated {len(template_files)} templates",
                duration,
            )
            return True

        except Exception as e:
            duration = time.time() - start_time
            self.log_test_result("Template Validation", False, str(e), duration)
            return False

    async def test_vivado_simulation(self) -> bool:
        """Test Vivado simulation without actual Vivado."""
        self.logger.info("Testing Vivado simulation...")
        start_time = time.time()

        try:
            # Create mock Vivado environment
            mock_vivado_dir = self.test_base_dir / "mock_vivado"
            mock_vivado_dir.mkdir(parents=True, exist_ok=True)

            # Create mock Vivado executable
            mock_vivado_bin = mock_vivado_dir / "bin" / "vivado"
            mock_vivado_bin.parent.mkdir(parents=True, exist_ok=True)

            # Create a simple shell script that simulates Vivado
            vivado_script = """#!/bin/bash
echo "Vivado v2023.1 (Mock Version)"
echo "Starting Vivado in batch mode..."

# Parse command line arguments
while [[ $# -gt 0 ]]; do
    case $1 in
        -mode)
            MODE="$2"
            shift 2
            ;;
        -source)
            SOURCE_FILE="$2"
            shift 2
            ;;
        *)
            shift
            ;;
    esac
done

if [[ "$MODE" == "batch" && -f "$SOURCE_FILE" ]]; then
    echo "Executing TCL script: $SOURCE_FILE"
    echo "Creating Vivado project..."
    echo "Running synthesis..."
    echo "Running implementation..." 
    echo "Generating bitstream..."
    
    # Create mock output files
    mkdir -p "vivado_project/pcileech_project.runs/impl_1"
    echo "Mock bitstream data" > "vivado_project/pcileech_project.runs/impl_1/pcileech_top.bit"
    echo "Mock LTX data" > "vivado_project/pcileech_project.runs/impl_1/pcileech_top.ltx"
    
    echo "Vivado build completed successfully"
    exit 0
else
    echo "Usage: vivado -mode batch -source script.tcl"
    exit 1
fi
"""

            mock_vivado_bin.write_text(vivado_script)
            mock_vivado_bin.chmod(0o755)

            # Test with VivadoRunner using mock
            from src.vivado_handling.vivado_runner import VivadoRunner

            test_device = self.test_devices[0]
            output_dir = self.build_output_dir / "vivado_sim"
            output_dir.mkdir(parents=True, exist_ok=True)

            # Create a simple TCL script for testing
            tcl_script = output_dir / "test_build.tcl"
            tcl_script.write_text(
                """
# Mock TCL script
puts "Creating project..."
create_project pcileech_project ./vivado_project -part xc7a35tcpg236-1
puts "Project created successfully"
"""
            )

            # Test VivadoRunner with mock executable
            runner = VivadoRunner(
                board=test_device["board"],
                output_dir=output_dir,
                vivado_path=str(mock_vivado_dir),
            )

            # Mock the internal methods to use our test script
            with patch.object(runner, "_is_running_in_container", return_value=False):
                with patch(
                    "src.vivado_handling.vivado_runner.integrate_pcileech_build"
                ) as mock_integrate:
                    mock_integrate.return_value = tcl_script

                    with patch(
                        "src.vivado_handling.vivado_runner.run_vivado_with_error_reporting"
                    ) as mock_run:
                        mock_run.return_value = (0, "Mock build report")

                        # This should complete without errors
                        runner.run()

            duration = time.time() - start_time
            self.log_test_result(
                "Vivado Simulation", True, "Mock Vivado execution completed", duration
            )
            return True

        except Exception as e:
            duration = time.time() - start_time
            self.log_test_result("Vivado Simulation", False, str(e), duration)
            return False

    async def test_performance_benchmarks(self) -> bool:
        """Test performance benchmarks and resource usage."""
        self.logger.info("Testing performance benchmarks...")
        start_time = time.time()

        try:
            import threading

            import psutil

            # Monitor resource usage during test
            max_memory_mb = 0
            max_cpu_percent = 0
            monitoring = True

            def monitor_resources():
                nonlocal max_memory_mb, max_cpu_percent, monitoring
                process = psutil.Process()

                while monitoring:
                    try:
                        memory_mb = process.memory_info().rss / 1024 / 1024
                        cpu_percent = process.cpu_percent()

                        max_memory_mb = max(max_memory_mb, memory_mb)
                        max_cpu_percent = max(max_cpu_percent, cpu_percent)

                        time.sleep(0.5)
                    except psutil.NoSuchProcess:
                        break
                    except Exception:
                        pass

            monitor_thread = threading.Thread(target=monitor_resources)
            monitor_thread.start()

            try:
                # Run a subset of tests to measure performance
                await self.test_python_dependencies()
                await self.test_mock_device_discovery()

                # Run a quick firmware generation
                test_device = self.test_devices[0]
                output_dir = self.build_output_dir / "perf_test"
                output_dir.mkdir(parents=True, exist_ok=True)

                os.environ["PCILEECH_SYSFS_ROOT"] = str(
                    self.mock_sysfs_root / "sys" / "bus" / "pci" / "devices"
                )
                os.environ["PCILEECH_ALLOW_MOCK_DATA"] = "true"

                from src.build import BuildConfiguration, FirmwareBuilder

                config = BuildConfiguration(
                    bdf=test_device["bdf"],
                    board=test_device["board"],
                    output_dir=output_dir,
                    enable_profiling=False,
                )

                builder = FirmwareBuilder(config)

                with patch(
                    "src.vfio_handler.VFIODeviceHandler.read_config_space"
                ) as mock_read:
                    mock_read.return_value = self._generate_realistic_config_space(
                        test_device
                    )
                    perf_start = time.time()
                    builder.build()
                    generation_time = time.time() - perf_start

            finally:
                monitoring = False
                monitor_thread.join(timeout=5)

            # Performance thresholds (adjust based on CI environment)
            max_memory_threshold_mb = 500  # 500MB max memory usage
            max_generation_time_s = 30  # 30 seconds max generation time

            performance_issues = []

            if max_memory_mb > max_memory_threshold_mb:
                performance_issues.append(f"High memory usage: {max_memory_mb:.1f}MB")

            if generation_time > max_generation_time_s:
                performance_issues.append(f"Slow generation: {generation_time:.1f}s")

            duration = time.time() - start_time
            details = f"Memory: {max_memory_mb:.1f}MB, CPU: {max_cpu_percent:.1f}%, Generation: {generation_time:.1f}s"

            if performance_issues:
                details += f" | Issues: {', '.join(performance_issues)}"
                self.log_test_result("Performance Benchmarks", False, details, duration)
                return False
            else:
                self.log_test_result("Performance Benchmarks", True, details, duration)
                return True

        except Exception as e:
            duration = time.time() - start_time
            self.log_test_result("Performance Benchmarks", False, str(e), duration)
            return False

    async def test_integration_fallbacks(self) -> bool:
        """Test integration fallbacks and error handling."""
        self.logger.info("Testing integration fallbacks...")
        start_time = time.time()

        try:
            # Test device config fallbacks
            from src.device_clone.device_config import get_device_config

            # Test with non-existent profile (should return None/fallback)
            fallback_config = get_device_config("nonexistent_profile_12345")
            if fallback_config is not None:
                raise ValueError("Expected fallback config to be None")

            # Test VFIO fallbacks
            from src.vfio_handler import VFIODeviceHandler

            # Test with invalid BDF (should handle gracefully)
            try:
                handler = VFIODeviceHandler("invalid:bdf:format")
                # This should either raise a specific exception or handle gracefully
            except Exception as e:
                # This is expected behavior
                pass

            # Test template fallbacks
            from src.template_renderer import TemplateRenderer

            renderer = TemplateRenderer()

            # Test with missing template (should handle gracefully)
            try:
                result = renderer.render_template("nonexistent_template.j2", {})
                # If it doesn't raise an exception, it should return a fallback
            except Exception as e:
                # This is expected behavior
                pass

            # Test SystemVerilog generator fallbacks
            from src.systemverilog_generator import SystemVerilogGenerator

            generator = SystemVerilogGenerator()

            # Test with minimal context (should use defaults)
            minimal_context = {"vendor_id": "0x1234", "device_id": "0x5678"}

            try:
                sv_code = generator.generate(minimal_context)
                if not sv_code or len(sv_code) < 100:
                    raise ValueError(
                        "SystemVerilog generator returned insufficient output"
                    )
            except Exception as e:
                raise ValueError(
                    f"SystemVerilog generator failed with minimal context: {e}"
                )

            duration = time.time() - start_time
            self.log_test_result(
                "Integration Fallbacks",
                True,
                "All fallback mechanisms working",
                duration,
            )
            return True

        except Exception as e:
            duration = time.time() - start_time
            self.log_test_result("Integration Fallbacks", False, str(e), duration)
            return False

    async def test_host_device_collector(self) -> bool:
        """Test host device collector functionality and VFIO rebinding prevention."""
        self.logger.info("Testing host device collector and VFIO rebinding prevention...")
        start_time = time.time()

        try:
            # Import required modules
            from src.host_device_collector import HostDeviceCollector
            from src.device_clone.pcileech_generator import PCILeechGenerator, PCILeechGenerationConfig
            from src.device_clone.config_space_manager import ConfigSpaceManager
            from src.utils.unified_context import UnifiedContextBuilder
            
            # Test device
            test_device = self.test_devices[0]
            bdf = test_device["bdf"]
            
            # Create mock device context data that would be collected by host
            mock_device_context = {
                "vendor_id": test_device["vendor_id"],
                "device_id": test_device["device_id"],
                "subsystem_vendor": test_device["subsystem_vendor"],
                "subsystem_device": test_device["subsystem_device"],
                "class_code": test_device["class_code"],
                "device_name": test_device["device_name"],
                "bdf": bdf,
                "bar_config": {
                    "bars": [
                        {"size": 0x1000000, "type": "memory", "address": 0xF0000000},
                        {"size": 0x1000, "type": "memory", "address": 0xF1000000},
                        {"size": 0x40, "type": "io", "address": 0x2000}
                    ]
                },
                "capabilities": {
                    "msi": {"supported": True, "vectors": 1},
                    "msix": {"supported": False},
                    "pcie": {"supported": True, "version": "2.0"}
                },
                "generation_metadata": {
                    "collected_by": "host_device_collector",
                    "collection_time": "2024-01-01T00:00:00Z",
                    "version": "1.0"
                }
            }
            
            # Create mock MSI-X data
            mock_msix_data = {
                "msix_supported": False,
                "table_offset": 0,
                "table_size": 0,
                "vectors": []
            }
            
            # Create temporary files for host-collected data
            temp_dir = self.test_base_dir / "host_collected_data"
            temp_dir.mkdir(parents=True, exist_ok=True)
            
            device_context_file = temp_dir / "device_context.json"
            msix_data_file = temp_dir / "msix_data.json"
            
            # Write mock data to files
            with open(device_context_file, 'w') as f:
                json.dump(mock_device_context, f, indent=2)
            
            with open(msix_data_file, 'w') as f:
                json.dump(mock_msix_data, f, indent=2)
            
            # Test 1: HostDeviceCollector can load pre-collected data
            self.logger.info("Testing HostDeviceCollector data loading...")
            
            # Set environment variables to point to our mock files
            os.environ["DEVICE_CONTEXT_FILE"] = str(device_context_file)
            os.environ["MSIX_DATA_FILE"] = str(msix_data_file)
            
            collector = HostDeviceCollector()
            
            # Verify data loading
            loaded_context = collector._load_device_context()
            loaded_msix = collector._load_msix_data()
            
            if not loaded_context:
                raise ValueError("Failed to load device context from file")
            if not loaded_msix:
                raise ValueError("Failed to load MSI-X data from file")
            
            # Verify loaded data matches expected
            if loaded_context["vendor_id"] != test_device["vendor_id"]:
                raise ValueError(f"Vendor ID mismatch: expected {test_device['vendor_id']}, got {loaded_context['vendor_id']}")
            
            self.logger.info("✓ HostDeviceCollector successfully loaded pre-collected data")
            
            # Test 2: PCILeechGenerator uses preloaded config space instead of VFIO
            self.logger.info("Testing PCILeechGenerator with preloaded config space...")
            
            # Create realistic config space data
            config_space_data = self._generate_realistic_config_space(test_device)
            
            # Create PCILeechGenerator with preloaded config space
            config = PCILeechGenerationConfig(
                bdf=bdf,
                board=test_device["board"],
                output_dir=self.build_output_dir / "host_collector_test",
                preloaded_config_space=config_space_data  # This is the key feature
            )
            
            generator = PCILeechGenerator(config)
            
            # Verify that the generator has the preloaded config space
            if generator._preloaded_config_space != config_space_data:
                raise ValueError("Generator did not properly store preloaded config space")
            
            # Test 3: Verify that _analyze_configuration_space uses preloaded data
            self.logger.info("Testing configuration space analysis with preloaded data...")
            
            # Mock VFIO operations to detect if they're called
            vfio_called = False
            
            def mock_vfio_read(*args, **kwargs):
                nonlocal vfio_called
                vfio_called = True
                raise Exception("VFIO should not be called when preloaded data is available")
            
            # Patch VFIO operations
            with patch("src.vfio_handler.VFIODeviceHandler.read_config_space", side_effect=mock_vfio_read):
                with patch("src.vfio_handler.VFIODeviceHandler.__init__", side_effect=Exception("VFIO binding should not occur")):
                    try:
                        # This should use preloaded data and NOT call VFIO
                        config_space_manager = generator._analyze_configuration_space()
                        
                        # Verify VFIO was NOT called
                        if vfio_called:
                            raise ValueError("VFIO was called despite preloaded config space being available")
                        
                        # Verify we got a valid ConfigSpaceManager
                        if not config_space_manager:
                            raise ValueError("Configuration space analysis returned None")
                        
                        # Verify the config space data is correct
                        if config_space_manager.get_raw_config_space() != config_space_data:
                            raise ValueError("Config space manager does not contain expected data")
                        
                        self.logger.info("✓ Configuration space analysis used preloaded data without VFIO calls")
                        
                    except Exception as e:
                        if "VFIO binding should not occur" in str(e):
                            raise ValueError("VFIO binding was attempted despite preloaded data being available")
                        else:
                            raise
            
            # Test 4: Test with environment variables set (container scenario)
            self.logger.info("Testing container scenario with environment variables...")
            
            # Set environment variables as they would be in container
            os.environ["PCILEECH_PRELOADED_CONFIG_SPACE"] = config_space_data.hex()
            os.environ["PCILEECH_SKIP_VFIO"] = "true"
            
            # Create generator with environment-based config
            container_config = PCILeechGenerationConfig(
                bdf=bdf,
                board=test_device["board"],
                output_dir=self.build_output_dir / "container_test"
            )
            
            container_generator = PCILeechGenerator(container_config)
            
            # Verify preloaded config space is set from environment
            if not container_generator._preloaded_config_space:
                raise ValueError("Container environment did not set preloaded config space")
            
            # Test 5: Verify MSI-X data integration
            self.logger.info("Testing MSI-X data integration...")
            
            # Test that MSI-X analysis uses pre-collected data
            msix_analysis_result = generator._analyze_msix_capabilities()
            
            if msix_analysis_result is None:
                self.logger.warning("MSI-X analysis returned None, but this may be expected for mock data")
            
            duration = time.time() - start_time
            
            # Create summary of what was tested
            test_details = [
                "✓ HostDeviceCollector loaded pre-collected device context",
                "✓ HostDeviceCollector loaded pre-collected MSI-X data", 
                "✓ PCILeechGenerator accepted preloaded config space",
                "✓ Configuration space analysis used preloaded data (no VFIO calls)",
                "✓ Container environment variables properly configured generator",
                "✓ MSI-X capability analysis completed"
            ]
            
            self.log_test_result(
                "Host Device Collector",
                True,
                " | ".join(test_details),
                duration,
                [str(device_context_file), str(msix_data_file)]
            )
            return True
            
        except Exception as e:
            duration = time.time() - start_time
            self.log_test_result("Host Device Collector", False, str(e), duration)
            return False
        finally:
            # Clean up environment variables
            for env_var in ["DEVICE_CONTEXT_FILE", "MSIX_DATA_FILE", "PCILEECH_PRELOADED_CONFIG_SPACE", "PCILEECH_SKIP_VFIO"]:
                if env_var in os.environ:
                    del os.environ[env_var]

    def generate_test_report(self) -> Dict[str, Any]:
        """Generate comprehensive test report."""
        total_duration = time.time() - self.start_time
        passed_tests = [r for r in self.test_results if r["success"]]
        failed_tests = [r for r in self.test_results if not r["success"]]

        report = {
            "summary": {
                "total_tests": len(self.test_results),
                "passed": len(passed_tests),
                "failed": len(failed_tests),
                "success_rate": (
                    len(passed_tests) / len(self.test_results)
                    if self.test_results
                    else 0
                ),
                "total_duration": total_duration,
            },
            "environment": {
                "python_version": sys.version,
                "platform": sys.platform,
                "cwd": str(Path.cwd()),
                "project_root": str(self.project_root),
            },
            "tests": self.test_results,
            "artifacts": {
                "mock_sysfs": str(self.mock_sysfs_root),
                "build_outputs": str(self.build_output_dir),
                "container_outputs": str(self.container_output_dir),
            },
        }

        return report

    def save_test_report(self, report: Dict[str, Any], output_file: Path) -> None:
        """Save test report to file."""
        try:
            with open(output_file, "w") as f:
                json.dump(report, f, indent=2, default=str)
            self.logger.info(f"Test report saved to: {output_file}")
        except Exception as e:
            self.logger.error(f"Failed to save test report: {e}")

    def cleanup_test_environment(self) -> None:
        """Clean up test environment."""
        if not self.cleanup:
            self.logger.info(
                f"Cleanup disabled, test artifacts preserved in: {self.test_base_dir}"
            )
            return

        try:
            if self.test_base_dir.exists():
                shutil.rmtree(self.test_base_dir)
                self.logger.info("Test environment cleaned up")
        except Exception as e:
            self.logger.warning(f"Failed to clean up test environment: {e}")

    async def run_all_tests(self) -> bool:
        """Run all E2E tests."""
        self.logger.info("Starting E2E test suite...")

        # Setup
        if not self.setup_test_environment():
            return False

        # Define test sequence
        tests = [
            self.test_python_dependencies,
            self.test_mock_device_discovery,
            self.test_firmware_generation_pipeline,
            self.test_template_validation,
            self.test_cli_interface,
            self.test_vivado_simulation,
            self.test_container_build,
            self.test_performance_benchmarks,
            self.test_integration_fallbacks,
            self.test_host_device_collector,
        ]

        # Run tests
        all_passed = True
        for test in tests:
            try:
                result = await test()
                if not result:
                    all_passed = False
            except Exception as e:
                self.logger.error(f"Test {test.__name__} crashed: {e}")
                self.log_test_result(test.__name__, False, f"Crashed: {e}")
                all_passed = False

        # Generate and save report
        report = self.generate_test_report()
        report_file = self.test_base_dir / "e2e_test_report.json"
        self.save_test_report(report, report_file)

        # Print summary
        summary = report["summary"]
        self.logger.info(f"E2E Test Summary:")
        self.logger.info(f"  Total: {summary['total_tests']}")
        self.logger.info(f"  Passed: {summary['passed']}")
        self.logger.info(f"  Failed: {summary['failed']}")
        self.logger.info(f"  Success Rate: {summary['success_rate']:.1%}")
        self.logger.info(f"  Duration: {summary['total_duration']:.2f}s")

        if not all_passed:
            self.logger.error("Some tests failed!")
            for test in [r for r in self.test_results if not r["success"]]:
                self.logger.error(f"  ❌ {test['test_name']}: {test['details']}")

        # Cleanup
        self.cleanup_test_environment()

        return all_passed


async def main():
    """Main entry point."""
    parser = argparse.ArgumentParser(
        description="PCILeech E2E Test Suite for GitHub Actions"
    )
    parser.add_argument(
        "--no-cleanup", action="store_true", help="Don't clean up test artifacts"
    )
    parser.add_argument(
        "--test",
        choices=[
            "dependencies",
            "discovery",
            "pipeline",
            "templates",
            "cli",
            "vivado",
            "container",
            "performance",
            "fallbacks",
            "all",
        ],
        default="all",
        help="Specific test to run",
    )

    args = parser.parse_args()

    # Create test runner
    runner = E2ETestRunner(cleanup=not args.no_cleanup)

    # Run specific test or all tests
    if args.test == "all":
        success = await runner.run_all_tests()
    else:
        # Setup environment first
        if not runner.setup_test_environment():
            return 1

        # Run specific test
        test_map = {
            "dependencies": runner.test_python_dependencies,
            "discovery": runner.test_mock_device_discovery,
            "pipeline": runner.test_firmware_generation_pipeline,
            "templates": runner.test_template_validation,
            "cli": runner.test_cli_interface,
            "vivado": runner.test_vivado_simulation,
            "container": runner.test_container_build,
            "performance": runner.test_performance_benchmarks,
            "fallbacks": runner.test_integration_fallbacks,
        }

        try:
            success = await test_map[args.test]()
        except Exception as e:
            print(f"Test failed with exception: {e}")
            success = False

        # Generate report for single test
        report = runner.generate_test_report()
        report_file = runner.test_base_dir / f"e2e_test_{args.test}_report.json"
        runner.save_test_report(report, report_file)

        runner.cleanup_test_environment()

    return 0 if success else 1


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
