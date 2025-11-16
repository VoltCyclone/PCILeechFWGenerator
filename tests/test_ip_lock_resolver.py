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
