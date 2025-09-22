#!/usr/bin/env python3
"""Configuration dataclass for PCILeech firmware generation."""

from dataclasses import dataclass
from typing import Optional

from src.string_utils import safe_format


@dataclass
class BuildConfig:
    """Strongly-typed configuration for firmware build process."""

    # Device configuration
    bdf: str
    vendor: str
    device: str
    board: str
    device_type: str

    # Advanced features
    advanced_sv: bool = True
    enable_variance: bool = True
    donor_dump: bool = True
    auto_install_headers: bool = True
    strict_vfio: bool = True  # Fail hard if VFIO is not available

    # Feature toggles
    disable_power_management: bool = False
    disable_error_handling: bool = False
    disable_performance_counters: bool = False

    flash: bool = True

    # Timing configuration
    behavior_profile_duration: int = 45

    # Mode configuration
    tui: bool = False
    interactive: bool = False

    # Runtime state
    original_driver: Optional[str] = None
    iommu_group: Optional[str] = None
    vfio_device: Optional[str] = None

    def __post_init__(self):
        """Validate configuration after initialization."""

        # Validate BDF format
        import re

        bdf_pattern = re.compile(
            r"^[0-9a-fA-F]{4}:[0-9a-fA-F]{2}:[0-9a-fA-F]{2}\.[0-7]$"
        )
        if not bdf_pattern.match(self.bdf):
            raise ValueError(
                safe_format(
                    "Invalid BDF format: {bdf}. Expected format: DDDD:BB:DD.F",
                    bdf=self.bdf,
                )
            )

        # Validate vendor and device IDs
        if not re.match(r"^[0-9a-fA-F]{4}$", self.vendor):
            raise ValueError(
                safe_format(
                    "Invalid vendor ID format: {vendor}. Expected 4-digit hex.",
                    vendor=self.vendor,
                )
            )
        if not re.match(r"^[0-9a-fA-F]{4}$", self.device):
            raise ValueError(
                safe_format(
                    "Invalid device ID format: {device}. Expected 4-digit hex.",
                    device=self.device,
                )
            )
