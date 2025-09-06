#!/usr/bin/env python3
"""
Tests for the improved SystemVerilog generator.

This test suite validates the modular SystemVerilog generator implementation.
"""

from pathlib import Path
from unittest.mock import MagicMock, Mock, patch

import pytest

from src.device_clone.device_config import DeviceClass, DeviceType
from src.templating.sv_constants import SV_VALIDATION
from src.templating.systemverilog_generator import (AdvancedSVGenerator,
                                                    DeviceSpecificLogic,
                                                    ErrorHandlingConfig,
                                                    PerformanceConfig,
                                                    PowerManagementConfig,
                                                    SystemVerilogGenerator,
                                                    TemplateRenderError)


class TestSystemVerilogGenerator:
    """Test the main SystemVerilog generator."""

    def test_initialization_with_defaults(self):
        """Test generator initialization with default configurations."""
        generator = SystemVerilogGenerator()

        assert generator.power_config is not None
        assert generator.error_config is not None
        assert generator.perf_config is not None
        assert generator.device_config is not None
        assert generator.use_pcileech_primary is True

    def test_initialization_with_custom_configs(self):
        """Test generator initialization with custom configurations."""
        device_config = DeviceSpecificLogic(
            device_type=DeviceType.NETWORK,
            device_class=DeviceClass.ENTERPRISE,
            max_payload_size=512,
        )

        generator = SystemVerilogGenerator(
            device_config=device_config, use_pcileech_primary=False
        )

        assert generator.device_config.device_type == DeviceType.NETWORK
        assert generator.device_config.max_payload_size == 512
        assert generator.use_pcileech_primary is False

    def test_backward_compatibility_alias(self):
        """Test that AdvancedSVGenerator alias works."""
        generator = AdvancedSVGenerator()
        assert isinstance(generator, SystemVerilogGenerator)

    def test_generate_modules_validation_error(self):
        """Test that invalid context raises validation error."""
        generator = SystemVerilogGenerator()

        # Missing device_signature (but valid device_config to pass device identification validation)
        invalid_context = {
            "device_config": {
                "vendor_id": "8086",
                "device_id": "1533",
                # Provide required donor identity fields so the error
                # surfaces for device_signature
                "subsystem_vendor_id": "0000",
                "subsystem_device_id": "0000",
                "class_code": "020000",
                "revision_id": "00",
            }
            # Missing device_signature
        }

        with pytest.raises(TemplateRenderError) as exc_info:
            generator.generate_modules(invalid_context)

        assert "device_signature" in str(exc_info.value)

    def test_generate_modules_with_valid_context(self):
        """Test module generation with valid context."""
        generator = SystemVerilogGenerator()

        valid_context = {
            "device_signature": "32'h12345678",
            "device_config": {
                "vendor_id": "10EC",
                "device_id": "8168",
                "subsystem_vendor_id": "1043",
                "subsystem_device_id": "8554",
                "class_code": "020000",
                "revision_id": "15",
            },
            "bar_config": {"bars": []},
            "generation_metadata": {},
        }

        # Mock the module generator to avoid actual template rendering
        with patch.object(
            generator.module_generator, "generate_pcileech_modules"
        ) as mock_gen:
            mock_gen.return_value = {"test_module": "module content"}

            modules = generator.generate_modules(valid_context)

            assert "test_module" in modules
            mock_gen.assert_called_once()

    def test_legacy_method_compatibility(self):
        """Test backward compatibility with legacy method names."""
        generator = SystemVerilogGenerator()

        valid_context = {
            "device_signature": "32'hDEADBEEF",
            "device_config": {
                "vendor_id": "8086",
                "device_id": "1234",
                "subsystem_vendor_id": "0000",
                "subsystem_device_id": "0000",
                "class_code": "030000",
                "revision_id": "00",
            },
            "bar_config": {"bars": []},
            "generation_metadata": {},
        }

        with patch.object(
            generator.module_generator, "generate_pcileech_modules"
        ) as mock_gen:
            mock_gen.return_value = {"module": "content"}

            # Test legacy method name
            modules = generator.generate_systemverilog_modules(valid_context)
            assert modules == {"module": "content"}

    def test_device_specific_ports_generation(self):
        """Test device-specific ports generation."""
        device_config = DeviceSpecificLogic(
            device_type=DeviceType.NETWORK,
            device_class=DeviceClass.ENTERPRISE,
        )

        generator = SystemVerilogGenerator(device_config=device_config)

        with patch.object(
            generator.module_generator, "generate_device_specific_ports"
        ) as mock_gen:
            mock_gen.return_value = "// Device ports"

            ports = generator.generate_device_specific_ports()

            mock_gen.assert_called_with(
                DeviceType.NETWORK.value, DeviceClass.ENTERPRISE.value, ""
            )

    def test_cache_clearing(self):
        """Test cache clearing functionality."""
        generator = SystemVerilogGenerator()

        # Just verify the method exists and can be called without error
        generator.clear_cache()
        # The actual cache clearing is an implementation detail
        # that doesn't need to be tested at this level


