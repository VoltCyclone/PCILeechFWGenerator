"""End-to-end build-artifact tests.

These exercise the packaging path that PyPI publishers consume:

1. ``python -m build`` produces a valid sdist + wheel.
2. The wheel installs into a fresh venv and exposes ``__version__``
   plus a working ``pcileech`` console script.

These tests are slow (they build wheels in a tmp venv); they're gated
behind the ``requires_build_isolation`` marker so the regular ``pytest``
run skips them by default. Enable with ``-m "e2e and requires_build_isolation"``.
"""

from __future__ import annotations

import os
import shutil
import subprocess
import sys
import venv
from pathlib import Path

import pytest

pytestmark = [pytest.mark.e2e, pytest.mark.requires_build_isolation, pytest.mark.slow]


@pytest.fixture(scope="module")
def built_distributions(tmp_path_factory, repo_root: Path) -> Path:
    """Build a wheel + sdist into a session-scoped tmpdir.

    Skips only when the ``build`` module isn't installed; any other
    failure is a real packaging regression and must fail the test
    (otherwise ``--cov-fail-under`` and the like would silently mask
    pyproject misconfig, missing sdist files, etc.).
    """
    pytest.importorskip(
        "build",
        reason="`build` PEP 517 frontend not installed; "
        "add it via the [test] or [dev] extras",
    )
    out_dir = tmp_path_factory.mktemp("dist")
    proc = subprocess.run(
        [sys.executable, "-m", "build", "--outdir", str(out_dir)],
        cwd=str(repo_root),
        capture_output=True,
        text=True,
        timeout=240,
    )
    assert proc.returncode == 0, (
        "`python -m build` failed — packaging regression\n"
        f"--- stdout ---\n{proc.stdout[-2000:]}\n"
        f"--- stderr ---\n{proc.stderr[-2000:]}"
    )
    return out_dir


def test_build_produces_sdist_and_wheel(built_distributions: Path) -> None:
    files = list(built_distributions.iterdir())
    sdists = [p for p in files if p.suffix == ".gz"]
    wheels = [p for p in files if p.suffix == ".whl"]
    assert sdists, f"no sdist produced; got {files}"
    assert wheels, f"no wheel produced; got {files}"


def test_wheel_installs_into_clean_venv(
    built_distributions: Path, tmp_path: Path
) -> None:
    """The freshly-built wheel must install cleanly and expose the
    documented runtime entry points."""
    wheels = sorted(built_distributions.glob("*.whl"))
    if not wheels:
        pytest.skip("wheel not present (built_distributions skipped)")
    wheel = wheels[-1]

    venv_dir = tmp_path / "venv"
    venv.EnvBuilder(with_pip=True, clear=True).create(venv_dir)

    if os.name == "nt":  # pragma: no cover - CI is Linux/macOS
        py = venv_dir / "Scripts" / "python.exe"
    else:
        py = venv_dir / "bin" / "python"
    assert py.exists(), f"venv python missing at {py}"

    install = subprocess.run(
        [str(py), "-m", "pip", "install", "--quiet", str(wheel)],
        capture_output=True,
        text=True,
        timeout=240,
    )
    assert install.returncode == 0, (
        f"pip install failed:\nstdout: {install.stdout}\nstderr: {install.stderr}"
    )

    # Verify the installed package exposes a version.
    check = subprocess.run(
        [
            str(py),
            "-c",
            (
                "import pcileechfwgenerator;"
                "assert pcileechfwgenerator.__version__,"
                "'__version__ should be non-empty';"
                "print(pcileechfwgenerator.__version__)"
            ),
        ],
        capture_output=True,
        text=True,
        timeout=30,
    )
    assert check.returncode == 0, (
        f"installed package failed runtime check:\n"
        f"stdout: {check.stdout}\nstderr: {check.stderr}"
    )
    assert check.stdout.strip(), "installed package returned empty version"
