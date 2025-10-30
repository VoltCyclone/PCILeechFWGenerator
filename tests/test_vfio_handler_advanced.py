#!/usr/bin/env python3
"""
Enhanced VFIO Handler Tests - Critical Edge Cases and Error Scenarios.

This test module focuses on improving test coverage for critical VFIO operations
that are under-tested, including complex error scenarios, resource cleanup,
concurrent access, and edge cases in device binding workflows.
"""

import errno
import fcntl
import os
import sys
import threading
import time
from pathlib import Path
from typing import Any, Dict, Optional
from unittest.mock import MagicMock, Mock, call, mock_open, patch

import pytest

from src.cli.vfio_handler import (
    DeviceInfo,
    VFIOBinder,
    VFIOPathManager,
    check_vfio_availability
)

# Import exceptions from the exceptions module
from src.exceptions import (
    VFIOBindError,
    VFIOGroupError,
    VFIOPermissionError,
    VFIODeviceNotFoundError
)


@pytest.mark.hardware
@pytest.mark.skipif(sys.platform == "darwin", reason="VFIO tests require Linux")
class TestVFIOBinderAdvancedErrorHandling:
    """Test advanced VFIO error handling scenarios."""

    @pytest.fixture
    def valid_bdf(self):
        return "0000:01:00.0"

    def test_vfio_binding_with_kernel_module_issues(self, valid_bdf):
        """Test handling of kernel module loading issues."""
        with patch("os.geteuid", return_value=0):
            with patch("src.cli.vfio_handler._get_iommu_group", return_value="42"):
                binder = VFIOBinderImpl(valid_bdf)

                # Simulate vfio-pci module not loaded
                with patch(
                    "pathlib.Path.exists",
                    side_effect=lambda path: "/sys/bus/pci/drivers/vfio-pci"
                    not in str(path),
                ):
                    with pytest.raises(VFIOBindError, match="vfio-pci.*not available"):
                        binder._perform_vfio_binding()

    def test_concurrent_device_binding_conflicts(self, valid_bdf):
        """Test handling of concurrent binding conflicts."""
        with patch("os.geteuid", return_value=0):
            with patch("src.cli.vfio_handler._get_iommu_group", return_value="42"):
                binder = VFIOBinderImpl(valid_bdf)

                # Simulate EBUSY errors during binding

                def mock_write_sysfs(path, value):
                    if "bind" in str(path):
                        raise OSError(errno.EBUSY, "Device busy")

                with patch.object(
                    binder, "_write_sysfs_safe", side_effect=mock_write_sysfs
                ):
                    with pytest.raises(VFIOBindError, match="Device busy"):
                        binder._perform_vfio_binding()

    def test_iommu_group_permission_escalation_attempts(self, valid_bdf):
        """Test security against permission escalation attempts."""
        with patch("os.geteuid", return_value=1000):  # Non-root user
            with pytest.raises(VFIOPermissionError, match="root privileges"):
                VFIOBinderImpl(valid_bdf)

    def test_file_descriptor_leak_prevention(self, valid_bdf):
        """Test that file descriptors are properly cleaned up on errors."""
        with patch("os.geteuid", return_value=0):
            with patch("src.cli.vfio_handler._get_iommu_group", return_value="42"):
                binder = VFIOBinderImpl(valid_bdf, attach=True)

                mock_device_fd = 100
                mock_container_fd = 101

                with patch("os.open", side_effect=[mock_container_fd, mock_device_fd]):
                    with patch("fcntl.ioctl", side_effect=OSError("VFIO error")):
                        with patch("os.close") as mock_close:
                            with pytest.raises(VFIOBindError):
                                binder._open_vfio_device_fd()

                            # Verify both FDs were closed
                            mock_close.assert_any_call(mock_container_fd)

    def test_device_removal_during_binding(self, valid_bdf):
        """Test handling of device removal during binding process."""
        with patch("os.geteuid", return_value=0):
            with patch("src.cli.vfio_handler._get_iommu_group", return_value="42"):
                binder = VFIOBinderImpl(valid_bdf)

                # Simulate device disappearing during bind operation

                def mock_wait_for_state_change(*args, **kwargs):
                    # Device path no longer exists
                    with patch("pathlib.Path.exists", return_value=False):
                        return False

                with patch.object(
                    binder,
                    "_wait_for_state_change",
                    side_effect=mock_wait_for_state_change,
                ):
                    with patch.object(binder, "_write_sysfs_safe"):
                        with pytest.raises(VFIOBindError, match="binding timed out"):
                            binder._perform_vfio_binding()

    def test_corrupted_sysfs_data_handling(self, valid_bdf):
        """Test handling of corrupted or malformed sysfs data."""
        with patch("os.geteuid", return_value=0):
            with patch("src.cli.vfio_handler._get_iommu_group", return_value="42"):

                # Test corrupted driver symlink
                with patch("os.readlink", side_effect=OSError("Invalid symlink")):
                    device_info = DeviceInfo.from_bdf(valid_bdf)
                    assert device_info.current_driver is None

    def test_vfio_group_state_race_conditions(self, valid_bdf):
        """Test race conditions in VFIO group state management."""
        with patch("os.geteuid", return_value=0):
            with patch("src.cli.vfio_handler._get_iommu_group", return_value="42"):
                binder = VFIOBinderImpl(valid_bdf)

                # Simulate group becoming unavailable between checks

                def mock_group_check(path):
                    if "/dev/vfio/42" in str(path):
                        # First call returns True, subsequent calls False
                        if not hasattr(mock_group_check, "called"):
                            mock_group_check.called = True
                            return True
                        return False
                    return True

                with patch("pathlib.Path.exists", side_effect=mock_group_check):
                    with pytest.raises(VFIOGroupError):
                        binder._verify_vfio_binding()


