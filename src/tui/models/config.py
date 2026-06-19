"""Backward-compatibility shim for the TUI configuration models.

The canonical models now live in
:mod:`pcileechfwgenerator.tui.models.configuration` as Pydantic models with
validation. This module previously defined a parallel dataclass
``BuildConfiguration``/``BuildProgress`` with the same fields; those were
consolidated to remove the duplication. Import from ``configuration`` (or the
``models`` package) directly in new code.
"""

from pcileechfwgenerator.tui.models.configuration import (  # noqa: F401
    BuildConfiguration,
    BuildProgress,
)

__all__ = ["BuildConfiguration", "BuildProgress"]
