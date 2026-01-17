#!/usr/bin/env python3
"""
Test the COE report generation functionality.
Tests include failsafe behavior to ensure visualization never breaks builds.
"""

import sys
import tempfile
from pathlib import Path

# Add project root to path
sys.path.insert(0, str(Path(__file__).parent.parent))

# Test imports
from pcileechfwgenerator.utils.coe_report import find_coe_files, generate_coe_report


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


def test_failsafe_missing_files():
    """Test that missing files don't cause exceptions."""
    with tempfile.TemporaryDirectory() as tmpdir:
        tmp_path = Path(tmpdir)
        
        # Try to generate report with no files - should not raise
        try:
            success = generate_coe_report(tmp_path)
            assert success is False, "Expected False when no files present"
            print("✓ failsafe test (missing files) passed")
        except Exception as e:
            raise AssertionError(f"Should not raise exception on missing files: {e}")


def test_failsafe_nonexistent_directory():
    """Test that nonexistent directory doesn't cause exceptions."""
    nonexistent = Path("/tmp/nonexistent_dir_12345_test")
    
    try:
        success = generate_coe_report(nonexistent)
        assert success is False, "Expected False for nonexistent directory"
        print("✓ failsafe test (nonexistent directory) passed")
    except Exception as e:
        raise AssertionError(f"Should not raise exception on nonexistent dir: {e}")


def test_failsafe_corrupted_files():
    """Test that corrupted .coe files don't cause exceptions."""
    with tempfile.TemporaryDirectory() as tmpdir:
        tmp_path = Path(tmpdir)
        
        # Create corrupted files
        template = tmp_path / "test_template.coe"
        generated = tmp_path / "test.coe"
        
        template.write_text("CORRUPTED DATA !@#$")
        generated.write_text("MORE CORRUPTED DATA")
        
        # Should handle gracefully
        try:
            success = generate_coe_report(tmp_path)
            # May succeed or fail, but should not raise
            print(f"✓ failsafe test (corrupted files) passed (success={success})")
        except Exception as e:
            raise AssertionError(f"Should not raise exception on corrupted files: {e}")


if __name__ == "__main__":
    test_find_coe_files()
    test_generate_report()
    test_failsafe_missing_files()
    test_failsafe_nonexistent_directory()
    test_failsafe_corrupted_files()
    print("\n✓ All tests passed!")
