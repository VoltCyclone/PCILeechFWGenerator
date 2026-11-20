"""Bounded experiment and durable-journal tests, all using fake registers."""

import json
import threading
from pathlib import Path
from uuid import uuid4

import pytest
from pcileechfwgenerator.device_clone.register_evidence import (
    ExperimentResult,
    Observation,
    RegisterRef,
)
from pcileechfwgenerator.host_collect import register_experiments as experiments
from pcileechfwgenerator.host_collect.register_access import (
    AccessResult,
    validate_access,
)
from pcileechfwgenerator.host_collect.register_experiments import (
    ExperimentJournal,
    ExperimentRecipe,
    JournalRecord,
    run_experiment,
)
from pydantic import ValidationError

from tests.register_fakes import FakeClock, FakeRegisterDevice


def recipe_for(device, **changes):
    data = dict(
        recipe_id="fake-storage-field",
        version="1",
        ref=device.ref,
        legal_widths=(device.ref.width,),
        probe_mask=device.ref.field_mask,
        preserve_value_mask=0,
        write_zero_mask=device.ref.full_mask ^ device.ref.field_mask,
        max_steps=1000,
        time_budget_ns=1_000_000,
        restoration_strategy="restore_probed_bits",
        restoration_time_budget_ns=1000,
        preconditions={"ownership": "exclusive fake session"},
    )
    data.update(changes)
    return ExperimentRecipe(**data)


def execute(tmp_path, device, recipe=None, *, clock=None, cancel=None, journal=None):
    return run_experiment(
        device,
        recipe or recipe_for(device),
        state_epoch=7,
        sequence_start=40,
        clock=clock or FakeClock(),
        cancel=cancel,
        journal=journal or ExperimentJournal(tmp_path, capture_id=uuid4()),
    )


@pytest.mark.parametrize("initial", [0, 1])
@pytest.mark.parametrize("repeats", [1, 3])
def test_rw_both_directions_repeats_and_raw_evidence(tmp_path, initial, repeats):
    device = FakeRegisterDevice(value=initial, rw_mask=1, mediation="physical")
    recipe = recipe_for(device, repeat_count=repeats)
    obs, result = execute(tmp_path, device, recipe)
    assert result.termination == "completed"
    assert result.restoration == "verified"
    assert result.baseline_value == initial
    assert result.tested_mask == 1
    assert device.value == initial
    assert len(obs) == 2 + 6 * repeats + 3
    assert result.observation_ids == tuple(range(40, 40 + len(obs)))
    assert all(item.source == "collector" and item.state_epoch == 7 for item in obs)
    assert all(item.mediation == "physical" and item.duration_ns >= 0 for item in obs)
    writes = [item for item in obs if item.operation == "write"]
    assert [item.attempted_value for item in writes] == [
        initial ^ 1,
        initial,
    ] * repeats + [initial]
    assert all(item.byte_enable == 15 for item in writes)
    for item in obs:
        assert Observation.model_validate_json(item.model_dump_json()) == item
    assert ExperimentResult.model_validate_json(result.model_dump_json()) == result


@pytest.mark.parametrize("value", [0, 1])
def test_device_ro_is_completed_raw_probe_not_backend_rejection(tmp_path, value):
    device = FakeRegisterDevice(value=value, rw_mask=0)
    obs, result = execute(tmp_path, device)
    assert result.termination == "completed"
    assert result.tested_mask == 1  # exercised, not classified as RW
    assert result.restoration == "verified"
    assert {item.returned_value for item in obs if item.operation == "read"} == {value}
    assert all(item.completion == "ok" for item in obs)


def test_nonwritable_backend_is_precondition_failure_with_no_io(tmp_path):
    device = FakeRegisterDevice(writable=False)
    obs, result = execute(tmp_path, device)
    assert obs == [] and device.operations == []
    assert result.termination == "precondition_failed"
    assert result.tested_mask == 0
    assert result.baseline_value is None
    assert (
        result.preconditions["executor.baseline_absent_reason"]
        == "backend_rejected:not_writable"
    )
    assert result.restoration == "not_attempted"
    assert list(tmp_path.iterdir()) == []


def test_mixed_w1c_and_action_neighbors_never_written_one(tmp_path):
    device = FakeRegisterDevice(value=0x502, rw_mask=0x101, w1c_mask=2)
    recipe = recipe_for(device, preserve_value_mask=0x100, write_zero_mask=0xFFFFFEFE)
    obs, result = execute(tmp_path, device, recipe)
    assert result.restoration == "verified"
    assert device.value == 0x502
    writes = [item.attempted_value for item in obs if item.operation == "write"]
    assert writes == [0x101, 0x100, 0x100]
    assert all(value & recipe.write_zero_mask == 0 for value in writes)


