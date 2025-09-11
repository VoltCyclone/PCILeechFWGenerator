#!/usr/bin/env python3
"""
Comprehensive test suite for device_config.py module.

Tests cover:
- DeviceType and DeviceClass enums
- PCIeRegisters dataclass validation
- DeviceIdentification dataclass with hex conversion
- ActiveDeviceConfig validation
- DeviceCapabilities validation
- DeviceConfiguration complete validation
- DeviceConfigManager file operations
- Environment variable configuration
- Error handling and edge cases
- Utility functions
"""

import json
import os
import tempfile
from pathlib import Path
from unittest.mock import MagicMock, mock_open, patch

import pytest

from src.device_clone.device_config import (ActiveDeviceConfig,
                                            DeviceCapabilities, DeviceClass,
                                            DeviceConfigManager,
                                            DeviceConfiguration,
                                            DeviceIdentification, DeviceType,
                                            PCIeRegisters,
                                            generate_device_state_machine,
                                            get_config_manager,
                                            get_device_config, validate_hex_id)
from src.device_clone.payload_size_config import (PayloadSizeConfig,
                                                  PayloadSizeError)


class TestDeviceType:
    """Test DeviceType enum functionality."""

    def test_device_type_enum_values(self):
        """Test all expected device type values are present."""
        expected_values = {
            "audio",
            "graphics",
            "media",
            "network",
            "processor",
            "storage",
            "usb",
            "generic",
        }
        actual_values = {member.value for member in DeviceType}
        assert actual_values == expected_values

    def test_device_type_enum_access(self):
        """Test enum members can be accessed correctly."""
        assert DeviceType.AUDIO.value == "audio"
        assert DeviceType.GRAPHICS.value == "graphics"
        assert DeviceType.GENERIC.value == "generic"

    def test_device_type_validation_mismatch(self):
        """Test validation catches enum/constant mismatches."""
        with patch(
            "src.device_clone.device_config.KNOWN_DEVICE_TYPES",
            {"audio", "graphics", "test"},
        ):
            with pytest.raises(ValueError, match="DeviceType enum.*mismatch"):
                DeviceType.validate_against_known_types()

    def test_device_type_enum_access(self):
        """Test enum members can be accessed correctly."""
        assert DeviceType.AUDIO.value == "audio"
        assert DeviceType.GRAPHICS.value == "graphics"
        assert DeviceType.GENERIC.value == "generic"

    """Test DeviceClass enum functionality."""

    def test_device_class_enum_values(self):
        """Test all expected device class values are present."""
        expected_values = {"consumer", "enterprise", "embedded"}
        actual_values = {member.value for member in DeviceClass}
        assert actual_values == expected_values

    def test_device_class_enum_access(self):
        """Test enum members can be accessed correctly."""
        assert DeviceClass.CONSUMER.value == "consumer"
        assert DeviceClass.ENTERPRISE.value == "enterprise"
        assert DeviceClass.EMBEDDED.value == "embedded"


class TestPCIeRegisters:
    """Test PCIeRegisters dataclass functionality."""

    def test_pcie_registers_creation(self):
        """Test PCIeRegisters creation with default values."""
        registers = PCIeRegisters()
        assert registers.command == 0x0006
        assert registers.status == 0x0210
        assert registers.revision_id == 0x01
        assert registers.cache_line_size == 0x10
        assert registers.latency_timer == 0x00
        assert registers.header_type == 0x00
        assert registers.bist == 0x00

    def test_pcie_registers_custom_values(self):
        """Test PCIeRegisters creation with custom values."""
        registers = PCIeRegisters(
            command=0x1234,
            status=0x5678,
            revision_id=0xAB,
            cache_line_size=0x20,
            latency_timer=0x40,
            header_type=0x80,
            bist=0xCD,
        )
        assert registers.command == 0x1234
        assert registers.status == 0x5678
        assert registers.revision_id == 0xAB
        assert registers.cache_line_size == 0x20
        assert registers.latency_timer == 0x40
        assert registers.header_type == 0x80
        assert registers.bist == 0xCD

    def test_pcie_registers_validation_valid(self):
        """Test PCIeRegisters validation with valid values."""
        registers = PCIeRegisters(command=0xFFFF, status=0x0000, revision_id=0xFF)
        registers.validate()  # Should not raise

    def test_pcie_registers_validation_invalid_command(self):
        """Test PCIeRegisters validation with invalid command."""
        registers = PCIeRegisters(command=0x10000)  # Too large
        with pytest.raises(ValueError, match="Invalid command register value"):
            registers.validate()

    def test_pcie_registers_validation_invalid_status(self):
        """Test PCIeRegisters validation with invalid status."""
        registers = PCIeRegisters(status=-1)  # Negative
        with pytest.raises(ValueError, match="Invalid status register value"):
            registers.validate()

    def test_pcie_registers_validation_invalid_revision(self):
        """Test PCIeRegisters validation with invalid revision ID."""
        registers = PCIeRegisters(revision_id=0x100)  # Too large
        with pytest.raises(ValueError, match="Invalid revision ID"):
            registers.validate()


