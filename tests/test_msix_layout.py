"""MX-03: validated MSI-X layout, explicit unsupported cases, single ownership."""

import pytest
from pcileechfwgenerator.device_clone.config_space_manager import BarInfo
from pcileechfwgenerator.device_clone.msix_layout import (
    MsixBar,
    MsixLayoutError,
    derive_msix_layout,
    read_msix_capability,
)
from pcileechfwgenerator.host_collect.offline import _msix_from_config


def config_with_msix(vectors, table_bir, table_off, pba_bir, pba_off, *, pm_first=True):
    """256-byte config space holding only a capability chain (no IDs)."""
    cfg = bytearray(256)
    cfg[0x06] = 0x10  # status: capability list present
    msix_at = 0x50 if pm_first else 0x40
    if pm_first:
        cfg[0x34] = 0x40
        cfg[0x40], cfg[0x41] = 0x01, msix_at  # power management -> MSI-X
    else:
        cfg[0x34] = msix_at
    cfg[msix_at] = 0x11
    cfg[msix_at + 1] = 0x00
    cfg[msix_at + 2 : msix_at + 4] = ((vectors - 1) & 0x7FF).to_bytes(2, "little")
    cfg[msix_at + 4 : msix_at + 8] = (table_off | table_bir).to_bytes(4, "little")
    cfg[msix_at + 8 : msix_at + 12] = (pba_off | pba_bir).to_bytes(4, "little")
    return bytes(cfg)


def bar(index, size, memory=True, is64=False):
    return BarInfo(index, "memory" if memory else "io", 0, size, False, is64)


BAR0_16K = [bar(0, 0x4000)]


def test_shared_bar_layout_has_exact_windows_and_core_width():
    layout = derive_msix_layout(config_with_msix(6, 0, 0x2000, 0, 0x3000), BAR0_16K, 0)
    assert layout.vectors == 6
    assert layout.windows() == {"table": (0x2000, 0x2060), "pba": (0x3000, 0x3008)}
    assert layout.irq_index_width == 3
    assert layout.decode_parameters() == {
        "bar_index": 0,
        "vectors": 6,
        "irq_index_width": 3,
        "table_first": 0x2000,
        "table_last": 0x205F,
        "pba_first": 0x3000,
        "pba_last": 0x3007,
    }


@pytest.mark.parametrize(
    "vectors,pba_bytes,width",
    [(1, 8, 1), (2, 8, 1), (33, 8, 6), (64, 8, 6), (65, 16, 7), (2048, 256, 11)],
)
def test_pba_is_qword_array_and_width_covers_every_vector(vectors, pba_bytes, width):
    layout = derive_msix_layout(
        config_with_msix(vectors, 0, 0, 0, 0x8000), [bar(0, 0x10000)], 0
    )
    start, end = layout.windows()["pba"]
    assert end - start == pba_bytes
    assert layout.irq_index_width == width
    assert (1 << width) >= vectors


def test_ownership_is_exclusive_and_never_reaches_padded_entries():
    layout = derive_msix_layout(config_with_msix(6, 0, 0x2000, 0, 0x3000), BAR0_16K, 0)
    assert layout.owner(0, 0x2000, 4) == "table"
    assert layout.owner(0, 0x2058, 8) == "table"  # vector 5 control + data
    # Entry 6 would exist in an 8-entry core but is not part of the donor table.
    assert layout.owner(0, 0x2060, 4) is None
    assert layout.owner(0, 0x3000, 8) == "pba"
    assert layout.owner(0, 0x3008, 4) is None
    assert layout.owner(0, 0x1FFC, 4) is None
    assert layout.owner(1, 0x2000, 4) is None


def test_access_straddling_a_window_edge_is_rejected():
    layout = derive_msix_layout(config_with_msix(6, 0, 0x2000, 0, 0x3000), BAR0_16K, 0)
    with pytest.raises(MsixLayoutError, match="straddles the MSI-X table"):
        layout.owner(0, 0x1FFC, 8)
    with pytest.raises(MsixLayoutError, match="straddles the MSI-X pba"):
        layout.owner(0, 0x3004, 8)


def test_no_capability_means_no_layout():
    cfg = bytearray(256)
    assert derive_msix_layout(bytes(cfg), BAR0_16K, 0) is None


@pytest.mark.parametrize(
    "cfg_args,bars,served,message",
    [
        ((8, 0, 0x0, 2, 0x0), [bar(0, 0x4000), bar(2, 0x4000)], 0, "PBA is in BAR2"),
        (
            (8, 2, 0x0, 2, 0x100),
            [bar(0, 0x4000), bar(2, 0x4000)],
            0,
            "table is in BAR2",
        ),
        (
            (8, 1, 0x0, 1, 0x100),
            [bar(0, 0x4000, is64=True)],
            0,
            "upper half of a 64-bit",
        ),
        ((8, 6, 0x0, 0, 0x100), BAR0_16K, 0, "BIR 6 is reserved"),
        ((8, 0, 0x0, 0, 0x100), [bar(0, 0x100, memory=False)], 0, "memory BAR"),
        # A sub-4 KiB served BAR is dropped by the IP override, so the hard IP
        # would serve a different BAR than this layout assumes.
        ((4, 2, 0x0, 2, 0x400), [bar(0, 0x4000), bar(2, 0x800)], 2, "at least 4 KiB"),
        ((4, 0, 0x0, 0, 0x400), [bar(0, 0x3000)], 0, "power of two"),
        ((8, 0, 0x0, 0, 0x100), [bar(0, 0x4000), bar(0, 0x4000)], 0, "duplicate BAR0"),
        ((8, 0, 0x3F90, 0, 0x100), BAR0_16K, 0, r"exceeds BAR0 size"),
        ((65, 0, 0x0, 0, 0x3FF8), BAR0_16K, 0, r"MSI-X pba \[0x3ff8, 0x4008\) exceeds"),
        ((8, 0, 0x0, 0, 0x40), BAR0_16K, 0, "overlaps its pending-bit array"),
        ((8, 0, 0x0, 0, 0x100), BAR0_16K, 3, "served BAR3 is not implemented"),
    ],
)
def test_unsupported_or_invalid_layouts_fail_explicitly(
    cfg_args, bars, served, message
):
    with pytest.raises(MsixLayoutError, match=message):
        derive_msix_layout(config_with_msix(*cfg_args), bars, served)


