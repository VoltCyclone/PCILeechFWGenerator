"""Policy compilation and passive BAR-model regressions without hardware."""

import json

import pytest
from pcileechfwgenerator.device_clone.bar_model_loader import (
    BarModel,
    RegisterSpec,
    serialize_bar_model,
)
from pcileechfwgenerator.device_clone.bar_model_synthesizer import synthesize_model
from pcileechfwgenerator.device_clone.mmio_tracer import MmioAccess, MmioTrace
from pcileechfwgenerator.device_clone.register_evidence import (
    FieldClaim,
    RegisterClassification,
    RegisterRef,
)
from pcileechfwgenerator.device_clone.register_policy import (
    EvidenceRegisterSpec,
    compile_bar_model,
)


def classification(
    *claims, width=4, mask=None, initial=0x55, offset=0, bar=0, space="mem_bar", **kw
):
    return RegisterClassification(
        ref=RegisterRef(
            space=space,
            bar_index=bar,
            offset=offset,
            width=width,
            field_mask=(1 << (8 * width)) - 1 if mask is None else mask,
        ),
        claims=claims,
        observed_initial_value=initial,
        **kw,
    )


def claim(semantic="rw", mask=1, strength="measured", epoch=0):
    return FieldClaim(
        semantic=semantic,
        mask=mask,
        strength=strength,
        state_epoch=epoch,
        experiment_ids=("e1",) if strength == "measured" else (),
        observation_ids=(1, 2, 3) if strength == "measured" else (),
        note="fixture evidence",
    )


def test_only_measured_and_documented_plain_rw_grant_writes():
    c = classification(
        claim(mask=1),
        claim(mask=2, strength="documented", epoch=None),
        claim("ro", mask=4),
        claim("unknown", mask=8, strength="observed"),
    )
    result = compile_bar_model([c], 0, 4096)
    reg = result.model.registers[0]
    assert isinstance(result.model, BarModel) and isinstance(reg, RegisterSpec)
    assert reg.rw_mask == 3
    assert reg.reset == 0x55
    assert reg.hints["unknown_mask"] == 0xFFFFFFF8
    assert "maybe_rw1c" not in reg.hints
    assert result.coverage.measured_bits == 2
    assert result.coverage.documented_bits == 1
    assert result.coverage.observed_bits == 1
    assert result.coverage.untested_bits == 28
    assert result.coverage.planned_bits == 32
    assert result.coverage.writable_bits == 2
    assert result.unsupported == []
    assert reg.hints["evidence"]["classifications"][0]["claims"][0][
        "experiment_ids"
    ] == ["e1"]


@pytest.mark.parametrize(
    "semantic", ["w1c", "w1s", "read_clear", "self_clearing", "action", "volatile"]
)
def test_unsupported_semantics_reported_and_never_grant_writes(semantic):
    result = compile_bar_model([classification(claim(semantic, mask=3))], 0, 4096)
    assert result.model.registers[0].rw_mask == 0
    assert result.unsupported[0].semantic == semantic
    assert result.unsupported[0].mask == 3
    assert result.unsupported[0].experiment_ids == ("e1",)
    assert result.coverage.unsupported_bits == 2
    assert result.coverage.measured_bits == 2
    assert result.coverage.unknown_bits == 30
    assert result.model.registers[0].hints["unsupported"][0]["semantic"] == semantic


def test_epoch_conflicts_require_selection_not_a_union():
    c = classification(claim(mask=1, epoch=0), claim("ro", mask=1, epoch=1))
    result = compile_bar_model([c], 0, 4096)
    assert result.state_epoch is None
    assert result.model.registers[0].rw_mask == 0
    assert result.model.registers[0].hints["unknown_mask"] == 0xFFFFFFFF
    assert any("state selection" in u.reason for u in result.unsupported)
    assert (
        compile_bar_model([c], 0, 4096, state_epoch=0).model.registers[0].rw_mask == 1
    )
    assert (
        compile_bar_model([c], 0, 4096, state_epoch=1).model.registers[0].rw_mask == 0
    )
    assert (
        compile_bar_model([c], 0, 4096, state_epoch=99).model.registers[0].rw_mask == 0
    )


def test_epoch_selection_is_bar_wide_not_independent_per_register():
    result = compile_bar_model(
        [classification(claim(epoch=0)), classification(claim(epoch=1), offset=4)],
        0,
        4096,
    )
    assert all(reg.rw_mask == 0 for reg in result.model.registers.values())


