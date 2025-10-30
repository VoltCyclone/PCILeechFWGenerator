#!/usr/bin/env python3
"""Test that PCILeechGenerator uses centralized device info lookup.

This guards against regressions re‑introducing duplicated config space parsing.
"""
from unittest.mock import patch

import pytest

from src.device_clone.pcileech_generator import (PCILeechGenerationConfig,
                                                 PCILeechGenerator)


@pytest.mark.unit
def test_generator_invokes_device_info_lookup():
    # Minimal valid config
    cfg = PCILeechGenerationConfig(
        device_bdf="0000:00:00.0",
        enable_behavior_profiling=False,
        strict_validation=False,  # Relax to avoid full context validation complexity
        fail_on_missing_data=False,
    )

    # vendor 0x8086 device 0x1234
    sample_cfg_bytes = b"\x86\x80\x34\x12" + b"\x00" * 252

    # Provide base extract_device_info missing vendor/device to force lookup usage
    base_extract = {"class_code": 0x020000, "revision_id": 0x01, "bars": []}

    # Mock downstream heavy steps to keep test focused and fast
    dummy_modules = {"pcileech_tlps128_bar_controller": "module m; endmodule"}

    with (
        patch(
            "src.device_clone.config_space_manager.ConfigSpaceManager"
            ".read_vfio_config_space",
            return_value=sample_cfg_bytes,
        ),
        patch(
            "src.device_clone.config_space_manager.ConfigSpaceManager"
            ".extract_device_info",
            return_value=base_extract,
        ),
        patch(
            "src.device_clone.pcileech_generator.lookup_device_info",
            return_value={
                "vendor_id": 0x8086,
                "device_id": 0x1234,
                "class_code": 0x020000,
                "revision_id": 0x01,
                "bars": [],
            },
        ) as mock_lookup,
        patch.object(
            PCILeechGenerator,
            "_build_template_context",
            return_value={
                "vendor_id": "8086",
                "device_id": "1234",
                "class_code": "020000",
                "revision_id": "01",
                "device_config": {},
                "config_space": {},
                "msix_config": {},
                "bar_config": {},
                "timing_config": {},
                "pcileech_config": {},
                "device_signature": "8086:1234:01",
            },
        ),
        patch.object(
            PCILeechGenerator,
            "_generate_systemverilog_modules",
            return_value=dummy_modules,
        ),
        patch.object(
            PCILeechGenerator,
            "_generate_firmware_components",
            return_value={"tcl_scripts": {}},
        ),
        patch.object(
            PCILeechGenerator,
            "_validate_generated_firmware",
            return_value=None,
        ),
    ):
        gen = PCILeechGenerator(cfg)
        result = gen.generate_pcileech_firmware()

    # Assert centralized lookup used
    assert mock_lookup.called, "lookup_device_info should be invoked during generation"

    # Sanity check that vendor/device propagated into config_space_data
    csd = result["config_space_data"]
    assert csd["vendor_id"] == "8086"
    assert csd["device_id"] == "1234"
