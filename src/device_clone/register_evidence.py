"""Evidence and semantic models for donor register learning.

Pure data contracts shared by host collection (Stage 1) and offline generation
(Stage 2). Nothing here touches hardware. Every claim about a register keeps
its evidence strength separate from its semantic type, and an untested bit is
reported as unknown rather than silently becoming RW or RO.

Integer values are logical device values (little-endian PCI interpretation);
byte-order conversion happens only at serialized hardware boundaries.
"""

from __future__ import annotations

from typing import Final, Literal
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field, model_validator

LEARNING_SCHEMA_VERSION: Final = 1

# Config space is a separate address space from every BAR; never mix them.
AddressSpace = Literal["config", "mem_bar", "io_bar"]
Operation = Literal["read", "write"]
ObservationSource = Literal["native_driver", "collector", "simulator"]
# Whether an access is known to reach the physical device or was serviced by
# an intermediate layer (e.g. VFIO config virtualization). Unknown until a
# backend conformance check establishes otherwise.
Mediation = Literal["physical", "mediated", "unknown"]
Completion = Literal["ok", "error", "timeout", "unsupported"]

SemanticType = Literal[
    "rw",  # plain storage: accepted transitions in both directions
    "ro",  # writes have no effect on the value
    "w1c",  # write one to clear
    "w1s",  # write one to set
    "read_clear",  # read side effect clears the field
    "self_clearing",  # set by a write, cleared by the device
    "action",  # write triggers a device action; readback is not storage
    "volatile",  # changes without a matching host write
    "unknown",
]
EvidenceStrength = Literal[
    "measured",  # established by a controlled experiment
    "documented",  # specified by PCIe spec or vendor documentation
    "observed",  # seen in passive traffic; semantics not established
    # Consistent with every native-driver access in a complete passive trace:
    # the driver changed the bit and read the new value back, and nothing in
    # the trace contradicts plain storage. Not a controlled experiment.
    "inferred",
    "ambiguous",  # contradictory or insufficient results
    "untested",
]
# Strengths that may back a semantic claim other than "unknown".
_CLAIM_STRENGTHS = frozenset({"measured", "documented", "inferred"})

ExperimentTermination = Literal[
    "completed",
    "timeout",
    "cancelled",
    "backend_error",
    "precondition_failed",
    "aborted",
]
Restoration = Literal["verified", "failed", "not_attempted", "not_restorable"]
LearningStatus = Literal["absent", "complete", "partial", "failed"]
GapReason = Literal[
    "backend_unavailable",
    "backend_rejected",
    "mediated",
    "no_recipe",
    "excluded_by_policy",
    "precondition_failed",
    "restoration_failed",
    "trace_incomplete",
    # Passive initial values were read without a preceding function reset.
    "initial_state_unverified",
    # A capture exercise could not undo a host change (e.g. link left up).
    "host_state_changed",
    # A device exercise did not run, so its driver paths were not traced.
    "exercise_skipped",
    "ambiguous",
]

_WIDTHS = (1, 2, 4, 8)
_CONFIG_SPACE_SIZE = 4096
_STANDARD_CAP_LIMIT = 0x100


def width_mask(width: int) -> int:
    """All-ones mask for a register of ``width`` bytes."""
    return (1 << (8 * width)) - 1


def _check_fits(value: int | None, width: int, name: str) -> None:
    if value is not None and not 0 <= value <= width_mask(width):
        raise ValueError(f"{name} does not fit a {width}-byte register")


class _Frozen(BaseModel):
    # Strict: no bool/str coercion into integers. Load serialized evidence
    # with ``model_validate_json``, whose strict mode still accepts JSON
    # arrays for tuples and strings for UUIDs.
    model_config = ConfigDict(extra="forbid", frozen=True, strict=True)