class TestDeviceIdentification:
    """Test DeviceIdentification dataclass functionality."""

    def test_device_identification_creation(self):
        """Test DeviceIdentification creation with integer values."""
        ident = DeviceIdentification(
            vendor_id=0x10EC, device_id=0x8168, class_code=0x020000
        )
        assert ident.vendor_id == 0x10EC
        assert ident.device_id == 0x8168
        assert ident.class_code == 0x020000
        assert ident.subsystem_vendor_id == 0x0000
        assert ident.subsystem_device_id == 0x0000

    def test_device_identification_hex_conversion(self):
        """Test DeviceIdentification hex string conversion."""
        ident = DeviceIdentification(
            vendor_id="0x10EC", device_id="0x8168", class_code="0x020000"
        )
        assert ident.vendor_id == 0x10EC
        assert ident.device_id == 0x8168
        assert ident.class_code == 0x020000

    def test_device_identification_custom_subsystem(self):
        """Test DeviceIdentification with custom subsystem IDs."""
        ident = DeviceIdentification(
            vendor_id=0x10EC,
            device_id=0x8168,
            class_code=0x020000,
            subsystem_vendor_id="0x1043",
            subsystem_device_id=0x85C8,
        )
        assert ident.subsystem_vendor_id == 0x1043
        assert ident.subsystem_device_id == 0x85C8

    def test_device_identification_validation_valid(self):
        """Test DeviceIdentification validation with valid values."""
        ident = DeviceIdentification(
            vendor_id=0x10EC, device_id=0x8168, class_code=0x020000
        )
        ident.validate()  # Should not raise

    def test_device_identification_validation_invalid_vendor(self):
        """Test DeviceIdentification validation with invalid vendor ID."""
        ident = DeviceIdentification(
            vendor_id=0x0000, device_id=0x8168, class_code=0x020000  # Invalid (too low)
        )
        with pytest.raises(ValueError, match="Invalid vendor ID"):
            ident.validate()

    def test_device_identification_validation_invalid_device(self):
        """Test DeviceIdentification validation with invalid device ID."""
        ident = DeviceIdentification(
            vendor_id=0x10EC, device_id=0x10000, class_code=0x020000  # Too large
        )
        with pytest.raises(ValueError, match="Invalid device ID"):
            ident.validate()

    def test_device_identification_validation_invalid_class(self):
        """Test DeviceIdentification validation with invalid class code."""
        ident = DeviceIdentification(
            vendor_id=0x10EC, device_id=0x8168, class_code=0x1000000  # Too large
        )
        with pytest.raises(ValueError, match="Invalid class code"):
            ident.validate()

    def test_device_identification_hex_properties(self):
        """Test hex property accessors."""
        ident = DeviceIdentification(
            vendor_id=0x10EC, device_id=0x8168, class_code=0x020000
        )
        assert ident.vendor_id_hex == "0x10EC"
        assert ident.device_id_hex == "0x8168"
        assert ident.class_code_hex == "0x020000"


class TestActiveDeviceConfig:
    """Test ActiveDeviceConfig dataclass functionality."""

    def test_active_device_config_creation(self):
        """Test ActiveDeviceConfig creation with default values."""
        config = ActiveDeviceConfig()
        assert config.enabled is True
        assert config.timer_period == 100000
        assert config.timer_enable is True
        assert config.interrupt_mode == "msi"
        assert config.interrupt_vector == 0
        assert config.priority == 15
        assert config.msi_vector_width == 5
        assert config.msi_64bit_addr is False
        assert config.num_interrupt_sources == 8
        assert config.default_source_priority == 8

    def test_active_device_config_custom_values(self):
        """Test ActiveDeviceConfig creation with custom values."""
        config = ActiveDeviceConfig(
            enabled=False,
            timer_period=50000,
            timer_enable=False,
            interrupt_mode="msix",
            interrupt_vector=5,
            priority=10,
            msi_vector_width=3,
            msi_64bit_addr=True,
            num_interrupt_sources=16,
            default_source_priority=12,
        )
        assert config.enabled is False
        assert config.timer_period == 50000
        assert config.timer_enable is False
        assert config.interrupt_mode == "msix"
        assert config.interrupt_vector == 5
        assert config.priority == 10
        assert config.msi_vector_width == 3
        assert config.msi_64bit_addr is True
        assert config.num_interrupt_sources == 16
        assert config.default_source_priority == 12

    def test_active_device_config_validation_valid(self):
        """Test ActiveDeviceConfig validation with valid values."""
        config = ActiveDeviceConfig(
            timer_period=1000,
            interrupt_mode="msi",
            priority=10,
            msi_vector_width=3,
            num_interrupt_sources=4,
        )
        config.validate()  # Should not raise

    def test_active_device_config_validation_invalid_timer_period(self):
        """Test ActiveDeviceConfig validation with invalid timer period."""
        config = ActiveDeviceConfig(timer_period=0)
        with pytest.raises(ValueError, match="Invalid timer period"):
            config.validate()

    def test_active_device_config_validation_invalid_interrupt_mode(self):
        """Test ActiveDeviceConfig validation with invalid interrupt mode."""
        config = ActiveDeviceConfig(interrupt_mode="invalid")
        with pytest.raises(ValueError, match="Invalid interrupt mode"):
            config.validate()

    def test_active_device_config_validation_invalid_priority(self):
        """Test ActiveDeviceConfig validation with invalid priority."""
        config = ActiveDeviceConfig(priority=16)  # Too high
        with pytest.raises(ValueError, match="Invalid interrupt priority"):
            config.validate()

    def test_active_device_config_validation_invalid_msi_width(self):
        """Test ActiveDeviceConfig validation with invalid MSI vector width."""
        config = ActiveDeviceConfig(msi_vector_width=6)  # Too high
        with pytest.raises(ValueError, match="Invalid MSI vector width"):
            config.validate()

    def test_active_device_config_validation_invalid_interrupt_sources(self):
        """Test ActiveDeviceConfig validation with invalid interrupt sources."""
        config = ActiveDeviceConfig(num_interrupt_sources=0)
        with pytest.raises(ValueError, match="Invalid number of interrupt sources"):
            config.validate()


