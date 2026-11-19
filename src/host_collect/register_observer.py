"""Pure parser for the REGISTER_OBSERVER_V1 passive MMIO stream.

CSV columns: kind,tid,monotonic_ns,width,vaddr,value,physical,size,call_id,status.
Integers are decimal or 0x-prefixed; empty fields mean absent. Width is bytes.
Kinds: map, unmap, read_enter, read_return, write, lost. Map physical/size
identify a resource range; call_id uniquely identifies a read invocation within
a tid (including nesting). Read returns repeat the saved entry address/width.
Status is ok/error on returns; lost uses value as its event count. A write is
an observed *attempt*, never proof of acceptance. No permission is inferred.

Input order is authoritative. A timestamp regression, lost marker, or malformed
line invalidates all mappings and pending reads: the missing event could have
been an unmap. Capture must start before mapping creation. Already existing
mappings and unsupported accessor paths cannot be reconstructed from pointers.
"""

from __future__ import annotations

import csv
import re
from collections.abc import Iterable
from typing import Any, Literal, cast

from pcileechfwgenerator.device_clone.register_evidence import (
    Observation,
    RegisterRef,
    width_mask,
)
from pydantic import BaseModel, ConfigDict, Field, ValidationError, model_validator

READY_MARKER = "REGISTER_OBSERVER_V1"


class BarResource(BaseModel):
    """Known memory resource for the target device (never a virtual address)."""

    model_config = ConfigDict(extra="forbid", frozen=True, strict=True)
    bar_index: int = Field(ge=0, le=5)
    physical_start: int = Field(ge=0)
    size: int = Field(gt=0)


class TraceRecord(BaseModel):
    """Validated wire record. Unused CSV fields must be empty."""

    model_config = ConfigDict(extra="forbid", frozen=True, strict=True)
    kind: Literal["map", "unmap", "read_enter", "read_return", "write", "lost"]
    tid: int = Field(ge=0)
    monotonic_ns: int = Field(ge=0)
    width: int | None = None
    vaddr: int | None = Field(default=None, ge=0)
    value: int | None = Field(default=None, ge=0)
    physical: int | None = Field(default=None, ge=0)
    size: int | None = Field(default=None, gt=0)
    call_id: int | None = Field(default=None, ge=0)
    status: Literal["ok", "error"] | None = None

    @model_validator(mode="after")
    def _shape(self) -> TraceRecord:
        required = {
            "map": {"vaddr", "physical", "size"},
            "unmap": {"vaddr"},
            "read_enter": {"vaddr", "width", "call_id"},
            "read_return": {"vaddr", "width", "call_id", "status"},
            "write": {"vaddr", "width", "value"},
            "lost": {"value"},
        }[self.kind]
        allowed = required | ({"value"} if self.kind == "read_return" else set())
        for key in ("width", "vaddr", "value", "physical", "size", "call_id", "status"):
            value = getattr(self, key)
            if key in required and value is None:
                raise ValueError(f"{self.kind} requires {key}")
            if key not in allowed and value is not None:
                raise ValueError(f"{self.kind} cannot contain {key}")
        if self.width is not None:
            if self.width not in (1, 2, 4, 8):
                raise ValueError("unsupported access width")
            if self.value is not None and self.value > width_mask(self.width):
                raise ValueError("value exceeds access width")
        if self.kind == "read_return":
            if (self.status == "ok") != (self.value is not None):
                raise ValueError(
                    "successful reads need a value; failed reads cannot have one"
                )
        if self.kind == "lost" and not self.value:
            raise ValueError("lost count must be positive")
        return self


