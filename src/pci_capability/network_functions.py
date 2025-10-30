#!/usr/bin/env python3
"""
Network Function Capabilities

This module provides dynamic network function capabilities for PCIe device
generation. It analyzes build-time provided vendor/device IDs to generate
realistic network and media function capabilities without hardcoding.

The module integrates with the existing templating and logging infrastructure to
provide production-ready dynamic capability generation.
"""

import logging
from typing import Any, Dict, List, Optional, Set, Tuple


from .base_function_analyzer import (BaseFunctionAnalyzer,
                                     create_function_capabilities)

from .constants import (  # Common PCI Capability IDs; Extended PCI Capability IDs; Network class codes; Network device ID thresholds; Network device patterns; Enterprise patterns; BAR configuration constants; Queue count constants; VF constants; Latency constants; SR-IOV constants; MSI-X BAR allocation patterns; Additional vendor IDs; Common device ID masks; Entropy and variation constants
    BASE_QUEUE_COUNT, BASE_REGISTER_SIZE_ADVANCED, BASE_REGISTER_SIZE_BASIC,
    BASE_VF_COUNT, BROADCOM_ENTERPRISE_MASK, BROADCOM_ENTERPRISE_THRESHOLD,
    CAP_ID_MSI, CAP_ID_MSIX, CAP_ID_PCIE, CAP_ID_PM, DEVICE_ID_LOWER_MASK,
    DEVICE_ID_THRESHOLD_ADVANCED, DEVICE_ID_THRESHOLD_BASIC,
    DEVICE_ID_THRESHOLD_ENTERPRISE, DEVICE_ID_THRESHOLD_HIGH_END,
    DEVICE_ID_THRESHOLD_ULTRA_HIGH, DEVICE_ID_THRESHOLD_WIFI_ADVANCED,
    DEVICE_ID_THRESHOLD_WIFI_PREMIUM, DEVICE_ID_THRESHOLD_WIFI_ULTRA,
    DEVICE_PATTERN_HIGH_FEATURE, DEVICE_PATTERN_INTEL_LAN_BASE,
    DEVICE_PATTERN_INTEL_LAN_EXT, DEVICE_PATTERN_INTEL_WIRELESS_BASE,
    DEVICE_PATTERN_INTEL_WIRELESS_EXT, DEVICE_PATTERN_REALTEK_ETH_BASE,
    DEVICE_PATTERN_REALTEK_ETH_EXT, DEVICE_PATTERN_REALTEK_WIFI_BASE,
    DEVICE_PATTERN_REALTEK_WIFI_EXT, DEVICE_PATTERN_WIRELESS, ENTROPY_DIVISOR,
    ENTROPY_MASK, ENTROPY_VARIATION_FACTOR, ETHERNET_FLASH_SIZE,
    EXT_CAP_ID_ACS, EXT_CAP_ID_ARI, EXT_CAP_ID_LTR, EXT_CAP_ID_PTM,
    EXT_CAP_ID_SRIOV, INTEL_ENTERPRISE_MASK, INTEL_ENTERPRISE_THRESHOLD,
    LTR_LATENCY_ETHERNET, LTR_LATENCY_WIFI, MSIX_BAR_VARIATION_MASK,
    MSIX_BAR_VARIATION_THRESHOLD, MSIX_TABLE_ALIGN_MASK, MSIX_TABLE_ENTRY_SIZE,
    MSIX_TABLE_MIN_SIZE, NETWORK_CLASS_CODES, PTM_CLOCK_GRANULARITY,
    QUEUE_COUNT_ADVANCED, QUEUE_COUNT_HIGH, QUEUE_COUNT_ULTRA,
    REGISTER_SIZE_VARIATION_MASK, REGISTER_SIZE_VARIATION_MULTIPLIER,
    SIZE_PADDING_MASK, SRIOV_MIN_QUEUES, SRIOV_SUPPORTED_PAGE_SIZES,
    SRIOV_SYSTEM_PAGE_SIZE, VENDOR_ID_BROADCOM, VF_COUNT_HIGH, VF_COUNT_ULTRA,
    VF_COUNT_VARIATION_MASK, VF_COUNT_VARIATION_OFFSET, WIFI_MAX_QUEUES,
    WIFI_REGISTER_SIZE)

logger = logging.getLogger(__name__)


