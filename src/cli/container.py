#!/usr/bin/env python3
"""container_build - unified VFIO‑aware Podman build runner"""
from __future__ import annotations

import json
import os
import re
import shutil
import subprocess
import sys
import textwrap
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import List, Optional, Sequence

from ..device_clone.constants import (
    PRODUCTION_DEFAULTS,
)  # Central production feature toggles

from ..exceptions import (
    BuildError,
    ConfigurationError,
    PCILeechBuildError,
    VFIOBindError,
    is_platform_error,
)

from ..log_config import get_logger

from ..shell import Shell

# Import safe logging functions
from ..string_utils import (
    log_debug_safe,
    log_error_safe,
    log_info_safe,
    log_warning_safe,
    safe_format,
)

from .build_constants import (
    DEFAULT_ACTIVE_INTERRUPT_MODE,
    DEFAULT_ACTIVE_INTERRUPT_VECTOR,
    DEFAULT_ACTIVE_PRIORITY,
    DEFAULT_ACTIVE_TIMER_PERIOD,
    DEFAULT_BEHAVIOR_PROFILE_DURATION,
)

from .vfio import VFIOBinder  # auto‑fix & diagnostics baked in

from .vfio import get_current_driver, restore_driver

logger = get_logger(__name__)


# Lightweight indirection so tests can monkeypatch container._get_iommu_group


def _get_iommu_group(bdf: str) -> int:
    """Return IOMMU group id as int for the given BDF.

    Delegates to vfio_handler and normalizes the result to int to make
    tests and callsites simpler.
    """
    from .vfio_handler import _get_iommu_group as _impl

    gid = _impl(bdf)
    try:
        return int(gid)  # type: ignore[arg-type]
    except (TypeError, ValueError):
        # Ensure string conversion path still returns a valid int
        return int(str(gid))


# ──────────────────────────────────────────────────────────────────────────────
# Build configuration (thin wrapper over original)
# ──────────────────────────────────────────────────────────────────────────────



