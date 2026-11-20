"""Passive learning from the native driver: mmiotrace -> inferred RW -> BAR RTL.

Synthetic traces only; no tracer, driver or device is touched.
"""

import logging

import pytest
from pcileechfwgenerator.device_clone.register_classifier import classify_register
from pcileechfwgenerator.device_clone.register_evidence import (
    Observation,
    RegisterRef,
)
from pcileechfwgenerator.host_collect import mmiotrace
from pcileechfwgenerator.host_collect.mmiotrace import (
    ObserverUnavailable,
    capture_native_probe,
    parse_mmiotrace,
)
from pcileechfwgenerator.host_collect.register_observer import BarResource

BAR0 = BarResource(bar_index=0, physical_start=0xF7E00000, size=0x4000)

# Driver probe: read ID (RO), read CTRL (0), set enable bit and read it back
# twice, write-then-read a scratch register in both directions, poke a status
# bit (W1C-like: writing 1 reads back 0), and another device's traffic.
PROBE = """\
VERSION 20070824
PCIDEV 0300 80861533 b 0 f7e00000 0 0 0 0 0 0 0 20000 0 0 0 0 0 0 e1000e
MAP 0.000100 1 0xf7e00000 0xffffc90000a00000 0x4000 0x0 0
R 4 0.000200 1 0xf7e00008 0x8086beef 0x0 0
R 4 0.000300 1 0xf7e00000 0x0 0x0 0
W 4 0.000400 1 0xf7e00000 0x1 0x0 0
R 4 0.000500 1 0xf7e00000 0x1 0x0 0
R 4 0.000550 1 0xf7e00000 0x1 0x0 0
R 4 0.000600 1 0xf7e00010 0x0 0x0 0
W 4 0.000700 1 0xf7e00010 0xa5a5a5a5 0x0 0
R 4 0.000800 1 0xf7e00010 0xa5a5a5a5 0x0 0
R 4 0.000850 1 0xf7e00010 0xa5a5a5a5 0x0 0
W 4 0.000900 1 0xf7e00010 0x5a5a5a5a 0x0 0
R 4 0.001000 1 0xf7e00010 0x5a5a5a5a 0x0 0
R 4 0.001050 1 0xf7e00010 0x5a5a5a5a 0x0 0
R 4 0.001100 1 0xf7e00020 0x4 0x0 0
W 4 0.001200 1 0xf7e00020 0x4 0x0 0
R 4 0.001300 1 0xf7e00020 0x0 0x0 0
R 4 0.001400 2 0xfe000000 0x12345678 0x0 0
UNMAP 0.002000 1 0x0 0
"""


def test_parser_attributes_donor_accesses_and_ignores_other_devices():
    result = parse_mmiotrace(PROBE.splitlines(), [BAR0])
    assert result.dropped_events == 0
    assert len(result.observations) == 15
    assert result.mapped_bars == [0]
    first = result.observations[0]
    assert (first.operation, first.ref.offset, first.returned_value) == (
        "read",
        8,
        0x8086BEEF,
    )
    assert first.source == "native_driver" and first.mediation == "physical"
    assert first.monotonic_ns == 200_000
    assert all(o.ref.bar_index == 0 for o in result.observations)


@pytest.mark.parametrize(
    "line,counter",
    [
        ("MARK 0.000000 Lost 17 events.", "lost_events"),
        ("mmiotrace has lost events", "lost_events"),
        ("UNKNOWN 0.0001 1 0xf7e00004 0x0 0x0 0", "discarded_observations"),
        ("R 4 0.0001 1 0xf7e00002 0x0 0x0 0", "unattributed_accesses"),
        ("R 4 0.0001 1 0xf7e03ffe 0x0 0x0 0", "unattributed_accesses"),
        ("R four 0.0001 1 0xf7e00000 0x0 0x0 0", "malformed_lines"),
        ("BOGUS 1 2 3", "malformed_lines"),
    ],
)
def test_parser_counts_anything_that_makes_the_trace_incomplete(line, counter):
    result = parse_mmiotrace([line], [BAR0])
    assert getattr(result, counter) >= 1 and result.dropped_events >= 1
    assert not result.observations


def test_parser_reports_lost_event_count():
    result = parse_mmiotrace(["MARK 0.000000 Lost 17 events."], [BAR0])
    assert result.lost_events == 17


# --- capture against a fake tracefs ---------------------------------------


@pytest.fixture
def host(tmp_path, monkeypatch):
    """Fake CPU hotplug state: tracing takes CPUs 1-3 offline."""
    cpus = tmp_path / "cpu"
    for n in range(4):
        (cpus / f"cpu{n}").mkdir(parents=True)
    online = cpus / "online"
    online.write_text("0-3\n")
    monkeypatch.setattr(mmiotrace, "CPU_ONLINE", online)
    monkeypatch.setattr(mmiotrace, "CPU_DIR", cpus)

    def set_online(cpu):  # what the kernel does on cpuN/online <- 1
        (cpus / f"cpu{cpu}" / "online").write_text("1")
        current = mmiotrace._cpu_set(online.read_text()) | {cpu}
        online.write_text(",".join(str(c) for c in sorted(current)) + "\n")

    monkeypatch.setattr(mmiotrace, "_set_cpu_online", set_online)
    return online


