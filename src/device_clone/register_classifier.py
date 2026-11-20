"""Pure register classification; no access backend or I/O.

Callers supply RecipeDeclaration objects alongside ExperimentResults. A declaration
binds an experiment ID and exact recipe ID/version to one supported probe kind:
``storage`` (independent reversible bits), ``w1c`` or ``volatile``. It explicitly
attests trace completeness. W1C declarations name control-read observation IDs;
a control must be a read immediately following another read, with the bit still
set, before the clear write. Recipe names alone never imply semantics.

Measured claims require completed experiments, verified restoration, collector
accesses established as physical, complete values, one state epoch and byte-lane
coverage. The executor owns preconditions, settling and restoration verification;
this module verifies the recorded transitions, not those external guarantees.
Storage RO requires unchanged readbacks after requests of BOTH 0 and 1. RW
requires actual accepted 0->1 AND 1->0 transitions, not writes of the current
value. Conflicting evidence remains ambiguous. Claims never cross epochs.
"""

from __future__ import annotations

from collections import defaultdict
from collections.abc import Iterable
from typing import TypeAlias

from pcileechfwgenerator.device_clone.register_evidence import (
    ExperimentResult,
    FieldClaim,
    Observation,
    RecipeRecord,
    RegisterClassification,
    RegisterRef,
)
from pcileechfwgenerator.log_config import get_logger

logger = get_logger(__name__)


# The persisted record and the classifier input are one contract.
RecipeDeclaration: TypeAlias = RecipeRecord


def _location(ref: RegisterRef) -> RegisterRef:
    return ref.model_copy(update={"field_mask": ref.full_mask})


def _overlaps(a: RegisterRef, b: RegisterRef) -> bool:
    return (
        a.space == b.space
        and a.bar_index == b.bar_index
        and a.offset < b.offset + b.width
        and b.offset < a.offset + a.width
    )


def _usable(obs: Observation) -> bool:
    value = obs.returned_value if obs.operation == "read" else obs.attempted_value
    return (
        obs.completion == "ok"
        and obs.source == "collector"
        and obs.mediation == "physical"
        and value is not None
    )


def _experiment_problem(
    exp: ExperimentResult,
    declaration: RecipeDeclaration | None,
    observations: tuple[Observation, ...],
    by_id: dict[int, Observation],
) -> str | None:
    if declaration is None:
        return "No recipe declaration; acceptance unknown"
    if (declaration.recipe_id, declaration.recipe_version) != (
        exp.recipe_id,
        exp.recipe_version,
    ):
        return "Recipe identity/version mismatch"
    if not declaration.trace_complete:
        return "Incomplete trace cannot establish transitions"
    if exp.termination != "completed" or exp.restoration != "verified":
        return "Experiment incomplete or restoration unverified"
    if not exp.observation_ids:
        return "Experiment has no observations"
    if len(set(exp.observation_ids)) != len(exp.observation_ids):
        return "Duplicate experiment observation references"
    if any(i not in by_id for i in exp.observation_ids):
        return "Experiment references missing observations"
    events = [by_id[i] for i in sorted(exp.observation_ids)]
    # Recipe flush reads target another register; they must be usable reads in
    # the same epoch but are not transition evidence for this register.
    flushes = [o for o in events if _location(o.ref) != _location(exp.ref)]
    if any(
        o.operation != "read"
        or _overlaps(o.ref, exp.ref)
        or o.state_epoch != exp.state_epoch
        or not _usable(o)
        for o in flushes
    ):
        return "Invalid flush access in experiment"
    events = _register_events(events, exp.ref)
    if not events:
        return "Experiment has no observations of the register"
    if any(
        _location(o.ref) != _location(exp.ref)
        or o.state_epoch != exp.state_epoch
        or not _usable(o)
        for o in events
    ):
        return "Invalid source, mediation, completion, value, location or epoch"
    if any(a.monotonic_ns > b.monotonic_ns for a, b in zip(events, events[1:])):
        return "Observation timestamps are not monotonic"
    if events[-1].monotonic_ns - events[0].monotonic_ns > exp.time_budget_ns:
        return "Observations exceed experiment time budget"
    included = set(exp.observation_ids)
    if any(
        events[0].sequence_id <= o.sequence_id <= events[-1].sequence_id
        and _overlaps(o.ref, exp.ref)
        and o.sequence_id not in included
        for o in observations
    ):
        return "Intervening register accesses omitted from experiment"
    if any(i not in included for i in declaration.control_read_ids):
        return "Control read is outside experiment"
    if exp.baseline_value is not None and (
        events[0].operation != "read"
        or (events[0].returned_value ^ exp.baseline_value)
        & exp.tested_mask
        & events[0].lane_mask
    ):
        return "Recorded baseline disagrees with collector read"
    return None


