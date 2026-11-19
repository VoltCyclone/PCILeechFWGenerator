"""Bounded, journaled storage experiments returning raw evidence only.

Recipes are reviewed field contracts, not permission to probe arbitrary device
registers. The caller owns binding, exclusive access and device-state assurance.
The injected clock returns monotonic integer nanoseconds. Budgets are checked
between calls; backends must independently bound individual synchronous calls.
"""

from __future__ import annotations

import os
import tempfile
import threading
from collections.abc import Callable
from datetime import datetime, timezone
from pathlib import Path
from typing import Literal
from uuid import UUID, uuid4

from pcileechfwgenerator.device_clone.register_evidence import (
    DonorIdentity,
    ExperimentResult,
    ExperimentTermination,
    Observation,
    Operation,
    RegisterRef,
    Restoration,
)
from pcileechfwgenerator.host_collect.register_access import (
    AccessResult,
    AccessValidationError,
    RegisterAccessBackend,
    validate_access,
)
from pcileechfwgenerator.log_config import get_logger
from pydantic import BaseModel, ConfigDict, Field, model_validator

logger = get_logger(__name__)


class _Contract(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True, strict=True)


class ExperimentRecipe(_Contract):
    """Reviewed reversible storage probe with exhaustive neighboring-bit policy.

    The three masks partition the entire transaction. Preserve bits use a fresh
    pre-write read; write-zero bits (including W1C/action neighbors) are never
    replayed from baseline. Unselected probe bits also retain their fresh value.
    ``readback_reads`` is a fixed settling sequence, not polling for the desired
    value: ignored writes must remain valid raw evidence. Optional flush reads
    precede that sequence. All these reads must be safe under the field contract.

    max_steps/time_budget_ns bound probe accesses; restoration has a separate
    allowance and ignores cancellation. Required state/ownership assertions in
    preconditions are supplied by the caller, not inferred by this executor.
    """

    recipe_id: str = Field(min_length=1, max_length=128)
    version: str = Field(min_length=1, max_length=64)
    ref: RegisterRef
    legal_widths: tuple[int, ...]
    probe_mask: int = Field(gt=0)
    preserve_value_mask: int = Field(ge=0)
    write_zero_mask: int = Field(ge=0)
    readback_reads: int = Field(default=1, ge=1, le=100)
    flush_ref: RegisterRef | None = None
    max_steps: int = Field(gt=0, le=100_000)
    time_budget_ns: int = Field(gt=0)
    repeat_count: int = Field(default=1, ge=1, le=1000)
    restoration_strategy: Literal["restore_probed_bits", "not_restorable"]
    restoration_max_steps: int = Field(default=16, ge=1, le=1000)
    restoration_time_budget_ns: int = Field(gt=0)
    required_state_epoch: int | None = Field(default=None, ge=0)
    preconditions: dict[str, str] = Field(default_factory=dict)

    @model_validator(mode="after")
    def _policy(self) -> ExperimentRecipe:
        if (
            not self.legal_widths
            or any(width not in (1, 2, 4, 8) for width in self.legal_widths)
            or len(set(self.legal_widths)) != len(self.legal_widths)
            or self.ref.width not in self.legal_widths
        ):
            raise ValueError("recipe must permit the exact register width")
        if self.probe_mask & ~self.ref.field_mask:
            raise ValueError("probe mask exceeds the applicable field")
        masks = (self.probe_mask, self.preserve_value_mask, self.write_zero_mask)
        combined = 0
        for mask in masks:
            if mask & ~self.ref.full_mask or mask & combined:
                raise ValueError("write policy masks overlap or exceed register width")
            combined |= mask
        if combined != self.ref.full_mask:
            raise ValueError("every register bit needs an explicit write policy")
        recovery_steps = 2 + self.readback_reads + (self.flush_ref is not None)
        if (
            self.restoration_strategy == "restore_probed_bits"
            and self.restoration_max_steps < recovery_steps
        ):
            raise ValueError("restoration budget cannot fit its recovery sequence")
        if any(key.startswith("executor.") for key in self.preconditions):
            raise ValueError("executor. precondition keys are reserved")
        for ref in (self.ref, self.flush_ref):
            if ref is not None and _forbidden_config_ref(ref):
                raise ValueError("recipe targets a config field that is never probed")
            # Capability-relative denylist entries need the capability identity;
            # a bare offset in capability space could hide PMCSR or Link Control.
            if (
                ref is not None
                and ref.space == "config"
                and ref.offset >= 0x40
                and ref.capability is None
            ):
                raise ValueError("config recipe in capability space must name it")
        # Write-zero bits are driven to 0 on every probe and restore write, so
        # forbidden bits may only be preserved, never probed or zeroed.
        if _forbidden_config_bits(self.ref) & (self.probe_mask | self.write_zero_mask):
            raise ValueError(
                "recipe probes or zeroes a decode, bus-master, power-state, "
                "reset or link-control bit"
            )
        return self