def _tracefs(tmp_path, *, available="nop function mmiotrace", current="nop"):
    root = tmp_path / "tracing"
    root.mkdir()
    (root / "available_tracers").write_text(available + "\n")
    (root / "set_event").write_text("")
    (root / "current_tracer").write_text(current + "\n")
    (root / "tracing_on").write_text("0\n")  # as found on a real host
    (root / "trace_pipe").write_text(PROBE)
    return root


def test_capture_enables_tracer_around_the_rebind_and_restores_nop(tmp_path, host):
    root = _tracefs(tmp_path)
    device = tmp_path / "device"
    device.mkdir()
    (device / "reset").write_text("")
    (device / "reset_method").write_text("flr pm\n")
    seen = []

    def rebind(after_unbind):
        seen.append((root / "current_tracer").read_text().strip())
        if len(seen) == 1:  # the traced probe
            assert (root / "tracing_on").read_text().strip() == "1"
            host.write_text("0\n")  # what mmiotrace does to the host
        assert (tmp_path / "j" / "tracer" / mmiotrace.TRACER_JOURNAL).exists()
        after_unbind()

    result = capture_native_probe(
        "0000:03:00.0",
        [BAR0],
        duration_sec=0,
        tracefs=root,
        rebind=rebind,
        sysfs_device=device,
        journal_dir=tmp_path / "j",
    )
    # Traced probe, then an untraced re-probe with every CPU back online so
    # the driver re-sizes per-CPU queues/vectors.
    assert seen == ["mmiotrace", "nop"]
    assert (device / "reset").read_text() == "1"  # function reset while unbound
    assert result.reset_performed is True
    assert (root / "current_tracer").read_text().strip() == "nop"
    assert (root / "tracing_on").read_text().strip() == "0"  # restored
    assert all(
        (host.parent / f"cpu{n}" / "online").read_text() == "1" for n in (1, 2, 3)
    )
    assert not (tmp_path / "j" / "tracer" / mmiotrace.TRACER_JOURNAL).exists()
    assert len(result.observations) == 15
    # The fake kernel log is unreadable, so loss cannot be ruled out.
    assert result.capture_incomplete >= 1


@pytest.mark.parametrize("methods", ["bus", "flr bus", ""])
def test_unconfined_reset_is_skipped_and_reported(tmp_path, host, methods):
    root = _tracefs(tmp_path)
    device = tmp_path / "device"
    device.mkdir()
    (device / "reset").write_text("")
    (device / "reset_method").write_text(methods + "\n")
    result = capture_native_probe(
        "0000:03:00.0",
        [BAR0],
        duration_sec=0,
        tracefs=root,
        rebind=lambda after: after(),
        sysfs_device=device,
        journal_dir=tmp_path / "j",
    )
    assert (device / "reset").read_text() == ""
    assert result.reset_performed is False


def test_capture_without_a_donor_mapping_is_unavailable(tmp_path, host):
    root = _tracefs(tmp_path)
    (root / "trace_pipe").write_text("R 4 0.1 1 0xf7e00000 0x0 0x0 0\n")
    with pytest.raises(ObserverUnavailable, match="no donor BAR mapping"):
        capture_native_probe(
            "0000:03:00.0",
            [BAR0],
            duration_sec=0,
            tracefs=root,
            rebind=lambda after: None,
            journal_dir=tmp_path / "j",
        )


def test_lockdown_is_unavailable_before_tracing(tmp_path, host, monkeypatch):
    root = _tracefs(tmp_path)
    lockdown = tmp_path / "lockdown"
    lockdown.write_text("none [integrity] confidentiality\n")
    monkeypatch.setattr(mmiotrace, "LOCKDOWN", lockdown)
    with pytest.raises(ObserverUnavailable, match="lockdown"):
        capture_native_probe(
            "0000:03:00.0",
            [BAR0],
            duration_sec=0,
            tracefs=root,
            rebind=lambda after: pytest.fail("must not rebind"),
            journal_dir=tmp_path / "j",
        )
    assert (root / "current_tracer").read_text().strip() == "nop"


