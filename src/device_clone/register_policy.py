"""Compile evidence into conservative BAR storage policy without hardware access.

Measured, documented and inferred (complete native-driver trace) plain RW
claims grant writes. A single recorded epoch
is selected automatically; multiple epochs require explicit ``state_epoch``.
They are never unioned. Unsupported semantics are retained in the result and
register hints; RO storage is an approximation, not implementation of them.

The legacy RegisterSpec requires an integer reset even when no value was read.
EvidenceRegisterSpec is a local, validated subclass permitting None with a reason.
No zero or first-write value is invented. Serializers preserve null; the legacy
loader and RTL template require follow-up changes before such a model can render.
"""

from __future__ import annotations

from collections import defaultdict
from collections.abc import Iterable

from pcileechfwgenerator.device_clone.bar_model_loader import BarModel, RegisterSpec
from pcileechfwgenerator.device_clone.register_evidence import (
    LearningManifest,
    Observation,
    RegisterClassification,
    RegisterRef,
    SemanticType,
    width_mask,
)
from pcileechfwgenerator.log_config import get_logger
from pydantic import BaseModel, ConfigDict, Field
from pydantic.dataclasses import dataclass

logger = get_logger(__name__)


@dataclass(config=ConfigDict(strict=True))
class EvidenceRegisterSpec(RegisterSpec):
    """Legacy register shape with a nullable initial value and absence reason."""

    reset: int | None

    def __post_init__(self) -> None:
        if self.width not in (1, 2, 4):
            raise ValueError("BAR model supports only 1, 2 or 4 byte registers")
        if self.offset < 0 or self.offset % self.width:
            raise ValueError("Invalid BAR register alignment")
        if not 0 <= self.rw_mask <= width_mask(self.width):
            raise ValueError("Writable mask exceeds register width")
        if self.reset is not None and not 0 <= self.reset <= width_mask(self.width):
            raise ValueError("Initial value exceeds register width")
        if self.reset is None and not self.hints.get("initial_value_absent_reason"):
            raise ValueError("Absent initial value requires a reason")


class UnsupportedItem(BaseModel):
    """A semantic or representation gap that requires a generation decision."""

    model_config = ConfigDict(extra="forbid", frozen=True, strict=True)
    ref: RegisterRef
    mask: int = Field(gt=0)
    semantic: SemanticType | None = None
    state_epoch: int | None = None
    reason: str
    experiment_ids: tuple[str, ...] = ()
    observation_ids: tuple[int, ...] = ()


class CoverageCounts(BaseModel):
    """Bit coverage of the supplied register plan, never the whole BAR aperture.

    Strength buckets partition planned bits in the selected state. Unknown and
    unsupported counts are independent dimensions and can overlap those buckets.
    """

    model_config = ConfigDict(extra="forbid", frozen=True, strict=True)
    registers: int = 0
    planned_bits: int = 0
    measured_bits: int = 0
    documented_bits: int = 0
    observed_bits: int = 0
    inferred_bits: int = 0
    # Registers served from reset 0 because the driver always wrote them
    # before reading (no observed initial value exists).
    unobserved_reset_registers: int = 0
    ambiguous_bits: int = 0
    untested_bits: int = 0
    unknown_bits: int = 0
    writable_bits: int = 0
    unsupported_bits: int = 0
    emitted_registers: int = 0


class BarCompilationResult(BaseModel):
    """Compiled model with semantic gaps and coverage; inspect before generation."""

    model_config = ConfigDict(extra="forbid", frozen=True)
    model: BarModel
    unsupported: list[UnsupportedItem]
    coverage: CoverageCounts
    state_epoch: int | None


