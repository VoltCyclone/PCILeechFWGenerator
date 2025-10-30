#!/usr/bin/env python3
"""
Unit tests for src.pci_capability.utils

Covers classification helpers, naming/formatting, offset validation,
size estimates, and pruning action mapping.
"""

import pytest

from src.pci_capability.types import (
    CapabilityInfo,
    CapabilityType,
    EmulationCategory,
    PCICapabilityID,
    PCIExtCapabilityID,
    PruningAction,
)
from src.pci_capability.constants import (
    EXT_CAP_SIZE_ACCESS_CONTROL_SERVICES,
    EXT_CAP_SIZE_ADVANCED_ERROR_REPORTING,
    EXT_CAP_SIZE_DEFAULT,
    EXT_CAP_SIZE_DOWNSTREAM_PORT_CONTAINMENT,
    EXT_CAP_SIZE_RESIZABLE_BAR,
    STD_CAP_SIZE_DEFAULT,
    STD_CAP_SIZE_MSI,
    STD_CAP_SIZE_MSI_X,
    STD_CAP_SIZE_PCI_EXPRESS,
    STD_CAP_SIZE_POWER_MANAGEMENT,
)
from src.pci_capability import utils as U


class TestHeaderHelpers:
    def test_is_two_byte_header_capability(self):
        assert U.is_two_byte_header_capability(0x07) is True  # PCI-X
        assert U.is_two_byte_header_capability(0x04) is True  # Slot ID
        assert U.is_two_byte_header_capability(0x10) is False  # PCIe


class TestCategorization:
    def _std(self, cap_id: int) -> CapabilityInfo:
        return CapabilityInfo(
            offset=0x50,
            cap_id=cap_id,
            cap_type=CapabilityType.STANDARD,
            next_ptr=0,
            name="",
        )

    def _ext(self, cap_id: int, ver: int = 1) -> CapabilityInfo:
        return CapabilityInfo(
            offset=0x100,
            cap_id=cap_id,
            cap_type=CapabilityType.EXTENDED,
            next_ptr=0,
            name="",
            version=ver,
        )

    def test_categorize_capability_standard(self):
        assert (
            U.categorize_capability(
                self._std(PCICapabilityID.POWER_MANAGEMENT.value)
            )
            == EmulationCategory.PARTIALLY_SUPPORTED
        )
        assert (
            U.categorize_capability(self._std(PCICapabilityID.MSI.value))
            == EmulationCategory.FULLY_SUPPORTED
        )
        assert (
            U.categorize_capability(self._std(PCICapabilityID.MSI_X.value))
            == EmulationCategory.FULLY_SUPPORTED
        )
        assert (
            U.categorize_capability(self._std(PCICapabilityID.PCI_EXPRESS.value))
            == EmulationCategory.PARTIALLY_SUPPORTED
        )
        assert (
            U.categorize_capability(self._std(0x99))
            == EmulationCategory.UNSUPPORTED
        )

    def test_categorize_capability_extended(self):
        assert (
            U.categorize_capability(
                self._ext(PCIExtCapabilityID.ADVANCED_ERROR_REPORTING.value)
            )
            == EmulationCategory.PARTIALLY_SUPPORTED
        )
        assert (
            U.categorize_capability(
                self._ext(PCIExtCapabilityID.ACCESS_CONTROL_SERVICES.value)
            )
            == EmulationCategory.PARTIALLY_SUPPORTED
        )
        assert (
            U.categorize_capability(
                self._ext(PCIExtCapabilityID.DOWNSTREAM_PORT_CONTAINMENT.value)
            )
            == EmulationCategory.PARTIALLY_SUPPORTED
        )
        assert (
            U.categorize_capability(
                self._ext(PCIExtCapabilityID.RESIZABLE_BAR.value)
            )
            == EmulationCategory.PARTIALLY_SUPPORTED
        )
        assert (
            U.categorize_capability(self._ext(0x00FE))
            == EmulationCategory.UNSUPPORTED
        )

    def test_categorize_capabilities_and_pruning_actions(self):
        caps = {
            0x50: self._std(PCICapabilityID.MSI.value),
            0x60: self._std(0x99),  # unknown
            0x100: self._ext(PCIExtCapabilityID.RESIZABLE_BAR.value),
        }
        categories = U.categorize_capabilities(caps)
        assert categories[0x50] == EmulationCategory.FULLY_SUPPORTED
        assert categories[0x60] == EmulationCategory.UNSUPPORTED
        assert categories[0x100] == EmulationCategory.PARTIALLY_SUPPORTED

        # Map categories to actions
        actions = U.determine_pruning_actions(caps, categories)
        assert actions[0x50] == PruningAction.KEEP
        assert actions[0x60] == PruningAction.REMOVE
        assert actions[0x100] == PruningAction.MODIFY

    def test_determine_pruning_action_scalars(self):
        assert (
            U.determine_pruning_action(EmulationCategory.FULLY_SUPPORTED)
            == PruningAction.KEEP
        )
        assert (
            U.determine_pruning_action(EmulationCategory.PARTIALLY_SUPPORTED)
            == PruningAction.MODIFY
        )
        assert (
            U.determine_pruning_action(EmulationCategory.UNSUPPORTED)
            == PruningAction.REMOVE
        )
        assert (
            U.determine_pruning_action(EmulationCategory.CRITICAL)
            == PruningAction.KEEP
        )


