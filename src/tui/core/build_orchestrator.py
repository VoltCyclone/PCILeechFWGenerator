"""
Build Orchestrator

Orchestrates the build process with real-time monitoring and progress tracking.
"""

import asyncio
import datetime
import logging
import os
import shutil
import subprocess
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from typing import Any, Callable, Dict, List, Optional, Tuple, Union

import psutil

from src.error_utils import (extract_root_cause, format_user_friendly_error,
                             log_error_with_root_cause)
from src.string_utils import (log_error_safe, log_info_safe, log_warning_safe,
                              safe_format)

from ..models.config import BuildConfiguration
from ..models.device import PCIDevice
from ..models.progress import BuildProgress, BuildStage, ValidationResult

# Constants
PCILEECH_FPGA_REPO = "https://github.com/VoltCyclone/voltcyclone-fpga.git"
RESOURCE_MONITOR_INTERVAL = 1.0  # Seconds between resource usage updates
PROCESS_TERMINATION_TIMEOUT = 2.0  # Seconds to wait for process termination

# Progress parsing tokens for easier maintenance
LOG_PROGRESS_TOKENS = {
    "Running synthesis": (
        BuildStage.VIVADO_SYNTHESIS,
        25,
        "Running synthesis",
    ),
    "Running implementation": (
        BuildStage.VIVADO_SYNTHESIS,
        50,
        "Running implementation",
    ),
    "Generating bitstream": (
        BuildStage.VIVADO_SYNTHESIS,
        75,
        "Generating bitstream",
    ),
}

logger = logging.getLogger(__name__)

# Import RepoManager for submodule access
try:
    from ...file_management.repo_manager import RepoManager
except ImportError:
    RepoManager = None


