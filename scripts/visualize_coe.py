#!/usr/bin/env python3
"""
Utility to visualize PCIe configuration space from .coe files.
Shows device/vendor IDs and other PCIe config space registers in a readable format.

"""

import argparse
import re
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Callable, Optional

# Add parent directory to path for imports
sys.path.insert(0, str(Path(__file__).parent.parent))

from src.pci_capability.constants import (
    PCIE_DEVICE_TYPE_NAMES,
    PCIE_LINK_SPEED_NAMES,
    STANDARD_CAPABILITY_NAMES,
    VENDOR_NAMES,
)

# Constants
BOX_WIDTH = 68


@dataclass
class RegisterDef:
    """Definition of a PCIe config space register."""
    name: str
    formatter: Callable[[int], str]


def format_split_dword(data: int) -> str:
    """Format a 32-bit DWORD as upper:lower 16-bit split."""
    upper = (data >> 16) & 0xFFFF
    lower = data & 0xFFFF
    return f"0x{upper:04X}:0x{lower:04X}"


def format_class_rev(data: int) -> str:
    """Format class code and revision."""
    class_code = (data >> 8) & 0xFFFFFF
    revision = data & 0xFF
    return f"0x{class_code:06X}:0x{revision:02X}"


# PCIe Configuration Space Header (first 16 DWORDs / 64 bytes)
PCIE_CONFIG_SPACE: dict[int, RegisterDef] = {
    0x00: RegisterDef("Device/Vendor ID", format_split_dword),
    0x04: RegisterDef("Status/Command", format_split_dword),
    0x08: RegisterDef("Class/Revision", format_class_rev),
    0x0C: RegisterDef("BIST/Hdr/Lat/Cache", lambda d: f"0x{d:08X}"),
    0x10: RegisterDef("BAR0", lambda d: f"0x{d:08X}"),
    0x14: RegisterDef("BAR1", lambda d: f"0x{d:08X}"),
    0x18: RegisterDef("BAR2", lambda d: f"0x{d:08X}"),
    0x1C: RegisterDef("BAR3", lambda d: f"0x{d:08X}"),
    0x20: RegisterDef("BAR4", lambda d: f"0x{d:08X}"),
    0x24: RegisterDef("BAR5", lambda d: f"0x{d:08X}"),
    0x28: RegisterDef("Cardbus CIS", lambda d: f"0x{d:08X}"),
    0x2C: RegisterDef("Subsystem ID/Vendor", format_split_dword),
    0x30: RegisterDef("ROM Base Addr", lambda d: f"0x{d:08X}"),
    0x34: RegisterDef("Rsvd/Cap Ptr", lambda d: f"0x{(d>>8)&0xFFFFFF:06X}:0x{d&0xFF:02X}"),
    0x38: RegisterDef("Reserved", lambda d: f"0x{d:08X}"),
    0x3C: RegisterDef("MaxLat/MinGnt/IRQ", lambda d: f"0x{d:08X}"),
}


class BoxPrinter:
    """Helper class for printing bordered boxes."""
    
    def __init__(self, width: int = BOX_WIDTH):
        self.width = width
    
    def print_top(self):
        """Print top border."""
        print("╔" + "═" * self.width + "╗")
    
    def print_bottom(self):
        """Print bottom border."""
        print("╚" + "═" * self.width + "╝")
    
    def print_separator(self):
        """Print middle separator."""
        print("╠" + "═" * self.width + "╣")
    
    def print_line(self, text: str, center: bool = False):
        """Print a line with borders, truncating if needed."""
        if center:
            content = text[:self.width].center(self.width)
        else:
            # Truncate if too long, preserving leading space
            if len(text) > self.width - 2:
                text = text[:self.width - 5] + "..."
            content = f" {text:<{self.width-2}} "
        print(f"║{content}║")