class ObserverResult(BaseModel):
    """Evidence and integrity counters; dropped_events can feed a manifest.

    Counters describe categories, not necessarily disjoint events: a truncated
    return can also leave an unmatched entry. dropped_events conservatively sums
    losses, malformed records, unmatched reads, and unattributed accesses.
    """

    observations: list[Observation] = Field(default_factory=list)
    lost_events: int = 0
    malformed_lines: int = 0
    unmatched_reads: int = 0
    unattributed_accesses: int = 0
    out_of_order_events: int = 0
    discarded_observations: int = 0
    # Capture-level problems the stream itself cannot show (CPUs left online
    # while tracing, tracer faults reported only in the kernel log).
    capture_incomplete: int = 0
    # BARs the driver mapped while the tracer was active (mmiotrace MAP).
    mapped_bars: list[int] = Field(default_factory=list)
    # Whether the function was reset before the traced probe (None: unknown).
    reset_performed: bool | None = None
    # Host changes an exercise could not undo (non-blocking, reported).
    host_changes: list[str] = Field(default_factory=list)
    # Device exercises skipped (less coverage) and run, for the manifest.
    exercises_skipped: list[str] = Field(default_factory=list)
    exercises_run: list[str] = Field(default_factory=list)
    notes: list[str] = Field(default_factory=list)
    # Ranges removed from the evidence and not served by the BAR model.
    excluded_ranges: list[str] = Field(default_factory=list)

    @property
    def dropped_events(self) -> int:
        return (
            self.lost_events
            + self.malformed_lines
            + self.unmatched_reads
            + self.unattributed_accesses
            + self.out_of_order_events
            + self.discarded_observations
            + self.capture_incomplete
        )


class _PendingRead(BaseModel):
    entry: TraceRecord
    ref: RegisterRef | None
    mapping: TraceRecord | None


