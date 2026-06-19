"""The generated build-integration module must require real timing data.

Timing parameters come from the collected donor profile (AGENTS.md: no
fallbacks for timing). The generated ``TimingConfig`` therefore has no field
defaults, and ``create_build_config`` raises ``ValueError`` when any timing key
is missing rather than silently inventing one.
"""

import inspect

import pytest
from pcileechfwgenerator.templating.template_renderer import TemplateRenderer

TEMPLATE = "python/pcileech_build_integration.py.j2"
RENDER_CONTEXT = {
    "pcileech_modules": ["pcileech_fifo", "bar_controller"],
    "integration_type": "pcileech",
}
COMPLETE_TIMING = {
    "clock_frequency_mhz": 100.0,
    "read_latency": 4,
    "write_latency": 2,
    "burst_length": 16,
    "timeout_cycles": 1024,
}


def _load_generated_module():
    """Render the build-integration template and exec it into a namespace."""
    renderer = TemplateRenderer()
    source = renderer.render_template(TEMPLATE, RENDER_CONTEXT)
    namespace: dict = {}
    exec(compile(source, "generated_build_integration.py", "exec"), namespace)
    return namespace


def test_timing_config_has_no_field_defaults():
    ns = _load_generated_module()
    sig = inspect.signature(ns["TimingConfig"])
    required = [
        p.name for p in sig.parameters.values() if p.default is inspect.Parameter.empty
    ]
    assert set(required) == set(COMPLETE_TIMING.keys())


def test_create_build_config_succeeds_with_complete_timing():
    ns = _load_generated_module()
    cfg = ns["create_build_config"]({"timing_config": dict(COMPLETE_TIMING)})
    assert cfg.timing.clock_frequency_mhz == 100.0
    assert cfg.timing.burst_length == 16


@pytest.mark.parametrize("missing_key", sorted(COMPLETE_TIMING.keys()))
def test_create_build_config_raises_on_missing_timing_key(missing_key):
    ns = _load_generated_module()
    partial = {k: v for k, v in COMPLETE_TIMING.items() if k != missing_key}
    with pytest.raises(ValueError, match="timing"):
        ns["create_build_config"]({"timing_config": partial})


def test_create_build_config_raises_on_empty_timing():
    ns = _load_generated_module()
    with pytest.raises(ValueError, match="timing"):
        ns["create_build_config"]({"timing_config": {}})


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
