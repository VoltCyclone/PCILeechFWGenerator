"""The staged pcileech_fifo.sv must serve the shadow config space.

Upstream resets rw[203] (CFGTLP ZERO DATA) to 1 so shadow reads return zero,
and rw[206] (CFGTLP PCIE WRITE ENABLE) to 0 so host writes are dropped before
the writemask is applied. The cloned extended capabilities (0x10C+) and the
writemask only take effect with rw[203]=0 / rw[206]=1, so the generator always
patches both in the staged copy (never the submodule).
"""

import re
from pathlib import Path

import pytest
from pcileechfwgenerator.vivado_handling.fifo_donor_patcher import (
    DonorIDs,
    FifoPatchError,
    apply_fifo_donor_patch,
    enable_shadow_cfgspace,
    patch_pcileech_fifo,
)

ROOT = Path(__file__).resolve().parents[1]
BOARD_FIFOS = sorted((ROOT / "lib" / "voltcyclone-fpga").rglob("pcileech_fifo.sv"))

DONOR = DonorIDs(
    vendor_id=0x8086,
    device_id=0x1521,
    subsystem_vendor_id=0x8086,
    subsystem_id=0x0001,
    revision_id=0x01,
)

SNIPPET = (
    "rw[202]     <= 1'b1;   //       CFGTLP PROCESSING ENABLE\n"
    "rw[203]     <= 1'b1;   //       CFGTLP ZERO DATA\n"
    "rw[204]     <= 1'b1;   //       CFGTLP FILTER TLP FROM USER\n"
    "rw[206]     <= 1'b0;   //       CFGTLP PCIE WRITE ENABLE\n"
)


def _reset_value(text: str, bit: int) -> str:
    values = re.findall(rf"rw\[{bit}\]\s*<=\s*1'b([01])", text)
    assert len(values) == 1, values
    return values[0]


def test_enable_rewrites_both_bits_and_keeps_comments():
    out = enable_shadow_cfgspace(SNIPPET)
    assert _reset_value(out, 203) == "0"
    assert _reset_value(out, 206) == "1"
    assert _reset_value(out, 202) == "1"
    assert _reset_value(out, 204) == "1"
    assert "CFGTLP ZERO DATA" in out and "CFGTLP PCIE WRITE ENABLE" in out


def test_enable_is_idempotent():
    once = enable_shadow_cfgspace(SNIPPET)
    assert enable_shadow_cfgspace(once) == once


@pytest.mark.parametrize("missing", [203, 206])
def test_missing_anchor_raises(missing):
    text = "\n".join(ln for ln in SNIPPET.splitlines() if f"rw[{missing}]" not in ln)
    with pytest.raises(FifoPatchError):
        enable_shadow_cfgspace(text)


def test_duplicate_anchor_raises():
    with pytest.raises(FifoPatchError):
        enable_shadow_cfgspace(SNIPPET + SNIPPET)


@pytest.mark.skipif(not BOARD_FIFOS, reason="voltcyclone-fpga submodule absent")
@pytest.mark.parametrize("fifo", BOARD_FIFOS, ids=lambda p: p.parent.parent.name)
def test_every_board_fifo_is_patched(fifo):
    out = patch_pcileech_fifo(fifo.read_text(), DONOR)
    assert _reset_value(out, 203) == "0"
    assert _reset_value(out, 206) == "1"


@pytest.mark.skipif(not BOARD_FIFOS, reason="voltcyclone-fpga submodule absent")
def test_apply_patches_staged_copy_only(tmp_path):
    source = next(p for p in BOARD_FIFOS if "PCIeSquirrel" in str(p))
    original = source.read_text()
    staged = tmp_path / "pcileech_fifo.sv"
    staged.write_text(original)
    summary = apply_fifo_donor_patch(tmp_path, DONOR)
    assert summary["changed"] is True
    assert _reset_value(staged.read_text(), 203) == "0"
    assert _reset_value(staged.read_text(), 206) == "1"
    assert source.read_text() == original