def parse_trace_records(
    lines: Iterable[str], bars: Iterable[BarResource], *, state_epoch: int = 0
) -> ObserverResult:
    """Parse one capture with fresh mapping state and sequential evidence IDs.

    Only unique, fully contained physical resource matches are attributable.
    Alignment and full access bounds are checked before constructing evidence.
    I/O-port BARs must not be supplied as memory resources.
    """
    if state_epoch < 0:
        raise ValueError("state_epoch must be nonnegative")
    resources = tuple(bars)
    result = ObserverResult()
    mappings: dict[int, TraceRecord] = {}
    pending: dict[tuple[int, int], _PendingRead] = {}
    last_ns = -1

    def emit(p: _PendingRead, end: TraceRecord | None, reason: str | None) -> None:
        if p.ref is None:
            return
        result.observations.append(
            Observation(
                sequence_id=len(result.observations),
                monotonic_ns=p.entry.monotonic_ns,
                operation="read",
                ref=p.ref,
                source="native_driver",
                state_epoch=state_epoch,
                returned_value=(
                    end.value if end is not None and reason is None else None
                ),
                completion="ok" if reason is None else "error",
                absent_reason=reason,
                duration_ns=(
                    end.monotonic_ns - p.entry.monotonic_ns if end is not None else None
                ),
            )
        )

    def invalidate(reason: str) -> None:
        for p in pending.values():
            result.unmatched_reads += 1
            emit(p, None, reason)
        pending.clear()
        mappings.clear()

    def resolve(record: TraceRecord) -> tuple[RegisterRef | None, TraceRecord | None]:
        # The per-kind record validator guarantees these fields are present.
        r_vaddr = cast(int, record.vaddr)
        r_width = cast(int, record.width)
        matches = [
            m
            for m in mappings.values()
            if cast(int, m.vaddr) <= r_vaddr
            and r_vaddr + r_width <= cast(int, m.vaddr) + cast(int, m.size)
        ]
        if len(matches) != 1:
            return None, None
        mapping = matches[0]
        m_physical = cast(int, mapping.physical)
        m_size = cast(int, mapping.size)
        candidates = [
            b
            for b in resources
            if b.physical_start <= m_physical
            and m_physical + m_size <= b.physical_start + b.size
        ]
        if len(candidates) != 1:
            return None, None
        bar = candidates[0]
        offset = m_physical - bar.physical_start + r_vaddr - cast(int, mapping.vaddr)
        if offset % r_width or offset + r_width > bar.size:
            return None, None
        return (
            RegisterRef(
                space="mem_bar",
                bar_index=bar.bar_index,
                offset=offset,
                width=r_width,
                field_mask=width_mask(r_width),
            ),
            mapping,
        )

    for line in lines:
        text = line.strip()
        if not text or text == READY_MARKER:
            continue
        lost = re.fullmatch(r"Lost (\d+) events(?:.*)?", text, re.IGNORECASE)
        if lost:
            result.lost_events += int(lost[1])
            invalidate("trace events lost")
            continue
        try:
            if not line.endswith("\n"):
                raise ValueError("truncated record")
            fields = next(csv.reader([text], strict=True))
            if len(fields) != 10:
                raise ValueError("expected ten CSV fields")
            keys = (
                "tid",
                "monotonic_ns",
                "width",
                "vaddr",
                "value",
                "physical",
                "size",
                "call_id",
            )
            values: dict[str, Any] = {
                key: (
                    int(value, 16 if value.startswith("0x") else 10) if value else None
                )
                for key, value in zip(keys, fields[1:9])
            }
            record = TraceRecord(
                kind=cast(Any, fields[0]), status=cast(Any, fields[9] or None), **values
            )
        except (ValueError, ValidationError, csv.Error):
            result.malformed_lines += 1
            invalidate("malformed or truncated trace record")
            continue
        if record.monotonic_ns < last_ns:
            result.out_of_order_events += 1
            invalidate("out-of-order trace record")
            # A delayed unmap may predate accesses already emitted. Retract
            # that evidence rather than retain potentially stale attribution.
            result.discarded_observations += len(result.observations)
            result.observations.clear()
            continue
        last_ns = record.monotonic_ns
        if record.kind == "lost":
            result.lost_events += cast(int, record.value)
            invalidate("trace events lost")
        elif record.kind == "map":
            # Even unrelated mappings retire overlapping target mappings.
            map_vaddr = cast(int, record.vaddr)
            map_size = cast(int, record.size)
            for base, old in list(mappings.items()):
                if (
                    map_vaddr < base + cast(int, old.size)
                    and base < map_vaddr + map_size
                ):
                    del mappings[base]
            if map_vaddr:  # NULL return is a failed mapping, not address zero.
                mappings[map_vaddr] = record
        elif record.kind == "unmap":
            mappings.pop(cast(int, record.vaddr), None)
        elif record.kind in ("read_enter", "write"):
            ref, mapping = resolve(record)
            if ref is None:
                result.unattributed_accesses += 1
            if record.kind == "read_enter":
                key = (record.tid, cast(int, record.call_id))
                if key in pending:
                    result.unmatched_reads += 2
                    emit(pending.pop(key), None, "duplicate read entry")
                    emit(
                        _PendingRead(entry=record, ref=ref, mapping=mapping),
                        None,
                        "duplicate read entry",
                    )
                    continue  # Neither entry can be paired unambiguously.
                pending[key] = _PendingRead(entry=record, ref=ref, mapping=mapping)
            elif ref is not None:
                result.observations.append(
                    Observation(
                        sequence_id=len(result.observations),
                        monotonic_ns=record.monotonic_ns,
                        operation="write",
                        ref=ref,
                        attempted_value=record.value,
                        completion="ok",
                        source="native_driver",
                        state_epoch=state_epoch,
                    )
                )
        else:  # read_return
            p = pending.pop((record.tid, cast(int, record.call_id)), None)
            if p is None:
                result.unmatched_reads += 1
                continue
            reason = None
            if (record.vaddr, record.width) != (p.entry.vaddr, p.entry.width):
                reason = "read entry/return mismatch"
                result.unmatched_reads += 1
            elif (
                p.mapping is not None
                and mappings.get(cast(int, p.mapping.vaddr)) is not p.mapping
            ):
                reason = "mapping retired before read return"
                result.unmatched_reads += 1
            elif record.status == "error":
                reason = "read accessor reported an error"
            emit(p, record, reason)
    invalidate("read return missing at end of trace")
    return result
