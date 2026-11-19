"""Contract tests for the donor register-learning evidence schema."""

from uuid import uuid4

import pytest
from pcileechfwgenerator.device_clone.register_evidence import (
    CapabilityLocator,
    CoverageGap,
    DonorIdentity,
    ExperimentResult,
    FieldClaim,
    LearningContext,
    LearningManifest,
    Observation,
    RegisterClassification,
    RegisterRef,
    width_mask,
)
from pydantic import ValidationError


def _ctx() -> LearningContext:
    return LearningContext(
        capture_id=uuid4(),
        donor=DonorIdentity(
            vendor_id=0x8086, device_id=0x1521, revision_id=1, class_code=0x020000
        ),
        bdf="0000:03:00.0",
        backend_version="test-1",
        experiment_version="test-1",
    )


def _cfg(offset=0x04, width=2, mask=0xFFFF) -> RegisterRef:
    return RegisterRef(space="config", offset=offset, width=width, field_mask=mask)


def _read(seq, value, ref=None, epoch=0) -> Observation:
    return Observation(
        sequence_id=seq,
        monotonic_ns=seq * 10,
        operation="read",
        ref=ref or _cfg(),
        returned_value=value,
        completion="ok",
        source="collector",
        state_epoch=epoch,
    )


def _write(seq, value, ref=None) -> Observation:
    return Observation(
        sequence_id=seq,
        monotonic_ns=seq * 10,
        operation="write",
        ref=ref or _cfg(),
        attempted_value=value,
        completion="ok",
        source="collector",
        state_epoch=0,
    )


def _experiment(ids, ref=None, **kw) -> ExperimentResult:
    ref = ref or _cfg()
    fields = dict(
        experiment_id="e1",
        recipe_id="storage",
        recipe_version="1",
        ref=ref,
        state_epoch=0,
        requested_mask=ref.field_mask,
        tested_mask=ref.field_mask,
        observation_ids=ids,
        time_budget_ns=1_000_000,
        termination="completed",
        restoration="verified",
    )
    fields.update(kw)
    return ExperimentResult(**fields)


class TestRegisterRef:
    def test_width_mask(self):
        assert width_mask(1) == 0xFF and width_mask(4) == 0xFFFFFFFF

    @pytest.mark.parametrize(
        "kw",
        [
            dict(space="config", offset=0x02, width=4, field_mask=1),  # misaligned
            dict(space="config", offset=0, width=3, field_mask=1),  # width
            dict(space="config", offset=0, width=2, field_mask=0x10000),  # mask
            dict(space="config", offset=0, width=2, field_mask=0),  # empty
            dict(space="config", offset=0xFFC, width=8, field_mask=1),  # 64-bit cfg
            dict(space="config", offset=0x1000, width=4, field_mask=1),  # outside
            dict(space="config", bar_index=0, offset=0, width=4, field_mask=1),
            dict(space="mem_bar", offset=0, width=4, field_mask=1),  # no BAR index
        ],
    )
    def test_rejects_invalid_locations(self, kw):
        with pytest.raises(ValidationError):
            RegisterRef(**kw)

    def test_bool_is_not_an_integer(self):
        with pytest.raises(ValidationError):
            RegisterRef(space="config", offset=0, width=4, field_mask=True)

    def test_capability_field_must_lie_in_register(self):
        cap = CapabilityLocator(
            kind="standard", cap_id=0x10, base_offset=0x70, relative_offset=0x08
        )
        RegisterRef(space="config", offset=0x78, width=2, field_mask=1, capability=cap)
        with pytest.raises(ValidationError):
            RegisterRef(
                space="config", offset=0x7C, width=2, field_mask=1, capability=cap
            )

    def test_capability_regions(self):
        with pytest.raises(ValidationError):
            CapabilityLocator(
                kind="extended", cap_id=1, base_offset=0x40, relative_offset=0
            )
        with pytest.raises(ValidationError):
            CapabilityLocator(
                kind="standard", cap_id=0x100, base_offset=0x40, relative_offset=0
            )


class TestObservation:
    def test_absent_read_value_needs_reason(self):
        with pytest.raises(ValidationError):
            Observation(
                sequence_id=0,
                monotonic_ns=0,
                operation="read",
                ref=_cfg(),
                completion="error",
                source="native_driver",
                state_epoch=0,
            )
        obs = Observation(
            sequence_id=0,
            monotonic_ns=0,
            operation="read",
            ref=_cfg(),
            absent_reason="unmatched kretprobe",
            completion="error",
            source="native_driver",
            state_epoch=0,
        )
        assert obs.returned_value is None  # never a fabricated zero

    def test_value_must_fit_width(self):
        with pytest.raises(ValidationError):
            _read(0, 0x1_0000)

    def test_write_cannot_return_value(self):
        with pytest.raises(ValidationError):
            Observation(
                sequence_id=0,
                monotonic_ns=0,
                operation="write",
                ref=_cfg(),
                attempted_value=1,
                returned_value=1,
                completion="ok",
                source="collector",
                state_epoch=0,
            )

    def test_byte_lanes(self):
        obs = Observation(
            sequence_id=0,
            monotonic_ns=0,
            operation="write",
            ref=_cfg(offset=0x10, width=4, mask=0xFFFFFFFF),
            byte_enable=0b0101,
            attempted_value=0x11223344,
            completion="ok",
            source="collector",
            state_epoch=0,
        )
        assert obs.lane_mask == 0x00FF00FF
        with pytest.raises(ValidationError):
            Observation(**{**obs.model_dump(), "byte_enable": 0b10000})


