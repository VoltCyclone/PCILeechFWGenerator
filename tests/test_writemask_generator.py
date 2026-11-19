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
from pcileechfwgenerator.device_clone.writemask_generator import (
    WritemaskGenerator,
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
        """Disabled MSI still grants its full 32-bit layout."""
        assert generator.get_msi_writemask({"enabled": False}) == (
            "00710000",
            "fffffffc",
            "0000ffff",
        )

    def test_get_msi_writemask_64bit(self, generator):
        """64-bit MSI grants both address halves, preserving attributes."""
        assert generator.get_msi_writemask({"64bit_capable": True}) == (
            "00710000",
            "fffffffc",
            "ffffffff",
            "0000ffff",
        )

    def test_get_msi_writemask_multiple_message_capable(self, generator):
        """Only supported vectors can be masked; Pending remains read-only."""
        assert generator.get_msi_writemask(
            {
                "multiple_message_capable": 3,
                "per_vector_masking": True,
            }
        ) == ("00710000", "fffffffc", "0000ffff", "000000ff", "00000000")

    def test_get_msi_writemask_multiple_message_enabled(self, generator):
        """Runtime MME does not change layout or immutable fields."""
        assert generator.get_msi_writemask(
            {
                "enabled": True,
                "multiple_message_enabled": True,
            }
        ) == ("00710000", "fffffffc", "0000ffff")

    def test_get_msix_writemask_small_table(self, generator):
        """Test MSI-X writemask for small table (<=8 entries)."""
        msix_config = {"table_size": 4}
        result = generator.get_msix_writemask(msix_config)
        assert result == ("c0000000", "00000000", "00000000")

    def test_get_msix_writemask_medium_table(self, generator):
        """Test MSI-X writemask for medium table (16 entries)."""
        msix_config = {"table_size": 16}
        result = generator.get_msix_writemask(msix_config)
        assert result == ("c0000000", "00000000", "00000000")

    def test_get_msix_writemask_large_table(self, generator):
        """Test MSI-X writemask for large table (>128 entries)."""
        msix_config = {"table_size": 256}
        result = generator.get_msix_writemask(msix_config)
        assert result == ("c0000000", "00000000", "00000000")

    # ========================================================================
    # COE File Parsing Tests
    # ========================================================================

    def test_read_cfg_space_basic(self, generator, sample_config_space):
        """Test basic COE file parsing."""
        result = generator.read_cfg_space(sample_config_space)
        assert len(result) == 16
        assert result[0] == 0x10DE1234
        assert result[13] == 0x50

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
        assert result == {0: 0x12345678, 1: 0xABCDEF00, 2: 0x11111111}

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
        assert result == {0: 0x12345678, 1: 0xABCDEF00}

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
        assert result == {}

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
        assert result == {"0x01": 0x40, "0x02": 0x50}

    def test_locate_capabilities_extended(self, generator):
        """Extended next pointers encode byte offsets directly."""
        dwords = {0x100 // 4: 0x14010001, 0x140 // 4: 0x00010003}
        assert generator.locate_capabilities(dwords) == {
            "0x0001": 0x100,
            "0x0003": 0x140,
        }

    def test_locate_capabilities_ext_cycle_detection(self, generator):
        """An extended cycle terminates after discovering both entries."""
        dwords = {0x100 // 4: 0x14010001, 0x140 // 4: 0x10010002}
        assert generator.locate_capabilities(dwords) == {
            "0x0001": 0x100,
            "0x0002": 0x140,
        }

    # ========================================================================
    # COE File Writing Tests
    # ========================================================================

    def test_write_writemask_coe_format(self, generator, tmp_path):
        """Test writemask COE file format."""
        output_file = tmp_path / "writemask.coe"
        wr_mask = [0xFFFFFFFF] * 100

        generator._write_writemask_coe(wr_mask, output_file)

        content = output_file.read_text()
        assert "memory_initialization_radix=16" in content
        assert "memory_initialization_vector=" in content
        assert "PCILeech" in content
        assert content.endswith("00000000,00000000,00000000,00000000;\n")
        assert list(generator.read_cfg_space(output_file).values()) == (
            [0xFFFFFFFF] * 100 + [0] * 924
        )

    def test_write_writemask_coe_padding(self, generator, tmp_path):
        """Short masks pad with read-only words, never writable defaults."""
        output_file = tmp_path / "writemask.coe"
        generator._write_writemask_coe([0x00000546] * 50, output_file)
        words = list(generator.read_cfg_space(output_file).values())
        assert words == [0x00000546] * 50 + [0] * 974

    def test_write_writemask_coe_truncation(self, generator, tmp_path):
        """Output truncates at the last DWORD of 4KB configuration space."""
        output_file = tmp_path / "writemask.coe"
        generator._write_writemask_coe([0x000000FF] * 2000, output_file)
        assert list(generator.read_cfg_space(output_file).values()) == [0xFF] * 1024

    # ========================================================================
    # Integration Tests
    # ========================================================================

    def test_generate_writemask_full_flow(
        self, generator, sample_config_space, tmp_path
    ):
        """Test full writemask generation flow."""
        output_file = tmp_path / "writemask_output.coe"

        generator.generate_writemask(
            sample_config_space, output_file, device_config=None
        )

        words = generator.read_cfg_space(output_file)
        assert len(words) == 1024
        assert [words[i] for i in (0, 1, 2, 3, 11, 13, 15)] == [
            0,
            0x546,
            0,
            0xFF,
            0,
            0,
            0xFF,
        ]
        assert all(words[i] == 0 for i in range(16, 1024))

    def test_generate_writemask_with_msi_config(
        self, generator, sample_config_space, tmp_path
    ):
        """Test writemask generation with MSI configuration."""
        output_file = tmp_path / "writemask_msi.coe"
        device_config = {"msi_config": {"enabled": True, "64bit_capable": True}}

        generator.generate_writemask(
            sample_config_space, output_file, device_config=device_config
        )

        words = generator.read_cfg_space(output_file)
        assert words[0] == 0
        assert words[1] == 0x546
        assert words[15] == 0xFF
        assert all(words[i] == 0 for i in range(16, 1024))

    def test_generate_writemask_with_msix_config(
        self, generator, sample_config_space, tmp_path
    ):
        """Test writemask generation with MSI-X configuration."""
        output_file = tmp_path / "writemask_msix.coe"
        device_config = {"msix_config": {"table_size": 64}}

        generator.generate_writemask(
            sample_config_space, output_file, device_config=device_config
        )

        words = generator.read_cfg_space(output_file)
        assert words[0] == 0
        assert words[1] == 0x546
        assert words[15] == 0xFF
        assert all(words[i] == 0 for i in range(16, 1024))

    def test_generate_writemask_visualization_disabled(
        self, generator, sample_config_space, tmp_path
    ):
        """Test writemask generation without visualization."""
        output_file = tmp_path / "writemask_no_viz.coe"

        # Should not raise even if rich not available
        generator.generate_writemask(sample_config_space, output_file, visualize=False)

        words = generator.read_cfg_space(output_file)
        assert words[0] == 0
        assert words[1] == 0x546
        assert words[15] == 0xFF
        assert all(words[i] == 0 for i in range(16, 1024))


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
        assert result == {}

    def test_read_cfg_space_malformed(self, generator, tmp_path):
        """Test reading malformed COE file."""
        malformed = tmp_path / "malformed.coe"
        malformed.write_text("not a valid coe file!")

        result = generator.read_cfg_space(malformed)
        # Should return empty or minimal dict, not crash
        assert result == {}

    def test_locate_capabilities_all_zeros(self, generator):
        """Test capability location with all-zero config space."""
        dwords = {i: 0 for i in range(1024)}
        result = generator.locate_capabilities(dwords)
        assert result == {}

    def test_locate_capabilities_all_ones(self, generator):
        """Test capability location with all-ones config space."""
        dwords = {i: 0xFFFFFFFF for i in range(1024)}
        result = generator.locate_capabilities(dwords)
        assert result == {}

    def test_generate_writemask_none_device_config(self, generator, tmp_path):
        """Test writemask generation with None device config."""
        coe_file = tmp_path / "cfg.coe"
        coe_file.write_text("""memory_initialization_radix=16;
memory_initialization_vector=
12345678;
""")
        output_file = tmp_path / "output.coe"

        generator.generate_writemask(coe_file, output_file, device_config=None)

        words = generator.read_cfg_space(output_file)
        assert words[0] == 0
        assert words[1] == 0x546
        assert words[15] == 0xFF
        assert all(words[i] == 0 for i in range(16, 1024))
