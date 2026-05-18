"""T7: persistent-log behaviour moved out from App.notify.

The override previously hijacked Textual's `notify`, so toasts never
appeared and any caller passing an unknown severity was silently shadowed.
After the rename, `log_notification` writes to `#notification-log` and
Textual's `notify` is left intact.
"""

from unittest.mock import MagicMock


from pcileechfwgenerator.tui.main import PCILeechTUI


def test_log_notification_writes_to_richlog():
    """log_notification should append a timestamped line to the RichLog."""
    app = PCILeechTUI.__new__(PCILeechTUI)
    log_widget = MagicMock()
    app.query_one = MagicMock(return_value=log_widget)

    app.log_notification("hello", severity="success")

    log_widget.write.assert_called_once()
    line = log_widget.write.call_args[0][0]
    assert "hello" in line
    assert "SUCCESS" in line


def test_notify_is_textual_builtin_not_overridden():
    """PCILeechTUI must not redefine notify (Textual owns it)."""
    from textual.app import App

    # The class-level method should be inherited from textual.app.App,
    # not redefined on PCILeechTUI itself.
    assert "notify" not in PCILeechTUI.__dict__
    assert PCILeechTUI.notify is App.notify


def test_log_notification_swallows_errors():
    """log_notification must never raise — it's a best-effort path."""
    app = PCILeechTUI.__new__(PCILeechTUI)
    app.query_one = MagicMock(side_effect=RuntimeError("no widget"))

    # Should not propagate.
    app.log_notification("anything")