def compile_bar_model(
    classifications: Iterable[RegisterClassification],
    bar_index: int,
    bar_size: int,
    *,
    state_epoch: int | None = None,
    allow_inferred: bool = True,
    read_offsets: frozenset[int] = frozenset(),
) -> BarCompilationResult:
    """Compile one memory BAR, keeping unsupported/unknown bits non-writable.

    Measured, documented and (when ``allow_inferred``) inferred plain-RW
    claims grant writes. Inferred claims come from a complete native-driver
    trace; the caller disables them when the trace dropped events.

    Other BARs and config space are ignored. I/O BARs and 64-bit registers are
    reported as unsupported and omitted. Overlapping register/field definitions
    and out-of-bounds references raise ValueError rather than guessing a layout.
    """
    if type(bar_index) is not int or not 0 <= bar_index <= 5:
        raise ValueError("BAR index must be an integer from zero through five")
    if type(bar_size) is not int or bar_size <= 0:
        raise ValueError("BAR size must be a positive integer")
    if state_epoch is not None and (type(state_epoch) is not int or state_epoch < 0):
        raise ValueError("State epoch must be a nonnegative integer")
    selected = [
        c
        for c in classifications
        if c.ref.space != "config" and c.ref.bar_index == bar_index
    ]
    epochs = {
        claim.state_epoch
        for c in selected
        for claim in c.claims
        if claim.state_epoch is not None
    }
    if state_epoch is None and len(epochs) == 1:
        state_epoch = next(iter(epochs))
    groups: dict[tuple[int, int], list[RegisterClassification]] = defaultdict(list)
    for c in selected:
        if c.ref.offset + c.ref.width > bar_size:
            raise ValueError("Register extends outside BAR")
        groups[(c.ref.offset, c.ref.width)].append(c)
    registers: dict[int, RegisterSpec] = {}
    unsupported: list[UnsupportedItem] = []
    counts = dict.fromkeys(CoverageCounts.model_fields, 0)
    end = 0
    # Drivers commonly mix widths on one register (ioread32 then ioread8 of a
    # byte field). Overlapping access shapes cannot share one storage entry, so
    # report each as unsupported and omit it rather than failing the whole BAR.
    overlapping: set[tuple[int, int]] = set()
    previous: tuple[int, int] | None = None
    for key in sorted(groups):
        if previous is not None and key[0] < end:
            overlapping.update((previous, key))
        if key[0] + key[1] > end:
            end, previous = key[0] + key[1], key
    for (offset, width), group in sorted(groups.items()):
        if (offset, width) in overlapping:
            ref = group[0].ref.model_copy(update={"field_mask": width_mask(width)})
            planned = 0
            for c in group:
                planned |= c.ref.field_mask
            unsupported.append(
                UnsupportedItem(
                    ref=ref,
                    mask=ref.field_mask,
                    reason="Overlapping access widths/offsets; register omitted",
                )
            )
            counts["registers"] += 1
            counts["planned_bits"] += planned.bit_count()
            counts["unknown_bits"] += planned.bit_count()
            counts["unsupported_bits"] += planned.bit_count()
            counts["untested_bits"] += planned.bit_count()
            continue
        full_mask = width_mask(width)
        ref = group[0].ref.model_copy(update={"field_mask": full_mask})
        planned = 0
        for c in group:
            if c.ref.space != ref.space or planned & c.ref.field_mask:
                raise ValueError("Overlapping classifications or mixed BAR spaces")
            planned |= c.ref.field_mask
        claims = [claim for c in group for claim in c.claims]
        active = [
            c for c in claims if c.state_epoch is None or c.state_epoch == state_epoch
        ]
        rw_mask = known = 0
        strength_masks: dict[str, int] = defaultdict(int)
        local_unsupported: list[UnsupportedItem] = []
        for claim in claims:
            reason = None
            if claim.semantic not in ("rw", "ro", "unknown"):
                reason = "BAR storage RTL cannot implement this semantic"
            elif claim.state_epoch is not None and state_epoch is None:
                reason = "Multiple epochs require explicit state selection"
            if reason is not None:
                local_unsupported.append(
                    UnsupportedItem(
                        ref=ref,
                        mask=claim.mask,
                        semantic=claim.semantic,
                        state_epoch=claim.state_epoch,
                        reason=reason,
                        experiment_ids=claim.experiment_ids,
                        observation_ids=claim.observation_ids,
                    )
                )
        for claim in active:
            strength_masks[claim.strength] |= claim.mask
            if claim.strength in ("measured", "documented") or (
                claim.strength == "inferred" and allow_inferred
            ):
                known |= claim.mask
                if claim.semantic == "rw":
                    rw_mask |= claim.mask
        # The shared schema separates reset_domain from first observations. No
        # reset is inferred here: accept only values explicitly carried by it.
        observed = {
            c.observed_initial_value
            for c in group
            if c.observed_initial_value is not None
        }
        resets = {
            (c.reset_value, c.reset_domain) for c in group if c.reset_value is not None
        }
        initial = next(iter(observed)) if len(observed) == 1 else None
        origin = "observed_initial_value" if initial is not None else None
        absent_reason: str | None = None
        if len(observed) > 1:
            absent_reason = "Conflicting observed initial values"
        elif initial is None and len(resets) == 1:
            initial = next(iter(resets))[0]
            origin = "explicit_reset_value"
        elif (
            initial is None
            and not resets
            and rw_mask == full_mask
            and offset in read_offsets
        ):
            # Every bit is driver-written storage, written before any read
            # and read back later (so it holds state; write-only doorbells
            # and set/clear registers are excluded). The donor's driver
            # never depended on the reset value; a target driver that reads
            # it first sees 0, not the hardware default. Served from 0 and
            # labelled, never presented as an observed value.
            initial = 0
            origin = "unobserved_written_before_read"
            counts["unobserved_reset_registers"] += 1
        elif initial is None:
            absent_reason = (
                "Conflicting reset values/domains"
                if len(resets) > 1
                else "No successful read or explicit reset value"
            )
        if initial is None:
            local_unsupported.append(
                UnsupportedItem(
                    ref=ref,
                    mask=full_mask,
                    reason=absent_reason
                    or "No successful read or explicit reset value",
                )
            )
        representable = ref.space == "mem_bar" and width in (1, 2, 4)
        if not representable:
            local_unsupported.append(
                UnsupportedItem(
                    ref=ref,
                    mask=full_mask,
                    reason=(
                        "Legacy BAR storage does not support I/O BARs "
                        "or 64-bit accesses"
                    ),
                )
            )
            rw_mask = 0
        unknown = full_mask & ~known
        # A register without a known initial value cannot be served; keep it
        # only as an unsupported gap so no consumer sees a null reset.
        if representable and initial is not None:
            registers[offset] = EvidenceRegisterSpec(
                offset=offset,
                width=width,
                reset=initial,
                rw_mask=rw_mask,
                hints={
                    "evidence": {
                        "classifications": [c.model_dump(mode="json") for c in group],
                        "state_epoch": state_epoch,
                        "initial_value_source": origin,
                    },
                    "initial_value_absent_reason": absent_reason,
                    "unknown_mask": unknown,
                    "unsupported": [
                        item.model_dump(mode="json") for item in local_unsupported
                    ],
                },
            )
        counts["registers"] += 1
        counts["planned_bits"] += planned.bit_count()
        covered = 0
        for strength in (
            "measured",
            "documented",
            "inferred",
            "observed",
            "ambiguous",
        ):
            mask = strength_masks[strength]
            counts[strength + "_bits"] += mask.bit_count()
            covered |= mask
        counts["untested_bits"] += (planned & ~covered).bit_count()
        counts["unknown_bits"] += (unknown & planned).bit_count()
        counts["writable_bits"] += rw_mask.bit_count()
        unsupported_mask = 0
        for item in local_unsupported:
            unsupported_mask |= item.mask
        counts["unsupported_bits"] += (unsupported_mask & planned).bit_count()
        unsupported.extend(local_unsupported)
    counts["emitted_registers"] = len(registers)
    return BarCompilationResult(
        model=BarModel(size=bar_size, registers=registers),
        unsupported=unsupported,
        coverage=CoverageCounts(**counts),
        state_epoch=state_epoch,
    )


