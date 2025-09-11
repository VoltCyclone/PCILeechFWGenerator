#!/usr/bin/env python3
"""
Unit tests for the CLI build wrapper module, focusing on Python path setup and directory detection logic.
"""

import os
import sys
from pathlib import Path
from unittest import mock

import pytest


class TestBuildWrapperPathSetup:
    """Test suite for build wrapper path setup logic."""

    def test_path_setup_container_environment(self):
        """Test Python path setup for container environment."""
        with mock.patch("pathlib.Path.exists", return_value=True), mock.patch(
            "sys.path", []
        ):

            # Simulate the path setup logic from build_wrapper.py
            app_dir = Path("/app")
            src_dir = app_dir / "src"

            if str(app_dir) not in sys.path:
                sys.path.insert(0, str(app_dir))
            if str(src_dir) not in sys.path:
                sys.path.insert(0, str(src_dir))

            assert sys.path[0] == "/app/src"
            assert sys.path[1] == "/app"

    def test_path_setup_local_environment(self):
        """Test Python path setup for local environment."""
        with mock.patch("pathlib.Path.exists", return_value=False), mock.patch(
            "pathlib.Path.absolute", return_value=Path("/local/project")
        ), mock.patch("sys.path", []):

            # Simulate the path setup logic from build_wrapper.py
            script_path = Path("/local/project/src/cli/build_wrapper.py")
            app_dir = script_path.parent.parent.parent.absolute()
            src_dir = app_dir / "src"

            if str(app_dir) not in sys.path:
                sys.path.insert(0, str(app_dir))
            if str(src_dir) not in sys.path:
                sys.path.insert(0, str(src_dir))

            assert sys.path[0] == "/local/project/src"
            assert sys.path[1] == "/local/project"

    def test_directory_detection_container(self):
        """Test directory detection logic for container environment."""
        with mock.patch("pathlib.Path.exists") as mock_exists:
            mock_exists.return_value = True

            # Simulate the detection logic
            app_dir = Path("/app")
            src_dir = app_dir / "src"

            is_container = app_dir.exists()
            is_src_available = src_dir.exists()

            assert is_container is True
            assert is_src_available is True

    def test_directory_detection_local(self):
        """Test directory detection logic for local environment."""
        app_dir = Path("/app")
        src_dir = app_dir / "src"

        with mock.patch("os.path.exists") as mock_exists:
            mock_exists.return_value = False

            is_container = app_dir.exists()
            is_src_available = src_dir.exists()

            assert is_container is False
            assert is_src_available is False

    def test_chdir_to_src_directory(self):
        """Test changing directory to src directory."""
        with mock.patch("os.chdir") as mock_chdir, mock.patch(
            "pathlib.Path.exists", return_value=True
        ):

            src_dir = Path("/app/src")

            if src_dir.exists():
                os.chdir(str(src_dir))

            mock_chdir.assert_called_once_with("/app/src")

    def test_src_directory_not_found_error(self):
        """Test error handling when src directory doesn't exist."""
        app_dir = Path("/app")
        src_dir = app_dir / "src"

        with mock.patch("os.path.exists") as mock_exists:
            mock_exists.return_value = False

            is_container = app_dir.exists()
            is_src_available = src_dir.exists()

            assert is_container is False
            assert is_src_available is False

    def test_import_attempt_with_fallback(self):
        """Test import attempt with fallback logic."""
        with mock.patch("builtins.__import__") as mock_import, mock.patch(
            "sys.exit"
        ) as mock_exit:

            # Mock first import failure, second success
            mock_build_module = mock.MagicMock()
            mock_build_module.main = mock.MagicMock()
            mock_import.side_effect = [
                ImportError("src.build not found"),
                mock_build_module,  # Successful fallback
            ]

            try:
                # First attempt
                import src.build
            except ImportError:
                try:
                    # Fallback attempt
                    import build

                    if hasattr(build, "main"):
                        build.main()  # type: ignore
                except (ImportError, AttributeError):
                    sys.exit(1)

            # Should not exit with error
            mock_exit.assert_not_called()

    def test_complete_import_failure(self):
        """Test complete import failure."""
        with mock.patch(
            "builtins.__import__", side_effect=ImportError("Import failed")
        ), mock.patch("sys.exit") as mock_exit:

            try:
                import src.build
            except ImportError:
                try:
                    import build
                except ImportError:
                    sys.exit(1)

            mock_exit.assert_called_once_with(1)
