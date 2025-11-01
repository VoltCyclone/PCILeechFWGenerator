"""Test script to verify version checking functionality."""

import os
from unittest.mock import Mock, patch

import pytest

from src.__version__ import __version__
from src.cli.version_checker import (check_and_notify, check_for_updates,
                                     fetch_latest_version, is_newer_version,
                                     parse_version)


class TestVersionParsing:
    """Test version parsing functionality."""

    def test_parse_version_basic(self):
        """Test basic version parsing."""
        assert parse_version("0.5.8") == (0, 5, 8)
        assert parse_version("1.2.3") == (1, 2, 3)
        assert parse_version("10.20.30") == (10, 20, 30)

    def test_parse_version_with_v_prefix(self):
        """Test parsing versions with 'v' prefix."""
        assert parse_version("v0.5.8") == (0, 5, 8)
        assert parse_version("v1.2.3") == (1, 2, 3)

    def test_parse_version_invalid(self):
        """Test parsing invalid versions."""
        assert parse_version("invalid") == (0, 0, 0)
        assert parse_version("") == (0, 0, 0)


class TestVersionComparison:
    """Test version comparison functionality."""

    def test_is_newer_version_newer(self):
        """Test detecting newer versions."""
        assert is_newer_version("0.5.7", "0.5.8") is True
        assert is_newer_version("0.5.8", "0.6.0") is True
        assert is_newer_version("0.9.9", "1.0.0") is True

    def test_is_newer_version_same(self):
        """Test same version comparison."""
        assert is_newer_version("0.5.8", "0.5.8") is False
        assert is_newer_version("1.0.0", "1.0.0") is False

    def test_is_newer_version_older(self):
        """Test detecting older versions."""
        assert is_newer_version("0.5.8", "0.5.7") is False
        assert is_newer_version("1.0.0", "0.9.9") is False

    def test_is_newer_version_with_prefix(self):
        """Test version comparison with v prefix."""
        assert is_newer_version("v0.5.7", "v0.5.8") is True
        assert is_newer_version("0.5.7", "v0.5.8") is True
        assert is_newer_version("v0.5.8", "0.5.7") is False


class TestNonBlockingBehavior:
    """Test that version checking never blocks the main program."""

    @patch("src.cli.version_checker.fetch_latest_version")
    def test_network_failure_does_not_block(self, mock_fetch):
        """Test that network failures don't block execution."""
        mock_fetch.side_effect = Exception("Network error")

        # Should return None without raising
        result = check_for_updates(force=True)
        assert result is None

    @patch("src.cli.version_checker.urlopen")
    def test_timeout_does_not_block(self, mock_urlopen):
        """Test that timeouts don't block execution."""
        mock_urlopen.side_effect = TimeoutError("Request timed out")

        # Should return None without raising
        result = fetch_latest_version()
        assert result is None

    @patch("src.cli.version_checker.check_for_updates")
    @patch("src.cli.version_checker.prompt_for_update")
    def test_check_and_notify_handles_errors(self, mock_prompt, mock_check):
        """Test that check_and_notify handles all errors gracefully."""
        # Test with various error scenarios
        test_cases = [
            Exception("Generic error"),
            ValueError("Invalid value"),
            KeyError("Missing key"),
            None,  # No error, but no update
        ]

        for error in test_cases:
            if error:
                mock_check.side_effect = error
            else:
                mock_check.return_value = None

            # Should not raise any exception
            try:
                check_and_notify()
            except Exception as e:
                pytest.fail(f"check_and_notify raised {type(e).__name__}: {e}")

    def test_cache_write_failure_does_not_block(self):
        """Test that cache write failures don't block execution."""
        from src.cli.version_checker import save_cache

        # Mock the CACHE_FILE to raise permission error
        with patch("src.cli.version_checker.CACHE_FILE") as mock_cache_file:
            mock_cache_file.parent.mkdir.side_effect = PermissionError("No permission")

            # Should handle gracefully without raising
            try:
                save_cache("1.0.0", True)
                # Function should complete without raising
            except Exception as e:
                pytest.fail(f"save_cache raised {type(e).__name__}: {e}")


class TestIntegration:
    """Integration tests for version checking."""

    @patch("src.cli.version_checker.fetch_latest_version")
    @patch.dict(os.environ, {"CI": "", "PCILEECH_DISABLE_UPDATE_CHECK": ""}, clear=True)
    def test_update_available_flow(self, mock_fetch):
        """Test the flow when an update is available."""
        mock_fetch.return_value = "99.0.0"  # Much newer version

        result = check_for_updates(force=True)
        assert result is not None
        latest_version, update_available = result
        assert latest_version == "99.0.0"
        assert update_available is True

    @patch("src.cli.version_checker.fetch_latest_version")
    @patch.dict(os.environ, {"CI": "", "PCILEECH_DISABLE_UPDATE_CHECK": ""}, clear=True)
    def test_no_update_needed_flow(self, mock_fetch):
        """Test the flow when no update is needed."""
        mock_fetch.return_value = __version__  # Same version

        result = check_for_updates(force=True)
        assert result is not None
        latest_version, update_available = result
        assert latest_version == __version__
        assert update_available is False