def _initial_in_epoch(
    ref: RegisterRef, observations: Iterable[Observation], epoch: int | None
) -> int | None:
    """First successful full-width read of ``ref`` in the served state only.

    A native-driver (or otherwise earlier-state) read never becomes the served
    initial value of a later state. Only reads before any write to the same
    location count: after a write the value is what the driver set, not the
    state the device presents at power-on.
    """
    for obs in observations:
        if (
            obs.operation == "write"
            and obs.state_epoch == epoch
            and obs.ref.space == ref.space
            and obs.ref.bar_index == ref.bar_index
            and obs.ref.offset < ref.offset + ref.width
            and ref.offset < obs.ref.offset + obs.ref.width
        ):
            return None
        if (
            obs.operation == "read"
            and obs.completion == "ok"
            and obs.returned_value is not None
            and obs.phase != "exercise"
            and obs.state_epoch == epoch
            and obs.lane_mask == obs.ref.full_mask
            and (obs.ref.space, obs.ref.bar_index, obs.ref.offset, obs.ref.width)
            == (ref.space, ref.bar_index, ref.offset, ref.width)
        ):
            return obs.returned_value
    return None


class LearningBuildBlocked(ValueError):
    """The capture's learning run left the donor in an unverified state.

    The capture stays loadable for inspection, but a normal build from it is
    refused: config space and BAR contents read after a failed recovery are not
    trustworthy donor state. Recollect the donor once it is restored.
    """

    def __init__(self, reasons: list[str]):
        self.reasons = reasons
        super().__init__(
            "register learning did not restore the donor; refusing to build "
            "from this capture (" + "; ".join(reasons) + "). Restore the donor "
            "driver binding and recollect."
        )


