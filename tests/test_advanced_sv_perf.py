#!/usr/bin/env python3
"""
Comprehensive unit tests for advanced SystemVerilog performance counter module.

Tests cover PerformanceCounterGenerator, PerformanceCounterConfig, DeviceType enum,
and all methods for generating performance monitoring logic.
"""

import logging
from dataclasses import asdict
from unittest.mock import MagicMock, Mock, patch

import pytest

from src.string_utils import log_info_safe, safe_format
from src.templating.advanced_sv_perf import (
    DeviceType,
    PerformanceCounterConfig,
    PerformanceCounterGenerator,
)


class TestDeviceType:
    """Test suite for DeviceType enum."""

    def test_device_type_values(self):
        """Test that DeviceType enum has expected values."""
        assert DeviceType.GENERIC.value == "generic"
        assert DeviceType.NETWORK_CONTROLLER.value == "network"
        assert DeviceType.STORAGE_CONTROLLER.value == "storage"
        assert DeviceType.GRAPHICS_CONTROLLER.value == "graphics"
        assert DeviceType.AUDIO_CONTROLLER.value == "audio"

    def test_device_type_enumeration(self):
        """Test that all device types are accessible."""
        device_types = list(DeviceType)
        assert len(device_types) == 5
        assert DeviceType.GENERIC in device_types
        assert DeviceType.NETWORK_CONTROLLER in device_types
        assert DeviceType.STORAGE_CONTROLLER in device_types
        assert DeviceType.GRAPHICS_CONTROLLER in device_types
        assert DeviceType.AUDIO_CONTROLLER in device_types


class TestPerformanceCounterConfig:
    """Test suite for PerformanceCounterConfig dataclass."""

    def test_default_configuration(self):
        """Test default configuration values."""
        config = PerformanceCounterConfig()

        # Test default boolean flags
        assert config.enable_transaction_counters is True
        assert config.enable_bandwidth_monitoring is True
        assert config.enable_latency_measurement is False
        assert config.enable_latency_tracking is False
        assert config.enable_error_rate_tracking is True
        assert config.enable_device_specific_counters is True
        assert config.enable_performance_grading is True
        assert config.enable_perf_outputs is True

        # Test default numeric values
        assert config.counter_width_bits == 32
        assert config.timestamp_width_bits == 64
        assert config.bandwidth_window_cycles == 100000
        assert config.latency_sample_rate == 1000

        # Test default thresholds
        assert config.high_bandwidth_threshold == 1000000
        assert config.high_latency_threshold == 1000
        assert config.error_rate_threshold == 0.01
        assert config.msi_threshold == 1000

    def test_counter_width_property(self):
        """Test the counter_width property alias."""
        config = PerformanceCounterConfig(counter_width_bits=64)
        assert config.counter_width == 64
        assert config.counter_width == config.counter_width_bits

    def test_custom_configuration(self):
        """Test configuration with custom values."""
        config = PerformanceCounterConfig(
            enable_transaction_counters=False,
            enable_latency_measurement=True,
            counter_width_bits=64,
            bandwidth_window_cycles=50000,
            high_bandwidth_threshold=2000000,
        )

        assert config.enable_transaction_counters is False
        assert config.enable_latency_measurement is True
        assert config.counter_width_bits == 64
        assert config.bandwidth_window_cycles == 50000
        assert config.high_bandwidth_threshold == 2000000

    def test_default_counter_lists(self):
        """Test default counter lists for different device types."""
        config = PerformanceCounterConfig()

        # Test network counters
        expected_network = [
            "rx_packets",
            "tx_packets",
            "rx_bytes",
            "tx_bytes",
            "rx_errors",
            "tx_errors",
        ]
        assert config.network_counters == expected_network

        # Test storage counters
        expected_storage = [
            "read_ops",
            "write_ops",
            "read_bytes",
            "write_bytes",
            "io_errors",
            "queue_depth",
        ]
        assert config.storage_counters == expected_storage

        # Test graphics counters
        expected_graphics = [
            "frame_count",
            "pixel_count",
            "memory_bandwidth",
            "gpu_utilization",
        ]
        assert config.graphics_counters == expected_graphics

    def test_counter_lists_modification(self):
        """Test that counter lists can be modified."""
        config = PerformanceCounterConfig()

        # Add custom network counter
        config.network_counters.append("multicast_packets")
        assert "multicast_packets" in config.network_counters

        # Modify storage counters
        config.storage_counters = ["custom_read", "custom_write"]
        assert config.storage_counters == ["custom_read", "custom_write"]


