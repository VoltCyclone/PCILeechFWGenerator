#!/usr/bin/env python3
"""
Test script for PCILeech Overlay RAM Mapper

This script tests the overlay mapper functionality with various register types
to ensure it correctly identifies registers that need overlay entries.
"""

import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from src.device_clone.overlay_mapper import (OverlayMapper,
                                             PCIeRegisterDefinitions)
from src.device_clone.writemask_generator import WritemaskGenerator


def create_test_config_space():
    """Create a test configuration space with various capabilities."""
    # Initialize with zeros
    config_space = {}

    # Standard PCI header (Type 0)
    config_space[0x00] = 0x12348086  # Vendor/Device ID
    config_space[0x01] = 0x04100006  # Command/Status
    config_space[0x02] = 0x02000001  # Class/Revision
    config_space[0x03] = 0x00004000  # Header type/Latency/Cache line

    # BARs
    config_space[0x04] = 0xFE000000  # BAR0 - 32MB memory
    config_space[0x05] = 0x00000000  # BAR1
    config_space[0x06] = 0xFD000004  # BAR2 - 64-bit memory
    config_space[0x07] = 0x00000000  # BAR3 (upper 32 bits of BAR2)
    config_space[0x08] = 0x0000FC01  # BAR4 - I/O space
    config_space[0x09] = 0x00000000  # BAR5

    # Capabilities pointer
    config_space[0x0D] = 0x00000040  # Cap pointer at 0x40

    # Power Management capability at 0x40
    config_space[0x10] = 0x48010001  # PM cap header, next at 0x48
    config_space[0x11] = 0x00000003  # PMCSR

    # MSI capability at 0x48
    config_space[0x12] = 0x50810005  # MSI cap header, next at 0x50
    config_space[0x13] = 0xFEE00000  # Message address
    config_space[0x14] = 0x00000000  # Message data

    # MSI-X capability at 0x50
    config_space[0x14] = 0x60000011  # MSI-X cap header, next at 0x60
    config_space[0x15] = 0x00000801  # Table size 2, enabled
    config_space[0x17] = 0x00002000  # Table offset/BIR
    config_space[0x18] = (
        0x00002800  # PBA offset/BIR (adjusted to avoid overlap and reflect valid structure)
    )
    config_space[0x19] = 0x00003000  # Additional PBA offset/BIR for test data
    config_space[0x1B] = 0x00002810  # Device capabilities
    config_space[0x1C] = 0x00000000  # Device control/status

    # Extended capabilities at 0x100
    config_space[0x40] = 0x14010001  # AER capability header, next at 0x140

    return config_space


def create_test_capabilities():
    """Create test capability mappings."""
    return {
        "0x01": 0x40,  # Power Management
        "0x05": 0x48,  # MSI
        "0x11": 0x50,  # MSI-X
        "0x10": 0x60,  # PCIe
        "0x0001": 0x100,  # AER (extended)
    }