def test_w1c_status_can_arrive_between_writes_without_being_cleared(tmp_path):
    def status_event(device):
        if device.writes == 1:
            device.set_status(2)

    device = FakeRegisterDevice(rw_mask=1, w1c_mask=2, on_operation=status_event)
    obs, result = execute(tmp_path, device)
    assert device.value == 2
    assert result.restoration == "verified"
    assert all(
        not item.attempted_value & 2 for item in obs if item.operation == "write"
    )


def test_preserve_policy_uses_current_neighbors_even_during_recovery(tmp_path):
    def external_update(device):
        if len(device.operations) == 8:
            device.value |= 0x100

    device = FakeRegisterDevice(rw_mask=0x101, on_operation=external_update)
    recipe = recipe_for(device, preserve_value_mask=0x100, write_zero_mask=0xFFFFFEFE)
    obs, result = execute(tmp_path, device, recipe)
    assert result.baseline_value == 0
    assert result.restoration == "verified"
    assert device.value == 0x100
    assert [item.attempted_value for item in obs if item.operation == "write"][
        -1
    ] == 0x100


def test_w1c_raw_probe_is_not_classified_and_can_be_nonrestorable(tmp_path):
    device = FakeRegisterDevice(value=1, rw_mask=0, w1c_mask=1)
    recipe = recipe_for(device, restoration_strategy="not_restorable")
    obs, result = execute(tmp_path, device, recipe)
    assert result.termination == "completed"
    assert result.restoration == "not_restorable"
    assert result.tested_mask == 1
    assert device.value == 0
    assert [item.attempted_value for item in obs if item.operation == "write"] == [0, 1]


def test_unstable_probe_baseline_stops_before_journal_or_writes(tmp_path):
    device = FakeRegisterDevice(volatile_mask=1)
    obs, result = execute(tmp_path, device)
    assert [item.returned_value for item in obs] == [1, 0]
    assert result.termination == "precondition_failed"
    assert result.restoration == "not_attempted"
    assert not device.writes
    assert list(tmp_path.iterdir()) == []


def test_volatile_write_zero_neighbor_does_not_invalidate_storage_baseline(tmp_path):
    device = FakeRegisterDevice(volatile_mask=2)
    obs, result = execute(tmp_path, device)
    assert result.termination == "completed"
    assert result.restoration == "verified"
    assert all(
        not item.attempted_value & 2 for item in obs if item.operation == "write"
    )


def test_delayed_completion_and_flush_reads_are_recorded(tmp_path):
    device = FakeRegisterDevice(delay_reads=3)
    flush = device.ref.model_copy(update={"offset": 4})
    recipe = recipe_for(device, readback_reads=3, flush_ref=flush)
    obs, result = execute(tmp_path, device, recipe)
    assert result.termination == "completed" and result.restoration == "verified"
    assert len([item for item in obs if item.ref == flush]) == 3
    first_write = next(
        index for index, item in enumerate(obs) if item.operation == "write"
    )
    assert [item.returned_value for item in obs[first_write + 1 : first_write + 5]] == [
        0,
        0,
        0,
        1,
    ]


def test_insufficient_settling_is_visible_as_failed_restoration(tmp_path):
    cancel = threading.Event()

    def cancel_after_write(device):
        if device.writes == 1:
            cancel.set()
            device.delay_reads = 2  # recovery transport now needs two reads

    device = FakeRegisterDevice(on_operation=cancel_after_write)
    obs, result = execute(tmp_path, device, cancel=cancel)
    # The away-write completed, but the restoration has not completed after
    # the recipe's single read. The mismatch must remain an explicit failure.
    assert result.termination == "cancelled"
    assert result.tested_mask == 0
    assert result.restoration == "failed"
    assert obs[-1].operation == "read"


@pytest.mark.parametrize("mediation", ["mediated", "unknown", "physical"])
def test_backend_mediation_is_preserved(tmp_path, mediation):
    obs, result = execute(tmp_path, FakeRegisterDevice(mediation=mediation))
    assert all(item.mediation == mediation for item in obs)
    assert result.termination == "completed"


@pytest.mark.parametrize("failure", ["short_io", "transport_error", "timeout"])
def test_failed_access_is_observed_and_recovery_still_runs(tmp_path, failure):
    device = FakeRegisterDevice(fail_after=4, failure=failure)
    obs, result = execute(tmp_path, device)
    assert result.termination == (
        "timeout" if failure == "timeout" else "backend_error"
    )
    assert result.restoration == "verified"
    assert result.tested_mask == 0
    failed = obs[4]
    assert failed.returned_value is None
    assert failed.absent_reason == failure
    assert failed.completion != "ok"
    assert device.value == 0


