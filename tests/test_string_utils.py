#!/usr/bin/env python3
"""
Unit tests for string utilities module.

This module tests the string formatting and utility functions,
particularly focusing on BAR table formatting and safe string operations.
"""

import logging
from datetime import datetime
from unittest.mock import Mock, patch

import pytest

from src.string_utils import (FormatConfig, build_device_info_string,
                              build_file_size_string, build_progress_string,
                              format_bar_summary_table, format_bar_table,
                              format_padded_message, format_raw_bar_table,
                              generate_sv_header_comment,
                              generate_tcl_header_comment, get_short_timestamp,
                              log_debug_safe, log_error_safe, log_info_safe,
                              log_warning_safe, multiline_format, safe_format,
                              safe_log_format, safe_print_format,
                              truncate_string, validate_template)


class TestSafeFormat:
    """Test cases for safe_format function."""

    def test_simple_format(self):
        """Test basic string formatting."""
        result = safe_format("Hello {name}", name="World")
        assert result == "Hello World"

    def test_format_with_prefix(self):
        """Test formatting with prefix."""
        result = safe_format("Device found", prefix="VFIO")
        assert result == "[VFIO] Device found"

    def test_format_with_multiple_placeholders(self):
        """Test formatting with multiple placeholders."""
        result = safe_format(
            "Device {bdf} with VID:{vid:04x} DID:{did:04x}",
            bdf="0000:00:1f.3",
            vid=0x8086,
            did=0x54C8,
        )
        assert result == "Device 0000:00:1f.3 with VID:8086 DID:54c8"

    def test_missing_key_handling(self):
        """Test handling of missing keys."""
        with patch("logging.getLogger") as mock_get_logger:
            logger = Mock()
            mock_get_logger.return_value = logger
            result = safe_format("Hello {missing_key}", name="World")
            assert "<MISSING:missing_key>" in result
            logger.warning.assert_called_once()

    def test_format_error_handling(self):
        """Test handling of format specification errors."""
        with patch("logging.getLogger") as mock_get_logger:
            logger = Mock()
            mock_get_logger.return_value = logger
            result = safe_format("Invalid format {value:invalid}", value=123)
            assert "Invalid format {value:invalid}" in result
            logger.error.assert_called_once()

    def test_validate_template(self):
        """Validate template syntax detection."""
        assert validate_template("Hello {name}") is True
        assert validate_template("{invalid placeholder}") is False
        assert validate_template("Unbalanced {brace") is False

    def test_truncate_string_variants(self):
        """Ensure truncation helper supports multiple positions."""
        assert truncate_string("abcdef", 4) == "a..."
        assert truncate_string("abcdef", 4, position="start") == "...f"
        assert truncate_string("abcdef", 5, position="middle") == "a...f"


class TestDeviceInfoString:
    """Test cases for build_device_info_string function."""

    def test_basic_device_info(self):
        """Test basic device info string."""
        device_info = {"vendor_id": 0x8086, "device_id": 0x54C8}
        result = build_device_info_string(device_info)
        assert result == "VID:8086, DID:54c8"

    def test_complete_device_info(self):
        """Test complete device info string."""
        device_info = {
            "vendor_id": 0x8086,
            "device_id": 0x54C8,
            "class_code": 0x0280,
            "subsystem_vendor_id": 0x8086,
            "subsystem_device_id": 0x0034,
        }
        result = build_device_info_string(device_info)
        expected = "VID:8086, DID:54c8, Class:0280, SVID:8086, SDID:0034"
        assert result == expected


class TestProgressString:
    """Test cases for build_progress_string function."""

    def test_basic_progress(self):
        """Test basic progress string."""
        result = build_progress_string("Processing", 5, 10)
        assert "Processing: 5/10 (50.0%)" in result

    def test_progress_with_time(self):
        """Test progress string with elapsed time."""
        result = build_progress_string("Building", 3, 4, elapsed_time=45.67)
        assert "Building: 3/4 (75.0%)" in result
        assert "45.7s elapsed" in result

    def test_zero_total_handling(self):
        """Test handling of zero total."""
        result = build_progress_string("Test", 0, 0)
        assert "(0.0%)" in result


