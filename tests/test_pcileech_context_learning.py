"""Stage 2 consumes artifacts without live I/O or BDF-only evidence caches."""

import builtins
import logging
from pathlib import Path
from types import SimpleNamespace

import pytest
from pcileechfwgenerator.device_clone.pcileech_context import PCILeechContextBuilder
from pcileechfwgenerator.host_collect.register_learning import LearningDiscovery
from pcileechfwgenerator.host_collect.snapshot import SnapshotError, write_snapshot

from tests.test_register_learning import session


@pytest.fixture
def config():
    data = bytearray(256)
    data[:4] = bytes.fromhex("de10d71a")
    return bytes(data)


@pytest.fixture
def forbid_hardware(monkeypatch):
    import subprocess

    import pcileechfwgenerator.device_clone.bar_model_loader as loader
    import pcileechfwgenerator.device_clone.mmio_tracer as tracer

    def forbidden(*args, **kwargs):
        pytest.fail("Stage 2 attempted hardware access, tracing, or a BDF cache")

    monkeypatch.setattr(tracer, "MmioTracer", forbidden)
    monkeypatch.setattr(subprocess, "run", forbidden)
    monkeypatch.setattr(subprocess, "Popen", forbidden)
    monkeypatch.setattr(loader, "load_bar_model", forbidden)
    real_open = builtins.open

    def safe_open(file, *args, **kwargs):
        if isinstance(file, (str, Path)) and str(file).startswith(("/sys/", "/dev/")):
            forbidden()
        return real_open(file, *args, **kwargs)

    monkeypatch.setattr(builtins, "open", safe_open)
    for method in (
        "open",
        "exists",
        "is_symlink",
        "resolve",
        "stat",
        "read_bytes",
        "read_text",
    ):
        original = getattr(Path, method)

        def checked(path, *args, _method=original, **kwargs):
            if str(path).startswith(("/sys/", "/dev/")):
                forbidden()
            return _method(path, *args, **kwargs)

        monkeypatch.setattr(Path, method, checked)
    monkeypatch.delenv("DEVICE_CONTEXT_PATH", raising=False)
    monkeypatch.delenv("MSIX_DATA_PATH", raising=False)


def consume(**kwargs):
    builder = object.__new__(PCILeechContextBuilder)
    return builder._capture_or_load_bar_models(
        "03:00.0", [SimpleNamespace(index=0, size=4096, is_memory=True)], **kwargs
    )


def publish(tmp_path, monkeypatch, config, *, learning=None, context=None):
    snapshot = write_snapshot(
        tmp_path,
        "03:00.0",
        config,
        {},
        context,
        learning=learning,
        capture_id=learning.context.capture_id if learning is not None else None,
    )
    monkeypatch.setenv("DEVICE_CONTEXT_PATH", str(tmp_path / "device_context.json"))
    return snapshot


def test_no_snapshot_never_attempts_live_collection(forbid_hardware):
    assert consume(force_recapture=True) == {}


@pytest.mark.parametrize("enabled", [True, False])
def test_manifest_is_compiled_without_hardware(
    tmp_path, monkeypatch, config, forbid_hardware, enabled
):

    service, _, _ = session(
        tmp_path / "journals", LearningDiscovery.from_config(config)
    )
    manifest = service.run()
    publish(tmp_path, monkeypatch, config, learning=manifest)
    models = consume(enable_mmio_learning=enabled, force_recapture=True)
    assert models[0].registers[0].rw_mask == 1
    assert models[0].registers[0].hints["evidence"]["classifications"]
    assert not (tmp_path / ".pcileech_cache").exists()


def test_legacy_capture_without_learning_is_absent(
    tmp_path, monkeypatch, config, forbid_hardware
):
    snapshot = publish(tmp_path, monkeypatch, config)
    assert snapshot.learning_status == "absent"
    assert consume() == {}


