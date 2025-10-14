#!/usr/bin/env python3
"""
Unit tests for template rendering with various data formats.

Tests that templates handle different data structures correctly:
- String values from enum.value conversions
- Dictionary objects with nested value keys
- Objects with .value attributes
"""

import sys
from pathlib import Path

import pytest

# Add src to path for imports
sys.path.insert(0, str(Path(__file__).parent.parent))

from src.templating.template_renderer import TemplateRenderer


class TestTemplateRendering:
    """Test template rendering with various data formats."""

    @pytest.fixture
    def renderer(self):
        """Create a template renderer instance with non-strict validation for testing."""
        # For testing purposes, we use non-strict mode to avoid having to fully initialize
        # every variable in every test. In production code, strict=True would be the default.
        renderer = TemplateRenderer()
        # Monkey patch the _validate_template_context method to use non-strict mode by default
        # For security tests to work properly, we need to keep using the real validation
        # but make sure our test contexts are complete in each test
        # No patching is needed since we'll provide complete contexts
        return renderer

    @pytest.fixture
    def base_context(self):
        """Base context with required fields for templates - fully initialized for security."""
        return {
            "header": "// Test header",
            "device_config": {
                "device_type": "network",  # String value (from enum.value)
                "device_class": "enterprise",  # String value (from enum.value)
                "max_payload_size": 256,
                "msi_vectors": 4,
                "device_bdf": "0000:06:00.0",
                "device_id": "0x1234",
                "vendor_id": "0x10EE",
                "class_code": "0x020000",
                # Required subsystem fields that were missing
                "subsystem_vendor_id": "0xABCD",
                "subsystem_device_id": "0xEF01",
                "revision_id": "0x01",
                "enable_advanced_features": True,
            },
            # Required fields for security validation
            "device_signature": "0xDEADBEEF",
            "board_config": {
                "name": "test_board",
                "fpga_part": "xcku060-ffva1156-2-e",
                "fpga_family": "UltraScale",
            },
            "registers": [],
            "power_management": False,
            "error_handling": False,
            "power_config": {},  # Empty but explicitly provided
            "error_config": {},  # Empty but explicitly provided
            # Add variance_model for templates that check for it
            "variance_model": {},  # Empty dict instead of None to satisfy validator
            "perf_config": {"counter_width": 32},
            "performance_counters": {"counter_width": 32},
            "device_type": "network",
            "device_class": "enterprise",
            "timing_config": {"clock_frequency": 250},
            "msix_config": {
                "is_supported": True,
                "num_vectors": 16,
                "table_offset": 0x2000,
                "table_bir": 0,
                "pba_offset": 0x3000,
                "pba_bir": 0,
            },
            "NUM_MSIX": 16,  # Required for MSIX templates
            "RESET_CLEAR": True,
            "USE_BYTE_ENABLES": True,
        }

    def test_device_specific_ports_with_string_values(self, renderer, base_context):
        """Test device_specific_ports template with string device_type."""
        result = renderer.render_template(
            "sv/components/device_specific_ports.sv.j2", base_context
        )

        # Should render network-specific ports
        assert "Network controller ports" in result or "link_up" in result
        assert (
            base_context["device_config"]["device_type"] in result.lower()
            or "network" in result.lower()
        )

    def test_main_module_with_string_values(self, renderer, base_context):
        """Test main_module template with string device_type and device_class."""
        from src.utils.unified_context import UnifiedContextBuilder

        # Create a complete context using the unified builder
        builder = UnifiedContextBuilder()
        complete_context = builder.create_complete_template_context(
            vendor_id="10EE",
            device_id="1234",
            device_type="network",
            device_class="enterprise",
        )

        # Convert to dict for template rendering
        context_dict = complete_context.to_dict()
        context_dict["error_signals_available"] = False  # Add missing variable
        context_dict["network_signals_available"] = False  # Add missing variable
        # Add other missing performance counter variables
        context_dict.update(
            {
                "enable_transaction_counters": False,
                "enable_bandwidth_monitoring": False,
                "enable_latency_measurement": False,
                "enable_latency_tracking": False,
                "enable_error_rate_tracking": False,
                "storage_signals_available": False,
                "graphics_signals_available": False,
                "generic_signals_available": False,
                "enable_performance_grading": False,
                "enable_perf_outputs": False,
            }
        )

        result = renderer.render_template("sv/main_module.sv.j2", context_dict)

        # Should include device type and class in module name
        assert "pcileech_advanced_network_enterprise" in result
        assert 'DEVICE_TYPE = "network"' in result
        assert 'DEVICE_CLASS = "enterprise"' in result

    def test_main_module_with_optional_performance_counters(
        self, renderer, base_context
    ):
        """Test main_module template with optional performance_counters."""
        from src.utils.unified_context import UnifiedContextBuilder

        # Create a complete context using the unified builder
        builder = UnifiedContextBuilder()
        complete_context = builder.create_complete_template_context(
            vendor_id="10EE",
            device_id="1234",
            enable_transaction_counters=True,  # Enable performance counters
        )

        # Override performance counters specifically
        context_dict = complete_context.to_dict()
        context_dict["performance_counters"] = {"counter_width": 32}
        context_dict["error_signals_available"] = False  # Add missing variable
        context_dict["network_signals_available"] = False  # Add missing variable
        # Add other missing performance counter variables
        context_dict.update(
            {
                "enable_transaction_counters": False,
                "enable_bandwidth_monitoring": False,
                "enable_latency_measurement": False,
                "enable_latency_tracking": False,
                "enable_error_rate_tracking": False,
                "storage_signals_available": False,
                "graphics_signals_available": False,
                "generic_signals_available": False,
                "enable_performance_grading": False,
                "enable_perf_outputs": False,
            }
        )

        result = renderer.render_template("sv/main_module.sv.j2", context_dict)

        # Should use explicitly provided counter width
        assert "COUNTER_WIDTH = 32" in result

    def test_main_module_with_performance_counters(self, renderer, base_context):
        """Test main_module template with performance_counters defined."""
        from src.utils.unified_context import UnifiedContextBuilder

        # Create a complete context using the unified builder
        builder = UnifiedContextBuilder()
        complete_context = builder.create_complete_template_context(
            vendor_id="10EE",
            device_id="1234",
            enable_transaction_counters=True,  # Enable performance counters
        )

        # Override performance counters specifically
        context_dict = complete_context.to_dict()
        context_dict["performance_counters"] = {"counter_width": 64}
        context_dict["error_signals_available"] = False  # Add missing variable
        context_dict["network_signals_available"] = False  # Add missing variable
        # Add other missing performance counter variables
        context_dict.update(
            {
                "enable_transaction_counters": False,
                "enable_bandwidth_monitoring": False,
                "enable_latency_measurement": False,
                "enable_latency_tracking": False,
                "enable_error_rate_tracking": False,
                "storage_signals_available": False,
                "graphics_signals_available": False,
                "generic_signals_available": False,
                "enable_performance_grading": False,
                "enable_perf_outputs": False,
            }
        )

        result = renderer.render_template("sv/main_module.sv.j2", context_dict)

        # Should use specified counter width
        assert "COUNTER_WIDTH = 64" in result

    def test_register_declarations_with_string_values(self, renderer, base_context):
        """Test register_declarations template with string values."""
        result = renderer.render_template(
            "sv/components/register_declarations.sv.j2", base_context
        )

        # Should render without errors
        assert result is not None
        assert len(result) > 0

    def test_template_with_dict_value_keys(self, renderer):
        """Test templates handle dictionaries with nested value keys."""
        context = {
            "header": "// Test header",
            "device_config": {
                "device_type": {"value": "storage", "name": "storage"},
                "device_class": {"value": "industrial", "name": "industrial"},
                "max_payload_size": 512,
                "msi_vectors": 8,
                "device_id": "0x5678",
                "vendor_id": "0x10EE",
                "class_code": "0x010000",
            },
            "registers": [],
        }

        result = renderer.render_template(
            "sv/components/device_specific_ports.sv.j2", context
        )

        # Should extract value from dictionary
        assert "Storage-specific ports" in result or "storage" in result.lower()

    def test_error_recovery_template_with_various_formats(self, renderer):
        """Test error_recovery template handles different error type formats."""
        from src.utils.unified_context import UnifiedContextBuilder

        # Create a complete context using the unified builder
        builder = UnifiedContextBuilder()
        complete_context = builder.create_complete_template_context(
            vendor_id="10ee", device_id="7024"
        )

        # Convert to dict and add specific test data
        context = complete_context.to_dict()
        context.update(
            {
                "config": {
                    "max_retry_count": 3,
                },
                "recoverable_errors": [
                    "ERROR_TIMEOUT",  # String
                    {"value": "ERROR_CRC", "name": "CRC Error"},  # Dict
                ],
                "fatal_errors": [
                    "ERROR_FATAL",
                    {"value": "ERROR_SYSTEM", "name": "System Error"},
                ],
                "error_types": [],
                "error_thresholds": {},  # Add empty error_thresholds
            }
        )

        # Template should handle both string and dict formats
        result = renderer.render_template(
            "sv/error_handling/error_recovery.sv.j2", context
        )
        assert "ERROR_TIMEOUT" in result
        assert "ERROR_CRC" in result

    def test_clock_gating_template_with_state_handling(self, renderer):
        """Test clock_gating template handles state objects correctly."""
        from src.utils.unified_context import UnifiedContextBuilder

        # Create a complete context using the unified builder
        builder = UnifiedContextBuilder()
        complete_context = builder.create_complete_template_context(
            vendor_id="10ee", device_id="7024"
        )

        # Convert to dict and add specific test data
        context = complete_context.to_dict()
        context.update(
            {
                "config": {
                    "enable_clock_gating": True,  # This template expects config.enable_clock_gating
                    "supported_states": [
                        "D0",  # String
                        {"value": "D1", "name": "D1_STATE"},  # Dict
                        {"value": "D3_HOT", "name": "D3_HOT_STATE"},
                    ],
                },
            }
        )

        result = renderer.render_template("sv/clock_gating.sv.j2", context)
        # Should handle all state formats
        assert result is not None

    def test_register_logic_with_value_handling(self, renderer):
        """Test register_logic template handles register values correctly."""
        from src.utils.unified_context import UnifiedContextBuilder

        # Create a complete context using the unified builder
        builder = UnifiedContextBuilder()
        complete_context = builder.create_complete_template_context(
            vendor_id="10ee", device_id="7024"
        )

        # Convert to dict and add specific test data
        context = complete_context.to_dict()
        context.update(
            {
                "registers": [
                    {"name": "control_reg", "value": 0x1234},  # Direct value
                    {
                        "name": "status_reg",
                        "value": {"value": 0x5678, "default": 0},
                    },  # Dict with value
                    {"name": "data_reg", "value": "0xABCD"},  # String value
                ],
                "variance_model": None,
            }
        )

        result = renderer.render_template("sv/components/register_logic.sv.j2", context)
        # Should handle all value formats
        assert "control_reg" in result
        assert "status_reg" in result
        assert "data_reg" in result

    def test_advanced_controller_template(self, renderer, base_context):
        """Test advanced_controller template with complete context."""
        from src.utils.unified_context import UnifiedContextBuilder

        # Create a complete context using the unified builder
        builder = UnifiedContextBuilder()
        complete_context = builder.create_complete_template_context(
            vendor_id="8086",
            device_id="1234",
            device_type="network",
            device_class="enterprise",
        )

        # Convert to dict for template rendering
        context_dict = complete_context.to_dict()

        result = renderer.render_template("sv/advanced_controller.sv.j2", context_dict)

        # Should render module with parameters
        assert "advanced_pcileech_controller" in result
        assert 'DEVICE_TYPE = "network"' in result
        assert 'DEVICE_CLASS = "enterprise"' in result

    def test_template_explicit_variable_initialization(self, renderer, base_context):
        """Test security-first approach requiring explicit variable initialization."""
        from src.utils.unified_context import UnifiedContextBuilder

        # Create a complete context using the unified builder
        builder = UnifiedContextBuilder()
        secure_context = builder.create_complete_template_context(
            vendor_id="8086",
            device_id="1234",
            device_type="network",
            device_class="enterprise",
        )

        # Convert to dict for template rendering
        context_dict = secure_context.to_dict()
        context_dict["error_signals_available"] = False  # Add missing variable
        context_dict["network_signals_available"] = False  # Add missing variable
        # Add other missing performance counter variables
        context_dict.update(
            {
                "enable_transaction_counters": False,
                "enable_bandwidth_monitoring": False,
                "enable_latency_measurement": False,
                "enable_latency_tracking": False,
                "enable_error_rate_tracking": False,
                "storage_signals_available": False,
                "graphics_signals_available": False,
                "generic_signals_available": False,
                "enable_performance_grading": False,
                "enable_perf_outputs": False,
            }
        )

        # With all variables explicitly initialized, rendering should succeed
        result = renderer.render_template("sv/main_module.sv.j2", context_dict)
        assert result is not None
        assert "pcileech_advanced" in result

        # Test that the renderer's strict validation would reject missing variables
        # This test simulates what would happen in the validate_template_context
        with pytest.raises(Exception) as exc_info:
            # Using a minimal context with missing variables should fail
            minimal_context = {
                "header": "// Test header",
                "device_config": base_context["device_config"],
                "registers": [],
                # Missing many required variables
            }

            # Try to render with incomplete context - should fail with security violation
            renderer.render_template("sv/main_module.sv.j2", minimal_context)

        # Should get a clear error message about undefined variable
        assert "performance_counters" in str(exc_info.value)
        assert "undefined" in str(exc_info.value)