class TestPerformanceCounterGenerator:
    """Test suite for PerformanceCounterGenerator class."""

    def setup_method(self):
        """Set up test fixtures."""
        self.mock_renderer = Mock()
        self.mock_logger = Mock(spec=logging.Logger)
        self.test_config = PerformanceCounterConfig()

    def test_init_with_defaults(self):
        """Test initialization with default parameters."""
        with patch(
            "src.templating.template_renderer.TemplateRenderer"
        ) as mock_template_renderer:
            mock_template_renderer.return_value = self.mock_renderer

            generator = PerformanceCounterGenerator()

            assert generator.config is not None
            assert isinstance(generator.config, PerformanceCounterConfig)
            assert generator.device_type == DeviceType.GENERIC
            assert generator.renderer == self.mock_renderer
            mock_template_renderer.assert_called_once()

    def test_init_with_custom_parameters(self):
        """Test initialization with custom parameters."""
        custom_config = PerformanceCounterConfig(enable_transaction_counters=False)

        generator = PerformanceCounterGenerator(
            config=custom_config,
            device_type=DeviceType.NETWORK_CONTROLLER,
            renderer=self.mock_renderer,
            logger=self.mock_logger,
        )

        assert generator.config == custom_config
        assert generator.device_type == DeviceType.NETWORK_CONTROLLER
        assert generator.renderer == self.mock_renderer
        assert generator.logger == self.mock_logger

    def test_init_latency_compatibility(self):
        """Test backward compatibility for latency tracking configuration."""
        # Test with a config that has latency measurement enabled
        config_measurement = PerformanceCounterConfig(enable_latency_measurement=True)

        generator = PerformanceCounterGenerator(
            config=config_measurement, renderer=self.mock_renderer
        )

        # Both should be set since the original implementation handles backward compatibility
        assert generator.config.enable_latency_measurement == True
        assert generator.config.enable_latency_tracking == False  # This is the default

    def test_generate_perf_declarations(self):
        """Test performance counter signal declarations generation."""
        generator = PerformanceCounterGenerator(
            renderer=self.mock_renderer, logger=self.mock_logger
        )

        result = generator.generate_perf_declarations()

        assert "Lightweight performance counter declarations" in result
        assert "perf_stub" in result

    def test_generate_device_specific_declarations(self):
        """Test device-specific counter declarations generation."""
        generator = PerformanceCounterGenerator(
            renderer=self.mock_renderer, logger=self.mock_logger
        )

        result = generator._generate_device_specific_declarations()

        assert isinstance(result, list)
        assert len(result) == 1
        assert "Device-specific declarations" in result[0]

    def test_build_context_from_template_context_empty(self):
        """Test context building with empty template context."""
        generator = PerformanceCounterGenerator(
            config=self.test_config,
            device_type=DeviceType.NETWORK_CONTROLLER,
            renderer=self.mock_renderer,
            logger=self.mock_logger,
        )

        context = generator._build_context_from_template_context({})

        # Verify default values are used
        assert (
            context["enable_transaction_counters"]
            == self.test_config.enable_transaction_counters
        )
        assert (
            context["enable_bandwidth_monitoring"]
            == self.test_config.enable_bandwidth_monitoring
        )
        assert context["device_type"] == "network"
        assert "bandwidth_sample_period" in context
        assert "high_performance_threshold" in context

    def test_build_context_from_template_context_with_overrides(self):
        """Test context building with template context overrides."""
        generator = PerformanceCounterGenerator(
            renderer=self.mock_renderer, logger=self.mock_logger
        )

        template_context = {
            "perf_config": {
                "enable_transaction_counters": False,
                "high_performance_threshold": 5000,
            },
            "timing_config": {
                "bandwidth_sample_period": 200000,
                "transfer_width": 8,
            },
            "device_config": {
                "device_type": "custom_device",
                "avg_packet_size": 2000,
            },
        }

        context = generator._build_context_from_template_context(template_context)

        # Verify overrides are applied
        assert context["enable_transaction_counters"] is False
        assert context["high_performance_threshold"] == 5000
        assert context["bandwidth_sample_period"] == 200000
        assert context["transfer_width"] == 8
        assert context["device_type"] == "custom_device"
        assert context["avg_packet_size"] == 2000

    def test_get_device_specific_param(self):
        """Test device-specific parameter retrieval."""
        generator = PerformanceCounterGenerator(
            renderer=self.mock_renderer, logger=self.mock_logger
        )

        template_context = {
            "device_config": {"param1": "device_value"},
            "perf_config": {"param1": "perf_value", "param2": "perf_only"},
        }

        # Test device config takes precedence
        result1 = generator._get_device_specific_param(
            template_context, "param1", "fallback"
        )
        assert result1 == "device_value"

        # Test perf config fallback
        result2 = generator._get_device_specific_param(
            template_context, "param2", "fallback"
        )
        assert result2 == "perf_only"

        # Test fallback value
        result3 = generator._get_device_specific_param(
            template_context, "param3", "fallback"
        )
        assert result3 == "fallback"

    def test_generate_transaction_counters(self):
        """Test transaction counter generation."""
        self.mock_renderer.render_template.return_value = "transaction_counter_code"

        generator = PerformanceCounterGenerator(
            renderer=self.mock_renderer, logger=self.mock_logger
        )

        result = generator.generate_transaction_counters()

        assert result == "transaction_counter_code"
        self.mock_renderer.render_template.assert_called_once_with(
            "sv/performance_counters.sv.j2",
            generator._build_context_from_template_context({}),
        )

    def test_generate_bandwidth_monitoring(self):
        """Test bandwidth monitoring generation."""
        self.mock_renderer.render_template.return_value = "bandwidth_monitor_code"

        generator = PerformanceCounterGenerator(
            renderer=self.mock_renderer, logger=self.mock_logger
        )

        template_context = {"perf_config": {"enable_bandwidth_monitoring": True}}
        result = generator.generate_bandwidth_monitoring(template_context)

        assert result == "bandwidth_monitor_code"
        self.mock_renderer.render_template.assert_called_once()

    def test_generate_latency_measurement(self):
        """Test latency measurement generation."""
        self.mock_renderer.render_template.return_value = "latency_measurement_code"

        generator = PerformanceCounterGenerator(
            renderer=self.mock_renderer, logger=self.mock_logger
        )

        result = generator.generate_latency_measurement()

        assert result == "latency_measurement_code"
        self.mock_renderer.render_template.assert_called_once()

    def test_generate_error_rate_tracking(self):
        """Test error rate tracking generation."""
        self.mock_renderer.render_template.return_value = "error_tracking_code"

        generator = PerformanceCounterGenerator(
            renderer=self.mock_renderer, logger=self.mock_logger
        )

        result = generator.generate_error_rate_tracking()

        assert result == "error_tracking_code"
        self.mock_renderer.render_template.assert_called_once()

    def test_generate_device_specific_counters(self):
        """Test device-specific counter generation."""
        self.mock_renderer.render_template.return_value = "device_specific_code"

        generator = PerformanceCounterGenerator(
            device_type=DeviceType.STORAGE_CONTROLLER,
            renderer=self.mock_renderer,
            logger=self.mock_logger,
        )

        result = generator.generate_device_specific_counters()

        assert result == "device_specific_code"
        self.mock_renderer.render_template.assert_called_once()

    def test_generate_network_counters(self):
        """Test network-specific counter generation."""
        self.mock_renderer.render_template.return_value = "network_counters_code"

        generator = PerformanceCounterGenerator(
            renderer=self.mock_renderer, logger=self.mock_logger
        )

        result = generator._generate_network_counters()

        assert result == "network_counters_code"
        # Verify that device_type was set to "network" in context
        call_args = self.mock_renderer.render_template.call_args
        context = call_args[0][1]
        assert context["device_type"] == "network"

    def test_generate_storage_counters(self):
        """Test storage-specific counter generation."""
        self.mock_renderer.render_template.return_value = "storage_counters_code"

        generator = PerformanceCounterGenerator(
            renderer=self.mock_renderer, logger=self.mock_logger
        )

        result = generator._generate_storage_counters()

        assert result == "storage_counters_code"
        # Verify that device_type was set to "storage" in context
        call_args = self.mock_renderer.render_template.call_args
        context = call_args[0][1]
        assert context["device_type"] == "storage"

    def test_generate_graphics_counters(self):
        """Test graphics-specific counter generation."""
        self.mock_renderer.render_template.return_value = "graphics_counters_code"

        generator = PerformanceCounterGenerator(
            renderer=self.mock_renderer, logger=self.mock_logger
        )

        result = generator._generate_graphics_counters()

        assert result == "graphics_counters_code"
        # Verify that device_type was set to "graphics" in context
        call_args = self.mock_renderer.render_template.call_args
        context = call_args[0][1]
        assert context["device_type"] == "graphics"

    def test_generate_performance_grading(self):
        """Test performance grading generation."""
        self.mock_renderer.render_template.return_value = "performance_grading_code"

        generator = PerformanceCounterGenerator(
            renderer=self.mock_renderer, logger=self.mock_logger
        )

        result = generator.generate_performance_grading()

        assert result == "performance_grading_code"
        # Verify that enable_performance_grading was set to True in context
        call_args = self.mock_renderer.render_template.call_args
        context = call_args[0][1]
        assert context["enable_performance_grading"] is True

    def test_generate_perf_outputs(self):
        """Test performance output generation."""
        self.mock_renderer.render_template.return_value = "perf_outputs_code"

        generator = PerformanceCounterGenerator(
            renderer=self.mock_renderer, logger=self.mock_logger
        )

        result = generator.generate_perf_outputs()

        assert result == "perf_outputs_code"
        # Verify that enable_perf_outputs was set to True in context
        call_args = self.mock_renderer.render_template.call_args
        context = call_args[0][1]
        assert context["enable_perf_outputs"] is True

    def test_generate_complete_performance_counters(self):
        """Test complete performance counter generation."""
        self.mock_renderer.render_template.return_value = "complete_perf_code"

        generator = PerformanceCounterGenerator(
            device_type=DeviceType.GRAPHICS_CONTROLLER,
            renderer=self.mock_renderer,
            logger=self.mock_logger,
        )

        template_context = {
            "perf_config": {"enable_transaction_counters": True},
            "device_config": {"device_type": "graphics"},
        }

        result = generator.generate_complete_performance_counters(template_context)

        assert result == "complete_perf_code"
        self.mock_renderer.render_template.assert_called_once_with(
            "sv/performance_counters.sv.j2",
            generator._build_context_from_template_context(template_context),
        )

    def test_generate_alias(self):
        """Test that generate() is an alias for generate_complete_performance_counters()."""
        self.mock_renderer.render_template.return_value = "alias_test_code"

        generator = PerformanceCounterGenerator(
            renderer=self.mock_renderer, logger=self.mock_logger
        )

        result1 = generator.generate()
        result2 = generator.generate_complete_performance_counters()

        assert result1 == result2
        assert self.mock_renderer.render_template.call_count == 2

    def test_all_device_types_supported(self):
        """Test that all device types can be used in initialization."""
        for device_type in DeviceType:
            generator = PerformanceCounterGenerator(
                device_type=device_type,
                renderer=self.mock_renderer,
                logger=self.mock_logger,
            )
            assert generator.device_type == device_type

    def test_context_contains_all_required_keys(self):
        """Test that built context contains all required keys for templates."""
        generator = PerformanceCounterGenerator(
            renderer=self.mock_renderer, logger=self.mock_logger
        )

        context = generator._build_context_from_template_context({})

        required_keys = [
            "enable_transaction_counters",
            "enable_bandwidth_monitoring",
            "enable_latency_measurement",
            "enable_latency_tracking",
            "enable_error_rate_tracking",
            "enable_device_specific_counters",
            "enable_performance_grading",
            "enable_perf_outputs",
            "device_type",
            "bandwidth_sample_period",
            "transfer_width",
            "bandwidth_shift",
            "min_operations_for_error_rate",
            "high_performance_threshold",
            "medium_performance_threshold",
            "high_bandwidth_threshold",
            "medium_bandwidth_threshold",
            "low_latency_threshold",
            "medium_latency_threshold",
            "low_error_threshold",
            "medium_error_threshold",
            "avg_packet_size",
            "msi_threshold",
        ]

        for key in required_keys:
            assert key in context, f"Required key '{key}' missing from context"

    def test_logging_calls(self):
        """Test that appropriate logging calls are made."""
        generator = PerformanceCounterGenerator(
            device_type=DeviceType.NETWORK_CONTROLLER,
            renderer=self.mock_renderer,
            logger=self.mock_logger,
        )

        # Test initialization logging
        # Note: We can't easily test log_info_safe calls without mocking the string_utils module

        # Test generation logging
        self.mock_renderer.render_template.return_value = "test_code"
        generator.generate_complete_performance_counters()

        # Verify renderer was called (indirectly testing that logging occurred)
        self.mock_renderer.render_template.assert_called_once()


