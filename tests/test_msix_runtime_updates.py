#!/usr/bin/env python3
"""Runtime MSI-X update handling tests.

Verifies that newly introduced msix_config runtime flags are present and that
SystemVerilog generation includes the capability register module wiring (by
string inspection of rendered template output). Keeps tests lightweight and
independent of hardware access.
"""
import importlib.util
from pathlib import Path
from unittest.mock import Mock

import pytest

from src.device_clone.pcileech_context import PCILeechContextBuilder
from src.templating.systemverilog_generator import AdvancedSVGenerator
from src.templating.advanced_sv_power import PowerManagementConfig
from src.templating.sv_config import ErrorHandlingConfig, PerformanceConfig

_TEMPLATE_HELPERS_PATH = (
    Path(__file__).parent / "test_systemverilog_template_integration.py"
)
_template_helpers_spec = importlib.util.spec_from_file_location(
    "test_systemverilog_template_integration", _TEMPLATE_HELPERS_PATH
)
if _template_helpers_spec is None or _template_helpers_spec.loader is None:
    raise ImportError("Unable to load TemplateContextBuilder helper module")
_template_helpers_module = importlib.util.module_from_spec(_template_helpers_spec)
_template_helpers_spec.loader.exec_module(_template_helpers_module)
TemplateContextBuilder = _template_helpers_module.TemplateContextBuilder


@pytest.fixture
def msix_capability_info():
    return {
        "table_size": 8,  # 8 vectors
        "table_bir": 0,
        "table_offset": 0x2000,
        "pba_bir": 0,
        "pba_offset": 0x3000,
        "enabled": False,
        "function_mask": False,
    }


@pytest.fixture
def msix_data(msix_capability_info):
    return {"capability_info": msix_capability_info}


@pytest.fixture
def minimal_config_space():
    return {
        "vendor_id": "10ee",
        "device_id": "7024",
        "class_code": "020000",
        "revision_id": "01",
        "config_space_hex": "00" * 256,
        "config_space_size": 256,
        "bars": [
            {
                "bar": 0,
                "index": 0,
                "type": "memory",
                "prefetchable": False,
                "address": 0x0,
                "size": 0x20000,
            }
        ],
    }


@pytest.fixture
def mock_config():
    class Cfg:  # Minimal stub config
        test_mode = True
        board = "default"

    return Cfg()


def test_msix_config_includes_runtime_flags(mock_config, msix_data):
    builder = PCILeechContextBuilder("0000:03:00.0", mock_config)
    msix_ctx = builder._build_msix_context(msix_data)

    # Verify runtime flags added
    for key in [
        "reset_clear",
        "use_byte_enables",
        "write_pba_allowed",
        "supports_staging",
        "supports_atomic_commit",
        "table_entry_dwords",
        "entry_size_bytes",
        "pba_size_dwords",
    ]:
        assert key in msix_ctx, f"Missing runtime key {key} in msix_config"

    assert msix_ctx["table_entry_dwords"] == 4
    assert msix_ctx["entry_size_bytes"] == 16


def test_bar_controller_template_contains_msix_capability_registers():
    """Inspect template directly for msix runtime wiring (fast + deterministic)."""
    tpl = Path("src/templates/sv/bar_controller.sv.j2")
    assert tpl.exists(), "bar_controller template missing"
    content = tpl.read_text(encoding="utf-8")
    assert (
        "msix_capability_registers" in content
    ), "msix_capability_registers module instantiation not present in template"
    for sig in [
        "msix_cap_wr",
        "msix_cap_addr",
        "msix_cap_wdata",
        "msix_cap_be",
    ]:
        assert sig in content, f"Missing signal {sig} in bar_controller template"


def test_msix_capability_render_preserves_signal_wiring():
    """Render bar controller to ensure MSI-X capability wiring remains intact."""
    generator = AdvancedSVGenerator(
        power_config=PowerManagementConfig(),
        error_config=ErrorHandlingConfig(),
        perf_config=PerformanceConfig(),
    )
    context = TemplateContextBuilder.create_minimal_context()

    result = generator.generate_pcileech_modules(context)
    bar_controller = result.get("pcileech_tlps128_bar_controller", "")

    assert bar_controller, "Failed to render pcileech_tlps128_bar_controller module"

    expected_wiring = [
        ".msix_cap_wr       (msix_cap_wr)",
        ".msix_cap_addr     (msix_cap_addr)",
        ".msix_cap_wdata    (msix_cap_wdata)",
        ".msix_cap_be       (msix_cap_be)",
    ]

    for snippet in expected_wiring:
        assert (
            snippet in bar_controller
        ), f"Missing MSI-X wiring snippet: {snippet.strip()}"
