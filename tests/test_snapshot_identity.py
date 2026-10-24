"""Donor snapshots must be positively identified before they reach a build."""

import io
import json
import os
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock

import pytest
from pcileechfwgenerator.build import FirmwareBuilder
from pcileechfwgenerator.exceptions import ConfigurationError
from pcileechfwgenerator.host_collect.collector import HostCollector
from pcileechfwgenerator.host_collect.snapshot import (
    SnapshotError,
    load_configured_snapshot,
    load_snapshot,
    normalize_bdf,
    verify_current_device,
    write_snapshot,
)

from tests.snapshot_helpers import config_bytes, make_snapshot


def read_snapshot(directory, bdf="0000:03:00.0"):
    return load_snapshot(
        directory / "device_context.json", directory / "msix_data.json", bdf
    )


def edit_json(path, change):
    payload = json.loads(path.read_text())
    change(payload)
    path.write_text(json.dumps(payload))


def test_build_rejects_wrong_donor_before_templating(tmp_path, monkeypatch):
    import pcileech

    (tmp_path / "device_context.json").write_text(
        json.dumps({"bdf": "0000:63:00.0", "config_space_hex": config_bytes().hex()})
    )
    monkeypatch.setattr(pcileech, "get_available_boards", lambda: ["test-board"])
    collect, template, vivado = (Mock(return_value=0) for _ in range(3))
    monkeypatch.setattr(pcileech, "run_host_collect", collect)
    monkeypatch.setattr(pcileech, "run_local_templating", template)
    monkeypatch.setattr(pcileech, "run_host_vivado", vivado)
    args = SimpleNamespace(
        board="test-board", bdf="0000:03:00.0", datastore=str(tmp_path), local=True
    )
    assert pcileech.handle_build(args) != 0
    collect.assert_not_called()
    template.assert_not_called()
    vivado.assert_not_called()


def test_config_loader_rejects_unidentified_legacy_capture(tmp_path, monkeypatch):
    path = tmp_path / "device_context.json"
    path.write_text(json.dumps({"config_space_hex": config_bytes().hex()}))
    monkeypatch.setenv("DEVICE_CONTEXT_PATH", str(path))
    builder = FirmwareBuilder.__new__(FirmwareBuilder)
    builder.config = SimpleNamespace(bdf="0000:03:00.0")
    builder.logger = Mock()
    with pytest.raises(ConfigurationError):
        builder._load_preloaded_config_space()


def test_collector_records_shared_capture_identity(tmp_path, monkeypatch):
    collector = HostCollector("03:00.0", tmp_path)
    monkeypatch.setattr(collector, "_read_config_space", config_bytes)
    assert collector.run() == 0
    context = json.loads((tmp_path / "device_context.json").read_text())
    sidecar = json.loads((tmp_path / "msix_data.json").read_text())
    assert context["capture"]["bdf"] == "0000:03:00.0"
    assert context["capture"] == sidecar["capture"]
    assert context["capture"]["vendor_id"] == 0x1912
    assert context["capture"]["device_id"] == 0x0014


@pytest.mark.parametrize("bdf", ["0000:03:00.0", "03:00.0"])
def test_matching_snapshot_loads_without_hardware(tmp_path, monkeypatch, bdf):
    context = make_snapshot(tmp_path)
    monkeypatch.setenv("DEVICE_CONTEXT_PATH", str(context))
    snapshot = load_configured_snapshot(bdf)
    assert snapshot.config_space == config_bytes()
    assert snapshot.context["vendor_id"] == 0x1912


@pytest.mark.parametrize("bdf", ["0000:03:20.0", "03:00.8", "../config", "", None])
def test_invalid_bdf_rejected(bdf):
    with pytest.raises(SnapshotError, match="PCI address"):
        normalize_bdf(bdf)


@pytest.mark.parametrize(
    "field,value",
    [
        ("schema_version", 2),
        ("schema_version", True),
        ("capture_id", "invalid"),
        ("collected_at", "2026-10-24T00:00:00"),
        ("vendor_id", "6418"),
        ("device_id", 0x0015),
        ("bdf", "0000:04:00.0"),
        ("config_space_size", 128),
        ("config_space_sha256", "0" * 64),
    ],
)
def test_invalid_identity_rejected(tmp_path, field, value):
    path = make_snapshot(tmp_path)
    edit_json(path, lambda p: p["capture"].update({field: value}))
    with pytest.raises(SnapshotError):
        read_snapshot(tmp_path)


@pytest.mark.parametrize("contents", ["{bad json", "[]", "{}", "null"])
def test_malformed_or_legacy_capture_rejected(tmp_path, contents):
    path = make_snapshot(tmp_path)
    path.write_text(contents)
    with pytest.raises(SnapshotError):
        read_snapshot(tmp_path)


