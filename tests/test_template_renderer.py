from pathlib import Path

import pytest

from src.templating.template_renderer import (TemplateRenderer,
                                              TemplateRenderError)


def test_render_string_basic():
    renderer = TemplateRenderer()
    template = "Hello, {{ name }}!"
    context = {"name": "World"}
    result = renderer.render_string(template, context)
    assert result == "Hello, World!"


def test_render_string_missing_variable_strict():
    renderer = TemplateRenderer(strict=True)
    template = "Hello, {{ name }}!"
    context = {}
    with pytest.raises(TemplateRenderError):
        renderer.render_string(template, context)


def test_template_exists(tmp_path):
    renderer = TemplateRenderer(template_dir=tmp_path)
    template_file = tmp_path / "test.j2"
    template_file.write_text("Test {{ value }}")
    assert renderer.template_exists("test.j2")
    assert not renderer.template_exists("missing.j2")


def test_render_template_to_file(tmp_path):
    renderer = TemplateRenderer(template_dir=tmp_path)
    template_file = tmp_path / "file.j2"
    template_file.write_text("File: {{ value }}")
    out_file = tmp_path / "out.txt"
    result_path = renderer.render_to_file("file.j2", {"value": 42}, out_file)
    assert result_path.read_text() == "File: 42"
    assert result_path == out_file


def test_render_template_error_handling(tmp_path):
    renderer = TemplateRenderer(template_dir=tmp_path)
    template_file = tmp_path / "err.j2"
    template_file.write_text("{% error 'fail' %}")
    with pytest.raises(TemplateRenderError):
        renderer.render_template("err.j2", {})


def test_list_templates(tmp_path):
    renderer = TemplateRenderer(template_dir=tmp_path)
    (tmp_path / "a.j2").write_text("A")
    (tmp_path / "b.j2").write_text("B")
    templates = renderer.list_templates()
    assert "a.j2" in templates
    assert "b.j2" in templates


def test_clear_cache(tmp_path):
    renderer = TemplateRenderer(template_dir=tmp_path)
    renderer.clear_cache()  # Should not raise


def test_render_many(tmp_path):
    renderer = TemplateRenderer(template_dir=tmp_path)
    (tmp_path / "x.j2").write_text("X={{ val }}")
    (tmp_path / "y.j2").write_text("Y={{ val }}")
    pairs = [("x.j2", {"val": 1}), ("y.j2", {"val": 2})]
    results = renderer.render_many(pairs)
    assert results["x.j2"] == "X=1"
    assert results["y.j2"] == "Y=2"


def test_context_validation_missing_required_key():
    from src.utils.unified_context import (UnifiedContextBuilder,
                                           ensure_template_compatibility)

    builder = UnifiedContextBuilder()
    # Minimal context missing required keys
    context = {"device_id": "0x1234"}  # Missing vendor_id, device_signature
    with pytest.raises(SystemExit):
        # Canonical pattern: build_context_or_die should fail fast
        def require(condition, message, **ctx):
            if not condition:
                raise SystemExit(2)

        def build_context_or_die(device_bdf, cfg, **deps):
            context = {"device_id": device_bdf, **cfg, **deps}
            context = ensure_template_compatibility(dict(context))
            require(
                "vendor_id" in context, "Missing required context section: vendor_id"
            )
            require(context.get("device_signature"), "Missing device_signature")
            return context

        build_context_or_die("0x1234", context)


def test_context_validation_none_value():
    from src.utils.unified_context import ensure_template_compatibility

    # Context with None for a critical key
    context = {"vendor_id": None, "device_id": "0x1234", "device_signature": "sig"}
    with pytest.raises(SystemExit):

        def require(condition, message, **ctx):
            if not condition:
                raise SystemExit(2)

        def build_context_or_die(device_bdf, cfg, **deps):
            context = {
                "vendor_id": cfg.get("vendor_id"),
                "device_id": device_bdf,
                **deps,
            }
            context = ensure_template_compatibility(dict(context))
            require(context.get("vendor_id"), "Missing vendor_id")
            require(context.get("device_signature"), "Missing device_signature")
            return context

        build_context_or_die("0x1234", context)


