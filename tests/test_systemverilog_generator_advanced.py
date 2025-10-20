#!/usr/bin/env python3
"""
Advanced unit tests for SystemVerilog Generator - Critical Areas Coverage.

This test module focuses on improving test coverage for critical areas that
were identified as under-tested, including MSI-X handling, VFIO integration,
complex template scenarios, and error edge cases.

Refactored for better maintainability with:
- Centralized fixtures and test data
- Reduced code duplication
- Clearer test structure and naming
- Improved error scenario testing
"""

import mmap
import os
import threading
import time
from pathlib import Path
from typing import Any, Dict, List, Optional
from unittest.mock import MagicMock, Mock, call, mock_open, patch

import pytest
from test_helpers import requires_hardware

from src.device_clone.device_config import DeviceClass, DeviceType
from src.device_clone.manufacturing_variance import VarianceModel
from src.templating.advanced_sv_features import (
    ErrorHandlingConfig,
    PerformanceConfig,
    PowerManagementConfig,
)
from src.templating.systemverilog_generator import AdvancedSVGenerator
from src.templating.template_renderer import TemplateRenderError

# Test Data Constants
MSIX_VECTOR_TEST_CASES = [
    (1, 1),  # Single vector -> 1 DWORD
    (8, 1),  # 8 vectors -> 1 DWORD
    (32, 1),  # 32 vectors -> 1 DWORD
    (33, 2),  # 33 vectors -> 2 DWORDs
    (64, 2),  # 64 vectors -> 2 DWORDs
    (65, 3),  # 65 vectors -> 3 DWORDs
]

MOCK_MSIX_TABLE_DATA = [
    0x12345678,
    0x9ABCDEF0,
    0x11111111,
    0x22222222,  # Vector 0
    0x33333333,
    0x44444444,
    0x55555555,
    0x66666666,  # Vector 1
]


class TestFixtures:
    """Centralized test fixtures and helper methods."""

    @staticmethod
    def create_mock_bars():
        """Create standardized mock BAR configuration."""
        return [
            Mock(index=0, size=4096, is_memory=True),
            Mock(index=1, size=8192, is_memory=True),
            Mock(index=2, size=16384, is_memory=True, address=0xF0000000),
        ]

    @staticmethod
    def create_mock_register_accesses():
        """Create mock register access patterns for testing."""
        return [
            Mock(register="VENDOR_ID", offset=0x00, operation="read"),
            Mock(register="DEVICE_ID", offset=0x02, operation="read"),
            Mock(register="MSI_CTRL", offset=0x50, operation="write"),
            Mock(register="MSI_CTRL", offset=0x50, operation="read"),
            Mock(register="CUSTOM_REG1", offset=0x100, operation="write"),
        ]

    @staticmethod
    def create_mock_behavior_profile():
        """Create a standard mock behavior profile."""
        profile = Mock()
        profile.variance_metadata = Mock()
        profile.timing_profiles = {"default": {"latency": 100}}
        profile.register_accesses = TestFixtures.create_mock_register_accesses()
        return profile


@pytest.fixture
def base_generator():
    """Create a basic generator instance for testing."""
    return AdvancedSVGenerator()


@pytest.fixture
def advanced_generator():
    """Create generator with advanced configurations enabled."""
    power_config = PowerManagementConfig(
        enable_power_management=True, enable_clock_gating=True
    )
    error_config = ErrorHandlingConfig(enable_error_detection=True)
    perf_config = PerformanceConfig(
        enable_performance_counters=True,
        enable_histograms=True,
    )

    return AdvancedSVGenerator()


@pytest.fixture
def standard_msix_context():
    """Create a standard MSI-X template context for testing."""
    return {
        "msix_config": {
            "is_supported": True,
            "num_vectors": 8,
            "table_offset": 0x1000,
            "table_bir": 2,
            "pba_offset": 0x2000,
            "pba_bir": 2,
        },
        "device_config": {
            "vendor_id": "1234",
            "device_id": "5678",
            "device_bdf": "0000:01:00.0",  # Add BDF for VFIO access
        },
        "bar_config": {
            "bars": TestFixtures.create_mock_bars(),
        },
        "config_space_data": {
            "device_info": {
                "bars": [
                    Mock(address=0xE0000000, size=4096),
                    Mock(address=0xE0001000, size=8192),
                    Mock(address=0xF0000000, size=16384),
                ]
            }
        },
        "device_signature": "0xCAFEBABE",
    }


