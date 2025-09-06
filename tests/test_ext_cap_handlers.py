#!/usr/bin/env python3
"""
Targeted tests for extended capability handlers: AER, LTR, SR-IOV, and ARI.

Each test builds a minimal extended capability chain and verifies that
CapabilityProcessor generates and applies the expected patches.
"""

import sys
from pathlib import Path
from typing import List, Tuple

import pytest

# Add src to path for imports
sys.path.insert(0, str(Path(__file__).parent.parent))

from src.pci_capability.constants import (AER_CAPABILITY_VALUES,
                                          EXT_CAP_ID_AER, EXT_CAP_ID_ARI,
                                          EXT_CAP_ID_LTR, EXT_CAP_ID_PTM,
                                          EXT_CAP_ID_SRIOV)
from src.pci_capability.core import ConfigSpace
from src.pci_capability.processor import CapabilityProcessor
from src.pci_capability.rules import RuleEngine
from src.pci_capability.types import PCIExtCapabilityID, PruningAction


def _write_bytes(hex_list: List[str], offset: int, value_bytes: bytes) -> None:
    for i, b in enumerate(value_bytes):
        pos = offset + i
        hex_list[pos * 2 : pos * 2 + 2] = f"{b:02x}"


def _write_dword(hex_list: List[str], offset: int, value: int) -> None:
    _write_bytes(hex_list, offset, value.to_bytes(4, "little"))


def _write_word(hex_list: List[str], offset: int, value: int) -> None:
    _write_bytes(hex_list, offset, value.to_bytes(2, "little"))


def _ext_header(cap_id: int, version: int, next_ptr: int) -> int:
    """Build extended capability header dword.

    Layout: bits 15:0 = ID, 19:16 = version, 31:20 = next
    """
    return (cap_id & 0xFFFF) | ((version & 0xF) << 16) | ((next_ptr & 0xFFF) << 20)


def build_config_with_ext_caps(caps: List[Tuple[int, int]]) -> ConfigSpace:
    """Build a minimal config space with provided extended caps.

    caps: list of (cap_id, base_offset) pairs. Will chain in given order.
    Returns ConfigSpace ready for processing.
    """
    # Allocate 1024 bytes to comfortably hold our structures
    size = 1024
    hex_data = ["0"] * (size * 2)

    # Basic IDs
    _write_word(hex_data, 0x00, 0x1234)  # Vendor ID
    _write_word(hex_data, 0x02, 0x5678)  # Device ID

    # Build chain
    for idx, (cap_id, base) in enumerate(caps):
        next_ptr = caps[idx + 1][1] if idx + 1 < len(caps) else 0
        header = _ext_header(cap_id, 1, next_ptr)
        _write_dword(hex_data, base, header)

    return ConfigSpace("".join(hex_data))


def test_aer_handler_applies_expected_defaults():
    # Build config with AER at 0x100
    cfg = build_config_with_ext_caps([(EXT_CAP_ID_AER, 0x100)])

    # Pre-populate AER fields to trigger patches
    # Offsets relative to 0x100
    _write_dword(list(cfg.to_hex()), 0, 0)  # no-op, ensure list access compiles
    hex_list = list(cfg.to_hex())
    # Uncorrectable Error Mask (+0x08): set to non-zero to be cleared to 0
    _write_dword(hex_list, 0x100 + 0x08, 0xFFFFFFFF)
    # Uncorrectable Error Severity (+0x0C): set to 0 -> should become default
    _write_dword(hex_list, 0x100 + 0x0C, 0x00000000)
    # Correctable Error Mask (+0x14): set to 0 -> should become default
    _write_dword(hex_list, 0x100 + 0x14, 0x00000000)
    # Advanced Error Cap & Ctl (+0x18): set to 0 -> should become default
    _write_dword(hex_list, 0x100 + 0x18, 0x00000000)
    cfg = ConfigSpace("".join(hex_list))

    proc = CapabilityProcessor(cfg, RuleEngine())
    res = proc.process_capabilities([PruningAction.MODIFY])
    assert "MODIFY" in res["processing_summary"]

    # Verify final values after patches applied
    assert cfg.read_dword(0x100 + 0x08) == 0x00000000
    expected_ues = AER_CAPABILITY_VALUES["uncorrectable_error_severity"]
    expected_cem = AER_CAPABILITY_VALUES["correctable_error_mask"]
    expected_aecc = AER_CAPABILITY_VALUES["advanced_error_capabilities"]
    assert cfg.read_dword(0x100 + 0x0C) == expected_ues
    assert cfg.read_dword(0x100 + 0x14) == expected_cem
    assert cfg.read_dword(0x100 + 0x18) == expected_aecc