def learning_build_blockers(manifest: LearningManifest) -> list[str]:
    """Reasons a capture must not be built from; empty when it may be.

    Only donor integrity blocks: the run may have left the donor changed
    (a write without verified restoration, a failed driver rebind, or an
    earlier unrecovered session). Failures before any write (observer,
    quiesce, cancellation, timeouts that restored) are coverage gaps; they
    yield fewer learned registers, not a blocked build. Decided from the
    evidence itself, never from the derived status alone.
    """
    writes = {
        o.sequence_id
        for o in manifest.observations
        if o.operation == "write" and o.source == "collector"
    }
    reasons: list[str] = []
    for gap in manifest.gaps:
        detail = gap.detail or "no detail recorded"
        if gap.reason == "restoration_failed":
            reasons.append("restoration failed: " + detail)
        elif gap.reason == "precondition_failed" and detail.startswith(
            "unfinished journal"
        ):
            reasons.append("an earlier session was not recovered: " + detail)
        elif (
            gap.reason == "backend_unavailable"
            and detail.startswith("ownership/baseline/backend failed")
            and writes
        ):
            reasons.append("backend failed after writes: " + detail)
    for exp in manifest.experiments:
        wrote = exp.tested_mask > 0 or any(i in writes for i in exp.observation_ids)
        if wrote and exp.restoration != "verified":
            reasons.append(
                "experiment "
                + exp.experiment_id
                + " wrote but restoration is "
                + exp.restoration
                + ": "
                + (exp.restoration_detail or "no detail recorded")
            )
    return list(dict.fromkeys(reasons))


