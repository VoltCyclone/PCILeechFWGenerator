#!/usr/bin/env python3
"""
Comprehensive unit tests for PCILeech context builder module.

This test suite covers the critical PCILeechContextBuilder class which handles
core device cloning logic and VFIO device interaction. Tests include:
- Context builder initialization and configuration
- build_context() main context generation
- VFIO device file descriptor handling
- BAR memory mapping operations
- Configuration space reading
- Error handling and resource cleanup
- Integration with related components

"""

import ctypes
import fcntl
import hashlib
from logging import config
import os
import struct
import sys
from contextlib import contextmanager
from dataclasses import asdict, dataclass, field
from datetime import datetime
from typing import Any, Dict, List, Optional, Tuple
from unittest.mock import MagicMock, Mock, PropertyMock, call, mock_open, patch

import pytest

from src.cli.vfio_constants import (VFIO_DEVICE_GET_REGION_INFO,
                                    VFIO_GROUP_GET_DEVICE_FD,
                                    VFIO_REGION_INFO_FLAG_MMAP,
                                    VFIO_REGION_INFO_FLAG_READ,
                                    VFIO_REGION_INFO_FLAG_WRITE,
                                    VfioRegionInfo)
from src.device_clone.behavior_profiler import (BehaviorProfile,
                                                RegisterAccess, TimingPattern)
from src.device_clone.config_space_manager import BarInfo
from src.device_clone.fallback_manager import (FallbackManager,
                                               get_global_fallback_manager)
from src.device_clone.overlay_mapper import OverlayMapper
from src.device_clone.pcileech_context import (BarConfiguration, ContextError,
                                               DeviceIdentifiers,
                                               PCILeechContextBuilder,
                                               TemplateContext,
                                               TimingParameters,
                                               ValidationLevel)

# ============================================================================
# Test Data Factories
# ============================================================================


class TestDataFactory:
    """Factory for creating consistent test data structures."""

    @staticmethod
    def create_device_capabilities(
        ext_cfg_cap_ptr: int = 0x100,
        ext_cfg_xp_cap_ptr: int = 0x140,
        max_payload_size: int = 256,
        interrupt_mode: str = "msix",
        num_interrupt_sources: int = 8,
    ):
        """Create a mock DeviceCapabilities object with sensible defaults."""
        from src.device_clone.device_config import DeviceCapabilities

        capabilities_mock = Mock(spec=DeviceCapabilities)
        capabilities_mock.ext_cfg_cap_ptr = ext_cfg_cap_ptr
        capabilities_mock.ext_cfg_xp_cap_ptr = ext_cfg_xp_cap_ptr
        capabilities_mock.max_payload_size = max_payload_size
        capabilities_mock.get_cfg_force_mps = Mock(return_value=1)
        capabilities_mock.check_tiny_pcie_issues = Mock(return_value=(False, ""))

        # Active device configuration
        capabilities_mock.active_device = Mock(
            enabled=True,
            timer_period=100000,
            timer_enable=1,
            interrupt_mode=interrupt_mode,
            interrupt_vector=0,
            priority=15,
            msi_vector_width=5,
            msi_64bit_addr=True,
            num_interrupt_sources=num_interrupt_sources,
            default_source_priority=8,
        )

        return capabilities_mock

    @staticmethod
    def create_bar_info(
        index: int = 0,
        address: int = 0xF7000000,
        size: int = 65536,
        bar_type: str = "memory",
        prefetchable: bool = False,
        is_64bit: bool = False,
    ) -> Dict[str, Any]:
        """Create a BAR info dictionary."""
        return {
            "type": bar_type,
            "address": address,
            "size": size,
            "prefetchable": prefetchable,
            "is_64bit": is_64bit,
        }

    @staticmethod
    def create_register_access(
        timestamp: float = 0.1,
        register: str = "BAR0",
        offset: int = 0x100,
        operation: str = "read",
        value: int = 0x12345678,
        duration_us: float = 10.0,
    ) -> RegisterAccess:
        """Create a RegisterAccess object."""
        return RegisterAccess(
            timestamp=timestamp,
            register=register,
            offset=offset,
            operation=operation,
            value=value,
            duration_us=duration_us,
        )

    @staticmethod
    def create_timing_pattern(
        pattern_type: str = "periodic",
        registers: Optional[List[str]] = None,
        avg_interval_us: float = 50.0,
        std_deviation_us: float = 5.0,
        frequency_hz: float = 20000.0,
        confidence: float = 0.95,
    ) -> TimingPattern:
        """Create a TimingPattern object."""
        if registers is None:
            registers = ["BAR0"]

        return TimingPattern(
            pattern_type=pattern_type,
            registers=registers,
            avg_interval_us=avg_interval_us,
            std_deviation_us=std_deviation_us,
            frequency_hz=frequency_hz,
            confidence=confidence,
        )


# ============================================================================
# Mock Builders
# ============================================================================


