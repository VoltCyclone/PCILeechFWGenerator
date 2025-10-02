#!/usr/bin/env python3
"""
Centralized version resolution for PCILeech Firmware Generator.

This module provides the single source of truth for version information,
eliminating duplication across the codebase.
"""

import os
from pathlib import Path
from typing import Optional

from src.log_config import get_logger
from src.string_utils import log_debug_safe

# Module logger for consistent logging
logger = get_logger("version_resolver")


def get_package_version() -> str:
    """
    Get the package version dynamically.

    This is the canonical version resolution function that all other
    modules should use. It tries multiple methods in order of preference:

    1. From __version__.py in the src directory (most reliable)
    2. From setuptools_scm if available
    3. From importlib.metadata
    4. Git describe as last resort
    5. Final fallback to "unknown"

    Returns:
        str: The package version
    """
    # Try __version__.py first (most reliable)
    version = _try_version_file()
    if version:
        return version

    # Try setuptools_scm
    version = _try_setuptools_scm()
    if version:
        return version

    # Try importlib.metadata
    version = _try_importlib_metadata()
    if version:
        return version

    # Try git describe as last resort
    version = _try_git_describe()
    if version:
        return version

    # Final fallback
    log_debug_safe(
        logger,
        "All version resolution methods failed, using fallback",
        prefix="VERSION",
    )
    return "unknown"


def _try_version_file() -> Optional[str]:
    """Try to get version from __version__.py file."""
    try:
        src_dir = Path(__file__).parent.parent
        version_file = src_dir / "__version__.py"

        if version_file.exists():
            version_dict = {}
            with open(version_file, "r") as f:
                exec(f.read(), version_dict)
            if "__version__" in version_dict:
                return version_dict["__version__"]
    except Exception as e:
        log_debug_safe(logger, f"Error reading __version__.py: {e}", prefix="VERSION")
    return None


def _try_setuptools_scm() -> Optional[str]:
    """Try to get version from setuptools_scm."""
    try:
        from setuptools_scm import get_version  # type: ignore

        return get_version(root="../..")
    except Exception as e:
        log_debug_safe(logger, f"Error with setuptools_scm: {e}", prefix="VERSION")
    return None


def _try_importlib_metadata() -> Optional[str]:
    """Try to get version from importlib.metadata."""
    try:
        from importlib.metadata import version

        return version("PCILeechFWGenerator")
    except Exception as e:
        log_debug_safe(logger, f"Error with importlib.metadata: {e}", prefix="VERSION")
    return None


def _try_git_describe() -> Optional[str]:
    """Try to get version from git describe."""
    try:
        import re
        import subprocess

        project_root = Path(__file__).parent.parent.parent
        result = subprocess.run(
            ["git", "describe", "--tags", "--dirty", "--always"],
            cwd=project_root,
            capture_output=True,
            text=True,
            timeout=5,
        )
        if result.returncode == 0 and result.stdout.strip():
            git_version = result.stdout.strip()
            # Clean up git version format
            version_match = re.match(r"v?(\d+\.\d+\.\d+)", git_version)
            if version_match:
                return version_match.group(1)
            return git_version
    except Exception as e:
        log_debug_safe(logger, f"Error with git describe: {e}", prefix="VERSION")
    return None


def get_version_info() -> dict:
    """
    Get comprehensive version information.

    Returns:
        dict: Version information including version string and metadata
    """
    version = get_package_version()

    # Try to get build info from __version__.py
    build_info = {}
    try:
        src_dir = Path(__file__).parent.parent
        version_file = src_dir / "__version__.py"

        if version_file.exists():
            version_dict = {}
            with open(version_file, "r") as f:
                exec(f.read(), version_dict)

            build_info = {
                "build_date": version_dict.get("__build_date__", "unknown"),
                "commit_hash": version_dict.get("__commit_hash__", "unknown"),
                "title": version_dict.get("__title__", "PCILeech Firmware Generator"),
                "description": version_dict.get("__description__", ""),
                "author": version_dict.get("__author__", ""),
                "license": version_dict.get("__license__", "MIT"),
                "url": version_dict.get("__url__", ""),
            }
    except Exception:
        pass

    return {"version": version, **build_info}
