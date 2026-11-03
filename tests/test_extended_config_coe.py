"""COE rendering of the relocated extended capability chain (#681)."""

import re
from copy import deepcopy
from typing import List

from pcileechfwgenerator.device_clone.extended_config import (
    build_relocated_extended_config,
)
from pcileechfwgenerator.pci_capability.core import ConfigSpace
from tests.test_coe_error_injection import (
    _minimal_base_context,
    _render_cfgspace_coe,
)
from tests.test_extended_config import _header_only_hex, _i350_donor_hex


def _parse_coe_dwords(coe_text: str) -> List[int]:
    """Parse a .coe into DWORDs; index i is byte offset i*4."""
    lines = [ln for ln in coe_text.splitlines() if not ln.strip().startswith(";")]
    body = "\n".join(lines)
    vec = body.split("memory_initialization_vector=", 1)[1]
    vec = vec.split(";", 1)[0]
    tokens = [t.strip() for t in vec.split(",") if t.strip()]
    # Tokens are normally 8 hex chars; the template's low-header section
    # currently emits one short token (offset 0x08), which int() tolerates.
    for t in tokens:
        assert re.fullmatch(r"[0-9A-Fa-f]{1,8}", t), f"bad COE token {t!r}"
    assert len(tokens) == 1024, len(tokens)
    return [int(t, 16) for t in tokens]


def _coe_chain_ids(dwords: List[int], start: int) -> List[int]:
    """Walk the extended capability chain in COE DWORDs from `start`."""
    ids, off, seen = [], start, set()
    while 0x100 <= off < 0x1000 and off not in seen:
        seen.add(off)
        hdr = dwords[off // 4]
        if hdr == 0:
            break
        ids.append(hdr & 0xFFFF)
        off = (hdr >> 20) & 0xFFC
    return ids


def _render(raw_hex: str) -> str:
    ctx = deepcopy(_minimal_base_context())
    ctx["config_space"] = {"raw_data": raw_hex}
    return _render_cfgspace_coe(ctx)


def test_coe_emits_relocated_chain_at_0x10c():
    relocated = build_relocated_extended_config(_i350_donor_hex())
    dwords = _parse_coe_dwords(_render(relocated))

    # Endianness check: COE DWORDs must equal ConfigSpace little-endian reads.
    cs = ConfigSpace(relocated)
    for off in range(0x100, 0x1000, 4):
        assert dwords[off // 4] == cs.read_dword(off), hex(off)

    assert dwords[0x10C // 4] & 0xFFFF == 0x0001  # AER
    assert _coe_chain_ids(dwords, 0x10C) == [0x0001, 0x000E, 0x0017, 0x0018, 0x000D]


def test_coe_standard_capabilities_sit_at_absolute_offsets():
    dwords = _parse_coe_dwords(_render(_i350_donor_hex()))
    assert dwords[0x38 // 4] == 0  # reserved
    assert dwords[0x3C // 4] == 0x0000010B  # interrupt line/pin
    assert dwords[0x40 // 4] & 0xFF == 0x01  # PM
    assert dwords[0x60 // 4] & 0xFF == 0x10  # PCIe
    # 0xA0 is reserved standard space (zeroed): AER is an extended capability
    # (ext cap ID 0x0001) cloned into extended config space at >=0x100, not a
    # standard-space header that would mis-read as a second Cap ID 0x01.
    assert dwords[0xA0 // 4] == 0x00000000


def test_coe_256_byte_donor_has_zero_extended_region():
    dwords = _parse_coe_dwords(_render(_header_only_hex()))
    assert dwords[0x100 // 4] == 0


def test_coe_standard_region_is_256_bytes_without_advanced_features():
    ctx = deepcopy(_minimal_base_context())
    ctx["device_config"]["enable_advanced_features"] = False
    ctx["config_space"] = {
        "raw_data": build_relocated_extended_config(_i350_donor_hex())
    }
    dwords = _parse_coe_dwords(_render_cfgspace_coe(ctx))
    assert _coe_chain_ids(dwords, 0x10C) == [0x0001, 0x000E, 0x0017, 0x0018, 0x000D]