def test_short_write_also_attempts_recovery(tmp_path):
    device = FakeRegisterDevice(fail_after=3, failure="short_io")
    obs, result = execute(tmp_path, device)
    assert obs[3].operation == "write" and obs[3].completion == "error"
    assert result.restoration == "verified"
    assert device.writes == 2


def test_removed_device_records_recovery_failure(tmp_path):
    def remove(device):
        if device.writes:
            device.removed = True

    device = FakeRegisterDevice(on_operation=remove)
    obs, result = execute(tmp_path, device)
    assert result.termination == "backend_error"
    assert result.restoration == "failed"
    assert "device_removed" in result.restoration_detail
    assert obs[-1].returned_value is None
    assert device.value == 1


def test_persistent_transport_failure_is_bounded(tmp_path):
    device = FakeRegisterDevice(fail_after=4, persistent_failure=True)
    obs, result = execute(tmp_path, device)
    assert result.restoration == "failed"
    assert len(obs) == 6  # failed read, then one failed recovery read


def test_restoration_write_failure_kept_separate_from_completed_probe(tmp_path):
    device = FakeRegisterDevice(fail_writes=frozenset({3}))
    obs, result = execute(tmp_path, device)
    assert result.termination == "completed"
    assert result.tested_mask == 1
    assert result.restoration == "failed"
    assert "restoration_write_failed" in result.restoration_detail
    assert obs[-1].completion == "error"


def test_restoration_read_mismatch_is_failure(tmp_path):
    def corrupt_recovery(device):
        if device.writes == 3:
            device.value = 1

    device = FakeRegisterDevice(on_operation=corrupt_recovery)
    _, result = execute(tmp_path, device)
    assert result.restoration == "failed"
    assert "differs" in result.restoration_detail


def test_cancellation_after_first_write_restores_with_cancel_still_set(tmp_path):
    cancel = threading.Event()

    def cancel_after_write(device):
        if device.writes:
            cancel.set()

    device = FakeRegisterDevice(on_operation=cancel_after_write)
    obs, result = execute(tmp_path, device, cancel=cancel)
    assert cancel.is_set()
    assert result.termination == "cancelled"
    assert result.restoration == "verified"
    assert result.tested_mask == 0
    assert device.value == 0
    assert [item.attempted_value for item in obs if item.operation == "write"] == [1, 0]


def test_cancellation_before_run_has_no_io(tmp_path):
    cancel = threading.Event()
    cancel.set()
    device = FakeRegisterDevice()
    obs, result = execute(tmp_path, device, cancel=cancel)
    assert obs == [] and device.operations == []
    assert result.termination == "cancelled"
    assert result.restoration == "not_attempted"


def test_timeout_uses_independent_recovery_budget(tmp_path):
    clock = FakeClock()
    device = FakeRegisterDevice(on_operation=lambda _: clock.advance(10))
    recipe = recipe_for(device, time_budget_ns=40)
    obs, result = execute(tmp_path, device, recipe, clock=clock)
    assert result.termination == "timeout"
    assert result.restoration == "verified"
    assert device.value == 0
    assert [item.monotonic_ns for item in obs] == [0, 10, 20, 30, 40, 50, 60]


def test_recovery_timeout_is_explicit_and_bounded(tmp_path):
    clock = FakeClock()
    device = FakeRegisterDevice(on_operation=lambda _: clock.advance(10))
    recipe = recipe_for(device, time_budget_ns=40, restoration_time_budget_ns=10)
    obs, result = execute(tmp_path, device, recipe, clock=clock)
    assert result.restoration == "failed"
    assert "timeout" in result.restoration_detail
    assert len(obs) == 5


@pytest.mark.parametrize("limit,expected_mask", [(4, 0), (8, 1)])
def test_max_steps_and_partial_bit_coverage(tmp_path, limit, expected_mask):
    ref = RegisterRef(space="mem_bar", bar_index=0, offset=0, width=4, field_mask=3)
    device = FakeRegisterDevice(ref=ref, rw_mask=3)
    recipe = recipe_for(device, max_steps=limit)
    obs, result = execute(tmp_path, device, recipe)
    assert result.termination == "aborted"
    assert result.tested_mask == expected_mask
    assert result.restoration == "verified"
    assert len(obs) == limit + 3
    assert device.value == 0


def test_interrupted_repeat_is_not_counted_as_complete_bit_coverage(tmp_path):
    device = FakeRegisterDevice()
    _, result = execute(
        tmp_path, device, recipe_for(device, repeat_count=2, max_steps=8)
    )
    assert result.tested_mask == 0
    assert result.termination == "aborted"


