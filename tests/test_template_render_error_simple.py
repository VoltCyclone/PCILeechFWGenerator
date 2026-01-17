#!/usr/bin/env python3
"""
Simple replacement tests for template render error functionality.
"""

import pytest


class TestTemplateRenderError:
    """Simple tests for template render error functionality."""

    def test_basic_error_creation(self):
        """Test basic error functionality."""
        from pcileechfwgenerator.templating.template_renderer import TemplateRenderError

        error = TemplateRenderError("Test message")
        assert "Test message" in str(error)

    def test_error_inheritance(self):
        """Test error inheritance."""
        from pcileechfwgenerator.templating.template_renderer import TemplateRenderError

        error = TemplateRenderError("Test")
        assert isinstance(error, Exception)

    def test_error_in_context(self):
        """Test error works in exception context."""
        from pcileechfwgenerator.templating.template_renderer import TemplateRenderError

        with pytest.raises(TemplateRenderError):
            raise TemplateRenderError("Test error")


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
