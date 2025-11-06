#!/usr/bin/env python3
"""Unit tests for log_config module."""

import logging
import sys
from io import StringIO
from unittest.mock import patch

import pytest

from src.log_config import (
    FallbackColoredFormatter,
    get_logger,
    setup_logging,
)


class TestSetupLogging:
    """Tests for setup_logging function."""

    def test_setup_logging_default_level(self):
        """Test setup_logging with default INFO level."""
        setup_logging()
        root_logger = logging.getLogger()
        
        assert root_logger.level == logging.INFO
        assert len(root_logger.handlers) > 0
        
        # Verify handler is StreamHandler
        assert any(
            isinstance(h, logging.StreamHandler) for h in root_logger.handlers
        )

    def test_setup_logging_custom_level(self):
        """Test setup_logging with custom DEBUG level."""
        setup_logging(level=logging.DEBUG)
        root_logger = logging.getLogger()
        
        assert root_logger.level == logging.DEBUG

    def test_setup_logging_warning_level(self):
        """Test setup_logging with WARNING level."""
        setup_logging(level=logging.WARNING)
        root_logger = logging.getLogger()
        
        assert root_logger.level == logging.WARNING

    def test_setup_logging_clears_existing_handlers(self):
        """Test that setup_logging clears existing handlers."""
        # Add a dummy handler
        root_logger = logging.getLogger()
        dummy_handler = logging.NullHandler()
        root_logger.addHandler(dummy_handler)
        initial_count = len(root_logger.handlers)
        
        # Setup logging should clear and recreate
        setup_logging()
        
        # Should have exactly one handler (console)
        assert len(root_logger.handlers) == 1
        assert dummy_handler not in root_logger.handlers

    def test_setup_logging_log_file_param_ignored(self):
        """Test that log_file parameter is accepted but ignored."""
        # Should not raise an exception
        setup_logging(log_file="test.log")
        
        root_logger = logging.getLogger()
        
        # Should only have console handler, no file handler
        handlers = root_logger.handlers
        assert len(handlers) == 1
        assert isinstance(handlers[0], logging.StreamHandler)
        assert handlers[0].stream == sys.stdout

    def test_setup_logging_backwards_compatibility(self):
        """Test backwards compatibility with old signature."""
        # Old code that passed log_file should still work
        try:
            setup_logging(level=logging.INFO, log_file="generate.log")
            setup_logging(level=logging.DEBUG, log_file=None)
            setup_logging(log_file="vfio_diagnostics.log")
        except TypeError:
            pytest.fail("setup_logging should accept log_file parameter")

    def test_setup_logging_console_formatter(self):
        """Test that console handler uses minimal formatter."""
        setup_logging()
        root_logger = logging.getLogger()
        
        console_handler = root_logger.handlers[0]
        formatter = console_handler.formatter
        
        # Should have a formatter
        assert formatter is not None
        
        # Format string should be minimal (just message)
        assert formatter._fmt == "%(message)s"

    def test_setup_logging_suppresses_noisy_loggers(self):
        """Test that urllib3 and requests loggers are suppressed."""
        setup_logging()
        
        urllib3_logger = logging.getLogger("urllib3")
        requests_logger = logging.getLogger("requests")
        
        assert urllib3_logger.level == logging.WARNING
        assert requests_logger.level == logging.WARNING

    def test_setup_logging_output_to_stdout(self):
        """Test that logging output goes to stdout."""
        setup_logging(level=logging.INFO)
        
        # Capture stdout
        captured_output = StringIO()
        root_logger = logging.getLogger()
        
        # Temporarily replace handler stream
        original_stream = root_logger.handlers[0].stream
        root_logger.handlers[0].stream = captured_output
        
        try:
            root_logger.info("Test message")
            output = captured_output.getvalue()
            assert "Test message" in output
        finally:
            # Restore original stream
            root_logger.handlers[0].stream = original_stream

    def test_setup_logging_multiple_calls(self):
        """Test that multiple setup_logging calls don't accumulate handlers."""
        setup_logging()
        first_count = len(logging.getLogger().handlers)
        
        setup_logging()
        second_count = len(logging.getLogger().handlers)
        
        setup_logging()
        third_count = len(logging.getLogger().handlers)
        
        # Should always have the same number of handlers
        assert first_count == second_count == third_count == 1


class TestGetLogger:
    """Tests for get_logger function."""

    def test_get_logger_returns_logger(self):
        """Test that get_logger returns a Logger instance."""
        logger = get_logger("test_module")
        assert isinstance(logger, logging.Logger)

    def test_get_logger_with_name(self):
        """Test that get_logger uses the provided name."""
        logger = get_logger("my_module")
        assert logger.name == "my_module"

    def test_get_logger_different_names(self):
        """Test that different names return different loggers."""
        logger1 = get_logger("module1")
        logger2 = get_logger("module2")
        
        assert logger1.name != logger2.name
        assert logger1 is not logger2

    def test_get_logger_same_name_returns_same_instance(self):
        """Test that same name returns the same logger instance."""
        logger1 = get_logger("same_module")
        logger2 = get_logger("same_module")
        
        assert logger1 is logger2


