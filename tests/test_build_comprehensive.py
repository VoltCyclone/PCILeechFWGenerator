#!/usr/bin/env python3
"""
Comprehensive unit tests for src/build.py

Tests critical functionality including BuildConfiguration, main build flow,
error handling, and validation to improve test coverage.
"""

import json
import logging
import tempfile
from dataclasses import asdict
from pathlib import Path
from typing import Any, Dict
from unittest.mock import Mock, patch

import pytest

from src.build import (BUFFER_SIZE, DEFAULT_OUTPUT_DIR,
                       DEFAULT_PROFILE_DURATION, MAX_PARALLEL_FILE_WRITES,
                       REQUIRED_MODULES, SPECIAL_FILE_EXTENSIONS,
                       SYSTEMVERILOG_EXTENSION, BuildConfiguration,
                       DeviceConfiguration, FirmwareBuilder, ModuleChecker,
                       MSIXData, _as_int, _optional_int)
from src.exceptions import (ConfigurationError, FileOperationError,
                            MSIXPreloadError, PCILeechBuildError,
                            VivadoIntegrationError)


class TestBuildHelperFunctions:
    """Test helper functions for build operations."""

    def test_as_int_with_integer(self):
        """Test _as_int with integer input."""
        assert _as_int(42, "test_field") == 42

    def test_as_int_with_hex_string(self):
        """Test _as_int with hex string input."""
        assert _as_int("0x1234", "test_field") == 0x1234  # 4660 decimal
        assert _as_int("0X1234", "test_field") == 0x1234  # 4660 decimal
        # Bare strings without prefix are interpreted as decimal by int(s, 0)
        assert _as_int("1234", "device_id") == 1234

    def test_as_int_with_bare_identifier(self):
        """Bare zero-padded identifiers must parse without raising."""
        assert _as_int("0014", "device_id") == 20

    def test_as_int_with_decimal_string(self):
        """Test _as_int with decimal string."""
        assert _as_int("123", "revision_id") == 123

    def test_optional_int_with_valid_values(self):
        """Test _optional_int with valid values."""
        assert _optional_int(42) == 42
        assert _optional_int("0x1234") == 0x1234
        assert _optional_int("0014") == 20
        assert _optional_int(None) is None
        assert _optional_int("") is None

    def test_optional_int_with_invalid_values(self):
        """Test _optional_int with invalid values returns None."""
        assert _optional_int("invalid") is None


class TestBuildConfiguration:
    """Test BuildConfiguration dataclass."""

    def test_basic_configuration(self):
        """Test basic BuildConfiguration creation."""
        config = BuildConfiguration(
            bdf="0000:03:00.0",
            board="pcileech_35t325_x4",
            output_dir=Path("/tmp/output"),
        )

        assert config.bdf == "0000:03:00.0"
        assert config.board == "pcileech_35t325_x4"
        assert config.output_dir == Path("/tmp/output")
        assert config.enable_profiling is True  # default
        assert config.preload_msix is True  # default

    def test_configuration_with_custom_values(self):
        """Test BuildConfiguration with custom values."""
        config = BuildConfiguration(
            bdf="0000:01:00.0",
            board="custom_board",
            output_dir=Path("/custom/output"),
            enable_profiling=False,
            preload_msix=False,
            profile_duration=60,
            parallel_writes=False,
            max_workers=2,
        )

        assert config.enable_profiling is False
        assert config.preload_msix is False
        assert config.profile_duration == 60
        assert config.parallel_writes is False
        assert config.max_workers == 2

    def test_configuration_dict_conversion(self):
        """Test configuration can be converted to dict."""
        config = BuildConfiguration(
            bdf="0000:03:00.0",
            board="test_board",
            output_dir=Path("/tmp"),
        )

        config_dict = asdict(config)
        assert config_dict["bdf"] == "0000:03:00.0"
        assert config_dict["board"] == "test_board"


class TestDeviceConfiguration:
    """Test DeviceConfiguration dataclass."""

    def test_device_configuration_creation(self):
        """Test DeviceConfiguration creation."""
        device_config = DeviceConfiguration(
            vendor_id=0x10DE,
            device_id=0x1234,
            revision_id=0x01,
            class_code=0x0200,
            requires_msix=True,
            pcie_lanes=4,
        )

        assert device_config.vendor_id == 0x10DE
        assert device_config.device_id == 0x1234
        assert device_config.revision_id == 0x01
        assert device_config.class_code == 0x0200
        assert device_config.requires_msix is True
        assert device_config.pcie_lanes == 4