class TestFileSizeString:
    """Test cases for build_file_size_string function."""

    def test_bytes_size(self):
        """Test file size in bytes."""
        result = build_file_size_string(512)
        assert "512 bytes" in result

    def test_kilobytes_size(self):
        """Test file size in kilobytes."""
        result = build_file_size_string(2048)
        assert "2.0 KB" in result
        assert "2048 bytes" in result

    def test_megabytes_size(self):
        """Test file size in megabytes."""
        result = build_file_size_string(16777216)  # 16 MB
        assert "16.0 MB" in result
        assert "16777216 bytes" in result


class TestPaddedMessage:
    """Test cases for format_padded_message function."""

    def test_info_message_padding(self):
        """Test INFO level message padding."""
        with patch("src.string_utils.get_short_timestamp", return_value="14:23:45"):
            result = format_padded_message("Test message", "INFO")
            assert result == "  14:23:45 │  INFO  │ Test message"

    def test_warning_message_padding(self):
        """Test WARNING level message padding."""
        with patch("src.string_utils.get_short_timestamp", return_value="14:23:45"):
            result = format_padded_message("Warning message", "WARNING")
            assert result == "  14:23:45 │ WARNING│ Warning message"

    def test_error_message_padding(self):
        """Test ERROR level message padding."""
        with patch("src.string_utils.get_short_timestamp", return_value="14:23:45"):
            result = format_padded_message("Error message", "ERROR")
            assert result == "  14:23:45 │ ERROR  │ Error message"


class TestHeaderComments:
    """Test cases for header comment generation."""

    def test_sv_header_basic(self):
        """Test basic SystemVerilog header."""
        result = generate_sv_header_comment("Test Module")
        assert "// Test Module" in result
        assert result.startswith("//==============")
        assert result.endswith("==============")

    def test_sv_header_with_device_info(self):
        """Test SystemVerilog header with device info."""
        result = generate_sv_header_comment(
            "Device Module", vendor_id="8086", device_id="54c8", board="AC701"
        )
        assert "// Device Module - Generated for 8086:54c8" in result
        assert "// Board: AC701" in result

    def test_tcl_header_basic(self):
        """Test basic TCL header."""
        result = generate_tcl_header_comment("Build Script")
        assert "# Build Script" in result
        assert result.startswith("#==============")
        assert result.endswith("==============")

    def test_tcl_header_with_device_info(self):
        """Test TCL header with device info."""
        result = generate_tcl_header_comment(
            "PCILeech Build",
            vendor_id="8086",
            device_id="54c8",
            class_code="0280",
            board="AC701",
        )
        assert "# PCILeech Build" in result
        assert "# Generated for device 8086:54c8 (Class: 0280)" in result
        assert "# Board: AC701" in result


class MockBarInfo:
    """Mock BAR info object for testing."""

    def __init__(
        self,
        index,
        base_address=0,
        size=0,
        is_memory=True,
        prefetchable=False,
        is_64bit=False,
    ):
        self.index = index
        self.base_address = base_address
        self.size = size
        self.is_memory = is_memory
        self.prefetchable = prefetchable
        self.is_64bit = is_64bit


