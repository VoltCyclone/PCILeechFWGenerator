"""
Build-Related Commands

This module contains commands for firmware build operations like validating and building firmware.
"""

from typing import Dict, Optional

from ..core.build_manager import BuildManager
from ..models.device import PCIDevice
from ..models.template import TemplateOption
from .command import Command


class ValidateConfigCommand(Command):
    """Command to validate the current device configuration."""

    def __init__(self, app, device: PCIDevice) -> None:
        """
        Initialize the validate config command.

        Args:
            app: The main application instance
            device: The device to validate configuration for
        """
        self.app = app
        self.device = device

    async def execute(self) -> bool:
        """
        Execute the validate config command.

        Returns:
            bool: True if the configuration is valid
        """
        try:
            # Get build manager
            build_manager = BuildManager(self.app)

            # Validate configuration
            is_valid, validation_messages = await build_manager.validate_configuration(
                self.device
            )

            # Update UI with validation results
            self.app.ui_coordinator.display_validation_results(
                is_valid, validation_messages
            )

            return is_valid
        except Exception as e:
            if hasattr(self.app, "error_handler"):
                self.app.error_handler.handle_operation_error(
                    "validating configuration", e
                )
            else:
                self.app.notify(
                    f"Failed to validate configuration: {e}", severity="error"
                )
            return False

    async def undo(self) -> bool:
        """
        Undo operation - clears validation results.

        Returns:
            bool: True if validation results were cleared
        """
        try:
            # Clear validation results display
            self.app.ui_coordinator.clear_validation_display()
            return True
        except Exception as e:
            if hasattr(self.app, "error_handler"):
                self.app.error_handler.handle_operation_error(
                    "clearing validation results", e
                )
            else:
                self.app.notify(
                    f"Failed to clear validation results: {e}", severity="error"
                )
            return False


class BuildFirmwareCommand(Command):
    """Command to build firmware for a device."""

    def __init__(self, app, device: PCIDevice, output_path: str) -> None:
        """
        Initialize the build firmware command.

        Args:
            app: The main application instance
            device: The device to build firmware for
            output_path: The path where firmware files should be saved
        """
        self.app = app
        self.device = device
        self.output_path = output_path
        self.build_artifacts: Optional[Dict[str, str]] = None

    async def execute(self) -> bool:
        """
        Execute the build firmware command.

        Returns:
            bool: True if firmware was built successfully
        """
        try:
            # Show building indicator
            self.app.ui_coordinator.set_building_status(True)

            # Get build manager
            build_manager = BuildManager(self.app)

            # Build firmware
            success, artifacts_or_error = await build_manager.build_firmware(
                self.device, self.output_path
            )

            # Store build artifacts for undo
            if success:
                self.build_artifacts = artifacts_or_error
                self.app.ui_coordinator.display_build_results(
                    True, self.build_artifacts
                )
            else:
                self.app.ui_coordinator.display_build_results(False, artifacts_or_error)

            # Hide building indicator
            self.app.ui_coordinator.set_building_status(False)

            return success
        except Exception as e:
            # Hide building indicator in case of error
            self.app.ui_coordinator.set_building_status(False)

            if hasattr(self.app, "error_handler"):
                self.app.error_handler.handle_operation_error("building firmware", e)
            else:
                self.app.notify(f"Failed to build firmware: {e}", severity="error")
            return False

    async def undo(self) -> bool:
        """
        Undo the build firmware command by cleaning up build artifacts.

        Returns:
            bool: True if build artifacts were cleaned up successfully
        """
        try:
            # Clear build results display
            self.app.ui_coordinator.clear_build_results()

            # Clean up build artifacts if they were created
            if self.build_artifacts:
                # Get build manager
                build_manager = BuildManager(self.app)

                # Clean up build artifacts
                await build_manager.clean_build_artifacts(self.build_artifacts)

            return True
        except Exception as e:
            if hasattr(self.app, "error_handler"):
                self.app.error_handler.handle_operation_error(
                    "cleaning up build artifacts", e
                )
            else:
                self.app.notify(
                    f"Failed to clean up build artifacts: {e}", severity="error"
                )
            return False


class SetTemplateOptionCommand(Command):
    """Command to set a template option value."""

    def __init__(
        self, app, device: PCIDevice, option: TemplateOption, new_value: str
    ) -> None:
        """
        Initialize the set template option command.

        Args:
            app: The main application instance
            device: The device to set option for
            option: The template option to set
            new_value: The new value for the option
        """
        self.app = app
        self.device = device
        self.option = option
        self.new_value = new_value
        self.old_value: Optional[str] = None

    async def execute(self) -> bool:
        """
        Execute the set template option command.

        Returns:
            bool: True if the option was set successfully
        """
        try:
            # Store old value for undo
            self.old_value = self.device.get_template_option_value(self.option.name)

            # Set new value
            self.device.set_template_option_value(self.option.name, self.new_value)

            # Update UI
            self.app.ui_coordinator.update_option_display(self.option)

            return True
        except Exception as e:
            if hasattr(self.app, "error_handler"):
                self.app.error_handler.handle_operation_error(
                    f"setting option '{self.option.name}'", e
                )
            else:
                self.app.notify(
                    f"Failed to set option '{self.option.name}': {e}", severity="error"
                )
            return False

    async def undo(self) -> bool:
        """
        Undo the set template option command by restoring the previous value.

        Returns:
            bool: True if the previous value was restored successfully
        """
        try:
            if self.old_value is not None:
                # Restore old value
                self.device.set_template_option_value(self.option.name, self.old_value)

                # Update UI
                self.app.ui_coordinator.update_option_display(self.option)

            return True
        except Exception as e:
            if hasattr(self.app, "error_handler"):
                self.app.error_handler.handle_operation_error(
                    f"restoring option '{self.option.name}'", e
                )
            else:
                self.app.notify(
                    f"Failed to restore option '{self.option.name}': {e}",
                    severity="error",
                )
            return False
