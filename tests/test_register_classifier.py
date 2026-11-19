"""Hardware-free classification tests: attempted writes never grant permission."""

import pytest
from pcileechfwgenerator.device_clone.register_classifier import (
    RecipeDeclaration,
    classify_register,
    classify_registers,
)
from pcileechfwgenerator.device_clone.register_evidence import (
    ExperimentResult,
    Observation,
    RegisterRef,
)
from pydantic import ValidationError


def ref(width=1, mask=None, **kw):
    return RegisterRef(
        space="mem_bar",
        bar_index=0,
        offset=0,
        width=width,
        field_mask=(1 << (width * 8)) - 1 if mask is None else mask,
        **kw,
    )


def events(pairs, *, epoch=0, start=0, register=None, **kw):
    register = register or ref()
    result = []
    for i, (op, value) in enumerate(pairs, start):
        fields = dict(
            sequence_id=i,
            monotonic_ns=i * 10,
            operation=op,
            ref=register,
            source="collector",
            mediation="physical",
            state_epoch=epoch,
            completion="ok",
        )
        fields["returned_value" if op == "read" else "attempted_value"] = value
        if value is None:
            fields["absent_reason"] = "Missing trace value"
        fields.update(kw)
        result.append(Observation(**fields))
    return result


def experiment(obs, *, name="e1", kind="storage", mask=1, controls=(), **kw):
    fields = dict(
        experiment_id=name,
        recipe_id="test-" + kind,
        recipe_version="1",
        ref=obs[0].ref,
        state_epoch=obs[0].state_epoch,
        requested_mask=mask,
        tested_mask=mask,
        observation_ids=tuple(o.sequence_id for o in obs),
        time_budget_ns=10000,
        termination="completed",
        restoration="verified",
    )
    fields.update(kw)
    exp = ExperimentResult(**fields)
    dec = RecipeDeclaration(
        experiment_id=name,
        recipe_id="test-" + kind,
        recipe_version="1",
        kind=kind,
        trace_complete=True,
        control_read_ids=controls,
    )
    return exp, dec


def classify(obs, *, kind="storage", mask=1, controls=(), **kw):
    exp, dec = experiment(obs, kind=kind, mask=mask, controls=controls, **kw)
    return classify_register(obs[0].ref, obs, [exp], [dec])


def rw_events(**kw):
    return events(
        [("read", 0), ("write", 1), ("read", 1), ("write", 0), ("read", 0)], **kw
    )


def test_reads_only_remain_observed_and_keep_first_successful_value():
    obs = events(
        [("read", None), ("read", 0x55), ("read", 0x56)], source="native_driver"
    )
    result = classify_register(ref(), reversed(obs))
    assert result.observed_initial_value == 0x55
    assert result.reset_value is None
    assert result.claims[0].semantic == "unknown"
    assert result.claims[0].strength == "observed"
    assert "possible volatility" in result.claims[0].note


def test_write_only_has_no_initial_or_reset_value():
    result = classify_register(ref(), events([("write", 0)]))
    assert result.observed_initial_value is result.reset_value is None
    assert result.claims[0].strength == "observed"
    assert "acceptance unknown" in result.claims[0].note


@pytest.mark.parametrize("active", [False, True])
def test_ignored_one_is_not_w1c(active):
    obs = events([("read", 0), ("write", 1), ("read", 0)])
    result = classify(obs) if active else classify_register(ref(), obs)
    assert all(c.semantic == "unknown" for c in result.claims)
    assert result.mask_for("w1c", state_epoch=0) == 0


def test_rw_only_tested_bits_and_epoch():
    obs = rw_events(epoch=7)
    result = classify(obs)
    assert result.mask_for("rw", state_epoch=7) == 1
    assert result.mask_for("rw", state_epoch=0) == result.mask_for("rw") == 0
    assert result.unknown_mask(state_epoch=7) == 0xFE
    measured = [c for c in result.claims if c.strength == "measured"]
    assert measured[0].experiment_ids == ("e1",)
    assert measured[0].observation_ids == tuple(range(5))


@pytest.mark.parametrize("constant", [0, 1])
def test_ro_needs_zero_and_one_requests(constant):
    obs = events(
        [
            ("read", constant),
            ("write", 0),
            ("read", constant),
            ("write", 1),
            ("read", constant),
        ]
    )
    result = classify(obs)
    assert result.mask_for("ro", state_epoch=0) == 1
    assert result.unknown_mask(state_epoch=0) == 0xFE