# Config ranges no recipe may touch at all: moving a BAR, bridge window or ROM
# decode under a live kernel mapping can hang the host before restoration runs.
_FORBIDDEN_CONFIG_RANGES = ((0x10, 0x3C),)
# Command (0x04): I/O space, memory space, bus master enables.
_COMMAND_OFFSET, _COMMAND_FORBIDDEN = 0x04, 0x0007
_PM_CAP_ID, _PMCSR_RELATIVE, _PMCSR_POWER_STATE = 0x01, 0x04, 0x0003
# PCI Express capability: Device Control Initiate FLR (bit 15); Link Control
# Link Disable (bit 4) and Retrain Link (bit 5).
_PCIE_CAP_ID = 0x10
_PCIE_FORBIDDEN = ((0x08, 0x8000), (0x10, 0x0030))


def _forbidden_config_ref(ref: RegisterRef) -> bool:
    if ref.space != "config":
        return False
    end = ref.offset + ref.width
    return any(ref.offset < hi and lo < end for lo, hi in _FORBIDDEN_CONFIG_RANGES)


def _forbidden_config_bits(ref: RegisterRef) -> int:
    """Bits of ``ref`` (as a register mask) a recipe must never probe."""
    if ref.space != "config":
        return 0
    bits = 0
    targets = [(_COMMAND_OFFSET, _COMMAND_FORBIDDEN)]
    cap = ref.capability
    if cap is not None and cap.kind == "standard" and cap.cap_id == _PM_CAP_ID:
        targets.append((cap.base_offset + _PMCSR_RELATIVE, _PMCSR_POWER_STATE))
    if cap is not None and cap.kind == "standard" and cap.cap_id == _PCIE_CAP_ID:
        targets.extend((cap.base_offset + rel, mask) for rel, mask in _PCIE_FORBIDDEN)
    for offset, mask in targets:
        shift = offset - ref.offset
        if 0 <= shift < ref.width:
            bits |= (mask << (8 * shift)) & ref.full_mask
        elif -2 < shift < 0:  # 16-bit field starting one byte before ref
            bits |= (mask >> (8 * -shift)) & ref.full_mask
    return bits


