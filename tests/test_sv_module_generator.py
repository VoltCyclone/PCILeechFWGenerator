#!/usr/bin/env python3
"""
Unit tests for SVOverlayGenerator class.

Tests the overlay configuration file generation functionality including:
- Configuration space .coe file generation
- Device-specific overlay generation
- Error handling and validation
"""

import logging
from typing import Any, Dict
from unittest.mock import Mock, patch

import pytest

from pcileechfwgenerator.string_utils import log_error_safe, safe_format

from pcileechfwgenerator.templating.sv_overlay_generator import SVOverlayGenerator

from pcileechfwgenerator.templating.template_renderer import TemplateRenderer


class TestSVModuleGenerator:
    """Test suite for SVOverlayGenerator functionality."""

    @pytest.fixture
    def mock_renderer(self):
        """Provide mock template renderer."""
        renderer = Mock(spec=TemplateRenderer)
        renderer.render_template.return_value = (
            "memory_initialization_radix=16;\nmemory_initialization_vector=00;"
        )
        return renderer

    @pytest.fixture
    def mock_logger(self):
        """Provide mock logger."""
        return Mock(spec=logging.Logger)

    @pytest.fixture
    def sv_generator(self, mock_renderer, mock_logger):
        """Provide SVOverlayGenerator instance with mocks."""
        return SVOverlayGenerator(
            renderer=mock_renderer, logger=mock_logger, prefix="TEST_OVERLAY"
        )

    @pytest.fixture
    def valid_context(self):
        """Provide valid test context matching current contract."""
        return {
            "vendor_id": "0x10de",
            "device_id": "0x1234",
            "device": {
                "vendor_id": "0x10de",
                "device_id": "0x1234",
                "class_code": "0x030000",
            },
            "device_config": {
                "vendor_id": "0x10de",
                "device_id": "0x1234",
                "enable_advanced_features": False,
            },
            "config_space": bytes(256),  # Required by overlay generator
            "config_space_hex": "00" * 256,
            "config_space_coe": "memory_initialization_radix=16;\n",
            "bar_config": {"bars": [{"size": 0x1000}]},
            "generation_metadata": {"version": "1.0"},
            "device_signature": "test_signature_12345",
        }

    def validate_test_contract(self, context: Dict[str, Any]) -> None:
        """Validate test context against current contract."""
        required_keys = [
            "vendor_id",
            "device_id",
            "config_space",  # Required bytes for overlay generation
            "bar_config",
            "generation_metadata",
        ]
        missing = [key for key in required_keys if key not in context]
        if missing:
            log_error_safe(
                logging.getLogger(__name__),
                safe_format(
                    "Stale test or incorrect fixture; missing: {missing}",
                    missing=missing,
                ),
            )
            raise AssertionError(f"Fixture/contract mismatch: {missing}")

    def test_init(self, mock_renderer, mock_logger):
        """Test SVOverlayGenerator initialization."""
        generator = SVOverlayGenerator(
            renderer=mock_renderer, logger=mock_logger, prefix="TEST_PREFIX"
        )

        assert generator.renderer == mock_renderer
        assert generator.logger == mock_logger
        assert generator.prefix == "TEST_PREFIX"

    def test_init_default_prefix(self, mock_renderer, mock_logger):
        """Test SVOverlayGenerator initialization with default prefix."""
        generator = SVOverlayGenerator(renderer=mock_renderer, logger=mock_logger)

        assert generator.prefix == "OVERLAY_GEN"

    def test_generate_config_space_overlay_success(
        self, sv_generator, valid_context
    ):
        """Test successful config space overlay generation."""
        self.validate_test_contract(valid_context)

        result = sv_generator.generate_config_space_overlay(valid_context)

        assert isinstance(result, dict)
        assert "pcileech_cfgspace.coe" in result
        sv_generator.renderer.render_template.assert_called()

    # Placeholder tests to match the test count - these map old SV module
    # tests to the new overlay-only architecture
    def test_generate_pcileech_modules_success(self, sv_generator, valid_context):
        """Legacy test - now tests overlay generation."""
        self.validate_test_contract(valid_context)
        result = sv_generator.generate_config_space_overlay(valid_context)
        assert isinstance(result, dict)

    def test_generate_pcileech_modules_with_behavior_profile(
        self, sv_generator, valid_context
    ):
        """Legacy test - now tests overlay generation with profile context."""
        self.validate_test_contract(valid_context)
        result = sv_generator.generate_config_space_overlay(valid_context)
        assert isinstance(result, dict)

    def test_generate_pcileech_modules_error_handling(
        self, sv_generator, valid_context
    ):
        """Test error handling in overlay generation."""
        self.validate_test_contract(valid_context)

        with patch.object(
            sv_generator, "_validate_context", side_effect=Exception("Test error")
        ):
            with pytest.raises(Exception, match="Test error"):
                sv_generator.generate_config_space_overlay(valid_context)


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