class TestDeviceSpecificLogic:
    """Test the DeviceSpecificLogic configuration class."""

    def test_default_values(self):
        """Test default configuration values."""
        config = DeviceSpecificLogic()

        assert config.device_type == DeviceType.GENERIC
        assert config.device_class == DeviceClass.CONSUMER
        assert config.max_payload_size == 256
        assert config.enable_dma is False

    def test_validation(self):
        """Test configuration validation."""
        # Valid configuration
        config = DeviceSpecificLogic(max_payload_size=512)
        config.validate()  # Should not raise

        # Invalid configuration
        config = DeviceSpecificLogic(max_payload_size=-1)
        with pytest.raises(ValueError) as exc_info:
            config.validate()
        assert "max_payload_size" in str(exc_info.value)


class TestValidation:
    """Test validation functionality."""

    def test_error_messages_available(self):
        """Test that error messages are properly defined."""
        messages = SV_VALIDATION.ERROR_MESSAGES

        assert "missing_device_signature" in messages
        assert "invalid_device_type" in messages
        assert "validation_failed" in messages

    def test_missing_device_signature_error(self):
        """Test specific error for missing device signature."""
        generator = SystemVerilogGenerator()

        context = {
            "device_config": {
                "vendor_id": "1234",
                "device_id": "5678",
                # Provide required donor identity fields so the error
                # surfaces for device_signature
                "subsystem_vendor_id": "0000",
                "subsystem_device_id": "0000",
                "class_code": "020000",
                "revision_id": "00",
            }
            # Missing device_signature
        }

        with pytest.raises(TemplateRenderError) as exc_info:
            generator.generate_modules(context)

        error_msg = str(exc_info.value)
        assert "device_signature" in error_msg
        assert "required" in error_msg.lower()


class TestIntegration:
    """Integration tests for the complete system."""

    def test_full_generation_flow(self):
        """Test the complete generation flow with all components."""
        # Create custom configurations
        device_config = DeviceSpecificLogic(
            device_type=DeviceType.NETWORK,
            device_class=DeviceClass.ENTERPRISE,
            max_payload_size=512,
            msix_vectors=8,
            enable_dma=True,
        )

        power_config = PowerManagementConfig(
            enable_pme=True,
            enable_wake_events=True,
        )

        error_config = ErrorHandlingConfig(
            enable_error_detection=True,
            enable_error_logging=True,
        )

        perf_config = PerformanceConfig(
            enable_performance_counters=True,
        )

        # Create generator
        generator = SystemVerilogGenerator(
            device_config=device_config,
            power_config=power_config,
            error_config=error_config,
            perf_config=perf_config,
        )

        # Create valid context
        context = {
            "device_signature": "32'h12345678",
            "device_config": {
                "vendor_id": "10EC",
                "device_id": "8168",
                "subsystem_vendor_id": "1043",
                "subsystem_device_id": "8554",
                "class_code": "020000",
                "revision_id": "15",
            },
            "msix_config": {
                "is_supported": True,
                "num_vectors": 8,
                "table_bir": 2,
                "table_offset": 0x1000,
            },
            "bar_config": {
                "bars": [
                    {"index": 0, "size": 0x100},
                    {"index": 2, "size": 0x4000},
                ]
            },
            "generation_metadata": {
                "timestamp": "2024-01-15T10:30:00Z",
                "version": "1.0.0",
            },
        }

        # Mock template rendering to avoid file system dependencies
        with patch.object(generator.renderer, "render_template") as mock_render:
            mock_render.return_value = "// Generated module content"

            modules = generator.generate_modules(context)

            # Verify modules were generated
            assert isinstance(modules, dict)
            assert len(modules) > 0

            # Verify render was called
            assert mock_render.call_count > 0


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
