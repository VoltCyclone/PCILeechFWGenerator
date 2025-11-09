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

from src.templating.template_renderer import (
    TemplateRenderer,
    TemplateRenderError,
)
from src.utils.unified_context import (
    UnifiedContextBuilder,
    ensure_template_compatibility,
)
from src.utils.validation_constants import REQUIRED_CONTEXT_SECTIONS


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
        # BAR implementation fallback & register map
        "sv/pcileech_bar_impl_device.sv.j2",
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
        if name.endswith("pcileech_bar_impl_device.sv.j2"):
            # Device signature expected in SV output.
            assert str(baseline_context.get("device_signature")) in content
            # Fallback path text should appear when bar_model is None.
            assert "No learned register map" in content or "Fallback" in content
            assert "module pcileech_bar_impl_device" in content
        elif name.endswith("pcileech_build_integration.py.j2"):
            # Python integration script does not embed the device signature.
            # Assert header marker only.
            assert "pcileech build integration".lower() in content.lower()


def test_bar_impl_injects_default_bar_size(baseline_context):
    """BAR_SIZE parameter should fall back to 4096 when bar_model is absent."""
    renderer = TemplateRenderer(strict=True)
    tmpl = "sv/pcileech_bar_impl_device.sv.j2"
    content = renderer.render_template(tmpl, dict(baseline_context))
    # Parameter line should include BAR_SIZE = 4096 when no model provided.
    assert (
        "BAR_SIZE = 4096" in content
        or "bar_size = 4096" in content.lower()
    )


def test_interrupt_config_strategy_default(baseline_context):
    """interrupt_config.strategy should be injected as 'none' if not provided."""
    # Remove interrupt_config to force validator default path
    ctx = dict(baseline_context)
    ctx.pop("interrupt_config", None)
    renderer = TemplateRenderer(strict=True)
    content = renderer.render_template("sv/pcileech_bar_impl_device.sv.j2", ctx)
    # Presence is indirect (conditional blocks disabled);
    # ensure no StrictUndefined error
    assert "module pcileech_bar_impl_device" in content