def parse_coe_file(coe_path: Path) -> Optional[list[int]]:
    """
    Parse a .coe file and extract the memory initialization vector.
    
    Handles variable-width hex values and normalizes to 32-bit words.
    Silently skips comments and malformed tokens.
    
    Args:
        coe_path: Path to the .coe file
        
    Returns:
        List of 32-bit integers representing the memory contents, or None on error
    """
    try:
        content = coe_path.read_text()
        
        # Find the memory_initialization_vector section
        vector_match = re.search(
            r'memory_initialization_vector\s*=\s*([^;]+);',
            content,
            re.MULTILINE | re.DOTALL
        )
        
        if not vector_match:
            print(f"Warning: No memory_initialization_vector found in {coe_path.name}")
            return None
        
        vector_text = vector_match.group(1)
        
        # Split by commas and parse each token
        values = []
        for token in vector_text.split(','):
            token = token.strip()
            # Skip empty tokens
            if not token:
                continue
            
            # Handle tokens that might contain comments
            # Split by newlines and take only non-comment lines
            lines = token.split('\n')
            for line in lines:
                line = line.strip()
                # Skip empty lines and comments
                if not line or line.startswith(('#', '//', '--')):
                    continue
                
                # Try to parse as hex (with or without 0x prefix)
                try:
                    if line.startswith('0x') or line.startswith('0X'):
                        val = int(line, 16)
                    else:
                        val = int(line, 16)
                    
                    # Validate it's a valid 32-bit value
                    if val > 0xFFFFFFFF:
                        print(f"Warning: Value {line} exceeds 32 bits, truncating")
                        val &= 0xFFFFFFFF
                    
                    values.append(val)
                except ValueError:
                    # Skip malformed tokens silently (might be comments or formatting)
                    continue
        
        if not values:
            print(f"Warning: No valid hex values found in {coe_path.name}")
            return None
        
        return values
        
    except Exception as e:
        print(f"Error parsing {coe_path.name}: {e}")
        return None


def check_endianness(data: list[int]) -> Optional[str]:
    """
    Heuristic check for possible endianness issues.
    
    Args:
        data: List of 32-bit words
        
    Returns:
        Warning message if endianness looks wrong, None otherwise
    """
    if not data:
        return None
    
    # Check Device/Vendor ID (offset 0x00)
    dword = data[0]
    vendor_id = dword & 0xFFFF
    device_id = (dword >> 16) & 0xFFFF
    
    # Vendor ID should never be 0xFFFF (invalid) or 0x0000 (reserved)
    # Device ID 0xFFFF is also suspicious
    if vendor_id == 0xFFFF or vendor_id == 0x0000:
        return f"Vendor ID 0x{vendor_id:04X} is invalid - possible endianness issue"
    
    # If both IDs are suspiciously high, might be byte-swapped
    if vendor_id > 0xFF00 and device_id > 0xFF00:
        return "Both IDs >0xFF00 - possible byte swap issue"
    
    return None


def decode_pcie_capability(data: list[int], cap_offset: int) -> Optional[str]:
    """
    Decode PCIe capability structure for key parameters.
    
    Args:
        data: Full config space data
        cap_offset: Byte offset to PCIe capability
        
    Returns:
        Human-readable summary of PCIe capability, or None if invalid
    """
    # PCIe capability is at least 0x3C bytes (version 1)
    # We need: cap header (4B) + device cap (4B) + device ctrl/status (4B) + link cap (4B)
    if cap_offset + 0x10 > len(data) * 4:
        return None
    
    try:
        # Read capability header (offset + 0x00)
        cap_idx = cap_offset // 4
        cap_header = data[cap_idx]
        pcie_cap_version = (cap_header >> 16) & 0xF
        device_type = (cap_header >> 20) & 0xF
        
        device_type_str = PCIE_DEVICE_TYPE_NAMES.get(
            device_type, f"Unknown (0x{device_type:X})"
        )
        version_str = f"PCIe Cap v{pcie_cap_version}"
        
        # Read Link Capabilities (offset + 0x0C)
        if cap_offset + 0x0C < len(data) * 4:
            link_cap_idx = (cap_offset + 0x0C) // 4
            link_cap = data[link_cap_idx]
            
            max_link_speed = link_cap & 0xF
            max_link_width = (link_cap >> 4) & 0x3F
            
            speed_str = PCIE_LINK_SPEED_NAMES.get(
                max_link_speed, f"Unknown (0x{max_link_speed:X})"
            )
            
            return (
                f"{version_str} | "
                f"{device_type_str} | "
                f"Max Link: x{max_link_width} @ {speed_str}"
            )
        else:
            return f"{version_str} | {device_type_str}"
            
    except (IndexError, KeyError):
        return None