@dataclass
class BuildConfig:
    bdf: str
    board: str
    # feature toggles (defaults from PRODUCTION_DEFAULTS)
    advanced_sv: bool = PRODUCTION_DEFAULTS.get("ADVANCED_SV", False)
    enable_variance: bool = PRODUCTION_DEFAULTS.get("MANUFACTURING_VARIANCE", False)
    # disable_* flags are inverse of production defaults
    disable_power_management: bool = not PRODUCTION_DEFAULTS.get(
        "POWER_MANAGEMENT", False
    )
    disable_error_handling: bool = not PRODUCTION_DEFAULTS.get(
        "ERROR_HANDLING", False
    )
    disable_performance_counters: bool = not PRODUCTION_DEFAULTS.get(
        "PERFORMANCE_COUNTERS", False
    )
    behavior_profile_duration: int = DEFAULT_BEHAVIOR_PROFILE_DURATION
    # runtime toggles
    auto_fix: bool = True  # hand to VFIOBinder
    container_tag: str = "latest"
    container_image: str = "pcileechfwgenerator"
    # dynamic image resolution toggle
    dynamic_image: bool = False  # derive tag from project version & flags
    # fallback control options
    fallback_mode: str = "none"  # "none", "prompt", or "auto"
    allowed_fallbacks: List[str] = field(default_factory=list)
    denied_fallbacks: List[str] = field(default_factory=list)
    # active device configuration
    disable_active_device: bool = False
    active_timer_period: int = DEFAULT_ACTIVE_TIMER_PERIOD
    active_interrupt_mode: str = DEFAULT_ACTIVE_INTERRUPT_MODE
    active_interrupt_vector: int = DEFAULT_ACTIVE_INTERRUPT_VECTOR
    active_priority: int = DEFAULT_ACTIVE_PRIORITY
    # output options
    output_template: Optional[str] = None
    donor_template: Optional[str] = None
    # experimental/testing features
    enable_error_injection: bool = False
    # MMIO learning flags
    enable_mmio_learning: bool = True
    force_recapture: bool = False

    # ------------------------------------------------------------------
    # Image reference helpers
    # ------------------------------------------------------------------

    def _get_project_version(self) -> str:
        """Return project version (lightweight, no heavy imports on failure).

        Falls back to 'latest' if version cannot be determined. Only used
        when dynamic_image=True so normal path remains unchanged.
        """
        try:  # Try canonical version module
            from src import __version__ as v  # type: ignore

            return getattr(v, "__version__", "latest")
        except Exception:
            pass
        # Fallback: parse pyproject.toml for version line (cheap scan)
        try:
            pyproj = Path(__file__).parent.parent.parent / "pyproject.toml"
            if pyproj.exists():
                for line in pyproj.read_text().splitlines():
                    if line.strip().startswith("version ="):
                        # version = "1.2.3"
                        val = line.split("=", 1)[1].strip().strip("\"'")
                        if val:
                            return val
        except Exception:
            pass
        return "latest"

    def resolve_image_parts(self) -> tuple[str, str]:
        """Return (image, tag) possibly rewritten if dynamic_image enabled.

        In dynamic mode the tag encodes version and enabled feature flags to
        reduce unnecessary rebuilds across differing feature sets while still
        allowing caching for identical configurations.
        """
        if not self.dynamic_image:
            return self.container_image, self.container_tag

        version = self._get_project_version()
        # Sanitize version/tag components to allowed chars [A-Za-z0-9_.-]

        def _sanitize(component: str) -> str:
            return "".join(c for c in component if c.isalnum() or c in "._-") or "x"

        parts = [_sanitize(version)]
        if self.advanced_sv:
            parts.append("adv")
        if self.enable_variance:
            parts.append("var")
        tag = "-".join(parts)
        # Ensure length constraint from build_image validation (<=128)
        if len(tag) > 128:
            tag = tag[:128]
        return self.container_image, tag

    def cmd_args(self) -> List[str]:
        """Structured argument vector (no shell concatenation).

        Returns list suitable for subprocess without shell=True. Ordering stable.
        """
        args: List[str] = ["--bdf", self.bdf, "--board", self.board]

        # Only include flags that build.py actually understands today.
        # (Keep unsupported toggles internal; they'll be surfaced once the
        # main builder exposes corresponding CLI flags.)
        if self.advanced_sv:
            args.append("--advanced-sv")
        if self.enable_variance:
            args.append("--enable-variance")
        if self.behavior_profile_duration != DEFAULT_BEHAVIOR_PROFILE_DURATION:
            # 0 disables profiling per build.py help.
            args.extend(["--profile", str(self.behavior_profile_duration)])
        if self.output_template:
            args.extend(["--output-template", self.output_template])
        if self.donor_template:
            args.extend(["--donor-template", self.donor_template])
        if self.enable_error_injection:
            args.append("--enable-error-injection")
        return args

    def __post_init__(self) -> None:
        """Validate critical fields early (fail fast; no fallbacks)."""
        bdf_pattern = re.compile(
            r"^[0-9a-fA-F]{4}:[0-9a-fA-F]{2}:[0-9a-fA-F]{2}\.[0-7]$"
        )
        if not bdf_pattern.match(self.bdf):
            raise ConfigurationError(
                safe_format(
                    "Invalid BDF format: {bdf}. Expected format: DDDD:BB:DD.F",
                    bdf=self.bdf,
                )
            )
        # Enhanced board validation - check for non-empty string with content
        if (
            not self.board
            or not isinstance(self.board, str)
            or not self.board.strip()
        ):
            raise ConfigurationError(
                "Board name is required and cannot be empty. "
                "Use --board to specify a valid board configuration "
                "(e.g., pcileech_100t484_x1)"
            )


# ──────────────────────────────────────────────────────────────────────────────
# Helpers
# ──────────────────────────────────────────────────────────────────────────────


def check_podman_available() -> bool:
    """Check if Podman is available and working."""
    if shutil.which("podman") is None:
        return False

    # Try to run a simple podman command to check if it's working
    try:
        shell = Shell()
        shell.run("podman version", timeout=5)
        return True
    except RuntimeError:
        return False