def test_legacy_serialized_context_model_without_evidence_is_ignored(
    tmp_path, monkeypatch, config, forbid_hardware
):
    from pcileechfwgenerator.device_clone.bar_model_loader import (
        BarModel,
        RegisterSpec,
        serialize_bar_model,
    )

    model = BarModel(size=4096, registers={0: RegisterSpec(0, 4, 7, 0, {})})
    snapshot = publish(
        tmp_path,
        monkeypatch,
        config,
        context={"bar_models": {"0": serialize_bar_model(model)}},
    )
    assert snapshot.learning is None
    # Old synthesizer output carries guessed masks; it is never served.
    assert consume() == {}


def test_learning_takes_precedence_over_serialized_models(
    tmp_path, monkeypatch, config, forbid_hardware
):
    from pcileechfwgenerator.host_collect.register_learning import run_register_learning

    manifest = run_register_learning(
        "03:00.0",
        discover=lambda: LearningDiscovery.from_config(config),
        journal_directory=tmp_path / "journals",
    )
    publish(
        tmp_path,
        monkeypatch,
        config,
        learning=manifest,
        context={"bar_models": {"0": {"invalid": "stale"}}},
    )
    assert consume() == {}


def test_corrupt_learning_does_not_fall_back_to_live_capture(
    tmp_path, monkeypatch, config, forbid_hardware
):
    service, _, _ = session(
        tmp_path / "journals", LearningDiscovery.from_config(config)
    )
    publish(tmp_path, monkeypatch, config, learning=service.run())
    (tmp_path / "register_learning.json").write_text("{}")
    with pytest.raises(SnapshotError):
        consume()


def test_consumes_already_loaded_snapshot_without_environment(
    tmp_path, monkeypatch, config, forbid_hardware
):

    service, _, _ = session(
        tmp_path / "journals", LearningDiscovery.from_config(config)
    )
    learning = service.run()
    snapshot = write_snapshot(
        tmp_path,
        "03:00.0",
        config,
        {},
        learning=learning,
        capture_id=learning.context.capture_id,
    )
    builder = object.__new__(PCILeechContextBuilder)
    builder.offline_capture = snapshot
    result = builder._capture_or_load_bar_models(
        "03:00.0",
        [SimpleNamespace(index=0, size=4096, is_memory=True)],
        enable_mmio_learning=False,
    )
    assert result[0].registers[0].rw_mask == 1


def test_bar_config_always_consumes_artifacts_even_with_flag_disabled(monkeypatch):
    builder = object.__new__(PCILeechContextBuilder)
    builder.offline_capture = object()
    builder.device_bdf = "03:00.0"
    bar = SimpleNamespace(index=0, size=4096, size_mb=4096 / (1024 * 1024))
    builder.logger = logging.getLogger(__name__)
    monkeypatch.setattr(builder, "_analyze_bars", lambda _: [bar])
    monkeypatch.setattr(builder, "_select_primary_bar", lambda _: bar)
    monkeypatch.setattr(builder, "_build_bar_config_dict", lambda *_: {})

    def consume_artifacts(**kwargs):
        assert kwargs["enable_mmio_learning"] is False
        raise SnapshotError("invalid evidence must propagate")

    monkeypatch.setattr(builder, "_capture_or_load_bar_models", consume_artifacts)
    with pytest.raises(SnapshotError, match="invalid evidence"):
        builder._build_bar_config(
            {"bars": [], "device_signature": "fixture"},
            None,
            enable_mmio_learning=False,
        )


def test_loaded_snapshot_bdf_mismatch_is_rejected(tmp_path, config, forbid_hardware):
    builder = object.__new__(PCILeechContextBuilder)
    builder.offline_capture = write_snapshot(tmp_path, "04:00.0", config, {})
    with pytest.raises(SnapshotError, match="another donor BDF"):
        builder._capture_or_load_bar_models("03:00.0", [])


