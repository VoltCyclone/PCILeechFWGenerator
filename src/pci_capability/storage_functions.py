#!/usr/bin/env python3
"""
Storage Function Capabilities

This module provides dynamic storage function capabilities for PCIe device
generation. It analyzes build-time provided vendor/device IDs to generate
realistic storage controller capabilities without hardcoding.

The module integrates with the existing templating and logging infrastructure
to provide production-ready dynamic capability generation.
"""

import logging

from typing import Any, Dict, List, Optional, Set

from .base_function_analyzer import (BaseFunctionAnalyzer,
                                     create_function_capabilities)

from .constants import (  # Common PCI Capability IDs; Storage class codes; Storage vendor IDs; Storage device ID ranges; Storage device ID thresholds; Storage MSI messages; Storage max payload sizes; Storage base queue counts; Storage BAR sizes; Storage power constants; Storage feature thresholds; Storage device limits; Storage bit manipulation; AER capability values
    AER_CAPABILITY_VALUES, CAP_ID_MSI, CAP_ID_MSIX, CAP_ID_PCIE, CAP_ID_PM,
    EXT_CAP_ID_AER, STORAGE_BAR_SIZES, STORAGE_BASE_QUEUE_COUNTS,
    STORAGE_BIT_MANIPULATION, STORAGE_CLASS_CODES, STORAGE_DEVICE_ID_RANGES,
    STORAGE_DEVICE_ID_THRESHOLDS, STORAGE_DEVICE_LIMITS,
    STORAGE_FEATURE_THRESHOLDS, STORAGE_MAX_PAYLOAD_SIZES,
    STORAGE_MSI_MESSAGES, STORAGE_POWER_CONSTANTS, VENDOR_ID_LSI_BROADCOM,
    VENDOR_ID_MARVELL, VENDOR_ID_SAMSUNG)

logger = logging.getLogger(__name__)


