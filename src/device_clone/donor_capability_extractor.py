"""Donor-side PCIe capability extraction for the IP override pipeline.

Reads raw config-space bytes (as a hex string) and returns structured donor
capabilities. Pure functions, no I/O, no logging — easy to unit-test against
crafted fixtures.

The keys this module produces are consumed by the override extractor
``donor_pcie_ip_config_from_result`` in
``src/vivado_handling/pcie_ip_donor_override.py``. The override module reads
specific paths in template_context; this module's job is to make sure those
paths exist with real donor values.
"""
from __future__ import annotations

from typing import Optional

from pcileechfwgenerator.pci_capability.core import (
    CapabilityWalker,
    ConfigSpace,
)

_EXT_CAP_ID_AER = 0x0001
_EXT_CAP_ID_DSN = 0x0003
_EXT_CAP_ID_ARI = 0x000E


def _safe_config_space(hex_data: str) -> Optional[ConfigSpace]:
    """Build a ConfigSpace from hex, returning None on any parse failure."""
    if not isinstance(hex_data, str) or not hex_data:
        return None
    try:
        return ConfigSpace(hex_data)
    except (ValueError, TypeError):
        return None


def extract_dsn_value(hex_data: str) -> Optional[int]:
    """Return the 64-bit Device Serial Number, or None if no DSN cap."""
    cs = _safe_config_space(hex_data)
    if cs is None:
        return None
    try:
        walker = CapabilityWalker(cs)
        for cap in walker.walk_extended_capabilities():
            if cap.cap_id == _EXT_CAP_ID_DSN:
                if not cs.has_data(cap.offset + 12, 0):
                    return None
                lower = cs.read_dword(cap.offset + 4)
                upper = cs.read_dword(cap.offset + 8)
                return (upper << 32) | lower
    except (IndexError, ValueError):
        return None
    return None


def _has_ext_cap(hex_data: str, cap_id: int) -> Optional[bool]:
    """Return True/False if the ext cap is present, None on parse failure."""
    cs = _safe_config_space(hex_data)
    if cs is None:
        return None
    try:
        walker = CapabilityWalker(cs)
        for cap in walker.walk_extended_capabilities():
            if cap.cap_id == cap_id:
                return True
        return False
    except (IndexError, ValueError):
        return None


def extract_aer_supported(hex_data: str) -> Optional[bool]:
    """Return True if AER (ext cap 0x0001) is in the donor's cap chain."""
    return _has_ext_cap(hex_data, _EXT_CAP_ID_AER)


def extract_ari_supported(hex_data: str) -> Optional[bool]:
    """Return True if ARI (ext cap 0x000E) is in the donor's cap chain."""
    return _has_ext_cap(hex_data, _EXT_CAP_ID_ARI)


_PCIE_STD_CAP_ID = 0x10
_DEVCAP2_OFFSET_FROM_CAP_BASE = 0x24

_CTRS_BITS_TO_TOKEN = {
    0b0000: "none",
    0b0001: "A",
    0b0010: "B",
    0b0100: "C",
    0b1000: "D",
    0b0011: "AB",
    0b0110: "BC",
    0b1110: "BCD",
    0b1111: "ABCD",
}


def _find_pcie_cap_offset(cs: ConfigSpace) -> Optional[int]:
    """Return the offset of the PCIe Express standard cap (0x10), or None."""
    try:
        walker = CapabilityWalker(cs)
        for cap in walker.walk_standard_capabilities():
            if cap.cap_id == _PCIE_STD_CAP_ID:
                return cap.offset
    except (IndexError, ValueError):
        return None
    return None


def extract_cpl_timeout_caps(hex_data: str) -> dict:
    """Return cpl_timeout_ranges and cpl_timeout_disable_supported from DevCap2."""
    none_result = {
        "cpl_timeout_ranges": None,
        "cpl_timeout_disable_supported": None,
    }
    cs = _safe_config_space(hex_data)
    if cs is None:
        return none_result

    pcie_cap_off = _find_pcie_cap_offset(cs)
    if pcie_cap_off is None:
        return none_result

    devcap2_off = pcie_cap_off + _DEVCAP2_OFFSET_FROM_CAP_BASE
    try:
        if not cs.has_data(devcap2_off, 4):
            return none_result
        devcap2 = cs.read_dword(devcap2_off)
    except (IndexError, ValueError):
        return none_result

    ctrs_bits = devcap2 & 0x0F
    disable_sup = bool(devcap2 & 0x10)

    return {
        "cpl_timeout_ranges": _CTRS_BITS_TO_TOKEN.get(ctrs_bits),
        "cpl_timeout_disable_supported": disable_sup,
    }