def test_serialized_model_without_provenance_is_ignored_even_if_null(
    tmp_path, monkeypatch, config, forbid_hardware
):
    context = {
        "bar_models": {
            "0": {
                "size": 4096,
                "regs": {
                    "0x0": {
                        "width": 4,
                        "reset": None,
                        "rw_mask": 0,
                        "hints": {"initial_value_absent_reason": "no successful reads"},
                    }
                },
            }
        }
    }
    publish(tmp_path, monkeypatch, config, context=context)
    assert consume() == {}


def test_serialized_model_with_provenance_hints_is_not_served(
    tmp_path, monkeypatch, config, forbid_hardware
):
    """provenance-looking hints are not evidence without a manifest."""
    context = {
        "bar_models": {
            "0": {
                "size": 4096,
                "regs": {
                    "0x0": {
                        "width": 4,
                        "reset": 0,
                        "rw_mask": 0xFFFFFFFF,
                        "hints": {
                            "evidence": {
                                "classifications": [{"not_a_classification": True}]
                            }
                        },
                    }
                },
            }
        }
    }
    snapshot = publish(tmp_path, monkeypatch, config, context=context)
    assert snapshot.learning is None
    assert consume() == {}


def test_native_driver_read_never_becomes_served_initial_value(
    tmp_path, monkeypatch, config, forbid_hardware
):
    """a register seen only under the native driver (epoch 0) is not
    served with that value in the post-bind state."""
    from pcileechfwgenerator.device_clone.register_evidence import (
        Observation,
        RegisterRef,
    )
    from pcileechfwgenerator.host_collect.register_observer import ObserverResult

    passive_ref = RegisterRef(
        space="mem_bar", bar_index=0, offset=4, width=4, field_mask=0xFFFFFFFF
    )

    def observer(*_):
        return ObserverResult(
            observations=[
                Observation(
                    sequence_id=0,
                    monotonic_ns=0,
                    operation="read",
                    ref=passive_ref,
                    returned_value=0xA5,
                    completion="ok",
                    source="native_driver",
                    state_epoch=0,
                )
            ]
        )

    service, _, _ = session(
        tmp_path / "journals",
        LearningDiscovery.from_config(config),
        passive_observer=observer,
    )
    publish(tmp_path, monkeypatch, config, learning=service.run())
    models = consume()
    assert 4 not in models[0].registers  # no epoch-1 read: omitted, not 0xA5
    assert models[0].registers[0].rw_mask == 1  # the measured register survives


def test_invented_measured_claim_cannot_be_published(tmp_path, monkeypatch, config):
    """At the public writer: no evidence, BAR claimed fully RW."""
    from uuid import uuid4

    from pcileechfwgenerator.device_clone.register_evidence import (
        ExperimentResult,
        FieldClaim,
        LearningContext,
        LearningManifest,
        RegisterClassification,
        RegisterRef,
    )
    from pydantic import ValidationError

    exp = ExperimentResult(
        experiment_id="unrelated",
        recipe_id="unknown-recipe",
        recipe_version="obsolete",
        ref=RegisterRef(space="config", offset=4, width=2, field_mask=1),
        state_epoch=1,
        requested_mask=1,
        tested_mask=0,
        time_budget_ns=100,
        termination="completed",
        restoration="verified",
    )
    bar = RegisterRef(
        space="mem_bar", bar_index=0, offset=0, width=4, field_mask=0xFFFFFFFF
    )
    claim = FieldClaim(
        mask=0xFFFFFFFF,
        semantic="rw",
        strength="measured",
        state_epoch=1,
        experiment_ids=("unrelated",),
    )
    with pytest.raises(ValidationError):
        LearningManifest(
            context=LearningContext(
                capture_id=uuid4(),
                donor=LearningDiscovery.from_config(config).donor,
                bdf="0000:03:00.0",
                backend_version="unknown",
                experiment_version="obsolete",
            ),
            status="complete",
            experiments=(exp,),
            classifications=(
                RegisterClassification(
                    ref=bar, observed_initial_value=0, claims=(claim,)
                ),
            ),
        )