def _register_events(events: list[Observation], ref: RegisterRef) -> list[Observation]:
    """Experiment events at the register itself, excluding flush reads."""
    return [o for o in events if _location(o.ref) == _location(ref)]


def _transitions(
    events: list[Observation], settling: int
) -> tuple[list[tuple[Observation, Observation, list[Observation]]], set[int], bool]:
    """Split register events into write transitions.

    Each write pairs the read immediately before it with the declared settling
    window: exactly ``settling`` reads that follow it, the last one decisive.
    Returns the transitions, the indices of reads inside windows, and whether
    any write lacked a well-formed before-read or complete window.
    """
    transitions = []
    in_window: set[int] = set()
    malformed = False
    for index, event in enumerate(events):
        if event.operation != "write":
            continue
        window = events[index + 1 : index + 1 + settling]
        if (
            index == 0
            or events[index - 1].operation != "read"
            or len(window) != settling
            or any(o.operation != "read" for o in window)
        ):
            malformed = True
            continue
        transitions.append((events[index - 1], event, window))
        in_window.update(range(index + 1, index + 1 + settling))
    return transitions, in_window, malformed


def _covers(bit: int, *events: Observation) -> bool:
    mask = bit
    for event in events:
        mask &= event.lane_mask & event.ref.field_mask
    return bool(mask)


def _bit_semantic(
    events: list[Observation], declaration: RecipeDeclaration, bit: int
) -> tuple[str, str]:
    """Evaluate one tested bit, preserving ignored/accepted contradictions.

    Within a settling window the bit may move at most once, from its pre-write
    value to the decisive (last) value: delayed completion. A bounce inside a
    window, or any change between reads outside windows, is spontaneous.
    """
    accepted: set[int] = set()
    unchanged: set[int] = set()
    rejected_transition = False
    spontaneous = False
    clears = False
    failed_clear = False
    uncontrolled_clear = False
    controls: set[int] = set()
    transitions, in_window, malformed = _transitions(events, declaration.settling_reads)
    for index, (a, b) in enumerate(zip(events, events[1:])):
        if a.operation == b.operation == "read" and _covers(bit, a, b):
            before, after = bool(a.returned_value & bit), bool(b.returned_value & bit)
            if not (index in in_window and index + 1 in in_window):
                spontaneous |= before != after
            if before and after and b.sequence_id in declaration.control_read_ids:
                controls.add(b.sequence_id)
    for a, w, window in transitions:
        if not _covers(bit, a, w, *window):
            continue
        before = bool(a.returned_value & bit)
        requested = bool(w.attempted_value & bit)
        seen = [bool(o.returned_value & bit) for o in window]
        after = seen[-1]
        moved = next((i for i, v in enumerate(seen) if v != before), len(seen))
        if any(v != after for v in seen[moved:]):
            spontaneous = True  # bounced inside the settling window
            continue
        if before != requested and after == requested:
            accepted.add(int(requested))
        if before == after:
            unchanged.add(int(requested))
            rejected_transition |= before != requested
        if before and requested and not after:
            clears = True
            uncontrolled_clear |= not any(i <= a.sequence_id for i in controls)
        failed_clear |= before and requested and after
    if malformed:
        return "unknown", "Write without a complete declared settling window"
    if declaration.kind == "volatile":
        if spontaneous and all(o.operation == "read" for o in events):
            return "volatile", "Value changed in a controlled read-only experiment"
    elif declaration.kind == "w1c":
        if (
            clears
            and not failed_clear
            and not uncontrolled_clear
            and not spontaneous
            and not accepted
        ):
            return "w1c", "Clear after write-one with stable no-write control reads"
    elif not spontaneous:
        if accepted == {0, 1} and not rejected_transition and not clears:
            return "rw", "Both storage transitions accepted via collector readback"
        if unchanged == {0, 1} and not accepted and not clears:
            return "ro", "Both zero and one requests left collector readback unchanged"
    return "unknown", "Incomplete or contradictory transitions; acceptance ambiguous"


