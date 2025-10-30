#!/usr/bin/env python3
"""
PCI Capability Pruning Implementation

This module contains the internal implementation for capability pruning
operations, used by the compatibility layer.
"""

import logging
from typing import Dict, List

from .constants import (ACS_CONTROL_REGISTER_OFFSET,
                        DPC_CONTROL_REGISTER_OFFSET, PCI_CAPABILITIES_POINTER,
                        PCI_EXT_CONFIG_SPACE_END,
                        PCIE_CAP_DEVICE_CONTROL2_OFFSET,
                        PCIE_CAP_LINK_CONTROL_OFFSET,
                        PCIE_DEVICE_CONTROL2_OBFF_LTR_MASK,
                        PCIE_LINK_CONTROL_ASPM_MASK,
                        PM_CAP_CAPABILITIES_OFFSET, PM_CAP_D3HOT_SUPPORT,
                        RBAR_CAPABILITY_REGISTER_OFFSET,
                        RBAR_SIZE_MASK_ABOVE_128MB,
                        TWO_BYTE_HEADER_CAPABILITIES)

from .core import CapabilityWalker, ConfigSpace

from .types import (CapabilityInfo, CapabilityType, PatchInfo, PCICapabilityID,
                    PCIExtCapabilityID, PruningAction)

logger = logging.getLogger(__name__)


def apply_pruning_actions(
    config_space: ConfigSpace, actions: Dict[int, PruningAction]
) -> None:
    """
    Apply pruning actions to the configuration space.

    Args:
        config_space: ConfigSpace instance to modify
        actions: Dictionary mapping capability offsets to pruning actions
    """
    walker = CapabilityWalker(config_space)
    all_caps = walker.get_all_capabilities()

    # Separate standard and extended capabilities
    std_caps = {
        offset: cap
        for offset, cap in all_caps.items()
        if cap.cap_type == CapabilityType.STANDARD
    }
    ext_caps = {
        offset: cap
        for offset, cap in all_caps.items()
        if cap.cap_type == CapabilityType.EXTENDED
    }

    # Process standard capabilities
    _apply_standard_capability_actions(config_space, std_caps, actions)

    # Process extended capabilities
    _apply_extended_capability_actions(config_space, ext_caps, actions)


def _apply_standard_capability_actions(
    config_space: ConfigSpace,
    std_caps: Dict[int, CapabilityInfo],
    actions: Dict[int, PruningAction],
) -> None:
    """Apply actions to standard capabilities."""
    std_cap_offsets = sorted(std_caps.keys())

    for i, offset in enumerate(std_cap_offsets):
        action = actions.get(offset, PruningAction.KEEP)
        cap = std_caps[offset]

        if action == PruningAction.REMOVE:
            if i > 0:
                prev_offset = std_cap_offsets[i - 1]
                next_ptr = cap.next_ptr

                # Detect header size for previous capability
                prev_cap = std_caps[prev_offset]
                header_offset_adj = (
                    2 if prev_cap.cap_id in TWO_BYTE_HEADER_CAPABILITIES else 1
                )

                # Update the next pointer of the previous capability
                ptr_offset = prev_offset + header_offset_adj
                config_space.write_byte(ptr_offset, next_ptr)
            else:
                # This is the first capability, update the capabilities pointer at 0x34
                next_ptr = cap.next_ptr
                config_space.write_byte(PCI_CAPABILITIES_POINTER, next_ptr)

                # Zero the original 2-byte header of the first cap
                config_space.write_byte(offset, 0)
                config_space.write_byte(offset + 1, 0)

        elif action == PruningAction.MODIFY:
            _modify_standard_capability(config_space, cap)


def _apply_extended_capability_actions(
    config_space: ConfigSpace,
    ext_caps: Dict[int, CapabilityInfo],
    actions: Dict[int, PruningAction],
) -> None:
    """Apply actions to extended capabilities."""
    ext_cap_offsets = sorted(ext_caps.keys())

    # First pass: Identify capabilities to remove and build a new chain
    new_chain = []
    for offset in ext_cap_offsets:
        action = actions.get(offset, PruningAction.KEEP)
        if action != PruningAction.REMOVE:
            new_chain.append(offset)

    # Second pass: Update the capability chain
    if new_chain:
        # Update the chain pointers
        for i, offset in enumerate(new_chain):
            cap = ext_caps[offset]

            # Set the next pointer
            next_ptr = 0  # Default to end of list
            if i < len(new_chain) - 1:
                next_ptr = new_chain[i + 1]

            # Reconstruct the header with the new next pointer
            new_header = (next_ptr << 20) | (cap.version << 16) | cap.cap_id
            config_space.write_dword(offset, new_header)

    # Third pass: Zero out removed capabilities and apply modifications
    for offset in ext_cap_offsets:
        action = actions.get(offset, PruningAction.KEEP)
        cap = ext_caps[offset]

        if action == PruningAction.REMOVE:
            # Find the range to zero - from this cap to the next cap or end of extended space
            next_cap_offset = (
                PCI_EXT_CONFIG_SPACE_END  # Default to end of extended config space
            )
            for other_offset in ext_cap_offsets:
                if other_offset > offset:
                    next_cap_offset = other_offset
                    break

            # Zero out from header through the byte before the next capability
            zero_len = next_cap_offset - offset
            for i in range(zero_len):
                if offset + i < len(config_space):
                    config_space.write_byte(offset + i, 0)

        elif action == PruningAction.MODIFY:
            _modify_extended_capability(config_space, cap)


