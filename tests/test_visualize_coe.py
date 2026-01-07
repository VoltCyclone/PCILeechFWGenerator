#!/usr/bin/env python3
"""
Unit tests for scripts/visualize_coe.py

Tests parsing, capability walking, and visualization logic.
"""

import sys
from pathlib import Path

import pytest

# Add parent directory to path
sys.path.insert(0, str(Path(__file__).parent.parent))

from scripts.visualize_coe import (
    BoxPrinter,
    check_endianness,
    decode_pcie_capability,
    format_class_rev,
    format_split_dword,
    parse_coe_file,
    walk_capabilities,
)


class TestFormatters:
    """Test formatting functions."""
    
    def test_format_split_dword_normal(self):
        """Test normal 16:16 split."""
        result = format_split_dword(0x10EC816A)
        assert result == "0x10EC:0x816A"
    
    def test_format_split_dword_zeros(self):
        """Test with zeros."""
        result = format_split_dword(0x00000000)
        assert result == "0x0000:0x0000"
    
    def test_format_split_dword_max(self):
        """Test with max values."""
        result = format_split_dword(0xFFFFFFFF)
        assert result == "0xFFFF:0xFFFF"
    
    def test_format_class_rev(self):
        """Test class code and revision formatting."""
        result = format_class_rev(0x02000004)
        assert result == "0x020000:0x04"
    
    def test_format_class_rev_max(self):
        """Test with max values."""
        result = format_class_rev(0xFFFFFFFF)
        assert result == "0xFFFFFF:0xFF"


class TestBoxPrinter:
    """Test BoxPrinter formatting."""
    
    def test_box_printer_initialization(self):
        """Test BoxPrinter can be initialized."""
        box = BoxPrinter(width=68)
        assert box.width == 68
    
    def test_box_printer_truncation(self, capsys):
        """Test that long lines are truncated."""
        box = BoxPrinter(width=20)
        box.print_line("This is a very long line that should be truncated")
        captured = capsys.readouterr()
        assert "..." in captured.out
        assert len(captured.out.split('\n')[0]) <= 22  # width + 2 for borders
    
    def test_box_printer_centering(self, capsys):
        """Test text centering."""
        box = BoxPrinter(width=20)
        box.print_line("Test", center=True)
        captured = capsys.readouterr()
        # Should be centered with spaces
        assert "Test" in captured.out


class TestCoeParser:
    """Test COE file parsing."""
    
    def test_parse_coe_basic(self, tmp_path):
        """Test parsing basic COE file."""
        coe_file = tmp_path / "test.coe"
        coe_file.write_text("""
memory_initialization_radix=16;
memory_initialization_vector=
10EC816A,
00100007,
02000004;
""")
        
        result = parse_coe_file(coe_file)
        assert result is not None
        assert len(result) == 3
        assert result[0] == 0x10EC816A
        assert result[1] == 0x00100007
        assert result[2] == 0x02000004
    
    def test_parse_coe_with_comments(self, tmp_path):
        """Test parsing COE with comments."""
        coe_file = tmp_path / "test.coe"
        coe_file.write_text("""
memory_initialization_radix=16;
memory_initialization_vector=
10EC816A,
# This is a comment
00100007,
// Another comment
02000004;
""")
        
        result = parse_coe_file(coe_file)
        assert result is not None
        # Parser treats comments as separate tokens that get filtered
        # Only actual hex values are parsed
        assert len(result) == 3
    
    def test_parse_coe_variable_width_hex(self, tmp_path):
        """Test parsing COE with variable width hex values."""
        coe_file = tmp_path / "test.coe"
        coe_file.write_text("""
memory_initialization_radix=16;
memory_initialization_vector=
0,
FF,
10EC816A,
FFFFFFFF;
""")
        
        result = parse_coe_file(coe_file)
        assert result is not None
        assert len(result) == 4
        assert result[0] == 0x0
        assert result[1] == 0xFF
        assert result[2] == 0x10EC816A
        assert result[3] == 0xFFFFFFFF
    
    def test_parse_coe_with_0x_prefix(self, tmp_path):
        """Test parsing COE with 0x prefix."""
        coe_file = tmp_path / "test.coe"
        coe_file.write_text("""
memory_initialization_radix=16;
memory_initialization_vector=
0x10EC816A,
0x00100007;
""")
        
        result = parse_coe_file(coe_file)
        assert result is not None
        assert len(result) == 2
        assert result[0] == 0x10EC816A
    
    def test_parse_coe_no_vector(self, tmp_path):
        """Test parsing COE without vector section."""
        coe_file = tmp_path / "test.coe"
        coe_file.write_text("""
memory_initialization_radix=16;
""")
        
        result = parse_coe_file(coe_file)
        assert result is None
    
    def test_parse_coe_invalid_file(self, tmp_path):
        """Test parsing non-existent file."""
        result = parse_coe_file(tmp_path / "nonexistent.coe")
        assert result is None
    
    def test_parse_coe_truncate_large_values(self, tmp_path):
        """Test that values >32 bits are truncated."""
        coe_file = tmp_path / "test.coe"
        coe_file.write_text("""
memory_initialization_radix=16;
memory_initialization_vector=
100000000;
""")
        
        result = parse_coe_file(coe_file)
        assert result is not None
        assert result[0] == 0x00000000  # Truncated to 32 bits


