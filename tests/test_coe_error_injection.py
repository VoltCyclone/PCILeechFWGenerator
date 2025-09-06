import re
from copy import deepcopy
from pathlib import Path

from src.templating.sv_constants import SVTemplates


def _render_cfgspace_coe(context: dict) -> str:
    """Render the cfgspace COE template with the provided minimal context.

    We bypass the full generator to keep the test focused on the conditional
    comment logic for error injection while still exercising the real template
    (strict mode) with a minimally valid context.
    """
    from src.templating.template_renderer import TemplateRenderer

    tr = TemplateRenderer()
    return tr.render_template(SVTemplates.PCILEECH_CFGSPACE, context)


def _minimal_base_context() -> dict:
    """Build a minimal context satisfying strict template requirements.

    Only include keys actually referenced by the cfgspace template. Values are
    deliberately simple but structurally correct – no hardcoded device-unique
    semantics beyond dummy hex identifiers suitable for test isolation.
    """

    return {
        "header": "; test cfgspace header",  # consumed at top of template
        "device_config": {
            "vendor_id": "1234",
            "device_id": "5678",
            "revision_id": "01",
            "class_code": "010203",
            "device_bdf": "0000:00:00.0",
            # Must enable advanced features to include AER block where the
            # error injection comment resides.
            "enable_advanced_features": True,
            # Flag toggled per test case below.
            "enable_error_injection": False,
            # Command register logic path expects this sometimes.
            "enable_dma_operations": True,
        },
        # Minimal device object exposing vendor_id/device_id attributes for
        # helper resolution
        "device": type("DummyDev", (), {"vendor_id": "1234", "device_id": "5678"})(),
        # AER structure required when enable_advanced_features is True.
        "aer": {
            "uncorrectable_error_mask": 0x0,
            "uncorrectable_error_severity": 0x0,
            "correctable_error_mask": 0x0,
            "advanced_error_capabilities": 0x0,
        },
        # Required configs with explicit values (template errors if missing)
        "pcileech_config": {
            "command_timeout": 1000,
            "buffer_size": 4096,
            "enable_dma": True,
            "enable_scatter_gather": False,
        },
        "timing_config": {
            "read_latency": 10,
            "write_latency": 10,
            "burst_length": 4,
            "clock_frequency_mhz": 100,
        },
        # Structures the template accesses permissively
        "config_space": {},
        "bar_config": {"bars": []},
        # Minimal MSI-X config: num_vectors required for unguarded template formatting
        "msix_config": {"num_vectors": 1},
        "generation_metadata": {"generated_at": "test"},
    }


def test_coe_error_injection_comment_toggles(tmp_path: Path):
    base_ctx = _minimal_base_context()

    # Render without flag
    coe_no_flag = _render_cfgspace_coe(base_ctx)
    assert (
        "This build enables error injection hooks" not in coe_no_flag
    ), "Error injection comment unexpectedly present when flag is disabled"

    # Enable flag
    ctx_flag = deepcopy(base_ctx)
    ctx_flag["device_config"]["enable_error_injection"] = True
    coe_flag = _render_cfgspace_coe(ctx_flag)
    assert (
        "This build enables error injection hooks" in coe_flag
    ), "Error injection comment missing when flag is enabled"

    # Basic AER field sanity: ensure capability section emitted
    aer_field_markers = [
        "Uncorrectable Error Mask",
        "Uncorrectable Error Severity",
        "Correctable Error Mask",
        "Advanced Error Capabilities",
    ]
    for marker in aer_field_markers:
        assert re.search(marker.split()[0], coe_flag, re.IGNORECASE)