def compile_manifest_bar_models(
    manifest: LearningManifest,
    bars: Iterable[tuple[int, int]],
    *,
    diagnostic: bool = False,
    include_empty: bool = False,
) -> dict[int, BarCompilationResult]:
    """The Stage 2 trust boundary for learned BAR models.

    Stored classifications are not trusted: they are re-derived from the
    manifest's raw observations and experiments (with their recorded recipe
    declarations). Initial values come only from the served
    state. ``bars`` is ``(index, size)`` for each memory BAR. BARs whose model
    has no emitted registers are left out unless ``include_empty`` (for the
    report: their unsupported behaviors must still be surfaced).

    Raises :class:`LearningBuildBlocked` when the run did not restore the donor,
    unless ``diagnostic`` (inspection only, never for generation).
    """
    if not diagnostic:
        blockers = learning_build_blockers(manifest)
        if blockers:
            raise LearningBuildBlocked(blockers)
    from pcileechfwgenerator.device_clone.register_classifier import (
        classify_registers,
    )

    recipes = list(manifest.recipes)
    qualified = {r.experiment_id for r in recipes}
    observations = list(manifest.observations)
    base = classify_registers(observations, manifest.experiments, recipes)
    results: dict[int, BarCompilationResult] = {}
    for index, size in bars:
        if not any(c.ref.space == "mem_bar" and c.ref.bar_index == index for c in base):
            continue
        # Serve the state of this BAR's qualified experiments only. Unqualified,
        # config-space and other-BAR experiments do not choose the state.
        epochs = {
            exp.state_epoch
            for exp in manifest.experiments
            if exp.experiment_id in qualified
            and exp.ref.space == "mem_bar"
            and exp.ref.bar_index == index
        }
        if len(epochs) > 1:
            logger.warning(
                "BAR%d evidence spans %d states; no state selected, no writes granted",
                index,
                len(epochs),
            )
            epoch: int | None = None
        elif epochs:
            epoch = next(iter(epochs))
        else:
            # Passive evidence only: serve the latest state that was observed.
            seen = {
                o.state_epoch
                for o in observations
                if o.ref.space == "mem_bar" and o.ref.bar_index == index
            }
            epoch = max(seen) if seen else None
        # A lost event could be a write that preceded a read: an incomplete
        # trace cannot tell reset state from a value the driver programmed.
        trusted = epoch is not None and manifest.dropped_events == 0
        classifications = [
            c.model_copy(
                update={
                    "observed_initial_value": (
                        _initial_in_epoch(c.ref, observations, epoch)
                        if trusted
                        else None
                    )
                }
            )
            for c in base
        ]
        result = compile_bar_model(
            classifications,
            index,
            size,
            state_epoch=epoch,
            # A lost event could be the write that explains a change, or a
            # contradicting read: incomplete traces never grant writes.
            allow_inferred=manifest.dropped_events == 0,
            read_offsets=frozenset(
                o.ref.offset
                for o in observations
                if o.operation == "read"
                and o.ref.space == "mem_bar"
                and o.ref.bar_index == index
                and o.state_epoch == epoch
            ),
        )
        if include_empty or result.model.registers:
            results[index] = result
    return results


def servable_models(results: dict[int, BarCompilationResult]) -> dict[int, BarModel]:
    """Models that may be served: only those with emitted registers."""
    return {i: r.model for i, r in results.items() if r.model.registers}


def compilation_report(results: dict[int, BarCompilationResult]) -> dict[str, dict]:
    """JSON-safe per-BAR summary of what was learned and what was left out.

    Carried into the template context and build output so omitted semantics
    (W1C, actions, registers without an initial value) are a visible decision.
    """
    report: dict[str, dict] = {}
    for index, result in sorted(results.items()):
        report[str(index)] = {
            "state_epoch": result.state_epoch,
            "coverage": result.coverage.model_dump(),
            "unsupported": [
                {
                    "offset": item.ref.offset,
                    "width": item.ref.width,
                    "mask": item.mask,
                    "semantic": item.semantic,
                    "reason": item.reason,
                }
                for item in result.unsupported
            ],
        }
    return report


# Served-BAR behavior when no learned model exists. Read-only is the default:
# it claims nothing the evidence does not show. Writable RAM is an opt-out.
BAR_FALLBACK_READONLY = "readonly_zero"
BAR_FALLBACK_WRITABLE = "generic_ram"
BAR_FALLBACKS = (BAR_FALLBACK_READONLY, BAR_FALLBACK_WRITABLE)


def bar_fallback_mode(bar_config: dict) -> str:
    mode = bar_config.get("bar_fallback", BAR_FALLBACK_READONLY)
    if mode not in BAR_FALLBACKS:
        raise ValueError(f"unknown bar_fallback {mode!r}")
    return mode


def served_bar_fidelity(bar_config: dict) -> str:
    """What the served BAR emulates.

    ``learned`` / ``learned_partial`` when the served BAR has a non-empty
    model (resolved from the same keys the overlay generator serves from),
    otherwise the fallback: ``readonly_zero`` (default) or ``generic_ram``
    (explicit opt-out).
    """
    served = bar_config.get("served_bar_index", 0)
    models = bar_config.get("bar_models") or {}
    model = models.get(served, models.get(str(served)))
    if not (model and (model.get("regs") or model.get("registers"))):
        return bar_fallback_mode(bar_config)
    report = (bar_config.get("bar_model_report") or {}).get(str(served)) or {}
    return "learned_partial" if report.get("unsupported") else "learned"
