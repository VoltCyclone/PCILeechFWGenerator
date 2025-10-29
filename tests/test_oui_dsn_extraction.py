#!/usr/bin/env python3
"""
Unit tests for OUI extraction and DSN semantic decomposition.

Tests verify that:
1. OUI is correctly extracted from vendor IDs
2. DSN is properly decomposed into semantic components
3. Template context contains all required fields for SystemVerilog defines
"""


import logging

import sys

from pathlib import Path

from typing import Any, Dict

import pytest

# Add src to path for imports
sys.path.insert(0, str(Path(__file__).parent.parent))

from src.templating.sv_context_builder import SVContextBuilder



class TestOUIExtraction:
    """Test suite for OUI extraction from vendor IDs."""

    @pytest.fixture
    def context_builder(self):
        """Provide SVContextBuilder instance."""
        logger = logging.getLogger("test_oui")
        return SVContextBuilder(logger)

    def test_oui_extraction_from_vendor_id(self, context_builder):
        """Test OUI extraction from vendor ID."""
        context = {}
        vendor_id_int = 0x10DE  # NVIDIA

        context_builder._add_vendor_oui(context, vendor_id_int)

        assert "vendor_oui" in context
        assert context["vendor_oui"] == 0x10DE
        assert "vendor_oui_hex" in context
        assert context["vendor_oui_hex"] == "0x0010DE"
        assert "pci_exp_ep_oui" in context
        assert context["pci_exp_ep_oui"] == 0x10DE

    def test_oui_extraction_24bit_mask(self, context_builder):
        """Test OUI is properly masked to 24 bits."""
        context = {}
        vendor_id_int = 0xFF10DE  # Value with upper bits set

        context_builder._add_vendor_oui(context, vendor_id_int)

        # Should mask to 24 bits (0xFFFFFF & 0xFF10DE = 0xFF10DE)
        assert context["vendor_oui"] == 0xFF10DE
        assert context["vendor_oui_hex"] == "0xFF10DE"

    def test_oui_extraction_zero(self, context_builder):
        """Test OUI extraction with zero vendor ID."""
        context = {}
        vendor_id_int = 0x0000

        context_builder._add_vendor_oui(context, vendor_id_int)

        assert context["vendor_oui"] == 0x0000
        assert context["vendor_oui_hex"] == "0x000000"

    def test_oui_extraction_max_value(self, context_builder):
        """Test OUI extraction with maximum 24-bit value."""
        context = {}
        vendor_id_int = 0xFFFFFF

        context_builder._add_vendor_oui(context, vendor_id_int)

        assert context["vendor_oui"] == 0xFFFFFF
        assert context["vendor_oui_hex"] == "0xFFFFFF"


class TestDSNSemanticDecomposition:
    """Test suite for DSN semantic decomposition."""

    @pytest.fixture
    def context_builder(self):
        """Provide SVContextBuilder instance."""
        logger = logging.getLogger("test_dsn")
        return SVContextBuilder(logger)

    def test_dsn_decomposition_basic(self, context_builder):
        """Test basic DSN decomposition into upper/lower 32-bit parts."""
        context = {}
        # Example DSN: upper 32 bits = 0x00000001, lower 32 bits = 0x01000A35
        dsn_value = 0x0000000101000A35

        context_builder._add_dsn_semantic_fields(context, dsn_value)

        assert context["dsn_upper_32"] == 0x00000001
        assert context["dsn_lower_32"] == 0x01000A35
        assert context["pci_exp_ep_dsn_2"] == 0x00000001
        assert context["pci_exp_ep_dsn_1"] == 0x01000A35

    def test_dsn_oui_extraction(self, context_builder):
        """Test OUI extraction from DSN lower 32 bits."""
        context = {}
        # DSN with OUI 0x000A35 in lower bits
        dsn_value = 0x0000000101000A35

        context_builder._add_dsn_semantic_fields(context, dsn_value)

        assert context["dsn_oui"] == 0x000A35
        assert context["dsn_oui_hex"] == "0x000A35"

    def test_dsn_extension_extraction(self, context_builder):
        """Test extension byte extraction from DSN lower 32 bits."""
        context = {}
        # DSN with extension byte 0x01 in bits [31:24] of lower 32 bits
        dsn_value = 0x0000000101000A35

        context_builder._add_dsn_semantic_fields(context, dsn_value)

        assert context["dsn_extension"] == 0x01
        assert context["dsn_extension_hex"] == "0x01"

    def test_dsn_hex_format_strings(self, context_builder):
        """Test DSN hex format strings for SystemVerilog defines."""
        context = {}
        dsn_value = 0x123456789ABCDEF0

        context_builder._add_dsn_semantic_fields(context, dsn_value)

        # Check SystemVerilog format strings
        assert context["pci_exp_ep_dsn_2_hex"] == "32'h12345678"
        assert context["pci_exp_ep_dsn_1_hex"] == "32'h9ABCDEF0"

    def test_dsn_zero_value(self, context_builder):
        """Test DSN decomposition with zero value."""
        context = {}
        dsn_value = 0x0000000000000000

        context_builder._add_dsn_semantic_fields(context, dsn_value)

        assert context["dsn_upper_32"] == 0x00000000
        assert context["dsn_lower_32"] == 0x00000000
        assert context["dsn_oui"] == 0x000000
        assert context["dsn_extension"] == 0x00

    def test_dsn_max_value(self, context_builder):
        """Test DSN decomposition with maximum value."""
        context = {}
        dsn_value = 0xFFFFFFFFFFFFFFFF

        context_builder._add_dsn_semantic_fields(context, dsn_value)

        assert context["dsn_upper_32"] == 0xFFFFFFFF
        assert context["dsn_lower_32"] == 0xFFFFFFFF
        assert context["dsn_oui"] == 0xFFFFFF
        assert context["dsn_extension"] == 0xFF