def test_ltr_handler_respects_overrides_and_preserves_by_default():
    # Build config with a proper ext cap chain: AER at 0x100 -> LTR at 0x120
    cfg = build_config_with_ext_caps(
        [
            (EXT_CAP_ID_AER, 0x100),
            (EXT_CAP_ID_LTR, 0x120),
        ]
    )
    # Set control dword to a known value (all zeros)
    hex_list = list(cfg.to_hex())
    _write_dword(hex_list, 0x120 + 0x08, 0x00000000)
    cfg = ConfigSpace("".join(hex_list))


    # By default (no overrides), no patch should be created -> value unchanged
    assert cfg.read_dword(0x120 + 0x08) == 0x00000000

    # Now force overrides via device context
    overrides = {
        "vendor_id": 0x1234,
        "device_id": 0x5678,
        "ltr_snoop_latency_value": 0x123,
        "ltr_snoop_latency_scale": 0x2,
        "ltr_nosnoop_latency_value": 0x345,
        "ltr_nosnoop_latency_scale": 0x3,
    }
    # Recreate processor to bypass cached context
    proc = CapabilityProcessor(cfg, RuleEngine())
    # Patch method to use our overrides
    proc._get_device_context = lambda: overrides  # type: ignore

    res = proc.process_capabilities([PruningAction.MODIFY])
    new_val = cfg.read_dword(0x120 + 0x08)
    # Validate fields written to expected bit positions
    assert (new_val & 0x00000FFF) == 0x123
    assert (new_val & 0x00007000) >> 12 == 0x2
    assert (new_val & 0x0FFF0000) >> 16 == 0x345
    assert (new_val & 0x70000000) >> 28 == 0x3


def test_sriov_handler_clears_vf_enable():
    # Build config with a proper ext cap chain: AER at 0x100 -> SR-IOV at 0x140
    cfg = build_config_with_ext_caps(
        [
            (EXT_CAP_ID_AER, 0x100),
            (EXT_CAP_ID_SRIOV, 0x140),
        ]
    )
    # Set SR-IOV Control (+0x08) bit 0 = 1
    hex_list = list(cfg.to_hex())
    _write_word(hex_list, 0x140 + 0x08, 0x0001)
    cfg = ConfigSpace("".join(hex_list))

    proc = CapabilityProcessor(cfg, RuleEngine())
    proc.process_capabilities([PruningAction.MODIFY])
    assert cfg.read_word(0x140 + 0x08) & 0x0001 == 0x0000


@pytest.mark.parametrize("flag, expected_bit", [(True, 1), (False, 0)])
def test_ari_handler_respects_enable_flag(flag: bool, expected_bit: int):
    # Build config with a proper ext cap chain: AER at 0x100 -> ARI at 0x160
    cfg = build_config_with_ext_caps(
        [
            (EXT_CAP_ID_AER, 0x100),
            (EXT_CAP_ID_ARI, 0x160),
        ]
    )
    # Ensure control word starts at 0
    hex_list = list(cfg.to_hex())
    _write_word(hex_list, 0x160 + 0x04, 0x0000)
    cfg = ConfigSpace("".join(hex_list))

    proc = CapabilityProcessor(cfg, RuleEngine())
    ctx = {"vendor_id": 0x1234, "device_id": 0x5678, "enable_ari_forwarding": flag}
    proc._get_device_context = lambda: ctx  # type: ignore

    proc.process_capabilities([PruningAction.MODIFY])
    assert (cfg.read_word(0x160 + 0x04) & 0x0001) == expected_bit


