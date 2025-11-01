#!/usr/bin/env python3
"""
MSI-X Capability Parser

This module provides functionality to parse MSI-X capability structures from
PCI configuration space and generate SystemVerilog code for MSI-X table replication.
"""

import struct

from typing import Any, Dict, List, Optional, Tuple

# Import project logging and string utilities

from src.log_config import get_logger

from src.string_utils import (format_kv_table, format_raw_bar_table,
                              log_debug_safe, log_error_safe, log_info_safe,
                              log_warning_safe, safe_format, safe_print_format)

# Import PCI capability infrastructure for extended capabilities support
from src.pci_capability.compat import find_cap as pci_find_cap

from src.pci_capability.compat import find_ext_cap

from src.pci_capability.types import CapabilityType

# Import template renderer
from src.templating.template_renderer import TemplateRenderer

# Import BAR size constants
from src.device_clone.constants import BAR_SIZE_CONSTANTS

logger = get_logger(__name__)

# Define commonly used BAR size constants
BAR_MEM_MIN_SIZE = BAR_SIZE_CONSTANTS["SIZE_4KB"]  

BAR_MEM_DEFAULT_SIZE = BAR_SIZE_CONSTANTS["SIZE_64KB"] 

BAR_IO_DEFAULT_SIZE = BAR_SIZE_CONSTANTS[
    "MAX_IO_SIZE"
]  # 256 bytes default for I/O BARs


def hex_to_bytes(hex_string: str) -> bytearray:
    """
    Convert hex string to bytearray for efficient byte-level operations.

    Args:
        hex_string: Configuration space as a hex string

    Returns:
        bytearray representation of the hex string
    """
    if len(hex_string) % 2 != 0:
        raise ValueError("Hex string must have even length")
    return bytearray.fromhex(hex_string)


def read_u8(data: bytearray, offset: int) -> int:
    """
    Read an 8-bit value from bytearray.

    Args:
        data: Byte data
        offset: Byte offset to read from

    Returns:
        8-bit unsigned integer value

    Raises:
        IndexError: If offset is out of bounds
    """
    return data[offset]


def read_u16_le(data: bytearray, offset: int) -> int:
    """
    Read a 16-bit little-endian value from bytearray.

    Args:
        data: Byte data
        offset: Byte offset to read from

    Returns:
        16-bit unsigned integer value

    Raises:
        struct.error: If offset is out of bounds
    """
    return struct.unpack_from("<H", data, offset)[0]


def read_u32_le(data: bytearray, offset: int) -> int:
    """
    Read a 32-bit little-endian value from bytearray.

    Args:
        data: Byte data
        offset: Byte offset to read from

    Returns:
        32-bit unsigned integer value

    Raises:
        struct.error: If offset is out of bounds
    """
    return struct.unpack_from("<I", data, offset)[0]


def is_valid_offset(data: bytearray, offset: int, size: int) -> bool:
    """
    Check if reading 'size' bytes from 'offset' is within bounds.

    Args:
        data: Byte data
        offset: Starting offset
        size: Number of bytes to read

    Returns:
        True if the read is within bounds
    """
    return offset + size <= len(data)


