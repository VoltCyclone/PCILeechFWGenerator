"""
Configuration Manager

Manages build configuration profiles and persistence.
"""

import json
import logging
import os
import stat
from datetime import datetime
from pathlib import Path
from typing import Dict, List, Optional

try:
    from pydantic import ValidationError
except ImportError:
    # Define a fallback ValidationError for environments without pydantic
    class ValidationError(Exception):
        """Fallback ValidationError when pydantic is not available."""



# Import both the legacy and new Pydantic configuration models
from ..models.config import BuildConfiguration as LegacyBuildConfiguration
from ..models.configuration import BuildConfiguration
from ..models.error import ErrorSeverity, TUIError

# Set up logging
logger = logging.getLogger(__name__)

# Default cache directory for PCILeech
CACHE_DIR = Path(
    os.environ.get(
        "PCILEECH_CACHE_DIR", os.path.expanduser("~/.cache/pcileech-fw-generator")
    )
)


class ConfigManager:
    """Manages build configuration and profiles."""

    def __init__(self):
        self._current_config: Optional[BuildConfiguration] = None
        self.config_dir = CACHE_DIR / "profiles"
        self.old_config_dir = Path.home() / ".pcileech" / "profiles"
        try:
            # Create directory with appropriate permissions if it doesn't exist
            self._ensure_config_directory()

            # Migrate profiles from old directory if needed
            self._migrate_old_profiles()
        except Exception as e:
            # Log the error but continue - we'll handle file operations
            # gracefully later
            print(f"Warning: Could not initialize config directory: {str(e)}")

    def get_current_config(self) -> BuildConfiguration:
        """Get current configuration, creating default if none exists."""
        if self._current_config is None:
            self._current_config = BuildConfiguration()

        # If the current config is the legacy type, convert it to the new Pydantic model
        if isinstance(self._current_config, LegacyBuildConfiguration):
            self._current_config = BuildConfiguration(**self._current_config.to_dict())

        return self._current_config

    def set_current_config(self, config: BuildConfiguration) -> None:
        """Set current configuration."""
        self._current_config = config
        # Update last used timestamp
        self._current_config.last_used = datetime.now().isoformat()

    def _ensure_config_directory(self) -> None:
        """
        Ensure the configuration directory exists with proper permissions.
        Creates the directory if it doesn't exist.
        """
        try:
            # Create directory with parents if it doesn't exist
            self.config_dir.mkdir(parents=True, exist_ok=True)

            # Set appropriate permissions (read/write for user only)
            # This helps prevent permission issues on multi-user systems
            if (
                os.name != "nt"
            ):  # Skip on Windows as it uses a different permission model
                os.chmod(
                    self.config_dir,
                    stat.S_IRUSR | stat.S_IWUSR | stat.S_IXUSR,  # User: rwx
                )
        except PermissionError as e:
            raise PermissionError(
                f"Insufficient permissions to create or access config directory: {str(e)}"
            )
        except Exception as e:
            raise Exception(f"Failed to create config directory: {str(e)}")

    def _migrate_old_profiles(self) -> None:
        """
        Migrate profiles from old ~/.pcileech/profiles directory to new cache location.
        This ensures users don't lose their profiles when upgrading.
        """
        if not self.old_config_dir.exists():
            return

        try:
            # Check if old directory exists and has profiles
            if not self.old_config_dir.is_dir():
                return

            old_profiles = list(self.old_config_dir.glob("*.json"))
            if not old_profiles:
                return

            print(
                f"Migrating {len(old_profiles)} profiles from {self.old_config_dir} to {self.config_dir}"
            )

            # Copy each profile to the new location
            for profile_path in old_profiles:
                try:
                    # Read the old profile
                    with open(profile_path, "r") as f:
                        profile_data = json.load(f)

                    # Create a BuildConfiguration from the data using the new Pydantic model
                    try:
                        config = BuildConfiguration(**profile_data)
                    except ValidationError as e:
                        logger.warning(
                            f"Validation error in profile {profile_path.name}: {e}"
                        )
                        # Fall back to legacy model if validation fails
                        config = LegacyBuildConfiguration(**profile_data)

                    # Save to new location
                    profile_name = profile_path.stem
                    self.save_profile(profile_name, config)
                    print(f"Migrated profile: {profile_name}")
                except Exception as e:
                    print(f"Failed to migrate profile {profile_path.name}: {e}")

        except Exception as e:
            print(f"Warning: Profile migration failed: {e}")
            # Continue even if migration fails - this is just a convenience feature

    def save_profile(self, name: str, config: BuildConfiguration) -> bool:
        """
        Save configuration profile to ~/.pcileech/profiles/.

        Returns:
            Boolean indicating success
        """
        # Update metadata
        config.name = name

        # Update timestamps with appropriate method based on config type
        timestamp = datetime.now().isoformat()
        if isinstance(config, BuildConfiguration):
            # For Pydantic model, use the model's dict() method and update specific fields
            config_dict = config.dict()
            config_dict["created_at"] = config_dict.get("created_at") or timestamp
            config_dict["last_used"] = timestamp
            # Create a new instance with updated values
            config = BuildConfiguration(**config_dict)
        else:
            # For legacy model, set attributes directly
            config.created_at = config.created_at or timestamp
            config.last_used = timestamp

        try:
            # Ensure directory exists before saving
            self._ensure_config_directory()

            # Save to file
            profile_path = self.config_dir / f"{self._sanitize_filename(name)}.json"
            config.save_to_file(profile_path)
            return True
        except PermissionError as e:
            print(f"Permission denied when saving profile '{name}': {e}")
            return False
        except Exception as e:
            error = TUIError(
                severity=ErrorSeverity.ERROR,
                category="config",
                message=f"Failed to save profile '{name}'",
                details=str(e),
                suggested_actions=[
                    "Check if your disk has sufficient space",
                    "Verify that the cache directory is accessible",
                ],
            )
            print(f"Error saving profile: {error.message}")
            return False

    def load_profile(self, name: str) -> Optional[BuildConfiguration]:
        """
        Load configuration profile.

        Returns:
            Configuration if successful, None otherwise
        """
        try:
            # Ensure directory exists
            self._ensure_config_directory()

            profile_path = self.config_dir / f"{self._sanitize_filename(name)}.json"
            if not profile_path.exists():
                error = TUIError(
                    severity=ErrorSeverity.WARNING,
                    category="config",
                    message=f"Profile '{name}' not found",
                    details=f"The configuration file does not exist at {profile_path}",
                    suggested_actions=[
                        "Check if the profile name is correct",
                        "Create a new profile with this name",
                    ],
                )
                print(f"Profile not found: {error.message}")
                return None

            # Load the profile data
            with open(profile_path, "r") as f:
                profile_data = json.load(f)

            # Try to create a Pydantic BuildConfiguration
            try:
                config = BuildConfiguration(**profile_data)
            except ValidationError as e:
                print(f"Warning: Validation errors in profile '{name}': {e}")
                # Fall back to legacy model if validation fails
                config = LegacyBuildConfiguration(**profile_data)
                # Attempt to convert to new model with default values for missing fields
                try:
                    config = BuildConfiguration(**config.to_dict())
                except ValidationError:
                    # If conversion fails, continue with legacy model
                    pass

            # Update last used timestamp
            timestamp = datetime.now().isoformat()
            if isinstance(config, BuildConfiguration):
                # For Pydantic model, create a new instance with updated timestamp
                config_dict = config.dict()
                config_dict["last_used"] = timestamp
                config = BuildConfiguration(**config_dict)
            else:
                # For legacy model, set attribute directly
                config.last_used = timestamp
            success = self.save_profile(name, config)  # Save updated timestamp
            if not success:
                # If we couldn't save the updated timestamp, just log and
                # continue
                print(
                    f"Warning: Could not update last_used timestamp for profile '{name}'"
                )

            return config

        except PermissionError as e:
            error = TUIError(
                severity=ErrorSeverity.ERROR,
                category="config",
                message=f"Permission denied when loading profile '{name}'",
                details=str(e),
                suggested_actions=[
                    f"Check file permissions in {CACHE_DIR}/profiles/",
                    "Ensure you have read access to your home directory",
                ],
            )
            print(f"Permission error: {error.message}")
            return None
        except json.JSONDecodeError as e:
            error = TUIError(
                severity=ErrorSeverity.ERROR,
                category="config",
                message=f"Invalid JSON in profile '{name}'",
                details=f"Error at line {e.lineno}, column {e.colno}: {e.msg}",
                suggested_actions=[
                    "The profile file may be corrupted",
                    "Try deleting and recreating the profile",
                ],
            )
            print(f"JSON error: {error.message}")
            return None
        except Exception as e:
            error = TUIError(
                severity=ErrorSeverity.ERROR,
                category="config",
                message=f"Failed to load profile '{name}'",
                details=str(e),
                suggested_actions=[
                    "Check if the profile file exists and is accessible",
                    "Verify that the file contains valid JSON data",
                ],
            )
            print(f"Error loading profile: {error.message}")
            return None

    def list_profiles(self) -> List[Dict[str, str]]:
        """
        List available configuration profiles.

        Returns:
            List of profile information dictionaries
        """
        try:
            # Ensure directory exists
            self._ensure_config_directory()

            profiles = []
            invalid_files = []

            for profile_file in self.config_dir.glob("*.json"):
                try:
                    with open(profile_file, "r") as f:
                        data = json.load(f)
                        profiles.append(
                            {
                                "name": data["name"],
                                "description": data["description"],
                                "created_at": data["created_at"],
                                "last_used": data["last_used"],
                                "filename": profile_file.name,
                            }
                        )
                except (json.JSONDecodeError, KeyError):
                    # Track invalid files but don't stop processing
                    invalid_files.append(profile_file.name)
                except PermissionError:
                    # Track permission issues but don't stop processing
                    invalid_files.append(f"{profile_file.name} (permission denied)")
                except Exception:
                    # Track other issues but don't stop processing
                    invalid_files.append(f"{profile_file.name} (unknown error)")

            # Sort by last used (most recent first)
            profiles.sort(key=lambda x: x["last_used"], reverse=True)

            # If we found invalid files, return a warning
            if invalid_files:
                error = TUIError(
                    severity=ErrorSeverity.WARNING,
                    category="config",
                    message="Some profile files could not be loaded",
                    details=f"Skipped invalid files: {', '.join(invalid_files)}",
                    suggested_actions=[
                        "Check file permissions and format of the skipped files",
                        "Consider deleting corrupted profile files",
                    ],
                )
                print(f"Warning: {error.message}")
                return profiles

            return profiles

        except PermissionError as e:
            error = TUIError(
                severity=ErrorSeverity.ERROR,
                category="config",
                message="Permission denied when listing profiles",
                details=str(e),
                suggested_actions=[
                    f"Check permissions for {CACHE_DIR}/profiles/ directory",
                    "Ensure you have read access to your home directory",
                ],
            )
            print(f"Permission error: {error.message}")
            return []
        except Exception as e:
            error = TUIError(
                severity=ErrorSeverity.ERROR,
                category="config",
                message="Failed to list profiles",
                details=str(e),
                suggested_actions=[
                    f"Check if the {CACHE_DIR} directory exists and is accessible"
                ],
            )
            print(f"Error listing profiles: {error.message}")
            return []

    def delete_profile(self, name: str) -> bool:
        """
        Delete a configuration profile.

        Returns:
            Boolean indicating success
        """
        try:
            profile_path = self.config_dir / f"{self._sanitize_filename(name)}.json"
            if profile_path.exists():
                profile_path.unlink()
                return True
            print(f"Profile '{name}' not found for deletion")
            return False
        except PermissionError as e:
            error = TUIError(
                severity=ErrorSeverity.ERROR,
                category="config",
                message=f"Permission denied when deleting profile '{name}'",
                details=str(e),
                suggested_actions=[
                    f"Check file permissions in {CACHE_DIR}/profiles/",
                    "Ensure you have write access to your home directory",
                ],
            )
            print(f"Permission error: {error.message}")
            return False
        except Exception as e:
            error = TUIError(
                severity=ErrorSeverity.ERROR,
                category="config",
                message=f"Failed to delete profile '{name}'",
                details=str(e),
                suggested_actions=[
                    "Check if the file is being used by another process",
                    f"Verify that the {CACHE_DIR} directory is accessible",
                ],
            )
            print(f"Error deleting profile: {error.message}")
            return False

    def profile_exists(self, name: str) -> bool:
        """Check if a profile exists."""
        profile_path = self.config_dir / f"{self._sanitize_filename(name)}.json"
        return profile_path.exists()

    def create_default_profiles(self) -> bool:
        """
        Create default configuration profiles.

        Returns:
            Boolean indicating success
        """
        try:
            # Ensure directory exists with proper permissions
            self._ensure_config_directory()

            default_profiles = [
                {
                    "name": "Network Device Standard",
                    "description": "Standard configuration for network devices",
                    "config": BuildConfiguration(
                        board_type="pcileech_75t484_x1",
                        advanced_sv=True,
                        enable_variance=True,
                        behavior_profiling=False,
                        profile_duration=30.0,
                        power_management=True,
                        error_handling=True,
                        performance_counters=True,
                        flash_after_build=False,
                    ),
                },
                {
                    "name": "Storage Device Optimized",
                    "description": "Optimized configuration for storage devices",
                    "config": BuildConfiguration(
                        board_type="pcileech_100t484_x1",
                        device_type="storage",
                        advanced_sv=True,
                        enable_variance=True,
                        behavior_profiling=True,
                        profile_duration=45.0,
                        power_management=True,
                        error_handling=True,
                        performance_counters=True,
                        flash_after_build=False,
                    ),
                },
                {
                    "name": "Quick Development",
                    "description": "Fast configuration for development and testing",
                    "config": BuildConfiguration(
                        board_type="pcileech_35t325_x1",
                        device_type="generic",
                        advanced_sv=False,
                        enable_variance=False,
                        behavior_profiling=False,
                        profile_duration=15.0,
                        power_management=False,
                        error_handling=False,
                        performance_counters=False,
                        flash_after_build=True,
                    ),
                },
                {
                    "name": "Full Featured",
                    "description": "All features enabled for comprehensive analysis",
                    "config": BuildConfiguration(
                        board_type="pcileech_100t484_x1",
                        device_type="generic",
                        advanced_sv=True,
                        enable_variance=True,
                        behavior_profiling=True,
                        profile_duration=60.0,
                        power_management=True,
                        error_handling=True,
                        performance_counters=True,
                        flash_after_build=False,
                    ),
                },
            ]

            created_count = 0
            errors = []

            for profile_data in default_profiles:
                if not self.profile_exists(profile_data["name"]):
                    config = profile_data["config"]
                    config.name = profile_data["name"]
                    config.description = profile_data["description"]
                    success = self.save_profile(profile_data["name"], config)
                    if success:
                        created_count += 1
                    else:
                        errors.append(f"Failed to create '{profile_data['name']}'")

            if errors:
                print(
                    f"Warning: Created {created_count} of {len(default_profiles)} default profiles"
                )
                print("\n".join(errors))
                return created_count > 0

            return True

        except PermissionError as e:
            print(f"Permission denied when creating default profiles: {e}")
            return False
        except Exception as e:
            print(f"Failed to create default profiles: {e}")
            return False

    def export_profile(self, name: str, export_path: Path) -> bool:
        """
        Export a profile to a specific path.

        Returns:
            Boolean indicating success
        """
        try:
            config = self.load_profile(name)
            if not config:
                return False

            if config:
                try:
                    config.save_to_file(export_path)
                    return True
                except PermissionError as e:
                    print(
                        f"Permission denied when exporting profile to {export_path}: {e}"
                    )
                    return False
                except Exception as e:
                    error = TUIError(
                        severity=ErrorSeverity.ERROR,
                        category="config",
                        message=f"Failed to export profile to {export_path}",
                        details=str(e),
                        suggested_actions=[
                            "Check if the target directory exists",
                            "Verify that you have sufficient disk space",
                        ],
                    )
                    return False
            print(f"Cannot export profile '{name}' - profile not loaded")
            return False
        except Exception as e:
            print(f"Unexpected error when exporting profile '{name}': {e}")
            return False

    def import_profile(
        self, import_path: Path, new_name: Optional[str] = None
    ) -> Optional[str]:
        """
        Import a profile from a file.

        Returns:
            Profile name if successful, None otherwise
        """
        try:
            if not import_path.exists():
                error = TUIError(
                    severity=ErrorSeverity.ERROR,
                    category="config",
                    message=f"Import file not found: {import_path}",
                    suggested_actions=[
                        "Check if the file path is correct",
                        "Verify that the file exists",
                    ],
                )
                print(f"Import file not found: {import_path}")
                return None

            try:
                config = BuildConfiguration.load_from_file(import_path)
            except json.JSONDecodeError as e:
                error = TUIError(
                    severity=ErrorSeverity.ERROR,
                    category="config",
                    message="Invalid JSON in import file",
                    details=f"Error at line {e.lineno}, column {e.colno}: {e.msg}",
                    suggested_actions=[
                        "Check if the file contains valid JSON data",
                        "Verify that the file is a valid configuration profile",
                    ],
                )
                print(
                    f"Invalid JSON in import file: Error at line {e.lineno}, column {e.colno}: {e.msg}"
                )
                return None
            except Exception as e:
                error = TUIError(
                    severity=ErrorSeverity.ERROR,
                    category="config",
                    message="Failed to parse import file",
                    details=str(e),
                    suggested_actions=[
                        "Check if the file is a valid configuration profile",
                        "Verify that the file is not corrupted",
                    ],
                )
                print(f"Failed to parse import file: {e}")
                return None

            # Use provided name or extract from config
            profile_name = new_name or config.name or import_path.stem

            # Ensure unique name
            original_name = profile_name
            counter = 1
            while self.profile_exists(profile_name):
                profile_name = f"{original_name} ({counter})"
                counter += 1

            success = self.save_profile(profile_name, config)
            if not success:
                return None

            return profile_name

        except PermissionError as e:
            error = TUIError(
                severity=ErrorSeverity.ERROR,
                category="config",
                message=f"Permission denied when importing profile from {import_path}",
                details=str(e),
                suggested_actions=[
                    "Check if you have read permissions for the source file",
                    f"Ensure you have write access to {CACHE_DIR}/profiles/",
                ],
            )
            print(f"Permission denied when importing profile: {e}")
            return None
        except Exception as e:
            print(f"Failed to import profile from {import_path}: {e}")
            return None

    def get_profile_summary(self, name: str) -> Dict[str, str]:
        """
        Get a summary of a profile's configuration.
        Always returns a dictionary, with error information if loading fails.
        """
        try:
            config = self.load_profile(name)
            if not config:
                return {
                    "error": "Profile not found",
                    "details": f"Could not load profile '{name}'",
                }

            if config:
                return {
                    "name": config.name,
                    "description": config.description,
                    "board_type": config.board_type,
                    "device_type": config.device_type,
                    "features": config.feature_summary,
                    "advanced": "Yes" if config.is_advanced else "No",
                    "last_used": (
                        config.last_used if config.last_used is not None else ""
                    ),
                }
            return {"error": "Failed to load profile", "details": "Unknown error"}
        except Exception as e:
            return {"error": "Failed to load profile", "details": str(e)}

    def _sanitize_filename(self, name: str) -> str:
        """Sanitize profile name for use as filename."""
        # Replace invalid characters with underscores
        invalid_chars = '<>:"/\\|?*'
        sanitized = name
        for char in invalid_chars:
            sanitized = sanitized.replace(char, "_")

        # Remove leading/trailing spaces and dots
        sanitized = sanitized.strip(" .")

        # Ensure it's not empty
        if not sanitized:
            sanitized = "unnamed_profile"

        return sanitized

    def validate_config(self, config: BuildConfiguration) -> List[str]:
        """Validate configuration and return list of issues."""
        issues = []

        try:
            # This will raise ValueError if invalid
            BuildConfiguration(**config.to_dict())
        except ValueError as e:
            issues.append(str(e))

        # Additional validation rules
        if config.behavior_profiling and config.profile_duration < 10:
            issues.append("Behavior profiling duration should be at least 10 seconds")

        if config.board_type == "35t" and config.is_advanced:
            issues.append("35t board may have limited resources for advanced features")

        return issues