def require_podman() -> None:
    if shutil.which("podman") is None:
        raise ConfigurationError("Podman not found - install it or adjust PATH")


def get_image_age_days(name: str) -> Optional[int]:
    """Get the age of a container image in days, or None if unable to determine."""
    try:
        result = subprocess.run(
            ["podman", "image", "inspect", name, "--format", "{{.Created}}"],
            capture_output=True,
            text=True,
        )
        if result.returncode != 0:
            return None
        
        created_str = result.stdout.strip()
        # Parse ISO format timestamp (e.g., "2024-12-01T10:30:00.123456789Z")
        from datetime import datetime, timezone
        # Handle formats with nanoseconds - truncate to microseconds
        for fmt in ["%Y-%m-%dT%H:%M:%S.%fZ", "%Y-%m-%dT%H:%M:%SZ", "%Y-%m-%dT%H:%M:%S"]:
            try:
                parse_str = created_str
                if "." in parse_str and len(parse_str.split(".")[-1].rstrip("Z")) > 6:
                    parts = parse_str.split(".")
                    parse_str = parts[0] + "." + parts[1][:6] + "Z"
                created = datetime.strptime(parse_str, fmt)
                if created.tzinfo is None:
                    created = created.replace(tzinfo=timezone.utc)
                now = datetime.now(timezone.utc)
                return (now - created).days
            except ValueError:
                continue
        return None
    except Exception:
        return None


def image_exists(name: str) -> bool:
    try:
        shell = Shell()
        out = shell.run(
            "podman images --format '{{.Repository}}:{{.Tag}}'",
            timeout=5,
        )
        target = name.strip()
        return any(line.strip() == target for line in out.splitlines())
    except RuntimeError as e:
        # If podman fails to connect, return False
        if "Cannot connect to Podman" in str(e) or "connection refused" in str(e):
            return False
        raise


def check_image_staleness(name: str) -> None:
    """Check if image exists and warn if it's potentially stale."""
    if not image_exists(name):
        return
    
    age_days = get_image_age_days(name)
    if age_days is not None and age_days > 7:
        log_warning_safe(
            logger,
            safe_format(
                "Container image '{img}' is {days} days old. "
                "If you experience issues, rebuild with: "
                "podman system prune -a -f && podman build -t {img} -f Containerfile .",
                img=name,
                days=age_days,
            ),
            prefix="BUILD",
        )
    else:
        log_info_safe(
            logger,
            safe_format("Using existing container image: {img}", img=name),
            prefix="BUILD",
        )


def build_image(name: str, tag: str) -> None:
    """Build podman image with stricter validation & no shell invocation."""
    import re

    # Repository/name (enforce lowercase per OCI/Docker convention)
    name_pattern = r"[a-z0-9]+(?:[._-][a-z0-9]+)*(?:/[a-z0-9]+(?:[._-][a-z0-9]+)*)*"
    if not re.fullmatch(name_pattern, name):
        raise ConfigurationError(f"Invalid container image name: {name}")
    if not re.fullmatch(r"[A-Za-z0-9_.-]{1,128}", tag):
        raise ConfigurationError(f"Invalid container tag: {tag}")

    log_info_safe(
        logger,
        "Building container image {name}:{tag}",
        name=name,
        tag=tag,
    )
    cmd = ["podman", "build", "-t", f"{name}:{tag}", "-f", "Containerfile", "."]
    subprocess.run(cmd, check=True)