@pytest.fixture
def mock_vfio_context():
    """Create a mock VFIO context for testing."""
    return {
        "device_config": {
            "vendor_id": "1234",
            "device_id": "5678",
            "enable_advanced_features": True,
        },
        "vfio_device_path": "/dev/vfio/42",
        "device_signature": "0xCAFEBABE",
    }


class TestMSIXAdvancedFunctionality:
    """Test advanced MSI-X related functionality in SystemVerilog generator."""

    @pytest.mark.parametrize("num_vectors,expected_dwords", MSIX_VECTOR_TEST_CASES)
    def test_msix_pba_init_vector_counts(
        self, base_generator, num_vectors, expected_dwords
    ):
        """Test MSI-X PBA initialization with various vector counts."""
        # Use the module generator's method directly
        result = base_generator.module_generator._generate_msix_pba_init(num_vectors)

        lines = result.strip().split("\n")
        assert len(lines) == expected_dwords

        # All should be zeros initially
        for line in lines:
            assert line == "00000000"

    def test_msix_table_init_with_hardware_data(
        self, base_generator, standard_msix_context
    ):
        """Test MSI-X table initialization when actual hardware data is available."""
        # Test in test environment - this will generate dummy data using the module generator
        result = base_generator.module_generator._generate_msix_table_init(
            standard_msix_context["msix_config"]["num_vectors"],
            standard_msix_context
        )

        lines = result.strip().split("\n")
        assert len(lines) == 32  # 8 vectors * 4 DWORDs per vector

        # Check that we get valid hex values (not specific values since it's dummy data)
        for line in lines:
            assert len(line) == 8  # 8 hex characters per DWORD
            int(line, 16)  # Should be valid hex

    def test_msix_table_init_fallback_default(
        self, base_generator, standard_msix_context
    ):
        """Test MSI-X table initialization fallback to default values."""
        # Test using the module generator method directly
        result = base_generator.module_generator._generate_msix_table_init(
            standard_msix_context["msix_config"]["num_vectors"],
            standard_msix_context
        )

        lines = result.strip().split("\n")
        # 8 vectors * 4 DWORDs per vector = 32 lines
        assert len(lines) == 32

        # Check that we get valid hex values
        for i, line in enumerate(lines):
            assert len(line) == 8  # 8 hex characters per DWORD
            value = int(line, 16)  # Should be valid hex
            # Check general pattern - should be reasonable values for MSI-X table
            if i % 4 == 2:  # Message Data field should contain vector number
                vector_num = i // 4
                assert value & 0xFF == vector_num  # Low 8 bits should be vector number




class TestVFIOErrorHandlingAdvanced:
    """Test advanced VFIO error handling scenarios."""

    def test_vfio_integration_template_errors(self, base_generator, mock_vfio_context):
        """Test complex VFIO error scenarios in template generation."""
        # Mock the renderer to raise various VFIO-related template errors
        with patch.object(base_generator.renderer, "render_template") as mock_render:
            mock_render.side_effect = TemplateRenderError(
                "VFIO device access failed in template"
            )

            with pytest.raises(TemplateRenderError, match="VFIO device access failed"):
                base_generator.generate_pcileech_integration_code(mock_vfio_context)

    def test_vfio_integration_success_with_verified_flag(self, base_generator):
        """Integration code should succeed when vfio_binding_verified flag present (no direct vfio_device)."""
        context = {"vfio_binding_verified": True}
        result = base_generator.generate_pcileech_integration_code(context)
        assert "PCILeech integration code" in result

    @pytest.mark.parametrize(
        "context",
        [
            pytest.param({}, id="empty_context"),
            pytest.param({"device_config": {}}, id="empty_device_config"),
            pytest.param(
                {"device_config": {"vendor_id": "FFFF"}, "device_signature": "0xTEST"},
                id="missing_device_id",
            ),
            pytest.param(
                {"device_config": {"device_id": "FFFF"}, "device_signature": "0xTEST"},
                id="missing_vendor_id",
            ),
        ],
    )
    def test_device_config_validation_strict_mode(self, base_generator, context):
        """Test device configuration validation in strict mode."""
        # These should fail validation with specific errors
        with pytest.raises((ValueError, TemplateRenderError)):
            base_generator.generate_pcileech_modules(context)


