"""Validated MSI-X layout: the single source of truth for MX-03.

The layout is derived only from captured config-space bytes (or their parsed
fields) and captured BAR records. Host-programmed message addresses and data
are runtime state and are never part of it.

Ownership model (MX-03; status in docs/development/MSIX_STATUS.md):

* The 7-series hard IP answers config space below 0xA8 itself, including its
  own MSI-X capability. It is configured from this layout by the IP override.
* The table and PBA share one served memory BAR. That MSI-X BAR may be separate
  from the primary register BAR when the validated served set includes both.
  Split table/PBA BARs remain unsupported.
* Inside the served BAR, the table window and the PBA window each have exactly
  one owner, the MSI-X subsystem. Learned and fallback register handlers never
  answer there. Windows are bounded by the real vector count, not a power of
  two, so padded core entries are never reachable.
"""

from __future__ import annotations

from typing import Any, Iterable, Literal, Optional

from pcileechfwgenerator.exceptions import PCILeechError
from pydantic import BaseModel, ConfigDict, Field, model_validator

MSIX_CAP_ID = 0x11
MAX_VECTORS = 2048
TABLE_ENTRY_BYTES = 16
STANDARD_BARS = 6
# The BAR controller decodes 32-bit offsets, so every MSI-X byte must sit below
# 4 GiB even inside a larger 64-bit BAR.
DECODE_LIMIT = 1 << 32
# Must match the IP override's served-BAR aperture rule
# (pcie_ip_donor_override._bar_aperture_error); a served BAR the override would
# drop would leave the hard IP serving a different BAR than this layout assumes.
MIN_SERVED_MEMORY_BAR = 4096

Window = Literal["table", "pba"]


class MsixLayoutError(PCILeechError):
    """The captured MSI-X layout is malformed or not supported by this build."""