class TestClaims:
    def test_observed_evidence_cannot_claim_rw(self):
        with pytest.raises(ValidationError):
            FieldClaim(mask=1, semantic="rw", strength="observed")

    def test_measured_claim_requires_experiment(self):
        with pytest.raises(ValidationError):
            FieldClaim(mask=1, semantic="rw", strength="measured")

    def test_untested_bits_stay_unknown(self):
        cls = RegisterClassification(
            ref=_cfg(),
            claims=(
                FieldClaim(
                    mask=0x0006,
                    semantic="rw",
                    strength="measured",
                    experiment_ids=("e1",),
                    state_epoch=0,
                ),
                FieldClaim(mask=0x0100, semantic="ro", strength="documented"),
                FieldClaim(mask=0x0400, semantic="unknown", strength="ambiguous"),
            ),
        )
        assert cls.mask_for("rw", state_epoch=0) == 0x0006
        assert cls.mask_for("rw") == 0  # state-scoped claim needs its state
        assert cls.mask_for("ro") == 0x0100
        assert cls.unknown_mask(state_epoch=0) == 0xFFFF & ~0x0106

    def test_overlap_rejected_within_state_but_allowed_across_states(self):
        rw = dict(semantic="rw", strength="measured", experiment_ids=("e1",))
        RegisterClassification(
            ref=_cfg(),
            claims=(
                FieldClaim(mask=1, state_epoch=0, **rw),
                FieldClaim(mask=1, state_epoch=1, **rw),
            ),
        )
        with pytest.raises(ValidationError):
            RegisterClassification(
                ref=_cfg(),
                claims=(
                    FieldClaim(mask=1, state_epoch=0, **rw),
                    FieldClaim(mask=1, semantic="ro", strength="documented"),
                ),
            )

    def test_reset_value_requires_domain(self):
        with pytest.raises(ValidationError):
            RegisterClassification(ref=_cfg(), reset_value=0)
        with pytest.raises(ValidationError, match="reset experiment"):
            RegisterClassification(ref=_cfg(), reset_value=0, reset_domain="flr")
        with pytest.raises(ValidationError, match="reset experiment"):
            RegisterClassification(ref=_cfg(), reset_experiment_ids=("r1",))
        cls = RegisterClassification(ref=_cfg(), observed_initial_value=0x0406)
        assert cls.reset_value is None