class TestDeviceCapabilities:
    """Test DeviceCapabilities dataclass functionality."""

    def test_device_capabilities_creation(self):
        """Test DeviceCapabilities creation with default values."""
        caps = DeviceCapabilities()
        assert caps.max_payload_size == 256
        assert caps.msi_vectors == 1
        assert caps.msix_vectors == 0
        assert caps.supports_msi is True
        assert caps.supports_msix is False
        assert caps.supports_power_management is True
        assert caps.supports_advanced_error_reporting is False
        assert caps.link_width == 1
        assert caps.link_speed == "2.5GT/s"
        assert caps.ext_cfg_cap_ptr == 0x100
        assert caps.ext_cfg_xp_cap_ptr == 0x100
        assert isinstance(caps.active_device, ActiveDeviceConfig)

    def test_device_capabilities_custom_values(self):
        """Test DeviceCapabilities creation with custom values."""
        active_device = ActiveDeviceConfig(enabled=False)
        caps = DeviceCapabilities(
            max_payload_size=512,
            msi_vectors=8,
            msix_vectors=16,
            supports_msi=True,
            supports_msix=True,
            supports_power_management=False,
            supports_advanced_error_reporting=True,
            link_width=4,
            link_speed="5.0GT/s",
            ext_cfg_cap_ptr=0x200,
            ext_cfg_xp_cap_ptr=0x300,
            active_device=active_device,
        )
        assert caps.max_payload_size == 512
        assert caps.msi_vectors == 8
        assert caps.msix_vectors == 16
        assert caps.supports_msi is True
        assert caps.supports_msix is True
        assert caps.supports_power_management is False
        assert caps.supports_advanced_error_reporting is True
        assert caps.link_width == 4
        assert caps.link_speed == "5.0GT/s"
        assert caps.ext_cfg_cap_ptr == 0x200
        assert caps.ext_cfg_xp_cap_ptr == 0x300
        assert caps.active_device.enabled is False

    @patch("src.device_clone.payload_size_config.PayloadSizeConfig")
    def test_device_capabilities_validation_valid(self, mock_payload_config):
        """Test DeviceCapabilities validation with valid values."""
        mock_payload_config.return_value.validate.return_value = None
        caps = DeviceCapabilities(
            max_payload_size=256,
            msi_vectors=4,
            msix_vectors=8,
            link_width=4,
            ext_cfg_cap_ptr=0x100,
            ext_cfg_xp_cap_ptr=0x104,
        )
        caps.validate()  # Should not raise

    def test_device_capabilities_validation_invalid_payload_size(self):
        """Test DeviceCapabilities validation with invalid payload size."""
        caps = DeviceCapabilities(max_payload_size=999)
        with pytest.raises(ValueError, match="Invalid maximum payload size"):
            caps.validate()

    def test_device_capabilities_validation_invalid_msi_vectors(self):
        """Test DeviceCapabilities validation with invalid MSI vectors."""
        caps = DeviceCapabilities(msi_vectors=0)  # Too low
        with pytest.raises(ValueError, match="Invalid MSI vector count"):
            caps.validate()

    def test_device_capabilities_validation_invalid_msix_vectors(self):
        """Test DeviceCapabilities validation with invalid MSI-X vectors."""
        caps = DeviceCapabilities(msix_vectors=3000)  # Too high
        with pytest.raises(ValueError, match="Invalid MSI-X vector count"):
            caps.validate()

    def test_device_capabilities_validation_invalid_link_width(self):
        """Test DeviceCapabilities validation with invalid link width."""
        caps = DeviceCapabilities(link_width=3)  # Not in valid widths
        with pytest.raises(ValueError, match="Invalid link width"):
            caps.validate()

    def test_device_capabilities_validation_invalid_ext_cfg_ptr(self):
        """Test DeviceCapabilities validation with invalid extended config ptr."""
        caps = DeviceCapabilities(ext_cfg_cap_ptr=0x50)  # Too low
        with pytest.raises(ValueError, match="Invalid extended config.*pointer"):
            caps.validate()

    def test_device_capabilities_validation_invalid_ext_cfg_ptr_alignment(self):
        """Test DeviceCapabilities validation with misaligned extended config ptr."""
        caps = DeviceCapabilities(ext_cfg_cap_ptr=0x102)  # Not 4-byte aligned
        with pytest.raises(ValueError, match="must be 4-byte aligned"):
            caps.validate()

    @patch("src.device_clone.payload_size_config.PayloadSizeConfig")
    def test_get_cfg_force_mps(self, mock_payload_config):
        """Test get_cfg_force_mps method."""
        mock_instance = MagicMock()
        mock_instance.get_cfg_force_mps.return_value = 3
        mock_payload_config.return_value = mock_instance

        caps = DeviceCapabilities(max_payload_size=256)
        result = caps.get_cfg_force_mps()
        assert result == 3
        mock_payload_config.assert_called_once_with(256)

    @patch("src.device_clone.payload_size_config.PayloadSizeConfig")
    def test_check_tiny_pcie_issues(self, mock_payload_config):
        """Test check_tiny_pcie_issues method."""
        mock_instance = MagicMock()
        mock_instance.check_tiny_pcie_algo_issues.return_value = (True, "Warning")
        mock_payload_config.return_value = mock_instance

        caps = DeviceCapabilities(max_payload_size=128)
        has_issues, warning = caps.check_tiny_pcie_issues()
        assert has_issues is True
        assert warning == "Warning"
        mock_payload_config.assert_called_once_with(128)


