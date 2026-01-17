"""
Build operations with integrated graceful degradation.

This module implements build operations with graceful degradation to ensure
the application can continue functioning even if specific operations fail.
"""

import asyncio
import logging
import time
from typing import Any, Dict, List, Optional, Tuple

from pcileechfwgenerator.tui.core.protocols import BuildOrchestrator, ConfigManager
from pcileechfwgenerator.tui.models.config import BuildConfiguration, BuildProgress
from pcileechfwgenerator.tui.utils.graceful_degradation import GracefulDegradation
from pcileechfwgenerator.tui.utils.input_validator import InputValidator

# Set up logging
logger = logging.getLogger(__name__)


class BuildOperations:
    """Handles build operations with graceful degradation."""

    def __init__(
        self,
        build_orchestrator: BuildOrchestrator,
        config_manager: ConfigManager,
        notify_callback,
    ):
        """
        Initialize the BuildOperations.

        Args:
            build_orchestrator: The build orchestrator to use.
            config_manager: The configuration manager to use.
            notify_callback: Callback function for notifications.
        """
        self.build_orchestrator = build_orchestrator
        self.config_manager = config_manager
        self.notify = notify_callback

        # Initialize graceful degradation
        self.graceful = GracefulDegradation(self)

        # Track build history
        self._build_history: List[Dict[str, Any]] = []
        self._current_build: Optional[BuildProgress] = None

    async def start_build(self, config: BuildConfiguration) -> bool:
        """
        Start a build with graceful degradation.

        Args:
            config: The build configuration to use.

        Returns:
            True if the build was started successfully, False otherwise.
        """
        return (
            await self.graceful.try_feature("build_start", self._start_build, config)
            or False
        )

    async def _start_build(self, config: BuildConfiguration) -> bool:
        """
        Internal implementation of start_build.

        Args:
            config: The build configuration to use.

        Returns:
            True if the build was started successfully, False otherwise.
        """
        # Validate configuration before starting build
        validation_result = self._validate_config(config)
        if not validation_result[0]:
            self.notify(
                f"Build configuration is invalid: {validation_result[1]}",
                severity="error",
            )
            return False

        # Generate a unique build ID
        build_id = f"build_{int(time.time())}"

        # Create build progress object
        self._current_build = BuildProgress(
            build_id=build_id,
            status="pending",
            progress=0.0,
            current_step="Initializing build",
            message="Build starting...",
            start_time=time.time(),
        )

        # Start the build
        success = await self.build_orchestrator.start_build(config.to_dict())

        if success:
            # Update build progress
            self._current_build.status = "running"
            self._current_build.message = "Build running..."

            # Notify user
            self.notify(f"Build {build_id} started", severity="info")

            # Start background monitoring
            asyncio.create_task(self._monitor_build(build_id))
        else:
            # Update build progress
            self._current_build.status = "failed"
            self._current_build.message = "Failed to start build"
            self._current_build.end_time = time.time()

            # Add to history
            self._build_history.append(self._current_build.to_dict())

            # Clear current build
            self._current_build = None

            # Notify user
            self.notify("Failed to start build", severity="error")

        return success

    def _validate_config(self, config: BuildConfiguration) -> Tuple[bool, str]:
        """
        Validate a build configuration using comprehensive validation.

        Args:
            config: The configuration to validate.

        Returns:
            A tuple of (is_valid, error_message) where is_valid is a boolean
            indicating if the configuration is valid, and error_message is a
            string describing any validation errors.
        """
        # Check for required fields with improved validation
        if not config.device_id:
            return False, "No device selected"

        # Validate BDF format if provided
        if config.device_id:
            is_valid, error = InputValidator.validate_bdf(config.device_id)
            if not is_valid:
                return is_valid, error

        # Validate board type is selected
        is_valid, error = InputValidator.validate_non_empty(
            config.board_type, "Board type"
        )
        if not is_valid:
            return is_valid, error

        # Validate output directory exists and is writable
        if not config.output_directory:
            return False, "No output directory specified"

        # Validate output directory path
        is_valid, error = InputValidator.validate_directory_path(
            config.output_directory
        )
        if not is_valid:
            return is_valid, error

        # Validate custom parameters if any
        if config.custom_parameters:
            for key, value in config.custom_parameters.items():
                # Validate string parameters
                if isinstance(value, str):
                    is_valid, error = InputValidator.validate_non_empty(value, key)
                    if not is_valid:
                        return is_valid, error

        # Configuration is valid
        return True, ""

    async def cancel_build(self) -> bool:
        """
        Cancel the current build with graceful degradation.

        Returns:
            True if the build was cancelled successfully, False otherwise.
        """
        return (
            await self.graceful.try_feature("build_cancel", self._cancel_build) or False
        )

    async def _cancel_build(self) -> bool:
        """
        Internal implementation of cancel_build.

        Returns:
            True if the build was cancelled successfully, False otherwise.
        """
        if not self._current_build:
            self.notify("No active build to cancel", severity="warning")
            return False

        # Cancel the build
        success = await self.build_orchestrator.cancel_build()

        if success:
            # Update build progress
            self._current_build.status = "cancelled"
            self._current_build.message = "Build cancelled by user"
            self._current_build.end_time = time.time()

            # Add to history
            self._build_history.append(self._current_build.to_dict())

            # Clear current build
            self._current_build = None

            # Notify user
            self.notify("Build cancelled", severity="info")
        else:
            # Notify user
            self.notify("Failed to cancel build", severity="error")

        return success

    async def get_build_status(self) -> Optional[BuildProgress]:
        """
        Get the current build status with graceful degradation.

        Returns:
            The current build progress, or None if there is no active build or
            if getting the status fails.
        """
        if not self._current_build:
            return None

        status = await self.graceful.try_feature("build_status", self._get_build_status)

        if status is None:
            return self._current_build

        return status

    async def _get_build_status(self) -> BuildProgress:
        """
        Internal implementation of get_build_status.

        Returns:
            The current build progress.

        Raises:
            ValueError: If there is no active build.
        """
        if not self._current_build:
            raise ValueError("No active build")

        # Get build status from orchestrator
        status_dict = await self.build_orchestrator.get_build_status()

        # Update build progress
        if "status" in status_dict:
            self._current_build.status = status_dict["status"]

        if "progress" in status_dict:
            self._current_build.progress = status_dict["progress"]

        if "message" in status_dict:
            self._current_build.message = status_dict["message"]

        if "current_step" in status_dict:
            self._current_build.current_step = status_dict["current_step"]

        if "logs" in status_dict:
            self._current_build.logs = status_dict["logs"]

        if "errors" in status_dict:
            self._current_build.errors = status_dict["errors"]

        if "warnings" in status_dict:
            self._current_build.warnings = status_dict["warnings"]

        # Check if build is complete
        if self._current_build.is_complete and not self._current_build.end_time:
            self._current_build.end_time = time.time()

            # Add to history
            self._build_history.append(self._current_build.to_dict())

            # Notify user
            if self._current_build.is_successful:
                self.notify(
                    f"Build {self._current_build.build_id} completed successfully",
                    severity="info",
                )
            else:
                self.notify(
                    f"Build {self._current_build.build_id} failed", severity="error"
                )

        return self._current_build

    async def _monitor_build(self, build_id: str) -> None:
        """
        Monitor a build in the background.

        Args:
            build_id: ID of the build to monitor.
        """
        try:
            while (
                self._current_build
                and self._current_build.build_id == build_id
                and not self._current_build.is_complete
            ):
                # Get build status
                await self._get_build_status()

                # Wait a short time before checking again
                await asyncio.sleep(1.0)
        except Exception as e:
            logger.exception(f"Error monitoring build {build_id}")

            if self._current_build and self._current_build.build_id == build_id:
                # Update build progress
                self._current_build.status = "failed"
                self._current_build.message = f"Monitoring failed: {str(e)}"
                self._current_build.end_time = time.time()

                # Add to history
                self._build_history.append(self._current_build.to_dict())

                # Notify user
                self.notify(
                    f"Failed to monitor build {build_id}: {str(e)}", severity="error"
                )

    def get_build_history(self) -> List[Dict[str, Any]]:
        """
        Get the build history.

        Returns:
            A list of build history entries.
        """
        return self._build_history.copy()