class TestIntegratedContextBuilding:
    """Test integrated context building with OUI and DSN fields."""

    @pytest.fixture
    def context_builder(self):
        """Provide SVContextBuilder instance."""
        logger = logging.getLogger("test_integrated")
        return SVContextBuilder(logger)

    @pytest.fixture
    def mock_template_context(self) -> Dict[str, Any]:
        """Provide mock template context with device data."""
        return {
            "device_config": {
                "vendor_id": "0x10DE",
                "device_id": "0x1234",
            },
            "device_serial_number": 0x0000000101000A35,
            "device_signature": "10de:1234",  # Required field
            "generation_metadata": {
                "version": "1.0",
            },
        }

    def test_full_context_with_oui_and_dsn(
        self, context_builder, mock_template_context
    ):
        """Test that full context contains OUI and DSN fields."""
        # Mock the power/error/perf configs
        mock_power_config = type("obj", (object,), {})()
        mock_error_config = type("obj", (object,), {})()
        mock_perf_config = type("obj", (object,), {})()
        mock_device_config = type("obj", (object,), {})()

        # Build context
        context = context_builder.build_enhanced_context(
            mock_template_context,
            mock_power_config,
            mock_error_config,
            mock_perf_config,
            mock_device_config,
        )

        # Verify OUI fields
        assert "vendor_oui" in context
        assert "vendor_oui_hex" in context
        assert "pci_exp_ep_oui" in context

        # Verify DSN semantic fields
        assert "dsn_upper_32" in context
        assert "dsn_lower_32" in context
        assert "dsn_oui" in context
        assert "dsn_extension" in context

        # Verify SystemVerilog define fields
        assert "pci_exp_ep_dsn_2" in context
        assert "pci_exp_ep_dsn_1" in context
        assert "pci_exp_ep_dsn_2_hex" in context
        assert "pci_exp_ep_dsn_1_hex" in context

    def test_context_values_match_input(
        self, context_builder, mock_template_context
    ):
        """Test that context values match input data."""
        # Mock configs
        mock_power_config = type("obj", (object,), {})()
        mock_error_config = type("obj", (object,), {})()
        mock_perf_config = type("obj", (object,), {})()
        mock_device_config = type("obj", (object,), {})()

        # Build context
        context = context_builder.build_enhanced_context(
            mock_template_context,
            mock_power_config,
            mock_error_config,
            mock_perf_config,
            mock_device_config,
        )

        # Verify vendor OUI matches vendor ID
        assert context["vendor_oui"] == 0x10DE

        # Verify DSN decomposition
        assert context["device_serial_number_int"] == 0x0000000101000A35
        assert context["dsn_upper_32"] == 0x00000001
        assert context["dsn_lower_32"] == 0x01000A35
        assert context["dsn_oui"] == 0x000A35


class TestTemplateCompatibility:
    """Test that new fields are compatible with templates."""

    @pytest.fixture
    def context_builder(self):
        """Provide SVContextBuilder instance."""
        logger = logging.getLogger("test_template_compat")
        return SVContextBuilder(logger)

    def test_all_required_fields_present(self, context_builder):
        """Test that all required fields for templates are present."""
        context = {}

        # Add OUI
        context_builder._add_vendor_oui(context, 0x10DE)

        # Add DSN semantic fields
        context_builder._add_dsn_semantic_fields(context, 0x0000000101000A35)

        # Required fields for pcie_endpoint_defines.sv.j2
        required_fields = [
            "vendor_oui",
            "vendor_oui_hex",
            "pci_exp_ep_oui",
            "dsn_upper_32",
            "dsn_lower_32",
            "dsn_oui",
            "dsn_oui_hex",
            "dsn_extension",
            "dsn_extension_hex",
            "pci_exp_ep_dsn_2",
            "pci_exp_ep_dsn_1",
            "pci_exp_ep_dsn_2_hex",
            "pci_exp_ep_dsn_1_hex",
        ]

        for field in required_fields:
            assert field in context, f"Missing required field: {field}"

    def test_format_strings_are_valid_systemverilog(self, context_builder):
        """Test that format strings are valid SystemVerilog syntax."""
        context = {}
        context_builder._add_dsn_semantic_fields(context, 0x123456789ABCDEF0)

        # Check that hex format strings match SystemVerilog syntax
        assert context["pci_exp_ep_dsn_2_hex"].startswith("32'h")
        assert context["pci_exp_ep_dsn_1_hex"].startswith("32'h")
        assert len(context["pci_exp_ep_dsn_2_hex"]) == 12  # "32'h" + 8 hex digits
        assert len(context["pci_exp_ep_dsn_1_hex"]) == 12


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
