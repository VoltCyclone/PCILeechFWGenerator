"""Fake-only lifecycle tests; these tests never construct a hardware backend."""

from contextlib import contextmanager
from threading import Event
from uuid import uuid4

import pytest
from pcileechfwgenerator.device_clone.register_evidence import Observation
from pcileechfwgenerator.host_collect.register_experiments import ExperimentJournal
from pcileechfwgenerator.host_collect.register_learning import (
    LearningDiscovery,
    LearningResource,
    RegisterLearningSession,
    run_register_learning,
)
from pcileechfwgenerator.host_collect.register_observer import ObserverResult

from tests.register_fakes import FakeClock, FakeRegisterDevice
from tests.test_register_experiments import recipe_for


@pytest.fixture
def discovery():
    config = bytearray(256)
    config[:4] = bytes.fromhex("de10d71a")
    return LearningDiscovery.from_config(
        bytes(config), resources=(LearningResource(index=0, size=4096),)
    )


def passive(device, *, value=0, dropped=0):
    return ObserverResult(
        observations=[
            Observation(
                sequence_id=90,
                monotonic_ns=0,
                operation="read",
                ref=device.ref,
                returned_value=value,
                completion="ok",
                source="native_driver",
                state_epoch=99,
            )
        ],
        lost_events=dropped,
    )


def session(tmp_path, discovery, **overrides):
    device = FakeRegisterDevice(mediation="physical")
    events = []

    @contextmanager
    def ownership():
        events.append("bind")
        try:
            yield
        finally:
            events.append("release")

    def observer(discovered, epoch):
        assert discovered == discovery and epoch == 0
        events.append("observe")
        return passive(device)

    def backend():
        events.append("backend")
        return device

    device.on_operation = lambda _: events.append("access")
    kwargs = dict(
        discover=lambda: discovery,
        journal_directory=tmp_path,
        passive_observer=observer,
        quiesce=lambda: events.append("quiesce"),
        ownership=ownership,
        collect_baseline=lambda _: events.append("baseline"),
        backend_factory=backend,
        experiment_plan=(recipe_for(device),),
        clock=FakeClock(),
    )
    kwargs.update(overrides)
    return RegisterLearningSession("03:00.0", **kwargs), device, events


def test_lifecycle_epochs_ids_and_validated_complete(tmp_path, discovery):
    service, device, events = session(tmp_path, discovery)
    capture_id = service.capture_id
    result = service.run()
    assert result.context.capture_id == capture_id
    assert events[:5] == ["observe", "quiesce", "bind", "baseline", "backend"]
    assert events[-1] == "release"
    assert events.count("bind") == events.count("release") == 1
    # Only bit 0 of the 32-bit register is measured; the other bits are merely
    # observed, so the register is not established and the capture is partial
    # (completeness must not over-claim).
    assert result.status == "partial"
    assert not result.gaps
    # The plan is persisted as the completeness denominator.
    (recipe,) = service.experiment_plan
    assert result.planned == (
        recipe.ref.model_copy(update={"field_mask": recipe.probe_mask}),
    )
    assert result.observations[0].state_epoch == 0
    assert all(o.state_epoch == 1 for o in result.observations[1:])
    assert [o.sequence_id for o in result.observations] == list(
        range(len(result.observations))
    )
    assert result.experiments[0].observation_ids == tuple(
        range(1, len(result.observations))
    )
    assert result.classifications[0].mask_for("rw", state_epoch=1) == 1
    assert result.classifications[0].mask_for("rw", state_epoch=0) == 0
    assert device.value == 0
    assert service.plan.discovery.resources[0].size == 4096
    assert not ExperimentJournal.find_unfinished(tmp_path)
    with pytest.raises(RuntimeError, match="single-use"):
        service.run()


def test_default_has_no_write_path_and_reports_gaps(tmp_path, discovery):
    result = run_register_learning(
        "03:00.0",
        discover=lambda: discovery,
        journal_directory=tmp_path,
    )
    assert result.status == "partial"
    # No observer supplied: the gap says so; no recipe gap (recipes are not
    # the learning path).
    assert {gap.reason for gap in result.gaps} == {"backend_unavailable"}
    assert not result.experiments and not result.observations
    assert not list(tmp_path.iterdir())


def test_providing_backend_does_not_enable_experiments(tmp_path, discovery):
    def forbidden():
        pytest.fail("empty default plan must not open a backend")

    result = run_register_learning(
        "03:00.0",
        discover=lambda: discovery,
        journal_directory=tmp_path,
        backend_factory=forbidden,
    )
    assert result.status == "partial"
    assert not result.experiments


def test_already_owned_never_nests_or_observes(tmp_path, discovery):
    def forbidden(*args):
        pytest.fail("cannot acquire or observe under an existing binder")

    service, _, events = session(
        tmp_path,
        discovery,
        already_owned=True,
        ownership=forbidden,
        passive_observer=forbidden,
    )
    result = service.run()
    assert result.experiments[0].restoration == "verified"
    assert events[0:3] == ["quiesce", "baseline", "backend"]
    assert "bind" not in events and "release" not in events
    assert any("ownership already held" in g.detail for g in result.gaps)


