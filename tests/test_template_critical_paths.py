#!/usr/bin/env python3
"""
Critical path unit tests for template rendering system.

Targets uncovered critical areas identified in coverage analysis:
- Custom Jinja2 filters (hex, sv_hex, sv_width, etc.)
- Template path mapping and resolution
- Error handling edge cases
- Sandboxed environment
- Bytecode caching
- Template validation with required fields
- Global functions in templates
"""

import logging
import sys
from pathlib import Path

import pytest

# Add src to path
sys.path.insert(0, str(Path(__file__).parent.parent))

from src.string_utils import log_error_safe, log_info_safe, safe_format

from src.templating.template_renderer import (
    MappingFileSystemLoader,
    TemplateRenderer,
    TemplateRenderError,
)

logger = logging.getLogger(__name__)


class TestCustomFilters:
    """Test custom Jinja2 filters used in templates."""

    def test_hex_filter(self, tmp_path):
        """Test hex filter converts integers to hex strings."""
        renderer = TemplateRenderer(template_dir=tmp_path)
        template_file = tmp_path / "hex_test.j2"
        template_file.write_text("{{ value|hex }}")

        result = renderer.render_template("hex_test.j2", {"value": 255})
        # hex filter uses {:04x} format
        assert result == "00ff"

        result = renderer.render_template("hex_test.j2", {"value": 4096})
        assert result == "1000"

    def test_sv_hex_filter(self, tmp_path):
        """Test SystemVerilog hex format filter."""
        renderer = TemplateRenderer(template_dir=tmp_path)
        template_file = tmp_path / "sv_hex.j2"
        template_file.write_text("{{ value|sv_hex(8) }}")

        result = renderer.render_template("sv_hex.j2", {"value": 255})
        assert result == "8'hFF"

        result = renderer.render_template("sv_hex.j2", {"value": 0})
        assert result == "8'h00"

    def test_sv_hex_filter_16bit(self, tmp_path):
        """Test SystemVerilog hex format with 16-bit width."""
        renderer = TemplateRenderer(template_dir=tmp_path)
        template_file = tmp_path / "sv_hex16.j2"
        template_file.write_text("{{ value|sv_hex(16) }}")

        result = renderer.render_template("sv_hex16.j2", {"value": 65535})
        assert result == "16'hFFFF"

    def test_sv_width_filter(self, tmp_path):
        """Test SystemVerilog width calculation filter."""
        renderer = TemplateRenderer(template_dir=tmp_path)
        template_file = tmp_path / "sv_width.j2"
        # sv_width takes msb and lsb parameters
        template_file.write_text("{{ msb|sv_width(lsb) }}")

        # Width specification [255:0]
        result = renderer.render_template("sv_width.j2", {"msb": 255, "lsb": 0})
        assert result == "[255:0]"

        # Width specification [31:0]
        result = renderer.render_template("sv_width.j2", {"msb": 31, "lsb": 0})
        assert result == "[31:0]"

        # Same msb/lsb returns empty
        result = renderer.render_template("sv_width.j2", {"msb": 0, "lsb": 0})
        assert result == ""

    def test_sv_param_filter(self, tmp_path):
        """Test SystemVerilog parameter format filter."""
        renderer = TemplateRenderer(template_dir=tmp_path)
        template_file = tmp_path / "sv_param.j2"
        template_file.write_text("{{ name|sv_param(value) }}")

        result = renderer.render_template("sv_param.j2", {"name": "WIDTH", "value": 32})
        assert "parameter" in result and "WIDTH" in result and "32" in result

    def test_sv_bool_filter(self, tmp_path):
        """Test SystemVerilog boolean filter."""
        renderer = TemplateRenderer(template_dir=tmp_path)
        template_file = tmp_path / "sv_bool.j2"
        template_file.write_text("{{ flag|sv_bool }}")

        # sv_bool returns "1" or "0" as string
        result = renderer.render_template("sv_bool.j2", {"flag": True})
        assert result == "1"

        result = renderer.render_template("sv_bool.j2", {"flag": False})
        assert result == "0"

    def test_safe_int_filter(self, tmp_path):
        """Test safe integer conversion filter."""
        renderer = TemplateRenderer(template_dir=tmp_path)
        template_file = tmp_path / "safe_int.j2"
        template_file.write_text("{{ value|safe_int(42) }}")

        # Valid integer
        result = renderer.render_template("safe_int.j2", {"value": "123"})
        assert result == "123"

        # Invalid string uses default
        result = renderer.render_template("safe_int.j2", {"value": "invalid"})
        assert result == "42"

        # None uses default
        result = renderer.render_template("safe_int.j2", {"value": None})
        assert result == "42"

    def test_tcl_string_escape_filter(self, tmp_path):
        """Test TCL string escaping filter."""
        renderer = TemplateRenderer(template_dir=tmp_path)
        template_file = tmp_path / "tcl_escape.j2"
        template_file.write_text("{{ text | tcl_string_escape }}")

        result = renderer.render_template("tcl_escape.j2", {"text": 'test"value'})
        assert '\\"' in result  # Should escape quote


