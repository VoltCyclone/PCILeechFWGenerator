"""
Build Manager for PCILeech TUI application.

This module provides services for validating and building firmware for devices.
"""

import asyncio
import os
import time
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple, Union

from ..models.device import PCIDevice
from ..models.progress import BuildProgress, BuildStage


class BuildManager:
    """Manages the firmware build process."""

    def __init__(self, app) -> None:
        """
        Initialize the build manager.

        Args:
            app: The main application instance
        """
        self.app = app

    async def validate_configuration(self, device: PCIDevice) -> Tuple[bool, List[str]]:
        """
        Validate the device configuration before building firmware.

        Args:
            device: The device to validate configuration for

        Returns:
            A tuple containing:
                - bool: True if the configuration is valid, False otherwise
                - List[str]: List of validation messages
        """
        validation_messages = []

        # Check if device is suitable for firmware generation
        if not device.is_suitable:
            validation_messages.append("Device is not suitable for firmware generation")
            for issue in device.compatibility_issues:
                validation_messages.append(f"  - {issue}")
            return False, validation_messages

        # Check template options if template system is used
        if hasattr(device, "template_options") and device.template_options:
            # Validate required template options
            template_system = (
                self.app.template_system
                if hasattr(self.app, "template_system")
                else None
            )

            if template_system:
                template = template_system.get_template_for_device(device)
                if template:
                    for option in template.options:
                        if option.required and not device.get_template_option_value(
                            option.name
                        ):
                            validation_messages.append(
                                f"Required option '{option.name}' is not set"
                            )

        # All checks passed if no validation messages
        is_valid = len(validation_messages) == 0

        if is_valid:
            validation_messages.append("Configuration is valid and ready for build")

        return is_valid, validation_messages

    async def build_firmware(
        self, device: PCIDevice, output_path: str
    ) -> Tuple[bool, Union[Dict[str, str], str]]:
        """
        Build firmware for a device.

        Args:
            device: The device to build firmware for
            output_path: The path where firmware files should be saved

        Returns:
            A tuple containing:
                - bool: True if build was successful, False otherwise
                - Union[Dict[str, str], str]:
                    If successful, a dictionary of artifacts (file paths)
                    If unsuccessful, an error message
        """
        try:
            # Create output directory if it doesn't exist
            os.makedirs(output_path, exist_ok=True)

            # Validate configuration first
            is_valid, validation_messages = await self.validate_configuration(device)
            if not is_valid:
                return False, "\n".join(validation_messages)

            # Get configuration from the current app state
            config = self.app.current_config

            # Initialize build progress with the environment validation stage
            build_progress = BuildProgress(
                stage=BuildStage.ENVIRONMENT_VALIDATION,
                completion_percent=0.0,
                current_operation="Initializing build environment",
            )
            self.app.build_progress = build_progress

            # Update UI with initial progress
            self.app.ui_coordinator.update_build_progress_display()

            # do this inside the function to avoid circular imports
            try:
                from src.build import BuildConfiguration as CoreBuildConfig
                from src.build import FirmwareBuilder
            except ImportError as e:
                build_progress.add_error(f"Failed to import build system: {str(e)}")
                return False, f"Build system import failed: {str(e)}"

            # Start with environment validation stage
            build_progress.stage = BuildStage.ENVIRONMENT_VALIDATION
            build_progress.completion_percent = 10.0
            build_progress.current_operation = "Setting up build configuration"
            self.app.ui_coordinator.update_build_progress_display()

            # Prepare build arguments
            try:
                # Map TUI configuration to core build configuration
                core_args = {
                    "bdf": device.bdf,
                    "board": config.board_type,
                    "output_dir": output_path,
                }

                # Add optional parameters if configured
                if config.donor_info_file:
                    core_args["donor_info_file"] = config.donor_info_file

                if config.behavior_profiling:
                    core_args["profile_duration"] = config.profile_duration

                # Set build flags based on configuration
                if hasattr(config, "local_build") and config.local_build:
                    core_args["local_build"] = True

                if hasattr(config, "skip_board_check") and config.skip_board_check:
                    core_args["skip_board_check"] = True

                # Create the core build configuration
                core_config = CoreBuildConfig(**core_args)

                # Update progress
                build_progress.completion_percent = 15.0
                build_progress.current_operation = "Creating firmware builder"
                self.app.ui_coordinator.update_build_progress_display()

                # Create the firmware builder
                builder = FirmwareBuilder(core_config)

                # Move to device analysis stage
                build_progress.stage = BuildStage.DEVICE_ANALYSIS
                build_progress.completion_percent = 0.0
                build_progress.current_operation = "Analyzing device configuration"
                build_progress.mark_stage_complete(BuildStage.ENVIRONMENT_VALIDATION)
                self.app.ui_coordinator.update_build_progress_display()

                # Run the actual build process
                try:
                    # Execute the build and get resulting artifacts
                    artifacts = builder.build()

                    # Mark the build as complete
                    build_progress.stage = BuildStage.BITSTREAM_GENERATION
                    build_progress.completion_percent = 100.0
                    build_progress.current_operation = "Build completed successfully"
                    build_progress.mark_stage_complete(BuildStage.DEVICE_ANALYSIS)
                    build_progress.mark_stage_complete(BuildStage.REGISTER_EXTRACTION)
                    build_progress.mark_stage_complete(
                        BuildStage.SYSTEMVERILOG_GENERATION
                    )
                    build_progress.mark_stage_complete(BuildStage.VIVADO_SYNTHESIS)
                    build_progress.mark_stage_complete(BuildStage.BITSTREAM_GENERATION)
                    self.app.ui_coordinator.update_build_progress_display()

                    # Return success with artifact paths
                    artifact_dict = {os.path.basename(path): path for path in artifacts}
                    return True, artifact_dict

                except Exception as build_error:
                    build_progress.add_error(
                        f"Build process failed: {str(build_error)}"
                    )
                    self.app.ui_coordinator.update_build_progress_display()
                    return False, f"Build execution failed: {str(build_error)}"

            except Exception as config_error:
                build_progress.add_error(f"Configuration error: {str(config_error)}")
                self.app.ui_coordinator.update_build_progress_display()
                return False, f"Build configuration failed: {str(config_error)}"

        except Exception as e:
            return False, f"Build failed: {str(e)}"

    async def clean_build_artifacts(self, artifacts: Dict[str, str]) -> bool:
        """
        Clean up build artifacts.

        Args:
            artifacts: Dictionary of artifact paths to clean up

        Returns:
            True if cleanup was successful, False otherwise
        """
        try:
            for path in artifacts.values():
                if os.path.exists(path):
                    os.remove(path)
            return True
        except Exception as e:
            if hasattr(self.app, "error_handler"):
                self.app.error_handler.handle_operation_error(
                    "cleaning build artifacts", e
                )
            return False