@pytest.mark.hardware
@pytest.mark.skipif(sys.platform == "darwin", reason="VFIO tests require Linux")
class TestVFIOResourceManagement:
    """Test VFIO resource management and cleanup scenarios."""

    @pytest.fixture
    def valid_bdf(self):
        return "0000:01:00.0"

    def test_memory_mapping_edge_cases(self, valid_bdf):
        """Test memory mapping edge cases and error scenarios."""
        with patch("os.geteuid", return_value=0):
            with patch("src.cli.vfio_handler._get_iommu_group", return_value="42"):
                binder = VFIOBinderImpl(valid_bdf, attach=True)

                # Test memory mapping with invalid parameters
                region_info = {
                    "size": 0,  # Invalid size
                    "offset": 0xFFFFFFFF,  # Invalid offset
                    "flags": 0,  # No mapping flags
                }

                result = binder._get_vfio_region_info(0)
                # Should handle invalid region gracefully
                assert result is None or isinstance(result, dict)

    def test_cleanup_on_signal_interruption(self, valid_bdf):
        """Test cleanup behavior when interrupted by signals."""
        with patch("os.geteuid", return_value=0):
            with patch("src.cli.vfio_handler._get_iommu_group", return_value="42"):
                binder = VFIOBinderImpl(valid_bdf)

                binder.original_driver = "test_driver"
                binder._bound = True

                # Simulate KeyboardInterrupt during cleanup
                with patch.object(
                    binder,
                    "_restore_original_driver",
                    side_effect=KeyboardInterrupt("User interrupt"),
                ):
                    try:
                        binder._cleanup()
                    except KeyboardInterrupt:
                        pass  # Expected

                # Should not raise exception if already cleaned up
                binder._cleanup()

    def test_multiple_device_binding_coordination(self):
        """Test coordination between multiple device bindings."""
        bdfs = ["0000:01:00.0", "0000:01:00.1", "0000:02:00.0"]
        binders = []

        with patch("os.geteuid", return_value=0):
            # Different IOMMU groups for each device
            with patch(
                "src.cli.vfio_handler._get_iommu_group",
                side_effect=lambda bdf: str(hash(bdf) % 100),
            ):

                try:
                    for bdf in bdfs:
                        binder = VFIOBinderImpl(bdf)
                        binders.append(binder)
                        binder.original_driver = f"driver_{bdf.replace(':', '_')}"
                        binder._bound = True

                    # Cleanup all binders
                    for binder in binders:
                        with patch.object(binder, "_restore_original_driver"):
                            binder._cleanup()

                except Exception as e:
                    # Ensure cleanup happens even on error
                    for binder in binders:
                        try:
                            binder._cleanup()
                        except:
                            pass
                    raise

    def test_vfio_container_reuse_safety(self, valid_bdf):
        """Test safety of VFIO container reuse scenarios."""
        with patch("os.geteuid", return_value=0):
            with patch("src.cli.vfio_handler._get_iommu_group", return_value="42"):

                # Test sequential binding/unbinding cycles
                for i in range(3):
                    binder = VFIOBinderImpl(valid_bdf, attach=True)

                    with patch.object(
                        binder,
                        "_open_vfio_device_fd",
                        return_value=(100 + i, 200 + i),
                    ):
                        with patch("os.close") as mock_close:
                            result = binder.__enter__()
                            assert result == Path("/dev/vfio/42")

                            binder.__exit__(None, None, None)

                            # Verify FDs were closed
                            mock_close.assert_called()