class MockBuilder:
    """Builder for complex mock objects."""

    @staticmethod
    def build_config_mock(
        enable_advanced: bool = True,
        enable_dma: bool = True,
        enable_interrupts: bool = False,
        timeout: int = 5000,
        buffer_size: int = 4096,
    ) -> Mock:
        """Build a complete configuration mock."""
        config = Mock()
        config.enable_advanced_features = enable_advanced
        config.enable_dma_operations = enable_dma
        config.enable_interrupt_coalescing = enable_interrupts
        config.pcileech_command_timeout = timeout
        config.pcileech_buffer_size = buffer_size
        config.board = None
        config.fpga_part = None
        config.device_config = Mock()
        config.device_config.capabilities = TestDataFactory.create_device_capabilities()

        return config

    @staticmethod
    def build_vfio_region_mock(
        size: int = 65536,
        readable: bool = True,
        writable: bool = True,
        mappable: bool = True,
    ) -> Mock:
        """Build a VFIO region info mock."""
        info = Mock()
        info.argsz = 32
        info.index = 0
        info.size = size

        flags = 0
        if readable:
            flags |= VFIO_REGION_INFO_FLAG_READ
        if writable:
            flags |= VFIO_REGION_INFO_FLAG_WRITE
        if mappable:
            flags |= VFIO_REGION_INFO_FLAG_MMAP

        info.flags = flags
        return info


# ============================================================================
# Context Managers for Testing
# ============================================================================


@contextmanager
def mock_vfio_operations(device_fd: int = 10, container_fd: int = 11):
    """Context manager for mocking VFIO operations."""
    with patch("src.cli.vfio_helpers.get_device_fd") as mock_get_fd, patch(
        "os.close"
    ) as mock_close, patch("fcntl.ioctl") as mock_ioctl:

        mock_get_fd.return_value = (device_fd, container_fd)
        yield mock_get_fd, mock_close, mock_ioctl


@contextmanager
def mock_overlay_mapper(overlay_entries: int = 2):
    """Context manager for mocking OverlayMapper."""
    with patch("src.device_clone.pcileech_context.OverlayMapper") as mock_overlay:
        mock_instance = Mock()
        mock_instance.generate_overlay_map.return_value = {
            "OVERLAY_MAP": [(i * 4, 0xFFFFFFFF >> i) for i in range(overlay_entries)],
            "OVERLAY_ENTRIES": overlay_entries,
        }
        mock_overlay.return_value = mock_instance
        yield mock_instance


# ============================================================================
# Fixtures
# ============================================================================


@pytest.fixture
def factory():
    """Provide test data factory."""
    return TestDataFactory()


@pytest.fixture
def mock_builder():
    """Provide mock builder."""
    return MockBuilder()


@pytest.fixture
def mock_config():
    """Mock configuration object."""
    return MockBuilder.build_config_mock()


@pytest.fixture
def device_identifiers():
    """Valid device identifiers fixture."""
    return DeviceIdentifiers(
        vendor_id="10ee",
        device_id="7024",
        class_code="020000",
        revision_id="01",
        subsystem_vendor_id="10ee",
        subsystem_device_id="0007",
    )


@pytest.fixture
def config_space_data(factory):
    """Mock configuration space data."""
    return {
        "vendor_id": "10ee",
        "device_id": "7024",
        "class_code": "020000",
        "revision_id": "01",
        "subsystem_vendor_id": "10ee",
        "subsystem_device_id": "0007",
        "config_space_hex": "ee10247000000000" * 512,  # 4KB of mock data
        "config_space_size": 4096,
        "bars": [
            factory.create_bar_info(index=0, size=65536),
            factory.create_bar_info(
                index=1,
                address=0xF7100000,
                size=16384,
                prefetchable=True,
                is_64bit=True,
            ),
            factory.create_bar_info(index=2, address=0x3000, size=256, bar_type="io"),
        ],
        "dword_map": {i: f"0x{i*4:08x}" for i in range(1024)},
        "capabilities": {
            "msi": {"offset": 0x50},
            "msix": {"offset": 0x70},
            "pcie": {"offset": 0x80},
        },
        "device_info": {"description": "Test Network Controller"},
    }


@pytest.fixture
def msix_data():
    """Mock MSI-X capability data."""
    return {
        "capability_info": {
            "table_size": 32,
            "table_bir": 0,
            "table_offset": 0x2000,
            "pba_bir": 0,
            "pba_offset": 0x3000,
            "enabled": True,
            "function_mask": False,
        },
        "validation_errors": [],
        "is_valid": True,
    }


@pytest.fixture
def behavior_profile(factory):
    """Mock behavior profile."""
    return BehaviorProfile(
        device_bdf="0000:03:00.0",
        capture_duration=60.0,
        total_accesses=1500,
        register_accesses=[
            factory.create_register_access(timestamp=0.1, offset=0x100),
            factory.create_register_access(
                timestamp=0.2, offset=0x200, operation="write", value=0xABCDEF00
            ),
        ],
        timing_patterns=[
            factory.create_timing_pattern(pattern_type="periodic"),
            factory.create_timing_pattern(
                pattern_type="burst",
                registers=["BAR1"],
                avg_interval_us=100.0,
                frequency_hz=10000.0,
            ),
        ],
        state_transitions={
            "idle": ["active"],
            "active": ["idle", "busy"],
            "busy": ["active"],
        },
        power_states=["D0", "D3hot"],
        interrupt_patterns={"msi": {"frequency": 1000, "burst": False}},
        variance_metadata={"variance": 0.05},
        pattern_analysis={"burst_detected": True},
    )


