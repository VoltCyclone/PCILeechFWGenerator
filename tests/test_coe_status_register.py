"""Status register (0x06-0x07) in pcileech_cfgspace.coe is cloned from the donor.

The old template hard-coded the Status register to 0x0210, which fabricated a
DEVSEL-timing bit (0x0200) that real PCIe endpoints never set (DEVSEL/66MHz/
FastB2B are RsvdZ on PCIe). The real I350 donor reports Status=0x0010. The
template now clones the donor's real status, forces the Capabilities-List bit
(the chain is always emitted), and clears runtime / RW1C latched bits.

COE DWORDs are the big-endian text of a little-endian config-space DWORD, so
the DWORD at config offset 0x04 packs command in [15:0] and status in [31:16].
"""

from copy import deepcopy

from tests.test_coe_error_injection import (
    _minimal_base_context,
    _render_cfgspace_coe,
)
from tests.test_extended_config_coe import _parse_coe_dwords


def _raw_with_status(status: int) -> str:
    """256-byte config image (hex) with a given Status register at 0x06-0x07."""
    raw = bytearray(256)
    raw[0:2] = (0x8086).to_bytes(2, "little")  # vendor
    raw[2:4] = (0x1521).to_bytes(2, "little")  # device (I350)
    raw[6:8] = (status & 0xFFFF).to_bytes(2, "little")
    return raw.hex()


def _render(status=None):
    ctx = deepcopy(_minimal_base_context())
    if status is not None:
        ctx["config_space"] = {"raw_data": _raw_with_status(status)}
    return _parse_coe_dwords(_render_cfgspace_coe(ctx))


def _status_of(dwords):
    return (dwords[0x04 // 4] >> 16) & 0xFFFF


def _command_of(dwords):
    return dwords[0x04 // 4] & 0xFFFF


def test_pcie_donor_status_has_no_fabricated_devsel():
    # Real PCIe donor (I350) reports 0x0010; the old hard-coded 0x0210 invented
    # DEVSEL timing bit 0x0200.
    status = _status_of(_render(0x0010))
    assert status == 0x0010, f"status=0x{status:04X}, expected 0x0010"
    assert status & 0x0200 == 0, "fabricated DEVSEL-timing bit present"


def test_status_forces_capabilities_list_bit():
    # Even if a donor dump lacks the caps bit, the chain is always emitted.
    assert _status_of(_render(0x0000)) & 0x0010


def test_status_clears_runtime_and_rw1c_latched_bits():
    # Interrupt Status (0x0008), Master Data Parity (0x0100), Signaled SERR
    # (0x4000) and Detected Parity (0x8000) are runtime/RW1C -> read 0 at reset.
    dirty = 0x0010 | 0x0008 | 0x0100 | 0x4000 | 0x8000
    assert _status_of(_render(dirty)) == 0x0010


def test_status_preserves_donor_static_bits():
    # 66 MHz Capable (0x0020) + Fast B2B (0x0080) are static RO bits: cloned.
    assert _status_of(_render(0x00A0)) == 0x00B0  # 0x00A0 | caps 0x0010


def test_status_defaults_to_caps_only_without_donor_data():
    assert _status_of(_render(None)) == 0x0010


def test_command_register_unchanged():
    # Command stays 0x0006 (Memory Space + Bus Master) -- functional, not cloned.
    assert _command_of(_render(0x0010)) == 0x0006
    assert _command_of(_render(None)) == 0x0006