class CapabilityLocator(_Frozen):
    """Identifies one capability instance; the ID alone is not unique."""

    kind: Literal["standard", "extended"]
    cap_id: int = Field(ge=0, le=0xFFFF)
    version: int | None = Field(default=None, ge=0, le=0xF)
    # Zero-based index among capabilities sharing ``kind`` and ``cap_id``.
    occurrence: int = Field(default=0, ge=0)
    base_offset: int = Field(ge=0, lt=_CONFIG_SPACE_SIZE)
    relative_offset: int = Field(ge=0, lt=_CONFIG_SPACE_SIZE)

    @model_validator(mode="after")
    def _region(self) -> CapabilityLocator:
        if self.kind == "standard":
            if not 0x40 <= self.base_offset < _STANDARD_CAP_LIMIT:
                raise ValueError("standard capability outside 0x40-0xFF")
            if self.cap_id > 0xFF:
                raise ValueError("standard capability ID exceeds 8 bits")
        elif self.base_offset < _STANDARD_CAP_LIMIT:
            raise ValueError("extended capability below 0x100")
        if self.base_offset + self.relative_offset >= _CONFIG_SPACE_SIZE:
            raise ValueError("capability field outside config space")
        return self


class RegisterRef(_Frozen):
    """A field location: address space, offset, access width and field bits."""

    space: AddressSpace
    bar_index: int | None = Field(default=None, ge=0, le=5)
    offset: int = Field(ge=0)
    width: int
    field_mask: int = Field(gt=0)
    capability: CapabilityLocator | None = None

    @model_validator(mode="after")
    def _location(self) -> RegisterRef:
        if self.width not in _WIDTHS:
            raise ValueError("register width must be 1, 2, 4 or 8 bytes")
        if self.offset % self.width:
            raise ValueError("register offset is not aligned to its width")
        _check_fits(self.field_mask, self.width, "field_mask")
        if self.space == "config":
            if self.bar_index is not None:
                raise ValueError("config register cannot name a BAR")
            if self.offset + self.width > _CONFIG_SPACE_SIZE:
                raise ValueError("config register outside config space")
            if self.width == 8:
                raise ValueError("config space has no 64-bit accesses")
            cap = self.capability
            if cap is not None:
                start = cap.base_offset + cap.relative_offset
                if not self.offset <= start < self.offset + self.width:
                    raise ValueError("capability field not inside the register")
        else:
            if self.bar_index is None:
                raise ValueError("BAR register requires bar_index")
            if self.capability is not None:
                raise ValueError("capabilities exist only in config space")
        return self

    @property
    def full_mask(self) -> int:
        return width_mask(self.width)


class DonorIdentity(_Frozen):
    vendor_id: int = Field(ge=0, le=0xFFFF)
    device_id: int = Field(ge=0, le=0xFFFF)
    revision_id: int = Field(ge=0, le=0xFF)
    class_code: int = Field(ge=0, le=0xFFFFFF)
    subsystem_vendor_id: int | None = Field(default=None, ge=0, le=0xFFFF)
    subsystem_device_id: int | None = Field(default=None, ge=0, le=0xFFFF)
    firmware_revision: str | None = None


class LearningContext(_Frozen):
    """What the evidence applies to. BDF is a locator, not an identity."""

    capture_id: UUID
    donor: DonorIdentity
    bdf: str
    kernel_version: str | None = None
    driver_name: str | None = None
    driver_version: str | None = None
    backend_version: str
    experiment_version: str


