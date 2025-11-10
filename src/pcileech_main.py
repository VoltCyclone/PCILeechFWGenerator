#!/usr/bin/env python3
"""
CLI entry point module for packaging.

This module provides the main entry point for the installed pcileech package.
"""
from __future__ import annotations

import sys
from pathlib import Path


def main() -> int:
    """Main entry point for installed package.

    This delegates to the unified pcileech.py orchestrator which handles
    the 3-stage build flow: host collection -> container templating -> host Vivado.
    """
    # Add project root to path for development
    here = Path(__file__).resolve()
    project_root = here.parents[1]
    src_dir = project_root / "src"
    
    for p in (project_root, src_dir):
        s = str(p)
        if s not in sys.path:
            sys.path.insert(0, s)

    # Import from root-level pcileech.py (unified orchestrator)
    try:
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