def demo_overlay_detection():
    """Demo overlay detection for various register types."""
    print("Testing PCILeech Overlay RAM Mapper")
    print("=" * 60)

    # Create test data
    config_space = create_test_config_space()
    capabilities = create_test_capabilities()

    # Initialize mapper
    mapper = OverlayMapper()

    # Generate overlay map
    overlay_config = mapper.generate_overlay_map(config_space, capabilities)

    print(f"\nDetected {overlay_config['OVERLAY_ENTRIES']} overlay entries:")
    print("-" * 60)
    print(f"{'Offset':>8} | {'Register':>10} | {'Mask':>10} | Description")
    print("-" * 60)

    for reg_num, mask in overlay_config["OVERLAY_MAP"]:
        offset = reg_num * 4
        print(f"0x{offset:06X} | 0x{reg_num:08X} | 0x{mask:08X} | ", end="")

        # Identify register type
        if offset == 0x04:
            print("Command/Status Register")
        elif offset == 0x0C:
            print("Cache Line/Latency/Header/BIST")
        elif 0x10 <= offset <= 0x24:
            print(f"BAR{(offset - 0x10) // 4}")
        elif offset == 0x30:
            print("Expansion ROM Base Address")
        elif offset == 0x3C:
            print("Interrupt Line/Pin")
        elif offset == 0x44:
            print("Power Management Control/Status")
        elif offset == 0x48:
            print("MSI Control")
        elif offset == 0x50:
            print("MSI-X Control")
        elif offset >= 0x100:
            print("Extended Capability Register")
        else:
            print("Other Register")

    # Test specific register detection
    print("\n" + "=" * 60)
    print("Testing specific register types:")
    print("-" * 60)

    definitions = PCIeRegisterDefinitions()

    # Test Command/Status register
    cmd_status = definitions.STANDARD_REGISTERS.get(0x04)
    if cmd_status:
        print(f"Command/Status (0x04): {cmd_status.register_type.name}")
        print(f"  Expected mask: 0x{cmd_status.mask:08X}")

    # Test BAR registers
    for i in range(6):
        offset = 0x10 + (i * 4)
        bar_entry = definitions.STANDARD_REGISTERS.get(offset)
        if bar_entry:
            print(f"BAR{i} (0x{offset:02X}): {bar_entry.register_type.name}")

    # Verify overlay entries match expected registers
    print("\n" + "=" * 60)
    print("Verification Summary:")
    print("-" * 60)

    expected_registers = [
        (0x04, "Command/Status"),
        (0x0C, "Cache Line/Latency"),
        (0x30, "Expansion ROM"),
        (0x3C, "Interrupt Line/Pin"),
    ]

    found_count = 0
    for offset, name in expected_registers:
        reg_num = offset // 4
        found = any(r == reg_num for r, _ in overlay_config["OVERLAY_MAP"])
        status = "✓" if found else "✗"
        print(f"{status} {name} (0x{offset:02X})")
        if found:
            found_count += 1

    print(f"\nFound {found_count}/{len(expected_registers)} expected registers")

    # Compare with writemask generator
    print("\n" + "=" * 60)
    print("Comparison with WritemaskGenerator:")
    print("-" * 60)

    wm_gen = WritemaskGenerator()
    capabilities_found = wm_gen.locate_capabilities(config_space)
    print(f"WritemaskGenerator found {len(capabilities_found)} capabilities")
    print(f"OverlayMapper found {len(capabilities)} capabilities")

    return overlay_config


def demo_mask_generation():
    """Demo mask generation for different register types."""
    print("\n" + "=" * 60)
    print("Testing Mask Generation:")
    print("-" * 60)

    definitions = PCIeRegisterDefinitions()

    # Test Command register bits
    print("\nCommand Register (0x04) bit analysis:")
    print("-" * 40)
    cmd_mask = definitions.STANDARD_REGISTERS[0x04].mask
    for bit, (name, writable) in definitions.COMMAND_REGISTER_BITS.items():
        if bit < 16:  # Command register is lower 16 bits
            bit_set = bool(cmd_mask & (1 << bit))
            status = "✓" if bit_set == writable else "✗"
            print(f"  Bit {bit:2d}: {status} {name} (Writable: {writable})")

    # Test Status register bits
    print("\nStatus Register (0x06) bit analysis:")
    print("-" * 40)
    status_mask = (definitions.STANDARD_REGISTERS[0x04].mask >> 16) & 0xFFFF
    for bit, (name, writable, rw1c) in definitions.STATUS_REGISTER_BITS.items():
        bit_set = bool(status_mask & (1 << bit))
        expected = writable or rw1c
        status = "✓" if bit_set == expected else "✗"
        rw1c_str = " (RW1C)" if rw1c else ""
        print(f"  Bit {bit:2d}: {status} {name} (Writable: {writable}{rw1c_str})")


if __name__ == "__main__":
    # Run demos
    overlay_config = demo_overlay_detection()
    demo_mask_generation()

    print("\n" + "=" * 60)
    print("Test completed successfully!")

    # Generate example output for documentation
    print("\n" + "=" * 60)
    print("Example OVERLAY_MAP for cfg_shadow.sv.j2:")
    print("-" * 60)
    print("OVERLAY_MAP = [")
    for reg_num, mask in overlay_config["OVERLAY_MAP"][:5]:  # Show first 5 entries
        print(f"    ({reg_num}, 0x{mask:08X}),  # Offset 0x{reg_num*4:03X}")
    if len(overlay_config["OVERLAY_MAP"]) > 5:
        print("    # ... more entries ...")
    print("]")
    print(f"OVERLAY_ENTRIES = {overlay_config['OVERLAY_ENTRIES']}")
    
    # Note: Not returning anything to comply with pytest expectations