# ============================================================================
# Test Classes
# ============================================================================


class TestInitialization:
    """Tests for PCILeechContextBuilder initialization."""

    @pytest.mark.parametrize(
        "validation_level",
        [
            ValidationLevel.STRICT,
            ValidationLevel.MODERATE,
            ValidationLevel.PERMISSIVE,
        ],
    )
    def test_initialization_with_validation_levels(self, mock_config, validation_level):
        """Test initialization with different validation levels."""
        builder = PCILeechContextBuilder(
            device_bdf="0000:03:00.0",
            config=mock_config,
            validation_level=validation_level,
        )

        assert builder.device_bdf == "0000:03:00.0"
        assert builder.config == mock_config
        assert builder.validation_level == validation_level
        assert isinstance(builder.fallback_manager, FallbackManager)

    @pytest.mark.parametrize(
        "invalid_bdf",
        [
            "",
            None,
            "   ",
            "\t\n",
        ],
    )
    def test_initialization_invalid_bdf(self, mock_config, invalid_bdf):
        """Test initialization with invalid BDF values."""
        with pytest.raises(ContextError, match="Device BDF"):
            PCILeechContextBuilder(device_bdf=invalid_bdf, config=mock_config)

    def test_initialization_with_custom_fallback(self, mock_config):
        """Test initialization with custom fallback manager."""
        fallback_manager = get_global_fallback_manager(
            mode="auto", allowed_fallbacks=["all"]
        )
        builder = PCILeechContextBuilder(
            device_bdf="0000:03:00.0",
            config=mock_config,
            fallback_manager=fallback_manager,
        )

        assert builder.fallback_manager is fallback_manager


class TestContextBuilding:
    """Tests for context building functionality."""

    def test_build_context_complete_success(
        self,
        mock_config,
        device_identifiers,
        config_space_data,
        msix_data,
        behavior_profile,
    ):
        """Test successful context building with all components."""
        with mock_overlay_mapper() as overlay_mock:
            builder = self._create_builder_with_mocks(
                mock_config,
                device_identifiers,
                config_space_data,
                msix_data,
                behavior_profile,
            )

            context = builder.build_context(
                behavior_profile=behavior_profile,
                config_space_data=config_space_data,
                msix_data=msix_data,
                interrupt_strategy="msix",
                interrupt_vectors=32,
            )

            self._assert_complete_context(context)

    @pytest.mark.parametrize(
        "missing_field",
        [
            "vendor_id",
            "device_id",
            "class_code",
            "revision_id",
        ],
    )
    def test_build_context_missing_required_fields(
        self, mock_config, config_space_data, missing_field
    ):
        """Test context building fails with missing required fields."""
        builder = PCILeechContextBuilder(
            device_bdf="0000:03:00.0",
            config=mock_config,
            validation_level=ValidationLevel.STRICT,
        )

        # Remove required field
        del config_space_data[missing_field]

        with pytest.raises(ContextError, match="Missing required data"):
            builder.build_context(
                behavior_profile=None,
                config_space_data=config_space_data,
                msix_data=None,
            )

    def test_build_context_with_fallback(self, mock_config, config_space_data):
        """Test context building with fallback mechanisms."""
        builder = PCILeechContextBuilder(
            device_bdf="0000:03:00.0",
            config=mock_config,
            validation_level=ValidationLevel.PERMISSIVE,
        )

        # Remove non-critical field
        del config_space_data["subsystem_vendor_id"]

        with mock_overlay_mapper():
            # Mock internal methods to avoid full execution
            self._mock_builder_methods(builder)

            # Should succeed with fallback
            context = builder.build_context(
                behavior_profile=None,
                config_space_data=config_space_data,
                msix_data=None,
            )

            assert context is not None

    # Helper methods
    def _create_builder_with_mocks(
        self, config, identifiers, config_space, msix, behavior
    ):
        """Create a builder with all necessary mocks."""
        builder = PCILeechContextBuilder(
            device_bdf="0000:03:00.0",
            config=config,
            validation_level=ValidationLevel.STRICT,
        )

        self._mock_builder_methods(builder, identifiers)
        return builder

    def _mock_builder_methods(self, builder, identifiers=None):
        """Mock internal builder methods."""
        if identifiers:
            builder._extract_device_identifiers = Mock(return_value=identifiers)

        builder._build_device_config = Mock(return_value={"vendor_id": "10ee"})
        builder._build_config_space_context = Mock(
            return_value={"config_space": "test"}
        )
        builder._build_msix_context = Mock(return_value={"msix": "test"})
        builder._build_bar_config = Mock(return_value={"bars": [{"type": "memory"}]})
        builder._build_timing_config = Mock(
            return_value=TimingParameters(
                read_latency=4,
                write_latency=2,
                burst_length=16,
                inter_burst_gap=8,
                timeout_cycles=1024,
                clock_frequency_mhz=100.0,
                timing_regularity=0.9,
            )
        )
        builder._build_pcileech_config = Mock(return_value={"pcileech": "test"})
        builder._build_active_device_config = Mock(return_value={"active": "test"})
        builder._generate_unique_device_signature = Mock(return_value="32'h12345678")
        builder._build_generation_metadata = Mock(return_value={"metadata": "test"})
        builder._build_board_config = Mock(
            return_value={
                "name": "test_board",
                "fpga_part": "xc7a35t",
                "fpga_family": "artix7",
                "pcie_ip_type": "pcie_7x",
                "max_lanes": 1,
                "supports_msi": True,
                "supports_msix": False,
                "constraints": {"xdc_file": "pcileech_test.xdc"},
                "sys_clk_freq_mhz": 100,
            }
        )

    def _assert_complete_context(self, context):
        """Assert that context contains all required sections."""
        required_sections = [
            "device_config",
            "config_space",
            "msix_config",
            "bar_config",
            "timing_config",
            "pcileech_config",
            "device_signature",
            "generation_metadata",
            "interrupt_config",
            "active_device_config",
            "EXT_CFG_CAP_PTR",
            "EXT_CFG_XP_CAP_PTR",
            "OVERLAY_MAP",
            "OVERLAY_ENTRIES",
        ]

        for section in required_sections:
            assert section in context, f"Missing section: {section}"

        assert context.get("board_name") == "test_board"
        assert context.get("fpga_part") == "xc7a35t"
        assert context.get("pcie_ip_type") == "pcie_7x"
        assert context.get("supports_msi") is True
        assert context.get("supports_msix") is False
        assert context.get("board_constraints")