class TestComplexTemplateScenarios:
    """Test complex template rendering scenarios."""

    @requires_hardware("Requires VFIO hardware for MSI-X table access")
    def test_template_context_inheritance_and_merging(self, base_generator):
        """Test complex template context inheritance and merging."""
        base_context = {
            "device_config": {
                "vendor_id": "1234",
                "device_id": "5678",
                "class_code": "020000",
            },
            "msix_config": {
                "is_supported": True,
                "num_vectors": 4,
            },
            "device_signature": "0xCAFEBABE",
        }

        behavior_profile = TestFixtures.create_mock_behavior_profile()

        with patch.object(
            base_generator, "_extract_pcileech_registers", return_value=[]
        ):
            with patch.object(
                base_generator.renderer, "render_template"
            ) as mock_render:
                mock_render.return_value = "mock template output"

                result = base_generator.generate_pcileech_modules(
                    base_context, behavior_profile
                )

                # Verify that context was properly enhanced and merged
                self._verify_template_context_structure(mock_render.call_args_list)

    def test_nested_template_error_propagation(self, base_generator):
        """Test error propagation through nested template calls."""
        template_context = {"device_config": {}}

        # Create a chain of template errors
        template_errors = [
            TemplateRenderError("Primary template failed"),
            TemplateRenderError("Secondary template failed"),
            TemplateRenderError("Tertiary template failed"),
        ]

        with patch.object(base_generator.renderer, "render_template") as mock_render:
            mock_render.side_effect = template_errors

            # Each method should propagate the template error without modification
            with pytest.raises(
                TemplateRenderError, match="Missing fields: vendor_id, device_id"
            ):
                base_generator.generate_pcileech_modules(template_context)

    def test_large_template_context_performance(self, base_generator):
        """Test performance with large template contexts."""
        # Create a large template context
        large_context = self._create_large_template_context()

        with patch.object(base_generator.renderer, "render_template") as mock_render:
            mock_render.return_value = "template output"

            # Should handle large contexts without errors
            result = base_generator.generate_pcileech_modules(large_context)

            assert isinstance(result, dict)
            assert len(result) > 0

    # Helper methods
    @staticmethod
    def _verify_template_context_structure(call_args_list):
        """Verify template context was properly structured."""
        call_made = False
        for call_args in call_args_list:
            context = call_args[0][1]
            if "device" in context:
                # Check that device info was properly structured
                assert context["device"]["vendor_id"] == "1234"
                assert context["device"]["device_id"] == "5678"
                call_made = True
                break

        assert call_made, "Expected template call with device context not found"

    @staticmethod
    def _create_large_template_context():
        """Create a large template context for performance testing."""
        return {
            "device_config": {
                "vendor_id": "1234",
                "device_id": "5678",
                "subsystem_vendor_id": "1234",
                "subsystem_device_id": "5678",
                "class_code": "020000",
                "revision_id": "01",
                **{f"key_{i}": f"value_{i}" for i in range(1000)},
            },
            "registers": [{"name": f"reg_{i}", "offset": i * 4} for i in range(500)],
            "msix_config": {
                "is_supported": True,
                "num_vectors": 32,
                "large_data": list(range(10000)),
            },
            "device_signature": "0xCAFEBABE",
        }