def test_state_epoch_precondition_fails_without_io(tmp_path):
    device = FakeRegisterDevice()
    obs, result = execute(tmp_path, device, recipe_for(device, required_state_epoch=8))
    assert obs == []
    assert result.termination == "precondition_failed"
    assert result.restoration == "not_attempted"


@pytest.mark.parametrize(
    "changes",
    [
        {"probe_mask": 2},
        {"preserve_value_mask": 1},
        {"write_zero_mask": 0},
        {"write_zero_mask": 1 << 32},
        {"legal_widths": (2,)},
        {"readback_reads": 0},
        {"repeat_count": 0},
        {"restoration_max_steps": 2},
        {"time_budget_ns": 0},
        {"preconditions": {"executor.fake": "spoof"}},
    ],
)
def test_invalid_or_incomplete_recipe_rejected(changes):
    with pytest.raises(ValidationError):
        recipe_for(FakeRegisterDevice(), **changes)


def test_dynamic_backend_rejection_remains_distinct_in_observation(tmp_path):
    class RejectWrites(FakeRegisterDevice):
        def write(self, ref, value, byte_enable):
            region = self.region.model_copy(update={"writable": False})
            validate_access(
                (region,), ref, "write", value=value, byte_enable=byte_enable
            )
            raise AssertionError("unreachable")

    obs, result = execute(tmp_path, RejectWrites())
    assert result.termination == "backend_error"
    rejected = [item for item in obs if item.operation == "write"]
    assert all(item.completion == "unsupported" for item in rejected)
    assert all(
        item.absent_reason == "backend_rejected:not_writable" for item in rejected
    )
    assert result.tested_mask == 0


def test_transport_exception_is_explicit(tmp_path):
    class TransportException(FakeRegisterDevice):
        def read(self, ref):
            raise OSError("fixture transport unavailable")

    obs, result = execute(tmp_path, TransportException())
    assert result.termination == "backend_error"
    assert obs[0].duration_ns is None and obs[0].returned_value is None
    assert "transport_error" in obs[0].absent_reason


def test_backend_invalid_read_value_cannot_become_evidence(tmp_path):
    class BadBackend(FakeRegisterDevice):
        def read(self, ref):
            return AccessResult(completion="ok", value=1 << 32, mediation="mediated")

    obs, result = execute(tmp_path, BadBackend())
    assert result.termination == "backend_error"
    assert obs[0].returned_value is None
    assert obs[0].mediation == "mediated"
    assert "backend_contract_violation" in obs[0].absent_reason


def test_journal_is_durable_before_first_write_and_complete_after_recovery(
    tmp_path, monkeypatch
):
    journal = ExperimentJournal(tmp_path, capture_id=uuid4())
    events = []
    original_fsync, original_replace = experiments.os.fsync, experiments.os.replace

    def fsync(fd):
        events.append("fsync")
        original_fsync(fd)

    def replace(source, target):
        events.append("rename")
        original_replace(source, target)

    monkeypatch.setattr(experiments.os, "fsync", fsync)
    monkeypatch.setattr(experiments.os, "replace", replace)

    def inspect_before_write(device):
        if device.writes == 1:
            record = JournalRecord.model_validate_json(journal.path.read_text())
            assert record.status == "unfinished"
            assert record.baseline == 0 and record.capture_id == journal.capture_id
            assert record.state_epoch == 7
            assert events[:3] == ["fsync", "rename", "fsync"]

    device = FakeRegisterDevice(on_operation=inspect_before_write)
    _, result = execute(tmp_path, device, journal=journal)
    record = JournalRecord.model_validate_json(journal.path.read_text())
    assert record.status == "complete"
    assert record.result == result
    assert events == ["fsync", "rename", "fsync"] * 2
    assert ExperimentJournal.find_unfinished(tmp_path) == []
    assert sorted(path.name for path in tmp_path.iterdir()) == [journal.path.name]


def test_unfinished_and_corrupt_journals_reported_without_replay(tmp_path):
    device = FakeRegisterDevice()
    journal = ExperimentJournal(tmp_path, capture_id=uuid4())
    journal.begin(recipe_for(device), 0, state_epoch=7, started_monotonic_ns=123)
    (tmp_path / "broken.json").write_text("{partial")
    unfinished = ExperimentJournal.find_unfinished(tmp_path)
    assert len(unfinished) == 2
    assert {item.path for item in unfinished} == {
        journal.path,
        tmp_path / "broken.json",
    }
    valid = next(item.record for item in unfinished if item.record is not None)
    assert valid.started_monotonic_ns == 123
    assert device.operations == []
    second = ExperimentJournal(
        tmp_path, capture_id=uuid4(), session_id=journal.session_id
    )
    obs, result = execute(tmp_path, device, journal=second)
    assert result.termination == "aborted"
    assert result.restoration == "not_attempted"
    assert all(item.operation == "read" for item in obs)
    assert JournalRecord.model_validate_json(journal.path.read_text()) == valid


