#!/usr/bin/env python3
"""
Unit tests for src.pci_capability._pruning

Covers:
- apply_pruning_actions for standard and extended capabilities
- generate_capability_patches for removal and modification cases
"""

from typing import Dict

import pytest


from src.pci_capability._pruning import (
    apply_pruning_actions,
    generate_capability_patches,
)
from src.pci_capability.core import ConfigSpace
from src.pci_capability.constants import (
    PCI_CAPABILITIES_POINTER,
    PCI_STATUS_CAP_LIST,
    PCI_STATUS_REGISTER,
    PM_CAP_CAPABILITIES_OFFSET,
    PCIE_CAP_LINK_CONTROL_OFFSET,
    PCIE_CAP_DEVICE_CONTROL2_OFFSET,
    RBAR_CAPABILITY_REGISTER_OFFSET,
)
from src.pci_capability.types import (
    CapabilityType,
    PCICapabilityID,
    PCIExtCapabilityID,
    PatchInfo,
    PruningAction,
)


def _mk_empty_cfg(size: int = 0x200) -> bytearray:
    """Create an empty config space of the given size (bytes)."""
    assert size >= 0x100  # need room for extended capabilities
    return bytearray([0x00] * size)


def _mk_cfg_with_caps() -> ConfigSpace:
    """Build a synthetic configuration space with a small chain of caps.

    Standard caps at 0x50 (PM), 0x60 (PCI-X, two-byte header), 0x70 (PCIe)
    Extended caps at 0x100 (ACS -> 0x140), 0x140 (DPC -> 0x180),
    0x180 (RBAR -> end)
    """
    data = _mk_empty_cfg()

    # Status: set capabilities list bit
    data[PCI_STATUS_REGISTER] = PCI_STATUS_CAP_LIST & 0xFF
    data[PCI_STATUS_REGISTER + 1] = (PCI_STATUS_CAP_LIST >> 8) & 0xFF

    # Capabilities pointer
    data[PCI_CAPABILITIES_POINTER] = 0x50

    # Standard capability: PM at 0x50, next -> 0x60
    pm_off = 0x50
    data[pm_off] = PCICapabilityID.POWER_MANAGEMENT.value
    data[pm_off + 1] = 0x60
    # PM Capabilities word at +2: set non-zero so modify is visible
    data[pm_off + PM_CAP_CAPABILITIES_OFFSET] = 0x34
    data[pm_off + PM_CAP_CAPABILITIES_OFFSET + 1] = 0x12

    # Standard capability: PCI-X (two-byte header) at 0x60, next -> 0x70
    pcix_off = 0x60
    data[pcix_off] = 0x07  # PCI-X
    data[pcix_off + 1] = 0xAA  # reserved/impl-defined for 2-byte header
    data[pcix_off + 2] = 0x70  # next pointer stored at +2 for 2-byte header caps

    # Standard capability: PCIe at 0x70, next -> 0x00 (end)
    pcie_off = 0x70
    data[pcie_off] = PCICapabilityID.PCI_EXPRESS.value
    data[pcie_off + 1] = 0x00
    # Link Control at +0x10: set ASPM bits and some other bits (0x00F3 -> ASPM 0b11)
    link_ctrl_off = pcie_off + PCIE_CAP_LINK_CONTROL_OFFSET
    data[link_ctrl_off] = 0xF3 & 0xFF
    data[link_ctrl_off + 1] = (0x00F3 >> 8) & 0xFF
    # Device Control 2 at +0x28: set to 0xFFFF so OBFF/LTR bits get cleared
    devctl2_off = pcie_off + PCIE_CAP_DEVICE_CONTROL2_OFFSET
    data[devctl2_off] = 0xFF
    data[devctl2_off + 1] = 0xFF

    # Extended capabilities start at 0x100
    # Helper to write extended header
    
    def _write_ext_hdr(off: int, cap_id: int, ver: int, next_ptr: int) -> None:
        hdr = (next_ptr << 20) | (ver << 16) | cap_id
        data[off : off + 4] = hdr.to_bytes(4, "little")

    # ACS at 0x100 -> next 0x140
    acs_off = 0x100
    _write_ext_hdr(
        acs_off,
        PCIExtCapabilityID.ACCESS_CONTROL_SERVICES.value,
        1,
        0x140,
    )
    # ACS control word at +6: set non-zero so zeroing is visible
    data[acs_off + 6] = 0xBE
    data[acs_off + 7] = 0xEF

    # DPC at 0x140 -> next 0x180
    dpc_off = 0x140
    _write_ext_hdr(
        dpc_off, PCIExtCapabilityID.DOWNSTREAM_PORT_CONTAINMENT.value, 1, 0x180
    )
    data[dpc_off + 6] = 0xAD
    data[dpc_off + 7] = 0xDE

    # RBAR at 0x180 -> next 0x000
    rbar_off = 0x180
    _write_ext_hdr(rbar_off, PCIExtCapabilityID.RESIZABLE_BAR.value, 1, 0)
    # RBAR capability at +8: set high bits so mask clears them
    rbar_cap_off = rbar_off + RBAR_CAPABILITY_REGISTER_OFFSET
    rbar_val = 0xF2345678
    data[rbar_cap_off : rbar_cap_off + 4] = rbar_val.to_bytes(4, "little")

    return ConfigSpace(data.hex())


