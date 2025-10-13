"""
Tests for the Template Context Validator

This module tests the functionality that ensures all template variables
are properly defined with appropriate defaults.
"""

import os
import tempfile
from pathlib import Path
from typing import Any, Dict

import pytest

from src.templating.template_context_validator import (
    TemplateContextValidator,
    TemplateVariableRequirements,
    analyze_template_variables,
    get_template_requirements,
    validate_template_context,
)


class TestTemplateContextValidator:
    """Test suite for the TemplateContextValidator class."""

    def setup_method(self):
        """Set up test fixtures."""
        self.validator = TemplateContextValidator()

    def test_get_template_requirements_sv_template(self):
        """Test getting requirements for SystemVerilog templates."""
        requirements = self.validator.get_template_requirements(
            "sv/pcileech_fifo.sv.j2"
        )

        # Check that required variables are identified
        assert "device_config" in requirements.required_vars
        assert "board_config" in requirements.required_vars

        # Check that optional variables are identified
        assert "supports_msix" in requirements.optional_vars
        assert "enable_clock_crossing" in requirements.optional_vars

        # Check that defaults are provided
        assert requirements.default_values["supports_msix"] is False
        # device_signature is required, not a default
        assert "device_signature" in requirements.required_vars

    def test_get_template_requirements_tcl_template(self):
        """Test getting requirements for TCL templates."""
        requirements = self.validator.get_template_requirements("tcl/constraints.j2")

        # Check required variables
        assert "board" in requirements.required_vars
        assert "device" in requirements.required_vars

        # Check optional variables
        assert "supports_msix" in requirements.optional_vars
        assert "generated_xdc_path" in requirements.optional_vars

        # Check defaults
        assert requirements.default_values["top_module"] == "pcileech_top"
        assert requirements.default_values["max_lanes"] == 1

    def test_validate_and_complete_context_adds_defaults(self):
        """Test that validation adds default values only in non-strict mode."""
        template_name = "sv/msix_table.sv.j2"
        context = {
            "NUM_MSIX": 32,  # Provide required variable
            # Include required fields for security validation
            "device_config": {
                "vendor_id": "1234",
                "device_id": "5678",
                "subsystem_vendor_id": "ABCD",
                "subsystem_device_id": "EFGH",
                "class_code": "020000",
                "revision_id": "10",
            },
            "board_config": {},
            "device_signature": "0xDEADBEEF",
        }

        # In non-strict mode, defaults should still be added
        validated = self.validator.validate_and_complete_context(
            template_name, context, strict=False
        )

        # Check that defaults were added
        assert validated["NUM_MSIX"] == 32  # Original value preserved
        assert validated["RESET_CLEAR"] is True  # Default added
        assert validated["USE_BYTE_ENABLES"] is True  # Default added
        assert validated["WRITE_PBA_ALLOWED"] is False  # Default added
        assert validated["INIT_TABLE"] is False  # Default added

        # Test that missing NUM_MSIX in strict mode fails
        incomplete_context = context.copy()
        del incomplete_context["NUM_MSIX"]

        with pytest.raises(ValueError) as exc_info:
            self.validator.validate_and_complete_context(
                template_name, incomplete_context, strict=True
            )

        # Error should mention security violation
        assert "SECURITY VIOLATION" in str(exc_info.value)

    def test_validate_and_complete_context_strict_mode(self):
        """Test that strict mode raises security error for missing required variables."""
        template_name = "sv/pcileech_fifo.sv.j2"
        context = {}  # Missing required variables

        with pytest.raises(ValueError) as exc_info:
            self.validator.validate_and_complete_context(
                template_name, context, strict=True
            )

        # Check for security-focused error message
        assert "SECURITY VIOLATION" in str(exc_info.value)
        assert "- Missing required variable 'device_config'" in str(exc_info.value)

    def test_pattern_matching(self):
        """Test that template pattern matching works correctly."""
        # Test wildcard matching
        assert self.validator._matches_pattern("sv/test.sv.j2", "sv/*.sv.j2")
        assert self.validator._matches_pattern(
            "sv/power_management.sv.j2", "sv/power_*.sv.j2"
        )
        assert self.validator._matches_pattern("sv/msix_table.sv.j2", "sv/msix_*.sv.j2")

        # Test non-matching patterns
        assert not self.validator._matches_pattern("tcl/test.j2", "sv/*.sv.j2")
        assert not self.validator._matches_pattern("sv/test.sv.j2", "tcl/*.j2")

    def test_analyze_template_for_variables(self):
        """Test analyzing a template file for variable references."""
        # Create a temporary template file
        with tempfile.NamedTemporaryFile(mode="w", suffix=".j2", delete=False) as f:
            f.write(
                """
            {% if supports_msix %}
            module msix_handler #(
                parameter NUM_VECTORS = {{ NUM_MSIX }}
            );
            {% endif %}
            
            {% for reg in registers %}
            assign {{ reg.name }} = {{ reg.value }};
            {% endfor %}
            
            {% if device_config.vendor_id is defined %}
            localparam VENDOR_ID = {{ device_config.vendor_id }};
            {% endif %}
            """
            )
            temp_path = Path(f.name)

        try:
            variables = self.validator.analyze_template_for_variables(temp_path)

            # Check that all variables were found
            assert "supports_msix" in variables
            assert "NUM_MSIX" in variables
            assert "registers" in variables
            assert "device_config" in variables

        finally:
            # Clean up
            os.unlink(temp_path)

    def test_generate_context_documentation(self):
        """Test generating documentation for template context."""
        doc = self.validator.generate_context_documentation("sv/msix_table.sv.j2")

        # Check that documentation includes required sections
        assert "Template: sv/msix_table.sv.j2" in doc
        assert "Required Variables:" in doc or "Optional Variables:" in doc
        assert "Default Values:" in doc

        # Check that specific variables are documented
        assert "NUM_MSIX" in doc
        assert "RESET_CLEAR" in doc

    def test_template_cache(self):
        """Test that template requirements are cached."""
        template_name = "sv/test.sv.j2"

        # First call should create and cache requirements
        req1 = self.validator.get_template_requirements(template_name)

        # Second call should return cached requirements
        req2 = self.validator.get_template_requirements(template_name)

        # Should be the same object
        assert req1 is req2

    def test_power_management_template_defaults(self):
        """Test power management templates with security validation."""
        template_name = "sv/power_management.sv.j2"

        # Complete context with all required fields
        complete_context = {
            "device_config": {
                "vendor_id": "1234",
                "device_id": "5678",
                "subsystem_vendor_id": "ABCD",
                "subsystem_device_id": "EFGH",
                "class_code": "020000",
                "revision_id": "10",
            },
            "board_config": {},
            "device_signature": "0xDEADBEEF",
        }

        # In non-strict mode, defaults can still be added
        validated = self.validator.validate_and_complete_context(
            template_name, complete_context, strict=False
        )

        # Check power-specific defaults
        assert validated["enable_wake_events"] is False
        assert validated["enable_pme"] is False

        # In strict mode, missing required vars should cause security error
        with pytest.raises(ValueError) as exc_info:
            self.validator.validate_and_complete_context(template_name, {}, strict=True)

        assert "SECURITY VIOLATION" in str(exc_info.value)

    def test_performance_counter_template_defaults(self):
        """Test performance counter templates with security validation."""
        template_name = "sv/performance_counters.sv.j2"

        # In non-strict mode with required vars, defaults can be added
        validated = self.validator.validate_and_complete_context(
            template_name, {"device_config": {}, "board_config": {}}, strict=False
        )

        # Check performance-specific defaults
        assert validated["enable_transaction_counters"] is False
        assert validated["enable_bandwidth_monitoring"] is False
        assert validated["enable_error_rate_tracking"] is False
        assert validated["error_signals_available"] is False

        # In strict mode, this should enforce all variables have values
        with pytest.raises(ValueError) as exc_info:
            self.validator.validate_and_complete_context(template_name, {}, strict=True)

        assert "SECURITY VIOLATION" in str(exc_info.value)

    def test_option_rom_template_defaults(self):
        """Test option ROM templates with security validation."""
        template_name = "sv/option_rom_spi_flash.sv.j2"

        # In non-strict mode with required vars, defaults can be added
        validated = self.validator.validate_and_complete_context(
            template_name, {"device_config": {}, "board_config": {}}, strict=False
        )

        # Check option ROM-specific defaults
        assert validated["USE_QSPI"] is False
        assert validated["ENABLE_CACHE"] is False
        assert validated["SPI_FAST_CMD"] == "0Bh"
        assert validated["FLASH_ADDR_OFFSET"] == "24'h000000"

        # In strict mode, missing required vars should cause security error
        with pytest.raises(ValueError) as exc_info:
            self.validator.validate_and_complete_context(template_name, {}, strict=True)

        assert "SECURITY VIOLATION" in str(exc_info.value)

    def test_global_functions(self):
        """Test the global helper functions with security focus."""
        # Test validate_template_context with strict=False to allow defaults
        context = {
            "NUM_MSIX": 16,
            # Include required fields for security validation
            "device_config": {
                "vendor_id": "1234",
                "device_id": "5678",
                "subsystem_vendor_id": "ABCD",
                "subsystem_device_id": "EFGH",
                "class_code": "020000",
                "revision_id": "10",
            },
            "board_config": {},
            "device_signature": "0xDEADBEEF",
        }
        validated = validate_template_context(
            "sv/msix_table.sv.j2", context, strict=False
        )
        assert validated["RESET_CLEAR"] is True

        # Test get_template_requirements
        requirements = get_template_requirements("sv/pcileech_fifo.sv.j2")
        assert "device_config" in requirements.required_vars

        # Test strict validation failure with global function
        with pytest.raises(ValueError) as exc_info:
            validate_template_context("sv/pcileech_fifo.sv.j2", {}, strict=True)
        assert "SECURITY VIOLATION" in str(exc_info.value)

    def test_context_preservation(self):
        """Test that existing context values are preserved."""
        template_name = "sv/msix_table.sv.j2"
        context = {
            "NUM_MSIX": 64,
            "RESET_CLEAR": False,  # Override default
            "custom_field": "custom_value",  # Extra field
            # Include required fields for security validation
            "device_config": {
                "vendor_id": "1234",
                "device_id": "5678",
                "subsystem_vendor_id": "ABCD",
                "subsystem_device_id": "EFGH",
                "class_code": "020000",
                "revision_id": "10",
            },
            "board_config": {},
            "device_signature": "0xDEADBEEF",
            "USE_BYTE_ENABLES": True,
        }

        validated = self.validator.validate_and_complete_context(
            template_name, context, strict=False
        )

        # Original values should be preserved
        assert validated["NUM_MSIX"] == 64
        assert validated["RESET_CLEAR"] is False
        assert validated["custom_field"] == "custom_value"

        # Explicitly provided values should be preserved
        assert validated["USE_BYTE_ENABLES"] is True

    def test_nested_template_patterns(self):
        """Test that multiple patterns can apply to the same template."""
        template_name = "sv/msix_implementation.sv.j2"
        context = {
            # Include required fields for security validation
            "device_config": {
                "vendor_id": "1234",
                "device_id": "5678",
                "subsystem_vendor_id": "ABCD",
                "subsystem_device_id": "EFGH",
                "class_code": "020000",
                "revision_id": "10",
            },
            "board_config": {},
            "device_signature": "0xDEADBEEF",
            "NUM_MSIX": 16,
        }

        validated = self.validator.validate_and_complete_context(
            template_name, context, strict=False
        )

        # Should get defaults from both sv/*.sv.j2 and sv/msix_*.sv.j2
        assert "device_config" in validated  # From sv/*.sv.j2
        assert "NUM_MSIX" in validated  # From sv/msix_*.sv.j2
        assert validated["supports_msix"] is False  # From sv/*.sv.j2
        assert validated["RESET_CLEAR"] is True  # From sv/msix_*.sv.j2

    def test_none_value_detection(self):
        """Test detection of None values in context variables."""
        template_name = "sv/msix_table.sv.j2"
        context = {
            "NUM_MSIX": None,  # Required variable is None
            "device_config": {},
            "board_config": {},
        }

        # Should fail in strict mode due to None value
        with pytest.raises(ValueError) as exc_info:
            self.validator.validate_and_complete_context(
                template_name, context, strict=True
            )

        assert "SECURITY VIOLATION" in str(exc_info.value)
        assert "Variable 'NUM_MSIX' has None value" in str(exc_info.value)

    def test_strict_mode_comprehensive_validation(self):
        """Test comprehensive validation in strict mode."""
        template_name = "sv/pcileech_fifo.sv.j2"

        # Partial context with missing required vars
        partial_context = {
            "device_config": {},  # Empty but not None
            "supports_msix": None,  # Optional var with None value
        }

        # Should fail in strict mode with detailed error message
        with pytest.raises(ValueError) as exc_info:
            self.validator.validate_and_complete_context(
                template_name, partial_context, strict=True
            )

        error_msg = str(exc_info.value)
        assert "SECURITY VIOLATION" in error_msg
        assert (
            "Explicit initialization of all template variables is required" in error_msg
        )

    def test_tcl_prunes_unused_device_requirement(self):
        """TCL template without 'device' usage should prune 'device' requirement."""
        # Create a temporary TCL template inside the real templates directory so
        # the validator can locate and analyze it.
        templates_dir = Path(__file__).parent.parent / "src" / "templates" / "tcl"
        temp_name = "test_no_device_usage_temp.j2"
        temp_path = templates_dir / temp_name
        try:
            temp_path.write_text(
                """
                # Simple TCL template without device references
                puts "Board name: {{ board.name | default('UNKNOWN') }}"
                {% set local_var = 1 %}
                """.strip()
            )

            context = {"board": {"name": "ACME"}}
            # 'device' intentionally omitted; should not raise due to pruning.
            validated = self.validator.validate_and_complete_context(
                f"tcl/{temp_name}", context, strict=True
            )
            assert "device" not in validated
            # Ensure original board data preserved
            assert validated["board"]["name"] == "ACME"
        finally:
            if temp_path.exists():
                temp_path.unlink()
            # Invalidate any cached requirements for this temp template
            from src.templating.template_context_validator import (
                invalidate_global_template,
            )

            invalidate_global_template(f"tcl/{temp_name}")

    def test_tcl_synthesizes_device_when_missing(self):
        """TCL template referencing 'device' synthesizes minimal object if absent."""
        template_name = "tcl/header.j2"
        context = {
            "board": {"name": "ACME"},
            # Provide device_config so synthesis has source data
            "device_config": {
                "vendor_id": "1234",
                "device_id": "5678",
                "class_code": "0x020000",
                "revision_id": "01",
                "subsys_vendor_id": "ABCD",
                "subsys_device_id": "EFGH",
            },
        }
        validated = self.validator.validate_and_complete_context(
            template_name, context, strict=True
        )
        # Device synthesized
        assert "device" in validated
        dev = validated["device"]
        # Attribute access (TemplateObject) or dict fallback
        vendor = getattr(dev, "vendor_id", None) or dev.get("vendor_id")
        device_id = getattr(dev, "device_id", None) or dev.get("device_id")
        assert vendor == "1234"
        assert device_id == "5678"


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
