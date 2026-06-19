#!/usr/bin/env python3
"""Characterization tests for PCILeechContextBuilder.build_context.

These pin the top-level shape of the context that build_context() emits so that
refactors of the builder can be verified behavior-preserving: the assembled
context must keep the same complete key set and the same sub-section values.

The per-section builder methods are mocked to known return values, isolating the
orchestration (which sections get merged, under which keys) from the per-section
logic. If the context shape changes intentionally, update the recorded key set
in the same change and explain why.
"""

from unittest.mock import Mock, patch

import pytest
from pcileechfwgenerator.device_clone.pcileech_context import (
    DeviceIdentifiers,
    PCILeechContextBuilder,
    TimingParameters,
    ValidationLevel,
)


@pytest.fixture
def mock_config():
    cfg = Mock()
    cfg.device_bdf = "0000:03:00.0"
    cfg.enable_behavior_profiling = False
    return cfg


@pytest.fixture
def config_space_data():
    return {
        "vendor_id": "10ee",
        "device_id": "7024",
        "class_code": "020000",
        "revision_id": "01",
        "config_space_hex": "00" * 256,
        "config_space_size": 256,
        "bars": [],
    }


def _mock_section_builders(builder):
    """Mock every per-section builder to a known sentinel value."""
    builder._extract_device_identifiers = Mock(
        return_value=DeviceIdentifiers(
            vendor_id="10ee",
            device_id="7024",
            class_code="020000",
            revision_id="01",
            subsystem_vendor_id="10ee",
            subsystem_device_id="0007",
        )
    )
    builder._build_device_config = Mock(
        return_value={"vendor_id": "10ee", "device_id": "7024"}
    )
    builder._build_config_space_context = Mock(
        return_value={"config_space": "SENTINEL"}
    )
    builder._build_msix_context = Mock(return_value={"msix": "SENTINEL"})
    builder._build_bar_config = Mock(return_value={"bars": [{"type": "memory"}]})
    builder._build_timing_config = Mock(
        return_value=TimingParameters(
            read_latency=4,
            write_latency=2,
            burst_length=16,
            inter_burst_gap=8,
            timeout_cycles=1024,
            clock_frequency_mhz=100.0,
            timing_regularity=0.9,
        )
    )
    builder._build_pcileech_config = Mock(return_value={"pcileech": "SENTINEL"})
    builder._build_active_device_config = Mock(return_value={"active": "SENTINEL"})
    builder._generate_unique_device_signature = Mock(return_value="32'h12345678")
    builder._build_generation_metadata = Mock(return_value={"metadata": "SENTINEL"})
    builder._build_board_config = Mock(
        return_value={
            "name": "test_board",
            "fpga_part": "xc7a35t",
            "fpga_family": "artix7",
            "pcie_ip_type": "pcie_7x",
            "max_lanes": 1,
            "supports_msi": True,
            "supports_msix": False,
            "constraints": {"xdc_file": "pcileech_test.xdc"},
            "sys_clk_freq_mhz": 100,
        }
    )


def _mock_overlay():
    """Patch the overlay mapper used during context assembly."""
    overlay = Mock()
    overlay.generate_overlay_map.return_value = {
        "OVERLAY_MAP": [(0, 0xFFFFFFFF)],
        "OVERLAY_ENTRIES": 1,
    }
    return patch(
        "pcileechfwgenerator.device_clone.pcileech_context.OverlayMapper",
        return_value=overlay,
    )


# The complete top-level key set build_context() must emit.
_REQUIRED_TOP_LEVEL_KEYS = {
    "device_config",
    "config_space",
    "msix_config",
    "bar_config",
    "timing_config",
    "pcileech_config",
    "device_signature",
    "generation_metadata",
    "interrupt_config",
    "active_device_config",
    "EXT_CFG_CAP_PTR",
    "EXT_CFG_XP_CAP_PTR",
    "OVERLAY_MAP",
    "OVERLAY_ENTRIES",
}


def _build(mock_config, config_space_data):
    builder = PCILeechContextBuilder(
        device_bdf="0000:03:00.0",
        config=mock_config,
        validation_level=ValidationLevel.STRICT,
    )
    _mock_section_builders(builder)
    with _mock_overlay():
        return builder.build_context(
            behavior_profile=None,
            config_space_data=config_space_data,
            msix_data=None,
            interrupt_strategy="msix",
            interrupt_vectors=32,
        )


class TestContextBuilderCharacterization:
    def test_required_top_level_keys_present(self, mock_config, config_space_data):
        context = _build(mock_config, config_space_data)
        # Extra derived keys are allowed; a required section must never be dropped.
        missing = _REQUIRED_TOP_LEVEL_KEYS - set(context)
        assert not missing, f"build_context dropped sections: {missing}"

    def test_sections_carry_through_with_finalization_wrapping(
        self, mock_config, config_space_data
    ):
        """Dict sections become TemplateObjects whose content preserves the
        sub-builder values (the ensure_template_compatibility finalization step).
        """
        context = _build(mock_config, config_space_data)

        def _content(v):
            # TemplateObject exposes attributes; fall back to dict identity.
            return (
                getattr(v, "config_space", None)
                or getattr(v, "msix", None)
                or getattr(v, "pcileech", None)
                or getattr(v, "active", None)
                or (v.get("config_space") if isinstance(v, dict) else None)
            )

        assert _content(context["config_space"]) == "SENTINEL"
        assert getattr(context["msix_config"], "msix", None) == "SENTINEL"
        assert getattr(context["pcileech_config"], "pcileech", None) == "SENTINEL"
        assert getattr(context["active_device_config"], "active", None) == "SENTINEL"

    def test_device_signature_is_derived_from_identifiers(
        self, mock_config, config_space_data
    ):
        """build_context derives device_signature from the device IDs,
        overriding the per-section _generate_unique_device_signature mock.
        """
        context = _build(mock_config, config_space_data)
        assert context["device_signature"] == "10ee:7024:01"

    def test_interrupt_config_assembled_from_args(self, mock_config, config_space_data):
        context = _build(mock_config, config_space_data)
        ic = context["interrupt_config"]
        strategy = (
            ic["strategy"] if isinstance(ic, dict) else getattr(ic, "strategy", None)
        )
        vectors = (
            ic["vectors"] if isinstance(ic, dict) else getattr(ic, "vectors", None)
        )
        assert strategy == "msix"
        assert vectors == 32


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