class TestTemplateFilters:
    """Test custom template filters."""

    @pytest.fixture
    def renderer(self):
        """Create a template renderer instance for filter tests."""
        # Filter tests don't need the context validation
        return TemplateRenderer()

    def test_sv_hex_filter(self, renderer):
        """Test SystemVerilog hex formatting filter."""
        template_str = "{{ value | sv_hex(32) }}"
        template = renderer.env.from_string(template_str)
        result = template.render(value=0x1234)
        assert result == "32'h00001234"

    def test_sv_hex_filter_with_width(self, renderer):
        """Test SystemVerilog hex formatting with different widths."""
        template_str = "{{ value | sv_hex(16) }}"
        template = renderer.env.from_string(template_str)
        result = template.render(value=0xABCD)
        # The sv_hex filter produces uppercase hex
        assert result == "16'hABCD"

    def test_hex_filter(self, renderer):
        """Test basic hex formatting filter."""
        template_str = "{{ value | hex(8) }}"
        template = renderer.env.from_string(template_str)
        result = template.render(value=255)
        assert result == "000000ff"

    def test_sv_hex_filter_colon_separated_vid_did(self, renderer):
        """sv_hex should accept colon-separated VID:DID strings."""
        template_str = "{{ value | sv_hex(32) }}"
        template = renderer.env.from_string(template_str)
        result = template.render(value="1912:0014")
        assert result == "32'h19120014"

    def test_sv_hex_filter_colon_three_parts_masks(self, renderer):
        """sv_hex should accept VID:DID:RID and mask to width when necessary."""
        template_str = "{{ value | sv_hex(32) }}"
        template = renderer.env.from_string(template_str)
        # 0x8086123415 masked to 32 bits -> 0x86123415
        result = template.render(value="8086:1234:15")
        assert result == "32'h86123415"