def walk_capabilities(data: list[int]) -> tuple[list[tuple[int, int, str]], Optional[str]]:
    """
    Walk the PCI capabilities linked list.
    
    Args:
        data: Full config space data (32-bit words)
        
    Returns:
        Tuple of (capabilities list, warning message if truncated)
        capabilities: List of (offset, cap_id, description) tuples
        warning: String if cap pointer points beyond available data, None otherwise
    """
    capabilities = []
    warning = None
    
    # Check if capabilities are supported (Status register bit 4 at offset 0x06)
    if len(data) < 2:
        return capabilities, warning
    
    status_cmd = data[1]  # Offset 0x04
    status = (status_cmd >> 16) & 0xFFFF
    cap_list_supported = (status >> 4) & 0x1
    
    if not cap_list_supported:
        return capabilities, warning
    
    # Get capabilities pointer from offset 0x34 (lower byte)
    if len(data) < 14:  # Need at least offset 0x34
        return capabilities, warning
    
    cap_ptr_dword = data[13]  # Offset 0x34
    cap_ptr = cap_ptr_dword & 0xFF
    
    # Check if first cap is beyond our data
    if cap_ptr != 0 and cap_ptr >= len(data) * 4:
        warning = f"Cap pointer 0x{cap_ptr:02X} beyond available data"
        return capabilities, warning
    
    # Walk the capabilities list
    visited = set()
    max_iterations = 48  # Safety limit
    
    while cap_ptr != 0 and cap_ptr >= 0x40 and max_iterations > 0:
        max_iterations -= 1
        
        # Check for loops
        if cap_ptr in visited:
            break
        visited.add(cap_ptr)
        
        # Validate offset is within bounds and aligned
        if cap_ptr % 4 != 0:
            warning = f"Misaligned cap pointer 0x{cap_ptr:02X}"
            break
            
        if cap_ptr >= len(data) * 4:
            # Hit the edge of available data
            if not warning:  # Don't overwrite earlier warnings
                warning = f"Capabilities continue beyond available data (0x{cap_ptr:02X}+)"
            break
        
        # Read capability header
        cap_idx = cap_ptr // 4
        if cap_idx >= len(data):
            break
            
        cap_header = data[cap_idx]
        cap_id = cap_header & 0xFF
        next_ptr = (cap_header >> 8) & 0xFF
        
        # Get capability name
        cap_name = STANDARD_CAPABILITY_NAMES.get(cap_id, f"Unknown (0x{cap_id:02X})")
        
        # Special decoding for PCIe capability
        if cap_id == 0x10:
            pcie_info = decode_pcie_capability(data, cap_ptr)
            if pcie_info:
                cap_name = f"PCIe: {pcie_info}"
        
        capabilities.append((cap_ptr, cap_id, cap_name))
        
        # Move to next capability
        cap_ptr = next_ptr
    
    return capabilities, warning


def visualize_pcie_config_space(
    data: list[int], 
    title: str = "PCIe Configuration Space"
):
    """
    Visualize PCIe configuration space data in a readable format.
    
    Args:
        data: List of 32-bit words representing config space
        title: Title for the visualization
    """
    box = BoxPrinter()
    
    print()
    box.print_top()
    box.print_line(title, center=True)
    box.print_separator()
    
    if not data:
        box.print_line("No data available")
        box.print_bottom()
        return
    
    # Check for endianness issues
    endian_warning = check_endianness(data)
    if endian_warning:
        box.print_line(f"WARNING: {endian_warning}")
        box.print_separator()
    
    # Display each DWORD
    for offset in sorted(PCIE_CONFIG_SPACE.keys()):
        idx = offset // 4
        if idx >= len(data):
            continue
            
        reg_def = PCIE_CONFIG_SPACE[offset]
        value = data[idx]
        formatted = reg_def.formatter(value)
        
        # Special formatting for Device ID / Vendor ID
        if offset == 0x00:
            device_id = (value >> 16) & 0xFFFF
            vendor_id = value & 0xFFFF
            vendor_name = VENDOR_NAMES.get(vendor_id, "Unknown")
            
            line = f" 0x{offset:02X}: {reg_def.name:20} │ {formatted:18} "
            box.print_line(line)
            
            vendor_line = (
                f"      └─ Device: 0x{device_id:04X}  "
                f"Vendor: 0x{vendor_id:04X} ({vendor_name})"
            )
            box.print_line(vendor_line)
        else:
            # Regular register display
            line = f" 0x{offset:02X}: {reg_def.name:20} │ {formatted:18} "
            box.print_line(line)
    
    # Show capabilities if present
    capabilities, cap_warning = walk_capabilities(data)
    if capabilities or cap_warning:
        box.print_separator()
        box.print_line(f"PCI Capabilities ({len(capabilities)} found)", center=True)
        box.print_separator()
        
        for cap_offset, cap_id, cap_name in capabilities:
            line = f" 0x{cap_offset:02X}: [0x{cap_id:02X}] {cap_name}"
            box.print_line(line)
        
        if cap_warning:
            box.print_line(f"Note: {cap_warning}")
    
    # Show additional data if present
    if len(data) > 16:
        box.print_separator()
        extra_count = min(len(data) - 16, 16)
        box.print_line(f"Extended Config ({extra_count} DWORDs)", center=True)
        box.print_separator()
        
        for i in range(16, min(32, len(data))):
            offset = i * 4
            value = data[i]
            line = f" 0x{offset:02X}: Word {i:2d}        │ 0x{value:08X}      "
            box.print_line(line)
    
    box.print_bottom()
    print()