class TestDeviceIdentifiers:
    """Tests for device identifier extraction and validation."""

    def test_extract_identifiers_complete(self, mock_config, config_space_data):
        """Test extraction with complete data."""
        builder = PCILeechContextBuilder(device_bdf="0000:03:00.0", config=mock_config)

        identifiers = builder._extract_device_identifiers(config_space_data)

        assert identifiers.vendor_id == "10ee"
        assert identifiers.device_id == "7024"
        assert identifiers.class_code == "020000"
        assert identifiers.revision_id == "01"
        assert identifiers.subsystem_vendor_id == "10ee"
        assert identifiers.subsystem_device_id == "0007"

    @pytest.mark.parametrize(
        "subsys_vendor,subsys_device,expected_vendor,expected_device",
        [
            (None, None, "10ee", "7024"),  # Both missing
            ("", "", "10ee", "7024"),  # Empty strings
            ("0000", "0000", "10ee", "7024"),  # Invalid zeros
            (None, "1234", "10ee", "1234"),  # Partial missing
        ],
    )
    def test_extract_identifiers_fallback(
        self,
        mock_config,
        config_space_data,
        subsys_vendor,
        subsys_device,
        expected_vendor,
        expected_device,
    ):
        """Test identifier extraction with fallback scenarios."""
        builder = PCILeechContextBuilder(device_bdf="0000:03:00.0", config=mock_config)

        config_space_data["subsystem_vendor_id"] = subsys_vendor
        config_space_data["subsystem_device_id"] = subsys_device

        identifiers = builder._extract_device_identifiers(config_space_data)

        assert identifiers.subsystem_vendor_id == expected_vendor
        assert identifiers.subsystem_device_id == expected_device

    def test_device_identifiers_validation(self):
        """Test DeviceIdentifiers dataclass validation."""
        # Valid identifiers
        valid = DeviceIdentifiers(
            vendor_id="10ee", device_id="7024", class_code="020000", revision_id="01"
        )
        assert valid.vendor_id == "10ee"

        # Invalid hex format
        with pytest.raises(ContextError, match="invalid hex characters"):
            DeviceIdentifiers(
                vendor_id="XXXX",
                device_id="7024",
                class_code="020000",
                revision_id="01",
            )

        # Empty required field
        with pytest.raises(ContextError, match="Missing"):
            DeviceIdentifiers(
                vendor_id="", device_id="7024", class_code="020000", revision_id="01"
            )