@pytest.mark.parametrize(
    "pairs",
    [
        [("read", 0), ("write", 1), ("read", 1)],
        [("read", 0), ("write", 0), ("read", 0)],
        [("read", 0), ("write", 1), ("read", 0)],
    ],
)
def test_partial_storage_evidence_is_ambiguous(pairs):
    result = classify(events(pairs))
    assert result.claims[0].semantic == "unknown"
    assert result.claims[0].strength == "ambiguous"


def test_contradiction_within_an_experiment_is_ambiguous():
    obs = rw_events() + events([("write", 1), ("read", 0)], start=5)
    assert classify(obs).mask_for("rw", state_epoch=0) == 0
    assert classify(obs).claims[0].strength == "ambiguous"


def test_contradictory_experiments_same_epoch_never_union():
    a = rw_events()
    b = events(
        [("read", 0), ("write", 0), ("read", 0), ("write", 1), ("read", 0)], start=5
    )
    ea, da = experiment(a)
    eb, db = experiment(b, name="e2")
    result = classify_register(ref(), a + b, [ea, eb], [da, db])
    assert (
        result.mask_for("rw", state_epoch=0)
        == result.mask_for("ro", state_epoch=0)
        == 0
    )
    assert result.claims[0].strength == "ambiguous"
    assert result.claims[0].experiment_ids == ("e1", "e2")


def test_different_epochs_keep_separate_claims():
    a = rw_events()
    b = events(
        [("read", 0), ("write", 0), ("read", 0), ("write", 1), ("read", 0)],
        start=5,
        epoch=1,
    )
    ea, da = experiment(a)
    eb, db = experiment(b, name="e2")
    result = classify_register(ref(), a + b, [ea, eb], [da, db])
    assert result.mask_for("rw", state_epoch=0) == 1
    assert result.mask_for("ro", state_epoch=1) == 1


@pytest.mark.parametrize("controls,expected", [((), 0), ((1,), 1)])
def test_w1c_needs_explicit_control_read(controls, expected):
    obs = events([("read", 1), ("read", 1), ("write", 1), ("read", 0)])
    result = classify(obs, kind="w1c", controls=controls)
    assert result.mask_for("w1c", state_epoch=0) == expected
    assert result.mask_for("rw", state_epoch=0) == 0


@pytest.mark.parametrize(
    "pairs,controls",
    [
        ([("read", 0), ("read", 0), ("write", 1), ("read", 0)], (1,)),
        ([("read", 1), ("write", 1), ("read", 0), ("read", 0)], (3,)),
        ([("read", 1), ("read", 0), ("write", 1), ("read", 0)], (1,)),
        ([("read", 1), ("read", 1), ("write", 0), ("read", 0)], (1,)),
    ],
)
def test_w1c_rejects_zero_baseline_late_control_async_clear_and_write_zero(
    pairs, controls
):
    assert (
        classify(events(pairs), kind="w1c", controls=controls).mask_for(
            "w1c", state_epoch=0
        )
        == 0
    )


def test_volatile_requires_controlled_experiment():
    obs = events([("read", 0), ("read", 1)])
    passive = classify_register(ref(), obs)
    active = classify(obs, kind="volatile")
    assert passive.mask_for("volatile", state_epoch=0) == 0
    assert active.mask_for("volatile", state_epoch=0) == 1
    assert "possible volatility" in passive.claims[0].note


@pytest.mark.parametrize(
    "override",
    [
        {"mediation": "mediated"},
        {"mediation": "unknown"},
        {"source": "native_driver"},
        {"source": "simulator"},
    ],
)
def test_nonphysical_noncollector_evidence_cannot_be_measured(override):
    result = classify(rw_events(**override))
    assert not any(c.strength == "measured" for c in result.claims)


@pytest.mark.parametrize("completion", ["error", "timeout", "unsupported"])
def test_failed_write_cannot_be_measured(completion):
    obs = rw_events()
    obs[1] = obs[1].model_copy(update={"completion": completion})
    assert classify(obs).mask_for("rw", state_epoch=0) == 0


@pytest.mark.parametrize("index", [0, 1, 2, 3, 4])
def test_missing_value_invalidates_measurement(index):
    obs = rw_events()
    key = "returned_value" if obs[index].operation == "read" else "attempted_value"
    obs[index] = obs[index].model_copy(update={key: None, "absent_reason": "missing"})
    assert classify(obs).mask_for("rw", state_epoch=0) == 0