class JournalRecord(_Contract):
    """Persisted intent and final outcome for a single experiment session."""

    schema_version: Literal[1] = 1
    capture_id: UUID
    session_id: UUID
    recipe: ExperimentRecipe
    ref: RegisterRef
    baseline: int = Field(ge=0)
    state_epoch: int = Field(ge=0)
    started_at: str
    started_monotonic_ns: int = Field(ge=0)
    # Which donor was touched, so an unresolved incident can be judged without
    # the capture. Optional only so journals written before this field load.
    bdf: str | None = None
    donor: DonorIdentity | None = None
    # "complete" means execution ended. It does not mean the donor was
    # restored: see needs_recovery(). "resolved" is an explicit operator
    # acknowledgement that the donor was restored out of band.
    status: Literal["unfinished", "complete", "resolved"] = "unfinished"
    result: ExperimentResult | None = None
    resolved_at: str | None = None
    resolution_note: str | None = None

    @model_validator(mode="after")
    def _integrity(self) -> JournalRecord:
        if self.ref != self.recipe.ref or self.baseline > self.ref.full_mask:
            raise ValueError("journal reference or baseline is invalid")
        if self.status == "unfinished" and self.result is not None:
            raise ValueError("only ended journals carry a result")
        if self.status == "complete" and self.result is None:
            raise ValueError("a complete journal carries its result")
        if (self.status == "resolved") != (
            self.resolved_at is not None and bool(self.resolution_note)
        ):
            raise ValueError("resolution requires a timestamp and a note")
        if self.result is not None and (
            self.result.experiment_id != str(self.session_id)
            or self.result.ref != self.ref
            or self.result.state_epoch != self.state_epoch
            or self.result.baseline_value != self.baseline
            or self.result.recipe_id != self.recipe.recipe_id
            or self.result.recipe_version != self.recipe.version
        ):
            raise ValueError("journal result does not match its intent")
        return self

    def needs_recovery(self) -> bool:
        """True while this session may have left the donor changed.

        An unfinished journal may have written anything. An ended one is
        clear only when restoration was verified, or never attempted because
        nothing was written. ``tested_mask`` is not a write indicator: a run
        stopped after its first write tests no bits. Only an explicit
        resolution clears the rest.
        """
        if self.status == "resolved":
            return False
        if self.status == "unfinished" or self.result is None:
            return True
        return self.result.restoration not in ("verified", "not_attempted")


class UnfinishedExperiment(_Contract):
    """An unfinished or unreadable journal; never an instruction to replay it."""

    path: Path
    record: JournalRecord | None
    reason: str


