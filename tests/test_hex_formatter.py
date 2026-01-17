#!/usr/bin/env python3
"""
Test Configuration Space Hex Formatter

Tests for the hex formatter module to ensure proper little-endian formatting
and Vivado-compatible hex file generation.
"""

import tempfile
from pathlib import Path

import pytest

from pcileechfwgenerator.device_clone.hex_formatter import (ConfigSpaceHexFormatter,
                                            create_config_space_hex_file)


class TestConfigSpaceHexFormatter:
    """Test cases for ConfigSpaceHexFormatter."""

    def setup_method(self):
        """Set up test fixtures."""
        self.formatter = ConfigSpaceHexFormatter()

        # Sample configuration space data (first 64 bytes of a typical PCIe device)
        # This represents:
        # 0x00: Vendor ID = 0x8086 (Intel), Device ID = 0x1234
        # 0x04: Command = 0x0006, Status = 0x0010
        # 0x08: Class Code = 0x020000 (Network Controller), Revision = 0x01
        # etc.
        self.sample_config_space = bytes(
            [
                0x86,
                0x80,
                0x34,
                0x12,  # 0x00: VID/DID (little-endian)
                0x06,
                0x00,
                0x10,
                0x00,  # 0x04: Command/Status
                0x01,
                0x00,
                0x00,
                0x02,  # 0x08: Rev/Class
                0x00,
                0x00,
                0x00,
                0x00,  # 0x0C: BIST/Header/Latency/Cache
                0x00,
                0x00,
                0x00,
                0xF0,  # 0x10: BAR0
                0x00,
                0x00,
                0x00,
                0x00,  # 0x14: BAR1
                0x00,
                0x00,
                0x00,
                0xF1,  # 0x18: BAR2
                0x00,
                0x00,
                0x00,
                0x00,  # 0x1C: BAR3
                0x00,
                0x00,
                0x00,
                0x00,  # 0x20: BAR4
                0x00,
                0x00,
                0x00,
                0x00,  # 0x24: BAR5
                0x00,
                0x00,
                0x00,
                0x00,  # 0x28: Cardbus CIS
                0x86,
                0x80,
                0x56,
                0x78,  # 0x2C: Subsys VID/DID
                0x00,
                0x00,
                0x00,
                0x00,  # 0x30: ROM Base
                0x40,
                0x00,
                0x00,
                0x00,  # 0x34: Cap Pointer
                0x00,
                0x00,
                0x00,
                0x00,  # 0x38: Reserved
                0x01,
                0x02,
                0x03,
                0x04,  # 0x3C: Int Line/Pin/Min/Max
            ]
        )

    def test_format_config_space_basic(self):
        """Test basic hex formatting without comments."""
        hex_output = self.formatter.format_config_space_to_hex(
            self.sample_config_space[:16], include_comments=False  # First 16 bytes
        )

        # Expected output: little-endian 32-bit words
        expected_lines = [
            "12348086",  # 0x00: VID/DID
            "00100006",  # 0x04: Command/Status
            "02000001",  # 0x08: Rev/Class
            "00000000",  # 0x0C: BIST/Header/Latency/Cache
        ]

        actual_lines = [line for line in hex_output.split("\n") if line.strip()]
        assert actual_lines == expected_lines

    def test_format_config_space_with_comments(self):
        """Test hex formatting with comments."""
        hex_output = self.formatter.format_config_space_to_hex(
            self.sample_config_space[:16], include_comments=True
        )

        # Check that comments are included
        assert "// config_space_init.hex" in hex_output
        assert "// Offset 0x000 - Device/Vendor ID" in hex_output
        assert "// Offset 0x004 - Status/Command" in hex_output

        # Check hex values
        assert "12348086" in hex_output
        assert "00100006" in hex_output

    def test_little_endian_conversion(self):
        """Test that little-endian conversion is correct."""
        # Test data with known pattern
        test_data = bytes(
            [
                0x01,
                0x02,
                0x03,
                0x04,  # Should become 0x04030201
                0xAA,
                0xBB,
                0xCC,
                0xDD,  # Should become 0xDDCCBBAA
            ]
        )

        hex_output = self.formatter.format_config_space_to_hex(
            test_data, include_comments=False
        )

        lines = hex_output.strip().split("\n")
        assert lines[0] == "04030201"
        assert lines[1] == "DDCCBBAA"

    def test_padding_alignment(self):
        """Test that data is properly padded to 32-bit alignment."""
        # Test with 6 bytes (not aligned to 4)
        unaligned_data = bytes([0x11, 0x22, 0x33, 0x44, 0x55, 0x66])

        hex_output = self.formatter.format_config_space_to_hex(
            unaligned_data, include_comments=False
        )

        lines = hex_output.strip().split("\n")
        assert len(lines) == 2  # Should be padded to 8 bytes = 2 dwords
        assert lines[0] == "44332211"
        assert lines[1] == "00006655"  # Padded with zeros

    def test_full_config_space(self):
        """Test formatting of full 4KB configuration space."""
        # Create 4KB of test data
        full_config = bytearray(4096)

        # Fill with pattern
        for i in range(0, 4096, 4):
            full_config[i : i + 4] = i.to_bytes(4, byteorder="little")

        hex_output = self.formatter.format_config_space_to_hex(
            bytes(full_config), include_comments=True
        )

        lines = hex_output.split("\n")
        hex_lines = [line for line in lines if line and not line.startswith("//")]

        # Should have 1024 hex lines (4096 bytes / 4 bytes per line)
        assert len(hex_lines) == 1024

        # Check first few values
        assert hex_lines[0] == "00000000"
        assert hex_lines[1] == "00000004"
        assert hex_lines[2] == "00000008"

    def test_write_hex_file(self):
        """Test writing hex file to disk."""
        with tempfile.TemporaryDirectory() as tmpdir:
            output_path = Path(tmpdir) / "test_config.hex"

            result_path = self.formatter.write_hex_file(
                self.sample_config_space, output_path, include_comments=True
            )

            assert result_path == output_path
            assert output_path.exists()

            # Read and verify content
            content = output_path.read_text()
            assert "// config_space_init.hex" in content
            assert "12348086" in content  # VID/DID

    def test_validate_hex_file(self):
        """Test hex file validation."""
        with tempfile.TemporaryDirectory() as tmpdir:
            # Create valid hex file
            valid_path = Path(tmpdir) / "valid.hex"
            valid_path.write_text(
                """// Comment line
12348086
00100006
02000001
00000000
"""
            )

            assert self.formatter.validate_hex_file(valid_path) is True

            # Create invalid hex file (wrong length)
            invalid_path1 = Path(tmpdir) / "invalid1.hex"
            invalid_path1.write_text("1234")  # Too short

            assert self.formatter.validate_hex_file(invalid_path1) is False

            # Create invalid hex file (non-hex characters)
            invalid_path2 = Path(tmpdir) / "invalid2.hex"
            invalid_path2.write_text("GHIJKLMN")  # Invalid hex

            assert self.formatter.validate_hex_file(invalid_path2) is False

    def test_convert_to_dword_list(self):
        """Test conversion to dword list."""
        test_data = bytes(
            [
                0x01,
                0x02,
                0x03,
                0x04,
                0xAA,
                0xBB,
                0xCC,
                0xDD,
            ]
        )

        dwords = self.formatter.convert_to_dword_list(test_data)

        assert len(dwords) == 2
        assert dwords[0] == 0x04030201  # Little-endian
        assert dwords[1] == 0xDDCCBBAA

    def test_create_config_space_hex_file_convenience(self):
        """Test convenience function."""
        with tempfile.TemporaryDirectory() as tmpdir:
            output_path = Path(tmpdir) / "convenience_test.hex"

            result = create_config_space_hex_file(
                self.sample_config_space[:16], output_path, include_comments=True
            )

            assert result == output_path
            assert output_path.exists()

            content = output_path.read_text()
            assert "12348086" in content


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
