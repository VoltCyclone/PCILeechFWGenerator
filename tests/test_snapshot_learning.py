"""Bounded, immutable learning artifacts; all evidence comes from test fixtures."""

import hashlib
import json
import os
from dataclasses import replace
from uuid import UUID, uuid4

import pytest
from pcileechfwgenerator.device_clone.register_evidence import (
    DonorIdentity,
    ExperimentResult,
    FieldClaim,
    LearningContext,
    LearningManifest,
    Observation,
    RegisterClassification,
    RegisterRef,
)
from pcileechfwgenerator.error_utils import extract_root_cause
from pcileechfwgenerator.host_collect import snapshot as snapshots
from pcileechfwgenerator.host_collect.offline import import_capture
from pcileechfwgenerator.host_collect.snapshot import (
    CONTEXT_NAME,
    LEARNING_NAME,
    MSIX_NAME,
    SnapshotError,
    ensure_capture_destination,
    load_snapshot,
    publish_snapshot,
    write_snapshot,
)

from tests.snapshot_helpers import config_bytes, host_binding_record, make_snapshot

CAPTURE_ID = UUID("68d77276-7b98-4e4a-a264-c986916aeaa0")


def manifest(config=None, capture_id=CAPTURE_ID, bdf="0000:03:00.0"):
    config = config_bytes() if config is None else config
    donor = DonorIdentity(
        vendor_id=int.from_bytes(config[:2], "little"),
        device_id=int.from_bytes(config[2:4], "little"),
        revision_id=config[8],
        class_code=int.from_bytes(config[9:12], "little"),
        subsystem_vendor_id=(
            int.from_bytes(config[44:46], "little") if config[14] & 0x7F == 0 else None
        ),
        subsystem_device_id=(
            int.from_bytes(config[46:48], "little") if config[14] & 0x7F == 0 else None
        ),
    )
    ref = RegisterRef(space="config", offset=4, width=2, field_mask=1)
    observation = Observation(
        sequence_id=0,
        monotonic_ns=10,
        operation="read",
        ref=ref,
        returned_value=0,
        completion="ok",
        source="simulator",
        state_epoch=0,
    )
    experiment = ExperimentResult(
        experiment_id="fixture-read",
        recipe_id="fixture",
        recipe_version="1",
        ref=ref,
        state_epoch=0,
        requested_mask=1,
        observation_ids=(0,),
        time_budget_ns=100,
        termination="precondition_failed",
        restoration="not_attempted",
    )
    classification = RegisterClassification(
        ref=ref,
        observed_initial_value=0,
        claims=(
            FieldClaim(
                mask=1, semantic="unknown", strength="observed", observation_ids=(0,)
            ),
        ),
    )
    return LearningManifest(
        context=LearningContext(
            capture_id=capture_id,
            donor=donor,
            bdf=bdf,
            backend_version="fixture-1",
            experiment_version="1",
        ),
        status="partial",
        observations=(observation,),
        experiments=(experiment,),
        classifications=(classification,),
    )


def read(directory):
    return load_snapshot(directory / CONTEXT_NAME, directory / MSIX_NAME, "03:00.0")


def reference(document):
    """Independent reference fixture, deliberately not the writer's helper."""
    raw = json.loads(document)
    return dict(
        schema_version=1,
        file="register_learning.json",
        sha256=hashlib.sha256(document).hexdigest(),
        byte_size=len(document),
        status=raw["status"],
        observation_count=len(raw["observations"]),
        experiment_count=len(raw["experiments"]),
        classification_count=len(raw["classifications"]),
    )


def set_document(directory, document, update_reference=True):
    (directory / LEARNING_NAME).write_bytes(document)
    if update_reference:
        path = directory / CONTEXT_NAME
        context = json.loads(path.read_text())
        context["register_learning"] = reference(document)
        path.write_text(json.dumps(context))


