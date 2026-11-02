"""Tests for relocating the donor extended capability chain to 0x10C."""

from typing import List, Tuple

import pytest

from pcileechfwgenerator.device_clone.extended_config import (
    EXT_CAP_DROP,
    EXT_CAP_OMIT,
    USER_EXT_CAP_START,
    build_relocated_extended_config,
)
from pcileechfwgenerator.pci_capability.core import CapabilityWalker, ConfigSpace

I350_EXT = {  # donor offset -> (cap_id, donor_next); shape of an I350 capture
    0x100: (0x0001, 0x140),  # AER
    0x140: (0x0003, 0x150),  # DSN (dropped)
    0x150: (0x000E, 0x1A0),  # ARI
    0x1A0: (0x0017, 0x1C0),  # TPH
    0x1C0: (0x0018, 0x1D0),  # LTR
    0x1D0: (0x000D, 0x000),  # ACS (terminal)
}


def _header_bytes() -> bytearray:
    """256-byte type-0 header with the Status capabilities-list bit set."""
    hdr = bytearray(256)
    hdr[0x06] = 0x10  # Status bit 4: capabilities list present
    hdr[0x0E] = 0x00  # header type 0
    hdr[0x34] = 0x00  # no standard caps (extended walk does not need them)
    return hdr


def _header_only_hex() -> str:
    return _header_bytes().hex()


def _ext_header(cap_id: int, nxt: int, version: int = 1) -> int:
    return cap_id | (version << 16) | (nxt << 20)


def _build(chain: List[Tuple[int, int, int]]) -> str:
    """chain: (offset, cap_id, next). Body bytes are 0xA0+index pattern."""
    img = bytearray(4096)
    img[:256] = _header_bytes()
    for idx, (off, cap_id, nxt) in enumerate(chain):
        img[off : off + 4] = _ext_header(cap_id, nxt).to_bytes(4, "little")
        for b in range(4, 8):
            img[off + b] = (0xA0 + idx) & 0xFF
    return img.hex()


def _i350_donor_hex() -> str:
    chain = [(off, cid, nxt) for off, (cid, nxt) in I350_EXT.items()]
    img = bytearray.fromhex(_build(chain))
    # ARI capability register with a non-zero Next Function (bits 15:8).
    img[0x154:0x158] = (0x00000500).to_bytes(4, "little")
    return img.hex()


def _donor_hex_with_caps(caps: List[Tuple[int, int]]) -> str:
    """caps: (cap_id, _) laid out 0x40 apart from 0x100; last is terminal."""
    chain = []
    for i, (cap_id, _) in enumerate(caps):
        off = 0x100 + i * 0x40
        nxt = 0x100 + (i + 1) * 0x40 if i + 1 < len(caps) else 0
        chain.append((off, cap_id, nxt))
    return _build(chain)


def _walk_ids(hex_image: str) -> List[Tuple[int, int, int]]:
    cs = ConfigSpace(hex_image)
    return [
        (c.offset, c.cap_id, c.next_ptr)
        for c in CapabilityWalker(cs).walk_extended_capabilities()
    ]


def test_relocates_chain_to_0x10c_in_donor_order():
    out = build_relocated_extended_config(_i350_donor_hex())
    walk = _walk_ids(out)
    user = [w for w in walk if w[0] >= USER_EXT_CAP_START]
    assert [w[1] for w in user] == [0x0001, 0x000E, 0x0017, 0x0018, 0x000D]
    assert user[0][0] == USER_EXT_CAP_START
    for cur, nxt in zip(user, user[1:]):
        assert cur[2] == nxt[0]
    assert user[-1][2] == 0


def test_dsn_shaped_header_at_0x100():
    out = build_relocated_extended_config(_i350_donor_hex())
    cs = ConfigSpace(out)
    hdr = cs.read_dword(0x100)
    assert hdr & 0xFFFF == 0x0003
    assert (hdr >> 16) & 0xF == 1
    assert hdr >> 20 == USER_EXT_CAP_START


def test_low_256_bytes_unchanged_and_length_preserved():
    donor = _i350_donor_hex()
    out = build_relocated_extended_config(donor)
    assert len(out) == len(donor)
    assert out[:512] == donor[:512]


