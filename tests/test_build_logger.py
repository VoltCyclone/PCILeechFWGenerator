#!/usr/bin/env python3
"""Unit tests for build logger."""

import logging
import pytest
from unittest.mock import MagicMock, patch, call

from src.utils.build_logger import BuildLogger, get_build_logger


class TestBuildLogger:
    """Test BuildLogger class."""

    def test_initialization(self):
        """Test BuildLogger initialization."""
        logger = logging.getLogger("test")
        build_logger = BuildLogger(logger)

        assert build_logger.logger == logger

    def test_format_message_with_prefix(self):
        """Test message formatting with prefix."""
        logger = logging.getLogger("test")
        build_logger = BuildLogger(logger)

        msg = build_logger._format_message("Test message", prefix="BUILD")
        assert msg == "[BUILD] Test message"

    def test_format_message_without_prefix(self):
        """Test message formatting without prefix."""
        logger = logging.getLogger("test")
        build_logger = BuildLogger(logger)

        msg = build_logger._format_message("Test message")
        assert msg == "Test message"

    def test_info_with_prefix(self):
        """Test info logging with prefix."""
        mock_logger = MagicMock()
        build_logger = BuildLogger(mock_logger)

        build_logger.info("Test info", prefix="BUILD")

        mock_logger.info.assert_called_once_with("[BUILD] Test info")

    def test_warning_with_prefix(self):
        """Test warning logging with prefix."""
        mock_logger = MagicMock()
        build_logger = BuildLogger(mock_logger)

        build_logger.warning("Test warning", prefix="WARN")

        mock_logger.warning.assert_called_once_with("[WARN] Test warning")

    def test_error_with_prefix(self):
        """Test error logging with prefix."""
        mock_logger = MagicMock()
        build_logger = BuildLogger(mock_logger)

        build_logger.error("Test error", prefix="ERROR")

        mock_logger.error.assert_called_once_with("[ERROR] Test error")

    def test_debug_with_prefix(self):
        """Test debug logging with prefix."""
        mock_logger = MagicMock()
        build_logger = BuildLogger(mock_logger)

        build_logger.debug("Test debug", prefix="DEBUG")

        mock_logger.debug.assert_called_once_with("[DEBUG] Test debug")

    def test_vfio_decision_info(self):
        """Test VFIO decision logging."""
        mock_logger = MagicMock()
        build_logger = BuildLogger(mock_logger)

        build_logger.vfio_decision_info("VFIO enabled")

        mock_logger.info.assert_called_once_with("[VFIO_DECISION] VFIO enabled")

    def test_vfio_info(self):
        """Test VFIO info logging."""
        mock_logger = MagicMock()
        build_logger = BuildLogger(mock_logger)

        build_logger.vfio_info("Device found")

        mock_logger.info.assert_called_once_with("[VFIO] Device found")

    def test_vfio_warning(self):
        """Test VFIO warning logging."""
        mock_logger = MagicMock()
        build_logger = BuildLogger(mock_logger)

        build_logger.vfio_warning("Device not accessible")

        mock_logger.warning.assert_called_once_with(
            "[VFIO] Device not accessible")

    def test_host_context_info(self):
        """Test host context info logging."""
        mock_logger = MagicMock()
        build_logger = BuildLogger(mock_logger)

        build_logger.host_context_info("Loading context")

        mock_logger.info.assert_called_once_with("[HOST_CFG] Loading context")

    def test_file_manager_info(self):
        """Test file manager info logging."""
        mock_logger = MagicMock()
        build_logger = BuildLogger(mock_logger)

        build_logger.file_manager_info("Copying file")

        mock_logger.info.assert_called_once_with("[FILEMGR] Copying file")

    def test_template_info(self):
        """Test template info logging."""
        mock_logger = MagicMock()
        build_logger = BuildLogger(mock_logger)

        build_logger.template_info("Rendering template")

        mock_logger.info.assert_called_once_with("[TEMPLATE] Rendering template")

    def test_tcl_format_info(self):
        """Test TCL format info logging."""
        mock_logger = MagicMock()
        build_logger = BuildLogger(mock_logger)

        build_logger.tcl_format_info("Formatting TCL")

        mock_logger.info.assert_called_once_with("[TCLFMT] Formatting TCL")

    def test_build_phase_info(self):
        """Test build phase info logging."""
        mock_logger = MagicMock()
        build_logger = BuildLogger(mock_logger)

        build_logger.build_phase_info("Starting synthesis")

        mock_logger.info.assert_called_once_with(
            "[BUILD] Starting synthesis")

    def test_build_phase_warning(self):
        """Test build phase warning logging."""
        mock_logger = MagicMock()
        build_logger = BuildLogger(mock_logger)

        build_logger.build_phase_warning("Missing file")

        mock_logger.warning.assert_called_once_with("[BUILD] Missing file")


class TestGetBuildLogger:
    """Test get_build_logger convenience function."""

    @patch('src.utils.build_logger.logging.getLogger')
    def test_get_build_logger_default(self, mock_get_logger):
        """Test get_build_logger with default logger."""
        mock_logger = MagicMock()
        mock_get_logger.return_value = mock_logger

        result = get_build_logger()

        assert isinstance(result, BuildLogger)
        mock_get_logger.assert_called_once_with("pcileechfwgenerator")

    @patch('src.utils.build_logger.logging.getLogger')
    def test_get_build_logger_custom_name(self, mock_get_logger):
        """Test get_build_logger with custom logger name."""
        mock_logger = MagicMock()
        mock_get_logger.return_value = mock_logger

        result = get_build_logger(name="custom_logger")

        assert isinstance(result, BuildLogger)
        mock_get_logger.assert_called_once_with("custom_logger")

    def test_get_build_logger_with_logger_instance(self):
        """Test get_build_logger with existing logger instance."""
        existing_logger = logging.getLogger("test")

        result = get_build_logger(logger=existing_logger)

        assert isinstance(result, BuildLogger)
        assert result.logger == existing_logger
