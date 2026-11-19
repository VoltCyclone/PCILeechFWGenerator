"""Explicit-width register access contracts; no live hardware implementation.

Binding/ownership remains the responsibility of the existing VFIOAccess seam.
Implementations must validate before I/O, return failures explicitly, and bound
each call themselves: a synchronous call cannot be preempted by the executor.
An ``ok`` write acknowledges the backend request, not device storage semantics
or posted-write completion. Recipes specify subsequent completion reads.
"""

from __future__ import annotations

from typing import Literal, NoReturn, Protocol, Sequence, runtime_checkable

from pcileechfwgenerator.device_clone.register_evidence import (
    AddressSpace,
    Completion,
    Mediation,
    Operation,
    RegisterRef,
)
from pcileechfwgenerator.log_config import get_logger
from pydantic import BaseModel, ConfigDict, Field, SkipValidation, model_validator

logger = get_logger(__name__)


class _Contract(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True, strict=True)


class RegisterRegion(_Contract):
    """One accessible address space; sizes and permitted widths are in bytes."""

    space: AddressSpace
    bar_index: int | None = Field(default=None, ge=0, le=5)
    size: int = Field(gt=0)
    permitted_widths: tuple[int, ...]
    readable: bool
    writable: bool

    @model_validator(mode="after")
    def _layout(self) -> RegisterRegion:
        if (self.space == "config") != (self.bar_index is None):
            raise ValueError("only BAR regions require a BAR index")
        allowed = (1, 2, 4) if self.space == "config" else (1, 2, 4, 8)
        if not self.permitted_widths or any(
            width not in allowed for width in self.permitted_widths
        ):
            raise ValueError("unsupported region access widths")
        if len(set(self.permitted_widths)) != len(self.permitted_widths):
            raise ValueError("duplicate region access widths")
        if self.space == "config" and self.size > 4096:
            raise ValueError("config region exceeds config space")
        return self


class AccessResult(_Contract):
    """Backend completion, with no inference about device writability.

    Reads return a logical little-endian value only on success. Writes return
    None with a reason (e.g. ``write_has_no_return_value``). Unknown duration
    stays None; mediation must be supplied, including explicit ``unknown``.
    """

    completion: Completion
    value: int | None = Field(default=None, ge=0)
    reason: str | None = Field(default=None, min_length=1)
    mediation: Mediation
    duration_ns: int | None = Field(default=None, ge=0)

    @model_validator(mode="after")
    def _outcome(self) -> AccessResult:
        if self.value is None and not self.reason:
            raise ValueError("absent value requires a reason")
        if self.completion != "ok" and self.value is not None:
            raise ValueError("failed access cannot return a value")
        return self


RejectionCode = Literal[
    "region_missing",
    "region_ambiguous",
    "misaligned",
    "unsupported_width",
    "out_of_bounds",
    "not_readable",
    "not_writable",
    "invalid_value",
    "invalid_byte_enable",
]


class AccessRejection(_Contract):
    """A pre-I/O rejection, never evidence that a device ignored a write."""

    code: RejectionCode
    # The rejected reference can itself be malformed (e.g. model_construct).
    # Preserve it in the error, without treating it as validated evidence.
    ref: SkipValidation[RegisterRef]
    operation: Operation
    detail: str


class AccessValidationError(ValueError):
    """Carries a structured rejection separate from an AccessResult."""

    def __init__(self, rejection: AccessRejection) -> None:
        self.rejection = rejection
        super().__init__(rejection.code)


def validate_access(
    regions: Sequence[RegisterRegion],
    ref: RegisterRef,
    operation: Operation,
    *,
    value: int | None = None,
    byte_enable: int | None = None,
) -> RegisterRegion:
    """Validate an exact transaction before I/O; never split or resize it.

    Return its unique region or raise AccessValidationError. RegisterRef itself
    also checks alignment, but this boundary checks again for unvalidated refs.
    """

    def reject(code: RejectionCode, detail: str) -> NoReturn:
        raise AccessValidationError(
            AccessRejection(code=code, ref=ref, operation=operation, detail=detail)
        )

    matches = [
        region
        for region in regions
        if (region.space, region.bar_index) == (ref.space, ref.bar_index)
    ]
    if not matches:
        reject("region_missing", "address space is not exposed by this backend")
    if len(matches) != 1:
        reject("region_ambiguous", "multiple regions describe this address space")
    region = matches[0]
    if ref.width not in region.permitted_widths:
        reject("unsupported_width", "backend does not support this exact width")
    if ref.offset % ref.width:
        reject("misaligned", "offset is not aligned to the requested width")
    if ref.offset < 0 or ref.offset + ref.width > region.size:
        reject("out_of_bounds", "access extent exceeds the region")
    if operation == "read":
        if not region.readable:
            reject("not_readable", "backend region does not permit reads")
        if value is not None:
            reject("invalid_value", "read must not supply a value")
    elif operation == "write":
        if not region.writable:
            reject("not_writable", "backend region does not permit writes")
        if type(value) is not int or not 0 <= value <= ref.full_mask:
            reject("invalid_value", "write value does not fit the access width")
        if type(byte_enable) is not int or not 0 < byte_enable < 1 << ref.width:
            reject("invalid_byte_enable", "write requires explicit valid byte lanes")
    else:
        raise ValueError("operation must be read or write")
    if byte_enable is not None and (
        type(byte_enable) is not int or not 0 < byte_enable < 1 << ref.width
    ):
        reject("invalid_byte_enable", "byte lanes exceed the access width")
    return region


@runtime_checkable
class RegisterAccessBackend(Protocol):
    """A session-owned backend; discovery must not modify device state.

    Methods raise AccessValidationError for pre-I/O rejection and otherwise
    return AccessResult, including short I/O, removal, and transport errors.
    Each implementation must validate_access before touching a device. It must
    not retry a rejected request through a less constrained backend.
    """

    def discover_regions(self) -> tuple[RegisterRegion, ...]:
        """Return region metadata; raise OSError if discovery is unavailable."""

    def read(self, ref: RegisterRef) -> AccessResult:
        """Read exactly ref.width bytes, reporting measured duration if known."""

    def write(self, ref: RegisterRef, value: int, byte_enable: int) -> AccessResult:
        """Issue one explicit-width write with the specified byte lanes."""
