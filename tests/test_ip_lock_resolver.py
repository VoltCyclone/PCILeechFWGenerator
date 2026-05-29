#!/usr/bin/env python3
"""Tests for Vivado IP artifact repair utilities."""

from __future__ import annotations

import importlib.util
import shutil
import stat
import sys
from pathlib import Path

import pytest

PROJECT_ROOT = Path(__file__).resolve().parents[1]
SRC_DIR = PROJECT_ROOT / "src"
MODULE_PATH = SRC_DIR / "vivado_handling" / "ip_lock_resolver.py"

_MODULE_NAME = "vivado_handling.ip_lock_resolver"
spec = importlib.util.spec_from_file_location(
    _MODULE_NAME,
    MODULE_PATH,
)
ip_lock_resolver = importlib.util.module_from_spec(spec)
# Register in sys.modules before exec_module so that dataclasses (Python 3.13+)
# can resolve the module dict when processing @dataclass fields.
sys.modules.setdefault(_MODULE_NAME, ip_lock_resolver)
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

    summary = patch_xci_donor_ids(tmp_path, _Donor(), class_code=0x010601)
    assert summary.num_patched == 1
    assert summary.patched == ["pcie_7x_0.xci"]
    assert summary.unmatched == [] and summary.failed == []

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

    summary = patch_xci_donor_ids(tmp_path, _Donor(), class_code=None)
    assert summary.num_patched == 1
    text = xci.read_text(encoding="utf-8")
    assert '"Vendor_ID": [ { "value": "1B21"' in text
    assert '"Class_Code_Base": [ { "value": "02"' in text  # untouched


def test_patch_xci_donor_ids_already_patched_no_warning(tmp_path):
    """An XCI that already holds the donor values must classify as patched,
    not unmatched: fields match the anchors (total > 0) but no rewrite is
    needed. It must not emit a false 'no fields matched' warning."""
    patch_xci_donor_ids = ip_lock_resolver.patch_xci_donor_ids
    ip_dir = tmp_path / "ip"
    ip_dir.mkdir()
    xci = ip_dir / "pcie_7x_0.xci"
    # Values already equal the donor below.
    xci.write_text(
        '{ "Vendor_ID": [ { "value": "1B21", "resolve_type": "user" } ],'
        '  "Device_ID": [ { "value": "1060", "resolve_type": "user" } ] }',
        encoding="utf-8",
    )

    class _Donor:
        vendor_id = 0x1B21
        device_id = 0x1060
        subsystem_vendor_id = 0x1043
        subsystem_id = 0x8730

    summary = patch_xci_donor_ids(tmp_path, _Donor(), class_code=None)
    assert summary.num_patched == 1
    assert summary.unmatched == []
    assert summary.failed == []


def test_patch_xci_donor_ids_warns_on_unmatched_format(tmp_path):
    """Returns 0 and leaves the file unchanged when no JSON fields match.

    Boards that ship XML-format XCI files (e.g. pciescreamer, acorn_ft2232h,
    NeTV2) produce zero substitutions.  The function must return 0 and must
    NOT modify the file (issue #622).
    """
    patch_xci_donor_ids = ip_lock_resolver.patch_xci_donor_ids

    ip_dir = tmp_path / "ip"
    ip_dir.mkdir()
    xci = ip_dir / "pcie_7x_0.xci"
    # XML-style content — no JSON "key": [ { "value": ... } ] fields.
    xml_content = (
        '<?xml version="1.0" encoding="UTF-8"?>\n'
        '<spirit:configurableElementValues xmlns:spirit="http://www.spiritconsortium.org/XMLSchema/SPIRIT/1685-2009">\n'
        '  <spirit:configurableElementValue '
        'spirit:referenceId="VENDOR_ID">10EE</spirit:configurableElementValue>\n'
        '</spirit:configurableElementValues>\n'
    )
    xci.write_text(xml_content, encoding="utf-8")

    class _Donor:
        vendor_id = 0x1B21
        device_id = 0x1060
        subsystem_vendor_id = 0x1043
        subsystem_id = 0x8730

    summary = patch_xci_donor_ids(tmp_path, _Donor(), class_code=0x010601)

    assert summary.num_patched == 0
    assert "pcie_7x_0.xci" in summary.unmatched
    assert xci.read_text(encoding="utf-8") == xml_content


