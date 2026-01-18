
from textual.app import ComposeResult
from textual.containers import Container, Horizontal, Vertical
from textual.screen import ModalScreen
from textual.widgets import Button, Static


class ConfirmationDialog(ModalScreen[bool]):
    """Modal dialog for confirming actions with warnings"""

    def __init__(self, title: str, message: str) -> None:
        super().__init__()
        self.title = title
        self.message = message

    def compose(self) -> ComposeResult:
        with Container(id="confirm-dialog"):
            yield Static(self.title, id="dialog-title")

            with Vertical(id="confirm-message"):
                yield Static(self.message)

            with Horizontal(id="dialog-buttons"):
                yield Button("Cancel", id="cancel-confirm", variant="default")
                yield Button("Continue", id="confirm-action", variant="primary")

    async def on_button_pressed(self, event: Button.Pressed) -> None:
        button_id = event.button.id
        if button_id == "cancel-confirm":
            self.dismiss(False)
        elif button_id == "confirm-action":
            self.dismiss(True)
