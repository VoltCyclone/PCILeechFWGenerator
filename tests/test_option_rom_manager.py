#!/usr/bin/env python3
"""
Unit tests for Option ROM Manager
"""

import os
import tempfile
from pathlib import Path
from unittest.mock import MagicMock, mock_open, patch

import pytest

from src.file_management.option_rom_manager import (
    OptionROMError,
    OptionROMExtractionError,
    OptionROMManager,
)


class TestOptionROMError:
    """Test Option ROM error classes"""

    def test_option_rom_error_basic(self):
        """Test basic OptionROMError"""
        error = OptionROMError("Test error")
        assert str(error) == "Test error"

    def test_option_rom_error_with_context(self):
        """Test OptionROMError with context"""
        error = OptionROMError(
            "Test error", rom_path="/path/to/rom", device_bdf="0000:03:00.0"
        )
        assert "Test error" in str(error)
        assert "/path/to/rom" in str(error)
        assert "0000:03:00.0" in str(error)

    def test_option_rom_extraction_error(self):
        """Test OptionROMExtractionError inherits properly"""
        error = OptionROMExtractionError("Extraction failed", device_bdf="0000:03:00.0")
        assert "Extraction failed" in str(error)
        assert isinstance(error, OptionROMError)


class TestOptionROMManager:
    """Test Option ROM Manager functionality"""

    def test_init_default_output_dir(self):
        """Test initialization with default output directory"""
        manager = OptionROMManager()
        assert manager.output_dir is not None
        assert isinstance(manager.output_dir, Path)
        assert manager.rom_file_path is None
        assert manager.rom_size == 0
        assert manager.rom_data is None

    def test_init_custom_output_dir(self):
        """Test initialization with custom output directory"""
        with tempfile.TemporaryDirectory() as tmpdir:
            manager = OptionROMManager(output_dir=tmpdir)
            assert manager.output_dir == Path(tmpdir)

    def test_init_with_rom_file_path(self):
        """Test initialization with ROM file path"""
        manager = OptionROMManager(rom_file_path="/path/to/rom.bin")
        assert manager.rom_file_path == "/path/to/rom.bin"

    def test_load_rom_file_missing_path(self):
        """Test loading ROM file without path specified"""
        manager = OptionROMManager()
        with pytest.raises(OptionROMError, match="No ROM file path specified"):
            manager.load_rom_file()

    def test_load_rom_file_not_found(self):
        """Test loading non-existent ROM file"""
        manager = OptionROMManager(rom_file_path="/nonexistent/rom.bin")
        with pytest.raises(OptionROMError, match="ROM file not found"):
            manager.load_rom_file()

    def test_load_rom_file_success(self):
        """Test successfully loading a ROM file"""
        with tempfile.NamedTemporaryFile(mode="wb", delete=False) as tmp:
            rom_data = b"\x55\xAA" + b"\x00" * 510  # Valid ROM signature + data
            tmp.write(rom_data)
            tmp_path = tmp.name

        try:
            manager = OptionROMManager(rom_file_path=tmp_path)
            result = manager.load_rom_file()
            assert result is True
            assert manager.rom_data == rom_data
            assert manager.rom_size == len(rom_data)
        finally:
            os.unlink(tmp_path)

    def test_get_rom_info_no_data(self):
        """Test get_rom_info with no ROM data"""
        manager = OptionROMManager()
        info = manager.get_rom_info()
        assert info["rom_size"] == "0"
        assert info["rom_file"] == ""

    def test_get_rom_info_with_valid_signature(self):
        """Test get_rom_info with valid ROM signature"""
        manager = OptionROMManager()
        manager.rom_data = b"\x55\xAA\x02" + b"\x00" * 509  # Valid signature + size
        manager.rom_size = len(manager.rom_data)
        
        info = manager.get_rom_info()
        assert info["rom_size"] == str(manager.rom_size)
        assert info["valid_signature"] == "True"
        assert info["rom_size_from_header"] == "1024"  # 2 blocks * 512

    def test_get_rom_info_with_invalid_signature(self):
        """Test get_rom_info with invalid ROM signature"""
        manager = OptionROMManager()
        manager.rom_data = b"\x00\x00" + b"\x00" * 510  # Invalid signature
        manager.rom_size = len(manager.rom_data)
        
        info = manager.get_rom_info()
        assert info["valid_signature"] == "False"

    def test_save_rom_hex_no_data(self):
        """Test save_rom_hex with no ROM data"""
        with tempfile.TemporaryDirectory() as tmpdir:
            manager = OptionROMManager(output_dir=tmpdir)
            with pytest.raises(OptionROMError, match="No ROM file path specified"):
                manager.save_rom_hex()

    def test_save_rom_hex_success(self):
        """Test successfully saving ROM data as hex"""
        with tempfile.TemporaryDirectory() as tmpdir:
            manager = OptionROMManager(output_dir=tmpdir)
            # Simple 8-byte ROM data
            manager.rom_data = b"\x55\xAA\x00\x00\x11\x22\x33\x44"
            manager.rom_size = len(manager.rom_data)
            
            hex_path = os.path.join(tmpdir, "test.hex")
            result = manager.save_rom_hex(hex_path)
            
            assert result is True
            assert os.path.exists(hex_path)
            
            # Verify hex file content (little-endian 32-bit words)
            with open(hex_path, "r") as f:
                lines = f.readlines()
                assert len(lines) == 2  # 8 bytes = 2 words
                assert lines[0].strip() == "0000aa55"  # First 4 bytes, little-endian
                assert lines[1].strip() == "44332211"  # Second 4 bytes, little-endian

    def test_save_rom_hex_padding(self):
        """Test save_rom_hex pads incomplete words correctly"""
        with tempfile.TemporaryDirectory() as tmpdir:
            manager = OptionROMManager(output_dir=tmpdir)
            # 5 bytes - requires padding
            manager.rom_data = b"\x55\xAA\x00\x00\x11"
            manager.rom_size = len(manager.rom_data)
            
            hex_path = os.path.join(tmpdir, "test.hex")
            result = manager.save_rom_hex(hex_path)
            
            assert result is True
            with open(hex_path, "r") as f:
                lines = f.readlines()
                assert len(lines) == 2  # 5 bytes rounds up to 2 words
                assert lines[0].strip() == "0000aa55"
                assert lines[1].strip() == "00000011"  # Padded with zeros

    def test_extract_rom_linux_invalid_bdf(self):
        """Test ROM extraction with invalid BDF format"""
        manager = OptionROMManager()
        
        # Test various invalid BDF formats
        invalid_bdfs = [
            "invalid",
            "03:00.0",  # Missing domain
            "0000:03:00:0",  # Wrong separator
            "0000:03:00.8",  # Invalid function (>7)
            "0000:gg:00.0",  # Invalid hex
        ]
        
        for bdf in invalid_bdfs:
            with pytest.raises(OptionROMExtractionError, match="Invalid BDF format"):
                manager.extract_rom_linux(bdf)

    def test_extract_rom_linux_valid_bdf_format(self):
        """Test that valid BDF formats are accepted"""
        manager = OptionROMManager()
        
        valid_bdfs = [
            "0000:03:00.0",
            "0000:ff:1f.7",
            "FFFF:FF:FF.7",
            "0000:00:00.0",
        ]
        
        for bdf in valid_bdfs:
            # Should return False for device not found, not raise invalid format
            success, _ = manager.extract_rom_linux(bdf)
            assert success is False

    @patch("os.path.exists")
    def test_extract_rom_linux_device_not_found(self, mock_exists):
        """Test ROM extraction when device doesn't exist"""
        mock_exists.return_value = False
        manager = OptionROMManager()
        
        success, path = manager.extract_rom_linux("0000:03:00.0")
        assert success is False
        assert path == ""

    @patch("os.path.exists")
    @patch("builtins.open", new_callable=mock_open)
    def test_extract_rom_linux_rom_enable_fails(self, mock_file, mock_exists):
        """Test ROM extraction when enabling ROM fails"""
        mock_exists.return_value = True
        mock_file.side_effect = OSError("Permission denied")
        
        manager = OptionROMManager()
        success, path = manager.extract_rom_linux("0000:03:00.0")
        assert success is False
        assert path == ""

    def test_setup_option_rom_extraction_failure(self):
        """Test setup_option_rom when extraction fails"""
        with tempfile.TemporaryDirectory() as tmpdir:
            manager = OptionROMManager(output_dir=tmpdir)
            
            with pytest.raises(OptionROMError):
                manager.setup_option_rom("invalid-bdf", use_existing_rom=False)

    def test_setup_option_rom_with_existing_file(self):
        """Test setup_option_rom with existing ROM file"""
        with tempfile.TemporaryDirectory() as tmpdir:
            # Create a ROM file
            rom_path = os.path.join(tmpdir, "donor.rom")
            rom_data = b"\x55\xAA\x02" + b"\x00" * 1021  # Valid ROM
            with open(rom_path, "wb") as f:
                f.write(rom_data)
            
            manager = OptionROMManager(output_dir=tmpdir, rom_file_path=rom_path)
            info = manager.setup_option_rom("0000:03:00.0", use_existing_rom=True)
            
            assert info["valid_signature"] == "True"
            assert info["rom_size"] == str(len(rom_data))
            
            # Check hex file was created
            hex_path = os.path.join(tmpdir, "rom_init.hex")
            assert os.path.exists(hex_path)