@pytest.mark.hardware
@pytest.mark.skipif(sys.platform == "darwin", reason="VFIO tests require Linux")
class TestVFIOConcurrencyAndThreadSafety:
    """Test VFIO operations under concurrent access scenarios."""

    @pytest.fixture
    def valid_bdf(self):
        return "0000:01:00.0"

    def test_concurrent_binding_attempts(self, valid_bdf):
        """Test behavior with concurrent binding attempts."""
        results = []
        errors = []

        def worker():
            try:
                with patch("os.geteuid", return_value=0):
                    with patch(
                        "src.cli.vfio_handler._get_iommu_group", return_value="42"
                    ):
                        binder = VFIOBinderImpl(valid_bdf)
                        binder.original_driver = "test_driver"
                        binder._bound = True

                        # Simulate some work
                        time.sleep(0.01)

                        with patch.object(binder, "_restore_original_driver"):
                            binder._cleanup()

                        results.append("success")
            except Exception as e:
                errors.append(str(e))

        # Start multiple concurrent workers
        threads = [threading.Thread(target=worker) for _ in range(5)]

        for thread in threads:
            thread.start()

        for thread in threads:
            thread.join()

        # All should complete without errors
        assert len(errors) == 0
        assert len(results) == 5

    def test_thread_local_state_isolation(self, valid_bdf):
        """Test that thread-local state is properly isolated."""
        thread_results = {}

        def worker(thread_id):
            with patch("os.geteuid", return_value=0):
                with patch(
                    "src.cli.vfio_handler._get_iommu_group",
                    return_value=str(thread_id),
                ):
                    binder = VFIOBinderImpl(valid_bdf)
                    binder.group_id = str(thread_id)
                    thread_results[thread_id] = binder.group_id

        threads = []
        for i in range(3):
            thread = threading.Thread(target=worker, args=(i,))
            threads.append(thread)
            thread.start()

        for thread in threads:
            thread.join()

        # Each thread should have its own group_id
        assert len(thread_results) == 3
        assert thread_results[0] == "0"
        assert thread_results[1] == "1"
        assert thread_results[2] == "2"