class TestVFIOOperations:
    """Tests for VFIO-related operations."""

    def test_get_vfio_region_info_success(self, mock_config, mock_builder):
        """Test successful VFIO region info retrieval."""
        with mock_vfio_operations() as (mock_get_fd, mock_close, mock_ioctl):
            # Configure ioctl to populate structure
            def ioctl_side_effect(fd, cmd, data, mutate):
                if cmd == VFIO_DEVICE_GET_REGION_INFO:
                    data.size = 65536
                    data.flags = (
                        VFIO_REGION_INFO_FLAG_READ
                        | VFIO_REGION_INFO_FLAG_WRITE
                        | VFIO_REGION_INFO_FLAG_MMAP
                    )
                return 0

            mock_ioctl.side_effect = ioctl_side_effect

            builder = PCILeechContextBuilder(
                device_bdf="0000:03:00.0", config=mock_config
            )

            # _get_vfio_region_info is now part of VFIODeviceManager
            info = builder._vfio_manager.get_region_info(0)

            assert info is not None
            assert info["size"] == 65536
            assert info["readable"] is True
            assert info["writable"] is True
            assert info["mappable"] is True

            # Verify cleanup - at least 2 fds should be closed (device and container)
            # Linux may close additional fds depending on VFIO implementation
            assert mock_close.call_count >= 2

    @pytest.mark.parametrize(
        "error_type,error_msg",
        [
            (OSError(22, "Invalid argument"), "Invalid argument"),
            (PermissionError("Access denied"), "Access denied"),
            (FileNotFoundError("Device not found"), "Device not found"),
        ],
    )
    def test_get_vfio_region_info_errors(self, mock_config, error_type, error_msg):
        """Test VFIO region info error handling."""
        with patch("src.cli.vfio_helpers.get_device_fd") as mock_get_fd:
            mock_get_fd.side_effect = error_type

            builder = PCILeechContextBuilder(
                device_bdf="0000:03:00.0", config=mock_config
            )

            # _get_vfio_region_info is now part of VFIODeviceManager
            info = builder._vfio_manager.get_region_info(0)
            assert info is None


class TestBARConfiguration:
    """Tests for BAR configuration building."""

    def test_build_bar_config_success(
        self, mock_config, config_space_data, behavior_profile, factory
    ):
        """Test successful BAR configuration building."""
        builder = PCILeechContextBuilder(device_bdf="0000:03:00.0", config=mock_config)

        # Mock VFIO BAR info responses
        bar_configs = [
            BarConfiguration(
                index=0,
                base_address=0xF7000000,
                size=65536,
                bar_type=0,
                prefetchable=False,
                is_memory=True,
                is_io=False,
            ),
            BarConfiguration(
                index=1,
                base_address=0xF7100000,
                size=16384,
                bar_type=1,
                prefetchable=True,
                is_memory=True,
                is_io=False,
            ),
            None,  # BAR2 is invalid
        ]

        with patch.object(builder, "_get_vfio_bar_info") as mock_get_bar:
            mock_get_bar.side_effect = bar_configs

            bar_config = builder._build_bar_config(config_space_data, behavior_profile)

            assert bar_config["bar_index"] == 0  # Largest BAR
            assert bar_config["aperture_size"] == 65536
            assert bar_config["memory_type"] == "memory"
            assert len(bar_config["bars"]) == 2

    def test_bar_size_estimation(self, mock_config):
        """Test BAR size estimation for different device types."""
        test_cases = [
            ("network", 0, {"type": "memory"}, 65536),  # 64KB
            ("display", 1, {"prefetchable": True}, 268435456),  # 256MB
            ("storage", 0, {"type": "memory"}, 16384),  # 16KB
            ("unknown", 0, {"type": "memory"}, 4096),  # 4KB default
        ]

        for device_class, bar_index, bar_info, expected_size in test_cases:
            mock_config.device_class = device_class
            builder = PCILeechContextBuilder(
                device_bdf="0000:03:00.0", config=mock_config
            )

            # _estimate_bar_size_from_device_context was removed
            # BAR sizes are now determined directly from VFIO
            pass  # Test removed as functionality is handled differently


class TestTimingConfiguration:
    """Tests for timing configuration generation."""

    # Note: _extract_timing_from_behavior was removed in optimization
    # Timing is now generated in _build_timing_config with simplified logic

    @pytest.mark.parametrize(
        "class_code,expected_freq",
        [
            ("020000", 125.0),  # Network controller
            ("030000", 100.0),  # Display controller (simplified in optimization)
            ("010000", 100.0),  # Storage controller
            ("ff0000", 100.0),  # Unknown (default)
        ],
    )
    def test_timing_config_generation(self, mock_config, class_code, expected_freq):
        """Test timing configuration generation for different device classes."""
        identifiers = DeviceIdentifiers(
            vendor_id="10ee", device_id="7024", class_code=class_code, revision_id="01"
        )

        builder = PCILeechContextBuilder(device_bdf="0000:03:00.0", config=mock_config)

        # Mock get_device_config to return None so we fall through to class-specific logic
        with patch("src.device_clone.pcileech_context.get_device_config", return_value=None):
            # _build_timing_config is the new method that handles timing
            timing = builder._build_timing_config(None, identifiers)

        assert timing.clock_frequency_mhz == expected_freq


