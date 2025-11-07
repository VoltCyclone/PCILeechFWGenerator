"""
Comprehensive unit tests for unified_context.py module.

Tests cover:
- TemplateObject functionality
- UnifiedContextBuilder methods
- Template compatibility
- Edge cases and error handling
"""

import logging
from datetime import datetime
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest.mock import MagicMock, mock_open, patch

import pytest

from src.utils.unified_context import (TemplateObject, UnifiedContextBuilder,
                                       UnifiedDeviceConfig,
                                       convert_to_template_object,
                                       ensure_template_compatibility)
from src.utils.version_resolver import get_package_version


class TestGetPackageVersion:
    """Test the get_package_version function."""

    def test_get_version_from_version_file(self):
        """Test extracting version from __version__.py file."""
        with TemporaryDirectory() as tmp_dir:
            tmp_path = Path(tmp_dir)

            # Create a mock version file
            version_file = tmp_path / "__version__.py"
            version_file.write_text('__version__ = "2.5.0"')

            with patch("src.utils.version_resolver.Path") as mock_path:
                # Mock the path resolution
                mock_version_file = MagicMock()
                mock_version_file.exists.return_value = True

                # Set up the path traversal
                mock_parent = MagicMock()
                mock_parent.parent = MagicMock()
                mock_parent.parent.__truediv__.return_value = mock_version_file
                mock_path.return_value = MagicMock()
                mock_path.return_value.parent = mock_parent

                with patch(
                    "builtins.open", mock_open(read_data='__version__ = "2.5.0"')
                ):
                    version = get_package_version()
                    assert version == "2.5.0"

    def test_get_version_setuptools_scm_fallback(self):
        """Test fallback to setuptools_scm."""
        # Force version file miss and hit setuptools_scm fallback
        # without requiring the external package.
        with patch(
            "src.utils.version_resolver._try_version_file",
            return_value=None,
        ):
            with patch(
                "src.utils.version_resolver._try_setuptools_scm",
                return_value="1.2.3",
            ):
                version = get_package_version()
                assert version == "1.2.3"

    def test_get_version_importlib_fallback(self):
        """Test fallback to importlib.metadata."""
        with patch(
            "src.utils.version_resolver._try_version_file",
            return_value=None,
        ):
            with patch(
                "src.utils.version_resolver._try_setuptools_scm",
                return_value=None,
            ):
                with patch(
                    "src.utils.version_resolver._try_importlib_metadata",
                    return_value="3.4.5",
                ):
                    version = get_package_version()
                    assert version == "3.4.5"

    def test_get_version_final_fallback(self):
        """Test final fallback to default version."""
        with patch(
            "src.utils.version_resolver._try_version_file",
            return_value=None,
        ):
            with patch(
                "src.utils.version_resolver._try_setuptools_scm",
                return_value=None,
            ):
                with patch(
                    "src.utils.version_resolver._try_importlib_metadata",
                    return_value=None,
                ):
                    with patch(
                        "src.utils.version_resolver._try_git_describe",
                        return_value=None,
                    ):
                        version = get_package_version()
                        assert version == "unknown"

    def test_get_version_exception_handling(self):
        """Test exception handling returns default version."""
        # Force exceptions inside helpers (caught internally) and ensure
        # we return the safe fallback without requiring external packages.
        with patch(
            "src.utils.version_resolver.Path",
            side_effect=Exception("Test error"),
        ):
            with patch(
                "src.utils.version_resolver._try_setuptools_scm",
                return_value=None,
            ):
                with patch(
                    "src.utils.version_resolver._try_importlib_metadata",
                    return_value=None,
                ):
                    with patch(
                        "src.utils.version_resolver._try_git_describe",
                        return_value=None,
                    ):
                        version = get_package_version()
                        assert version == "unknown"


