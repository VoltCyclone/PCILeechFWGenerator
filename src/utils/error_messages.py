"""
Centralized error message strings.

Keep these messages in one place to ensure consistency and easy reuse.
"""

from typing import Final

# Metadata/version resolution errors
META_ERR_READ_VERSION_FILE: Final[str] = "Error reading __version__.py: {err}"
META_ERR_SETTOOLS_SCM: Final[str] = "Error getting version from setuptools_scm: {err}"
META_ERR_IMPORTLIB_METADATA: Final[str] = (
    "Error getting version from importlib.metadata: {err}"
)
