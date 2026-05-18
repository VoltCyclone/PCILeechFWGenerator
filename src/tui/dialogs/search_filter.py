from typing import Any, Dict

from textual.app import ComposeResult
from textual.containers import Container, Horizontal, Vertical
from textual.widgets import Button, Input, Label, Select, Static

from .base import BaseDialog


class SearchFilterDialog(BaseDialog[Dict[str, Any]]):
    """Modal dialog for searching and filtering devices"""

    def compose(self) -> ComposeResult:
        with Container(id="search-filter-dialog"):
            yield Static("🔍 Search & Filter Devices", classes="dialog-title")

            with Vertical(id="search-form"):
                yield Label("Search by Device Name:")
                yield Input(
                    placeholder="Enter device name or vendor", id="device-search"
                )

                yield Label("Filter by Class:")
                yield Select(
                    [
                        ("All Classes", "all"),
                        ("Network", "network"),
                        ("Storage", "storage"),
                        ("Display", "display"),
                        ("Multimedia", "multimedia"),
                        ("Bridge", "bridge"),
                        ("Other", "other"),
                    ],
                    value="all",
                    id="class-filter",
                )

                yield Label("Filter by Status:")
                yield Select(
                    [
                        ("All Devices", "all"),
                        ("Suitable Only", "suitable"),
                        ("Driver Bound", "bound"),
                        ("No Driver", "unbound"),
                        ("VFIO Compatible", "vfio"),
                    ],
                    value="all",
                    id="status-filter",
                )

                yield Label("Minimum Suitability Score:")
                yield Input(placeholder="0.0 - 1.0", value="0.0", id="score-filter")

            with Horizontal(classes="dialog-buttons"):
                yield Button("Clear", id="clear-filters", variant="default")
                yield Button("Apply", id="apply-filters", variant="primary")

    async def on_button_pressed(self, event: Button.Pressed) -> None:
        button_id = event.button.id
        if button_id == "clear-filters":
            self._clear_all_filters()
        elif button_id == "apply-filters":
            filters = self._get_filter_criteria()
            self.dismiss(filters)

    def _clear_all_filters(self) -> None:
        try:
            self.query_one("#device-search", Input).value = ""
            self.query_one("#class-filter", Select).value = "all"
            self.query_one("#status-filter", Select).value = "all"
            self.query_one("#score-filter", Input).value = "0.0"
        except Exception:
            pass

    def _get_filter_criteria(self) -> Dict[str, Any]:
        try:
            score_text = self.query_one("#score-filter", Input).value
            min_score = float(score_text) if score_text else 0.0
        except Exception:
            min_score = 0.0

        return {
            "device_search": self.query_one("#device-search", Input).value,
            "class_filter": self.query_one("#class-filter", Select).value,
            "status_filter": self.query_one("#status-filter", Select).value,
            "min_score": min_score,
        }
