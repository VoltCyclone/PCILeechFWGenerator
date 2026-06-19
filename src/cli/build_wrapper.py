#!/usr/bin/env python3
"""Thin wrapper that delegates to the installed build module.

Historically this script manipulated ``sys.path`` and ``os.chdir``-ed into
``src/`` so that loose-checkout relative imports would resolve. The project is
now an installed package (``pcileechfwgenerator``) with absolute imports, so the
path/cwd juggling is unnecessary — we simply import and delegate. The container
invokes ``python3 -m pcileechfwgenerator.build`` directly; this wrapper is kept
only for backward-compatible ``build_wrapper`` invocations.
"""

import sys

from pcileechfwgenerator import build


def main() -> int:
    """Run the build module's entry point and return its exit code."""
    sys.argv[0] = "build.py"  # Fix the script name for argument parsing
    return build.main() or 0


if __name__ == "__main__":
    sys.exit(main())
