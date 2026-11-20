"""Validated register and MSI-X BAR ownership shared by generation stages."""

from __future__ import annotations

from typing import Any, Literal, Optional

from pydantic import BaseModel, ConfigDict, Field, model_validator


def _get(record: Any, name: str, default: Any = None) -> Any:
    if hasattr(record, "get") and callable(record.get):
        return record.get(name, default)
    return getattr(record, name, default)


BarRole = Literal["registers", "msix", "registers+msix"]


class ServedBar(BaseModel):
    """Captured aperture and the owner instantiated at its real PCI BAR index."""

    model_config = ConfigDict(extra="forbid", frozen=True, strict=True)

    index: int = Field(ge=0, le=5)
    size: int = Field(gt=0)
    is_memory: bool
    is_64bit: bool = False
    prefetchable: bool = False
    role: BarRole

    @model_validator(mode="after")
    def _valid_aperture(self) -> "ServedBar":
        if self.size & (self.size - 1):
            raise ValueError("served BAR size must be a power of two")
        if self.size < (4096 if self.is_memory else 4):
            raise ValueError("served BAR aperture is below the supported minimum")
        if self.is_64bit and (not self.is_memory or self.index == 5):
            raise ValueError("64-bit BAR needs a standard upper-half partner")
        if not self.is_memory and (self.prefetchable or self.role != "registers"):
            raise ValueError("IO BAR cannot be prefetchable or hold MSI-X")
        return self

    @classmethod
    def from_record(cls, record: Any, role: BarRole) -> "ServedBar":
        """Adapt captured BAR records without changing their aperture geometry."""
        memory = _get(record, "is_memory")
        if memory is None:
            kind = _get(record, "bar_type")
            memory = kind == "memory" if isinstance(kind, str) else None
        if memory is None and _get(record, "is_io") is not None:
            memory = not _get(record, "is_io")
        return cls(
            index=_get(record, "index"),
            size=_get(record, "size"),
            is_memory=memory,
            is_64bit=_get(record, "is_64bit", False),
            prefetchable=_get(record, "prefetchable", False),
            role=role,
        )


def served_bars_from_config(config: Any) -> Optional[tuple[ServedBar, ...]]:
    """Validate an explicit served set, or return None for legacy scalar input."""
    raw = _get(config, "served_bars")
    if raw is None:
        return None
    bars = tuple(
        b if isinstance(b, ServedBar) else ServedBar.model_validate(dict(b.items()))
        for b in raw
    )
    if not 1 <= len(bars) <= 2:
        raise ValueError("serve only the register BAR and optional MSI-X BAR")
    registers = [b for b in bars if b.role in ("registers", "registers+msix")]
    msix = [b for b in bars if b.role in ("msix", "registers+msix")]
    if len(registers) != 1 or len(msix) > 1 or (len(bars) == 2 and len(msix) != 1):
        raise ValueError(
            "served BAR roles must identify one register and one MSI-X owner"
        )
    occupied: set[int] = set()
    for bar in bars:
        slots = {bar.index, bar.index + 1} if bar.is_64bit else {bar.index}
        if occupied & slots:
            raise ValueError("served BARs overlap a 64-bit upper-half partner")
        occupied.update(slots)
    captured = _get(config, "bars")
    if captured is not None:
        for bar in bars:
            matches = [
                record for record in captured if _get(record, "index") == bar.index
            ]
            if len(matches) != 1 or ServedBar.from_record(matches[0], bar.role) != bar:
                raise ValueError(
                    "served BAR descriptor disagrees with captured aperture"
                )
        position = _get(config, "primary_bar")
        if position is not None and (
            type(position) is not int
            or not 0 <= position < len(captured)
            or _get(captured[position], "index") != registers[0].index
        ):
            raise ValueError("primary_bar position disagrees with the register BAR")
    for key, expected in (
        ("served_bar_index", registers[0].index),
        ("aperture_size", registers[0].size),
        ("served_is_64bit", registers[0].is_64bit),
    ):
        value = _get(config, key)
        if value is not None and value != expected:
            raise ValueError(f"{key} disagrees with the register BAR descriptor")
    return bars


def register_bar_config(config: Any) -> dict[str, Any]:
    """Derive legacy register-BAR keys from the authoritative served descriptor.

    Scalar-only contexts retain their historical defaults. An explicit served
    set must never fall back to BAR0 merely because compatibility keys are absent.
    Existing contradictory keys are rejected by served_bars_from_config first.
    """
    normalized = dict(config.items()) if hasattr(config, "items") else {}
    descriptors = served_bars_from_config(normalized)
    if descriptors is None:
        return normalized
    register = next(
        bar for bar in descriptors if bar.role in ("registers", "registers+msix")
    )
    captured = normalized.get("bars")
    if captured is None:
        captured = list(descriptors)
        normalized["bars"] = captured
    normalized.update(
        served_bar_index=register.index,
        bar_index=register.index,
        aperture_size=register.size,
        served_is_64bit=register.is_64bit,
        primary_bar=next(
            index
            for index, bar in enumerate(captured)
            if _get(bar, "index") == register.index
        ),
    )
    return normalized


def select_served_bars(
    primary: Any,
    bars: list[Any],
    table_bir: Optional[int] = None,
    pba_bir: Optional[int] = None,
) -> tuple[ServedBar, ...]:
    """Serve the primary register BAR and, if needed, one captured MSI-X BAR."""
    if table_bir is None and pba_bir is None:
        selected: tuple[ServedBar, ...] = (ServedBar.from_record(primary, "registers"),)
    else:
        if table_bir != pba_bir:
            raise ValueError("MSI-X table and PBA must occupy the same BAR")
        candidates = [b for b in bars if _get(b, "index") == table_bir]
        if len(candidates) != 1:
            raise ValueError("MSI-X names no unique implemented BAR")
        if table_bir == _get(primary, "index"):
            selected = (ServedBar.from_record(primary, "registers+msix"),)
        else:
            selected = (
                ServedBar.from_record(primary, "registers"),
                ServedBar.from_record(candidates[0], "msix"),
            )
    return served_bars_from_config({"served_bars": selected}) or ()
