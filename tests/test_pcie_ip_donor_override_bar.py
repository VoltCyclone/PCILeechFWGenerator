"""Tests for donor BAR aperture override (gaps A5 + C).

Propagates the served donor BAR's geometry (size/type/64-bit/prefetchable)
into the Xilinx 7-series PCIe IP via ``CONFIG.Bar{N}_*`` so PCIe enumeration's
size probe sees the donor aperture instead of the Xilinx 4 KB default.

v1 mirrors a single register BAR, served at its real donor PCI index N (gap C);
every other BAR index is disabled so the host enumerates exactly one window.
"""
import sys
from pathlib import Path

import pytest

project_root = Path(__file__).parent.parent.resolve()
if str(project_root) not in sys.path:
    sys.path.insert(0, str(project_root))

from pcileechfwgenerator.vivado_handling.fifo_donor_patcher import DonorIDs
from pcileechfwgenerator.device_clone.pcileech_context import BarConfiguration
from pcileechfwgenerator.vivado_handling.pcie_ip_donor_override import (
    DonorPCIeIPConfig,
    _bytes_to_xilinx_scale_size,
    donor_pcie_ip_config_from_result,
    generate_pcie_ip_override_tcl,
)

_SCALE_UNITS = {
    "Bytes": 1,
    "Kilobytes": 1024,
    "Megabytes": 1024 * 1024,
    "Gigabytes": 1024 * 1024 * 1024,
}


def _decode_emitted_bar0_bytes(tcl: str) -> int:
    """Recover the BAR0 aperture in bytes from the emitted CONFIG lines."""
    scale = size = None
    for line in tcl.splitlines():
        line = line.strip().rstrip("\\").strip()
        if line.startswith("CONFIG.Bar0_Scale "):
            scale = line.split()[1]
        elif line.startswith("CONFIG.Bar0_Size "):
            size = int(line.split()[1])
    assert scale is not None and size is not None, tcl
    return size * _SCALE_UNITS[scale]


def _mem_bar(index, size, is_64bit=False, prefetchable=False) -> BarConfiguration:
    return BarConfiguration(
        index=index,
        base_address=0xF0000000,
        size=size,
        bar_type=0,
        prefetchable=prefetchable,
        is_memory=True,
        is_io=False,
        is_64bit=is_64bit,
    )


def _intel_donor() -> DonorIDs:
    return DonorIDs(
        vendor_id=0x8086,
        device_id=0x1533,
        subsystem_vendor_id=0x8086,
        subsystem_id=0x0001,
        revision_id=0x03,
    )


class TestBytesToXilinxScaleSize:
    """``_bytes_to_xilinx_scale_size`` maps a byte size to the IP's (Scale, Size)."""

    @pytest.mark.parametrize(
        "size_bytes,expected",
        [
            (4 * 1024, ("Kilobytes", 4)),
            (64 * 1024, ("Kilobytes", 64)),
            (512 * 1024, ("Kilobytes", 512)),
            (1 * 1024 * 1024, ("Megabytes", 1)),
            (2 * 1024 * 1024, ("Megabytes", 2)),
            (16 * 1024 * 1024, ("Megabytes", 16)),
            (1 * 1024 * 1024 * 1024, ("Gigabytes", 1)),
            (2 * 1024 * 1024 * 1024, ("Gigabytes", 2)),
        ],
    )
    def test_canonical_scale_uses_largest_unit(self, size_bytes, expected):
        # Always pick the largest scale that divides evenly so the multiplier
        # stays below 1024 (matches the Vivado GUI's Scale/Size split).
        assert _bytes_to_xilinx_scale_size(size_bytes) == expected


