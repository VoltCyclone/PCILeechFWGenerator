#!/usr/bin/env python3
"""
USB Function Capabilities

This module provides dynamic USB function capabilities for PCIe device
generation. It analyzes build-time provided vendor/device IDs to generate
realistic USB controller capabilities without hardcoding.

The module integrates with the existing templating and logging infrastructure
to provide production-ready dynamic capability generation.
"""

import logging

from typing import Any, Dict, List, Optional, Set


from .base_function_analyzer import (BaseFunctionAnalyzer,
                                     create_function_capabilities)

from .constants import AMD_EHCI_PATTERNS  # USB Function Analyzer Constants

from .constants import (AMD_XHCI_PATTERNS, CLASS_CODES, INTEL_EHCI_PATTERNS,
                        INTEL_UHCI_PATTERNS, INTEL_XHCI_PATTERNS,
                        NEC_XHCI_PATTERNS, USB_AUX_CURRENT_OTHER,
                        USB_AUX_CURRENT_XHCI_USB4, USB_BAR_SIZE_EHCI_BASE,
                        USB_BAR_SIZE_IO_PORTS, USB_BAR_SIZE_MSIX_TABLE,
                        USB_BAR_SIZE_XHCI_BASE, USB_CAP_ID_MSI,
                        USB_CAP_ID_MSIX, USB_CAP_ID_PCIE, USB_CAP_ID_PM,
                        USB_CATEGORY_EHCI_THRESHOLD,
                        USB_CATEGORY_UHCI_THRESHOLD,
                        USB_CATEGORY_USB4_THRESHOLD,
                        USB_CATEGORY_XHCI_THRESHOLD_HIGH,
                        USB_CATEGORY_XHCI_THRESHOLD_LOW, USB_DEVICE_LOWER_MASK,
                        USB_DEVICE_UPPER_MASK, USB_DEVICE_UPPER_SHIFT,
                        USB_ENTROPY_DIVISOR, USB_ENTROPY_MASK,
                        USB_ENTROPY_VARIATION_FACTOR, USB_MSI_MESSAGES_OTHER,
                        USB_MSI_MESSAGES_XHCI_USB4, USB_MSIX_SUPPORT_THRESHOLD,
                        USB_PCIE_MAX_PAYLOAD_SIZE, USB_PORT_COUNT_EHCI_HIGH,
                        USB_PORT_COUNT_EHCI_LOW,
                        USB_PORT_COUNT_HIGH_THRESHOLD_EHCI,
                        USB_PORT_COUNT_HIGH_THRESHOLD_XHCI,
                        USB_PORT_COUNT_OHCI, USB_PORT_COUNT_UHCI,
                        USB_PORT_COUNT_USB4, USB_PORT_COUNT_XHCI_HIGH,
                        USB_PORT_COUNT_XHCI_LOW, USB_QUEUE_BASE_EHCI,
                        USB_QUEUE_BASE_OTHER, USB_QUEUE_BASE_USB4,
                        USB_QUEUE_BASE_XHCI, USB_SPEED_5GBPS, USB_SPEED_10GBPS,
                        USB_SPEED_12MBPS, USB_SPEED_40GBPS, USB_SPEED_480MBPS,
                        USB_VERSION_11, USB_VERSION_20, USB_VERSION_30,
                        USB_VERSION_31, USB_VERSION_31_THRESHOLD,
                        USB_VERSION_40, VENDOR_ID_NEC, VENDOR_ID_VIA,
                        VIA_UHCI_PATTERNS)

logger = logging.getLogger(__name__)


