"""Mandatory Stage 1 learning lifecycle, with no production write backend.

All I/O is injected. Observers are synchronous, bounded workload adapters which
stop their capture before returning. Ownership factories must use the existing
binder/group lock; an already-owned session never acquires another binder.
"""

from __future__ import annotations

import os
import sys
import threading
import time
from collections.abc import Callable
from pathlib import Path
from typing import ContextManager, Literal
from uuid import uuid4

from pcileechfwgenerator.device_clone.register_classifier import (
    RecipeDeclaration,
    classify_registers,
)
from pcileechfwgenerator.device_clone.register_evidence import (
    CoverageGap,
    DonorIdentity,
    ExperimentResult,
    GapReason,
    LearningContext,
    LearningManifest,
    Observation,
    RegisterRef,
)
from pcileechfwgenerator.exceptions import PCILeechError, VFIOBindError
from pcileechfwgenerator.host_collect.register_access import RegisterAccessBackend
from pcileechfwgenerator.host_collect.register_experiments import (
    RESOLVE_HINT,
    ExperimentJournal,
    ExperimentRecipe,
    run_experiment,
)
from pcileechfwgenerator.host_collect.register_observer import ObserverResult
from pcileechfwgenerator.host_collect.snapshot import device_identity, normalize_bdf
from pcileechfwgenerator.log_config import get_logger
from pydantic import BaseModel, ConfigDict, Field, ValidationError

logger = get_logger(__name__)

# These are operational failures at injectable boundaries. Programming errors
# (TypeError, AttributeError, AssertionError, etc.) propagate after cleanup.
_OPERATIONAL_ERRORS = (OSError, RuntimeError, ValueError, PCILeechError, VFIOBindError)
# The shared schema requires a register for even session-wide gaps. This is a
# locator for the donor identity header, NOT a claimed measurement of its bits.
_SESSION_REF = RegisterRef(space="config", offset=0, width=4, field_mask=0xFFFFFFFF)


class LearningResource(BaseModel):
    """Discovered BAR extent, without invented access capabilities."""

    model_config = ConfigDict(extra="forbid", frozen=True, strict=True)
    index: int = Field(ge=0, le=5)
    size: int = Field(gt=0)
    space: Literal["mem_bar", "io_bar"] = "mem_bar"


class LearningDiscovery(BaseModel):
    """Read-only discovery supplied by the host collector or a fixture."""

    model_config = ConfigDict(extra="forbid", frozen=True, strict=True)
    donor: DonorIdentity
    resources: tuple[LearningResource, ...] = ()
    resources_absent_reason: str | None = "BAR resources not supplied"
    driver_name: str | None = None
    binding_absent_reason: str | None = "native binding not established"
    kernel_version: str | None = None
    trace_facilities: tuple[str, ...] = ()
    access_backends: tuple[str, ...] = ()

    @classmethod
    def from_config(
        cls, config: bytes, *, resources: tuple[LearningResource, ...] = ()
    ) -> LearningDiscovery:
        """Reuse the snapshot identity accessor; never supply default donor IDs."""
        return cls(
            donor=DonorIdentity(**device_identity(config)),
            resources=resources,
            resources_absent_reason=None if resources else "BAR extents unavailable",
        )


class MeasurementPlan(BaseModel):
    """Explicit supported plan; an empty plan is a coverage gap, not success."""

    model_config = ConfigDict(extra="forbid", frozen=True, strict=True)
    discovery: LearningDiscovery
    experiments: tuple[ExperimentRecipe, ...] = ()
    passive_supported: bool
    exclusive_access: bool
    backend_supported: bool


