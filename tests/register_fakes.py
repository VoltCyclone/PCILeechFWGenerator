"""Reusable, hardware-free register backend with independently modeled fields."""

from __future__ import annotations

import time
from collections.abc import Callable

from pcileechfwgenerator.device_clone.register_evidence import Mediation, RegisterRef
from pcileechfwgenerator.host_collect.register_access import (
    AccessResult,
    RegisterRegion,
    validate_access,
)


class FakeClock:
    """Manually advanced nanosecond clock, independent of machine load."""

    def __init__(self) -> None:
        self.now = 0

    def __call__(self) -> int:
        return self.now

    def advance(self, nanoseconds: int = 1) -> None:
        self.now += nanoseconds


class FakeRegisterDevice:
    """One register plus an explicit zero-valued flush register.

    Writes assign RW bits and clear enabled W1C bits set in the request; all
    other bits ignore writes. Volatile bits toggle on every register read.
    Delayed writes apply on the Nth subsequent register read. Error injection
    happens after validation and before semantics, and can be one-shot or
    persistent. The operation callback runs after semantics, allowing precise
    cancellation, status updates and injected-clock advancement.
    """

    def __init__(
        self,
        *,
        ref: RegisterRef | None = None,
        value: int = 0,
        rw_mask: int = 1,
        w1c_mask: int = 0,
        volatile_mask: int = 0,
        delay_reads: int = 0,
        writable: bool = True,
        mediation: Mediation = "unknown",
        fail_after: int | None = None,
        failure: str = "transport_error",
        persistent_failure: bool = False,
        fail_writes: frozenset[int] = frozenset(),
        on_operation: Callable[[FakeRegisterDevice], None] | None = None,
    ) -> None:
        self.ref = ref or RegisterRef(
            space="mem_bar", bar_index=0, offset=0, width=4, field_mask=1
        )
        self.value = value
        self.rw_mask = rw_mask
        self.w1c_mask = w1c_mask
        self.volatile_mask = volatile_mask
        self.delay_reads = delay_reads
        self.mediation = mediation
        self.fail_after = fail_after
        self.failure = failure
        self.persistent_failure = persistent_failure
        self.fail_writes = fail_writes
        self.on_operation = on_operation
        self.removed = False
        self.operations: list[tuple[str, RegisterRef, int | None, int | None]] = []
        self.writes = 0
        self.pending: list[tuple[int, int, int]] = []
        self.region = RegisterRegion(
            space=self.ref.space,
            bar_index=self.ref.bar_index,
            size=self.ref.offset + 2 * self.ref.width,
            permitted_widths=(self.ref.width,),
            readable=True,
            writable=writable,
        )

    def discover_regions(self) -> tuple[RegisterRegion, ...]:
        return (self.region,)

    def set_status(self, mask: int) -> None:
        """Model an external event setting documented W1C status bits."""
        self.value |= mask & self.w1c_mask

    def _apply(self, value: int, lanes: int) -> None:
        for byte in range(self.ref.width):
            if not lanes & (1 << byte):
                continue
            for bit in range(8 * byte, 8 * byte + 8):
                mask = 1 << bit
                if self.rw_mask & mask:
                    if value & mask:
                        self.value |= mask
                    else:
                        self.value &= ~mask
                elif self.w1c_mask & mask and value & mask:
                    self.value &= ~mask

    def _transaction(
        self, operation: str, ref: RegisterRef, value: int | None, lanes: int | None
    ) -> AccessResult:
        validate_access(
            self.discover_regions(), ref, operation, value=value, byte_enable=lanes
        )
        if ref.offset not in (self.ref.offset, self.ref.offset + self.ref.width):
            raise AssertionError(
                "fake exposes only the register and its flush location"
            )
        started = time.monotonic_ns()
        self.operations.append((operation, ref, value, lanes))
        if operation == "write":
            self.writes += 1
        reason = None
        if self.removed:
            reason = "device_removed"
        elif self.fail_after is not None and len(self.operations) > self.fail_after:
            reason = self.failure
            if not self.persistent_failure:
                self.fail_after = None
        elif operation == "write" and self.writes in self.fail_writes:
            reason = "restoration_write_failed"
        returned = None
        if reason is None:
            if ref.offset != self.ref.offset:
                if operation == "write":
                    raise AssertionError("flush location must never be written")
                returned = 0  # explicitly modeled fake flush register
            elif operation == "write":
                assert value is not None and lanes is not None
                if self.delay_reads:
                    self.pending.append((value, lanes, self.delay_reads))
                else:
                    self._apply(value, lanes)
            else:
                pending = []
                for pending_value, pending_lanes, remaining in self.pending:
                    if remaining <= 1:
                        self._apply(pending_value, pending_lanes)
                    else:
                        pending.append((pending_value, pending_lanes, remaining - 1))
                self.pending = pending
                self.value ^= self.volatile_mask
                returned = self.value
        if self.on_operation is not None:
            self.on_operation(self)
        return AccessResult(
            completion=(
                ("timeout" if reason == "timeout" else "error") if reason else "ok"
            ),
            value=returned,
            reason=reason
            or ("write_has_no_return_value" if operation == "write" else None),
            mediation=self.mediation,
            duration_ns=time.monotonic_ns() - started,
        )

    def read(self, ref: RegisterRef) -> AccessResult:
        return self._transaction("read", ref, None, None)

    def write(self, ref: RegisterRef, value: int, byte_enable: int) -> AccessResult:
        return self._transaction("write", ref, value, byte_enable)
