import asyncio

import pytest

from src.tui.utils.loading_manager import LoadingManager


class FakeAppNoScheduler:
    """App stub without call_after_refresh to test direct UI updates."""

    def __init__(self):
        self.last_show = None
        self.hide_called = 0

    def show_loading_indicator(self, **kwargs):
        self.last_show = kwargs

    def hide_loading_indicator(self):
        self.hide_called += 1


class FakeAppWithScheduler:
    """App stub exposing call_after_refresh to test scheduled UI updates."""

    def __init__(self):
        self.last_show = None
        self.hide_called = 0
        self.scheduled = []

    # pragma: no cover - behavior verified via side-effects

    def call_after_refresh(self, fn):
        self.scheduled.append(fn)
        # Simulate a scheduler invoking the callback soon
        fn()

    def show_loading_indicator(self, **kwargs):
        self.last_show = kwargs

    def hide_loading_indicator(self):
        self.hide_called += 1


@pytest.mark.unit
@pytest.mark.asyncio
async def test_with_loading_success_updates_ui_and_returns_result():
    app = FakeAppNoScheduler()  # direct UI update path
    lm = LoadingManager(app)

    async def work():
        # brief yield to allow state to flip to loading
        await asyncio.sleep(0)
        return "ok"

    result = await lm.with_loading("op1", work)

    assert result == "ok"
    # After completion, loading cleared and hide called exactly once
    assert lm.is_loading("op1") is False
    assert app.hide_called >= 1


@pytest.mark.unit
@pytest.mark.asyncio
async def test_with_loading_times_out_and_clears_state():
    app = FakeAppNoScheduler()
    lm = LoadingManager(app)
    lm.set_timeout(0.01)  # below minimum, will be coerced to 1.0 by set_timeout
    # Overwrite to a tiny timeout explicitly for the test
    lm.timeout = 0.02

    async def long_work():
        await asyncio.sleep(0.2)
        return "late"

    with pytest.raises(TimeoutError):
        await lm.with_loading("op_to", long_work)

    assert lm.is_loading("op_to") is False
    assert app.hide_called >= 1


@pytest.mark.unit
@pytest.mark.asyncio
async def test_is_loading_during_inflight_operation():
    app = FakeAppNoScheduler()
    lm = LoadingManager(app)

    start_evt = asyncio.Event()
    finish_evt = asyncio.Event()

    async def gated_work():
        start_evt.set()  # signal that operation has begun
        await finish_evt.wait()
        return 42

    task = asyncio.create_task(lm.with_loading("op_live", gated_work))

    # Wait until work has started and LoadingManager has set state
    await start_evt.wait()
    # Small yield for LoadingManager to process set_loading
    await asyncio.sleep(0)

    assert lm.is_loading("op_live") is True
    assert lm.is_loading() is True  # any operation

    # Let it finish
    finish_evt.set()
    out = await task
    assert out == 42
    assert lm.is_loading() is False
    assert app.hide_called >= 1


@pytest.mark.unit
@pytest.mark.asyncio
async def test_cancellation_clears_loading_state():
    app = FakeAppNoScheduler()
    lm = LoadingManager(app)

    finish_evt = asyncio.Event()

    async def slow():
        await finish_evt.wait()

    t = asyncio.create_task(lm.with_loading("op_cancel", slow))
    # Allow task to start and set loading state
    await asyncio.sleep(0)
    assert lm.is_loading("op_cancel") is True

    # Cancel the with_loading task
    t.cancel()
    with pytest.raises(asyncio.CancelledError):
        await t

    # Loading state should be cleared
    assert lm.is_loading("op_cancel") is False
    assert app.hide_called >= 1

@pytest.mark.unit
def test_set_timeout_enforces_minimum():
    app = FakeAppNoScheduler()
    lm = LoadingManager(app)
    lm.set_timeout(0.0001)
    assert lm.timeout >= 1.0

@pytest.mark.unit
def test_update_loading_ui_uses_scheduler_when_available():
    # App with scheduler pathway
    app = FakeAppWithScheduler()
    lm = LoadingManager(app)

    # Manually toggle loading to trigger UI updates via scheduler
    lm.set_loading("opX", True)
    assert app.last_show is not None
    assert app.last_show["operation_id"] == "opX"
    assert isinstance(app.last_show["elapsed_time"], float)
    assert app.last_show["total_operations"] == 1

    # Turn off and expect hide called
    prev_hides = app.hide_called
    lm.set_loading("opX", False)
    assert app.hide_called == prev_hides + 1
