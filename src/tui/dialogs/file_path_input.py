from textual.app import ComposeResult
from textual.containers import Container, Horizontal, Vertical
from textual.widgets import Button, Input, Static

from .base import BaseDialog


class FilePathInputDialog(BaseDialog[str | None]):
    """Modal input dialog to get a file path from the user."""

    def __init__(self, prompt: str, default: str | None = None) -> None:
        super().__init__()
        self.prompt = prompt
        self.default = default

    def compose(self) -> ComposeResult:
        with Container(id="file-path-dialog"):
            yield Static(self.prompt, classes="dialog-title")

            with Vertical(id="file-path-input"):
                yield Input(
                    value=self.default or "",
                    placeholder="Enter path...",
                    id="path-input",
                )

            with Horizontal(classes="dialog-buttons"):
                yield Button("Cancel", id="cancel-path", variant="default")
                yield Button("Ok", id="confirm-path", variant="primary")

    async def on_button_pressed(self, event: Button.Pressed) -> None:
        button_id = event.button.id
        if button_id == "cancel-path":
            self.dismiss(None)
        elif button_id == "confirm-path":
            input_widget = self.query_one("#path-input", Input)
            self.dismiss(input_widget.value)