def test_no_exclusive_access_never_opens_backend(tmp_path, discovery):
    service, device, _ = session(tmp_path, discovery, ownership=None)
    result = service.run()
    assert not device.operations
    assert any("exclusive ownership" in g.detail for g in result.gaps)


@pytest.mark.parametrize(
    "boundary",
    ["passive_observer", "quiesce", "ownership", "backend_factory", "collect_baseline"],
)
def test_operational_boundary_failures_are_explicit(tmp_path, discovery, boundary):
    def fail(*args):
        raise RuntimeError(f"{boundary} unavailable")

    service, _, events = session(tmp_path, discovery, **{boundary: fail})
    result = service.run()
    assert result.status == "failed"
    assert any(boundary in g.detail for g in result.gaps)
    if boundary in ("backend_factory", "collect_baseline", "passive_observer"):
        assert events[-1] == "release"
    else:
        assert "baseline" not in events


def test_failed_binder_entry_does_not_call_exit(tmp_path, discovery):
    class Owner:
        def __enter__(self):
            raise OSError("lock unavailable")

        def __exit__(self, *args):
            pytest.fail("failed enter owns no session")

    service, device, events = session(tmp_path, discovery, ownership=Owner)
    assert service.run().status == "failed"
    assert not device.operations and "baseline" not in events


def test_release_failure_separate_from_successful_probe(tmp_path, discovery):
    @contextmanager
    def owner():
        yield
        raise OSError("restore driver failed")

    service, _, _ = session(tmp_path, discovery, ownership=owner)
    result = service.run()
    assert result.status == "failed"
    assert result.experiments[0].termination == "completed"
    assert result.experiments[0].restoration == "verified"
    assert any(g.reason == "restoration_failed" for g in result.gaps)


def test_programming_error_propagates_after_release(tmp_path, discovery):
    def fail():
        raise TypeError("programming error")

    service, _, events = session(tmp_path, discovery, backend_factory=fail)
    with pytest.raises(TypeError, match="programming error"):
        service.run()
    assert events[-1] == "release"


def test_observer_loss_cannot_be_complete(tmp_path, discovery):
    device = FakeRegisterDevice()
    service, _, _ = session(
        tmp_path,
        discovery,
        passive_observer=lambda *_: passive(device, dropped=3),
    )
    result = service.run()
    assert result.dropped_events == 3
    assert result.status == "partial"
    assert any(g.reason == "trace_incomplete" for g in result.gaps)


@pytest.mark.parametrize("when", ["before", "observe", "write"])
def test_cancellation_releases_and_recovers(tmp_path, discovery, when):
    cancel = Event()
    service, device, events = session(tmp_path, discovery, cancel=cancel)
    if when == "before":
        cancel.set()
    elif when == "observe":
        service.passive_observer = lambda *_: (cancel.set() or passive(device))
    else:
        device.on_operation = lambda d: cancel.set() if d.writes else None
    result = service.run()
    assert result.status == "failed"
    if when == "write":
        assert result.experiments[0].termination == "cancelled"
        assert result.experiments[0].restoration == "verified"
        assert events[-1] == "release"
        assert device.value == 0
    else:
        assert "bind" not in events and not device.operations


@pytest.mark.parametrize("corrupt", [False, True])
def test_unfinished_journal_blocks_without_replay(tmp_path, discovery, corrupt):
    device = FakeRegisterDevice()
    if corrupt:
        (tmp_path / "broken.json").write_text("{")
    else:
        ExperimentJournal(tmp_path, capture_id=uuid4()).begin(
            recipe_for(device),
            0,
            state_epoch=1,
            started_monotonic_ns=0,
        )
    before = {p.name: p.read_bytes() for p in tmp_path.iterdir()}
    service, backend, events = session(tmp_path, discovery)
    result = service.run()
    assert result.status == "failed"
    assert any("unfinished journal" in g.detail for g in result.gaps)
    assert not events and not backend.operations
    assert before == {p.name: p.read_bytes() for p in tmp_path.iterdir()}


def test_journal_scan_failure_is_reported(tmp_path, discovery, monkeypatch):
    def fail(_):
        raise PermissionError("journal unreadable")

    monkeypatch.setattr(ExperimentJournal, "find_unfinished", fail)
    service, _, events = session(tmp_path, discovery)
    result = service.run()
    assert result.status == "failed" and not events
    assert "journal scan failed" in result.gaps[0].detail


def test_probe_restoration_failure_stops_remaining_plan(tmp_path, discovery):
    service, device, events = session(tmp_path, discovery)
    device.fail_writes = frozenset({3})
    service.experiment_plan = (recipe_for(device), recipe_for(device))
    result = service.run()
    assert len(result.experiments) == 1
    assert result.experiments[0].restoration == "failed"
    assert any(g.reason == "restoration_failed" for g in result.gaps)
    assert result.status == "failed" and events[-1] == "release"


