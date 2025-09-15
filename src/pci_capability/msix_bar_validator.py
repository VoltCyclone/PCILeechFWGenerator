#!/usr/bin/env python3
"""
MSI-X and BAR Configuration Validator

This module provides utilities to validate MSI-X and BAR configurations
to prevent driver errors and hardware conflicts.
"""

import logging
from typing import Any, Dict, List, Optional, Tuple

from src.pci_capability.constants import (BAR_INDEX_MAX, BAR_INDEX_MIN,
                                          CACHELINE_OPTIMAL, DWORD_SIZE_BYTES,
                                          EXCESSIVE_VECTOR_THRESHOLD_LOWEND,
                                          INTEL_VECTOR_WARNING_THRESHOLD,
                                          LARGE_VECTOR_WARNING_THRESHOLD,
                                          LOW_END_DEVICE_ID_THRESHOLD,
                                          MSIX_TABLE_ENTRY_SIZE_BYTES,
                                          NON_STANDARD_BAR_MAX,
                                          OVERSIZED_BAR_FACTOR, PAGE_SIZE_4K,
                                          PBA_VECTORS_PER_DWORD,
                                          RESERVED_REGION_CONTROL_END,
                                          RESERVED_REGIONS)

MSIX_CAP_ID: int = 0x11  # Kept local to avoid circular dependency
MSIX_MIN_TABLE_ENTRIES: int = 1
MSIX_MAX_TABLE_ENTRIES: int = 2048
MSIX_OFFSET_MIN_ALIGNMENT: int = 8

