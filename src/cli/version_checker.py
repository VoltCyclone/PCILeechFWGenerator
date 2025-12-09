#!/usr/bin/env python3
"""Version checker for PCILeech Firmware Generator.

This module checks if the user is running the latest version and prompts
them to update if a newer version is available.
"""

from __future__ import annotations

import json
import os
import sys
from datetime import datetime, timedelta
from pathlib import Path
from typing import Optional, Tuple
from urllib.error import HTTPError
from urllib.request import Request, urlopen

try:
    from ..__version__ import __url__, __version__
    from ..log_config import get_logger
    from ..string_utils import log_info_safe, log_warning_safe
except ImportError:
    # Fallback for direct execution
    sys.path.insert(0, str(Path(__file__).parent.parent))
    from __version__ import __url__, __version__
    from log_config import get_logger
    from src.string_utils import log_info_safe, log_warning_safe

logger = get_logger(__name__)

# Cache file to avoid checking too frequently
CACHE_FILE = Path.home() / ".cache" / "pcileech-fw-generator" / "version_check.json"
CHECK_INTERVAL_DAYS = 1  # Check once per day
GITHUB_API_URL = (
    "https://api.github.com/repos/voltcyclone/PCILeechFWGenerator/releases/latest"
)
PYPI_API_URL = "https://pypi.org/pypi/pcileech-fw-generator/json"
# Env var to control version source behaviour. Defaults to 'github'.
# Allowed: 'github' (only GitHub), 'pypi' (only PyPI), 'auto' (try GitHub then PyPI).
VERSION_SOURCE_ENV = "PCILEECH_VERSION_SOURCE"
USE_CACHE_ENV = "PCILEECH_USE_CACHE"


# Enhanced version checking with build metadata awareness
def get_build_info() -> dict:
    """Get build information from version file."""
    try:
        from ..__version__ import __build_date__, __commit_hash__

        return {"build_date": __build_date__, "commit_hash": __commit_hash__}
    except ImportError:
        return {"build_date": "unknown", "commit_hash": "unknown"}


def parse_version(version_str: str) -> Tuple[int, ...]:
    """Parse version string into tuple of integers for comparison.

    Args:
        version_str: Version string like "0.5.8" or "v0.5.8"

    Returns:
        Tuple of integers (major, minor, patch)
    """
    # Robust integer tuple parsing: strip whitespace, remove leading 'v',
    # ignore build metadata and pre-release suffixes.
    if not version_str or not isinstance(version_str, str):
        return (0, 0, 0)

    s = version_str.strip()
    if s.startswith("v") or s.startswith("V"):
        s = s[1:]

    # Drop build metadata (+...) and prerelease (-...) if present
    for sep in ("+", "-"):
        if sep in s:
            s = s.split(sep, 1)[0]

    parts = s.split(".")
    result: list[int] = []
    for p in parts[:3]:
        num = "".join(ch for ch in p if ch.isdigit())
        if num:
            try:
                result.append(int(num))
            except ValueError:
                result.append(0)
        else:
            result.append(0)

    # Pad to 3 parts
    while len(result) < 3:
        result.append(0)

    return tuple(result)


def is_newer_version(current: str, latest: str) -> bool:
    """Check if latest version is newer than current version.

    Args:
        current: Current version string
        latest: Latest version string

    Returns:
        True if latest is newer than current
    """
    # Prefer packaging.version when available for robust SemVer-like comparisons
    try:
        from packaging.version import InvalidVersion, Version

        try:
            return Version(latest) > Version(current)
        except InvalidVersion:
            # Fall through to tuple-based comparison
            pass
    except Exception:
        # packaging not available - fall back
        pass

    current_tuple = parse_version(current)
    latest_tuple = parse_version(latest)
    return latest_tuple > current_tuple


def get_cached_check() -> Optional[dict]:
    """Get cached version check result if it's still fresh.

    Returns:
        Cached data dict or None if cache is stale/missing
    """
    if not CACHE_FILE.exists():
        return None

    try:
        with open(CACHE_FILE, "r") as f:
            data = json.load(f)

        # Check if cache is still fresh
        last_check = datetime.fromisoformat(data["last_check"])
        if datetime.now() - last_check < timedelta(days=CHECK_INTERVAL_DAYS):
            return data
    except (json.JSONDecodeError, KeyError, ValueError):
        pass

    return None


def save_cache(latest_version: str, update_available: bool):
    """Save version check result to cache.

    Args:
        latest_version: The latest version found
        update_available: Whether an update is available
    """
    try:
        CACHE_FILE.parent.mkdir(parents=True, exist_ok=True)

        data = {
            "last_check": datetime.now().isoformat(),
            "latest_version": latest_version,
            "current_version": __version__,
            "update_available": update_available,
        }

        with open(CACHE_FILE, "w") as f:
            json.dump(data, f)
    except Exception as e:
        # Silently fail if we can't write cache
        logger.debug(f"Failed to save cache: {e}")
        pass


