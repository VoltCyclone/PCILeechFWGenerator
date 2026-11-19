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
    assert dwords[0x44 // 4] == 0x00000103  # PMCSR
    assert dwords[0x50 // 4] == 0xC0000000  # MSI-X control
    assert dwords[0x54 // 4 : 0x5C // 4] == [0, 0]  # Table and PBA
    assert dwords[0x68 // 4] == 0x00007FFF  # Device Control, FLR RO


def test_extended_rcrb_and_vsec_both_remain_read_only():
    # RCRB -> VSEC: even all-one payloads must not grant extended writes.
    image = {i: 0xFFFFFFFF for i in range(64, 1024)}
    image[64] = 0x1101000A
    image[68] = 0x0001000B
    mask = WritemaskGenerator().build_writemask(image)
    assert mask[64:] == [0] * 960