def compare_coe_files(template_path: Path, generated_path: Path):
    """
    Compare template and generated .coe files.
    
    Visualizes the generated config space and highlights differences
    from the template (typically injected Device/Vendor IDs).
    
    Args:
        template_path: Path to template .coe file
        generated_path: Path to generated .coe file
    """
    print("\n" + "=" * 70)
    print("  PCIe Configuration Space - Generated .COE File")
    print("=" * 70)
    
    template_data = parse_coe_file(template_path)
    generated_data = parse_coe_file(generated_path)
    
    if generated_data:
        visualize_pcie_config_space(
            generated_data, 
            f"Generated: {generated_path.name}"
        )
    
    # Show what changed from template
    if not template_data:
        print("\nWarning: Could not parse template file")
        return 1
    
    if not generated_data:
        print("\nWarning: Could not parse generated file")
        return 1
    
    if template_data and generated_data:
        box = BoxPrinter()
        box.print_top()
        box.print_line("Device IDs Injected", center=True)
        box.print_separator()
        
        differences_found = False
        for i in range(min(len(template_data), len(generated_data))):
            if template_data[i] != generated_data[i]:
                offset = i * 4
                differences_found = True
                
                if offset == 0x00:
                    # Device/Vendor ID
                    device_id = (generated_data[i] >> 16) & 0xFFFF
                    vendor_id = generated_data[i] & 0xFFFF
                    
                    box.print_line(f" 0x{offset:02X}: Device/Vendor ID")
                    box.print_line(
                        f"      → 0x{device_id:04X}:0x{vendor_id:04X} "
                        "(donor device)"
                    )
                elif offset == 0x2C:
                    # Subsystem IDs
                    subsys_id = (generated_data[i] >> 16) & 0xFFFF
                    subsys_vendor = generated_data[i] & 0xFFFF
                    
                    box.print_line(f" 0x{offset:02X}: Subsystem IDs")
                    box.print_line(f"      → 0x{subsys_id:04X}:0x{subsys_vendor:04X}")
        
        if not differences_found:
            box.print_line("No differences from template")
        
        box.print_bottom()
    
    return 0


def main():
    """Main entry point with argparse CLI."""
    parser = argparse.ArgumentParser(
        description="Visualize PCIe configuration space from .coe files",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
Examples:
  %(prog)s file.coe                    # Visualize single file
  %(prog)s template.coe generated.coe  # Compare and show differences
        """
    )
    
    parser.add_argument(
        'files',
        nargs='+',
        type=Path,
        metavar='FILE',
        help='.coe file(s) to visualize (1 or 2 files)'
    )
    
    parser.add_argument(
        '--no-endian-check',
        action='store_true',
        help='Skip endianness validation warnings'
    )
    
    args = parser.parse_args()
    
    if len(args.files) == 1:
        # Single file visualization
        coe_path = args.files[0]
        if not coe_path.exists():
            print(f"Error: File not found: {coe_path}", file=sys.stderr)
            return 1
        
        data = parse_coe_file(coe_path)
        if not data:
            print("Failed to parse .coe file", file=sys.stderr)
            return 1
        
        visualize_pcie_config_space(data, f"PCIe Config Space: {coe_path.name}")
        return 0
    
    elif len(args.files) == 2:
        # Compare two files
        template_path = args.files[0]
        generated_path = args.files[1]
        
        if not template_path.exists():
            print(f"Error: Template file not found: {template_path}", file=sys.stderr)
            return 1
        
        if not generated_path.exists():
            print(f"Error: Generated file not found: {generated_path}", file=sys.stderr)
            return 1
        
        return compare_coe_files(template_path, generated_path)
    
    else:
        parser.print_help()
        print(f"\nError: Expected 1 or 2 files, got {len(args.files)}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    sys.exit(main())
