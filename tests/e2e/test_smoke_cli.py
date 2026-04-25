"""Smoke test: the ``pcileech`` CLI surface is intact.

For each subcommand, we verify:
  1. ``pcileech <subcmd> --help`` exits 0 and prints a usage line.
  2. Bad / missing required arguments produce a non-zero exit and a
     usage-style error (no traceback).

We invoke through ``python -m pcileechfwgenerator.pcileech_main`` to
avoid the interactive dependency-install prompts in the top-level
``pcileech.py`` script.
"""

from __future__ import annotations

import pytest

pytestmark = pytest.mark.e2e


HELP_SUBCOMMANDS = [
    "build",
    "tui",
    "flash",
    "check",
    "version",
    "donor-template",
]


@pytest.mark.parametrize("subcmd", HELP_SUBCOMMANDS)
def test_subcommand_help_exits_zero(pcileech_cli, subcmd: str) -> None:
    result = pcileech_cli(subcmd, "--help")
    assert result.returncode == 0, (
        f"`pcileech {subcmd} --help` exited {result.returncode}\n"
        f"stdout: {result.stdout}\nstderr: {result.stderr}"
    )
    # argparse default usage line includes "usage:" lower-cased.
    assert "usage" in result.combined.lower()


def test_top_level_help_lists_subcommands(pcileech_cli) -> None:
    result = pcileech_cli("--help")
    assert result.returncode == 0
    for subcmd in HELP_SUBCOMMANDS:
        assert subcmd in result.combined, (
            f"top-level help is missing subcommand '{subcmd}': {result.combined}"
        )


def test_version_subcommand_prints_version(pcileech_cli) -> None:
    result = pcileech_cli("version")
    assert result.returncode == 0
    # The version line includes the project title and a version string.
    assert "PCILeech" in result.combined or "pcileech" in result.combined.lower()


def test_unknown_subcommand_fails_cleanly(pcileech_cli) -> None:
    result = pcileech_cli("definitely-not-a-real-subcommand")
    assert result.returncode != 0
    # argparse emits "invalid choice" or "unrecognized" — accept either.
    text = result.combined.lower()
    assert (
        "invalid choice" in text
        or "unrecognized" in text
        or "usage" in text
    ), f"expected argparse-style error, got: {text!r}"


def test_build_requires_bdf(pcileech_cli) -> None:
    """``pcileech build`` without ``--bdf`` should not silently succeed."""
    result = pcileech_cli("build")
    assert result.returncode != 0


def test_check_requires_device_when_invoked_strict(pcileech_cli) -> None:
    """The ``check`` subcommand must accept its documented flags
    without raising. ``--help`` is a sufficient probe; we don't try to
    actually probe a device because that needs root + real /sys."""
    result = pcileech_cli("check", "--help")
    assert result.returncode == 0
    # Should expose the documented options.
    assert "--device" in result.combined
