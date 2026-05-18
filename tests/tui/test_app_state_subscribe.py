"""T6: `AppState.update_state` must not expose the live state dict.

Subscribers previously received the live `self._state`, so any subscriber
that triggered a re-entrant update or mutated the dict could corrupt the
iteration. Verify they now receive a copy.
"""

from pcileechfwgenerator.tui.core.app_state import AppState


def test_subscriber_receives_independent_copy():
    state = AppState()
    captured: dict = {}

    def cb(old, new):
        captured["new"] = new
        # Try to mutate the snapshot — must not affect the live store.
        new["devices"] = "mutated-by-subscriber"

    state.subscribe(cb)
    state.set_devices(["a", "b"])

    assert captured["new"] != state.get_state("devices")
    assert state.get_state("devices") == ["a", "b"]


def test_reentrant_update_does_not_corrupt_iteration():
    """A subscriber that triggers update_state mid-notification must not
    cause the outer iteration to misfire."""
    state = AppState()
    calls: list[str] = []

    def first(_old, _new):
        calls.append("first")
        # Re-entrant call inside iteration — would explode if the
        # subscriber list weren't snapshotted and the state weren't copied.
        if "reentered" not in calls:
            calls.append("reentered")
            state.set_selected_device("dev-1")

    def second(_old, _new):
        calls.append("second")

    state.subscribe(first)
    state.subscribe(second)
    state.set_devices(["a"])

    # Both subscribers ran for the outer update, and the inner update
    # delivered to both as well.
    assert calls.count("first") >= 2
    assert calls.count("second") >= 2