class ExperimentJournal:
    """Single-use durable journal. No hardware access and no automatic replay.

    Use one instance per experiment. A unique session name prevents collisions
    between concurrent collectors; an existing session is never overwritten.
    A completed record can still report failed restoration and needs inspection.
    """

    MAX_BYTES = 256 * 1024

    def __init__(
        self,
        directory: Path,
        *,
        capture_id: UUID,
        session_id: UUID | None = None,
        bdf: str | None = None,
        donor: DonorIdentity | None = None,
    ) -> None:
        self.directory = Path(directory)
        self.capture_id = capture_id
        self.bdf = bdf
        self.donor = donor
        self.session_id = session_id if session_id is not None else uuid4()
        self.path = self.directory / f"{self.session_id}.json"
        self._record: JournalRecord | None = None

    @staticmethod
    def _sync_directory(path: Path) -> None:
        directory_fd = os.open(path, os.O_RDONLY | os.O_DIRECTORY)
        try:
            os.fsync(directory_fd)
        finally:
            os.close(directory_fd)

    def _ensure_directory(self) -> None:
        missing = []
        path = self.directory
        while not path.exists():
            missing.append(path)
            path = path.parent
        self.directory.mkdir(parents=True, exist_ok=True)
        # Persist newly created directory entries as well as the journal file.
        for path in reversed(missing):
            self._sync_directory(path.parent)

    def _publish(self, record: JournalRecord) -> None:
        data = record.model_dump_json().encode("utf-8")
        if len(data) > self.MAX_BYTES:
            raise OSError("journal exceeds size limit")
        temporary: str | None = None
        try:
            with tempfile.NamedTemporaryFile(
                dir=self.directory, prefix=".journal-", delete=False
            ) as stream:
                temporary = stream.name
                stream.write(data)
                stream.flush()
                os.fsync(stream.fileno())
            os.replace(temporary, self.path)
            temporary = None
            self._sync_directory(self.directory)
        finally:
            if temporary is not None:
                Path(temporary).unlink(missing_ok=True)

    def begin(
        self,
        recipe: ExperimentRecipe,
        baseline: int,
        *,
        state_epoch: int,
        started_monotonic_ns: int,
    ) -> None:
        """Durably record intent before any write; refuse reuse of a session."""
        if self._record is not None:
            raise FileExistsError("journal session has already started")
        self._ensure_directory()
        record = JournalRecord(
            capture_id=self.capture_id,
            session_id=self.session_id,
            recipe=recipe,
            ref=recipe.ref,
            baseline=baseline,
            state_epoch=state_epoch,
            started_at=datetime.now(timezone.utc).isoformat(),
            started_monotonic_ns=started_monotonic_ns,
            bdf=self.bdf,
            donor=self.donor,
        )
        # Exclusive reservation prevents two instances reusing a session ID.
        # A crash before publication leaves an unreadable journal, also reported.
        with self.path.open("xb"):
            pass
        self._publish(record)
        self._record = record

    def complete(self, result: ExperimentResult) -> None:
        """Durably mark the run ended, including its actual recovery outcome."""
        if self._record is None:
            raise ValueError("journal has not started")
        if result.experiment_id != str(self.session_id):
            raise ValueError("result belongs to another journal session")
        record = JournalRecord.model_validate(
            self._record.model_copy(update={"status": "complete", "result": result})
        )
        self._publish(record)
        self._record = record

    @staticmethod
    def resolve(path: Path, *, note: str, force: bool = False) -> JournalRecord:
        """Operator acknowledgement that the donor was restored out of band.

        Never replays or performs any restoration write; it only records that
        a human verified the device (e.g. after a power cycle or driver
        rebind). Unreadable journals cannot be resolved: inspect and remove
        them by hand. An ``unfinished`` journal may belong to a session that
        is still running, so it needs ``force``. The record is only replaced if
        the file is unchanged since it was read.
        """
        if not note.strip():
            raise ValueError("a resolution note is required")
        path = Path(path)

        def read() -> bytes:
            with path.open("rb") as stream:
                data = stream.read(ExperimentJournal.MAX_BYTES + 1)
            if len(data) > ExperimentJournal.MAX_BYTES:
                raise ValueError("journal exceeds size limit")
            return data

        data = read()
        record = JournalRecord.model_validate_json(data)
        if path.stem != str(record.session_id):
            raise ValueError("journal file name does not match its session")
        if record.status == "resolved":
            return record
        if record.status == "unfinished" and not force:
            raise ValueError(
                "journal is unfinished; its session may still be running. "
                "Stop all collectors, then resolve with force"
            )
        resolved = JournalRecord.model_validate(
            record.model_copy(
                update={
                    "status": "resolved",
                    "resolved_at": datetime.now(timezone.utc).isoformat(),
                    "resolution_note": note.strip(),
                }
            ).model_dump()
        )
        if read() != data:
            raise ValueError("journal changed while resolving; inspect it again")
        journal = ExperimentJournal(
            path.parent, capture_id=record.capture_id, session_id=record.session_id
        )
        journal._publish(resolved)
        logger.warning(
            "Journal %s marked resolved by operator; no writes were replayed",
            path.name,
        )
        return resolved

    @staticmethod
    def find_unfinished(directory: Path) -> list[UnfinishedExperiment]:
        """Sessions that block new writes: unfinished, unreadable, or ended
        without verified recovery. Inspect without replay; corrupt files are
        results, scan errors propagate."""
        found = []
        try:
            paths = sorted(
                path for path in Path(directory).iterdir() if path.suffix == ".json"
            )
        except FileNotFoundError:
            return []
        for path in paths:
            try:
                with path.open("rb") as stream:
                    data = stream.read(ExperimentJournal.MAX_BYTES + 1)
                if len(data) > ExperimentJournal.MAX_BYTES:
                    raise ValueError("journal exceeds size limit")
                record = JournalRecord.model_validate_json(data)
            except (OSError, ValueError) as error:
                found.append(
                    UnfinishedExperiment(path=path, record=None, reason=str(error))
                )
                continue
            if record.status == "unfinished":
                found.append(
                    UnfinishedExperiment(path=path, record=record, reason="unfinished")
                )
            elif record.result is not None and record.needs_recovery():
                result = record.result
                found.append(
                    UnfinishedExperiment(
                        path=path,
                        record=record,
                        reason=(
                            "recovery unresolved: restoration "
                            + result.restoration
                            + (
                                ": " + result.restoration_detail
                                if result.restoration_detail
                                else ""
                            )
                        ),
                    )
                )
        return found


