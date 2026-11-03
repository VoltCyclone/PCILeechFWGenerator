#!/usr/bin/env python3
"""
CLI entry point module for packaging.

This module provides the main entry point for the installed pcileech package.
"""
from __future__ import annotations

import os
import sys


def _ensure_orchestrator_importable() -> None:
    """Put the top-level ``pcileech`` orchestrator on ``sys.path``.

    ``pcileech.py`` ships as a top-level py-module, so a normal wheel install
    drops it in the site-packages root (always importable). Two cases break a
    bare ``from pcileech import main``: setuptools' editable-install finder does
    not expose top-level py-modules at all, and even a wheel install is not
    importable when the process CWD is not the directory holding the module.

    In both the source tree (``src/pcileech_main.py`` -> repo root) and a wheel
    (``pcileechfwgenerator/pcileech_main.py`` -> site-packages root) the
    orchestrator sits exactly two levels up from this file, so add that
    directory to ``sys.path`` when it actually contains ``pcileech.py``.
    """
    candidate = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    if os.path.isfile(os.path.join(candidate, "pcileech.py")) and (
        candidate not in sys.path
    ):
        # Append (lowest priority) so this never shadows a real top-level
        # module; we only need ``pcileech`` to become findable at all.
        sys.path.append(candidate)


def main() -> int:
    """Main entry point for installed package.

    This delegates to the unified pcileech.py orchestrator which handles
    the 3-stage build flow: host collection -> container templating -> host Vivado.
    """
    try:
        _ensure_orchestrator_importable()
        from pcileech import main as pcileech_main  # type: ignore
        return int(pcileech_main() or 0)
    except ImportError as e:
        print(
            f"Error: Could not import pcileech main function: {e}",
            file=sys.stderr,
        )
        print(
            "Please ensure the package is properly installed.",
            file=sys.stderr,
        )
        return 1


if __name__ == "__main__":  # pragma: no cover
    sys.exit(main())
