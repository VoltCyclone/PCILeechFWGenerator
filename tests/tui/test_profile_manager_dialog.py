"""T4: `ProfileManagerDialog` button methods.

The dialog's button handlers called five methods that didn't exist; every
press raised AttributeError (swallowed silently). These tests pin the new
behaviours: load / delete / export / import / create_new.
"""

from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

import pytest

from pcileechfwgenerator.tui.dialogs.profile_manager import ProfileManagerDialog


@pytest.fixture
def patch_dialog_app(monkeypatch):
    """Replace Screen.app property with a settable attribute for tests."""
    holder: dict = {}

    monkeypatch.setattr(
        ProfileManagerDialog,
        "app",
        property(lambda self: holder["app"]),
    )
    return holder


def _make_dialog(holder, selected_name: str | None = "profile-a"):
    cm = MagicMock()
    cm.list_profiles.return_value = [
        {"name": "profile-a", "description": "", "last_used": "2025-01-01"},
        {"name": "profile-b", "description": "", "last_used": "2024-12-31"},
    ]
    dialog = ProfileManagerDialog.__new__(ProfileManagerDialog)
    dialog.config_manager = cm
    dialog.profiles = cm.list_profiles.return_value
    dialog._refresh_profiles = MagicMock()
    dialog.dismiss = MagicMock()
    dialog._selected_profile_name = MagicMock(return_value=selected_name)

    app = MagicMock()
    app.push_screen_wait = AsyncMock()
    app.notify = MagicMock()
    app.current_config = SimpleNamespace()
    holder["app"] = app
    return dialog, app, cm


@pytest.mark.asyncio
async def test_load_dismisses_with_selected_name(patch_dialog_app):
    dialog, _, _ = _make_dialog(patch_dialog_app, "profile-a")
    await dialog._load_selected_profile()
    dialog.dismiss.assert_called_once_with("profile-a")


@pytest.mark.asyncio
async def test_load_no_selection_notifies_error(patch_dialog_app):
    dialog, app, _ = _make_dialog(patch_dialog_app, selected_name=None)
    await dialog._load_selected_profile()
    dialog.dismiss.assert_not_called()
    app.notify.assert_called()


@pytest.mark.asyncio
async def test_delete_confirms_then_calls_config_manager(patch_dialog_app):
    dialog, app, cm = _make_dialog(patch_dialog_app, "profile-a")
    app.push_screen_wait.return_value = True
    cm.delete_profile.return_value = True

    await dialog._delete_selected_profile()

    cm.delete_profile.assert_called_once_with("profile-a")
    dialog._refresh_profiles.assert_called_once()


@pytest.mark.asyncio
async def test_delete_cancel_skips_delete(patch_dialog_app):
    dialog, app, cm = _make_dialog(patch_dialog_app, "profile-a")
    app.push_screen_wait.return_value = False

    await dialog._delete_selected_profile()

    cm.delete_profile.assert_not_called()


@pytest.mark.asyncio
async def test_export_prompts_then_calls_export_profile(patch_dialog_app, tmp_path):
    dialog, app, cm = _make_dialog(patch_dialog_app, "profile-a")
    target = tmp_path / "out.json"
    app.push_screen_wait.return_value = str(target)
    cm.export_profile.return_value = True

    await dialog._export_selected_profile()

    cm.export_profile.assert_called_once_with("profile-a", Path(str(target)))


@pytest.mark.asyncio
async def test_import_prompts_then_refreshes(patch_dialog_app, tmp_path):
    dialog, app, cm = _make_dialog(patch_dialog_app)
    target = tmp_path / "in.json"
    app.push_screen_wait.return_value = str(target)
    cm.import_profile.return_value = "imported-name"

    await dialog._import_profile()

    cm.import_profile.assert_called_once_with(Path(str(target)))
    dialog._refresh_profiles.assert_called_once()


@pytest.mark.asyncio
async def test_create_new_saves_current_config(patch_dialog_app):
    dialog, app, cm = _make_dialog(patch_dialog_app)
    app.push_screen_wait.return_value = "fresh-profile"
    cm.save_profile.return_value = True

    await dialog._create_new_profile()

    cm.save_profile.assert_called_once_with("fresh-profile", app.current_config)
    dialog._refresh_profiles.assert_called_once()


@pytest.mark.asyncio
async def test_create_new_blank_name_skips_save(patch_dialog_app):
    dialog, app, cm = _make_dialog(patch_dialog_app)
    app.push_screen_wait.return_value = None

    await dialog._create_new_profile()

    cm.save_profile.assert_not_called()


@pytest.mark.asyncio
async def test_export_failure_notifies_error(patch_dialog_app, tmp_path):
    dialog, app, cm = _make_dialog(patch_dialog_app, "profile-a")
    app.push_screen_wait.return_value = str(tmp_path / "out.json")
    cm.export_profile.side_effect = RuntimeError("boom")

    await dialog._export_selected_profile()

    # Last notify call records the failure with severity=error.
    severities = [
        c.kwargs.get("severity") for c in app.notify.call_args_list
    ]
    assert "error" in severities
