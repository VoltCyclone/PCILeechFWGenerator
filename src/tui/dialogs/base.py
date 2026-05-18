"""Shared modal dialog base class.

Provides escape-to-dismiss and a stable convention for title/button styling
so individual dialogs don't reinvent scaffolding (and don't collide on IDs
across the modal screen stack).
"""

from typing import Generic, TypeVar

from textual.binding import Binding
from textual.screen import ModalScreen

T = TypeVar("T")


class BaseDialog(ModalScreen[T], Generic[T]):
    """ModalScreen with a uniform Escape binding.

    Subclasses returning a non-None default on dismiss should override
    ``action_dismiss_dialog``.
    """

    BINDINGS = [
        Binding("escape", "dismiss_dialog", "Close", priority=True),
    ]

    def action_dismiss_dialog(self) -> None:
        self.dismiss(None)