class TestNamingOffsetsFormatting:
    def test_get_capability_name(self):
        # Known standard
        assert (
            U.get_capability_name(
                PCICapabilityID.PCI_EXPRESS.value, CapabilityType.STANDARD
            )
            == "PCI Express"
        )
        # Unknown standard -> Unknown (0x..) pattern
        unk_std = U.get_capability_name(0xFE, CapabilityType.STANDARD)
        assert unk_std.startswith("Unknown (") and "0xfe" in unk_std.lower()

        # Known extended
        assert (
            U.get_capability_name(
                PCIExtCapabilityID.RESIZABLE_BAR.value, CapabilityType.EXTENDED
            )
            == "Resizable BAR"
        )
        # Unknown extended (above 0x29 to avoid special logging path)
        unk_ext = U.get_capability_name(0x0030, CapabilityType.EXTENDED)
        assert unk_ext.startswith("Unknown Extended (") and "0x0030" in unk_ext

    def test_validate_capability_offset(self):
        # Standard in range
        assert U.validate_capability_offset(0x40, CapabilityType.STANDARD) is True
        assert U.validate_capability_offset(0x50, CapabilityType.STANDARD) is True
        # Standard out of range
        assert U.validate_capability_offset(0x3F, CapabilityType.STANDARD) is False
        assert U.validate_capability_offset(0x100, CapabilityType.STANDARD) is False

        # Extended valid (aligned)
        assert U.validate_capability_offset(0x100, CapabilityType.EXTENDED) is True
        assert U.validate_capability_offset(0x104, CapabilityType.EXTENDED) is True
        # Extended invalid (misaligned or out of range)
        assert U.validate_capability_offset(0x102, CapabilityType.EXTENDED) is False
        assert U.validate_capability_offset(0x1000, CapabilityType.EXTENDED) is False

    def test_format_capability_info(self):
        std = CapabilityInfo(
            offset=0x50,
            cap_id=PCICapabilityID.MSI.value,
            cap_type=CapabilityType.STANDARD,
            next_ptr=0x60,
            name="MSI",
        )
        ext = CapabilityInfo(
            offset=0x100,
            cap_id=PCIExtCapabilityID.RESIZABLE_BAR.value,
            cap_type=CapabilityType.EXTENDED,
            next_ptr=0,
            name="Resizable BAR",
            version=1,
        )
        s = U.format_capability_info(std)
        assert "Standard Cap @ 0x50" in s and "ID: 0x05" in s
        e = U.format_capability_info(ext)
        assert "Extended Cap @ 0x100" in e and "ID: 0x0015" in e and "Ver: 1" in e


class TestSizeEstimates:
    def _std(self, cap_id: int) -> CapabilityInfo:
        return CapabilityInfo(0x40, cap_id, CapabilityType.STANDARD, 0, "")

    def _ext(self, cap_id: int) -> CapabilityInfo:
        return CapabilityInfo(0x100, cap_id, CapabilityType.EXTENDED, 0, "", 1)

    def test_standard_sizes(self):
        assert (
            U.get_capability_size_estimate(
                self._std(PCICapabilityID.POWER_MANAGEMENT.value)
            )
            == STD_CAP_SIZE_POWER_MANAGEMENT
        )
        assert (
            U.get_capability_size_estimate(self._std(PCICapabilityID.MSI.value))
            == STD_CAP_SIZE_MSI
        )
        assert (
            U.get_capability_size_estimate(self._std(PCICapabilityID.MSI_X.value))
            == STD_CAP_SIZE_MSI_X
        )
        assert (
            U.get_capability_size_estimate(
                self._std(PCICapabilityID.PCI_EXPRESS.value)
            )
            == STD_CAP_SIZE_PCI_EXPRESS
        )
        # Unknown standard -> default
        assert (
            U.get_capability_size_estimate(self._std(0x99))
            == STD_CAP_SIZE_DEFAULT
        )

    def test_extended_sizes(self):
        assert (
            U.get_capability_size_estimate(
                self._ext(PCIExtCapabilityID.ADVANCED_ERROR_REPORTING.value)
            )
            == EXT_CAP_SIZE_ADVANCED_ERROR_REPORTING
        )
        assert (
            U.get_capability_size_estimate(
                self._ext(PCIExtCapabilityID.ACCESS_CONTROL_SERVICES.value)
            )
            == EXT_CAP_SIZE_ACCESS_CONTROL_SERVICES
        )
        assert (
            U.get_capability_size_estimate(
                self._ext(PCIExtCapabilityID.DOWNSTREAM_PORT_CONTAINMENT.value)
            )
            == EXT_CAP_SIZE_DOWNSTREAM_PORT_CONTAINMENT
        )
        assert (
            U.get_capability_size_estimate(
                self._ext(PCIExtCapabilityID.RESIZABLE_BAR.value)
            )
            == EXT_CAP_SIZE_RESIZABLE_BAR
        )
        # Unknown extended -> default
        assert (
            U.get_capability_size_estimate(self._ext(0x00FE))
            == EXT_CAP_SIZE_DEFAULT
        )