def test_state_independent_documented_claim_is_usable_with_multiple_epochs():
    c = classification(
        claim(epoch=0),
        claim("ro", epoch=1),
        claim(mask=2, strength="documented", epoch=None),
    )
    assert compile_bar_model([c], 0, 4096).model.registers[0].rw_mask == 2


def test_absent_value_is_null_with_reason_and_serializes_as_null():
    # Omitted from the served model (no consumer may see a null reset) and
    # reported as an explicit gap instead.
    result = compile_bar_model([classification(initial=None)], 0, 4096)
    assert 0 not in result.model.registers
    serialized = json.loads(json.dumps(serialize_bar_model(result.model)))
    assert "0x0" not in serialized["regs"]
    assert any("No successful read" in u.reason for u in result.unsupported)


def test_explicit_reset_can_supply_initial_value_without_claiming_a_read():
    c = classification(
        initial=None,
        reset_value=0xA5,
        reset_domain="function-reset",
        reset_experiment_ids=("r1",),
    )
    result = compile_bar_model([c], 0, 4096)
    reg = result.model.registers[0]
    assert reg.reset == 0xA5
    assert reg.hints["evidence"]["initial_value_source"] == "explicit_reset_value"
    assert reg.hints["evidence"]["classifications"][0]["observed_initial_value"] is None
    assert (
        reg.hints["evidence"]["classifications"][0]["reset_domain"] == "function-reset"
    )


def test_observed_value_is_distinct_from_reset_in_provenance():
    c = classification(
        initial=0x55,
        reset_value=0xA5,
        reset_domain="function-reset",
        reset_experiment_ids=("r1",),
    )
    reg = compile_bar_model([c], 0, 4096).model.registers[0]
    assert reg.reset == 0x55
    evidence = reg.hints["evidence"]
    assert evidence["initial_value_source"] == "observed_initial_value"
    assert evidence["classifications"][0]["reset_value"] == 0xA5


def test_merge_disjoint_fields_and_keep_other_bits_unknown():
    a = classification(claim(mask=1), mask=1)
    b = classification(claim("ro", mask=2), mask=2)
    result = compile_bar_model([a, b], 0, 4096)
    assert result.model.registers[0].rw_mask == 1
    assert result.model.registers[0].hints["unknown_mask"] == 0xFFFFFFFC
    assert result.coverage.planned_bits == 2
    assert result.coverage.unknown_bits == 0  # denominator is supplied fields
    assert result.coverage.registers == 1


def test_conflicting_initial_values_are_not_arbitrarily_selected():
    result = compile_bar_model(
        [classification(mask=1, initial=0x55), classification(mask=2, initial=0x56)],
        0,
        4096,
    )
    assert 0 not in result.model.registers
    assert any("Conflicting observed" in u.reason for u in result.unsupported)


def test_config_and_other_bars_cannot_grant_target_bar_permissions():
    result = compile_bar_model(
        [
            classification(claim(), space="config", bar=None),
            classification(claim(), bar=1),
        ],
        0,
        4096,
    )
    assert not result.model.registers
    assert result.coverage.planned_bits == 0


@pytest.mark.parametrize("kw", [{"space": "io_bar"}, {"width": 8}])
def test_unrepresentable_accesses_reported_not_split_or_truncated(kw):
    result = compile_bar_model([classification(claim(), **kw)], 0, 4096)
    assert not result.model.registers
    assert result.coverage.writable_bits == 0
    assert any("does not support" in u.reason for u in result.unsupported)


@pytest.mark.parametrize(
    "items",
    [
        [classification(), classification()],
        [classification(offset=4096)],
    ],
)
def test_invalid_layout_rejected(items):
    with pytest.raises(ValueError):
        compile_bar_model(items, 0, 4096)


@pytest.mark.parametrize("other", [dict(width=2), dict(width=1, offset=1)])
def test_overlapping_access_shapes_are_omitted_and_reported(other):
    result = compile_bar_model(
        [classification(), classification(**other), classification(offset=8)],
        0,
        4096,
    )
    assert set(result.model.registers) == {8}
    assert sum("Overlapping access" in u.reason for u in result.unsupported) == 2


@pytest.mark.parametrize(
    "bar,size", [(6, 4096), (-1, 4096), (True, 4096), (0, 0), (0, -1)]
)
def test_invalid_bar_metadata_rejected(bar, size):
    with pytest.raises(ValueError):
        compile_bar_model([], bar, size)


