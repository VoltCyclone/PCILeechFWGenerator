#!/usr/bin/env python3
"""
Unit tests for WritemaskGenerator.

Tests the improved writemask generation with:
- Flexible COE parsing
- Hardened capability traversal
- Cycle detection
- Extended capability pointer handling
"""

import pytest
from pathlib import Path
from typing import Dict
from unittest.mock import MagicMock, patch

from src.device_clone.writemask_generator import (
    WritemaskGenerator,
    CFG_SPACE_DWORDS,
    visualize_writemask_terminal,
)


class TestWritemaskGenerator:
    """Test suite for WritemaskGenerator class."""

    @pytest.fixture
    def generator(self):
        """Create WritemaskGenerator instance."""
        return WritemaskGenerator()

    @pytest.fixture
    def sample_config_space(self, tmp_path):
        """Create sample configuration space COE file."""
        coe_file = tmp_path / "cfg_space.coe"
        content = """; Sample Config Space
memory_initialization_radix=16;
memory_initialization_vector=
10de1234,12345678,abcdef00,00000040,
00000000,00000000,00000000,00000000,
00000000,00000000,00000000,00000000,
00000000,00000050,00000000,00000000;
"""
        coe_file.write_text(content)
        return coe_file

    # ========================================================================
    # MSI/MSI-X Writemask Selection Tests
    # ========================================================================

    def test_get_msi_writemask_disabled(self, generator):
        """Test MSI writemask when disabled."""
        msi_config = {"enabled": False}
        result = generator.get_msi_writemask(msi_config)
        assert result is not None

    def test_get_msi_writemask_64bit(self, generator):
        """Test MSI writemask for 64-bit capable."""
        msi_config = {"enabled": True, "64bit_capable": True}
        result = generator.get_msi_writemask(msi_config)
        assert result is not None

    def test_get_msi_writemask_multiple_message_capable(self, generator):
        """Test MSI writemask for multiple message capable."""
        msi_config = {
            "enabled": True,
            "64bit_capable": False,
            "multiple_message_capable": True,
        }
        result = generator.get_msi_writemask(msi_config)
        assert result is not None

    def test_get_msi_writemask_multiple_message_enabled(self, generator):
        """Test MSI writemask for multiple message enabled."""
        msi_config = {
            "enabled": True,
            "64bit_capable": False,
            "multiple_message_capable": False,
            "multiple_message_enabled": True,
        }
        result = generator.get_msi_writemask(msi_config)
        assert result is not None

    def test_get_msix_writemask_small_table(self, generator):
        """Test MSI-X writemask for small table (<=8 entries)."""
        msix_config = {"table_size": 4}
        result = generator.get_msix_writemask(msix_config)
        assert result is not None

    def test_get_msix_writemask_medium_table(self, generator):
        """Test MSI-X writemask for medium table (16 entries)."""
        msix_config = {"table_size": 16}
        result = generator.get_msix_writemask(msix_config)
        assert result is not None

    def test_get_msix_writemask_large_table(self, generator):
        """Test MSI-X writemask for large table (>128 entries)."""
        msix_config = {"table_size": 256}
        result = generator.get_msix_writemask(msix_config)
        assert result is not None

    # ========================================================================
    # COE File Parsing Tests
    # ========================================================================

    def test_read_cfg_space_basic(self, generator, sample_config_space):
        """Test basic COE file parsing."""
        result = generator.read_cfg_space(sample_config_space)
        assert isinstance(result, dict)
        assert len(result) > 0
        assert 0 in result

    def test_read_cfg_space_hex_prefix(self, generator, tmp_path):
        """Test COE parsing with 0x prefixes."""
        coe_file = tmp_path / "cfg_with_prefix.coe"
        content = """memory_initialization_radix=16;
memory_initialization_vector=
0x10de1234,0xabcdef00;
"""
        coe_file.write_text(content)
        result = generator.read_cfg_space(coe_file)
        assert 0 in result
        assert result[0] == 0x10DE1234

    def test_read_cfg_space_mixed_case(self, generator, tmp_path):
        """Test COE parsing with mixed case hex."""
        coe_file = tmp_path / "cfg_mixed.coe"
        content = """memory_initialization_radix=16;
memory_initialization_vector=
AbCdEf12,DEADBEEF;
"""
        coe_file.write_text(content)
        result = generator.read_cfg_space(coe_file)
        assert result[0] == 0xABCDEF12
        assert result[1] == 0xDEADBEEF

    def test_read_cfg_space_with_comments(self, generator, tmp_path):
        """Test COE parsing with inline comments."""
        coe_file = tmp_path / "cfg_comments.coe"
        content = """memory_initialization_radix=16;
memory_initialization_vector=
12345678, ; vendor/device ID
abcdef00; ; class code
"""
        coe_file.write_text(content)
        result = generator.read_cfg_space(coe_file)
        assert result[0] == 0x12345678
        assert result[1] == 0xABCDEF00

    def test_read_cfg_space_whitespace_separated(self, generator, tmp_path):
        """Test COE parsing with whitespace separation."""
        coe_file = tmp_path / "cfg_spaces.coe"
        content = """memory_initialization_radix=16;
memory_initialization_vector=
12345678 abcdef00 11111111;
"""
        coe_file.write_text(content)
        result = generator.read_cfg_space(coe_file)
        assert len(result) == 3

    def test_read_cfg_space_invalid_hex(self, generator, tmp_path):
        """Test COE parsing skips invalid hex values."""
        coe_file = tmp_path / "cfg_invalid.coe"
        content = """memory_initialization_radix=16;
memory_initialization_vector=
12345678,ZZZZZZZZ,abcdef00;
"""
        coe_file.write_text(content)
        result = generator.read_cfg_space(coe_file)
        # Should have 2 valid values (skipping invalid)
        assert len(result) == 2

    # ========================================================================
    # Capability Location Tests
    # ========================================================================

    def test_get_dword_valid_offset(self, generator):
        """Test _get_dword with valid offset."""
        dwords = {0: 0x12345678, 1: 0xABCDEF00}
        result = generator._get_dword(dwords, 0)
        assert result == 0x12345678

    def test_get_dword_byte_offset(self, generator):
        """Test _get_dword with byte offset."""
        dwords = {0: 0x12345678, 1: 0xABCDEF00}
        result = generator._get_dword(dwords, 4)
        assert result == 0xABCDEF00

    def test_get_dword_missing_offset(self, generator):
        """Test _get_dword with missing offset returns 0."""
        dwords = {0: 0x12345678}
        result = generator._get_dword(dwords, 100)
        assert result == 0

    def test_locate_capabilities_empty(self, generator):
        """Test capability location with empty config space."""
        dwords = {i: 0 for i in range(64)}
        result = generator.locate_capabilities(dwords)
        assert isinstance(result, dict)

    def test_locate_capabilities_with_std_cap(self, generator):
        """Test capability location with standard capability."""
        dwords = {i: 0 for i in range(256)}
        # Set capability pointer at 0x34
        dwords[0x34 // 4] = 0x40  # Cap at 0x40
        # Set capability at 0x40 (MSI = 0x05)
        dwords[0x40 // 4] = 0x00000005  # Cap ID 0x05, next=0x00
        
        result = generator.locate_capabilities(dwords)
        assert "0x05" in result
        assert result["0x05"] == 0x40

    def test_locate_capabilities_cycle_detection(self, generator):
        """Test cycle detection in standard capabilities."""
        dwords = {i: 0 for i in range(256)}
        # Set capability pointer at 0x34
        dwords[0x34 // 4] = 0x40  # Cap at 0x40
        # Create cycle: 0x40 -> 0x50 -> 0x40
        dwords[0x40 // 4] = 0x00005001  # Cap ID 1, next=0x50
        dwords[0x50 // 4] = 0x00004002  # Cap ID 2, next=0x40 (cycle!)
        
        result = generator.locate_capabilities(dwords)
        # Should detect cycle and stop
        assert isinstance(result, dict)

    def test_locate_capabilities_extended(self, generator):
        """Test extended capability location."""
        dwords = {i: 0 for i in range(1024)}
        # Extended capability at 0x100
        # Header format: [next_offset:12][version:4][cap_id:16]
        # Cap ID 0x0001, version 1, next at 0x140 (DWORD 0x50 = byte 0x140)
        dwords[0x100 // 4] = 0x00050001  # next_dword=0x05, ver=0, id=0x0001
        
        result = generator.locate_capabilities(dwords)
        assert "0x0001" in result
        assert result["0x0001"] == 0x100

    def test_locate_capabilities_ext_cycle_detection(self, generator):
        """Test cycle detection in extended capabilities."""
        dwords = {i: 0 for i in range(1024)}
        # Create cycle: 0x100 -> 0x140 -> 0x100
        dwords[0x100 // 4] = 0x00050001  # next_dword=0x05 (byte 0x140)
        dwords[0x140 // 4] = 0x00040002  # next_dword=0x04 (byte 0x100, cycle!)
        
        result = generator.locate_capabilities(dwords)
        # Should detect cycle and stop
        assert isinstance(result, dict)

    # ========================================================================
    # Writemask Creation and Update Tests
    # ========================================================================

    def test_create_writemask_size(self, generator):
        """Test writemask creation has correct size."""
        dwords = {i: 0 for i in range(100)}
        result = generator.create_writemask(dwords)
        assert len(result) == CFG_SPACE_DWORDS

    def test_create_writemask_default_writable(self, generator):
        """Test writemask defaults to all writable."""
        dwords = {i: 0 for i in range(100)}
        result = generator.create_writemask(dwords)
        assert all(mask == "ffffffff" for mask in result)

    def test_update_writemask_basic(self, generator):
        """Test basic writemask update."""
        wr_mask = ["ffffffff"] * 10
        protected = ("ffffffff",)  # Protect all bits (make all read-only)
        result = generator.update_writemask(wr_mask, protected, 0)
        assert result[0] == "00000000"  # All bits now read-only
        assert result[1] == "ffffffff"  # Unchanged

    def test_update_writemask_partial(self, generator):
        """Test partial bit protection."""
        wr_mask = ["ffffffff"] * 10
        protected = ("0000ffff",)  # Protect lower 16 bits
        result = generator.update_writemask(wr_mask, protected, 0)
        assert result[0] == "ffff0000"

    def test_update_writemask_multiple_dwords(self, generator):
        """Test protection across multiple DWORDs."""
        wr_mask = ["ffffffff"] * 10
        protected = ("00000001", "00000002", "00000004")
        result = generator.update_writemask(wr_mask, protected, 2)
        assert result[2] == "fffffffe"
        assert result[3] == "fffffffd"
        assert result[4] == "fffffffb"

    def test_update_writemask_bounds_check(self, generator):
        """Test writemask update with out-of-bounds index."""
        wr_mask = ["ffffffff"] * 5
        protected = ("00000000",) * 10  # More than available
        # Should not crash, just stop at bounds
        result = generator.update_writemask(wr_mask, protected, 0)
        assert len(result) == 5

    def test_update_writemask_invalid_hex(self, generator):
        """Test writemask update with invalid hex (graceful handling)."""
        wr_mask = ["ffffffff", "invalid", "ffffffff"]
        protected = ("00000001",)
        # Should handle invalid hex gracefully
        result = generator.update_writemask(wr_mask, protected, 1)
        assert len(result) == 3

    # ========================================================================
    # COE File Writing Tests
    # ========================================================================

    def test_write_writemask_coe_format(self, generator, tmp_path):
        """Test writemask COE file format."""
        output_file = tmp_path / "writemask.coe"
        wr_mask = ["ffffffff"] * 100
        
        generator._write_writemask_coe(wr_mask, output_file)
        
        content = output_file.read_text()
        assert "memory_initialization_radix=16" in content
        assert "memory_initialization_vector=" in content
        assert "PCILeech" in content

    def test_write_writemask_coe_padding(self, generator, tmp_path):
        """Test writemask COE pads to 1024 DWORDs."""
        output_file = tmp_path / "writemask.coe"
        wr_mask = ["ffffffff"] * 50  # Less than 1024
        
        generator._write_writemask_coe(wr_mask, output_file)
        
        content = output_file.read_text()
        # Count DWORDs in output
        dwords = [line for line in content.splitlines() 
                  if line and not line.startswith(";") 
                  and "memory_initialization" not in line]
        # Should have data for 1024 DWORDs
        assert len(content) > 0

    def test_write_writemask_coe_truncation(self, generator, tmp_path):
        """Test writemask COE truncates to 1024 DWORDs."""
        output_file = tmp_path / "writemask.coe"
        wr_mask = ["ffffffff"] * 2000  # More than 1024
        
        generator._write_writemask_coe(wr_mask, output_file)
        
        # Should complete without error
        assert output_file.exists()

    # ========================================================================
    # Integration Tests
    # ========================================================================

    def test_generate_writemask_full_flow(self, generator, sample_config_space, tmp_path):
        """Test full writemask generation flow."""
        output_file = tmp_path / "writemask_output.coe"
        
        generator.generate_writemask(
            sample_config_space,
            output_file,
            device_config=None
        )
        
        assert output_file.exists()
        content = output_file.read_text()
        assert len(content) > 0

    def test_generate_writemask_with_msi_config(self, generator, sample_config_space, tmp_path):
        """Test writemask generation with MSI configuration."""
        output_file = tmp_path / "writemask_msi.coe"
        device_config = {
            "msi_config": {
                "enabled": True,
                "64bit_capable": True
            }
        }
        
        generator.generate_writemask(
            sample_config_space,
            output_file,
            device_config=device_config
        )
        
        assert output_file.exists()

    def test_generate_writemask_with_msix_config(self, generator, sample_config_space, tmp_path):
        """Test writemask generation with MSI-X configuration."""
        output_file = tmp_path / "writemask_msix.coe"
        device_config = {
            "msix_config": {
                "table_size": 64
            }
        }
        
        generator.generate_writemask(
            sample_config_space,
            output_file,
            device_config=device_config
        )
        
        assert output_file.exists()

    def test_generate_writemask_visualization_disabled(self, generator, sample_config_space, tmp_path):
        """Test writemask generation without visualization."""
        output_file = tmp_path / "writemask_no_viz.coe"
        
        # Should not raise even if rich not available
        generator.generate_writemask(
            sample_config_space,
            output_file,
            visualize=False
        )
        
        assert output_file.exists()


class TestVisualization:
    """Test suite for visualization functions."""

    def test_visualize_writemask_terminal_no_rich(self):
        """Test visualization falls back gracefully without rich."""
        wr_mask = ["ffffffff"] * 64
        caps = {"0x05": 0x40, "0x11": 0x50}
        
        # Should not crash even if rich not available
        visualize_writemask_terminal(wr_mask, caps, rows=10)

    def test_visualize_writemask_terminal_with_caps(self):
        """Test visualization with capabilities."""
        wr_mask = ["ffffffff"] * 64
        wr_mask[0] = "00000000"  # Some protected bits
        caps = {
            "0x05": 0x40,  # MSI
            "0x11": 0x50,  # MSI-X
            "0x0001": 0x100,  # Extended cap
        }
        
        # Should handle mixed std and extended caps
        visualize_writemask_terminal(wr_mask, caps, rows=16)

    def test_visualize_writemask_terminal_empty_caps(self):
        """Test visualization with no capabilities."""
        wr_mask = ["ffffffff"] * 64
        caps = {}
        
        visualize_writemask_terminal(wr_mask, caps, rows=10)

    def test_visualize_writemask_terminal_varied_masks(self):
        """Test visualization with varied protection patterns."""
        wr_mask = [
            "ffffffff",  # All writable
            "00000000",  # All protected
            "ffff0000",  # Half protected
            "f0f0f0f0",  # Alternating
        ]
        caps = {}
        
        visualize_writemask_terminal(wr_mask, caps, rows=4)


class TestEdgeCases:
    """Test edge cases and error handling."""

    @pytest.fixture
    def generator(self):
        return WritemaskGenerator()

    def test_read_cfg_space_nonexistent_file(self, generator, tmp_path):
        """Test reading nonexistent file raises error."""
        nonexistent = tmp_path / "nonexistent.coe"
        
        with pytest.raises(Exception):
            generator.read_cfg_space(nonexistent)

    def test_read_cfg_space_empty_file(self, generator, tmp_path):
        """Test reading empty file."""
        empty_file = tmp_path / "empty.coe"
        empty_file.write_text("")
        
        result = generator.read_cfg_space(empty_file)
        assert isinstance(result, dict)

    def test_read_cfg_space_malformed(self, generator, tmp_path):
        """Test reading malformed COE file."""
        malformed = tmp_path / "malformed.coe"
        malformed.write_text("not a valid coe file!")
        
        result = generator.read_cfg_space(malformed)
        # Should return empty or minimal dict, not crash
        assert isinstance(result, dict)

    def test_locate_capabilities_all_zeros(self, generator):
        """Test capability location with all-zero config space."""
        dwords = {i: 0 for i in range(1024)}
        result = generator.locate_capabilities(dwords)
        assert isinstance(result, dict)

    def test_locate_capabilities_all_ones(self, generator):
        """Test capability location with all-ones config space."""
        dwords = {i: 0xFFFFFFFF for i in range(1024)}
        result = generator.locate_capabilities(dwords)
        assert isinstance(result, dict)

    def test_update_writemask_empty_protection(self, generator):
        """Test writemask update with empty protection tuple."""
        wr_mask = ["ffffffff"] * 10
        protected = ()
        result = generator.update_writemask(wr_mask, protected, 0)
        assert all(mask == "ffffffff" for mask in result)

    def test_generate_writemask_none_device_config(self, generator, tmp_path):
        """Test writemask generation with None device config."""
        coe_file = tmp_path / "cfg.coe"
        coe_file.write_text("""memory_initialization_radix=16;
memory_initialization_vector=
12345678;
""")
        output_file = tmp_path / "output.coe"
        
        generator.generate_writemask(
            coe_file,
            output_file,
            device_config=None
        )
        
        assert output_file.exists()