def test_interrupted_capture_is_recovered_on_next_run(tmp_path, host):
    root = _tracefs(tmp_path, current="mmiotrace")
    host.write_text("0\n")
    journal = tmp_path / "j"
    journal.mkdir()
    (journal / mmiotrace.TRACER_JOURNAL).write_text(
        '{"bdf": "0000:03:00.0", "cpus_online": [0, 1, 2, 3], "tracing_on": "0"}'
    )
    (root / "tracing_on").write_text("1\n")
    notes = mmiotrace.recover_stale_tracer(root, journal)
    assert notes and (root / "current_tracer").read_text().strip() == "nop"
    assert (root / "tracing_on").read_text().strip() == "0"
    assert (host.parent / "cpu3" / "online").read_text() == "1"
    assert not (journal / mmiotrace.TRACER_JOURNAL).exists()


def test_foreign_mmiotrace_is_left_alone(tmp_path, host):
    root = _tracefs(tmp_path, current="mmiotrace")
    assert mmiotrace.recover_stale_tracer(root, tmp_path) == []
    assert (root / "current_tracer").read_text().strip() == "mmiotrace"


def test_tracer_that_cannot_be_disabled_keeps_the_journal(tmp_path, host, monkeypatch):
    root = _tracefs(tmp_path)
    monkeypatch.setattr(mmiotrace, "_stop_tracer", lambda *a: "stuck")
    with pytest.raises(mmiotrace.HostRestorationError, match="stuck"):
        capture_native_probe(
            "0000:03:00.0",
            [BAR0],
            duration_sec=0,
            tracefs=root,
            rebind=lambda after: None,
            journal_dir=tmp_path / "j",
        )
    assert (tmp_path / "j" / "tracer" / mmiotrace.TRACER_JOURNAL).exists()


@pytest.mark.parametrize(
    "kwargs,reason",
    [
        ({"available": "nop function"}, "CONFIG_MMIOTRACE"),
        ({"current": "function"}, "already active"),
    ],
)
def test_capture_unavailable_before_changing_anything(tmp_path, host, kwargs, reason):
    root = _tracefs(tmp_path, **kwargs)

    def rebind(_after):
        pytest.fail("must not rebind")

    with pytest.raises(ObserverUnavailable, match=reason):
        capture_native_probe(
            "0000:03:00.0",
            [BAR0],
            duration_sec=0,
            tracefs=root,
            rebind=rebind,
            journal_dir=tmp_path / "j",
        )


def test_refused_rebind_is_unavailable_and_tracer_restored(tmp_path, host):
    root = _tracefs(tmp_path)

    def refuse(_after):
        raise RuntimeError("Refusing to rebind 0000:03:00.0: network eth0 is up")

    with pytest.raises(ObserverUnavailable, match="eth0 is up"):
        capture_native_probe(
            "0000:03:00.0",
            [BAR0],
            duration_sec=0,
            tracefs=root,
            rebind=refuse,
            journal_dir=tmp_path / "j",
        )
    assert (root / "current_tracer").read_text().strip() == "nop"


def test_failed_rebind_is_a_real_error_and_tracer_restored(tmp_path, host):
    root = _tracefs(tmp_path)

    def broken(_after):
        raise RuntimeError("Driver rebind left it on no driver")

    with pytest.raises(RuntimeError, match="no driver"):
        capture_native_probe(
            "0000:03:00.0",
            [BAR0],
            duration_sec=0,
            tracefs=root,
            rebind=broken,
            journal_dir=tmp_path / "j",
        )
    assert (root / "current_tracer").read_text().strip() == "nop"


def test_tests_never_reach_the_real_tracer():
    with pytest.raises(ObserverUnavailable):
        mmiotrace.find_tracefs()


def test_bar_resources_skip_io_and_unassigned_bars():
    resource = "\n".join(
        [
            "0x00000000f7e00000 0x00000000f7e03fff 0x0000000000040200",
            "0x0000000000000000 0x0000000000000000 0x0000000000000000",
            "0x000000000000e000 0x000000000000e01f 0x0000000000040101",
        ]
        + ["0x0 0x0 0x0"] * 3
    )
    header = bytearray(64)
    header[16 + 2 * 4] = 1  # BAR2 is I/O
    assert mmiotrace.bar_resources(resource, bytes(header)) == [BAR0]


# --- inference rule -------------------------------------------------------


def _ref(offset=0):
    return RegisterRef(
        space="mem_bar", bar_index=0, offset=offset, width=4, field_mask=0xFFFFFFFF
    )


def _native(pairs, **overrides):
    out = []
    for i, (op, value) in enumerate(pairs):
        fields = dict(
            sequence_id=i,
            monotonic_ns=i * 10,
            operation=op,
            ref=_ref(),
            completion="ok",
            source="native_driver",
            mediation="physical",
            state_epoch=0,
        )
        fields["returned_value" if op == "read" else "attempted_value"] = value
        fields.update(overrides)
        out.append(Observation(**fields))
    return out


def _rw(pairs, **overrides):
    return classify_register(_ref(), _native(pairs, **overrides)).mask_for(
        "rw", state_epoch=0
    )