class Observation(_Frozen):
    """One raw access. Absent values are null with a reason, never zero."""

    sequence_id: int = Field(ge=0)
    monotonic_ns: int = Field(ge=0)
    operation: Operation
    ref: RegisterRef
    # One bit per byte lane of ``ref.width``; defaults to all lanes.
    byte_enable: int | None = Field(default=None, ge=1)
    attempted_value: int | None = Field(default=None, ge=0)
    returned_value: int | None = Field(default=None, ge=0)
    absent_reason: str | None = None
    completion: Completion
    source: ObservationSource
    mediation: Mediation = "unknown"
    state_epoch: int = Field(ge=0)
    # Measured only; never a fixed placeholder latency.
    duration_ns: int | None = Field(default=None, ge=0)
    # For native-driver traces: "probe" accesses follow the reset; accesses
    # provoked by device exercises reflect activated state, never initial.
    phase: Literal["probe", "exercise"] | None = None

    @model_validator(mode="after")
    def _values(self) -> Observation:
        width = self.ref.width
        if self.byte_enable is not None and self.byte_enable >= 1 << width:
            raise ValueError("byte_enable has lanes beyond the register width")
        _check_fits(self.attempted_value, width, "attempted_value")
        _check_fits(self.returned_value, width, "returned_value")
        if self.operation == "write":
            if self.returned_value is not None:
                raise ValueError("a write does not return a value")
            if self.attempted_value is None and self.absent_reason is None:
                raise ValueError("write without value requires absent_reason")
        else:
            if self.attempted_value is not None:
                raise ValueError("a read has no attempted value")
            if self.returned_value is None and self.absent_reason is None:
                raise ValueError("read without value requires absent_reason")
            if self.returned_value is not None and self.completion != "ok":
                raise ValueError("a failed read cannot return a value")
        return self

    @property
    def lane_mask(self) -> int:
        """Bit mask covered by the enabled byte lanes."""
        lanes = self.byte_enable
        if lanes is None:
            return self.ref.full_mask
        return sum(0xFF << (8 * i) for i in range(self.ref.width) if lanes >> i & 1)


class ExperimentResult(_Frozen):
    """Outcome of one bounded experiment recipe against one register."""

    experiment_id: str = Field(min_length=1)
    recipe_id: str = Field(min_length=1)
    recipe_version: str = Field(min_length=1)
    ref: RegisterRef
    state_epoch: int = Field(ge=0)
    preconditions: dict[str, str] = Field(default_factory=dict)
    baseline_value: int | None = Field(default=None, ge=0)
    requested_mask: int = Field(gt=0)
    tested_mask: int = Field(default=0, ge=0)
    observation_ids: tuple[int, ...] = ()
    time_budget_ns: int = Field(gt=0)
    termination: ExperimentTermination
    restoration: Restoration
    restoration_detail: str | None = None

    @model_validator(mode="after")
    def _bits(self) -> ExperimentResult:
        _check_fits(self.baseline_value, self.ref.width, "baseline_value")
        if self.requested_mask & ~self.ref.field_mask:
            raise ValueError("requested bits outside the field")
        if self.tested_mask & ~self.requested_mask:
            raise ValueError("tested bits were not requested")
        if self.restoration == "failed" and not self.restoration_detail:
            raise ValueError("failed restoration requires detail")
        return self


class FieldClaim(_Frozen):
    """A semantic claim for a subset of a register's bits in one state."""

    mask: int = Field(gt=0)
    semantic: SemanticType
    strength: EvidenceStrength
    # None means the claim is not state-scoped (e.g. documented RO).
    state_epoch: int | None = Field(default=None, ge=0)
    experiment_ids: tuple[str, ...] = ()
    observation_ids: tuple[int, ...] = ()
    note: str | None = None

    @model_validator(mode="after")
    def _consistent(self) -> FieldClaim:
        if self.semantic != "unknown" and self.strength not in _CLAIM_STRENGTHS:
            raise ValueError(
                f"{self.strength} evidence cannot establish {self.semantic}"
            )
        if self.semantic == "unknown" and self.strength in _CLAIM_STRENGTHS:
            raise ValueError("measured or documented evidence names a semantic")
        if self.strength == "measured" and not self.experiment_ids:
            raise ValueError("measured claim requires experiment references")
        if self.strength == "inferred" and (
            self.semantic != "rw" or not self.observation_ids
        ):
            raise ValueError("inferred claims are plain RW backed by observations")
        return self


