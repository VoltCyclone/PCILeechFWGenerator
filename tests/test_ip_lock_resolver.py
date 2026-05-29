#!/usr/bin/env python3
"""Tests for Vivado IP artifact repair utilities."""

from __future__ import annotations

import importlib.util
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


# --- patch_xci_donor_ids tests ---


def test_patch_xci_donor_ids(tmp_path):
    patch_xci_donor_ids = ip_lock_resolver.patch_xci_donor_ids

    ip_dir = tmp_path / "ip"
    ip_dir.mkdir()
    xci = ip_dir / "pcie_7x_0.xci"
    xci.write_text(
        """{
  "ip_inst": { "parameters": { "component_parameters": {
    "Vendor_ID": [ { "value": "10EE", "resolve_type": "user", "usage": "all" } ],
    "Device_ID": [ { "value": "0666", "value_src": "user", "usage": "all" } ],
    "Subsystem_Vendor_ID": [ { "value": "10EE", "resolve_type": "user" } ],
    "Subsystem_ID": [ { "value": "0007", "resolve_type": "user" } ],
    "Class_Code_Base": [ { "value": "02", "value_src": "user" } ],
    "Class_Code_Sub": [ { "value": "00", "value_src": "user" } ],
    "Class_Code_Interface": [ { "value": "00", "resolve_type": "user" } ],
    "ven_id": [ { "value": "10EE", "resolve_type": "generated" } ],
    "dev_id": [ { "value": "0666", "resolve_type": "generated" } ],
    "subsys_ven_id": [ { "value": "10EE", "resolve_type": "generated" } ],
    "subsys_id": [ { "value": "0007", "resolve_type": "generated" } ],
    "class_code": [ { "value": "020000", "resolve_type": "generated" } ]
  } } }
}""",
        encoding="utf-8",
    )

    class _Donor:
        vendor_id = 0x1B21
        device_id = 0x1060
        subsystem_vendor_id = 0x1043
        subsystem_id = 0x8730

    patched = patch_xci_donor_ids(tmp_path, _Donor(), class_code=0x010601)
    assert patched == 1

    text = xci.read_text(encoding="utf-8")
    assert '"Vendor_ID": [ { "value": "1B21"' in text
    assert '"Device_ID": [ { "value": "1060"' in text
    assert '"Subsystem_Vendor_ID": [ { "value": "1043"' in text
    assert '"Subsystem_ID": [ { "value": "8730"' in text
    assert '"Class_Code_Base": [ { "value": "01"' in text
    assert '"Class_Code_Sub": [ { "value": "06"' in text
    assert '"Class_Code_Interface": [ { "value": "01"' in text
    assert '"ven_id": [ { "value": "1B21"' in text
    assert '"dev_id": [ { "value": "1060"' in text
    assert '"subsys_ven_id": [ { "value": "1043"' in text
    assert '"subsys_id": [ { "value": "8730"' in text
    assert '"class_code": [ { "value": "010601"' in text
    assert "10EE" not in text and "0666" not in text


def test_patch_xci_donor_ids_skips_class_code_when_none(tmp_path):
    patch_xci_donor_ids = ip_lock_resolver.patch_xci_donor_ids
    ip_dir = tmp_path / "ip"
    ip_dir.mkdir()
    xci = ip_dir / "pcie_7x_0.xci"
    xci.write_text(
        '{ "Vendor_ID": [ { "value": "10EE", "resolve_type": "user" } ],'
        '  "Class_Code_Base": [ { "value": "02", "resolve_type": "user" } ] }',
        encoding="utf-8",
    )

    class _Donor:
        vendor_id = 0x1B21
        device_id = 0x1060
        subsystem_vendor_id = 0x1043
        subsystem_id = 0x8730

    patch_xci_donor_ids(tmp_path, _Donor(), class_code=None)
    text = xci.read_text(encoding="utf-8")
    assert '"Vendor_ID": [ { "value": "1B21"' in text
    assert '"Class_Code_Base": [ { "value": "02"' in text  # untouched