class TestManifest:
    def test_absent_carries_no_evidence(self):
        manifest = LearningManifest.absent(_ctx())
        assert manifest.status == "absent"
        with pytest.raises(ValidationError):
            LearningManifest(
                context=_ctx(), status="absent", observations=(_read(0, 1),)
            )

    def test_complete_rejected_with_gaps_or_failed_restoration(self):
        with pytest.raises(ValidationError):
            LearningManifest(
                context=_ctx(),
                status="complete",
                gaps=(CoverageGap(ref=_cfg(), reason="no_recipe"),),
            )
        obs = (_read(0, 0), _write(1, 1), _read(2, 1))
        with pytest.raises(ValidationError):
            LearningManifest(
                context=_ctx(),
                status="complete",
                observations=obs,
                experiments=(
                    _experiment(
                        (0, 1, 2), restoration="failed", restoration_detail="stuck"
                    ),
                ),
            )
        with pytest.raises(ValidationError):
            LearningManifest(context=_ctx(), status="complete", dropped_events=3)

    def test_references_must_resolve(self):
        with pytest.raises(ValidationError):
            LearningManifest(
                context=_ctx(), status="partial", experiments=(_experiment((7,)),)
            )
        claim = FieldClaim(
            mask=1, semantic="rw", strength="measured", experiment_ids=("missing",)
        )
        with pytest.raises(ValidationError):
            LearningManifest(
                context=_ctx(),
                status="partial",
                classifications=(RegisterClassification(ref=_cfg(), claims=(claim,)),),
            )

    def test_json_round_trip(self):
        manifest = _measured_manifest()
        restored = LearningManifest.model_validate_json(manifest.model_dump_json())
        assert restored == manifest

    # --- Measured claims must be backed by matching evidence ---------

    @pytest.mark.parametrize(
        "change,match",
        [
            (dict(recipes=()), "no recipe record"),
            (dict(exp=dict(tested_mask=0x0002)), "never tested"),
            (dict(exp=dict(state_epoch=1)), "another state"),
            (
                dict(exp=dict(restoration="failed", restoration_detail="x")),
                "unfinished",
            ),
            (dict(exp=dict(termination="timeout")), "unfinished"),
            (
                dict(exp=dict(ref=_cfg(offset=0x08, width=2, mask=0xFFFF))),
                "elsewhere",
            ),
            (dict(mediation="unknown"), "physical collector"),
        ],
    )
    def test_measured_claim_without_matching_evidence_is_rejected(self, change, match):
        with pytest.raises(ValidationError, match=match):
            _measured_manifest(**change)

    def test_unrelated_config_experiment_cannot_back_bar_rw_claim(self):
        """The review's reproducer: nothing measured, BAR claimed fully RW."""
        bar = RegisterRef(
            space="mem_bar", bar_index=0, offset=0, width=4, field_mask=0xFFFFFFFF
        )
        exp = _experiment((), tested_mask=0)
        with pytest.raises(ValidationError):
            LearningManifest(
                context=_ctx(),
                status="complete",
                experiments=(exp,),
                recipes=(_recipe(),),
                classifications=(
                    RegisterClassification(
                        ref=bar,
                        observed_initial_value=0,
                        claims=(
                            FieldClaim(
                                mask=0xFFFFFFFF,
                                semantic="rw",
                                strength="measured",
                                state_epoch=0,
                                experiment_ids=("e1",),
                            ),
                        ),
                    ),
                ),
            )

    def test_complete_requires_established_registers(self):
        with pytest.raises(ValidationError, match="not complete"):
            LearningManifest(context=_ctx(), status="complete")  # nothing at all
        observed = RegisterClassification(
            ref=_cfg(),
            claims=(FieldClaim(mask=0xFFFF, semantic="unknown", strength="observed"),),
        )
        with pytest.raises(ValidationError, match="not complete"):
            LearningManifest(
                context=_ctx(), status="complete", classifications=(observed,)
            )
        partial = _measured_manifest(status="complete", field_mask=0xFFFF)
        assert partial.status == "complete"

    def test_recipe_record_must_match_experiment(self):
        with pytest.raises(ValidationError, match="disagrees"):
            _measured_manifest(recipes=(_recipe(recipe_version="2"),))


def _recipe(**kw):
    from pcileechfwgenerator.device_clone.register_evidence import RecipeRecord

    fields = dict(
        experiment_id="e1",
        recipe_id="storage",
        recipe_version="1",
        kind="storage",
        trace_complete=True,
    )
    fields.update(kw)
    return RecipeRecord(**fields)


def _measured_manifest(
    *, exp=None, recipes=None, mediation="physical", status="partial", field_mask=None
):
    obs = tuple(
        o.model_copy(update={"mediation": mediation})
        for o in (_read(0, 0x0000), _write(1, 0x0006), _read(2, 0x0006))
    )
    experiment = _experiment((0, 1, 2), tested_mask=0x0006)
    if exp:
        experiment = experiment.model_copy(update=exp)
    ref = _cfg() if field_mask is None else _cfg(mask=field_mask)
    claims = [
        FieldClaim(
            mask=0x0006,
            semantic="rw",
            strength="measured",
            state_epoch=0,
            experiment_ids=("e1",),
            observation_ids=(0, 1, 2),
        )
    ]
    if field_mask is not None:
        claims.append(
            FieldClaim(mask=0xFFFF & ~0x0006, semantic="ro", strength="documented")
        )
    return LearningManifest(
        context=_ctx(),
        status=status,
        observations=obs,
        experiments=(experiment,),
        recipes=(_recipe(),) if recipes is None else recipes,
        classifications=(
            RegisterClassification(
                ref=ref, claims=tuple(claims), observed_initial_value=0
            ),
        ),
    )


def _phys(o):
    return o.model_copy(update={"mediation": "physical"})


def _reset_manifest(*, value=0x0406, exp=None, recipe=None, obs=None):
    """One FLR experiment at the register: reset, then two full reads."""
    reads = obs or (_phys(_read(0, 0x0406)), _phys(_read(1, 0x0406)))
    experiment = _experiment(
        tuple(o.sequence_id for o in reads),
        experiment_id="r1",
        recipe_id="flr",
        tested_mask=0xFFFF,
        preconditions={"reset_domain": "flr"},
        restoration="not_restorable",
    )
    if exp:
        experiment = experiment.model_copy(update=exp)
    record = _recipe(experiment_id="r1", recipe_id="flr", kind="reset")
    if recipe:
        record = record.model_copy(update=recipe)
    return LearningManifest(
        context=_ctx(),
        status="partial",
        observations=reads,
        experiments=(experiment,),
        recipes=(record,),
        classifications=(
            RegisterClassification(
                ref=_cfg(),
                reset_value=value,
                reset_domain="flr",
                reset_experiment_ids=("r1",),
            ),
        ),
    )


