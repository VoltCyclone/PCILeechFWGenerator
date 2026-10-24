"""Unsupported donor-dump requests stop before kernel/module operations."""

from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

import pytest
from pcileechfwgenerator.tui.core.ui_coordinator import UICoordinator
from pcileechfwgenerator.tui.models.configuration import BuildConfiguration

def _make_app():
    app = MagicMock()
    app.selected_device = SimpleNamespace(bdf="0000:01:00.0", is_suitable=True)
    app.current_config = BuildConfiguration(
        board_type="pcileech_75t484_x1", donor_dump=True
    )
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
async def test_unsupported_donor_dump_aborts_before_install():
    app = _make_app()
    coord = UICoordinator(app)
    coord._validate_donor_module = AsyncMock(return_value=False)

    await coord.handle_build_start()

    coord._validate_donor_module.assert_not_called()
    app.build_orchestrator.start_build.assert_not_called()
    app.error_handler.handle_operation_error.assert_called_once()
    assert "donor_dump" in str(
        app.error_handler.handle_operation_error.call_args.args[1]
    )


@pytest.mark.asyncio
async def test_supported_configuration_starts_without_module_install():
    app = _make_app()
    app.current_config.donor_dump = False
    coord = UICoordinator(app)
    coord._validate_donor_module = AsyncMock(return_value=True)

    await coord.handle_build_start()

    coord._validate_donor_module.assert_not_called()
    app.build_orchestrator.start_build.assert_awaited_once()
