"""T14: viewport shift must restore cursor against the absolute index.

DataTable.rows is an OrderedDict[RowKey, Row], not a list — the previous
`row[0] == current_key` loop indexed into a RowKey object. Verify the new
absolute-index restore.
"""

from types import SimpleNamespace

import pytest
from textual.app import App, ComposeResult

from pcileechfwgenerator.tui.widgets.virtual_device_table import (
    VirtualDeviceTable,
)


def _make_device(i: int):
    return SimpleNamespace(
        bdf=f"0000:{i:02x}:00.0",
        vendor_name="v",
        device_name="d",
        driver=None,
        iommu_group=str(i),
        suitability_score=1.0,
        is_suitable=True,
        status_indicator="",
    )


class _Harness(App):
    table: VirtualDeviceTable

    def compose(self) -> ComposeResult:  # pragma: no cover
        self.table = VirtualDeviceTable(id="dt")
        yield self.table


@pytest.mark.asyncio
async def test_cursor_stays_on_selected_device_after_viewport_shift():
    devices = [_make_device(i) for i in range(120)]
    app = _Harness()
    async with app.run_test() as pilot:
        await pilot.pause()
        table = app.table
        table.set_data(devices)
        await pilot.pause()

        # Place cursor on absolute row 30 (within initial 0..49 viewport).
        table.move_cursor(row=30)
        await pilot.pause()

        # Shift the viewport to start at 25 — absolute 30 is still visible.
        table._update_viewport(25)
        await pilot.pause()

        assert table.visible_start == 25
        # Cursor must follow the device: absolute 30 - new_start 25 = 5.
        assert table.cursor_row == 5


@pytest.mark.asyncio
async def test_cursor_clamps_to_zero_when_selection_scrolls_out():
    devices = [_make_device(i) for i in range(120)]
    app = _Harness()
    async with app.run_test() as pilot:
        await pilot.pause()
        table = app.table
        table.set_data(devices)
        await pilot.pause()

        table.move_cursor(row=5)  # absolute row 5
        await pilot.pause()

        # Jump the viewport far away; absolute 5 is no longer visible.
        table._update_viewport(70)
        await pilot.pause()

        assert table.visible_start == 70
        assert table.cursor_row == 0
