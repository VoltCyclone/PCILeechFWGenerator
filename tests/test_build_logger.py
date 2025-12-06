#!/usr/bin/env python3
"""Unit tests for build logger."""

import logging
import pytest
from unittest.mock import MagicMock, patch

from src.utils.build_logger import BuildLogger, get_build_logger


class TestBuildLogger:
    """Test BuildLogger class."""

    def test_initialization(self):
        """Test BuildLogger initialization."""
        logger = logging.getLogger("test")
        build_logger = BuildLogger(logger)

        assert build_logger.logger == logger

    def test_initialization_default_logger(self):
        """Test BuildLogger uses default logger when none provided."""
        build_logger = BuildLogger()
        assert build_logger.logger is not None

    @patch('src.utils.build_logger.log_info_safe')
    def test_info_with_prefix(self, mock_log_info):
        """Test info logging with prefix."""
        mock_logger = MagicMock()
        build_logger = BuildLogger(mock_logger)

        build_logger.info("Test info", prefix="BUILD")

        mock_log_info.assert_called_once_with(
            mock_logger, "Test info", prefix="BUILD"
        )

    @patch('src.utils.build_logger.log_warning_safe')
    def test_warning_with_prefix(self, mock_log_warning):
        """Test warning logging with prefix."""
        mock_logger = MagicMock()
        build_logger = BuildLogger(mock_logger)

        build_logger.warning("Test warning", prefix="WARN")

        mock_log_warning.assert_called_once_with(
            mock_logger, "Test warning", prefix="WARN"
        )

    @patch('src.utils.build_logger.log_error_safe')
    def test_error_with_prefix(self, mock_log_error):
        """Test error logging with prefix."""
        mock_logger = MagicMock()
        build_logger = BuildLogger(mock_logger)

        build_logger.error("Test error", prefix="ERROR")

        mock_log_error.assert_called_once_with(
            mock_logger, "Test error", prefix="ERROR"
        )

    @patch('src.utils.build_logger.log_debug_safe')
    def test_debug_with_prefix(self, mock_log_debug):
        """Test debug logging with prefix."""
        mock_logger = MagicMock()
        build_logger = BuildLogger(mock_logger)

        build_logger.debug("Test debug", prefix="DEBUG")

        mock_log_debug.assert_called_once_with(
            mock_logger, "Test debug", prefix="DEBUG"
        )

    @patch('src.utils.build_logger.log_info_safe')
    def test_vfio_info(self, mock_log_info):
        """Test VFIO info logging."""
        mock_logger = MagicMock()
        build_logger = BuildLogger(mock_logger)

        build_logger.vfio_info("Device found")

        mock_log_info.assert_called_once_with(
            mock_logger, "Device found", prefix="VFIO"
        )

    @patch('src.utils.build_logger.log_info_safe')
    def test_host_cfg_info(self, mock_log_info):
        """Test host config info logging."""
        mock_logger = MagicMock()
        build_logger = BuildLogger(mock_logger)

        build_logger.host_cfg_info("Loading context")

        mock_log_info.assert_called_once_with(
            mock_logger, "Loading context", prefix="HOST_CFG"
        )

    @patch('src.utils.build_logger.log_info_safe')
    def test_filemgr_info(self, mock_log_info):
        """Test file manager info logging."""
        mock_logger = MagicMock()
        build_logger = BuildLogger(mock_logger)

        build_logger.filemgr_info("Copying file")

        mock_log_info.assert_called_once_with(
            mock_logger, "Copying file", prefix="FILEMGR"
        )

    @patch('src.utils.build_logger.log_info_safe')
    def test_template_info(self, mock_log_info):
        """Test template info logging."""
        mock_logger = MagicMock()
        build_logger = BuildLogger(mock_logger)

        build_logger.template_info("Rendering template")

        mock_log_info.assert_called_once_with(
            mock_logger, "Rendering template", prefix="TEMPLATE"
        )

    @patch('src.utils.build_logger.log_info_safe')
    def test_vivado_info(self, mock_log_info):
        """Test Vivado info logging."""
        mock_logger = MagicMock()
        build_logger = BuildLogger(mock_logger)

        build_logger.vivado_info("Starting synthesis")

        mock_log_info.assert_called_once_with(
            mock_logger, "Starting synthesis", prefix="VIVADO"
        )

    @patch('src.utils.build_logger.log_info_safe')
    def test_device_info(self, mock_log_info):
        """Test device info logging."""
        mock_logger = MagicMock()
        build_logger = BuildLogger(mock_logger)

        build_logger.device_info("Device detected")

        mock_log_info.assert_called_once_with(
            mock_logger, "Device detected", prefix="DEVICE"
        )

    @patch('src.utils.build_logger.log_info_safe')
    def test_validation_info(self, mock_log_info):
        """Test validation info logging."""
        mock_logger = MagicMock()
        build_logger = BuildLogger(mock_logger)

        build_logger.validation_info("Validating config")

        mock_log_info.assert_called_once_with(
            mock_logger, "Validating config", prefix="VALID"
        )

    @patch('src.utils.build_logger.log_info_safe')
    def test_msix_info(self, mock_log_info):
        """Test MSI-X info logging."""
        mock_logger = MagicMock()
        build_logger = BuildLogger(mock_logger)

        build_logger.msix_info("MSI-X capability found")

        mock_log_info.assert_called_once_with(
            mock_logger, "MSI-X capability found", prefix="MSIX"
        )

    @patch('src.utils.build_logger.log_info_safe')
    def test_bar_info(self, mock_log_info):
        """Test BAR info logging."""
        mock_logger = MagicMock()
        build_logger = BuildLogger(mock_logger)

        build_logger.bar_info("BAR0 size: 4KB")

        mock_log_info.assert_called_once_with(
            mock_logger, "BAR0 size: 4KB", prefix="BAR"
        )

    @patch('src.utils.build_logger.log_info_safe')
    def test_pcil_info(self, mock_log_info):
        """Test PCILeech generator info logging."""
        mock_logger = MagicMock()
        build_logger = BuildLogger(mock_logger)

        build_logger.pcil_info("Generating firmware")

        mock_log_info.assert_called_once_with(
            mock_logger, "Generating firmware", prefix="PCIL"
        )

    @patch('src.utils.build_logger.log_info_safe')
    def test_repo_info(self, mock_log_info):
        """Test repository info logging."""
        mock_logger = MagicMock()
        build_logger = BuildLogger(mock_logger)

        build_logger.repo_info("Cloning repository")

        mock_log_info.assert_called_once_with(
            mock_logger, "Cloning repository", prefix="REPO"
        )

    def test_prefix_normalization(self):
        """Test that prefixes are normalized from PREFIXES dict."""
        build_logger = BuildLogger()

        # Check that known prefixes are in the dict
        assert "BUILD" in build_logger.PREFIXES.values()
        assert "VFIO" in build_logger.PREFIXES.values()
        assert "HOST_CFG" in build_logger.PREFIXES.values()

    def test_phase_stack_operations(self):
        """Test phase stack push/pop operations."""
        build_logger = BuildLogger()

        assert build_logger.current_phase() is None

        build_logger._phase_stack.append("synthesis")
        assert build_logger.current_phase() == "synthesis"

        build_logger._phase_stack.pop()
        assert build_logger.current_phase() is None


class TestGetBuildLogger:
    """Test get_build_logger convenience function."""

    def test_get_build_logger_default(self):
        """Test get_build_logger with default logger."""
        result = get_build_logger()

        assert isinstance(result, BuildLogger)
        assert result.logger is not None

    def test_get_build_logger_with_logger_instance(self):
        """Test get_build_logger with existing logger instance."""
        existing_logger = logging.getLogger("test")

        result = get_build_logger(logger=existing_logger)

        assert isinstance(result, BuildLogger)
        assert result.logger == existing_logger
