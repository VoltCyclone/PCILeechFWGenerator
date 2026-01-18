import json
import os
from unittest import mock

import pytest

from ..models.config import BuildConfiguration
from .config_manager import ConfigManager


@pytest.fixture
def config_manager():
    with mock.patch("pathlib.Path.mkdir"), mock.patch("os.chmod"):
        manager = ConfigManager()
        # Patch _migrate_old_profiles to avoid side effects
        with mock.patch.object(manager, "_migrate_old_profiles"):
            yield manager


@pytest.fixture
def mock_config_dir(tmp_path):
    """Create a temporary directory for config files"""
    config_dir = tmp_path / "profiles"
    config_dir.mkdir(parents=True, exist_ok=True)
    return config_dir


@pytest.fixture
def sample_config():
    """Create a sample BuildConfiguration for testing"""
    return BuildConfiguration(
        name="Test Profile",
        description="Test Description",
        board_type="pcileech_35t325_x1",
        advanced_sv=True,
        enable_variance=True,
        behavior_profiling=False,
        profile_duration=30.0,
        power_management=True,
        error_handling=True,
        performance_counters=True,
        flash_after_build=False,
    )


class TestConfigManager:
    def test_init(self):
        """Test initialization of ConfigManager"""
        with mock.patch("pathlib.Path.mkdir") as mock_mkdir, mock.patch(
            "os.chmod"
        ) as mock_chmod:
            manager = ConfigManager()
            mock_mkdir.assert_called_once()
            if os.name != "nt":
                mock_chmod.assert_called_once()

    def test_get_current_config_creates_default(self, config_manager):
        """Test that get_current_config creates a default config if none exists"""
        config = config_manager.get_current_config()
        assert isinstance(config, BuildConfiguration)
        assert config_manager._current_config is not None

    def test_set_current_config(self, config_manager, sample_config):
        """Test setting the current configuration"""
        config_manager.set_current_config(sample_config)
        assert config_manager._current_config == sample_config
        assert config_manager._current_config.last_used is not None

    def test_ensure_config_directory(self, config_manager):
        """Test directory creation"""
        with mock.patch("pathlib.Path.mkdir") as mock_mkdir:
            config_manager._ensure_config_directory()
            mock_mkdir.assert_called_once_with(parents=True, exist_ok=True)

    def test_ensure_config_directory_permission_error(self, config_manager):
        """Test handling of permission error during directory creation"""
        with mock.patch(
            "pathlib.Path.mkdir", side_effect=PermissionError("Permission denied")
        ):
            with pytest.raises(PermissionError):
                config_manager._ensure_config_directory()

    def test_sanitize_filename(self, config_manager):
        """Test filename sanitization"""
        assert (
            config_manager._sanitize_filename('test<>:"/\\|?*file')
            == "test________file"
        )
        assert config_manager._sanitize_filename(" . test . ") == "test"
        assert config_manager._sanitize_filename("") == "unnamed_profile"

    def test_save_profile(self, config_manager, sample_config, mock_config_dir):
        """Test saving a profile"""
        with mock.patch.object(
            config_manager, "_ensure_config_directory"
        ), mock.patch.object(
            config_manager, "config_dir", mock_config_dir
        ), mock.patch.object(
            sample_config, "save_to_file"
        ) as mock_save:

            result = config_manager.save_profile("Test Profile", sample_config)
            assert result is True
            mock_save.assert_called_once()
            assert sample_config.name == "Test Profile"
            assert sample_config.created_at is not None
            assert sample_config.last_used is not None

    def test_save_profile_permission_error(self, config_manager, sample_config):
        """Test handling permission error when saving profile"""
        with mock.patch.object(
            config_manager, "_ensure_config_directory"
        ), mock.patch.object(
            sample_config,
            "save_to_file",
            side_effect=PermissionError("Permission denied"),
        ):

            result = config_manager.save_profile("Test Profile", sample_config)
            assert result is False

    def test_load_profile_not_found(self, config_manager, mock_config_dir):
        """Test loading a non-existent profile"""
        with mock.patch.object(
            config_manager, "_ensure_config_directory"
        ), mock.patch.object(config_manager, "config_dir", mock_config_dir):

            result = config_manager.load_profile("NonExistentProfile")
            assert result is None

    def test_load_profile(self, config_manager, sample_config, mock_config_dir):
        """Test loading an existing profile"""
        profile_path = mock_config_dir / "test_profile.json"

        # Create a test profile file
        with mock.patch.object(
            config_manager, "_ensure_config_directory"
        ), mock.patch.object(
            config_manager, "config_dir", mock_config_dir
        ), mock.patch.object(
            BuildConfiguration, "load_from_file", return_value=sample_config
        ), mock.patch.object(
            config_manager, "save_profile", return_value=True
        ), mock.patch(
            "pathlib.Path.exists", return_value=True
        ):

            result = config_manager.load_profile("test_profile")
            assert result == sample_config
            assert result.last_used is not None

    def test_profile_exists(self, config_manager, mock_config_dir):
        """Test checking if a profile exists"""
        with mock.patch.object(
            config_manager, "config_dir", mock_config_dir
        ), mock.patch("pathlib.Path.exists", return_value=True):

            assert config_manager.profile_exists("test_profile") is True

    def test_delete_profile(self, config_manager, mock_config_dir):
        """Test deleting a profile"""
        with mock.patch.object(
            config_manager, "config_dir", mock_config_dir
        ), mock.patch("pathlib.Path.exists", return_value=True), mock.patch(
            "pathlib.Path.unlink"
        ) as mock_unlink:

            result = config_manager.delete_profile("test_profile")
            assert result is True
            mock_unlink.assert_called_once()

    def test_delete_profile_not_found(self, config_manager, mock_config_dir):
        """Test deleting a non-existent profile"""
        with mock.patch.object(
            config_manager, "config_dir", mock_config_dir
        ), mock.patch("pathlib.Path.exists", return_value=False):

            result = config_manager.delete_profile("nonexistent")
            assert result is False

    def test_list_profiles(self, config_manager, mock_config_dir):
        """Test listing profiles"""
        mock_files = [
            mock_config_dir / "profile1.json",
            mock_config_dir / "profile2.json",
        ]
        mock_data = {
            "name": "Test Profile",
            "description": "Test Description",
            "created_at": "2023-01-01T12:00:00",
            "last_used": "2023-01-02T12:00:00",
        }

        with mock.patch.object(
            config_manager, "_ensure_config_directory"
        ), mock.patch.object(config_manager, "config_dir", mock_config_dir), mock.patch(
            "pathlib.Path.glob", return_value=mock_files
        ), mock.patch(
            "builtins.open", mock.mock_open(read_data=json.dumps(mock_data))
        ):

            profiles = config_manager.list_profiles()
            assert len(profiles) == 2
            assert profiles[0]["name"] == "Test Profile"
            assert profiles[0]["description"] == "Test Description"
            assert "filename" in profiles[0]

    def test_validate_config(self, config_manager, sample_config):
        """Test configuration validation"""
        # Valid configuration
        issues = config_manager.validate_config(sample_config)
        assert len(issues) == 0

        # Invalid configuration - behavior profiling with short duration
        sample_config.behavior_profiling = True
        sample_config.profile_duration = 5
        issues = config_manager.validate_config(sample_config)
        assert len(issues) == 1
        assert "duration" in issues[0]

        # Invalid configuration - 35t board with advanced features
        sample_config.behavior_profiling = False
        sample_config.board_type = "35t"
        sample_config.advanced_sv = True
        issues = config_manager.validate_config(sample_config)
        assert len(issues) == 1
        assert "35t board" in issues[0]

    def test_create_default_profiles(self, config_manager):
        """Test creating default profiles"""
        with mock.patch.object(
            config_manager, "_ensure_config_directory"
        ), mock.patch.object(
            config_manager, "profile_exists", return_value=False
        ), mock.patch.object(
            config_manager, "save_profile", return_value=True
        ):

            result = config_manager.create_default_profiles()
            assert result is True

    def test_get_profile_summary(self, config_manager, sample_config):
        """Test getting profile summary"""
        with mock.patch.object(
            config_manager, "load_profile", return_value=sample_config
        ):
            summary = config_manager.get_profile_summary("test_profile")
            assert summary["name"] == sample_config.name
            assert summary["description"] == sample_config.description
            assert summary["board_type"] == sample_config.board_type
            assert "features" in summary

        # Test with profile not found
        with mock.patch.object(config_manager, "load_profile", return_value=None):
            summary = config_manager.get_profile_summary("nonexistent")
            assert "error" in summary
            assert summary["error"] == "Profile not found"

    def test_export_profile(self, config_manager, sample_config, tmp_path):
        """Test exporting a profile"""
        export_path = tmp_path / "exported_profile.json"

        with mock.patch.object(
            config_manager, "load_profile", return_value=sample_config
        ), mock.patch.object(sample_config, "save_to_file") as mock_save:

            result = config_manager.export_profile("test_profile", export_path)
            assert result is True
            mock_save.assert_called_once_with(export_path)

    def test_import_profile(self, config_manager, sample_config, tmp_path):
        """Test importing a profile"""
        import_path = tmp_path / "import_profile.json"

        with mock.patch("pathlib.Path.exists", return_value=True), mock.patch.object(
            BuildConfiguration, "load_from_file", return_value=sample_config
        ), mock.patch.object(
            config_manager, "profile_exists", return_value=False
        ), mock.patch.object(
            config_manager, "save_profile", return_value=True
        ):

            result = config_manager.import_profile(import_path)
            assert result is not None

        # Test with file not found
        with mock.patch("pathlib.Path.exists", return_value=False):
            result = config_manager.import_profile(import_path)
            assert result is None
