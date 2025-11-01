#!/usr/bin/env python3
"""Unit tests for log_config module."""

import logging
import os
import tempfile
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

from src.log_config import (
    _StripAnsiFilter,
    get_logger,
    setup_logging,
)


class TestSetupLogging:
    """Test cases for setup_logging function."""

    def setup_method(self):
        """Reset logging state before each test."""
        # Clear all handlers and reset configuration state
        root_logger = logging.getLogger()
        for handler in root_logger.handlers[:]:
            root_logger.removeHandler(handler)
        if hasattr(root_logger, "_myapp_logging_configured"):
            delattr(root_logger, "_myapp_logging_configured")
        root_logger.setLevel(logging.WARNING)
        
        # Clear any environment variables
        for var in ["LOG_LEVEL", "LOG_FILE"]:
            if var in os.environ:
                del os.environ[var]

    def teardown_method(self):
        """Clean up after each test."""
        # Clear handlers again
        root_logger = logging.getLogger()
        for handler in root_logger.handlers[:]:
            handler.close()
            root_logger.removeHandler(handler)
        if hasattr(root_logger, "_myapp_logging_configured"):
            delattr(root_logger, "_myapp_logging_configured")
        
        # Clear environment variables
        for var in ["LOG_LEVEL", "LOG_FILE"]:
            if var in os.environ:
                del os.environ[var]

    def test_basic_setup(self):
        """Test basic logging setup without file."""
        setup_logging(level=logging.INFO, log_file=None)
        
        root_logger = logging.getLogger()
        assert root_logger.level == logging.INFO
        assert len(root_logger.handlers) == 1
        assert isinstance(root_logger.handlers[0], logging.StreamHandler)
        assert hasattr(root_logger, "_myapp_logging_configured")
        assert root_logger._myapp_logging_configured is True

    def test_setup_with_file(self):
        """Test logging setup with file handler."""
        with tempfile.NamedTemporaryFile(mode="w", delete=False, suffix=".log") as f:
            log_file = f.name
        
        try:
            setup_logging(level=logging.DEBUG, log_file=log_file)
            
            root_logger = logging.getLogger()
            assert root_logger.level == logging.DEBUG
            assert len(root_logger.handlers) == 2
            
            # Check console handler
            console_handler = root_logger.handlers[0]
            assert isinstance(console_handler, logging.StreamHandler)
            
            # Check file handler
            file_handler = root_logger.handlers[1]
            assert isinstance(file_handler, logging.FileHandler)
            assert file_handler.baseFilename == log_file
        finally:
            # Cleanup
            root_logger = logging.getLogger()
            for handler in root_logger.handlers[:]:
                handler.close()
            if os.path.exists(log_file):
                os.unlink(log_file)

    def test_no_clobber_without_force(self):
        """Test that setup_logging doesn't reconfigure without force=True."""
        # First setup
        setup_logging(level=logging.INFO, log_file=None)
        root_logger = logging.getLogger()
        initial_handler_count = len(root_logger.handlers)
        initial_handler = root_logger.handlers[0]
        
        # Second setup without force - should only change level
        setup_logging(level=logging.DEBUG, log_file=None)
        
        assert root_logger.level == logging.DEBUG
        assert len(root_logger.handlers) == initial_handler_count
        assert root_logger.handlers[0] is initial_handler  # Same handler object

    def test_force_reconfigure(self):
        """Test that force=True reconfigures logging."""
        # First setup
        setup_logging(level=logging.INFO, log_file=None)
        root_logger = logging.getLogger()
        initial_handler = root_logger.handlers[0]
        
        # Second setup with force - should replace handlers
        setup_logging(level=logging.WARNING, log_file=None, force=True)
        
        assert root_logger.level == logging.WARNING
        assert len(root_logger.handlers) == 1
        assert root_logger.handlers[0] is not initial_handler  # Different handler

    def test_env_level_numeric(self):
        """Test LOG_LEVEL environment variable with numeric value."""
        os.environ["LOG_LEVEL"] = "10"  # DEBUG level
        
        setup_logging(level=logging.ERROR)  # Should be overridden
        
        root_logger = logging.getLogger()
        assert root_logger.level == 10

    def test_env_level_name(self):
        """Test LOG_LEVEL environment variable with level name."""
        os.environ["LOG_LEVEL"] = "DEBUG"
        
        setup_logging(level=logging.ERROR)  # Should be overridden
        
        root_logger = logging.getLogger()
        assert root_logger.level == logging.DEBUG

    def test_env_level_lowercase(self):
        """Test LOG_LEVEL environment variable with lowercase name."""
        os.environ["LOG_LEVEL"] = "warning"
        
        setup_logging(level=logging.ERROR)  # Should be overridden
        
        root_logger = logging.getLogger()
        assert root_logger.level == logging.WARNING

    def test_env_level_invalid(self):
        """Test LOG_LEVEL environment variable with invalid value."""
        os.environ["LOG_LEVEL"] = "INVALID"
        
        # Should fall back to provided level
        setup_logging(level=logging.INFO)
        
        root_logger = logging.getLogger()
        assert root_logger.level == logging.INFO

    def test_env_log_file(self):
        """Test LOG_FILE environment variable."""
        with tempfile.NamedTemporaryFile(mode="w", delete=False, suffix=".log") as f:
            env_log_file = f.name
        
        try:
            os.environ["LOG_FILE"] = env_log_file
            
            setup_logging(level=logging.INFO, log_file="other.log")
            
            root_logger = logging.getLogger()
            assert len(root_logger.handlers) == 2
            file_handler = root_logger.handlers[1]
            assert file_handler.baseFilename == env_log_file
        finally:
            root_logger = logging.getLogger()
            for handler in root_logger.handlers[:]:
                handler.close()
            if os.path.exists(env_log_file):
                os.unlink(env_log_file)

    def test_file_append_mode(self):
        """Test that file handler uses append mode."""
        with tempfile.NamedTemporaryFile(mode="w", delete=False, suffix=".log") as f:
            log_file = f.name
            f.write("existing content\n")
        
        try:
            setup_logging(level=logging.INFO, log_file=log_file)
            logger = logging.getLogger(__name__)
            logger.info("new message")
            
            # Close handlers to flush
            root_logger = logging.getLogger()
            for handler in root_logger.handlers[:]:
                handler.close()
            
            # Check file contains both old and new content
            with open(log_file, "r") as f:
                content = f.read()
            
            assert "existing content" in content
            assert "new message" in content
        finally:
            if os.path.exists(log_file):
                os.unlink(log_file)

    def test_file_encoding_utf8(self):
        """Test that file handler uses UTF-8 encoding."""
        with tempfile.NamedTemporaryFile(mode="w", delete=False, suffix=".log") as f:
            log_file = f.name
        
        try:
            setup_logging(level=logging.INFO, log_file=log_file)
            logger = logging.getLogger(__name__)
            
            # Log message with UTF-8 characters
            logger.info("Unicode test: 你好 世界 🚀")
            
            # Close handlers to flush
            root_logger = logging.getLogger()
            for handler in root_logger.handlers[:]:
                handler.close()
            
            # Read file and verify UTF-8 content
            with open(log_file, "r", encoding="utf-8") as f:
                content = f.read()
            
            assert "你好" in content
            assert "世界" in content
            assert "🚀" in content
        finally:
            if os.path.exists(log_file):
                os.unlink(log_file)

    def test_noisy_loggers_suppressed(self):
        """Test that noisy third-party loggers are suppressed."""
        setup_logging(level=logging.DEBUG)
        
        # Check that noisy loggers are set to WARNING
        for logger_name in ("urllib3", "requests", "botocore", "boto3"):
            logger = logging.getLogger(logger_name)
            assert logger.level == logging.WARNING

    def test_console_handler_formatter(self):
        """Test that console handler has minimal formatter."""
        setup_logging(level=logging.INFO, log_file=None)
        
        root_logger = logging.getLogger()
        console_handler = root_logger.handlers[0]
        
        # Check formatter format string
        formatter = console_handler.formatter
        assert formatter._fmt == "%(message)s"

    def test_file_handler_formatter(self):
        """Test that file handler has detailed formatter."""
        with tempfile.NamedTemporaryFile(mode="w", delete=False, suffix=".log") as f:
            log_file = f.name
        
        try:
            setup_logging(level=logging.INFO, log_file=log_file)
            
            root_logger = logging.getLogger()
            file_handler = root_logger.handlers[1]
            
            # Check formatter format string
            formatter = file_handler.formatter
            assert "%(asctime)s" in formatter._fmt
            assert "%(name)s" in formatter._fmt
            assert "%(levelname)s" in formatter._fmt
            assert "%(message)s" in formatter._fmt
        finally:
            root_logger = logging.getLogger()
            for handler in root_logger.handlers[:]:
                handler.close()
            if os.path.exists(log_file):
                os.unlink(log_file)

    def test_file_handler_has_ansi_filter(self):
        """Test that file handler has ANSI stripping filter."""
        with tempfile.NamedTemporaryFile(mode="w", delete=False, suffix=".log") as f:
            log_file = f.name
        
        try:
            setup_logging(level=logging.INFO, log_file=log_file)
            
            root_logger = logging.getLogger()
            file_handler = root_logger.handlers[1]
            
            # Check that filter is present
            assert len(file_handler.filters) > 0
            assert isinstance(file_handler.filters[0], _StripAnsiFilter)
        finally:
            root_logger = logging.getLogger()
            for handler in root_logger.handlers[:]:
                handler.close()
            if os.path.exists(log_file):
                os.unlink(log_file)

    def test_multiple_setups_without_force(self):
        """Test multiple setup calls without force."""
        setup_logging(level=logging.INFO, log_file=None)
        setup_logging(level=logging.DEBUG, log_file=None)
        setup_logging(level=logging.WARNING, log_file=None)
        
        root_logger = logging.getLogger()
        # Should only have one console handler
        assert len(root_logger.handlers) == 1
        # Level should be from last call
        assert root_logger.level == logging.WARNING