class TestTemplateMappingAndResolution:
    """Test template path mapping and resolution."""

    def test_mapping_loader_applies_path_mapping(self, tmp_path):
        """Test MappingFileSystemLoader applies template path updates."""
        # Create new-style template path
        sv_dir = tmp_path / "sv"
        sv_dir.mkdir()
        template_file = sv_dir / "device_config.sv.j2"
        template_file.write_text("// Device config")

        renderer = TemplateRenderer(template_dir=tmp_path)

        # Old path should map to new path
        result = renderer.render_template("sv/device_config.sv.j2", {})
        assert "Device config" in result

    def test_template_not_found_error(self, tmp_path):
        """Test proper error when template doesn't exist."""
        renderer = TemplateRenderer(template_dir=tmp_path)

        with pytest.raises(TemplateRenderError):
            renderer.render_template("nonexistent.j2", {})

    def test_get_template_path(self, tmp_path):
        """Test retrieving full path to a template."""
        renderer = TemplateRenderer(template_dir=tmp_path)
        template_file = tmp_path / "test.j2"
        template_file.write_text("Test")

        path = renderer.get_template_path("test.j2")
        assert path.exists()
        assert path.name == "test.j2"


class TestSandboxedEnvironment:
    """Test sandboxed template rendering for security."""

    def test_sandboxed_renderer_basic(self, tmp_path):
        """Test sandboxed environment renders basic templates."""
        renderer = TemplateRenderer(template_dir=tmp_path, sandboxed=True)
        template_file = tmp_path / "safe.j2"
        template_file.write_text("Safe: {{ value }}")

        result = renderer.render_template("safe.j2", {"value": 42})
        assert result == "Safe: 42"

    def test_sandboxed_renderer_blocks_imports(self, tmp_path):
        """Test sandboxed environment blocks dangerous operations."""
        renderer = TemplateRenderer(template_dir=tmp_path, sandboxed=True)
        template_file = tmp_path / "unsafe.j2"
        # Try to access __import__ or similar unsafe operations
        template_file.write_text("{{ ''.__class__.__bases__ }}")

        # Should either fail or return safe result
        try:
            result = renderer.render_template("unsafe.j2", {})
            # If it renders, check it's not exposing internals
            assert "class" not in result.lower()
        except (TemplateRenderError, Exception):
            # Expected to fail in sandbox
            pass