class TestOptionROMManagerEdgeCases:
    """Test edge cases and error conditions"""

    def test_empty_rom_data_handling(self):
        """Test handling of empty ROM data"""
        manager = OptionROMManager()
        manager.rom_data = b""
        manager.rom_size = 0
        
        info = manager.get_rom_info()
        assert "valid_signature" not in info

    def test_rom_data_too_small_for_signature(self):
        """Test ROM data smaller than signature"""
        manager = OptionROMManager()
        manager.rom_data = b"\x55"  # Only 1 byte
        manager.rom_size = 1
        
        info = manager.get_rom_info()
        assert "valid_signature" not in info

    def test_rom_data_no_size_header(self):
        """Test ROM data without size header"""
        manager = OptionROMManager()
        manager.rom_data = b"\x55\xAA"  # Only signature, no size
        manager.rom_size = 2
        
        info = manager.get_rom_info()
        assert "rom_size_from_header" not in info

    def test_output_directory_creation(self):
        """Test that output directory is created if it doesn't exist"""
        with tempfile.TemporaryDirectory() as tmpdir:
            nested_dir = os.path.join(tmpdir, "nested", "output")
            manager = OptionROMManager(output_dir=nested_dir)
            manager.rom_data = b"\x55\xAA\x00\x00"
            manager.rom_size = 4
            
            result = manager.save_rom_hex()
            assert result is True
            assert os.path.exists(nested_dir)