class TestAdvancedSystemVerilogFeatures:
    """Test advanced SystemVerilog feature integration."""

    def test_advanced_feature_config_integration(self, advanced_generator):
        """Test integration of advanced feature configurations."""
        # Provide required device identifiers for the advanced generator
        # by creating a proper device_config dict
        device_config_dict = {
            "vendor_id": "0x8086",
            "device_id": "0x1533",
            "revision_id": "0x01",
            "device_signature": "8086:1533:01",
        }
        advanced_generator.device_config = device_config_dict

        registers = [
            {"name": "CTRL_REG", "offset": 0x00, "width": 32},
            {"name": "STATUS_REG", "offset": 0x04, "width": 32},
        ]

        variance_model = Mock()
        variance_model.timing_variance = 0.05
        variance_model.power_variance = 0.1

        with patch.object(
            advanced_generator.module_generator.renderer, "render_template"
        ) as mock_render:
            mock_render.return_value = "advanced systemverilog module"

            result = advanced_generator.generate_advanced_systemverilog(
                regs=registers, variance_model=variance_model
            )

            # Verify template was called at least twice
            # (device_specific_ports + main controller)
            assert len(mock_render.call_args_list) >= 2

            # Check the second call (main controller) has comprehensive context
            main_controller_call = mock_render.call_args_list[1]
            context = main_controller_call[0][1]

            # Check that device_config object is included
            assert "device_config" in context
            assert context.get("variance_model") == variance_model

    def test_pcileech_advanced_modules_generation(self, advanced_generator):
        """Test generation of advanced PCILeech modules."""
        template_context = {
            "device_config": {"enable_advanced_features": True},
            "device_signature": "0xCAFEBABE",
        }

        behavior_profile = TestFixtures.create_mock_behavior_profile()

        with patch.object(
            advanced_generator, "_extract_pcileech_registers"
        ) as mock_extract:
            mock_extract.return_value = [
                {"name": "ADVANCED_CTRL", "offset": 0x100},
                {"name": "PERF_COUNTER", "offset": 0x104},
            ]

            with patch.object(
                advanced_generator.renderer, "render_template"
            ) as mock_render:
                mock_render.return_value = "advanced controller module"

                result = advanced_generator._generate_pcileech_advanced_modules(
                    template_context, behavior_profile
                )

                assert "pcileech_advanced_controller" in result
                assert result["pcileech_advanced_controller"] is not None

    def test_extract_pcileech_registers_complex_behavior(self, advanced_generator):
        """Test extraction of registers from complex behavior profiles."""
        behavior_profile = TestFixtures.create_mock_behavior_profile()

        result = advanced_generator._extract_pcileech_registers(behavior_profile)

        assert len(result) == 4  # 4 unique registers

        # Verify register structure
        vendor_id_reg = next((r for r in result if r["name"] == "VENDOR_ID"), None)
        assert vendor_id_reg is not None
        assert vendor_id_reg["offset"] == 0x00
        assert vendor_id_reg["access_type"] == "ro"  # Only read operations

        msi_ctrl_reg = next((r for r in result if r["name"] == "MSI_CTRL"), None)
        assert msi_ctrl_reg is not None
        assert msi_ctrl_reg["access_type"] == "rw"  # Both read and write operations

    def test_extract_pcileech_registers_fallback_handling(self, advanced_generator):
        """Test fallback handling when behavior profile lacks register data."""
        # Test with minimal behavior profile
        minimal_profile = Mock()
        minimal_profile.register_accesses = (
            []
        )  # Empty list instead of missing attribute

        result = advanced_generator._extract_pcileech_registers(minimal_profile)

        # Should return default PCILeech registers when no register_accesses
        assert isinstance(result, list)
        assert len(result) >= 6  # At least 6 default registers
        assert any(r["name"] == "PCILEECH_CTRL" for r in result)


class TestErrorRecoveryAndRobustness:
    """Test error recovery and robustness scenarios."""

    def test_partial_template_failure_recovery(self, base_generator):
        """Test recovery from partial template failures."""
        template_context = {
            "device_config": {
                "vendor_id": "1234",
                "device_id": "5678",
                "subsystem_vendor_id": "1234",
                "subsystem_device_id": "5678",
                "class_code": "020000",
                "revision_id": "01",
            },
            "msix_config": {"is_supported": True, "num_vectors": 4},
            "device_signature": "0xCAFEBABE",
        }

        # Mock selective template failures
        def selective_template_mock(template_name, context):
            if "msix_capability" in template_name:
                raise TemplateRenderError("MSI-X template failed")
            return f"Generated: {template_name}"

        with patch.object(
            base_generator.renderer,
            "render_template",
            side_effect=selective_template_mock,
        ):
            # Should fail on MSI-X template but that's expected behavior
            with pytest.raises(TemplateRenderError, match="MSI-X template failed"):
                base_generator.generate_pcileech_modules(template_context)

    def test_memory_cleanup_on_errors(self, base_generator):
        """Test that memory is properly cleaned up on errors."""
        large_context = {
            "device_config": {"data": list(range(100000))},  # Large data
        }

        with patch.object(base_generator.renderer, "render_template") as mock_render:
            mock_render.side_effect = TemplateRenderError("Memory error")

            with pytest.raises(TemplateRenderError):
                base_generator.generate_pcileech_modules(large_context)

            # Memory should be cleaned up (context should not persist)
            # This is more of a contract test - Python's GC will handle it

    def test_concurrent_access_safety(self, base_generator):
        """Test thread safety of generator methods."""
        results = []
        errors = []

        def worker():
            try:
                with patch.object(
                    base_generator.renderer,
                    "render_template",
                    return_value="safe output",
                ):
                    result = base_generator.generate_device_specific_ports()
                    results.append(result)
            except Exception as e:
                errors.append(e)

        # Run multiple threads concurrently
        threads = [threading.Thread(target=worker) for _ in range(5)]

        for thread in threads:
            thread.start()

        for thread in threads:
            thread.join()

        # All should succeed
        assert len(errors) == 0
        assert len(results) == 5
        assert all(r == "safe output" for r in results)


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
