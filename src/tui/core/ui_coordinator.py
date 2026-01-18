"""
UI Coordinator for PCILeech TUI

Coordinates UI operations and business logic for the PCILeech TUI application.
"""

import json
from pathlib import Path
from typing import Any, Dict, List, Optional

from ..models.config import BuildConfiguration
from ..models.device import PCIDevice
from ..models.progress import BuildProgress


class UICoordinator:
    """
    Coordinates UI operations and business logic for the PCILeech TUI application.

    This class separates UI logic from business logic, making the application more
    maintainable and testable. It orchestrates interactions between the user interface
    and the underlying services.
    """

    def __init__(self, app):
        """
        Initialize the UI coordinator with the app and services.

        Args:
            app: The main TUI application instance
        """
        self.app = app
        self.device_manager = app.device_manager
        self.config_manager = app.config_manager
        self.build_orchestrator = app.build_orchestrator
        self.status_monitor = app.status_monitor

    # Device Selection and Management

    async def handle_device_selection(self, device: PCIDevice) -> None:
        """
        Handle device selection events

        Args:
            device: The selected device
        """
        # Update app state
        self.app.app_state.set_selected_device(device)
        self._update_compatibility_display(device)

        # Enable relevant buttons
        self._update_buttons_for_device_selection(device)

        # Notify user
        self.app.notify(f"Selected device: {device.bdf}", severity="info")

    async def scan_devices(self) -> List[PCIDevice]:
        """
        Scan for PCIe devices and update the UI

        Returns:
            List of discovered devices
        """
        try:
            devices = await self.device_manager.scan_devices()
            # Update app state instead of directly modifying app._devices
            self.app.app_state.set_devices(devices)
            self.apply_device_filters()
            self.update_device_table()

            # Update device count in title
            self._update_device_panel_title()

            return devices
        except Exception as e:
            if hasattr(self.app, "error_handler"):
                self.app.error_handler.handle_operation_error("scanning devices", e)
            else:
                self.app.notify(f"Failed to scan devices: {e}", severity="error")
            return []

    def apply_device_filters(self) -> None:
        """Apply current filters to device list"""
        # The filtering logic is now in PCILeechTUI.filtered_devices property
        # This method is kept for backwards compatibility, but delegates to the app state
        # Update the filters in the app state from the current UI
        try:
            search_text = self.app.query_one("#quick-search").value.lower()
            current_filters = (
                self.app.device_filters.copy() if self.app.device_filters else {}
            )

            if search_text:
                current_filters["search_text"] = search_text

            # Update app state with filters
            self.app.app_state.set_filters(current_filters)
        except Exception:
            pass  # Ignore if search widget not ready

    def update_device_table(self) -> None:
        """Update the device table with current filtered devices"""
        device_table = self.app.query_one("#device-table")
        
        # Use VirtualDeviceTable's set_data method for proper virtualization
        # The VirtualDeviceTable will handle clearing and rendering efficiently
        device_table.set_data(self.app.filtered_devices)

    def _update_device_panel_title(self) -> None:
        """Update the device panel title with device count"""
        # Use the app's properties which use app_state
        device_count = len(getattr(self.app, "filtered_devices", []) or [])
        # Some minimal test apps may not expose a full `devices` property; fall back
        # to the filtered list if `devices` is not available.
        total_count = len(
            getattr(self.app, "devices", getattr(self.app, "filtered_devices", []))
            or []
        )
        device_panel = self.app.query_one("#device-panel .panel-title")

        if device_count == total_count:
            device_panel.update(f"📡 PCIe Devices Found: {device_count}")
        else:
            device_panel.update(
                f"📡 PCIe Devices: {device_count}/{total_count} (filtered)"
            )

    def _update_buttons_for_device_selection(self, device: PCIDevice) -> None:
        """
        Update button states based on device selection

        Args:
            device: The selected device
        """
        try:
            self.app.query_one("#device-details").disabled = False
            self.app.query_one("#start-build").disabled = not device.is_suitable
        except Exception:
            # Ignore if buttons don't exist (e.g., in tests)
            pass

    # Build Orchestration

    async def handle_build_start(self) -> None:
        """
        Start the build process with validation and error handling
        """
        if not self.app.selected_device:
            self.app.notify("Please select a device first", severity="error")
            return

        if self.build_orchestrator.is_building():
            self.app.notify("Build already in progress", severity="warning")
            return

        # Check donor module status before starting build if donor_dump is enabled
        if (
            self.app.current_config.donor_dump
            and not self.app.current_config.local_build
        ):
            await self._validate_donor_module()

        try:
            # Update button states
            self.app.query_one("#start-build").disabled = True
            self.app.query_one("#stop-build").disabled = False

            # Start build with progress callback
            success = await self.build_orchestrator.start_build(
                self.app.selected_device,
                self.app.current_config,
                self._on_build_progress,
            )

            if success:
                self.app.notify("Build completed successfully!", severity="success")
            else:
                self.app.notify("Build was cancelled", severity="warning")

        except Exception as e:
            error_msg = str(e)
            if hasattr(self.app, "error_handler"):
                self.app.error_handler.handle_operation_error("starting build", e)
            else:
                # Check if this is a platform compatibility error
                if (
                    "requires Linux" in error_msg
                    or "platform incompatibility" in error_msg
                    or "only available on Linux" in error_msg
                ):
                    self.app.notify(
                        "Build skipped: Platform compatibility issue (see logs)",
                        severity="warning",
                    )
                else:
                    self.app.notify(f"Build failed: {e}", severity="error")
        finally:
            # Reset button states
            self.app.query_one("#start-build").disabled = False
            self.app.query_one("#stop-build").disabled = True

    async def handle_build_stop(self) -> None:
        """Stop the current build process"""
        await self.build_orchestrator.cancel_build()
        self.app.notify("Build cancelled", severity="info")

    def handle_build_progress(self, progress: BuildProgress) -> None:
        """
        Handle build progress updates

        Args:
            progress: The current build progress
        """
        self.app.app_state.set_build_progress(progress)
        self._update_build_progress_display()

    def _on_build_progress(self, progress: BuildProgress) -> None:
        """
        Callback for build progress updates from build orchestrator

        Args:
            progress: The current build progress
        """
        # Forward to the main handler
        self.handle_build_progress(progress)

    def _update_build_progress_display(self) -> None:
        """Update the UI with current build progress"""
        if not self.app.build_progress:
            return

        progress = self.app.build_progress

        # Update status
        self.app.query_one("#build-status").update(f"Status: {progress.status_text}")

        # Update progress bar
        progress_bar = self.app.query_one("#build-progress")
        progress_bar.progress = progress.overall_progress

        # Update progress text
        self.app.query_one("#progress-text").update(progress.progress_bar_text)

        # Update resource usage
        if progress.resource_usage:
            cpu = progress.resource_usage.get("cpu", 0)
            memory = progress.resource_usage.get("memory", 0)
            disk = progress.resource_usage.get("disk_free", 0)
            resource_text = f"Resources: CPU: {cpu:.1f}% | Memory: {memory:.1f}GB | Disk: {disk:.1f}GB free"
            self.app.query_one("#resource-usage").update(resource_text)

    async def _validate_donor_module(self) -> bool:
        """
        Validate donor module status before starting build

        Returns:
            True if validation passed or user confirmed to continue, False otherwise
        """
        module_status = await self.app._check_donor_module_status(
            show_notification=False
        )

        if module_status and module_status.get("status") != "installed":
            # Show warning dialog with issues and fixes
            self.app.notify(
                "⚠️ Donor module is not properly installed. This may affect the build.",
                severity="warning",
            )

            # Show detailed issues and fixes
            issues = module_status.get("issues", [])
            fixes = module_status.get("fixes", [])

            if issues:
                self.app.notify(f"Issues: {issues[0]}", severity="warning")
            if fixes:
                self.app.notify(
                    f"Suggested fix: {fixes[0]}",
                    severity="information",
                )

            # Ask if user wants to continue anyway
            should_continue = await self.app._confirm_with_warnings(
                "⚠️ Warning: Donor Module Issues",
                "The donor module is not properly installed. This may affect the build. Do you want to continue anyway?",
            )

            if not should_continue:
                self.app.notify("Build cancelled by user", severity="information")
                return False

        return True

    # Public convenience methods (UI-facing helpers)

    async def load_profile_by_name(
        self, profile_name: str
    ) -> Optional[BuildConfiguration]:
        """
        Load a profile by name and update the application state.

        Returns the loaded BuildConfiguration or None on failure.
        """
        try:
            config = self.config_manager.load_profile(profile_name)
            if config:
                # Update app state and config manager
                self.app.app_state.set_config(config)
                self.config_manager.set_current_config(config)
                # Update UI displays
                self._update_config_display()
                self.app.notify(f"Loaded profile: {profile_name}", severity="success")
                return config
            return None
        except Exception as e:
            if hasattr(self.app, "error_handler"):
                self.app.error_handler.handle_operation_error("loading profile", e)
            else:
                if hasattr(self.app, "notify"):
                    self.app.notify(f"Failed to load profile: {e}", severity="error")
            return None

    async def apply_filters(self, filters: Dict[str, Any]) -> None:
        """Apply filter dictionary to the app state and refresh displayed devices."""
        try:
            # Normalize filter keys to the app's expectation
            self.app.app_state.set_filters(filters or {})
            # Let the app's computed property and coordinator update the table
            self.update_device_table()
            # Update device panel title
            self._update_device_panel_title()
            if hasattr(self.app, "notify"):
                self.app.notify("Filters applied", severity="success")
        except Exception as e:
            if hasattr(self.app, "error_handler"):
                self.app.error_handler.handle_operation_error("applying filters", e)
            else:
                if hasattr(self.app, "notify"):
                    self.app.notify(f"Failed to apply filters: {e}", severity="error")

    def get_current_build_log(self) -> List[str]:
        """Return current build log lines via the build orchestrator."""
        try:
            return self.build_orchestrator.get_current_build_log()
        except Exception:
            return []

    def get_build_history(self) -> List[Any]:
        """Return a lightweight build history list."""
        try:
            # Defer to build_orchestrator if it implements history retrieval
            if hasattr(self.build_orchestrator, "get_build_history"):
                return self.build_orchestrator.get_build_history()
            # Otherwise return a minimal, empty history
            return []
        except Exception:
            return []

    async def generate_donor_template(self) -> Optional[Path]:
        """Generate a donor info template file and return its path if created."""
        try:
            from pathlib import Path

            # Prefer build module's generation if available, but keep a device_clone fallback
            try:
                from pcileechfwgenerator.build import FirmwareBuilder  # noqa: F401
                from pcileechfwgenerator.device_clone.donor_info_template import (
                    DonorInfoTemplateGenerator,
                )

                output_path = Path("donor_info_template.json")
                DonorInfoTemplateGenerator.save_template(output_path, pretty=True)
            except Exception:
                from pcileechfwgenerator.device_clone.donor_info_template import (
                    DonorInfoTemplateGenerator,
                )

                output_path = Path("donor_info_template.json")
                DonorInfoTemplateGenerator.save_template(output_path, pretty=True)

            if hasattr(self.app, "notify"):
                self.app.notify(
                    f"✓ Donor info template saved to: {output_path}", severity="success"
                )
            return output_path
        except Exception as e:
            if hasattr(self.app, "error_handler"):
                self.app.error_handler.handle_operation_error(
                    "generating donor template", e
                )
            else:
                if hasattr(self.app, "notify"):
                    self.app.notify(
                        f"Failed to generate donor template: {e}", severity="error"
                    )
            return None

    async def check_donor_module_status(
        self, show_notification: bool = True
    ) -> Dict[str, Any]:
        """Check donor dump kernel module status and return status dict."""
        try:
            try:
                from pcileechfwgenerator.file_management.donor_dump_manager import (
                    DonorDumpManager,
                )
            except Exception:
                from file_management.donor_dump_manager import DonorDumpManager

            manager = DonorDumpManager()
            module_status = manager.check_module_installation()

            # Update system status and display via core update
            if getattr(self.app, "_system_status", None) is not None:
                self.app._system_status["donor_module"] = module_status

            if show_notification and hasattr(self.app, "notify"):
                status = module_status.get("status")
                details = module_status.get("details")
                if status == "installed":
                    self.app.notify(
                        f"Donor module status: {details}", severity="success"
                    )
                elif status in ["built_not_loaded", "loaded_but_error"]:
                    self.app.notify(
                        f"Donor module status: {details}", severity="warning"
                    )
                else:
                    self.app.notify(f"Donor module status: {details}", severity="error")

            return module_status
        except Exception as e:
            # Return an error-shaped dictionary
            err = {
                "status": "error",
                "details": f"Error checking module: {str(e)}",
                "issues": [str(e)],
                "fixes": [
                    "Check if src/file_management/donor_dump_manager.py is accessible"
                ],
            }
            if show_notification and hasattr(self.app, "notify"):
                self.app.notify(
                    f"Failed to check donor module status: {e}", severity="error"
                )
            if getattr(self.app, "_system_status", None) is not None:
                self.app._system_status["donor_module"] = err
            return err

    # Public wrappers for UI usage
    def update_compatibility_display(self, device: PCIDevice) -> None:
        """Public wrapper to update compatibility display for a device.

        This delegates to the internal implementation but gives a stable
        public API for callers in the TUI layer.
        """
        try:
            self._update_compatibility_display(device)
        except Exception:
            # Ignore UI update errors to avoid bubbling into app logic
            pass

    def update_build_progress_display(self) -> None:
        """Public wrapper to refresh build progress display in the UI."""
        try:
            self._update_build_progress_display()
        except Exception:
            pass

    def update_config_display(self) -> None:
        """Public wrapper to refresh configuration-related UI elements."""
        try:
            self._update_config_display()
        except Exception:
            pass

    # Configuration Management

    async def handle_configuration_update(self, config: BuildConfiguration) -> None:
        """
        Handle configuration updates from dialogs

        Args:
            config: The updated configuration
        """
        if config is not None:
            # Update app state instead of directly modifying app.current_config
            self.app.app_state.set_config(config)
            # Save the configuration to the config manager
            self.config_manager.set_current_config(config)
            self._update_config_display()
            self.app.notify("Configuration updated successfully", severity="success")

    def _update_config_display(self) -> None:
        """Update configuration display in the UI"""
        config = self.app.current_config

        try:
            # Import here to avoid circular import
            from pcileechfwgenerator.tui.utils.ui_helpers import (
                format_build_mode,
                safely_update_static,
            )

            # Update board type
            safely_update_static(
                self.app, "#board-type", f"Board Type: {config.board_type}"
            )

            # Update features display
            features = "Enabled" if config.is_advanced else "Basic"
            safely_update_static(
                self.app, "#advanced-features", f"Advanced Features: {features}"
            )

            # Update build mode with helper function
            build_mode = format_build_mode(config)
            safely_update_static(self.app, "#build-mode", build_mode)

            # Update donor dump button
            self._update_donor_dump_button()
        except Exception as e:
            # Handle any UI update errors gracefully
            if hasattr(self.app, "error_handler"):
                self.app.error_handler.handle_operation_error(
                    "updating configuration display", e
                )
            else:
                print(f"Error updating configuration display: {e}")
                self.app.notify("Error displaying configuration", severity="error")

    def _update_donor_dump_button(self) -> None:
        """Update the donor dump button text and style based on current state"""
        try:
            button = self.app.query_one("#enable-donor-dump")
            if self.app.current_config.donor_dump:
                button.label = "🚫 Disable Donor Dump"
                button.variant = "error"
            else:
                button.label = "🎯 Enable Donor Dump"
                button.variant = "success"
        except Exception:
            # Button might not exist in tests
            pass

    # Compatibility Display

    def _update_compatibility_display(self, device: PCIDevice) -> None:
        """
        Update the compatibility factors display for the selected device

        Args:
            device: The selected device
        """
        try:
            # Update title and score safely
            compatibility_title = self.app.query_one("#compatibility-title")
            display_name = getattr(device, "display_name", "Unknown Device")
            compatibility_title.update(f"Device: {display_name}")

            compatibility_score = self.app.query_one("#compatibility-score")

            # Format score safely
            try:
                score = float(getattr(device, "suitability_score", 0.0))
                score_text = f"Final Score: [bold]{score:.2f}[/bold]"

                # Check if suitable (safely)
                is_suitable = getattr(device, "is_suitable", False)
                if is_suitable:
                    score_text = f"[green]{score_text}[/green]"
                else:
                    score_text = f"[red]{score_text}[/red]"
            except (ValueError, TypeError):
                score_text = "Final Score: [bold]N/A[/bold]"

            # Add detailed status indicators with safe attribute access
            status_indicators = []

            # Safe access to all indicator attributes
            for indicator_name, attr_name in [
                ("Valid", "validity_indicator"),
                ("Driver", "driver_indicator"),
                ("VFIO", "vfio_indicator"),
                ("IOMMU", "iommu_indicator"),
                ("Ready", "ready_indicator"),
            ]:
                try:
                    indicator_value = getattr(device, attr_name, "❓")
                    status_indicators.append(f"{indicator_name}: {indicator_value}")
                except Exception:
                    status_indicators.append(f"{indicator_name}: ❓")

            status_line = " | ".join(status_indicators)
            score_text += f"\n{status_line}"
            compatibility_score.update(score_text)

            # Update factors table
            factors_table = self.app.query_one("#compatibility-table")
            factors_table.clear()

            # Set up columns if not already done
            if not factors_table.columns:
                factors_table.add_columns("Status Check", "Result", "Details")

            # Add detailed status information
            self._add_detailed_status_rows(factors_table, device)

            # Add compatibility factors if available
            factors = getattr(device, "compatibility_factors", [])
            for factor in factors:
                try:
                    name = factor.get("name", "Unknown Factor")

                    # Safe conversion for adjustment
                    try:
                        adjustment = float(factor.get("adjustment", 0.0))
                        # Format adjustment with sign and color
                        if adjustment > 0:
                            adj_text = f"[green]+{adjustment:.1f}[/green]"
                        elif adjustment < 0:
                            adj_text = f"[red]{adjustment:.1f}[/red]"
                        else:
                            adj_text = f"{adjustment:.1f}"
                    except (ValueError, TypeError):
                        adj_text = "0.0"

                    description = factor.get("description", "No description")
                    # Add row with appropriate styling
                    factors_table.add_row(name, adj_text, description)
                except Exception as e:
                    print(f"Error adding factor row: {e}")
                    # Add fallback row for errors
                    factors_table.add_row(
                        "Factor Error", "N/A", f"Error: {str(e)[:50]}"
                    )
        except Exception as e:
            print(f"Error updating compatibility display: {e}")
            # Try to show error in compatibility title as fallback
            try:
                compatibility_title = self.app.query_one("#compatibility-title")
                compatibility_title.update(
                    f"Error displaying compatibility: {str(e)[:50]}"
                )
            except Exception:
                pass

    def _add_detailed_status_rows(self, table, device: PCIDevice) -> None:
        """
        Add detailed status information to the compatibility table

        Args:
            table: The DataTable to add rows to
            device: The device to display status for
        """
        try:
            # Device validity (with safe access)
            is_valid = getattr(device, "is_valid", False)
            valid_status = (
                "[green]✅ Valid[/green]" if is_valid else "[red]❌ Invalid[/red]"
            )
            table.add_row(
                "Device Accessibility",
                valid_status,
                "Device is properly detected and accessible",
            )

            # Driver status (with safe access)
            has_driver = getattr(device, "has_driver", False)
            is_detached = getattr(device, "is_detached", False)
            driver = getattr(device, "driver", "unknown")

            if has_driver:
                if is_detached:
                    driver_status = "[green]🔓 Detached[/green]"
                    driver_details = f"Device detached from {driver} for VFIO use"
                else:
                    driver_status = "[yellow]🔒 Bound[/yellow]"
                    driver_details = f"Device bound to {driver} driver"
            else:
                driver_status = "[blue]🔌 No Driver[/blue]"
                driver_details = "No driver currently bound to device"
            table.add_row("Driver Status", driver_status, driver_details)

            # VFIO compatibility (with safe access)
            vfio_compatible = getattr(device, "vfio_compatible", False)
            vfio_status = (
                "[green]🛡️ Compatible[/green]"
                if vfio_compatible
                else "[red]❌ Incompatible[/red]"
            )
            vfio_details = (
                "Device supports VFIO passthrough"
                if vfio_compatible
                else "Device cannot use VFIO passthrough"
            )
            table.add_row("VFIO Support", vfio_status, vfio_details)

            # IOMMU status (with safe access)
            iommu_enabled = getattr(device, "iommu_enabled", False)
            iommu_group = getattr(device, "iommu_group", None)

            iommu_status = (
                "[green]🔒 Enabled[/green]"
                if iommu_enabled
                else "[red]❌ Disabled[/red]"
            )

            iommu_details = (
                f"IOMMU group: {iommu_group}"
                if iommu_enabled and iommu_group is not None
                else "IOMMU not properly configured"
            )
            table.add_row("IOMMU Configuration", iommu_status, iommu_details)

            # Overall readiness (with safe access)
            is_suitable = getattr(device, "is_suitable", False)

            if is_valid and vfio_compatible and iommu_enabled:
                ready_status = "[green]⚡ Ready[/green]"
                ready_details = "Device is ready for firmware generation"
            elif is_suitable:
                ready_status = "[yellow]⚠️ Caution[/yellow]"
                ready_details = "Device may work but has some compatibility issues"
            else:
                ready_status = "[red]❌ Not Ready[/red]"
                ready_details = "Device has significant compatibility issues"
            table.add_row("Overall Status", ready_status, ready_details)

        except Exception as e:
            # Add error row if anything fails
            print(f"Error adding detailed status rows: {e}")
            table.add_row(
                "Status Error",
                "[red]❌ Error[/red]",
                f"Error displaying device status: {str(e)[:50]}",
            )

    def clear_compatibility_display(self) -> None:
        """Clear the compatibility display when no device is selected"""
        try:
            compatibility_title = self.app.query_one("#compatibility-title")
            compatibility_title.update("Select a device to view compatibility factors")

            compatibility_score = self.app.query_one("#compatibility-score")
            compatibility_score.update("")

            factors_table = self.app.query_one("#compatibility-table")
            factors_table.clear()
        except Exception:
            # Ignore DOM errors in tests or during initialization
            pass

    # Utility Methods

    async def export_device_list(self) -> None:
        """Export current device list to JSON.

        Uses the public `filtered_devices` property on the app rather than
        referencing private attributes. This is safer and easier to test.
        """
        try:
            # Prefer public property; fall back to empty list
            devices = getattr(self.app, "filtered_devices", []) or []
            devices_data = [device.to_dict() for device in devices]
            export_path = Path("pcie_devices.json")

            with open(export_path, "w") as f:
                json.dump(
                    {
                        "export_time": getattr(
                            self.app, "_get_current_timestamp", lambda: ""
                        )(),
                        "device_count": len(devices_data),
                        "devices": devices_data,
                    },
                    f,
                    indent=2,
                )

            # Notify through app
            if hasattr(self.app, "notify"):
                self.app.notify(
                    f"Device list exported to {export_path}", severity="success"
                )
        except Exception as e:
            if hasattr(self.app, "error_handler"):
                self.app.error_handler.handle_operation_error(
                    "exporting device list", e
                )
            else:
                if hasattr(self.app, "notify"):
                    self.app.notify(
                        f"Failed to export device list: {e}", severity="error"
                    )
