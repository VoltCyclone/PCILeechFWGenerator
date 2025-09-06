#!/usr/bin/env python3
"""Tests for MSI-X data persistence and targeted logging before rendering."""

from unittest.mock import patch

from src.templating.systemverilog_generator import SystemVerilogGenerator


def _minimal_valid_context_with_msix():
    return {
        "device_signature": "32'hAABBCCDD",
        "device_config": {
            "vendor_id": "10EC",
            "device_id": "8168",
            "subsystem_vendor_id": "0000",
            "subsystem_device_id": "0000",
            "class_code": "020000",
            "revision_id": "00",
        },
        "bar_config": {"bars": []},
        "generation_metadata": {},
        # Provide MSI-X payload as both table_init_hex and table_entries
        "msix_data": {
            "table_size": 2,
            "table_bir": 2,
            "table_offset": 0x1000,
            "table_init_hex": (
                "00000001\n00000000\n00000002\n00000000\n"
                "00000003\n00000000\n00000004\n00000000\n"
            ),
            "table_entries": [
                {
                    "vector": 0,
                    "data": "01000000000000000200000000000000",
                    "enabled": True,
                },
                {
                    "vector": 1,
                    "data": "03000000000000000400000000000000",
                    "enabled": True,
                },
            ],
        },
    }


def test_msix_data_persistence_to_renderer_and_logging():
    gen = SystemVerilogGenerator()

    ctx = _minimal_valid_context_with_msix()
    captured_context = {}

    # Patch the module generator to capture the context it receives
    with patch.object(
        gen.module_generator, "generate_pcileech_modules"
    ) as mock_gen, patch(
        "src.templating.systemverilog_generator.log_info_safe"
    ) as mock_log:

        def _capture(enhanced_ctx, behavior_profile):
            nonlocal captured_context
            captured_context = enhanced_ctx
            return {"dummy": "ok"}

        mock_gen.side_effect = _capture

        modules = gen.generate_modules(ctx)

        assert modules == {"dummy": "ok"}
        # Ensure msix_data persisted into the enhanced context
        assert "msix_data" in captured_context
        md = captured_context["msix_data"]
        assert isinstance(md.get("table_entries"), list)
        assert isinstance(md.get("table_init_hex"), str)
        # Nested template_context mirror should also have msix_data
        tc = captured_context.get("template_context", {})
        assert tc.get("msix_data") == md

        # Verify the targeted pre-render log was emitted
        log_msgs = [str(call.args[1]) for call in mock_log.call_args_list if call.args]
        assert any("Pre-render MSI-X" in m for m in log_msgs)
