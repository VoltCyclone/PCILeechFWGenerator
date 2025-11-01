"""
Configuration models for the PCILeech TUI application using Pydantic.

This module defines Pydantic models for representing build configurations
with robust validation.
"""

from datetime import datetime
from typing import Any, ClassVar, Dict, List, Optional

from pydantic import BaseModel, Field, field_validator, model_validator

from ...utils.validation_constants import KNOWN_DEVICE_TYPES

# Define valid board types for validation
VALID_BOARD_TYPES = [
    "pcileech_75t484_x1",
    "pcileech_35t484_x1",
    "pcileech_100t484_x1",
    "pcileech_35t325_x4",
    "default",
]

# Define valid device types
VALID_DEVICE_TYPES = KNOWN_DEVICE_TYPES

# Define valid optimization levels
VALID_OPTIMIZATION_LEVELS = ["debug", "balanced", "size", "performance"]


class BuildConfiguration(BaseModel):
    """Configuration for a PCILeech firmware build with validation."""

    # Basic configuration
    name: str = Field(
        default="Default Configuration", description="Name of the configuration profile"
    )
    description: str = Field(
        default="", description="Description of this configuration profile"
    )
    device_id: Optional[str] = Field(
        default=None, description="Device ID of the target device"
    )
    board_type: str = Field(default="default", description="Board type for the build")
    device_type: str = Field(
        default="generic", description="Type of device being emulated"
    )
    output_directory: Optional[str] = Field(
        default=None, description="Directory to store build outputs"
    )

    enable_performance_counters: bool = Field(
        default=False, description="Enable performance counters"
    )

    # Advanced options
    advanced_sv: bool = Field(
        default=True, description="Enable advanced SystemVerilog features"
    )
    enable_variance: bool = Field(
        default=True, description="Enable manufacturing variance simulation"
    )
    behavior_profiling: bool = Field(
        default=False, description="Enable behavior profiling"
    )
    profile_duration: float = Field(
        default=30.0, description="Duration for behavior profiling in seconds"
    )
    power_management: bool = Field(
        default=True, description="Enable power management features"
    )
    error_handling: bool = Field(
        default=True, description="Enable error handling features"
    )
    # Legacy / compatibility options (present in the older dataclass model)
    donor_dump: bool = Field(
        default=True, description="Use donor_dump kernel module for donor info"
    )
    auto_install_headers: bool = Field(
        default=False, description="Auto-install kernel headers when needed"
    )
    local_build: bool = Field(
        default=False, description="Force local build instead of donor-based build"
    )
    skip_board_check: bool = Field(
        default=False, description="Skip hardware/board checks during generation"
    )
    donor_info_file: Optional[str] = Field(
        default=None, description="Path to donor info JSON file"
    )
    disable_ftrace: bool = Field(
        default=False, description="Disable ftrace instrumentation"
    )
    # Some callers expect a shorthand 'performance_counters' flag (legacy)
    performance_counters: bool = Field(
        default=False, description="Enable performance counters (legacy alias)"
    )

    # Advanced custom parameters
    custom_parameters: Dict[str, Any] = Field(
        default_factory=dict, description="Custom build parameters"
    )

    # Metadata
    created_at: Optional[str] = Field(
        default=None, description="Creation timestamp (ISO format)"
    )
    last_used: Optional[str] = Field(
        default=None, description="Last used timestamp (ISO format)"
    )

    @model_validator(mode="before")
    @classmethod
    def set_timestamps(cls, values):
        """Set timestamps if not provided."""
        if isinstance(values, dict):
            if values.get("created_at") is None:
                values["created_at"] = datetime.now().isoformat()

            # Always update last_used when validated
            values["last_used"] = datetime.now().isoformat()

        return values

    model_config = {
        "validate_assignment": True,  # Validate when attributes are assigned
        "extra": "forbid",  # Forbid extra attributes not defined in the model
    }

    # Method for compatibility with the existing codebase
    def to_dict(self) -> Dict[str, Any]:
        """Convert configuration to a dictionary for serialization."""
        # Use pydantic v2 model_dump when available, fall back to dict()
        try:
            return self.model_dump()
        except Exception:
            return self.dict()

    # Compatibility helpers for legacy code expecting dataclass-like API
    def copy(self) -> "BuildConfiguration":
        """Return a deep copy of this configuration (compatibility with dataclass)."""
        try:
            return self.model_copy(deep=True)
        except Exception:
            # Fallback: create a new instance from dict
            return self.__class__(**self.to_dict())

    @property
    def is_advanced(self) -> bool:
        """Compatibility property to match legacy BuildConfiguration API."""
        return bool(self.advanced_sv)

    # Keep alias semantics so callers using either name work
    @model_validator(mode="after")
    def _sync_legacy_flags(self):
        # Ensure legacy 'performance_counters' follows 'enable_performance_counters' if unset
        try:
            if not getattr(self, "performance_counters", False) and getattr(
                self, "enable_performance_counters", False
            ):
                object.__setattr__(self, "performance_counters", True)
        except Exception:
            pass
        return self

    # Class method for compatibility with the existing codebase
    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> "BuildConfiguration":
        """Create a configuration from a dictionary."""
        return cls(**data)

    def save_to_file(self, file_path):
        """Save configuration to a file."""
        import json
        import os

        # Ensure directory exists
        os.makedirs(os.path.dirname(file_path), exist_ok=True)

        # Write JSON to file
        with open(file_path, "w") as f:
            json.dump(self.to_dict(), f, indent=2)

    @classmethod
    def load_from_file(cls, file_path):
        """Load configuration from a file."""
        import json

        with open(file_path, "r") as f:
            data = json.load(f)

        return cls.from_dict(data)


class BuildProgress(BaseModel):
    """Progress information for a PCILeech firmware build."""

    build_id: str
    status: str  # "pending", "running", "completed", "failed", "cancelled"
    progress: float = Field(..., ge=0.0, le=100.0)  # 0.0 to 100.0
    current_step: Optional[str] = None
    message: Optional[str] = None
    start_time: Optional[float] = None
    end_time: Optional[float] = None
    logs: List[str] = Field(default_factory=list)
    errors: List[str] = Field(default_factory=list)
    warnings: List[str] = Field(default_factory=list)

    @field_validator("status")
    @classmethod
    def validate_status(cls, v):
        """Validate that the status is one of the allowed values."""
        valid_statuses = ["pending", "running", "completed", "failed", "cancelled"]
        if v not in valid_statuses:
            raise ValueError(
                f'Invalid status: {v}. Valid statuses are: {", ".join(valid_statuses)}'
            )
        return v

    @property
    def is_complete(self) -> bool:
        """Check if the build is complete."""
        return self.status in ("completed", "failed", "cancelled")

    @property
    def is_successful(self) -> bool:
        """Check if the build was successful."""
        return self.status == "completed" and not self.errors

    # Method for compatibility with the existing codebase
    def to_dict(self) -> Dict[str, Any]:
        """Convert progress information to a dictionary for serialization."""
        return self.dict()