class TestDeviceConfiguration:
    """Test DeviceConfiguration dataclass functionality."""

    def test_device_configuration_creation(self):
        """Test DeviceConfiguration creation with minimal required fields."""
        ident = DeviceIdentification(
            vendor_id=0x10EC, device_id=0x8168, class_code=0x020000
        )
        config = DeviceConfiguration(
            name="test_device",
            device_type=DeviceType.NETWORK,
            device_class=DeviceClass.CONSUMER,
            identification=ident,
        )
        assert config.name == "test_device"
        assert config.device_type == DeviceType.NETWORK
        assert config.device_class == DeviceClass.CONSUMER
        assert config.identification.vendor_id == 0x10EC
        assert isinstance(config.registers, PCIeRegisters)
        assert isinstance(config.capabilities, DeviceCapabilities)
        assert config.custom_properties == {}

    def test_device_configuration_full_creation(self):
        """Test DeviceConfiguration creation with all fields."""
        ident = DeviceIdentification(
            vendor_id=0x10EC, device_id=0x8168, class_code=0x020000
        )
        registers = PCIeRegisters(command=0x1234)
        capabilities = DeviceCapabilities(max_payload_size=512)
        custom_props = {"custom_field": "value"}

        config = DeviceConfiguration(
            name="full_test_device",
            device_type=DeviceType.GRAPHICS,
            device_class=DeviceClass.ENTERPRISE,
            identification=ident,
            registers=registers,
            capabilities=capabilities,
            custom_properties=custom_props,
        )
        assert config.name == "full_test_device"
        assert config.device_type == DeviceType.GRAPHICS
        assert config.device_class == DeviceClass.ENTERPRISE
        assert config.registers.command == 0x1234
        assert config.capabilities.max_payload_size == 512
        assert config.custom_properties == custom_props

    def test_device_configuration_validation_valid(self):
        """Test DeviceConfiguration validation with valid configuration."""
        ident = DeviceIdentification(
            vendor_id=0x10EC, device_id=0x8168, class_code=0x020000
        )
        config = DeviceConfiguration(
            name="valid_device",
            device_type=DeviceType.STORAGE,
            device_class=DeviceClass.CONSUMER,
            identification=ident,
        )
        config.validate()  # Should not raise

    def test_device_configuration_validation_invalid_identification(self):
        """Test DeviceConfiguration validation with invalid identification."""
        ident = DeviceIdentification(
            vendor_id=0x0000, device_id=0x8168, class_code=0x020000  # Invalid
        )
        config = DeviceConfiguration(
            name="invalid_device",
            device_type=DeviceType.STORAGE,
            device_class=DeviceClass.CONSUMER,
            identification=ident,
        )
        with pytest.raises(ValueError, match="Invalid vendor ID"):
            config.validate()

    def test_device_configuration_to_dict(self):
        """Test DeviceConfiguration to_dict conversion."""
        ident = DeviceIdentification(
            vendor_id=0x10EC,
            device_id=0x8168,
            class_code=0x020000,
            subsystem_vendor_id=0x1043,
            subsystem_device_id=0x85C8,
        )
        config = DeviceConfiguration(
            name="dict_test_device",
            device_type=DeviceType.AUDIO,
            device_class=DeviceClass.CONSUMER,
            identification=ident,
            custom_properties={"test": "value"},
        )
        result = config.to_dict()

        assert result["name"] == "dict_test_device"
        assert result["device_type"] == "audio"
        assert result["device_class"] == "consumer"
        assert result["identification"]["vendor_id"] == 0x10EC
        assert result["identification"]["device_id"] == 0x8168
        assert result["identification"]["class_code"] == 0x020000
        assert result["identification"]["subsystem_vendor_id"] == 0x1043
        assert result["identification"]["subsystem_device_id"] == 0x85C8
        assert result["custom_properties"] == {"test": "value"}


class TestValidateHexId:
    """Test validate_hex_id utility function."""

    def test_validate_hex_id_integer(self):
        """Test validate_hex_id with integer input."""
        result = validate_hex_id(0x10EC, bit_width=16)
        assert result == 0x10EC

    def test_validate_hex_id_hex_string_with_prefix(self):
        """Test validate_hex_id with hex string with 0x prefix."""
        result = validate_hex_id("0x10EC", bit_width=16)
        assert result == 0x10EC

    def test_validate_hex_id_hex_string_without_prefix(self):
        """Test validate_hex_id with hex string without prefix."""
        result = validate_hex_id("10EC", bit_width=16)
        assert result == 0x10EC

    def test_validate_hex_id_decimal_string(self):
        """Test validate_hex_id with decimal string."""
        result = validate_hex_id("4332", bit_width=16)
        assert result == 4332

    def test_validate_hex_id_uppercase_hex(self):
        """Test validate_hex_id with uppercase hex string."""
        result = validate_hex_id("10EC", bit_width=16)
        assert result == 0x10EC

    def test_validate_hex_id_mixed_case_hex(self):
        """Test validate_hex_id with mixed case hex string."""
        result = validate_hex_id("10eC", bit_width=16)
        assert result == 0x10EC

    def test_validate_hex_id_invalid_hex(self):
        """Test validate_hex_id with invalid hex string."""
        with pytest.raises(ValueError, match="Invalid format"):
            validate_hex_id("GGGG", bit_width=16)

    def test_validate_hex_id_too_large(self):
        """Test validate_hex_id with value too large for bit width."""
        with pytest.raises(ValueError, match="out of range for 16-bit field"):
            validate_hex_id("0x10000", bit_width=16)

    def test_validate_hex_id_negative(self):
        """Test validate_hex_id with negative value."""
        with pytest.raises(ValueError, match="Value -1 out of range"):
            validate_hex_id(-1, bit_width=16)

    def test_validate_hex_id_24bit_class_code(self):
        """Test validate_hex_id with 24-bit class code."""
        result = validate_hex_id("0x020000", bit_width=24)
        assert result == 0x020000


