"""The public canary that marks firmware built from unverified captures."""

import pytest
from pcileechfwgenerator.host_collect.canary import (
    CANARY_EMPTY_CHAIN_OFFSET,
    CANARY_MAGIC,
    CANARY_SIZE,
    apply_offline_canary,
    build_canary_block,
    encode_version,
    ext_capability_offsets,
    find_canary,
)
from pcileechfwgenerator.host_collect.snapshot import SnapshotError

from tests.snapshot_helpers import (
    config_with_ext_caps,
    config_with_nonzero_ext_space,
    make_config,
)

SHA = "ab" * 32


def test_block_layout():
    b = build_canary_block(SHA, "0.17.2")
    assert len(b) == CANARY_SIZE == 32
    assert b[0:4] == (0x0001000B).to_bytes(4, "little")
    assert b[4:8] == (0x02010000).to_bytes(4, "little")
    assert b[8:24] == CANARY_MAGIC == b"PCILEECH-OFFLINE"
    assert b[24:28] == ((0 << 16) | (17 << 8) | 2).to_bytes(4, "little")
    assert b[28:32] == bytes.fromhex("abababab")


def test_empty_chain_places_at_0x10c():
    out = apply_offline_canary(bytes(256), SHA, "1.0.0")
    assert len(out) == 4096 and find_canary(out) == 0x10C
    assert CANARY_EMPTY_CHAIN_OFFSET == 0x10C


def test_all_ones_header_is_an_empty_chain():
    cfg = bytearray(make_config())
    cfg[0x100:0x104] = b"\xff" * 4
    assert ext_capability_offsets(bytes(cfg)) == []


def test_appends_after_existing_chain():
    cfg = config_with_ext_caps([(0x100, 0x0001), (0x150, 0x0003)])  # AER, DSN
    out = apply_offline_canary(cfg, SHA, "1.0.0")
    off = find_canary(out)
    assert off == 0xFE0
    assert int.from_bytes(out[0x150:0x154], "little") >> 20 == 0xFE0
    assert out[:0x150] == cfg[:0x150]
    assert out[0x154:0xFE0] == cfg[0x154:0xFE0]


def test_candidate_must_clear_last_capability_by_0x80():
    cfg = config_with_ext_caps([(0x100, 0x0001), (0xF80, 0x0003)])
    with pytest.raises(SnapshotError, match="no free extended config space"):
        apply_offline_canary(cfg, SHA, "1.0.0")


def test_full_ext_space_raises():
    with pytest.raises(SnapshotError, match="no free extended config space"):
        apply_offline_canary(config_with_nonzero_ext_space(), SHA, "1.0.0")


@pytest.mark.parametrize(
    "caps",
    [
        [(0x100, 1, 0x100)],  # self loop
        [(0x100, 1, 0x150), (0x150, 3, 0x100)],  # two-node cycle
        [(0x100, 1, 0x152)],  # unaligned
        [(0x100, 1, 0x0FC)],  # below extended space
    ],
)
def test_malformed_chain_raises(caps):
    with pytest.raises(SnapshotError, match="malformed extended capability chain"):
        apply_offline_canary(config_with_ext_caps(caps), SHA, "1.0.0")


def test_find_canary_tolerates_malformed_chain():
    assert find_canary(config_with_ext_caps([(0x100, 1, 0x100)])) is None


def test_input_not_mutated_and_idempotence_refused():
    cfg = bytes(4096)
    out = apply_offline_canary(cfg, SHA, "1.0.0")
    assert cfg == bytes(4096)
    with pytest.raises(SnapshotError, match="already carries"):
        apply_offline_canary(out, SHA, "1.0.0")


def test_live_config_has_no_canary():
    assert find_canary(make_config()) is None
    assert find_canary(config_with_ext_caps([(0x100, 1), (0x150, 3)])) is None


def test_magic_without_header_is_not_a_canary():
    cfg = bytearray(4096)
    cfg[0x10C + 8 : 0x10C + 24] = CANARY_MAGIC
    assert find_canary(bytes(cfg)) is None


@pytest.mark.parametrize(
    "version,expected",
    [
        ("dev", 0),
        ("", 0),
        ("1.0.0", 1 << 16),
        ("0.17.0.post1.dev5+g4fb977171", 17 << 8),
        ("2.3", (2 << 16) | (3 << 8)),
    ],
)
def test_encode_version(version, expected):
    assert encode_version(version) == expected
