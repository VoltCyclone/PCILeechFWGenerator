#!/usr/bin/env python3
"""
Centralized version resolution for PCILeech Firmware Generator.

The package version is derived from git tags by setuptools-scm. This module
exposes the resolved value plus a small bundle of project metadata for
callers that need a single dict (CLI banners, error reports, etc.).

Resolution order:
  1. ``importlib.metadata.version("PCILeechFWGenerator")`` — works for any
     installed wheel or ``pip install -e .`` checkout.
  2. ``src/_version.py`` — written by setuptools-scm at build /
     editable-install time. The file is gitignored.
  3. ``git describe`` — last resort for raw source checkouts that haven't
     been pip-installed.
"""

from __future__ import annotations

import re
from pathlib import Path
from typing import Optional

from pcileechfwgenerator.log_config import get_logger
from pcileechfwgenerator.string_utils import log_debug_safe

logger = get_logger("version_resolver")


def get_package_version() -> str:
    """Return the package version string."""
    for resolver in (_try_importlib_metadata, _try_scm_version_file, _try_git_describe):
        try:
            value = resolver()
        except Exception as exc:  # pragma: no cover - defensive
            log_debug_safe(
                logger,
                f"Version resolver {getattr(resolver, '__name__', resolver)!s} raised: {exc}",
                prefix="VERSION",
            )
            continue
        if value:
            return value
    return "0.0.0+unknown"


def _try_importlib_metadata() -> Optional[str]:
    from importlib.metadata import PackageNotFoundError, version

    try:
        return version("PCILeechFWGenerator")
    except PackageNotFoundError:
        return None


def _try_scm_version_file() -> Optional[str]:
    src_dir = Path(__file__).resolve().parent.parent
    version_file = src_dir / "_version.py"
    if not version_file.exists():
        return None
    namespace: dict = {}
    exec(version_file.read_text(), namespace)
    value = namespace.get("version") or namespace.get("__version__")
    return str(value) if value else None


def _try_git_describe() -> Optional[str]:
    import subprocess

    project_root = Path(__file__).resolve().parents[2]
    if not (project_root / ".git").exists():
        return None
    try:
        result = subprocess.run(
            ["git", "describe", "--tags", "--dirty", "--always"],
            cwd=project_root,
            capture_output=True,
            text=True,
            timeout=5,
            check=False,
        )
    except (OSError, subprocess.SubprocessError):
        return None
    raw = result.stdout.strip()
    if not raw:
        return None
    match = re.match(r"v?(\d+\.\d+\.\d+(?:[+\-][0-9A-Za-z\.\-]+)?)", raw)
    return match.group(1) if match else raw


def get_version_info() -> dict:
    """Return a metadata dict for CLI banners and diagnostics."""
    from pcileechfwgenerator.__version__ import (
        __author__,
        __description__,
        __license__,
        __title__,
        __url__,
    )

    return {
        "version": get_package_version(),
        "title": __title__,
        "description": __description__,
        "author": __author__,
        "license": __license__,
        "url": __url__,
    }