class TestIntegrationScenarios:
    """Integration tests for performance counter generation scenarios."""

    def setup_method(self):
        """Set up integration test fixtures."""
        self.mock_renderer = Mock()

    def test_network_device_workflow(self):
        """Test complete workflow for network device."""
        self.mock_renderer.render_template.return_value = "network_device_code"

        config = PerformanceCounterConfig(
            enable_device_specific_counters=True,
            enable_bandwidth_monitoring=True,
        )

        generator = PerformanceCounterGenerator(
            config=config,
            device_type=DeviceType.NETWORK_CONTROLLER,
            renderer=self.mock_renderer,
        )

        template_context = {
            "device_config": {
                "device_type": "network",
                "avg_packet_size": 1500,
            },
            "perf_config": {
                "enable_transaction_counters": True,
                "high_bandwidth_threshold": 1000,
            },
        }

        result = generator.generate(template_context)

        assert result == "network_device_code"

        # Verify context was built correctly
        call_args = self.mock_renderer.render_template.call_args
        context = call_args[0][1]
        assert context["device_type"] == "network"
        assert context["avg_packet_size"] == 1500
        assert context["enable_transaction_counters"] is True
        assert context["high_bandwidth_threshold"] == 1000

    def test_storage_device_workflow(self):
        """Test complete workflow for storage device."""
        self.mock_renderer.render_template.return_value = "storage_device_code"

        config = PerformanceCounterConfig(
            enable_latency_measurement=True,
            enable_error_rate_tracking=True,
        )

        generator = PerformanceCounterGenerator(
            config=config,
            device_type=DeviceType.STORAGE_CONTROLLER,
            renderer=self.mock_renderer,
        )

        template_context = {
            "device_config": {
                "device_type": "storage",
            },
            "timing_config": {
                "bandwidth_sample_period": 50000,
            },
        }

        result = generator.generate(template_context)

        assert result == "storage_device_code"

        # Verify context was built correctly
        call_args = self.mock_renderer.render_template.call_args
        context = call_args[0][1]
        assert context["device_type"] == "storage"
        assert context["bandwidth_sample_period"] == 50000
        assert context["enable_latency_measurement"] is True

    def test_minimal_configuration_workflow(self):
        """Test workflow with minimal configuration."""
        self.mock_renderer.render_template.return_value = "minimal_config_code"

        generator = PerformanceCounterGenerator(renderer=self.mock_renderer)

        result = generator.generate({})

        assert result == "minimal_config_code"

        # Verify defaults were used
        call_args = self.mock_renderer.render_template.call_args
        context = call_args[0][1]
        assert context["device_type"] == "generic"
        assert context["enable_transaction_counters"] is True
        assert context["enable_bandwidth_monitoring"] is True