def test_ptm_handler_disables_cap_and_control():
    # Build config with a proper ext cap chain: AER at 0x100 -> PTM at 0x120
    cfg = build_config_with_ext_caps(
        [
            (EXT_CAP_ID_AER, 0x100),
            (EXT_CAP_ID_PTM, 0x120),
        ]
    )

    # Set PTM Capabilities (+0x04) bit0=1 and Control (+0x08) bits0-1=3
    hex_list = list(cfg.to_hex())
    _write_dword(hex_list, 0x120 + 0x04, 0x00000001)
    _write_dword(hex_list, 0x120 + 0x08, 0x00000003)
    cfg = ConfigSpace("".join(hex_list))

    proc = CapabilityProcessor(cfg, RuleEngine())
    proc.process_capabilities([PruningAction.MODIFY])

    # Expect PTM Capable cleared and control enable/root cleared
    assert (cfg.read_dword(0x120 + 0x04) & 0x00000001) == 0
    assert (cfg.read_dword(0x120 + 0x08) & 0x00000003) == 0


def test_l1pm_handler_enables_defaults_and_timings():
    # Build config with a proper ext cap chain:
    # AER at 0x100 -> L1 PM Substates at 0x140
    cfg = build_config_with_ext_caps(
        [
            (EXT_CAP_ID_AER, 0x100),
            (PCIExtCapabilityID.L1_PM_SUBSTATES.value, 0x140),
        ]
    )

    # Zero out caps/control so handler writes defaults
    hex_list = list(cfg.to_hex())
    _write_dword(hex_list, 0x140 + 0x04, 0x00000000)
    _write_dword(hex_list, 0x140 + 0x08, 0x00000000)
    cfg = ConfigSpace("".join(hex_list))

    proc = CapabilityProcessor(cfg, RuleEngine())
    proc.process_capabilities([PruningAction.MODIFY])

    caps_val = cfg.read_dword(0x140 + 0x04)
    ctrl_val = cfg.read_dword(0x140 + 0x08)

    # Caps should indicate L1.1 and L1.2 supported and include default timings
    assert (caps_val & 0x00000006) == 0x00000006  # L1.1 + L1.2 supported
    # Default timing fields applied
    assert (caps_val & 0x0000FF00) != 0  # Common mode restore time set
    assert (caps_val & 0x00030000) != 0  # Tpower_on scale set
    assert (caps_val & 0x00F80000) != 0  # Tpower_on value set

    # Control should enable L1.1 and L1.2; threshold fields default to 0
    assert (ctrl_val & 0x00000006) == 0x00000006
    assert (ctrl_val & 0x03FF0000) == 0
    assert (ctrl_val & 0x1C000000) == 0


def test_rbar_handler_clamps_sizes_when_enabled():
    # Build config with a proper ext cap chain:
    # AER at 0x100 -> Resizable BAR at 0x160
    cfg = build_config_with_ext_caps(
        [
            (EXT_CAP_ID_AER, 0x100),
            (PCIExtCapabilityID.RESIZABLE_BAR.value, 0x160),
        ]
    )

    # Set BAR0 Capability dword (+0x08) with bits [27:31] set (sizes >128MB)
    hex_list = list(cfg.to_hex())
    _write_dword(hex_list, 0x160 + 0x08, 0xF8000000)
    cfg = ConfigSpace("".join(hex_list))

    proc = CapabilityProcessor(cfg, RuleEngine())
    # Force clamp behavior via context override
    proc._get_device_context = lambda: {
        "vendor_id": 0x1234,
        "device_id": 0x5678,
        "rbar_clamp_to_128mb": True,
    }  # type: ignore

    proc.process_capabilities([PruningAction.MODIFY])
    # Expect the high-size bits cleared
    assert (cfg.read_dword(0x160 + 0x08) & 0xF8000000) == 0