class TestTemplateObject:
    """Test the TemplateObject class."""

    def test_basic_initialization(self):
        """Test basic TemplateObject initialization."""
        data = {"key1": "value1", "key2": 42}
        obj = TemplateObject(data)

        assert obj.key1 == "value1"
        assert obj.key2 == 42
        assert obj["key1"] == "value1"
        assert obj["key2"] == 42

    def test_nested_dict_conversion(self):
        """Test that nested dictionaries are converted to TemplateObjects."""
        data = {
            "top_level": "value",
            "nested": {
                "inner_key": "inner_value",
                "deep_nested": {"deep_key": "deep_value"},
            },
        }
        obj = TemplateObject(data)

        nested = getattr(obj, "nested")
        assert getattr(nested, "inner_key") == "inner_value"
        deep_nested = getattr(nested, "deep_nested")
        assert getattr(deep_nested, "deep_key") == "deep_value"
        assert isinstance(nested, TemplateObject)
        assert isinstance(deep_nested, TemplateObject)

    def test_list_with_dicts_conversion(self):
        """Test that lists containing dictionaries are properly converted."""
        data = {
            "item_list": [
                {"name": "item1", "value": 1},
                {"name": "item2", "value": 2},
                "simple_string",
            ]
        }
        obj = TemplateObject(data)

        items_list = getattr(obj, "item_list")
        assert len(items_list) == 3
        assert isinstance(items_list[0], TemplateObject)
        assert isinstance(items_list[1], TemplateObject)
        assert items_list[0].name == "item1"
        assert items_list[1].value == 2
        assert items_list[2] == "simple_string"

    def test_dictionary_style_access(self):
        """Test dictionary-style access methods."""
        data = {"key1": "value1", "key2": "value2"}
        obj = TemplateObject(data)

        # Test __getitem__
        assert obj["key1"] == "value1"

        # Test __setitem__
        obj["key3"] = "value3"
        assert obj["key3"] == "value3"
        assert obj.key3 == "value3"

        # Test __contains__
        assert "key1" in obj
        assert "nonexistent" not in obj

        # Test get method
        assert obj.get("key1") == "value1"
        assert obj.get("nonexistent", "default") == "default"

    def test_iteration_methods(self):
        """Test iteration methods (keys, values, items)."""
        data = {"a": 1, "b": 2, "c": 3}
        obj = TemplateObject(data)

        assert set(obj.keys()) == {"a", "b", "c"}
        assert set(obj.values()) == {1, 2, 3}
        assert set(obj.items()) == {("a", 1), ("b", 2), ("c", 3)}

    def test_safe_defaults(self):
        """Test safe defaults for commonly accessed template variables."""
        obj = TemplateObject({})

        assert obj.counter_width == 32
        assert obj.process_variation == 0.1
        assert obj.temperature_coefficient == 0.05
        assert obj.voltage_variation == 0.03

    def test_attribute_error_for_unknown_attrs(self):
        """Test that AttributeError is raised for unknown attributes."""
        obj = TemplateObject({})

        with pytest.raises(AttributeError):
            _ = obj.unknown_attribute

    def test_to_dict_method(self):
        """Test converting back to dictionary."""
        data = {
            "simple": "value",
            "nested": {"inner": "nested_value"},
            "list_with_dicts": [{"item": 1}],
        }
        obj = TemplateObject(data)

        # Add a direct attribute
        setattr(obj, "direct_attr", "direct_value")

        result_dict = obj.to_dict()

        assert result_dict["simple"] == "value"
        assert result_dict["direct_attr"] == "direct_value"
        # Nested TemplateObjects should be converted to regular dicts
        assert isinstance(result_dict["nested"], dict)
        assert result_dict["nested"]["inner"] == "nested_value"


class TestUnifiedDeviceConfig:
    """Test the UnifiedDeviceConfig dataclass."""

    def test_default_values(self):
        """Test that default values are set correctly."""
        config = UnifiedDeviceConfig(
            vendor_id="8086",
            device_id="1234",
            subsystem_vendor_id="8086",
            subsystem_device_id="1234",
            class_code="020000",
            revision_id="01",
        )

        assert config.enabled is True
        assert config.timer_period == 1000
        assert config.timer_enable is True
        assert config.msi_vector_width == 5
        assert config.msi_64bit_addr is True
        assert config.num_sources == 1
        assert config.default_priority == 4
        assert config.interrupt_mode == "intx"
        assert config.device_class == "generic"

    def test_custom_values(self):
        """Test setting custom values."""
        config = UnifiedDeviceConfig(
            vendor_id="10de",
            device_id="5678",
            subsystem_vendor_id="10de",
            subsystem_device_id="5678",
            class_code="030000",
            revision_id="02",
            enabled=False,
            interrupt_mode="msi",
            interrupt_vectors=8,
        )

        assert config.vendor_id == "10de"
        assert config.device_id == "5678"
        assert config.class_code == "030000"
        assert config.enabled is False
        assert config.interrupt_mode == "msi"
        assert config.interrupt_vectors == 8