def _passive_bit(events: list[Observation], bit: int) -> tuple[bool, str]:
    """Whether a native-driver trace is consistent with ``bit`` being storage.

    Any bit the driver writes is granted plain RW unless the trace
    contradicts it. Each driver write opens a window of the reads that
    follow it (until the next write); inside a window the bit may move once,
    to its final value (delayed completion). Contradictions:

    * a window settling on something other than the requested value (write
      ignored, reverted, or self-clearing / write-1-to-clear);
    * a bounce inside a window;
    * a change between reads with no write in between (hardware-driven).

    Write-once bits (never read back) are granted on the write alone: the
    driver's own access pattern shows it treats them as configuration. A
    command bit the driver never reads back is therefore served as storage.
    """
    written = verified = 0
    known: bool | None = None  # bit value last established by a read
    pending: tuple[bool | None, bool] | None = None
    window: list[bool] = []

    def close() -> str | None:
        nonlocal verified, known
        if pending is None:
            return None
        before, requested = pending
        if not window:
            known = None  # never read back: the current value is unknown
            return None
        final = window[-1]
        anchor = before if before is not None else window[0]
        moved = next((i for i, v in enumerate(window) if v != anchor), len(window))
        if any(v != final for v in window[moved:]):
            return "value bounced after a driver write"
        if final != requested:
            return "driver write was ignored or reverted"
        verified += 1
        return None

    for event in events:
        if not event.lane_mask & event.ref.field_mask & bit:
            continue
        if event.operation == "write":
            problem = close()
            if problem:
                return False, problem
            written += 1
            pending = (known, bool(event.attempted_value & bit))
            window = []
            continue
        value = bool(event.returned_value & bit)
        if pending is None:
            if known is not None and value != known:
                return False, "value changed without a driver write"
        else:
            window.append(value)
        known = value
    problem = close()
    if problem:
        return False, problem
    if not written:
        return False, "never written by the driver"
    if verified:
        return True, f"driver wrote the bit {written} time(s), {verified} read back"
    return True, f"driver wrote the bit {written} time(s), never read back"


def _passive_events(epoch_obs: list[Observation]) -> list[Observation]:
    """Complete, physical native-driver accesses; anything else voids inference."""
    usable = [
        o
        for o in epoch_obs
        if o.source == "native_driver"
        and o.mediation == "physical"
        and o.completion == "ok"
        and (o.returned_value if o.operation == "read" else o.attempted_value)
        is not None
    ]
    return usable if len(usable) == len(epoch_obs) else []


