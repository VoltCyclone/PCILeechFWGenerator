from typing import Any, Dict

from textual.app import ComposeResult
from textual.containers import Container, Horizontal, Vertical
from textual.widgets import Button, Input, Static

from .base import BaseDialog


class ConfigurationDialog(BaseDialog[dict | None]):
    """Modal dialog to edit a simple configuration dict."""

    def __init__(self, title: str, config: Dict[str, Any]):
        super().__init__()
        self.title = title
        self.config = dict(config)

    def compose(self) -> ComposeResult:
        with Container(id="config-dialog"):
            yield Static(self.title, classes="dialog-title")

            with Vertical(id="config-items"):
                for key, value in self.config.items():
                    yield Static(key)
                    yield Input(value=str(value), id=f"config-{key}")

            with Horizontal(classes="dialog-buttons"):
                yield Button("Cancel", id="cancel-config", variant="default")
                yield Button("Save", id="save-config", variant="primary")

    async def on_button_pressed(self, event: Button.Pressed) -> None:
        button_id = event.button.id
        if button_id == "cancel-config":
            self.dismiss(None)
        elif button_id == "save-config":
            new_conf: Dict[str, Any] = {}
            for key in self.config.keys():
                input_widget = self.query_one(f"#config-{key}", Input)
                new_conf[key] = input_widget.value
            self.dismiss(new_conf)
