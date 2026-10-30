"""
Tests for the configuration and plugin system.
"""

import json
import os
import tempfile
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

from pcileechfwgenerator.tui.models.configuration import VALID_BOARD_TYPES, BuildConfiguration
from pcileechfwgenerator.tui.plugins.plugin_base import PCILeechPlugin, SimplePlugin
from pcileechfwgenerator.tui.plugins.plugin_manager import PluginManager


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
        assert config.advanced_sv is False  # Unsupported override defaults off

    def test_configuration_validation(self):
        """Test validation of configuration values."""
        # Valid configuration
        config = BuildConfiguration(name="Test Config", board_type="pcileech_75t484_x1")
        assert config.name == "Test Config"

        # Invalid board type should raise an error
        with pytest.raises(ValueError) as exc_info:
            BuildConfiguration(name="Test Config", board_type="invalid_board")
        assert "Invalid board type" in str(exc_info.value)

        # Empty name should raise an error
        with pytest.raises(ValueError) as exc_info:
            BuildConfiguration(name="", board_type="pcileech_75t484_x1")
        assert "Configuration name cannot be empty" in str(exc_info.value)

    def test_profiling_does_not_require_unavailable_advanced_sv_override(self):
        config = BuildConfiguration(
            board_type="pcileech_75t484_x1",
            advanced_sv=False,
            behavior_profiling=True,
            local_build=True,
        )
        assert config.behavior_profiling

    def test_profile_duration_validation(self):
        """Test validation of profile duration."""
        # Valid duration
        config = BuildConfiguration(
            name="Test Config", board_type="pcileech_75t484_x1", profile_duration=60.0
        )
        assert config.profile_duration == 60.0

        # Zero duration should raise an error
        with pytest.raises(ValueError) as exc_info:
            BuildConfiguration(
                name="Test Config",
                board_type="pcileech_75t484_x1",
                profile_duration=0.0,
            )
        assert "Profile duration must be positive" in str(exc_info.value)

        # Negative duration should raise an error
        with pytest.raises(ValueError) as exc_info:
            BuildConfiguration(
                name="Test Config",
                board_type="pcileech_75t484_x1",
                profile_duration=-10.0,
            )
        assert "Profile duration must be positive" in str(exc_info.value)

        # Too long duration should raise an error
        with pytest.raises(ValueError) as exc_info:
            BuildConfiguration(
                name="Test Config",
                board_type="pcileech_75t484_x1",
                profile_duration=4000.0,
            )
        assert "Profile duration too long" in str(exc_info.value)

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


class TestPluginSystem:
    """Tests for the plugin system."""

    def test_plugin_manager_registration(self):
        """Test registering plugins with the manager."""
        manager = PluginManager()

        # Create a simple plugin
        plugin = SimplePlugin(
            name="Test Plugin", version="1.0.0", description="A test plugin"
        )

        # Register the plugin
        success = manager.register_plugin("test_plugin", plugin)
        assert success is True

        # Retrieve the plugin
        retrieved = manager.get_plugin("test_plugin")
        assert retrieved is plugin
        assert retrieved.get_name() == "Test Plugin"
        assert retrieved.get_version() == "1.0.0"

    def test_plugin_component_retrieval(self):
        """Test retrieving plugin components."""
        manager = PluginManager()

        # Create a mock device analyzer
        analyzer = MagicMock()
        analyzer.analyze_device.return_value = {"test": "result"}
        analyzer.get_name.return_value = "Test Analyzer"

        # Create a plugin with the analyzer
        plugin = SimplePlugin(
            name="Test Plugin",
            version="1.0.0",
            description="A test plugin",
            device_analyzer=analyzer,
        )

        # Register the plugin
        manager.register_plugin("test_plugin", plugin)

        # Get device analyzers
        analyzers = manager.get_device_analyzers()
        assert len(analyzers) == 1
        assert analyzers[0] is analyzer

        # Test the analyzer
        result = analyzers[0].analyze_device("test_device", b"test_data")
        assert result == {"test": "result"}

    def test_plugin_lifecycle(self):
        """Test plugin initialization and shutdown."""
        manager = PluginManager()

        # Create a plugin with lifecycle tracking
        plugin = MagicMock(spec=PCILeechPlugin)
        plugin.get_name.return_value = "Test Plugin"
        plugin.get_version.return_value = "1.0.0"
        plugin.get_description.return_value = "A test plugin"
        plugin.initialize.return_value = True

        # Register the plugin
        manager.register_plugin("test_plugin", plugin)

        # Set app context
        context = {"app_name": "PCILeech TUI"}
        manager.set_app_context(context)

        # Check initialize was called
        plugin.initialize.assert_called_once_with(context)

        # Shutdown the plugin
        manager.unregister_plugin("test_plugin")
        plugin.shutdown.assert_called_once()


def test_legacy_profile_is_migrated_with_warnings():
    from pcileechfwgenerator.tui.core.config_manager import migrate_legacy_configuration
    from pcileechfwgenerator.tui.core.build_contract import validate_build_configuration

    original = dict(
        board_type="pcileech_75t484_x1",
        advanced_sv=True,
        enable_variance=True,
        donor_dump=True,
    )
    data, warnings = migrate_legacy_configuration(original)
    assert validate_build_configuration(BuildConfiguration(**data)) is None
    assert len(warnings) == 3
    assert original["advanced_sv"] is True
    assert migrate_legacy_configuration(data) == (data, [])
    assert migrate_legacy_configuration({}) == ({}, [])


@pytest.mark.parametrize("imported", [False, True])
def test_saved_legacy_profile_loads_with_warnings(
    tmp_path, monkeypatch, capsys, imported
):
    from pcileechfwgenerator.tui.core import config_manager
    from pcileechfwgenerator.tui.core.build_contract import validate_build_configuration

    monkeypatch.setattr(config_manager, "CACHE_DIR", tmp_path)
    monkeypatch.setattr(
        config_manager.ConfigManager, "_migrate_old_profiles", lambda self: None
    )
    manager = config_manager.ConfigManager()
    path = manager.config_dir / "legacy.json"
    path.write_text(
        json.dumps(
            dict(
                board_type="pcileech_75t484_x1",
                advanced_sv=True,
                enable_variance=True,
                donor_dump=True,
            )
        )
    )
    name = manager.import_profile(path) if imported else "legacy"
    config = manager.load_profile(name)
    assert config is not None
    assert validate_build_configuration(config) is None
    warnings = capsys.readouterr().out
    assert all(
        field in warnings for field in ("advanced_sv", "enable_variance", "donor_dump")
    )


def test_import_profile_drops_unknown_legacy_keys(tmp_path, monkeypatch):
    """Profiles from older versions with retired keys must still import."""
    from pcileechfwgenerator.tui.core import config_manager

    monkeypatch.setattr(config_manager, "CACHE_DIR", tmp_path)
    monkeypatch.setattr(
        config_manager.ConfigManager, "_migrate_old_profiles", lambda self: None
    )
    manager = config_manager.ConfigManager()
    path = tmp_path / "old.json"
    path.write_text(
        json.dumps(
            dict(board_type="pcileech_75t484_x1", advanced_sv=True, retired_key=1)
        )
    )
    name = manager.import_profile(path)
    assert name is not None
    assert manager.load_profile(name).advanced_sv is False
