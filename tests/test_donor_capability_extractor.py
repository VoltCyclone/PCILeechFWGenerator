"""Tests for donor PCIe capability extraction (Step 3 of override expansion).

These tests use crafted config-space hex strings — no real donor capture
needed. The strings are sized to PCI_CONFIG_SPACE_MIN_SIZE so ConfigSpace
accepts them.
"""
import sys
from pathlib import Path

import pytest

project_root = Path(__file__).parent.parent.resolve()
if str(project_root) not in sys.path:
    sys.path.insert(0, str(project_root))

from pcileechfwgenerator.device_clone.donor_capability_extractor import (
    extract_dsn_value,
)


def _make_config_space(
    *,
    cap_ptr: int = 0x40,
    ext_caps: list[tuple[int, int, list[int]]] = None,
) -> str:
    """Build a 4096-byte config-space hex string with the given ext caps."""
    size = 4096
    data = bytearray(size)
    data[0x34] = cap_ptr
    data[0x06] = 0x10

    if ext_caps:
        offset = 0x100
        for cap_id, next_off, payload in ext_caps:
            header = (next_off << 20) | (1 << 16) | cap_id
            data[offset : offset + 4] = header.to_bytes(4, "little")
            for i, dword in enumerate(payload):
                p_off = offset + 4 + i * 4
                data[p_off : p_off + 4] = dword.to_bytes(4, "little")
            offset = next_off if next_off else size

    return data.hex()


class TestExtractDsnValue:
    def test_returns_dsn_when_ext_cap_3_present(self):
        hex_cs = _make_config_space(
            ext_caps=[(0x0003, 0, [0x01001B21, 0xDEADBEEF])]
        )
        result = extract_dsn_value(hex_cs)
        assert result == (0xDEADBEEF << 32) | 0x01001B21

    def test_returns_none_when_no_dsn_cap(self):
        hex_cs = _make_config_space(
            ext_caps=[(0x0001, 0, [0, 0])]
        )
        assert extract_dsn_value(hex_cs) is None

    def test_returns_none_when_ext_cap_chain_empty(self):
        hex_cs = _make_config_space(ext_caps=None)
        assert extract_dsn_value(hex_cs) is None

    def test_returns_none_on_malformed_hex(self):
        assert extract_dsn_value("not-hex") is None
        assert extract_dsn_value("") is None
        assert extract_dsn_value("ab") is None

    def test_walks_chain_to_find_dsn_when_not_first(self):
        hex_cs = _make_config_space(
            ext_caps=[
                (0x0001, 0x140, [0, 0]),
                (0x0003, 0, [0x01001B21, 0xDEADBEEF]),
            ]
        )
        assert extract_dsn_value(hex_cs) == (0xDEADBEEF << 32) | 0x01001B21


from pcileechfwgenerator.device_clone.donor_capability_extractor import (
    extract_aer_supported,
    extract_ari_supported,
)


class TestExtractAerSupported:
    def test_returns_true_when_aer_ext_cap_present(self):
        hex_cs = _make_config_space(
            ext_caps=[(0x0001, 0, [0, 0, 0, 0, 0])]
        )
        assert extract_aer_supported(hex_cs) is True

    def test_returns_false_when_aer_absent(self):
        hex_cs = _make_config_space(
            ext_caps=[(0x0003, 0, [0x01001B21, 0xDEADBEEF])]
        )
        assert extract_aer_supported(hex_cs) is False

    def test_returns_false_on_empty_chain(self):
        assert extract_aer_supported(_make_config_space(ext_caps=None)) is False

    def test_returns_none_on_malformed_hex(self):
        assert extract_aer_supported("not-hex") is None


class TestExtractAriSupported:
    def test_returns_true_when_ari_ext_cap_present(self):
        hex_cs = _make_config_space(
            ext_caps=[(0x000E, 0, [0])]
        )
        assert extract_ari_supported(hex_cs) is True

    def test_returns_false_when_ari_absent(self):
        hex_cs = _make_config_space(
            ext_caps=[(0x0001, 0, [0, 0])]
        )
        assert extract_ari_supported(hex_cs) is False

    def test_returns_none_on_malformed_hex(self):
        assert extract_ari_supported("") is None


from pcileechfwgenerator.device_clone.donor_capability_extractor import (
    extract_cpl_timeout_caps,
)


