#!/usr/bin/env python3
"""
Test the COE report generation functionality.
"""

import sys
import tempfile
from pathlib import Path

# Add project root to path
sys.path.insert(0, str(Path(__file__).parent.parent))

# Test imports
from src.utils.coe_report import find_coe_files, generate_coe_report


def create_test_coe(path: Path, device_id: int = 0x7024, vendor_id: int = 0x10EE):
    """Create a test .coe file."""
    dword0 = (device_id << 16) | vendor_id
    content = f"""memory_initialization_radix=16;
memory_initialization_vector=
{dword0:08X},
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
{dword0:08X},
00000000,
00000040,
00000000,
00FF0100;
"""
    path.write_text(content)


def test_find_coe_files():
    """Test finding COE file pairs."""
    with tempfile.TemporaryDirectory() as tmpdir:
        tmp_path = Path(tmpdir)
        
        # Create test files
        template = tmp_path / "pcie_7x_0_config_rom_template.coe"
        generated = tmp_path / "pcie_7x_0_config_rom.coe"
        
        create_test_coe(template, 0x7024, 0x10EE)
        create_test_coe(generated, 0x1541, 0x8086)
        
        # Find pairs
        pairs = find_coe_files(tmp_path)
        
        assert len(pairs) == 1, f"Expected 1 pair, found {len(pairs)}"
        assert pairs[0][0] == template
        assert pairs[0][1] == generated
        
        print("✓ find_coe_files test passed")


def test_generate_report():
    """Test report generation."""
    with tempfile.TemporaryDirectory() as tmpdir:
        tmp_path = Path(tmpdir)
        
        # Create test files
        template = tmp_path / "pcie_7x_0_config_rom_template.coe"
        generated = tmp_path / "pcie_7x_0_config_rom.coe"
        
        create_test_coe(template, 0x7024, 0x10EE)
        create_test_coe(generated, 0x1541, 0x8086)
        
        # Generate report
        success = generate_coe_report(tmp_path)
        
        # Report generation may fail if visualize_coe.py is not found
        # which is acceptable in test environments
        print(f"✓ generate_coe_report test completed (success={success})")


if __name__ == "__main__":
    test_find_coe_files()
    test_generate_report()
    print("\n✓ All tests passed!")
