"""Tests for donor PCIe capability extraction (Step 3 of override expansion).

These tests use crafted config-space hex strings — no real donor capture
needed. The strings are sized to PCI_CONFIG_SPACE_MIN_SIZE so ConfigSpace
accepts them.
"""
import sys
from pathlib import Path


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