def test_validation_error_keeps_recovery_message_and_omits_input(tmp_path):
    from pcileechfwgenerator.error_utils import extract_root_cause

    path = make_snapshot(tmp_path)
    marker = "private-donor-data-marker"
    edit_json(path, lambda p: p["capture"].update({"capture_id": marker}))
    with pytest.raises(SnapshotError) as error:
        read_snapshot(tmp_path)
    message = extract_root_cause(error.value)
    assert "new --datastore" in message
    assert marker not in message


@pytest.mark.parametrize(
    "field,value",
    [
        ("vendor_id", 1),
        ("config_space_hex", "xyz"),
        ("config_space_hex", "00" * 256),
        ("device_info", {"device_id": 1}),
        ("template_context", {"device_config": {"vendor_id": "8086"}}),
    ],
)
def test_conflicting_payload_rejected(tmp_path, field, value):
    path = make_snapshot(tmp_path)
    edit_json(path, lambda p: p.update({field: value}))
    with pytest.raises(SnapshotError):
        read_snapshot(tmp_path)


def test_sidecar_from_another_capture_at_same_address_rejected(tmp_path):
    first, second = tmp_path / "first", tmp_path / "second"
    make_snapshot(first)
    make_snapshot(second)
    (first / "msix_data.json").write_bytes((second / "msix_data.json").read_bytes())
    with pytest.raises(SnapshotError, match="different captures"):
        read_snapshot(first)


@pytest.mark.parametrize("missing", ["device_context.json", "msix_data.json"])
def test_incomplete_capture_rejected(tmp_path, missing, monkeypatch):
    path = make_snapshot(tmp_path)
    (tmp_path / missing).unlink()
    monkeypatch.setenv("DEVICE_CONTEXT_PATH", str(path))
    with pytest.raises(SnapshotError, match="missing or unreadable"):
        load_configured_snapshot("03:00.0")


@pytest.mark.parametrize("offset", [0, 2, 8, 9, 44, 46])
def test_reused_address_changed_identity_rejected(tmp_path, monkeypatch, offset):
    make_snapshot(tmp_path)
    snapshot = read_snapshot(tmp_path)
    changed = bytearray(config_bytes())
    changed[offset] ^= 1
    monkeypatch.setattr(Path, "open", lambda *a, **kw: io.BytesIO(changed))
    with pytest.raises(SnapshotError, match="changed identity"):
        verify_current_device(snapshot)


def test_live_check_ignores_mutable_command_and_bar_registers(tmp_path, monkeypatch):
    make_snapshot(tmp_path)
    snapshot = read_snapshot(tmp_path)
    changed = bytearray(config_bytes())
    changed[4:8] = b"\xff" * 4
    changed[16:40] = b"\xff" * 24
    monkeypatch.setattr(Path, "open", lambda *a, **kw: io.BytesIO(changed))
    verify_current_device(snapshot)


def test_live_check_requires_readable_header(tmp_path, monkeypatch):
    make_snapshot(tmp_path)
    snapshot = read_snapshot(tmp_path)
    monkeypatch.setattr(Path, "open", Mock(side_effect=PermissionError))
    with pytest.raises(SnapshotError, match="cannot verify"):
        verify_current_device(snapshot)


@pytest.mark.parametrize(
    "manager_module",
    ["pcileechfwgenerator.build", "pcileechfwgenerator.device_clone.msix"],
)
@pytest.mark.parametrize("offline", [True, False])
def test_msix_consumers_reject_wrong_capture_without_live_fallback(
    tmp_path, monkeypatch, manager_module, offline
):
    import importlib

    make_snapshot(tmp_path, bdf="0000:63:00.0", msix_info={"table_size": 8})
    monkeypatch.setenv("MSIX_DATA_PATH", str(tmp_path / "msix_data.json"))
    monkeypatch.setenv("PCILEECH_DISABLE_VFIO", "1" if offline else "0")
    manager = importlib.import_module(manager_module).MSIXManager("0000:03:00.0")
    live_read = Mock(side_effect=AssertionError("unexpected live read"))
    monkeypatch.setattr(manager, "_read_config_space", live_read)
    with pytest.raises(SnapshotError, match="requested donor"):
        manager.preload_data()
    live_read.assert_not_called()


@pytest.mark.parametrize(
    "method", ["_load_preloaded_config_space", "_check_host_collected_context"]
)
def test_builder_consumers_reject_wrong_capture(tmp_path, monkeypatch, method):
    path = make_snapshot(tmp_path, bdf="0000:63:00.0")
    monkeypatch.setenv("DEVICE_CONTEXT_PATH", str(path))
    builder = FirmwareBuilder.__new__(FirmwareBuilder)
    builder.config = SimpleNamespace(bdf="0000:03:00.0")
    with pytest.raises(SnapshotError, match="requested donor"):
        getattr(builder, method)()


