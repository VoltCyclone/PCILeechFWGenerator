#!/usr/bin/env python3
"""
Tests for MSI-X/BAR validator hook integration in PCILeechGenerator.

Covers:
- Valid configuration passes
- Invalid configuration (overlap) fails fast with clear error
"""
from typing import Any, Dict

import pytest

from src.device_clone.pcileech_generator import (PCILeechGenerationConfig,
                                                 PCILeechGenerationError,
                                                 PCILeechGenerator)


class DummyConfigSpaceManager:
    """Lightweight stub matching the real manager's interface.

    Bars are supplied via class attributes set by the test helper.
    """

    # Set per-test via make_generator
    _bars: list = []
    _cfg: bytes = bytes(256)

    def __init__(self, bdf: str, strict_vfio: bool = True):
        self.bdf = bdf
        self.strict_vfio = strict_vfio

    def _read_sysfs_config_space(self) -> bytes:
        # Minimal 256-byte config to satisfy size checks
        cfg = type(self)._cfg
        if isinstance(cfg, (bytes, bytearray)):
            return bytes(cfg)
        return bytes(256)

    # Some code paths may call this variant; keep identical behavior

    def read_vfio_config_space(self) -> bytes:
        return self._read_sysfs_config_space()

    def extract_device_info(self, config_space_bytes: bytes) -> Dict[str, Any]:
        # Provide device ids and our provided bars
        return {
            "vendor_id": 0x1234,
            "device_id": 0x5678,
            "bars": list(type(self)._bars),
        }


class DummyVFIOBinder:
    def __init__(self, bdf):
        self.bdf = bdf

    def __enter__(self):
        return f"/dev/vfio/{self.bdf}"

    def __exit__(self, exc_type, exc, tb):
        return False


@pytest.fixture(autouse=True)
def patch_dependencies(monkeypatch):
    # Patch VFIO binder
    monkeypatch.setitem(
        __import__("sys").modules,
        "src.cli.vfio_handler",
        type("m", (), {"VFIOBinder": DummyVFIOBinder}),
    )

    # Patch ConfigSpaceManager in generator to use dummy class with expected API
    monkeypatch.setattr(
        "src.device_clone.pcileech_generator.ConfigSpaceManager",
        DummyConfigSpaceManager,
        raising=True,
    )

    yield


def make_generator(bars, msix_cap) -> PCILeechGenerator:
    # Bars are BarInfo-like dicts the generator will coerce.
    # msix_cap is a dict with table_size (N), table_bir, table_offset,
    # pba_bir, pba_offset.
    # Configure the dummy manager factory
    ConfigSpaceManager = __import__(
        "src.device_clone.pcileech_generator", fromlist=["ConfigSpaceManager"]
    ).ConfigSpaceManager
    # Provide bars and a minimal config space blob via the dummy class attributes
    ConfigSpaceManager._bars = bars
    ConfigSpaceManager._cfg = bytes(256)

    cfg = PCILeechGenerationConfig(
        device_bdf="0000:00:00.0",
        strict_validation=True,
        enable_behavior_profiling=False,
    )
    gen = PCILeechGenerator(cfg)

    # Patch internal helpers to feed msix_data and to skip expensive steps

    def _preload_msix_data_early_stub():
        if not msix_cap:
            return None
        return {
            "table_size": msix_cap.get("table_size", 0),
            "table_bir": msix_cap.get("table_bir", 0),
            "table_offset": msix_cap.get("table_offset", 0),
            "pba_bir": msix_cap.get("pba_bir", 0),
            "pba_offset": msix_cap.get("pba_offset", 0),
            "enabled": True,
            "function_mask": False,
        }

    gen._preload_msix_data_early = _preload_msix_data_early_stub

    # Skip actual VFIO msix table capture

    def _capture_msix_table_entries(msix_data: Dict[str, Any]):
        return None

    gen._capture_msix_table_entries = _capture_msix_table_entries

    # Short-circuit heavy context building and validation

    def _build_template_context(
        behavior_profile,
        config_space_data,
        msix_data,
        interrupt_strategy,
        interrupt_vectors,
    ) -> Dict[str, Any]:
        return {
            "vendor_id": "1234",
            "device_id": "5678",
            "config_space_data": config_space_data,
            # Minimal additions required by enforced TCL templates.
            # Tests focus on MSI-X/BAR validation; provide stable FPGA metadata
            # so template rendering doesn't fail on unrelated missing keys.
            "fpga_family": "7series",
            "fpga_part": "xc7a35t-csg324-1",
            "board_name": "test_board",
        }

    gen._build_template_context = _build_template_context

    def _validate_template_context(context: Dict[str, Any]):
        return None

    gen._validate_template_context = _validate_template_context

    # Keep SV generation lightweight by stubbing to return a minimal module
    gen.sv_generator.generate_systemverilog_modules = (
        lambda template_context, behavior_profile=None: {
            "pcileech_tlps128_bar_controller": "module foo; endmodule"
        }
    )

    # Avoid generating extra components

    def _generate_firmware_components(template_context: Dict[str, Any]):
        return {}

    gen._generate_firmware_components = _generate_firmware_components

    return gen


def test_validator_hook_passes_on_valid_layout():
    bars = [
        {"bar": 0, "type": "memory", "size": 0x10000, "prefetchable": False},
    ]
    msix = {
        "table_size": 8,
        "table_bir": 0,
        "table_offset": 0x8000,
        "pba_bir": 0,
        "pba_offset": 0x9000,
    }

    gen = make_generator(bars, msix)
    result = gen.generate_pcileech_firmware()
    assert "systemverilog_modules" in result


def test_validator_hook_fails_on_overlap():
    bars = [
        {"bar": 0, "type": "memory", "size": 0x10000, "prefetchable": False},
    ]
    # Overlapping table and PBA
    msix = {
        "table_size": 8,
        "table_bir": 0,
        "table_offset": 0x1000,  # table 0x1000-0x107F (8 entries * 16B)
        "pba_bir": 0,
        "pba_offset": 0x1070,  # overlaps last 16B of table
    }

    gen = make_generator(bars, msix)
    with pytest.raises(PCILeechGenerationError):
        gen.generate_pcileech_firmware()