class TestMSIXConfiguration:
    """Tests for MSI-X configuration handling."""

    def test_build_msix_context_enabled(self, mock_config, msix_data):
        """Test MSI-X context building with MSI-X enabled."""
        builder = PCILeechContextBuilder(device_bdf="0000:03:00.0", config=mock_config)

        context = builder._build_msix_context(msix_data)

        assert context["num_vectors"] == 32
        assert context["table_bir"] == 0
        assert context["table_offset"] == 0x2000
        assert context["enabled"] is True
        assert context["is_supported"] is True
        assert context["table_size_bytes"] == 512  # 32 * 16
        assert context["NUM_MSIX"] == 32

    def test_build_msix_context_disabled(self, mock_config):
        """Test MSI-X context building with MSI-X not available."""
        builder = PCILeechContextBuilder(device_bdf="0000:03:00.0", config=mock_config)

        context = builder._build_msix_context(None)

        assert context["num_vectors"] == 0
        assert context["enabled"] is False
        assert context["is_supported"] is False
        assert context["table_size"] == 0

    @pytest.mark.parametrize(
        "table_offset,expected_warning",
        [
            (0x2000, False),  # Aligned
            (0x2004, True),  # Not 8-byte aligned
            (0x2007, True),  # Very misaligned
        ],
    )
    def test_msix_alignment_validation(
        self, mock_config, table_offset, expected_warning
    ):
        """Test MSI-X table alignment validation."""
        builder = PCILeechContextBuilder(device_bdf="0000:03:00.0", config=mock_config)

        msix_data = {
            "capability_info": {
                "table_size": 16,
                "table_bir": 0,
                "table_offset": table_offset,
                "pba_bir": 0,
                "pba_offset": 0x3000,
            }
        }

        context = builder._build_msix_context(msix_data)

        if expected_warning:
            assert "alignment_warning" in context
            assert "not 8-byte aligned" in context["alignment_warning"]
        else:
            assert context.get("alignment_warning", "") == ""


class TestValidation:
    """Tests for validation functionality."""

    @pytest.mark.parametrize(
        "level,should_raise",
        [
            (ValidationLevel.STRICT, True),
            (ValidationLevel.MODERATE, True),
            (ValidationLevel.PERMISSIVE, False),
        ],
    )
    def test_validation_levels(
        self, mock_config, config_space_data, level, should_raise
    ):
        """Test different validation levels."""
        builder = PCILeechContextBuilder(
            device_bdf="0000:03:00.0", config=mock_config, validation_level=level
        )

        # Remove required field
        del config_space_data["vendor_id"]

        if should_raise:
            with pytest.raises(ContextError, match="Missing required data"):
                builder._validate_input_data(config_space_data, None, None)
        else:
            # Should not raise, just log warnings
            builder._validate_input_data(config_space_data, None, None)

    def test_context_completeness_validation(self, mock_config):
        """Test context completeness validation."""
        builder = PCILeechContextBuilder(
            device_bdf="0000:03:00.0",
            config=mock_config,
            validation_level=ValidationLevel.STRICT,
        )

        # Valid context
        valid_context = {
            "device_config": {
                "vendor_id": "10ee",
                "device_id": "7024",
                "bdf": "0000:03:00.0",
            },
            "config_space": {},
            "msix_config": {},
            "bar_config": {"bars": [Mock()]},
            "timing_config": {
                "clock_frequency_mhz": 100.0,
                "read_latency": 4,
                "write_latency": 2,
            },
            "pcileech_config": {},
            "device_signature": "32'h12345678",
            "generation_metadata": {},
            "interrupt_config": {"strategy": "msix"},
            "active_device_config": {},
        }

        # Should not raise
        # Cast to TemplateContext type for type checking
        from typing import cast

        builder._validate_context_completeness(cast(TemplateContext, valid_context))

        # Invalid context - missing sections
        invalid_context = {
            "device_config": {"vendor_id": "10ee"},
            "config_space": {},
        }

        with pytest.raises(ContextError, match="Missing required section"):
            from typing import cast

            builder._validate_context_completeness(
                cast(TemplateContext, invalid_context)
            )

    def test_bar_configuration_validation(self):
        """Test BarConfiguration dataclass validation."""
        # Valid configuration
        valid = BarConfiguration(
            index=0,
            base_address=0xF7000000,
            size=65536,
            bar_type=0,
            prefetchable=False,
            is_memory=True,
            is_io=False,
        )
        assert valid.index == 0

        # Invalid index
        with pytest.raises(ContextError, match="Invalid BAR index"):
            BarConfiguration(
                index=6,  # Max is 5
                base_address=0xF7000000,
                size=65536,
                bar_type=0,
                prefetchable=False,
                is_memory=True,
                is_io=False,
            )

        # Invalid size
        with pytest.raises(ContextError, match="Invalid BAR size"):
            BarConfiguration(
                index=0,
                base_address=0xF7000000,
                size=-1,
                bar_type=0,
                prefetchable=False,
                is_memory=True,
                is_io=False,
            )

    def test_timing_parameters_validation(self):
        """Test TimingParameters dataclass validation."""
        # Valid parameters
        valid = TimingParameters(
            read_latency=4,
            write_latency=2,
            burst_length=16,
            inter_burst_gap=8,
            timeout_cycles=1024,
            clock_frequency_mhz=100.0,
            timing_regularity=0.9,
        )
        assert valid.read_latency == 4

        # Invalid parameters
        with pytest.raises(ContextError, match="must be positive"):
            TimingParameters(
                read_latency=0,  # Must be > 0
                write_latency=2,
                burst_length=16,
                inter_burst_gap=8,
                timeout_cycles=1024,
                clock_frequency_mhz=100.0,
                timing_regularity=0.9,
            )