class RegisterLearningSession:
    """Single-use lifecycle. Allocate identity before discovery or binding.

    ``collect_baseline(epoch)`` collects the host snapshot under ownership. Each
    executor additionally reads its own field baseline in that new epoch.
    ``already_owned`` transfers neither lock nor cleanup ownership: observation
    is skipped and the caller remains responsible for releasing its session.
    The default plan is empty; merely providing a backend never enables writes.
    """

    def __init__(
        self,
        bdf: str,
        *,
        discover: Callable[[], LearningDiscovery],
        journal_directory: Path,
        passive_observer: (
            Callable[[LearningDiscovery, int], ObserverResult] | None
        ) = None,
        quiesce: Callable[[], None] | None = None,
        ownership: Callable[[], ContextManager[object]] | None = None,
        already_owned: bool = False,
        collect_baseline: Callable[[int], None] | None = None,
        backend_factory: Callable[[], RegisterAccessBackend] | None = None,
        experiment_plan: tuple[ExperimentRecipe, ...] = (),
        clock: Callable[[], int] = time.monotonic_ns,
        cancel: threading.Event | None = None,
    ) -> None:
        self.capture_id = uuid4()
        self.bdf = normalize_bdf(bdf)
        self.discover = discover
        self.journal_directory = Path(journal_directory)
        self.passive_observer = passive_observer
        self.quiesce = quiesce
        self.ownership = ownership
        self.already_owned = already_owned
        self.collect_baseline = collect_baseline
        self.backend_factory = backend_factory
        self.experiment_plan = experiment_plan
        self.clock = clock
        self.cancel = cancel
        self.plan: MeasurementPlan | None = None
        self._ran = False
        self._gaps: list[CoverageGap] = []
        self._failed = False
        self._observations: list[Observation] = []
        self._experiments: list[ExperimentResult] = []
        self._declarations: list[RecipeDeclaration] = []
        self._dropped = 0
        self._capture_notes: list[str] = []

    def _gap(
        self,
        reason: GapReason,
        detail: str,
        ref: RegisterRef = _SESSION_REF,
        *,
        failed: bool = False,
    ) -> None:
        self._gaps.append(CoverageGap(ref=ref, reason=reason, detail=detail))
        self._failed |= failed

    def _cancelled(self) -> bool:
        if self.cancel is not None and self.cancel.is_set():
            self._gap("precondition_failed", "session cancelled", failed=True)
            return True
        return False

    def _measure(self) -> None:
        # Epoch zero belongs exclusively to pre-bind/native observations.
        epoch = 1
        plan = self.plan
        if plan is None:
            raise RuntimeError("measurement requires a plan")
        if self._cancelled():
            return
        if self.collect_baseline is not None:
            self.collect_baseline(epoch)
        # Passive native-driver observation is the learning path. Active
        # experiments run only when a caller supplies both a plan and a backend.
        if not self.experiment_plan or self.backend_factory is None:
            return
        if not (self.ownership is not None or self.already_owned):
            self._gap("precondition_failed", "experiments require exclusive ownership")
            return
        if self._cancelled():
            return
        backend = self.backend_factory()
        for recipe in self.experiment_plan:
            if self._cancelled():
                break
            journal = ExperimentJournal(
                self.journal_directory,
                capture_id=self.capture_id,
                bdf=self.bdf,
                donor=plan.discovery.donor,
            )
            observations, result = run_experiment(
                backend,
                recipe,
                state_epoch=epoch,
                sequence_start=len(self._observations),
                clock=self.clock,
                cancel=self.cancel,
                journal=journal,
            )
            self._observations.extend(observations)
            self._experiments.append(result)
            # ExperimentRecipe is specifically a reversible storage contract;
            # never infer W1C or action semantics from a recipe's name.
            self._declarations.append(
                RecipeDeclaration(
                    experiment_id=result.experiment_id,
                    recipe_id=recipe.recipe_id,
                    recipe_version=recipe.version,
                    kind="storage",
                    trace_complete=True,
                    settling_reads=recipe.readback_reads,
                )
            )
            if result.termination != "completed":
                self._gap(
                    "backend_rejected",
                    result.preconditions.get(
                        "executor.termination_detail", result.termination
                    ),
                    recipe.ref,
                    failed=True,
                )
            if result.restoration == "failed":
                self._gap(
                    "restoration_failed",
                    result.restoration_detail,
                    recipe.ref,
                    failed=True,
                )
                break  # State is no longer safe for the next recipe.
            if result.restoration != "verified":
                self._gap(
                    "restoration_failed",
                    "recovery unverified; remaining plan stopped",
                    recipe.ref,
                    failed=True,
                )
                break
            if result.termination != "completed":
                break

    def run(self) -> LearningManifest:
        """Observe, acquire once, baseline/probe, release, then validate evidence.

        Discovery failures propagate: without identity a manifest cannot be
        truthfully constructed. Unfinished journals block the lifecycle and are
        reported for inspection, never replayed. Expected boundary failures are
        explicit failed manifests; unexpected errors propagate after release.
        """
        if self._ran:
            raise RuntimeError("learning sessions are single-use")
        self._ran = True
        discovery = self.discover()
        self.plan = MeasurementPlan(
            discovery=discovery,
            experiments=self.experiment_plan,
            passive_supported=self.passive_observer is not None
            and not self.already_owned,
            exclusive_access=self.ownership is not None or self.already_owned,
            backend_supported=self.backend_factory is not None,
        )
        context = LearningContext(
            capture_id=self.capture_id,
            donor=discovery.donor,
            bdf=self.bdf,
            driver_name=discovery.driver_name,
            kernel_version=discovery.kernel_version,
            backend_version="injected" if self.backend_factory else "unavailable",
            experiment_version="register-learning-v1",
        )
        try:
            unfinished = ExperimentJournal.find_unfinished(self.journal_directory)
        except OSError as error:
            self._gap(
                "precondition_failed", f"journal scan failed: {error}", failed=True
            )
            return self._finish(context)
        if unfinished:
            for item in unfinished:
                self._gap(
                    "precondition_failed",
                    f"unfinished journal; no replay: {item.path}: {item.reason}; "
                    + RESOLVE_HINT,
                    item.record.ref if item.record else _SESSION_REF,
                    failed=True,
                )
            return self._finish(context)
        if self._cancelled():
            return self._finish(context)
        if self.plan.passive_supported and self.passive_observer is not None:
            try:
                from pcileechfwgenerator.host_collect.mmiotrace import (
                    HostRestorationError,
                    ObserverUnavailable,
                )

                result = self.passive_observer(discovery, 0)
                self._dropped = result.dropped_events
                # Parser IDs can restart for each capture. Normalize the combined
                # stream before giving the executor its next free sequence ID.
                self._observations.extend(
                    obs.model_copy(update={"sequence_id": i, "state_epoch": 0})
                    for i, obs in enumerate(result.observations)
                )
                if result.dropped_events:
                    self._gap(
                        "trace_incomplete",
                        "; ".join(["passive observer dropped events", *result.notes]),
                    )
                else:
                    self._capture_notes.extend(result.notes)
                self._capture_notes.extend(
                    f"exercise ran: {name}" for name in result.exercises_run
                )
                for excluded in result.excluded_ranges:
                    self._gap("excluded_by_policy", excluded)
                for change in result.host_changes:
                    self._gap("host_state_changed", change)
                for skipped in result.exercises_skipped:
                    self._gap("exercise_skipped", skipped)
                if result.reset_performed is False:
                    self._gap(
                        "initial_state_unverified",
                        "function was not reset before the traced probe; "
                        "initial values reflect the state the driver left",
                    )
            except HostRestorationError as error:
                # The host may be degraded (tracer on, CPUs offline, donor
                # unbound): block the build and stop touching the device.
                self._gap(
                    "restoration_failed", f"passive observation: {error}", failed=True
                )
                return self._finish(context)
            except ObserverUnavailable as error:
                # Not a failure: nothing was changed, the build serves the
                # read-only fallback for unobserved registers.
                self._gap("backend_unavailable", f"passive observation: {error}")
            except _OPERATIONAL_ERRORS as error:
                self._gap(
                    "backend_unavailable", f"observer failed: {error}", failed=True
                )
        else:
            self._gap(
                "backend_unavailable",
                (
                    "passive observation skipped: ownership already held"
                    if self.already_owned
                    else "no supported native-driver observer/workload"
                ),
            )
        try:
            if self.quiesce is not None:
                self.quiesce()
        except _OPERATIONAL_ERRORS as error:
            self._gap("precondition_failed", f"quiesce failed: {error}", failed=True)
            return self._finish(context)
        if self._cancelled():
            return self._finish(context)
        owner = None
        try:
            if self.ownership is not None and not self.already_owned:
                pending_owner = self.ownership()
                pending_owner.__enter__()
                owner = pending_owner
            self._measure()
        except _OPERATIONAL_ERRORS as error:
            self._gap(
                "backend_unavailable",
                f"ownership/baseline/backend failed: {error}",
                failed=True,
            )
        finally:
            if owner is not None:
                try:
                    owner.__exit__(*sys.exc_info())
                except _OPERATIONAL_ERRORS as error:
                    self._gap(
                        "restoration_failed",
                        f"binding release failed: {error}",
                        failed=True,
                    )
        return self._finish(context)

    def _finish(self, context: LearningContext) -> LearningManifest:
        classifications = classify_registers(
            self._observations, self._experiments, self._declarations
        )
        # The shared classification shape has one initial value, not one per
        # epoch. Prefer the new baseline where available; never initialize a
        # post-bind model from an older native-driver read of that register.
        for index, classification in enumerate(classifications):
            reads = [
                o
                for o in self._observations
                if o.state_epoch == 1
                and o.operation == "read"
                and o.completion == "ok"
                and o.returned_value is not None
                and o.lane_mask == o.ref.full_mask
                and o.ref.model_copy(update={"field_mask": o.ref.full_mask})
                == classification.ref
            ]
            active = any(
                exp.ref.model_copy(update={"field_mask": exp.ref.full_mask})
                == classification.ref
                for exp in self._experiments
            )
            if reads or active:
                classifications[index] = classification.model_copy(
                    update={
                        "observed_initial_value": (
                            reads[0].returned_value if reads else None
                        )
                    }
                )
        for recipe in self.experiment_plan:
            known = 0
            for classification in classifications:
                if (
                    classification.ref.space,
                    classification.ref.bar_index,
                    classification.ref.offset,
                    classification.ref.width,
                ) == (
                    recipe.ref.space,
                    recipe.ref.bar_index,
                    recipe.ref.offset,
                    recipe.ref.width,
                ):
                    known |= classification.ref.full_mask ^ classification.unknown_mask(
                        1
                    )
            if recipe.probe_mask & ~known:
                self._gap(
                    "ambiguous",
                    "planned bits remain unestablished in baseline epoch",
                    recipe.ref,
                )
        fields = dict(
            context=context,
            observations=tuple(self._observations),
            experiments=tuple(self._experiments),
            classifications=tuple(classifications),
            recipes=tuple(self._declarations),
            gaps=tuple(self._gaps),
            dropped_events=self._dropped,
            capture_notes=tuple(self._capture_notes),
            planned=tuple(
                recipe.ref.model_copy(update={"field_mask": recipe.probe_mask})
                for recipe in self.experiment_plan
            ),
        )
        if self._failed:
            return LearningManifest(status="failed", **fields)
        partial = LearningManifest(status="partial", **fields)
        try:
            return LearningManifest(status="complete", **fields)
        except ValidationError:
            # The partial manifest already passed every integrity check. Only
            # the complete-status invariant differs in the second validation.
            return partial


