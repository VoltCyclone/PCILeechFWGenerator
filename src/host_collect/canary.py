"""Public canary marking firmware rendered from an unverified donor capture.

Builds from ``--from-capture`` (or a reused capture whose donor is absent)
cannot prove the donor's identity, so their config space carries a
documented Vendor-Specific Extended Capability containing the ASCII magic
``PCILEECH-OFFLINE``. Detect it by searching extended config space for the
magic; the VSEC ID is not the identifier because it is read under the
donor's vendor numbering. See docs/development/OFFLINE_CAPTURE.md.
"""

from __future__ import annotations

import re
from typing import Final

from .snapshot import SnapshotError

CANARY_MAGIC: Final[bytes] = b"PCILEECH-OFFLINE"
CANARY_SIZE: Final[int] = 32
# Every board XCI sets EXT_PCI_CFG_Space_Addr = 0x043: the first user-owned
# extended DWORD, which the core's own DSN capability at 0x100 links to.
CANARY_EMPTY_CHAIN_OFFSET: Final[int] = 0x10C

_EXT_START: Final[int] = 0x100
_EXT_LAST_DWORD: Final[int] = 0xFFC
_CONFIG_SIZE: Final[int] = 0x1000
_VSEC_ID: Final[int] = 0x000B
_SCAN_TOP: Final[int] = _CONFIG_SIZE - CANARY_SIZE  # 0xFE0
_MIN_GAP_AFTER_LAST_CAP: Final[int] = 0x80
_MAX_CHAIN_STEPS: Final[int] = (_CONFIG_SIZE - _EXT_START) // 4


def _dword(config: bytes, offset: int) -> int:
    return int.from_bytes(config[offset : offset + 4], "little")


def encode_version(version: str) -> int:
    """Encode the leading ``major.minor.patch`` integers as a u32; else 0."""
    match = re.match(r"(\d+)(?:\.(\d+))?(?:\.(\d+))?", version or "")
    if not match:
        return 0
    major, minor, patch = (int(part or 0) for part in match.groups())
    return (min(major, 0xFFFF) << 16) | (min(minor, 0xFF) << 8) | min(patch, 0xFF)


def build_canary_block(capture_sha256: str, version: str) -> bytes:
    """Return the 32-byte VSEC block with next pointer 0."""
    header = (1 << 16) | _VSEC_ID
    vsec_header = (CANARY_SIZE << 20) | (1 << 16) | 0x0000
    return b"".join(
        (
            header.to_bytes(4, "little"),
            vsec_header.to_bytes(4, "little"),
            CANARY_MAGIC,
            encode_version(version).to_bytes(4, "little"),
            bytes.fromhex(capture_sha256)[:4],
        )
    )


def ext_capability_offsets(config: bytes) -> list[int]:
    """Walk the extended capability chain from 0x100 in chain order.

    An all-zero or all-ones first header means no chain. Cycles, unaligned
    pointers and pointers outside 0x100-0xFFC raise SnapshotError.
    """
    if len(config) < _CONFIG_SIZE:
        return []
    if _dword(config, _EXT_START) in (0x00000000, 0xFFFFFFFF):
        return []
    offsets: list[int] = []
    offset = _EXT_START
    for _ in range(_MAX_CHAIN_STEPS):
        if offset in offsets:
            break  # cycle
        offsets.append(offset)
        next_offset = _dword(config, offset) >> 20
        if next_offset == 0:
            return offsets
        if next_offset & 3 or not _EXT_START <= next_offset <= _EXT_LAST_DWORD:
            break
        offset = next_offset
    raise SnapshotError("malformed extended capability chain")


def _is_canary_at(config: bytes, offset: int) -> bool:
    return (
        _dword(config, offset) & 0xFFFF == _VSEC_ID
        and config[offset + 8 : offset + 24] == CANARY_MAGIC
    )


def find_canary(config: bytes) -> int | None:
    """Return the offset of a well-formed canary reachable via the chain.

    With no chain, the anchor at 0x10C (reached on hardware through the
    core's DSN capability) is checked. A malformed chain yields None rather
    than an error, so live builds never fail on the check itself.
    """
    if len(config) < _CONFIG_SIZE:
        return None
    try:
        offsets = ext_capability_offsets(config)
    except SnapshotError:
        return None
    for offset in offsets or [CANARY_EMPTY_CHAIN_OFFSET]:
        if _is_canary_at(config, offset):
            return offset
    return None


def apply_offline_canary(config: bytes, capture_sha256: str, version: str) -> bytes:
    """Return a 4096-byte copy of ``config`` carrying the canary.

    Never mutates its input. Raises SnapshotError when the chain is malformed,
    no free 32-byte window exists, or the config already carries a canary.
    """
    if len(config) > _CONFIG_SIZE:
        raise SnapshotError("config space is larger than 4096 bytes")
    out = bytearray(config) + bytes(_CONFIG_SIZE - len(config))
    if find_canary(bytes(out)) is not None:
        raise SnapshotError("config space already carries the offline canary")
    offsets = ext_capability_offsets(bytes(out))
    block = build_canary_block(capture_sha256, version)
    if not offsets:
        target = CANARY_EMPTY_CHAIN_OFFSET
        if any(out[target : target + CANARY_SIZE]):
            raise SnapshotError("no free extended config space for offline canary")
    else:
        floor = max(offsets) + _MIN_GAP_AFTER_LAST_CAP
        target = next(
            (
                candidate
                for candidate in range(_SCAN_TOP, CANARY_EMPTY_CHAIN_OFFSET - 1, -4)
                if candidate >= floor
                and not any(out[candidate : candidate + CANARY_SIZE])
            ),
            None,
        )
        if target is None:
            raise SnapshotError("no free extended config space for offline canary")
        last = offsets[-1]
        header = _dword(out, last)
        out[last : last + 4] = ((header & 0x000FFFFF) | (target << 20)).to_bytes(
            4, "little"
        )
    out[target : target + CANARY_SIZE] = block
    return bytes(out)