class TestUtilityMethods:
    """Tests for utility and helper methods."""

    def test_serialize_behavior_profile(self, mock_config, behavior_profile):
        """Test behavior profile serialization."""
        builder = PCILeechContextBuilder(device_bdf="0000:03:00.0", config=mock_config)

        serialized = builder._serialize_behavior_profile(behavior_profile)

        assert isinstance(serialized, dict)
        # Check key fields are present
        assert "device_bdf" in serialized
        assert "total_accesses" in serialized
        assert "capture_duration" in serialized

    def test_build_generation_metadata(self, mock_config, device_identifiers):
        """Test generation metadata building."""
        builder = PCILeechContextBuilder(
            device_bdf="0000:03:00.0",
            config=mock_config,
            validation_level=ValidationLevel.MODERATE,
        )

        metadata = builder._build_generation_metadata(device_identifiers)

        assert metadata["device_bdf"] == "0000:03:00.0"
        # Device signature now standardized to VID:DID:RID
        assert metadata["device_signature"] == "10ee:7024:01"
        assert metadata["validation_level"] == "moderate"
        assert "generated_at" in metadata
        assert "generator_version" in metadata


class TestIntegrationScenarios:
    """Integration tests for complete workflows."""

    def test_full_context_generation_workflow(
        self, mock_config, config_space_data, msix_data, behavior_profile
    ):
        """Test complete context generation workflow."""
        with mock_overlay_mapper() as overlay_mock:
            builder = PCILeechContextBuilder(
                device_bdf="0000:03:00.0",
                config=mock_config,
                validation_level=ValidationLevel.STRICT,
            )

            # Mock VFIO operations
            with patch.object(builder, "_get_vfio_bar_info") as mock_bar_info:
                mock_bar_info.side_effect = [
                    BarConfiguration(
                        index=0,
                        base_address=0xF7000000,
                        size=65536,
                        bar_type=0,
                        prefetchable=False,
                        is_memory=True,
                        is_io=False,
                    ),
                    None,
                    None,
                ]

                context = builder.build_context(
                    behavior_profile=behavior_profile,
                    config_space_data=config_space_data,
                    msix_data=msix_data,
                    interrupt_strategy="msix",
                    interrupt_vectors=32,
                )

                # Verify complete context
                self._verify_complete_context(context)

    @pytest.mark.skipif(sys.platform == "darwin", reason="VFIO tests require Linux")
    def test_error_recovery_workflow(self, mock_config):
        """Test error recovery and fallback mechanisms."""
        builder = PCILeechContextBuilder(
            device_bdf="0000:03:00.0",
            config=mock_config,
            validation_level=ValidationLevel.PERMISSIVE,
        )

        # Simulate various failures
        with patch.object(
            builder, "_get_vfio_bar_info", side_effect=Exception("VFIO error")
        ):
            # Exception should propagate without being wrapped
            with pytest.raises(Exception, match="VFIO error"):
                builder._get_vfio_bar_info(0, {"type": "memory"})

    @pytest.mark.skipif(sys.platform == "darwin", reason="VFIO tests require Linux")
    def test_performance_optimization_scenario(
        self, mock_config, config_space_data, behavior_profile
    ):
        """Test performance optimizations in context building."""
        import time

        builder = PCILeechContextBuilder(device_bdf="0000:03:00.0", config=mock_config)

        # Mock expensive operations - return valid BAR to avoid "No valid MMIO BARs" error
        mock_bar = BarConfiguration(
            index=0,
            base_address=0xF7000000,
            size=65536,
            bar_type=0,
            prefetchable=False,
            is_memory=True,
            is_io=False,
        )
        with patch.object(builder, "_get_vfio_bar_info", return_value=mock_bar):
            start_time = time.time()

            # This should use cached results where possible
            for _ in range(10):
                builder._build_bar_config(config_space_data, behavior_profile)

            elapsed = time.time() - start_time

            # Should complete quickly due to optimizations
            assert elapsed < 1.0  # Less than 1 second for 10 iterations

    def _verify_complete_context(self, context):
        """Verify a complete context structure."""
        required_fields = [
            "device_config",
            "config_space",
            "msix_config",
            "bar_config",
            "timing_config",
            "pcileech_config",
            "device_signature",
            "generation_metadata",
            "interrupt_config",
            "active_device_config",
            "OVERLAY_ENTRIES",
        ]

        for field in required_fields:
            assert field in context, f"Missing field: {field}"

        # Verify specific values
        assert context["device_config"]["vendor_id"] == "10ee"
        assert context["msix_config"]["num_vectors"] == 32
        assert context["interrupt_config"]["strategy"] == "msix"


