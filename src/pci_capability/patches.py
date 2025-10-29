#!/usr/bin/env python3
"""
PCI Capability Patch Engine

This module provides efficient binary patch operations for PCI configuration
space modifications. It supports batch operations, validation to prevent
configuration space corruption, and integration with the new PatchInfo
binary format.
"""

import logging

from typing import Dict, List, Optional, Tuple

from ..string_utils import (log_debug_safe, log_error_safe, log_info_safe,
                            log_warning_safe, safe_format)

from .core import ConfigSpace

from .types import PatchInfo

logger = logging.getLogger(__name__)


class BinaryPatch:
    """
    Efficient representation of a binary patch operation.

    A BinaryPatch represents a single modification to configuration space,
    including the offset, original data, and new data. It provides validation
    and rollback capabilities.
    """

    def __init__(
        self,
        offset: int,
        original_data: bytes,
        new_data: bytes,
        description: Optional[str] = None,
    ) -> None:
        """
        Initialize a binary patch.

        Args:
            offset: Byte offset in configuration space
            original_data: Original bytes at the offset
            new_data: New bytes to write at the offset
            description: Human-readable description of the patch

        Raises:
            ValueError: If data lengths don't match or offset is invalid
        """
        if len(original_data) != len(new_data):
            raise ValueError(
                safe_format(
                    "Original data length {orig_len} doesn't match new data length {new_len}",
                    orig_len=len(original_data),
                    new_len=len(new_data),
                )
            )

        if offset < 0:
            raise ValueError(
                safe_format(
                    "Invalid offset: {offset}",
                    offset=offset,
                )
            )

        self.offset = offset
        self.original_data = original_data
        self.new_data = new_data
        self.description = description or safe_format(
            "Patch at offset 0x{offset:02x}",
            offset=offset,
        )
        self.applied = False

    @property
    def size(self) -> int:
        """Get the size of the patch in bytes."""
        return len(self.new_data)

    @property
    def end_offset(self) -> int:
        """Get the end offset of the patch."""
        return self.offset + self.size

    def overlaps_with(self, other: "BinaryPatch") -> bool:
        """
        Check if this patch overlaps with another patch.

        Args:
            other: Another BinaryPatch to check against

        Returns:
            True if the patches overlap, False otherwise
        """
        return not (self.end_offset <= other.offset or other.end_offset <= self.offset)

    def can_apply_to(self, config_space: ConfigSpace) -> bool:
        """
        Check if this patch can be applied to the given configuration space.

        Args:
            config_space: ConfigSpace to check

        Returns:
            True if the patch can be applied, False otherwise
        """
        # Check bounds
        if not config_space.has_data(self.offset, self.size):
            return False

        # Check that current data matches expected original data
        try:
            current_data = bytes(config_space[self.offset : self.offset + self.size])
            return current_data == self.original_data
        except (IndexError, ValueError):
            return False

    def apply_to(self, config_space: ConfigSpace) -> bool:
        """
        Apply this patch to the configuration space.

        Args:
            config_space: ConfigSpace to modify

        Returns:
            True if the patch was applied successfully, False otherwise
        """
        if not self.can_apply_to(config_space):
            log_warning_safe(
                logger,
                safe_format(
                    "Cannot apply patch: {self.description}",
                    self=self,
                ),
                prefix="PCI_CAP",
            )
            return False

        try:
            # Apply the patch
            for i, byte_value in enumerate(self.new_data):
                config_space[self.offset + i] = byte_value

            self.applied = True
            log_debug_safe(
                logger,
                safe_format(
                    "Applied patch: {description}",
                    description=self.description,
                ),
                prefix="PCI_CAP",
            )
            return True

        except (IndexError, ValueError) as e:
            log_error_safe(
                logger,
                safe_format(
                    "Failed to apply patch {self.description}: {e}",
                    self=self,
                    e=e,
                ),
                prefix="PCI_CAP",
            )
            return False

    def rollback_from(self, config_space: ConfigSpace) -> bool:
        """
        Rollback this patch from the configuration space.

        Args:
            config_space: ConfigSpace to modify

        Returns:
            True if the patch was rolled back successfully, False otherwise
        """
        if not self.applied:
            log_warning_safe(
                logger,
                safe_format(
                    "Patch not applied, cannot rollback: {self.description}",
                    self=self,
                ),
                prefix="PCI_CAP",
            )
            return False

        if not config_space.has_data(self.offset, self.size):
            log_error_safe(
                logger,
                safe_format(
                    "Cannot rollback patch, invalid bounds: {self.description}",
                    self=self,
                ),
                prefix="PCI_CAP",
            )
            return False

        try:
            # Rollback the patch
            for i, byte_value in enumerate(self.original_data):
                config_space[self.offset + i] = byte_value

            self.applied = False
            log_debug_safe(
                logger,
                safe_format(
                    "Rolled back patch: {self.description}",
                    self=self,
                ),
                prefix="PCI_CAP",
            )
            return True

        except (IndexError, ValueError) as e:
            log_error_safe(
                logger,
                safe_format(
                    "Failed to rollback patch {self.description}: {e}",
                    self=self,
                    e=e,
                ),
                prefix="PCI_CAP",
            )
            return False

    def to_patch_info(self, action: str) -> PatchInfo:
        """
        Convert this BinaryPatch to a PatchInfo object.

        Args:
            action: Action description for the patch

        Returns:
            PatchInfo object representing this patch
        """
        return PatchInfo(
            offset=self.offset,
            action=action,
            before_bytes=self.original_data.hex(),
            after_bytes=self.new_data.hex(),
        )

    def __repr__(self) -> str:
        return safe_format(
            "BinaryPatch(offset=0x{offset:02x}, size={size}, applied={applied})",
            offset=self.offset,
            size=self.size,
            applied=self.applied,
        )


