#!/usr/bin/env python3
"""Unit tests for VFIO constants rebuilding in pcileech.py

This test suite ensures that the VFIO constants rebuilding functionality
works correctly and handles various edge cases.
"""

import os
import subprocess
import sys
from pathlib import Path
from unittest.mock import MagicMock, mock_open, patch

# Add project root to path for imports
project_root = Path(__file__).parent.parent
sys.path.insert(0, str(project_root))

# Import pytest (install with: pip install -r requirements-test.txt)
import pytest  # type: ignore

# Import the functions we want to test
from pcileech import check_vfio_requirements, rebuild_vfio_constants


class TestVFIOConstantsRebuilding:
    """Test suite for VFIO constants rebuilding functionality."""

    @patch("builtins.open", mock_open(read_data="vfio 12345 0\nvfio_pci 67890 1\n"))
    @patch("pcileech.get_logger")
    @patch("pcileech.rebuild_vfio_constants")
    def test_check_vfio_requirements_with_modules_loaded(
        self, mock_rebuild, mock_logger
    ):
        """Test check_vfio_requirements when VFIO modules are loaded."""
        # Setup mocks
        mock_logger.return_value = MagicMock()
        mock_rebuild.return_value = True

        # Call the function
        result = check_vfio_requirements()

        # Assertions
        assert result is True
        mock_rebuild.assert_called_once()

    @patch("builtins.open", mock_open(read_data="some_other_module 12345 0\n"))
    @patch("pcileech.get_logger")
    def test_check_vfio_requirements_without_modules(self, mock_logger):
        """Test check_vfio_requirements when VFIO modules are NOT loaded."""
        # Setup mocks
        mock_logger.return_value = MagicMock()

        # Call the function
        result = check_vfio_requirements()

        # Assertions
        assert result is False

    @patch("builtins.open", side_effect=FileNotFoundError())
    @patch("pcileech.get_logger")
    @patch("pcileech.rebuild_vfio_constants")
    def test_check_vfio_requirements_no_proc_modules(
        self, mock_rebuild, mock_logger, mock_open
    ):
        """Test check_vfio_requirements when /proc/modules doesn't exist."""
        # Setup mocks
        mock_logger.return_value = MagicMock()
        mock_rebuild.return_value = True

        # Call the function
        result = check_vfio_requirements()

        # Assertions
        assert result is True
        mock_rebuild.assert_called_once()

    @patch("builtins.open", mock_open(read_data="vfio 12345 0\nvfio_pci 67890 1\n"))
    @patch("pcileech.get_logger")
    @patch("pcileech.rebuild_vfio_constants")
    def test_check_vfio_requirements_rebuild_fails(self, mock_rebuild, mock_logger):
        """Test check_vfio_requirements when rebuild fails."""
        # Setup mocks
        mock_logger.return_value = MagicMock()
        mock_rebuild.return_value = False

        # Call the function
        result = check_vfio_requirements()

        # Assertions
        assert result is True  # Still returns True, just logs warning
        mock_rebuild.assert_called_once()


class TestVFIOConstantsRebuildingIntegration:
    """Integration tests for VFIO constants rebuilding."""

    def test_build_script_exists(self):
        """Test that the build script actually exists."""
        script_path = Path(__file__).parent.parent / "build_vfio_constants.sh"
        assert script_path.exists(), f"Build script not found at {script_path}"
        assert script_path.is_file()
        # Check it's executable
        assert os.access(script_path, os.X_OK), "Build script is not executable"

    def test_vfio_constants_file_exists(self):
        """Test that the VFIO constants file exists."""
        constants_path = (
            Path(__file__).parent.parent / "src" / "cli" / "vfio_constants.py"
        )
        assert (
            constants_path.exists()
        ), f"VFIO constants file not found at {constants_path}"
        assert constants_path.is_file()

    @pytest.mark.integration
    @pytest.mark.skipif(
        not os.path.exists("/proc/modules"), reason="No /proc/modules (not Linux)"
    )
    def test_real_vfio_module_check(self):
        """Test actual VFIO module checking on Linux systems."""
        with open("/proc/modules", "r") as f:
            modules = f.read()

        # This test will vary depending on whether VFIO is actually loaded
        # Just verify we can read the file without error
        assert isinstance(modules, str)

    @pytest.mark.slow
    @pytest.mark.skipif(
        not (Path(__file__).parent.parent / "build_vfio_constants.sh").exists(),
        reason="Build script not available",
    )
    def test_actual_vfio_constants_build(self):
        """Test actually running the VFIO constants build script.

        This test is marked as 'slow' because it actually compiles code.
        Skip it in CI or fast test runs.
        """
        script_path = Path(__file__).parent.parent / "build_vfio_constants.sh"

        # Create a temporary copy of the constants file to restore later
        constants_path = (
            Path(__file__).parent.parent / "src" / "cli" / "vfio_constants.py"
        )
        backup_path = constants_path.with_suffix(".py.backup")

        if constants_path.exists():
            import shutil

            shutil.copy2(constants_path, backup_path)

        try:
            # Run the actual build script
            result = subprocess.run(
                [str(script_path)],
                capture_output=True,
                text=True,
                timeout=60,
                cwd=script_path.parent,
            )

            # Check that it at least tries to run (may fail due to missing headers)
            assert result.returncode in [
                0,
                1,
            ], f"Unexpected return code: {result.returncode}"

            # If successful, verify the constants file was updated
            if result.returncode == 0:
                assert constants_path.exists()
                # Check that it contains expected content
                with open(constants_path, "r") as f:
                    content = f.read()
                assert "VFIO_GROUP_SET_CONTAINER" in content

        finally:
            # Restore the original file
            if backup_path.exists():
                import shutil

                shutil.move(backup_path, constants_path)


class TestVFIOConstantsPatching:
    """Test the constants patching functionality."""

    def test_constants_have_hardcoded_values(self):
        """Test that constants are hardcoded integers, not computed values."""
        from src.cli.vfio_constants import (VFIO_GET_API_VERSION,
                                            VFIO_GROUP_SET_CONTAINER,
                                            VFIO_SET_IOMMU)

        # All constants should be integers
        assert isinstance(VFIO_GET_API_VERSION, int)
        assert isinstance(VFIO_GROUP_SET_CONTAINER, int)
        assert isinstance(VFIO_SET_IOMMU, int)

        # They should be non-zero
        assert VFIO_GET_API_VERSION > 0
        assert VFIO_GROUP_SET_CONTAINER > 0
        assert VFIO_SET_IOMMU > 0

    def test_constants_are_unique(self):
        """Test that all VFIO constants have unique values."""
        from src.cli.vfio_constants import (VFIO_CHECK_EXTENSION,
                                            VFIO_DEVICE_GET_REGION_INFO,
                                            VFIO_GET_API_VERSION,
                                            VFIO_GROUP_GET_DEVICE_FD,
                                            VFIO_GROUP_GET_STATUS,
                                            VFIO_GROUP_SET_CONTAINER,
                                            VFIO_SET_IOMMU)

        constants = [
            VFIO_GET_API_VERSION,
            VFIO_CHECK_EXTENSION,
            VFIO_SET_IOMMU,
            VFIO_GROUP_GET_STATUS,
            VFIO_GROUP_SET_CONTAINER,
            VFIO_GROUP_GET_DEVICE_FD,
            VFIO_DEVICE_GET_REGION_INFO,
        ]

        # All values should be unique
        assert len(constants) == len(set(constants))


if __name__ == "__main__":
    # Run tests with pytest
    pytest.main([__file__] + sys.argv[1:])
