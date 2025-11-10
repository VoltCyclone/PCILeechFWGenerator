#!/usr/bin/env python3
"""CLI components for PCILeech FW Generator."""

# Import non-circular components
from .config import BuildConfig
from .flash import flash_firmware
from .vfio_handler import VFIOBinder

# Define what symbols this package exports
__all__ = [
    "BuildConfig",
    "flash_firmware",
    "VFIOBinder",
    "get_parser",
    "main",
]


# Define functions to import lazily only when needed
def get_parser(*args, **kwargs):
    """Get the CLI parser (forwarded to cli module)."""
    import importlib

    cli = importlib.import_module(".cli", package="src.cli")
    return cli.get_parser(*args, **kwargs)


def main(*args, **kwargs):
    """Main CLI entry point (forwarded to cli module)."""
    import importlib

    cli = importlib.import_module(".cli", package="src.cli")
    return cli.main(*args, **kwargs)

