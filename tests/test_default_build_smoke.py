#!/usr/bin/env python3
"""Smoke tests for default build path.

Focus: critical context construction and rendering a minimal set of templates
that exercise the PCILeech generation pipeline without requiring donor-specific
dynamic profiling data. These tests intentionally avoid heavy I/O and large
dependency chains while validating that the baseline context produced by
UnifiedContextBuilder is consumable by the template system.
"""

from typing import Any, Dict

import pytest
from pcileechfwgenerator.templating.template_renderer import (
    TemplateRenderer,
    TemplateRenderError,
)
from pcileechfwgenerator.utils.unified_context import (
    UnifiedContextBuilder,
    ensure_template_compatibility,
)
from pcileechfwgenerator.utils.validation_constants import REQUIRED_CONTEXT_SECTIONS


def _context_to_dict(obj: Any) -> Dict[str, Any]:
    """Helper to convert TemplateObject-like structures into plain dicts."""
    if hasattr(obj, "to_dict"):
        try:
            return obj.to_dict()  # type: ignore[attr-defined]
        except Exception:
            pass
    if isinstance(obj, dict):
        return obj
    return dict(obj)


@pytest.fixture(scope="module")
def baseline_context() -> Dict[str, Any]:
    builder = UnifiedContextBuilder()
    ctx_obj = builder.create_complete_template_context(
        vendor_id="10ee", device_id="7024"
    )
    ctx = _context_to_dict(ctx_obj)
    # Compatibility normalization (adds template-ready wrappers where needed)
    ctx = ensure_template_compatibility(dict(ctx))
    return ctx


def test_unified_context_has_required_sections(baseline_context):
    """All declared required sections must be present in baseline context."""
    missing = [s for s in REQUIRED_CONTEXT_SECTIONS if s not in baseline_context]
    assert not missing, f"Missing required context sections: {missing}"
    # Spot-check critical identifiers.
    for key in ("vendor_id", "device_id", "device_signature"):
        assert baseline_context.get(key), f"Expected '{key}' present and non-empty"


def test_render_critical_pcileech_templates(baseline_context):
    """Render a minimal set of critical templates to ensure fallback paths work."""
    renderer = TemplateRenderer(strict=True)
    critical = [
        # No-model builds select the generic implementation in the controller.
        "sv/pcileech_tlps128_bar_controller.sv.j2",
        # Python integration script
        "python/pcileech_build_integration.py.j2",
    ]

    for name in critical:
        assert renderer.template_exists(name), f"Template missing: {name}"
        try:
            content = renderer.render_template(name, dict(baseline_context))
        except TemplateRenderError as e:
            pytest.fail(f"Failed rendering {name}: {e}")
        # Assertions specific to each template type.
        if name.endswith("pcileech_tlps128_bar_controller.sv.j2"):
            assert "pcileech_bar_impl_zerowrite4k" in content
        elif name.endswith("pcileech_build_integration.py.j2"):
            # Python integration script does not embed the device signature.
            # Assert header marker only.
            assert "pcileech build integration".lower() in content.lower()


def test_bar_impl_rejects_missing_model(baseline_context):
    """The device template cannot invent a writable BAR when learning is absent."""
    renderer = TemplateRenderer(strict=True)
    with pytest.raises(TemplateRenderError, match="nonempty learned register model"):
        renderer.render_template(
            "sv/pcileech_bar_impl_device.sv.j2", dict(baseline_context)
        )


def test_interrupt_config_strategy_default(baseline_context):
    """interrupt_config.strategy should be injected as 'none' if not provided."""
    # Remove interrupt_config to force validator default path
    ctx = dict(baseline_context)
    ctx.pop("interrupt_config", None)
    # Supply an observed read-only storage fixture; model absence now fails.
    ctx["bar_model"] = {
        "size": 4096,
        "registers": {0: {"width": 4, "reset": 0xC7813596, "rw_mask": 0, "hints": {}}},
    }
    renderer = TemplateRenderer(strict=True)
    content = renderer.render_template("sv/pcileech_bar_impl_device.sv.j2", ctx)
    # Presence is indirect (conditional blocks disabled);
    # ensure no StrictUndefined error
    assert "module pcileech_bar_impl_device" in content
