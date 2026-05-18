"""Regression tests for `BackgroundMonitor._monitor_device_changes`.

T5 from `docs/plans/tui-correctness-pass.md`: previously the monitor wrote
to `app._devices`, which the reactive/filter chain does not observe. Fixed
to push through `app_state.set_devices(...)` and re-run the filter pipeline.
"""

from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from pcileechfwgenerator.tui.core.background_monitor import BackgroundMonitor


def _make_device(bdf: str, driver: str = "vfio-pci", is_suitable: bool = True):
    return SimpleNamespace(bdf=bdf, driver=driver, is_suitable=is_suitable)


def _make_app(devices):
    app = MagicMock()
    app.device_manager.scan_devices = AsyncMock(return_value=devices)
    app.app_state = MagicMock()
    app.ui_coordinator = MagicMock()
    return app


async def _run_one_iteration(monitor: BackgroundMonitor) -> None:
    """Drive `_monitor_device_changes` for exactly one loop iteration.

    The loop's `await asyncio.sleep(interval)` is the only suspension point
    between iterations; replacing it with a hook that flips `_running` lets
    us return after a single tick without timing dependencies.
    """

    async def _stop(_interval):
        monitor._running = False

    with patch(
        "pcileechfwgenerator.tui.core.background_monitor.asyncio.sleep",
        side_effect=_stop,
    ):
        await monitor._monitor_device_changes(interval=0)


@pytest.mark.asyncio
async def test_monitor_pushes_devices_through_app_state():
    devices = [_make_device("0000:01:00.0"), _make_device("0000:02:00.0")]
    app = _make_app(devices)
    monitor = BackgroundMonitor(app)

    await _run_one_iteration(monitor)

    app.app_state.set_devices.assert_called_once_with(devices)
    app.ui_coordinator.apply_device_filters.assert_called_once()
    app.ui_coordinator.update_device_table.assert_called_once()


@pytest.mark.asyncio
async def test_monitor_skips_update_when_device_signature_unchanged():
    devices = [_make_device("0000:01:00.0")]
    app = _make_app(devices)
    monitor = BackgroundMonitor(app)

    monitor._last_status["devices_signature"] = (
        ("0000:01:00.0", "vfio-pci", True),
    )

    await _run_one_iteration(monitor)

    app.app_state.set_devices.assert_not_called()
    app.ui_coordinator.apply_device_filters.assert_not_called()