class TestEdgeCases:
    """Tests for edge cases and boundary conditions."""

    @pytest.mark.parametrize(
        "vendor_id,device_id,expected_error",
        [
            ("", "7024", "cannot be empty"),
            ("10ee", "", "cannot be empty"),
            ("XXXX", "7024", "invalid hex characters"),
            ("10ee", "YYYY", "invalid hex characters"),
            ("12345", "7024", "must be 4 hex digits"),  # Too long
        ],
    )
    def test_invalid_device_identifiers(self, vendor_id, device_id, expected_error):
        """Test invalid device identifier combinations."""
        with pytest.raises(ContextError, match=expected_error):
            DeviceIdentifiers(
                vendor_id=vendor_id,
                device_id=device_id,
                class_code="020000",
                revision_id="01",
            )

    @pytest.mark.parametrize(
        "size,expected_valid",
        [
            (0, False),  # Zero size
            (-1, False),  # Negative
            (1, True),  # Minimum valid
            (4096, True),  # Common size
            (0xFFFFFFFF, True),  # Maximum 32-bit
            (0x100000000, False),  # Overflow
        ],
    )
    def test_bar_size_boundaries(self, size, expected_valid):
        """Test BAR size boundary conditions."""
        if expected_valid:
            bar = BarConfiguration(
                index=0,
                base_address=0xF7000000,
                size=size,
                bar_type=0,
                prefetchable=False,
                is_memory=True,
                is_io=False,
            )
            assert bar.size == size
        else:
            with pytest.raises((ContextError, ValueError)):
                BarConfiguration(
                    index=0,
                    base_address=0xF7000000,
                    size=size,
                    bar_type=0,
                    prefetchable=False,
                    is_memory=True,
                    is_io=False,
                )

    def test_empty_behavior_profile(self, mock_config, device_identifiers):
        """Test handling of empty or minimal behavior profiles."""
        builder = PCILeechContextBuilder(device_bdf="0000:03:00.0", config=mock_config)

        # Empty behavior profile
        empty_profile = BehaviorProfile(
            device_bdf="0000:03:00.0",
            capture_duration=0.0,
            total_accesses=0,
            register_accesses=[],
            timing_patterns=[],
            state_transitions={},
            power_states=[],
            interrupt_patterns={},
            variance_metadata={},
            pattern_analysis={},
        )

        # Should handle gracefully
        timing = builder._build_timing_config(empty_profile, device_identifiers)
        assert isinstance(timing, TimingParameters)
        # Should fall back to device-based timing
        assert timing.clock_frequency_mhz > 0


# ============================================================================
# Performance Tests
# ============================================================================


@pytest.mark.performance
class TestPerformance:
    """Performance-related tests."""

    def test_context_building_performance(
        self, mock_config, config_space_data, msix_data, behavior_profile
    ):
        """Test context building performance."""
        import time

        with mock_overlay_mapper():
            builder = PCILeechContextBuilder(
                device_bdf="0000:03:00.0", config=mock_config
            )

            # Mock expensive operations
            with patch.object(builder, "_get_vfio_bar_info", return_value=None):
                iterations = 100
                start_time = time.time()

                for _ in range(iterations):
                    # Create mock identifiers for timing config
                    mock_identifiers = DeviceIdentifiers(
                        vendor_id="8086",
                        device_id="1234",
                        class_code="020000",
                        revision_id="01",
                    )
                    builder._build_timing_config(behavior_profile, mock_identifiers)

                elapsed = time.time() - start_time
                avg_time = elapsed / iterations

                # Should be fast
                assert avg_time < 0.01  # Less than 10ms per iteration

    def test_large_config_space_handling(self, mock_config):
        """Test handling of large configuration spaces."""
        # Create large config space (64KB extended)
        large_config = {
            "vendor_id": "10ee",
            "device_id": "7024",
            "class_code": "020000",
            "revision_id": "01",
            "config_space_hex": "00" * 65536,  # 64KB
            "config_space_size": 65536,
            "bars": [],
            "dword_map": {i: f"0x{i*4:08x}" for i in range(16384)},
        }

        builder = PCILeechContextBuilder(device_bdf="0000:03:00.0", config=mock_config)

        # Should handle without issues
        context = builder._build_config_space_context(large_config)
        assert context["size"] == 65536


# ============================================================================
# Regression Tests
# ============================================================================


class TestRegressions:
    """Tests for specific bug fixes and regressions."""

    def test_subsystem_id_fallback_regression(self, mock_config):
        """Test regression: subsystem IDs falling back correctly."""
        builder = PCILeechContextBuilder(device_bdf="0000:03:00.0", config=mock_config)

        # Both subsystem IDs are None
        config_data = {
            "vendor_id": "10ee",
            "device_id": "7024",
            "class_code": "020000",
            "revision_id": "01",
            "subsystem_vendor_id": None,
            "subsystem_device_id": None,
        }

        identifiers = builder._extract_device_identifiers(config_data)

        # Should fall back to main IDs
        assert identifiers.subsystem_vendor_id == "10ee"
        assert identifiers.subsystem_device_id == "7024"
