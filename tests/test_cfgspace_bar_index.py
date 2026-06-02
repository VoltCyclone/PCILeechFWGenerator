"""cfgspace COE renders the served BAR at its donor PCI index (gap C).

Since the firmware serves exactly one register window (the donor's primary MMIO
BAR at index N) and the IP disables all other BARs, the config-space shadow must
place that BAR's size encoding at slot N and leave every other BAR slot zeroed —
not render BARs positionally by list order (the old behavior, which also wrongly
hardcoded slot 2 as IO).
"""
import re
from copy import deepcopy

from pcileechfwgenerator.device_clone.config_space_manager import BarInfo
from tests.test_coe_error_injection import (
    _minimal_base_context,
    _render_cfgspace_coe,
)


def _bar_dwords(coe: str):
    """Return the six BAR dwords (0x10-0x27) in slot order."""
    region = coe.split("Base Address Registers", 1)[1].split("Cardbus CIS", 1)[0]
    return re.findall(r"^([0-9A-Fa-f]{8}),", region, re.MULTILINE)


def _ctx_with_served(index, size, *, is_64bit=False, is_io=False):
    ctx = deepcopy(_minimal_base_context())
    bar = BarInfo(
        index=index,
        bar_type="io" if is_io else "memory",
        address=0xF7000000,
        size=size,
        prefetchable=False,
        is_64bit=is_64bit,
    )
    ctx["bar_config"] = {
        "bars": [bar],
        "primary_bar": 0,  # list position of served BAR within bars
        "served_bar_index": index,  # donor PCI index N
        "served_is_64bit": is_64bit,
    }
    return ctx


class TestCfgspaceBarIndex:
    def test_served_bar_rendered_at_its_donor_index(self):
        coe = _render_cfgspace_coe(_ctx_with_served(2, 65536))
        dwords = _bar_dwords(coe)
        assert len(dwords) == 6
        # 64 KiB memory size mask = ~(0x10000-1) = 0xFFFF0000.
        assert dwords[2].upper() == "FFFF0000"
        # Every other slot is disabled.
        for k in (0, 1, 3, 4, 5):
            assert dwords[k] == "00000000"

    def test_64bit_served_marks_partner_slot_upper_dword(self):
        coe = _render_cfgspace_coe(
            _ctx_with_served(2, 2 * 1024 * 1024, is_64bit=True)
        )
        dwords = _bar_dwords(coe)
        # Lower dword at slot 2 carries the 64-bit type bit (0x4).
        assert int(dwords[2], 16) & 0x4
        # Slot 3 (partner) is the all-ones upper dword.
        assert dwords[3].upper() == "FFFFFFFF"

    def test_index_0_back_compat_slot0_carries_encoding(self):
        coe = _render_cfgspace_coe(_ctx_with_served(0, 4096))
        dwords = _bar_dwords(coe)
        assert dwords[0] != "00000000"
        for k in (1, 2, 3, 4, 5):
            assert dwords[k] == "00000000"