class RegisterClassification(_Frozen):
    """All claims for one register. Unclaimed bits are unknown."""

    ref: RegisterRef
    claims: tuple[FieldClaim, ...] = ()
    observed_initial_value: int | None = Field(default=None, ge=0)
    # A reset value needs an identified reset domain from a controlled reset:
    # it cites the experiments that reset the domain and read the register.
    reset_value: int | None = Field(default=None, ge=0)
    reset_domain: str | None = Field(default=None, min_length=1)
    reset_experiment_ids: tuple[str, ...] = ()

    @model_validator(mode="after")
    def _claims(self) -> RegisterClassification:
        width = self.ref.width
        _check_fits(self.observed_initial_value, width, "observed_initial_value")
        _check_fits(self.reset_value, width, "reset_value")
        if (self.reset_value is None) != (self.reset_domain is None):
            raise ValueError("reset_value and reset_domain go together")
        if (self.reset_value is None) != (not self.reset_experiment_ids):
            raise ValueError("reset_value requires reset experiment references")
        if self.reset_value is not None and self.reset_value & ~self.ref.field_mask:
            raise ValueError("reset_value sets bits outside the field")
        claimed: dict[int | None, int] = {}
        for claim in self.claims:
            if claim.mask & ~self.ref.field_mask:
                raise ValueError("claim covers bits outside the field")
            # A state-independent claim conflicts with every state.
            overlapping = [
                bits
                for epoch, bits in claimed.items()
                if epoch is None
                or claim.state_epoch is None
                or epoch == claim.state_epoch
            ]
            if any(bits & claim.mask for bits in overlapping):
                raise ValueError("overlapping claims for the same state")
            key = claim.state_epoch
            claimed[key] = claimed.get(key, 0) | claim.mask
        return self

    def mask_for(
        self,
        semantic: SemanticType,
        *,
        strengths: frozenset[str] = _CLAIM_STRENGTHS,
        state_epoch: int | None = None,
    ) -> int:
        """Bits claimed as ``semantic`` with an accepted strength.

        With ``state_epoch`` set, state-independent claims and claims for that
        state count; otherwise only state-independent claims do.
        """
        bits = 0
        for claim in self.claims:
            if claim.semantic != semantic or claim.strength not in strengths:
                continue
            if claim.state_epoch is None or claim.state_epoch == state_epoch:
                bits |= claim.mask
        return bits

    def unknown_mask(self, state_epoch: int | None = None) -> int:
        """Field bits without an established semantic in the given state."""
        known = 0
        for claim in self.claims:
            if claim.semantic == "unknown" or claim.strength not in _CLAIM_STRENGTHS:
                continue
            if claim.state_epoch is None or claim.state_epoch == state_epoch:
                known |= claim.mask
        return self.ref.field_mask & ~known


class CoverageGap(_Frozen):
    ref: RegisterRef
    reason: GapReason
    detail: str | None = None


class RecipeRecord(_Frozen):
    """The reviewed recipe declaration an experiment ran under.

    Persisted so Stage 2 can re-derive classifications from raw evidence and
    check the recipe against the supported-recipe registry, rather than trust
    stored claims. ``trace_complete`` is the executor's attestation.
    """

    experiment_id: str = Field(min_length=1)
    recipe_id: str = Field(min_length=1)
    recipe_version: str = Field(min_length=1)
    # "reset": the executor reset ``preconditions["reset_domain"]`` before the
    # experiment's first observation, then only read.
    kind: Literal["storage", "w1c", "volatile", "reset"]
    trace_complete: bool
    control_read_ids: tuple[int, ...] = ()
    # Completion criterion: every write is followed by exactly this many reads
    # of the register (the executor's readback_reads); the last is decisive.
    settling_reads: int = Field(default=1, ge=1, le=100)


def sum_masks(masks) -> int:
    """Union of bit masks."""
    out = 0
    for mask in masks:
        out |= mask
    return out


def _same_location(a: RegisterRef, b: RegisterRef) -> bool:
    return (a.space, a.bar_index, a.offset, a.width) == (
        b.space,
        b.bar_index,
        b.offset,
        b.width,
    )


