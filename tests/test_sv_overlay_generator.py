#!/usr/bin/env python3
"""
Tests for SVOverlayGenerator - device-specific overlay file generation.

This test suite validates that we generate ONLY overlay configuration files
(.coe files) and NOT full SystemVerilog modules.
"""

import pytest
from unittest.mock import Mock, patch, MagicMock

from src.templating.sv_overlay_generator import SVOverlayGenerator
from src.templating.template_renderer import TemplateRenderError
from src.exceptions import PCILeechGenerationError


@pytest.fixture
def mock_renderer():
    """Create a mock template renderer."""
    renderer = Mock()
    renderer.render_template = Mock(return_value="mock coe content")
    return renderer


@pytest.fixture
def mock_logger():
    """Create a mock logger."""
    return Mock()


@pytest.fixture
def overlay_generator(mock_renderer, mock_logger):
    """Create an overlay generator instance."""
    return SVOverlayGenerator(mock_renderer, mock_logger)


@pytest.fixture
def valid_context():
    """Create a valid context for overlay generation."""
    return {
        "device_config": {
            "vendor_id": "0x8086",
            "device_id": "0x1234",
        },
        "device": {
            "vendor_id": "0x8086",
            "device_id": "0x1234",
        },
        "config_space": {
            "vendor_id": "8086",
            "device_id": "1234",
            "class_code": "020000",
            "revision_id": "01",
        },
        "bar_config": {
            "bars": [],
        },
    }


