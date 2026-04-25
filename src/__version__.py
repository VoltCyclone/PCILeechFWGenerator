#!/usr/bin/env python3
"""Version information for PCILeech Firmware Generator.

The version itself is derived from git tags via setuptools-scm. This file
just exposes a stable runtime accessor; see ``[tool.setuptools_scm]`` in
``pyproject.toml`` for the build-time configuration.

Resolution order:
  1. ``importlib.metadata`` (works for installed wheels and editable installs)
  2. ``src/_version.py`` (written by setuptools-scm during ``pip install``)
  3. A development sentinel
"""

from __future__ import annotations

from importlib.metadata import PackageNotFoundError
from importlib.metadata import version as _pkg_version


def _resolve_version() -> str:
    try:
        return _pkg_version("PCILeechFWGenerator")
    except PackageNotFoundError:
        pass
    try:
        from ._version import version as _scm_version  # type: ignore[attr-defined]

        return _scm_version
    except ImportError:
        return "0.0.0+unknown"


def _resolve_version_tuple() -> tuple:
    """Return ``(major, minor, patch)`` as integers.

    Strips any local/dev/post suffix so callers get a stable 3-tuple even
    on a development checkout where the version may be e.g.
    ``0.14.15.post1.dev6+gaf607a9``.
    """
    parts = __version__.split("+", 1)[0].split(".")
    out: list = []
    for part in parts:
        try:
            out.append(int(part))
        except ValueError:
            break
        if len(out) == 3:
            break
    while len(out) < 3:
        out.append(0)
    return tuple(out[:3])


__version__: str = _resolve_version()
__version_info__: tuple = _resolve_version_tuple()

# Static project metadata. The version is dynamic; everything else here is
# canonical and rarely changes.
__title__ = "PCILeech Firmware Generator"
__description__ = "Generate spoofed PCIe DMA firmware from real donor hardware"
__author__ = "Ramsey McGrath"
__author_email__ = "ramsey@voltcyclone.info"
__license__ = "MIT"
__url__ = "https://github.com/voltcyclone/PCILeechFWGenerator"