@pytest.mark.parametrize(
    "pairs,expected",
    [
        ([("read", 0), ("write", 1), ("read", 1), ("read", 1)], 1),  # set
        ([("read", 0), ("write", 1), ("read", 0), ("read", 1), ("read", 1)], 1),
        ([("read", 1), ("write", 0), ("read", 0), ("read", 0)], 1),  # cleared
        ([("read", 0), ("write", 1), ("read", 1)], 1),  # one flush read
        ([("read", 0), ("write", 1), ("read", 0)], 0),  # ignored write
        ([("read", 1), ("write", 1), ("read", 0)], 0),  # W1C / self-clearing
        ([("read", 0), ("write", 1), ("read", 1), ("read", 0)], 0),  # bounce
        ([("read", 0), ("read", 1), ("write", 0), ("read", 0)], 0),  # volatile
        ([("write", 1), ("read", 1)], 1),  # no known prior value
        ([("read", 0), ("write", 0), ("read", 0)], 1),  # no transition
        ([("read", 0), ("write", 1), ("write", 0), ("read", 0)], 1),  # unread
        ([("write", 1)], 1),  # write-once: never read back
        ([("read", 0), ("read", 0)], 0),  # never written
    ],
)
def test_inferred_storage_rule(pairs, expected):
    assert _rw(pairs) & 1 == expected


def test_inference_needs_physical_native_driver_evidence():
    pairs = [("read", 0), ("write", 1), ("read", 1), ("read", 1)]
    assert _rw(pairs) & 1
    assert _rw(pairs, mediation="unknown") == 0  # e.g. kprobe tracing
    assert _rw(pairs, source="simulator") == 0


def test_inferred_claim_names_its_evidence():
    result = classify_register(
        _ref(), _native([("read", 0), ("write", 1), ("read", 1), ("read", 1)])
    )
    claim = next(c for c in result.claims if c.semantic == "rw")
    assert claim.strength == "inferred" and claim.observation_ids
    assert "read back" in claim.note


def test_write_once_claim_says_it_was_never_read_back():
    result = classify_register(_ref(), _native([("read", 0), ("write", 1)]))
    claim = next(c for c in result.claims if c.semantic == "rw")
    assert claim.strength == "inferred" and "never read back" in claim.note


def _manifest(tmp_path, monkeypatch, *, dropped=0):
    from pcileechfwgenerator.host_collect.register_learning import (
        LearningDiscovery,
        LearningResource,
        run_register_learning,
    )

    config = bytearray(256)
    config[:4] = bytes.fromhex("86803315")
    discovery = LearningDiscovery.from_config(
        bytes(config), resources=(LearningResource(index=0, size=0x4000),)
    )

    def observer(_discovery, epoch):
        result = parse_mmiotrace(PROBE.splitlines(), [BAR0], state_epoch=epoch)
        result.lost_events += dropped
        return result

    return run_register_learning(
        "03:00.0",
        discover=lambda: discovery,
        journal_directory=tmp_path,
        passive_observer=observer,
    )


def test_native_probe_trace_becomes_a_servable_model(tmp_path, monkeypatch):
    from pcileechfwgenerator.device_clone.register_policy import (
        compile_manifest_bar_models,
        servable_models,
    )

    manifest = _manifest(tmp_path, monkeypatch)
    assert manifest.status != "failed"
    result = compile_manifest_bar_models(manifest, [(0, 0x4000)])[0]
    model = servable_models({0: result})[0]
    regs = model.registers
    assert regs[0x8].rw_mask == 0 and regs[0x8].reset == 0x8086BEEF  # ID, RO
    # Writing 0x1 also writes 0 to the other bits, none contradicted.
    assert regs[0x0].rw_mask == 0xFFFFFFFF and regs[0x0].reset == 0
    # Scratch: every bit changed both ways and read back.
    assert regs[0x10].rw_mask == 0xFFFFFFFF and regs[0x10].reset == 0
    # W1C-like bit 2 read back 0 after writing 1: refused; the rest written 0.
    assert regs[0x20].rw_mask == 0xFFFFFFFB and regs[0x20].reset == 4
    assert result.coverage.inferred_bits == 32 + 32 + 31
    assert not any(g.reason == "initial_state_unverified" for g in manifest.gaps)


def test_incomplete_trace_grants_no_writes(tmp_path, monkeypatch):
    from pcileechfwgenerator.device_clone.register_policy import (
        compile_manifest_bar_models,
    )

    manifest = _manifest(tmp_path, monkeypatch, dropped=1)
    results = compile_manifest_bar_models(manifest, [(0, 0x4000)], include_empty=True)
    model = results[0].model
    assert all(r.rw_mask == 0 for r in model.registers.values())
    # A lost write could precede any read: no value counts as reset state.
    assert all(r.reset == 0 for r in model.registers.values())


