#!/usr/bin/env python3
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from src.device_clone.overlay_mapper import OverlayMapper


def _make_config_and_caps():
    # Minimal config space + capabilities map mirroring existing test setup
    config_space = {}
    # Capabilities pointer (not used by mapper here but kept for realism)
    config_space[0x0D] = 0x00000040

    # Power Management at 0x40
    config_space[0x10] = 0x48010001
    config_space[0x11] = 0x00000003

    # MSI at 0x48
    config_space[0x12] = 0x50810005
    config_space[0x13] = 0xFEE00000
    config_space[0x14] = 0x00000000

    # PCIe at 0x60 (for partial mask include test)
    config_space[0x18] = 0x00000000
    config_space[0x19] = 0x00000000

    # Extended AER at 0x100
    config_space[0x40] = 0x14010001

    capabilities = {
        "0x01": 0x40,  # PM
        "0x05": 0x48,  # MSI
        "0x10": 0x60,  # PCIe
        "0x0001": 0x100,  # AER (extended)
    }
    return config_space, capabilities


def _overlay_regs(mapper, config_space, capabilities):
    out = mapper.generate_overlay_map(config_space, capabilities)
    return set(reg for reg, _ in out["OVERLAY_MAP"]), dict(
        out["OVERLAY_MAP"]
    )  # (set of reg nums, map to mask)


def test_includes_aer_rw1c_even_with_full_mask():
    config_space, capabilities = _make_config_and_caps()
    mapper = OverlayMapper()

    regs, masks = _overlay_regs(mapper, config_space, capabilities)

    # AER base 0x100, RW1C at offsets 0x04 and 0x10
    aer_usts_reg = (0x100 + 0x04) // 4
    aer_csts_reg = (0x100 + 0x10) // 4

    assert aer_usts_reg in regs, "AER Uncorrectable Error Status must be included"
    assert aer_csts_reg in regs, "AER Correctable Error Status must be included"

    # Masks should be full for RW1C status registers per definitions
    assert masks[aer_usts_reg] == 0xFFFFFFFF
    assert masks[aer_csts_reg] == 0xFFFFFFFF


def test_includes_partial_mask_pcie_device_ctrl_status():
    config_space, capabilities = _make_config_and_caps()
    mapper = OverlayMapper()

    regs, _ = _overlay_regs(mapper, config_space, capabilities)

    # PCIe Device Control/Status at cap 0x60 + 0x08
    pcie_dcs_reg = (0x60 + 0x08) // 4
    assert (
        pcie_dcs_reg in regs
    ), "PCIe Device Control/Status (partial mask) should be included"


def test_excludes_read_only_aer_header_logs():
    config_space, capabilities = _make_config_and_caps()
    mapper = OverlayMapper()

    regs, _ = _overlay_regs(mapper, config_space, capabilities)

    # AER Header Log 1 at 0x100 + 0x1C has mask 0 (RO) and should be excluded
    aer_hdr1_reg = (0x100 + 0x1C) // 4
    assert aer_hdr1_reg not in regs, "AER header logs (RO) must be excluded"