def test_stored_template_context_models_are_rederived(tmp_path, monkeypatch, config):
    """A stored template_context cannot smuggle BAR models past the manifest."""
    from types import SimpleNamespace as NS

    from pcileechfwgenerator.build import _rederive_bar_models

    forged = {
        "size": 4096,
        "regs": {"0x0": {"width": 4, "reset": 0, "rw_mask": 0xFFFFFFFF}},
    }
    stored = {
        "bar_models": {"0": forged},
        "bar_config": {
            "bar_models": {"0": forged},
            "bars": [{"index": 0, "size": 4096, "is_memory": True}],
        },
    }
    stripped = _rederive_bar_models(stored, NS(learning=None))
    assert "bar_models" not in stripped and "bar_models" not in stripped["bar_config"]
    assert stored["bar_config"]["bar_models"]  # caller's copy untouched

    service, _, _ = session(
        tmp_path / "journals", LearningDiscovery.from_config(config)
    )
    rederived = _rederive_bar_models(stored, NS(learning=service.run()))
    regs = rederived["bar_config"]["bar_models"][0]["regs"]
    assert regs["0x0"]["rw_mask"] == 1  # from evidence, not the forged 0xFFFFFFFF


def test_unrelated_experiment_state_does_not_hide_measured_bar_bits(
    tmp_path, monkeypatch, config
):
    from pcileechfwgenerator.device_clone.register_evidence import (
        ExperimentResult,
        LearningManifest,
        RegisterRef,
    )
    from pcileechfwgenerator.device_clone.register_policy import (
        compile_manifest_bar_models,
    )

    service, _, _ = session(
        tmp_path / "journals", LearningDiscovery.from_config(config)
    )
    manifest = service.run()
    other = ExperimentResult(
        experiment_id="config-elsewhere",
        recipe_id="unqualified",
        recipe_version="1",
        ref=RegisterRef(space="config", offset=0x3C, width=1, field_mask=0xFF),
        state_epoch=7,
        requested_mask=1,
        time_budget_ns=1,
        termination="completed",
        restoration="verified",
    )
    import json

    data = json.loads(manifest.model_dump_json())
    # A later-state observation elsewhere used to win the max(epoch) fallback
    # and silently drop BAR0's measured bits.
    seq = max(o.sequence_id for o in manifest.observations) + 1
    data["observations"].append(
        {
            "sequence_id": seq,
            "monotonic_ns": 0,
            "operation": "read",
            "ref": json.loads(other.ref.model_dump_json()),
            "returned_value": 0,
            "completion": "ok",
            "source": "collector",
            "state_epoch": 7,
        }
    )
    other_data = json.loads(other.model_dump_json())
    other_data["observation_ids"] = [seq]
    data["experiments"].append(other_data)
    data["status"] = "partial"
    widened = LearningManifest.model_validate_json(json.dumps(data))
    results = compile_manifest_bar_models(widened, [(0, 4096)])
    assert results[0].model.registers[0].rw_mask == 1


# --- Failed learning blocks the build; omissions are reported --------


def _failed_release_manifest(tmp_path, config):
    from contextlib import contextmanager

    @contextmanager
    def owner():
        yield
        raise OSError("restore driver failed")

    service, _, _ = session(
        tmp_path / "journals", LearningDiscovery.from_config(config), ownership=owner
    )
    manifest = service.run()
    assert manifest.status == "failed"
    return manifest


def test_failed_restoration_blocks_stage2_build(
    tmp_path, monkeypatch, config, forbid_hardware
):
    """The review's reproducer: experiments succeeded, the driver rebind did not.
    Writable models must not reach a build from that capture."""
    from pcileechfwgenerator.device_clone.register_policy import LearningBuildBlocked

    publish(
        tmp_path,
        monkeypatch,
        config,
        learning=_failed_release_manifest(tmp_path, config),
    )
    with pytest.raises(LearningBuildBlocked, match="restore driver failed"):
        consume()