def find_cap(cfg: str, cap_id: int) -> Optional[int]:
    """
    Find a capability in the PCI configuration space,

    Args:
        cfg: Configuration space as a hex string
        cap_id: Capability ID to find (e.g., 0x11 for MSI-X)

    Returns:
        Offset of the capability in the configuration space, or None if not found
    """
    log_debug_safe(
        logger,
        safe_format(
            "Searching for capability ID 0x{cap_id:02x} in configuration space, Configuration space length: {length} characters",
            cap_id=cap_id,
            length=len(cfg),
        ),
        prefix="PCICAP",
    )

    # Try to use the PCI capability infrastructure first
    try:
        # First try standard capabilities
        standard_offset = pci_find_cap(cfg, cap_id)
        if standard_offset is not None:
            log_debug_safe(
                logger,
                safe_format(
                    "Found capability ID 0x{cap_id:02x} at "
                    "standard offset 0x{offset:02x}",
                    cap_id=cap_id,
                    offset=standard_offset,
                ),
                prefix="PCICAP",
            )
            return standard_offset

        # If not found in standard space, try extended capabilities
        extended_offset = find_ext_cap(cfg, cap_id)
        if extended_offset is not None:
            log_debug_safe(
                logger,
                safe_format(
                    "Found capability ID 0x{cap_id:02x} at "
                    "extended offset 0x{offset:03x}",
                    cap_id=cap_id,
                    offset=extended_offset,
                ),
                prefix="PCICAP",
            )
            return extended_offset

        # Not found in either space
        log_debug_safe(
            logger,
            safe_format("Capability ID 0x{cap_id:02x} not found", cap_id=cap_id),
            prefix="PCICAP",
        )
        return None

    except Exception as e:
        log_warning_safe(
            logger,
            safe_format(
                "Error using PCI capability infrastructure: {error}, "
                "falling back to local implementation",
                error=e,
            ),
            prefix="PCICAP",
        )
        # Fall through to local implementation

    # Fallback to local implementation for standard capabilities only
    if not cfg or len(cfg) < 512:  # 256 bytes = 512 hex chars
        log_warning_safe(
            logger,
            safe_format("Configuration space is too small (need ≥256 bytes)"),
            prefix="PCICAP",
        )
        return None

    try:
        # Convert hex string to bytes for efficient processing
        cfg_bytes = hex_to_bytes(cfg)
    except ValueError as e:
        log_error_safe(
            logger,
            safe_format("Invalid hex string in configuration space: {error}", error=e),
            prefix="PCICAP",
        )
        return None

    # Check if capabilities are supported (Status register bit 4)
    status_offset = 0x06
    if not is_valid_offset(cfg_bytes, status_offset, 2):
        log_warning_safe(
            logger,
            safe_format("Status register not found in configuration space"),
            prefix="PCICAP",
        )
        return None

    try:
        status = read_u16_le(cfg_bytes, status_offset)
        if not (status & 0x10):  # Check capabilities bit
            log_debug_safe(
                logger,
                safe_format("Device does not support capabilities"),
                prefix="PCICAP",
            )
            return None
    except struct.error:
        log_warning_safe(
            logger,
            safe_format("Failed to read status register"),
            prefix="PCICAP",
        )
        return None

    # Get capabilities pointer (offset 0x34)
    cap_ptr_offset = 0x34
    if not is_valid_offset(cfg_bytes, cap_ptr_offset, 1):
        log_warning_safe(
            logger,
            "Capabilities pointer not found in configuration space",
            prefix="PCICAP",
        )
        return None

    try:
        cap_ptr = read_u8(cfg_bytes, cap_ptr_offset)
        if cap_ptr == 0:
            log_debug_safe(logger, "No capabilities present", prefix="PCICAP")
            return None
    except IndexError:
        log_warning_safe(
            logger, "Failed to read capabilities pointer", prefix="PCICAP"
        )
        return None

    # Walk the capabilities list
    current_ptr = cap_ptr
    visited = set()  # To detect loops

    while current_ptr and current_ptr != 0 and current_ptr not in visited:
        visited.add(current_ptr)

        # Ensure we have enough data for capability header (ID + next pointer)
        if not is_valid_offset(cfg_bytes, current_ptr, 2):
            log_warning_safe(
                logger,
                safe_format(
                    "Capability pointer 0x{current_ptr:02x} is out of bounds",
                    current_ptr=current_ptr,
                ),
                prefix="PCICAP",
            )
            return None

        # Read capability ID and next pointer
        try:
            current_cap_id = read_u8(cfg_bytes, current_ptr)
            next_ptr = read_u8(cfg_bytes, current_ptr + 1)

            if current_cap_id == cap_id:
                return current_ptr

            current_ptr = next_ptr
        except IndexError:
            log_warning_safe(
                logger,
                safe_format(
                    "Invalid capability data at offset 0x{current_ptr:02x}",
                    current_ptr=current_ptr,
                ),
                prefix="PCICAP",
            )
            return None

    log_debug_safe(logger, "Capability ID 0x{cap_id:02x} not found", cap_id=cap_id)
    return None


