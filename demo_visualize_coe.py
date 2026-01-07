#!/usr/bin/env python3
"""
Demo script for visualize_coe.py utility.
Creates sample .coe files and demonstrates visualization and comparison features.
"""

import tempfile
import subprocess
from pathlib import Path


def create_sample_coe_template() -> str:
    """Create a sample template .coe file with Xilinx device IDs."""
    return """memory_initialization_radix=16;
memory_initialization_vector=
10EE7024,
00100006,
05800001,
00000000,
00000004,
00000000,
00000000,
00000000,
00000000,
00000000,
00000000,
10EE0007,
00000000,
00000040,
00000000,
00FF0100,
00000000,
00000000,
00000000,
00000000,
00000000,
00000000,
00000000,
00000000;
"""


def create_sample_coe_generated() -> str:
    """Create a sample generated .coe file with donor device IDs (Intel NIC)."""
    return """memory_initialization_radix=16;
memory_initialization_vector=
15418086,
00100006,
02000002,
00000000,
00000004,
00000000,
00000000,
00000000,
00000000,
00000000,
00000000,
00018086,
00000000,
00000040,
00000000,
00FF0100,
00000000,
00000000,
00000000,
00000000,
00000000,
00000000,
00000000,
00000000;
"""


def create_sample_coe_nvidia() -> str:
    """Create a sample .coe file with NVIDIA device IDs."""
    return """memory_initialization_radix=16;
memory_initialization_vector=
1B8010DE,
00100407,
03000000,
00000000,
00000004,
00000000,
00000000,
00000000,
00000000,
00000000,
00000000,
119810DE,
00000000,
00000060,
00000000,
01050100;
"""


def run_demo():
    """Run the complete demo."""
    print("=" * 80)
    print("  VISUALIZE_COE.PY DEMONSTRATION")
    print("=" * 80)
    print()
    print("This demo will:")
    print("  1. Create sample .coe files")
    print("  2. Visualize a single .coe file")
    print("  3. Compare template vs generated .coe files")
    print("  4. Show different vendor devices")
    print()
    input("Press Enter to continue...")
    
    # Create temporary directory for sample files
    with tempfile.TemporaryDirectory() as tmpdir:
        tmp_path = Path(tmpdir)
        
        # Create sample files
        template_file = tmp_path / "template.coe"
        generated_file = tmp_path / "generated.coe"
        nvidia_file = tmp_path / "nvidia_device.coe"
        
        template_file.write_text(create_sample_coe_template())
        generated_file.write_text(create_sample_coe_generated())
        nvidia_file.write_text(create_sample_coe_nvidia())
        
        print("\n" + "=" * 80)
        print("  DEMO 1: Visualizing a Single .COE File (Template)")
        print("=" * 80)
        print("\nThis shows the PCIe configuration space for a Xilinx device.")
        input("\nPress Enter to visualize...")
        
        subprocess.run([
            "python3",
            "scripts/visualize_coe.py",
            str(template_file)
        ])
        
        print("\n" + "=" * 80)
        print("  DEMO 2: Comparing Template vs Generated Files")
        print("=" * 80)
        print("\nThis demonstrates how device IDs are replaced during generation.")
        print("Template has Xilinx IDs (0x10EE), generated has Intel IDs (0x8086).")
        input("\nPress Enter to compare...")
        
        subprocess.run([
            "python3",
            "scripts/visualize_coe.py",
            str(template_file),
            str(generated_file)
        ])
        
        print("\n" + "=" * 80)
        print("  DEMO 3: Visualizing Different Vendor (NVIDIA)")
        print("=" * 80)
        print("\nThis shows how the tool recognizes different vendors.")
        input("\nPress Enter to visualize...")
        
        subprocess.run([
            "python3",
            "scripts/visualize_coe.py",
            str(nvidia_file)
        ])
        
        print("\n" + "=" * 80)
        print("  DEMO 4: Usage Examples")
        print("=" * 80)
        print("\nCommand-line usage:")
        print("  # Visualize single file:")
        print("  ./scripts/visualize_coe.py path/to/file.coe")
        print()
        print("  # Compare template vs generated:")
        print("  ./scripts/visualize_coe.py template.coe generated.coe")
        print()
        print("  # Real-world example:")
        print("  ./scripts/visualize_coe.py \\")
        print("    pcileech_datastore/35T/pcie_7x_0_config_rom_template.coe \\")
        print("    output/some_device/pcie_7x_0_config_rom.coe")
        print()
    
    print("=" * 80)
    print("  DEMO COMPLETE")
    print("=" * 80)
    print("\nKey Features Demonstrated:")
    print("  ✓ Parsing .coe memory initialization vectors")
    print("  ✓ Decoding PCIe configuration space (Device/Vendor IDs, BARs, etc.)")
    print("  ✓ Recognizing vendor names (Intel, AMD, NVIDIA, Xilinx, etc.)")
    print("  ✓ Comparing files to show injected device IDs")
    print("  ✓ Pretty-printed box drawing output")
    print()


if __name__ == "__main__":
    run_demo()