def _build_podman_command(
    cfg: BuildConfig, group_dev: str, output_dir: Path
) -> Sequence[str]:
    """Construct podman run command as argument vector (no shell=True)."""
    uname_release = os.uname().release if hasattr(os, "uname") else ""
    kernel_headers = f"/lib/modules/{uname_release}/build" if uname_release else None
    cmd: List[str] = [
        "podman",
        "run",
        "--rm",
        "--privileged",
        f"--device={group_dev}",
        "--device=/dev/vfio/vfio",
        "--entrypoint",
        "python3",
        "--user",
        "root",
    ]
    # Mount output dir
    cmd.extend(["-v", f"{output_dir}:/app/output"])
    
    # Set environment variables for prefilled data paths
    cmd.extend(["-e", "DEVICE_CONTEXT_PATH=/app/output/device_context.json"])
    cmd.extend(["-e", "MSIX_DATA_PATH=/app/output/msix_data.json"])
    # Signal container to use host-collected context and avoid VFIO/sysfs operations
    cmd.extend(["-e", "PCILEECH_HOST_CONTEXT_ONLY=1"])
    
    # Mount kernel headers if present (Linux). On macOS this path won't exist.
    if kernel_headers and Path(kernel_headers).exists():
        cmd.extend(["-v", f"{kernel_headers}:/kernel-headers:ro"])
    else:
        log_warning_safe(
            logger,
            safe_format(
                "Kernel headers path missing; skipping mount (host={platform})",
                platform=sys.platform,
            ),
            prefix="CONT",
        )
    # Mount host debugfs to container to avoid privileged mount issues
    debugfs_path = "/sys/kernel/debug"
    if Path(debugfs_path).exists():
        cmd.extend(["-v", f"{debugfs_path}:{debugfs_path}:rw"])
        log_debug_safe(
            logger,
            safe_format(
                "Mounted host debugfs at {path}",
                path=debugfs_path,
            ),
            prefix="CONT",
        )
    else:
        log_debug_safe(
            logger,
            safe_format(
                "Host debugfs not available for mount at {path}",
                path=debugfs_path,
            ),
            prefix="CONT",
        )

    # Image (respect dynamic tagging if enabled)
    _image, _tag = cfg.resolve_image_parts()
    cmd.append(f"{_image}:{_tag}")
    # Python module invocation and build args
    cmd.extend(["-m", "src.build"])  # entrypoint already python3
    cmd.extend(cfg.cmd_args())
    return cmd


# ──────────────────────────────────────────────────────────────────────────────
# Public façade
# ──────────────────────────────────────────────────────────────────────────────


def prompt_user_for_local_build() -> bool:
    """Prompt user to confirm local build when Podman is unavailable.

    Preserve historical interactive behavior for backward‑compat tests.
    Only suppress prompting when explicitly signalled via NO_INTERACTIVE or CI.
    (Do NOT auto‑disable merely due to missing TTY; pytest monkeypatches input).
    """
    non_interactive = bool(
        os.environ.get("NO_INTERACTIVE") or os.environ.get("CI")
    )
    if non_interactive:
        log_error_safe(
            logger,
            "Interactive fallback disabled (NO_INTERACTIVE/CI) - aborting",
            prefix="BUILD",
        )
        return False

    # Minimal user guidance (kept intentionally terse per repo docs rules)
    print("Podman unavailable; optionally run local host build.")
    while True:
        try:
            response = input("Run local build? [y/N]: ").strip().lower()
        except EOFError:
            # Non‑interactive stdin fallback to default 'no'
            return False
        if response in ("y", "yes"):
            return True
        if response in ("n", "no", ""):
            return False
        print("Enter y or n.")


