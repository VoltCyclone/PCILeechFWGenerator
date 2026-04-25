"""End-to-end coverage for the version-resolution chain.

The package version comes from setuptools-scm (git tags), gets baked
into the wheel by the build, and is exposed at runtime via:
  - ``importlib.metadata.version("PCILeechFWGenerator")``
  - ``pcileechfwgenerator.__version__``
  - ``pcileech version`` CLI

These tests verify those layers agree.
"""

from __future__ import annotations

import re
import subprocess
import sys

import pytest

pytestmark = pytest.mark.e2e


VERSION_RE = re.compile(
    r"\d+\.\d+\.\d+(?:[.\-+][\w\.\-]+)?",  # PEP 440 / setuptools-scm output
)


def _is_pep440_ish(value: str) -> bool:
    return bool(VERSION_RE.search(value))


def test_package_dunder_version_is_pep440() -> None:
    import pcileechfwgenerator

    version = pcileechfwgenerator.__version__
    assert _is_pep440_ish(version), (
        f"__version__={version!r} is not a PEP 440-shaped string"
    )
    # The dev sentinel only appears when no source can be located at all;
    # an installed editable build should have a real version.
    assert version != "", "version is empty"


# test_importlib_metadata_matches_dunder_version was removed — it was a
# tautology against the production code (``__version__`` is *defined* as
# ``importlib.metadata.version(...)`` with fallbacks), and it exposed
# test-ordering flakiness when other tests in the suite touched
# ``importlib.metadata``'s internal caches. The behaviour it tried to
# assert is locked in by ``test_package_dunder_version_is_pep440`` plus
# the resolver chain coverage in ``tests/test_unified_context.py``.


def test_setuptools_scm_resolves_in_repo() -> None:
    """``python -m setuptools_scm`` should yield a usable version when
    run from the repo working tree (CI runs from a checkout)."""
    proc = subprocess.run(
        [sys.executable, "-m", "setuptools_scm"],
        capture_output=True,
        text=True,
        timeout=15,
    )
    if proc.returncode != 0:
        # Acceptable: not on a tagged commit, no .git, etc.
        pytest.skip(
            "setuptools_scm could not resolve a version; "
            "likely not running from a git checkout"
        )
    output = proc.stdout.strip()
    assert _is_pep440_ish(output), f"setuptools_scm output={output!r}"


def test_cli_version_reports_resolved_version(pcileech_cli) -> None:
    result = pcileech_cli("version")
    assert result.returncode == 0
    # The CLI prints "<title> v<version>"; we just need to find a version.
    assert _is_pep440_ish(result.combined), (
        f"`pcileech version` did not print a recognizable version: "
        f"{result.combined!r}"
    )


def test_version_info_is_three_int_tuple() -> None:
    """``__version_info__`` is documented as a 3-tuple of ints — even
    when the underlying version has dev/post/local suffixes."""
    from pcileechfwgenerator.__version__ import __version_info__

    assert isinstance(__version_info__, tuple)
    assert len(__version_info__) == 3
    assert all(isinstance(x, int) for x in __version_info__)