@pytest.mark.parametrize(
    "override",
    [
        {"termination": "cancelled"},
        {"termination": "backend_error"},
        {"restoration": "not_attempted"},
        {"restoration": "not_restorable"},
        {"restoration": "failed", "restoration_detail": "readback mismatch"},
        {"state_epoch": 1},
        {"baseline_value": 1},
        {"time_budget_ns": 1},
        {"observation_ids": (0, 1, 2, 3, 99)},
        {"observation_ids": (0, 1, 2, 3, 4, 4)},
    ],
)
def test_invalid_experiments_stay_ambiguous(override):
    result = classify(rw_events(), **override)
    assert result.mask_for("rw", state_epoch=0) == 0
    assert not any(c.strength == "measured" for c in result.claims)


def test_both_directions_cannot_be_joined_across_epochs():
    obs = rw_events()
    obs[3:] = [o.model_copy(update={"state_epoch": 1}) for o in obs[3:]]
    assert classify(obs).mask_for("rw", state_epoch=0) == 0


@pytest.mark.parametrize(
    "change",
    [{"recipe_version": "2"}, {"trace_complete": False}, {"control_read_ids": (99,)}],
)
def test_stale_incomplete_or_invalid_recipe_declarations(change):
    obs = rw_events()
    exp, dec = experiment(obs)
    dec = dec.model_copy(update=change)
    result = classify_register(ref(), obs, [exp], [dec])
    assert result.mask_for("rw", state_epoch=0) == 0


def test_missing_recipe_does_not_infer_from_name():
    obs = rw_events()
    exp, _ = experiment(obs)
    assert classify_register(ref(), obs, [exp]).mask_for("rw", state_epoch=0) == 0


def test_omitted_intervening_write_invalidates_experiment():
    obs = rw_events()
    # Include a competing write between the first write and read.
    obs = [
        o.model_copy(
            update={
                "sequence_id": o.sequence_id * 2,
                "monotonic_ns": o.sequence_id * 20,
            }
        )
        for o in obs
    ]
    exp, dec = experiment(obs)
    extra = events([("write", 0)], start=3)[0]
    result = classify_register(ref(), obs + [extra], [exp], [dec])
    assert result.mask_for("rw", state_epoch=0) == 0
    assert "omitted" in result.claims[0].note


def test_byte_enable_limits_tested_bits():
    register = ref(width=2)
    obs = events(
        [("read", 0), ("write", 0x101), ("read", 0x101), ("write", 0), ("read", 0)],
        register=register,
    )
    obs[1] = obs[1].model_copy(update={"byte_enable": 1})
    obs[3] = obs[3].model_copy(update={"byte_enable": 1})
    result = classify(obs, mask=0x101)
    assert result.mask_for("rw", state_epoch=0) == 1
    assert result.unknown_mask(state_epoch=0) == 0xFFFE


def test_partial_reads_never_fabricate_full_initial_value():
    obs = rw_events(register=ref(width=2), byte_enable=1)
    result = classify(obs)
    assert result.observed_initial_value is None
    assert result.mask_for("rw", state_epoch=0) == 1


def test_mixed_bits_and_untested_coverage():
    obs = events([("read", 0), ("write", 3), ("read", 1), ("write", 0), ("read", 0)])
    result = classify(obs, mask=3)
    assert result.mask_for("rw", state_epoch=0) == 1
    assert result.mask_for("ro", state_epoch=0) == 2
    assert result.unknown_mask(state_epoch=0) == 0xFC


def test_same_values_in_distinct_registers_cannot_form_transitions():
    obs = rw_events()
    obs[2] = obs[2].model_copy(update={"ref": ref().model_copy(update={"offset": 4})})
    assert classify(obs).mask_for("rw", state_epoch=0) == 0
    assert len(classify_registers(obs)) == 2


def test_empty_register_is_untested_not_zero():
    result = classify_register(ref(), [])
    assert result.observed_initial_value is None
    assert result.claims[0].strength == "untested"


@pytest.mark.parametrize("duplicate", ["observation", "experiment", "recipe"])
def test_duplicate_identifiers_rejected(duplicate):
    obs = rw_events()
    exp, dec = experiment(obs)
    with pytest.raises(ValueError, match="Duplicate"):
        classify_register(
            ref(),
            obs + ([obs[0]] if duplicate == "observation" else []),
            [exp] * (2 if duplicate == "experiment" else 1),
            [dec] * (2 if duplicate == "recipe" else 1),
        )