def test_failed_capture_stays_inspectable(tmp_path, monkeypatch, config):
    """Diagnostic compilation still works, so the capture can be examined."""
    from pcileechfwgenerator.device_clone.register_policy import (
        compile_manifest_bar_models,
    )

    manifest = _failed_release_manifest(tmp_path, config)
    results = compile_manifest_bar_models(manifest, [(0, 4096)], diagnostic=True)
    assert results[0].model.registers[0].rw_mask == 1


def test_stored_template_context_from_failed_capture_is_blocked(tmp_path, config):
    from types import SimpleNamespace as NS

    from pcileechfwgenerator.build import _rederive_bar_models
    from pcileechfwgenerator.device_clone.register_policy import LearningBuildBlocked

    manifest = _failed_release_manifest(tmp_path, config)
    # No bar_config at all: the gate must not depend on the context's shape.
    with pytest.raises(LearningBuildBlocked):
        _rederive_bar_models({"device_config": {}}, NS(learning=manifest))


def test_failed_experiment_restoration_blocks_even_if_status_partial(tmp_path, config):
    """The gate reads the evidence, not only the derived status."""
    from pcileechfwgenerator.device_clone.register_policy import (
        learning_build_blockers,
    )

    service, _, _ = session(
        tmp_path / "journals", LearningDiscovery.from_config(config)
    )
    manifest = service.run()
    assert learning_build_blockers(manifest) == []
    failed = manifest.experiments[0].model_copy(
        update={"restoration": "failed", "restoration_detail": "readback 0x1"}
    )
    tampered = manifest.model_copy(
        update={"status": "partial", "experiments": (failed,)}
    )
    assert any("readback 0x1" in r for r in learning_build_blockers(tampered))


def test_served_bar_fidelity_is_explicit(caplog):
    builder = PCILeechContextBuilder.__new__(PCILeechContextBuilder)
    builder.logger = logging.getLogger("fidelity")
    readonly = {"served_bar_index": 0, "aperture_size": 65536}
    with caplog.at_level(logging.WARNING, logger="fidelity"):
        builder._record_served_bar_fidelity(readonly)
    assert readonly["served_bar_fidelity"] == "readonly_zero"
    assert "read-only" in caplog.text

    generic = {
        "served_bar_index": 0,
        "aperture_size": 65536,
        "bar_fallback": "generic_ram",
    }
    with caplog.at_level(logging.WARNING, logger="fidelity"):
        builder._record_served_bar_fidelity(generic)
    assert generic["served_bar_fidelity"] == "generic_ram"
    assert "repeats every 4 KiB" in caplog.text

    learned = {
        "served_bar_index": 2,
        "aperture_size": 4096,
        "bar_models": {2: {"size": 4096, "regs": {"0x0": {"width": 4}}}},
    }
    builder._record_served_bar_fidelity(learned)
    assert learned["served_bar_fidelity"] == "learned"

    other_bar = {"served_bar_index": 0, "bar_models": {2: learned["bar_models"][2]}}
    builder._record_served_bar_fidelity(other_bar)
    assert other_bar["served_bar_fidelity"] == "readonly_zero"


def test_compilation_report_lists_omitted_behaviors(tmp_path, monkeypatch, config):
    from pcileechfwgenerator.device_clone.register_policy import (
        compilation_report,
        compile_manifest_bar_models,
    )

    service, _, _ = session(
        tmp_path / "journals", LearningDiscovery.from_config(config)
    )
    report = compilation_report(compile_manifest_bar_models(service.run(), [(0, 4096)]))
    entry = report["0"]
    assert entry["state_epoch"] == 1
    assert entry["coverage"]["measured_bits"] >= 1
    assert isinstance(entry["unsupported"], list)
    import json

    json.dumps(report)  # must be serializable into build output


