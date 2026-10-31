"""Offline use of a capture is locked to the host that collected it."""

import hashlib
import json

import pytest
from pcileechfwgenerator.host_collect import host_binding
from pcileechfwgenerator.host_collect.offline import load_offline_capture
from pcileechfwgenerator.host_collect.snapshot import SnapshotError, write_snapshot

from tests.snapshot_helpers import (
    TEST_HOST_FINGERPRINT,
    make_config,
    offline_fixture,
    host_binding_record,
)

BDF = "0000:03:00.0"


def _edit_binding(directory, binding):
    path = directory / "device_context.json"
    payload = json.loads(path.read_text())
    if binding is None:
        payload.pop("host_binding")
    else:
        payload["host_binding"] = binding
    path.write_text(json.dumps(payload))


def test_fingerprint_is_salted_hash_of_machine_id(tmp_path, monkeypatch):
    machine_id = tmp_path / "machine-id"
    machine_id.write_text("0123456789abcdef0123456789abcdef\n")
    monkeypatch.undo()  # drop the conftest pin to test the real function
    monkeypatch.setattr(host_binding, "MACHINE_ID_PATHS", (machine_id,))
    fingerprint = host_binding.host_fingerprint()
    assert len(fingerprint) == 64
    assert "0123456789abcdef" not in fingerprint
    assert (
        fingerprint != hashlib.sha256(b"0123456789abcdef0123456789abcdef").hexdigest()
    )


def test_fingerprint_unavailable_raises(tmp_path, monkeypatch):
    monkeypatch.undo()
    monkeypatch.setattr(host_binding, "MACHINE_ID_PATHS", (tmp_path / "missing",))
    with pytest.raises(SnapshotError, match="cannot identify this host"):
        host_binding.host_fingerprint()


def test_collection_records_this_host(tmp_path):
    snapshot = write_snapshot(tmp_path, BDF, make_config(), {})
    assert snapshot.context["host_binding"] == host_binding_record()
    on_disk = json.loads((tmp_path / "device_context.json").read_text())
    assert on_disk["host_binding"]["fingerprint"] == TEST_HOST_FINGERPRINT


def test_collection_ignores_caller_supplied_binding(tmp_path):
    forged = host_binding_record("0" * 64)
    snapshot = write_snapshot(
        tmp_path, BDF, make_config(), {}, {"host_binding": forged}
    )
    assert snapshot.context["host_binding"]["fingerprint"] == TEST_HOST_FINGERPRINT


def test_unidentifiable_host_still_collects_without_binding(tmp_path, monkeypatch):
    def unavailable():
        raise SnapshotError("cannot identify this host")

    monkeypatch.setattr(host_binding, "host_fingerprint", unavailable)
    snapshot = write_snapshot(tmp_path, BDF, make_config(), {})
    assert "host_binding" not in snapshot.context


def test_offline_use_on_collecting_host_allowed(tmp_path):
    offline_fixture(tmp_path)
    load_offline_capture(tmp_path)


def test_offline_use_on_other_host_refused(tmp_path, monkeypatch):
    offline_fixture(tmp_path)
    monkeypatch.setattr(host_binding, "host_fingerprint", lambda: "0" * 64)
    with pytest.raises(SnapshotError, match="different machine"):
        load_offline_capture(tmp_path)


@pytest.mark.parametrize(
    "binding",
    [
        None,
        "fff",
        {"schema_version": 2, "method": "machine-id-sha256", "fingerprint": "f"},
    ],
)
def test_unbound_or_malformed_binding_refused(tmp_path, binding):
    offline_fixture(tmp_path)
    _edit_binding(tmp_path, binding)
    with pytest.raises(SnapshotError, match="not bound to a collecting host"):
        load_offline_capture(tmp_path)
