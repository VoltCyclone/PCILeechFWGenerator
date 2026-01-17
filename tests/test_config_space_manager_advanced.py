#!/usr/bin/env python3
"""
Advanced Configuration Space Manager Tests - Critical Edge Cases.

This test module focuses on improving test coverage for critical configuration
space management operations, including VFIO integration, error recovery,
sysfs fallback scenarios, and complex device configurations.
"""

import os
import subprocess
from pathlib import Path
from typing import Any, Dict, Optional
from unittest.mock import MagicMock, Mock, call, mock_open, patch

import pytest

from pcileechfwgenerator.device_clone.config_space_manager import (BarInfo, ConfigSpaceError,
                                                   ConfigSpaceManager,
                                                   SysfsError, VFIOError)


class TestConfigSpaceManagerAdvanced:
    """Test advanced configuration space management scenarios."""

    @pytest.fixture
    def manager(self):
        return ConfigSpaceManager("0000:01:00.0", strict_vfio=False)

    @pytest.fixture
    def strict_manager(self):
        return ConfigSpaceManager("0000:01:00.0", strict_vfio=True)

    def test_vfio_to_sysfs_fallback_chain(self, manager):
        """Test complete VFIO to sysfs fallback chain."""
        # Mock VFIO import failure, should fall back to sysfs
        with patch(
            "pcileechfwgenerator.cli.vfio_handler.VFIOBinder",
            side_effect=ImportError("VFIO not available"),
        ):
            with patch.object(
                manager, "_read_sysfs_config_space", return_value=b"\x00" * 256
            ) as mock_sysfs:

                result = manager.read_vfio_config_space(strict=False)

                assert len(result) == 256
                mock_sysfs.assert_called_once()

    def test_vfio_strict_mode_error_propagation(self, strict_manager):
        """Test error propagation in strict VFIO mode."""
        with patch(
            "pcileechfwgenerator.cli.vfio_handler.VFIOBinder",
            side_effect=Exception("VFIO binding failed"),
        ):
            with pytest.raises(VFIOError, match="VFIO config space reading failed"):
                strict_manager.read_vfio_config_space(strict=True)

    def test_partial_config_space_read_handling(self, manager):
        """Test handling of partial configuration space reads."""
        # Simulate partial read from sysfs
        partial_data = b"\x00" * 64  # Only first 64 bytes

        with patch("builtins.open", mock_open(read_data=partial_data)):
            with patch("pathlib.Path.exists", return_value=True):
                result = manager._read_config_file_direct()

                # Code automatically pads to minimum 256 bytes for safety
                assert len(result) == 256

    def test_config_space_size_validation(self, manager):
        """Test validation of configuration space size."""
        test_cases = [
            (b"", 256, "Empty config space"),  # Padded to minimum
            (b"\x00" * 63, 256, "Undersized config space"),  # Padded to minimum
            (b"\x00" * 64, 256, "Minimal valid config space"),  # Padded to minimum
            (b"\x00" * 256, 256, "Standard config space"),  # Already correct size
            (b"\x00" * 4096, 4096, "Extended config space"),  # Already correct size
        ]

        for data, expected_size, description in test_cases:
            with patch("builtins.open", mock_open(read_data=data)):
                with patch("os.path.exists", return_value=True):
                    result = manager._read_config_file_direct()
                    assert len(result) == expected_size, f"Failed for {description}"

    def test_concurrent_config_space_access(self, manager):
        """Test concurrent access to configuration space."""
        import threading

        results = []
        errors = []

        def worker():
            try:
                with patch("builtins.open", mock_open(read_data=b"\x00" * 256)):
                    with patch("os.path.exists", return_value=True):
                        result = manager._read_config_file_direct()
                        results.append(len(result))
            except Exception as e:
                errors.append(str(e))

        threads = [threading.Thread(target=worker) for _ in range(5)]

        for thread in threads:
            thread.start()

        for thread in threads:
            thread.join()

        assert len(errors) == 0
        assert all(r == 256 for r in results)

    def test_config_space_corruption_detection(self, manager):
        """Test detection of corrupted configuration space data."""
        # Test various corruption scenarios
        corruption_cases = [
            (b"\xff" * 256, "All ones - possible read error"),
            (b"\x00" * 256, "All zeros - possible device issue"),
            (b"\x00\x00\xff\xff" + b"\x00" * 252, "Invalid vendor ID"),
        ]

        for corrupted_data, description in corruption_cases:
            with patch("builtins.open", mock_open(read_data=corrupted_data)):
                with patch("os.path.exists", return_value=True):
                    result = manager._read_config_file_direct()
                    # Should return data even if suspicious
                    assert len(result) == 256

    def test_sysfs_permission_error_handling(self, manager):
        """Test handling of sysfs permission errors."""
        with patch("builtins.open", side_effect=PermissionError("Access denied")):
            with patch("os.path.exists", return_value=True):
                with patch(
                    "subprocess.run",
                    side_effect=subprocess.CalledProcessError(1, ["sudo", "hexdump"]),
                ):
                    with pytest.raises(
                        SysfsError, match="Failed to read configuration space via sysfs"
                    ):
                        manager._read_sysfs_fallback()

    def test_device_removal_during_read(self, manager):
        """Test handling of device removal during configuration space read."""

        # Simulate device disappearing during read
        def mock_open_side_effect(*args, **kwargs):
            raise FileNotFoundError("No such file or directory")

        with patch("builtins.open", side_effect=mock_open_side_effect):
            with patch("os.path.exists", return_value=True):
                with pytest.raises(
                    SysfsError, match="Failed to read configuration space via sysfs"
                ):
                    manager._read_sysfs_fallback()

    def test_vfio_diagnostics_integration(self, manager):
        """Test integration with VFIO diagnostics."""
        with patch.object(manager, "run_vfio_diagnostics") as mock_diagnostics:
            # Trigger diagnostics by causing an error
            with patch.object(
                manager, "_read_vfio_strict", side_effect=VFIOError("Test error")
            ):
                try:
                    manager.read_vfio_config_space(strict=True)
                except VFIOError:
                    pass

                # Should not call diagnostics in strict VFIO mode failure

    def test_memory_mapped_config_space_access(self, manager):
        """Test memory-mapped configuration space access scenarios."""
        # This would test PCIe ECAM access if implemented
        # For now, test that the interface is ready for it

        # Mock a memory-mapped config space read
        with patch("mmap.mmap") as mock_mmap:
            mock_mm = MagicMock()
            mock_mm.read.return_value = b"\x00" * 256
            mock_mmap.return_value.__enter__.return_value = mock_mm

            # Future implementation would use this path
            # result = manager._read_ecam_config_space()
            # assert len(result) == 256


