"""BRAM word-order conversion for cfgspace / writemask COE files.

The shadow BRAM stores PCI config byte +0 in bits [31:24] of each word (see
pcileech_tlps128_cfgspace_shadow.sv: pcie_rx_be = {tdata[32],..,tdata[35]} with
unswapped payload data, and pcileech_fifo.sv's USB path placing LeechCore's
pb[i] in rx_data[31:24]). These tests pin the conversion with literal values.
"""

import pytest
from pcileechfwgenerator.device_clone.coe_word_order import (
    BRAM_ORDER_MARKER,
    is_bram_order,
    logical_coe_to_bram,
    parse_coe_logical_words,
)

LOGICAL = (
    "; header comment\n"
    "memory_initialization_radix=16;\n"
    "memory_initialization_vector=\n"
    "; Vendor/Device\n"
    "15218086,\n"
    "00100006, 00000040 ; inline\n"
    "0000010B;\n"
)


def test_converts_each_word_to_msb_first_byte_order():
    out = logical_coe_to_bram(LOGICAL)
    assert BRAM_ORDER_MARKER in out
    # VID 0x8086 / DID 0x1521 -> config bytes 86 80 21 15 -> byte +0 in MSB.
    assert "86802115" in out
    assert "06001000" in out
    assert "40000000" in out
    assert "0B010000" in out.upper()
    assert "15218086" not in out


def test_comments_and_structure_preserved():
    out = logical_coe_to_bram(LOGICAL)
    assert "; header comment" in out
    assert "; Vendor/Device" in out
    assert "; inline" in out
    assert out.rstrip().endswith(";")


def test_idempotent():
    once = logical_coe_to_bram(LOGICAL)
    assert logical_coe_to_bram(once) == once


def test_round_trip_parse_returns_logical_values():
    assert parse_coe_logical_words(LOGICAL) == [
        0x15218086,
        0x00100006,
        0x00000040,
        0x0000010B,
    ]
    assert parse_coe_logical_words(logical_coe_to_bram(LOGICAL)) == [
        0x15218086,
        0x00100006,
        0x00000040,
        0x0000010B,
    ]


def test_is_bram_order():
    assert not is_bram_order(LOGICAL)
    assert is_bram_order(logical_coe_to_bram(LOGICAL))


def test_rejects_malformed_word():
    with pytest.raises(ValueError):
        logical_coe_to_bram(
            "memory_initialization_radix=16;\nmemory_initialization_vector=\nxyz;\n"
        )


def test_short_tokens_are_zero_extended_before_swap():
    out = logical_coe_to_bram(
        "memory_initialization_radix=16;\nmemory_initialization_vector=\n40;\n"
    )
    assert "40000000" in out
