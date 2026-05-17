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