class BuildOrchestrator:
    """
    Orchestrates the build process with real-time monitoring and progress tracking.

    This class manages the entire build pipeline from environment validation to
    bitstream generation, with progress reporting and resource monitoring.
    """

    def __init__(self):
        """Initialize the build orchestrator with default state."""
        self._current_progress: Optional[BuildProgress] = None
        self._build_process: Optional[asyncio.subprocess.Process] = None
        self._progress_callback: Optional[Callable[[BuildProgress], None]] = None
        self._is_building = False
        self._should_cancel = False
        self._executor = ThreadPoolExecutor(max_workers=4)
        self._last_resource_update = 0

    # -----------------------------
    # Internal helpers (errors/logs)
    # -----------------------------

    def _add_progress_error(self, message: str, **kwargs: Any) -> None:
        """Add a formatted error message to progress if available."""
        if self._current_progress:
            self._current_progress.add_error(safe_format(message, **kwargs))

    def _add_progress_warning(self, message: str, **kwargs: Any) -> None:
        """Add a formatted warning message to progress if available."""
        if self._current_progress:
            self._current_progress.add_warning(safe_format(message, **kwargs))

    def _report_exception(
        self,
        prefix: str,
        exc: BaseException,
        *,
        platform_hint: bool = False,
    ) -> None:
        """
        Record exception with root cause and optionally convert to a
        platform-compatibility warning.
        """
        error_str = str(exc)
        exc_for_root = exc if isinstance(exc, Exception) else Exception(error_str)
        root_cause = extract_root_cause(exc_for_root)

        # Platform-compatibility special-casing
        if platform_hint and (
            ("requires Linux" in error_str)
            or ("platform incompatibility" in error_str)
            or ("only available on Linux" in error_str)
        ):
            # Progress warning
            self._add_progress_warning("{prefix}: {msg}", prefix=prefix, msg=error_str)
            # Log as WARNING with safe formatter
            log_warning_safe(
                logger,
                "{pfx}: {rc}",
                pfx=safe_format(prefix),
                rc=root_cause,
            )
            return

        # Progress error with a concise, user-friendly message
        try:
            friendly = format_user_friendly_error(exc_for_root, context=prefix)
            self._add_progress_error("{msg}", msg=friendly)
        except Exception:
            # Fallback to simple prefix + message if formatting failed
            self._add_progress_error("{prefix}: {msg}", prefix=prefix, msg=error_str)

        # Log error with extracted root cause (concise by default)
        log_error_with_root_cause(
            logger,
            safe_format("{pfx} failed", pfx=prefix),
            exc_for_root,
            show_full_traceback=False,
        )

    async def start_build(
        self,
        device: PCIDevice,
        config: BuildConfiguration,
        progress_callback: Optional[Callable[[BuildProgress], None]] = None,
    ) -> bool:
        """
        Start the build process with progress monitoring.

        Args:
            device: The PCI device to build for
            config: Build configuration parameters
            progress_callback: Optional callback for progress updates

        Returns:
            bool: True if build completed successfully

        Raises:
            RuntimeError: If a build is already in progress or other errors occur
        """
        if self._is_building:
            raise RuntimeError("Build already in progress")

        self._is_building = True
        self._should_cancel = False
        self._progress_callback = progress_callback

        # Initialize progress tracking
        self._current_progress = BuildProgress(
            stage=BuildStage.ENVIRONMENT_VALIDATION,
            completion_percent=0.0,
            current_operation="Initializing build process",
        )

        try:
            # Define and execute build stages
            build_stages = self._create_build_stages(device, config)

            # Execute all stages
            for stage, coro, start_msg, end_msg in build_stages:
                await self._run_stage(stage, coro, start_msg, end_msg)

            # Build complete
            self._current_progress.completion_percent = 100.0
            self._current_progress.current_operation = "Build completed successfully"
            await self._notify_progress()

            return True

        except asyncio.CancelledError:
            self._add_progress_warning("Build cancelled by user")
            await self._notify_progress()
            return False
        except Exception as e:
            self._report_exception("Build failed", e, platform_hint=True)
            await self._notify_progress()
            raise
        finally:
            self._is_building = False
            self._executor.shutdown(wait=True)

    def _create_build_stages(
        self, device: PCIDevice, config: BuildConfiguration
    ) -> List[Tuple[BuildStage, Callable, str, str]]:
        """
        Create the ordered list of build stages based on configuration.

        Args:
            device: The PCI device to build for
            config: Build configuration parameters

        Returns:
            List of (stage, coroutine, start_message, end_message) tuples
        """
        # Start with validation stages
        build_stages = [
            (
                BuildStage.ENVIRONMENT_VALIDATION,
                lambda: self._validate_environment(),
                "Validating environment",
                "Environment validation complete",
            ),
            (
                BuildStage.ENVIRONMENT_VALIDATION,
                lambda: self._validate_pci_config(device, config),
                "Validating PCI configuration values",
                "PCI configuration validation complete",
            ),
        ]

        # Add donor module check if needed
        if config.donor_dump and not config.local_build:
            build_stages.append(
                (
                    BuildStage.ENVIRONMENT_VALIDATION,
                    lambda: self._check_donor_module(config),
                    "Checking donor_dump module status",
                    "Donor module check complete",
                )
            )

        # Add device analysis and register extraction
        build_stages.extend(
            [
                (
                    BuildStage.DEVICE_ANALYSIS,
                    lambda: self._analyze_device(device),
                    "Analyzing device configuration",
                    "Device analysis complete",
                ),
                (
                    BuildStage.REGISTER_EXTRACTION,
                    lambda: self._extract_registers(device),
                    "Extracting device registers",
                    "Register extraction complete",
                ),
            ]
        )

        # Add behavior profiling if enabled
        if config.behavior_profiling:
            build_stages.append(
                (
                    BuildStage.REGISTER_EXTRACTION,
                    lambda: self._run_behavior_profiling(device, config),
                    "Starting behavior profiling",
                    "Behavior profiling complete",
                )
            )

        # Add final stages
        build_stages.extend(
            [
                (
                    BuildStage.SYSTEMVERILOG_GENERATION,
                    lambda: self._generate_systemverilog(device, config),
                    "Generating SystemVerilog",
                    "SystemVerilog generation complete",
                ),
                (
                    BuildStage.VIVADO_SYNTHESIS,
                    lambda: self._run_vivado_synthesis(device, config),
                    "Starting Vivado synthesis",
                    "Vivado synthesis complete",
                ),
                (
                    BuildStage.BITSTREAM_GENERATION,
                    lambda: self._generate_bitstream(config),
                    "Generating bitstream",
                    "Bitstream generation complete",
                ),
            ]
        )

        return build_stages

    async def _run_stage(
        self, stage: BuildStage, coro: Callable, start_msg: str, end_msg: str
    ) -> None:
        """
        Run a single build stage with progress tracking.

        Args:
            stage: The build stage being executed
            coro: Coroutine function to execute
            start_msg: Message to display at start
            end_msg: Message to display at completion

        Raises:
            asyncio.CancelledError: If build is cancelled
        """
        if self._should_cancel:
            raise asyncio.CancelledError("Build cancelled")

        await self._update_progress(stage, 0, start_msg)
        await coro()
        await self._update_progress(stage, 100, end_msg)

        if self._current_progress:
            self._current_progress.mark_stage_complete(stage)

    async def cancel_build(self) -> None:
        """
        Cancel the current build process gracefully.

        This attempts to terminate the process gracefully first,
        then forcefully kills it if necessary.
        """
        self._should_cancel = True

        if self._build_process:
            try:
                logger.info("Attempting to cancel build process")
                self._build_process.terminate()

                # Wait for graceful termination
                try:
                    await asyncio.wait_for(
                        self._build_process.wait(),
                        timeout=PROCESS_TERMINATION_TIMEOUT,
                    )
                except asyncio.TimeoutError:
                    logger.warning("Process did not terminate gracefully, forcing kill")
                    self._build_process.kill()

            except (psutil.Error, asyncio.CancelledError, ProcessLookupError) as e:
                self._report_exception("Error during build cancellation", e)

    def get_current_progress(self) -> Optional[BuildProgress]:
        """Get the current build progress state."""
        return self._current_progress

    def is_building(self) -> bool:
        """Check if a build is currently in progress."""
        return self._is_building

    async def _update_progress(
        self, stage: BuildStage, percent: float, operation: str
    ) -> None:
        """
        Update progress state and notify callback.

        Args:
            stage: Current build stage
            percent: Completion percentage (0-100)
            operation: Description of current operation
        """
        if not self._current_progress:
            return

        self._current_progress.stage = stage
        self._current_progress.completion_percent = percent
        self._current_progress.current_operation = operation

        # Update resource usage periodically rather than on every progress update
        current_time = datetime.datetime.now().timestamp()
        if current_time - self._last_resource_update >= RESOURCE_MONITOR_INTERVAL:
            await self._update_resource_usage()
            self._last_resource_update = current_time

        await self._notify_progress()

    async def _notify_progress(self) -> None:
        """Notify progress callback with current progress state."""
        if self._progress_callback and self._current_progress:
            try:
                self._progress_callback(self._current_progress)
            except Exception as e:
                # Log but don't fail the orchestrator on callback errors
                exc_obj = e if isinstance(e, Exception) else Exception(str(e))
                log_error_safe(
                    logger,
                    "Progress callback error: {msg}",
                    msg=extract_root_cause(exc_obj),
                )

    async def _update_resource_usage(self) -> None:
        """
        Update system resource usage metrics in the progress state.

        Collects CPU, memory, and disk usage information.
        """
        if not self._current_progress:
            return

        try:
            # Run resource-intensive operations in thread pool
            loop = asyncio.get_running_loop()

            # Get CPU usage (non-blocking)
            cpu_percent = psutil.cpu_percent(interval=None)

            # Get memory and disk info in thread pool
            memory = await loop.run_in_executor(
                self._executor,
                psutil.virtual_memory,
            )
            disk = await loop.run_in_executor(self._executor, psutil.disk_usage, "/")

            self._current_progress.update_resource_usage(
                cpu=cpu_percent,
                memory=memory.used / (1024**3),  # GB
                disk_free=disk.free / (1024**3),  # GB
            )
        except (psutil.Error, OSError) as e:
            log_warning_safe(
                logger,
                safe_format(
                    "Resource monitoring failed: {msg}",
                    msg=str(e),
                ),
                prefix="BUILD",
            )

    async def _validate_environment(self) -> None:
        """
        Validate the build environment requirements.

        Checks for required tools, permissions, and directories.

        Raises:
            RuntimeError: If environment validation fails
        """
        # Get current configuration
        app = self._get_app()
        config = getattr(app, "current_config", None)
        local_build = config and config.local_build

        if not local_build:
            await self._validate_container_environment()
        else:
            await self._validate_local_environment(config)

        # Check output directory
        output_dir = Path("output")
        output_dir.mkdir(exist_ok=True)

        # Ensure pcileech-fpga repository is available
        await self._ensure_git_repo()

    def _get_app(self):
        """
        Get the parent app instance.

        Returns:
            The parent app instance or None if not found
        """
        # Find the app instance in the widget tree
        widget = getattr(self, "_progress_callback", None)
        while widget:
            if hasattr(widget, "app"):
                return widget.app
            widget = getattr(widget, "parent", None)
        return None

    async def _validate_container_environment(self) -> None:
        """
        Validate environment for container-based builds.

        Checks for root privileges, Podman availability, and container image.

        Raises:
            RuntimeError: If validation fails
        """
        # Check if running as root (only needed for non-local builds)
        if os.geteuid() != 0:
            raise RuntimeError("Root privileges required for device binding")

        # Check if Podman is available
        try:
            result = await self._run_shell("podman --version", monitor=False)
            if result.returncode != 0:
                raise RuntimeError("Podman not available")
        except FileNotFoundError:
            raise RuntimeError("Podman not found in PATH")

        # Check if container image exists
        result = await self._run_shell(
            "podman images pcileech-fw-generator --format '{{.Repository}}'",
            monitor=False,
        )

        if "pcileech-fw-generator" not in result.stdout:
            await self._build_container_image()

    async def _build_container_image(self) -> None:
        """
        Build the pcileech-fw-generator container image.

        Raises:
            RuntimeError: If container build fails
        """
        if self._current_progress:
            self._current_progress.current_operation = (
                "Building container image 'pcileech-fw-generator'"
            )
            await self._notify_progress()

        try:
            logger.info("Container image 'pcileech-fw-generator' not found.")
            logger.info("Building it now...")
            build_result = await self._run_shell(
                "podman build -t pcileech-fw-generator:latest .", monitor=False
            )

            if build_result.returncode != 0:
                raise RuntimeError(
                    f"Failed to build container image: {build_result.stderr}"
                )

            logger.info("Container image built successfully")

        except Exception as e:
            raise RuntimeError(
                safe_format(
                    "Container image 'pcileech-fw-generator' not found and build "
                    "failed: {msg}",
                    msg=str(e),
                )
            )

    async def _validate_local_environment(
        self, config: Optional[BuildConfiguration]
    ) -> None:
        """
        Validate environment for local builds.

        Checks for build.py and donor info file.

        Args:
            config: Build configuration

        Raises:
            RuntimeError: If validation fails
        """
        # Check if build.py exists
        if not Path("src/build.py").exists():
            raise RuntimeError("build.py not found in src directory")

        # Check if donor info file exists if specified
        if (
            config
            and config.donor_info_file
            and not Path(config.donor_info_file).exists()
        ):
            if self._current_progress:
                self._current_progress.add_warning(
                    f"Donor info file not found: {config.donor_info_file}"
                )

    async def _ensure_git_repo(self) -> None:
        """
        Ensure the voltcyclone-fpga git submodule is available.

        Uses RepoManager to check submodule status.
        """
        if self._current_progress:
            self._current_progress.current_operation = (
                "Checking voltcyclone-fpga submodule"
            )
            await self._notify_progress()

        try:
            # RepoManager will raise RuntimeError if submodule not initialized
            repo_path = RepoManager.ensure_repo()
            
            if self._current_progress:
                self._current_progress.current_operation = (
                    f"voltcyclone-fpga submodule ready at {repo_path}"
                )
                await self._notify_progress()
        except RuntimeError as e:
            error_msg = str(e)
            self._add_progress_error(
                "Submodule not initialized: {msg}. "
                "Run: git submodule update --init --recursive",
                msg=error_msg
            )
            raise

    async def _check_donor_module(self, config: BuildConfiguration) -> None:
        """
        Check if donor_dump kernel module is properly installed.

        Args:
            config: Current build configuration
        """
        # Skip check if donor_dump is disabled or using local build
        if not config.donor_dump or config.local_build:
            return

        try:
            # Import donor_dump_manager
            donor_dump_manager = await self._import_donor_dump_manager()
            if not donor_dump_manager:
                return

            # Create manager and check status
            manager = donor_dump_manager.DonorDumpManager()
            module_status = manager.check_module_installation()

            await self._handle_module_status(config, manager, module_status)

        except ImportError as e:
            self._report_exception("Failed to import donor_dump_manager", e)
            self._report_donor_module_error(
                safe_format("Failed to import donor_dump_manager: {msg}", msg=str(e))
            )
        except Exception as e:
            self._report_exception("Error checking donor module", e)
            self._report_donor_module_error(
                safe_format("Error checking donor module: {msg}", msg=str(e))
            )

    def _report_donor_module_error(self, error_message: str) -> None:
        """
        Report donor module error in progress.

        Args:
            error_message: Error message to report
        """
        if self._current_progress:
            self._current_progress.add_error(error_message)

    async def _import_donor_dump_manager(self):
        """
        Import donor_dump_manager module dynamically.

        Returns:
            module or None: The imported module or None if import failed
        """
        try:
            import sys
            from pathlib import Path

            # Add project root to path
            project_root = Path(__file__).parent.parent.parent.parent
            if str(project_root) not in sys.path:
                sys.path.append(str(project_root))

            # Import the module
            # Return the module
            import file_management.donor_dump_manager as donor_dump_manager
            from file_management.donor_dump_manager import DonorDumpManager

            return donor_dump_manager

        except ImportError as e:
            logger.exception(f"Failed to import donor_dump_manager: {e}")
            if self._current_progress:
                self._current_progress.add_error(
                    f"Failed to import donor_dump_manager: {str(e)}"
                )
                await self._notify_progress()
            return None

    async def _handle_module_status(
        self, config: BuildConfiguration, manager: Any, module_status: Dict[str, Any]
    ) -> None:
        """
        Handle donor module status and attempt fixes if needed.

        Args:
            config: Build configuration
            manager: DonorDumpManager instance
            module_status: Module status information
        """
        if not self._current_progress:
            return

        status = module_status.get("status", "")
        details = module_status.get("details", "")

        if status != "installed":
            # Report module status issues
            self._report_module_status_issues(module_status)

            # Try to fix common issues if auto_install_headers is enabled
            if (
                config.auto_install_headers
                and status == "not_built"
                and "headers" in str(module_status.get("issues", []))
            ):
                await self._attempt_header_installation(
                    config,
                    manager,
                    module_status,
                )
        else:
            # Module is properly installed
            self._current_progress.current_operation = (
                "Donor module is properly installed"
            )
            await self._notify_progress()

    def _report_module_status_issues(self, module_status: Dict[str, Any]) -> None:
        """
        Report donor module status issues in progress.

        Args:
            module_status: Module status information
        """
        if not self._current_progress:
            return

        details = module_status.get("details", "")
        self._current_progress.add_warning(f"Donor module status: {details}")

        # Add first issue and fix to progress
        issues = module_status.get("issues", [])
        fixes = module_status.get("fixes", [])

        if issues:
            self._current_progress.add_warning(f"Issue: {issues[0]}")
        if fixes:
            self._current_progress.add_warning(f"Suggested fix: {fixes[0]}")

    async def _attempt_header_installation(
        self, config: BuildConfiguration, manager: Any, module_status: Dict[str, Any]
    ) -> None:
        """
        Attempt to install kernel headers and build donor module.

        Args:
            config: Build configuration
            manager: DonorDumpManager instance
            module_status: Module status information
        """
        if not self._current_progress:
            return

        self._current_progress.current_operation = (
            "Attempting to install kernel headers"
        )
        await self._notify_progress()

        # Get kernel version
        raw_status = module_status.get("raw_status", {})
        kernel_version = raw_status.get("kernel_version", "")
        if not kernel_version:
            self._current_progress.add_error(
                safe_format("Could not determine kernel version")
            )
            return

        try:
            self._current_progress.add_warning(
                safe_format("Detecting distro and installing kernel headers...")
            )

            # Install headers
            headers_installed = manager.install_kernel_headers(kernel_version)

            if headers_installed:
                await self._build_donor_module_after_headers(manager)
            else:
                self._report_header_installation_failure(manager, kernel_version)

        except Exception as e:
            self._current_progress.add_error(
                safe_format("Failed to install kernel headers: {err}", err=str(e))
            )
            self._current_progress.add_warning(
                safe_format(
                    "You may need to install kernel headers manually for your distro"
                )
            )

    async def _build_donor_module_after_headers(self, manager: Any) -> None:
        """
        Build donor module after successful header installation.

        Args:
            manager: DonorDumpManager instance
        """
        if not self._current_progress:
            return

        self._current_progress.add_warning("Kernel headers installed successfully")
        self._current_progress.current_operation = "Building donor_dump module"
        await self._notify_progress()

        try:
            manager.build_module(force_rebuild=True)
            self._current_progress.add_warning("Donor module built successfully")
        except Exception as build_error:
            self._current_progress.add_error(
                f"Failed to build module: {str(build_error)}"
            )

            # Add more detailed error information
            if "ModuleBuildError" in str(type(build_error)):
                self._current_progress.add_warning(
                    "This may be due to kernel mismatch or missing build tools."
                )
                self._current_progress.add_warning(
                    "Try: sudo apt-get install build-essential"
                )

    def _report_header_installation_failure(
        self, manager: Any, kernel_version: str
    ) -> None:
        """
        Report header installation failure with manual instructions.

        Args:
            manager: DonorDumpManager instance
            kernel_version: Kernel version string
        """
        if not self._current_progress:
            return

        self._current_progress.add_error(
            "Failed to install kernel headers automatically"
        )

        # Add manual instructions
        try:
            distro = manager._detect_linux_distribution()
            install_cmd = manager._get_header_install_command(distro, kernel_version)
            self._current_progress.add_warning(
                f"Please try installing headers manually: {install_cmd}"
            )
        except Exception as e:
            self._current_progress.add_warning(
                "Could not determine header install command for your distro"
            )

    async def _run_shell(self, cmd, monitor=True) -> subprocess.CompletedProcess:
        """
        Run a shell command with optional output monitoring.

        Args:
            cmd: Command to run (string or list)
            monitor: Whether to monitor output for progress updates

        Returns:
            subprocess.CompletedProcess: Command result

        Raises:
            RuntimeError: If command fails
        """
        if isinstance(cmd, list):
            cmd_str = " ".join(cmd)
        else:
            cmd_str = cmd

        if not monitor:
            # Simple command execution without monitoring
            process = await asyncio.create_subprocess_shell(
                cmd_str,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
            )
            stdout, stderr = await process.communicate()

            returncode = process.returncode if process.returncode is not None else 0
            return subprocess.CompletedProcess(
                args=cmd_str,
                returncode=returncode,
                stdout=stdout.decode("utf-8"),
                stderr=stderr.decode("utf-8"),
            )

        # Monitored command execution
        self._build_process = await asyncio.create_subprocess_shell(
            cmd_str,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )

        # Monitor process output for progress updates
        while True:
            if self._build_process.stdout:
                line = await self._build_process.stdout.readline()
                if not line:
                    break

                line_str = line.decode("utf-8").strip()
                if line_str:
                    # Update progress based on output using LOG_PROGRESS_TOKENS
                    for pattern, (stage, percent, msg) in LOG_PROGRESS_TOKENS.items():
                        if pattern in line_str:
                            await self._update_progress(stage, percent, msg)
                            break

            # Check if process has completed
            if self._build_process.returncode is not None:
                break

            await asyncio.sleep(0.1)

        # Wait for process to complete
        await self._build_process.wait()

        if self._build_process.returncode != 0:
            error_msg = ""
            if self._build_process.stderr:
                stderr = await self._build_process.stderr.read()
                error_msg = stderr.decode("utf-8")
            self._add_progress_error(
                "Build command failed: {msg}", msg=error_msg or "unknown error"
            )
            raise RuntimeError(
                safe_format(
                    "Build command failed with code {code}",
                    code=self._build_process.returncode,
                )
            )

        # Return a CompletedProcess-like object for compatibility
        return subprocess.CompletedProcess(
            args=cmd_str,
            returncode=self._build_process.returncode,
            stdout="",  # stdout was consumed during monitoring
            stderr="",  # stderr will be read if there's an error
        )

    async def _validate_pci_config(
        self, device: PCIDevice, config: BuildConfiguration
    ) -> None:
        """
        Validate PCI configuration values against donor card.

        Args:
            device: The PCIe device to validate
            config: Current build configuration
        """
        try:
            # Skip validation for local builds without donor info file
            if config.local_build and not config.donor_info_file:
                if self._current_progress:
                    self._add_progress_warning(
                        "Skipping PCI config validation - no donor info file"
                    )
                return

            # Import build module
            from pathlib import Path

            # For local builds with donor info file, load and validate the file
            if config.local_build and config.donor_info_file:
                import json

                try:
                    with open(config.donor_info_file, "r") as f:
                        donor_info = json.load(f)

                    # Validate the donor info file was loaded successfully
                    if self._current_progress:
                        self._current_progress.current_operation = (
                            "Validating donor info file structure"
                        )
                        await self._notify_progress()

                    # Compare with device info
                    validation_results = []

                    # Check vendor ID
                    if device.vendor_id and "vendor_id" in donor_info:
                        device_vendor = device.vendor_id.lower().replace("0x", "")
                        donor_vendor = donor_info["vendor_id"].lower()
                        donor_vendor = donor_vendor.replace("0x", "")
                        if device_vendor != donor_vendor:
                            validation_results.append(
                                ValidationResult(
                                    field="vendor_id",
                                    expected=donor_vendor,
                                    actual=device_vendor,
                                    status="mismatch",
                                )
                            )

                    # Check device ID
                    if device.device_id and "device_id" in donor_info:
                        device_id = device.device_id.lower().replace("0x", "")
                        donor_id = donor_info["device_id"].lower().replace("0x", "")
                        if device_id != donor_id:
                            validation_results.append(
                                ValidationResult(
                                    field="device_id",
                                    expected=donor_id,
                                    actual=device_id,
                                    status="mismatch",
                                )
                            )

                    # Check subsystem vendor ID
                    if device.subsystem_vendor and "subvendor_id" in donor_info:
                        device_subvendor = device.subsystem_vendor.lower().replace(
                            "0x", ""
                        )
                        donor_subvendor = donor_info["subvendor_id"].lower()
                        donor_subvendor = donor_subvendor.replace("0x", "")
                        if device_subvendor != donor_subvendor:
                            validation_results.append(
                                ValidationResult(
                                    field="subvendor_id",
                                    expected=donor_subvendor,
                                    actual=device_subvendor,
                                    status="mismatch",
                                )
                            )

                    # Check subsystem device ID
                    if device.subsystem_device and "subsystem_id" in donor_info:
                        device_subsystem = device.subsystem_device.lower().replace(
                            "0x", ""
                        )
                        donor_subsystem = donor_info["subsystem_id"].lower()
                        donor_subsystem = donor_subsystem.replace("0x", "")
                        if device_subsystem != donor_subsystem:
                            validation_results.append(
                                ValidationResult(
                                    field="subsystem_id",
                                    expected=donor_subsystem,
                                    actual=device_subsystem,
                                    status="mismatch",
                                )
                            )

                    # Add validation results to progress
                    if validation_results:
                        if self._current_progress:
                            self._current_progress.add_warning(
                                safe_format(
                                    "Found {n} PCI config mismatches",
                                    n=len(validation_results),
                                )
                            )
                            for result in validation_results:
                                self._add_progress_warning(
                                    "PCI mismatch: {field} - expected {exp}, "
                                    "got {act}",
                                    field=result.field,
                                    exp=result.expected,
                                    act=result.actual,
                                )
                    else:
                        if self._current_progress:
                            self._current_progress.current_operation = (
                                "PCI configuration values match donor card"
                            )
                            await self._notify_progress()

                except FileNotFoundError:
                    self._add_progress_error(
                        "Donor info file missing: {path}",
                        path=str(config.donor_info_file),
                    )
                except json.JSONDecodeError:
                    self._add_progress_error(
                        "Invalid JSON in donor info file: {path}",
                        path=str(config.donor_info_file),
                    )
                except Exception as e:
                    self._report_exception("Error validating PCI configuration", e)

            # For non-local builds with donor_dump, validation happens during
            # donor_dump extraction
            elif not config.local_build and config.donor_dump:
                if self._current_progress:
                    self._current_progress.current_operation = (
                        "PCI validation will be performed during donor extraction"
                    )
                    await self._notify_progress()

        except ImportError as e:
            self._report_exception("Failed to validate PCI configuration", e)
            await self._notify_progress()
        except Exception as e:
            self._report_exception("Failed to validate PCI configuration", e)
            await self._notify_progress()

    async def _analyze_device(self, device: PCIDevice) -> None:
        """
        Analyze device configuration.

        Args:
            device: The PCIe device to analyze
        """
        # Import existing functions
        from pathlib import Path

        from ...cli.vfio import get_current_driver
        from ...cli.vfio_handler import _get_iommu_group

        # Get current device state
        current_driver = await asyncio.get_event_loop().run_in_executor(
            None, get_current_driver, device.bdf
        )

        iommu_group = await asyncio.get_event_loop().run_in_executor(
            None, _get_iommu_group, device.bdf
        )

        # Validate VFIO device path
        vfio_device = f"/dev/vfio/{iommu_group}"
        if not os.path.exists(vfio_device) and self._current_progress:
            self._current_progress.add_warning(
                safe_format("Missing VFIO device {dev}", dev=vfio_device)
            )

    async def _extract_registers(self, device: PCIDevice) -> None:
        """
        Extract device registers.

        Args:
            device: The PCIe device to extract registers from
        """
        # This would integrate with existing register extraction logic
        await asyncio.sleep(1)  # Simulate register extraction

        if self._current_progress:
            self._current_progress.current_operation = (
                f"Extracted registers from device {device.bdf}"
            )
            await self._notify_progress()

    async def _run_behavior_profiling(
        self, device: PCIDevice, config: BuildConfiguration
    ) -> None:
        """
        Run behavior profiling on the device.

        Args:
            device: The PCIe device to profile
            config: Build configuration
        """
        # Import behavior profiler
        import sys
        from pathlib import Path

        sys.path.append(str(Path(__file__).parent.parent.parent))
        from device_clone.behavior_profiler import BehaviorProfiler

        # Log the start of profiling
        if self._current_progress:
            self._current_progress.current_operation = safe_format(
                "Profiling device {bdf}", bdf=device.bdf
            )
            await self._notify_progress()

        # Run profiling in a separate thread to avoid blocking the event loop

        def run_profiling():
            try:
                # Use enable_ftrace=True for real hardware; requires root
                enable_ftrace = not config.disable_ftrace and os.geteuid() == 0
                profiler = BehaviorProfiler(
                    bdf=device.bdf, debug=True, enable_ftrace=enable_ftrace
                )
                profile = profiler.capture_behavior_profile(
                    duration=config.profile_duration
                )
                return profile
            except Exception as e:
                if self._current_progress:
                    self._current_progress.add_error(
                        safe_format("Behavior profiling failed: {err}", err=str(e))
                    )
                return None

        # Execute profiling in a thread pool
        loop = asyncio.get_running_loop()
        profile = await loop.run_in_executor(self._executor, run_profiling)

        # Update progress with results
        if profile and self._current_progress:
            self._current_progress.current_operation = safe_format(
                "Analyzed {n} register accesses",
                n=profile.total_accesses,
            )
            self._current_progress.add_warning(
                safe_format("Found {n} timing patterns", n=len(profile.timing_patterns))
            )
            self._current_progress.add_warning(
                safe_format(
                    "Identified {n} state transitions",
                    n=len(profile.state_transitions),
                )
            )

    async def _generate_systemverilog(
        self, device: PCIDevice, config: BuildConfiguration
    ) -> None:
        """
        Generate SystemVerilog code.

        Args:
            device: The PCIe device to generate code for
            config: Build configuration
        """
        # This would integrate with existing SystemVerilog generation
        await asyncio.sleep(2)  # Simulate SystemVerilog generation

        if self._current_progress:
            self._current_progress.current_operation = (
                f"Generated SystemVerilog for device {device.bdf}"
            )
            await self._notify_progress()

    async def _run_vivado_synthesis(
        self, device: PCIDevice, config: BuildConfiguration
    ) -> None:
        """
        Run Vivado synthesis in container or locally.

        Args:
            device: The PCIe device to synthesize for
            config: Build configuration
        """
        # Convert config to CLI-like args (no method available on TUI model)
        cli_args = {
            "advanced_sv": bool(config.advanced_sv),
            "enable_variance": bool(config.enable_variance),
            "enable_behavior_profiling": bool(config.behavior_profiling),
            "behavior_profile_duration": float(config.profile_duration),
            "use_donor_dump": bool(config.donor_dump),
            "donor_info_file": config.donor_info_file,
            "skip_board_check": bool(config.skip_board_check),
        }

        # Build command parts
        build_cmd_parts = [
            f"python3 src/build.py --bdf {device.bdf} --board {config.board_type}"
        ]

        if cli_args.get("advanced_sv"):
            build_cmd_parts.append("--advanced-sv")
        if cli_args.get("enable_variance"):
            build_cmd_parts.append("--enable-variance")
        if cli_args.get("enable_behavior_profiling"):
            build_cmd_parts.append("--enable-behavior-profiling")
            build_cmd_parts.append(
                f"--profile-duration {cli_args['behavior_profile_duration']}"
            )

        # Add donor dump options
        if cli_args.get("use_donor_dump"):
            build_cmd_parts.append("--use-donor-dump")
        # Only add donor_info_file when explicitly provided and not empty
        donor_info_file = cli_args.get("donor_info_file")
        if (
            donor_info_file
            and isinstance(donor_info_file, str)
            and donor_info_file.strip()
        ):
            build_cmd_parts.append(f"--donor-info-file {donor_info_file}")
        if cli_args.get("skip_board_check"):
            build_cmd_parts.append("--skip-board-check")

        # Add Vivado execution flag to actually run Vivado
        build_cmd_parts.append("--run-vivado")

        build_cmd = " ".join(build_cmd_parts)

        if config.local_build:
            # Run locally
            if self._current_progress:
                self._current_progress.current_operation = "Running local build"
                await self._notify_progress()

            # Run the build command directly
            await self._run_shell(build_cmd.split())
        else:
            # Get IOMMU group for VFIO device
            from pathlib import Path

            from ...cli.vfio_handler import _get_iommu_group

            iommu_group = await asyncio.get_running_loop().run_in_executor(
                None, _get_iommu_group, device.bdf
            )
            vfio_device = f"/dev/vfio/{iommu_group}"

            # Construct container command
            container_cmd = [
                "podman",
                "run",
                "--rm",
                "-it",
                "--privileged",
                f"--device={vfio_device}",
                "--device=/dev/vfio/vfio",
                "-v",
                f"{os.getcwd()}/output:/app/output",
                "pcileech-fw-generator:latest",
                (
                    f"python3 /app/src/build.py --bdf {device.bdf} "
                    f"--board {config.board_type}"
                ),
            ]

            # Add the same options to the container command
            for option in build_cmd_parts[
                1:
            ]:  # Skip the first part (python3 src/build.py)
                container_cmd.append(option)

            # Run container with progress monitoring
            await self._run_shell(container_cmd)

    async def _generate_bitstream(self, config: BuildConfiguration) -> None:
        """
        Generate final bitstream.

        Args:
            config: Build configuration
        """
        # This would be part of the Vivado synthesis step
        await asyncio.sleep(1)  # Simulate bitstream generation

        if self._current_progress:
            self._current_progress.current_operation = "Bitstream generation complete"
            await self._notify_progress()