class TestStripAnsiFilter:
    """Test cases for _StripAnsiFilter class."""

    def test_strips_ansi_codes(self):
        """Test that ANSI codes are stripped from messages."""
        ansi_filter = _StripAnsiFilter()
        
        record = logging.LogRecord(
            name="test",
            level=logging.INFO,
            pathname="",
            lineno=0,
            msg="\033[31mRed text\033[0m and \033[32mgreen\033[0m",
            args=(),
            exc_info=None,
        )
        
        result = ansi_filter.filter(record)
        
        assert result is True
        assert record.msg == "Red text and green"
        assert "\033[" not in record.msg

    def test_handles_non_string_messages(self):
        """Test that filter handles non-string messages."""
        ansi_filter = _StripAnsiFilter()
        
        record = logging.LogRecord(
            name="test",
            level=logging.INFO,
            pathname="",
            lineno=0,
            msg=12345,  # Non-string message
            args=(),
            exc_info=None,
        )
        
        result = ansi_filter.filter(record)
        
        assert result is True
        assert record.msg == 12345

    def test_strips_various_ansi_codes(self):
        """Test stripping of various ANSI escape sequences."""
        ansi_filter = _StripAnsiFilter()
        
        test_cases = [
            ("\033[0m", ""),
            ("\033[31m", ""),
            ("\033[1;32m", ""),
            ("\033[0;31;1m", ""),
            ("Text \033[31mRed\033[0m Normal", "Text Red Normal"),
            ("\033[36mCyan\033[0m \033[33mYellow\033[0m", "Cyan Yellow"),
        ]
        
        for input_msg, expected_output in test_cases:
            record = logging.LogRecord(
                name="test",
                level=logging.INFO,
                pathname="",
                lineno=0,
                msg=input_msg,
                args=(),
                exc_info=None,
            )
            
            ansi_filter.filter(record)
            assert record.msg == expected_output

    def test_preserves_message_without_ansi(self):
        """Test that messages without ANSI codes are preserved."""
        ansi_filter = _StripAnsiFilter()
        
        original_msg = "Plain text message with no colors"
        record = logging.LogRecord(
            name="test",
            level=logging.INFO,
            pathname="",
            lineno=0,
            msg=original_msg,
            args=(),
            exc_info=None,
        )
        
        ansi_filter.filter(record)
        assert record.msg == original_msg