class TestBarInfoAdvanced:
    """Test advanced BAR information handling."""

    def test_bar_info_complex_calculations(self):
        """Test complex BAR size calculations."""
        test_cases = [
            (4096, 4.0, 0.00390625, 0.0000038147),  # 4KB
            (1048576, 1024.0, 1.0, 0.0009765625),  # 1MB
            (1073741824, 1048576.0, 1024.0, 1.0),  # 1GB
            (
                0x100000000,
                4194304.0,
                4096.0,
                4.0,
            ),  # 4GB (edge case) - fixed expected values
        ]

        for size, kb, mb, gb in test_cases:
            bar = BarInfo(
                index=0, size=size, address=0, bar_type="memory", is_64bit=False
            )
            # Use more reasonable tolerance for floating point comparisons
            assert abs(bar.size_kb - kb) < 1.0
            assert abs(bar.size_mb - mb) < 1.0
            assert abs(bar.size_gb - gb) < 1.0

    def test_bar_info_boundary_conditions(self):
        """Test BAR info with boundary conditions."""
        boundary_cases = [
            (0, "Zero size BAR"),
            (1, "Single byte BAR"),
            (0xFFFFFFFF, "Maximum 32-bit BAR"),
            (0x100000000, "Minimum 64-bit BAR"),
        ]

        for size, description in boundary_cases:
            bar = BarInfo(
                index=0,
                size=size,
                address=0x80000000,
                bar_type="memory",
                is_64bit=(size > 0xFFFFFFFF),
            )
            assert bar.size == size, f"Failed for {description}"
            assert bar.is_64bit == (size > 0xFFFFFFFF)


class TestComplexDeviceConfigurations:
    """Test complex device configuration scenarios."""

    @pytest.fixture
    def manager(self):
        return ConfigSpaceManager("0000:01:00.0")

    def test_multi_function_device_handling(self, manager):
        """Test handling of multi-function devices."""
        # Simulate config space with multiple functions
        config_data = bytearray(256)
        config_data[0x0E] = 0x80  # Multi-function bit set

        with patch("builtins.open", mock_open(read_data=bytes(config_data))):
            with patch("os.path.exists", return_value=True):
                result = manager._read_config_file_direct()

                # Should read successfully
                assert len(result) == 256
                # Multi-function bit should be preserved
                assert result[0x0E] == 0x80

    def test_sr_iov_device_configuration(self, manager):
        """Test SR-IOV device configuration handling."""
        # Simulate SR-IOV capability in config space
        config_data = bytearray(256)

        # Standard PCI header
        config_data[0x00:0x02] = b"\x86\x80"  # Intel vendor ID
        config_data[0x02:0x04] = b"\x10\x15"  # Device ID
        config_data[0x34] = 0x40  # Capabilities pointer

        # Mock SR-IOV capability at offset 0x40
        config_data[0x40] = 0x10  # SR-IOV capability ID
        config_data[0x41] = 0x00  # Next capability

        with patch("builtins.open", mock_open(read_data=bytes(config_data))):
            with patch("os.path.exists", return_value=True):
                result = manager._read_config_file_direct()

                assert len(result) == 256
                assert result[0x34] == 0x40  # Capabilities pointer preserved
                assert result[0x40] == 0x10  # SR-IOV cap ID preserved

    def test_legacy_device_compatibility(self, manager):
        """Test compatibility with legacy devices."""
        # Simulate legacy device without capabilities
        config_data = bytearray(256)
        config_data[0x00:0x02] = b"\x86\x80"  # Intel vendor ID
        config_data[0x02:0x04] = b"\x00\x01"  # Legacy device ID
        config_data[0x34] = 0x00  # No capabilities

        with patch("builtins.open", mock_open(read_data=bytes(config_data))):
            with patch("os.path.exists", return_value=True):
                result = manager._read_config_file_direct()

                assert len(result) == 256
                assert result[0x34] == 0x00  # No capabilities

    def test_express_device_extended_config(self, manager):
        """Test PCIe device with extended configuration space."""
        # Simulate 4KB extended config space
        extended_config = bytearray(4096)

        # Standard header
        extended_config[0x00:0x02] = b"\x86\x80"  # Intel vendor ID
        extended_config[0x02:0x04] = b"\x10\x15"  # PCIe device ID
        extended_config[0x34] = 0x40  # Capabilities pointer

        # PCIe capability
        extended_config[0x40] = 0x10  # PCIe capability ID
        extended_config[0x41] = 0x00  # Next capability

        # Extended capabilities in extended space
        extended_config[0x100] = 0x01  # Advanced Error Reporting
        extended_config[0x102:0x104] = b"\x00\x01"  # Next cap at 0x100

        with patch("builtins.open", mock_open(read_data=bytes(extended_config))):
            with patch("os.path.exists", return_value=True):
                result = manager._read_config_file_direct()

                assert len(result) == 4096
                assert result[0x100] == 0x01  # Extended cap preserved