class NetworkFunctionAnalyzer(BaseFunctionAnalyzer):
    """
    Dynamic network function capability analyzer.

    Analyzes vendor/device IDs provided at build time to generate realistic
    network function capabilities without hardcoding device-specific behavior.
    """

    # PCI class codes for network devices
    CLASS_CODES = NETWORK_CLASS_CODES

    def __init__(self, vendor_id: int, device_id: int):
        """
        Initialize analyzer with build-time provided vendor/device IDs.

        Args:
            vendor_id: PCI vendor ID from build process
            device_id: PCI device ID from build process
        """
        super().__init__(vendor_id, device_id, "network")

    def _analyze_device_category(self) -> str:
        """
        Analyze device category based on vendor/device ID patterns.

        Returns:
            Device category string (ethernet, wifi, bluetooth, cellular, unknown)
        """
        # Pattern-based analysis without hardcoding specific device IDs
        device_lower = self.device_id & DEVICE_ID_LOWER_MASK
        device_upper = (self.device_id >> 8) & 0xFF

        # Import vendor ID constants
        from src.device_clone.constants import (VENDOR_ID_INTEL,
                                                VENDOR_ID_REALTEK)

        # Vendor-specific patterns
        if self.vendor_id == VENDOR_ID_INTEL:  # Intel
            if device_lower in [
                DEVICE_PATTERN_INTEL_LAN_BASE,
                DEVICE_PATTERN_INTEL_LAN_EXT,
            ]:
                return "ethernet"
            if device_lower in [
                DEVICE_PATTERN_INTEL_WIRELESS_BASE,
                DEVICE_PATTERN_INTEL_WIRELESS_EXT,
            ]:
                return "wifi"
            # Add more Intel device ranges as needed
        if self.vendor_id == VENDOR_ID_REALTEK:  # Realtek
            if device_lower in [
                DEVICE_PATTERN_REALTEK_ETH_BASE,
                DEVICE_PATTERN_REALTEK_ETH_EXT,
            ]:
                return "ethernet"
            if device_lower in [
                DEVICE_PATTERN_REALTEK_WIFI_BASE,
                DEVICE_PATTERN_REALTEK_WIFI_EXT,
            ]:
                return "wifi"

        # Generic patterns based on device ID structure
        # Higher device IDs often indicate advanced features
        if device_upper >= DEVICE_PATTERN_HIGH_FEATURE:
            return "ethernet"
        if device_upper >= DEVICE_PATTERN_WIRELESS:
            return "wifi"

        return "ethernet"  # Default fallback

    def _analyze_capabilities(self) -> Set[int]:
        """
        Analyze which capabilities this device should support.

        Returns:
            Set of capability IDs that should be present
        """
        caps = set()

        # Always include basic network capabilities
        caps.update([0x01, 0x05, 0x10, 0x11])  # PM, MSI, PCIe, MSI-X

        # Advanced capabilities based on device analysis
        if self._supports_sriov():
            caps.add(EXT_CAP_ID_SRIOV)
            # Add ACS/ARI with device-specific variation for security
            if (self.device_id & 0x3) != 0:  # 75% chance based on device ID bits
                caps.add(EXT_CAP_ID_ACS)
            if (self.vendor_id & 0x1) == 0:  # 50% chance based on vendor ID bit
                caps.add(EXT_CAP_ID_ARI)

        if self._supports_ltr():
            caps.add(EXT_CAP_ID_LTR)

        if self._supports_ptm():
            caps.add(EXT_CAP_ID_PTM)

        return caps

    def _supports_sriov(self) -> bool:
        """Check if device likely supports SR-IOV based on patterns."""
        # Import vendor ID constants
        from src.device_clone.constants import VENDOR_ID_INTEL

        # Convert enum to int if needed
        intel_vendor_id = int(VENDOR_ID_INTEL) if hasattr(VENDOR_ID_INTEL, 'value') else VENDOR_ID_INTEL

        # High-end devices (higher device IDs) more likely to support SR-IOV
        if (
            self.device_id > DEVICE_ID_THRESHOLD_ADVANCED
            and self._device_category == "ethernet"
        ):
            # Check for enterprise/datacenter patterns
            if (
                self.vendor_id == intel_vendor_id
                and (self.device_id & INTEL_ENTERPRISE_MASK)
                >= INTEL_ENTERPRISE_THRESHOLD
            ):
                return True
            if (
                self.vendor_id == VENDOR_ID_BROADCOM
                and (self.device_id & BROADCOM_ENTERPRISE_MASK)
                >= BROADCOM_ENTERPRISE_THRESHOLD
            ):
                return True
        return False

    def _supports_ltr(self) -> bool:
        """Check if device likely supports LTR."""
        # Most modern network devices support LTR
        return self.device_id > DEVICE_ID_THRESHOLD_BASIC

    def _supports_ptm(self) -> bool:
        """Check if device likely supports PTM."""
        # PTM mainly for high-speed Ethernet
        return (
            self._device_category == "ethernet"
            and self.device_id > DEVICE_ID_THRESHOLD_ADVANCED
            and self._supports_sriov()
        )

    def get_device_class_code(self) -> int:
        """Get appropriate PCI class code for this device."""
        default_class = self.CLASS_CODES["ethernet"]
        return self.CLASS_CODES.get(self._device_category, default_class)

    def _create_capability_by_id(self, cap_id: int) -> Optional[Dict[str, Any]]:
        """Override base class to handle network-specific capabilities."""
        # Handle network-specific capabilities
        if cap_id == EXT_CAP_ID_SRIOV:
            return self._create_sriov_capability()
        if cap_id == EXT_CAP_ID_ACS:
            return self._create_acs_capability()
        if cap_id == EXT_CAP_ID_LTR:
            return self._create_ltr_capability()
        if cap_id == EXT_CAP_ID_PTM:
            return self._create_ptm_capability()
        if cap_id == EXT_CAP_ID_ARI:
            return self._create_ari_capability()

        # Use base class for common capabilities with network-specific parameters
        if cap_id == CAP_ID_PM:
            return self._create_pm_capability(
                aux_current=0
            )  # Network devices don't need aux power
        if cap_id == CAP_ID_MSI:
            queue_count = self._calculate_network_queue_count()
            masking_support = self.device_id > DEVICE_ID_THRESHOLD_BASIC
            return self._create_msi_capability(
                multi_message_capable=min(5, queue_count.bit_length()),
                supports_per_vector_masking=masking_support,
            )
        if cap_id == CAP_ID_PCIE:
            max_payload = 512 if self.device_id > DEVICE_ID_THRESHOLD_ADVANCED else 256
            return self._create_pcie_capability(
                max_payload_size=max_payload, supports_flr=True
            )
        if cap_id == CAP_ID_MSIX:
            table_bar, pba_bar = self._get_network_msix_bars()
            return self._create_msix_capability(
                table_size=self._calculate_network_queue_count(),
                table_bar=table_bar,
                pba_bar=pba_bar,
            )

        return None

    def _get_network_msix_bars(self) -> Tuple[int, int]:
        """Get network-specific MSI-X BAR allocation with entropy for uniqueness."""
        # Network devices typically use BAR 1 for MSI-X
        # Add device-specific variation for security against firmware fingerprinting
        if (self.device_id & MSIX_BAR_VARIATION_MASK) >= MSIX_BAR_VARIATION_THRESHOLD:
            return (0, 0)  # Some devices use BAR 0
        return (1, 1)  # Most use BAR 1

    def _get_default_msix_bar_allocation(self) -> Tuple[int, int]:
        """Override base class for network-specific MSI-X allocation."""
        return self._get_network_msix_bars()

    def _calculate_network_queue_count(self) -> int:
        """Calculate network-specific queue count with entropy."""
        base_queues = BASE_QUEUE_COUNT

        # Scale based on device ID (higher = more capable)
        if self.device_id > DEVICE_ID_THRESHOLD_ULTRA_HIGH:
            base_queues = QUEUE_COUNT_ULTRA
        elif self.device_id > DEVICE_ID_THRESHOLD_ADVANCED:
            base_queues = QUEUE_COUNT_HIGH
        elif self.device_id > DEVICE_ID_THRESHOLD_BASIC:
            base_queues = QUEUE_COUNT_ADVANCED

        # Adjust for device category
        if self._device_category == "wifi":
            # WiFi typically has fewer queues
            base_queues = min(base_queues, WIFI_MAX_QUEUES)
        elif self._device_category == "ethernet" and self._supports_sriov():
            # SR-IOV devices need more queues
            base_queues = max(base_queues, SRIOV_MIN_QUEUES)

        # Add entropy-based variation for security (±25% based on ID bits)
        entropy_factor = (
            (self.vendor_id ^ self.device_id) & ENTROPY_MASK
        ) / ENTROPY_DIVISOR
        variation = int(base_queues * entropy_factor * ENTROPY_VARIATION_FACTOR)
        if (self.device_id & 0x1) == 0:
            variation = -variation

        final_queues = max(1, base_queues + variation)
        # Ensure power of 2 for realistic hardware
        return 1 << (final_queues - 1).bit_length()

    def _calculate_default_queue_count(self) -> int:
        """Override base class to use network-specific calculation."""
        return self._calculate_network_queue_count()

    def generate_bar_configuration(self) -> List[Dict[str, Any]]:
        """Generate realistic BAR configuration for network device."""
        bars = []

        # Base register space - size based on device complexity with entropy
        base_size = (
            BASE_REGISTER_SIZE_BASIC
            if self.device_id < DEVICE_ID_THRESHOLD_ADVANCED
            else BASE_REGISTER_SIZE_ADVANCED
        )
        # Add device-specific size variation for security
        size_variation = (
            self.device_id & REGISTER_SIZE_VARIATION_MASK
        ) * REGISTER_SIZE_VARIATION_MULTIPLIER
        base_size += size_variation

        bars.append(
            {
                "bar": 0,
                "type": "memory",
                "size": base_size,
                "prefetchable": False,
                "description": "Device registers",
            }
        )

        # MSI-X table space with dynamic sizing
        if 0x11 in self._capabilities:
            # Vary MSI-X table BAR size based on vector count and device entropy
            vector_count = self._calculate_network_queue_count()
            table_size = max(
                MSIX_TABLE_MIN_SIZE,
                (
                    (vector_count * MSIX_TABLE_ENTRY_SIZE + MSIX_TABLE_ALIGN_MASK)
                    & ~MSIX_TABLE_ALIGN_MASK
                ),
            )
            # Add entropy-based padding for uniqueness
            size_padding = (
                (self.vendor_id ^ self.device_id) & SIZE_PADDING_MASK
            ) * REGISTER_SIZE_VARIATION_MULTIPLIER
            table_size += size_padding

            bars.append(
                {
                    "bar": 1,
                    "type": "memory",
                    "size": table_size,
                    "prefetchable": False,
                    "description": "MSI-X table",
                }
            )

        # Flash/EEPROM space for Ethernet
        if self._device_category == "ethernet":
            bars.append(
                {
                    "bar": 2,
                    "type": "memory",
                    "size": ETHERNET_FLASH_SIZE,
                    "prefetchable": False,
                    "description": "Flash/EEPROM",
                }
            )

        # Additional register space for WiFi
        elif self._device_category == "wifi":
            bars.append(
                {
                    "bar": 2,
                    "type": "memory",
                    "size": WIFI_REGISTER_SIZE,
                    "prefetchable": False,
                    "description": "WiFi registers",
                }
            )

        return bars

    def generate_device_features(self) -> Dict[str, Any]:
        """Generate network-specific device features."""
        features = {
            "category": self._device_category,
            "queue_count": self._calculate_network_queue_count(),
            "supports_rss": True,
            "supports_tso": True,
            "supports_checksum_offload": True,
            "supports_vlan": True,
        }

        # Category-specific features
        if self._device_category == "ethernet":
            jumbo_support = self.device_id > DEVICE_ID_THRESHOLD_ENTERPRISE
            features.update(
                {
                    "supports_jumbo_frames": jumbo_support,
                    "supports_flow_control": True,
                    "max_link_speed": self._estimate_link_speed(),
                }
            )
        elif self._device_category == "wifi":
            features.update(
                {
                    "supports_mimo": True,
                    "max_spatial_streams": self._estimate_spatial_streams(),
                    "supported_bands": self._estimate_wifi_bands(),
                }
            )

        # Advanced features for high-end devices
        if self._supports_sriov():
            features["supports_sriov"] = True
            features["max_vfs"] = self._calculate_max_vfs()

        return features

    def _create_sriov_capability(self) -> Dict[str, Any]:
        """Create SR-IOV capability."""
        max_vfs = self._calculate_max_vfs()
        return {
            "cap_id": EXT_CAP_ID_SRIOV,
            "initial_vfs": max_vfs,
            "total_vfs": max_vfs,
            "num_vf_bars": 6,
            # VF typically has incremented device ID
            "vf_device_id": self.device_id + 1,
            "supported_page_sizes": SRIOV_SUPPORTED_PAGE_SIZES,
            "system_page_size": SRIOV_SYSTEM_PAGE_SIZE,
        }

    def _create_acs_capability(self) -> Dict[str, Any]:
        """Create Access Control Services capability."""
        return {
            "cap_id": EXT_CAP_ID_ACS,
            "source_validation": True,
            "translation_blocking": True,
            "p2p_request_redirect": True,
            "p2p_completion_redirect": True,
            "upstream_forwarding": True,
        }

    def _create_ltr_capability(self) -> Dict[str, Any]:
        """Create Latency Tolerance Reporting capability."""
        # Calculate realistic latency values based on device type
        base_latency = (
            LTR_LATENCY_WIFI
            if self._device_category == "wifi"
            else LTR_LATENCY_ETHERNET
        )
        return {
            "cap_id": EXT_CAP_ID_LTR,
            "max_snoop_latency": base_latency,
            "max_no_snoop_latency": base_latency,
        }

    def _create_ptm_capability(self) -> Dict[str, Any]:
        """Create Precision Time Measurement capability."""
        return {
            "cap_id": EXT_CAP_ID_PTM,
            "ptm_requester_capable": True,
            "ptm_responder_capable": True,
            "ptm_root_capable": False,
            "local_clock_granularity": PTM_CLOCK_GRANULARITY,
        }

    def _create_ari_capability(self) -> Dict[str, Any]:
        """Create Alternative Routing-ID Interpretation capability."""
        return {
            "cap_id": EXT_CAP_ID_ARI,
            "mfvc_function_groups_capability": False,
            "acs_function_groups_capability": False,
            "next_function_number": 0,
        }

    def _calculate_max_vfs(self) -> int:
        """Calculate maximum VFs for SR-IOV devices."""
        if not self._supports_sriov():
            return 0

        # Base VF count on device capability with entropy
        base_vfs = BASE_VF_COUNT
        if self.device_id > DEVICE_ID_THRESHOLD_HIGH_END:
            base_vfs = VF_COUNT_ULTRA
        elif self.device_id > DEVICE_ID_THRESHOLD_ADVANCED:
            base_vfs = VF_COUNT_HIGH

        # Add device-specific variation for uniqueness
        variation = (
            self.device_id & VF_COUNT_VARIATION_MASK
        ) - VF_COUNT_VARIATION_OFFSET
        return max(1, base_vfs + variation)

    def _estimate_link_speed(self) -> str:
        """Estimate link speed for Ethernet devices."""
        if self.device_id > DEVICE_ID_THRESHOLD_HIGH_END:
            return "25Gbps"
        if self.device_id > DEVICE_ID_THRESHOLD_ADVANCED:
            return "10Gbps"
        if self.device_id > DEVICE_ID_THRESHOLD_ENTERPRISE:
            return "1Gbps"
        return "100Mbps"

    def _estimate_spatial_streams(self) -> int:
        """Estimate spatial streams for WiFi devices."""
        if self.device_id > DEVICE_ID_THRESHOLD_WIFI_ULTRA:
            return 8
        if self.device_id > DEVICE_ID_THRESHOLD_WIFI_ADVANCED:
            return 4
        return 2

    def _estimate_wifi_bands(self) -> List[str]:
        """Estimate supported WiFi bands."""
        bands = ["2.4GHz"]

        if self.device_id > DEVICE_ID_THRESHOLD_ULTRA_HIGH:
            bands.append("5GHz")
        if self.device_id > DEVICE_ID_THRESHOLD_WIFI_PREMIUM:
            bands.append("6GHz")

        return bands


def create_network_function_capabilities(
    vendor_id: int, device_id: int
) -> Dict[str, Any]:
    """
    Factory function to create network function capabilities from build-time IDs.

    Args:
        vendor_id: PCI vendor ID from build process
        device_id: PCI device ID from build process

    Returns:
        Complete network device configuration dictionary
    """
    return create_function_capabilities(
        NetworkFunctionAnalyzer, vendor_id, device_id, "NetworkFunctionAnalyzer"
    )