class TestBarApertureEmit:
    """``generate_pcie_ip_override_tcl`` emits ``CONFIG.Bar0_*`` from the aperture."""

    def test_memory_bar_emits_full_bar0_block(self):
        extra = DonorPCIeIPConfig(
            bar_aperture_size=64 * 1024,  # 64 KiB
            bar_is_memory=True,
            bar_is_64bit=False,
            bar_prefetchable=False,
        )
        tcl = generate_pcie_ip_override_tcl(_intel_donor(), extra=extra)
        assert "CONFIG.Bar0_Enabled true" in tcl
        assert "CONFIG.Bar0_Type Memory" in tcl
        assert "CONFIG.Bar0_64bit false" in tcl
        assert "CONFIG.Bar0_Prefetchable false" in tcl
        assert "CONFIG.Bar0_Scale Kilobytes" in tcl
        assert "CONFIG.Bar0_Size 64" in tcl

    def test_64bit_prefetchable_bar_sets_flags_and_disables_bar1(self):
        extra = DonorPCIeIPConfig(
            bar_aperture_size=2 * 1024 * 1024,  # 2 MiB
            bar_is_memory=True,
            bar_is_64bit=True,
            bar_prefetchable=True,
        )
        tcl = generate_pcie_ip_override_tcl(_intel_donor(), extra=extra)
        assert "CONFIG.Bar0_Type Memory" in tcl
        assert "CONFIG.Bar0_64bit true" in tcl
        assert "CONFIG.Bar0_Prefetchable true" in tcl
        assert "CONFIG.Bar0_Scale Megabytes" in tcl
        assert "CONFIG.Bar0_Size 2" in tcl
        # A 64-bit BAR consumes the next slot.
        assert "CONFIG.Bar1_Enabled false" in tcl

    def test_io_bar_forces_no_64bit_no_prefetch(self):
        extra = DonorPCIeIPConfig(
            bar_aperture_size=256,
            bar_is_memory=False,
            bar_is_64bit=True,  # nonsensical for IO — must be coerced off
            bar_prefetchable=True,  # ditto
        )
        tcl = generate_pcie_ip_override_tcl(_intel_donor(), extra=extra)
        assert "CONFIG.Bar0_Type IO" in tcl
        assert "CONFIG.Bar0_64bit false" in tcl
        assert "CONFIG.Bar0_Prefetchable false" in tcl
        assert "CONFIG.Bar0_Scale Bytes" in tcl
        assert "CONFIG.Bar0_Size 256" in tcl

    def test_no_aperture_emits_no_bar_lines(self):
        tcl = generate_pcie_ip_override_tcl(_intel_donor(), extra=DonorPCIeIPConfig())
        assert "Bar0_" not in tcl

    def test_served_at_index_2_emits_bar2_and_disables_others(self):
        extra = DonorPCIeIPConfig(
            bar_aperture_size=64 * 1024,
            bar_is_memory=True,
            bar_index=2,
        )
        tcl = generate_pcie_ip_override_tcl(_intel_donor(), extra=extra)
        assert "CONFIG.Bar2_Enabled true" in tcl
        assert "CONFIG.Bar2_Type Memory" in tcl
        assert "CONFIG.Bar2_Scale Kilobytes" in tcl
        assert "CONFIG.Bar2_Size 64" in tcl
        # The served index is enabled, never disabled.
        assert "CONFIG.Bar2_Enabled false" not in tcl
        # Non-served indices (incl. the .xci default BAR0) are disabled so the
        # host sees exactly one BAR — no phantom/hanging windows.
        assert "CONFIG.Bar0_Enabled false" in tcl
        assert "CONFIG.Bar1_Enabled false" in tcl
        assert "CONFIG.Bar3_Enabled false" in tcl

    def test_64bit_served_at_index_2_disables_partner_bar3(self):
        extra = DonorPCIeIPConfig(
            bar_aperture_size=2 * 1024 * 1024,
            bar_is_memory=True,
            bar_is_64bit=True,
            bar_index=2,
        )
        tcl = generate_pcie_ip_override_tcl(_intel_donor(), extra=extra)
        assert "CONFIG.Bar2_Enabled true" in tcl
        assert "CONFIG.Bar2_64bit true" in tcl
        # 64-bit consumes the adjacent slot.
        assert "CONFIG.Bar3_Enabled false" in tcl

    def test_64bit_at_top_index_rejected(self):
        # A 64-bit BAR at index 5 would need BAR6 (Expansion ROM) as its upper
        # half — electrically invalid PCI. Reject loudly rather than emit a
        # mismatched cfgspace/IP/controller triple.
        extra = DonorPCIeIPConfig(
            bar_aperture_size=2 * 1024 * 1024,
            bar_is_memory=True,
            bar_is_64bit=True,
            bar_index=5,
        )
        with pytest.raises(ValueError):
            generate_pcie_ip_override_tcl(_intel_donor(), extra=extra)

    def test_index_0_back_compat_enables_bar0_disables_rest(self):
        extra = DonorPCIeIPConfig(
            bar_aperture_size=4 * 1024, bar_is_memory=True, bar_index=0
        )
        tcl = generate_pcie_ip_override_tcl(_intel_donor(), extra=extra)
        assert "CONFIG.Bar0_Enabled true" in tcl
        assert "CONFIG.Bar0_Enabled false" not in tcl
        assert "CONFIG.Bar1_Enabled false" in tcl

    def test_sub_4kb_memory_bar_rejected(self):
        extra = DonorPCIeIPConfig(bar_aperture_size=2048, bar_is_memory=True)
        with pytest.raises(ValueError):
            generate_pcie_ip_override_tcl(_intel_donor(), extra=extra)

    def test_non_power_of_two_rejected(self):
        extra = DonorPCIeIPConfig(bar_aperture_size=48 * 1024, bar_is_memory=True)
        with pytest.raises(ValueError):
            generate_pcie_ip_override_tcl(_intel_donor(), extra=extra)


