"""
Build Orchestrator

Orchestrates the build process with real-time monitoring and progress tracking.
This implementation exposes two UI-facing methods for the Build Log dialog:
- get_current_build_log() -> List[str]
- get_build_history() -> List[Dict[str, Any]]
"""

import asyncio
import datetime
import logging
import os
import shutil
import subprocess
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from typing import Any, Callable, Dict, List, Optional, Tuple

import psutil

from src.error_utils import (
    extract_root_cause,
    format_user_friendly_error,
    log_error_with_root_cause,
)
from string_utils import log_error_safe, log_warning_safe, safe_format

from ..models.config import BuildConfiguration
from ..models.device import PCIDevice
from ..models.progress import BuildProgress, BuildStage, ValidationResult

# Constants
PCILEECH_FPGA_REPO = "https://github.com/ufrisk/pcileech-fpga.git"
REPO_CACHE_DIR = Path(os.path.expanduser("~/.cache/pcileech-fw-generator/repos"))
GIT_REPO_UPDATE_DAYS = 7
RESOURCE_MONITOR_INTERVAL = 1.0
PROCESS_TERMINATION_TIMEOUT = 2.0

# Progress parsing tokens
LOG_PROGRESS_TOKENS: Dict[str, Tuple[BuildStage, int, str]] = {
    "Running synthesis": (BuildStage.VIVADO_SYNTHESIS, 25, "Running synthesis"),
    "Running implementation": (
        BuildStage.VIVADO_SYNTHESIS,
        50,
        "Running implementation",
    ),
    "Generating bitstream": (BuildStage.VIVADO_SYNTHESIS, 75, "Generating bitstream"),
}

logger = logging.getLogger(__name__)

# Optional imports with fallbacks
try:  # pragma: no cover
    from git import GitCommandError, InvalidGitRepositoryError, Repo

    GIT_AVAILABLE = True
except ModuleNotFoundError:  # pragma: no cover
    GIT_AVAILABLE = False
    Repo = None  # type: ignore
    GitCommandError = InvalidGitRepositoryError = Exception  # type: ignore

try:  # pragma: no cover
    from ...file_management.repo_manager import RepoManager
except ImportError:  # pragma: no cover
    RepoManager = None  # type: ignore