def pba_size_bytes(vectors: int) -> int:
    """Return the PBA allocation in bytes, rounded up to whole QWORDs."""
    return 8 * ((vectors + 63) // 64)


class MsixCapability(BaseModel):
    """Raw MSI-X capability fields read from config space."""

    model_config = ConfigDict(extra="forbid", frozen=True, strict=True)

    # Older parsed captures retain the layout but not the capability address.
    cap_offset: Optional[int] = Field(default=None, ge=0x40, le=0xF4)
    table_size: int = Field(ge=1, le=MAX_VECTORS)
    table_bir: int = Field(ge=0, le=7)
    table_offset: int = Field(ge=0)
    pba_bir: int = Field(ge=0, le=7)
    pba_offset: int = Field(ge=0)


class MsixBar(BaseModel):
    """The captured facts about one BAR that the layout depends on."""

    model_config = ConfigDict(extra="forbid", frozen=True, strict=True)

    index: int = Field(ge=0, lt=STANDARD_BARS)
    size: int = Field(ge=0)
    is_memory: bool
    is_64bit: bool = False

    @classmethod
    def from_record(cls, record: Any) -> "MsixBar":
        """Adapt a BarInfo, BarConfiguration or template dict without guessing."""

        def get(name: str, default: Any = None) -> Any:
            if isinstance(record, dict):
                return record.get(name, default)
            return getattr(record, name, default)

        if get("index") is None:
            raise MsixLayoutError("BAR record has no index")
        is_memory = get("is_memory")
        if is_memory is None:
            bar_type = get("bar_type")
            if isinstance(bar_type, str):
                is_memory = bar_type.lower() == "memory"
            elif get("is_io") is not None:
                is_memory = not get("is_io")
        if is_memory is None:
            raise MsixLayoutError(f"BAR record {get('index')!r} has no memory/IO type")
        return cls(
            index=int(get("index")),
            size=int(get("size") or 0),
            is_memory=bool(is_memory),
            is_64bit=bool(get("is_64bit", False)),
        )


class MsixLayout(BaseModel):
    """A validated, servable MSI-X layout inside the served BAR."""

    model_config = ConfigDict(extra="forbid", frozen=True, strict=True)

    capability: MsixCapability
    served_bar: MsixBar

    @property
    def vectors(self) -> int:
        return self.capability.table_size

    @property
    def table_start(self) -> int:
        return self.capability.table_offset

    @property
    def table_end(self) -> int:
        return self.table_start + TABLE_ENTRY_BYTES * self.vectors

    @property
    def pba_start(self) -> int:
        return self.capability.pba_offset

    @property
    def pba_end(self) -> int:
        # The PBA is an array of QWORDs, one bit per vector.
        return self.pba_start + pba_size_bytes(self.vectors)

    @property
    def irq_index_width(self) -> int:
        """Core IRQ_INDEX_WIDTH: smallest width that indexes every vector."""
        return max(1, (self.vectors - 1).bit_length())

    def windows(self) -> dict[Window, tuple[int, int]]:
        return {
            "table": (self.table_start, self.table_end),
            "pba": (self.pba_start, self.pba_end),
        }

    def owner(self, bar_index: int, offset: int, width: int) -> Optional[Window]:
        """Which MSI-X window owns one DWORD or QWORD access, or None.

        Intended for single MSI-X-sized accesses. Windows are QWORD aligned, so
        aligned 1/2/4/8-byte accesses can never straddle an edge; an unaligned
        span that does raises. Larger spans (multi-DWORD TLPs, learned register
        ranges) must be split per DWORD with ``dword_owners`` instead, which is
        also how the BAR engines present them to the RTL.
        """
        if width <= 0 or offset < 0:
            raise ValueError("access offset/width must be positive")
        if bar_index != self.served_bar.index:
            return None
        end = offset + width
        for name, (start, stop) in self.windows().items():
            if offset < stop and start < end:
                if offset < start or end > stop:
                    raise MsixLayoutError(
                        f"BAR{bar_index} access [{offset:#x}, {end:#x}) "
                        f"straddles the MSI-X {name} window [{start:#x}, {stop:#x})"
                    )
                return name
        return None

    def dword_owners(
        self, bar_index: int, offset: int, width: int
    ) -> list[tuple[int, Optional[Window]]]:
        """Split a span into DWORDs and give each its single owner."""
        if width <= 0 or offset < 0:
            raise ValueError("access offset/width must be positive")
        first, last = offset & ~3, (offset + width - 1) & ~3
        return [(dw, self.owner(bar_index, dw, 4)) for dw in range(first, last + 4, 4)]

    def decode_parameters(self) -> dict[str, int]:
        """Inclusive byte bounds and core width for generated RTL routing.

        Bounds are inclusive last bytes so they always fit 32 bits; an exclusive
        end can equal 4 GiB and wrap to zero in a 32-bit compare.
        """
        return {
            "bar_index": self.served_bar.index,
            "vectors": self.vectors,
            "irq_index_width": self.irq_index_width,
            "table_first": self.table_start,
            "table_last": self.table_end - 1,
            "pba_first": self.pba_start,
            "pba_last": self.pba_end - 1,
        }

    @model_validator(mode="after")
    def _check_against_served_bar(self) -> "MsixLayout":
        cap, bar = self.capability, self.served_bar
        for name, bir in (("table", cap.table_bir), ("PBA", cap.pba_bir)):
            if bir != bar.index:
                raise MsixLayoutError(
                    f"MSI-X {name} is in BAR{bir} but the served MSI-X BAR is "
                    f"BAR{bar.index}; the table and PBA must share that BAR"
                )
        if not bar.is_memory or bar.size <= 0:
            raise MsixLayoutError(
                f"MSI-X structures need an implemented memory BAR; BAR{bar.index} "
                "is not one"
            )
        if bar.size < MIN_SERVED_MEMORY_BAR or bar.size & (bar.size - 1):
            raise MsixLayoutError(
                f"served BAR{bar.index} size {bar.size:#x} is not an aperture the "
                "hard IP can serve (power of two, at least 4 KiB)"
            )
        for name, (start, stop) in self.windows().items():
            if start % 8:
                raise MsixLayoutError(
                    f"MSI-X {name} offset {start:#x} is not QWORD aligned"
                )
            if stop > bar.size:
                raise MsixLayoutError(
                    f"MSI-X {name} [{start:#x}, {stop:#x}) exceeds BAR{bar.index} "
                    f"size {bar.size:#x}"
                )
            if stop > DECODE_LIMIT:
                raise MsixLayoutError(
                    f"MSI-X {name} [{start:#x}, {stop:#x}) is beyond the 32-bit "
                    "BAR offset decode"
                )
        (a0, a1), (b0, b1) = self.windows().values()
        if a0 < b1 and b0 < a1:
            raise MsixLayoutError("MSI-X table overlaps its pending-bit array")
        return self


def read_msix_capability(config: bytes) -> Optional[MsixCapability]:
    """Strictly walk the standard capability list; None when MSI-X is absent.

    Malformed chains raise rather than being skipped. A wrong layout would
    route host accesses to the wrong owner.
    """
    if len(config) < 256:
        raise MsixLayoutError("config space capture is shorter than 256 bytes")
    if not config[0x06] & 0x10:
        if config[0x34]:
            raise MsixLayoutError(
                "capability pointer set without the status capability bit"
            )
        return None
    pointer = config[0x34] & 0xFC
    seen: set[int] = set()
    found: Optional[MsixCapability] = None
    while pointer:
        if pointer < 0x40 or pointer in seen:
            raise MsixLayoutError("invalid or cyclic PCI capability chain")
        seen.add(pointer)
        if config[pointer] == MSIX_CAP_ID:
            if found is not None:
                raise MsixLayoutError("duplicate MSI-X capability")
            if pointer + 12 > 256:
                raise MsixLayoutError("truncated MSI-X capability")
            control = int.from_bytes(config[pointer + 2 : pointer + 4], "little")
            table = int.from_bytes(config[pointer + 4 : pointer + 8], "little")
            pba = int.from_bytes(config[pointer + 8 : pointer + 12], "little")
            found = MsixCapability(
                cap_offset=pointer,
                table_size=(control & 0x7FF) + 1,
                table_bir=table & 7,
                table_offset=table & ~7 & 0xFFFFFFFF,
                pba_bir=pba & 7,
                pba_offset=pba & ~7 & 0xFFFFFFFF,
            )
        pointer = config[pointer + 1] & 0xFC
    return found


def derive_msix_layout(
    config: bytes, bars: Iterable[Any], served_bar_index: int
) -> Optional[MsixLayout]:
    """Build the layout from a capture, or None if the donor has no MSI-X.

    ``bars`` are the implemented BAR records; the upper half of a 64-bit BAR
    has no record of its own, so a BIR pointing at it is rejected.
    """
    capability = read_msix_capability(config)
    if capability is None:
        return None
    return derive_msix_layout_from_capability(capability, bars, served_bar_index)


def derive_msix_layout_from_capability(
    capability: MsixCapability, bars: Iterable[Any], served_bar_index: int
) -> MsixLayout:
    """Validate already-parsed capture fields using the same BAR/layout checks."""
    records: dict[int, MsixBar] = {}
    for record in bars:
        parsed = MsixBar.from_record(record)
        if parsed.index in records:
            raise MsixLayoutError(f"duplicate BAR{parsed.index} record")
        records[parsed.index] = parsed
    for name, bir in (("table", capability.table_bir), ("PBA", capability.pba_bir)):
        if bir >= STANDARD_BARS:
            raise MsixLayoutError(f"MSI-X {name} BIR {bir} is reserved")
        if bir not in records:
            raise MsixLayoutError(
                f"MSI-X {name} BIR {bir} names no implemented BAR "
                "(unimplemented, or the upper half of a 64-bit BAR)"
            )
    served = records.get(served_bar_index)
    if served is None:
        raise MsixLayoutError(f"served BAR{served_bar_index} is not implemented")
    return MsixLayout(capability=capability, served_bar=served)
