"""Tests for the BaseDialog escape-to-dismiss contract.

These tests use Textual's Pilot harness to verify that dialogs dismiss
with the documented default value when the user presses Escape.
"""

import pytest
from textual.app import App, ComposeResult
from textual.widgets import Button

from pcileechfwgenerator.tui.dialogs.confirmation import ConfirmationDialog
from pcileechfwgenerator.tui.dialogs.file_path_input import FilePathInputDialog
from pcileechfwgenerator.tui.dialogs.help_dialog import HelpDialog


class _DismissCapture:
    def __init__(self) -> None:
        self.value = "<unset>"

    def __call__(self, value) -> None:
        self.value = value


def _harness(dialog):
    """Build a tiny App that pushes the given dialog on mount."""
    captured = _DismissCapture()

    class _Harness(App):
        def compose(self) -> ComposeResult:  # pragma: no cover - trivial
            yield Button("noop")

        async def on_mount(self) -> None:
            await self.push_screen(dialog, captured)

    return _Harness(), captured


@pytest.mark.asyncio
async def test_confirmation_dialog_escape_returns_false():
    app, captured = _harness(ConfirmationDialog("Title", "msg"))
    async with app.run_test() as pilot:
        await pilot.press("escape")
        await pilot.pause()
    assert captured.value is False


@pytest.mark.asyncio
async def test_help_dialog_escape_returns_true():
    app, captured = _harness(HelpDialog())
    async with app.run_test() as pilot:
        await pilot.press("escape")
        await pilot.pause()
    assert captured.value is True


@pytest.mark.asyncio
async def test_file_path_input_dialog_escape_returns_none():
    app, captured = _harness(FilePathInputDialog("Pick a path"))
    async with app.run_test() as pilot:
        await pilot.press("escape")
        await pilot.pause()
    assert captured.value is None
