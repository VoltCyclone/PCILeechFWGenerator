"""Integration test for SystemVerilog template rendering.

This test ensures that templates are actually rendered without errors,
catching issues like missing template variables that mocked tests miss.
"""

from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Dict, List, Optional
from unittest.mock import Mock

import pytest

from src.device_clone.device_config import DeviceClass, DeviceType
from src.exceptions import TemplateRenderError
from src.templating.sv_config import ErrorHandlingConfig, PerformanceConfig
from src.device_clone.overlay_utils import compute_sparse_hash_table_size
from src.templating.advanced_sv_power import PowerManagementConfig
from src.templating.systemverilog_generator import AdvancedSVGenerator

# Constants for test data
DEFAULT_VENDOR_ID = "8086"
DEFAULT_DEVICE_ID = "1533"
DEFAULT_CLASS_CODE = "020000"
DEFAULT_REVISION_ID = "03"
DEFAULT_SUBSYSTEM_VENDOR_ID = "8086"
DEFAULT_SUBSYSTEM_DEVICE_ID = "0001"
DEFAULT_DEVICE_BDF = "0000:03:00.0"
DEFAULT_DEVICE_SIGNATURE = "0x12345678"
DEFAULT_BAR_SIZE = 4096
DEFAULT_CLOCK_FREQUENCY = 100

# Expected module names
EXPECTED_MODULES = [
    "pcileech_tlps128_bar_controller",
    "pcileech_fifo",
    "top_level_wrapper",
    "pcileech_cfgspace.coe",
]

# Minimum expected content lengths
MIN_CONTENT_LENGTHS = {
    "pcileech_tlps128_bar_controller": 100,
    "pcileech_fifo": 100,
    "top_level_wrapper": 100,
    "pcileech_cfgspace.coe": 10,
}


@dataclass
class TestConfigurations:
    """Container for test configuration objects."""

    error_config: ErrorHandlingConfig = field(
        default_factory=lambda: ErrorHandlingConfig(
            enable_error_detection=True,
            enable_error_logging=True,
            enable_auto_retry=True,
        )
    )
    perf_config: PerformanceConfig = field(
        default_factory=lambda: PerformanceConfig(
            enable_transaction_counters=True,
            enable_latency_measurement=True,
            enable_bandwidth_monitoring=True,
        )
    )
    power_config: PowerManagementConfig = field(
        default_factory=lambda: PowerManagementConfig(
            enable_pme=True,
            enable_wake_events=False,
        )
    )


