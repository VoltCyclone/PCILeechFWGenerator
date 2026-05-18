from pathlib import Path
from typing import Dict, List, Optional

from textual.app import ComposeResult
from textual.containers import Container, Horizontal, Vertical
from textual.widgets import Button, DataTable, Static

from .base import BaseDialog
from .confirmation import ConfirmationDialog
from .file_path_input import FilePathInputDialog


class ProfileManagerDialog(BaseDialog[Optional[str]]):
    """Modal dialog for managing configuration profiles"""

    def __init__(self, config_manager) -> None:
        super().__init__()
        self.config_manager = config_manager
        self.profiles: List[Dict[str, str]] = []

    def compose(self) -> ComposeResult:
        with Container(id="profile-manager-dialog"):
            yield Static("📋 Configuration Profiles", classes="dialog-title")

            with Horizontal():
                with Vertical(id="profile-list-panel"):
                    yield Static("Available Profiles:", classes="text-bold")
                    yield DataTable(id="profiles-table")

                    with Horizontal(classes="button-row"):
                        yield Button("Load", id="load-profile-btn", variant="primary")
                        yield Button("Delete", id="delete-profile-btn", variant="error")
                        yield Button("Export", id="export-profile-btn")

                with Vertical(id="profile-details-panel"):
                    yield Static("Profile Details:", classes="text-bold")
                    yield Static(
                        "Select a profile to view details", id="profile-details"
                    )

                    with Horizontal(classes="button-row"):
                        yield Button(
                            "Import", id="import-profile-btn", variant="success"
                        )
                        yield Button(
                            "Create New", id="create-profile-btn", variant="primary"
                        )

            with Horizontal(classes="dialog-buttons"):
                yield Button("Close", id="close-profiles", variant="primary")

    def on_mount(self) -> None:
        self._refresh_profiles()

    def _refresh_profiles(self) -> None:
        try:
            self.profiles = self.config_manager.list_profiles()
            table = self.query_one("#profiles-table", DataTable)
            table.clear()
            if not table.columns:
                table.add_columns("Name", "Description", "Last Used")

            for profile in self.profiles:
                table.add_row(
                    profile["name"],
                    profile.get("description", ""),
                    profile.get("last_used", "Never"),
                    key=profile["name"],
                )
        except Exception:
            try:
                self.app.log_notification("Failed to load profiles", severity="error")
            except Exception:
                pass

    async def on_button_pressed(self, event: Button.Pressed) -> None:
        button_id = event.button.id
        if button_id == "close-profiles":
            self.dismiss(None)
        elif button_id == "load-profile-btn":
            await self._load_selected_profile()
        elif button_id == "delete-profile-btn":
            await self._delete_selected_profile()
        elif button_id == "export-profile-btn":
            await self._export_selected_profile()
        elif button_id == "import-profile-btn":
            await self._import_profile()
        elif button_id == "create-profile-btn":
            await self._create_new_profile()

    # ---- helpers --------------------------------------------------------

    def _selected_profile_name(self) -> Optional[str]:
        """Return the profile name corresponding to the highlighted row."""
        try:
            table = self.query_one("#profiles-table", DataTable)
            row = table.cursor_row
        except Exception:
            return None
        if row is None or row < 0 or row >= len(self.profiles):
            return None
        return self.profiles[row].get("name")

    def _notify_error(self, message: str) -> None:
        try:
            self.app.log_notification(message, severity="error")
        except Exception:
            # Best-effort UI notification — never raise from this path.
            pass

    def _notify_info(self, message: str) -> None:
        try:
            self.app.log_notification(message, severity="information")
        except Exception:
            # Best-effort UI notification — never raise from this path.
            pass

    # ---- button actions -------------------------------------------------

    async def _load_selected_profile(self) -> None:
        name = self._selected_profile_name()
        if not name:
            self._notify_error("Select a profile to load")
            return
        # Caller in main.py reads the dismiss value and applies the profile.
        self.dismiss(name)

    async def _delete_selected_profile(self) -> None:
        name = self._selected_profile_name()
        if not name:
            self._notify_error("Select a profile to delete")
            return

        confirmed = await self.app.push_screen_wait(
            ConfirmationDialog(
                "Delete Profile",
                f"Delete profile '{name}'? This cannot be undone.",
            )
        )
        if not confirmed:
            return

        try:
            ok = self.config_manager.delete_profile(name)
            if not ok:
                self._notify_error(f"Failed to delete profile '{name}'")
                return
            self._notify_info(f"Deleted profile '{name}'")
            self._refresh_profiles()
        except Exception as exc:
            self._notify_error(f"Delete failed: {exc}")

    async def _export_selected_profile(self) -> None:
        name = self._selected_profile_name()
        if not name:
            self._notify_error("Select a profile to export")
            return

        path_str = await self.app.push_screen_wait(
            FilePathInputDialog(
                f"Export '{name}' to file path:",
                default=f"{name}.json",
            )
        )
        if not path_str:
            return

        try:
            ok = self.config_manager.export_profile(name, Path(path_str))
            if ok:
                self._notify_info(f"Exported profile '{name}' to {path_str}")
            else:
                self._notify_error(f"Failed to export profile '{name}'")
        except Exception as exc:
            self._notify_error(f"Export failed: {exc}")

    async def _import_profile(self) -> None:
        path_str = await self.app.push_screen_wait(
            FilePathInputDialog("Import profile from file path:")
        )
        if not path_str:
            return

        try:
            new_name = self.config_manager.import_profile(Path(path_str))
            if not new_name:
                self._notify_error(f"Failed to import profile from {path_str}")
                return
            self._notify_info(f"Imported profile '{new_name}'")
            self._refresh_profiles()
        except Exception as exc:
            self._notify_error(f"Import failed: {exc}")

    async def _create_new_profile(self) -> None:
        # TODO(tui-pass-2): replace FilePathInputDialog with a dedicated
        # name-input primitive. Reusing it here keeps scope contained.
        name = await self.app.push_screen_wait(
            FilePathInputDialog("New profile name:")
        )
        if not name:
            return

        try:
            current = getattr(self.app, "current_config", None)
            if current is None:
                self._notify_error("No current configuration to save")
                return
            ok = self.config_manager.save_profile(name, current)
            if not ok:
                self._notify_error(f"Failed to save profile '{name}'")
                return
            self._notify_info(f"Created profile '{name}'")
            self._refresh_profiles()
        except Exception as exc:
            self._notify_error(f"Create failed: {exc}")