def test_separate_bar_layout_is_valid_once_that_bar_is_served():
    """Table and PBA together in BAR2 are supported when BAR2 is the served BAR."""
    layout = derive_msix_layout(
        config_with_msix(16, 2, 0x0, 2, 0x800),
        [bar(0, 0x4000), bar(2, 0x1000, is64=True)],
        2,
    )
    assert layout.served_bar.index == 2
    assert layout.owner(2, 0x0F0, 16) == "table"
    assert layout.owner(0, 0x0, 4) is None


@pytest.mark.parametrize(
    "mutate,message",
    [
        (lambda c: c.__setitem__(0x41, 0x40), "cyclic"),
        (lambda c: c.__setitem__(0x34, 0x20), "invalid or cyclic"),
        (lambda c: (c.__setitem__(0x51, 0x60), c.__setitem__(0x60, 0x11)), "duplicate"),
        (lambda c: c.__setitem__(0x06, 0x00), "without the status capability bit"),
    ],
)
def test_malformed_capability_chains_are_rejected(mutate, message):
    cfg = bytearray(config_with_msix(8, 0, 0, 0, 0x100))
    mutate(cfg)
    with pytest.raises(MsixLayoutError, match=message):
        read_msix_capability(bytes(cfg))


def test_truncated_config_space_is_rejected():
    with pytest.raises(MsixLayoutError, match="shorter than 256"):
        read_msix_capability(bytes(64))


def test_windows_must_be_reachable_by_the_32_bit_offset_decode():
    huge = [bar(0, 1 << 33, is64=True)]
    with pytest.raises(MsixLayoutError, match="32-bit"):
        derive_msix_layout(config_with_msix(2048, 0, 0xFFFFF000, 0, 0x100), huge, 0)
    # A table ending exactly at 4 GiB is reachable; inclusive bounds fit 32 bits.
    layout = derive_msix_layout(
        config_with_msix(256, 0, 0xFFFFF000, 0, 0x100), [bar(0, 1 << 32, is64=True)], 0
    )
    assert layout.decode_parameters()["table_last"] == 0xFFFFFFFF


def test_dword_split_gives_each_dword_one_owner():
    layout = derive_msix_layout(config_with_msix(6, 0, 0x2000, 0, 0x3000), BAR0_16K, 0)
    assert layout.dword_owners(0, 0x1FFC, 8) == [(0x1FFC, None), (0x2000, "table")]
    assert layout.dword_owners(0, 0x205C, 8) == [(0x205C, "table"), (0x2060, None)]
    assert layout.dword_owners(0, 0x3002, 1) == [(0x3000, "pba")]


def test_bar_record_without_index_is_a_layout_error():
    with pytest.raises(MsixLayoutError, match="no index"):
        MsixBar.from_record({"size": 4096, "is_memory": True})


def test_served_bar_floor_matches_the_ip_override_aperture_rule():
    from pcileechfwgenerator.vivado_handling.pcie_ip_donor_override import (
        _bar_aperture_error,
    )

    for size in (0x800, 0x1000, 0x3000, 0x4000):
        cfg = config_with_msix(1, 0, 0, 0, 0x400)
        try:
            derive_msix_layout(cfg, [bar(0, size)], 0)
            accepted = True
        except MsixLayoutError:
            accepted = False
        assert accepted == (_bar_aperture_error(size, True) is None), hex(size)


def test_reserved_pointer_bits_are_masked_as_the_spec_requires():
    """PCI requires software to ignore pointer bits [1:0]. The offline capture
    validator rejects such a chain instead, which fails safe."""
    cfg = bytearray(config_with_msix(8, 0, 0x2000, 0, 0x3000))
    cfg[0x41] = 0x52
    assert read_msix_capability(bytes(cfg)).cap_offset == 0x50


@pytest.mark.parametrize("pm_first", [True, False])
def test_reader_agrees_with_the_offline_capture_validator(pm_first):
    """For well-formed chains (no reserved pointer bits) both parsers agree."""
    cfg = config_with_msix(48, 0, 0x2000, 0, 0x3000, pm_first=pm_first)
    cap = read_msix_capability(cfg)
    offline = _msix_from_config(cfg)
    assert {
        "table_size": cap.table_size,
        "table_bir": cap.table_bir,
        "table_offset": cap.table_offset,
        "pba_bir": cap.pba_bir,
        "pba_offset": cap.pba_offset,
    } == {
        k: offline[k]
        for k in ("table_size", "table_bir", "table_offset", "pba_bir", "pba_offset")
    }


def test_bar_records_from_dicts_and_typed_flags():
    assert MsixBar.from_record({"index": 1, "size": 4096, "is_io": False}).is_memory
    assert not MsixBar.from_record({"index": 1, "size": 16, "bar_type": "io"}).is_memory
    with pytest.raises(MsixLayoutError, match="no memory/IO type"):
        MsixBar.from_record({"index": 1, "size": 16})
