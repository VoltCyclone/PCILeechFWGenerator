import json
from typing import Any, Dict

from textual.app import ComposeResult
from textual.containers import Container, Horizontal, Vertical
from textual.widgets import Button, Checkbox, Input, Static

from .base import BaseDialog

# Field names whose values are dict/list (rendered as JSON in an Input).
# Kept as a constant so a regression in BuildConfiguration's schema is loud.
JSON_FIELDS = ("custom_parameters", "feature_flags", "compatibility_overrides")


def _widget_id(key: str) -> str:
    return f"config-{key}"


class ConfigurationDialog(BaseDialog[dict | None]):
    """Modal dialog to edit a BuildConfiguration dict.

    Bool fields render as a Checkbox; dict/list fields render as JSON in an
    Input and parse with json.loads on save; int/float fields parse with
    their constructor and surface errors via ``app.log_notification`` (T7
    rename) instead of silently corrupting the value.
    """

    def __init__(self, title: str, config: Dict[str, Any]):
        super().__init__()
        self.title = title
        self.config = dict(config)

    def compose(self) -> ComposeResult:
        with Container(id="config-dialog"):
            yield Static(self.title, classes="dialog-title")

            with Vertical(id="config-items"):
                for key, value in self.config.items():
                    yield Static(key)
                    if isinstance(value, bool):
                        yield Checkbox(value=value, id=_widget_id(key))
                    elif key in JSON_FIELDS or isinstance(value, (dict, list)):
                        yield Input(
                            value=json.dumps(value),
                            id=_widget_id(key),
                        )
                    else:
                        yield Input(value="" if value is None else str(value), id=_widget_id(key))

            with Horizontal(classes="dialog-buttons"):
                yield Button("Cancel", id="cancel-config", variant="default")
                yield Button("Save", id="save-config", variant="primary")

    def _notify_error(self, message: str) -> None:
        try:
            self.app.log_notification(message, severity="error")
        except Exception:
            # Best-effort UI notification — never raise from this path.
            pass

    def _coerce(self, key: str, original: Any, raw: Any) -> Any:
        """Coerce a raw widget value back to the original field type.

        Raises ValueError on parse failure; the caller surfaces it.
        """
        if isinstance(original, bool):
            return bool(raw)
        if key in JSON_FIELDS or isinstance(original, (dict, list)):
            try:
                return json.loads(raw) if isinstance(raw, str) else raw
            except json.JSONDecodeError as exc:
                raise ValueError(f"{key}: invalid JSON ({exc.msg})") from exc
        if isinstance(original, int) and not isinstance(original, bool):
            try:
                return int(raw)
            except (TypeError, ValueError) as exc:
                raise ValueError(f"{key}: expected an integer") from exc
        if isinstance(original, float):
            try:
                return float(raw)
            except (TypeError, ValueError) as exc:
                raise ValueError(f"{key}: expected a number") from exc
        if original is None and raw == "":
            return None
        return raw

    async def on_button_pressed(self, event: Button.Pressed) -> None:
        button_id = event.button.id
        if button_id == "cancel-config":
            self.dismiss(None)
            return
        if button_id != "save-config":
            return

        new_conf: Dict[str, Any] = {}
        for key, original in self.config.items():
            try:
                widget = self.query_one(f"#{_widget_id(key)}")
            except Exception:
                # Widget missing (shouldn't happen) — keep original value
                new_conf[key] = original
                continue
            raw = widget.value
            try:
                new_conf[key] = self._coerce(key, original, raw)
            except ValueError as exc:
                self._notify_error(str(exc))
                return  # leave dialog open so user can fix it

        self.dismiss(new_conf)