class USBFunctionAnalyzer(BaseFunctionAnalyzer):
    """
    Dynamic USB function capability analyzer.

    Analyzes vendor/device IDs provided at build time to generate realistic
    USB function capabilities without hardcoding device-specific behavior.
    """

    def __init__(self, vendor_id: int, device_id: int):
        """
        Initialize analyzer with build-time provided vendor/device IDs.

        Args:
            vendor_id: PCI vendor ID from build process
            device_id: PCI device ID from build process
        """
        super().__init__(vendor_id, device_id, "usb")

    def _analyze_device_category(self) -> str:
        """
        Analyze device category based on vendor/device ID patterns.

        Returns:
            Device category string (uhci, ohci, ehci, xhci, usb4, other_usb)
        """
        device_lower = self.device_id & USB_DEVICE_LOWER_MASK
        device_upper = (
            self.device_id >> USB_DEVICE_UPPER_SHIFT
        ) & USB_DEVICE_UPPER_MASK

        # Import vendor ID constants
        from src.device_clone.constants import VENDOR_ID_AMD, VENDOR_ID_INTEL

        # Vendor-specific patterns
        if self.vendor_id == VENDOR_ID_INTEL:  # Intel
            if device_lower in INTEL_XHCI_PATTERNS:
                return "xhci"
            if device_lower in INTEL_EHCI_PATTERNS:
                return "ehci"
            if device_lower in INTEL_UHCI_PATTERNS:
                return "uhci"
        if self.vendor_id == VENDOR_ID_AMD:  # AMD
            if device_lower in AMD_XHCI_PATTERNS:
                return "xhci"
            if device_lower in AMD_EHCI_PATTERNS:
                return "ehci"
        if self.vendor_id == VENDOR_ID_NEC:  # NEC
            if device_lower in NEC_XHCI_PATTERNS:
                return "xhci"
        if self.vendor_id == VENDOR_ID_VIA:  # VIA
            if device_lower in VIA_UHCI_PATTERNS:
                return "uhci"

        # Generic patterns
        if device_upper >= USB_CATEGORY_XHCI_THRESHOLD_HIGH:
            return "usb4" if device_upper >= USB_CATEGORY_USB4_THRESHOLD else "xhci"
        if device_upper >= USB_CATEGORY_XHCI_THRESHOLD_LOW:
            return "xhci"
        if device_upper >= USB_CATEGORY_EHCI_THRESHOLD:
            return "ehci"
        if device_upper >= USB_CATEGORY_UHCI_THRESHOLD:
            return "uhci"
        else:
            return "ohci"

    def _analyze_capabilities(self) -> Set[int]:
        caps = set()
        caps.update([USB_CAP_ID_PM, USB_CAP_ID_MSI, USB_CAP_ID_PCIE])
        if self._supports_msix():
            caps.add(USB_CAP_ID_MSIX)
        return caps

    def _supports_msix(self) -> bool:
        return (
            self._device_category in ["xhci", "usb4"]
            and self.device_id > USB_MSIX_SUPPORT_THRESHOLD
        )

    def get_device_class_code(self) -> int:
        """Get appropriate PCI class code for this device."""
        return CLASS_CODES.get(self._device_category, CLASS_CODES["xhci"])

    def _create_pm_capability(self, aux_current: int = 0) -> Dict[str, Any]:
        if self._device_category in ["xhci", "usb4"]:
            aux_current = USB_AUX_CURRENT_XHCI_USB4
        else:
            aux_current = USB_AUX_CURRENT_OTHER
        return super()._create_pm_capability(aux_current)

    def _create_msi_capability(
        self,
        multi_message_capable: Optional[int] = None,
        supports_per_vector_masking: Optional[bool] = None,
    ) -> Dict[str, Any]:
        if multi_message_capable is None:
            if self._device_category in ["xhci", "usb4"]:
                multi_message_capable = USB_MSI_MESSAGES_XHCI_USB4
            else:
                multi_message_capable = USB_MSI_MESSAGES_OTHER

        return super()._create_msi_capability(
            multi_message_capable, supports_per_vector_masking
        )

    def _create_pcie_capability(
        self,
        max_payload_size: Optional[int] = None,
        supports_flr: bool = True,
    ) -> Dict[str, Any]:
        if max_payload_size is None:
            max_payload_size = USB_PCIE_MAX_PAYLOAD_SIZE
        return super()._create_pcie_capability(max_payload_size, supports_flr)

    def _calculate_default_queue_count(self) -> int:
        if self._device_category == "usb4":
            base_queues = USB_QUEUE_BASE_USB4
        elif self._device_category == "xhci":
            base_queues = USB_QUEUE_BASE_XHCI
        elif self._device_category == "ehci":
            base_queues = USB_QUEUE_BASE_EHCI
        else:
            base_queues = USB_QUEUE_BASE_OTHER

        entropy_factor = (self.vendor_id ^ self.device_id) & USB_ENTROPY_MASK
        entropy_factor = entropy_factor / USB_ENTROPY_DIVISOR
        variation = int(base_queues * entropy_factor * USB_ENTROPY_VARIATION_FACTOR)
        if (self.device_id & 0x1) == 0:
            variation = -variation

        final_queues = max(1, base_queues + variation)
        return 1 << (final_queues - 1).bit_length()

    def generate_bar_configuration(self) -> List[Dict[str, Any]]:
        bars = []
        if self._device_category in ["xhci", "usb4"]:
            base_size = USB_BAR_SIZE_XHCI_BASE
            bars.append(
                {
                    "bar": 0,
                    "type": "memory",
                    "size": base_size,
                    "prefetchable": False,
                    "description": "xHCI registers",
                }
            )

            if USB_CAP_ID_MSIX in self._capabilities:
                bars.append(
                    {
                        "bar": 1,
                        "type": "memory",
                        "size": USB_BAR_SIZE_MSIX_TABLE,
                        "prefetchable": False,
                        "description": "MSI-X table",
                    }
                )
        elif self._device_category == "ehci":
            base_size = USB_BAR_SIZE_EHCI_BASE
            bars.append(
                {
                    "bar": 0,
                    "type": "memory",
                    "size": base_size,
                    "prefetchable": False,
                    "description": "EHCI registers",
                }
            )
        else:
            bars.append(
                {
                    "bar": 0,
                    "type": "io",
                    "size": USB_BAR_SIZE_IO_PORTS,
                    "prefetchable": False,
                    "description": "USB IO ports",
                }
            )

        return bars

    def generate_device_features(self) -> Dict[str, Any]:
        features = {
            "category": self._device_category,
            "queue_count": self._calculate_default_queue_count(),
        }

        if self._device_category == "usb4":
            features.update(
                {
                    "usb_version": USB_VERSION_40,
                    "max_speed": USB_SPEED_40GBPS,
                    "port_count": USB_PORT_COUNT_USB4,
                    "supports_thunderbolt": True,
                    "supports_display_port": True,
                    "supports_pcie_tunneling": True,
                }
            )
        elif self._device_category == "xhci":
            usb_version = (
                USB_VERSION_31
                if self.device_id > USB_VERSION_31_THRESHOLD
                else USB_VERSION_30
            )
            max_speed = (
                USB_SPEED_10GBPS
                if self.device_id > USB_VERSION_31_THRESHOLD
                else USB_SPEED_5GBPS
            )
            port_count = (
                USB_PORT_COUNT_XHCI_HIGH
                if self.device_id > USB_PORT_COUNT_HIGH_THRESHOLD_XHCI
                else USB_PORT_COUNT_XHCI_LOW
            )

            features.update(
                {
                    "usb_version": usb_version,
                    "max_speed": max_speed,
                    "port_count": port_count,
                    "supports_streams": True,
                    "supports_lpm": True,
                }
            )
        elif self._device_category == "ehci":
            port_count = (
                USB_PORT_COUNT_EHCI_HIGH
                if self.device_id > USB_PORT_COUNT_HIGH_THRESHOLD_EHCI
                else USB_PORT_COUNT_EHCI_LOW
            )
            features.update(
                {
                    "usb_version": USB_VERSION_20,
                    "max_speed": USB_SPEED_480MBPS,
                    "port_count": port_count,
                    "supports_tt": True,
                }
            )
        elif self._device_category == "uhci":
            features.update(
                {
                    "usb_version": USB_VERSION_11,
                    "max_speed": USB_SPEED_12MBPS,
                    "port_count": USB_PORT_COUNT_UHCI,
                    "supports_legacy": True,
                }
            )
        elif self._device_category == "ohci":
            features.update(
                {
                    "usb_version": USB_VERSION_11,
                    "max_speed": USB_SPEED_12MBPS,
                    "port_count": USB_PORT_COUNT_OHCI,
                    "supports_isochronous": True,
                }
            )

        features["supports_power_management"] = True
        if self._device_category in ["xhci", "usb4"]:
            features["supports_runtime_pm"] = True

        return features


def create_usb_function_capabilities(vendor_id: int, device_id: int) -> Dict[str, Any]:
    return create_function_capabilities(
        USBFunctionAnalyzer, vendor_id, device_id, "USBFunctionAnalyzer"
    )
