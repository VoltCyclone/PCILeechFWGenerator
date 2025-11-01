"""Tests for version checker functionality."""

import json
import os
import tempfile
from datetime import datetime, timedelta
from pathlib import Path
from unittest.mock import Mock, mock_open, patch

import pytest

from src.cli.version_checker import (CACHE_FILE, check_for_updates,
                                     fetch_latest_version_github,
                                     fetch_latest_version_pypi,
                                     get_cached_check, is_newer_version,
                                     parse_version, save_cache)


class TestVersionParsing:
    """Test version parsing and comparison."""

    def test_parse_version_basic(self):
        """Test basic version parsing."""
        assert parse_version("1.2.3") == (1, 2, 3)
        assert parse_version("0.5.8") == (0, 5, 8)
        assert parse_version("10.20.30") == (10, 20, 30)

    def test_parse_version_with_v_prefix(self):
        """Test parsing versions with 'v' prefix."""
        assert parse_version("v1.2.3") == (1, 2, 3)
        assert parse_version("v0.5.8") == (0, 5, 8)

    def test_parse_version_invalid(self):
        """Test parsing invalid versions."""
        assert parse_version("invalid") == (0, 0, 0)
        assert parse_version("") == (0, 0, 0)
        # Test with a non-string type converted to string
        assert parse_version(str(None)) == (0, 0, 0)

    def test_is_newer_version(self):
        """Test version comparison."""
        # Newer versions
        assert is_newer_version("0.5.7", "0.5.8") is True
        assert is_newer_version("0.5.8", "0.6.0") is True
        assert is_newer_version("0.5.8", "1.0.0") is True

        # Same version
        assert is_newer_version("0.5.8", "0.5.8") is False

        # Older versions
        assert is_newer_version("0.5.8", "0.5.7") is False
        assert is_newer_version("1.0.0", "0.9.9") is False

        # With v prefix
        assert is_newer_version("v0.5.7", "v0.5.8") is True
        assert is_newer_version("0.5.7", "v0.5.8") is True


class TestCaching:
    """Test version check caching."""

    def test_get_cached_check_no_file(self):
        """Test getting cache when file doesn't exist."""
        with patch.object(Path, "exists", return_value=False):
            assert get_cached_check() is None

    def test_get_cached_check_fresh(self):
        """Test getting fresh cache."""
        cache_data = {
            "last_check": datetime.now().isoformat(),
            "latest_version": "0.6.0",
            "current_version": "0.5.8",
            "update_available": True,
        }

        with patch.object(Path, "exists", return_value=True):
            with patch("builtins.open", mock_open(read_data=json.dumps(cache_data))):
                result = get_cached_check()
                assert result == cache_data

    def test_get_cached_check_stale(self):
        """Test getting stale cache."""
        old_date = datetime.now() - timedelta(days=2)
        cache_data = {
            "last_check": old_date.isoformat(),
            "latest_version": "0.6.0",
            "current_version": "0.5.8",
            "update_available": True,
        }

        with patch.object(Path, "exists", return_value=True):
            with patch("builtins.open", mock_open(read_data=json.dumps(cache_data))):
                assert get_cached_check() is None

    def test_save_cache(self):
        """Test saving cache."""
        with tempfile.TemporaryDirectory() as tmpdir:
            cache_file = Path(tmpdir) / "version_check.json"

            with patch("src.cli.version_checker.CACHE_FILE", cache_file):
                save_cache("0.6.0", True)

                assert cache_file.exists()
                with open(cache_file) as f:
                    data = json.load(f)

                assert data["latest_version"] == "0.6.0"
                assert data["update_available"] is True
                assert "last_check" in data
                assert "current_version" in data


class TestFetching:
    """Test fetching latest version from APIs."""

    @patch("src.cli.version_checker.urlopen")
    def test_fetch_latest_version_github_success(self, mock_urlopen):
        """Test successful GitHub API fetch."""
        mock_response = Mock()
        mock_response.read.return_value = json.dumps(
            {"tag_name": "v0.6.0", "name": "Release 0.6.0"}
        ).encode()
        mock_urlopen.return_value.__enter__.return_value = mock_response

        result = fetch_latest_version_github()
        assert result == "0.6.0"

    @patch("src.cli.version_checker.urlopen")
    def test_fetch_latest_version_github_failure(self, mock_urlopen):
        """Test GitHub API fetch failure."""
        mock_urlopen.side_effect = Exception("Network error")

        result = fetch_latest_version_github()
        assert result is None

    @patch("src.cli.version_checker.urlopen")
    def test_fetch_latest_version_pypi_success(self, mock_urlopen):
        """Test successful PyPI API fetch."""
        mock_response = Mock()
        mock_response.read.return_value = json.dumps(
            {"info": {"version": "0.6.0", "name": "pcileech-fw-generator"}}
        ).encode()
        mock_urlopen.return_value.__enter__.return_value = mock_response

        result = fetch_latest_version_pypi()
        assert result == "0.6.0"


class TestUpdateChecking:
    """Test the main update checking functionality."""

    def test_check_for_updates_ci_environment(self):
        """Test that update checks are skipped in CI."""
        with patch.dict(os.environ, {"CI": "true"}):
            result = check_for_updates()
            assert result is None

    def test_check_for_updates_disabled(self):
        """Test that update checks can be disabled."""
        with patch.dict(os.environ, {"PCILEECH_DISABLE_UPDATE_CHECK": "1"}):
            result = check_for_updates()
            assert result is None

    @patch("src.cli.version_checker.get_cached_check")
    @patch.dict(os.environ, {"CI": "", "PCILEECH_DISABLE_UPDATE_CHECK": ""}, clear=True)
    def test_check_for_updates_from_cache(self, mock_cache):
        """Test getting update info from cache."""
        mock_cache.return_value = {"latest_version": "0.6.0", "update_available": True}

        # Set environment to use cache
        with patch.dict(os.environ, {"PCILEECH_USE_CACHE": "1"}):
            result = check_for_updates(force=False)
            assert result == ("0.6.0", True)

    @patch("src.cli.version_checker.fetch_latest_version")
    @patch("src.cli.version_checker.save_cache")
    @patch("src.cli.version_checker.get_cached_check")
    @patch.dict(os.environ, {"CI": "", "PCILEECH_DISABLE_UPDATE_CHECK": ""}, clear=True)
    def test_check_for_updates_fresh_check(self, mock_cache, mock_save, mock_fetch):
        """Test fresh update check."""
        mock_cache.return_value = None
        mock_fetch.return_value = "0.6.0"

        with patch("src.cli.version_checker.__version__", "0.5.8"):
            result = check_for_updates(force=True)
            assert result == ("0.6.0", True)
            mock_save.assert_called_once_with("0.6.0", True)

    @patch("src.cli.version_checker.fetch_latest_version")
    @patch("src.cli.version_checker.save_cache")
    @patch.dict(os.environ, {"CI": "", "PCILEECH_DISABLE_UPDATE_CHECK": ""}, clear=True)
    def test_check_for_updates_no_update_needed(self, mock_save, mock_fetch):
        """Test when no update is needed."""
        mock_fetch.return_value = "0.5.8"

        with patch("src.cli.version_checker.__version__", "0.5.8"):
            result = check_for_updates(force=True)
            assert result == ("0.5.8", False)
            mock_save.assert_called_once_with("0.5.8", False)
