"""
Build Manager for PCILeech TUI application.

This module provides services for validating and building firmware for devices.
"""

import asyncio
import os
from pathlib import Path
from typing import Dict, List, Tuple, Union

from ..models.device import PCIDevice
from ..models.progress import BuildProgress
from .build_contract import resolve_datastore_directory, validate_build_configuration


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
        try:
            validate_build_configuration(self.app.current_config)
        except ValueError as exc:
            validation_messages.append(str(exc))

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
            is_valid, messages = await self.validate_configuration(device)
            if not is_valid:
                return False, "\n".join(messages)
            config = self.app.current_config.copy()
            config.output_directory = output_path
            output = Path(output_path).expanduser().resolve()
            datastore = resolve_datastore_directory(config)

            def snapshot() -> Dict[str, Path]:
                return {
                    str(path.relative_to(output)): path
                    for path in output.rglob("*")
                    if path.is_file() and not path.resolve().is_relative_to(datastore)
                }

            existing = await asyncio.to_thread(snapshot)

            def on_progress(progress: BuildProgress) -> None:
                self.app.build_progress = progress
                self.app.ui_coordinator.update_build_progress_display()

            success = await self.app.build_orchestrator.start_build(
                device, config, on_progress
            )
            if not success:
                return False, "Build cancelled"
            # Undo owns only newly created files, never pre-existing outputs.
            created = await asyncio.to_thread(snapshot)
            artifacts = {
                name: str(path)
                for name, path in created.items()
                if name not in existing
            }
            return True, artifacts
        except Exception as exc:
            return False, f"Build failed: {exc}"

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
