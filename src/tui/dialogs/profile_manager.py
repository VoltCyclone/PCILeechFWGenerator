from typing import Dict, List, Optional

from textual.app import ComposeResult
from textual.containers import Container, Horizontal, Vertical
from textual.screen import ModalScreen
from textual.widgets import Button, DataTable, Static


class ProfileManagerDialog(ModalScreen[Optional[str]]):
    """Modal dialog for managing configuration profiles"""

    def __init__(self, config_manager) -> None:
        super().__init__()
        self.config_manager = config_manager
        self.profiles: List[Dict[str, str]] = []

    def compose(self) -> ComposeResult:
        with Container(id="profile-manager-dialog"):
            yield Static("📋 Configuration Profiles", id="dialog-title")

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

            with Horizontal(id="dialog-buttons"):
                yield Button("Close", id="close-profiles", variant="default")

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
                self.app.notify("Failed to load profiles", severity="error")
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

    # The rest of the helper methods are intentionally left in the main app where they are coupled to app services
