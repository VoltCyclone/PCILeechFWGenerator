#!/usr/bin/env python3
"""
MSI-X Capability Handler

This module provides MSI-X specific operations that integrate with the existing
MSI-X capability functionality while leveraging the new ConfigSpace and
CapabilityWalker infrastructure. It maintains compatibility with existing
MSI-X functionality and provides enhanced categorization through the rule engine.
"""

import logging
from typing import Any, Dict, List, Optional, Tuple

from ..string_utils import (log_debug_safe, log_error_safe, log_info_safe,
                            log_warning_safe, safe_format)
from .constants import (MSIX_BIR_MASK, MSIX_CAPABILITY_SIZE, MSIX_ENABLE_BIT,
                        MSIX_FUNCTION_MASK_BIT, MSIX_LARGE_TABLE_THRESHOLD,
                        MSIX_MAX_BIR, MSIX_MAX_TABLE_SIZE,
                        MSIX_MESSAGE_CONTROL_OFFSET, MSIX_MIN_TABLE_SIZE,
                        MSIX_OFFSET_ALIGNMENT, MSIX_OFFSET_MASK,
                        MSIX_PBA_OFFSET_BIR_OFFSET,
                        MSIX_TABLE_OFFSET_BIR_OFFSET, MSIX_TABLE_SIZE_MASK,
                        PCI_CAP_ID_OFFSET, PCI_CAP_NEXT_PTR_OFFSET)
from .core import CapabilityWalker, ConfigSpace
from .patches import BinaryPatch, PatchEngine
from .rules import RuleEngine
from .types import (CapabilityInfo, CapabilityType, EmulationCategory,
                    PCICapabilityID, PruningAction)

logger = logging.getLogger(__name__)