class TestResetEvidence:
    def test_finished_reset_of_the_domain_supports_the_value(self):
        manifest = _reset_manifest()
        assert manifest.classifications[0].reset_value == 0x0406

    @pytest.mark.parametrize(
        "kw,reason",
        [
            ({"value": 0x0407}, "disagrees with the post-reset reads"),
            (
                {"obs": (_phys(_read(0, 0x0007)), _phys(_read(1, 0x0406)))},
                "disagrees with the post-reset reads",
            ),
            (
                {"obs": (_phys(_write(0, 0x0406)), _phys(_read(1, 0x0406)))},
                "with writes",
            ),
            ({"obs": (_phys(_read(0, 0x0406, epoch=1)),)}, "full physical reads"),
            (
                {
                    "obs": (
                        _phys(_read(0, 0x0006)).model_copy(update={"byte_enable": 0x1}),
                    )
                },
                "full physical reads",
            ),
            ({"obs": (_read(0, 0x0406), _read(1, 0x0406))}, "full physical reads"),
            ({"recipe": {"kind": "volatile"}}, "non-reset recipe"),
            ({"recipe": {"trace_complete": False}}, "incomplete trace"),
            ({"exp": {"preconditions": {"reset_domain": "hot"}}}, "another domain"),
            ({"exp": {"preconditions": {}}}, "another domain"),
            ({"exp": {"termination": "timeout"}}, "unfinished"),
            ({"exp": {"ref": _cfg(offset=0x08)}}, "elsewhere"),
        ],
    )
    def test_unbacked_reset_value_is_rejected(self, kw, reason):
        with pytest.raises(ValidationError, match=reason):
            _reset_manifest(**kw)

    def test_reset_value_must_fit_the_field(self):
        with pytest.raises(ValidationError, match="outside the field"):
            RegisterClassification(
                ref=_cfg(mask=0x00FF),
                reset_value=0x0100,
                reset_domain="flr",
                reset_experiment_ids=("r1",),
            )

    def test_unknown_reset_experiment_is_rejected(self):
        manifest = _reset_manifest()
        with pytest.raises(ValidationError, match="unknown experiment"):
            _with(manifest, experiments=(), recipes=())

    def test_measured_claim_cannot_cite_a_reset_experiment(self):
        with pytest.raises(ValidationError, match="reset experiment"):
            _measured_manifest(recipes=(_recipe(kind="reset"),))


def _with(manifest, **update):
    fields = {k: getattr(manifest, k) for k in type(manifest).model_fields}
    return LearningManifest(**(fields | update))


class TestPlannedDenominator:
    """A complete manifest must cover what its plan set out to establish."""

    def test_complete_when_every_planned_bit_is_established(self):
        base = _measured_manifest(field_mask=0xFFFF)
        manifest = _with(base, status="complete", planned=(_cfg(mask=0x0006),))
        assert manifest.status == "complete"

    @pytest.mark.parametrize(
        "planned",
        [
            (),
            (_cfg(mask=0x0006), _cfg(offset=0x08, mask=0x1)),
        ],
        ids=["empty-plan", "unclassified-planned-register"],
    )
    def test_narrow_capture_is_not_complete(self, planned):
        base = _measured_manifest(field_mask=0xFFFF)
        with pytest.raises(ValidationError, match="not complete"):
            _with(base, status="complete", planned=planned)
        assert _with(base, planned=planned).status == "partial"

    def test_planned_bits_beyond_the_claims_are_not_complete(self):
        base = _measured_manifest()  # claims cover only 0x0006 of 0xFFFF
        with pytest.raises(ValidationError, match="not complete"):
            _with(base, status="complete", planned=(_cfg(mask=0x0106),))

    def test_planned_bits_outside_the_classified_field_are_not_complete(self):
        base = _measured_manifest()
        (cls,) = base.classifications
        narrow = RegisterClassification(
            ref=_cfg(mask=0x0006), claims=cls.claims, observed_initial_value=0
        )
        assert (
            _with(
                base,
                classifications=(narrow,),
                status="complete",
                planned=(_cfg(mask=0x0006),),
            ).status
            == "complete"
        )
        with pytest.raises(ValidationError, match="not complete"):
            _with(
                base,
                classifications=(narrow,),
                status="complete",
                planned=(_cfg(mask=0x0F06),),
            )

    def test_manifest_without_a_plan_keeps_the_older_rule(self):
        base = _measured_manifest(field_mask=0xFFFF)
        assert base.planned is None
        assert _with(base, status="complete").status == "complete"