def fetch_latest_version_github() -> Optional[str]:
    """Fetch latest version from GitHub releases API.

    Returns:
        Latest version string or None if fetch fails
    """
    try:
        # Use a User-Agent and optional token to reduce rate-limit issues
        headers = {
            "Accept": "application/vnd.github+json",
            "User-Agent": "pcileech-fw-generator-version-check/1",
        }

        token = os.environ.get("GITHUB_TOKEN") or os.environ.get("GH_TOKEN")
        if token:
            headers["Authorization"] = f"token {token}"

        req = Request(GITHUB_API_URL, headers=headers)
        with urlopen(req, timeout=5) as response:
            # If rate limited, GitHub returns 403 and includes rate limit headers
            data = json.loads(response.read().decode())
            tag = data.get("tag_name", "")
            if tag:
                return tag.lstrip("v")
    except Exception as e:
        # Distinguish HTTP errors for better debug logging
        if isinstance(e, HTTPError):
            try:
                reason = e.read().decode()
            except Exception:
                reason = str(e)
            logger.debug(f"GitHub API HTTPError: {e.code} {reason}")
        else:
            logger.debug(f"Failed to fetch from GitHub: {e}")
        return None


def fetch_latest_version_pypi() -> Optional[str]:
    """Fetch latest version from PyPI API.

    Returns:
        Latest version string or None if fetch fails
    """
    try:
        with urlopen(PYPI_API_URL, timeout=5) as response:
            data = json.loads(response.read().decode())
            return data.get("info", {}).get("version")
    except Exception as e:
        logger.debug(f"Failed to fetch from PyPI: {e}")
        return None


def fetch_latest_version() -> Optional[str]:
    """Fetch latest version from GitHub or PyPI.

    Returns:
        Latest version string or None if all fetches fail
    """
    try:
        source = os.environ.get(VERSION_SOURCE_ENV, "github").lower()

        if source == "github":
            return fetch_latest_version_github()

        if source == "pypi":
            return fetch_latest_version_pypi()

        # auto: try GitHub first, then PyPI
        if source == "auto":
            latest = fetch_latest_version_github()
            if latest:
                return latest
            return fetch_latest_version_pypi()

        # Unknown value: default to GitHub
        return fetch_latest_version_github()
    except Exception as e:
        logger.debug(f"Failed to fetch latest version: {e}")
        return None


def check_for_updates(force: bool = False) -> Optional[Tuple[str, bool]]:
    """Check if a newer version is available.

    Args:
        force: Force check even if cache is fresh

    Returns:
        Tuple of (latest_version, update_available) or None if check fails
    """
    try:
        # Skip check if running in CI/CD or if explicitly disabled
        if os.environ.get("CI") or os.environ.get("PCILEECH_DISABLE_UPDATE_CHECK"):
            return None

        # By default, perform a live network check every run.
        # To retain previous cache-first behavior set PCILEECH_USE_CACHE=1 in environment.
        use_cache = os.environ.get(USE_CACHE_ENV, "0") == "1"
        if use_cache and not force:
            cached = get_cached_check()
            if cached:
                return cached["latest_version"], cached["update_available"]

        # Fetch latest version
        latest_version = fetch_latest_version()
        if not latest_version:
            return None

        # Compare versions
        update_available = is_newer_version(__version__, latest_version)

        # Save to cache
        save_cache(latest_version, update_available)

        return latest_version, update_available
    except Exception as e:
        logger.debug(f"Error checking for updates: {e}")
        return None


def prompt_for_update(latest_version: str):
    """Display update prompt to user.

    Args:
        latest_version: The latest available version
    """
    build_info = get_build_info()

    log_warning_safe(
        logger,
        "\n" + "=" * 60 + "\n"
        "📦 A new version of PCILeech Firmware Generator is available!\n"
        f"   Current version: {__version__}\n"
        f"   Latest version:  {latest_version}\n"
        f"   Build date:      {build_info['build_date']}\n"
        f"   Commit hash:     {build_info['commit_hash']}\n"
        "\n"
        "   Update with one of these commands:\n"
        "   • pip install --upgrade pcileech-fw-generator\n"
        "   • git pull (if installed from source)\n"
        "\n"
        f"   Release notes: {__url__}/releases\n" + "=" * 60 + "\n",
    )


def check_and_notify():
    """Check for updates and notify user if available.

    This is the main entry point for version checking.
    """
    try:
        result = check_for_updates()
        if result:
            latest_version, update_available = result
            if update_available:
                prompt_for_update(latest_version)
    except Exception as e:
        # Never let version checking break the main program
        logger.debug(f"Version check failed: {e}")
        pass


# Add command line argument support
def add_version_args(parser):
    """Add version-related arguments to argument parser.

    Args:
        parser: ArgumentParser instance
    """
    parser.add_argument(
        "--skip-version-check",
        action="store_true",
        help="Skip checking for newer versions",
    )
    parser.add_argument(
        "--check-version", action="store_true", help="Check for newer versions and exit"
    )