def _modify_standard_capability(config_space: ConfigSpace, cap: CapabilityInfo) -> None:
    """Modify a standard capability based on its type."""
    if cap.cap_id == PCICapabilityID.POWER_MANAGEMENT.value:
        # Modify Power Management Capability
        # Keep only D0 and D3hot support, clear PME support
        pm_cap_offset = cap.offset + PM_CAP_CAPABILITIES_OFFSET
        if config_space.has_data(pm_cap_offset, 2):
            # Set only D3hot support (bit 3)
            config_space.write_word(pm_cap_offset, PM_CAP_D3HOT_SUPPORT)

    elif cap.cap_id == PCICapabilityID.PCI_EXPRESS.value:
        # Modify PCI Express Capability
        # Clear ASPM support in Link Control register
        link_control_offset = cap.offset + PCIE_CAP_LINK_CONTROL_OFFSET
        if config_space.has_data(link_control_offset, 2):
            link_control = config_space.read_word(link_control_offset)
            # Clear ASPM bits (bits 0-1)
            link_control &= ~PCIE_LINK_CONTROL_ASPM_MASK
            config_space.write_word(link_control_offset, link_control)

        # Clear OBFF and LTR bits in Device Control 2 register
        dev_control2_offset = cap.offset + PCIE_CAP_DEVICE_CONTROL2_OFFSET
        if config_space.has_data(dev_control2_offset, 2):
            dev_control2 = config_space.read_word(dev_control2_offset)
            # Clear OBFF Enable (bits 13-14) and LTR Enable (bit 10)
            dev_control2 &= ~PCIE_DEVICE_CONTROL2_OBFF_LTR_MASK
            config_space.write_word(dev_control2_offset, dev_control2)


def _modify_extended_capability(config_space: ConfigSpace, cap: CapabilityInfo) -> None:
    """Modify an extended capability based on its type."""
    if cap.cap_id == PCIExtCapabilityID.ACCESS_CONTROL_SERVICES.value:
        # ACS - keep header + control regs, zero feature bits
        control_offset = cap.offset + ACS_CONTROL_REGISTER_OFFSET
        if config_space.has_data(control_offset, 2):
            # Clear feature bits but keep control structure
            config_space.write_word(control_offset, 0)

    elif cap.cap_id == PCIExtCapabilityID.DOWNSTREAM_PORT_CONTAINMENT.value:
        # DPC - similar to ACS
        control_offset = cap.offset + DPC_CONTROL_REGISTER_OFFSET
        if config_space.has_data(control_offset, 2):
            config_space.write_word(control_offset, 0)

    elif cap.cap_id == PCIExtCapabilityID.RESIZABLE_BAR.value:
        # Resizable BAR - clamp size bits to 128 MB and below
        cap_reg_offset = cap.offset + RBAR_CAPABILITY_REGISTER_OFFSET
        if config_space.has_data(cap_reg_offset, 4):
            current_val = config_space.read_dword(cap_reg_offset)
            # Clear size bits above 128MB (bit 27 and above)
            clamped_val = current_val & RBAR_SIZE_MASK_ABOVE_128MB
            config_space.write_dword(cap_reg_offset, clamped_val)


def generate_capability_patches(
    config_space: ConfigSpace, actions: Dict[int, PruningAction]
) -> List[PatchInfo]:
    """
    Generate a list of patches for capability modifications.

    Args:
        config_space: ConfigSpace instance
        actions: Dictionary mapping capability offsets to pruning actions

    Returns:
        List of PatchInfo objects describing the changes
    """
    patches = []
    walker = CapabilityWalker(config_space)
    all_caps = walker.get_all_capabilities()

    # Separate standard and extended capabilities
    std_caps = {
        offset: cap
        for offset, cap in all_caps.items()
        if cap.cap_type == CapabilityType.STANDARD
    }
    ext_caps = {
        offset: cap
        for offset, cap in all_caps.items()
        if cap.cap_type == CapabilityType.EXTENDED
    }

    # Generate patches for standard capabilities
    patches.extend(
        _generate_standard_capability_patches(config_space, std_caps, actions)
    )

    # Generate patches for extended capabilities
    patches.extend(
        _generate_extended_capability_patches(config_space, ext_caps, actions)
    )

    return patches