class TestApplyPruningActions:
    def test_standard_remove_middle_and_modify_pm_pcie(self):
        cfg = _mk_cfg_with_caps()

        # Actions: remove PCI-X at 0x60, modify PM at 0x50 and PCIe at 0x70
        actions: Dict[int, PruningAction] = {
            0x60: PruningAction.REMOVE,
            0x50: PruningAction.MODIFY,
            0x70: PruningAction.MODIFY,
        }

        apply_pruning_actions(cfg, actions)

        # Previous (PM) next pointer should now skip to 0x70
        assert cfg.read_byte(0x51) == 0x70
        # PM capabilities word set to D3hot only (0x0008)
        assert cfg.read_word(0x50 + PM_CAP_CAPABILITIES_OFFSET) == 0x0008

        # PCIe link control ASPM cleared: 0x00F3 -> 0x00F0
        assert cfg.read_word(0x70 + PCIE_CAP_LINK_CONTROL_OFFSET) == 0x00F0
    # PCIe device control 2 OBFF/LTR cleared: 0xFFFF -> 0x9BFF
        assert cfg.read_word(0x70 + PCIE_CAP_DEVICE_CONTROL2_OFFSET) == 0x9BFF

    def test_standard_remove_first_updates_cap_ptr_and_zeros_header(self):
        # Build minimal config: PM at 0x50 -> PCIe at 0x70
        cfg = _mk_cfg_with_caps()
        # Remove the first standard capability (PM at 0x50)
        actions: Dict[int, PruningAction] = {0x50: PruningAction.REMOVE}

        apply_pruning_actions(cfg, actions)

        # Capabilities pointer at 0x34 now points to 0x60 (original next of PM)
        assert cfg.read_byte(PCI_CAPABILITIES_POINTER) == 0x60
        # Original PM header bytes zeroed
        assert cfg.read_byte(0x50) == 0x00
        assert cfg.read_byte(0x51) == 0x00

    def test_extended_remove_and_modify(self):
        cfg = _mk_cfg_with_caps()

        # Remove DPC at 0x140; modify ACS at 0x100 and RBAR at 0x180
        actions: Dict[int, PruningAction] = {
            0x140: PruningAction.REMOVE,
            0x100: PruningAction.MODIFY,
            0x180: PruningAction.MODIFY,
        }

        apply_pruning_actions(cfg, actions)

        # ACS header should now point to RBAR (0x180)
        new_hdr = cfg.read_dword(0x100)
        cap_id = new_hdr & 0xFFFF
        ver = (new_hdr >> 16) & 0xF
        next_ptr = (new_hdr >> 20) & 0xFFF
        assert cap_id == PCIExtCapabilityID.ACCESS_CONTROL_SERVICES.value
        assert ver == 1
        assert next_ptr == 0x180

        # DPC region [0x140, 0x180) zeroed
        assert cfg.read_dword(0x140) == 0x00000000
        # ACS control cleared to 0
        assert cfg.read_word(0x100 + 6) == 0x0000
        # RBAR capability masked high bits: 0xF2345678 -> 0x02345678
        assert cfg.read_dword(0x180 + RBAR_CAPABILITY_REGISTER_OFFSET) == 0x02345678


class TestGenerateCapabilityPatches:
    def test_standard_remove_first_and_modify_pm(self):
        cfg = _mk_cfg_with_caps()
        actions: Dict[int, PruningAction] = {
            0x50: PruningAction.REMOVE,  # remove PM (first capability)
            # modify PCIe (note: only PM produces std-modify patch)
            0x70: PruningAction.MODIFY,
            0x60: PruningAction.KEEP,
        }

        patches = generate_capability_patches(cfg, actions)
        assert isinstance(patches, list)
        assert all(isinstance(p, PatchInfo) for p in patches)

        # Expect: REMOVE_STD_CAP for 0x50 and UPDATE_CAP_PTR at 0x34
        has_remove_pm = any(
            p.action == "REMOVE_STD_CAP" and p.offset == 0x50 for p in patches
        )
        has_update_ptr = any(
            p.action == "UPDATE_CAP_PTR"
            and p.offset == PCI_CAPABILITIES_POINTER
            and p.after_bytes.lower() == f"{0x60:02x}"
            for p in patches
        )
        assert has_remove_pm and has_update_ptr

    def test_extended_remove_and_modify_patches(self):
        cfg = _mk_cfg_with_caps()
        actions: Dict[int, PruningAction] = {
            0x140: PruningAction.REMOVE,  # DPC
            0x100: PruningAction.MODIFY,  # ACS
            0x180: PruningAction.MODIFY,  # RBAR
        }

        patches = generate_capability_patches(cfg, actions)

        # Expect REMOVE_EXT_CAP for 0x140 spanning until 0x180
        rem = next(
            p for p in patches if p.action == "REMOVE_EXT_CAP" and p.offset == 0x140
        )
        # Length should be (0x180 - 0x140) * 2 hex chars
        assert len(rem.before_bytes) == (0x180 - 0x140) * 2
        assert rem.after_bytes == "00" * (0x180 - 0x140)

        # Expect MODIFY_ACS at 0x100+6 and MODIFY_RBAR at 0x180+8
        has_mod_acs = any(
            p.action == "MODIFY_ACS" and p.offset == 0x100 + 6 for p in patches
        )
        has_mod_rbar = any(
            p.action == "MODIFY_RBAR"
            and p.offset == 0x180 + RBAR_CAPABILITY_REGISTER_OFFSET
            for p in patches
        )
        assert has_mod_acs and has_mod_rbar