def test_discovery_failure_propagates_without_fabricating_identity(tmp_path):
    def fail():
        raise OSError("donor missing")

    with pytest.raises(OSError, match="donor missing"):
        run_register_learning("03:00.0", discover=fail, journal_directory=tmp_path)


def test_post_bind_initial_value_never_reuses_native_epoch(tmp_path, discovery):
    service, device, _ = session(tmp_path, discovery)
    service.passive_observer = lambda *_: passive(device, value=7)
    result = service.run()
    assert result.observations[0].returned_value == 7
    assert result.experiments[0].baseline_value == 0
    assert result.classifications[0].observed_initial_value == 0
    assert result.classifications[0].mask_for("rw", state_epoch=1) == 1


def test_failed_new_baseline_does_not_reuse_native_initial(tmp_path, discovery):
    service, device, _ = session(tmp_path, discovery)
    service.passive_observer = lambda *_: passive(device, value=7)
    device.fail_after = 0
    result = service.run()
    assert result.status == "failed"
    assert result.classifications[0].observed_initial_value is None


def test_failed_recovery_blocks_the_next_session(tmp_path, discovery):
    """a prior session that ended without verified recovery stops the
    next one before it binds, observes or writes."""
    from tests.test_register_experiments import execute

    def corrupt_recovery(device):
        if device.writes == 3:
            device.value = 1

    _, prior = execute(tmp_path, FakeRegisterDevice(on_operation=corrupt_recovery))
    assert prior.restoration == "failed"
    service, backend, events = session(tmp_path, discovery)
    result = service.run()
    assert result.status == "failed"
    assert any(
        "unfinished journal" in g.detail and "recovery unresolved" in g.detail
        for g in result.gaps
    )
    assert not events and not backend.operations


def test_session_journals_carry_donor_identity(tmp_path, discovery):
    import json

    service, _, _ = session(tmp_path, discovery)
    service.run()
    (path,) = [p for p in tmp_path.iterdir() if p.suffix == ".json"]
    data = json.loads(path.read_text())
    assert data["bdf"] == "0000:03:00.0"
    assert data["donor"]["vendor_id"] == discovery.donor.vendor_id


def test_default_journal_directory_is_host_wide(monkeypatch, tmp_path):
    """every entry point shares one journal location per host."""
    import pcileechfwgenerator.host_collect.register_learning as module

    monkeypatch.setenv(module.JOURNAL_DIR_ENV, str(tmp_path / "override"))
    assert module.default_journal_directory() == tmp_path / "override"
    monkeypatch.delenv(module.JOURNAL_DIR_ENV)
    monkeypatch.setattr(module.os, "geteuid", lambda: 0, raising=False)
    assert module.default_journal_directory() == module._SYSTEM_JOURNAL_DIR
    monkeypatch.setattr(module.os, "geteuid", lambda: 1000, raising=False)
    monkeypatch.setenv("XDG_STATE_HOME", str(tmp_path / "state"))
    assert module.default_journal_directory().is_relative_to(tmp_path / "state")


def test_both_collectors_use_the_shared_journal_directory():
    import inspect

    from pcileechfwgenerator.cli import host_device_collector
    from pcileechfwgenerator.host_collect import collector

    for module in (collector, host_device_collector):
        source = inspect.getsource(module)
        assert "journal_directory=default_journal_directory()" in source
        assert ".register_learning_journals" not in source


def test_delayed_storage_is_learned_end_to_end(tmp_path, discovery, monkeypatch):
    """executor -> classifier -> Stage 2 policy for a delayed RW bit."""
    from pcileechfwgenerator.device_clone.register_policy import (
        compile_manifest_bar_models,
        servable_models,
    )

    service, device, _ = session(tmp_path, discovery)
    device.delay_reads = 3
    service.experiment_plan = (recipe_for(device, readback_reads=3),)
    manifest = service.run()
    assert manifest.experiments[0].restoration == "verified"
    assert manifest.recipes[0].settling_reads == 3
    assert manifest.classifications[0].mask_for("rw", state_epoch=1) == 1
    models = servable_models(compile_manifest_bar_models(manifest, [(0, 4096)]))
    assert models[0].registers[0].rw_mask == 1


def test_unavailable_observer_is_a_gap_not_a_failure(tmp_path, discovery):
    from pcileechfwgenerator.host_collect.mmiotrace import ObserverUnavailable

    def unavailable(_discovery, _epoch):
        raise ObserverUnavailable("kernel lacks mmiotrace (CONFIG_MMIOTRACE)")

    result = run_register_learning(
        "03:00.0",
        discover=lambda: discovery,
        journal_directory=tmp_path,
        passive_observer=unavailable,
    )
    assert result.status != "failed"
    assert any("CONFIG_MMIOTRACE" in g.detail for g in result.gaps)


def test_observer_crash_after_changes_is_a_failure(tmp_path, discovery):
    def crashed(_discovery, _epoch):
        raise RuntimeError("Driver rebind left it on no driver")

    result = run_register_learning(
        "03:00.0",
        discover=lambda: discovery,
        journal_directory=tmp_path,
        passive_observer=crashed,
    )
    assert result.status == "failed"