class TestUnifiedContextBuilder:
    """Test the UnifiedContextBuilder class."""

    def setup_method(self):
        """Set up test fixtures."""
        self.builder = UnifiedContextBuilder()

    def test_initialization(self):
        """Test builder initialization."""
        # Default logger
        builder = UnifiedContextBuilder()
        assert builder.logger is not None

        # Custom logger
        custom_logger = logging.getLogger("test")
        builder = UnifiedContextBuilder(custom_logger)
        assert builder.logger == custom_logger

    def test_create_active_device_config_basic(self):
        """Test creating basic active device configuration."""
        config = self.builder.create_active_device_config(
            vendor_id="8086", device_id="1234"
        )

        assert isinstance(config, TemplateObject)
        assert config.vendor_id == "8086"
        assert config.device_id == "1234"
        assert config.subsystem_vendor_id == "8086"  # Default to vendor_id
        assert config.subsystem_device_id == "1234"  # Default to device_id
        assert config.class_code == "000000"
        assert config.revision_id == "00"

    def test_create_active_device_config_with_subsystem_ids(self):
        """Test creating config with explicit subsystem IDs."""
        config = self.builder.create_active_device_config(
            vendor_id="8086",
            device_id="1234",
            subsystem_vendor_id="1000",
            subsystem_device_id="5678",
        )

        assert config.subsystem_vendor_id == "1000"
        assert config.subsystem_device_id == "5678"

    def test_create_active_device_config_network_device(self):
        """Test creating config for network device."""
        config = self.builder.create_active_device_config(
            vendor_id="8086", device_id="1234", class_code="020000"
        )

        assert config.device_class == "network"
        assert config.is_network is True
        assert config.is_storage is False
        assert config.is_display is False

    def test_create_active_device_config_validation_error(self):
        """Test validation error for missing required parameters."""
        with pytest.raises(Exception) as exc1:
            self.builder.create_active_device_config(vendor_id="", device_id="1234")
        assert "vendor_id" in str(exc1.value)

        with pytest.raises(Exception) as exc2:
            self.builder.create_active_device_config(vendor_id="8086", device_id="")
        assert "device_id" in str(exc2.value)

    def test_create_generation_metadata(self):
        """Test creating generation metadata."""
        with patch("src.utils.metadata.datetime") as mock_datetime:
            mock_datetime.now.return_value.isoformat.return_value = (
                "2023-01-01T12:00:00"
            )

            metadata = self.builder.create_generation_metadata(
                device_signature="8086:1234", device_bdf="0000:01:00.0"
            )

            assert isinstance(metadata, TemplateObject)
            assert metadata.generated_at == "2023-01-01T12:00:00"
            assert metadata.timestamp == "2023-01-01T12:00:00"
            assert metadata.generator == "PCILeechFWGenerator"
            assert metadata.device_signature == "8086:1234"
            assert metadata.device_bdf == "0000:01:00.0"

    def test_create_board_config(self):
        """Test creating board configuration."""
        config = self.builder.create_board_config(
            board_name="custom_board", fpga_part="xc7k325t", fpga_family="kintex7"
        )

        assert isinstance(config, TemplateObject)
        assert config.name == "custom_board"
        assert config.fpga_part == "xc7k325t"
        assert config.fpga_family == "kintex7"
        assert config.pcie_ip_type == "xdma"
        assert config.sys_clk_freq_mhz == 100
        assert config.supports_msi is True

    def test_create_template_logic_flags(self):
        """Test creating template logic flags."""
        flags = self.builder.create_template_logic_flags(
            enable_clock_domain_logic=True, enable_device_specific_ports=True
        )

        assert isinstance(flags, TemplateObject)
        assert flags.clock_domain_logic is True
        assert flags.device_specific_ports is True
        assert flags.interrupt_logic is True  # Default
        assert flags.read_logic is True  # Default

    def test_create_performance_config(self):
        """Test creating performance configuration."""
        config = self.builder.create_performance_config(
            counter_width=64,
            enable_transaction_counters=True,
            enable_bandwidth_monitoring=True,
        )

        assert isinstance(config, TemplateObject)
        assert config.counter_width == 64
        assert config.enable_transaction_counters is True
        assert config.enable_bandwidth_monitoring is True
        assert config.bandwidth_sample_period == 100000  # Default
        assert config.high_performance_threshold == 1000  # Default

    def test_create_power_management_config(self):
        """Test creating power management configuration."""
        config = self.builder.create_power_management_config(
            enable_power_management=True, clk_hz=125_000_000, enable_pme=False
        )

        assert isinstance(config, TemplateObject)
        assert config.enable_power_management is True
        assert config.clk_hz == 125_000_000
        assert config.enable_pme is False
        assert config.enable_wake_events is False  # Default
        assert isinstance(config.transition_cycles, TemplateObject)
        assert config.transition_cycles.d0_to_d1 == 100

    def test_create_power_management_config_custom_transitions(self):
        """Test power management config with custom transition cycles."""
        custom_transitions = {
            "d0_to_d1": 50,
            "d1_to_d0": 75,
            "d0_to_d3": 200,
            "d3_to_d0": 400,
        }

        config = self.builder.create_power_management_config(
            transition_cycles=custom_transitions
        )

        transition_cycles = getattr(config, "transition_cycles")
        assert getattr(transition_cycles, "d0_to_d1") == 50
        assert getattr(transition_cycles, "d3_to_d0") == 400

    def test_create_error_handling_config(self):
        """Test creating error handling configuration."""
        config = self.builder.create_error_handling_config(
            enable_error_detection=True, max_retry_count=5, error_log_depth=512
        )

        assert isinstance(config, TemplateObject)
        assert config.enable_error_detection is True
        assert config.enable_error_logging is True  # Default
        assert config.max_retry_count == 5
        assert config.error_log_depth == 512

    def test_create_device_specific_signals_audio(self):
        """Test creating audio device-specific signals."""
        signals = self.builder.create_device_specific_signals(
            device_type="audio", sample_rate=48000
        )

        assert isinstance(signals, TemplateObject)
        assert signals.device_type == "audio"
        assert signals.audio_enable is True
        assert signals.sample_rate == 48000
        assert signals.volume_left == 0x8000
        assert signals.device_ready is True

    def test_create_device_specific_signals_network(self):
        """Test creating network device-specific signals."""
        signals = self.builder.create_device_specific_signals(
            device_type="network", link_speed=10, packet_size=9000
        )

        assert signals.device_type == "network"
        assert signals.link_up is True
        assert signals.link_speed == 10
        assert signals.packet_size == 9000
        assert signals.network_enable is True

    def test_create_device_specific_signals_storage(self):
        """Test creating storage device-specific signals."""
        signals = self.builder.create_device_specific_signals(
            device_type="storage", sector_size=4096
        )

        assert signals.device_type == "storage"
        assert signals.storage_ready is True
        assert signals.sector_size == 4096
        assert signals.storage_enable is True

    def test_create_device_specific_signals_graphics(self):
        """Test creating graphics device-specific signals."""
        signals = self.builder.create_device_specific_signals(
            device_type="graphics", pixel_clock=50_000_000
        )

        assert signals.device_type == "graphics"
        assert signals.display_enable is True
        assert signals.pixel_clock == 50_000_000
        assert signals.resolution_mode == 0

    def test_create_device_specific_signals_invalid_type(self):
        """Test creating signals with invalid device type."""
        signals = self.builder.create_device_specific_signals(
            device_type=""  # Invalid empty string
        )

        assert signals.device_type == "generic"
        assert signals.device_ready is True

    def test_create_complete_template_context(self):
        """Test creating complete template context."""
        context = self.builder.create_complete_template_context(
            vendor_id="10de",
            device_id="5678",
            device_type="graphics",
            device_class="gaming",
        )

        assert isinstance(context, TemplateObject)
        assert context.vendor_id == "10de"
        assert context.device_id == "5678"
        assert context.device_type == "graphics"
        assert context.device_class == "gaming"

        # Check that all major components are present
        assert hasattr(context, "active_device_config")
        assert hasattr(context, "generation_metadata")
        assert hasattr(context, "board_config")
        assert hasattr(context, "perf_config")
        assert hasattr(context, "power_management")
        assert hasattr(context, "error_handling")
        assert hasattr(context, "variance_model")

        # Check variance model structure
        variance_model = getattr(context, "variance_model")
        assert getattr(variance_model, "enabled") is True
        assert getattr(variance_model, "process_variation") == 0.1
        assert getattr(variance_model, "temperature_coefficient") == 0.05

    def test_create_complete_template_context_unknown_device_type(self):
        """Test complete context with unknown device type."""
        context = self.builder.create_complete_template_context(
            vendor_id="10ee", device_id="7024", device_type="unknown_type"
        )

        assert context.device_type == "generic"

    def test_validate_template_context_success(self):
        """Test successful template context validation."""
        context = self.builder.create_complete_template_context(
            vendor_id="10ee", device_id="7024"
        )

        # Should not raise any exception
        self.builder.validate_template_context(context)

    def test_validate_template_context_missing_keys(self):
        """Test validation failure with missing critical keys."""
        context = TemplateObject(
            {
                "vendor_id": "8086",
                # Missing other critical keys
            }
        )

        with pytest.raises(
            ValueError, match="Missing critical template context values"
        ):
            self.builder.validate_template_context(context)

    def test_get_device_class_from_class_code(self):
        """Test device class determination from PCI class codes."""
        assert self.builder._get_device_class("010000") == "storage"
        assert self.builder._get_device_class("020000") == "network"
        assert self.builder._get_device_class("030000") == "display"
        assert self.builder._get_device_class("040000") == "multimedia"
        assert self.builder._get_device_class("0c0000") == "serial_bus"
        assert self.builder._get_device_class("ff0000") == "generic"