def test_recipe_declaration_requires_explicit_completeness():
    with pytest.raises(ValidationError):
        RecipeDeclaration(
            experiment_id="e1", recipe_id="storage", recipe_version="1", kind="storage"
        )


def test_w1c_contradictory_clear_requests_stay_ambiguous():
    obs = events(
        [("read", 1), ("read", 1), ("write", 1), ("read", 1), ("write", 1), ("read", 0)]
    )
    result = classify(obs, kind="w1c", controls=(1,))
    assert result.mask_for("w1c", state_epoch=0) == 0
    assert result.claims[0].strength == "ambiguous"


def test_nonmonotonic_time_cannot_support_measurement():
    obs = rw_events()
    obs[2] = obs[2].model_copy(update={"monotonic_ns": 0})
    assert classify(obs).mask_for("rw", state_epoch=0) == 0


def test_experiment_tested_mask_cannot_replace_transition_evidence():
    obs = rw_events()
    result = classify(obs, mask=0xFF)
    assert result.mask_for("rw", state_epoch=0) == 1
    assert result.unknown_mask(state_epoch=0) == 0xFE


def test_intervening_narrow_write_also_invalidates_measurement():
    obs = rw_events(register=ref(width=4))
    obs = [
        o.model_copy(
            update={
                "sequence_id": o.sequence_id * 2,
                "monotonic_ns": o.sequence_id * 20,
            }
        )
        for o in obs
    ]
    exp, dec = experiment(obs)
    narrow = events([("write", 0)], start=3, register=ref(width=1))[0]
    result = classify_register(obs[0].ref, obs + [narrow], [exp], [dec])
    assert result.mask_for("rw", state_epoch=0) == 0
    assert "omitted" in result.claims[0].note


# --- Delayed completion within a declared settling window -------------


def settled(pairs, settling, *, kind="storage", controls=()):
    obs = events(pairs)
    exp, dec = experiment(obs, kind=kind, controls=controls)
    dec = dec.model_copy(update={"settling_reads": settling})
    return classify_register(obs[0].ref, obs, [exp], [dec])


DELAYED_RW = [
    ("read", 0),
    ("write", 1),
    ("read", 0),
    ("read", 1),
    ("write", 0),
    ("read", 1),
    ("read", 0),
]


def test_delayed_storage_write_is_rw_within_declared_window():
    assert settled(DELAYED_RW, 2).mask_for("rw", state_epoch=0) == 1


def test_without_a_declared_window_delay_stays_ambiguous():
    result = settled(DELAYED_RW, 1)
    assert result.mask_for("rw", state_epoch=0) == 0
    assert result.claims[0].strength == "ambiguous"


@pytest.mark.parametrize(
    "pairs",
    [
        # bounce inside the window
        [("read", 0), ("write", 1), ("read", 1), ("read", 0)]
        + [("write", 0), ("read", 0), ("read", 0)],
        # change between the decisive read and the next pre-write read
        [("read", 0), ("write", 1), ("read", 0), ("read", 1), ("read", 0)]
        + [("write", 0), ("read", 0), ("read", 0)],
        # window shorter than declared
        [("read", 0), ("write", 1), ("read", 1), ("write", 0), ("read", 0)],
        # write with no pre-write read
        [("write", 1), ("read", 1), ("read", 1), ("write", 0), ("read", 0)]
        + [("read", 0)],
    ],
)
def test_malformed_or_unstable_windows_stay_ambiguous(pairs):
    result = settled(pairs, 2)
    assert result.mask_for("rw", state_epoch=0) == 0
    assert result.claims[0].strength == "ambiguous"


def test_delayed_w1c_clear_within_window():
    pairs = [("read", 1), ("read", 1), ("write", 1), ("read", 1), ("read", 0)]
    assert (
        settled(pairs, 2, kind="w1c", controls=(1,)).mask_for("w1c", state_epoch=0) == 1
    )


def test_settling_reads_defaults_to_one_for_old_manifests():
    dec = RecipeDeclaration(
        experiment_id="e",
        recipe_id="r",
        recipe_version="1",
        kind="storage",
        trace_complete=True,
    )
    assert dec.settling_reads == 1
    with pytest.raises(ValidationError):
        dec.model_validate({**dec.model_dump(), "settling_reads": 0})