def test_learned_model_renders_as_the_served_bar(tmp_path, monkeypatch):
    from pcileechfwgenerator.device_clone.bar_model_loader import serialize_bar_model
    from pcileechfwgenerator.device_clone.register_policy import (
        compile_manifest_bar_models,
        servable_models,
    )
    from pcileechfwgenerator.templating.sv_overlay_generator import (
        SVOverlayGenerator,
    )
    from pcileechfwgenerator.templating.template_renderer import TemplateRenderer

    manifest = _manifest(tmp_path, monkeypatch)
    model = servable_models(compile_manifest_bar_models(manifest, [(0, 0x4000)]))[0]
    context = {
        "header": "// gen",
        "device_signature": "fixture",
        "bar_config": {
            "served_bar_index": 0,
            "aperture_size": 0x4000,
            "bars": [{"size": 0x4000}],
            "bar_models": {0: serialize_bar_model(model)},
        },
    }
    generator = SVOverlayGenerator(TemplateRenderer(), logging.getLogger("t"))
    rtl = generator._generate_bar_implementation(context)
    assert rtl and "READ-ONLY FALLBACK" not in rtl
    assert "8086beef" in rtl.lower()


def test_driver_written_value_is_not_the_initial_value(tmp_path):
    """A register the driver writes before ever reading has no reset value."""
    from pcileechfwgenerator.device_clone.register_policy import _initial_in_epoch

    obs = _native([("write", 1), ("read", 1), ("read", 1)])
    assert _initial_in_epoch(_ref(), obs, 0) is None
    obs = _native([("read", 0), ("write", 1), ("read", 1)])
    assert _initial_in_epoch(_ref(), obs, 0) == 0


def test_capture_without_reset_is_recorded_as_a_gap(tmp_path):
    from pcileechfwgenerator.host_collect.register_learning import (
        LearningDiscovery,
        LearningResource,
        run_register_learning,
    )

    config = bytearray(256)
    config[:4] = bytes.fromhex("86803315")
    discovery = LearningDiscovery.from_config(
        bytes(config), resources=(LearningResource(index=0, size=0x4000),)
    )

    def observer(_discovery, epoch):
        result = parse_mmiotrace(PROBE.splitlines(), [BAR0], state_epoch=epoch)
        result.reset_performed = False
        return result

    manifest = run_register_learning(
        "03:00.0",
        discover=lambda: discovery,
        journal_directory=tmp_path,
        passive_observer=observer,
    )
    assert any(g.reason == "initial_state_unverified" for g in manifest.gaps)


# --- Follow-ups -------------------------------------------------------------


def _capture(tmp_path, root, rebind, **kwargs):
    return capture_native_probe(
        "0000:03:00.0",
        [BAR0],
        duration_sec=0,
        tracefs=root,
        rebind=rebind,
        journal_dir=tmp_path / "j",
        **kwargs,
    )


def test_tracer_journal_does_not_block_the_experiment_scan(tmp_path):
    from pcileechfwgenerator.host_collect.register_experiments import (
        ExperimentJournal,
    )

    tracer_dir = tmp_path / "tracer"
    tracer_dir.mkdir()
    (tracer_dir / mmiotrace.TRACER_JOURNAL).write_text('{"cpus_online": [0]}')
    assert ExperimentJournal.find_unfinished(tmp_path) == []


def test_shutdown_failure_replaces_an_error_in_flight(tmp_path, host, monkeypatch):
    """A refused rebind must not hide a tracer that could not be stopped."""
    root = _tracefs(tmp_path)
    monkeypatch.setattr(mmiotrace, "_stop_tracer", lambda *a: "stuck")

    def refuse(_after):
        raise RuntimeError("Refusing to rebind: in use")

    with pytest.raises(mmiotrace.HostRestorationError, match="stuck"):
        _capture(tmp_path, root, refuse)


def test_failed_rebind_is_a_restoration_failure(tmp_path, host):
    root = _tracefs(tmp_path)

    def broken(_after):
        raise RuntimeError("Driver rebind left it on no driver")

    with pytest.raises(mmiotrace.HostRestorationError, match="no driver"):
        _capture(tmp_path, root, broken)
    assert (root / "current_tracer").read_text().strip() == "nop"


def test_concurrent_capture_is_refused(tmp_path, host):
    root = _tracefs(tmp_path)
    # One host-wide lock, whatever journal directory each run uses.
    with mmiotrace._HostLock(mmiotrace.HOST_LOCK):
        with pytest.raises(ObserverUnavailable, match="another"):
            _capture(tmp_path, root, lambda after: pytest.fail("ran"))
    assert (root / "current_tracer").read_text().strip() == "nop"