def _make_cs_with_pcie_cap(
    *,
    pcie_cap_offset: int = 0x40,
    devcap2_value: int = 0,
) -> str:
    """Build a config-space hex with the PCIe capability at pcie_cap_offset."""
    size = 4096
    data = bytearray(size)
    data[0x06] = 0x10
    data[0x34] = pcie_cap_offset
    data[pcie_cap_offset] = 0x10
    data[pcie_cap_offset + 1] = 0x00
    devcap2_off = pcie_cap_offset + 0x24
    data[devcap2_off : devcap2_off + 4] = devcap2_value.to_bytes(4, "little")
    return data.hex()


class TestExtractCplTimeoutCaps:
    @pytest.mark.parametrize("ctrs_bits,expected_ranges", [
        (0b0000, "none"),
        (0b0001, "A"),
        (0b0010, "B"),
        (0b0100, "C"),
        (0b1000, "D"),
        (0b0011, "AB"),
        (0b0110, "BC"),
        (0b1110, "BCD"),
        (0b1111, "ABCD"),
    ])
    def test_decodes_ctrs_bits_to_xilinx_token(self, ctrs_bits, expected_ranges):
        hex_cs = _make_cs_with_pcie_cap(devcap2_value=ctrs_bits)
        result = extract_cpl_timeout_caps(hex_cs)
        assert result["cpl_timeout_ranges"] == expected_ranges
        assert result["cpl_timeout_disable_supported"] is False

    def test_decodes_disable_supported_bit(self):
        hex_cs = _make_cs_with_pcie_cap(devcap2_value=0b00010000)
        result = extract_cpl_timeout_caps(hex_cs)
        assert result["cpl_timeout_disable_supported"] is True
        assert result["cpl_timeout_ranges"] == "none"

    @pytest.mark.parametrize("unsupported_bits", [
        0b0101,
        0b1001,
        0b1010,
        0b1100,
        0b0111,
        0b1011,
        0b1101,
    ])
    def test_returns_none_ranges_for_unsupported_combinations(self, unsupported_bits):
        hex_cs = _make_cs_with_pcie_cap(devcap2_value=unsupported_bits)
        result = extract_cpl_timeout_caps(hex_cs)
        assert result["cpl_timeout_ranges"] is None

    def test_returns_all_none_when_pcie_cap_absent(self):
        size = 4096
        data = bytearray(size)
        data[0x06] = 0x10
        data[0x34] = 0x40
        data[0x40] = 0x01
        data[0x41] = 0x00
        hex_cs = data.hex()
        result = extract_cpl_timeout_caps(hex_cs)
        assert result == {
            "cpl_timeout_ranges": None,
            "cpl_timeout_disable_supported": None,
        }

    def test_returns_all_none_on_malformed_hex(self):
        result = extract_cpl_timeout_caps("not-hex")
        assert result == {
            "cpl_timeout_ranges": None,
            "cpl_timeout_disable_supported": None,
        }


from pcileechfwgenerator.device_clone.donor_capability_extractor import (
    extract_donor_capabilities,
)


class TestExtractDonorCapabilities:
    def test_aggregates_all_four_capability_groups(self):
        size = 4096
        data = bytearray(size)
        data[0x06] = 0x10
        data[0x34] = 0x40
        data[0x40] = 0x10
        devcap2 = 0b00011111
        data[0x64 : 0x68] = devcap2.to_bytes(4, "little")
        data[0x100 : 0x104] = ((0x140 << 20) | (1 << 16) | 0x0001).to_bytes(4, "little")
        data[0x140 : 0x144] = ((0x180 << 20) | (1 << 16) | 0x0003).to_bytes(4, "little")
        data[0x144 : 0x148] = (0x01001B21).to_bytes(4, "little")
        data[0x148 : 0x14C] = (0xDEADBEEF).to_bytes(4, "little")
        data[0x180 : 0x184] = ((0 << 20) | (1 << 16) | 0x000E).to_bytes(4, "little")
        hex_cs = data.hex()

        result = extract_donor_capabilities(hex_cs)
        assert result == {
            "dsn_value": (0xDEADBEEF << 32) | 0x01001B21,
            "supports_aer": True,
            "ari_capable": True,
            "cpl_timeout_ranges": "ABCD",
            "cpl_timeout_disable_sup": True,
        }

    def test_returns_all_none_on_malformed_hex(self):
        result = extract_donor_capabilities("not-hex")
        assert result == {
            "dsn_value": None,
            "supports_aer": None,
            "ari_capable": None,
            "cpl_timeout_ranges": None,
            "cpl_timeout_disable_sup": None,
        }

    def test_keys_match_override_extractor_read_paths(self):
        result = extract_donor_capabilities(_make_config_space(ext_caps=None))
        assert set(result.keys()) == {
            "dsn_value",
            "supports_aer",
            "ari_capable",
            "cpl_timeout_ranges",
            "cpl_timeout_disable_sup",
        }