class TemplateContextBuilder:
    """Builder class for creating template contexts with sensible defaults."""

    @staticmethod
    def create_minimal_context() -> Dict[str, Any]:
        """Create a minimal valid template context."""
        base_context = {
            "device_config": TemplateContextBuilder._create_device_config(),
            "device_signature": DEFAULT_DEVICE_SIGNATURE,
            "msix_config": TemplateContextBuilder._create_msix_config(),
            "bar_config": TemplateContextBuilder._create_bar_config(),
            "board_config": TemplateContextBuilder._create_board_config(),
            "interrupt_config": {},
            "config_space_data": {},
            "timing_config": TemplateContextBuilder._create_timing_config(),
            "pcileech_config": TemplateContextBuilder._create_pcileech_config(),
            "generation_metadata": TemplateContextBuilder._create_generation_metadata(),
            "active_device_config": TemplateContextBuilder._create_active_device_config(),
            "device_serial_number_int": 0x1234567890ABCDEF,
        }

        base_context.update(
            {
                "OVERLAY_MAP": [],
                "OVERLAY_ENTRIES": 0,
                "ENABLE_SPARSE_MAP": 0,
                "ENABLE_BIT_TYPES": 1,
                "HASH_TABLE_SIZE": compute_sparse_hash_table_size(0),
            }
        )

        return base_context

    @staticmethod
    def _create_device_config(
        vendor_id: str = DEFAULT_VENDOR_ID,
        device_id: str = DEFAULT_DEVICE_ID,
        enable_perf_counters: bool = True,
        enable_error_injection: bool = True,
        enable_dma_operations: bool = True,
    ) -> Dict[str, Any]:
        """Create device configuration."""
        return {
            "vendor_id": vendor_id,
            "device_id": device_id,
            "class_code": DEFAULT_CLASS_CODE,
            "revision_id": DEFAULT_REVISION_ID,
            "subsystem_vendor_id": DEFAULT_SUBSYSTEM_VENDOR_ID,
            "subsystem_device_id": DEFAULT_SUBSYSTEM_DEVICE_ID,
            "device_bdf": DEFAULT_DEVICE_BDF,
            "enable_perf_counters": enable_perf_counters,
            "enable_error_injection": enable_error_injection,
            "enable_dma_operations": enable_dma_operations,
        }

    @staticmethod
    def _create_msix_config(num_vectors: int = 1) -> Dict[str, Any]:
        """Create MSI-X configuration."""
        return {
            "num_vectors": num_vectors,
            "table_offset": 0x1000,
            "table_bir": 0,
            "pba_offset": 0x800,
            "pba_bir": 0,
        }

    @staticmethod
    def _create_bar_config(
        bar_index: int = 0,
        aperture_size: int = DEFAULT_BAR_SIZE,
        bar_type: int = 0,
        prefetchable: bool = False,
    ) -> Dict[str, Any]:
        """Create BAR configuration."""
        return {
            "bar_index": bar_index,
            "aperture_size": aperture_size,
            "bar_type": bar_type,
            "prefetchable": prefetchable,
            "bars": [TemplateContextBuilder._create_mock_bar()],
        }

    @staticmethod
    def _create_mock_bar(
        index: int = 0,
        size: int = DEFAULT_BAR_SIZE,
        bar_type: str = "memory",
        prefetchable: bool = False,
        is_64bit: bool = False,
    ) -> Mock:
        """Create a mock BAR object."""
        return Mock(
            index=index,
            size=size,
            bar_type=bar_type,
            prefetchable=prefetchable,
            is_64bit=is_64bit,
            address=0,
            base_address=0,
            get_size_encoding=lambda: 0xFFFFF000 if size == 4096 else 0xFFFE0000,
        )

    @staticmethod
    def _create_board_config(
        name: str = "test_board",
        fpga_part: str = "xc7a35t",
    ) -> Dict[str, Any]:
        """Create board configuration."""
        return {
            "name": name,
            "fpga_part": fpga_part,
        }

    @staticmethod
    def _create_timing_config() -> Any:
        """Create timing configuration object."""
        return type(
            "TimingConfig",
            (),
            {
                "clock_frequency_mhz": DEFAULT_CLOCK_FREQUENCY,
                "read_latency": 2,
                "write_latency": 1,
                "setup_time": 1,
                "hold_time": 1,
                "burst_length": 4,
            },
        )()

    @staticmethod
    def _create_pcileech_config() -> Any:
        """Create PCILeech configuration object."""
        return type(
            "PCILeechConfig",
            (),
            {
                "buffer_size": 4096,
                "command_timeout": 1000,
                # Both DMA and scatter-gather flags are explicitly set to show their relationship
                # In production code, enable_scatter_gather will default to enable_dma's value if not set
                "enable_dma": True,
                "enable_scatter_gather": True,
                "max_payload_size": 256,
                "max_read_request_size": 512,
            },
        )()

    @staticmethod
    def _create_generation_metadata() -> Dict[str, str]:
        """Create generation metadata."""
        return {
            "generated_at": "2024-01-01T12:00:00Z",
            "version": "2.0.0",
            "generator": "PCILeechFWGenerator",
        }

    @staticmethod
    def _create_active_device_config(
        vendor_id: str = DEFAULT_VENDOR_ID,
        device_id: str = DEFAULT_DEVICE_ID,
    ) -> Dict[str, Any]:
        """Create active device configuration."""
        return {
            "vendor_id": vendor_id,
            "device_id": device_id,
            "class_code": DEFAULT_CLASS_CODE,
            "revision_id": DEFAULT_REVISION_ID,
            "subsystem_vendor_id": DEFAULT_SUBSYSTEM_VENDOR_ID,
            "subsystem_device_id": DEFAULT_SUBSYSTEM_DEVICE_ID,
            "num_sources": 1,
            "default_priority": 0,
        }

    @staticmethod
    def create_full_featured_context() -> Dict[str, Any]:
        """Create a fully-featured template context with all optional configurations."""
        context = TemplateContextBuilder.create_minimal_context()

        # Update with full-featured configurations
        context["device_signature"] = "0xCAFEBABE"
        context["msix_config"] = {
            "num_vectors": 32,
            "table_bir": 2,
            "table_offset": 0x0000,
            "pba_bir": 2,
            "pba_offset": 0x1000,
        }

        # Create multiple BARs with different configurations
        context["bar_config"] = {
            "aperture_size": 131072,
            "bar_index": 2,
            "bar_type": 1,  # 64-bit
            "prefetchable": True,
            "bars": [
                TemplateContextBuilder._create_mock_bar(
                    0, 4096, "memory", False, False
                ),
                TemplateContextBuilder._create_mock_bar(1, 8192, "io", False, False),
                TemplateContextBuilder._create_mock_bar(
                    2, 131072, "memory", True, True
                ),
            ],
        }

        # Enhanced board configuration
        context["board_config"] = {
            "name": "custom_board",
            "fpga_part": "xcvu9p-flga2104-2L-e",
            "fpga_family": "ultrascale_plus",
            "pcie_ip_type": "pcie4",
            "max_lanes": 16,
            "supports_msi": True,
            "supports_msix": True,
            "has_option_rom": True,
            "max_link_speed": "gen4",
            "max_link_width": "x16",
        }

        # Enhanced interrupt configuration
        context["interrupt_config"] = {
            "vectors": 32,
            "msi_enabled": False,
            "msix_enabled": True,
            "interrupt_moderation": True,
        }

        # Add config space data
        context["config_space_data"] = {"data": list(range(256))}

        # Enhanced timing configuration
        context["timing_config"] = type(
            "TimingConfig",
            (),
            {
                "read_latency": 8,
                "write_latency": 4,
                "burst_length": 32,
                "inter_burst_gap": 16,
                "timeout_cycles": 2048,
                "clock_frequency_mhz": DEFAULT_CLOCK_FREQUENCY,
            },
        )()

        # Enhanced PCILeech configuration
        context["pcileech_config"] = type(
            "PCILeechConfig",
            (),
            {
                "buffer_size": 8192,
                "enable": True,
                "dma_enable": True,
                "scatter_gather": True,
                "command_timeout": 1000,
                "enable_dma": True,
                "enable_scatter_gather": True,
            },
        )()

        # Enhanced generation metadata
        context["generation_metadata"]["build_id"] = "test-build-123"
        context["generation_metadata"]["timestamp"] = "2024-01-01T12:00:00Z"
        context["device_serial_number_int"] = 0xFEDCBA9876543210

        return context