def test_nullable_register_requires_absence_reason():
    with pytest.raises(ValueError, match="reason"):
        EvidenceRegisterSpec(offset=0, width=4, reset=None, rw_mask=0, hints={})


# No existing synthesizer tests covered these defects. Keep the three review
# examples here alongside the public compiler/adapter compatibility checks.
@pytest.mark.parametrize(
    "pairs,initial",
    [
        ([("R", 0x55)], 0x55),
        ([("W", 0)], None),
        ([("R", 0), ("W", 1), ("R", 0)], 0),
        ([("W", 0xA5)], None),
        ([("R", None), ("R", 0x55)], 0x55),
    ],
)
def test_passive_trace_review_regressions(pairs, initial):
    trace = MmioTrace(
        0,
        0,
        4096,
        [MmioAccess(i, op, 4, 0, value) for i, (op, value) in enumerate(pairs)],
    )
    model = synthesize_model(trace)
    if initial is None:
        # No observed value: omitted rather than served with a null reset.
        assert 0 not in model.registers
        return
    reg = model.registers[0]
    assert reg.rw_mask == 0
    assert reg.reset == initial
    assert reg.hints["unknown_mask"] == 0xFFFFFFFF
    assert not reg.hints.get("maybe_rw1c", False)
    evidence = reg.hints["evidence"]["classifications"][0]
    assert evidence["reset_value"] is None
    assert evidence["observed_initial_value"] == initial
    assert all(
        c["semantic"] == "unknown" and c["strength"] == "observed"
        for c in evidence["claims"]
    )


def test_empty_trace_has_no_writable_model():
    result = synthesize_model(MmioTrace(0, 0, 4096))
    assert result.size == 4096
    assert result.registers == {}


@pytest.mark.parametrize(
    "access",
    [
        MmioAccess(0, "?", 4, 0, 0),
        MmioAccess(0, "R", 4, 1, 0),
        MmioAccess(0, "R", 4, 4096, 0),
        MmioAccess(0, "R", 1, 0, 0x100),
    ],
)
def test_invalid_legacy_trace_is_not_silently_clamped(access):
    with pytest.raises(ValueError):
        synthesize_model(MmioTrace(0, 0, 4096, [access]))


def test_mixed_trace_widths_omit_register_instead_of_guessing_majority_width():
    model = synthesize_model(
        MmioTrace(
            0,
            0,
            4096,
            [
                MmioAccess(0, "R", 4, 0, 0x11223344),
                MmioAccess(1, "R", 1, 0, 0x44),
                MmioAccess(2, "R", 4, 8, 0x55),
            ],
        )
    )
    # Overlapping shapes at 0x0 are omitted; the rest of the BAR still compiles.
    assert set(model.registers) == {8}
    assert model.registers[8].reset == 0x55 and model.registers[8].rw_mask == 0


def test_register_without_initial_value_is_omitted_not_null():
    model = synthesize_model(MmioTrace(0, 0, 4096, [MmioAccess(0, "W", 4, 0x10, 1)]))
    assert 0x10 not in model.registers


def test_passive_trace_render_removes_writes_and_guessed_interrupt_roles():
    from pcileechfwgenerator.templating.template_renderer import TemplateRenderer

    # Both offsets used to trigger guessed command/interrupt roles when promoted
    # to writable by the old synthesizer.
    model = synthesize_model(
        MmioTrace(
            0,
            0,
            4096,
            [
                MmioAccess(0, "R", 4, 0x14, 0x55),
                MmioAccess(1, "W", 4, 0x14, 1),
                MmioAccess(2, "R", 4, 0xD0, 0),
                MmioAccess(3, "W", 4, 0xD0, 1),
            ],
        )
    )
    rendered = TemplateRenderer().render_template(
        "sv/pcileech_bar_impl_device.sv.j2",
        {
            "header": "// offline fixture",
            "device_signature": "fixture",
            "bar_model": model,
            "interrupt_config": {"strategy": "msi"},
            "vendor_id": "0x8086",
            "device_id": "0x1521",
        },
    )
    assert "reg_0x0014 <= 32'h00000055" in rendered
    assert "if (wr_be[" not in rendered
    assert "Potential command/doorbell register" not in rendered
    assert "int_mask_shadow <= wr_data" not in rendered
    assert "int_status_shadow <= int_status_shadow & ~wr_data" not in rendered