def test_stalled_probe_stops_tracing_at_the_deadline(tmp_path, host, monkeypatch):
    import time

    root = _tracefs(tmp_path)
    monkeypatch.setattr(mmiotrace, "PROBE_DEADLINE_SEC", 0.2)
    states = []

    def stalled(_after):
        # Stall until tracing is switched off (bounded). Released on the
        # event, not on a wall-clock timer, so a slow host cannot let the
        # probe finish inside the deadline. If capture waited on the probe
        # before stopping the tracer, this would time out and still read
        # "mmiotrace".
        deadline = time.monotonic() + 5
        tracer = root / "current_tracer"
        while tracer.read_text().strip() != "nop" and time.monotonic() < deadline:
            time.sleep(0.01)
        states.append(tracer.read_text().strip())

    result = _capture(tmp_path, root, stalled)
    # Tracer was already off when the probe ended. That late probe ran with
    # every CPU back, so no restoring re-probe is needed (or attempted).
    assert states == ["nop"]
    assert any("deadline" in n for n in result.notes)
    assert result.dropped_events >= 1


def test_unaligned_mapping_marks_trace_incomplete():
    trace = PROBE.replace("MAP 0.000100 1 0xf7e00000", "MAP 0.000100 1 0xf7e00010")
    result = parse_mmiotrace(trace.splitlines(), [BAR0])
    assert result.capture_incomplete == 1 and result.dropped_events >= 1


@pytest.mark.parametrize(
    "line,trouble",
    [
        ("6,1,2,-;kmmio: recursive probe hit on CPU 0", True),
        ("4,2,3,-;mmiotrace: Unable to set page fault", True),
        ("4,2,3,-;mmiotrace: Error taking CPU1 down: -16", True),
        # Logged by the kernel when the ring buffer refuses records.
        ("4,5,6,-;mmiotrace has lost events", True),
        ("6,3,4,-;mmiotrace: enabled CPU1.", False),
        ("6,3,4,-;mmiotrace: MMIO tracing disabled.", False),
    ],
)
def test_kernel_log_trouble_patterns(line, trouble):
    text = line.split(";", 1)[1]
    assert bool(mmiotrace._KMSG_TROUBLE.search(text)) is trouble


def test_mismatched_read_back_is_refused_even_after_writes_elsewhere():
    """Index/data: a read-back of another index's data contradicts storage."""
    data = _native([("read", 0), ("write", 1), ("read", 0)])
    index = Observation(
        sequence_id=10,
        monotonic_ns=15,
        operation="write",
        ref=RegisterRef(
            space="mem_bar", bar_index=0, offset=4, width=4, field_mask=0xFFFFFFFF
        ),
        attempted_value=3,
        completion="ok",
        source="native_driver",
        mediation="physical",
        state_epoch=0,
    )
    result = classify_register(_ref(), [*data, index])
    assert result.mask_for("rw", state_epoch=0) & 1 == 0


def test_msix_table_and_pba_are_left_out_of_learned_evidence():
    config = bytearray(256)
    config[0x34] = 0x40
    config[0x40:0x42] = bytes([0x11, 0x00])  # MSI-X, next = 0
    config[0x42:0x44] = (3).to_bytes(2, "little")  # 4 entries
    config[0x44:0x48] = (0x2000 | 0).to_bytes(4, "little")  # table BAR0+0x2000
    config[0x48:0x4C] = (0x3000 | 0).to_bytes(4, "little")  # PBA BAR0+0x3000
    config[0x06] = 0x10  # capability list present
    ranges = mmiotrace.msix_ranges(bytes(config))
    assert ranges == [(0, 0x2000, 0x2040), (0, 0x3000, 0x3008)]
    trace = PROBE + "R 4 0.003000 1 0xf7e02000 0x0 0x0 0\n"
    result = parse_mmiotrace(trace.splitlines(), [BAR0])
    before = len(result.observations)
    mmiotrace.drop_reserved_ranges(result, ranges)
    assert len(result.observations) == before - 1
    assert all(o.ref.offset != 0x2000 for o in result.observations)
    # Not served by anything: reported, never presented as handled.
    assert len(result.excluded_ranges) == 2
    assert all("not served" in r for r in result.excluded_ranges)


def test_degraded_host_blocks_the_build_and_stops_collection(tmp_path):
    from pcileechfwgenerator.device_clone.register_policy import (
        learning_build_blockers,
    )
    from pcileechfwgenerator.host_collect.register_learning import (
        LearningDiscovery,
        LearningResource,
        run_register_learning,
    )

    config = bytearray(256)
    config[:4] = bytes.fromhex("86803315")
    discovery = LearningDiscovery.from_config(
        bytes(config), resources=(LearningResource(index=0, size=0x4000),)
    )
    quiesced = []

    def observer(_discovery, _epoch):
        raise mmiotrace.HostRestorationError("mmiotrace could not be disabled")

    manifest = run_register_learning(
        "03:00.0",
        discover=lambda: discovery,
        journal_directory=tmp_path,
        passive_observer=observer,
        quiesce=lambda: quiesced.append(True),
    )
    assert manifest.status == "failed" and not quiesced
    assert any("could not be disabled" in b for b in learning_build_blockers(manifest))