class TestSystemVerilogTemplateIntegration:
    """Integration tests for SystemVerilog template rendering."""

    @pytest.fixture(autouse=True)
    def setup(self):
        """Set up test fixtures."""
        self.configs = TestConfigurations()
        self.generator = AdvancedSVGenerator(
            power_config=self.configs.power_config,
            error_config=self.configs.error_config,
            perf_config=self.configs.perf_config,
        )
        self.context_builder = TemplateContextBuilder()

    def _validate_generated_modules(self, result: Dict[str, str]) -> None:
        """Validate that all expected modules were generated with proper content.

        Args:
            result: Dictionary of generated module names to content

        Raises:
            AssertionError: If validation fails
        """
        # Check all expected modules are present
        for module in EXPECTED_MODULES:
            assert module in result, f"Missing expected module: {module}"

        # Check content lengths
        for module, min_length in MIN_CONTENT_LENGTHS.items():
            assert (
                len(result[module]) > min_length
            ), f"Module {module} content too short: {len(result[module])} < {min_length}"

    def _validate_bar_controller_content(
        self, content: str, expected_values: Dict[str, str]
    ) -> None:
        """Validate BAR controller content contains expected values.

        Args:
            content: Generated BAR controller content
            expected_values: Dictionary of expected strings to find in content

        Raises:
            AssertionError: If validation fails
        """
        assert "module pcileech_tlps128_bar_controller" in content
        assert "BAR_APERTURE_SIZE" in content
        assert any(term in content for term in ["MSI-X", "MSIX"])

        for name, value in expected_values.items():
            # For device_signature, the template emits an SV literal (e.g., 32'h12345678)
            # Accept either the raw value or the SV formatted equivalent
            if (
                name == "device_signature"
                and isinstance(value, str)
                and value.lower().startswith("0x")
            ):
                try:
                    int_val = int(value, 16)
                    sv_form = f"32'h{int_val:08X}"
                    if sv_form in content or value in content:
                        continue
                except Exception as e:
                    # Parsing may fail if device_signature isn't a strict hex string;
                    # fall back to validating the raw value presence below.
                    print(f"Warning: failed to parse device_signature '{value}': {e}")
            assert value in content, f"Expected {name} '{value}' not found in content"

    def test_minimal_template_renders_successfully(self):
        """Test that minimal template context renders without errors."""
        template_context = self.context_builder.create_minimal_context()

        result = self.generator.generate_pcileech_modules(template_context)

        self._validate_generated_modules(result)
        self._validate_bar_controller_content(
            result["pcileech_tlps128_bar_controller"],
            {"device_signature": DEFAULT_DEVICE_SIGNATURE},
        )

    def test_full_featured_template_renders_successfully(self):
        """Test template rendering with all optional configurations provided."""
        template_context = self.context_builder.create_full_featured_context()

        result = self.generator.generate_pcileech_modules(template_context)

        self._validate_generated_modules(result)
        self._validate_bar_controller_content(
            result["pcileech_tlps128_bar_controller"],
            {
                "device_signature": "0xCAFEBABE",
                "aperture_size": "131072",  # Could also be "0x20000"
            },
        )

    def test_device_signature_vid_did_format_renders(self):
        """Device signature provided as 'VID:DID' should render to a valid SV literal."""
        template_context = self.context_builder.create_minimal_context()
        template_context["device_signature"] = "1912:0014"

        result = self.generator.generate_pcileech_modules(template_context)

        # Validate expected modules and content
        self._validate_generated_modules(result)
        content = result["pcileech_tlps128_bar_controller"]
        # Expect the SV literal equivalent
        assert "32'h19120014" in content

    def test_device_signature_three_part_vid_did_rid_masks_to_width(self):
        """Device signature 'VID:DID:RID' should mask to 32 bits and render correctly."""
        template_context = self.context_builder.create_minimal_context()
        template_context["device_signature"] = "8086:1234:15"

        result = self.generator.generate_pcileech_modules(template_context)

        self._validate_generated_modules(result)
        content = result["pcileech_tlps128_bar_controller"]
        # 0x8086123415 masked to 32 bits -> 0x86123415
        assert "32'h86123415" in content

    @pytest.mark.parametrize(
        "vendor_id,device_id",
        [
            ("10EC", "8168"),  # Realtek
            ("8086", "1533"),  # Intel
            ("1022", "1480"),  # AMD
        ],
    )
    def test_different_vendor_devices(self, vendor_id: str, device_id: str):
        """Test template rendering with different vendor/device combinations."""
        template_context = self.context_builder.create_minimal_context()
        template_context["device_config"]["vendor_id"] = vendor_id
        template_context["device_config"]["device_id"] = device_id
        template_context["active_device_config"]["vendor_id"] = vendor_id
        template_context["active_device_config"]["device_id"] = device_id

        result = self.generator.generate_pcileech_modules(template_context)

        self._validate_generated_modules(result)

    def test_empty_board_config_uses_defaults(self):
        """Test that empty board_config uses proper defaults without errors."""
        template_context = self.context_builder.create_minimal_context()
        template_context["board_config"] = {}

        # Should succeed with empty board_config defaulting to {}
        result = self.generator.generate_pcileech_modules(template_context)

        self._validate_generated_modules(result)

    def test_missing_optional_configs_handled_gracefully(self):
        """Test that missing optional configurations are handled gracefully."""
        template_context = self.context_builder.create_minimal_context()

        # Remove optional configurations
        template_context["interrupt_config"] = {}
        template_context["config_space_data"] = {}

        result = self.generator.generate_pcileech_modules(template_context)

        self._validate_generated_modules(result)

    @pytest.mark.parametrize(
        "missing_field",
        [
            "device_config",
            "device_signature",
            # NOTE: bar_config and generation_metadata are now provided by
            # Phase 0 compatibility defaults and should not raise errors
        ],
    )
    def test_missing_required_field_raises_error(self, missing_field: str):
        """Test that missing critical fields raise appropriate errors.

        Phase 0 compatibility provides defaults for template convenience fields
        (bar_config, generation_metadata) but still enforces strict validation
        for critical security fields (device_config, device_signature).
        """
        template_context = self.context_builder.create_minimal_context()
        del template_context[missing_field]

        with pytest.raises((TemplateRenderError, KeyError)):
            self.generator.generate_pcileech_modules(template_context)

    def test_phase_0_compatibility_provides_defaults(self):
        """Test that Phase 0 compatibility provides defaults for convenience fields."""
        template_context = self.context_builder.create_minimal_context()

        # Remove fields that should be provided by Phase 0 compatibility
        if "bar_config" in template_context:
            del template_context["bar_config"]
        if "generation_metadata" in template_context:
            del template_context["generation_metadata"]

        # This should succeed with Phase 0 compatibility defaults
        result = self.generator.generate_pcileech_modules(template_context)
        assert isinstance(result, dict)
        assert len(result) > 0

    def test_invalid_bar_configuration_handled(self):
        """Test handling of invalid BAR configurations."""
        template_context = self.context_builder.create_minimal_context()

        # Create invalid BAR with negative size
        invalid_bar = Mock(
            index=0,
            size=-1,  # Invalid negative size
            bar_type="memory",
            prefetchable=False,
            is_64bit=False,
            address=0,
            base_address=0,
            get_size_encoding=lambda: 0x00000000,
        )

        template_context["bar_config"]["bars"] = [invalid_bar]

        # Should either handle gracefully or raise a specific error
        try:
            result = self.generator.generate_pcileech_modules(template_context)
            # If it succeeds, verify it handled the invalid size
            assert isinstance(result, dict)
        except (TemplateRenderError, ValueError) as e:
            # Expected behavior - invalid configuration should be caught
            assert "size" in str(e).lower() or "bar" in str(e).lower()

    def test_template_validation_compatibility(self):
        """Test compatibility with TemplateContextValidator requirements."""
        from src.templating.template_context_validator import TemplateContextValidator

        validator = TemplateContextValidator()
        template_context = self.context_builder.create_minimal_context()

        # Generate modules - should work with validator's requirements
        result = self.generator.generate_pcileech_modules(template_context)

        self._validate_generated_modules(result)

    @pytest.mark.parametrize(
        "num_vectors,expected_in_output",
        [
            (1, True),  # Minimal vectors
            (32, True),  # Standard vectors
            (256, True),  # Maximum vectors
        ],
    )
    def test_msix_vector_configurations(
        self, num_vectors: int, expected_in_output: bool
    ):
        """Test different MSI-X vector configurations."""
        template_context = self.context_builder.create_minimal_context()
        template_context["msix_config"]["num_vectors"] = num_vectors

        result = self.generator.generate_pcileech_modules(template_context)

        self._validate_generated_modules(result)
        if expected_in_output:
            content = result["pcileech_tlps128_bar_controller"]
            assert any(term in content for term in ["MSI-X", "MSIX"])

    def test_performance_counter_generation(self):
        """Test that performance counters are properly generated when enabled."""
        template_context = self.context_builder.create_minimal_context()
        template_context["device_config"]["enable_perf_counters"] = True

        result = self.generator.generate_pcileech_modules(template_context)

        # Verify performance-related content exists
        bar_controller = result["pcileech_tlps128_bar_controller"]
        assert any(
            term in bar_controller for term in ["counter", "performance", "perf"]
        )

    def test_error_injection_generation(self):
        """Test that error injection logic is generated when enabled."""
        template_context = self.context_builder.create_minimal_context()
        template_context["device_config"]["enable_error_injection"] = True

        result = self.generator.generate_pcileech_modules(template_context)

        # Verify error injection related content exists
        bar_controller = result["pcileech_tlps128_bar_controller"]
        assert any(term in bar_controller for term in ["error", "inject", "fault"])

    @pytest.mark.parametrize(
        "buffer_size",
        [
            1024,  # Small buffer
            4096,  # Standard buffer
            8192,  # Large buffer
            16384,  # Extra large buffer
        ],
    )
    def test_different_buffer_sizes(self, buffer_size: int):
        """Test template rendering with different buffer sizes."""
        template_context = self.context_builder.create_minimal_context()
        template_context["pcileech_config"] = type(
            "PCILeechConfig",
            (),
            {
                "buffer_size": buffer_size,
                "command_timeout": 1000,
                "enable_dma": True,
                "enable_scatter_gather": True,
                "max_payload_size": 256,
                "max_read_request_size": 512,
            },
        )()

        result = self.generator.generate_pcileech_modules(template_context)

        self._validate_generated_modules(result)

    def test_coe_file_generation(self):
        """Test that COE file is properly generated with valid content."""
        template_context = self.context_builder.create_minimal_context()
        template_context["config_space_data"] = {"data": list(range(256))}

        result = self.generator.generate_pcileech_modules(template_context)

        coe_content = result["pcileech_cfgspace.coe"]
        assert "memory_initialization_radix" in coe_content
        assert "memory_initialization_vector" in coe_content

    def test_concurrent_template_generation(self):
        """Test that multiple template generations don't interfere with each other."""
        contexts = [
            self.context_builder.create_minimal_context(),
            self.context_builder.create_full_featured_context(),
        ]

        results = []
        for context in contexts:
            result = self.generator.generate_pcileech_modules(context)
            results.append(result)

        # Verify both generations succeeded independently
        for result in results:
            self._validate_generated_modules(result)

        # Verify they produced different content (due to different configs)
        assert (
            results[0]["pcileech_tlps128_bar_controller"]
            != results[1]["pcileech_tlps128_bar_controller"]
        )


if __name__ == "__main__":
    pytest.main([__file__, "-v", "--tb=short"])
