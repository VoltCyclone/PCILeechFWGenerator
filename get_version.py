#!/usr/bin/env python3
"""Simple script to get the current version for use in shell scripts."""

import sys
from pathlib import Path

# Add src to path
sys.path.insert(0, str(Path(__file__).parent / "src"))

from pcileechfwgenerator.__version__ import __title__, __version__

print(f"{__title__} v{__version__}")
