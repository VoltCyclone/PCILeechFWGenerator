#!/usr/bin/env python3
"""
Critical path tests for the require() validation function in pcileech_context.

Tests the error handling behavior of require(), which is the primary validation
function used throughout the build process to enforce preconditions. Proper
behavior is critical - failed validations must abort with SystemExit(2) and
clear error messages.
"""

import logging
from unittest.mock import patch

import pytest

from src.device_clone.pcileech_context import require


class TestRequireValidation:
    """Test suite for require() validation function critical paths."""

    def test_require_passes_with_true_condition(self):
        """Test require() allows execution to continue when condition is True."""
        # Should not raise any exception
        require(True, "This should not trigger an error")
        require(1 == 1, "Equality check should pass")
        require(bool("non-empty"), "Non-empty string should be truthy")

    def test_require_raises_system_exit_on_false_condition(self):
        """Test require() raises SystemExit(2) when condition is False."""
        with pytest.raises(SystemExit) as exc_info:
            require(False, "This should trigger SystemExit")

        assert exc_info.value.code == 2

    def test_require_raises_system_exit_on_zero(self):
        """Test require() raises SystemExit(2) when condition evaluates to 0."""
        with pytest.raises(SystemExit) as exc_info:
            require(0, "Zero should trigger SystemExit")

        assert exc_info.value.code == 2

    def test_require_raises_system_exit_on_none(self):
        """Test require() raises SystemExit(2) when condition is None."""
        with pytest.raises(SystemExit) as exc_info:
            require(None, "None should trigger SystemExit")

        assert exc_info.value.code == 2

    def test_require_raises_system_exit_on_empty_string(self):
        """Test require() raises SystemExit(2) when condition is empty string."""
        with pytest.raises(SystemExit) as exc_info:
            require("", "Empty string should trigger SystemExit")

        assert exc_info.value.code == 2

    def test_require_raises_system_exit_on_empty_list(self):
        """Test require() raises SystemExit(2) when condition is empty list."""
        with pytest.raises(SystemExit) as exc_info:
            require([], "Empty list should trigger SystemExit")

        assert exc_info.value.code == 2

    def test_require_raises_system_exit_on_empty_dict(self):
        """Test require() raises SystemExit(2) when condition is empty dict."""
        with pytest.raises(SystemExit) as exc_info:
            require({}, "Empty dict should trigger SystemExit")

        assert exc_info.value.code == 2

    @patch("src.device_clone.pcileech_context.log_error_safe")
    def test_require_logs_error_message_on_failure(self, mock_log):
        """Test require() logs error message with safe formatting on failure."""
        with pytest.raises(SystemExit):
            require(False, "Test error message")

        # Verify log_error_safe was called
        assert mock_log.called
        call_args = mock_log.call_args

        # Verify logger is passed
        assert isinstance(call_args[0][0], logging.Logger)

        # Verify message contains our error text
        message = call_args[0][1]
        assert "Test error message" in message
        assert "Build aborted" in message

    @patch("src.device_clone.pcileech_context.log_error_safe")
    def test_require_includes_context_in_error_message(self, mock_log):
        """Test require() includes context kwargs in error message."""
        with pytest.raises(SystemExit):
            require(
                False,
                "Missing device ID",
                vendor_id="0x10de",
                device_id=None,
                bdf="0000:01:00.0",
            )

        # Verify log_error_safe was called
        assert mock_log.called
        message = mock_log.call_args[0][1]

        # Verify context is in message
        assert "vendor_id" in message or "0x10de" in message

    @patch("src.device_clone.pcileech_context.log_error_safe")
    def test_require_uses_pcil_prefix(self, mock_log):
        """Test require() uses PCIL prefix for log messages."""
        with pytest.raises(SystemExit):
            require(False, "Test message")

        # Verify prefix keyword argument was passed
        call_kwargs = mock_log.call_args[1]
        assert "prefix" in call_kwargs
        assert call_kwargs["prefix"] == "PCIL"

    def test_require_real_world_vendor_id_validation(self):
        """Test require() with realistic vendor ID validation scenario."""
        # Simulate missing vendor_id
        vendor_id = None

        with pytest.raises(SystemExit) as exc_info:
            require(
                vendor_id is not None and vendor_id != "",
                "Missing vendor_id from config space data",
            )

        assert exc_info.value.code == 2

    def test_require_real_world_device_id_validation(self):
        """Test require() with realistic device ID validation scenario."""
        # Simulate empty device_id
        device_id = ""

        with pytest.raises(SystemExit) as exc_info:
            require(
                device_id is not None and device_id != "",
                "Missing device_id from config space data",
            )

        assert exc_info.value.code == 2

    def test_require_real_world_bar_validation(self):
        """Test require() with realistic BAR validation scenario."""
        # Simulate invalid BAR size
        bar_size = 0

        with pytest.raises(SystemExit) as exc_info:
            require(bar_size > 0, "Invalid BAR size", bar_index=0, size=bar_size)

        assert exc_info.value.code == 2

    def test_require_real_world_device_signature_validation(self):
        """Test require() with realistic device signature validation."""
        # Simulate missing device signature
        device_signature = None

        with pytest.raises(SystemExit) as exc_info:
            require(
                bool(device_signature),
                "device_signature missing",
            )

        assert exc_info.value.code == 2

    def test_require_allows_positive_integers(self):
        """Test require() passes with positive integer conditions."""
        require(1, "Positive integer should pass")
        require(42, "Any positive integer should pass")
        require(0xFFFF, "Hex values should pass")

    def test_require_allows_non_empty_strings(self):
        """Test require() passes with non-empty string conditions."""
        require("valid", "Non-empty string should pass")
        require("0x10de", "Device ID string should pass")
        require("0000:01:00.0", "BDF string should pass")

    def test_require_allows_non_empty_collections(self):
        """Test require() passes with non-empty collection conditions."""
        require([1, 2, 3], "Non-empty list should pass")
        require({"key": "value"}, "Non-empty dict should pass")
        require({1, 2, 3}, "Non-empty set should pass")
        require((1, 2), "Non-empty tuple should pass")

    def test_require_complex_condition_expression(self):
        """Test require() with complex boolean expressions."""
        vendor_id = "0x10de"
        device_id = "0x1234"

        # Should pass
        require(
            vendor_id is not None
            and device_id is not None
            and vendor_id != ""
            and device_id != "",
            "All identifiers must be present",
        )

    def test_require_complex_condition_expression_failure(self):
        """Test require() with complex boolean expressions that fail."""
        vendor_id = "0x10de"
        device_id = None  # Missing

        with pytest.raises(SystemExit):
            require(
                vendor_id is not None
                and device_id is not None
                and vendor_id != ""
                and device_id != "",
                "All identifiers must be present",
            )

    @patch("src.device_clone.pcileech_context.log_error_safe")
    def test_require_handles_context_with_special_characters(self, mock_log):
        """Test require() safely handles context with special characters."""
        with pytest.raises(SystemExit):
            require(
                False,
                "Error with special chars",
                path="/dev/vfio/0",
                error_msg="Failed: {error}",
                data={"key": "value"},
            )

        # Should not raise formatting errors
        assert mock_log.called

    @patch("src.device_clone.pcileech_context.log_error_safe")
    def test_require_handles_unicode_in_message(self, mock_log):
        """Test require() safely handles Unicode characters in messages."""
        with pytest.raises(SystemExit):
            require(False, "Error: Device not found → check configuration")

        # Should handle Unicode without errors
        assert mock_log.called
        message = mock_log.call_args[0][1]
        assert "→" in message or "Error" in message