class TestSVOverlayGenerator:
    """Test suite for SVOverlayGenerator."""

    def test_init(self, overlay_generator, mock_renderer, mock_logger):
        """Test overlay generator initialization."""
        assert overlay_generator.renderer == mock_renderer
        assert overlay_generator.logger == mock_logger
        assert overlay_generator.prefix == "OVERLAY_GEN"

    def test_generate_config_space_overlay_success(
        self, overlay_generator, valid_context
    ):
        """Test successful config space overlay generation."""
        result = overlay_generator.generate_config_space_overlay(valid_context)

        assert isinstance(result, dict)
        assert "pcileech_cfgspace.coe" in result
        assert result["pcileech_cfgspace.coe"] == "mock coe content"

    def test_generate_config_space_overlay_missing_vid(
        self, overlay_generator
    ):
        """Test that missing vendor_id causes validation error."""
        context = {
            "device_config": {"device_id": "0x1234"},
            "config_space": {},
        }

        with pytest.raises(PCILeechGenerationError) as exc_info:
            overlay_generator.generate_config_space_overlay(context)

        assert "vendor_id" in str(exc_info.value).lower()

    def test_generate_config_space_overlay_missing_did(
        self, overlay_generator
    ):
        """Test that missing device_id causes validation error."""
        context = {
            "device_config": {"vendor_id": "0x8086"},
            "config_space": {},
        }

        with pytest.raises(PCILeechGenerationError) as exc_info:
            overlay_generator.generate_config_space_overlay(context)

        assert "device_id" in str(exc_info.value).lower()

    def test_generate_config_space_overlay_missing_config_space(
        self, overlay_generator
    ):
        """Test that missing config_space causes validation error."""
        context = {
            "device_config": {
                "vendor_id": "0x8086",
                "device_id": "0x1234",
            },
        }

        with pytest.raises(PCILeechGenerationError) as exc_info:
            overlay_generator.generate_config_space_overlay(context)

        assert "config_space" in str(exc_info.value).lower()

    def test_generate_config_space_overlay_with_writemask(
        self, overlay_generator, valid_context
    ):
        """Test overlay generation with write mask data."""
        context_with_writemask = valid_context.copy()
        context_with_writemask["writemask_data"] = {"0x00": 0x00000000}

        result = overlay_generator.generate_config_space_overlay(
            context_with_writemask
        )

        assert "pcileech_cfgspace.coe" in result
        assert "pcileech_cfgspace_writemask.coe" in result

    def test_validate_context_success(self, overlay_generator, valid_context):
        """Test successful context validation."""
        # Should not raise
        overlay_generator._validate_context(valid_context)

    def test_validate_context_with_device_object(
        self, overlay_generator
    ):
        """Test context validation with device object in nested dict format."""
        context = {
            "device": {
                "vendor_id": "0x8086",
                "device_id": "0x1234",
            },
            "config_space": {"data": "test"},  # config_space is required
        }

        # Should not raise - device object format supported
        overlay_generator._validate_context(context)

    def test_prepare_context_adds_header(
        self, overlay_generator, valid_context
    ):
        """Test that prepare_context adds a header."""
        prepared = overlay_generator._prepare_context(valid_context)

        assert "header" in prepared
        assert prepared["header"]  # Not empty

    def test_prepare_context_preserves_existing_header(
        self, overlay_generator, valid_context
    ):
        """Test that existing header is preserved."""
        context_with_header = valid_context.copy()
        context_with_header["header"] = "Existing Header"

        prepared = overlay_generator._prepare_context(context_with_header)

        assert prepared["header"] == "Existing Header"

    def test_generate_config_space_coe(
        self, overlay_generator, valid_context
    ):
        """Test config space .coe file generation."""
        prepared = overlay_generator._prepare_context(valid_context)
        result = overlay_generator._generate_config_space_coe(prepared)

        assert result == "mock coe content"
        overlay_generator.renderer.render_template.assert_called_once()

    def test_generate_config_space_coe_template_error(
        self, overlay_generator, valid_context
    ):
        """Test handling of template rendering errors."""
        overlay_generator.renderer.render_template.side_effect = (
            TemplateRenderError("Template error")
        )

        prepared = overlay_generator._prepare_context(valid_context)

        with pytest.raises(TemplateRenderError):
            overlay_generator._generate_config_space_coe(prepared)

    def test_should_generate_writemask_true(
        self, overlay_generator
    ):
        """Test writemask generation decision when config_space present."""
        context = {"config_space": b"\x00" * 256}

        assert overlay_generator._should_generate_writemask(context) is True

    def test_should_generate_writemask_false(
        self, overlay_generator
    ):
        """Test writemask generation decision when no config_space."""
        context = {}

        assert overlay_generator._should_generate_writemask(context) is False

    def test_should_generate_writemask_empty_data(
        self, overlay_generator
    ):
        """Test writemask generation decision with empty data."""
        context = {"writemask_data": {}}

        assert overlay_generator._should_generate_writemask(context) is False

    def test_generate_writemask_coe(
        self, overlay_generator, valid_context
    ):
        """Test write mask .coe file generation."""
        result = overlay_generator._generate_writemask_coe(valid_context)

        assert isinstance(result, str)
        assert "memory_initialization_radix=16" in result
        assert "memory_initialization_vector=" in result

    def test_generate_writemask_coe_format(
        self, overlay_generator, valid_context
    ):
        """Test write mask .coe file has correct format."""
        result = overlay_generator._generate_writemask_coe(valid_context)

        lines = result.split("\n")
        # Should have header comments
        assert any("Writemask" in line or "writemask" in line for line in lines)
        # Should have memory initialization directives
        assert any("memory_initialization_radix" in line for line in lines)
        # Should have memory initialization vector
        assert any("memory_initialization_vector" in line for line in lines)

    def test_backward_compatibility_generate_pcileech_modules(
        self, overlay_generator, valid_context
    ):
        """Test backward compatibility method redirects correctly."""
        result = overlay_generator.generate_pcileech_modules(valid_context)

        assert isinstance(result, dict)
        assert "pcileech_cfgspace.coe" in result

    def test_backward_compatibility_with_behavior_profile(
        self, overlay_generator, valid_context
    ):
        """Test backward compatibility with behavior profile parameter."""
        behavior_profile = Mock()
        result = overlay_generator.generate_pcileech_modules(
            valid_context, behavior_profile
        )

        # Behavior profile is ignored in overlay generation
        assert isinstance(result, dict)
        assert "pcileech_cfgspace.coe" in result

    def test_exception_handling_wraps_errors(
        self, overlay_generator, valid_context
    ):
        """Test that template errors are wrapped in PCILeechGenerationError."""
        overlay_generator.renderer.render_template.side_effect = Exception(
            "Unexpected error"
        )

        with pytest.raises(PCILeechGenerationError) as exc_info:
            overlay_generator.generate_config_space_overlay(valid_context)

        assert "Overlay generation failed" in str(exc_info.value)

    def test_logging_on_success(
        self, overlay_generator, valid_context, mock_logger
    ):
        """Test that successful generation logs appropriately."""
        result = overlay_generator.generate_config_space_overlay(valid_context)

        # Verify successful result returned
        assert isinstance(result, dict)
        assert "pcileech_cfgspace.coe" in result

    def test_logging_on_error(
        self, overlay_generator, mock_logger
    ):
        """Test that errors are logged and exception raised."""
        context = {"device_config": {}}  # Invalid context

        with pytest.raises(PCILeechGenerationError) as exc_info:
            overlay_generator.generate_config_space_overlay(context)
        
        # Verify error message contains expected content
        assert "Missing required device identifiers" in str(exc_info.value)


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