def test_drops_donor_dsn():
    out = build_relocated_extended_config(_i350_donor_hex())
    assert all(
        cap_id != 0x0003 for off, cap_id, _ in _walk_ids(out) if off >= USER_EXT_CAP_START
    )


def test_zeroes_ari_next_function():
    out = build_relocated_extended_config(_i350_donor_hex())
    cs = ConfigSpace(out)
    ari = next(
        c for c in CapabilityWalker(cs).walk_extended_capabilities() if c.cap_id == 0x000E
    )
    cap_reg = cs.read_dword(ari.offset + 4)
    assert (cap_reg >> 8) & 0xFF == 0


def test_omits_unhonourable_caps():
    donor = _donor_hex_with_caps([(0x0001, 0), (0x0010, 0), (0x000D, 0)])
    out = build_relocated_extended_config(donor)
    ids = [cid for _, cid, _ in _walk_ids(out)]
    assert 0x0010 not in ids
    assert 0x0001 in ids and 0x000D in ids


def test_256_byte_donor_unchanged():
    donor = _header_only_hex()
    assert build_relocated_extended_config(donor) == donor


def test_only_dropped_dsn_yields_empty_user_chain():
    donor = _donor_hex_with_caps([(0x0003, 0)])
    out = build_relocated_extended_config(donor)
    cs = ConfigSpace(out)
    assert cs.read_dword(USER_EXT_CAP_START) == 0


def test_terminal_cap_body_is_clamped_to_4kb():
    # Terminal cap at the very end of the space: fixed-length copy must clamp.
    img = _build([(0x100, 0x0001, 0xFF8), (0xFF8, 0x000D, 0)])
    out = build_relocated_extended_config(img)
    assert len(out) == len(img)
    user = [w for w in _walk_ids(out) if w[0] >= USER_EXT_CAP_START]
    # AER's donor span (0x100..0xFF8) cannot fit after 0x10C, so it is dropped
    # rather than overflowing 4KB; the user chain is empty and terminated.
    assert user == []
    assert ConfigSpace(out).read_dword(USER_EXT_CAP_START) == 0


def test_chain_that_cannot_fit_is_truncated_not_overflowed():
    img = _build([(0x100, 0x0001, 0x140), (0x140, 0x000E, 0xFF0), (0xFF0, 0x000D, 0)])
    out = build_relocated_extended_config(img)
    assert len(out) == len(img)
    user = [w for w in _walk_ids(out) if w[0] >= USER_EXT_CAP_START]
    assert [w[1] for w in user] == [0x0001, 0x000E]
    assert 0x000D not in [w[1] for w in user]
    assert user[-1][2] == 0


def test_drop_and_omit_sets_are_pinned():
    assert EXT_CAP_DROP == frozenset({0x0003})
    assert EXT_CAP_OMIT == frozenset({0x0010, 0x000F, 0x0013, 0x001B, 0x0015, 0x001D})


@pytest.mark.parametrize("omit_id", sorted(EXT_CAP_OMIT))
def test_each_omitted_cap_is_removed(omit_id):
    donor = _donor_hex_with_caps([(0x0001, 0), (omit_id, 0), (0x000D, 0)])
    out = build_relocated_extended_config(donor)
    user = [w for w in _walk_ids(out) if w[0] >= USER_EXT_CAP_START]
    assert [w[1] for w in user] == [0x0001, 0x000D]


def _assert_in_bounds_and_terminates(donor: str) -> None:
    out = build_relocated_extended_config(donor)
    assert len(out) == len(donor)
    walk = _walk_ids(out)  # walker visits each offset once; must return
    assert all(0x100 <= off < 0x1000 and off % 4 == 0 for off, _, _ in walk)
    assert all(nxt == 0 or 0x100 <= nxt < 0x1000 for _, _, nxt in walk)
    user = [w for w in walk if w[0] >= USER_EXT_CAP_START]
    if user:
        assert user[-1][2] == 0


def test_cyclic_donor_pointer_terminates():
    img = _build([(0x100, 0x0001, 0x140), (0x140, 0x000E, 0x100)])
    _assert_in_bounds_and_terminates(img)


def test_unaligned_donor_pointer_terminates():
    img = _build([(0x100, 0x0001, 0x142), (0x140, 0x000E, 0)])
    _assert_in_bounds_and_terminates(img)