def _compile_one(pairs, dropped=0, *, tmp_path):
    from pcileechfwgenerator.device_clone.register_policy import (
        compile_manifest_bar_models,
    )
    from pcileechfwgenerator.host_collect.register_learning import (
        LearningDiscovery,
        LearningResource,
        run_register_learning,
    )
    from pcileechfwgenerator.host_collect.register_observer import ObserverResult

    config = bytearray(256)
    config[:4] = bytes.fromhex("86803315")
    discovery = LearningDiscovery.from_config(
        bytes(config), resources=(LearningResource(index=0, size=0x4000),)
    )
    manifest = run_register_learning(
        "03:00.0",
        discover=lambda: discovery,
        journal_directory=tmp_path,
        passive_observer=lambda _d, _e: ObserverResult(
            observations=_native(pairs), lost_events=dropped
        ),
    )
    return compile_manifest_bar_models(manifest, [(0, 0x4000)], include_empty=True)[0]


def test_written_before_read_register_is_served_from_zero_and_labelled(tmp_path):
    result = _compile_one([("write", 0x1234), ("read", 0x1234)], tmp_path=tmp_path)
    reg = result.model.registers[0]
    assert reg.reset == 0 and reg.rw_mask == 0xFFFFFFFF
    assert reg.hints["evidence"]["initial_value_source"] == (
        "unobserved_written_before_read"
    )
    assert result.coverage.unobserved_reset_registers == 1


def test_partly_contradicted_or_incomplete_register_is_not_invented(tmp_path):
    # A self-clearing bit means not every bit is storage: value unknown.
    result = _compile_one([("write", 1), ("read", 0)], tmp_path=tmp_path / "a")
    assert 0 not in result.model.registers
    # A lossy trace grants nothing, so nothing is invented either.
    result = _compile_one([("write", 2)], 1, tmp_path=tmp_path / "b")
    assert 0 not in result.model.registers


def test_unrestored_exercise_is_a_non_blocking_gap(tmp_path):
    from pcileechfwgenerator.device_clone.register_policy import (
        learning_build_blockers,
    )
    from pcileechfwgenerator.host_collect.register_learning import (
        LearningDiscovery,
        LearningResource,
        run_register_learning,
    )

    config = bytearray(256)
    config[:4] = bytes.fromhex("86803315")
    discovery = LearningDiscovery.from_config(
        bytes(config), resources=(LearningResource(index=0, size=0x4000),)
    )

    def observer(_discovery, epoch):
        result = parse_mmiotrace(PROBE.splitlines(), [BAR0], state_epoch=epoch)
        result.host_changes.append("eno4 left up")
        return result

    manifest = run_register_learning(
        "03:00.0",
        discover=lambda: discovery,
        journal_directory=tmp_path,
        passive_observer=observer,
    )
    assert any(
        g.reason == "host_state_changed" and g.detail == "eno4 left up"
        for g in manifest.gaps
    )
    assert learning_build_blockers(manifest) == []


def test_write_only_register_is_not_served_from_zero(tmp_path):
    """Doorbells / set-clear registers are never read: not storage."""
    result = _compile_one([("write", 7), ("write", 9)], tmp_path=tmp_path)
    assert 0 not in result.model.registers
    assert result.coverage.unobserved_reset_registers == 0


def test_failed_restoring_reprobe_is_reported(tmp_path, host):
    root = _tracefs(tmp_path)
    calls = []

    def rebind(after):
        calls.append(1)
        if len(calls) == 2:
            raise RuntimeError("Refusing to rebind: device busy")
        after()

    result = _capture(tmp_path, root, rebind)
    assert len(calls) == 2
    # Refused: the driver stayed bound, with its one-CPU configuration.
    assert any("re-probe refused" in c for c in result.host_changes)
    assert not (tmp_path / "j" / "tracer" / mmiotrace.TRACER_JOURNAL).exists()


def test_restoring_reprobe_failure_blocks_and_marks_an_incident(tmp_path, host):
    root = _tracefs(tmp_path)
    calls = []

    def rebind(after):
        calls.append(1)
        if len(calls) == 2:
            raise RuntimeError("Driver rebind left it on no driver")
        after()

    with pytest.raises(mmiotrace.HostRestorationError, match="re-probe failed"):
        _capture(tmp_path, root, rebind)
    journal = tmp_path / "j" / "tracer" / mmiotrace.TRACER_JOURNAL
    assert "incident" in journal.read_text()
    # The next capture refuses until an operator checks the device.
    with pytest.raises(
        mmiotrace.HostRestorationError, match="did not finish restoring"
    ):
        _capture(tmp_path, root, lambda after: pytest.fail("ran"))