def _generate_standard_capability_patches(
    config_space: ConfigSpace,
    std_caps: Dict[int, CapabilityInfo],
    actions: Dict[int, PruningAction],
) -> List[PatchInfo]:
    """Generate patches for standard capabilities."""
    patches = []
    std_cap_offsets = sorted(std_caps.keys())

    for i, offset in enumerate(std_cap_offsets):
        action = actions.get(offset, PruningAction.KEEP)
        cap = std_caps[offset]

        if action == PruningAction.REMOVE:
            # Record the removal patch
            before_bytes = f"{config_space.read_byte(offset):02x}{config_space.read_byte(offset + 1):02x}"
            patches.append(PatchInfo(offset, "REMOVE_STD_CAP", before_bytes, "0000"))

            # Update pointer chain
            if i > 0:
                prev_offset = std_cap_offsets[i - 1]
                next_ptr = cap.next_ptr

                # Detect header size for previous capability
                prev_cap = std_caps[prev_offset]
                header_offset_adj = (
                    2 if prev_cap.cap_id in TWO_BYTE_HEADER_CAPABILITIES else 1
                )

                ptr_offset = prev_offset + header_offset_adj
                before_bytes = f"{config_space.read_byte(ptr_offset):02x}"
                patches.append(
                    PatchInfo(
                        ptr_offset, "UPDATE_STD_PTR", before_bytes, f"{next_ptr:02x}"
                    )
                )
            else:
                # Update capabilities pointer at 0x34
                next_ptr = cap.next_ptr
                before_bytes = f"{config_space.read_byte(PCI_CAPABILITIES_POINTER):02x}"
                patches.append(
                    PatchInfo(
                        PCI_CAPABILITIES_POINTER,
                        "UPDATE_CAP_PTR",
                        before_bytes,
                        f"{next_ptr:02x}",
                    )
                )

        elif action == PruningAction.MODIFY:
            # Record modification patches based on capability type
            if cap.cap_id == PCICapabilityID.POWER_MANAGEMENT.value:
                pm_cap_offset = cap.offset + PM_CAP_CAPABILITIES_OFFSET
                before_bytes = f"{config_space.read_word(pm_cap_offset):04x}"
                patches.append(
                    PatchInfo(
                        pm_cap_offset,
                        "MODIFY_PM_CAP",
                        before_bytes,
                        f"{PM_CAP_D3HOT_SUPPORT:04x}",
                    )
                )

    return patches


def _generate_extended_capability_patches(
    config_space: ConfigSpace,
    ext_caps: Dict[int, CapabilityInfo],
    actions: Dict[int, PruningAction],
) -> List[PatchInfo]:
    """Generate patches for extended capabilities."""
    patches = []
    ext_cap_offsets = sorted(ext_caps.keys())

    for offset in ext_cap_offsets:
        action = actions.get(offset, PruningAction.KEEP)
        cap = ext_caps[offset]

        if action == PruningAction.REMOVE:
            # Find the range to zero
            next_cap_offset = (
                PCI_EXT_CONFIG_SPACE_END  # Default to end of extended config space
            )
            for other_offset in ext_cap_offsets:
                if other_offset > offset:
                    next_cap_offset = other_offset
                    break

            zero_len = min(next_cap_offset - offset, len(config_space) - offset)
            before_bytes = ""
            for i in range(zero_len):
                if offset + i < len(config_space):
                    before_bytes += f"{config_space.read_byte(offset + i):02x}"
            after_bytes = "00" * zero_len
            patches.append(
                PatchInfo(offset, "REMOVE_EXT_CAP", before_bytes, after_bytes)
            )

        elif action == PruningAction.MODIFY:
            if cap.cap_id == PCIExtCapabilityID.ACCESS_CONTROL_SERVICES.value:
                # ACS - keep header + control regs, zero feature bits
                control_offset = cap.offset + ACS_CONTROL_REGISTER_OFFSET
                before_bytes = f"{config_space.read_word(control_offset):04x}"
                patches.append(
                    PatchInfo(control_offset, "MODIFY_ACS", before_bytes, "0000")
                )

            elif cap.cap_id == PCIExtCapabilityID.DOWNSTREAM_PORT_CONTAINMENT.value:
                # DPC - similar to ACS
                control_offset = cap.offset + DPC_CONTROL_REGISTER_OFFSET
                before_bytes = f"{config_space.read_word(control_offset):04x}"
                patches.append(
                    PatchInfo(control_offset, "MODIFY_DPC", before_bytes, "0000")
                )

            elif cap.cap_id == PCIExtCapabilityID.RESIZABLE_BAR.value:
                # Resizable BAR - clamp size bits to 128 MB and below
                cap_offset = cap.offset + RBAR_CAPABILITY_REGISTER_OFFSET
                if config_space.has_data(cap_offset, 4):
                    before_val = config_space.read_dword(cap_offset)
                    before_bytes = f"{before_val:08x}"
                    # Clear size bits above 128MB (bit 27 and above)
                    clamped_val = before_val & RBAR_SIZE_MASK_ABOVE_128MB
                    after_bytes = f"{clamped_val:08x}"
                    patches.append(
                        PatchInfo(cap_offset, "MODIFY_RBAR", before_bytes, after_bytes)
                    )

    return patches
