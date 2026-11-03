#!/usr/bin/env python3
"""Relocate a donor's extended capability chain behind the core's hard DSN.

The FPGA PCIe core owns a hard Device Serial Number capability at
0x100-0x10B whose next-pointer targets 0x10C.  The donor's remaining
extended capabilities are therefore re-laid contiguously from 0x10C with
rewritten next-pointers.
"""

from __future__ import annotations

from typing import List

from pcileechfwgenerator.pci_capability.constants import (
    EXT_CAP_SIZE_DEFAULT,
    PCI_EXT_CAP_NEXT_PTR_SHIFT,
    PCI_EXT_CAP_START,
    PCI_EXT_CAP_VERSION_SHIFT,
    PCI_EXT_CONFIG_SPACE_END,
)
from pcileechfwgenerator.pci_capability.core import CapabilityWalker, ConfigSpace
from pcileechfwgenerator.pci_capability.types import CapabilityInfo

# First offset available to the relocated user chain (core DSN owns 0x100-0x10B).
USER_EXT_CAP_START = 0x10C

_DSN_CAP_ID = 0x0003
_ARI_CAP_ID = 0x000E

# Donor capabilities replaced by the core's hard IP.
EXT_CAP_DROP = frozenset({_DSN_CAP_ID})

# Donor capabilities the generated core cannot honour; omitted rather than
# half-cloned: SR-IOV (0x0010), ATS (0x000F), PRI (0x0013), PASID (0x001B),
# Resizable BAR (0x0015), DPC (0x001D), and Multi-Function VC (0x0008, 0x0009;
# a single-function clone must not advertise multi-function VC topology).
EXT_CAP_OMIT = frozenset(
    {0x0008, 0x0009, 0x0010, 0x000F, 0x0013, 0x001B, 0x0015, 0x001D}
)

_HEADER_MASK = 0x000FFFFF  # id + version; clears next-pointer bits 31:20


def _body_length(cap: CapabilityInfo, limit: int) -> int:
    """Bytes to copy for a capability, clamped so offset + length <= limit."""
    if cap.next_ptr > cap.offset:
        length = cap.next_ptr - cap.offset
    else:
        # Terminal (or non-monotonic pointer): conservative fixed length.
        length = EXT_CAP_SIZE_DEFAULT
    return max(4, min(length, limit - cap.offset))


def build_relocated_extended_config(config_hex: str) -> str:
    """Return a config-space image with the extended chain relocated to 0x10C.

    Inputs of 256 bytes or fewer (no extended space) are returned unchanged.
    """
    cs = ConfigSpace(config_hex)
    size = len(cs)
    limit = min(size, PCI_EXT_CONFIG_SPACE_END)
    if size <= PCI_EXT_CAP_START or limit < USER_EXT_CAP_START + 4:
        return config_hex

    donor = bytes(cs[0:size])
    kept: List[CapabilityInfo] = [
        c
        for c in CapabilityWalker(cs).walk_extended_capabilities()
        if c.cap_id not in EXT_CAP_DROP and c.cap_id not in EXT_CAP_OMIT
    ]

    out = bytearray(size)
    out[:PCI_EXT_CAP_START] = donor[:PCI_EXT_CAP_START]
    dsn_hdr = (
        _DSN_CAP_ID | (1 << PCI_EXT_CAP_VERSION_SHIFT)
        | (USER_EXT_CAP_START << PCI_EXT_CAP_NEXT_PTR_SHIFT)
    )
    out[PCI_EXT_CAP_START : PCI_EXT_CAP_START + 4] = dsn_hdr.to_bytes(4, "little")

    # Plan placements first so next-pointers are known; drop caps that no
    # longer fit rather than overflowing the 4KB space.
    placements = []  # (cap, length, new_offset)
    cursor = USER_EXT_CAP_START
    for cap in kept:
        length = _body_length(cap, limit)
        length = min(length, size - cap.offset)
        if cursor + length > limit:
            break
        placements.append((cap, length, cursor))
        cursor += (length + 3) & ~3  # keep DWORD alignment

    for i, (cap, length, new_off) in enumerate(placements):
        out[new_off : new_off + length] = donor[cap.offset : cap.offset + length]
        nxt = placements[i + 1][2] if i + 1 < len(placements) else 0
        hdr = int.from_bytes(donor[cap.offset : cap.offset + 4], "little")
        hdr = (hdr & _HEADER_MASK) | (nxt << PCI_EXT_CAP_NEXT_PTR_SHIFT)
        out[new_off : new_off + 4] = hdr.to_bytes(4, "little")
        if cap.cap_id == _ARI_CAP_ID and length >= 8:
            reg = int.from_bytes(out[new_off + 4 : new_off + 8], "little")
            reg &= ~0xFF00  # clear Next Function Number (bits 15:8)
            out[new_off + 4 : new_off + 8] = reg.to_bytes(4, "little")

    return out.hex()