def run_local_build(cfg: BuildConfig) -> None:
    """Run build locally without container."""
    import sys

    log_info_safe(
        logger,
        safe_format(
            "Running local build - board={board}",
            board=cfg.board,
        ),
        prefix="LOCAL",
    )

    # Ensure output directory exists
    output_dir = Path.cwd() / "output"
    output_dir = output_dir.resolve()
    output_dir.mkdir(parents=True, exist_ok=True)

    # Add src to path if needed
    src_path = Path(__file__).parent.parent
    if str(src_path) not in sys.path:
        sys.path.insert(0, str(src_path))

    # Import build module
    try:
        from ..build import main as build_main
    except ImportError:
        # Try alternative import path
        sys.path.insert(0, str(Path(__file__).parent.parent.parent))
        try:
            from src.build import main as build_main
        except ImportError:
            log_error_safe(
                logger,
                "Failed to import build module - cannot run local build",
                prefix="LOCAL",
            )
            raise ImportError("Cannot run local build - missing build module")

    # Process the command arguments properly
    build_args = cfg.cmd_args()

    log_info_safe(
        logger,
        safe_format(
            "Executing local build with args: {args}",
            args=" ".join(build_args),
        ),
        prefix="LOCAL",
    )

    # Run the build
    start = time.time()
    try:
        result = build_main(build_args)
        if result != 0:
            raise BuildError(
                safe_format(
                    "Local build failed with exit code {result}", result=result
                )
            )

        elapsed = time.time() - start
        log_info_safe(
            logger,
            safe_format(
                "Local build completed in {elapsed:.1f}s ✓", elapsed=elapsed
            ),
            prefix="LOCAL",
        )
    except Exception as e:
        elapsed = time.time() - start
        # Check if this is a platform compatibility error (centralized helper)
        error_str = str(e)
        if is_platform_error(error_str):
            log_info_safe(
                logger,
                (
                    "Local build skipped due to platform incompatibility "
                    "(see details above)"
                ),
                prefix="LOCAL",
            )
            raise BuildError(
                f"Local build not supported on this platform: {error_str}"
            ) from e
        else:
            log_error_safe(
                logger,
                safe_format(
                    "Local build failed after {elapsed:.1f}s: {error}",
                    elapsed=elapsed,
                    error=error_str,
                ),
                prefix="LOCAL",
            )
            raise BuildError(f"Local build failed: {error_str}") from e