class PatchEngine:
    """
    Engine for applying multiple binary patches efficiently.

    The PatchEngine manages collections of BinaryPatch objects and provides
    methods for batch operations, validation, and rollback. It ensures that
    patches don't conflict and that configuration space integrity is maintained.
    """

    def __init__(self) -> None:
        """Initialize the patch engine."""
        self.patches: List[BinaryPatch] = []
        self.applied_patches: List[BinaryPatch] = []

    def add_patch(self, patch: BinaryPatch) -> bool:
        """
        Add a patch to the engine.

        Args:
            patch: BinaryPatch to add

        Returns:
            True if the patch was added successfully, False if it conflicts
        """
        # Check for conflicts with existing patches
        for existing_patch in self.patches:
            if patch.overlaps_with(existing_patch):
                # Allow identical duplicate patches (same offset, size, and data)
                if (
                    patch.offset == existing_patch.offset
                    and patch.size == existing_patch.size
                    and patch.original_data == existing_patch.original_data
                    and patch.new_data == existing_patch.new_data
                ):
                    log_debug_safe(
                        logger,
                        safe_format(
                            "Suppressing duplicate patch: {patch}",
                            patch=patch,
                        ),
                        prefix="PCI_CAP",
                    )
                    return True  # treat as success without adding duplicate

                log_warning_safe(
                    logger,
                    safe_format(
                        "Patch conflict: {new_patch} overlaps with {existing_patch}",
                        new_patch=patch,
                        existing_patch=existing_patch,
                    ),
                    prefix="PCI_CAP",
                )
                return False

        self.patches.append(patch)
        log_debug_safe(
            logger,
            safe_format(
                "Added patch: {description}",
                description=patch.description,
            ),
            prefix="PCI_CAP",
        )
        return True

    def create_patch(
        self,
        offset: int,
        original_data: bytes,
        new_data: bytes,
        description: Optional[str] = None,
    ) -> Optional[BinaryPatch]:
        """
        Create and add a patch to the engine.

        Args:
            offset: Byte offset in configuration space
            original_data: Original bytes at the offset
            new_data: New bytes to write at the offset
            description: Human-readable description of the patch

        Returns:
            BinaryPatch if created and added successfully, None otherwise
        """
        try:
            patch = BinaryPatch(offset, original_data, new_data, description)
            if self.add_patch(patch):
                return patch
            return None
        except ValueError as e:
            log_error_safe(
                logger,
                "Failed to create patch: {e}",
                prefix="PCI_CAP",
                e=e,
            )
            return None

    def create_byte_patch(
        self,
        offset: int,
        original_value: int,
        new_value: int,
        description: Optional[str] = None,
    ) -> Optional[BinaryPatch]:
        """
        Create a single-byte patch.

        Args:
            offset: Byte offset in configuration space
            original_value: Original byte value (0-255)
            new_value: New byte value (0-255)
            description: Human-readable description of the patch

        Returns:
            BinaryPatch if created successfully, None otherwise
        """
        if not (0 <= original_value <= 255) or not (0 <= new_value <= 255):
            log_error_safe(
                logger,
                safe_format(
                    "Invalid byte values: original={original_value}, new={new_value}",
                    original_value=original_value,
                    new_value=new_value,
                ),
                prefix="PCI_CAP",
            )
            return None

        return self.create_patch(
            offset,
            bytes([original_value]),
            bytes([new_value]),
            description
            or safe_format(
                "Byte patch at 0x{offset:02x}: 0x{original_value:02x} -> 0x{new_value:02x}",
                offset=offset,
                original_value=original_value,
                new_value=new_value,
            ),
        )

    def create_word_patch(
        self,
        offset: int,
        original_value: int,
        new_value: int,
        description: Optional[str] = None,
    ) -> Optional[BinaryPatch]:
        """
        Create a 16-bit word patch (little-endian).

        Args:
            offset: Byte offset in configuration space
            original_value: Original word value (0-65535)
            new_value: New word value (0-65535)
            description: Human-readable description of the patch

        Returns:
            BinaryPatch if created successfully, None otherwise
        """
        if not (0 <= original_value <= 0xFFFF) or not (0 <= new_value <= 0xFFFF):
            log_error_safe(
                logger,
                safe_format(
                    "Invalid word values: original={original_value}, new={new_value}",
                    original_value=original_value,
                    new_value=new_value,
                ),
                prefix="PCI_CAP",
            )
            return None

        return self.create_patch(
            offset,
            original_value.to_bytes(2, "little"),
            new_value.to_bytes(2, "little"),
            description
            or safe_format(
                "Word patch at 0x{offset:02x}: 0x{original_value:04x} -> 0x{new_value:04x}",
                offset=offset,
                original_value=original_value,
                new_value=new_value,
            ),
        )

    def create_dword_patch(
        self,
        offset: int,
        original_value: int,
        new_value: int,
        description: Optional[str] = None,
    ) -> Optional[BinaryPatch]:
        """
        Create a 32-bit dword patch (little-endian).

        Args:
            offset: Byte offset in configuration space
            original_value: Original dword value (0-4294967295)
            new_value: New dword value (0-4294967295)
            description: Human-readable description of the patch

        Returns:
            BinaryPatch if created successfully, None otherwise
        """
        if not (0 <= original_value <= 0xFFFFFFFF) or not (
            0 <= new_value <= 0xFFFFFFFF
        ):
            log_error_safe(
                logger,
                safe_format(
                    "Invalid dword values: original={original_value}, new={new_value}",
                    original_value=original_value,
                    new_value=new_value,
                ),
            )
            return None

        return self.create_patch(
            offset,
            original_value.to_bytes(4, "little"),
            new_value.to_bytes(4, "little"),
            description
            or safe_format(
                "Dword patch at 0x{offset:02x}: 0x{original_value:08x} -> 0x{new_value:08x}",
                offset=offset,
                original_value=original_value,
                new_value=new_value,
            ),
        )

    def validate_patches(
        self, config_space: ConfigSpace
    ) -> Tuple[List[BinaryPatch], List[str]]:
        """
        Validate all patches against the configuration space.

        Args:
            config_space: ConfigSpace to validate against

        Returns:
            Tuple of (valid_patches, error_messages)
        """
        valid_patches = []
        errors = []

        for patch in self.patches:
            if patch.can_apply_to(config_space):
                valid_patches.append(patch)
            else:
                error_msg = safe_format("Patch validation failed: {patch.description}")
                errors.append(error_msg)
                log_warning_safe(
                    logger,
                    error_msg,
                    prefix="PCI_CAP",
                )

        return valid_patches, errors

    def apply_all_patches(
        self, config_space: ConfigSpace, validate_first: bool = True
    ) -> Tuple[int, List[str]]:
        """
        Apply all patches to the configuration space.

        Args:
            config_space: ConfigSpace to modify
            validate_first: Whether to validate patches before applying

        Returns:
            Tuple of (patches_applied_count, error_messages)
        """
        if validate_first:
            valid_patches, validation_errors = self.validate_patches(config_space)
            if validation_errors:
                log_warning_safe(
                    logger,
                    "Validation found {len_validation_errors} errors",
                    prefix="PCI_CAP",
                    len_validation_errors=len(validation_errors),
                )
        else:
            valid_patches = self.patches
            validation_errors = []

        applied_count = 0
        errors = list(validation_errors)

        # Sort patches by offset for consistent application order
        sorted_patches = sorted(valid_patches, key=lambda p: p.offset)

        for patch in sorted_patches:
            if patch.apply_to(config_space):
                self.applied_patches.append(patch)
                applied_count += 1
            else:
                error_msg = safe_format("Failed to apply patch: {patch.description}")
                errors.append(error_msg)

        log_info_safe(
            logger,
            "Applied {applied_count} patches successfully",
            prefix="PCI_CAP",
            applied_count=applied_count,
        )
        if errors:
            log_warning_safe(
                logger,
                "Encountered {error_count} errors during patch application",
                prefix="PCI_CAP",
                error_count=len(errors),
            )

        return applied_count, errors

    def rollback_all_patches(self, config_space: ConfigSpace) -> Tuple[int, List[str]]:
        """
        Rollback all applied patches from the configuration space.

        Args:
            config_space: ConfigSpace to modify

        Returns:
            Tuple of (patches_rolled_back_count, error_messages)
        """
        rolled_back_count = 0
        errors = []

        # Rollback in reverse order
        for patch in reversed(self.applied_patches):
            if patch.rollback_from(config_space):
                rolled_back_count += 1
            else:
                error_msg = safe_format("Failed to rollback patch: {patch.description}")
                errors.append(error_msg)

        # Clear applied patches list
        self.applied_patches.clear()

        log_info_safe(
            logger,
            "Rolled back {rolled_back_count} patches successfully",
            prefix="PCI_CAP",
            rolled_back_count=rolled_back_count,
        )
        if errors:
            log_warning_safe(
                logger,
                "Encountered {error_count} errors during rollback",
                prefix="PCI_CAP",
                error_count=len(errors),
            )

        return rolled_back_count, errors

    def get_patch_info_list(self, action_prefix: str = "modify") -> List[PatchInfo]:
        """
        Get a list of PatchInfo objects for all patches.

        Args:
            action_prefix: Prefix for the action description

        Returns:
            List of PatchInfo objects
        """
        patch_infos = []

        for i, patch in enumerate(self.patches):
            action = safe_format(
                "{action_prefix}_{i:03d}",
                action_prefix=action_prefix,
                i=i,
            )
            patch_infos.append(patch.to_patch_info(action))

        return patch_infos

    def clear_patches(self) -> None:
        """Clear all patches from the engine."""
        self.patches.clear()
        self.applied_patches.clear()
        log_debug_safe(
            logger,
            "Cleared all patches from engine",
            prefix="PCI_CAP",
        )

    def get_coverage_map(self) -> Dict[int, BinaryPatch]:
        """
        Get a map of all bytes covered by patches.

        Returns:
            Dictionary mapping byte offsets to the patch that covers them
        """
        coverage = {}

        for patch in self.patches:
            for offset in range(patch.offset, patch.end_offset):
                coverage[offset] = patch

        return coverage

    def get_statistics(self) -> Dict[str, int]:
        """
        Get statistics about the patches in the engine.

        Returns:
            Dictionary with patch statistics
        """
        total_bytes = sum(patch.size for patch in self.patches)
        applied_count = len(self.applied_patches)

        return {
            "total_patches": len(self.patches),
            "applied_patches": applied_count,
            "pending_patches": len(self.patches) - applied_count,
            "total_bytes_modified": total_bytes,
        }

    def __len__(self) -> int:
        """Return the number of patches in the engine."""
        return len(self.patches)

    def __repr__(self) -> str:
        stats = self.get_statistics()
        return safe_format(
            "PatchEngine(patches={total}, applied={applied}, bytes={bytes})",
            total=stats["total_patches"],
            applied=stats["applied_patches"],
            bytes=stats["total_bytes_modified"],
        )