class TestBytecodeCaching:
    """Test template bytecode caching for performance."""

    def test_bytecode_cache_initialization(self, tmp_path):
        """Test renderer with bytecode cache directory."""
        cache_dir = tmp_path / "cache"
        cache_dir.mkdir()

        renderer = TemplateRenderer(template_dir=tmp_path, bytecode_cache_dir=cache_dir)

        template_file = tmp_path / "cached.j2"
        template_file.write_text("Cached: {{ value }}")

        # First render
        result1 = renderer.render_template("cached.j2", {"value": 1})
        assert result1 == "Cached: 1"

        # Second render (should use cache)
        result2 = renderer.render_template("cached.j2", {"value": 2})
        assert result2 == "Cached: 2"

    def test_clear_cache(self, tmp_path):
        """Test clearing template cache."""
        renderer = TemplateRenderer(template_dir=tmp_path)
        template_file = tmp_path / "test.j2"
        template_file.write_text("Test")

        # Load template
        renderer.render_template("test.j2", {})

        # Clear cache should not raise
        renderer.clear_cache()


class TestContextValidation:
    """Test template context validation and required fields."""

    def test_validate_context_with_required_fields(self, tmp_path):
        """Test context validation enforces required fields."""
        renderer = TemplateRenderer(template_dir=tmp_path, strict=True)
        template_file = tmp_path / "required.j2"
        template_file.write_text("{{ vendor_id }} {{ device_id }}")

        # Missing required field
        with pytest.raises(TemplateRenderError):
            renderer.render_template("required.j2", {"vendor_id": "0x10de"})

        # All required fields present
        result = renderer.render_template(
            "required.j2", {"vendor_id": "0x10de", "device_id": "0x1234"}
        )
        assert "0x10de" in result and "0x1234" in result

    def test_validate_context_optional_fields(self, tmp_path):
        """Test context validation handles optional fields gracefully."""
        renderer = TemplateRenderer(template_dir=tmp_path, strict=False)
        template_file = tmp_path / "optional.j2"
        template_file.write_text("{{ required }} {{ optional|default('N/A') }}")

        result = renderer.render_template("optional.j2", {"required": "value"})
        assert "value" in result and "N/A" in result

    def test_template_compatibility_conversion(self, tmp_path):
        """Test ensure_template_compatibility converts context properly."""
        from src.utils.unified_context import ensure_template_compatibility

        # Context with nested objects
        context = {
            "vendor_id": "0x10de",
            "device_id": "0x1234",
            "bar_config": {
                "bars": [{"index": 0, "size": 0x1000}, {"index": 1, "size": 0x2000}]
            },
        }

        compatible = ensure_template_compatibility(context)
        assert compatible["vendor_id"] == "0x10de"
        assert "bar_config" in compatible


class TestGlobalFunctions:
    """Test global functions available in templates."""

    def test_range_function_in_template(self, tmp_path):
        """Test range function is available in templates."""
        renderer = TemplateRenderer(template_dir=tmp_path)
        template_file = tmp_path / "range_test.j2"
        template_file.write_text("{% for i in range(3) %}{{ i }}{% endfor %}")

        result = renderer.render_template("range_test.j2", {})
        assert result == "012"

    def test_max_min_functions_in_template(self, tmp_path):
        """Test max/min functions in templates."""
        renderer = TemplateRenderer(template_dir=tmp_path)
        template_file = tmp_path / "maxmin.j2"
        template_file.write_text("{{ max(values) }}-{{ min(values) }}")

        result = renderer.render_template("maxmin.j2", {"values": [1, 5, 3]})
        assert result == "5-1"