def test_patch_xci_donor_ids_xml(tmp_path):
    patch_xci_donor_ids = ip_lock_resolver.patch_xci_donor_ids

    ip_dir = tmp_path / "ip"
    ip_dir.mkdir()
    xci = ip_dir / "pcie_7x_0.xci"
    xci.write_text(
        '<?xml version="1.0" encoding="UTF-8"?>\n'
        '<spirit:design xmlns:spirit="http://www.spiritconsortium.org/x">\n'
        '  <spirit:configurableElementValue spirit:referenceId="MODELPARAM_VALUE.class_code">020000</spirit:configurableElementValue>\n'
        '  <spirit:configurableElementValue spirit:referenceId="MODELPARAM_VALUE.dev_id">0666</spirit:configurableElementValue>\n'
        '  <spirit:configurableElementValue spirit:referenceId="MODELPARAM_VALUE.subsys_id">0007</spirit:configurableElementValue>\n'
        '  <spirit:configurableElementValue spirit:referenceId="MODELPARAM_VALUE.subsys_ven_id">10EE</spirit:configurableElementValue>\n'
        '  <spirit:configurableElementValue spirit:referenceId="MODELPARAM_VALUE.ven_id">10EE</spirit:configurableElementValue>\n'
        '  <spirit:configurableElementValue spirit:referenceId="PARAM_VALUE.Class_Code_Base">02</spirit:configurableElementValue>\n'
        '  <spirit:configurableElementValue spirit:referenceId="PARAM_VALUE.Class_Code_Interface">00</spirit:configurableElementValue>\n'
        '  <spirit:configurableElementValue spirit:referenceId="PARAM_VALUE.Class_Code_Sub">00</spirit:configurableElementValue>\n'
        '  <spirit:configurableElementValue spirit:referenceId="PARAM_VALUE.Device_ID">0666</spirit:configurableElementValue>\n'
        '  <spirit:configurableElementValue spirit:referenceId="PARAM_VALUE.Subsystem_ID">0007</spirit:configurableElementValue>\n'
        '  <spirit:configurableElementValue spirit:referenceId="PARAM_VALUE.Subsystem_Vendor_ID">10EE</spirit:configurableElementValue>\n'
        '  <spirit:configurableElementValue spirit:referenceId="PARAM_VALUE.Vendor_ID">10EE</spirit:configurableElementValue>\n'
        '</spirit:design>\n',
        encoding="utf-8",
    )

    class _Donor:
        vendor_id = 0x1B21
        device_id = 0x1060
        subsystem_vendor_id = 0x1043
        subsystem_id = 0x8730

    summary = patch_xci_donor_ids(tmp_path, _Donor(), class_code=0x010601)
    assert summary.num_patched == 1

    text = xci.read_text(encoding="utf-8")
    assert ">1B21<" in text and ">1060<" in text
    assert ">1043<" in text and ">8730<" in text
    assert 'PARAM_VALUE.Class_Code_Base">01<' in text
    assert 'PARAM_VALUE.Class_Code_Sub">06<' in text
    assert 'PARAM_VALUE.Class_Code_Interface">01<' in text
    assert 'MODELPARAM_VALUE.class_code">010601<' in text
    assert ">10EE<" not in text and ">0666<" not in text and ">020000<" not in text
    assert text.startswith('<?xml version="1.0"')
    assert "xmlns:spirit" in text


def test_patch_xci_donor_ids_xml_regex_safety(tmp_path):
    """class_code must not match inside Class_Code_Base; ven_id not inside
    subsys_ven_id (key-anchored, XML form)."""
    patch_xci_donor_ids = ip_lock_resolver.patch_xci_donor_ids
    ip_dir = tmp_path / "ip"
    ip_dir.mkdir()
    xci = ip_dir / "pcie_7x_0.xci"
    xci.write_text(
        '<?xml version="1.0"?>\n<r xmlns:spirit="x">\n'
        '  <spirit:configurableElementValue spirit:referenceId="PARAM_VALUE.Class_Code_Base">02</spirit:configurableElementValue>\n'
        '  <spirit:configurableElementValue spirit:referenceId="MODELPARAM_VALUE.subsys_ven_id">10EE</spirit:configurableElementValue>\n'
        '  <spirit:configurableElementValue spirit:referenceId="MODELPARAM_VALUE.ven_id">10EE</spirit:configurableElementValue>\n'
        '</r>\n',
        encoding="utf-8",
    )

    class _Donor:
        vendor_id = 0x1B21
        device_id = 0x1060
        subsystem_vendor_id = 0x1043
        subsystem_id = 0x8730

    patch_xci_donor_ids(tmp_path, _Donor(), class_code=0x010601)
    text = xci.read_text(encoding="utf-8")
    assert 'PARAM_VALUE.Class_Code_Base">01<' in text
    assert 'MODELPARAM_VALUE.subsys_ven_id">1043<' in text
    assert 'MODELPARAM_VALUE.ven_id">1B21<' in text