class TestMSIXData:
    """Test MSIXData dataclass."""

    def test_msix_data_creation(self):
        """Test MSIXData creation."""
        msix_data = MSIXData(
            preloaded=True,
            msix_info={"vector_count": 32},
            config_space_hex="1234abcd",
        )

        assert msix_data.preloaded is True
        assert msix_data.msix_info["vector_count"] == 32
        assert msix_data.config_space_hex == "1234abcd"

    def test_msix_data_defaults(self):
        """Test MSIXData with defaults."""
        msix_data = MSIXData(preloaded=False)

        assert msix_data.preloaded is False
        assert msix_data.msix_info is None
        assert msix_data.config_space_hex is None
        assert msix_data.config_space_bytes is None


class TestModuleChecker:
    """Test ModuleChecker class."""

    def test_module_checker_initialization(self):
        """Test ModuleChecker initialization."""
        modules = ["test.module1", "test.module2"]
        checker = ModuleChecker(modules)

        assert checker.required_modules == modules
        assert checker.logger is not None

    @patch("builtins.__import__")
    def test_check_all_success(self, mock_import):
        """Test successful module checking."""
        mock_import.return_value = Mock()
        modules = ["test.module1", "test.module2"]
        checker = ModuleChecker(modules)

        # Should not raise an exception
        checker.check_all()

    @patch("builtins.__import__")
    def test_check_all_missing_module(self, mock_import):
        """Test module checking with missing module."""
        mock_import.side_effect = ImportError("Module not found")
        modules = ["test.missing_module"]
        checker = ModuleChecker(modules)

        with pytest.raises(Exception):  # Will raise ModuleImportError or similar
            checker.check_all()


class TestFirmwareBuilder:
    """Test FirmwareBuilder class."""

    @pytest.fixture
    def mock_config(self):
        """Create a mock configuration for testing."""
        return BuildConfiguration(
            bdf="0000:03:00.0",
            board="pcileech_35t325_x4",
            output_dir=Path("/tmp/test_output"),
        )

    @patch("src.build.MSIXManager")
    @patch("src.build.FileOperationsManager")
    @patch("src.build.ConfigurationManager")
    def test_initialization(
        self, mock_config_mgr, mock_file_mgr, mock_msix_mgr, mock_config
    ):
        """Test firmware builder initialization."""
        builder = FirmwareBuilder(mock_config)

        assert builder.config == mock_config
        assert builder.logger is not None

    @patch("src.build.MSIXManager")
    @patch("src.build.FileOperationsManager")
    @patch("src.build.ConfigurationManager")
    def test_initialization_with_custom_managers(
        self, mock_config_mgr, mock_file_mgr, mock_msix_mgr, mock_config
    ):
        """Test initialization with custom managers."""
        custom_msix = Mock()
        custom_file = Mock()
        custom_config = Mock()
        custom_logger = Mock()

        builder = FirmwareBuilder(
            mock_config,
            msix_manager=custom_msix,
            file_manager=custom_file,
            config_manager=custom_config,
            logger=custom_logger,
        )

        assert builder.msix_manager == custom_msix
        assert builder.file_manager == custom_file
        assert builder.config_manager == custom_config
        assert builder.logger == custom_logger


class TestBuildConstants:
    """Test build constants are properly defined."""

    def test_buffer_size_constant(self):
        """Test BUFFER_SIZE constant is defined."""
        assert BUFFER_SIZE == 1024 * 1024

    def test_default_output_dir_constant(self):
        """Test DEFAULT_OUTPUT_DIR constant is defined."""
        assert DEFAULT_OUTPUT_DIR == "output"

    def test_default_profile_duration_constant(self):
        """Test DEFAULT_PROFILE_DURATION constant is defined."""
        assert DEFAULT_PROFILE_DURATION == 30

    def test_max_parallel_file_writes_constant(self):
        """Test MAX_PARALLEL_FILE_WRITES constant is defined."""
        assert MAX_PARALLEL_FILE_WRITES == 4

    def test_required_modules_constant(self):
        """Test REQUIRED_MODULES constant is defined."""
        assert isinstance(REQUIRED_MODULES, list)
        assert len(REQUIRED_MODULES) > 0
        assert "src.device_clone.pcileech_generator" in REQUIRED_MODULES

    def test_special_file_extensions_constant(self):
        """Test SPECIAL_FILE_EXTENSIONS constant is defined."""
        assert isinstance(SPECIAL_FILE_EXTENSIONS, set)
        assert ".coe" in SPECIAL_FILE_EXTENSIONS
        assert ".hex" in SPECIAL_FILE_EXTENSIONS

    def test_systemverilog_extension_constant(self):
        """Test SYSTEMVERILOG_EXTENSION constant is defined."""
        assert SYSTEMVERILOG_EXTENSION == ".sv"


if __name__ == "__main__":
    pytest.main([__file__])
