"""Fixtures and helpers shared across the end-to-end test suite.

These tests exercise the CLI surface, packaging, container build, and
template-rendering pipeline against the *installed* package — no fake
imports, no hand-rolled test runner. Tests should be fast enough to live
in the regular CI; anything genuinely slow or hardware-dependent is
gated behind the ``requires_container_runtime`` /
``requires_build_isolation`` / ``hardware`` markers.
"""

from __future__ import annotations

import os
import shutil
import subprocess
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Iterable, Sequence

import pytest


REPO_ROOT = Path(__file__).resolve().parents[2]


@dataclass
class CLIResult:
    """Captured output of a ``pcileech`` CLI invocation."""

    returncode: int
    stdout: str
    stderr: str

    @property
    def combined(self) -> str:
        return f"{self.stdout}\n{self.stderr}"


@pytest.fixture(scope="session")
def repo_root() -> Path:
    return REPO_ROOT


@pytest.fixture(scope="session")
def mock_sysfs_root() -> Path:
    """Path to the prebuilt mock sysfs tree under ``tests/mock_sysfs``."""
    path = REPO_ROOT / "tests" / "mock_sysfs"
    if not path.exists():
        pytest.skip("tests/mock_sysfs/ is missing — cannot run sysfs-backed tests")
    return path


@pytest.fixture(scope="session")
def mock_bdf(mock_sysfs_root: Path) -> str:
    return (mock_sysfs_root / "BDF").read_text().strip()


def _pcileech_cli_command() -> Sequence[str]:
    """Return the argv prefix that invokes the installed ``pcileech`` CLI.

    We deliberately invoke through the installed package entry point
    (``python -m pcileechfwgenerator.pcileech_main``) rather than the
    top-level ``pcileech.py`` script — the script has interactive
    dependency-install prompts that would hang in CI.
    """
    return [sys.executable, "-m", "pcileechfwgenerator.pcileech_main"]


def run_pcileech(
    *args: str,
    env: dict | None = None,
    cwd: Path | None = None,
    timeout: float = 30.0,
) -> CLIResult:
    """Invoke the pcileech CLI and capture its output.

    The default cwd is a session-scoped tmp directory so the command can
    write whatever it likes without polluting the repo. Pass an explicit
    ``cwd`` if a test needs the command to see the repo working tree.
    """
    cmd = list(_pcileech_cli_command()) + list(args)
    base_env = os.environ.copy()
    # Force non-interactive behaviour everywhere.
    base_env["NO_INTERACTIVE"] = "1"
    base_env["PCILEECH_DISABLE_UPDATE_CHECK"] = "1"
    if env:
        base_env.update(env)

    completed = subprocess.run(
        cmd,
        cwd=str(cwd) if cwd else None,
        capture_output=True,
        text=True,
        env=base_env,
        timeout=timeout,
    )
    return CLIResult(
        returncode=completed.returncode,
        stdout=completed.stdout,
        stderr=completed.stderr,
    )


@pytest.fixture
def pcileech_cli():
    """Function fixture — call ``pcileech_cli("subcmd", "--flag", ...)``."""
    return run_pcileech


@pytest.fixture
def isolated_workdir(tmp_path: Path, monkeypatch) -> Path:
    """A clean working directory for an e2e test.

    Each invocation gets its own tmp_path; ``HOME`` is redirected to a
    subdirectory so anything the CLI writes to ``~`` lands in tmp.
    """
    home = tmp_path / "home"
    home.mkdir()
    monkeypatch.setenv("HOME", str(home))
    monkeypatch.setenv("XDG_CONFIG_HOME", str(home / ".config"))
    monkeypatch.setenv("XDG_CACHE_HOME", str(home / ".cache"))
    return tmp_path


@pytest.fixture(scope="session")
def container_runtime() -> str:
    """Return the available container runtime name, or skip."""
    for runtime in ("podman", "docker"):
        if shutil.which(runtime):
            return runtime
    pytest.skip("No container runtime (podman/docker) available")


def collect_iter(path: Path, pattern: str) -> Iterable[Path]:
    """Helper for assertions: yield matching files relative to ``path``."""
    if not path.exists():
        return iter(())
    return path.rglob(pattern)
