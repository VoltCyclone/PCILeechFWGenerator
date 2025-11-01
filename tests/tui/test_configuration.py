"""
Tests for the configuration and plugin system.
"""

import os
import tempfile
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

from src.tui.models.configuration import VALID_BOARD_TYPES, BuildConfiguration
from src.tui.plugins.plugin_base import PCILeechPlugin, SimplePlugin
from src.tui.plugins.plugin_manager import PluginManager


class TestBuildConfiguration:
    """Tests for the BuildConfiguration Pydantic model."""

    def test_basic_configuration(self):
        """Test creating a basic configuration."""
        config = BuildConfiguration(
            name="Test Config", board_type="pcileech_75t484_x1", device_type="network"
        )

        assert config.name == "Test Config"
        assert config.board_type == "pcileech_75t484_x1"
        assert config.device_type == "network"
        assert config.advanced_sv is True  # Default value

    def test_timestamp_automatic_setting(self):
        """Test automatic setting of timestamps."""
        config = BuildConfiguration(name="Test Config", board_type="pcileech_75t484_x1")

        # Timestamps should be automatically set
        assert config.created_at is not None
        assert config.last_used is not None

    def test_dict_conversion(self):
        """Test conversion to and from dictionary."""
        original = BuildConfiguration(
            name="Test Config",
            board_type="pcileech_75t484_x1",
            device_type="network",
            enable_performance_counters=True,
            custom_parameters={"test_param": "value"},
        )

        # Convert to dictionary
        config_dict = original.to_dict()

        # Convert back to object
        recreated = BuildConfiguration.from_dict(config_dict)

        # Check values are preserved
        assert recreated.name == original.name
        assert recreated.board_type == original.board_type
        assert recreated.device_type == original.device_type
        assert (
            recreated.enable_performance_counters
            == original.enable_performance_counters
        )
        assert (
            recreated.custom_parameters["test_param"]
            == original.custom_parameters["test_param"]
        )

    def test_file_persistence(self):
        """Test saving and loading from file."""
        config = BuildConfiguration(
            name="Test Config", board_type="pcileech_75t484_x1", device_type="network"
        )

        # Create a temporary file
        with tempfile.NamedTemporaryFile(delete=False, suffix=".json") as temp_file:
            temp_path = Path(temp_file.name)

        try:
            # Save to file
            config.save_to_file(temp_path)

            # Load from file
            loaded_config = BuildConfiguration.load_from_file(temp_path)

            # Check values are preserved
            assert loaded_config.name == config.name
            assert loaded_config.board_type == config.board_type
            assert loaded_config.device_type == config.device_type
        finally:
            # Clean up
            if temp_path.exists():
                os.unlink(temp_path)