class _Stop(Exception):
    def __init__(self, termination: ExperimentTermination, detail: str) -> None:
        self.termination = termination
        self.detail = detail
        super().__init__(detail)


class _Runner:
    def __init__(
        self,
        backend: RegisterAccessBackend,
        recipe: ExperimentRecipe,
        state_epoch: int,
        sequence_start: int,
        clock: Callable[[], int],
        cancel: threading.Event | None,
        journal: ExperimentJournal,
    ) -> None:
        self.backend = backend
        self.recipe = recipe
        self.state_epoch = state_epoch
        self.sequence_start = sequence_start
        self.clock = clock
        self.cancel = cancel
        self.journal = journal
        self.observations: list[Observation] = []
        self.started = clock()
        self.deadline = self.started + recipe.time_budget_ns
        self.steps = 0
        self.step_limit = recipe.max_steps
        self.recovering = False
        self.baseline: int | None = None
        self.touched_mask = 0
        self.tested_mask = 0
        self.journal_started = False

    def _guard(self, *, check_steps: bool = True) -> None:
        if not self.recovering and self.cancel is not None and self.cancel.is_set():
            raise _Stop("cancelled", "cancellation requested")
        if self.clock() >= self.deadline:
            raise _Stop("timeout", "time budget exhausted")
        if check_steps and self.steps >= self.step_limit:
            raise _Stop("aborted", "maximum access steps exhausted")

    def _access(
        self,
        operation: Operation,
        ref: RegisterRef,
        value: int | None = None,
        *,
        modifying_mask: int = 0,
    ) -> int | None:
        self._guard()
        timestamp = self.clock()
        lanes = (1 << ref.width) - 1
        self.steps += 1
        try:
            validate_access(
                self.regions,
                ref,
                operation,
                value=value,
                byte_enable=lanes,
            )
            if operation == "write":
                assert value is not None  # checked before any backend call
                # Even a failed/short write might partially modify state.
                self.touched_mask |= modifying_mask
                outcome = self.backend.write(ref, value, lanes)
            else:
                outcome = self.backend.read(ref)
        except AccessValidationError as error:
            outcome = AccessResult(
                completion="unsupported",
                reason=f"backend_rejected:{error.rejection.code}",
                mediation="unknown",
            )
        except OSError as error:
            # Defensive boundary for backend adapters; never invent mediation or
            # latency for an exception that supplies neither.
            outcome = AccessResult(
                completion="error",
                reason=f"transport_error:{error}",
                mediation="unknown",
            )
        if outcome.completion == "ok" and (
            (operation == "read" and outcome.value is None)
            or (outcome.value is not None and outcome.value > ref.full_mask)
            or (operation == "write" and outcome.value is not None)
        ):
            outcome = AccessResult(
                completion="error",
                reason="backend_contract_violation: invalid returned value",
                mediation=outcome.mediation,
                duration_ns=outcome.duration_ns,
            )
        self.observations.append(
            Observation(
                sequence_id=self.sequence_start + len(self.observations),
                monotonic_ns=timestamp,
                operation=operation,
                ref=ref,
                byte_enable=lanes,
                attempted_value=value,
                returned_value=outcome.value,
                absent_reason=outcome.reason,
                completion=outcome.completion,
                source="collector",
                mediation=outcome.mediation,
                state_epoch=self.state_epoch,
                duration_ns=outcome.duration_ns,
            )
        )
        if outcome.completion != "ok":
            termination: ExperimentTermination = (
                "timeout" if outcome.completion == "timeout" else "backend_error"
            )
            raise _Stop(termination, outcome.reason or "backend failure")
        self._guard(check_steps=False)
        return outcome.value

    def _read(self) -> int:
        value = self._access("read", self.recipe.ref)
        assert value is not None  # checked by _access, never a fabricated zero
        return value

    def _settle(self) -> int:
        if self.recipe.flush_ref is not None:
            self._access("read", self.recipe.flush_ref)
        value = self._read()
        for _ in range(self.recipe.readback_reads - 1):
            value = self._read()
        return value

    def probe(self) -> None:
        recipe = self.recipe
        self._guard()
        if recipe.required_state_epoch not in (None, self.state_epoch):
            raise _Stop("precondition_failed", "required state epoch does not match")
        try:
            self.regions = self.backend.discover_regions()
            validate_access(self.regions, recipe.ref, "read")
            validate_access(
                self.regions,
                recipe.ref,
                "write",
                value=0,
                byte_enable=(1 << recipe.ref.width) - 1,
            )
            if recipe.flush_ref is not None:
                validate_access(self.regions, recipe.flush_ref, "read")
        except AccessValidationError as error:
            raise _Stop(
                "precondition_failed", f"backend_rejected:{error.rejection.code}"
            ) from error
        except OSError as error:
            raise _Stop("backend_error", f"region_discovery_failed:{error}") from error
        self.baseline = self._read()
        stable = recipe.probe_mask | recipe.preserve_value_mask
        if (self._read() ^ self.baseline) & stable:
            raise _Stop("precondition_failed", "baseline changed in storage fields")
        self._guard()
        try:
            self.journal.begin(
                recipe,
                self.baseline,
                state_epoch=self.state_epoch,
                started_monotonic_ns=self.started,
            )
        except OSError as error:
            raise _Stop("aborted", f"journal_begin_failed:{error}") from error
        self.journal_started = True
        for index in range(8 * recipe.ref.width):
            bit = 1 << index
            if not recipe.probe_mask & bit:
                continue
            for _ in range(recipe.repeat_count):
                for target in (self.baseline ^ bit, self.baseline):
                    current = self._read()
                    keep = recipe.preserve_value_mask | (recipe.probe_mask & ~bit)
                    value = (current & keep) | (target & bit)
                    self._guard()
                    self._access("write", recipe.ref, value, modifying_mask=bit)
                    self._settle()
            # Coverage means both requests AND their completion reads occurred,
            # in every repeat. It is not an assertion that either write stuck.
            self.tested_mask |= bit

    def restore(self) -> tuple[Restoration, str]:
        if not self.touched_mask:
            return "not_attempted", "no modifying access was attempted"
        if self.recipe.restoration_strategy == "not_restorable":
            return "not_restorable", "recipe declares no recovery operation"
        assert self.baseline is not None
        self.recovering = True
        self.steps = 0
        self.step_limit = self.recipe.restoration_max_steps
        self.deadline = self.clock() + self.recipe.restoration_time_budget_ns
        try:
            current = self._read()
            keep = self.recipe.preserve_value_mask | (
                self.recipe.probe_mask & ~self.touched_mask
            )
            value = (current & keep) | (self.baseline & self.touched_mask)
            self._access("write", self.recipe.ref, value)
            restored = self._settle()
            if (restored ^ self.baseline) & self.touched_mask:
                return "failed", "restoration readback differs in probed bits"
            detail = "readback matches baseline for attempted probe bits only"
            # Write-zero bits are never replayed. One set at baseline that now
            # reads 0 may be a level-sensitive bit the probe cleared, or a
            # volatile/W1C bit the device cleared; surface it for review.
            cleared = self.baseline & self.recipe.write_zero_mask & ~restored
            if cleared and self.recipe.ref.space == "config":
                # Config write-zero bits are W1C status or level controls; a
                # cleared control is real state loss, so do not claim recovery.
                return (
                    "failed",
                    f"write-zero bits set at baseline now read 0: {cleared:#x}",
                )
            if cleared:
                detail += f"; write-zero bits set at baseline now read 0: {cleared:#x}"
            return "verified", detail
        except _Stop as error:
            return "failed", f"{error.termination}:{error.detail}"