def run_build(cfg: BuildConfig) -> None:
    """
    DEPRECATED: High‑level orchestration: VFIO bind → container run → cleanup.
    
    This function is deprecated and will be removed in a future version.
    Use pcileech.py's unified 3-stage flow instead:
      1. pcileech.run_host_collect() - Host-side data collection
      2. pcileech.run_container_templating() - Container-based rendering
      3. pcileech.run_host_vivado() - Host-side Vivado synthesis
    
    For internal use only. External callers should use:
        python3 pcileech.py build --bdf ... --board ...
    """
    import warnings
    
    warnings.warn(
        "run_build() is deprecated. Use pcileech.py's unified flow instead. "
        "See UNIFIED_FLOW_IMPLEMENTATION.md for details.",
        DeprecationWarning,
        stacklevel=2,
    )
    # Resolve image and tag once, respect dynamic tagging if enabled
    resolved_image, resolved_tag = cfg.resolve_image_parts()
    # Check if Podman is available and working
    podman_available = check_podman_available()

    if not podman_available:
        log_warning_safe(
            logger,
            "Podman not available or cannot connect",
            prefix="BUILD",
        )

        non_interactive = bool(
            os.environ.get("NO_INTERACTIVE") or os.environ.get("CI")
        )
        if non_interactive:
            log_error_safe(
                logger,
                "Aborting (non-interactive mode) - no Podman available",
                prefix="BUILD",
            )
            sys.exit(2)

        # Prompt user for local build (interactive only)
        if prompt_user_for_local_build():
            run_local_build(cfg)
        else:
            log_info_safe(
                logger,
                "Build cancelled by user",
                prefix="BUILD",
            )
            sys.exit(1)
        return

    # Try container build first
    try:
        require_podman()
        # Always rebuild container to ensure latest code
        image_name = f"{resolved_image}:{resolved_tag}"
        if image_exists(image_name):
            log_info_safe(
                logger,
                safe_format("Removing old container image: {img}", img=image_name),
                prefix="BUILD",
            )
            try:
                subprocess.run(
                    ["podman", "rmi", "-f", image_name],
                    check=True,
                    capture_output=True,
                )
            except subprocess.CalledProcessError:
                # Ignore removal errors, build will overwrite anyway
                pass
        build_image(resolved_image, resolved_tag)
    except (ConfigurationError, RuntimeError) as e:
        if "Cannot connect to Podman" in str(e) or "connection refused" in str(e):
            log_warning_safe(
                logger,
                safe_format(
                    "Podman connection failed: {error}",
                    error=str(e),
                ),
                prefix="BUILD",
            )

            non_interactive = bool(
                os.environ.get("NO_INTERACTIVE") or os.environ.get("CI")
            )
            if non_interactive:
                log_error_safe(
                    logger,
                    "Aborting (non-interactive mode) - Podman connection failed",
                    prefix="BUILD",
                )
                sys.exit(2)
            # Prompt user for local build (interactive only)
            if prompt_user_for_local_build():
                run_local_build(cfg)
            else:
                log_info_safe(
                    logger,
                    "Build cancelled by user",
                    prefix="BUILD",
                )
                sys.exit(1)
            return
        raise

    # Ensure host output dir exists and is absolute
    output_dir = (Path.cwd() / "output").resolve()
    output_dir.mkdir(parents=True, exist_ok=True)

    # Save original driver before any VFIO operations
    original_driver = get_current_driver(cfg.bdf)
    log_info_safe(
        logger,
        safe_format(
            "Device {bdf} original driver: {driver}",
            bdf=cfg.bdf,
            driver=original_driver or "none",
        ),
        prefix="HOST",
    )

    try:
        # Collect complete device context on host using single VFIO binding
        from .host_device_collector import HostDeviceCollector

        # Be tolerant of older/mocked collector signatures in tests by
        # retrying without keyword extras if not supported.
        try:
            collector = HostDeviceCollector(
                cfg.bdf,
                logger,
                enable_mmio_learning=cfg.enable_mmio_learning,
                force_recapture=cfg.force_recapture,
            )
        except TypeError:
            # Fallback to legacy signature: (bdf, logger)
            collector = HostDeviceCollector(cfg.bdf, logger)
        try:
            # Collect all device info (MSI-X, config space, BARs, etc.) in one go
            # Returns collected_data dict saved to output_dir for container use
            collected_data = collector.collect_device_context(output_dir)
            
            log_info_safe(
                logger,
                "Complete device context collected on host - "
                "container will use prefilled data",
                prefix="HOST",
            )
        except BuildError as e:
            log_error_safe(
                logger,
                safe_format(
                    "Host device collection failed: {error}",
                    error=str(e)
                ),
                prefix="HOST",
            )
            raise

        # Get the group device path as a string (safe, just a string)
        group_id = _get_iommu_group(cfg.bdf)
        group_dev = f"/dev/vfio/{group_id}"

        # Harden preflight: ensure group device exists and group is fully bound
        try:
            from pathlib import Path as _Path
            
            from ..string_utils import log_error_safe as _les

            # 1) Verify VFIO container device exists on host
            if not _Path("/dev/vfio/vfio").exists():
                _les(
                    logger,
                    safe_format(
                        "Missing host VFIO control device {path}. "
                        "Ensure vfio is loaded (vfio, vfio_pci).",
                        path="/dev/vfio/vfio",
                    ),
                    prefix="BUILD",
                )
                raise BuildError(
                    "VFIO not available on host: /dev/vfio/vfio missing"
                )

            # 2) Verify group device exists on host at launch-time
            if not _Path(group_dev).exists():
                _les(
                    logger,
                    safe_format(
                        "VFIO group device {group_dev} not found. "
                        "Bind ALL devices in IOMMU group {group} to vfio-pci "
                        "and retry.",
                        group_dev=group_dev,
                        group=group_id,
                    ),
                    prefix="BUILD",
                )
                # Provide actionable guidance
                _les(
                    logger,
                    safe_format(
                        "Hint: Check /sys/kernel/iommu_groups/{group}/devices "
                        "and ensure each device is bound to vfio-pci.",
                        group=group_id,
                    ),
                    prefix="BUILD",
                )
                raise BuildError(
                    safe_format("Missing VFIO group device: {gd}", gd=group_dev)
                )

            # 3) Verify all devices in the IOMMU group are bound to vfio-pci
            devices_dir = _Path(
                safe_format(
                    "/sys/kernel/iommu_groups/{group}/devices", group=group_id
                )
            )
            if devices_dir.exists():
                unbound: list[str] = []
                wrong: list[str] = []
                for dev in sorted(p.name for p in devices_dir.iterdir()):
                    drv_link = _Path(f"/sys/bus/pci/devices/{dev}/driver")
                    if drv_link.exists():
                        try:
                            drv = drv_link.resolve().name
                            if drv != "vfio-pci":
                                wrong.append(f"{dev}→{drv}")
                        except Exception:
                            unbound.append(dev)
                    else:
                        unbound.append(dev)
                if unbound or wrong:
                    msg_lines = [
                        safe_format(
                            "IOMMU group {group} not fully vfio-pci bound",
                            group=group_id,
                        )
                    ]
                    if unbound:
                        msg_lines.append(safe_format("Unbound: {u}", u=unbound))
                    if wrong:
                        msg_lines.append(safe_format("Wrong driver: {w}", w=wrong))
                    _les(
                        logger,
                        "\n".join(msg_lines)
                        + "\nAll devices in a group must be bound to vfio-pci "
                        + "before starting the container.",
                        prefix="BUILD",
                    )
                    raise BuildError("IOMMU group not fully bound to vfio-pci")
            else:
                # If the directory is missing, keep going (some hosts differ),
                # container will still receive the device
                log_warning_safe(
                    logger,
                    safe_format(
                        "IOMMU group devices dir missing: {path}",
                        path=str(devices_dir),
                    ),
                    prefix="BUILD",
                )
        except BuildError:
            raise
        except Exception as _e:
            # Non-fatal preflight anomaly; log as warning and proceed
            log_warning_safe(
                logger,
                safe_format("Preflight anomaly (non-fatal): {err}", err=str(_e)),
                prefix="BUILD",
            )

        log_info_safe(
            logger,
            safe_format(
                "Launching build container - board={board}, tag={tag}",
                board=cfg.board,
                tag=resolved_tag,
            ),
            prefix="CONT",
        )

        podman_cmd_vec = _build_podman_command(cfg, group_dev, output_dir)
        log_debug_safe(
            logger,
            safe_format(
                "Container command (argv): {cmd}",
                cmd=" ".join(podman_cmd_vec),
            ),
            prefix="CONT",
        )
        start = time.time()
        try:
            # Capture output to surface container errors on failure
            result = subprocess.run(
                podman_cmd_vec,
                capture_output=True,
                text=True,
            )
            # Stream output to user's console for visibility only on success
            if result.returncode == 0:
                if result.stdout:
                    for line in result.stdout.splitlines():
                        log_info_safe(logger, line, prefix="CONT")
                if result.stderr:
                    for line in result.stderr.splitlines():
                        # Use warning level for stderr to distinguish from stdout
                        log_warning_safe(logger, line, prefix="CONT")
            # Check return code after logging output
            if result.returncode != 0:
                raise subprocess.CalledProcessError(
                    result.returncode,
                    podman_cmd_vec,
                    output=result.stdout,
                    stderr=result.stderr,
                )
        except subprocess.CalledProcessError as e:
            # Log captured container output if available
            if hasattr(e, 'stdout') and e.stdout:
                log_error_safe(
                    logger,
                    "Container stdout:",
                    prefix="CONT",
                )
                for line in e.stdout.splitlines()[-50:]:  # Last 50 lines
                    log_error_safe(logger, f"  {line}", prefix="CONT")
            if hasattr(e, 'stderr') and e.stderr:
                log_error_safe(
                    logger,
                    "Container stderr:",
                    prefix="CONT",
                )
                for line in e.stderr.splitlines()[-50:]:  # Last 50 lines
                    log_error_safe(logger, f"  {line}", prefix="CONT")
            # Enrich error when VFIO device nodes are not present inside container
            try:
                # Quick probe: if container failed and host devices exist,
                # surface a helpful hint
                if (not os.path.exists(group_dev) or
                        not os.path.exists("/dev/vfio/vfio")):
                    log_error_safe(
                        logger,
                        safe_format(
                            "Container failed and VFIO devices were not present: "
                            "gd={gd} vfio={vfio}",
                            gd=group_dev,
                            vfio="/dev/vfio/vfio",
                        ),
                        prefix="CONT",
                    )
            except Exception:
                pass
            raise BuildError(f"Build failed (exit {e.returncode})") from e
        except KeyboardInterrupt:
            log_warning_safe(
                logger,
                "Build interrupted by user - cleaning up...",
                prefix="CONT",
            )
            # Get the container ID if possible
            try:
                container_id = (
                    # Use the actual configured image:tag instead of a hardcoded name
                    subprocess.check_output(
                        [
                            "podman",
                            "ps",
                            "-q",
                            "--filter",
                            f"ancestor={resolved_image}:{resolved_tag}",
                        ]
                    )
                    .decode()
                    .strip()
                )
                if container_id:
                    log_info_safe(
                        logger,
                        safe_format(
                            "Stopping container {container_id}",
                            container_id=container_id,
                        ),
                        prefix="CONT",
                    )
                    # Best‑effort stop; no shell required
                    subprocess.run(["podman", "stop", container_id], check=False)
            except Exception as e:
                log_warning_safe(
                    logger,
                    safe_format(
                        "Failed to clean up container: {error}",
                        error=str(e),
                    ),
                    prefix="CONT",
                )

            # Ensure VFIO cleanup
            try:
                if cfg.bdf:  # This is sufficient since bdf is a required field
                    log_info_safe(
                        logger,
                        safe_format(
                            "Ensuring VFIO cleanup for device {bdf}",
                            bdf=cfg.bdf,
                        ),
                        prefix="CLEA",
                    )
                    # Get original driver if possible
                    try:
                        original_driver = get_current_driver(cfg.bdf)
                        restore_driver(cfg.bdf, original_driver)
                    except Exception:
                        # Just try to unbind from vfio-pci
                        try:
                            with open(
                                f"/sys/bus/pci/drivers/vfio-pci/unbind", "w"
                            ) as f:
                                f.write(f"{cfg.bdf}\n")
                        except Exception:
                            pass
            except Exception as e:
                log_warning_safe(
                    logger,
                    safe_format(
                        "VFIO cleanup after interrupt failed: {error}",
                        error=str(e),
                    ),
                    prefix="CLEA",
                )

            raise KeyboardInterrupt("Build interrupted by user")
        duration = time.time() - start
        log_info_safe(
            logger,
            safe_format(
                "Build completed in {duration:.1f}s",
                duration=duration,
            ),
            prefix="CONT",
        )
    except VFIOBindError as e:
        # VFIO binding failed, diagnostics have already been run
        log_error_safe(
            logger,
            safe_format(
                "Build aborted due to VFIO issues: {error}",
                error=str(e),
            ),
            prefix="VFIO",
        )
        from .vfio_diagnostics import Diagnostics, render

        # Run diagnostics one more time to ensure user sees the report
        diag = Diagnostics(cfg.bdf)
        report = diag.run()
        if not report.can_proceed:
            log_error_safe(
                logger,
                (
                    "VFIO diagnostics indicate system is not ready for VFIO "
                    "operations"
                ),
                prefix="VFIO",
            )
            log_error_safe(
                logger,
                "Please fix the issues reported above and try again",
                prefix="VFIO",
            )
        sys.exit(1)
    except (BuildError, PCILeechBuildError):
        # Re-raise known build errors as-is
        raise
    except Exception as e:
        # Wrap unexpected errors
        raise BuildError(f"Unexpected build failure: {str(e)}") from e
    finally:
        # Always restore original driver after container completes or fails
        if original_driver:
            log_info_safe(
                logger,
                safe_format(
                    "Restoring device {bdf} to original driver {driver}",
                    bdf=cfg.bdf,
                    driver=original_driver,
                ),
                prefix="CLEA",
            )
            try:
                restore_driver(cfg.bdf, original_driver)
                log_info_safe(
                    logger,
                    safe_format(
                        "Successfully restored {bdf} to {driver}",
                        bdf=cfg.bdf,
                        driver=original_driver,
                    ),
                    prefix="CLEA",
                )
            except Exception as e:
                log_warning_safe(
                    logger,
                    safe_format(
                        "Failed to restore driver: {error}",
                        error=str(e),
                    ),
                    prefix="CLEA",
                )
