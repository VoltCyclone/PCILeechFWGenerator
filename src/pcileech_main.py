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

    When installed via pip, this delegates to the build module's main function.
    For development, it tries to import from the root pcileech.py script.
    """
    # Try to import from package first (installed package path)
    try:
        from pcileechfwgenerator.build import main as build_main
        return int(build_main() or 0)
    except ImportError:
        pass

    # Fall back to development mode - try root-level pcileech.py
    try:
        # Add project root to path for development
        here = Path(__file__).resolve()
        project_root = here.parents[1]
        src_dir = project_root / "src"
        
        for p in (project_root, src_dir):
            s = str(p)
            if s not in sys.path:
                sys.path.insert(0, s)

        # Import from root-level pcileech.py (development mode)
        from pcileech import main as root_main  # type: ignore
        return int(root_main() or 0)
    except ImportError as e:
        print(f"Error: Could not import main function: {e}", file=sys.stderr)
        print("Please ensure the package is properly installed.", file=sys.stderr)
        return 1


if __name__ == "__main__":  # pragma: no cover
    sys.exit(main())