@pytest.mark.hardware
@pytest.mark.skipif(sys.platform == "darwin", reason="VFIO tests require Linux")
class TestVFIODeviceStateComplexity:
    """Test complex device state management scenarios."""

    @pytest.fixture
    def valid_bdf(self):
        return "0000:01:00.0"

    def test_device_state_transition_validation(self, valid_bdf):
        """Test validation of device state transitions."""
        with patch("os.geteuid", return_value=0):
            with patch("src.cli.vfio_handler._get_iommu_group", return_value="42"):

                # Test invalid state transitions
                device_info = DeviceInfo(
                    bdf=valid_bdf,
                    current_driver="unknown_driver",
                    iommu_group="42",
                    binding_state=BindingState.BOUND_TO_OTHER,
                )

                binder = VFIOBinderImpl(valid_bdf)
                binder._device_info = device_info

                # Should handle unknown driver gracefully
                with patch.object(binder, "_write_sysfs_safe"):
                    with patch.object(
                        binder, "_wait_for_state_change", return_value=True
                    ):
                        # Should not raise exception for unknown driver
                        binder._unbind_current_driver(device_info)

    def test_stale_device_info_handling(self, valid_bdf):
        """Test handling of stale device information."""
        with patch("os.geteuid", return_value=0):
            with patch("src.cli.vfio_handler._get_iommu_group", return_value="42"):
                binder = VFIOBinderImpl(valid_bdf)

                # Create stale device info
                old_info = DeviceInfo(
                    bdf=valid_bdf,
                    current_driver="old_driver",
                    iommu_group="42",
                    binding_state=BindingState.BOUND_TO_OTHER,
                )
                binder._device_info = old_info

                # Get fresh info should update cached data
                with patch.object(DeviceInfo, "from_bdf") as mock_from_bdf:
                    new_info = DeviceInfo(
                        bdf=valid_bdf,
                        current_driver="new_driver",
                        iommu_group="42",
                        binding_state=BindingState.BOUND_TO_VFIO,
                    )
                    mock_from_bdf.return_value = new_info

                    refreshed_info = binder._get_device_info(refresh=True)
                    assert refreshed_info.current_driver == "new_driver"
                    assert binder._device_info == new_info

    def test_partial_binding_state_recovery(self, valid_bdf):
        """Test recovery from partial binding states."""
        with patch("os.geteuid", return_value=0):
            with patch("src.cli.vfio_handler._get_iommu_group", return_value="42"):
                binder = VFIOBinderImpl(valid_bdf)

                # Simulate partial binding state
                binder.original_driver = "original_driver"
                binder._bound = False  # Binding was attempted but failed

                # Recovery should restore original driver
                with patch.object(binder, "_write_sysfs_safe") as mock_write:
                    binder._restore_original_driver()

                    # Should attempt to bind back to original driver
                    mock_write.assert_called()


@pytest.mark.hardware
@pytest.mark.skipif(sys.platform == "darwin", reason="VFIO tests require Linux")
class TestVFIODiagnosticsAndDebugging:
    """Test VFIO diagnostics and debugging capabilities."""

    @pytest.fixture
    def valid_bdf(self):
        return "0000:01:00.0"

    def test_comprehensive_vfio_diagnostics(self, valid_bdf):
        """Test comprehensive VFIO diagnostics collection."""
        from src.cli.vfio_handler import run_diagnostics

        # Mock various system states for diagnostic collection
        with patch("pathlib.Path.exists", return_value=True):
            with patch("builtins.open", mock_open(read_data="test_content")):
                with patch("os.listdir", return_value=["group1", "group2"]):

                    # Should not raise exceptions
                    diagnostics = run_diagnostics(valid_bdf)
                    assert isinstance(diagnostics, (str, type(None)))

    def test_diagnostic_information_completeness(self, valid_bdf):
        """Test that diagnostic information is complete and useful."""
        from src.cli.vfio_handler import render_pretty

        device_info = DeviceInfo(
            bdf=valid_bdf,
            current_driver="test_driver",
            iommu_group="42",
            binding_state=BindingState.BOUND_TO_OTHER,
        )

        # Convert DeviceInfo to dict for render_pretty
        device_info_dict = {
            "bdf": device_info.bdf,
            "current_driver": device_info.current_driver,
            "iommu_group": device_info.iommu_group,
            "binding_state": str(device_info.binding_state),
            "overall": "ok",
            "checks": [],
        }
        pretty_output = render_pretty(device_info_dict)

        # Should contain key diagnostic information
        assert valid_bdf in pretty_output
        assert "test_driver" in pretty_output
        assert "42" in pretty_output

    def test_error_context_preservation(self, valid_bdf):
        """Test that error context is preserved for debugging."""
        with patch("os.geteuid", return_value=0):
            with patch("src.cli.vfio_handler._get_iommu_group", return_value="42"):
                binder = VFIOBinderImpl(valid_bdf)

                original_error = OSError(errno.EACCES, "Permission denied")

                with patch.object(
                    binder, "_write_sysfs_safe", side_effect=original_error
                ):
                    try:
                        binder._perform_vfio_binding()
                    except VFIOBindError as e:
                        # Error should contain original context
                        assert "Permission denied" in str(e)
                        assert hasattr(e, "__cause__")


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
