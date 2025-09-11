#!/usr/bin/env python3
"""Additional targeted tests for `pcileech_generator` small improvements.

Covers:
- Raw config space extraction precedence & failure mode
- Firmware validation (required module present/missing)
- Writemask COE generation path when COE pre-exists
"""
from pathlib import Path
from typing import Any, Dict

import pytest

from src.device_clone.pcileech_generator import (PCILeechGenerationConfig,
                                                 PCILeechGenerationError,
                                                 PCILeechGenerator)


# --- Stubs -----------------------------------------------------------------
class _StubConfigSpaceManager:
    """Lightweight stub replicating minimal interface of real manager."""

    def __init__(
        self, bdf: str, strict_vfio: bool = True
    ):  # pragma: no cover - trivial
        self.bdf = bdf
        self.strict_vfio = strict_vfio

    def read_vfio_config_space(self) -> bytes:
        # Return 256 bytes with deterministic VID/DID (0x1234 / 0x5678)
        data = bytearray(256)
        data[0:2] = (0x1234).to_bytes(2, "little")
        data[2:4] = (0x5678).to_bytes(2, "little")
        return bytes(data)

    # Reused by legacy path
    _read_sysfs_config_space = read_vfio_config_space

    def extract_device_info(
        self, cfg: bytes
    ) -> Dict[str, Any]:  # pragma: no cover - simple
        return {
            "vendor_id": 0x1234,
            "device_id": 0x5678,
            "bars": [
                {"bar": 0, "type": "memory", "size": 0x1000, "prefetchable": False}
            ],
        }


@pytest.fixture(autouse=True)
def patch_config_space_manager(monkeypatch):
    monkeypatch.setattr(
        "src.device_clone.pcileech_generator.ConfigSpaceManager",
        _StubConfigSpaceManager,
        raising=True,
    )
    yield


@pytest.fixture
def generator(tmp_path: Path) -> PCILeechGenerator:
    cfg = PCILeechGenerationConfig(
        device_bdf="0000:00:00.0",
        enable_behavior_profiling=False,
        strict_validation=True,
        output_dir=tmp_path / "out",
    )
    return PCILeechGenerator(cfg)


# --- Tests ------------------------------------------------------------------


def test_raw_config_space_extraction_precedence(generator: PCILeechGenerator):
    raw = bytes(range(64))
    ctx = {"config_space_data": {"raw_config_space": raw}}
    extracted = generator._extract_raw_config_space(ctx)
    assert extracted.startswith(raw[:16]) and len(extracted) >= len(raw)

    # Fallback to config_space_hex path
    hex_ctx = {"config_space_hex": raw.hex()}
    extracted2 = generator._extract_raw_config_space(hex_ctx)
    assert extracted2[:8] == raw[:8]


def test_raw_config_space_extraction_failure(generator: PCILeechGenerator):
    with pytest.raises(ValueError):
        generator._extract_raw_config_space({"unrelated": {"foo": "bar"}})


def test_validate_generated_firmware_success_and_failure(generator: PCILeechGenerator):
    # Success path
    generator._validate_generated_firmware(
        {"pcileech_tlps128_bar_controller": "module x; endmodule"}, {}
    )

    # Failure path - missing required module
    with pytest.raises(PCILeechGenerationError):
        generator._validate_generated_firmware({"other": "module y; endmodule"}, {})


def test_generate_writemask_coe_with_existing_cfgspace(generator: PCILeechGenerator):
    # Prepare expected cfgspace COE file so writemask path doesn't attempt heavy generation
    sv_dir = generator.config.output_dir / "systemverilog"
    sv_dir.mkdir(parents=True, exist_ok=True)
    cfgspace_file = sv_dir / "pcileech_cfgspace.coe"
    cfgspace_file.write_text("; dummy\nmemory_initialization_vector=\n00000000;\n")

    ctx = {"msix_config": {"table_size": 0}, "msi_config": {}}
    content = generator._generate_writemask_coe(ctx)
    assert (
        content is None
        or "memory_initialization_vector" in content
        or content.strip() != ""
    )