class BuildOrchestrator:
    """Coordinate the build pipeline and surface progress to the UI."""

    def __init__(self) -> None:
        self._current_progress: Optional[BuildProgress] = None
        self._build_process: Optional[asyncio.subprocess.Process] = None
        self._progress_callback: Optional[Callable[[BuildProgress], None]] = None
        self._is_building = False
        self._should_cancel = False
        self._executor = ThreadPoolExecutor(max_workers=4)
        self._last_resource_update = 0.0
        # UI context + buffers
        self._current_config: Optional[BuildConfiguration] = None
        self._current_device: Optional[PCIDevice] = None
        self._build_start_time: Optional[datetime.datetime] = None
        self._current_log_lines: List[str] = []
        self._build_history: List[Dict[str, Any]] = []

    # ---------- error helpers ----------
    def _add_progress_error(self, message: str, **kwargs: Any) -> None:
        if self._current_progress:
            self._current_progress.add_error(safe_format(message, **kwargs))

    def _add_progress_warning(self, message: str, **kwargs: Any) -> None:
        if self._current_progress:
            self._current_progress.add_warning(safe_format(message, **kwargs))

    def _report_exception(
        self, prefix: str, exc: BaseException, *, platform_hint: bool = False
    ) -> None:
        error_str = str(exc)
        exc_for_root = exc if isinstance(exc, Exception) else Exception(error_str)
        root_cause = extract_root_cause(exc_for_root)

        if platform_hint and (
            ("requires Linux" in error_str)
            or ("platform incompatibility" in error_str)
            or ("only available on Linux" in error_str)
        ):
            self._add_progress_warning("{prefix}: {msg}", prefix=prefix, msg=error_str)
            log_warning_safe(
                logger, "{pfx}: {rc}", pfx=safe_format(prefix), rc=root_cause
            )
            return

        try:
            friendly = format_user_friendly_error(exc_for_root, context=prefix)
            self._add_progress_error("{msg}", msg=friendly)
        except Exception:
            self._add_progress_error("{prefix}: {msg}", prefix=prefix, msg=error_str)

        log_error_with_root_cause(
            logger,
            safe_format("{pfx} failed", pfx=prefix),
            exc_for_root,
            show_full_traceback=False,
        )

    # ---------- public API ----------
    async def start_build(
        self,
        device: PCIDevice,
        config: BuildConfiguration,
        progress_callback: Optional[Callable[[BuildProgress], None]] = None,
    ) -> bool:
        if self._is_building:
            raise RuntimeError("Build already in progress")

        self._is_building = True
        self._should_cancel = False
        self._progress_callback = progress_callback
        self._current_config = config
        self._current_device = device
        self._build_start_time = datetime.datetime.now()
        self._current_log_lines = []

        self._current_progress = BuildProgress(
            stage=BuildStage.ENVIRONMENT_VALIDATION,
            completion_percent=0.0,
            current_operation="Initializing build",
        )
        await self._notify_progress()

        try:
            for stage, coro, start_msg, end_msg in self._create_build_stages(
                device, config
            ):
                await self._run_stage(stage, coro, start_msg, end_msg)

            if self._current_progress:
                self._current_progress.completion_percent = 100.0
                self._current_progress.current_operation = (
                    "Build completed successfully"
                )
            await self._notify_progress()
            try:
                self._record_build_history(status="success")
            except Exception:
                pass
            return True
        except asyncio.CancelledError:
            self._add_progress_warning("Build cancelled by user")
            await self._notify_progress()
            try:
                self._record_build_history(status="cancelled")
            except Exception:
                pass
            return False
        except Exception as e:
            self._report_exception("Build failed", e, platform_hint=True)
            await self._notify_progress()
            try:
                self._record_build_history(status="error", error=str(e))
            except Exception:
                pass
            raise
        finally:
            self._is_building = False
            try:
                self._executor.shutdown(wait=True)
            except Exception:
                pass

    def _create_build_stages(
        self, device: PCIDevice, config: BuildConfiguration
    ) -> List[Tuple[BuildStage, Callable, str, str]]:
        stages: List[Tuple[BuildStage, Callable, str, str]] = [
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
        if config.donor_dump and not config.local_build:
            stages.append(
                (
                    BuildStage.ENVIRONMENT_VALIDATION,
                    lambda: self._check_donor_module(config),
                    "Checking donor_dump module status",
                    "Donor module check complete",
                )
            )
        stages.extend(
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
        if config.behavior_profiling:
            stages.append(
                (
                    BuildStage.REGISTER_EXTRACTION,
                    lambda: self._run_behavior_profiling(device, config),
                    "Starting behavior profiling",
                    "Behavior profiling complete",
                )
            )
        stages.extend(
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
        return stages

    async def _run_stage(
        self, stage: BuildStage, coro: Callable, start_msg: str, end_msg: str
    ) -> None:
        if self._should_cancel:
            raise asyncio.CancelledError("Build cancelled")
        await self._update_progress(stage, 0, start_msg)
        await coro()
        await self._update_progress(stage, 100, end_msg)
        if self._current_progress:
            self._current_progress.mark_stage_complete(stage)

    async def cancel_build(self) -> None:
        self._should_cancel = True
        if self._build_process:
            try:
                logger.info("Attempting to cancel build process")
                self._build_process.terminate()
                try:
                    await asyncio.wait_for(
                        self._build_process.wait(), timeout=PROCESS_TERMINATION_TIMEOUT
                    )
                except asyncio.TimeoutError:
                    logger.warning("Process did not terminate gracefully, forcing kill")
                    self._build_process.kill()
            except (psutil.Error, asyncio.CancelledError, ProcessLookupError) as e:
                self._report_exception("Error during build cancellation", e)

    def get_current_progress(self) -> Optional[BuildProgress]:
        return self._current_progress

    def is_building(self) -> bool:
        return self._is_building

    async def _update_progress(
        self, stage: BuildStage, percent: float, operation: str
    ) -> None:
        if not self._current_progress:
            return
        self._current_progress.stage = stage
        self._current_progress.completion_percent = percent
        self._current_progress.current_operation = operation
        current_time = datetime.datetime.now().timestamp()
        if current_time - self._last_resource_update >= RESOURCE_MONITOR_INTERVAL:
            await self._update_resource_usage()
            self._last_resource_update = current_time
        await self._notify_progress()

    async def _notify_progress(self) -> None:
        if self._progress_callback and self._current_progress:
            try:
                self._progress_callback(self._current_progress)
            except Exception as e:  # pragma: no cover
                exc_obj = e if isinstance(e, Exception) else Exception(str(e))
                log_error_safe(
                    logger,
                    "Progress callback error: {msg}",
                    msg=extract_root_cause(exc_obj),
                )

    async def _update_resource_usage(self) -> None:
        if not self._current_progress:
            return
        try:
            loop = asyncio.get_running_loop()
            cpu_percent = psutil.cpu_percent(interval=None)
            memory = await loop.run_in_executor(self._executor, psutil.virtual_memory)
            disk = await loop.run_in_executor(self._executor, psutil.disk_usage, "/")
            self._current_progress.update_resource_usage(
                cpu=cpu_percent,
                memory=memory.used / (1024**3),
                disk_free=disk.free / (1024**3),
            )
        except (psutil.Error, OSError) as e:
            log_warning_safe(logger, "Resource monitoring failed: {msg}", msg=str(e))

    async def _validate_environment(self) -> None:
        config = self._current_config
        local_build = bool(config and config.local_build)
        if not local_build:
            await self._validate_container_environment()
        else:
            await self._validate_local_environment(config)
        Path("output").mkdir(exist_ok=True)
        await self._ensure_git_repo()

    def _get_app(self):  # Deprecated, kept for compatibility
        return None

    async def _validate_container_environment(self) -> None:
        if os.geteuid() != 0:
            raise RuntimeError("Root privileges required for device binding")
        try:
            result = await self._run_shell("podman --version", monitor=False)
            if result.returncode != 0:
                raise RuntimeError("Podman not available")
        except FileNotFoundError:
            raise RuntimeError("Podman not found in PATH")
        result = await self._run_shell(
            "podman images pcileech-fw-generator --format '{{.Repository}}'",
            monitor=False,
        )
        if "pcileech-fw-generator" not in result.stdout:
            await self._build_container_image()

    async def _build_container_image(self) -> None:
        if self._current_progress:
            self._current_progress.current_operation = (
                "Building container image 'pcileech-fw-generator'"
            )
            await self._notify_progress()
        logger.info("Container image 'pcileech-fw-generator' not found. Building...")
        build_result = await self._run_shell(
            "podman build -t pcileech-fw-generator:latest .", monitor=False
        )
        if build_result.returncode != 0:
            raise RuntimeError(
                f"Failed to build container image: {build_result.stderr}"
            )

    async def _validate_local_environment(
        self, config: Optional[BuildConfiguration]
    ) -> None:
        if not Path("src/build.py").exists():
            raise RuntimeError("build.py not found in src directory")
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
        if self._current_progress:
            self._current_progress.current_operation = (
                "Checking pcileech-fpga repository"
            )
            await self._notify_progress()
        repo_dir = REPO_CACHE_DIR / "pcileech-fpga"
        os.makedirs(REPO_CACHE_DIR, exist_ok=True)
        if RepoManager is not None:  # type: ignore
            await self._ensure_repo_with_manager()
            return
        if GIT_AVAILABLE and Repo is not None:  # type: ignore
            await self._ensure_repo_with_git(repo_dir)
            return
        await self._ensure_repo_fallback(repo_dir)

    async def _ensure_repo_with_manager(self) -> None:
        if RepoManager is None:
            return
        repo_path = RepoManager.ensure_repo(repo_url=PCILEECH_FPGA_REPO)
        if self._current_progress:
            self._current_progress.current_operation = (
                f"PCILeech FPGA repository ensured at {repo_path}"
            )
            await self._notify_progress()

    async def _ensure_repo_with_git(self, repo_dir: Path) -> None:
        if not GIT_AVAILABLE or Repo is None:
            return
        try:
            os.makedirs(repo_dir, exist_ok=True)
            repo = Repo(repo_dir)  # type: ignore
            if self._current_progress:
                self._current_progress.current_operation = (
                    f"PCILeech FPGA repository found at {repo_dir}"
                )
                await self._notify_progress()
            await self._update_git_repo_if_needed(repo, repo_dir)
        except (InvalidGitRepositoryError, GitCommandError):  # type: ignore
            await self._clone_git_repo(repo_dir)

    async def _update_git_repo_if_needed(self, repo: Any, repo_dir: Path) -> None:
        try:
            last_update_file = repo_dir / ".last_update"
            update_needed = True
            if last_update_file.exists():
                try:
                    with open(last_update_file, "r") as f:
                        last_update = datetime.datetime.fromisoformat(f.read().strip())
                    days_since_update = (datetime.datetime.now() - last_update).days
                    update_needed = days_since_update >= GIT_REPO_UPDATE_DAYS
                except Exception:
                    update_needed = True
            if update_needed:
                await self._update_git_repo(repo, last_update_file)
        except (OSError, IOError) as e:
            self._add_progress_warning(
                "Error checking repository update status: {msg}", msg=str(e)
            )

    async def _update_git_repo(self, repo: Any, last_update_file: Path) -> None:
        if self._current_progress:
            self._current_progress.current_operation = (
                "Updating PCILeech FPGA repository"
            )
            await self._notify_progress()
        try:
            origin = repo.remotes.origin
            origin.pull()
            with open(last_update_file, "w") as f:
                f.write(datetime.datetime.now().isoformat())
            if self._current_progress:
                self._current_progress.current_operation = (
                    "PCILeech FPGA repository updated successfully"
                )
                await self._notify_progress()
        except GitCommandError as e:  # type: ignore
            if self._current_progress:
                self._current_progress.add_warning(
                    f"Failed to update repository: {str(e)}"
                )

    async def _clone_git_repo(self, repo_dir: Path) -> None:
        if not GIT_AVAILABLE or Repo is None:
            return
        if self._current_progress:
            self._current_progress.current_operation = (
                f"Cloning PCILeech FPGA repository to {repo_dir}"
            )
            await self._notify_progress()
        try:
            if os.path.exists(repo_dir):
                shutil.rmtree(repo_dir)
            Repo.clone_from(PCILEECH_FPGA_REPO, repo_dir)  # type: ignore
            with open(repo_dir / ".last_update", "w") as f:
                f.write(datetime.datetime.now().isoformat())
            if self._current_progress:
                self._current_progress.current_operation = (
                    "PCILeech FPGA repository cloned successfully"
                )
                await self._notify_progress()
        except (GitCommandError, OSError) as e:  # type: ignore
            self._add_progress_error("Failed to clone repository: {msg}", msg=str(e))
            raise RuntimeError(f"Failed to clone PCILeech FPGA repository: {str(e)}")

    async def _ensure_repo_fallback(self, repo_dir: Path) -> None:
        os.makedirs(repo_dir, exist_ok=True)
        if self._current_progress:
            self._current_progress.add_warning(
                "GitPython not available. Using fallback directory."
            )
            self._current_progress.current_operation = (
                f"Using fallback directory at {repo_dir}"
            )
            await self._notify_progress()

    async def _check_donor_module(self, config: BuildConfiguration) -> None:
        if not config.donor_dump or config.local_build:
            return
        try:
            donor_dump_manager = await self._import_donor_dump_manager()
            if not donor_dump_manager:
                return
            manager = donor_dump_manager.DonorDumpManager()
            module_status = manager.check_module_installation()
            await self._handle_module_status(config, manager, module_status)
        except ImportError as e:
            self._report_exception("Failed to import donor_dump_manager", e)
            self._report_donor_module_error(
                safe_format("Failed to import donor_dump_manager: {msg}", msg=str(e))
            )
        except Exception as e:  # pragma: no cover
            self._report_exception("Error checking donor module", e)
            self._report_donor_module_error(
                safe_format("Error checking donor module: {msg}", msg=str(e))
            )

    def _report_donor_module_error(self, error_message: str) -> None:
        if self._current_progress:
            self._current_progress.add_error(error_message)

    async def _import_donor_dump_manager(self):  # pragma: no cover
        try:
            import sys
            from pathlib import Path as _Path

            project_root = _Path(__file__).parent.parent.parent.parent
            if str(project_root) not in sys.path:
                sys.path.append(str(project_root))
            import file_management.donor_dump_manager as donor_dump_manager

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
        if not self._current_progress:
            return
        status = module_status.get("status", "")
        if status != "installed":
            self._report_module_status_issues(module_status)
            if (
                config.auto_install_headers
                and status == "not_built"
                and "headers" in str(module_status.get("issues", []))
            ):
                await self._attempt_header_installation(config, manager, module_status)
        else:
            self._current_progress.current_operation = (
                "Donor module is properly installed"
            )
            await self._notify_progress()

    def _report_module_status_issues(self, module_status: Dict[str, Any]) -> None:
        if not self._current_progress:
            return
        details = module_status.get("details", "")
        self._current_progress.add_warning(f"Donor module status: {details}")
        issues = module_status.get("issues", [])
        fixes = module_status.get("fixes", [])
        if issues:
            self._current_progress.add_warning(f"Issue: {issues[0]}")
        if fixes:
            self._current_progress.add_warning(f"Suggested fix: {fixes[0]}")

    async def _attempt_header_installation(
        self, config: BuildConfiguration, manager: Any, module_status: Dict[str, Any]
    ) -> None:
        if not self._current_progress:
            return
        self._current_progress.current_operation = (
            "Attempting to install kernel headers"
        )
        await self._notify_progress()
        raw_status = module_status.get("raw_status", {})
        kernel_version = raw_status.get("kernel_version", "")
        if not kernel_version:
            self._current_progress.add_error("Could not determine kernel version")
            return
        try:
            self._current_progress.add_warning(
                safe_format("Detecting distro and installing kernel headers...")
            )
            headers_installed = manager.install_kernel_headers(kernel_version)
            if headers_installed:
                await self._build_donor_module_after_headers(manager)
            else:
                self._report_header_installation_failure(manager, kernel_version)
        except Exception as e:  # pragma: no cover
            self._current_progress.add_error(
                safe_format("Failed to install kernel headers: {err}", err=str(e))
            )
            self._current_progress.add_warning(
                safe_format(
                    "You may need to install kernel headers manually for your distro"
                )
            )

    async def _build_donor_module_after_headers(self, manager: Any) -> None:
        if not self._current_progress:
            return
        self._current_progress.add_warning("Kernel headers installed successfully")
        self._current_progress.current_operation = "Building donor_dump module"
        await self._notify_progress()
        try:
            manager.build_module(force_rebuild=True)
            self._current_progress.add_warning("Donor module built successfully")
        except Exception as build_error:  # pragma: no cover
            self._current_progress.add_error(
                f"Failed to build module: {str(build_error)}"
            )
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
        if not self._current_progress:
            return
        self._current_progress.add_error(
            "Failed to install kernel headers automatically"
        )
        try:  # pragma: no cover
            distro = manager._detect_linux_distribution()
            install_cmd = manager._get_header_install_command(distro, kernel_version)
            self._current_progress.add_warning(
                f"Please try installing headers manually: {install_cmd}"
            )
        except Exception:
            self._current_progress.add_warning(
                "Could not determine header install command for your distro"
            )

    async def _run_shell(
        self, cmd: Any, monitor: bool = True
    ) -> subprocess.CompletedProcess:
        cmd_str = " ".join(cmd) if isinstance(cmd, list) else str(cmd)
        if not monitor:
            process = await asyncio.create_subprocess_shell(
                cmd_str, stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE
            )
            stdout, stderr = await process.communicate()
            returncode = process.returncode if process.returncode is not None else 0
            try:
                if stdout:
                    for ln in stdout.decode("utf-8", errors="replace").splitlines():
                        self._append_log_line(ln)
                if stderr:
                    for ln in stderr.decode("utf-8", errors="replace").splitlines():
                        self._append_log_line(ln)
            except Exception:
                pass
            return subprocess.CompletedProcess(
                args=cmd_str,
                returncode=returncode,
                stdout=stdout.decode("utf-8"),
                stderr=stderr.decode("utf-8"),
            )

        self._build_process = await asyncio.create_subprocess_shell(
            cmd_str, stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE
        )
        assert self._build_process.stdout is not None
        while True:
            line = await self._build_process.stdout.readline()
            if not line:
                break
            line_str = line.decode("utf-8", errors="replace").strip()
            if line_str:
                self._append_log_line(line_str)
                for pattern, (stage, percent, msg) in LOG_PROGRESS_TOKENS.items():
                    if pattern in line_str:
                        await self._update_progress(stage, percent, msg)
                        break
            if self._build_process.returncode is not None:
                break
            await asyncio.sleep(0.1)

        await self._build_process.wait()
        if self._build_process.returncode != 0:
            error_msg = ""
            if self._build_process.stderr:
                stderr = await self._build_process.stderr.read()
                error_msg = stderr.decode("utf-8", errors="replace")
                try:
                    for ln in error_msg.splitlines():
                        self._append_log_line(ln)
                except Exception:
                    pass
            self._add_progress_error(
                "Build command failed: {msg}", msg=error_msg or "unknown error"
            )
            raise RuntimeError(
                safe_format(
                    "Build command failed with code {code}",
                    code=self._build_process.returncode,
                )
            )
        return subprocess.CompletedProcess(
            args=cmd_str,
            returncode=self._build_process.returncode,
            stdout="",
            stderr="",
        )

    async def _validate_pci_config(
        self, device: PCIDevice, config: BuildConfiguration
    ) -> None:
        try:
            if config.local_build and not config.donor_info_file:
                if self._current_progress:
                    self._add_progress_warning(
                        "Skipping PCI config validation - no donor info file"
                    )
                return
            if config.local_build and config.donor_info_file:
                import json

                try:
                    with open(config.donor_info_file, "r") as f:
                        donor_info = json.load(f)
                    if self._current_progress:
                        self._current_progress.current_operation = (
                            "Validating donor info file structure"
                        )
                        await self._notify_progress()
                    validation_results: List[ValidationResult] = []
                    if device.vendor_id and "vendor_id" in donor_info:
                        device_vendor = device.vendor_id.lower().replace("0x", "")
                        donor_vendor = donor_info["vendor_id"].lower().replace("0x", "")
                        if device_vendor != donor_vendor:
                            validation_results.append(
                                ValidationResult(
                                    field="vendor_id",
                                    expected=donor_vendor,
                                    actual=device_vendor,
                                    status="mismatch",
                                )
                            )
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
                    if device.subsystem_vendor and "subvendor_id" in donor_info:
                        device_subvendor = device.subsystem_vendor.lower().replace(
                            "0x", ""
                        )
                        donor_subvendor = (
                            donor_info["subvendor_id"].lower().replace("0x", "")
                        )
                        if device_subvendor != donor_subvendor:
                            validation_results.append(
                                ValidationResult(
                                    field="subvendor_id",
                                    expected=donor_subvendor,
                                    actual=device_subvendor,
                                    status="mismatch",
                                )
                            )
                    if device.subsystem_device and "subsystem_id" in donor_info:
                        device_subsystem = device.subsystem_device.lower().replace(
                            "0x", ""
                        )
                        donor_subsystem = (
                            donor_info["subsystem_id"].lower().replace("0x", "")
                        )
                        if device_subsystem != donor_subsystem:
                            validation_results.append(
                                ValidationResult(
                                    field="subsystem_id",
                                    expected=donor_subsystem,
                                    actual=device_subsystem,
                                    status="mismatch",
                                )
                            )
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
                                    "PCI mismatch: {field} - expected {exp}, got {act}",
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
            elif not config.local_build and config.donor_dump:
                if self._current_progress:
                    self._current_progress.current_operation = (
                        "PCI validation will be performed during donor extraction"
                    )
                    await self._notify_progress()
        except ImportError as e:
            self._report_exception("Failed to validate PCI configuration", e)
            await self._notify_progress()
        except Exception as e:  # pragma: no cover
            self._report_exception("Failed to validate PCI configuration", e)
            await self._notify_progress()

    async def _analyze_device(self, device: PCIDevice) -> None:
        current_driver = await asyncio.get_event_loop().run_in_executor(
            None, self._get_current_driver_safe, device.bdf
        )
        iommu_group = await asyncio.get_event_loop().run_in_executor(
            None, self._get_iommu_group_safe, device.bdf
        )
        vfio_device = f"/dev/vfio/{iommu_group}"
        if not os.path.exists(vfio_device) and self._current_progress:
            self._current_progress.add_warning(
                safe_format("Missing VFIO device {dev}", dev=vfio_device)
            )

    def _get_current_driver_safe(self, bdf: str) -> str:
        try:
            from ...cli.vfio import get_current_driver

            value = get_current_driver(bdf)
            return value if isinstance(value, str) else ""
        except Exception:
            return ""

    def _get_iommu_group_safe(self, bdf: str) -> str:
        try:
            from ...cli.vfio_handler import _get_iommu_group

            value = _get_iommu_group(bdf)
            return value if isinstance(value, str) else ""
        except Exception:
            return ""

    async def _extract_registers(self, device: PCIDevice) -> None:
        await asyncio.sleep(1)
        if self._current_progress:
            self._current_progress.current_operation = (
                f"Extracted registers from device {device.bdf}"
            )
            await self._notify_progress()

    async def _run_behavior_profiling(
        self, device: PCIDevice, config: BuildConfiguration
    ) -> None:
        import sys
        from pathlib import Path as _Path

        sys.path.append(str(_Path(__file__).parent.parent.parent))
        from device_clone.behavior_profiler import BehaviorProfiler

        if self._current_progress:
            self._current_progress.current_operation = safe_format(
                "Profiling device {bdf}", bdf=device.bdf
            )
            await self._notify_progress()

        def run_profiling():
            try:
                enable_ftrace = not config.disable_ftrace and os.geteuid() == 0
                profiler = BehaviorProfiler(
                    bdf=device.bdf, debug=True, enable_ftrace=enable_ftrace
                )
                profile = profiler.capture_behavior_profile(
                    duration=config.profile_duration
                )
                return profile
            except Exception as e:  # pragma: no cover
                if self._current_progress:
                    self._current_progress.add_error(
                        safe_format("Behavior profiling failed: {err}", err=str(e))
                    )
                return None

        loop = asyncio.get_running_loop()
        profile = await loop.run_in_executor(self._executor, run_profiling)
        if profile and self._current_progress:
            self._current_progress.current_operation = safe_format(
                "Analyzed {n} register accesses", n=profile.total_accesses
            )
            self._current_progress.add_warning(
                safe_format("Found {n} timing patterns", n=len(profile.timing_patterns))
            )
            self._current_progress.add_warning(
                safe_format(
                    "Identified {n} state transitions", n=len(profile.state_transitions)
                )
            )

    async def _generate_systemverilog(
        self, device: PCIDevice, config: BuildConfiguration
    ) -> None:
        await asyncio.sleep(2)
        if self._current_progress:
            self._current_progress.current_operation = (
                f"Generated SystemVerilog for device {device.bdf}"
            )
            await self._notify_progress()

    async def _run_vivado_synthesis(
        self, device: PCIDevice, config: BuildConfiguration
    ) -> None:
        cli_args: Dict[str, Any] = {
            "advanced_sv": bool(config.advanced_sv),
            "enable_variance": bool(getattr(config, "enable_variance", False)),
            "enable_behavior_profiling": bool(config.behavior_profiling),
            "behavior_profile_duration": float(config.profile_duration),
            "use_donor_dump": bool(config.donor_dump),
            "donor_info_file": config.donor_info_file,
            "skip_board_check": bool(getattr(config, "skip_board_check", False)),
        }
        build_cmd_parts: List[str] = [
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
        if cli_args.get("use_donor_dump"):
            build_cmd_parts.append("--use-donor-dump")
        donor_info_file = cli_args.get("donor_info_file")
        if (
            donor_info_file
            and isinstance(donor_info_file, str)
            and donor_info_file.strip()
        ):
            build_cmd_parts.append(f"--donor-info-file {donor_info_file}")
        if cli_args.get("skip_board_check"):
            build_cmd_parts.append("--skip-board-check")
        build_cmd_parts.append("--run-vivado")
        build_cmd = " ".join(build_cmd_parts)
        if config.local_build:
            if self._current_progress:
                self._current_progress.current_operation = "Running local build"
                await self._notify_progress()
            await self._run_shell(build_cmd.split())
        else:
            from ...cli.vfio_handler import _get_iommu_group

            iommu_group = await asyncio.get_running_loop().run_in_executor(
                None, _get_iommu_group, device.bdf
            )
            vfio_device = f"/dev/vfio/{iommu_group}"
            container_cmd: List[str] = [
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
                    f"python3 /app/src/build.py --bdf {device.bdf} --board {config.board_type}"
                ),
            ]
            for option in build_cmd_parts[1:]:
                container_cmd.append(option)
            await self._run_shell(container_cmd)

    async def _generate_bitstream(self, config: BuildConfiguration) -> None:
        await asyncio.sleep(1)
        if self._current_progress:
            self._current_progress.current_operation = "Bitstream generation complete"
            await self._notify_progress()

    # ---------- log/history helpers ----------
    def _append_log_line(self, line: str) -> None:
        try:
            self._current_log_lines.append(line)
            if len(self._current_log_lines) > 5000:
                self._current_log_lines = self._current_log_lines[-5000:]
        except Exception:
            pass

    def _record_build_history(
        self, *, status: str, error: Optional[str] = None
    ) -> None:
        try:
            start = self._build_start_time or datetime.datetime.now()
            end = datetime.datetime.now()
            duration = max((end - start).total_seconds(), 0.0)
            entry: Dict[str, Any] = {
                "timestamp": start.isoformat(),
                "device": getattr(self._current_device, "bdf", "unknown"),
                "board": getattr(self._current_config, "board_type", "unknown"),
                "status": status,
                "duration": f"{duration:.1f}s",
            }
            if error:
                entry["error"] = error
            self._build_history.append(entry)
            if len(self._build_history) > 50:
                self._build_history = self._build_history[-50:]
        except Exception:
            pass

    def get_current_build_log(self) -> List[str]:
        try:
            return list(self._current_log_lines)
        except Exception:
            return []

    def get_build_history(self) -> List[Dict[str, Any]]:
        try:
            return list(self._build_history)
        except Exception:
            return []