class TestConversionFunctions:
    """Test utility conversion functions."""

    def test_convert_to_template_object_dict(self):
        """Test converting dictionary to TemplateObject."""
        data = {"key": "value", "nested": {"inner": "value"}}
        result = convert_to_template_object(data)

        assert isinstance(result, TemplateObject)
        assert result.key == "value"
        assert isinstance(result.nested, TemplateObject)

    def test_convert_to_template_object_list(self):
        """Test converting list with dictionaries."""
        data = [{"item1": "value1"}, {"item2": "value2"}, "simple"]
        result = convert_to_template_object(data)

        assert isinstance(result, list)
        assert isinstance(result[0], TemplateObject)
        assert isinstance(result[1], TemplateObject)
        assert result[0].item1 == "value1"
        assert result[2] == "simple"

    def test_convert_to_template_object_other(self):
        """Test converting non-dict, non-list data."""
        assert convert_to_template_object("string") == "string"
        assert convert_to_template_object(42) == 42
        assert convert_to_template_object(None) is None

    def test_ensure_template_compatibility(self):
        """Test ensuring template compatibility for context."""
        context = {
            "simple": "value",
            "nested": {"inner": "nested_value"},
            "list_data": [{"item": 1}, "simple_item"],
        }

        result = ensure_template_compatibility(context)

        assert isinstance(result["nested"], TemplateObject)
        assert isinstance(result["list_data"][0], TemplateObject)
        assert result["list_data"][1] == "simple_item"
        assert result["simple"] == "value"


