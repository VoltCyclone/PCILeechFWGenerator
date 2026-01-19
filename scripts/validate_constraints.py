#!/usr/bin/env python3
"""
Constraint Validation Script for PCILeech Firmware Generator

This script validates that constraint files have proper pin assignments
and I/O standards for all required ports.
"""

import argparse
import re
import sys
from pathlib import Path
from typing import Set, Tuple


def extract_ports_from_systemverilog(sv_file: Path) -> Set[str]:
    """Extract port names from SystemVerilog module definition."""
    ports = set()

    try:
        with open(sv_file, "r") as f:
            content = f.read()

        # Find module definition
        module_match = re.search(
            r"module\s+pcileech_top\s*\((.*?)\);", content, re.DOTALL
        )
        if not module_match:
            print(f"Warning: Could not find pcileech_top module in {sv_file}")
            return ports

        port_section = module_match.group(1)

        # Extract individual ports
        port_lines = re.findall(
            r"(input|output)\s+logic(?:\s*\[[^\]]+\])?\s+(\w+)", port_section
        )

        for direction, port_name in port_lines:
            ports.add(port_name)

            # For bus signals, also check individual bits
            bus_match = re.search(
                rf"{re.escape(port_name)}\s*\[(\d+):(\d+)\]", port_section
            )
            if bus_match:
                high_bit = int(bus_match.group(1))
                low_bit = int(bus_match.group(2))
                for bit in range(low_bit, high_bit + 1):
                    ports.add(f"{port_name}[{bit}]")

    except Exception as e:
        print(f"Error reading SystemVerilog file {sv_file}: {e}")

    return ports


def extract_constraints_from_xdc(xdc_file: Path) -> Tuple[Set[str], Set[str]]:
    """Extract pin assignments and I/O standards from XDC file."""
    pin_assignments = set()
    iostandards = set()

    try:
        with open(xdc_file, "r") as f:
            content = f.read()

        # Find PACKAGE_PIN assignments
        pin_matches = re.findall(
            r"set_property\s+PACKAGE_PIN\s+\w+\s+\[get_ports\s+(?:\{)?([^\}]+)(?:\})?\]",
            content,
        )
        for match in pin_matches:
            # Clean up port name
            port_name = match.strip("{}").strip()
            pin_assignments.add(port_name)

        # Find IOSTANDARD assignments
        iostd_matches = re.findall(
            r"set_property\s+IOSTANDARD\s+\w+\s+\[get_ports\s+([^\]]+)\]", content
        )
        for match in iostd_matches:
            # Handle wildcard patterns
            port_pattern = match.strip()
            iostandards.add(port_pattern)

    except Exception as e:
        print(f"Error reading XDC file {xdc_file}: {e}")

    return pin_assignments, iostandards


def validate_constraints(sv_file: Path, xdc_file: Path) -> bool:
    """Validate that all ports have proper constraints."""
    print(f"Validating constraints...")
    print(f"SystemVerilog file: {sv_file}")
    print(f"Constraints file: {xdc_file}")
    print("-" * 60)

    # Extract ports from SystemVerilog
    required_ports = extract_ports_from_systemverilog(sv_file)
    if not required_ports:
        print("Error: No ports found in SystemVerilog file")
        return False

    print(f"Found {len(required_ports)} ports in SystemVerilog module")

    # Extract constraints from XDC
    pin_assignments, iostandards = extract_constraints_from_xdc(xdc_file)

    print(f"Found {len(pin_assignments)} pin assignments in constraints")
    print(f"Found {len(iostandards)} I/O standard assignments in constraints")
    print()

    # Check for missing pin assignments
    missing_pins = []
    missing_iostandards = []

    for port in required_ports:
        # Skip internal signals that don't need pin assignments
        if any(
            internal in port
            for internal in ["bar_", "cfg_device", "cfg_class", "cfg_subsystem"]
        ):
            continue

        # Check pin assignment
        has_pin = any(
            port in assigned or port.replace("[", "\\[").replace("]", "\\]") in assigned
            for assigned in pin_assignments
        )
        if not has_pin:
            # Check for wildcard matches
            base_port = port.split("[")[0] if "[" in port else port
            has_wildcard_pin = any(
                f"{base_port}*" in assigned for assigned in pin_assignments
            )
            if not has_wildcard_pin:
                missing_pins.append(port)

        # Check I/O standard
        has_iostd = any(
            port in iostd or f"{port.split('[')[0]}*" in iostd for iostd in iostandards
        )
        if not has_iostd:
            missing_iostandards.append(port)

    # Report results
    success = True

    if missing_pins:
        print("❌ MISSING PIN ASSIGNMENTS:")
        for port in sorted(missing_pins):
            print(f"  - {port}")
        print()
        success = False
    else:
        print("✅ All ports have pin assignments")

    if missing_iostandards:
        print("❌ MISSING I/O STANDARDS:")
        for port in sorted(missing_iostandards):
            print(f"  - {port}")
        print()
        success = False
    else:
        print("✅ All ports have I/O standards")

    if success:
        print("🎉 All constraints validation passed!")
    else:
        print("⚠️  Constraint validation failed - please update your XDC file")
        print("\nRefer to docs/PIN_ASSIGNMENT_GUIDE.md for help")

    return success


def main():
    parser = argparse.ArgumentParser(description="Validate PCILeech constraint files")
    parser.add_argument(
        "--sv-file",
        type=Path,
        help="SystemVerilog top module file (default: search for pcileech_top.sv)",
    )
    parser.add_argument(
        "--xdc-file", type=Path, help="XDC constraints file (default: search for *.xdc)"
    )
    parser.add_argument(
        "--output-dir",
        type=Path,
        default=Path("output"),
        help="Output directory to search for files",
    )

    args = parser.parse_args()

    # Find SystemVerilog file
    sv_file = args.sv_file
    if not sv_file:
        # Search for pcileech_top.sv in output directory
        candidates = list(args.output_dir.glob("**/pcileech_top.sv"))
        if candidates:
            sv_file = candidates[0]
        else:
            print("Error: Could not find pcileech_top.sv file")
            print(
                "Please specify --sv-file or ensure the file exists in the output directory"
            )
            return 1

    if not sv_file.exists():
        print(f"Error: SystemVerilog file not found: {sv_file}")
        return 1

    # Find XDC file
    xdc_file = args.xdc_file
    if not xdc_file:
        # Search for XDC files in output directory
        candidates = list(args.output_dir.glob("**/*.xdc"))
        if candidates:
            xdc_file = candidates[0]
        else:
            print("Error: Could not find XDC constraints file")
            print(
                "Please specify --xdc-file or ensure XDC files exist in the output directory"
            )
            return 1

    if not xdc_file.exists():
        print(f"Error: XDC file not found: {xdc_file}")
        return 1

    # Validate constraints
    success = validate_constraints(sv_file, xdc_file)
    return 0 if success else 1


if __name__ == "__main__":
    sys.exit(main())
