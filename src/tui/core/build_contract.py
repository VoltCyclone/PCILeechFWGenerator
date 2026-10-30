"""Translate TUI settings into the unified, installed CLI build interface."""

import sys
from pathlib import Path
from typing import Any

from ..models.configuration import BuildConfiguration

# Legacy fields remain loadable so profiles can be reviewed and corrected.
# These neutral values do not request a backend override.
UNSUPPORTED_SETTINGS: dict[str, Any] = {
    "device_type": "generic",
    "optimization_level": "balanced",
    "advanced_sv": False,
    "enable_variance": False,
    "enable_performance_counters": False,
    "performance_counters": False,
    "enable_error_counters": True,
    "power_management": True,
    "error_handling": True,
    "flash_after_build": False,
    "donor_dump": False,
    "auto_install_headers": False,
    "skip_board_check": False,
    "disable_ftrace": False,
    "custom_parameters": {},
    "feature_flags": {},
    "compatibility_overrides": [],
}


def resolve_datastore_directory(config: BuildConfiguration) -> Path:
    """Resolve capture storage independently of generated output files."""
    if config.datastore_directory:
        return Path(config.datastore_directory).expanduser().resolve()
    if config.output_directory:
        return (
            Path(config.output_directory).expanduser().resolve().parent
            / "pcileech_datastore"
        )
    return Path.cwd() / "pcileech_datastore"


def validate_build_configuration(config: BuildConfiguration) -> None:
    """Reject unsupported requests before collection or other side effects."""
    if config.output_directory and config.datastore_directory:
        output = Path(config.output_directory).expanduser().resolve()
        datastore = resolve_datastore_directory(config)
        if datastore.is_relative_to(output):
            raise ValueError("datastore must be outside the output directory")
    invalid = [
        f"{field} (use {neutral!r})"
        for field, neutral in UNSUPPORTED_SETTINGS.items()
        if getattr(config, field) != neutral
    ]
    if invalid:
        raise ValueError("Unsupported build settings: " + ", ".join(invalid))
    if config.board_type == "default":
        raise ValueError("Select a target board before building")
    if config.behavior_profiling and not config.local_build:
        raise ValueError(
            "behavior_profiling requires local_build; "
            "container profiling is unavailable"
        )
    if config.debug_mode and not config.enable_logging:
        raise ValueError("debug_mode requires enable_logging")
    if (
        config.donor_info_file
        and not Path(config.donor_info_file).expanduser().is_file()
    ):
        raise ValueError(
            "donor_info_file must point to an existing donor template JSON file"
        )


def build_command(bdf: str, config: BuildConfiguration) -> list[str]:
    """Return argv for the complete host → templating → Vivado pipeline."""
    validate_build_configuration(config)
    command = [sys.executable, "-m", "pcileechfwgenerator.pcileech_main"]
    if config.debug_mode:
        command.append("--verbose")
    elif not config.enable_logging:
        command.append("--quiet")
    command.extend(
        [
            "build",
            "--bdf",
            bdf,
            "--board",
            config.board_type,
            "--container-mode",
            "local" if config.local_build else "container",
            "--profile",
            str(config.profile_duration if config.behavior_profiling else 0),
        ]
    )
    for option, value in (
        ("--output", config.output_directory),
        ("--datastore", resolve_datastore_directory(config)),
        ("--donor-template", config.donor_info_file),
    ):
        if value:
            command.extend([option, str(Path(value).expanduser().resolve())])
    return command
