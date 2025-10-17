#!/usr/bin/env python3
from src.templating.template_renderer import TemplateRenderer


def test_pcileech_header_template_renders_with_defaults():
    r = TemplateRenderer()
    assert r.template_exists("sv/pcileech_header.svh.j2")
    out = r.render_template("sv/pcileech_header.svh.j2", {})
    assert "`ifndef PCILEECH_HEADER_SVH" in out
    # Default includes are emitted
    assert '`include "tlp_pkg.svh"' in out
    assert '`include "bar_layout_pkg.svh"' in out


def test_pcileech_header_template_custom_includes():
    r = TemplateRenderer()
    custom = ["a.svh", "b/c.svh"]
    out = r.render_template("sv/pcileech_header.svh.j2", {"header_includes": custom})
    for inc in custom:
        assert f'`include "{inc}"' in out
