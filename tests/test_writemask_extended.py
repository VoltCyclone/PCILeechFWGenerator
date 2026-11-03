"""Writemask: the cloned extended region (0x100-0xFFF) must be read-only (#681).

The shadow BRAM write path is plain masked read/write with no RW1C hardware,
so any writable extended bit would latch a write-1-to-clear instead of
clearing it.
"""

from pathlib import Path

from pcileechfwgenerator.device_clone.writemask_generator import WritemaskGenerator
from tests.test_extended_config import _i350_donor_hex
from tests.test_extended_config_coe import _parse_coe_dwords, _render


def _generate_writemask_from_donor(donor_hex: str, tmp_path: Path) -> str:
    cfg = tmp_path / "cfgspace.coe"
    wm = tmp_path / "writemask.coe"
    cfg.write_text(_render(donor_hex), encoding="utf-8")
    WritemaskGenerator().generate_writemask(cfg, wm)
    return wm.read_text(encoding="utf-8")


def test_cloned_extended_region_is_read_only(tmp_path):
    wm = _generate_writemask_from_donor(_i350_donor_hex(), tmp_path)
    dwords = _parse_coe_dwords(wm)
    assert all(dwords[i] == 0 for i in range(0x100 // 4, 0x1000 // 4))


def test_standard_region_still_has_writable_bits(tmp_path):
    wm = _generate_writemask_from_donor(_i350_donor_hex(), tmp_path)
    dwords = _parse_coe_dwords(wm)
    assert any(dwords[i] != 0 for i in range(0x40 // 4, 0x100 // 4))


def test_writemask_dict_0x000a_maps_to_rcrb_not_vsec():
    from pcileechfwgenerator.device_clone.writemask_constants import WRITEMASK_DICT

    from pcileechfwgenerator.device_clone.writemask_constants import (
        WRITE_PROTECTED_BITS_VSEC,
    )

    assert WRITEMASK_DICT.get("0x000A") != WRITEMASK_DICT.get("0x000B")
    assert WRITEMASK_DICT.get("0x000A") != WRITE_PROTECTED_BITS_VSEC
    assert WRITEMASK_DICT.get("0x000B") == WRITE_PROTECTED_BITS_VSEC