def run_experiment(
    backend: RegisterAccessBackend,
    recipe: ExperimentRecipe,
    *,
    state_epoch: int,
    sequence_start: int,
    clock: Callable[[], int],
    cancel: threading.Event | None,
    journal: ExperimentJournal,
) -> tuple[list[Observation], ExperimentResult]:
    """Execute one recipe without classification, always attempting recovery.

    Stop reasons use ``preconditions['executor.termination_detail']`` because
    the shared ExperimentResult has no termination-detail field. Probe failure
    and restoration failure remain separate. Unexpected programming exceptions
    propagate after bounded recovery; their journal remains unfinished.
    """
    if type(state_epoch) is not int or state_epoch < 0:
        raise ValueError("state_epoch must be a nonnegative integer")
    if type(sequence_start) is not int or sequence_start < 0:
        raise ValueError("sequence_start must be a nonnegative integer")
    runner = _Runner(
        backend, recipe, state_epoch, sequence_start, clock, cancel, journal
    )
    termination: ExperimentTermination = "completed"
    detail = "all requested probe cycles completed"
    try:
        runner.probe()
    except _Stop as error:
        termination, detail = error.termination, error.detail
    finally:
        restoration, restoration_detail = runner.restore()
    preconditions = dict(recipe.preconditions)
    preconditions["executor.termination_detail"] = detail
    if runner.baseline is None:
        preconditions["executor.baseline_absent_reason"] = detail
    result = ExperimentResult(
        experiment_id=str(journal.session_id),
        recipe_id=recipe.recipe_id,
        recipe_version=recipe.version,
        ref=recipe.ref,
        state_epoch=state_epoch,
        preconditions=preconditions,
        baseline_value=runner.baseline,
        requested_mask=recipe.probe_mask,
        tested_mask=runner.tested_mask,
        observation_ids=tuple(item.sequence_id for item in runner.observations),
        time_budget_ns=recipe.time_budget_ns,
        termination=termination,
        restoration=restoration,
        restoration_detail=restoration_detail,
    )
    if runner.journal_started:
        try:
            journal.complete(result)
        except OSError as error:
            preconditions["executor.journal_error"] = f"journal_complete_failed:{error}"
            result = result.model_copy(
                update={
                    "termination": (
                        "aborted" if termination == "completed" else termination
                    ),
                    "preconditions": preconditions,
                }
            )
    return runner.observations, result