class MSIXCapabilityHandler:
    """
    Handler for MSI-X capability specific operations.

    This class provides specialized functionality for MSI-X capabilities,
    including integration with existing MSI-X parsing functionality,
    enhanced categorization through the rule engine, and efficient
    modification operations.
    """

    def __init__(
        self, config_space: ConfigSpace, rule_engine: Optional[RuleEngine] = None
    ) -> None:
        """
        Initialize MSI-X capability handler.

        Args:
            config_space: ConfigSpace instance to work with
            rule_engine: Optional RuleEngine for categorization
        """
        self.config_space = config_space
        self.rule_engine = rule_engine or RuleEngine()
        self.walker = CapabilityWalker(config_space)

    def find_msix_capabilities(self) -> List[CapabilityInfo]:
        """
        Find all MSI-X capabilities in the configuration space.

        Returns:
            List of CapabilityInfo objects for MSI-X capabilities
        """
        msix_capabilities = []

        for cap_info in self.walker.walk_standard_capabilities():
            if cap_info.cap_id == PCICapabilityID.MSI_X.value:
                msix_capabilities.append(cap_info)

        log_debug_safe(
            logger,
            "Found {count} MSI-X capabilities",
            prefix="PCI_CAP",
            count=len(msix_capabilities),
        )
        return msix_capabilities

    def get_msix_capability_info(self, offset: int) -> Optional[Dict[str, Any]]:
        """
        Get detailed information about an MSI-X capability.

        Args:
            offset: Offset of the MSI-X capability

        Returns:
            Dictionary with MSI-X capability details, or None if invalid
        """
        if not self.config_space.has_data(offset, MSIX_CAPABILITY_SIZE):
            log_warning_safe(
                logger,
                safe_format(
                    "MSI-X capability at offset 0x{offset:02x} is truncated",
                    offset=offset,
                ),
                prefix="PCI_CAP",
            )
            return None

        try:
            # Read MSI-X capability header
            cap_id = self.config_space.read_byte(offset + PCI_CAP_ID_OFFSET)
            next_ptr = self.config_space.read_byte(offset + PCI_CAP_NEXT_PTR_OFFSET)

            if cap_id != PCICapabilityID.MSI_X.value:
                log_warning_safe(
                    logger,
                    safe_format(
                        "Expected MSI-X capability (0x11) at offset 0x{offset:02x}, found 0x{cap_id:02x}",
                        offset=offset,
                        cap_id=cap_id,
                    ),
                    prefix="PCI_CAP",
                )
                return None

            # Read MSI-X Message Control register
            message_control = self.config_space.read_word(
                offset + MSIX_MESSAGE_CONTROL_OFFSET
            )

            # Extract fields from Message Control
            table_size = (
                message_control & MSIX_TABLE_SIZE_MASK
            ) + 1  # Add 1 for actual size
            function_mask = bool(message_control & MSIX_FUNCTION_MASK_BIT)
            msix_enable = bool(message_control & MSIX_ENABLE_BIT)

            # Read Table Offset/BIR register
            table_offset_bir = self.config_space.read_dword(
                offset + MSIX_TABLE_OFFSET_BIR_OFFSET
            )
            table_bir = table_offset_bir & MSIX_BIR_MASK
            table_offset = table_offset_bir & MSIX_OFFSET_MASK

            # Read PBA Offset/BIR register
            pba_offset_bir = self.config_space.read_dword(
                offset + MSIX_PBA_OFFSET_BIR_OFFSET
            )
            pba_bir = pba_offset_bir & MSIX_BIR_MASK
            pba_offset = pba_offset_bir & MSIX_OFFSET_MASK

            return {
                "offset": offset,
                "cap_id": cap_id,
                "next_ptr": next_ptr,
                "table_size": table_size,
                "function_mask": function_mask,
                "msix_enable": msix_enable,
                "table_bir": table_bir,
                "table_offset": table_offset,
                "pba_bir": pba_bir,
                "pba_offset": pba_offset,
                "message_control": message_control,
                "table_offset_bir": table_offset_bir,
                "pba_offset_bir": pba_offset_bir,
            }

        except (IndexError, ValueError) as e:
            log_error_safe(
                logger,
                safe_format(
                    "Failed to read MSI-X capability at offset 0x{offset:02x}: {e}",
                    offset=offset,
                    e=e,
                ),
                prefix="PCI_CAP",
            )
            return None

    def categorize_msix_capability(
        self, cap_info: CapabilityInfo, device_context: Optional[Dict[str, Any]] = None
    ) -> EmulationCategory:
        """
        Categorize an MSI-X capability using the rule engine.

        Args:
            cap_info: MSI-X capability information
            device_context: Optional device context for rule evaluation

        Returns:
            EmulationCategory for the MSI-X capability
        """
        return self.rule_engine.categorize_capability(
            cap_info, self.config_space, device_context
        )

    def create_msix_disable_patch(self, offset: int) -> Optional[BinaryPatch]:
        """
        Create a patch to disable an MSI-X capability.

        Args:
            offset: Offset of the MSI-X capability

        Returns:
            BinaryPatch to disable MSI-X, or None if failed
        """
        msix_info = self.get_msix_capability_info(offset)
        if not msix_info:
            return None

        # Create patch to clear MSI-X Enable bit in Message Control
        message_control = msix_info["message_control"]
        new_message_control = message_control & ~MSIX_ENABLE_BIT  # Clear enable bit

        if message_control == new_message_control:
            log_debug_safe(
                logger,
                "MSI-X at offset 0x{offset:02x} is already disabled",
                prefix="PCI_CAP",
                offset=offset,
            )
            return None

        patch = BinaryPatch(
            offset + MSIX_MESSAGE_CONTROL_OFFSET,  # Message Control register offset
            message_control.to_bytes(2, "little"),
            new_message_control.to_bytes(2, "little"),
            safe_format(
                "Disable MSI-X at offset 0x{offset:02x}",
                offset=offset,
            ),
        )

        return patch

    def create_msix_table_size_patch(
        self, offset: int, new_table_size: int
    ) -> Optional[BinaryPatch]:
        """
        Create a patch to modify MSI-X table size.

        Args:
            offset: Offset of the MSI-X capability
            new_table_size: New table size (1-2048)

        Returns:
            BinaryPatch to modify table size, or None if failed
        """
        if not (MSIX_MIN_TABLE_SIZE <= new_table_size <= MSIX_MAX_TABLE_SIZE):
            log_error_safe(
                logger,
                "Invalid MSI-X table size: {new_table_size} (must be {min}-{max})",
                prefix="PCI_CAP",
                new_table_size=new_table_size,
                min=MSIX_MIN_TABLE_SIZE,
                max=MSIX_MAX_TABLE_SIZE,
            )
            return None

        msix_info = self.get_msix_capability_info(offset)
        if not msix_info:
            return None

        # Calculate new Message Control value
        message_control = msix_info["message_control"]
        # Clear table size bits and set new size (subtract 1 for encoding)
        new_message_control = (message_control & ~MSIX_TABLE_SIZE_MASK) | (
            (new_table_size - 1) & MSIX_TABLE_SIZE_MASK
        )

        if message_control == new_message_control:
            log_debug_safe(
                logger,
                safe_format(
                    "MSI-X table size at offset 0x{offset:02x} is already {new_table_size}",
                    offset=offset,
                    new_table_size=new_table_size,
                ),
                prefix="PCI_CAP",
            )
            return None

        patch = BinaryPatch(
            offset + MSIX_MESSAGE_CONTROL_OFFSET,  # Message Control register offset
            message_control.to_bytes(2, "little"),
            new_message_control.to_bytes(2, "little"),
            safe_format(
                "Set MSI-X table size to {new_table_size} at offset 0x{offset:02x}",
                new_table_size=new_table_size,
                offset=offset,
            ),
        )

        return patch

    def create_msix_removal_patches(self, offset: int) -> List[BinaryPatch]:
        """
        Create patches to remove an MSI-X capability from the capability list.

        Args:
            offset: Offset of the MSI-X capability to remove

        Returns:
            List of BinaryPatch objects to remove the capability
        """
        patches = []

        msix_info = self.get_msix_capability_info(offset)
        if not msix_info:
            return patches

        next_ptr = msix_info["next_ptr"]

        # Find the capability that points to this MSI-X capability
        previous_cap_offset = self._find_previous_capability(offset)

        if previous_cap_offset is not None:
            # Read the current value from the previous capability's next pointer
            current_next_ptr = self.config_space.read_byte(
                previous_cap_offset + PCI_CAP_NEXT_PTR_OFFSET
            )

            # Verify we're actually pointing to the MSI-X capability we want to remove
            if current_next_ptr != offset:
                log_warning_safe(
                    logger,
                    safe_format(
                        "Previous capability at 0x{prev_offset:02x} points to 0x{current:02x}, not MSI-X at 0x{offset:02x}",
                        prev_offset=previous_cap_offset,
                        current=current_next_ptr,
                        offset=offset,
                    ),
                    prefix="PCI_CAP",
                )
                return patches  # Don't create invalid patches

            # Update the previous capability's next pointer
            patch = BinaryPatch(
                previous_cap_offset + PCI_CAP_NEXT_PTR_OFFSET,
                bytes([current_next_ptr]),
                bytes([next_ptr]),  # New pointer value
                safe_format(
                    "Update capability chain to skip MSI-X at 0x{offset:02x}",
                    offset=offset,
                ),
            )
            patches.append(patch)
        else:
            # This is the first capability, update the capabilities pointer
            from .constants import PCI_CAPABILITIES_POINTER

            # Read the current capabilities pointer
            current_cap_ptr = self.config_space.read_byte(PCI_CAPABILITIES_POINTER)

            # Verify it points to our MSI-X capability
            if current_cap_ptr != offset:
                log_warning_safe(
                    logger,
                    safe_format(
                        "Capabilities pointer is 0x{current:02x}, not MSI-X at 0x{offset:02x}",
                        current=current_cap_ptr,
                        offset=offset,
                    ),
                    prefix="PCI_CAP",
                )
                return patches  # Don't create invalid patches

            patch = BinaryPatch(
                PCI_CAPABILITIES_POINTER,
                bytes([current_cap_ptr]),  # Current pointer value
                bytes([next_ptr]),  # New pointer value
                safe_format(
                    "Update capabilities pointer to skip MSI-X at 0x{offset:02x}",
                    offset=offset,
                ),
            )
            patches.append(patch)

        # Zero out the MSI-X capability structure
        if self.config_space.has_data(offset, MSIX_CAPABILITY_SIZE):
            current_data = bytes(
                self.config_space[offset : offset + MSIX_CAPABILITY_SIZE]
            )
            zero_data = bytes(MSIX_CAPABILITY_SIZE)  # All zeros

            patch = BinaryPatch(
                offset,
                current_data,
                zero_data,
                safe_format(
                    "Zero out MSI-X capability at 0x{offset:02x}",
                    offset=offset,
                ),
            )
            patches.append(patch)

        return patches

    def apply_msix_pruning(
        self,
        action: PruningAction,
        patch_engine: PatchEngine,
        device_context: Optional[Dict[str, Any]] = None,
    ) -> int:
        """
        Apply MSI-X specific pruning operations.

        Args:
            action: Pruning action to apply
            patch_engine: PatchEngine to add patches to
            device_context: Optional device context

        Returns:
            Number of patches created
        """
        msix_capabilities = self.find_msix_capabilities()
        patches_created = 0

        for cap_info in msix_capabilities:
            category = self.categorize_msix_capability(cap_info, device_context)

            if (
                action == PruningAction.REMOVE
                and category == EmulationCategory.UNSUPPORTED
            ):
                # Remove unsupported MSI-X capabilities
                patches = self.create_msix_removal_patches(cap_info.offset)
                for patch in patches:
                    if patch_engine.add_patch(patch):
                        patches_created += 1

            elif (
                action == PruningAction.MODIFY
                and category == EmulationCategory.PARTIALLY_SUPPORTED
            ):
                # Disable partially supported MSI-X capabilities
                patch = self.create_msix_disable_patch(cap_info.offset)
                if patch and patch_engine.add_patch(patch):
                    patches_created += 1

        log_info_safe(
            logger,
            safe_format(
                "Created {patches_created} MSI-X pruning patches for action {action.name}",
                patches_created=patches_created,
            ),
        )
        return patches_created

    def create_msix_enable_patch(self, offset: int) -> Optional[BinaryPatch]:
        """
        Create a patch to enable an MSI-X capability.

        Args:
            offset: Offset of the MSI-X capability

        Returns:
            BinaryPatch to enable MSI-X, or None if failed
        """
        msix_info = self.get_msix_capability_info(offset)
        if not msix_info:
            return None

        # Create patch to set MSI-X Enable bit in Message Control
        message_control = msix_info["message_control"]
        new_message_control = message_control | MSIX_ENABLE_BIT  # Set enable bit

        if message_control == new_message_control:
            log_debug_safe(
                logger,
                "MSI-X at offset 0x{offset:02x} is already enabled",
                prefix="PCI_CAP",
                offset=offset,
            )
            return None

        patch = BinaryPatch(
            offset + MSIX_MESSAGE_CONTROL_OFFSET,
            message_control.to_bytes(2, "little"),
            new_message_control.to_bytes(2, "little"),
            safe_format(
                "Enable MSI-X at offset 0x{offset:02x}",
                offset=offset,
            ),
        )

        return patch

    def create_atomic_msix_patches(
        self, operations: List[Tuple[str, int, Any]]
    ) -> List[BinaryPatch]:
        """
        Create multiple MSI-X patches atomically with validation.

        Args:
            operations: List of (operation, offset, args) tuples
                       Operations: 'disable', 'enable', 'set_table_size', 'remove'

        Returns:
            List of validated patches
        """
        patches = []

        # Validate all operations first
        for op_name, offset, args in operations:
            if not self.get_msix_capability_info(offset):
                log_error_safe(
                    logger,
                    "Invalid MSI-X capability at offset 0x{offset:02x} for operation {op}",
                    prefix="PCI_CAP",
                    offset=offset,
                    op=op_name,
                )
                return []  # Return empty list on validation failure

        # Create patches
        for op_name, offset, args in operations:
            patch = None

            if op_name == "disable":
                patch = self.create_msix_disable_patch(offset)
            elif op_name == "enable":
                patch = self.create_msix_enable_patch(offset)
            elif op_name == "set_table_size":
                patch = self.create_msix_table_size_patch(offset, args)
            elif op_name == "remove":
                patches.extend(self.create_msix_removal_patches(offset))
                continue
            else:
                log_error_safe(
                    logger,
                    "Unknown MSI-X operation: {op}",
                    prefix="PCI_CAP",
                    op=op_name,
                )
                continue

            if patch:
                patches.append(patch)

        return patches

    def get_msix_integration_info(self) -> Dict[str, Any]:
        """
        Get information for integration with existing MSI-X functionality.

        Returns:
            Dictionary with MSI-X integration information
        """
        msix_capabilities = self.find_msix_capabilities()
        integration_info = {
            "msix_count": len(msix_capabilities),
            "msix_offsets": [cap.offset for cap in msix_capabilities],
            "msix_details": [],
        }

        for cap_info in msix_capabilities:
            msix_info = self.get_msix_capability_info(cap_info.offset)
            if msix_info:
                # Add categorization information
                category = self.categorize_msix_capability(cap_info)
                msix_info["emulation_category"] = category.name
                integration_info["msix_details"].append(msix_info)

        return integration_info

    def check_msix_requirements(
        self, device_context: Optional[Dict[str, Any]] = None
    ) -> Dict[str, Any]:
        """
        Check MSI-X requirements and constraints for the device.

        Args:
            device_context: Optional device context for additional checks

        Returns:
            Dictionary with requirement analysis
        """
        msix_capabilities = self.find_msix_capabilities()
        requirements = {
            "has_msix": len(msix_capabilities) > 0,
            "msix_count": len(msix_capabilities),
            "total_vectors": 0,
            "issues": [],
            "recommendations": [],
        }

        total_vectors = 0

        for cap_info in msix_capabilities:
            msix_info = self.get_msix_capability_info(cap_info.offset)
            if msix_info:
                table_size = msix_info["table_size"]
                total_vectors += table_size

                # Then in the check_msix_requirements method:
                if table_size > MSIX_LARGE_TABLE_THRESHOLD:
                    requirements["issues"].append(
                        f"Large MSI-X table size ({table_size}) at offset 0x{cap_info.offset:02x}"
                    )

                if msix_info["table_bir"] == msix_info["pba_bir"]:
                    requirements["recommendations"].append(
                        f"MSI-X table and PBA share same BAR at offset 0x{cap_info.offset:02x}"
                    )

        requirements["total_vectors"] = total_vectors

        # Check device context requirements
        if device_context:
            required_vectors = device_context.get("required_msix_vectors", 0)
            if required_vectors > total_vectors:
                requirements["issues"].append(
                    f"Device requires {required_vectors} vectors but only {total_vectors} available"
                )

        return requirements

    def _find_previous_capability(self, target_offset: int) -> Optional[int]:
        """
        Find the capability that points to the target offset.

        Args:
            target_offset: Offset of the target capability

        Returns:
            Offset of the previous capability, or None if target is first
        """
        for cap_info in self.walker.walk_standard_capabilities():
            if cap_info.next_ptr == target_offset:
                return cap_info.offset

        return None

    def validate_msix_capability(self, offset: int) -> Tuple[bool, List[str]]:
        """
        Validate an MSI-X capability structure.

        Args:
            offset: Offset of the MSI-X capability

        Returns:
            Tuple of (is_valid, error_messages)
        """
        errors = []

        # Check basic structure
        if not self.config_space.has_data(offset, MSIX_CAPABILITY_SIZE):
            errors.append(
                safe_format(
                    "MSI-X capability at 0x{offset:02x} is truncated (need {size} bytes)",
                    offset=offset,
                    size=MSIX_CAPABILITY_SIZE,
                )
            )
            return False, errors

        msix_info = self.get_msix_capability_info(offset)
        if not msix_info:
            errors.append(
                safe_format(
                    "Failed to parse MSI-X capability at 0x{offset:02x}",
                    offset=offset,
                )
            )
            return False, errors

        # Validate table size
        table_size = msix_info["table_size"]
        if not (MSIX_MIN_TABLE_SIZE <= table_size <= MSIX_MAX_TABLE_SIZE):
            errors.append(
                safe_format(
                    "Invalid MSI-X table size: {table_size} (must be {min}-{max})",
                    table_size=table_size,
                    min=MSIX_MIN_TABLE_SIZE,
                    max=MSIX_MAX_TABLE_SIZE,
                )
            )

        # Validate BIR values
        table_bir = msix_info["table_bir"]
        pba_bir = msix_info["pba_bir"]
        if table_bir > MSIX_MAX_BIR:
            errors.append(
                safe_format(
                    "Invalid MSI-X table BIR: {table_bir} (max {max})",
                    table_bir=table_bir,
                    max=MSIX_MAX_BIR,
                )
            )
        if pba_bir > MSIX_MAX_BIR:
            errors.append(
                safe_format(
                    "Invalid MSI-X PBA BIR: {pba_bir} (max {max})",
                    pba_bir=pba_bir,
                    max=MSIX_MAX_BIR,
                )
            )

        # Validate alignment
        table_offset = msix_info["table_offset"]
        pba_offset = msix_info["pba_offset"]
        if table_offset & (MSIX_OFFSET_ALIGNMENT - 1):
            errors.append(
                safe_format(
                    "MSI-X table offset 0x{table_offset:08x} is not {alignment}-byte aligned",
                    table_offset=table_offset,
                    alignment=MSIX_OFFSET_ALIGNMENT,
                )
            )
        if pba_offset & (MSIX_OFFSET_ALIGNMENT - 1):
            errors.append(
                safe_format(
                    "MSI-X PBA offset 0x{pba_offset:08x} is not {alignment}-byte aligned",
                    pba_offset=pba_offset,
                    alignment=MSIX_OFFSET_ALIGNMENT,
                )
            )

        is_valid = len(errors) == 0
        return is_valid, errors

    def __repr__(self) -> str:
        msix_count = len(self.find_msix_capabilities())
        return safe_format(
            "MSIXCapabilityHandler(config_space_size={size}, msix_count={count})",
            size=len(self.config_space),
            count=msix_count,
        )