def test_patch_xci_donor_ids_malformed_xml(tmp_path):
    """Malformed XML lands in `failed`, not crash, not write."""
    patch_xci_donor_ids = ip_lock_resolver.patch_xci_donor_ids
    ip_dir = tmp_path / "ip"
    ip_dir.mkdir()
    xci = ip_dir / "pcie_7x_0.xci"
    original = '<not-closed referenceId="PARAM_VALUE.Vendor_ID">10EE'
    xci.write_text(original, encoding="utf-8")

    class _Donor:
        vendor_id = 0x1B21
        device_id = 0x1060
        subsystem_vendor_id = 0x1043
        subsystem_id = 0x8730

    summary = patch_xci_donor_ids(tmp_path, _Donor(), class_code=0x010601)
    assert summary.num_patched == 0
    assert "pcie_7x_0.xci" in summary.failed
    assert xci.read_text(encoding="utf-8") == original


def test_xci_patch_summary_helpers():
    XciPatchSummary = ip_lock_resolver.XciPatchSummary
    s = XciPatchSummary(
        patched=["pcie_7x_0.xci"],
        unmatched=["other.xci"],
        failed=[],
        total_files=2,
    )
    assert s.num_patched == 1
    assert s.has_unmatched_core() is False

    s2 = XciPatchSummary(
        patched=[], unmatched=["pcie_7x_0.xci"], failed=[], total_files=1
    )
    assert s2.has_unmatched_core() is True

    s3 = XciPatchSummary(
        patched=[], unmatched=[], failed=["PCIE_7X_0.xci"], total_files=1
    )
    assert s3.has_unmatched_core() is True  # case-insensitive, checks failed too


def test_patch_xci_donor_ids_real_xml_fixture(tmp_path):
    """Patch a real XML-format board XCI from the submodule (#622)."""
    real_xci = (
        PROJECT_ROOT
        / "lib"
        / "voltcyclone-fpga"
        / "NeTV2"
        / "ip"
        / "pcie_7x_0.xci"
    )
    if not real_xci.exists():
        pytest.skip("voltcyclone-fpga submodule not checked out")

    patch_xci_donor_ids = ip_lock_resolver.patch_xci_donor_ids
    ip_dir = tmp_path / "ip"
    ip_dir.mkdir()
    staged = ip_dir / "pcie_7x_0.xci"
    shutil.copy(real_xci, staged)

    class _Donor:
        vendor_id = 0x1B21
        device_id = 0x1060
        subsystem_vendor_id = 0x1043
        subsystem_id = 0x8730

    summary = patch_xci_donor_ids(tmp_path, _Donor(), class_code=0x010601)
    assert summary.num_patched == 1

    text = staged.read_text(encoding="utf-8")
    # No Xilinx defaults remain in any ID element text.
    for ref in (
        "PARAM_VALUE.Vendor_ID",
        "PARAM_VALUE.Device_ID",
        "PARAM_VALUE.Subsystem_Vendor_ID",
        "PARAM_VALUE.Subsystem_ID",
        "MODELPARAM_VALUE.ven_id",
        "MODELPARAM_VALUE.dev_id",
        "MODELPARAM_VALUE.subsys_ven_id",
        "MODELPARAM_VALUE.subsys_id",
    ):
        assert f'{ref}">10EE<' not in text
        assert f'{ref}">0666<' not in text
        assert f'{ref}">0007<' not in text
    assert 'MODELPARAM_VALUE.ven_id">1B21<' in text
    assert 'MODELPARAM_VALUE.dev_id">1060<' in text
    assert 'MODELPARAM_VALUE.class_code">010601<' in text
    # Still valid XML after the edit.
    import xml.etree.ElementTree as ET

    ET.fromstring(text)  # raises if we corrupted it
