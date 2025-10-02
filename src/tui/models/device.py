"""
Device models for the PCILeech TUI application.

This module defines data classes for representing PCI devices in the application.
"""

from dataclasses import asdict, dataclass, field
from typing import Any, Dict, List, Optional, Union


@dataclass
class PCIDevice:
    """Enhanced PCIe device information."""

    bdf: str  # Bus/Device/Function identifier (e.g., "0000:00:00.0")
    vendor_id: str
    device_id: str
    vendor_name: str
    device_name: str
    device_class: str
    subsystem_vendor: Optional[str] = None
    subsystem_device: Optional[str] = None
    driver: Optional[str] = None
    iommu_group: Optional[int] = None
    power_state: Optional[str] = None
    link_speed: Optional[str] = None
    bars: Dict[str, Dict[str, str]] = field(default_factory=dict)
    suitability_score: float = 0.0
    compatibility_issues: List[str] = field(default_factory=list)
    compatibility_factors: List[Dict[str, Any]] = field(default_factory=list)
    detailed_status: Dict[str, str] = field(default_factory=dict)
    template_options: Dict[str, str] = field(default_factory=dict)
    is_valid: bool = True
    has_driver: bool = False
    is_detached: bool = False
    vfio_compatible: bool = False
    iommu_enabled: bool = False

    def __post_init__(self):
        """Ensure proper types for all fields after initialization."""
        # Ensure proper string types
        self.bdf = str(self.bdf) if self.bdf is not None else ""
        self.vendor_id = str(self.vendor_id) if self.vendor_id is not None else ""
        self.device_id = str(self.device_id) if self.device_id is not None else ""
        self.vendor_name = str(self.vendor_name) if self.vendor_name is not None else ""
        self.device_name = str(self.device_name) if self.device_name is not None else ""
        self.device_class = (
            str(self.device_class) if self.device_class is not None else ""
        )

        # Handle optional fields
        if self.subsystem_vendor is not None:
            self.subsystem_vendor = str(self.subsystem_vendor)
        if self.subsystem_device is not None:
            self.subsystem_device = str(self.subsystem_device)
        if self.driver is not None:
            self.driver = str(self.driver)
        if self.power_state is not None:
            self.power_state = str(self.power_state)
        if self.link_speed is not None:
            self.link_speed = str(self.link_speed)

        # Ensure numeric types
        try:
            self.suitability_score = float(self.suitability_score)
        except (ValueError, TypeError):
            self.suitability_score = 0.0

        try:
            if self.iommu_group is not None:
                self.iommu_group = int(self.iommu_group)
        except (ValueError, TypeError):
            self.iommu_group = None

        # Ensure boolean types
        self.is_valid = bool(self.is_valid)
        self.has_driver = bool(self.has_driver)
        self.is_detached = bool(self.is_detached)
        self.vfio_compatible = bool(self.vfio_compatible)
        self.iommu_enabled = bool(self.iommu_enabled)

    @property
    def display_name(self) -> str:
        """Return a user-friendly display name for the device."""
        return f"{self.vendor_name} {self.device_name} ({self.bdf})"

    @property
    def is_supported(self) -> bool:
        """Return True to avoid overzealous unsupported labeling in the TUI.

        Historically this was tied directly to the presence of any
        compatibility_issues, which caused all devices to show as unsupported
        in some environments. Readiness for builds should instead be reflected
        by is_suitable; the UI can surface issues separately without marking
        the device as unsupported.
        """
        return True

    @property
    def is_suitable(self) -> bool:
        """Check if the device is suitable for firmware generation."""
        return (
            self.is_valid
            and self.vfio_compatible
            and len(self.compatibility_issues) == 0
        )

    @property
    def id(self) -> str:
        """Return the BDF as the device ID for backward compatibility."""
        return self.bdf

    @property
    def name(self) -> str:
        """Return the device name for backward compatibility."""
        return self.device_name

    @property
    def class_id(self) -> str:
        """Return the class ID for backward compatibility."""
        return self.device_class[:4] if self.device_class else "0000"

    @property
    def status_indicator(self) -> str:
        """Return a status indicator emoji based on suitability."""
        return "✅" if self.is_suitable else "❌"

    @property
    def compact_status(self) -> str:
        """Return a compact status string for display in tables."""
        try:
            score = float(self.suitability_score)
            if score > 0.8:
                return f"Score: {score:.2f} ✓"
            elif score > 0.5:
                return f"Score: {score:.2f} ⚠"
            else:
                return f"Score: {score:.2f} ✗"
        except (TypeError, ValueError):
            # Handle case where suitability_score is not a valid float
            return "Score: 0.00 ✗"

    @property
    def validity_indicator(self) -> str:
        """Return indicator for device validity."""
        return "✓" if self.is_valid else "✗"

    @property
    def driver_indicator(self) -> str:
        """Return indicator for driver status."""
        if not self.has_driver:
            return "✓"  # No driver is good for our purposes
        return "⚠" if self.is_detached else "✗"

    @property
    def vfio_indicator(self) -> str:
        """Return indicator for VFIO compatibility."""
        return "✓" if self.vfio_compatible else "✗"

    @property
    def iommu_indicator(self) -> str:
        """Return indicator for IOMMU status."""
        return "✓" if self.iommu_enabled else "✗"

    @property
    def ready_indicator(self) -> str:
        """Return indicator for overall readiness."""
        return "✓" if self.is_suitable else "✗"

    @property
    def class_name(self) -> str:
        """Return a human-readable class name based on the class ID."""
        class_names = {
            "0100": "SCSI Storage Controller",
            "0101": "IDE Controller",
            "0102": "Floppy Controller",
            "0103": "IPI Controller",
            "0104": "RAID Controller",
            "0105": "ATA Controller",
            "0106": "SATA Controller",
            "0107": "SAS Controller",
            "0180": "Other Storage Controller",
            "0200": "Ethernet Controller",
            "0280": "Other Network Controller",
            "0300": "VGA Compatible Controller",
            "0301": "XGA Controller",
            "0302": "3D Controller",
            "0380": "Other Display Controller",
            "0400": "Multimedia Video Controller",
            "0401": "Multimedia Audio Controller",
            "0402": "Computer Telephony Device",
            "0403": "Audio Device",
            "0480": "Other Multimedia Controller",
        }

        class_id = self.class_id
        return class_names.get(class_id, f"Unknown Device Class ({class_id})")

    def get_template_option_value(self, option_name: str) -> Optional[str]:
        """
        Get the value of a template option.

        Args:
            option_name: The name of the option to retrieve

        Returns:
            The option value if set, None otherwise
        """
        return self.template_options.get(option_name)

    def set_template_option_value(self, option_name: str, value: str) -> None:
        """
        Set the value of a template option.

        Args:
            option_name: The name of the option to set
            value: The value to set for the option
        """
        self.template_options[option_name] = value

    def to_dict(self) -> Dict[str, Any]:
        """Convert device information to a dictionary for serialization."""
        # asdict imported at module scope

        # Create a deep copy to avoid modifying the original
        device_dict = asdict(self)

        # Add any computed properties that should be included
        device_dict["is_suitable"] = self.is_suitable
        device_dict["is_supported"] = self.is_supported
        device_dict["class_name"] = self.class_name
        device_dict["display_name"] = self.display_name

        return device_dict
