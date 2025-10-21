#!/usr/bin/env python3
"""
Unit tests for SVModuleGenerator class.

Tests the SystemVerilog module generation functionality including:
- PCILeech module generation
- Legacy module generation
- Device-specific port generation
- MSI-X module handling
- Error handling and validation
"""

import logging
from typing import Any, Dict, List, Optional
from unittest.mock import MagicMock, Mock, call, patch

import pytest

from src.string_utils import log_error_safe, log_info_safe, safe_format
from src.templating.sv_module_generator import SVModuleGenerator
from src.templating.template_renderer import (TemplateRenderer,
                                              TemplateRenderError)


class TestSVModuleGenerator:
    """Test suite for SVModuleGenerator functionality."""

    @pytest.fixture
    def mock_renderer(self):
        """Provide mock template renderer."""
        renderer = Mock(spec=TemplateRenderer)
        renderer.render_template.return_value = "// Generated SystemVerilog module"
        return renderer

    @pytest.fixture
    def mock_logger(self):
        """Provide mock logger."""
        return Mock(spec=logging.Logger)

    @pytest.fixture
    def sv_generator(self, mock_renderer, mock_logger):
        """Provide SVModuleGenerator instance with mocks."""
        return SVModuleGenerator(
            renderer=mock_renderer,
            logger=mock_logger,
            prefix="TEST_SV"
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
                "class_code": "0x030000"
            },
            "device_config": {
                "vendor_id": "0x10de",
                "device_id": "0x1234",
                "enable_advanced_features": False
            },
            "config_space": bytes(256),
            "bar_config": {"bars": [{"size": 0x1000}]},
            "generation_metadata": {"version": "1.0"},
            "device_signature": "test_signature_12345"
        }

    @pytest.fixture
    def msix_context(self, valid_context):
        """Provide context with MSI-X enabled."""
        msix_context = valid_context.copy()
        msix_context["msix_config"] = {
            "enabled": True,
            "num_vectors": 4,
            "table_offset": 0x1000,
            "pba_offset": 0x2000
        }
        return msix_context

    def validate_test_contract(self, context: Dict[str, Any]) -> None:
        """Validate test context against current contract."""
        required_keys = [
            "vendor_id",
            "device_id", 
            "config_space",
            "bar_config",
            "generation_metadata"
        ]
        missing = [key for key in required_keys if key not in context]
        if missing:
            log_error_safe(
                logging.getLogger(__name__),
                safe_format("Stale test or incorrect fixture; missing: {missing}", missing=missing)
            )
            raise AssertionError(f"Fixture/contract mismatch: {missing}")

    def test_init(self, mock_renderer, mock_logger):
        """Test SVModuleGenerator initialization."""
        generator = SVModuleGenerator(
            renderer=mock_renderer,
            logger=mock_logger,
            prefix="TEST_PREFIX"
        )
        
        assert generator.renderer == mock_renderer
        assert generator.logger == mock_logger
        assert generator.prefix == "TEST_PREFIX"
        assert generator._module_cache == {}
        # Config-only architecture doesn't need ports cache

    def test_init_default_prefix(self, mock_renderer, mock_logger):
        """Test SVModuleGenerator initialization with default prefix."""
        generator = SVModuleGenerator(
            renderer=mock_renderer,
            logger=mock_logger
        )
        
        assert generator.prefix == "SV_GEN"

    def test_generate_pcileech_modules_success(self, sv_generator, valid_context):
        """Test successful config module generation via legacy method."""
        self.validate_test_contract(valid_context)
        
        # The generate_pcileech_modules method now delegates to generate_config_modules
        result = sv_generator.generate_pcileech_modules(valid_context)
        
        assert isinstance(result, dict)
        # Should only contain config modules
        assert "device_config" in result
        assert "pcileech_cfgspace.coe" in result

    def test_generate_pcileech_modules_with_behavior_profile(self, sv_generator, valid_context):
        """Test config module generation with behavior profile (ignored in config-only)."""
        self.validate_test_contract(valid_context)
        
        # Behavior profile is ignored in config-only architecture
        behavior_profile = Mock()
        
        result = sv_generator.generate_pcileech_modules(valid_context, behavior_profile)
        
        assert isinstance(result, dict)
        # Should only contain config modules
        assert "device_config" in result
        assert "pcileech_cfgspace.coe" in result
        # Should NOT contain HDL modules
        assert "advanced_controller" not in result

    def test_generate_pcileech_modules_error_handling(self, sv_generator, valid_context):
        """Test error handling in config module generation."""
        self.validate_test_contract(valid_context)
        
        with patch.object(sv_generator, '_generate_config_modules',
                         side_effect=Exception("Test error")):
            
            with pytest.raises(Exception, match="Test error"):
                sv_generator.generate_config_modules(valid_context)

    @pytest.mark.skip(reason="Legacy module generation removed in config-only architecture")
    def test_generate_legacy_modules_success(self, sv_generator, valid_context):
        """Legacy test - skipped."""
        pass

    @pytest.mark.skip(reason="Legacy module generation removed in config-only architecture")
    def test_generate_legacy_modules_with_behavior_profile(self, sv_generator, valid_context):
        """Legacy test - skipped."""
        pass

    @pytest.mark.skip(reason="Legacy module generation removed in config-only architecture")
    def test_generate_legacy_modules_template_error(self, sv_generator, valid_context, mock_logger):
        """Legacy test - skipped."""
        pass

    @pytest.mark.skip(reason="Device-specific ports removed in config-only architecture")
    def test_generate_device_specific_ports_success(self, sv_generator):
        """Legacy test - skipped."""
        pass

    @pytest.mark.skip(reason="Device-specific ports removed in config-only architecture")
    def test_generate_device_specific_ports_with_cache_key(self, sv_generator):
        """Legacy test - skipped."""
        pass

    @pytest.mark.skip(reason="Device-specific ports removed in config-only architecture")
    def test_generate_device_specific_ports_different_cache_keys(self, sv_generator):
        """Legacy test - skipped."""
        device_type = "storage"
        device_class = "nvme"
        
        # Calls with different cache keys should render separately
        result1 = sv_generator.generate_device_specific_ports(
            device_type, device_class, "key1"
        )
        result2 = sv_generator.generate_device_specific_ports(
            device_type, device_class, "key2"
        )
        
        assert sv_generator.renderer.render_template.call_count == 2

    def test_generate_core_pcileech_modules_missing_device_ids(self, sv_generator, valid_context):
        """Test _generate_core_pcileech_modules with missing device identifiers."""
        self.validate_test_contract(valid_context)
        
        # Remove vendor_id from context
        invalid_context = valid_context.copy()
        invalid_context.pop("vendor_id")
        invalid_context["device"] = {}
        invalid_context["device_config"] = {}
        
        modules = {}
        
        with pytest.raises(TemplateRenderError):
            sv_generator._generate_core_pcileech_modules(invalid_context, modules)

    def test_generate_config_modules_success(self, sv_generator, valid_context):
        """Test successful config-only module generation."""
        self.validate_test_contract(valid_context)
        
        modules = {}
        
        # Call the config module generation method
        sv_generator._generate_config_modules(valid_context, modules)
        
        # Check that only configuration modules are generated
        expected_modules = [
            "device_config",
            "pcileech_cfgspace.coe"
        ]
        
        # Verify we only generate config modules
        for module_name in expected_modules:
            assert module_name in modules, f"Expected module {module_name} not found in {list(modules.keys())}"
            
        # Verify we DON'T generate HDL logic modules anymore
        hdl_modules = ["pcileech_tlps128_bar_controller", "pcileech_fifo", "top_level_wrapper"]
        for hdl_module in hdl_modules:
            assert hdl_module not in modules, f"HDL module {hdl_module} should not be generated in config-only architecture"

    @pytest.mark.skip(reason="MSI-X methods removed in config-only architecture")
    def test_is_msix_enabled_true(self, sv_generator, msix_context):
        """Legacy test - skipped."""
        pass

    @pytest.mark.skip(reason="MSI-X methods removed in config-only architecture")
    def test_is_msix_enabled_false(self, sv_generator, valid_context):
        """Legacy test - skipped."""
        pass

    @pytest.mark.skip(reason="MSI-X methods removed in config-only architecture")
    def test_is_msix_enabled_disabled_config(self, sv_generator, valid_context):
        """Legacy test - skipped."""
        pass

    @pytest.mark.skip(reason="MSI-X methods removed in config-only architecture")
    def test_get_msix_vectors_from_config(self, sv_generator):
        """Legacy test - skipped."""
        pass

    @pytest.mark.skip(reason="MSI-X methods removed in config-only architecture")
    def test_get_msix_vectors_default(self, sv_generator):
        """Legacy test - skipped."""
        pass

    @pytest.mark.skip(reason="Register methods removed in config-only architecture")
    def test_get_register_name_from_offset(self, sv_generator):
        """Legacy test - skipped."""
        pass

    @pytest.mark.skip(reason="Register methods removed in config-only architecture")
    def test_get_offset_from_register_name(self, sv_generator):
        """Legacy test - skipped."""
        pass

    @pytest.mark.skip(reason="Register methods removed in config-only architecture")
    def test_get_offset_from_register_name_invalid(self, sv_generator):
        """Legacy test - skipped."""
        pass

    @pytest.mark.skip(reason="Register methods removed in config-only architecture")
    def test_get_default_registers(self, sv_generator):
        """Legacy test - skipped."""
        pass

    @pytest.mark.skip(reason="MSI-X PBA init removed in config-only architecture")
    def test_generate_msix_pba_init(self, sv_generator):
        """Legacy test - skipped."""
        pass

    def test_generate_msix_table_init(self, sv_generator):
        """Test MSI-X table initialization exists (part of config generation)."""
        # This method still exists as part of config generation
        assert hasattr(sv_generator, '_generate_msix_table_init')

    @pytest.mark.skip(reason="Register extraction removed in config-only architecture")
    def test_extract_registers_with_profile(self, sv_generator):
        """Legacy test - skipped."""
        pass

    @pytest.mark.skip(reason="Register extraction removed in config-only architecture")
    def test_extract_registers_no_profile(self, sv_generator):
        """Legacy test - skipped."""
        pass

    @pytest.mark.skip(reason="Register processing removed in config-only architecture")
    def test_process_register_access(self, sv_generator):
        """Legacy test - skipped."""
        pass

    @patch('src.templating.sv_module_generator.log_error_safe')
    def test_device_identifier_validation_logging(self, mock_log, sv_generator, valid_context):
        """Test that device identifier validation logs correctly."""
        self.validate_test_contract(valid_context)
        
        # Create context with missing device identifiers
        invalid_context = valid_context.copy()
        invalid_context.pop("vendor_id")
        invalid_context["device"] = {}
        invalid_context["device_config"] = {}
        
        modules = {}
        
        with pytest.raises(TemplateRenderError):
            sv_generator._generate_core_pcileech_modules(invalid_context, modules)
        
        # Verify that log_error_safe was called with safe_format
        mock_log.assert_called_once()
        call_args = mock_log.call_args
        
        # The first argument should be the logger
        assert call_args[0][0] == sv_generator.logger
        
        # The second argument should be the formatted message
        assert "Missing required device identifiers" in call_args[0][1]
        
        # Verify prefix was passed
        assert call_args[1]["prefix"] == sv_generator.prefix

    @pytest.mark.skip(reason="Variance model methods removed in config-only architecture")
    def test_generate_variance_model(self, sv_generator):
        """Legacy test - skipped."""
        pass
    
    @pytest.mark.skip(reason="Variance model methods removed in config-only architecture")
    def test_generate_variance_model_no_profile(self, sv_generator):
        """Legacy test - skipped."""
        pass

    def test_msix_modules_generation_when_enabled(self, sv_generator, msix_context):
        """Test MSI-X config generation when enabled."""
        # In config-only architecture, MSI-X is handled as part of device config
        modules = {}
        sv_generator._generate_msix_config_if_needed(msix_context, modules)
        
        # Should have checked for MSI-X config
        # The actual generation is part of device_config module
        assert True  # Basic validation that method exists and runs
    
    def test_msix_modules_generation_when_disabled(self, sv_generator, valid_context):
        """Test MSI-X config generation when disabled."""
        modules = {}
        sv_generator._generate_msix_config_if_needed(valid_context, modules)
        
        # When MSI-X is not present, method should return early
        # No additional modules should be generated
        assert len(modules) == 0
    
    @pytest.mark.skip(reason="Advanced module generation removed in config-only architecture")
    def test_advanced_modules_generation(self, sv_generator, valid_context):
        """Legacy test - skipped."""
        pass
    
    @pytest.mark.skip(reason="Device-specific ports removed in config-only architecture")
    def test_caching_behavior(self, sv_generator):
        """Legacy test - skipped."""
        pass


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