def learning_capture(directory):
    learning = manifest()
    document = learning.model_dump_json(indent=2).encode()
    make_snapshot(
        directory,
        capture_id=CAPTURE_ID,
        context={
            "register_learning": reference(document),
            "host_binding": host_binding_record(),
            "offline_resources": {
                "schema_version": 1,
                "source": "linux_sysfs_resource",
                "bar_sizes": [0] * 6,
            },
        },
    )
    (directory / LEARNING_NAME).write_bytes(document)
    return learning


def edit_reference(directory, key, value):
    path = directory / CONTEXT_NAME
    context = json.loads(path.read_text())
    context["register_learning"][key] = value
    path.write_text(json.dumps(context))


def test_deterministic_round_trip(tmp_path):
    learning = manifest()
    first = write_snapshot(
        tmp_path / "a",
        "03:00.0",
        config_bytes(),
        {},
        learning=learning,
        capture_id=CAPTURE_ID,
    )
    second = write_snapshot(
        tmp_path / "b",
        "03:00.0",
        config_bytes(),
        {},
        learning=learning,
        capture_id=CAPTURE_ID,
    )
    document = (tmp_path / "a" / LEARNING_NAME).read_bytes()
    assert document == (tmp_path / "b" / LEARNING_NAME).read_bytes()
    assert first.context["register_learning"] == reference(document)
    assert first.context["register_learning"] == second.context["register_learning"]
    assert read(tmp_path / "a") == first
    assert first.learning == learning
    assert first.learning_status == "partial"
    assert first.learning.classifications[0].unknown_mask() == 1


def test_independently_serialized_capture_and_offline_import(tmp_path):
    source, destination = tmp_path / "source", tmp_path / "destination"
    learning = learning_capture(source)
    original = (source / LEARNING_NAME).read_bytes()
    # Whitespace and Unicode encoding are part of the checksum, too.
    original = b"\n" + original + b"\n"
    set_document(source, original)
    imported = import_capture(source, destination, "03:00.0")
    assert imported.learning == learning
    assert read(destination) == imported == read(source)
    assert (destination / LEARNING_NAME).read_bytes() == original
    assert (source / LEARNING_NAME).read_bytes() == original


@pytest.mark.parametrize("orphan", [None, "corrupt", "symlink", "oversized"])
def test_legacy_capture_is_absent_and_unreferenced_learning_is_ignored(
    tmp_path, orphan
):
    make_snapshot(tmp_path)
    path = tmp_path / LEARNING_NAME
    if orphan == "corrupt":
        path.write_text("not JSON")
    elif orphan == "symlink":
        path.symlink_to(tmp_path / "missing")
    elif orphan == "oversized":
        with path.open("wb") as stream:
            stream.truncate(snapshots.MAX_LEARNING_FILE_BYTES + 1)
    loaded = read(tmp_path)
    assert loaded.learning is None
    assert loaded.learning_status == "absent"
    if orphan:
        with pytest.raises(SnapshotError, match="already contains"):
            ensure_capture_destination(tmp_path)


@pytest.mark.parametrize("kind", ["missing", "symlink", "directory", "fifo"])
def test_missing_or_nonregular_learning_rejected(tmp_path, kind):
    learning_capture(tmp_path)
    path = tmp_path / LEARNING_NAME
    original = path.read_bytes()
    path.unlink()
    if kind == "symlink":
        target = tmp_path / "real.json"
        target.write_bytes(original)
        path.symlink_to(target)
    elif kind == "directory":
        path.mkdir()
    elif kind == "fifo":
        os.mkfifo(path)
    with pytest.raises(SnapshotError):
        read(tmp_path)


@pytest.mark.parametrize(
    "key,value",
    [
        ("schema_version", 2),
        ("schema_version", True),
        ("schema_version", "1"),
        ("file", "../register_learning.json"),
        ("file", "/tmp/register_learning.json"),
        ("file", "alternate.json"),
        ("sha256", "0" * 64),
        ("sha256", "bad"),
        ("byte_size", 1),
        ("byte_size", True),
        ("byte_size", 0),
        ("byte_size", snapshots.MAX_LEARNING_FILE_BYTES + 1),
        ("status", "complete"),
        ("status", "unknown"),
        ("observation_count", 0),
        ("observation_count", True),
        ("observation_count", snapshots.MAX_LEARNING_OBSERVATIONS + 1),
        ("experiment_count", 0),
        ("experiment_count", -1),
        ("classification_count", 0),
        ("classification_count", "1"),
        ("unexpected", "private-donor-data-marker"),
    ],
)
def test_reference_fields_validated(tmp_path, key, value):
    learning_capture(tmp_path)
    edit_reference(tmp_path, key, value)
    with pytest.raises(SnapshotError):
        read(tmp_path)