# --- Learning failures -----------------------------------------------------


def _blockers_for(manifest, **update):
    from pcileechfwgenerator.device_clone.register_policy import (
        learning_build_blockers,
    )

    return learning_build_blockers(manifest.model_copy(update=update))


def test_failures_before_any_write_do_not_block(tmp_path, config):
    from pcileechfwgenerator.device_clone.register_evidence import (
        CoverageGap,
        RegisterRef,
    )

    service, _, _ = session(
        tmp_path / "journals", LearningDiscovery.from_config(config)
    )
    manifest = service.run()
    ref = RegisterRef(space="config", offset=0, width=4, field_mask=0xFFFFFFFF)
    for detail, reason in [
        ("observer failed: no bpftrace", "backend_unavailable"),
        ("quiesce failed: busy", "precondition_failed"),
        ("session cancelled", "precondition_failed"),
    ]:
        gap = CoverageGap(ref=ref, reason=reason, detail=detail)
        assert _blockers_for(manifest, status="failed", gaps=(gap,)) == []
    # Timed out but restored: a coverage gap, not a donor-integrity problem.
    timed_out = manifest.experiments[0].model_copy(update={"termination": "timeout"})
    assert _blockers_for(manifest, experiments=(timed_out,)) == []


@pytest.mark.parametrize("restoration", ["not_attempted", "not_restorable"])
def test_unverified_restoration_after_writes_blocks(tmp_path, config, restoration):
    service, _, _ = session(
        tmp_path / "journals", LearningDiscovery.from_config(config)
    )
    manifest = service.run()
    exp = manifest.experiments[0].model_copy(update={"restoration": restoration})
    assert any(restoration in r for r in _blockers_for(manifest, experiments=(exp,)))


def test_unrecovered_journal_blocks(tmp_path, config):
    from pcileechfwgenerator.device_clone.register_evidence import (
        CoverageGap,
        RegisterRef,
    )

    service, _, _ = session(
        tmp_path / "journals", LearningDiscovery.from_config(config)
    )
    manifest = service.run()
    gap = CoverageGap(
        ref=RegisterRef(space="config", offset=0, width=4, field_mask=0xFFFFFFFF),
        reason="precondition_failed",
        detail="unfinished journal; no replay: x.json: crashed",
    )
    assert _blockers_for(manifest, gaps=(gap,))


def test_donor_template_cannot_supply_bar_models():
    builder = PCILeechContextBuilder.__new__(PCILeechContextBuilder)
    builder.logger = logging.getLogger("merge")
    forged = {"size": 4096, "regs": {"0x0": {"width": 4, "rw_mask": 0xFFFFFFFF}}}
    donor = {
        "bar_models": {0: forged},
        "bar_config": {
            "bar_models": {0: forged},
            "served_bar_fidelity": "learned",
            "bar_model_report": {"0": {}},
            "bar_fallback": "generic_ram",
            "note": "kept",
        },
    }
    live = {"bar_config": {"served_bar_index": 0, "served_bar_fidelity": "generic_ram"}}
    merged = builder._merge_donor_template(live, donor)
    bar = dict(merged["bar_config"])
    assert "bar_models" not in merged and "bar_models" not in bar
    assert bar["served_bar_fidelity"] == "generic_ram"
    assert "bar_model_report" not in bar and bar["note"] == "kept"
    assert "bar_fallback" not in bar  # only the build flag may opt out


