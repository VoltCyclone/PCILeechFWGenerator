"""T12: ConfigurationDialog must preserve types across save/load.

Old dialog rendered every value as Input(str(value)) and returned raw
strings on save. Dict/list values came back as repr strings; saves
silently failed downstream. Verify the new typed save:
  - dict survives a round-trip
  - list survives a round-trip
  - bool stays bool
  - int/float parse cleanly
  - invalid int surfaces an error and skips dismiss
"""

import json
from types import SimpleNamespace
from unittest.mock import MagicMock

import pytest

from pcileechfwgenerator.tui.dialogs.configuration import ConfigurationDialog


@pytest.fixture
def patch_app(monkeypatch):
    holder: dict = {}
    monkeypatch.setattr(
        ConfigurationDialog,
        "app",
        property(lambda self: holder["app"]),
    )
    return holder


def _make_event(button_id: str):
    btn = SimpleNamespace(id=button_id)
    return SimpleNamespace(button=btn)


def _bare_dialog(initial):
    d = ConfigurationDialog.__new__(ConfigurationDialog)
    # Skip Textual reactives (title is reactive on Screen); set via __dict__.
    object.__setattr__(d, "config", dict(initial))
    d.dismiss = MagicMock()
    return d


@pytest.mark.asyncio
async def test_dict_round_trips_through_json(patch_app):
    initial = {"custom_parameters": {"foo": 1, "bar": "baz"}}
    d = _bare_dialog(initial)
    patch_app["app"] = MagicMock()

    # query_one returns a widget whose .value is the JSON the user typed.
    widget = SimpleNamespace(value=json.dumps({"foo": 1, "bar": "baz"}))
    d.query_one = MagicMock(return_value=widget)

    await d.on_button_pressed(_make_event("save-config"))

    d.dismiss.assert_called_once()
    saved = d.dismiss.call_args[0][0]
    assert saved["custom_parameters"] == {"foo": 1, "bar": "baz"}


@pytest.mark.asyncio
async def test_list_round_trips_through_json(patch_app):
    initial = {"compatibility_overrides": ["a", "b"]}
    d = _bare_dialog(initial)
    patch_app["app"] = MagicMock()
    widget = SimpleNamespace(value=json.dumps(["a", "b", "c"]))
    d.query_one = MagicMock(return_value=widget)

    await d.on_button_pressed(_make_event("save-config"))

    saved = d.dismiss.call_args[0][0]
    assert saved["compatibility_overrides"] == ["a", "b", "c"]


@pytest.mark.asyncio
async def test_bool_preserved(patch_app):
    initial = {"debug_mode": False}
    d = _bare_dialog(initial)
    patch_app["app"] = MagicMock()
    widget = SimpleNamespace(value=True)  # Checkbox returns bool
    d.query_one = MagicMock(return_value=widget)

    await d.on_button_pressed(_make_event("save-config"))

    saved = d.dismiss.call_args[0][0]
    assert saved["debug_mode"] is True


@pytest.mark.asyncio
async def test_int_parses_cleanly(patch_app):
    initial = {"profile_duration": 30.0}
    d = _bare_dialog(initial)
    patch_app["app"] = MagicMock()
    widget = SimpleNamespace(value="45.5")
    d.query_one = MagicMock(return_value=widget)

    await d.on_button_pressed(_make_event("save-config"))

    saved = d.dismiss.call_args[0][0]
    assert saved["profile_duration"] == 45.5


@pytest.mark.asyncio
async def test_invalid_number_surfaces_error_and_skips_dismiss(patch_app):
    initial = {"profile_duration": 30.0}
    d = _bare_dialog(initial)
    app = MagicMock()
    patch_app["app"] = app
    widget = SimpleNamespace(value="not-a-number")
    d.query_one = MagicMock(return_value=widget)

    await d.on_button_pressed(_make_event("save-config"))

    d.dismiss.assert_not_called()
    app.log_notification.assert_called()
    severities = [c.kwargs.get("severity") for c in app.log_notification.call_args_list]
    assert "error" in severities


@pytest.mark.asyncio
async def test_cancel_dismisses_none(patch_app):
    d = _bare_dialog({"x": 1})
    patch_app["app"] = MagicMock()

    await d.on_button_pressed(_make_event("cancel-config"))

    d.dismiss.assert_called_once_with(None)