class StorageFunctionAnalyzer(BaseFunctionAnalyzer):
    """
    Dynamic storage function capability analyzer.

    Analyzes vendor/device IDs provided at build time to generate realistic
    storage function capabilities without hardcoding device-specific behavior.
    """

    def __init__(self, vendor_id: int, device_id: int):
        """
        Initialize analyzer with build-time provided vendor/device IDs.

        Args:
            vendor_id: PCI vendor ID from build process
            device_id: PCI device ID from build process
        """
        super().__init__(vendor_id, device_id, "storage")

    def _analyze_device_category(self) -> str:
        """
        Analyze device category based on vendor/device ID patterns.

        Returns:
            Device category string (scsi, nvme, sata, etc.)
        """
        # Pattern-based analysis without hardcoding specific device IDs
        device_lower = self.device_id & STORAGE_BIT_MANIPULATION["device_id_lower_mask"]
        device_upper = (
            self.device_id >> STORAGE_BIT_MANIPULATION["device_id_upper_shift"]
        ) & STORAGE_BIT_MANIPULATION["device_id_upper_mask"]

        # Import vendor ID constants
        from src.device_clone.constants import VENDOR_ID_INTEL

        # Vendor-specific patterns
        if self.vendor_id == VENDOR_ID_INTEL:  # Intel
            if device_lower in STORAGE_DEVICE_ID_RANGES["intel_sata"]:  # SATA ranges
                return "sata"
            if device_lower in STORAGE_DEVICE_ID_RANGES["intel_nvme"]:  # NVMe ranges
                return "nvme"
        if self.vendor_id == VENDOR_ID_SAMSUNG:  # Samsung
            if device_lower in STORAGE_DEVICE_ID_RANGES["samsung_nvme"]:
                return "nvme"
        if self.vendor_id == VENDOR_ID_MARVELL:  # Marvell
            if device_lower in STORAGE_DEVICE_ID_RANGES["marvell_sata"]:
                return "sata"
        if self.vendor_id == VENDOR_ID_LSI_BROADCOM:  # LSI/Broadcom
            if device_lower in STORAGE_DEVICE_ID_RANGES["lsi_sas"]:  # SAS ranges
                return "sas"
            if device_lower in STORAGE_DEVICE_ID_RANGES["lsi_raid"]:  # RAID ranges
                return "raid"

        # Generic patterns based on device ID structure
        # High device IDs often NVMe
        if device_upper >= STORAGE_DEVICE_ID_THRESHOLDS["device_upper_nvme"]:
            return "nvme"
        # Mid-high often SATA
        if device_upper >= STORAGE_DEVICE_ID_THRESHOLDS["device_upper_sata"]:
            return "sata"
        # Mid often SAS
        if device_upper >= STORAGE_DEVICE_ID_THRESHOLDS["device_upper_sas"]:
            return "sas"

        return "sata"  # Default fallback

    def _analyze_capabilities(self) -> Set[int]:
        """
        Analyze which capabilities this device should support.

        Returns:
            Set of capability IDs that should be present
        """
        caps = set()

        # Always include basic storage capabilities
        caps.update(
            [
                CAP_ID_PM,
                CAP_ID_MSI,
                CAP_ID_PCIE,
                CAP_ID_MSIX,
            ]
        )

        # Advanced capabilities based on device analysis
        if self._supports_aer():
            caps.add(EXT_CAP_ID_AER)

        return caps

    def _supports_aer(self) -> bool:
        """Check if device likely supports Advanced Error Reporting."""
        # High-end storage devices (NVMe, enterprise SATA/SAS) support AER
        if self._device_category in ["nvme", "sas"]:
            return True
        if (
            self._device_category in ["sata", "raid"]
            and self.device_id > STORAGE_DEVICE_ID_THRESHOLDS["high_end_storage"]
        ):
            return True
        return False

    def get_device_class_code(self) -> int:
        """Get appropriate PCI class code for this device."""
        return STORAGE_CLASS_CODES.get(
            self._device_category, STORAGE_CLASS_CODES["sata"]
        )

    def _create_capability_by_id(self, cap_id: int) -> Optional[Dict[str, Any]]:
        """Create capability by ID, handling storage-specific capabilities."""
        # Try base class capabilities first
        capability = super()._create_capability_by_id(cap_id)
        if capability:
            return capability

        # Handle storage-specific capabilities
        if cap_id == EXT_CAP_ID_AER:
            return self._create_aer_capability()
        return None

    def _create_pm_capability(self, aux_current: int = 0) -> Dict[str, Any]:
        """Create Power Management capability for storage devices."""
        # RAID controllers may need aux power
        aux_current = (
            STORAGE_POWER_CONSTANTS["raid_aux_current"]
            if self._device_category == "raid"
            else STORAGE_POWER_CONSTANTS["default_aux_current"]
        )
        return super()._create_pm_capability(aux_current)

    def _create_msi_capability(
        self,
        multi_message_capable: Optional[int] = None,
        supports_per_vector_masking: Optional[bool] = None,
    ) -> Dict[str, Any]:
        """Create MSI capability for storage devices."""
        if multi_message_capable is None:
            # Storage devices typically need more interrupts
            if self._device_category == "nvme":
                multi_message_capable = STORAGE_MSI_MESSAGES["nvme"]
            elif self._device_category in ["sas", "raid"]:
                multi_message_capable = STORAGE_MSI_MESSAGES["sas"]
            else:
                multi_message_capable = STORAGE_MSI_MESSAGES["default"]

        return super()._create_msi_capability(
            multi_message_capable, supports_per_vector_masking
        )

    def _create_pcie_capability(
        self,
        max_payload_size: Optional[int] = None,
        supports_flr: bool = True,
    ) -> Dict[str, Any]:
        """Create PCIe Express capability for storage devices."""
        if max_payload_size is None:
            # Storage devices benefit from larger payloads
            if self._device_category == "nvme":
                max_payload_size = STORAGE_MAX_PAYLOAD_SIZES["nvme"]
            elif self._device_category in ["sas", "raid"]:
                max_payload_size = STORAGE_MAX_PAYLOAD_SIZES["sas"]
            else:
                max_payload_size = STORAGE_MAX_PAYLOAD_SIZES["default"]

        return super()._create_pcie_capability(max_payload_size, supports_flr)

    def _calculate_default_queue_count(self) -> int:
        """Calculate appropriate queue count for storage devices."""
        base_queues = STORAGE_BASE_QUEUE_COUNTS["minimum"]

        # Scale based on storage type
        if self._device_category == "nvme":
            base_queues = (
                STORAGE_BASE_QUEUE_COUNTS["nvme_high_end"]
                if self.device_id > STORAGE_DEVICE_ID_THRESHOLDS["high_end_nvme"]
                else STORAGE_BASE_QUEUE_COUNTS["nvme_standard"]
            )
        elif self._device_category in ["sas", "raid"]:
            enterprise_threshold = STORAGE_DEVICE_ID_THRESHOLDS["enterprise_storage"]
            base_queues = (
                STORAGE_BASE_QUEUE_COUNTS["sas_enterprise"]
                if self.device_id > enterprise_threshold
                else STORAGE_BASE_QUEUE_COUNTS["sas_standard"]
            )
        else:
            base_queues = STORAGE_BASE_QUEUE_COUNTS["default"]

        # Add entropy-based variation for security
        entropy_factor = (
            (self.vendor_id ^ self.device_id) & STORAGE_BIT_MANIPULATION["entropy_mask"]
        ) / STORAGE_BIT_MANIPULATION["entropy_divisor"]
        entropy_multiplier = STORAGE_BIT_MANIPULATION["entropy_factor"]
        variation = int(base_queues * entropy_factor * entropy_multiplier)
        if (self.device_id & STORAGE_BIT_MANIPULATION["device_id_parity_mask"]) == 0:
            variation = -variation

        final_queues = max(1, base_queues + variation)
        return 1 << (final_queues - 1).bit_length()

    def _create_aer_capability(self) -> Dict[str, Any]:
        """Create Advanced Error Reporting capability."""
        aer_values = AER_CAPABILITY_VALUES
        return {
            "cap_id": EXT_CAP_ID_AER,
            "uncorrectable_error_mask": aer_values["uncorrectable_error_mask"],
            "uncorrectable_error_severity": (
                aer_values["uncorrectable_error_severity"]
            ),
            "correctable_error_mask": aer_values["correctable_error_mask"],
            "advanced_error_capabilities": aer_values["advanced_error_capabilities"],
        }

    def generate_bar_configuration(self) -> List[Dict[str, Any]]:
        """Generate realistic BAR configuration for storage device."""
        bars = []

        # Base register space - size based on device type
        if self._device_category == "nvme":
            # NVMe controllers need larger register space
            base_size = STORAGE_BAR_SIZES["nvme_registers"]
            bars.append(
                {
                    "bar": 0,
                    "type": "memory",
                    "size": base_size,
                    "prefetchable": False,
                    "description": "NVMe registers",
                }
            )
        elif self._device_category in ["sas", "raid"]:
            # SAS/RAID controllers
            base_size = STORAGE_BAR_SIZES["sas_raid_registers"]
            bars.append(
                {
                    "bar": 0,
                    "type": "memory",
                    "size": base_size,
                    "prefetchable": False,
                    "description": "Controller registers",
                }
            )
            # Optional IO space for legacy compatibility
            bars.append(
                {
                    "bar": 1,
                    "type": "io",
                    "size": STORAGE_BAR_SIZES["legacy_io"],
                    "prefetchable": False,
                    "description": "Legacy IO",
                }
            )
        else:
            # SATA/IDE controllers
            base_size = STORAGE_BAR_SIZES["sata_registers"]
            bars.append(
                {
                    "bar": 0,
                    "type": "memory",
                    "size": base_size,
                    "prefetchable": False,
                    "description": "SATA registers",
                }
            )

        # MSI-X table space for devices that support it
        if CAP_ID_MSIX in self._capabilities:
            vector_count = self._calculate_default_queue_count()
            table_size = max(
                STORAGE_BAR_SIZES["minimum_msix_table"],
                (vector_count * 16 + STORAGE_BIT_MANIPULATION["alignment_mask"])
                & ~STORAGE_BIT_MANIPULATION["alignment_mask"],
            )

            bars.append(
                {
                    "bar": 2,
                    "type": "memory",
                    "size": table_size,
                    "prefetchable": False,
                    "description": "MSI-X table",
                }
            )

        return bars

    def generate_device_features(self) -> Dict[str, Any]:
        """Generate storage-specific device features."""
        features = {
            "category": self._device_category,
            "queue_count": self._calculate_default_queue_count(),
            "supports_ncq": True,
            "supports_trim": self._device_category in ["nvme", "sata"],
        }

        # Category-specific features
        if self._device_category == "nvme":
            namespace_threshold = STORAGE_FEATURE_THRESHOLDS["namespace_management"]
            namespaces_threshold = STORAGE_FEATURE_THRESHOLDS["max_namespaces_high"]
            pci_gen4_threshold = STORAGE_FEATURE_THRESHOLDS["pci_gen4"]

            features.update(
                {
                    "supports_namespace_management": (
                        self.device_id > namespace_threshold
                    ),
                    "max_namespaces": (
                        STORAGE_DEVICE_LIMITS["max_namespaces_high"]
                        if self.device_id > namespaces_threshold
                        else STORAGE_DEVICE_LIMITS["max_namespaces_standard"]
                    ),
                    "supports_nvme_mi": True,
                    "pci_gen": 4 if self.device_id > pci_gen4_threshold else 3,
                }
            )
        elif self._device_category in ["sas", "raid"]:
            enterprise_threshold = STORAGE_DEVICE_ID_THRESHOLDS["enterprise_storage"]
            features.update(
                {
                    "supports_raid_levels": [0, 1, 5, 6, 10],
                    "max_drives": (
                        STORAGE_DEVICE_LIMITS["max_drives_enterprise"]
                        if self.device_id > enterprise_threshold
                        else STORAGE_DEVICE_LIMITS["max_drives_standard"]
                    ),
                    "supports_hot_swap": True,
                }
            )
        elif self._device_category == "sata":
            high_end_threshold = STORAGE_DEVICE_ID_THRESHOLDS["high_end_storage"]
            port_multiplier_threshold = STORAGE_FEATURE_THRESHOLDS["port_multiplier"]
            features.update(
                {
                    "max_ports": (
                        STORAGE_DEVICE_LIMITS["max_ports_high"]
                        if self.device_id > high_end_threshold
                        else STORAGE_DEVICE_LIMITS["max_ports_standard"]
                    ),
                    "supports_port_multiplier": (
                        self.device_id > port_multiplier_threshold
                    ),
                    "supports_fis_switching": True,
                }
            )

        # Advanced features for high-end devices
        if self._supports_aer():
            features["supports_aer"] = True

        return features


def create_storage_function_capabilities(
    vendor_id: int, device_id: int
) -> Dict[str, Any]:
    """
    Factory function to create storage function capabilities from build-time IDs.

    Args:
        vendor_id: PCI vendor ID from build process
        device_id: PCI device ID from build process

    Returns:
        Complete storage device configuration dictionary
    """
    return create_function_capabilities(
        StorageFunctionAnalyzer, vendor_id, device_id, "StorageFunctionAnalyzer"
    )
