"""T2: `handle_build_start` must honor a False return from `_validate_donor_module`.

Previously the coordinator discarded the validator's return value and started
the build even when the user cancelled the install confirmation dialog.
"""

from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

import pytest

from pcileechfwgenerator.tui.core.ui_coordinator import UICoordinator


def _make_app():
    app = MagicMock()
    app.selected_device = SimpleNamespace(bdf="0000:01:00.0", is_suitable=True)
    app.current_config = SimpleNamespace(donor_dump=True, local_build=False)
    app.build_orchestrator = MagicMock()
    app.build_orchestrator.is_building.return_value = False
    app.build_orchestrator.start_build = AsyncMock(return_value=True)
    app.device_manager = MagicMock()
    app.config_manager = MagicMock()
    app.status_monitor = MagicMock()
    # query_one returns a widget mock with a settable .disabled
    app.query_one.return_value = MagicMock(disabled=False)
    return app


@pytest.mark.asyncio
async def test_donor_validation_cancel_aborts_build():
    app = _make_app()
    coord = UICoordinator(app)
    coord._validate_donor_module = AsyncMock(return_value=False)

    await coord.handle_build_start()

    app.build_orchestrator.start_build.assert_not_called()
    # The cancel notice fires through the persistent log; don't pin text.
    assert app.log_notification.called or app.notify.called


@pytest.mark.asyncio
async def test_donor_validation_pass_starts_build():
    app = _make_app()
    coord = UICoordinator(app)
    coord._validate_donor_module = AsyncMock(return_value=True)

    await coord.handle_build_start()

    app.build_orchestrator.start_build.assert_awaited_once()