def test_journal_fsync_failure_prevents_writes(tmp_path, monkeypatch):
    def fail_fsync(fd):
        raise OSError("fixture disk failure")

    monkeypatch.setattr(experiments.os, "fsync", fail_fsync)
    device = FakeRegisterDevice()
    _, result = execute(tmp_path, device)
    assert result.termination == "aborted"
    assert "journal_begin_failed" in result.preconditions["executor.termination_detail"]
    assert device.writes == 0
    assert len(ExperimentJournal.find_unfinished(tmp_path)) == 1
    assert not list(tmp_path.glob(".journal-*"))


def test_journal_completion_failure_is_explicit_and_stays_unfinished(
    tmp_path, monkeypatch
):
    journal = ExperimentJournal(tmp_path, capture_id=uuid4())

    def fail_complete(result):
        raise OSError("fixture disk full")

    monkeypatch.setattr(journal, "complete", fail_complete)
    _, result = execute(tmp_path, FakeRegisterDevice(), journal=journal)
    assert result.termination == "aborted"
    assert result.restoration == "verified"
    assert "journal_complete_failed" in result.preconditions["executor.journal_error"]
    assert len(ExperimentJournal.find_unfinished(tmp_path)) == 1


def test_unexpected_programming_error_propagates_after_recovery(tmp_path):
    once = False

    def broken_adapter(device):
        nonlocal once
        if device.writes == 1 and not once:
            once = True
            raise RuntimeError("programming bug")

    device = FakeRegisterDevice(on_operation=broken_adapter)
    with pytest.raises(RuntimeError, match="programming bug"):
        execute(tmp_path, device)
    assert device.value == 0
    assert len(ExperimentJournal.find_unfinished(tmp_path)) == 1


def test_oversized_journal_is_reported(tmp_path):
    (tmp_path / "oversized.json").write_bytes(b" " * (ExperimentJournal.MAX_BYTES + 1))
    found = ExperimentJournal.find_unfinished(tmp_path)
    assert len(found) == 1 and found[0].record is None
    assert "size limit" in found[0].reason


def test_corrupt_complete_journal_is_not_hidden(tmp_path):
    journal = ExperimentJournal(tmp_path, capture_id=uuid4())
    journal.begin(
        recipe_for(FakeRegisterDevice()), 0, state_epoch=7, started_monotonic_ns=0
    )
    data = json.loads(journal.path.read_text())
    data["status"] = "complete"  # missing final result
    journal.path.write_text(json.dumps(data))
    found = ExperimentJournal.find_unfinished(tmp_path)
    assert len(found) == 1 and found[0].record is None
    assert "complete journal carries its result" in found[0].reason


def test_invalid_flush_region_rejected_before_any_access(tmp_path):
    device = FakeRegisterDevice()
    recipe = recipe_for(
        device, flush_ref=device.ref.model_copy(update={"bar_index": 1})
    )
    obs, result = execute(tmp_path, device, recipe)
    assert obs == [] and device.operations == []
    assert result.termination == "precondition_failed"


def test_discovery_failure_has_explicit_absent_baseline(tmp_path):
    class NoRegions(FakeRegisterDevice):
        def discover_regions(self):
            raise OSError("discovery unavailable")

    obs, result = execute(tmp_path, NoRegions())
    assert obs == []
    assert result.termination == "backend_error"
    assert (
        "region_discovery_failed"
        in result.preconditions["executor.baseline_absent_reason"]
    )


def test_cancellation_before_first_write_does_not_trigger_recovery_write(tmp_path):
    cancel = threading.Event()

    def stop_before_write(device):
        if len(device.operations) == 3:
            cancel.set()

    device = FakeRegisterDevice(on_operation=stop_before_write)
    obs, result = execute(tmp_path, device, cancel=cancel)
    assert result.termination == "cancelled"
    assert result.restoration == "not_attempted"
    assert all(item.operation == "read" for item in obs)
    assert device.writes == 0


def test_recovery_preserves_untested_probe_bits_from_current_state(tmp_path):
    cancel = threading.Event()

    def stop_after_first_bit(device):
        if device.writes == 1:
            device.value |= 2  # external update of a not-yet-probed field
            cancel.set()

    ref = RegisterRef(space="mem_bar", bar_index=0, offset=0, width=4, field_mask=3)
    device = FakeRegisterDevice(ref=ref, rw_mask=3, on_operation=stop_after_first_bit)
    obs, result = execute(tmp_path, device, cancel=cancel)
    assert result.restoration == "verified"
    assert result.tested_mask == 0
    assert device.value == 2
    assert [item.attempted_value for item in obs if item.operation == "write"] == [1, 2]


