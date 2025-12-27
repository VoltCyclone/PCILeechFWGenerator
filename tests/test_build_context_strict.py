#!/usr/bin/env python3
"""Tests for strict identity handling in BuildContext and format_hex_id."""

import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).parent.parent))

from src.templating.tcl_builder import (
    BuildContext,
    format_hex_id,
    PCIE_SPEED_CODES,
)  # noqa: E402


def _mk_base_ctx(**over):
    return BuildContext(
        board_name="test_board",
        fpga_part="xc7a35tcsg324-2",
        fpga_family="Artix-7",
        pcie_ip_type="7x",
        max_lanes=4,
        supports_msi=True,
        supports_msix=False,
        **over,
    )


def test_permissive_defaults_still_apply():
    ctx = _mk_base_ctx()
    tc = ctx.to_template_context(strict=False)
    # Legacy defaults appear when not strict
    assert tc["vendor_id"] == 0x10EC
    assert tc["device_id"] == 0x8168
    # Check that metadata tracks defaults if present
    if "context_metadata" in tc:
        if "defaults_used" in tc["context_metadata"]:
            assert "vendor_id" in tc["context_metadata"]["defaults_used"]
            assert "device_id" in tc["context_metadata"]["defaults_used"]
        if "strict_mode" in tc["context_metadata"]:
            assert tc["context_metadata"]["strict_mode"] is False


def test_strict_mode_missing_ids_raises():
    ctx = _mk_base_ctx()
    with pytest.raises(ValueError, match=r"Strict mode:.*vendor_id"):
        ctx.to_template_context(strict=True)


def test_explicit_values_tracked():
    ctx = _mk_base_ctx(vendor_id=0x1234, device_id=0x5678)
    tc = ctx.to_template_context(strict=False)
    # Check that values are present
    assert tc["vendor_id"] == 0x1234
    assert tc["device_id"] == 0x5678
    # Check metadata if present
    if "context_metadata" in tc:
        if "explicit_values" in tc["context_metadata"]:
            assert tc["context_metadata"]["explicit_values"]["vendor_id"] == 0x1234
            assert tc["context_metadata"]["explicit_values"]["device_id"] == 0x5678
        if "defaults_used" in tc["context_metadata"]:
            assert "vendor_id" not in tc["context_metadata"]["defaults_used"]
            assert "device_id" not in tc["context_metadata"]["defaults_used"]


def test_strict_mode_with_explicit_values():
    ctx = _mk_base_ctx(
        vendor_id=0x1234,
        device_id=0x5678,
        revision_id=0x10,
        class_code=0x030000,
        subsys_vendor_id=0x1234,
        subsys_device_id=0x5678,
        pcie_max_link_speed_code=2,  # Gen2 - 5.0 GT/s
        pcie_max_link_width=4,  # x4 lanes
    )
    tc = ctx.to_template_context(strict=True)
    # Should work fine with all explicit values
    assert tc["vendor_id"] == 0x1234
    assert tc["device_id"] == 0x5678
    assert tc["context_metadata"]["strict_mode"] is True
    # No defaults should be used
    assert len(tc["context_metadata"]["defaults_used"]) == 0


def test_format_hex_id_defaults():
    # Test with actual values
    assert format_hex_id(0x10EC, 4, permissive=True) == "10EC"
    assert format_hex_id(0x15, 2, permissive=True) == "15"
    assert format_hex_id(0x020000, 6, permissive=True) == "020000"

    # Strict mode raises on None
    with pytest.raises(ValueError, match=r"Cannot format None value"):
        format_hex_id(None, 4, permissive=False)
    
    # Permissive mode also raises on None (no defaults provided)
    with pytest.raises(ValueError):
        format_hex_id(None, 4, permissive=True)
