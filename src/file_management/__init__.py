#!/usr/bin/env python3
"""
File Management Package

This package contains modules for managing files, repositories, and related operations
for the PCILeech firmware generator.

Modules:
- file_manager: Handles file operations, cleanup, and validation
- repo_manager: Manages repository cloning, updates, and queries
- donor_dump_manager: Manages donor dump kernel module and file operations
- option_rom_manager: Manages Option-ROM file extraction and preparation
- board_discovery: Dynamically discovers boards from pcileech-fpga repository
"""

from .board_discovery import *  # noqa: F401, F403
from .donor_dump_manager import *  # noqa: F401, F403
from .file_manager import *  # noqa: F401, F403
from .option_rom_manager import *  # noqa: F401, F403
from .repo_manager import *  # noqa: F401, F403

__all__ = [
    # Re-export all public symbols from submodules
    # This will be populated automatically by the import * statements above
]
