"""T3: device row selection must extract the underlying RowKey value.

The handler previously did `device.bdf == event.row_key`, which compared a
plain str to Textual's `RowKey` wrapper. In some Textual versions that's
always False, so `selected_device` stayed None and Start Build never
enabled.
"""

from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

import pytest


class _FakeRowKey:
    """RowKey-like wrapper whose `__eq__` does NOT match plain strings.

    Modelled on older Textual versions where the bug surfaced. Hard-rejecting
    str comparison here lets the test catch any regression to direct compare.
    """

    def __init__(self, value: str) -> None:
        self.value = value

    def __eq__(self, other) -> bool:  # noqa: D401
        return isinstance(other, _FakeRowKey) and other.value == self.value

    def __hash__(self) -> int:
        return hash(("_FakeRowKey", self.value))


def _build_app_for(devices):
    """Construct a PCILeechTUI bare enough for on_data_table_row_selected."""
    from pcileechfwgenerator.tui.main import PCILeechTUI

    app = PCILeechTUI.__new__(PCILeechTUI)  # skip __init__ — no Textual loop
    app.app_state = MagicMock()

    # filtered_devices is a computed property that reads from app_state and
    # the device_filters property; stub the property at the class level for
    # this test so we don't need a full AppState wiring.
    PCILeechTUI._test_devices = devices  # type: ignore[attr-defined]
    app.ui_coordinator = MagicMock()
    app.ui_coordinator.handle_device_selection = AsyncMock()
    return app


@pytest.fixture
def patch_filtered_devices(monkeypatch):
    from pcileechfwgenerator.tui.main import PCILeechTUI

    monkeypatch.setattr(
        PCILeechTUI,
        "filtered_devices",
        property(lambda self: getattr(self, "_test_devices", [])),
    )


@pytest.mark.asyncio
async def test_row_selected_matches_against_row_key_value(patch_filtered_devices):
    from pcileechfwgenerator.tui.main import PCILeechTUI

    dev = SimpleNamespace(bdf="0000:01:00.0", is_suitable=True)
    app = _build_app_for([dev])

    event = SimpleNamespace(row_key=_FakeRowKey("0000:01:00.0"))
    await PCILeechTUI.on_data_table_row_selected(app, event)

    app.app_state.set_selected_device.assert_called_once_with(dev)
    app.ui_coordinator.handle_device_selection.assert_awaited_once_with(dev)


@pytest.mark.asyncio
async def test_row_selected_no_match_leaves_state_alone(patch_filtered_devices):
    from pcileechfwgenerator.tui.main import PCILeechTUI

    dev = SimpleNamespace(bdf="0000:02:00.0", is_suitable=True)
    app = _build_app_for([dev])

    event = SimpleNamespace(row_key=_FakeRowKey("0000:99:99.9"))
    await PCILeechTUI.on_data_table_row_selected(app, event)

    app.app_state.set_selected_device.assert_not_called()
    app.ui_coordinator.handle_device_selection.assert_not_awaited()