def test_missing_backend_duration_stays_unknown_in_observations(tmp_path):
    class NoDuration(FakeRegisterDevice):
        def read(self, ref):
            return super().read(ref).model_copy(update={"duration_ns": None})

        def write(self, ref, value, byte_enable):
            return (
                super()
                .write(ref, value, byte_enable)
                .model_copy(update={"duration_ns": None})
            )

    obs, result = execute(tmp_path, NoDuration())
    assert result.termination == "completed"
    assert all(item.duration_ns is None for item in obs)


def test_journal_directory_sync_failure_prevents_first_write(tmp_path, monkeypatch):
    original = experiments.os.fsync
    calls = 0

    def fail_directory(fd):
        nonlocal calls
        calls += 1
        if calls == 2:
            raise OSError("directory sync failed")
        original(fd)

    monkeypatch.setattr(experiments.os, "fsync", fail_directory)
    device = FakeRegisterDevice()
    _, result = execute(tmp_path, device)
    assert result.termination == "aborted"
    assert device.writes == 0
    assert len(ExperimentJournal.find_unfinished(tmp_path)) == 1


def test_wrong_result_cannot_complete_a_journal(tmp_path):
    journal = ExperimentJournal(tmp_path, capture_id=uuid4())
    _, result = execute(tmp_path, FakeRegisterDevice(), journal=journal)
    with pytest.raises(ValidationError, match="does not match"):
        journal.complete(result.model_copy(update={"state_epoch": 8}))
    with pytest.raises(ValueError, match="another journal"):
        journal.complete(result.model_copy(update={"experiment_id": str(uuid4())}))


def test_new_journal_directory_entries_are_synced(tmp_path, monkeypatch):
    paths = []
    original = ExperimentJournal._sync_directory

    def sync(path):
        paths.append(path)
        original(path)

    monkeypatch.setattr(ExperimentJournal, "_sync_directory", staticmethod(sync))
    directory = tmp_path / "capture" / "journals"
    journal = ExperimentJournal(directory, capture_id=uuid4())
    _, result = execute(tmp_path, FakeRegisterDevice(), journal=journal)
    assert result.termination == "completed"
    assert paths == [tmp_path, tmp_path / "capture", directory, directory]


def test_journal_discovery_does_not_silently_ignore_directory_permission_error(
    tmp_path, monkeypatch
):
    def denied(path):
        raise PermissionError("journal directory inaccessible")

    monkeypatch.setattr(Path, "iterdir", denied)
    with pytest.raises(PermissionError, match="inaccessible"):
        ExperimentJournal.find_unfinished(tmp_path)


def test_journal_discovery_of_missing_directory_returns_no_sessions(tmp_path):
    assert ExperimentJournal.find_unfinished(tmp_path / "absent") == []


# --- Hardware-safety review follow-ups -------------------------------------

from pcileechfwgenerator.device_clone.register_evidence import (
    CapabilityLocator as _Cap,
)  # noqa: E402
from pcileechfwgenerator.device_clone.register_evidence import RegisterRef as _Ref
from pcileechfwgenerator.host_collect.register_experiments import (  # noqa: E402
    ExperimentRecipe as _Recipe,
)


def _recipe_for(ref, probe):
    return dict(
        recipe_id="r",
        version="1",
        ref=ref,
        legal_widths=(ref.width,),
        probe_mask=probe,
        preserve_value_mask=ref.full_mask & ~probe,
        write_zero_mask=0,
        max_steps=100,
        time_budget_ns=10**9,
        restoration_strategy="restore_probed_bits",
        restoration_time_budget_ns=10**9,
    )


@pytest.mark.parametrize("offset", [0x10, 0x14, 0x24, 0x30])
def test_recipe_rejects_bar_and_rom_registers(offset):
    ref = _Ref(space="config", offset=offset, width=4, field_mask=0xFFFFFFFF)
    with pytest.raises(ValueError, match="never probed"):
        _Recipe(**_recipe_for(ref, 0x1))


@pytest.mark.parametrize("probe", [0x1, 0x2, 0x4])
def test_recipe_rejects_command_decode_and_bus_master_bits(probe):
    ref = _Ref(space="config", offset=0x04, width=4, field_mask=0xFFFFFFFF)
    with pytest.raises(ValueError, match="bus-master"):
        _Recipe(**_recipe_for(ref, probe))
    # INTx disable (bit 10) is not on the denylist.
    _Recipe(**_recipe_for(ref, 0x400))