class TestGetLogger:
    """Test cases for get_logger function."""

    def test_get_logger_returns_logger(self):
        """Test that get_logger returns a logger instance."""
        logger = get_logger("test_logger")
        
        assert isinstance(logger, logging.Logger)
        assert logger.name == "test_logger"

    def test_get_logger_same_name_returns_same_instance(self):
        """Test that same name returns same logger instance."""
        logger1 = get_logger("test_logger")
        logger2 = get_logger("test_logger")
        
        assert logger1 is logger2

    def test_get_logger_different_names(self):
        """Test that different names return different instances."""
        logger1 = get_logger("logger1")
        logger2 = get_logger("logger2")
        
        assert logger1 is not logger2
        assert logger1.name == "logger1"
        assert logger2.name == "logger2"


class TestIntegration:
    """Integration tests for log_config module."""

    def setup_method(self):
        """Reset logging state before each test."""
        root_logger = logging.getLogger()
        for handler in root_logger.handlers[:]:
            root_logger.removeHandler(handler)
        if hasattr(root_logger, "_myapp_logging_configured"):
            delattr(root_logger, "_myapp_logging_configured")

    def teardown_method(self):
        """Clean up after each test."""
        root_logger = logging.getLogger()
        for handler in root_logger.handlers[:]:
            handler.close()
            root_logger.removeHandler(handler)
        if hasattr(root_logger, "_myapp_logging_configured"):
            delattr(root_logger, "_myapp_logging_configured")

    def test_end_to_end_logging_to_file(self):
        """Test complete logging flow to file with ANSI stripping."""
        with tempfile.NamedTemporaryFile(mode="w", delete=False, suffix=".log") as f:
            log_file = f.name
        
        try:
            setup_logging(level=logging.INFO, log_file=log_file)
            logger = get_logger(__name__)
            
            # Log messages with ANSI codes
            logger.info("Plain message")
            logger.info("\033[31mRed\033[0m message")
            logger.warning("\033[33mYellow\033[0m warning")
            
            # Close handlers to flush
            root_logger = logging.getLogger()
            for handler in root_logger.handlers[:]:
                handler.close()
            
            # Read and verify file
            with open(log_file, "r", encoding="utf-8") as f:
                content = f.read()
            
            assert "Plain message" in content
            assert "Red message" in content
            assert "Yellow warning" in content
            # Verify ANSI codes are stripped
            assert "\033[" not in content
        finally:
            if os.path.exists(log_file):
                os.unlink(log_file)

    def test_logging_levels_work_correctly(self):
        """Test that different logging levels work correctly."""
        with tempfile.NamedTemporaryFile(mode="w", delete=False, suffix=".log") as f:
            log_file = f.name
        
        try:
            setup_logging(level=logging.WARNING, log_file=log_file)
            logger = get_logger(__name__)
            
            logger.debug("Debug message")  # Should not appear
            logger.info("Info message")  # Should not appear
            logger.warning("Warning message")  # Should appear
            logger.error("Error message")  # Should appear
            
            # Close handlers to flush
            root_logger = logging.getLogger()
            for handler in root_logger.handlers[:]:
                handler.close()
            
            # Read and verify file
            with open(log_file, "r", encoding="utf-8") as f:
                content = f.read()
            
            assert "Debug message" not in content
            assert "Info message" not in content
            assert "Warning message" in content
            assert "Error message" in content
        finally:
            if os.path.exists(log_file):
                os.unlink(log_file)