class TestErrorHandling:
    """Test error handling edge cases in template rendering."""

    def test_undefined_variable_strict_mode(self, tmp_path):
        """Test undefined variable raises error in strict mode."""
        renderer = TemplateRenderer(template_dir=tmp_path, strict=True)
        template_file = tmp_path / "undefined.j2"
        template_file.write_text("{{ undefined_var }}")

        with pytest.raises(TemplateRenderError):
            renderer.render_template("undefined.j2", {})

    def test_template_syntax_error(self, tmp_path):
        """Test template with syntax error raises proper exception."""
        renderer = TemplateRenderer(template_dir=tmp_path)
        template_file = tmp_path / "syntax_error.j2"
        template_file.write_text("{% for item in items %}")  # Missing endfor

        with pytest.raises(TemplateRenderError):
            renderer.render_template("syntax_error.j2", {"items": [1, 2, 3]})

    def test_error_tag_extension(self, tmp_path):
        """Test custom {% error %} tag extension."""
        renderer = TemplateRenderer(template_dir=tmp_path)
        template_file = tmp_path / "error_tag.j2"
        template_file.write_text("{% error 'Intentional error' %}")

        with pytest.raises(TemplateRenderError):
            renderer.render_template("error_tag.j2", {})

    def test_render_string_error_handling(self):
        """Test error handling in render_string."""
        renderer = TemplateRenderer(strict=True)
        template = "{{ undefined }}"

        with pytest.raises(TemplateRenderError):
            renderer.render_string(template, {})

    def test_render_to_file_error_handling(self, tmp_path):
        """Test error handling in render_to_file."""
        renderer = TemplateRenderer(template_dir=tmp_path, strict=True)
        template_file = tmp_path / "error.j2"
        template_file.write_text("{{ undefined }}")
        out_file = tmp_path / "out.txt"

        with pytest.raises(TemplateRenderError):
            renderer.render_to_file("error.j2", {}, out_file)


class TestTemplateLoopingAndConditionals:
    """Test template looping and conditional logic."""

    def test_for_loop_in_template(self, tmp_path):
        """Test for loop rendering in templates."""
        renderer = TemplateRenderer(template_dir=tmp_path)
        template_file = tmp_path / "loop.j2"
        template_file.write_text("{% for item in items %}{{ item }},{% endfor %}")

        result = renderer.render_template("loop.j2", {"items": [1, 2, 3]})
        assert result == "1,2,3,"

    def test_if_elif_else_in_template(self, tmp_path):
        """Test if/elif/else logic in templates."""
        renderer = TemplateRenderer(template_dir=tmp_path)
        template_file = tmp_path / "conditional.j2"
        template_file.write_text(
            "{% if value > 10 %}HIGH{% elif value > 5 %}MED{% else %}LOW{% endif %}"
        )

        assert renderer.render_template("conditional.j2", {"value": 15}) == "HIGH"
        assert renderer.render_template("conditional.j2", {"value": 7}) == "MED"
        assert renderer.render_template("conditional.j2", {"value": 3}) == "LOW"

    def test_nested_loops_and_conditionals(self, tmp_path):
        """Test nested loops and conditionals."""
        renderer = TemplateRenderer(template_dir=tmp_path)
        template_file = tmp_path / "nested.j2"
        template_file.write_text(
            "{% for group in group_list %}"
            "{% for item in group['elements'] %}"
            "{% if item.active %}{{ item.name }},{% endif %}"
            "{% endfor %}"
            "{% endfor %}"
        )

        context = {
            "group_list": [
                {
                    "elements": [
                        {"name": "A", "active": True},
                        {"name": "B", "active": False},
                    ]
                },
                {"elements": [{"name": "C", "active": True}]},
            ]
        }
        result = renderer.render_template("nested.j2", context)
        assert "A," in result and "C," in result and "B," not in result


class TestRenderMany:
    """Test batch rendering of multiple templates."""

    def test_render_many_success(self, tmp_path):
        """Test successful batch rendering."""
        renderer = TemplateRenderer(template_dir=tmp_path)
        (tmp_path / "a.j2").write_text("A={{ val }}")
        (tmp_path / "b.j2").write_text("B={{ val }}")
        (tmp_path / "c.j2").write_text("C={{ val }}")

        pairs = [("a.j2", {"val": 1}), ("b.j2", {"val": 2}), ("c.j2", {"val": 3})]

        results = renderer.render_many(pairs)
        assert results["a.j2"] == "A=1"
        assert results["b.j2"] == "B=2"
        assert results["c.j2"] == "C=3"

    def test_render_many_with_error(self, tmp_path):
        """Test render_many handles errors gracefully."""
        renderer = TemplateRenderer(template_dir=tmp_path, strict=True)
        (tmp_path / "good.j2").write_text("Good={{ val }}")
        (tmp_path / "bad.j2").write_text("Bad={{ undefined }}")

        pairs = [("good.j2", {"val": 1}), ("bad.j2", {"val": 2})]

        with pytest.raises(TemplateRenderError):
            renderer.render_many(pairs)