class TestEndiannessCheck:
    """Test endianness validation."""
    
    def test_check_endianness_valid(self):
        """Test with valid vendor ID."""
        data = [0x8086100E]  # Intel vendor, normal device
        result = check_endianness(data)
        assert result is None
    
    def test_check_endianness_invalid_vendor_ffff(self):
        """Test with invalid vendor ID 0xFFFF."""
        data = [0x0000FFFF]  # Vendor=0xFFFF (lower 16 bits), Device=0x0000 (upper 16 bits)
        result = check_endianness(data)
        assert result is not None
        assert "0xFFFF" in result
        assert "invalid" in result.lower()
    
    def test_check_endianness_invalid_vendor_0000(self):
        """Test with invalid vendor ID 0x0000."""
        data = [0x00000000]
        result = check_endianness(data)
        assert result is not None
        assert "0x0000" in result
    
    def test_check_endianness_byte_swap_suspected(self):
        """Test suspected byte swap (both IDs > 0xFF00)."""
        data = [0xFFEEFFDD]
        result = check_endianness(data)
        assert result is not None
        assert "FF00" in result or "byte swap" in result.lower()
    
    def test_check_endianness_empty_data(self):
        """Test with empty data."""
        result = check_endianness([])
        assert result is None


class TestCapabilityWalking:
    """Test PCI capability list walking."""
    
    def test_walk_capabilities_no_caps(self):
        """Test with device that has no capabilities."""
        # Status register without capability list bit
        data = [0] * 20
        data[1] = 0x00000000  # Status=0x0000 (no cap list bit)
        data[13] = 0x00000000  # No cap pointer
        
        capabilities, warning = walk_capabilities(data)
        assert capabilities == []
        assert warning is None
    
    def test_walk_capabilities_with_power_mgmt(self):
        """Test walking capabilities list with Power Management."""
        data = [0] * 32
        data[1] = 0x00100000  # Status with cap list bit (bit 4)
        data[13] = 0x00000050  # Cap pointer at 0x50
        
        # Power Management capability at 0x50
        data[20] = 0x00006001  # Cap ID=0x01 (PM), Next=0x60
        
        # End of list at 0x60
        data[24] = 0x00000005  # Cap ID=0x05 (MSI), Next=0x00
        
        capabilities, warning = walk_capabilities(data)
        assert len(capabilities) == 2
        assert capabilities[0][1] == 0x01  # PM
        assert capabilities[1][1] == 0x05  # MSI
        assert warning is None
    
    def test_walk_capabilities_truncated(self):
        """Test capability list that extends beyond available data."""
        data = [0] * 16
        data[1] = 0x00100000  # Status with cap list bit
        data[13] = 0x00000050  # Cap pointer at 0x50 (beyond data)
        
        capabilities, warning = walk_capabilities(data)
        assert capabilities == []
        assert warning is not None
        assert "0x50" in warning
        assert "beyond" in warning.lower()
    
    def test_walk_capabilities_circular_reference(self):
        """Test handling of circular capability list."""
        data = [0] * 32
        data[1] = 0x00100000  # Status with cap list bit
        data[13] = 0x00000050  # Cap pointer at 0x50
        
        # Circular reference: 0x50 -> 0x60 -> 0x50
        data[20] = 0x00006001  # Cap ID=0x01, Next=0x60
        data[24] = 0x00005005  # Cap ID=0x05, Next=0x50 (circular!)
        
        capabilities, warning = walk_capabilities(data)
        # Should detect loop and stop
        assert len(capabilities) == 2
    
    def test_walk_capabilities_misaligned(self):
        """Test handling of misaligned capability pointer."""
        data = [0] * 32
        data[1] = 0x00100000  # Status with cap list bit
        data[13] = 0x00000050  # Cap pointer at 0x50
        
        # First cap points to misaligned address
        data[20] = 0x00006201  # Cap ID=0x01, Next=0x62 (misaligned!)
        
        capabilities, warning = walk_capabilities(data)
        assert len(capabilities) == 1
        assert warning is not None
        assert "0x62" in warning
        assert "misaligned" in warning.lower()
    
    def test_walk_capabilities_insufficient_data(self):
        """Test with insufficient data for capability pointer."""
        data = [0] * 5  # Not enough data for offset 0x34
        
        capabilities, warning = walk_capabilities(data)
        assert capabilities == []
        assert warning is None


