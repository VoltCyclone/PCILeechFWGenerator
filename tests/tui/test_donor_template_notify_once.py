"""T8: success notification fires exactly once.

Both the coordinator's `generate_donor_template` and main's
`_generate_donor_template` emitted the same "Donor template saved" line.
After the dedup, only the coordinator's notification carries the result;
main may add a *different* hint line.
"""

from pathlib import Path
from unittest.mock import AsyncMock, MagicMock

import pytest

from pcileechfwgenerator.tui.main import PCILeechTUI


@pytest.mark.asyncio
async def test_success_notification_does_not_duplicate():
    app = PCILeechTUI.__new__(PCILeechTUI)
    app.ui_coordinator = MagicMock()
    app.ui_coordinator.generate_donor_template = AsyncMock(
        return_value=Path("donor_info_template.json")
    )
    app.log_notification = MagicMock()

    await PCILeechTUI._generate_donor_template(app)

    saved_msgs = [
        c.args[0]
        for c in app.log_notification.call_args_list
        if "saved" in c.args[0].lower()
    ]
    # The "saved" line lives in the coordinator — which is mocked here, so
    # zero "saved" calls on main's log_notification means no duplicate.
    assert saved_msgs == []
    # The hint still fires.
    hint_msgs = [
        c.args[0]
        for c in app.log_notification.call_args_list
        if "Fill in" in c.args[0]
    ]
    assert len(hint_msgs) == 1


@pytest.mark.asyncio
async def test_failure_emits_no_main_side_hint():
    app = PCILeechTUI.__new__(PCILeechTUI)
    app.ui_coordinator = MagicMock()
    app.ui_coordinator.generate_donor_template = AsyncMock(return_value=None)
    app.log_notification = MagicMock()

    await PCILeechTUI._generate_donor_template(app)

    app.log_notification.assert_not_called()