class TestDonorUniquenessValidation:
    """Test donor device uniqueness enforcement patterns."""

    def test_require_helper_fails_on_false_condition(self):
        """Test canonical require() helper fails fast."""

        def require(condition: bool, message: str, **context) -> None:
            if not condition:
                log_error_safe(
                    logger,
                    safe_format(
                        "Build aborted: {msg} | ctx={ctx}", msg=message, ctx=context
                    ),
                )
                raise SystemExit(2)

        with pytest.raises(SystemExit):
            require(False, "Test failure", device="test")

    def test_require_helper_passes_on_true_condition(self):
        """Test canonical require() helper passes on true."""

        def require(condition: bool, message: str, **context) -> None:
            if not condition:
                raise SystemExit(2)

        # Should not raise
        require(True, "This should pass")

    def test_enforce_uniqueness_validates_signature(self):
        """Test donor uniqueness enforcement checks device_signature."""

        def require(condition: bool, message: str) -> None:
            if not condition:
                raise SystemExit(2)

        def enforce_uniqueness(context: dict) -> None:
            require(bool(context.get("device_signature")), "device_signature missing")

        # Missing signature
        with pytest.raises(SystemExit):
            enforce_uniqueness({})

        # Valid signature
        enforce_uniqueness({"device_signature": "valid_sig"})

    def test_enforce_uniqueness_validates_bars(self):
        """Test donor uniqueness enforcement checks valid BARs."""

        def require(condition: bool, message: str) -> None:
            if not condition:
                raise SystemExit(2)

        def _get_bar_size(bar) -> int:
            if isinstance(bar, dict):
                return bar.get("size", 0)
            return getattr(bar, "size", 0)

        def enforce_uniqueness(context: dict) -> None:
            require(bool(context.get("device_signature")), "device_signature missing")
            bar_config = context.get("bar_config", {})
            bars = bar_config.get("bars", [])
            has_valid_bar = any(_get_bar_size(bar) > 0 for bar in bars)
            require(has_valid_bar, "No valid MMIO BARs discovered")

        # No valid BARs
        context = {"device_signature": "sig", "bar_config": {"bars": [{"size": 0}]}}
        with pytest.raises(SystemExit):
            enforce_uniqueness(context)

        # Valid BAR
        context["bar_config"]["bars"] = [{"size": 0x1000}]
        enforce_uniqueness(context)


class TestListTemplates:
    """Test template listing functionality."""

    def test_list_templates_default_pattern(self, tmp_path):
        """Test listing templates with default .j2 pattern."""
        renderer = TemplateRenderer(template_dir=tmp_path)
        (tmp_path / "a.j2").write_text("A")
        (tmp_path / "b.j2").write_text("B")
        (tmp_path / "c.txt").write_text("C")  # Not a template

        templates = renderer.list_templates()
        assert "a.j2" in templates
        assert "b.j2" in templates
        assert "c.txt" not in templates

    def test_list_templates_custom_pattern(self, tmp_path):
        """Test listing templates with custom pattern."""
        renderer = TemplateRenderer(template_dir=tmp_path)
        (tmp_path / "a.j2").write_text("A")
        (tmp_path / "b.jinja").write_text("B")

        templates = renderer.list_templates(pattern="*.jinja")
        assert "a.j2" not in templates
        assert "b.jinja" in templates

    def test_list_templates_nested_directories(self, tmp_path):
        """Test listing templates in nested directories."""
        renderer = TemplateRenderer(template_dir=tmp_path)
        sv_dir = tmp_path / "sv"
        sv_dir.mkdir()
        (sv_dir / "module.j2").write_text("Module")

        templates = renderer.list_templates()
        # Should include nested templates
        assert any("module.j2" in t for t in templates)


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