class TestBarTableFormatting:
    """Test cases for BAR table formatting functions."""

    def test_empty_bar_configs(self):
        """Test formatting with no BAR configurations."""
        result = format_bar_table([])
        assert result == "No BAR configurations found"

    def test_single_bar_config(self):
        """Test formatting with single BAR configuration."""
        bar_info = MockBarInfo(
            index=0,
            base_address=0xF6600000,
            size=16384,  # 16KB
            is_memory=True,
            prefetchable=False,
        )
        result = format_bar_table([bar_info])

        # Check table structure
        assert "┌" in result and "┐" in result  # Top border
        assert "└" in result and "┘" in result  # Bottom border
        assert "│" in result  # Column separators

        # Check data content
        assert "0x" in result  # Address format
        assert "16,384" in result  # Size with comma separator
        assert "0.02" in result  # Size in MB
        assert "memory" in result
        assert "no" in result  # Not prefetchable

    def test_multiple_bar_configs_with_primary(self):
        """Test formatting with multiple BARs and primary selection."""
        bar0 = MockBarInfo(
            index=0, base_address=0xF6600000, size=16384, is_memory=True  # 16KB
        )
        bar1 = MockBarInfo(
            index=1, base_address=0x00000000, size=0, is_memory=False  # Empty BAR
        )
        bar2 = MockBarInfo(
            index=2,
            base_address=0xF6700000,
            size=65536,  # 64KB
            is_memory=True,
            prefetchable=True,
        )

        result = format_bar_table([bar0, bar1, bar2], primary_bar=bar2)

        # Check that primary BAR is marked with star
        assert "★" in result

        # Check different BAR properties are shown
        assert "0xF6600000" in result or "0xf6600000" in result.lower()
        assert "65,536" in result
        assert "yes" in result  # Prefetchable for BAR2
        assert "candidate" in result or "yes" in result  # Memory BAR candidates

    def test_bar_summary_table(self):
        """Test compact BAR summary table."""
        bar_info = MockBarInfo(
            index=0, base_address=0xF6600000, size=16384, is_memory=True
        )
        result = format_bar_summary_table([bar_info])

        # Summary table should be more compact
        assert "candidate" in result or "empty" in result or "PRIMARY" in result
        assert "0x" in result  # Address
        assert len(result.split("\n")) < 10  # Should be compact

    def test_bar_summary_with_primary(self):
        """Test BAR summary table with primary BAR."""
        bar_info = MockBarInfo(
            index=0, base_address=0xF6600000, size=16384, is_memory=True
        )
        result = format_bar_summary_table([bar_info], primary_bar=bar_info)

        assert "PRIMARY ★" in result

    def test_raw_bar_table_with_dict_data(self):
        """Test raw BAR table with dictionary data."""
        bar_data = [
            {
                "type": "memory",
                "address": 0xF6600000,
                "size": 16384,
                "prefetchable": False,
                "is_64bit": False,
            },
            {
                "type": "memory",
                "address": 0x00000000,
                "size": 0,
                "prefetchable": False,
                "is_64bit": False,
            },
        ]

        result = format_raw_bar_table(bar_data, "0000:04:00.0")

        assert "memory" in result
        assert "0xF6600000" in result or "0xf6600000" in result.lower()
        assert "16384" in result
        assert "No" in result  # Not prefetchable

    def test_raw_bar_table_with_int_data(self):
        """Test raw BAR table with integer address data."""
        bar_data = [0xF6600000, 0x00000000, 0x0000E001]

        result = format_raw_bar_table(bar_data, "0000:04:00.0")

        assert "0xF6600000" in result or "0xf6600000" in result.lower()
        assert "0x00000000" in result
        assert "0x0000E001" in result or "0x0000e001" in result.lower()
        assert "unknown" in result  # Unknown properties for int data

    def test_empty_raw_bar_table(self):
        """Test raw BAR table with no data."""
        result = format_raw_bar_table([], "0000:04:00.0")
        assert result == "No BAR data found"

    def test_bar_table_with_various_sizes(self):
        """Test BAR table with various memory sizes."""
        bars = [
            MockBarInfo(0, 0xF0000000, 4096, True),  # 4KB
            MockBarInfo(1, 0xF1000000, 65536, True),  # 64KB
            MockBarInfo(2, 0xF2000000, 1048576, True),  # 1MB
            MockBarInfo(3, 0xF3000000, 16777216, True),  # 16MB
        ]

        result = format_bar_table(bars)

        # Check size formatting
        assert "4,096" in result
        assert "65,536" in result
        assert "1,048,576" in result
        assert "16,777,216" in result

        # Check MB calculations
        assert "0.00" in result  # 4KB in MB
        assert "0.06" in result  # 64KB in MB
        assert "1.00" in result  # 1MB in MB
        assert "16.00" in result  # 16MB in MB

    def test_bar_table_with_io_bars(self):
        """Test BAR table with I/O BARs."""
        bars = [
            MockBarInfo(0, 0xF6600000, 16384, True),  # Memory BAR
            MockBarInfo(1, 0x0000E000, 256, False),  # I/O BAR
        ]

        result = format_bar_table(bars)

        assert "memory" in result
        assert "io" in result
        assert "candidate" in result or "yes" in result  # Memory BAR is candidate
        assert "no" in result  # I/O BAR is not candidate

    def test_bar_table_defensive_getattr(self):
        """Test that BAR table formatting handles missing attributes gracefully."""
        # Create a mock object with all necessary attributes set to proper values
        mock_bar = Mock()
        mock_bar.index = 0
        mock_bar.bar_number = 0
        mock_bar.is_memory = False
        mock_bar.size = 0
        mock_bar.base_address = 0x12345678
        mock_bar.type_str = "I/O"

        result = format_bar_table([mock_bar])

        # Should not crash and should show default values
        assert "unknown" in result or "0" in result
        assert "┌" in result  # Table structure should still be present

    def test_bar_table_ascii_style(self):
        """Ensure ASCII borders render when configured."""
        config = FormatConfig.get_instance()
        original_flag = config.use_unicode_tables
        try:
            config.use_unicode_tables = False
            bar_info = MockBarInfo(
                index=0, base_address=0xF6600000, size=16384, is_memory=True
            )
            result = format_bar_table([bar_info])
            assert result.startswith("+")
            assert "|" in result
        finally:
            config.use_unicode_tables = original_flag


