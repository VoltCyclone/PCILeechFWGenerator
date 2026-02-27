#!/usr/bin/env python3
"""Tests for Vivado IP artifact repair utilities."""

from __future__ import annotations

import importlib.util
import os
import stat
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[1]
SRC_DIR = PROJECT_ROOT / "src"
MODULE_PATH = SRC_DIR / "vivado_handling" / "ip_lock_resolver.py"

spec = importlib.util.spec_from_file_location(
    "vivado_handling.ip_lock_resolver",
    MODULE_PATH,
)
ip_lock_resolver = importlib.util.module_from_spec(spec)
assert spec and spec.loader  # pragma: no cover - importlib contract
spec.loader.exec_module(ip_lock_resolver)

repair_ip_artifacts = ip_lock_resolver.repair_ip_artifacts
patch_xci_speed_grade = ip_lock_resolver.patch_xci_speed_grade


def _is_writable(path: Path) -> bool:
    mode = path.stat().st_mode
    return bool(mode & stat.S_IWUSR)


def test_repair_ip_artifacts_cleans_locks_and_permissions(tmp_path):
    output_root = tmp_path / "pcileech_board"
    ip_dir = output_root / "ip"
    nested_ip_dir = output_root / "nested" / "ip"
    ip_dir.mkdir(parents=True)
    nested_ip_dir.mkdir(parents=True)

    locked_xci = ip_dir / "foo.xci"
    locked_xci.write_text("test")
    locked_xci.chmod(0o444)

    nested_locked = nested_ip_dir / "bar.xci"
    nested_locked.write_text("data")
    nested_locked.chmod(0o400)

    lock_file = ip_dir / "foo.xci.lck"
    lock_file.write_text("locked")

    stats = repair_ip_artifacts(output_root)

    assert not lock_file.exists()
    assert _is_writable(locked_xci)
    assert _is_writable(nested_locked)
    assert stats["locks_removed"] == 1
    assert stats["files_repaired"] >= 2
    assert stats["ip_dirs"] == 2


def test_repair_ip_artifacts_handles_missing_dirs(tmp_path):
    missing_root = tmp_path / "missing"
    stats = repair_ip_artifacts(missing_root)
    assert stats == {"ip_dirs": 0, "locks_removed": 0, "files_repaired": 0}


# --- patch_xci_speed_grade tests ---

_SAMPLE_XCI = """{
  "SPEEDGRADE": [ { "value": "-2" } ],
  "other_key": "unchanged"
}"""


def _make_xci(tmp_path, content=_SAMPLE_XCI):
    """Create a minimal ip/ tree with one .xci file."""
    ip_dir = tmp_path / "ip"
    ip_dir.mkdir(parents=True)
    xci = ip_dir / "test_core.xci"
    xci.write_text(content, encoding="utf-8")
    return xci


def test_patch_xci_speed_grade_patches_mismatch(tmp_path):
    """SPEEDGRADE is rewritten when it differs from target part."""
    xci = _make_xci(tmp_path)
    count = patch_xci_speed_grade(tmp_path, "xc7a100tfgg484-1")
    assert count == 1
    text = xci.read_text(encoding="utf-8")
    assert '"-1"' in text
    assert '"-2"' not in text


def test_patch_xci_speed_grade_no_change_when_matching(tmp_path):
    """No rewrite when SPEEDGRADE already matches."""
    xci = _make_xci(tmp_path)
    count = patch_xci_speed_grade(tmp_path, "xc7a100tfgg484-2")
    assert count == 0
    text = xci.read_text(encoding="utf-8")
    assert '"-2"' in text


def test_patch_xci_speed_grade_handles_suffixed_part(tmp_path):
    """Parts like xczu3eg-sbva484-1-e have the speed grade extracted."""
    xci = _make_xci(tmp_path)
    count = patch_xci_speed_grade(tmp_path, "xczu3eg-sbva484-1-e")
    assert count == 1
    text = xci.read_text(encoding="utf-8")
    assert '"-1"' in text


def test_patch_xci_speed_grade_unparseable_part(tmp_path):
    """Returns 0 when speed grade cannot be parsed from the part."""
    _make_xci(tmp_path)
    count = patch_xci_speed_grade(tmp_path, "no_speed_grade_here")
    assert count == 0


def test_patch_xci_speed_grade_no_ip_dirs(tmp_path):
    """Returns 0 when there are no ip directories."""
    count = patch_xci_speed_grade(tmp_path, "xc7a100tfgg484-1")
    assert count == 0
