"""Every board file tracked in voltcyclone-fpga ships in the wheel and sdist.

The wheel bundles the boards through package-data globs, and the sdist through
MANIFEST.in. A board file type missing from either list would leave a
pip-installed board partial, so the build would fail late or lose an IP file.
"""

import fnmatch
import re
import shutil
import subprocess
import tomllib
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
BOARDS = ROOT / "lib" / "voltcyclone-fpga"
PACKAGE = "pcileechfwgenerator.voltcyclone_fpga"


def _tracked_board_files():
    if not (BOARDS / "CaptainDMA").is_dir() or not shutil.which("git"):
        pytest.skip("voltcyclone-fpga submodule not checked out")
    out = subprocess.run(
        ["git", "-C", str(BOARDS), "ls-files"],
        capture_output=True,
        text=True,
        check=True,
    ).stdout.split()
    # Dotfiles (.gitignore, tool config) are not board inputs.
    return [f for f in out if not any(part.startswith(".") for part in f.split("/"))]


def test_package_data_covers_every_board_file():
    config = tomllib.loads((ROOT / "pyproject.toml").read_text())
    patterns = config["tool"]["setuptools"]["package-data"][PACKAGE]
    suffixes = [p.removeprefix("**/") for p in patterns]
    missing = [
        f
        for f in _tracked_board_files()
        if not any(fnmatch.fnmatch(Path(f).name, s) for s in suffixes)
    ]
    assert missing == [], f"add these types to package-data: {missing}"


def test_manifest_covers_every_board_file():
    manifest = (ROOT / "MANIFEST.in").read_text()
    line = re.search(r"^recursive-include lib/voltcyclone-fpga (.+)$", manifest, re.M)
    assert line, "MANIFEST.in no longer includes the board submodule"
    suffixes = line.group(1).split()
    missing = [
        f
        for f in _tracked_board_files()
        if not any(fnmatch.fnmatch(Path(f).name, s) for s in suffixes)
    ]
    assert missing == [], f"add these types to MANIFEST.in: {missing}"