@pytest.mark.parametrize("value", [None, [], "private-donor-data-marker", {}])
def test_invalid_reference_rejected_without_input_values(tmp_path, value):
    make_snapshot(tmp_path, context={"register_learning": value})
    with pytest.raises(SnapshotError) as error:
        read(tmp_path)
    message = extract_root_cause(error.value)
    assert "new --datastore" in message
    assert "private-donor-data-marker" not in message


@pytest.mark.parametrize("document", [b"{broken", b"[]", b"null", b"\xff"])
def test_corrupt_json_with_matching_checksum_rejected(tmp_path, document):
    learning_capture(tmp_path)
    set_document(tmp_path, document, update_reference=False)
    edit_reference(tmp_path, "sha256", hashlib.sha256(document).hexdigest())
    edit_reference(tmp_path, "byte_size", len(document))
    with pytest.raises(SnapshotError, match="schema or JSON"):
        read(tmp_path)


def test_same_size_corruption_rejected_before_parsing(tmp_path, monkeypatch):
    learning_capture(tmp_path)
    path = tmp_path / LEARNING_NAME
    path.write_bytes(path.read_bytes().replace(b"partial", b"invalid"))
    monkeypatch.setattr(
        LearningManifest,
        "model_validate_json",
        lambda *a, **k: pytest.fail("must not parse bad checksum"),
    )
    with pytest.raises(SnapshotError, match="checksum"):
        read(tmp_path)


def test_oversized_file_rejected_before_read_or_parse(tmp_path, monkeypatch):
    learning_capture(tmp_path)
    with (tmp_path / LEARNING_NAME).open("wb") as stream:
        stream.truncate(snapshots.MAX_LEARNING_FILE_BYTES + 1)
    monkeypatch.setattr(
        os, "open", lambda *a, **k: pytest.fail("must not open oversized")
    )
    monkeypatch.setattr(
        LearningManifest,
        "model_validate_json",
        lambda *a, **k: pytest.fail("must not parse oversized"),
    )
    with pytest.raises(SnapshotError, match="too large"):
        read(tmp_path)


def test_observation_limit_checked_before_model_parsing(tmp_path, monkeypatch):
    learning_capture(tmp_path)
    raw = json.loads((tmp_path / LEARNING_NAME).read_bytes())
    raw["observations"] = [None] * (snapshots.MAX_LEARNING_OBSERVATIONS + 1)
    document = json.dumps(raw).encode()
    set_document(tmp_path, document, update_reference=False)
    edit_reference(tmp_path, "sha256", hashlib.sha256(document).hexdigest())
    edit_reference(tmp_path, "byte_size", len(document))
    monkeypatch.setattr(
        LearningManifest,
        "model_validate_json",
        lambda *a, **k: pytest.fail("must not parse excessive events"),
    )
    with pytest.raises(SnapshotError, match="observation count"):
        read(tmp_path)


@pytest.mark.parametrize(
    "mutation",
    [
        lambda p: p.update(schema_version=2),
        lambda p: p.update(schema_version=True),
        lambda p: p["observations"][0].update(
            returned_value="private-donor-data-marker"
        ),
        lambda p: p["observations"][0]["ref"].update(width=3),
        lambda p: p["observations"][0]["ref"].update(offset=4096),
        lambda p: p["classifications"][0]["claims"][0].update(mask=2),
        lambda p: p["experiments"][0].update(observation_ids=[99]),
        lambda p: p.update(status="complete"),
        lambda p: p["context"].update(capture_id=str(uuid4())),
        lambda p: p["context"].update(bdf="0000:04:00.0"),
        lambda p: p["context"]["donor"].update(device_id=0x15),
    ],
)
def test_invalid_or_foreign_evidence_with_valid_checksum_rejected(tmp_path, mutation):
    learning_capture(tmp_path)
    raw = json.loads((tmp_path / LEARNING_NAME).read_bytes())
    mutation(raw)
    set_document(tmp_path, json.dumps(raw).encode())
    with pytest.raises(SnapshotError) as error:
        read(tmp_path)
    message = extract_root_cause(error.value)
    assert "new --datastore" in message
    assert "private-donor-data-marker" not in message