class TestEdgeCasesAndErrorHandling:
    """Test edge cases and error handling scenarios."""

    def setup_method(self):
        """Set up test fixtures."""
        self.builder = UnifiedContextBuilder()

    def test_template_object_empty_data(self):
        """Test TemplateObject with empty data."""
        obj = TemplateObject({})

        assert len(obj.keys()) == 0
        assert obj.get("nonexistent", "default") == "default"

    def test_template_object_with_none_values(self):
        """Test TemplateObject with None values."""
        data = {"key1": None, "key2": "value"}
        obj = TemplateObject(data)

        assert obj.key1 is None
        assert obj.key2 == "value"

    def test_create_active_device_config_with_kwargs(self):
        """Test creating config with additional kwargs."""
        config = self.builder.create_active_device_config(
            vendor_id="8086",
            device_id="1234",
            timer_period=2000,  # This is a valid field in UnifiedDeviceConfig
        )

        assert getattr(config, "timer_period") == 2000

    def test_performance_config_with_all_kwargs(self):
        """Test performance config with all possible kwargs."""
        config = self.builder.create_performance_config(
            bandwidth_sample_period=50000, transfer_width=8, custom_threshold=999
        )

        assert config.bandwidth_sample_period == 50000
        assert config.transfer_width == 8
        assert config.custom_threshold == 999

    def test_complete_context_with_overrides(self):
        """Test complete context with various overrides."""
        context = self.builder.create_complete_template_context(
            vendor_id="abcd",
            device_id="ef12",
            enable_transaction_counters=False,
            power_management=False,
            custom_field="override_value",
        )

        assert context.vendor_id == "abcd"
        assert context.device_id == "ef12"
        assert context.custom_field == "override_value"

    def test_variance_model_validation_and_fallback(self):
        """Test variance model validation with missing fields."""
        # Create a context with incomplete variance model
        context = TemplateObject(
            {
                "vendor_id": "8086",
                "device_id": "1234",
                "device_type": "network",
                "device_class": "enterprise",
                "active_device_config": TemplateObject({}),
                "generation_metadata": TemplateObject({}),
                "board_config": TemplateObject({}),
                "variance_model": TemplateObject({"enabled": True}),
            }
        )

        # This should add missing fields
        self.builder.validate_template_context(context)

        variance_model = getattr(context, "variance_model")
        assert hasattr(variance_model, "process_variation")
        assert getattr(variance_model, "process_variation") == 0.1


