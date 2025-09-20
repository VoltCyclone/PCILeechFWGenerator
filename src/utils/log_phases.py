"""Phase logging utilities to standardize build/status output.

Minimal, dependency-light helper consolidating repeated glyph + formatting
patterns used across build flows. Avoids hardcoding icons in multiple files.
"""

from __future__ import annotations

import time
from typing import Optional

from src.string_utils import log_info_safe, safe_format

GLYPHS = {
    "start": "➤",
    "ok": "✓",
    "warn": "⚠",
    "step": "•",
}


class PhaseLogger:
    """Lightweight phase logger accumulating timing for phases."""

    def __init__(self, logger, emit_durations: bool = True):
        self.logger = logger
        self.emit_durations = emit_durations
        self._active_name: Optional[str] = None
        self._start_ts: float = 0.0

    def begin(self, name: str, message: Optional[str] = None):
        self._finish_if_active()
        self._active_name = name
        self._start_ts = time.perf_counter()
        log_info_safe(
            self.logger,
            safe_format("{g} {msg}", g=GLYPHS["start"], msg=message or name),
            prefix="BUILD",
        )

    def step(self, message: str):
        log_info_safe(
            self.logger,
            safe_format("  {g} {msg}", g=GLYPHS["step"], msg=message),
            prefix="BUILD",
        )

    def success(self, message: Optional[str] = None):
        if not self._active_name:
            return
        dur = time.perf_counter() - self._start_ts
        if self.emit_durations:
            log_info_safe(
                self.logger,
                safe_format(
                    "{g} {name} {ok} ({sec:.1f}s)",
                    g=GLYPHS["ok"],
                    name=self._active_name,
                    ok=message or "done",
                    sec=dur,
                ),
                prefix="BUILD",
            )
        else:
            log_info_safe(
                self.logger,
                safe_format(
                    "{g} {name} {ok}",
                    g=GLYPHS["ok"],
                    name=self._active_name,
                    ok=message or "done",
                ),
                prefix="BUILD",
            )
        self._active_name = None

    def _finish_if_active(self):  # internal safety
        if self._active_name:
            self.success("(auto)")


__all__ = ["PhaseLogger", "GLYPHS"]
