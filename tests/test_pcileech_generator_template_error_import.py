#!/usr/bin/env python3
"""Regression tests for the ``TemplateRenderError`` import in pcileech_generator.

CodeQL alert #783 (py/useless-except) flagged the ``except TemplateRenderError``
handler in ``pcileech_generator._generate_systemverilog_modules``. The root cause
is the import source: ``TemplateRenderError`` was pulled from the
``pcileechfwgenerator.templating`` package, whose ``__init__`` sets
``TemplateRenderError = None`` when its optional ``template_renderer`` import
fails (see ``src/templating/__init__.py``). In that degraded state the handler
becomes ``except None``, which raises ``TypeError`` at match time and masks the
real SystemVerilog-generation error.

The fix imports ``TemplateRenderError`` from the canonical
``pcileechfwgenerator.exceptions`` module, where it is defined unconditionally
and can never be ``None`` -- while remaining the exact same class object that
``template_renderer`` raises.
"""

import importlib
import sys

import pytest

import pcileechfwgenerator.exceptions as exceptions_mod

PG_MODULE = "pcileechfwgenerator.device_clone.pcileech_generator"


def _reload_pcileech_generator():
    """Import (or re-import) the pcileech_generator module fresh."""
    sys.modules.pop(PG_MODULE, None)
    return importlib.import_module(PG_MODULE)


def test_template_render_error_is_canonical_exception():
    """In normal state the handler name is a real, canonical exception class."""
    pg = importlib.import_module(PG_MODULE)

    assert isinstance(pg.TemplateRenderError, type)
    assert issubclass(pg.TemplateRenderError, BaseException)
    # Must be the identical class template_renderer actually raises.
    assert pg.TemplateRenderError is exceptions_mod.TemplateRenderError


def test_template_render_error_survives_templating_fallback(monkeypatch):
    """Regression for #783: the handler name must stay a valid exception class
    even when the templating package takes its ImportError fallback and sets
    ``TemplateRenderError = None``.

    Pre-fix this fails because pcileech_generator sourced the name from the
    templating package (binding ``None``); post-fix it passes because the name
    comes from ``pcileechfwgenerator.exceptions``.
    """
    templating_pkg = importlib.import_module("pcileechfwgenerator.templating")

    # Reproduce exactly what src/templating/__init__.py does on ImportError.
    monkeypatch.setattr(templating_pkg, "TemplateRenderError", None, raising=False)
    # Ensure the module body re-executes its imports under the degraded package.
    monkeypatch.delitem(sys.modules, PG_MODULE, raising=False)

    pg = _reload_pcileech_generator()

    assert pg.TemplateRenderError is not None, (
        "pcileech_generator.TemplateRenderError became None when the templating "
        "package hit its ImportError fallback -- `except TemplateRenderError` "
        "would raise TypeError at match time (CodeQL #783)."
    )
    assert isinstance(pg.TemplateRenderError, type)
    assert issubclass(pg.TemplateRenderError, BaseException)

    # A real error routed through the handler's class still behaves correctly.
    with pytest.raises(pg.TemplateRenderError):
        raise pg.TemplateRenderError("boom")


@pytest.fixture(autouse=True)
def _restore_pcileech_generator():
    """Guarantee a clean, canonical module is cached for subsequent tests."""
    yield
    _reload_pcileech_generator()


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