decode_table_size = lambda raw: raw + 1
compute_table_bytes = lambda entries: entries * MSIX_TABLE_ENTRY_SIZE_BYTES
compute_pba_bytes = lambda entries: (
    ((entries + (PBA_VECTORS_PER_DWORD - 1)) // PBA_VECTORS_PER_DWORD)
    * DWORD_SIZE_BYTES
)

logger = logging.getLogger(__name__)


def validate_msix_bar_configuration(
    bars: List[Dict[str, Any]],
    capabilities: List[Dict[str, Any]],
    device_info: Optional[Dict[str, Any]] = None,
) -> Tuple[bool, List[str], List[str]]:
    """
    Comprehensive validation of MSI-X and BAR configuration.

    Args:
        bars: List of BAR configuration dictionaries
        capabilities: List of capability dictionaries
        device_info: Optional device information (vendor_id, device_id)

    Returns:
        Tuple of (is_valid, errors, warnings)
    """
    errors = []
    warnings = []

    if device_info is None:
        device_info = {"vendor_id": 0x0000, "device_id": 0x0000}

    # Find MSI-X capability
    msix_cap = None
    for cap in capabilities:
        if cap.get("cap_id") == MSIX_CAP_ID:
            msix_cap = cap
            break

    if not msix_cap:
        # No MSI-X capability, validate basic BAR configuration only
        _validate_basic_bar_configuration(bars, errors, warnings)
        return len(errors) == 0, errors, warnings

    # Validate MSI-X capability structure
    _validate_msix_capability_structure(msix_cap, errors, warnings)

    # Validate BAR configuration
    _validate_bar_configuration_for_msix(bars, msix_cap, errors, warnings)

    # Validate MSI-X memory layout
    _validate_msix_memory_layout(bars, msix_cap, errors, warnings)

    # Check for common driver compatibility issues
    _validate_driver_compatibility(bars, msix_cap, errors, warnings, device_info)

    # Performance and optimization warnings
    _validate_performance_considerations(bars, msix_cap, warnings, device_info)

    is_valid = len(errors) == 0
    return is_valid, errors, warnings


def _validate_msix_capability_structure(
    msix_cap: Dict[str, Any], errors: List[str], warnings: List[str]
) -> None:
    """Validate MSI-X capability structure fields."""
    # Validate table size
    table_size = decode_table_size(msix_cap.get("table_size", 0))
    if not (MSIX_MIN_TABLE_ENTRIES <= table_size <= MSIX_MAX_TABLE_ENTRIES):
        errors.append(
            (
                f"MSI-X table size {table_size} is invalid (must be "
                f"{MSIX_MIN_TABLE_ENTRIES}-{MSIX_MAX_TABLE_ENTRIES})"
            )
        )

    # Validate BIR values
    table_bar = msix_cap.get("table_bar", 0)
    pba_bar = msix_cap.get("pba_bar", 0)

    if table_bar > BAR_INDEX_MAX:
        errors.append(
            (
                f"MSI-X table BIR {table_bar} is invalid (must be "
                f"{BAR_INDEX_MIN}-{BAR_INDEX_MAX})"
            )
        )
    if pba_bar > BAR_INDEX_MAX:
        errors.append(
            (
                f"MSI-X PBA BIR {pba_bar} is invalid (must be "
                f"{BAR_INDEX_MIN}-{BAR_INDEX_MAX})"
            )
        )

    # Validate offset alignment (PCIe spec requires 8-byte alignment minimum)
    table_offset = msix_cap.get("table_offset", 0)
    pba_offset = msix_cap.get("pba_offset", 0)

    if table_offset % MSIX_OFFSET_MIN_ALIGNMENT != 0:
        errors.append(
            (
                f"MSI-X table offset 0x{table_offset:x} is not "
                f"{MSIX_OFFSET_MIN_ALIGNMENT}-byte aligned"
            )
        )
    if pba_offset % MSIX_OFFSET_MIN_ALIGNMENT != 0:
        errors.append(
            (
                f"MSI-X PBA offset 0x{pba_offset:x} is not "
                f"{MSIX_OFFSET_MIN_ALIGNMENT}-byte aligned"
            )
        )

    # Performance alignment warnings
    if table_offset % PAGE_SIZE_4K != 0:
        warnings.append(
            (
                f"MSI-X table offset 0x{table_offset:x} is not 4KB aligned "
                "(may impact DMA performance)"
            )
        )
    if pba_offset % PAGE_SIZE_4K != 0:
        warnings.append(
            (
                f"MSI-X PBA offset 0x{pba_offset:x} is not 4KB aligned "
                "(may impact DMA performance)"
            )
        )


def _validate_bar_configuration_for_msix(
    bars: List[Dict[str, Any]],
    msix_cap: Dict[str, Any],
    errors: List[str],
    warnings: List[str],
) -> None:
    """Validate BAR configuration for MSI-X compatibility."""
    table_bar = msix_cap.get("table_bar", 0)
    pba_bar = msix_cap.get("pba_bar", 0)

    # Find relevant BARs
    table_bar_config = None
    pba_bar_config = None

    for bar_config in bars:
        if bar_config.get("bar") == table_bar:
            table_bar_config = bar_config
        if bar_config.get("bar") == pba_bar:
            pba_bar_config = bar_config

    # Validate BAR existence
    if table_bar_config is None:
        errors.append(f"MSI-X table BAR {table_bar} is not configured")
    if pba_bar_config is None:
        errors.append(f"MSI-X PBA BAR {pba_bar} is not configured")

    # If either BAR is missing, we can't continue validation
    if table_bar_config is None or pba_bar_config is None:
        return

    # Validate BAR types
    if table_bar_config.get("type") != "memory":
        errors.append(
            (
                f"MSI-X table BAR {table_bar} must be memory type, got "
                f"{table_bar_config.get('type')}"
            )
        )
    if pba_bar_config.get("type") != "memory":
        errors.append(
            (
                f"MSI-X PBA BAR {pba_bar} must be memory type, got "
                f"{pba_bar_config.get('type')}"
            )
        )

    # Check if prefetchable (generally not recommended for MSI-X)
    if table_bar_config.get("prefetchable", False):
        warnings.append(
            (
                f"MSI-X table BAR {table_bar} is prefetchable (may cause issues "
                "with some drivers)"
            )
        )
    if pba_bar_config.get("prefetchable", False):
        warnings.append(
            (
                f"MSI-X PBA BAR {pba_bar} is prefetchable (may cause issues "
                "with some drivers)"
            )
        )


def _validate_msix_memory_layout(
    bars: List[Dict[str, Any]],
    msix_cap: Dict[str, Any],
    errors: List[str],
    warnings: List[str],
) -> None:
    """Validate MSI-X memory layout for conflicts and sizing."""
    table_bar = msix_cap.get("table_bar", 0)
    pba_bar = msix_cap.get("pba_bar", 0)
    table_offset = msix_cap.get("table_offset", 0)
    pba_offset = msix_cap.get("pba_offset", 0)
    table_size = decode_table_size(msix_cap.get("table_size", 0))

    # Calculate structure sizes
    table_size_bytes = compute_table_bytes(table_size)
    pba_size_bytes = compute_pba_bytes(table_size)

    # Find BAR configurations
    table_bar_config = None
    pba_bar_config = None

    for bar_config in bars:
        if bar_config.get("bar") == table_bar:
            table_bar_config = bar_config
        if bar_config.get("bar") == pba_bar:
            pba_bar_config = bar_config

    if not table_bar_config or not pba_bar_config:
        return  # Already handled in previous validation

    # Validate structures fit in BARs
    table_bar_size = table_bar_config.get("size", 0)
    table_end = table_offset + table_size_bytes

    if table_end > table_bar_size:
        errors.append(
            f"MSI-X table (offset 0x{table_offset:x}, size {table_size_bytes}) "
            f"exceeds BAR {table_bar} size (0x{table_bar_size:x})"
        )

    pba_bar_size = pba_bar_config.get("size", 0)
    pba_end = pba_offset + pba_size_bytes

    if pba_end > pba_bar_size:
        errors.append(
            f"MSI-X PBA (offset 0x{pba_offset:x}, size {pba_size_bytes}) "
            f"exceeds BAR {pba_bar} size (0x{pba_bar_size:x})"
        )

    # Check for overlap if same BAR
    if table_bar == pba_bar:
        if table_offset < pba_end and table_end > pba_offset:
            errors.append(
                (
                    f"MSI-X table (0x{table_offset:x}-0x{table_end:x}) and PBA "
                    f"(0x{pba_offset:x}-0x{pba_end:x}) overlap in BAR {table_bar}"
                )
            )

    # Check for conflicts with common reserved regions
    _validate_reserved_region_conflicts(
        table_bar, table_offset, table_end, "table", errors
    )
    _validate_reserved_region_conflicts(pba_bar, pba_offset, pba_end, "PBA", errors)


def _validate_reserved_region_conflicts(
    bar_index: int,
    start_offset: int,
    end_offset: int,
    structure_name: str,
    errors: List[str],
) -> None:
    """Check for conflicts with known reserved memory regions."""
    # Common reserved regions in PCIe devices
    reserved_regions = RESERVED_REGIONS

    # Check conflicts only for BAR 0 (most common for control regions)
    if bar_index == 0:
        for region in reserved_regions:
            if start_offset < region["end"] and end_offset > region["start"]:
                errors.append(
                    (
                        f"MSI-X {structure_name} (0x{start_offset:x}-0x{end_offset:x}) "
                        f"conflicts with {region['name']} "
                        f"(0x{region['start']:x}-0x{region['end']:x})"
                    )
                )


def _validate_basic_bar_configuration(
    bars: List[Dict[str, Any]], errors: List[str], warnings: List[str]
) -> None:
    """Validate basic BAR configuration without MSI-X."""
    for bar_config in bars:
        bar_index = bar_config.get("bar", -1)
        if bar_index < BAR_INDEX_MIN or bar_index > BAR_INDEX_MAX:
            errors.append(
                (
                    f"Invalid BAR index {bar_index} (must be {BAR_INDEX_MIN}-"
                    f"{BAR_INDEX_MAX})"
                )
            )

        bar_size = bar_config.get("size", 0)
        if bar_size == 0:
            warnings.append(f"BAR {bar_index} has size 0")
        elif bar_size & (bar_size - 1) != 0:
            warnings.append(
                (
                    f"BAR {bar_index} size 0x{bar_size:x} is not power of 2 "
                    "(may cause alignment issues)"
                )
            )


def _validate_driver_compatibility(
    bars: List[Dict[str, Any]],
    msix_cap: Dict[str, Any],
    errors: List[str],
    warnings: List[str],
    device_info: Dict[str, Any],
) -> None:
    """Check for common driver compatibility issues."""
    table_bar = msix_cap.get("table_bar", 0)
    pba_bar = msix_cap.get("pba_bar", 0)
    table_size = decode_table_size(msix_cap.get("table_size", 0))

    # Check if table and PBA are in different BARs
    if table_bar != pba_bar:
        warnings.append(
            (
                f"MSI-X table (BAR {table_bar}) and PBA (BAR {pba_bar}) in "
                "different BARs (may complicate driver implementation)"
            )
        )

    # Check for very large vector counts
    if table_size > LARGE_VECTOR_WARNING_THRESHOLD:
        warnings.append(
            (
                f"Large MSI-X table size ({table_size}) may impact performance and "
                "memory usage"
            )
        )

    # Check for non-standard BAR usage
    if table_bar > NON_STANDARD_BAR_MAX:
        warnings.append(
            f"MSI-X table in BAR {table_bar} (drivers typically expect BAR 0-2)"
        )

    # Device-specific warnings
    vendor_id = device_info.get("vendor_id", 0)

    # Import vendor ID constants
    from src.device_clone.constants import VENDOR_ID_INTEL, VENDOR_ID_NVIDIA

    if vendor_id == VENDOR_ID_INTEL:  # Intel
        if table_size > INTEL_VECTOR_WARNING_THRESHOLD:
            warnings.append(
                (
                    "Intel devices with >128 MSI-X vectors may have compatibility "
                    "issues"
                )
            )
    elif vendor_id == VENDOR_ID_NVIDIA:  # NVIDIA
        if table_bar != 0:
            warnings.append("NVIDIA drivers typically expect MSI-X structures in BAR 0")


def _validate_performance_considerations(
    bars: List[Dict[str, Any]],
    msix_cap: Dict[str, Any],
    warnings: List[str],
    device_info: Dict[str, Any],
) -> None:
    """Check for performance-related configuration issues."""
    table_offset = msix_cap.get("table_offset", 0)
    pba_offset = msix_cap.get("pba_offset", 0)
    table_size = decode_table_size(msix_cap.get("table_size", 0))

    # Check for optimal cache line alignment
    if table_offset % CACHELINE_OPTIMAL != 0:
        warnings.append(
            f"MSI-X table offset 0x{table_offset:x} is not 64-byte aligned "
            "(suboptimal for cache)"
        )
    if pba_offset % CACHELINE_OPTIMAL != 0:
        warnings.append(
            f"MSI-X PBA offset 0x{pba_offset:x} is not 64-byte aligned "
            f"(suboptimal for cache)"
        )

    # Check for reasonable vector distribution
    if table_size > EXCESSIVE_VECTOR_THRESHOLD_LOWEND:
        device_id = device_info.get("device_id", 0)
        # High-end devices can justify more vectors
        if device_id < LOW_END_DEVICE_ID_THRESHOLD:
            warnings.append(
                f"MSI-X table size {table_size} may be excessive "
                f"for this device class"
            )

    # Check BAR sizes for efficiency
    table_bar = msix_cap.get("table_bar", 0)
    for bar_config in bars:
        if bar_config.get("bar") == table_bar:
            bar_size = bar_config.get("size", 0)
            # Warn if BAR is much larger than needed
            table_size_bytes = compute_table_bytes(table_size)
            if bar_size > table_size_bytes * OVERSIZED_BAR_FACTOR:
                warnings.append(
                    (
                        f"BAR {table_bar} size (0x{bar_size:x}) is much larger than "
                        f"MSI-X requirements (0x{table_size_bytes:x})"
                    )
                )


def auto_fix_msix_configuration(
    bars: List[Dict[str, Any]], capabilities: List[Dict[str, Any]]
) -> Tuple[List[Dict[str, Any]], List[Dict[str, Any]], List[str]]:
    """
    Automatically fix common MSI-X configuration issues.

    Args:
        bars: List of BAR configuration dictionaries
        capabilities: List of capability dictionaries

    Returns:
        Tuple of (fixed_bars, fixed_capabilities, fix_messages)
    """
    fixed_bars = [bar.copy() for bar in bars]
    fixed_capabilities = [cap.copy() for cap in capabilities]
    fix_messages = []

    # Find MSI-X capability
    msix_cap_index = None
    for i, cap in enumerate(fixed_capabilities):
        if cap.get("cap_id") == MSIX_CAP_ID:
            msix_cap_index = i
            break

    if msix_cap_index is None:
        return fixed_bars, fixed_capabilities, fix_messages

    msix_cap = fixed_capabilities[msix_cap_index]
    table_bar_index = msix_cap.get("table_bar", 0)
    pba_bar_index = msix_cap.get("pba_bar", 0)
    table_size_value = decode_table_size(msix_cap.get("table_size", 0))

    # Fix 1: Align offsets to 4KB boundaries
    table_offset_value = msix_cap.get("table_offset", 0)
    if table_offset_value % PAGE_SIZE_4K != 0:
        new_table_offset = (
            (table_offset_value + (PAGE_SIZE_4K - 1)) // PAGE_SIZE_4K
        ) * PAGE_SIZE_4K
        msix_cap["table_offset"] = new_table_offset
        fix_messages.append(f"Aligned MSI-X table offset to 0x{new_table_offset:x}")

    pba_offset_value = msix_cap.get("pba_offset", 0)
    if pba_offset_value % PAGE_SIZE_4K != 0:
        new_pba_offset = (
            (pba_offset_value + (PAGE_SIZE_4K - 1)) // PAGE_SIZE_4K
        ) * PAGE_SIZE_4K
        msix_cap["pba_offset"] = new_pba_offset
        fix_messages.append(f"Aligned MSI-X PBA offset to 0x{new_pba_offset:x}")

    # Fix 2: Resolve overlaps in same BAR
    if table_bar_index == pba_bar_index:
        updated_table_offset = msix_cap.get("table_offset", 0)
        updated_pba_offset = msix_cap.get("pba_offset", 0)
        table_size_bytes = compute_table_bytes(table_size_value)
        table_end_value = updated_table_offset + table_size_bytes
        pba_size_bytes = compute_pba_bytes(table_size_value)
        pba_end_value = updated_pba_offset + pba_size_bytes

        if (
            updated_table_offset < pba_end_value
            and table_end_value > updated_pba_offset
        ):
            # Move PBA after table
            new_pba_offset_value = (
                (table_end_value + (PAGE_SIZE_4K - 1)) // PAGE_SIZE_4K
            ) * PAGE_SIZE_4K
            msix_cap["pba_offset"] = new_pba_offset_value
            fix_messages.append(
                (
                    f"Moved MSI-X PBA to 0x{new_pba_offset_value:x} to avoid "
                    "table overlap"
                )
            )

    # Fix 3: Ensure adequate BAR sizes
    for bar_config in fixed_bars:
        bar_index = bar_config.get("bar", -1)

        if bar_index == table_bar_index:
            required_size = msix_cap.get("table_offset", 0) + compute_table_bytes(
                table_size_value
            )
            required_size = (
                (required_size + (PAGE_SIZE_4K - 1)) // PAGE_SIZE_4K
            ) * PAGE_SIZE_4K  # Round up to 4KB
            if bar_config.get("size", 0) < required_size:
                bar_config["size"] = required_size
                fix_messages.append(
                    (
                        f"Increased BAR {bar_index} size to 0x{required_size:x} "
                        f"for MSI-X table"
                    )
                )

        if bar_index == pba_bar_index:
            pba_size_bytes = compute_pba_bytes(table_size_value)
            required_size = msix_cap.get("pba_offset", 0) + pba_size_bytes
            required_size = (
                (required_size + (PAGE_SIZE_4K - 1)) // PAGE_SIZE_4K
            ) * PAGE_SIZE_4K  # Round up to 4KB
            if bar_config.get("size", 0) < required_size:
                bar_config["size"] = required_size
                fix_messages.append(
                    (
                        f"Increased BAR {bar_index} size to 0x{required_size:x} "
                        f"for MSI-X PBA"
                    )
                )

    # Fix 4: Move structures away from reserved regions
    reserved_end = RESERVED_REGION_CONTROL_END  # First 32KB reserved region

    updated_table_offset = msix_cap.get("table_offset", 0)
    if updated_table_offset < reserved_end:
        new_table_offset_value = (
            (reserved_end + (PAGE_SIZE_4K - 1)) // PAGE_SIZE_4K
        ) * PAGE_SIZE_4K
        msix_cap["table_offset"] = new_table_offset_value
        fix_messages.append(
            (
                f"Moved MSI-X table to 0x{new_table_offset_value:x} to avoid "
                "reserved region"
            )
        )

    updated_pba_offset = msix_cap.get("pba_offset", 0)
    if updated_pba_offset < reserved_end:
        new_pba_offset_value = (
            (reserved_end + (PAGE_SIZE_4K - 1)) // PAGE_SIZE_4K
        ) * PAGE_SIZE_4K
        # Ensure no overlap with moved table
        updated_table_end = msix_cap.get("table_offset", 0) + compute_table_bytes(
            table_size_value
        )
        if new_pba_offset_value < updated_table_end:
            new_pba_offset_value = (
                (updated_table_end + (PAGE_SIZE_4K - 1)) // PAGE_SIZE_4K
            ) * PAGE_SIZE_4K
        msix_cap["pba_offset"] = new_pba_offset_value
        fix_messages.append(
            f"Moved MSI-X PBA to 0x{new_pba_offset_value:x} to avoid reserved region"
        )

    return fixed_bars, fixed_capabilities, fix_messages


def print_validation_report(
    is_valid: bool,
    errors: List[str],
    warnings: List[str],
    device_info: Optional[Dict[str, Any]] = None,
) -> None:
    """
    Print a formatted validation report.

    Args:
        is_valid: Whether configuration is valid
        errors: List of error messages
        warnings: List of warning messages
        device_info: Optional device information
    """
    if device_info:
        vendor_id = device_info.get("vendor_id", 0)
        device_id = device_info.get("device_id", 0)
        print(f"\nMSI-X/BAR Validation Report for {vendor_id:04x}:{device_id:04x}")
    else:
        print("\nMSI-X/BAR Validation Report")

    print("=" * 60)

    if is_valid:
        print("✅ Configuration is VALID")
    else:
        print("❌ Configuration is INVALID")

    if errors:
        print(f"\n🚨 Errors ({len(errors)}):")
        for i, error in enumerate(errors, 1):
            print(f"  {i}. {error}")

    if warnings:
        print(f"\n⚠️  Warnings ({len(warnings)}):")
        for i, warning in enumerate(warnings, 1):
            print(f"  {i}. {warning}")

    if not errors and not warnings:
        print("\n✨ No issues found!")

    print("=" * 60)


if __name__ == "__main__":
    # Example usage and testing
    sample_bars = [
        {"bar": 0, "type": "memory", "size": 0x10000, "prefetchable": False},
        {"bar": 1, "type": "memory", "size": 0x8000, "prefetchable": False},
    ]

    sample_capabilities = [
        {
            "cap_id": 0x11,  # MSI-X
            "table_size": 7,  # Encoded as N-1, so 8 vectors
            "table_bar": 1,
            "table_offset": 0x1000,
            "pba_bar": 1,
            "pba_offset": 0x2000,
        }
    ]

    # Import vendor ID constant for sample
    from src.device_clone.constants import VENDOR_ID_INTEL

    sample_device = {"vendor_id": VENDOR_ID_INTEL, "device_id": 0x1572}

    is_valid, errors, warnings = validate_msix_bar_configuration(
        sample_bars, sample_capabilities, sample_device
    )

    print_validation_report(is_valid, errors, warnings, sample_device)

    if not is_valid:
        print("\nAttempting auto-fix...")
        fixed_bars, fixed_caps, fix_messages = auto_fix_msix_configuration(
            sample_bars, sample_capabilities
        )

        print("Fix messages:")
        for msg in fix_messages:
            print(f"  - {msg}")

        # Re-validate
        (
            is_valid_after,
            errors_after,
            warnings_after,
        ) = validate_msix_bar_configuration(fixed_bars, fixed_caps, sample_device)

        print_validation_report(
            is_valid_after, errors_after, warnings_after, sample_device
        )
