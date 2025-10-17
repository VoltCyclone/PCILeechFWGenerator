#!/usr/bin/env python3
"""
CLI entry point module for packaging.

This thin wrapper ensures console_scripts defined in pyproject.toml
resolve to a module inside the src/ package layout, avoiding conflicts
with package discovery.
"""
from __future__ import annotations

import sys

from pathlib import Path


def main() -> int:
    """Delegate to the top-level pcileech.py for now.

    We keep the rich logic in pcileech.py to avoid risk. This module only
    adjusts sys.path so imports continue to work when invoked as an installed
    console script.
    """
    # Ensure project root and src on path when running from an installed entry point
    here = Path(__file__).resolve()
    project_root = here.parents[1]
    src_dir = project_root / "src"
    for p in (project_root, src_dir):
        s = str(p)
        if s not in sys.path:
            sys.path.insert(0, s)

    # Import and dispatch to the existing main
    from pcileech import main as root_main  # type: ignore

    return int(root_main() or 0)


if __name__ == "__main__":  # pragma: no cover
    sys.exit(main())