@pytest.mark.parametrize(
    "field",
    [
        "capture_id",
        "bdf",
        "vendor_id",
        "device_id",
        "revision_id",
        "class_code",
        "subsystem_vendor_id",
        "subsystem_device_id",
    ],
)
def test_write_rejects_mismatched_identity_before_publication(tmp_path, field):
    learning = manifest()
    raw = json.loads(learning.model_dump_json())
    if field == "capture_id":
        raw["context"][field] = str(uuid4())
    elif field == "bdf":
        raw["context"][field] = "0000:04:00.0"
    else:
        raw["context"]["donor"][field] ^= 1
    learning = LearningManifest.model_validate_json(json.dumps(raw))
    with pytest.raises(SnapshotError):
        write_snapshot(
            tmp_path,
            "03:00.0",
            config_bytes(),
            {},
            learning=learning,
            capture_id=CAPTURE_ID,
        )
    assert not list(tmp_path.iterdir())


def test_bridge_identity_omits_subsystem_fields(tmp_path):
    config = bytearray(config_bytes())
    config[14] = 1
    config = bytes(config)
    learning = manifest(config=config)
    write_snapshot(
        tmp_path, "03:00.0", config, {}, learning=learning, capture_id=CAPTURE_ID
    )
    assert read(tmp_path).learning == learning


@pytest.mark.parametrize("failure_at", [MSIX_NAME, CONTEXT_NAME, None])
def test_publication_order_and_interruption(tmp_path, monkeypatch, failure_at):
    real_link = os.link
    linked = []

    def link(source, destination):
        if destination.name == failure_at:
            raise OSError("simulated interruption")
        real_link(source, destination)
        linked.append(destination.name)

    monkeypatch.setattr(os, "link", link)
    if failure_at:
        with pytest.raises(SnapshotError, match="publication"):
            write_snapshot(
                tmp_path,
                "03:00.0",
                config_bytes(),
                {},
                learning=manifest(),
                capture_id=CAPTURE_ID,
            )
        assert linked == (
            [LEARNING_NAME] if failure_at == MSIX_NAME else [LEARNING_NAME, MSIX_NAME]
        )
        assert not (tmp_path / CONTEXT_NAME).exists()
        with pytest.raises(SnapshotError):
            read(tmp_path)
        with pytest.raises(SnapshotError, match="already contains"):
            write_snapshot(tmp_path, "03:00.0", config_bytes(), {})
    else:
        write_snapshot(
            tmp_path,
            "03:00.0",
            config_bytes(),
            {},
            learning=manifest(),
            capture_id=CAPTURE_ID,
        )
        assert linked == [LEARNING_NAME, MSIX_NAME, CONTEXT_NAME]
        assert read(tmp_path).learning == manifest()
    assert not list(tmp_path.glob(".capture-*"))
    assert (tmp_path / LEARNING_NAME).stat().st_mode & 0o777 == 0o644


def test_learning_destination_race_does_not_overwrite(tmp_path, monkeypatch):
    real_link = os.link

    def link(source, destination):
        destination.write_bytes(b"another writer won")
        real_link(source, destination)

    monkeypatch.setattr(os, "link", link)
    with pytest.raises(SnapshotError, match="publication"):
        write_snapshot(
            tmp_path,
            "03:00.0",
            config_bytes(),
            {},
            learning=manifest(),
            capture_id=CAPTURE_ID,
        )
    assert (tmp_path / LEARNING_NAME).read_bytes() == b"another writer won"
    assert not (tmp_path / CONTEXT_NAME).exists()
    assert not list(tmp_path.glob(".capture-*"))