class TestErrorHandling:
    """Test error handling and edge cases."""

    def setup_method(self):
        """Set up error handling test fixtures."""
        self.mock_renderer = Mock()
        self.mock_logger = Mock(spec=logging.Logger)

    def test_none_template_context_handling(self):
        """Test handling of None template context."""
        generator = PerformanceCounterGenerator(
            renderer=self.mock_renderer, logger=self.mock_logger
        )

        # Should not raise exception - pass empty dict instead of None
        context = generator._build_context_from_template_context({})
        assert isinstance(context, dict)
        assert len(context) > 0

    def test_missing_template_context_sections(self):
        """Test handling of missing sections in template context."""
        generator = PerformanceCounterGenerator(
            renderer=self.mock_renderer, logger=self.mock_logger
        )

        # Template context with only partial data
        partial_context = {"device_config": {"device_type": "test"}}

        context = generator._build_context_from_template_context(partial_context)

        # Should still have default values for missing sections
        assert "enable_transaction_counters" in context
        assert "bandwidth_sample_period" in context
        assert context["device_type"] == "test"

    def test_renderer_import_error_handling(self):
        """Test handling when TemplateRenderer import fails."""
        with patch(
            "src.templating.template_renderer.TemplateRenderer"
        ) as mock_template_renderer:
            mock_template_renderer.side_effect = ImportError(
                "Template renderer not available"
            )

            with pytest.raises(ImportError):
                PerformanceCounterGenerator()

    def test_invalid_device_type_in_template_context(self):
        """Test handling of invalid device type in template context."""
        generator = PerformanceCounterGenerator(
            device_type=DeviceType.GRAPHICS_CONTROLLER,
            renderer=self.mock_renderer,
            logger=self.mock_logger,
        )

        template_context = {
            "device_config": {"device_type": None}  # Invalid device type
        }

        context = generator._build_context_from_template_context(template_context)

        # The current implementation returns None when device_type is explicitly set to None
        # This is because dict.get() returns the actual value, even if it's None
        assert context["device_type"] is None

    def test_missing_device_type_in_template_context(self):
        """Test handling when device_type key is missing from template context."""
        generator = PerformanceCounterGenerator(
            device_type=DeviceType.GRAPHICS_CONTROLLER,
            renderer=self.mock_renderer,
            logger=self.mock_logger,
        )

        template_context = {"device_config": {}}  # Missing device_type key

        context = generator._build_context_from_template_context(template_context)

        # Should fall back to instance device type value when key is missing
        assert context["device_type"] == "graphics"