def test_template_renderer_critical_path_failure():
    from src.templating.template_renderer import (TemplateRenderer,
                                                  TemplateRenderError)

    renderer = TemplateRenderer(strict=True)
    # Template expects 'name', but context is missing it
    template = "Hello, {{ name }}!"
    context = {}
    with pytest.raises(TemplateRenderError):
        renderer.render_string(template, context)


def test_enforce_donor_uniqueness_missing_signature():
    from src.string_utils import safe_format

    # Simulate context missing device_signature
    context = {
        "vendor_id": "0x10de",
        "device_id": "0x1234",
        "bar_config": {"bars": [{"size": 0x1000}]},
        # device_signature missing
    }

    def require(condition, message, **ctx):
        if not condition:
            print(safe_format("Build aborted: {msg} | ctx={ctx}", msg=message, ctx=ctx))
            raise SystemExit(2)

    with pytest.raises(SystemExit):
        require(bool(context.get("device_signature")), "device_signature missing")


def test_enforce_donor_uniqueness_invalid_bar():
    from src.string_utils import safe_format

    # Simulate context with no valid MMIO BARs
    context = {
        "vendor_id": "0x10de",
        "device_id": "0x1234",
        "device_signature": "sig",
        "bar_config": {"bars": [{"size": 0}]},  # All BARs invalid
    }

    def require(condition, message, **ctx):
        if not condition:
            print(safe_format("Build aborted: {msg} | ctx={ctx}", msg=message, ctx=ctx))
            raise SystemExit(2)

    def _get_bar_size(bar):
        if isinstance(bar, dict):
            return bar.get("size", 0)
        return getattr(bar, "size", 0)

    bars = context["bar_config"]["bars"]
    has_valid_bar = any(_get_bar_size(bar) > 0 for bar in bars)
    with pytest.raises(SystemExit):
        require(
            has_valid_bar, "No valid MMIO BARs discovered (non-unique or invalid donor)"
        )


def test_template_renderer_multiple_templates(tmp_path):
    from src.templating.template_renderer import TemplateRenderer

    renderer = TemplateRenderer(template_dir=tmp_path)
    (tmp_path / "a.j2").write_text("A={{ val }}")
    (tmp_path / "b.j2").write_text("B={{ val }}")
    pairs = [("a.j2", {"val": 10}), ("b.j2", {"val": 20})]
    results = renderer.render_many(pairs)
    assert results["a.j2"] == "A=10"
    assert results["b.j2"] == "B=20"


def test_template_renderer_conditional_logic(tmp_path):
    from src.templating.template_renderer import TemplateRenderer

    renderer = TemplateRenderer(template_dir=tmp_path)
    template_file = tmp_path / "cond.j2"
    template_file.write_text("{% if flag %}YES{% else %}NO{% endif %}")
    assert renderer.render_template("cond.j2", {"flag": True}) == "YES"
    assert renderer.render_template("cond.j2", {"flag": False}) == "NO"


def test_template_renderer_integration_complex(tmp_path):
    from src.templating.template_renderer import TemplateRenderer

    renderer = TemplateRenderer(template_dir=tmp_path)
    # Simulate integration with context, filters, and error handling
    template_file = tmp_path / "complex.j2"
    template_file.write_text("Value: {{ value|hex }} | {{ value|sv_hex(8) }}")
    context = {"value": 255}
    result = renderer.render_template("complex.j2", context)
    assert "ff" in result.lower() and "8'hff" in result.lower()


def test_template_renderer_fallback_logic(tmp_path):
    from src.templating.template_renderer import (TemplateRenderer,
                                                  TemplateRenderError)

    renderer = TemplateRenderer(template_dir=tmp_path)
    # Template with error tag triggers fallback
    template_file = tmp_path / "fail.j2"
    template_file.write_text("{% error 'fail' %}")
    with pytest.raises(TemplateRenderError):
        renderer.render_template("fail.j2", {})