class TestBarApertureExtractor:
    """``donor_pcie_ip_config_from_result`` reads the served BAR from bar_config."""

    def test_extracts_served_bar_geometry(self):
        result = {
            "template_context": {
                "bar_config": {
                    "bars": [_mem_bar(0, 64 * 1024, is_64bit=True, prefetchable=True)],
                }
            }
        }
        cfg = donor_pcie_ip_config_from_result(result)
        assert cfg.bar_aperture_size == 64 * 1024
        assert cfg.bar_is_memory is True
        assert cfg.bar_is_64bit is True
        assert cfg.bar_prefetchable is True

    def test_extractor_reads_served_bar_index(self):
        result = {
            "template_context": {
                "bar_config": {
                    "served_bar_index": 2,
                    "primary_bar": 1,
                    "bars": [_mem_bar(0, 4096), _mem_bar(2, 65536)],
                }
            }
        }
        cfg = donor_pcie_ip_config_from_result(result)
        assert cfg.bar_index == 2
        assert cfg.bar_aperture_size == 65536

    def test_no_bar_config_leaves_aperture_none(self):
        cfg = donor_pcie_ip_config_from_result({"template_context": {}})
        assert cfg.bar_aperture_size is None

    def test_sub_4kb_served_memory_bar_is_skipped_not_fatal(self):
        # Defensive: a sub-4KB served BAR must leave the IP default in place,
        # never make the emit raise and break the build.
        result = {
            "template_context": {"bar_config": {"bars": [_mem_bar(0, 2048)]}}
        }
        cfg = donor_pcie_ip_config_from_result(result)
        assert cfg.bar_aperture_size is None
        # And the override still renders fine (just no Bar0 block).
        generate_pcie_ip_override_tcl(_intel_donor(), extra=cfg)

    def test_served_bar_follows_primary_bar_index_like_controller(self):
        # The controller's BAR_SIZE uses bars[primary_bar|default(0)]; the
        # extractor must anchor on the same entry so the IP aperture matches.
        result = {
            "template_context": {
                "bar_config": {
                    "primary_bar": 1,
                    "bars": [_mem_bar(0, 4 * 1024), _mem_bar(2, 1024 * 1024)],
                }
            }
        }
        cfg = donor_pcie_ip_config_from_result(result)
        assert cfg.bar_aperture_size == 1024 * 1024


class TestApertureMatchesControllerBarSize:
    """Invariant: emitted IP aperture == the controller's BAR_SIZE for the donor."""

    @pytest.mark.parametrize("size", [4 * 1024, 64 * 1024, 16 * 1024 * 1024])
    def test_emitted_bar0_decodes_to_served_size(self, size):
        served = _mem_bar(0, size)
        result = {"template_context": {"bar_config": {"bars": [served]}}}
        cfg = donor_pcie_ip_config_from_result(result)
        tcl = generate_pcie_ip_override_tcl(_intel_donor(), extra=cfg)
        # Controller's BAR_SIZE = bars[primary_bar|default(0)].size == served.size
        assert _decode_emitted_bar0_bytes(tcl) == served.size