@pytest.mark.parametrize("kind", ["file", "symlink"])
def test_orphan_learning_alone_blocks_destination(tmp_path, kind):
    path = tmp_path / LEARNING_NAME
    if kind == "file":
        path.write_bytes(b"orphan")
    else:
        path.symlink_to(tmp_path / "missing")
    with pytest.raises(SnapshotError, match="already contains"):
        ensure_capture_destination(tmp_path)


def test_writer_rejects_context_reference_without_manifest(tmp_path):
    with pytest.raises(SnapshotError, match="learning argument"):
        write_snapshot(
            tmp_path, "03:00.0", config_bytes(), {}, context={"register_learning": {}}
        )


def test_republication_rejects_missing_or_changed_evidence(tmp_path):
    learning_capture(tmp_path / "source")
    loaded = read(tmp_path / "source")
    with pytest.raises(SnapshotError, match="no evidence"):
        publish_snapshot(tmp_path / "missing", replace(loaded, learning=None))
    with pytest.raises(SnapshotError, match="serialized evidence"):
        publish_snapshot(
            tmp_path / "changed",
            replace(
                loaded, learning=loaded.learning.model_copy(update={"status": "failed"})
            ),
        )


@pytest.mark.parametrize("limit", ["bytes", "observations"])
def test_writer_enforces_learning_limits_before_publication(
    tmp_path, monkeypatch, limit
):
    if limit == "bytes":
        monkeypatch.setattr(snapshots, "MAX_LEARNING_FILE_BYTES", 1)
    else:
        monkeypatch.setattr(snapshots, "MAX_LEARNING_OBSERVATIONS", 0)
    with pytest.raises(SnapshotError, match="too large"):
        write_snapshot(
            tmp_path,
            "03:00.0",
            config_bytes(),
            {},
            learning=manifest(),
            capture_id=CAPTURE_ID,
        )
    assert not list(tmp_path.iterdir())


def test_learning_file_growth_after_stat_stays_bounded(tmp_path, monkeypatch):
    learning_capture(tmp_path)
    real_open = os.open

    def grow_then_open(path, flags, *args, **kwargs):
        with path.open("wb") as stream:
            stream.truncate(snapshots.MAX_LEARNING_FILE_BYTES + 1)
        return real_open(path, flags, *args, **kwargs)

    monkeypatch.setattr(os, "open", grow_then_open)
    with pytest.raises(SnapshotError, match="too large"):
        read(tmp_path)


def test_symlink_replacement_between_stat_and_open_is_rejected(tmp_path, monkeypatch):
    learning_capture(tmp_path)
    real_open = os.open
    target = tmp_path / "target.json"
    target.write_bytes((tmp_path / LEARNING_NAME).read_bytes())

    def replace_then_open(path, flags, *args, **kwargs):
        path.unlink()
        path.symlink_to(target)
        return real_open(path, flags, *args, **kwargs)

    monkeypatch.setattr(os, "open", replace_then_open)
    with pytest.raises(SnapshotError, match="unreadable"):
        read(tmp_path)


def test_all_temporary_files_fsynced_before_learning_link(tmp_path, monkeypatch):
    real_fsync, real_link = os.fsync, os.link
    events = []

    def fsync(fd):
        events.append("fsync")
        real_fsync(fd)

    def link(source, destination):
        events.append(destination.name)
        real_link(source, destination)

    monkeypatch.setattr(os, "fsync", fsync)
    monkeypatch.setattr(os, "link", link)
    write_snapshot(
        tmp_path,
        "03:00.0",
        config_bytes(),
        {},
        learning=manifest(),
        capture_id=CAPTURE_ID,
    )
    assert events[:9] == [
        "fsync",
        "fsync",
        "fsync",
        LEARNING_NAME,
        "fsync",
        MSIX_NAME,
        "fsync",
        CONTEXT_NAME,
        "fsync",
    ]