def test_recipe_rejects_pm_power_state():
    cap = _Cap(kind="standard", cap_id=0x01, base_offset=0x40, relative_offset=4)
    ref = _Ref(space="config", offset=0x44, width=2, field_mask=0xFFFF, capability=cap)
    with pytest.raises(ValueError, match="power-state"):
        _Recipe(**_recipe_for(ref, 0x2))
    _Recipe(**_recipe_for(ref, 0x100))  # PME_En is allowed


def test_flush_read_recipe_can_still_yield_measured_rw(tmp_path):
    from pcileechfwgenerator.device_clone.register_classifier import (
        RecipeDeclaration,
        classify_registers,
    )

    device = FakeRegisterDevice(rw_mask=1, mediation="physical")
    flush = device.ref.model_copy(update={"offset": 4})
    recipe = recipe_for(device, flush_ref=flush)
    obs, result = execute(tmp_path, device, recipe)
    assert result.restoration == "verified"
    declaration = RecipeDeclaration(
        experiment_id=result.experiment_id,
        recipe_id=result.recipe_id,
        recipe_version=result.recipe_version,
        kind="storage",
        trace_complete=True,
    )
    target = [
        c
        for c in classify_registers(obs, [result], [declaration])
        if c.ref.offset == device.ref.offset
    ]
    assert target and target[0].mask_for("rw", state_epoch=result.state_epoch) == 1


def test_recipe_rejects_zeroing_bus_master_bits():
    ref = _Ref(space="config", offset=0x04, width=2, field_mask=0xFFFF)
    fields = _recipe_for(ref, 0x400)
    fields.update(preserve_value_mask=0xFFFF & ~0x400 & ~0x6, write_zero_mask=0x6)
    with pytest.raises(ValueError, match="zeroes"):
        _Recipe(**fields)


def test_capability_space_recipe_must_name_capability():
    ref = _Ref(space="config", offset=0x44, width=2, field_mask=0xFFFF)
    with pytest.raises(ValueError, match="must name it"):
        _Recipe(**_recipe_for(ref, 0x100))


@pytest.mark.parametrize("rel,probe", [(0x08, 0x8000), (0x10, 0x10), (0x10, 0x20)])
def test_recipe_rejects_flr_and_link_control(rel, probe):
    cap = _Cap(kind="standard", cap_id=0x10, base_offset=0x60, relative_offset=rel)
    ref = _Ref(
        space="config", offset=0x60 + rel, width=2, field_mask=0xFFFF, capability=cap
    )
    with pytest.raises(ValueError, match="link-control"):
        _Recipe(**_recipe_for(ref, probe))


def test_bridge_windows_and_type1_rom_are_never_probed():
    for offset in (0x28, 0x2C, 0x38):
        ref = _Ref(space="config", offset=offset, width=4, field_mask=0xFFFFFFFF)
        with pytest.raises(ValueError, match="never probed"):
            _Recipe(**_recipe_for(ref, 0x1))


# --- Ended is not recovered --------------------------------------------


def _corrupting_device():
    def corrupt_recovery(device):
        if device.writes == 3:
            device.value = 1

    return FakeRegisterDevice(on_operation=corrupt_recovery)


def test_failed_recovery_journal_still_blocks(tmp_path):
    """The review's reproducer: the journal used to be hidden once complete."""
    _, result = execute(tmp_path, _corrupting_device())
    assert result.restoration == "failed"
    found = ExperimentJournal.find_unfinished(tmp_path)
    assert len(found) == 1
    assert found[0].record is not None and found[0].record.status == "complete"
    assert found[0].reason.startswith("recovery unresolved: restoration failed")


def test_verified_recovery_journal_does_not_block(tmp_path):
    _, result = execute(tmp_path, FakeRegisterDevice())
    assert result.restoration == "verified"
    assert ExperimentJournal.find_unfinished(tmp_path) == []


def test_resolution_is_explicit_and_never_replays(tmp_path):
    device = _corrupting_device()
    execute(tmp_path, device)
    (entry,) = ExperimentJournal.find_unfinished(tmp_path)
    operations = list(device.operations)
    with pytest.raises(ValueError, match="note"):
        ExperimentJournal.resolve(entry.path, note="  ")
    assert ExperimentJournal.find_unfinished(tmp_path)  # still blocked
    resolved = ExperimentJournal.resolve(
        entry.path, note="power-cycled host; driver rebound and verified"
    )
    assert resolved.status == "resolved" and resolved.resolved_at
    assert resolved.result.restoration == "failed"  # history is preserved
    assert ExperimentJournal.find_unfinished(tmp_path) == []
    assert device.operations == operations  # nothing touched the device


