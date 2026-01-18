"""
Status panel widget for PCILeech TUI application.

This module defines a widget for displaying status information in the TUI.
"""

from typing import Dict, List, Union

from rich.console import RenderableType
from rich.panel import Panel
from rich.table import Table
from textual.reactive import reactive
from textual.widget import Widget


class StatusPanel(Widget):
    """A panel for displaying system status information."""

    DEFAULT_CSS = """
    StatusPanel {
        width: 100%;
        height: auto;
        padding: 0 1;
        margin: 0 0 1 0;
        background: $boost;
        border: solid $accent;
    }
    """

    # Reactive properties for status information
    title = reactive("System Status")
    status_items = reactive([])
    build_progress = reactive(0)
    build_status_message = reactive("")
    is_building = reactive(False)

    def __init__(self, title: str = "System Status", **kwargs):
        """
        Initialize the status panel widget.

        Args:
            title: The panel title
            **kwargs: Additional keyword arguments for the widget
        """
        super().__init__(**kwargs)
        self.title = title

    def set_status_items(self, items: List[Dict[str, Union[str, bool]]]) -> None:
        """
        Set the status items to display.

        Args:
            items: List of status item dictionaries with keys:
                - name: Name of the status item
                - value: Value of the status item
                - is_ok: Whether the status is OK (affects rendering)
        """
        self.status_items = items

    def update_build_status(
        self, message: str = "", progress: int = 0, is_building: bool = False
    ) -> None:
        """
        Update the build status information.

        Args:
            message: Status message for the build
            progress: Build progress percentage (0-100)
            is_building: Whether a build is in progress
        """
        self.build_status_message = message
        self.build_progress = progress
        self.is_building = is_building

    def render(self) -> RenderableType:
        """
        Render the status panel.

        Returns:
            A rich renderable for the panel
        """
        # Create status table
        status_table = Table(box=None, show_header=False, padding=(0, 1, 0, 0))
        status_table.add_column("Name", style="bold")
        status_table.add_column("Value")
        status_table.add_column("Status", justify="right")

        # Add status items
        for item in self.status_items:
            name = item.get("name", "Unknown")
            value = item.get("value", "")
            is_ok = item.get("is_ok", True)

            # Format status indicator
            status = "✅" if is_ok else "❌"

            status_table.add_row(name, str(value), status)

        # Add build progress if building
        if self.is_building:
            progress_text = f"[{self.build_progress}%] {self.build_status_message}"
            status_table.add_row("Build Status", progress_text, "⏳")

        return Panel(
            status_table,
            title=self.title,
            border_style="blue",
            title_align="left",
        )