def msix_size(cfg: str) -> int:
    """
    Determine the MSI-X table size from the configuration space.

    Args:
        cfg: Configuration space as a hex string

    Returns:
        Number of MSI-X table entries, or 0 if MSI-X is not supported
    """
    # Find MSI-X capability (ID 0x11)
    cap = find_cap(cfg, 0x11)
    if cap is None:
        log_info_safe(logger, "MSI-X capability not found", prefix="PCICAP")
        return 0

    try:
        # Convert hex string to bytes for efficient processing
        cfg_bytes = hex_to_bytes(cfg)
    except ValueError as e:
        log_error_safe(
            logger,
            safe_format("Invalid hex string in configuration space: {error}", error=e),
            prefix="PCICAP",
        )
        return 0

    # Read Message Control register (offset 2 from capability start)
    msg_ctrl_offset = cap + 2
    if not is_valid_offset(cfg_bytes, msg_ctrl_offset, 2):
        log_warning_safe(
            logger, "MSI-X Message Control register is out of bounds", prefix="PCICAP"
        )
        return 0

    try:
        # Read 16-bit little-endian Message Control register
        msg_ctrl = read_u16_le(cfg_bytes, msg_ctrl_offset)

        # Table size is encoded in the lower 11 bits (Table Size field)
        table_size = (msg_ctrl & 0x7FF) + 1

        log_debug_safe(
            logger,
            safe_format(
                "MSI-X table size: {table_size} entries (msg_ctrl=0x{msg_ctrl:04x})",
                table_size=table_size,
                msg_ctrl=msg_ctrl,
            ),
            prefix="PCICAP",
        )
        return table_size
    except struct.error:
        log_warning_safe(
            logger, "Failed to read MSI-X Message Control register", prefix="PCICAP"
        )
        return 0


def parse_msix_capability(cfg: str) -> Dict[str, Any]:
    """
    Parse the MSI-X capability structure from the configuration space.

    Args:
        cfg: Configuration space as a hex string

    Returns:
        Dictionary containing MSI-X capability information:
        - table_size: Number of MSI-X table entries
        - table_bir: BAR indicator for the MSI-X table
        - table_offset: Offset of the MSI-X table in the BAR
        - pba_bir: BAR indicator for the PBA
        - pba_offset: Offset of the PBA in the BAR
        - enabled: Whether MSI-X is enabled
        - function_mask: Whether the function is masked
    """
    result = {
        "table_size": 0,
        "table_bir": 0,
        "table_offset": 0,
        "pba_bir": 0,
        "pba_offset": 0,
        "enabled": False,
        "function_mask": False,
    }
    # Find MSI-X capability (ID 0x11)
    cap = find_cap(cfg, 0x11)
    if cap is None:
        log_info_safe(
            logger,
            "MSI-X capability not found",
            prefix="PCICAP",
        )
        return result
    log_debug_safe(
        logger,
        safe_format("MSI-X capability found at offset 0x{cap:02x}", cap=cap),
        prefix="PCICAP",
    )
    try:
        # Convert hex string to bytes for efficient processing
        cfg_bytes = hex_to_bytes(cfg)
    except ValueError as e:
        log_error_safe(
            logger,
            safe_format("Invalid hex string in configuration space: {error}", error=e),
            prefix="PCICAP",
        )
        return result

    # Read Message Control register (offset 2 from capability start)
    msg_ctrl_offset = cap + 2
    if not is_valid_offset(cfg_bytes, msg_ctrl_offset, 2):
        log_warning_safe(
            logger, "MSI-X Message Control register is out of bounds", prefix="PCICAP"
        )
        return result

    try:
        # Read 16-bit little-endian Message Control register
        msg_ctrl = read_u16_le(cfg_bytes, msg_ctrl_offset)

        # Parse Message Control fields
        table_size = (msg_ctrl & 0x7FF) + 1  # Bits 10:0
        enabled = bool(msg_ctrl & 0x8000)  # Bit 15
        function_mask = bool(msg_ctrl & 0x4000)  # Bit 14

        # Read Table Offset/BIR register (offset 4 from capability start)
        table_offset_bir_offset = cap + 4
        if not is_valid_offset(cfg_bytes, table_offset_bir_offset, 4):
            log_warning_safe(
                logger,
                "MSI-X Table Offset/BIR register is out of bounds",
                prefix="PCICAP",
            )
            return result

        table_offset_bir = read_u32_le(cfg_bytes, table_offset_bir_offset)
        table_bir = table_offset_bir & 0x7  # Lower 3 bits
        table_offset = (
            table_offset_bir & 0xFFFFFFF8
        )  # Clear lower 3 bits for 8-byte alignment

        # Read PBA Offset/BIR register (offset 8 from capability start)
        pba_offset_bir_offset = cap + 8
        if not is_valid_offset(cfg_bytes, pba_offset_bir_offset, 4):
            log_warning_safe(
                logger,
                safe_format(
                    "MSI-X PBA Offset/BIR register at 0x{offset:02x} is out of bounds",
                    offset=pba_offset_bir_offset,
                ),
                prefix="PCICAP",
            )
            return result

        pba_offset_bir = read_u32_le(cfg_bytes, pba_offset_bir_offset)
        pba_bir = pba_offset_bir & 0x7  # Lower 3 bits
        pba_offset = (
            pba_offset_bir & 0xFFFFFFF8
        )  # Clear lower 3 bits for 8-byte alignment

        # Update result
        result.update(
            {
                "table_size": table_size,
                "table_bir": table_bir,
                "table_offset": table_offset,
                "pba_bir": pba_bir,
                "pba_offset": pba_offset,
                "enabled": enabled,
                "function_mask": function_mask,
            }
        )

        log_info_safe(
            logger,
            safe_format(
                "MSI-X capability found: {table_size} entries, "
                "table BIR {table_bir} offset 0x{table_offset:x}, "
                "PBA BIR {pba_bir} offset 0x{pba_offset:x}",
                table_size=table_size,
                table_bir=table_bir,
                table_offset=table_offset,
                pba_bir=pba_bir,
                pba_offset=pba_offset,
            ),
        )
        # Check for alignment warnings
        if table_offset & 0x7 != 0:
            log_warning_safe(
                logger,
                safe_format(
                    "MSI-X table offset 0x{table_offset:x} is not 8-byte aligned "
                    "(actual offset: 0x{table_offset:x}, aligned: 0x{aligned:x})",
                    table_offset=table_offset,
                    aligned=table_offset & 0xFFFFFFF8,
                ),
                prefix="PCICAP",
            )

        return result

    except struct.error as e:
        log_warning_safe(
            logger,
            safe_format("Error reading MSI-X capability registers: {error}", error=e),
            prefix="PCICAP",
        )
        return result