def test_resolved_status_requires_note_and_timestamp(tmp_path):
    journal = ExperimentJournal(tmp_path, capture_id=uuid4())
    execute(tmp_path, FakeRegisterDevice(), journal=journal)
    data = json.loads(journal.path.read_text())
    data["status"] = "resolved"
    journal.path.write_text(json.dumps(data))
    (found,) = ExperimentJournal.find_unfinished(tmp_path)
    assert found.record is None and "resolution requires" in found.reason


def test_journal_records_donor_identity_and_old_journals_load(tmp_path):
    from pcileechfwgenerator.device_clone.register_evidence import DonorIdentity

    donor = DonorIdentity(
        vendor_id=0x8086, device_id=0x1533, revision_id=3, class_code=0x020000
    )
    journal = ExperimentJournal(
        tmp_path, capture_id=uuid4(), bdf="0000:03:00.0", donor=donor
    )
    execute(tmp_path, _corrupting_device(), journal=journal)
    (found,) = ExperimentJournal.find_unfinished(tmp_path)
    assert found.record.bdf == "0000:03:00.0" and found.record.donor == donor
    # A journal written before these fields existed still loads and blocks.
    data = json.loads(journal.path.read_text())
    for key in ("bdf", "donor", "resolved_at", "resolution_note"):
        data.pop(key)
    journal.path.write_text(json.dumps(data))
    (old,) = ExperimentJournal.find_unfinished(tmp_path)
    assert old.record is not None and old.record.donor is None


def test_resolve_command_lists_and_resolves(tmp_path, capsys):
    from pcileechfwgenerator.host_collect.register_experiments import main

    execute(tmp_path, _corrupting_device())
    assert main(["list", str(tmp_path)]) == 0
    listed = capsys.readouterr().out
    assert "recovery unresolved" in listed
    path = listed.split("\t")[0]
    assert main(["resolve", path, "--note", "rebound driver, verified"]) == 0
    assert ExperimentJournal.find_unfinished(tmp_path) == []


def test_stopped_after_first_write_needs_recovery(tmp_path):
    """tested_mask stays 0 when a run stops after its first write;
    an unverified restoration must still block the next session."""
    journal = ExperimentJournal(tmp_path, capture_id=uuid4())
    _, result = execute(tmp_path, FakeRegisterDevice(), journal=journal)
    for restoration, detail in [("not_restorable", None), ("failed", "x")]:
        stopped = result.model_copy(
            update={
                "tested_mask": 0,
                "termination": "cancelled",
                "restoration": restoration,
                "restoration_detail": detail,
            }
        )
        record = JournalRecord.model_validate_json(journal.path.read_text())
        assert record.model_copy(update={"result": stopped}).needs_recovery()
    nothing_written = result.model_copy(
        update={"tested_mask": 0, "restoration": "not_attempted"}
    )
    record = JournalRecord.model_validate_json(journal.path.read_text())
    assert not record.model_copy(update={"result": nothing_written}).needs_recovery()


def test_resolve_refuses_unfinished_without_force(tmp_path):
    journal = ExperimentJournal(tmp_path, capture_id=uuid4())
    journal.begin(
        recipe_for(FakeRegisterDevice()), 0, state_epoch=7, started_monotonic_ns=0
    )
    with pytest.raises(ValueError, match="still be running"):
        ExperimentJournal.resolve(journal.path, note="checked")
    assert ExperimentJournal.resolve(journal.path, note="checked", force=True)
    assert ExperimentJournal.find_unfinished(tmp_path) == []


def test_resolve_rejects_renamed_copy(tmp_path):
    execute(tmp_path, _corrupting_device())
    (entry,) = ExperimentJournal.find_unfinished(tmp_path)
    copy = tmp_path / "elsewhere" / "copy.json"
    copy.parent.mkdir()
    copy.write_bytes(entry.path.read_bytes())
    with pytest.raises(ValueError, match="does not match its session"):
        ExperimentJournal.resolve(copy, note="checked")
    assert ExperimentJournal.find_unfinished(tmp_path)  # real journal still blocks


def test_resolve_does_not_overwrite_a_concurrent_update(tmp_path, monkeypatch):
    execute(tmp_path, _corrupting_device())
    (entry,) = ExperimentJournal.find_unfinished(tmp_path)
    original = JournalRecord.model_validate

    def race(*args, **kwargs):
        record = original(*args, **kwargs)
        if getattr(record, "status", None) == "resolved":
            entry.path.write_text(entry.path.read_text() + " ")  # concurrent writer
        return record

    monkeypatch.setattr(JournalRecord, "model_validate", race)
    with pytest.raises(ValueError, match="changed while resolving"):
        ExperimentJournal.resolve(entry.path, note="checked")
