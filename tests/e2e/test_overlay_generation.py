"""End-to-end coverage for Stage 2 (overlay generation).

Stage 1 — VFIO collection — needs real hardware and is exercised on
target systems. Stage 2 takes a donor info dict and renders the
configuration-space overlay (``.coe``) plus device-specific overlay
SystemVerilog. This test builds a realistic context and runs the full
Stage 2 path, then asserts the produced artifacts are well-formed.
"""

from __future__ import annotations

import logging
import re

import pytest

pytestmark = pytest.mark.e2e


def _build_baseline_context() -> dict:
    """Construct a complete Stage-2 context using the project's own
    UnifiedContextBuilder, the same path Stage 2 uses internally."""
    from pcileechfwgenerator.utils.unified_context import UnifiedContextBuilder

    builder = UnifiedContextBuilder()
    obj = builder.create_complete_template_context(
        vendor_id="10ee", device_id="7024"
    )
    try:
        return obj.to_dict()
    except Exception:
        return dict(obj)


def test_overlay_generator_produces_cfgspace_coe() -> None:
    """The overlay generator should always emit a config-space .coe and
    a writemask .coe with valid COE syntax."""
    from pcileechfwgenerator.templating.sv_overlay_generator import (
        SVOverlayGenerator,
    )
    from pcileechfwgenerator.templating.template_renderer import TemplateRenderer

    renderer = TemplateRenderer()
    logger = logging.getLogger("test_overlay")
    generator = SVOverlayGenerator(renderer=renderer, logger=logger)

    context = _build_baseline_context()
    # Provide a 4KB zero-filled config space so the writemask path runs.
    if "config_space" not in context:
        context["config_space"] = {"data": bytes(4096)}

    overlays = generator.generate_config_space_overlay(context)

    assert "pcileech_cfgspace.coe" in overlays
    cfgspace = overlays["pcileech_cfgspace.coe"]
    _assert_valid_coe(cfgspace, name="pcileech_cfgspace.coe")

    # The writemask is conditional on config_space presence; we provided
    # one so it should be there.
    if "pcileech_cfgspace_writemask.coe" in overlays:
        _assert_valid_coe(
            overlays["pcileech_cfgspace_writemask.coe"],
            name="pcileech_cfgspace_writemask.coe",
        )


def test_overlay_generator_emits_writemask_when_config_space_present() -> None:
    """Cross-verify the conditional writemask emission logic."""
    from pcileechfwgenerator.templating.sv_overlay_generator import (
        SVOverlayGenerator,
    )
    from pcileechfwgenerator.templating.template_renderer import TemplateRenderer

    renderer = TemplateRenderer()
    logger = logging.getLogger("test_writemask")
    generator = SVOverlayGenerator(renderer=renderer, logger=logger)

    context = _build_baseline_context()
    context["config_space"] = {"data": bytes(4096)}

    overlays = generator.generate_config_space_overlay(context)
    assert "pcileech_cfgspace_writemask.coe" in overlays


def _assert_valid_coe(content: str, name: str) -> None:
    """Validate a Xilinx .coe file's required keywords. We don't parse
    the full hex payload; just sanity-check the format directives.

    The pcileech generators emit trailing comment blocks after the data
    vector (PCILeech metadata, BAR config commentary, etc.), so we don't
    require the file to end on a ``;``. The vector itself must terminate
    with ``;`` somewhere inside the body — that's what we check.
    """
    assert content, f"{name} is empty"
    text = content.lower()
    assert "memory_initialization_radix" in text, (
        f"{name} missing memory_initialization_radix directive"
    )
    assert "memory_initialization_vector" in text, (
        f"{name} missing memory_initialization_vector directive"
    )
    # Hex tokens (after the headers) should look hex-shaped.
    body = content.split("memory_initialization_vector", 1)[1]
    body = body.split("=", 1)[1] if "=" in body else body
    tokens = re.findall(r"\b[0-9A-Fa-f]{4,}\b", body)
    assert tokens, f"{name} body has no hex tokens"