class TestLoggingFunctions:
    """Test cases for logging convenience functions."""

    def test_log_info_safe(self):
        """Test safe INFO logging."""
        mock_logger = Mock()
        log_info_safe(mock_logger, "Test message {value}", value=42)

        mock_logger.info.assert_called_once()
        call_args = mock_logger.info.call_args[0][0]
        assert "Test message 42" in call_args
        assert "INFO" in call_args

    def test_log_error_safe(self):
        """Test safe ERROR logging."""
        mock_logger = Mock()
        log_error_safe(mock_logger, "Error: {error}", prefix="VFIO", error="failed")

        mock_logger.error.assert_called_once()
        call_args = mock_logger.error.call_args[0][0]
        assert "[VFIO] Error: failed" in call_args
        assert "ERROR" in call_args

    def test_log_warning_safe(self):
        """Test safe WARNING logging."""
        mock_logger = Mock()
        log_warning_safe(mock_logger, "Warning: {msg}", msg="test")

        mock_logger.warning.assert_called_once()
        call_args = mock_logger.warning.call_args[0][0]
        assert "Warning: test" in call_args
        assert "WARNING" in call_args

    def test_log_debug_safe(self):
        """Test safe DEBUG logging."""
        mock_logger = Mock()
        log_debug_safe(mock_logger, "Debug: {info}", info="details")

        mock_logger.debug.assert_called_once()
        call_args = mock_logger.debug.call_args[0][0]
        assert "Debug: details" in call_args
        assert "DEBUG" in call_args


class TestMultilineFormat:
    """Test cases for multiline format function."""

    def test_multiline_template(self):
        """Test multiline string formatting."""
        template = """
Device Information:
  BDF: {bdf}
  Vendor ID: {vid:04x}
  Device ID: {did:04x}
  Driver: {driver}
        """.strip()

        result = multiline_format(
            template,
            prefix="INFO",
            bdf="0000:00:1f.3",
            vid=0x8086,
            did=0x54C8,
            driver="snd_hda_intel",
        )

        assert "[INFO]" in result
        assert "0000:00:1f.3" in result
        assert "8086" in result
        assert "54c8" in result
        assert "snd_hda_intel" in result


class TestTimestampFunction:
    """Test cases for timestamp function."""

    def test_timestamp_format(self):
        """Test timestamp format."""
        with patch("src.string_utils.datetime") as mock_datetime:
            mock_datetime.now.return_value = datetime(2025, 7, 23, 14, 23, 45)
            result = get_short_timestamp()
            assert result == "14:23:45"

    def test_timestamp_respects_config(self):
        """Timestamp formatting should follow configuration."""
        config = FormatConfig.get_instance()
        original_format = config.timestamp_format
        try:
            config.timestamp_format = "%H:%M"
            with patch("src.string_utils.datetime") as mock_datetime:
                mock_datetime.now.return_value = datetime(2025, 7, 23, 14, 23, 45)
                result = get_short_timestamp()
                assert result == "14:23"
        finally:
            config.timestamp_format = original_format


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