class LearningManifest(_Frozen):
    """Complete learning result for one capture.

    Status is derived, not asserted: a manifest claiming ``complete`` while
    listing gaps, failed restorations or ambiguous claims is rejected.
    """

    schema_version: Literal[1] = LEARNING_SCHEMA_VERSION
    context: LearningContext
    status: LearningStatus
    observations: tuple[Observation, ...] = ()
    experiments: tuple[ExperimentResult, ...] = ()
    classifications: tuple[RegisterClassification, ...] = ()
    recipes: tuple[RecipeRecord, ...] = ()
    gaps: tuple[CoverageGap, ...] = ()
    # Dropped trace events invalidate affected transition evidence.
    dropped_events: int = Field(default=0, ge=0)
    # Qualifications of a successful capture (topology, exercises run).
    capture_notes: tuple[str, ...] = ()
    # The completeness denominator: each planned register, with field_mask set
    # to the bits the plan set out to establish. None means the manifest
    # predates the field and keeps the older rule; an empty plan is never
    # complete.
    planned: tuple[RegisterRef, ...] | None = None

    @model_validator(mode="after")
    def _integrity(self) -> LearningManifest:
        seqs = [o.sequence_id for o in self.observations]
        if len(set(seqs)) != len(seqs):
            raise ValueError("duplicate observation sequence IDs")
        known_obs = set(seqs)
        exp_ids = [e.experiment_id for e in self.experiments]
        if len(set(exp_ids)) != len(exp_ids):
            raise ValueError("duplicate experiment IDs")
        known_exp = set(exp_ids)
        for experiment in self.experiments:
            if not known_obs.issuperset(experiment.observation_ids):
                raise ValueError("experiment references an unknown observation")
        experiments = {e.experiment_id: e for e in self.experiments}
        recipes = {r.experiment_id: r for r in self.recipes}
        if len(recipes) != len(self.recipes):
            raise ValueError("duplicate recipe records")
        observations = {o.sequence_id: o for o in self.observations}
        for record in self.recipes:
            exp = experiments.get(record.experiment_id)
            if exp is None:
                raise ValueError("recipe record references an unknown experiment")
            if (exp.recipe_id, exp.recipe_version) != (
                record.recipe_id,
                record.recipe_version,
            ):
                raise ValueError("recipe record disagrees with its experiment")
            if not set(record.control_read_ids) <= set(exp.observation_ids):
                raise ValueError("control read outside its experiment")
        refs = [c.ref for c in self.classifications]
        if len(set(refs)) != len(refs):
            raise ValueError("duplicate register classification")
        for cls in self.classifications:
            for claim in cls.claims:
                if not known_obs.issuperset(claim.observation_ids):
                    raise ValueError("claim references an unknown observation")
                if not known_exp.issuperset(claim.experiment_ids):
                    raise ValueError("claim references an unknown experiment")
                if claim.strength == "measured":
                    self._check_measured(cls, claim, experiments, recipes, observations)
            if cls.reset_value is not None:
                self._check_reset(cls, experiments, recipes, observations)
        if self.status == "absent" and (
            self.observations or self.experiments or self.classifications
        ):
            raise ValueError("absent learning cannot carry evidence")
        if self.status == "complete" and not self._is_complete():
            raise ValueError("learning with gaps or failures is not complete")
        return self

    @staticmethod
    def _check_measured(
        cls: RegisterClassification,
        claim: FieldClaim,
        experiments: dict[str, ExperimentResult],
        recipes: dict[str, RecipeRecord],
        observations: dict[int, Observation],
    ) -> None:
        """A measured claim must be backed by a matching, completed experiment.

        Structural checks only; stored claims are advisory. Stage 2 re-derives
        semantics from raw evidence through the classifier and recipe registry.
        """
        for exp_id in claim.experiment_ids:
            exp = experiments[exp_id]
            if exp_id not in recipes:
                raise ValueError("measured claim has no recipe record")
            if not recipes[exp_id].trace_complete:
                raise ValueError("measured claim cites an incomplete trace")
            if recipes[exp_id].kind == "reset":
                raise ValueError("measured claim cites a reset experiment")
            if not any(
                _same_location(observations[i].ref, cls.ref)
                for i in exp.observation_ids
            ):
                raise ValueError("measured claim has no access at its register")
            if not _same_location(exp.ref, cls.ref):
                raise ValueError("measured claim cites an experiment elsewhere")
            if claim.state_epoch != exp.state_epoch:
                raise ValueError("measured claim cites an experiment in another state")
            if exp.termination != "completed" or exp.restoration != "verified":
                raise ValueError("measured claim cites an unfinished experiment")
            if claim.mask & ~exp.tested_mask:
                raise ValueError(
                    "measured claim covers bits the experiment never tested"
                )
            if not exp.observation_ids or any(
                observations[i].source != "collector"
                or observations[i].mediation != "physical"
                for i in exp.observation_ids
            ):
                raise ValueError("measured claim lacks physical collector evidence")

    @staticmethod
    def _check_reset(
        cls: RegisterClassification,
        experiments: dict[str, ExperimentResult],
        recipes: dict[str, RecipeRecord],
        observations: dict[int, Observation],
    ) -> None:
        """A reset value must come from a finished reset of its domain.

        Each cited experiment is a ``reset`` recipe at this register for the
        same ``reset_domain``, on a complete trace. The recipe resets before
        its first observation and then only reads, so every observation is a
        post-reset read: all must be physical collector reads in the
        experiment's state, cover the whole field, and agree with
        ``reset_value``. A write, or a value that moves, rejects it.
        """
        mask = cls.ref.field_mask
        for exp_id in cls.reset_experiment_ids:
            exp = experiments.get(exp_id)
            if exp is None:
                raise ValueError("reset value references an unknown experiment")
            record = recipes.get(exp_id)
            if record is None or not record.trace_complete:
                raise ValueError("reset value cites an incomplete trace")
            if record.kind != "reset":
                raise ValueError("reset value cites a non-reset recipe")
            if not _same_location(exp.ref, cls.ref):
                raise ValueError("reset value cites an experiment elsewhere")
            if exp.preconditions.get("reset_domain") != cls.reset_domain:
                raise ValueError("reset value cites a reset of another domain")
            if exp.termination != "completed" or exp.restoration == "failed":
                raise ValueError("reset value cites an unfinished experiment")
            events = [observations[i] for i in exp.observation_ids]
            if any(o.operation != "read" for o in events):
                raise ValueError("reset value cites an experiment with writes")
            reads = [o for o in events if _same_location(o.ref, cls.ref)]
            if not reads or any(
                o.completion != "ok"
                or o.source != "collector"
                or o.mediation != "physical"
                or o.state_epoch != exp.state_epoch
                or mask & ~o.lane_mask
                or o.returned_value is None
                for o in reads
            ):
                raise ValueError(
                    "reset value lacks full physical reads at its register"
                )
            if any(
                o.returned_value is None or (o.returned_value & mask) != cls.reset_value
                for o in reads
            ):
                raise ValueError("reset value disagrees with the post-reset reads")

    def _is_complete(self) -> bool:
        if self.gaps or self.dropped_events:
            return False
        if not self.classifications or self.planned == ():
            return False
        # Every planned bit is established in some state of its register.
        for plan in self.planned or ():
            claims = [
                claim
                for c in self.classifications
                if _same_location(c.ref, plan)
                for claim in c.claims
                if claim.semantic != "unknown" and claim.strength in _CLAIM_STRENGTHS
            ]
            epochs = {c.state_epoch for c in claims}
            if not any(
                not plan.field_mask
                & ~sum_masks(
                    c.mask
                    for c in claims
                    if c.state_epoch is None or c.state_epoch == epoch
                )
                for epoch in epochs
            ):
                return False
        # Every classified register must be fully established (measured or
        # documented) in at least one state. Observed-only registers never are;
        # passive observations from other states do not block completeness.
        for cls in self.classifications:
            epochs = {
                c.state_epoch for c in cls.claims if c.strength in _CLAIM_STRENGTHS
            }
            if not epochs or all(cls.unknown_mask(epoch) for epoch in epochs):
                return False
        if any(
            e.termination != "completed" or e.restoration in ("failed", "not_attempted")
            for e in self.experiments
        ):
            return False
        return not any(
            claim.strength == "ambiguous"
            for cls in self.classifications
            for claim in cls.claims
        )

    @classmethod
    def absent(cls, context: LearningContext) -> LearningManifest:
        """Placeholder for captures without learning evidence (legacy or failed)."""
        return cls(context=context, status="absent")
