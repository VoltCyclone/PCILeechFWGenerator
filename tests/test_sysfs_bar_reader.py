#!/usr/bin/env python3
"""Tests for sysfs BAR reader module."""

import os
import struct
import tempfile
from pathlib import Path
from unittest.mock import MagicMock, mock_open, patch

import pytest

from src.device_clone.sysfs_bar_reader import BarInfo, SysfsBarReader


class TestBarInfo:
    """Test BarInfo dataclass."""

    def test_bar_info_creation(self):
        """Test creating a BarInfo instance."""
        bar = BarInfo(
            index=0,
            start=0xF0000000,
            end=0xF0000FFF,
            size=0x1000,
            flags=0x40200,
            is_io=False,
            is_64bit=False,
        )

        assert bar.index == 0
        assert bar.start == 0xF0000000
        assert bar.end == 0xF0000FFF
        assert bar.size == 0x1000
        assert not bar.is_io
        assert not bar.is_64bit


class TestSysfsBarReader:
    """Test SysfsBarReader functionality."""

    @pytest.fixture
    def mock_sysfs_path(self, tmp_path):
        """Create a mock sysfs device directory."""
        device_bdf = "0000:03:00.0"
        device_path = tmp_path / "sys" / "bus" / "pci" / "devices" / device_bdf
        device_path.mkdir(parents=True, exist_ok=True)
        return device_path

    @pytest.fixture
    def valid_resource_content(self):
        """Provide valid resource file content."""
        return (
            "0x00000000f0000000 0x00000000f0000fff 0x0000000000040200\n"
            "0x00000000f0001000 0x00000000f0001fff 0x0000000000040200\n"
            "0x0000000000000000 0x0000000000000000 0x0000000000000000\n"
            "0x0000000000000000 0x0000000000000000 0x0000000000000000\n"
            "0x0000000000000000 0x0000000000000000 0x0000000000000000\n"
            "0x0000000000000000 0x0000000000000000 0x0000000000000000\n"
        )

    def test_init_device_not_found(self):
        """Test initialization with nonexistent device."""
        with pytest.raises(FileNotFoundError, match="not found in sysfs"):
            SysfsBarReader("0000:99:99.9")

    @patch("pathlib.Path.exists")
    def test_init_success(self, mock_exists):
        """Test successful initialization."""
        mock_exists.return_value = True
        reader = SysfsBarReader("0000:03:00.0")
        assert reader.device_bdf == "0000:03:00.0"
        assert str(reader.sysfs_path).endswith("0000:03:00.0")

    @patch("pathlib.Path.exists")
    @patch("builtins.open", new_callable=mock_open)
    def test_get_bar_info_success(self, mock_file, mock_exists):
        """Test reading BAR info successfully."""
        mock_exists.return_value = True
        resource_content = (
            "0x00000000f0000000 0x00000000f0000fff 0x0000000000040200\n"
        )
        mock_file.return_value.readlines.return_value = resource_content.splitlines(
            keepends=True
        )

        reader = SysfsBarReader("0000:03:00.0")
        bar_info = reader.get_bar_info(0)

        assert bar_info is not None
        assert bar_info.index == 0
        assert bar_info.start == 0xF0000000
        assert bar_info.end == 0xF0000FFF
        assert bar_info.size == 0x1000
        assert bar_info.flags == 0x40200
        assert not bar_info.is_io  # Bit 0 clear
        assert not bar_info.is_64bit

    @patch("pathlib.Path.exists")
    @patch("builtins.open", new_callable=mock_open)
    def test_get_bar_info_io_bar(self, mock_file, mock_exists):
        """Test detecting I/O BAR correctly."""
        mock_exists.return_value = True
        # I/O BAR has bit 0 set in flags
        resource_content = (
            "0x0000000000001000 0x00000000000010ff 0x0000000000040101\n"
        )
        mock_file.return_value.readlines.return_value = resource_content.splitlines(
            keepends=True
        )

        reader = SysfsBarReader("0000:03:00.0")
        bar_info = reader.get_bar_info(0)

        assert bar_info is not None
        assert bar_info.is_io
        assert not bar_info.is_64bit

    @patch("pathlib.Path.exists")
    @patch("builtins.open", new_callable=mock_open)
    def test_get_bar_info_empty_bar(self, mock_file, mock_exists):
        """Test handling empty/unpopulated BAR."""
        mock_exists.return_value = True
        resource_content = (
            "0x0000000000000000 0x0000000000000000 0x0000000000000000\n"
        )
        mock_file.return_value.readlines.return_value = resource_content.splitlines(
            keepends=True
        )

        reader = SysfsBarReader("0000:03:00.0")
        bar_info = reader.get_bar_info(0)

        assert bar_info is None

    @patch("pathlib.Path.exists")
    @patch("builtins.open", new_callable=mock_open)
    def test_get_bar_info_out_of_range(self, mock_file, mock_exists):
        """Test requesting BAR index beyond available BARs."""
        mock_exists.return_value = True
        resource_content = (
            "0x00000000f0000000 0x00000000f0000fff 0x0000000000040200\n"
        )
        mock_file.return_value.readlines.return_value = resource_content.splitlines(
            keepends=True
        )

        reader = SysfsBarReader("0000:03:00.0")
        bar_info = reader.get_bar_info(5)  # Only 1 BAR in file

        assert bar_info is None

    @patch("pathlib.Path.exists")
    @patch("builtins.open", new_callable=mock_open)
    def test_list_bars(self, mock_file, mock_exists):
        """Test listing all present BARs."""
        mock_exists.return_value = True
        resource_content = (
            "0x00000000f0000000 0x00000000f0000fff 0x0000000000040200\n"
            "0x00000000f0001000 0x00000000f0001fff 0x0000000000040200\n"
            "0x0000000000000000 0x0000000000000000 0x0000000000000000\n"
        )
        mock_file.return_value.readlines.return_value = resource_content.splitlines(
            keepends=True
        )

        reader = SysfsBarReader("0000:03:00.0")
        bars = reader.list_bars()

        assert len(bars) == 2
        assert bars[0].index == 0
        assert bars[0].size == 0x1000
        assert bars[1].index == 1
        assert bars[1].size == 0x1000

    @patch("pathlib.Path.exists")
    @patch("builtins.open", new_callable=mock_open)
    def test_enable_memory_decoding_success(self, mock_file, mock_exists):
        """Test enabling memory decoding."""
        mock_exists.return_value = True

        reader = SysfsBarReader("0000:03:00.0")
        result = reader.enable_memory_decoding()

        assert result
        mock_file.assert_called()
        mock_file.return_value.write.assert_called_with("1")

    @patch("pathlib.Path.exists")
    @patch("builtins.open", new_callable=mock_open)
    def test_enable_memory_decoding_no_file(self, mock_file, mock_exists):
        """Test enabling when enable file doesn't exist."""
        # sysfs_path exists (for init), enable file check returns False
        call_count = [0]

        def exists_side_effect(*args, **kwargs):
            call_count[0] += 1
            # First call: sysfs path check in __init__ (return True)
            # Second call: enable file check (return False)
            if call_count[0] == 1:
                return True
            return False

        mock_exists.side_effect = exists_side_effect

        reader = SysfsBarReader("0000:03:00.0")
        result = reader.enable_memory_decoding()

        assert not result

    @patch("pathlib.Path.exists")
    @patch("os.open")
    @patch("os.close")
    @patch("mmap.mmap")
    def test_read_bar_bytes_success(
        self, mock_mmap, mock_close, mock_open, mock_exists
    ):
        """Test reading bytes from BAR."""
        mock_exists.return_value = True
        test_data = b"\x00" * 256 + b"\xFF" * 256
        mock_mm = MagicMock()
        mock_mm.__enter__.return_value = mock_mm
        mock_mm.__getitem__.return_value = test_data[:128]
        mock_mmap.return_value = mock_mm
        mock_open.return_value = 3  # File descriptor

        # Mock get_bar_info
        with patch.object(
            SysfsBarReader,
            "get_bar_info",
            return_value=BarInfo(
                index=0,
                start=0xF0000000,
                end=0xF00001FF,
                size=512,
                flags=0x40200,
                is_io=False,
                is_64bit=False,
            ),
        ):
            with patch.object(SysfsBarReader, "enable_memory_decoding"):
                reader = SysfsBarReader("0000:03:00.0")
                data = reader.read_bar_bytes(0, offset=0, length=128)

                assert data == test_data[:128]
                mock_open.assert_called_once()
                mock_close.assert_called_once()

    @patch("pathlib.Path.exists")
    def test_read_bar_bytes_io_bar(self, mock_exists):
        """Test reading from I/O BAR (should fail)."""
        mock_exists.return_value = True

        with patch.object(
            SysfsBarReader,
            "get_bar_info",
            return_value=BarInfo(
                index=0,
                start=0x1000,
                end=0x10FF,
                size=256,
                flags=0x40101,  # I/O BAR
                is_io=True,
                is_64bit=False,
            ),
        ):
            reader = SysfsBarReader("0000:03:00.0")
            data = reader.read_bar_bytes(0)

            assert data is None

    @patch("pathlib.Path.exists")
    def test_read_bar_bytes_invalid_offset(self, mock_exists):
        """Test reading with invalid offset."""
        mock_exists.return_value = True

        with patch.object(
            SysfsBarReader,
            "get_bar_info",
            return_value=BarInfo(
                index=0,
                start=0xF0000000,
                end=0xF00000FF,
                size=256,
                flags=0x40200,
                is_io=False,
                is_64bit=False,
            ),
        ):
            reader = SysfsBarReader("0000:03:00.0")

            with pytest.raises(ValueError, match="out of range"):
                reader.read_bar_bytes(0, offset=512)  # Beyond BAR size

    @patch("pathlib.Path.exists")
    def test_read_bar_dword_success(self, mock_exists):
        """Test reading a DWORD from BAR."""
        mock_exists.return_value = True
        test_value = 0x12345678
        test_bytes = struct.pack("<I", test_value)

        with patch.object(
            SysfsBarReader, "read_bar_bytes", return_value=test_bytes
        ):
            reader = SysfsBarReader("0000:03:00.0")
            value = reader.read_bar_dword(0, 0)

            assert value == test_value

    @patch("pathlib.Path.exists")
    def test_read_bar_dword_unaligned(self, mock_exists):
        """Test reading DWORD with unaligned offset."""
        mock_exists.return_value = True

        reader = SysfsBarReader("0000:03:00.0")

        with pytest.raises(ValueError, match="not 4-byte aligned"):
            reader.read_bar_dword(0, 3)  # Not 4-byte aligned

    @patch("pathlib.Path.exists")
    def test_sample_bar_registers(self, mock_exists):
        """Test sampling register values from BAR."""
        mock_exists.return_value = True

        # Mock BAR info
        with patch.object(
            SysfsBarReader,
            "get_bar_info",
            return_value=BarInfo(
                index=0,
                start=0xF0000000,
                end=0xF0000FFF,
                size=0x1000,
                flags=0x40200,
                is_io=False,
                is_64bit=False,
            ),
        ):
            # Mock read_bar_dword to return predictable values
            def mock_read(bar_idx, offset):
                return 0x1000 + offset

            with patch.object(
                SysfsBarReader, "read_bar_dword", side_effect=mock_read
            ):
                reader = SysfsBarReader("0000:03:00.0")
                samples = reader.sample_bar_registers(
                    0, offsets=[0x0, 0x4, 0x8, 0xC]
                )

                assert len(samples) == 4
                assert samples[0x0] == 0x1000
                assert samples[0x4] == 0x1004
                assert samples[0x8] == 0x1008
                assert samples[0xC] == 0x100C

    @patch("pathlib.Path.exists")
    @patch("builtins.open", new_callable=mock_open)
    def test_dump_bar_to_file(self, mock_file, mock_exists):
        """Test dumping BAR to file."""
        mock_exists.return_value = True
        test_data = b"\xAB" * 1024

        with patch.object(SysfsBarReader, "read_bar_bytes", return_value=test_data):
            reader = SysfsBarReader("0000:03:00.0")
            output_path = Path("/tmp/bar0_dump.bin")

            result = reader.dump_bar_to_file(0, output_path)

            assert result
            mock_file.return_value.write.assert_called_with(test_data)


class TestSysfsBarReaderIntegration:
    """Integration tests (require actual sysfs or mocking)."""

    @pytest.mark.skipif(
        not os.path.exists("/sys/bus/pci/devices"),
        reason="Requires sysfs (Linux only)",
    )
    def test_real_sysfs_read(self):
        """Test reading from real sysfs if available."""
        # This test will only run on Linux with actual PCI devices
        devices = list(Path("/sys/bus/pci/devices").iterdir())
        if not devices:
            pytest.skip("No PCI devices found")

        # Try first device
        device_bdf = devices[0].name
        try:
            reader = SysfsBarReader(device_bdf)
            bars = reader.list_bars()
            # Just verify we can list BARs without crashing
            assert isinstance(bars, list)
        except PermissionError:
            pytest.skip("Insufficient permissions for sysfs access")