@pytest.mark.parametrize("existing", ["device_context.json", "msix_data.json"])
def test_collection_never_overwrites_or_probes_occupied_destination(
    tmp_path, monkeypatch, existing
):
    path = tmp_path / existing
    path.write_bytes(b"previous capture")
    read = Mock(side_effect=AssertionError("unexpected donor read"))
    collector = HostCollector("03:00.0", tmp_path)
    monkeypatch.setattr(collector, "_read_config_space", read)
    assert collector.run() == 1
    read.assert_not_called()
    assert path.read_bytes() == b"previous capture"


def test_interrupted_pair_publication_is_rejected_and_preserved(tmp_path, monkeypatch):
    link = os.link

    def interrupt_second(source, destination):
        if destination.name == "device_context.json":
            raise OSError("simulated interruption")
        link(source, destination)

    monkeypatch.setattr(os, "link", interrupt_second)
    with pytest.raises(SnapshotError, match="publication"):
        write_snapshot(tmp_path, "03:00.0", config_bytes(), {})
    assert not list(tmp_path.glob(".capture-*"))
    assert not (tmp_path / "device_context.json").exists()
    sidecar = (tmp_path / "msix_data.json").read_bytes()
    with pytest.raises(SnapshotError):
        read_snapshot(tmp_path)
    with pytest.raises(SnapshotError, match="already contains"):
        write_snapshot(tmp_path, "03:00.0", config_bytes(), {})
    assert (tmp_path / "msix_data.json").read_bytes() == sidecar


def test_concurrent_destination_creation_cannot_overwrite_capture(
    tmp_path, monkeypatch
):
    link = os.link

    def racing_link(source, destination):
        destination.write_bytes(b"another writer won")
        link(source, destination)

    monkeypatch.setattr(os, "link", racing_link)
    with pytest.raises(SnapshotError, match="publication"):
        write_snapshot(tmp_path, "03:00.0", config_bytes(), {})
    assert (tmp_path / "msix_data.json").read_bytes() == b"another writer won"
    assert not list(tmp_path.glob(".capture-*"))


def test_published_capture_is_readable_and_consistent(tmp_path):
    write_snapshot(tmp_path, "03:00.0", config_bytes(), {})
    snapshot = read_snapshot(tmp_path)
    assert snapshot.config_space == config_bytes()
    assert snapshot.identity.bdf == "0000:03:00.0"
    assert not list(tmp_path.glob(".capture-*"))
    assert (tmp_path / "device_context.json").stat().st_mode & 0o444 == 0o444


def test_bar_context_does_not_swallow_rejected_capture(tmp_path, monkeypatch):
    from pcileechfwgenerator.device_clone.pcileech_context import PCILeechContextBuilder

    path = make_snapshot(tmp_path, bdf="0000:63:00.0")
    monkeypatch.setenv("DEVICE_CONTEXT_PATH", str(path))
    builder = PCILeechContextBuilder.__new__(PCILeechContextBuilder)
    builder.device_bdf = "0000:03:00.0"
    builder.logger = Mock()
    bar = SimpleNamespace(index=0, size=4096, size_mb=4096 / 1024**2)
    for method, result in [
        ("_check_and_fix_power_state", None),
        ("_analyze_bars", [bar]),
        ("_select_primary_bar", bar),
        ("_build_bar_config_dict", {}),
        ("_sample_bar_data_direct", {}),
    ]:
        monkeypatch.setattr(builder, method, Mock(return_value=result))
    with pytest.raises(SnapshotError, match="requested donor"):
        builder._build_bar_config({"bars": [], "device_signature": "fixture"}, None)


@pytest.mark.parametrize("changed", [True, False])
def test_host_reuse_checks_live_identity_before_templating(
    tmp_path, monkeypatch, changed
):
    import pcileech

    make_snapshot(tmp_path)
    live = bytearray(config_bytes())
    if changed:
        live[2] ^= 1
    original_open = Path.open

    def open_with_fake_header(path, *args, **kwargs):
        if str(path).startswith("/sys/bus/pci/devices/"):
            return io.BytesIO(live)
        return original_open(path, *args, **kwargs)

    monkeypatch.setattr(Path, "open", open_with_fake_header)
    monkeypatch.setattr(pcileech, "get_available_boards", lambda: ["test-board"])
    collect, template, vivado = (Mock(return_value=0) for _ in range(3))
    monkeypatch.setattr(pcileech, "run_host_collect", collect)
    monkeypatch.setattr(pcileech, "run_local_templating", template)
    monkeypatch.setattr(pcileech, "run_host_vivado", vivado)
    args = SimpleNamespace(
        board="test-board", bdf="0000:03:00.0", datastore=str(tmp_path), local=True
    )
    assert pcileech.handle_build(args) == (1 if changed else 0)
    collect.assert_not_called()
    assert template.call_count == (0 if changed else 1)
    assert vivado.call_count == (0 if changed else 1)
