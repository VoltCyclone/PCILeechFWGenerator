"""Standard-header capability-list correctness in pcileech_cfgspace.coe.

COE DWORDs are the big-endian text of a little-endian config-space DWORD, so
the byte at config offset N is the low byte of the DWORD at N & ~3.

Covers three bugs:
  * Capabilities Pointer must land in byte 0x34 (not 0x37).
  * The capability chain must stay walkable to MSI-X and PCIe for an MSI-X
    donor (PM must not point at the zeroed MSI slot at 0x48).
  * The Status Capabilities-List bit must be set whenever the chain exists,
    independent of DMA enablement.
"""

from copy import deepcopy

from tests.test_coe_error_injection import (
    _minimal_base_context,
    _render_cfgspace_coe,
)
from tests.test_extended_config_coe import _parse_coe_dwords

CAP_PTR_OFFSET = 0x34
FIRST_CAP_OFFSET = 0x40  # CAP_OFFSET_PM
MSI_OFFSET = 0x48
MSIX_OFFSET = 0x50
PCIE_OFFSET = 0x60

CAP_ID_PM = 0x01
CAP_ID_MSI = 0x05
CAP_ID_MSIX = 0x11
CAP_ID_PCIE = 0x10


def _walk_std_caps(dwords):
    """Walk the standard capability linked list from the 0x34 pointer.

    Returns a list of (offset, cap_id, next_ptr). Each standard cap header's
    low byte is Cap ID and the next byte is the next-pointer.
    """
    off = dwords[CAP_PTR_OFFSET // 4] & 0xFF
    seen, chain = set(), []
    while off and 0x40 <= off < 0x100 and off not in seen:
        seen.add(off)
        hdr = dwords[off // 4]
        chain.append((off, hdr & 0xFF, (hdr >> 8) & 0xFF))
        off = (hdr >> 8) & 0xFF
    return chain


def _render(num_vectors=1, enable_dma=True):
    ctx = deepcopy(_minimal_base_context())
    ctx["msix_config"] = {"num_vectors": num_vectors}
    ctx["device_config"]["enable_dma_operations"] = enable_dma
    return _parse_coe_dwords(_render_cfgspace_coe(ctx))


def test_capabilities_pointer_in_byte_0x34():
    dword = _render()[CAP_PTR_OFFSET // 4]
    assert dword & 0xFF == FIRST_CAP_OFFSET, (
        f"byte 0x34 = 0x{dword & 0xFF:02X}, expected 0x{FIRST_CAP_OFFSET:02X}; "
        f"full DWORD 0x{dword:08X}"
    )
    assert dword == FIRST_CAP_OFFSET, (
        f"cap-pointer DWORD 0x{dword:08X}, expected 0x{FIRST_CAP_OFFSET:08X}"
    )


def test_msix_donor_chain_reaches_msix_and_pcie():
    chain = _walk_std_caps(_render(num_vectors=8))
    ids = {cap_id for _, cap_id, _ in chain}
    offs = {off for off, _, _ in chain}
    assert CAP_ID_MSIX in ids, f"MSI-X unreachable; chain={chain}"
    assert CAP_ID_PCIE in ids, f"PCIe unreachable; chain={chain}"
    assert MSIX_OFFSET in offs and PCIE_OFFSET in offs, f"chain={chain}"


def test_msi_donor_chain_reaches_pcie():
    chain = _walk_std_caps(_render(num_vectors=0))
    ids = {cap_id for _, cap_id, _ in chain}
    assert CAP_ID_MSI in ids, f"MSI unreachable; chain={chain}"
    assert CAP_ID_PCIE in ids, f"PCIe unreachable; chain={chain}"


def test_std_chain_terminates_at_pcie_without_malformed_aer():
    # AER is a PCIe *extended* capability (ext cap ID 0x0001) valid only at
    # config offset >= 0x100, where the extended-config pipeline clones the
    # donor's real AER. The 0xA0 block in standard space reads as a second
    # standard Cap ID 0x01 (Power Management) if the PCIe cap links to it via
    # an 8-bit next-pointer. The standard chain must end at the PCIe cap so no
    # OS walks into that malformed block -- on both MSI-X and MSI donors.
    for num_vectors in (8, 0):
        chain = _walk_std_caps(_render(num_vectors=num_vectors))
        offs = {off for off, _, _ in chain}
        assert 0xA0 not in offs, f"chain walks into misplaced AER at 0xA0: {chain}"
        pm_caps = [off for off, cid, _ in chain if cid == CAP_ID_PM]
        assert len(pm_caps) == 1, f"expected one PM cap, got {pm_caps}: {chain}"
        last_off, last_id, last_next = chain[-1]
        assert last_id == CAP_ID_PCIE and last_next == 0x00, (
            f"chain must terminate at PCIe (next=0x00): {chain}"
        )


def test_capabilities_list_bit_set_regardless_of_dma():
    # Status register is bytes 0x06-0x07 = bits [31:16] of the DWORD at 0x04.
    for enable_dma in (True, False):
        dword = _render(enable_dma=enable_dma)[0x04 // 4]
        status = (dword >> 16) & 0xFFFF
        assert status & 0x0010, (
            f"Capabilities-List bit clear with enable_dma={enable_dma}; "
            f"status=0x{status:04X}, DWORD 0x{dword:08X}"
        )