JOURNAL_DIR_ENV = "PCILEECH_LEARNING_JOURNAL_DIR"
_SYSTEM_JOURNAL_DIR = Path("/var/lib/pcileechfwgenerator/register-learning-journals")


def default_journal_directory() -> Path:
    """One journal location per host, shared by every collection entry point.

    Unresolved recovery incidents must block every later session on this host,
    whatever output or datastore directory it uses. Root
    (the only user that can do VFIO) uses a fixed system path; others use
    their state directory. ``PCILEECH_LEARNING_JOURNAL_DIR`` overrides both.
    """
    override = os.environ.get(JOURNAL_DIR_ENV)
    if override:
        return Path(override)
    if hasattr(os, "geteuid") and os.geteuid() == 0:
        return _SYSTEM_JOURNAL_DIR
    state = os.environ.get("XDG_STATE_HOME") or str(Path.home() / ".local" / "state")
    return Path(state) / "pcileechfwgenerator" / "register-learning-journals"


def run_register_learning(
    bdf: str,
    *,
    discover: Callable[[], LearningDiscovery],
    journal_directory: Path,
    passive_observer: Callable[[LearningDiscovery, int], ObserverResult] | None = None,
    quiesce: Callable[[], None] | None = None,
    ownership: Callable[[], ContextManager[object]] | None = None,
    already_owned: bool = False,
    collect_baseline: Callable[[int], None] | None = None,
    backend_factory: Callable[[], RegisterAccessBackend] | None = None,
    experiment_plan: tuple[ExperimentRecipe, ...] = (),
    clock: Callable[[], int] = time.monotonic_ns,
    cancel: threading.Event | None = None,
) -> LearningManifest:
    """Run the shared lifecycle; compatibility learning flags never disable it."""
    return RegisterLearningSession(
        bdf,
        discover=discover,
        journal_directory=journal_directory,
        passive_observer=passive_observer,
        quiesce=quiesce,
        ownership=ownership,
        already_owned=already_owned,
        collect_baseline=collect_baseline,
        backend_factory=backend_factory,
        experiment_plan=experiment_plan,
        clock=clock,
        cancel=cancel,
    ).run()