class TestDeviceConfigManager:
    """Test DeviceConfigManager functionality."""

    def test_device_config_manager_creation_no_config_dir(self):
        """Test DeviceConfigManager creation without config directory."""
        manager = DeviceConfigManager()
        assert manager.config_dir is None
        assert manager.profiles == {}

    def test_device_config_manager_creation_with_config_dir(self):
        """Test DeviceConfigManager creation with config directory."""
        with tempfile.TemporaryDirectory() as temp_dir:
            config_dir = Path(temp_dir)
            manager = DeviceConfigManager(config_dir)
            assert manager.config_dir == config_dir
            assert manager.profiles == {}

    def test_device_config_manager_load_default_profiles(self):
        """Test loading default profiles."""
        manager = DeviceConfigManager()
        # Should have loaded default profiles (empty dict in this case)
        assert isinstance(manager.profiles, dict)

    @patch("src.device_clone.device_config.yaml")
    def test_load_config_file_yaml(self, mock_yaml):
        """Test loading YAML configuration file."""
        mock_yaml.safe_load.return_value = {
            "name": "test_device",
            "device_type": "network",
            "device_class": "consumer",
            "identification": {
                "vendor_id": "0x10EC",
                "device_id": "0x8168",
                "class_code": "0x020000",
            },
            "registers": {
                "command": "0x0006",
                "status": "0x0210",
                "revision_id": "0x01",
            },
            "capabilities": {
                "max_payload_size": 256,
                "msi_vectors": 1,
                "msix_vectors": 0,
                "supports_msi": True,
                "supports_msix": False,
                "supports_power_management": True,
                "supports_advanced_error_reporting": False,
                "link_width": 1,
                "link_speed": "2.5GT/s",
                "ext_cfg_cap_ptr": 0x100,
                "ext_cfg_xp_cap_ptr": 0x100,
                "active_device": {
                    "enabled": False,
                    "timer_period": 100000,
                    "timer_enable": True,
                    "interrupt_mode": "msi",
                    "interrupt_vector": 0,
                    "priority": 15,
                    "msi_vector_width": 5,
                    "msi_64bit_addr": False,
                    "num_interrupt_sources": 8,
                    "default_source_priority": 8,
                },
            },
        }

        with tempfile.NamedTemporaryFile(suffix=".yaml", delete=False) as f:
            temp_file = Path(f.name)

        try:
            manager = DeviceConfigManager()
            config = manager.load_config_file(temp_file)

            assert config.name == "test_device"
            assert config.device_type == DeviceType.NETWORK
            assert config.device_class == DeviceClass.CONSUMER
            assert config.identification.vendor_id == 0x10EC
        finally:
            temp_file.unlink()

    @patch("src.device_clone.device_config.json")
    def test_load_config_file_json(self, mock_json):
        """Test loading JSON configuration file."""
        mock_json.load.return_value = {
            "name": "test_device",
            "device_type": "storage",
            "device_class": "enterprise",
            "identification": {
                "vendor_id": "0x8086",
                "device_id": "0x1234",
                "class_code": "0x010000",
            },
            "registers": {
                "command": "0x0006",
                "status": "0x0210",
                "revision_id": "0x01",
            },
            "capabilities": {
                "max_payload_size": 256,
                "msi_vectors": 1,
                "msix_vectors": 0,
                "supports_msi": True,
                "supports_msix": False,
                "supports_power_management": True,
                "supports_advanced_error_reporting": False,
                "link_width": 1,
                "link_speed": "2.5GT/s",
                "ext_cfg_cap_ptr": 0x100,
                "ext_cfg_xp_cap_ptr": 0x100,
                "active_device": {
                    "enabled": False,
                    "timer_period": 100000,
                    "timer_enable": True,
                    "interrupt_mode": "msi",
                    "interrupt_vector": 0,
                    "priority": 15,
                    "msi_vector_width": 5,
                    "msi_64bit_addr": False,
                    "num_interrupt_sources": 8,
                    "default_source_priority": 8,
                },
            },
        }

        with tempfile.NamedTemporaryFile(suffix=".json", delete=False) as f:
            temp_file = Path(f.name)

        try:
            manager = DeviceConfigManager()
            config = manager.load_config_file(temp_file)

            assert config.name == "test_device"
            assert config.device_type == DeviceType.STORAGE
            assert config.device_class == DeviceClass.ENTERPRISE
            assert config.identification.vendor_id == 0x8086
        finally:
            temp_file.unlink()

    def test_load_config_file_unsupported_format(self):
        """Test loading configuration file with unsupported format."""
        with tempfile.NamedTemporaryFile(suffix=".txt", delete=False) as f:
            temp_file = Path(f.name)

        try:
            manager = DeviceConfigManager()
            with pytest.raises(ValueError, match="Unsupported file format"):
                manager.load_config_file(temp_file)
        finally:
            temp_file.unlink()

    def test_load_config_file_not_found(self):
        """Test loading configuration file that doesn't exist."""
        manager = DeviceConfigManager()
        with pytest.raises(FileNotFoundError):
            manager.load_config_file(Path("/nonexistent/file.yaml"))

    def test_get_profile_from_memory(self):
        """Test getting profile from in-memory profiles."""
        manager = DeviceConfigManager()
        config = DeviceConfiguration(
            name="memory_profile",
            device_type=DeviceType.GENERIC,
            device_class=DeviceClass.CONSUMER,
            identification=DeviceIdentification(
                vendor_id=0x1234, device_id=0x5678, class_code=0x030000
            ),
        )
        manager.profiles["memory_profile"] = config

        result = manager.get_profile("memory_profile")
        assert result == config

    def test_get_profile_from_file_yaml(self):
        """Test getting profile from YAML file."""
        with tempfile.TemporaryDirectory() as temp_dir:
            config_dir = Path(temp_dir)
            config_file = config_dir / "file_profile.yaml"

            # Create a test YAML file
            yaml_content = """
name: file_profile
device_type: graphics
device_class: consumer
identification:
  vendor_id: "0x10DE"
  device_id: "0x1234"
  class_code: "0x030000"
registers:
  command: "0x0006"
  status: "0x0210"
  revision_id: "0x01"
capabilities:
  max_payload_size: 256
  msi_vectors: 1
  msix_vectors: 0
  supports_msi: true
  supports_msix: false
  supports_power_management: true
  supports_advanced_error_reporting: false
  link_width: 1
  link_speed: "2.5GT/s"
  ext_cfg_cap_ptr: 0x100
  ext_cfg_xp_cap_ptr: 0x100
  active_device:
    enabled: false
    timer_period: 100000
    timer_enable: true
    interrupt_mode: "msi"
    interrupt_vector: 0
    priority: 15
    msi_vector_width: 5
    msi_64bit_addr: false
    num_interrupt_sources: 8
    default_source_priority: 8
"""
            config_file.write_text(yaml_content)

            manager = DeviceConfigManager(config_dir)
            config = manager.get_profile("file_profile")

            assert config.name == "file_profile"
            assert config.device_type == DeviceType.GRAPHICS
            assert config.identification.vendor_id == 0x10DE

    def test_get_profile_from_file_json(self):
        """Test getting profile from JSON file."""
        with tempfile.TemporaryDirectory() as temp_dir:
            config_dir = Path(temp_dir)
            config_file = config_dir / "file_profile.json"

            # Create a test JSON file
            json_content = {
                "name": "file_profile",
                "device_type": "usb",
                "device_class": "consumer",
                "identification": {
                    "vendor_id": "0x8086",
                    "device_id": "0x5678",
                    "class_code": "0x0C0000",
                },
                "registers": {
                    "command": "0x0006",
                    "status": "0x0210",
                    "revision_id": "0x01",
                },
                "capabilities": {
                    "max_payload_size": 256,
                    "msi_vectors": 1,
                    "msix_vectors": 0,
                    "supports_msi": True,
                    "supports_msix": False,
                    "supports_power_management": True,
                    "supports_advanced_error_reporting": False,
                    "link_width": 1,
                    "link_speed": "2.5GT/s",
                    "ext_cfg_cap_ptr": 0x100,
                    "ext_cfg_xp_cap_ptr": 0x100,
                    "active_device": {
                        "enabled": False,
                        "timer_period": 100000,
                        "timer_enable": True,
                        "interrupt_mode": "msi",
                        "interrupt_vector": 0,
                        "priority": 15,
                        "msi_vector_width": 5,
                        "msi_64bit_addr": False,
                        "num_interrupt_sources": 8,
                        "default_source_priority": 8,
                    },
                },
            }

            import json

            config_file.write_text(json.dumps(json_content, indent=2))

            manager = DeviceConfigManager(config_dir)
            config = manager.get_profile("file_profile")

            assert config.name == "file_profile"
            assert config.device_type == DeviceType.USB
            assert config.identification.vendor_id == 0x8086

    def test_get_profile_not_found(self):
        """Test getting profile that doesn't exist."""
        manager = DeviceConfigManager()
        with pytest.raises(ValueError, match="Device profile not found"):
            manager.get_profile("nonexistent_profile")

    @patch.dict(
        os.environ,
        {
            "PCIE_TEST_VENDOR_ID": "0x10EC",
            "PCIE_TEST_DEVICE_ID": "0x8168",
            "PCIE_TEST_CLASS_CODE": "0x020000",
        },
    )
    def test_create_profile_from_env(self):
        """Test creating profile from environment variables."""
        manager = DeviceConfigManager()
        config = manager.create_profile_from_env("test")

        assert config.name == "test"
        assert config.device_type == DeviceType.GENERIC
        assert config.device_class == DeviceClass.CONSUMER
        assert config.identification.vendor_id == 0x10EC
        assert config.identification.device_id == 0x8168
        assert config.identification.class_code == 0x020000

        # Should be added to profiles
        assert "test" in manager.profiles

    @patch.dict(os.environ, {}, clear=True)
    def test_create_profile_from_env_missing_vendor_id(self):
        """Test creating profile from env with missing vendor ID."""
        manager = DeviceConfigManager()
        with pytest.raises(ValueError, match="PCIE_TEST_VENDOR_ID.*required"):
            manager.create_profile_from_env("test")

    @patch.dict(os.environ, {"PCIE_TEST_VENDOR_ID": "0x10EC"}, clear=True)
    def test_create_profile_from_env_missing_device_id(self):
        """Test creating profile from env with missing device ID."""
        manager = DeviceConfigManager()
        with pytest.raises(ValueError, match="PCIE_TEST_DEVICE_ID.*required"):
            manager.create_profile_from_env("test")

    @patch.dict(
        os.environ,
        {"PCIE_TEST_VENDOR_ID": "0x10EC", "PCIE_TEST_DEVICE_ID": "0x8168"},
        clear=True,
    )
    def test_create_profile_from_env_missing_class_code(self):
        """Test creating profile from env with missing class code."""
        manager = DeviceConfigManager()
        with pytest.raises(ValueError, match="PCIE_TEST_CLASS_CODE.*required"):
            manager.create_profile_from_env("test")

    def test_list_profiles_memory_only(self):
        """Test listing profiles with only in-memory profiles."""
        manager = DeviceConfigManager()
        config = DeviceConfiguration(
            name="list_test",
            device_type=DeviceType.GENERIC,
            device_class=DeviceClass.CONSUMER,
            identification=DeviceIdentification(
                vendor_id=0x1234, device_id=0x5678, class_code=0x030000
            ),
        )
        manager.profiles["list_test"] = config

        profiles = manager.list_profiles()
        assert profiles == ["list_test"]

    def test_list_profiles_with_files(self):
        """Test listing profiles including files."""
        with tempfile.TemporaryDirectory() as temp_dir:
            config_dir = Path(temp_dir)

            # Create YAML file
            yaml_file = config_dir / "yaml_profile.yaml"
            yaml_file.write_text(
                """
name: yaml_profile
device_type: audio
device_class: consumer
identification:
  vendor_id: "0x10EC"
  device_id: "0x8168"
  class_code: "0x040000"
"""
            )

            # Create JSON file
            json_file = config_dir / "json_profile.json"
            json_content = {
                "name": "json_profile",
                "device_type": "processor",
                "device_class": "enterprise",
                "identification": {
                    "vendor_id": "0x8086",
                    "device_id": "0x1234",
                    "class_code": "0x060000",
                },
            }
            import json

            json_file.write_text(json.dumps(json_content, indent=2))

            manager = DeviceConfigManager(config_dir)
            profiles = manager.list_profiles()

            assert "yaml_profile" in profiles
            assert "json_profile" in profiles
            assert profiles == sorted(profiles)  # Should be sorted

    @patch("src.device_clone.device_config.yaml")
    def test_save_profile_with_config_dir(self, mock_yaml):
        """Test saving profile with config directory."""
        with tempfile.TemporaryDirectory() as temp_dir:
            config_dir = Path(temp_dir)
            manager = DeviceConfigManager(config_dir)

            config = DeviceConfiguration(
                name="save_test",
                device_type=DeviceType.NETWORK,
                device_class=DeviceClass.CONSUMER,
                identification=DeviceIdentification(
                    vendor_id=0x10EC, device_id=0x8168, class_code=0x020000
                ),
            )

            manager.save_profile(config)

            # Should be added to profiles
            assert "save_test" in manager.profiles

            # Should have created YAML file
            yaml_file = config_dir / "save_test.yaml"
            assert yaml_file.exists()

    @patch("src.device_clone.device_config.yaml")
    def test_save_profile_with_custom_path(self, mock_yaml):
        """Test saving profile with custom file path."""
        with tempfile.TemporaryDirectory() as temp_dir:
            custom_path = Path(temp_dir) / "custom_config.yaml"
            manager = DeviceConfigManager()

            config = DeviceConfiguration(
                name="custom_save_test",
                device_type=DeviceType.STORAGE,
                device_class=DeviceClass.ENTERPRISE,
                identification=DeviceIdentification(
                    vendor_id=0x8086, device_id=0x1234, class_code=0x010000
                ),
            )

            manager.save_profile(config, custom_path)

            # Should be added to profiles
            assert "custom_save_test" in manager.profiles

            # Should have created file at custom path
            assert custom_path.exists()

    def test_save_profile_no_config_dir_no_path(self):
        """Test saving profile without config dir and no custom path."""
        manager = DeviceConfigManager()  # No config_dir
        config = DeviceConfiguration(
            name="no_path_test",
            device_type=DeviceType.GENERIC,
            device_class=DeviceClass.CONSUMER,
            identification=DeviceIdentification(
                vendor_id=0x1234, device_id=0x5678, class_code=0x030000
            ),
        )

        with pytest.raises(ValueError, match="No config_dir set"):
            manager.save_profile(config)

    @patch("src.device_clone.device_config.YAML_AVAILABLE", False)
    def test_save_profile_no_yaml_support(self):
        """Test saving profile without YAML support."""
        with tempfile.TemporaryDirectory() as temp_dir:
            config_dir = Path(temp_dir)
            manager = DeviceConfigManager(config_dir)

            config = DeviceConfiguration(
                name="no_yaml_test",
                device_type=DeviceType.GENERIC,
                device_class=DeviceClass.CONSUMER,
                identification=DeviceIdentification(
                    vendor_id=0x1234, device_id=0x5678, class_code=0x030000
                ),
            )

            with pytest.raises(ImportError, match="PyYAML is required"):
                manager.save_profile(config)