def parse_bar_info_from_config_space(cfg: str) -> List[Dict[str, Any]]:
    """
    Parse BAR information from configuration space for overlap detection.

    This method uses the unified BAR parser for consistent BAR parsing
    across the codebase.

    Args:
        cfg: Configuration space as a hex string

    Returns:
        List of dictionaries containing BAR information with keys:
        - index: BAR index (0-5)
        - bar_type: "memory" or "io"
        - address: Base address (64-bit for 64-bit BARs)
        - size: BAR size in bytes
        - is_64bit: Whether this is a 64-bit BAR
        - prefetchable: Whether the BAR is prefetchable
    """
    from src.device_clone.bar_parser import parse_bar_info_as_dicts
    return parse_bar_info_as_dicts(cfg)


def validate_msix_configuration_enhanced(
    msix_info: Dict[str, Any], cfg: str
) -> Tuple[bool, List[str]]:
    """
    Enhanced MSI-X configuration validation with proper 64-bit BAR support.

    Args:
        msix_info: Dictionary containing MSI-X capability information
        cfg: Configuration space as a hex string for BAR parsing

    Returns:
        Tuple of (is_valid, error_messages)
    """
    errors = []

    # Check table size validity
    table_size = msix_info.get("table_size", 0)
    if table_size == 0:
        errors.append("MSI-X table size is zero")
    elif table_size > 2048:  # PCIe spec maximum
        errors.append(
            safe_format(
                "MSI-X table size {table_size} exceeds maximum of 2048",
                table_size=table_size,
            )
        )

    # Check BIR validity (must be 0-5 for standard BARs)
    table_bir = msix_info.get("table_bir", 0)
    pba_bir = msix_info.get("pba_bir", 0)

    if table_bir > 5:
        errors.append(
            safe_format(
                "MSI-X table BIR {table_bir} is invalid (must be 0-5)",
                table_bir=table_bir,
            )
        )
    if pba_bir > 5:
        errors.append(
            safe_format(
                "MSI-X PBA BIR {pba_bir} is invalid (must be 0-5)", pba_bir=pba_bir
            )
        )

    # Check alignment requirements
    table_offset = msix_info.get("table_offset", 0)
    pba_offset = msix_info.get("pba_offset", 0)

    if table_offset % 8 != 0:
        errors.append(
            safe_format(
                "MSI-X table offset 0x{table_offset:x} is not 8-byte aligned",
                table_offset=table_offset,
            )
        )
    if pba_offset % 8 != 0:
        errors.append(
            safe_format(
                "MSI-X PBA offset 0x{pba_offset:x} is not 8-byte aligned",
                pba_offset=pba_offset,
            )
        )

    # Enhanced overlap detection with proper BAR parsing
    if table_bir == pba_bir:
        # Parse BAR information from configuration space
        bars = parse_bar_info_from_config_space(cfg)

        # Find the relevant BAR
        target_bar = None
        for bar in bars:
            if bar["index"] == table_bir:
                target_bar = bar
                break

        if target_bar is None:
            log_warning_safe(
                logger,
                safe_format(
                    "Could not find BAR {bir} information for overlap validation",
                    bir=table_bir,
                ),
                prefix="PCICAP",
            )
            # Fall back to basic overlap detection
            table_end = table_offset + (table_size * 16)  # 16 bytes per entry
            pba_size = ((table_size + 31) // 32) * 4  # PBA size in bytes
            pba_end = pba_offset + pba_size

            if table_offset < pba_end and table_end > pba_offset:
                errors.append(
                    "MSI-X table and PBA overlap in the same BAR (basic validation)"
                )
        else:
            # Enhanced validation with actual BAR information
            bar_size = target_bar["size"]
            bar_is_64bit = target_bar["is_64bit"]

            log_debug_safe(
                logger,
                safe_format(
                    "Validating MSI-X overlap in BAR {bir}: size=0x{size:x}, "
                    "64bit={is_64bit}",
                    bir=table_bir,
                    size=bar_size,
                    is_64bit=bar_is_64bit,
                ),
                prefix="PCICAP",
            )

            # Calculate table and PBA regions with proper 64-bit support
            table_end = table_offset + (table_size * 16)  # 16 bytes per entry
            pba_size = ((table_size + 31) // 32) * 4  # PBA size in bytes
            pba_end = pba_offset + pba_size

            # Check if regions fit within the BAR
            if bar_size > 0:  # Only validate if we have BAR size information
                if table_end > bar_size:
                    errors.append(
                        safe_format(
                            "MSI-X table extends beyond BAR {bir} "
                            "(table ends at 0x{table_end:x}, "
                            "BAR size is 0x{bar_size:x})",
                            bir=table_bir,
                            table_end=table_end,
                            bar_size=bar_size,
                        )
                    )

                if pba_end > bar_size:
                    errors.append(
                        safe_format(
                            "MSI-X PBA extends beyond BAR {bir} "
                            "(PBA ends at 0x{pba_end:x}, BAR size is 0x{bar_size:x})",
                            bir=pba_bir,
                            pba_end=pba_end,
                            bar_size=bar_size,
                        )
                    )

            # Check for overlap between table and PBA
            if table_offset < pba_end and table_end > pba_offset:
                errors.append(
                    safe_format(
                        "MSI-X table (0x{table_offset:x}-0x{table_end:x}) and "
                        "PBA (0x{pba_offset:x}-0x{pba_end:x}) "
                        "overlap in BAR {bir}",
                        table_offset=table_offset,
                        table_end=table_end,
                        pba_offset=pba_offset,
                        pba_end=pba_end,
                        bir=table_bir,
                    )
                )

    _is_valid = len(errors) == 0
    return _is_valid, errors


def generate_msix_table_sv(msix_info: Dict[str, Any]) -> str:
    """
    Generate SystemVerilog code for the MSI-X table and PBA.

    Args:
        msix_info: Dictionary containing MSI-X capability information

    Returns:
        SystemVerilog code for the MSI-X table and PBA
    """
    # Validate required fields to prevent template rendering errors
    required_fields = [
        "table_size",
        "table_bir",
        "table_offset",
        "pba_bir",
        "pba_offset",
        "enabled",
        "function_mask",
    ]
    missing_fields = [field for field in required_fields if field not in msix_info]
    if missing_fields:
        log_error_safe(
            logger,
            safe_format(
                "CRITICAL: Missing required MSI-X fields: {fields}",
                fields=missing_fields,
            ),
            prefix="PCICAP",
        )

        raise ValueError(
            safe_format(
                "Cannot generate MSI-X module - "
                "missing critical fields: {fields}. "
                "MSI-X BAR indices and offsets must come "
                "from actual hardware configuration.",
                fields=missing_fields,
            )
        )

    if msix_info["table_size"] == 0:
        log_debug_safe(
            logger,
            safe_format("MSI-X: Table size is 0, generating disabled MSI-X module"),
            prefix="PCICAP",
        )
        # Generate a proper disabled module instead of returning a comment
        table_size = 1  # Minimum size for valid SystemVerilog
        pba_size = 1
        alignment_warning = "// MSI-X disabled - no interrupt vectors configured"
        enabled_val = 0
        function_mask_val = 1  # Force masked when disabled
    else:
        log_debug_safe(
            logger,
            "MSI-X: Found, generating SystemVerilog code for MSI-X table",
            prefix="PCICAP",
        )
        table_size = msix_info["table_size"]
        pba_size = (table_size + 31) // 32  # Number of 32-bit words needed for PBA
        enabled_val = 1 if msix_info["enabled"] else 0
        function_mask_val = 1 if msix_info["function_mask"] else 0

        # Generate alignment warning if needed
        alignment_warning = ""
        if msix_info["table_offset"] % 8 != 0:
            alignment_warning = safe_format(
                "// Warning: MSI-X table offset " "0x{offset:x} is not 8-byte aligned",
                offset=msix_info["table_offset"],
            )

    # Prepare template context
    context = {
        "table_size": table_size,
        "table_bir": msix_info["table_bir"],
        "table_offset": msix_info["table_offset"],
        "pba_bir": msix_info["pba_bir"],
        "pba_offset": msix_info["pba_offset"],
        "enabled_val": enabled_val,
        "function_mask_val": function_mask_val,
        "pba_size": pba_size,
        "pba_size_minus_one": pba_size - 1,
        "alignment_warning": alignment_warning,
    }

    # Use template renderer
    renderer = TemplateRenderer()
    main_template = renderer.render_template(
        "systemverilog/msix_implementation.sv.j2", context
    )
    capability_registers = generate_msix_capability_registers(msix_info)
    return main_template + "\n" + capability_registers


def validate_msix_configuration(
    msix_info: Dict[str, Any], cfg: str = ""
) -> Tuple[bool, List[str]]:
    """
    Validate MSI-X configuration for correctness and compliance.

    This function now supports both legacy mode (without cfg parameter) and
    enhanced mode (with cfg parameter for proper 64-bit BAR validation).

    Args:
        msix_info: Dictionary containing MSI-X capability information
        cfg: Optional configuration space hex string for enhanced validation

    Returns:
        Tuple of (is_valid, error_messages)
    """
    if cfg:
        # Use enhanced validation with proper BAR parsing
        return validate_msix_configuration_enhanced(msix_info, cfg)
    else:
        # Legacy validation mode for backward compatibility
        errors = []

        # Check table size validity
        table_size = msix_info.get("table_size", 0)
        if table_size == 0:
            errors.append("MSI-X table size is zero")
        elif table_size > 2048:  # PCIe spec maximum
            errors.append(
                safe_format(
                    "MSI-X table size {size} exceeds maximum of 2048", size=table_size
                )
            )

        # Check BIR validity (must be 0-5 for standard BARs)
        table_bir = msix_info.get("table_bir", 0)
        pba_bir = msix_info.get("pba_bir", 0)

        if table_bir > 5:
            errors.append(
                safe_format(
                    "MSI-X table BIR {bir} is invalid (must be 0-5)", bir=table_bir
                )
            )
        if pba_bir > 5:
            errors.append(
                safe_format("MSI-X PBA BIR {bir} is invalid (must be 0-5)", bir=pba_bir)
            )

        # Check alignment requirements
        table_offset = msix_info.get("table_offset", 0)
        pba_offset = msix_info.get("pba_offset", 0)

        if table_offset % 8 != 0:
            errors.append(
                safe_format(
                    "MSI-X table offset 0x{offset:x} is not 8-byte aligned",
                    offset=table_offset,
                )
            )
        if pba_offset % 8 != 0:
            errors.append(
                safe_format(
                    "MSI-X PBA offset 0x{offset:x} is not 8-byte aligned",
                    offset=pba_offset,
                )
            )

        # Basic overlap detection for legacy mode
        if table_bir == pba_bir:
            table_end = table_offset + (table_size * 16)  # 16 bytes per entry
            pba_size = ((table_size + 31) // 32) * 4  # PBA size in bytes
            pba_end = pba_offset + pba_size

            if table_offset < pba_end and table_end > pba_offset:
                errors.append(
                    "MSI-X table and PBA overlap in the same BAR (basic validation)"
                )

        _is_valid = len(errors) == 0
        return _is_valid, errors


def generate_msix_capability_registers(msix_info: Dict[str, Any]) -> str:
    """
    Generate SystemVerilog code for MSI-X capability register handling.

    Args:
        msix_info: Dictionary containing MSI-X capability information

    Returns:
        SystemVerilog code for MSI-X capability register management
    """
    # Always generate a proper module, even for disabled MSI-X
    table_size = max(
        1, msix_info.get("table_size", 1)
    )  # Minimum size 1 for valid SystemVerilog

    # Precompute encoded offset|BIR values (keep lines short)
    _table_enc = msix_info.get("table_offset", 0x1000) | msix_info.get("table_bir", 0)
    _pba_enc = msix_info.get("pba_offset", 0x2000) | msix_info.get("pba_bir", 0)

    context = {
        "table_size_minus_one": table_size - 1,
        "table_offset_bir": "32'h" + f"{_table_enc:08X}",
        "pba_offset_bir": "32'h" + f"{_pba_enc:08X}",
    }

    # Use template renderer
    renderer = TemplateRenderer()
    return renderer.render_template(
        "systemverilog/msix_capability_registers.sv.j2", context
    )


if __name__ == "__main__":
    # Example usage
    import sys

    if len(sys.argv) < 2:
        print("Usage: python msix_capability.py <config_space_hex_file>")
        sys.exit(1)

    with open(sys.argv[1], "r") as f:
        config_space = f.read().strip()

    msix_info = parse_msix_capability(config_space)

    # Pretty table for MSI-X summary using shared formatter

    msix_rows: List[Tuple[str, str]] = [
        ("Table Size", str(msix_info.get("table_size", 0))),
        ("Table BIR", str(msix_info.get("table_bir", 0))),
        ("Table Offset", f"0x{msix_info.get('table_offset', 0):x}"),
        ("PBA BIR", str(msix_info.get("pba_bir", 0))),
        ("PBA Offset", f"0x{msix_info.get('pba_offset', 0):x}"),
        ("Enabled", "yes" if msix_info.get("enabled", False) else "no"),
        ("Function Mask", "yes" if msix_info.get("function_mask", False) else "no"),
    ]
    print(format_kv_table(msix_rows, title="MSI-X Summary"))

    # Enhanced validation with BAR parsing
    is_valid, errors = validate_msix_configuration(msix_info, config_space)
    safe_print_format(
        template="Validation Result: {status}",
        prefix="PCICAP",
        status=("VALID" if is_valid else "INVALID"),
    )
    if errors:
        # Render errors in a compact table
        err_rows = [("Issue", e) for e in errors]
    print(format_kv_table(err_rows, title="Validation Errors"))

    # Parse and display BAR information
    bars = parse_bar_info_from_config_space(config_space)
    if bars:
        safe_print_format(
            template="Parsed BARs ({count} active):",
            prefix="PCICAP",
            count=len(bars),
        )
        print(format_raw_bar_table(bars, device_bdf="N/A"))

    sv_code = generate_msix_table_sv(msix_info)
    safe_print_format(template="SystemVerilog Code:", prefix="PCICAP")
    print(sv_code)