RESOLVE_HINT = (
    "after verifying the donor is restored (e.g. power cycle, driver rebind), "
    "run: python -m pcileechfwgenerator.host_collect.register_experiments "
    "resolve <journal> --note '<what you checked>'"
)


def main(argv: list[str] | None = None) -> int:
    """Inspect or resolve experiment journals. Never touches hardware."""
    import argparse

    parser = argparse.ArgumentParser(
        prog="python -m pcileechfwgenerator.host_collect.register_experiments"
    )
    sub = parser.add_subparsers(dest="command", required=True)
    listing = sub.add_parser("list", help="show journals that block new sessions")
    listing.add_argument("directory", type=Path)
    resolve = sub.add_parser(
        "resolve", help="record that a donor was restored out of band"
    )
    resolve.add_argument("journal", type=Path)
    resolve.add_argument("--note", required=True)
    resolve.add_argument(
        "--force",
        action="store_true",
        help="also resolve an unfinished journal (stop all collectors first)",
    )
    args = parser.parse_args(argv)
    if args.command == "list":
        for item in ExperimentJournal.find_unfinished(args.directory):
            record = item.record
            donor = (
                f"{record.donor.vendor_id:04x}:{record.donor.device_id:04x}"
                if record is not None and record.donor is not None
                else "unknown donor"
            )
            bdf = (record.bdf if record is not None else None) or "unknown BDF"
            print(f"{item.path}\t{bdf}\t{donor}\t{item.reason}")
        return 0
    record = ExperimentJournal.resolve(args.journal, note=args.note, force=args.force)
    print(f"{args.journal}: {record.status} at {record.resolved_at}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