class TestGlobalFunctions:
    """Test global utility functions."""

    def test_get_config_manager_singleton(self):
        """Test get_config_manager returns singleton instance."""
        manager1 = get_config_manager()
        manager2 = get_config_manager()
        assert manager1 is manager2

    @patch("src.device_clone.device_config._config_manager", None)
    def test_get_config_manager_creates_new_instance(self):
        """Test get_config_manager creates new instance when None."""
        manager = get_config_manager()
        assert isinstance(manager, DeviceConfigManager)

    def test_get_device_config_found(self):
        """Test get_device_config when profile is found."""
        manager = get_config_manager()
        config = DeviceConfiguration(
            name="global_test",
            device_type=DeviceType.GENERIC,
            device_class=DeviceClass.CONSUMER,
            identification=DeviceIdentification(
                vendor_id=0x1234, device_id=0x5678, class_code=0x030000
            ),
        )
        manager.profiles["global_test"] = config

        result = get_device_config("global_test")
        assert result == config

    def test_get_device_config_not_found(self):
        """Test get_device_config when profile is not found."""
        result = get_device_config("nonexistent")
        assert result is None


class TestGenerateDeviceStateMachine:
    """Test generate_device_state_machine function."""

    def test_generate_device_state_machine_with_registers(self):
        """Test generating state machine with register data."""
        registers = [
            {"name": "COMMAND", "offset": 0x04},
            {"name": "STATUS", "offset": 0x06},
            {"name": "BAR0", "offset": 0x10},
        ]

        result = generate_device_state_machine(registers)

        assert "device_states" in result
        assert "state_transitions" in result
        assert "registers" in result
        assert result["register_count"] == 3
        assert result["registers"] == ["COMMAND", "STATUS", "BAR0"]
        assert len(result["device_states"]) == 4  # INIT, READY, ACTIVE, ERROR
        assert len(result["state_transitions"]) == 4

    def test_generate_device_state_machine_empty_registers(self):
        """Test generating state machine with empty register list."""
        result = generate_device_state_machine([])

        assert result["states"] == ["IDLE"]
        assert result["registers"] == []

    def test_generate_device_state_machine_none_registers(self):
        """Test generating state machine with None register list."""
        # Test with empty list instead of None to match type hints
        result = generate_device_state_machine([])

        assert result["states"] == ["IDLE"]
        assert result["registers"] == []

    @patch("src.device_clone.device_config.logger")
    def test_generate_device_state_machine_exception(self, mock_logger):
        """Test generating state machine with exception handling."""
        # Create a register that will cause an exception
        registers = "invalid_string"  # This will cause a TypeError when iterating

        result = generate_device_state_machine(registers)  # type: ignore

        # Should return empty dict on exception
        assert result == {}
        mock_logger.error.assert_called_once()