def classify_register(
    ref: RegisterRef,
    observations: Iterable[Observation],
    experiments: Iterable[ExperimentResult] = (),
    recipes: Iterable[RecipeDeclaration] = (),
) -> RegisterClassification:
    """Classify one field, keeping measured claims separate for every epoch.

    Duplicate event/experiment/declaration IDs are malformed input (ValueError).
    Failed or insufficient experiments produce explicit ambiguous claims. Absent
    initial values remain None; their reason is retained in the unknown claim.
    """
    observations = tuple(sorted(observations, key=lambda o: o.sequence_id))
    experiments = tuple(experiments)
    recipes = tuple(recipes)
    by_id = {o.sequence_id: o for o in observations}
    if len(by_id) != len(observations):
        raise ValueError("Duplicate observation sequence IDs")
    if len({e.experiment_id for e in experiments}) != len(experiments):
        raise ValueError("Duplicate experiment IDs")
    declarations = {r.experiment_id: r for r in recipes}
    if len(declarations) != len(recipes):
        raise ValueError("Duplicate recipe declarations")
    relevant = [o for o in observations if _location(o.ref) == _location(ref)]
    initial = next(
        (
            o.returned_value
            for o in relevant
            if o.operation == "read"
            and o.completion == "ok"
            and o.returned_value is not None
            and o.lane_mask == ref.full_mask
        ),
        None,
    )
    # epoch -> bit -> candidate claims; no union across states or semantics.
    candidates: dict[int, dict[int, list[FieldClaim]]] = defaultdict(
        lambda: defaultdict(list)
    )
    for exp in experiments:
        if _location(exp.ref) != _location(ref):
            continue
        declaration = declarations.get(exp.experiment_id)
        if declaration is not None and declaration.kind == "reset":
            # Reset experiments support reset values, not field semantics.
            continue
        problem = _experiment_problem(exp, declaration, observations, by_id)
        mask = exp.tested_mask if problem is None else exp.requested_mask
        mask &= ref.field_mask
        events = _register_events(
            [by_id[i] for i in sorted(exp.observation_ids) if i in by_id], ref
        )
        for index in range(ref.width * 8):
            bit = 1 << index
            if not mask & bit:
                continue
            if problem is not None:
                semantic, note = "unknown", problem
            else:
                semantic, note = _bit_semantic(events, declaration, bit)
            candidates[exp.state_epoch][bit].append(
                FieldClaim(
                    mask=bit,
                    semantic=semantic,
                    strength="ambiguous" if semantic == "unknown" else "measured",
                    state_epoch=exp.state_epoch,
                    experiment_ids=(exp.experiment_id,),
                    observation_ids=tuple(o.sequence_id for o in events),
                    note=note,
                )
            )
    epochs = sorted({o.state_epoch for o in relevant} | set(candidates))
    claims: list[FieldClaim] = []
    for epoch in epochs:
        epoch_obs = [o for o in relevant if o.state_epoch == epoch]
        passive = _passive_events(epoch_obs) if not candidates[epoch] else []
        passive_note = "Observed accesses only; write acceptance unknown"
        if any(
            a.operation == b.operation == "read"
            and a.returned_value is not None
            and b.returned_value is not None
            and a.returned_value != b.returned_value
            for a, b in zip(epoch_obs, epoch_obs[1:])
        ):
            passive_note += "; values change without a write (possible volatility)"
        if initial is None:
            passive_note += "; initial value absent: no successful full-width read"
        groups: dict[tuple, int] = defaultdict(int)
        for index in range(ref.width * 8):
            bit = 1 << index
            if not ref.field_mask & bit:
                continue
            choices = candidates[epoch].get(bit, [])
            if choices:
                semantics = {c.semantic for c in choices}
                semantic = next(iter(semantics)) if len(semantics) == 1 else "unknown"
                strength = "ambiguous" if semantic == "unknown" else "measured"
                exp_ids = tuple(sorted({i for c in choices for i in c.experiment_ids}))
                obs_ids = tuple(sorted({i for c in choices for i in c.observation_ids}))
                note = "; ".join(sorted({c.note for c in choices}))
                if len(semantics) > 1:
                    note = "Contradictory results in the same epoch; " + note
            else:
                semantic, strength, exp_ids = "unknown", "observed", ()
                obs_ids = tuple(o.sequence_id for o in epoch_obs if o.lane_mask & bit)
                note = passive_note
                if not obs_ids:
                    strength, note = "untested", "No observations cover this bit"
                elif passive and any(o.operation == "write" for o in passive):
                    storage, why = _passive_bit(passive, bit)
                    if storage:
                        semantic, strength, note = "rw", "inferred", why
                    else:
                        note = passive_note + "; " + why
            groups[(semantic, strength, exp_ids, obs_ids, note)] |= bit
        for (semantic, strength, exp_ids, obs_ids, note), mask in groups.items():
            claims.append(
                FieldClaim(
                    mask=mask,
                    semantic=semantic,
                    strength=strength,
                    state_epoch=epoch,
                    experiment_ids=exp_ids,
                    observation_ids=obs_ids,
                    note=note,
                )
            )
    if not epochs:
        claims.append(
            FieldClaim(
                mask=ref.field_mask,
                semantic="unknown",
                strength="untested",
                note="Initial value absent: no observations or experiments",
            )
        )
    return RegisterClassification(
        ref=ref, claims=tuple(claims), observed_initial_value=initial
    )


def classify_registers(
    observations: Iterable[Observation],
    experiments: Iterable[ExperimentResult] = (),
    recipes: Iterable[RecipeDeclaration] = (),
) -> list[RegisterClassification]:
    """Classify all referenced registers; full-width gaps remain unknown."""
    observations, experiments, recipes = (
        tuple(observations),
        tuple(experiments),
        tuple(recipes),
    )
    refs = {_location(item.ref) for item in (*observations, *experiments)}
    return [
        classify_register(ref, observations, experiments, recipes)
        for ref in sorted(
            refs,
            key=lambda r: (
                r.space,
                r.bar_index if r.bar_index is not None else -1,
                r.offset,
                r.width,
                repr(r.capability),
            ),
        )
    ]
