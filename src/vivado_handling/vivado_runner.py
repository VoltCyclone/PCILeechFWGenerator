#!/usr/bin/env python3
"""
VivadoRunner: Simplified Vivado Integration

A streamlined class that handles Vivado execution with minimal overhead,
designed to replace the complex container-based approach.
"""

import logging

import os

from pathlib import Path

from typing import Any, Dict, Optional

from src.string_utils import log_info_safe, log_warning_safe, safe_format

from ..log_config import get_logger

from .ip_lock_resolver import repair_ip_artifacts


class VivadoIntegrationError(Exception):
    """Exception raised when Vivado integration fails."""

    pass


class VivadoRunner:
    """
    Handles everything Vivado SIMPLY

    Attributes:
        board: current target device
        output_dir: dir for generated vivado project
        vivado_path: root path to xilinx vivado installation
            (all paths derived from here)
        logger: attach a logger
    """

    def __init__(
        self,
        board: str,
        output_dir: Path,
        vivado_path: str,
        logger: Optional[logging.Logger] = None,
        device_config: Optional[Dict[str, Any]] = None,
        prefix: str = "VIVADO",
    ):
        """Initialize VivadoRunner with simplified configuration.

        Args:
            board: Target board name (e.g., "pcileech_35t325_x1")
            output_dir: Directory for generated Vivado project
            vivado_path: Root path to Xilinx Vivado installation
            logger: Optional logger instance
            device_config: Optional device configuration dictionary
        """
        self.logger: logging.Logger = logger or get_logger(self.__class__.__name__)
        self.board: str = board
        self.output_dir: Path = Path(output_dir)
        self.vivado_path: str = vivado_path
        self.device_config: Optional[Dict[str, Any]] = device_config

        # Derive paths from vivado_path
        self.vivado_executable: str = f"{self.vivado_path}/bin/vivado"
        self.vivado_bin_dir: str = f"{self.vivado_path}/bin"

        # Extract version from path (simple heuristic)
        self.vivado_version: str = self._extract_version_from_path(vivado_path)
        self.prefix: str = prefix

    def _extract_version_from_path(self, path: str) -> str:
        """Extract Vivado version from installation path."""
        # Look for version pattern like /tools/Xilinx/2025.1/Vivado
        import re

        version_match = re.search(r"(\d{4}\.\d+)", path)
        if version_match:
            return version_match.group(1)
        return "unknown"

    def _is_running_in_container(self) -> bool:
        """Check if we're running inside a container."""
        # Check for common container indicators
        container_indicators = [
            "/.dockerenv",
            "/run/.containerenv",
        ]

        for indicator in container_indicators:
            if Path(indicator).exists():
                return True

        # Check /proc/1/environ for container markers
        try:
            with open("/proc/1/environ", "rb") as f:
                environ = f.read().decode("utf-8", errors="ignore")
                if "container=podman" in environ or "container=docker" in environ:
                    return True
        except (OSError, IOError):
            pass

        return False

    def _run_vivado_on_host(self) -> None:
        """Drop out of container and run Vivado on the host system."""
        log_info_safe(
            self.logger,
            "Dropping out of container to run Vivado on host",
            prefix=self.prefix,
        )

        # Prepare the host command to run Vivado
        host_output_dir = Path("/app/output")  # This should be mounted from host
        host_vivado_path = os.environ.get(
            "HOST_VIVADO_PATH", "/tools/Xilinx/2025.1/Vivado"
        )

        # Create a script that the host can execute
        host_script = host_output_dir / "run_vivado_on_host.sh"

        script_content = safe_format(
            """#!/bin/bash
set -e

echo "Running Vivado on host system"
echo "Vivado path: {host_vivado_path}"
echo "Output directory: {host_output_dir}"
echo "Board: {self.board}"

# Change to output directory
cd {host_output_dir}

# Run Vivado with the generated scripts
{host_vivado_path}/bin/vivado -mode batch -source vivado_build.tcl

echo "Vivado synthesis completed on host"
""",
            host_vivado_path=host_vivado_path,
            host_output_dir=str(host_output_dir),
            self=self,
        )

        try:
            with open(host_script, "w") as f:
                f.write(script_content)

            # Make script executable (owner only)
            os.chmod(host_script, 0o700)

            log_info_safe(
                self.logger,
                safe_format(
                    "Created host execution script: {path}",
                    path=str(host_script),
                ),
                prefix=self.prefix,
            )
            log_info_safe(
                self.logger,
                "To complete Vivado synthesis, run this on the host:",
                prefix=self.prefix,
            )

            raise VivadoIntegrationError(
                safe_format(
                    "Container detected. Vivado must be run on host. "
                    "Please execute: {host_script}",
                    host_script=str(host_script),
                )
            )

        except Exception as e:
            raise VivadoIntegrationError(
                safe_format(
                    "Failed to create host execution script: {err}",
                    err=str(e),
                )
            )

    def run(self) -> None:
        """
        Hand-off to Vivado in batch mode using the generated scripts.
        If running in container, drop out to host for Vivado execution.

        Raises:
            VivadoIntegrationError: If Vivado integration fails
        """
        # Ensure IP artifacts are writable/unlocked before Vivado touches them
        self._prepare_ip_artifacts()

        # Check if we're running in a container
        if self._is_running_in_container():
            log_info_safe(
                self.logger,
                "Container detected - dropping out to host for Vivado execution",
                prefix=self.prefix,
            )
            self._run_vivado_on_host()
            return

        log_info_safe(
            self.logger,
            safe_format(
                "Starting Vivado build for board: {board}",
                board=self.board,
            ),
            prefix=self.prefix,
        )
        log_info_safe(
            self.logger,
            safe_format(
                "Output directory: {dir}",
                dir=str(self.output_dir),
            ),
            prefix=self.prefix,
        )

        # Import these functions dynamically to avoid circular dependencies
        try:
            from .pcileech_build_integration import integrate_pcileech_build

            from .vivado_error_reporter import run_vivado_with_error_reporting
        except ImportError as e:
            raise VivadoIntegrationError(
                "Vivado handling modules not available"
            ) from e

        try:
            # Use integrated build if available
            build_script = integrate_pcileech_build(
                self.board,
                self.output_dir,
                device_config=self.device_config,
            )
            log_info_safe(
                self.logger,
                safe_format(
                    "Using integrated build script: {path}",
                    path=str(build_script),
                ),
                prefix=self.prefix,
            )
            build_tcl = build_script
        except Exception as e:
            log_warning_safe(
                self.logger,
                safe_format(
                    "Failed to use integrated build; "
                    "falling back to generated scripts: {err}",
                    err=str(e),
                ),
                prefix=self.prefix,
            )
            build_tcl = self.output_dir / "vivado_build.tcl"

            # Ensure fallback script exists
            if not build_tcl.exists():
                raise VivadoIntegrationError(
                    safe_format(
                        "No build script found at {build_tcl}. Cannot proceed.",
                        build_tcl=build_tcl,
                    )
                )

        # Execute Vivado with comprehensive error reporting
        return_code, report = run_vivado_with_error_reporting(
            build_tcl,
            self.output_dir,
            self.vivado_executable,
        )

        if return_code != 0:
            raise VivadoIntegrationError(
                safe_format(
                    "Vivado build failed with return code {return_code}. "
                    "See error report: {report}",
                    return_code=return_code,
                    report=report,
                )
            )

        log_info_safe(
            self.logger,
            "Vivado implementation finished successfully ✓",
            prefix=self.prefix,
        )

    def _prepare_ip_artifacts(self) -> None:
        """Repair stale lock files or permissions inside the output tree."""
        try:
            repair_ip_artifacts(
                self.output_dir,
                logger=self.logger,
                prefix=self.prefix,
            )
        except Exception as exc:  # pragma: no cover - best effort safeguard
            log_warning_safe(
                self.logger,
                safe_format(
                    "Failed to preflight IP artifacts: {err}",
                    err=str(exc),
                ),
                prefix=self.prefix,
            )

    def get_vivado_info(self) -> Dict[str, str]:
        """Get information about the Vivado installation.

        Returns:
            Dictionary with Vivado installation details
        """
        return {
            "executable": self.vivado_executable,
            "bin_dir": self.vivado_bin_dir,
            "version": self.vivado_version,
            "installation_path": self.vivado_path,
        }


def create_vivado_runner(
    board: str,
    output_dir: Path,
    vivado_path: str,
    device_config: Optional[Dict[str, Any]] = None,
    logger: Optional[logging.Logger] = None,
) -> "VivadoRunner":
    """Factory function to create a VivadoRunner instance."""
    return VivadoRunner(
        board=board,
        output_dir=output_dir,
        vivado_path=vivado_path,
        device_config=device_config,
        logger=logger,
    )