def test_restoring_reprobe_runs_even_when_no_mapping_was_traced(tmp_path, host):
    root = _tracefs(tmp_path)
    (root / "trace_pipe").write_text("")
    calls = []
    with pytest.raises(ObserverUnavailable, match="no donor BAR mapping"):
        _capture(tmp_path, root, lambda after: (calls.append(1), after()))
    assert len(calls) == 2


def test_cleanup_attempts_every_step(tmp_path, host, monkeypatch):
    """A failing CPU restore still restores tracing_on, and never raises."""
    root = _tracefs(tmp_path)
    (root / "tracing_on").write_text("1\n")

    def broken(_expected):
        raise OSError("cpu online unreadable")

    monkeypatch.setattr(mmiotrace, "_online_cpus", broken)
    problem = mmiotrace._stop_tracer(root, {0, 1}, "0")
    assert "CPU state could not be restored" in problem
    assert (root / "tracing_on").read_text() == "0"


def test_enabled_trace_events_are_not_clobbered(tmp_path, host):
    root = _tracefs(tmp_path)
    (root / "set_event").write_text("sched:sched_switch\n")
    with pytest.raises(ObserverUnavailable, match="trace events are enabled"):
        _capture(tmp_path, root, lambda after: pytest.fail("ran"))


def test_journal_from_a_previous_boot_is_discarded(tmp_path, host, monkeypatch):
    root = _tracefs(tmp_path)
    boot = tmp_path / "boot_id"
    boot.write_text("new-boot\n")
    monkeypatch.setattr(mmiotrace, "BOOT_ID", boot)
    journal_dir = tmp_path / "jd"
    journal_dir.mkdir()
    (journal_dir / mmiotrace.TRACER_JOURNAL).write_text(
        '{"cpus_online": [0, 1], "tracing_on": "0", "boot_id": "old-boot"}'
    )
    (root / "tracing_on").write_text("1\n")
    notes = mmiotrace.recover_stale_tracer(root, journal_dir)
    assert "previous boot" in notes[0]
    assert (root / "tracing_on").read_text().strip() == "1"  # untouched


def test_recovery_leaves_a_foreign_tracer_alone(tmp_path, host):
    root = _tracefs(tmp_path)
    (root / "current_tracer").write_text("function\n")
    (root / "tracing_on").write_text("1\n")
    journal_dir = tmp_path / "jd"
    journal_dir.mkdir()
    (journal_dir / mmiotrace.TRACER_JOURNAL).write_text(
        '{"cpus_online": [0, 1, 2, 3], "tracing_on": "0"}'
    )
    mmiotrace.recover_stale_tracer(root, journal_dir)
    assert (root / "tracing_on").read_text().strip() == "1"
    assert (root / "current_tracer").read_text().strip() == "function"


def test_exercise_reads_are_marked_and_not_initial_values():
    lines = [
        "MAP 0.1 1 0xf7e00000 0xffff 0x4000 0x0 0",
        "R 4 0.2 1 0xf7e00008 0x5 0x0 0",
        f"MARK 0.3 {mmiotrace.EXERCISE_MARK}",
        "R 4 0.4 1 0xf7e0000c 0x1 0x0 0",
    ]
    result = mmiotrace.parse_mmiotrace(lines, [BAR0])
    assert [o.phase for o in result.observations] == ["probe", "exercise"]
    from pcileechfwgenerator.device_clone.register_policy import _initial_in_epoch

    refs = [o.ref for o in result.observations]
    assert _initial_in_epoch(refs[0], result.observations, 0) == 5
    assert _initial_in_epoch(refs[1], result.observations, 0) is None


def test_msix_exclusion_fails_closed_on_an_unreadable_capability_chain():
    config = bytearray(256)
    config[0x06] = 0x10
    config[0x34] = 0x40
    config[0x40:0x42] = bytes([0x11, 0x40])  # MSI-X pointing at itself
    with pytest.raises(mmiotrace.ObserverUnavailable, match="MSI-X layout"):
        mmiotrace.msix_ranges(bytes(config))
    with pytest.raises(mmiotrace.ObserverUnavailable, match="shorter than 256"):
        mmiotrace.msix_ranges(bytes(64))


def test_msix_exclusion_matches_build_side_layout_parser():
    from pcileechfwgenerator.device_clone.msix_layout import read_msix_capability

    config = bytearray(256)
    config[0x06] = 0x10
    config[0x34] = 0x40
    config[0x40:0x42] = bytes([0x11, 0x00])
    config[0x42:0x44] = (64).to_bytes(2, "little")  # 65 entries
    config[0x44:0x48] = (0x2000 | 2).to_bytes(4, "little")
    config[0x48:0x4C] = (0x4000 | 2).to_bytes(4, "little")
    cap = read_msix_capability(bytes(config))
    assert mmiotrace.msix_ranges(bytes(config)) == [
        (2, 0x2000, 0x2000 + 16 * cap.table_size),
        (2, 0x4000, 0x4010),
    ]