class TestPcieCapabilityDecoding:
    """Test PCIe capability decoding."""
    
    def test_decode_pcie_capability_endpoint(self):
        """Test decoding PCIe endpoint capability."""
        data = [0] * 32
        
        # PCIe cap at offset 0x50 (index 20)
        data[20] = 0x00020010  # Cap ID=0x10, Version=2, Type=0 (Endpoint)
        data[23] = 0x10011051  # Link cap: width=5 (x5), speed=1 (Gen1)
        
        result = decode_pcie_capability(data, 0x50)
        assert result is not None
        assert "Endpoint" in result
        assert "x5" in result
        assert "2.5 GT/s" in result or "Gen 1" in result
    
    def test_decode_pcie_capability_root_port(self):
        """Test decoding Root Port capability."""
        data = [0] * 32
        
        # PCIe cap at offset 0x50
        data[20] = 0x00420010  # Cap ID=0x10, Version=2, Type=4 (Root Port)
        data[23] = 0x20021051  # Link cap: width=5 (x5), speed=1 (Gen1)
        
        result = decode_pcie_capability(data, 0x50)
        assert result is not None
        assert "Root Port" in result
        assert "x5" in result
        assert "2.5 GT/s" in result or "Gen 1" in result
    
    def test_decode_pcie_capability_switch_port(self):
        """Test decoding Switch Port capability."""
        data = [0] * 32
        
        # Downstream Switch Port
        data[20] = 0x00620010  # Type=6 (Downstream Switch Port)
        data[23] = 0x40041051  # Link cap: width=5 (x5), speed=1 (Gen1)
        
        result = decode_pcie_capability(data, 0x50)
        assert result is not None
        assert "Switch Port" in result
        assert "x5" in result
        assert "2.5 GT/s" in result or "Gen 1" in result
    
    def test_decode_pcie_capability_without_link_caps(self):
        """Test decoding when config space is too short for full capability."""
        # Note: Due to the function's logic, if there's enough data for the
        # capability header (offset + 0x10), there's always enough for link caps (offset + 0x0C).
        # So this test verifies the function returns None when data is too short.
        data = [0] * 22  # Not enough data
        
        data[20] = 0x00020010  # PCIe cap at offset 0x50 (index 20)
        # offset 0x50 + 0x10 = 0x60 = 96 bytes needed
        # But we only have 22 * 4 = 88 bytes
        
        result = decode_pcie_capability(data, 0x50)
        # Should return None because data is too short
        assert result is None
    
    def test_decode_pcie_capability_out_of_bounds(self):
        """Test decoding when capability is out of bounds."""
        data = [0] * 10
        
        result = decode_pcie_capability(data, 0x50)
        assert result is None
    
    def test_decode_pcie_capability_gen4_gen5(self):
        """Test decoding Gen4 and Gen5 speeds."""
        data = [0] * 32
        
        # Gen4: speed=4 at bits [3:0]
        data[20] = 0x00020010
        data[23] = 0x10011054  # Link cap: width=5 (x5), speed=4 (Gen4)
        
        result = decode_pcie_capability(data, 0x50)
        assert result is not None
        assert "16.0 GT/s" in result or "Gen 4" in result
        
        # Gen5: speed=5 at bits [3:0]
        data[23] = 0x10011055  # Link cap: width=5 (x5), speed=5 (Gen5)
        result = decode_pcie_capability(data, 0x50)
        assert result is not None
        assert "32.0 GT/s" in result or "Gen 5" in result


class TestIntegration:
    """Integration tests for end-to-end functionality."""
    
    def test_full_parse_and_walk(self, tmp_path):
        """Test complete flow: parse COE and walk capabilities."""
        coe_file = tmp_path / "integrated.coe"
        coe_file.write_text("""
memory_initialization_radix=16;
memory_initialization_vector=
8086100E,
00100007,
02000003,
00000010,
00000004,
00000000,00000000,00000000,
00000000,00000000,00000000,
80861000,
00000000,
00000050,
00000000,
0000010B,
00000000,00000000,00000000,00000000,
05006001,
00000000,00000000,00000000,
00011011;
""")
        
        data = parse_coe_file(coe_file)
        assert data is not None
        
        # Check endianness
        warning = check_endianness(data)
        assert warning is None  # Valid Intel vendor ID
        
        # Walk capabilities
        capabilities, cap_warning = walk_capabilities(data)
        assert len(capabilities) == 2
        assert capabilities[0][1] == 0x01  # Power Management
        assert capabilities[1][1] == 0x11  # MSI-X


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
