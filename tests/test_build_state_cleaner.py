#!/usr/bin/env python3
"""Tests for the pre-build Vivado state cleaner."""

from __future__ import annotations

import importlib.util
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[1]
SRC_DIR = PROJECT_ROOT / "src"
MODULE_PATH = SRC_DIR / "vivado_handling" / "build_state_cleaner.py"

spec = importlib.util.spec_from_file_location(
    "vivado_handling.build_state_cleaner",
    MODULE_PATH,
)
build_state_cleaner = importlib.util.module_from_spec(spec)
assert spec and spec.loader  # pragma: no cover - importlib contract
spec.loader.exec_module(build_state_cleaner)

clean_stale_build_state = build_state_cleaner.clean_stale_build_state


def _make_stale_tree(root: Path) -> dict:
    """Populate ``root`` with the kind of state Vivado leaves between runs.

    Returns the dict of paths so individual assertions can target each one.
    """
    root.mkdir(parents=True, exist_ok=True)

    project_dir = root / "vivado_project"
    (project_dir / "sources_1" / "ip" / "fifo_64_64").mkdir(parents=True)
    (project_dir / "sources_1" / "ip" / "fifo_64_64" / "fifo_64_64.xci").write_text(
        "locked"
    )
    (project_dir / "pcileech.xpr").write_text("project")

    xil_top = root / ".Xil"
    xil_top.mkdir()
    (xil_top / "scratch.tmp").write_text("temp")

    xil_nested = project_dir / ".Xil"
    xil_nested.mkdir(parents=True)
    (xil_nested / "more.tmp").write_text("temp")

    (root / "vivado.jou").write_text("journal")
    (root / "vivado_pid12345.str").write_text("strategy")

    src_dir = root / "src"
    src_dir.mkdir()
    (src_dir / "pcileech_fifo.sv").write_text("// source")

    ip_dir = root / "ip"
    ip_dir.mkdir()
    (ip_dir / "core.xci").write_text("ip core")

    (root / "pcileech.bit").write_text("bitstream")
    (root / "pcileech.mcs").write_text("flash")
    (root / "timing.rpt").write_text("timing report")
    (root / "vivado.log").write_text("log")
    (root / "build.tcl").write_text("script")
    (root / "manifest.json").write_text("{}")

    return {
        "project_dir": project_dir,
        "xil_top": xil_top,
        "xil_nested": xil_nested,
        "jou": root / "vivado.jou",
        "str": root / "vivado_pid12345.str",
        "src_dir": src_dir,
        "ip_dir": ip_dir,
        "bit": root / "pcileech.bit",
        "mcs": root / "pcileech.mcs",
        "rpt": root / "timing.rpt",
        "log": root / "vivado.log",
        "tcl": root / "build.tcl",
        "manifest": root / "manifest.json",
    }


def test_clean_stale_build_state_removes_vivado_artifacts(tmp_path):
    """Vivado project + .Xil dirs + jou/str files are wiped."""
    paths = _make_stale_tree(tmp_path)

    summary = clean_stale_build_state(tmp_path)

    assert not paths["project_dir"].exists()
    assert not paths["xil_top"].exists()
    # Nested .Xil lived under vivado_project/, which we removed above — but
    # the cleaner must also handle .Xil dirs that aren't inside vivado_project.
    assert not paths["xil_nested"].exists()
    assert not paths["jou"].exists()
    assert not paths["str"].exists()

    assert summary["vivado_project_removed"] == 1
    # Nested .Xil disappeared with vivado_project, so only the top-level
    # .Xil should be counted by the explicit removal pass.
    assert summary["xil_dirs_removed"] >= 1
    assert summary["files_removed"] == 2


def test_clean_stale_build_state_preserves_artifacts(tmp_path):
    """Source, IP, bitstream, reports, logs, scripts and manifests survive."""
    paths = _make_stale_tree(tmp_path)

    clean_stale_build_state(tmp_path)

    assert paths["src_dir"].is_dir()
    assert (paths["src_dir"] / "pcileech_fifo.sv").is_file()
    assert paths["ip_dir"].is_dir()
    assert (paths["ip_dir"] / "core.xci").is_file()
    assert paths["bit"].is_file()
    assert paths["mcs"].is_file()
    assert paths["rpt"].is_file()
    assert paths["log"].is_file()
    assert paths["tcl"].is_file()
    assert paths["manifest"].is_file()


def test_clean_stale_build_state_handles_missing_dir(tmp_path):
    """A non-existent output dir is a silent no-op, not an error."""
    missing = tmp_path / "never_built"
    summary = clean_stale_build_state(missing)
    assert summary == {
        "vivado_project_removed": 0,
        "xil_dirs_removed": 0,
        "files_removed": 0,
    }


def test_clean_stale_build_state_is_idempotent(tmp_path):
    """Second run is a no-op: zero counts, no errors."""
    _make_stale_tree(tmp_path)

    first = clean_stale_build_state(tmp_path)
    second = clean_stale_build_state(tmp_path)

    assert first["vivado_project_removed"] == 1
    assert second == {
        "vivado_project_removed": 0,
        "xil_dirs_removed": 0,
        "files_removed": 0,
    }


def test_clean_stale_build_state_fresh_dir_is_noop(tmp_path):
    """A freshly-created empty dir produces a zero-count summary."""
    summary = clean_stale_build_state(tmp_path)
    assert summary == {
        "vivado_project_removed": 0,
        "xil_dirs_removed": 0,
        "files_removed": 0,
    }


def test_clean_stale_build_state_ignores_unrelated_jou_str_in_subdirs(tmp_path):
    """Only top-level *.jou / *.str are removed — subdir matches are left alone."""
    tmp_path.mkdir(exist_ok=True)
    nested = tmp_path / "user_data"
    nested.mkdir()
    nested_jou = nested / "kept.jou"
    nested_jou.write_text("user data masquerading as a journal")

    summary = clean_stale_build_state(tmp_path)

    assert nested_jou.is_file()
    assert summary["files_removed"] == 0