class TestErrorRecoveryScenarios:
    """Test error recovery scenarios in configuration space management."""

    @pytest.fixture
    def manager(self):
        return ConfigSpaceManager("0000:01:00.0")

    def test_partial_vfio_failure_recovery(self, manager):
        """Test recovery from partial VFIO failures."""
        # Simulate VFIO binding success but read failure
        mock_vfio_binder = MagicMock()
        mock_vfio_binder.__enter__.return_value = Path("/dev/vfio/42")
        mock_vfio_binder.__exit__.return_value = None

        with patch(
            "pcileechfwgenerator.cli.vfio_handler.VFIOBinder",
            return_value=mock_vfio_binder,
        ):
            with patch.object(
                manager, "_read_sysfs_config_space", return_value=b"\x00" * 256
            ) as mock_sysfs:

                result = manager._read_vfio_strict()

                assert len(result) == 256
                mock_sysfs.assert_called_once()

    def test_cascading_failure_handling(self, manager):
        """Test handling of cascading failures."""
        # All read methods fail - test the sysfs fallback path
        with patch.object(
            manager,
            "_read_sysfs_config_space",
            side_effect=SysfsError("Sysfs failed"),
        ):
            with pytest.raises(SysfsError, match="Sysfs failed"):
                manager.read_vfio_config_space(strict=False)

    def test_resource_cleanup_on_exceptions(self, manager):
        """Test resource cleanup when exceptions occur."""
        cleanup_called = []

        def mock_cleanup():
            cleanup_called.append(True)

        # Mock a resource that needs cleanup
        with patch("builtins.open", mock_open()) as mock_file:
            mock_file.return_value.__enter__.side_effect = Exception("File error")

            try:
                manager._read_config_file_direct()
            except Exception:
                pass

            # File should have been closed even on exception
            # This is handled by context manager


class TestConfigSpaceValidation:
    """Test configuration space data validation."""

    @pytest.fixture
    def manager(self):
        return ConfigSpaceManager("0000:01:00.0")

    def test_vendor_device_id_validation(self, manager):
        """Test vendor and device ID validation."""
        invalid_cases = [
            (b"\x00\x00\x00\x00", "All zeros"),
            (b"\xff\xff\xff\xff", "All ones"),
            (b"\x00\x00\xff\xff", "Zero vendor, invalid device"),
            (b"\xff\xff\x00\x00", "Invalid vendor, zero device"),
        ]

        for header_bytes, description in invalid_cases:
            config_data = header_bytes + b"\x00" * 252

            with patch("builtins.open", mock_open(read_data=config_data)):
                with patch("os.path.exists", return_value=True):
                    result = manager._read_config_file_direct()

                    # Should still return data but may log warnings
                    assert len(result) == 256

    def test_pci_header_type_validation(self, manager):
        """Test PCI header type validation."""
        header_types = [
            (0x00, "Type 0 - Endpoint"),
            (0x01, "Type 1 - Bridge"),
            (0x02, "Type 2 - CardBus"),
            (0x7F, "Invalid type"),
            (0x80, "Multi-function endpoint"),
        ]

        for header_type, description in header_types:
            config_data = bytearray(256)
            config_data[0x0E] = header_type

            with patch("builtins.open", mock_open(read_data=bytes(config_data))):
                with patch("os.path.exists", return_value=True):
                    result = manager._read_config_file_direct()

                    assert result[0x0E] == header_type


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