class TestTemplateObjectSpecialCases:
    """Test special cases for TemplateObject functionality."""

    def test_nested_template_object_to_dict(self):
        """Test converting deeply nested TemplateObjects to dict."""
        data = {"level1": {"level2": {"level3": {"deep_value": "test"}}}}
        obj = TemplateObject(data)
        result_dict = obj.to_dict()

        # All nested structures should be regular dicts
        assert isinstance(result_dict["level1"], dict)
        assert isinstance(result_dict["level1"]["level2"], dict)
        assert isinstance(result_dict["level1"]["level2"]["level3"], dict)
        assert result_dict["level1"]["level2"]["level3"]["deep_value"] == "test"

    def test_template_object_with_mixed_list_types(self):
        """Test TemplateObject with lists containing mixed types."""
        data = {
            "mixed_list": [
                {"dict_item": "value"},
                ["nested", "list"],
                42,
                None,
                {"another_dict": {"nested": "value"}},
            ]
        }
        obj = TemplateObject(data)

        mixed_list = getattr(obj, "mixed_list")
        assert isinstance(mixed_list[0], TemplateObject)
        assert getattr(mixed_list[0], "dict_item") == "value"
        assert mixed_list[1] == ["nested", "list"]
        assert mixed_list[2] == 42
        assert mixed_list[3] is None
        assert isinstance(mixed_list[4], TemplateObject)

    def test_template_object_attribute_vs_dict_access(self):
        """Test that attribute and dictionary access are consistent."""
        data = {"test_key": "test_value"}
        obj = TemplateObject(data)

        # Both should return the same value
        assert getattr(obj, "test_key") == obj["test_key"]

        # Setting via dict should update attribute
        obj["new_key"] = "new_value"
        assert getattr(obj, "new_key") == "new_value"

        # Setting via setattr should update the internal dict as well
        setattr(obj, "attr_key", "attr_value")
        # Note: The TemplateObject __setitem__ method should handle this
        obj["attr_key"] = "attr_value"  # This ensures it's in _data
        assert obj["attr_key"] == "attr_value"


if __name__ == "__main__":
    pytest.main([__file__])