def test_bar_with_only_unsupported_registers_is_reported(tmp_path, monkeypatch, config):
    """A BAR that compiles to zero registers still surfaces what was dropped."""
    from pcileechfwgenerator.device_clone.register_evidence import (
        Observation,
        RegisterRef,
    )
    from pcileechfwgenerator.device_clone.register_policy import (
        compilation_report,
        compile_manifest_bar_models,
        servable_models,
    )
    from pcileechfwgenerator.host_collect.register_observer import ObserverResult

    def read(seq, width):
        return Observation(
            sequence_id=seq,
            monotonic_ns=seq,
            operation="read",
            ref=RegisterRef(
                space="mem_bar",
                bar_index=2,
                offset=0,
                width=width,
                field_mask=(1 << (8 * width)) - 1,
            ),
            returned_value=0,
            completion="ok",
            source="native_driver",
            state_epoch=0,
        )

    service, _, _ = session(
        tmp_path / "journals",
        LearningDiscovery.from_config(config),
        passive_observer=lambda *_: ObserverResult(
            observations=[read(0, 4), read(1, 2)]
        ),
    )
    results = compile_manifest_bar_models(
        service.run(), [(2, 4096)], include_empty=True
    )
    assert 2 not in servable_models(results)
    assert compilation_report(results)["2"]["unsupported"]


def test_stored_context_fidelity_is_recomputed(tmp_path, config):
    from types import SimpleNamespace as NS

    from pcileechfwgenerator.build import _rederive_bar_models

    stored = {
        "bar_config": {
            "served_bar_index": 0,
            "served_bar_fidelity": "learned",
            "bar_model_report": {"0": {"stale": True}},
            "bars": [{"index": 0, "size": 4096, "is_memory": True}],
        }
    }
    stored["bar_config"]["bar_fallback"] = "generic_ram"  # stale, not a flag
    out = _rederive_bar_models(stored, NS(learning=None))["bar_config"]
    assert out["served_bar_fidelity"] == "readonly_zero"
    assert out["bar_model_report"] == {}
    opted = _rederive_bar_models(stored, NS(learning=None), writable_bar_fallback=True)[
        "bar_config"
    ]
    assert opted["served_bar_fidelity"] == "generic_ram"


def test_partial_learned_model_is_labelled_partial():
    from pcileechfwgenerator.device_clone.register_policy import served_bar_fidelity

    config = {
        "served_bar_index": 0,
        "bar_models": {0: {"regs": {"0x0": {}}}},
        "bar_model_report": {"0": {"unsupported": [{"reason": "w1c"}]}},
    }
    assert served_bar_fidelity(config) == "learned_partial"


def test_live_build_bar_config_does_no_bar_mmio(monkeypatch, config, forbid_hardware):
    """even without an offline capture, Stage 2 never samples BARs or
    touches the device's power state."""
    import builtins as _builtins

    from pcileechfwgenerator.device_clone import pcileech_context as module

    assert not hasattr(PCILeechContextBuilder, "_sample_bar_data_direct")
    assert not hasattr(PCILeechContextBuilder, "_check_and_fix_power_state")
    real_open = _builtins.open

    def guarded(path, *args, **kwargs):
        if str(path).startswith("/sys/"):
            raise AssertionError(f"Stage 2 touched sysfs: {path}")
        return real_open(path, *args, **kwargs)

    monkeypatch.setattr(_builtins, "open", guarded)
    builder = PCILeechContextBuilder.__new__(PCILeechContextBuilder)
    builder.logger = logging.getLogger("f07")
    builder.device_bdf = "0000:03:00.0"
    builder.config = SimpleNamespace(writable_bar_fallback=False)
    bar = module.BarConfiguration(
        index=0,
        base_address=0xF7E00000,
        size=4096,
        bar_type=0,
        prefetchable=False,
        is_memory=True,
        is_io=False,
    )
    monkeypatch.setattr(builder, "_analyze_bars", lambda bars: [bar])
    monkeypatch.setattr(builder, "_capture_or_load_bar_models", lambda **kw: {})
    result = builder._build_bar_config(
        {"bars": [], "device_signature": "fixture", "device_bdf": "0000:03:00.0"},
        None,
    )
    assert "bar_samples" not in result
    assert result["served_bar_fidelity"] == "readonly_zero"