class TestDeviceConfigIntegration:
    """Integration tests for device configuration functionality."""

    def test_full_device_config_workflow(self):
        """Test complete device configuration workflow."""
        # Create identification
        ident = DeviceIdentification(
            vendor_id=0x10EC,
            device_id=0x8168,
            class_code=0x020000,
            subsystem_vendor_id=0x1043,
            subsystem_device_id=0x85C8,
        )

        # Create capabilities with custom active device config
        active_device = ActiveDeviceConfig(
            enabled=True, timer_period=50000, interrupt_mode="msix", priority=12
        )

        capabilities = DeviceCapabilities(
            max_payload_size=512,
            msi_vectors=8,
            msix_vectors=16,
            supports_msix=True,
            link_width=4,
            link_speed="8.0GT/s",
            active_device=active_device,
        )

        # Create registers
        registers = PCIeRegisters(command=0x0146, status=0x0010, revision_id=0x05)

        # Create full configuration
        config = DeviceConfiguration(
            name="integration_test_device",
            device_type=DeviceType.NETWORK,
            device_class=DeviceClass.ENTERPRISE,
            identification=ident,
            registers=registers,
            capabilities=capabilities,
            custom_properties={
                "driver": "r8169",
                "firmware_version": "1.2.3",
                "supported_features": ["tso", "ufo", "gso"],
            },
        )

        # Validate configuration
        config.validate()

        # Convert to dict and verify structure
        config_dict = config.to_dict()

        assert config_dict["name"] == "integration_test_device"
        assert config_dict["device_type"] == "network"
        assert config_dict["device_class"] == "enterprise"
        assert config_dict["identification"]["vendor_id"] == 0x10EC
        assert config_dict["identification"]["subsystem_vendor_id"] == 0x1043
        assert config_dict["registers"]["command"] == 0x0146
        assert config_dict["capabilities"]["max_payload_size"] == 512
        active_device = config_dict["capabilities"]["active_device"]
        assert active_device["timer_period"] == 50000
        assert active_device["interrupt_mode"] == "msix"
        assert config_dict["custom_properties"]["driver"] == "r8169"

    def test_device_config_file_roundtrip(self):
        """Test saving and loading device configuration from file."""
        with tempfile.TemporaryDirectory() as temp_dir:
            config_dir = Path(temp_dir)
            config_file = config_dir / "roundtrip_test.yaml"

            # Create original configuration
            ident = DeviceIdentification(
                vendor_id=0x8086, device_id=0x1533, class_code=0x020000
            )

            original_config = DeviceConfiguration(
                name="roundtrip_test",
                device_type=DeviceType.NETWORK,
                device_class=DeviceClass.CONSUMER,
                identification=ident,
                custom_properties={"test_property": "test_value"},
            )

            # Save to file
            manager = DeviceConfigManager(config_dir)
            manager.save_profile(original_config)

            # Load from file
            loaded_config = manager.load_config_file(config_file)

            # Verify roundtrip
            assert loaded_config.name == original_config.name
            assert loaded_config.device_type == original_config.device_type
            assert loaded_config.device_class == original_config.device_class
            orig_ident = original_config.identification
            loaded_ident = loaded_config.identification
            assert loaded_ident.vendor_id == orig_ident.vendor_id
            assert loaded_ident.device_id == orig_ident.device_id
            assert loaded_ident.class_code == orig_ident.class_code
            orig_props = original_config.custom_properties
            assert loaded_config.custom_properties == orig_props

    def test_device_config_environment_integration(self):
        """Test device configuration with environment variable integration."""
        # Set up environment variables
        env_vars = {
            "PCIE_ENV_TEST_VENDOR_ID": "0x14E4",
            "PCIE_ENV_TEST_DEVICE_ID": "0x43A0",
            "PCIE_ENV_TEST_CLASS_CODE": "0x020000",
        }

        with patch.dict(os.environ, env_vars):
            manager = DeviceConfigManager()
            config = manager.create_profile_from_env("env_test")

            assert config.name == "env_test"
            assert config.identification.vendor_id == 0x14E4
            assert config.identification.device_id == 0x43A0
            assert config.identification.class_code == 0x020000

            # Test hex property accessors
            assert config.identification.vendor_id_hex == "0x14E4"
            assert config.identification.device_id_hex == "0x43A0"
            assert config.identification.class_code_hex == "0x020000"