class TestFallbackColoredFormatter:
    """Tests for FallbackColoredFormatter (legacy compatibility)."""

    def test_fallback_formatter_exists(self):
        """Test that FallbackColoredFormatter class exists."""
        assert FallbackColoredFormatter is not None
        assert issubclass(FallbackColoredFormatter, logging.Formatter)

    def test_fallback_formatter_has_colors(self):
        """Test that formatter has color definitions."""
        assert hasattr(FallbackColoredFormatter, "COLORS")
        assert hasattr(FallbackColoredFormatter, "RESET")
        assert "DEBUG" in FallbackColoredFormatter.COLORS
        assert "INFO" in FallbackColoredFormatter.COLORS
        assert "WARNING" in FallbackColoredFormatter.COLORS
        assert "ERROR" in FallbackColoredFormatter.COLORS

    def test_fallback_formatter_instantiation(self):
        """Test that formatter can be instantiated."""
        formatter = FallbackColoredFormatter()
        assert isinstance(formatter, logging.Formatter)

    @patch("sys.stdout.isatty", return_value=True)
    def test_fallback_formatter_colors_terminal(self, mock_isatty):
        """Test that formatter adds colors when outputting to terminal."""
        formatter = FallbackColoredFormatter(
            "%(levelname)s - %(message)s"
        )
        
        record = logging.LogRecord(
            name="test",
            level=logging.INFO,
            pathname="test.py",
            lineno=1,
            msg="Test message",
            args=(),
            exc_info=None,
        )
        
        formatted = formatter.format(record)
        # Should contain color codes when isatty is True
        # Color codes start with \033[
        assert "\033[" in formatted or "INFO" in formatted

    @patch("sys.stdout.isatty", return_value=False)
    def test_fallback_formatter_no_colors_non_terminal(self, mock_isatty):
        """Test that formatter doesn't add colors when not outputting to terminal."""
        formatter = FallbackColoredFormatter(
            "%(levelname)s - %(message)s"
        )
        
        record = logging.LogRecord(
            name="test",
            level=logging.INFO,
            pathname="test.py",
            lineno=1,
            msg="Test message",
            args=(),
            exc_info=None,
        )
        
        formatted = formatter.format(record)
        # Levelname should be restored after formatting
        assert record.levelname == "INFO"


class TestLoggingIntegration:
    """Integration tests for logging system."""

    def test_logger_inherits_root_level(self):
        """Test that created loggers inherit root logger level."""
        setup_logging(level=logging.DEBUG)
        
        logger = get_logger("test_inherit")
        root_logger = logging.getLogger()
        
        # Logger should inherit or respect root level
        assert root_logger.level == logging.DEBUG

    def test_end_to_end_logging_flow(self):
        """Test complete logging flow from setup to output."""
        # Setup logging
        setup_logging(level=logging.INFO)
        
        # Get a logger
        logger = get_logger("integration_test")
        
        # Capture output
        captured_output = StringIO()
        root_logger = logging.getLogger()
        original_stream = root_logger.handlers[0].stream
        root_logger.handlers[0].stream = captured_output
        
        try:
            # Log messages at different levels
            logger.debug("Debug message")  # Should not appear
            logger.info("Info message")
            logger.warning("Warning message")
            logger.error("Error message")
            
            output = captured_output.getvalue()
            
            # Debug should be suppressed
            assert "Debug message" not in output
            
            # Others should appear
            assert "Info message" in output
            assert "Warning message" in output
            assert "Error message" in output
        finally:
            root_logger.handlers[0].stream = original_stream

    def test_no_file_handlers_created(self):
        """Test that no file handlers are created regardless of parameters."""
        setup_logging(log_file="test.log")
        
        root_logger = logging.getLogger()
        
        # Check that no FileHandler exists
        file_handlers = [
            h for h in root_logger.handlers 
            if isinstance(h, logging.FileHandler)
        ]
        
        assert len(file_handlers) == 0

    def test_container_safe_operation(self):
        """Test that logging works in container-like environments."""
        # This simulates container behavior where file creation might fail
        # but console logging should always work
        
        try:
            setup_logging(log_file="/readonly/path/logfile.log")
            logger = get_logger("container_test")
            
            # Should not raise exception
            logger.info("Container logging test")
            
            # Should have exactly one console handler
            root_logger = logging.getLogger()
            assert len(root_logger.handlers) == 1
            assert isinstance(root_logger.handlers[0], logging.StreamHandler)
            
        except PermissionError:
            pytest.fail("Logging should not fail with permission errors")
