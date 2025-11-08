#!/usr/bin/env python3
"""
VFIO Container Hardening Tests.

Tests for container-specific VFIO hardening features:
- Inter-process locking
- Device node waiting
- Aggressive remove_id cleanup  
- SELinux/AppArmor diagnostics
"""

import os
import sys
import time
import fcntl
from pathlib import Path
from unittest.mock import MagicMock, Mock, call, mock_open, patch

import pytest

from src.cli.vfio_handler import VFIOBinder, VFIOPathManager
from src.exceptions import VFIOBindError, VFIOGroupError


class TestVFIOContainerHardening:
    """Test container-specific VFIO hardening features."""

    @pytest.fixture
    def valid_bdf(self):
        """Valid BDF for testing."""
        return "0000:01:00.0"

    @pytest.fixture
    def mock_vfio_environment(self):
        """Mock a complete VFIO environment."""
        with patch("os.geteuid", return_value=0), \
             patch("pathlib.Path.exists", return_value=True), \
             patch("pathlib.Path.read_text", return_value="0x10de\n"), \
             patch("pathlib.Path.write_text"), \
             patch("pathlib.Path.resolve") as mock_resolve:
            
            # Mock IOMMU group path resolution
            mock_group_path = Mock()
            mock_group_path.name = "42"
            mock_resolve.return_value = mock_group_path
            
            yield

    def test_wait_for_group_node_success(self, valid_bdf, mock_vfio_environment):
        """Test successful wait for group device node."""
        with patch.object(VFIOBinder, "_check_security_context"):
            binder = VFIOBinder(valid_bdf, attach=False)
            
            # Mock device node appearing immediately
            with patch("pathlib.Path.exists", return_value=True):
                # Should not raise
                binder._wait_for_group_node("42", timeout=1.0)

    def test_wait_for_group_node_timeout(self, valid_bdf, mock_vfio_environment):
        """Test timeout waiting for group device node."""
        with patch.object(VFIOBinder, "_check_security_context"):
            binder = VFIOBinder(valid_bdf, attach=False)
            
            # Mock device node never appearing
            with patch("pathlib.Path.exists", return_value=False):
                with pytest.raises(VFIOGroupError, match="Timeout waiting"):
                    binder._wait_for_group_node("42", timeout=0.1)

    def test_wait_for_group_node_delayed(self, valid_bdf, mock_vfio_environment):
        """Test wait for group node that appears after delay."""
        with patch.object(VFIOBinder, "_check_security_context"):
            binder = VFIOBinder(valid_bdf, attach=False)
            
            # Mock device node appearing after 2 checks
            call_count = {"value": 0}
            
            def delayed_exists(self):
                call_count["value"] += 1
                return call_count["value"] > 2
            
            with patch("pathlib.Path.exists", delayed_exists):
                # Should succeed after delay
                binder._wait_for_group_node("42", timeout=1.0)
                assert call_count["value"] > 2

    def test_acquire_group_lock_success(self, valid_bdf, mock_vfio_environment):
        """Test successful group lock acquisition."""
        with patch.object(VFIOBinder, "_check_security_context"):
            binder = VFIOBinder(valid_bdf, attach=False)
            
            mock_file = MagicMock()
            mock_file.fileno.return_value = 42
            
            with patch("builtins.open", return_value=mock_file), \
                 patch("fcntl.flock") as mock_flock, \
                 patch("pathlib.Path.mkdir"):
                
                binder._acquire_group_lock("42")
                
                # Verify lock acquired
                assert binder._group_lock is not None
                mock_flock.assert_called_once_with(42, fcntl.LOCK_EX | fcntl.LOCK_NB)

    def test_acquire_group_lock_already_locked(
        self, valid_bdf, mock_vfio_environment
    ):
        """Test handling of already locked group."""
        with patch.object(VFIOBinder, "_check_security_context"):
            binder = VFIOBinder(valid_bdf, attach=False)
            
            mock_file = MagicMock()
            mock_file.fileno.return_value = 42
            
            with patch("builtins.open", return_value=mock_file), \
                 patch(
                    "fcntl.flock",
                    side_effect=OSError("Resource unavailable")
                 ), \
                 patch("pathlib.Path.mkdir"):
                
                with pytest.raises(VFIOBindError, match="already in use"):
                    binder._acquire_group_lock("42")
                
                # Verify file was closed
                mock_file.close.assert_called_once()

    def test_release_group_lock(self, valid_bdf, mock_vfio_environment):
        """Test group lock release."""
        with patch.object(VFIOBinder, "_check_security_context"):
            binder = VFIOBinder(valid_bdf, attach=False)
            
            mock_file = MagicMock()
            mock_file.fileno.return_value = 42
            binder._group_lock = mock_file
            
            with patch("fcntl.flock") as mock_flock:
                binder._release_group_lock()
                
                # Verify unlock and close
                mock_flock.assert_called_once_with(42, fcntl.LOCK_UN)
                mock_file.close.assert_called_once()
                assert binder._group_lock is None

    def test_release_group_lock_handles_errors(
        self, valid_bdf, mock_vfio_environment
    ):
        """Test group lock release handles errors gracefully."""
        with patch.object(VFIOBinder, "_check_security_context"):
            binder = VFIOBinder(valid_bdf, attach=False)
            
            mock_file = MagicMock()
            mock_file.fileno.return_value = 42
            mock_file.close.side_effect = OSError("Close failed")
            binder._group_lock = mock_file
            
            # Should not raise, just log warning
            binder._release_group_lock()
            
            # Lock should be cleared even on error
            assert binder._group_lock is None

    def test_cleanup_device_ids_single_id(self, valid_bdf, mock_vfio_environment):
        """Test cleanup of a single added device ID."""
        with patch.object(VFIOBinder, "_check_security_context"):
            binder = VFIOBinder(valid_bdf, attach=False)
            binder._added_device_ids = [("10de", "1234")]
            
            with patch("pathlib.Path.exists", return_value=True), \
                 patch("pathlib.Path.write_text") as mock_write:
                
                binder._cleanup_device_ids()
                
                # Verify remove_id was written
                mock_write.assert_called_once_with("10de 1234\n")
                # List should be cleared
                assert len(binder._added_device_ids) == 0

    def test_cleanup_device_ids_multiple_ids(
        self, valid_bdf, mock_vfio_environment
    ):
        """Test cleanup of multiple added device IDs."""
        with patch.object(VFIOBinder, "_check_security_context"):
            binder = VFIOBinder(valid_bdf, attach=False)
            binder._added_device_ids = [
                ("10de", "1234"),
                ("8086", "5678"),
                ("1002", "abcd"),
            ]
            
            with patch("pathlib.Path.exists", return_value=True), \
                 patch("pathlib.Path.write_text") as mock_write:
                
                binder._cleanup_device_ids()
                
                # Verify all IDs were removed
                assert mock_write.call_count == 3
                assert len(binder._added_device_ids) == 0

    def test_cleanup_device_ids_handles_errors(
        self, valid_bdf, mock_vfio_environment
    ):
        """Test cleanup handles errors without raising."""
        with patch.object(VFIOBinder, "_check_security_context"):
            binder = VFIOBinder(valid_bdf, attach=False)
            binder._added_device_ids = [("10de", "1234")]
            
            with patch("pathlib.Path.exists", return_value=True), \
                 patch(
                    "pathlib.Path.write_text",
                    side_effect=OSError("Write failed")
                 ):
                
                # Should not raise, just log
                binder._cleanup_device_ids()
                
                # List should still be cleared
                assert len(binder._added_device_ids) == 0

    def test_cleanup_device_ids_no_remove_id_sysfs(
        self, valid_bdf, mock_vfio_environment
    ):
        """Test cleanup when remove_id sysfs entry doesn't exist."""
        with patch.object(VFIOBinder, "_check_security_context"):
            binder = VFIOBinder(valid_bdf, attach=False)
            binder._added_device_ids = [("10de", "1234")]
            
            with patch("pathlib.Path.exists", return_value=False):
                # Should not raise, just skip cleanup
                binder._cleanup_device_ids()
                
                # List should NOT be cleared when remove_id doesn't exist
                assert len(binder._added_device_ids) == 1

    def test_check_security_context_selinux_enforcing(
        self, valid_bdf, mock_vfio_environment
    ):
        """Test SELinux enforcing detection."""
        selinux_checks = {"called": False}
        
        def mock_exists(self):
            if "selinux/enforce" in str(self):
                return True
            return True
        
        def mock_read_text(self):
            if "selinux/enforce" in str(self):
                selinux_checks["called"] = True
                return "1\n"
            return "0x10de\n"
        
        with patch("pathlib.Path.exists", mock_exists), \
             patch("pathlib.Path.read_text", mock_read_text):
            
            # Should emit warning but not raise
            binder = VFIOBinder(valid_bdf, attach=False)
            assert selinux_checks["called"]

    def test_check_security_context_selinux_permissive(
        self, valid_bdf, mock_vfio_environment
    ):
        """Test SELinux permissive mode (no warning)."""
        def mock_exists(self):
            if "selinux/enforce" in str(self):
                return True
            return True
        
        def mock_read_text(self):
            if "selinux/enforce" in str(self):
                return "0\n"  # Permissive
            return "0x10de\n"
        
        with patch("pathlib.Path.exists", mock_exists), \
             patch("pathlib.Path.read_text", mock_read_text):
            
            # Should not emit warning
            binder = VFIOBinder(valid_bdf, attach=False)

    def test_check_security_context_apparmor_enabled(
        self, valid_bdf, mock_vfio_environment
    ):
        """Test AppArmor enabled detection."""
        apparmor_checks = {"called": False}
        
        def mock_exists(self):
            if "apparmor/parameters/enabled" in str(self):
                return True
            return True
        
        def mock_read_text(self):
            if "apparmor/parameters/enabled" in str(self):
                apparmor_checks["called"] = True
                return "Y\n"
            return "0x10de\n"
        
        with patch("pathlib.Path.exists", mock_exists), \
             patch("pathlib.Path.read_text", mock_read_text):
            
            # Should emit warning but not raise
            binder = VFIOBinder(valid_bdf, attach=False)
            assert apparmor_checks["called"]

    def test_bind_acquires_lock_before_operations(
        self, valid_bdf, mock_vfio_environment
    ):
        """Test that bind() acquires lock before performing operations."""
        with patch.object(VFIOBinder, "_check_security_context"):
            binder = VFIOBinder(valid_bdf, attach=False)
            
            lock_acquired = {"value": False}
            
            def mock_acquire_lock(group_id):
                lock_acquired["value"] = True
            
            with patch.object(binder, "_acquire_group_lock", mock_acquire_lock), \
                 patch.object(binder, "_check_iommu"), \
                 patch.object(binder, "_get_iommu_group", return_value="42"), \
                 patch.object(binder, "_save_original_driver"), \
                 patch.object(binder, "_unbind_current_driver"), \
                 patch.object(binder, "_set_driver_override"), \
                 patch.object(binder, "_bind_to_vfio"), \
                 patch.object(binder, "_wait_for_group_node"):
                
                binder.bind()
                
                # Verify lock was acquired
                assert lock_acquired["value"]

    def test_bind_waits_for_group_node_after_bind_to_vfio(
        self, valid_bdf, mock_vfio_environment
    ):
        """Test that bind() waits for group node after binding."""
        with patch.object(VFIOBinder, "_check_security_context"):
            binder = VFIOBinder(valid_bdf, attach=False)
            
            wait_called = {"value": False}
            
            def mock_wait(group_id, timeout=3.0):
                wait_called["value"] = True
            
            with patch.object(binder, "_acquire_group_lock"), \
                 patch.object(binder, "_check_iommu"), \
                 patch.object(binder, "_get_iommu_group", return_value="42"), \
                 patch.object(binder, "_save_original_driver"), \
                 patch.object(binder, "_unbind_current_driver"), \
                 patch.object(binder, "_set_driver_override"), \
                 patch.object(binder, "_bind_to_vfio"), \
                 patch.object(binder, "_wait_for_group_node", mock_wait):
                
                binder.bind()
                
                # Verify wait was called
                assert wait_called["value"]

    def test_bind_releases_lock_on_error(self, valid_bdf, mock_vfio_environment):
        """Test that bind() releases lock when errors occur."""
        with patch.object(VFIOBinder, "_check_security_context"):
            binder = VFIOBinder(valid_bdf, attach=False)
            
            lock_released = {"value": False}
            
            def mock_release_lock():
                lock_released["value"] = True
            
            with patch.object(binder, "_acquire_group_lock"), \
                 patch.object(binder, "_check_iommu"), \
                 patch.object(binder, "_get_iommu_group", return_value="42"), \
                 patch.object(
                    binder, "_save_original_driver",
                    side_effect=Exception("Test error")
                 ), \
                 patch.object(binder, "_release_group_lock", mock_release_lock):
                
                with pytest.raises(Exception, match="Test error"):
                    binder.bind()
                
                # Verify lock was released
                assert lock_released["value"]

    def test_unbind_cleans_up_device_ids(self, valid_bdf, mock_vfio_environment):
        """Test that unbind() cleans up device IDs."""
        with patch.object(VFIOBinder, "_check_security_context"):
            binder = VFIOBinder(valid_bdf, attach=False)
            binder._bound = True
            binder._added_device_ids = [("10de", "1234")]
            
            cleanup_called = {"value": False}
            
            def mock_cleanup():
                cleanup_called["value"] = True
                binder._added_device_ids.clear()
            
            with patch("pathlib.Path.exists", return_value=False), \
                 patch.object(binder, "_cleanup_device_ids", mock_cleanup), \
                 patch.object(binder, "_release_group_lock"):
                
                binder.unbind()
                
                # Verify cleanup was called
                assert cleanup_called["value"]

    def test_unbind_releases_lock(self, valid_bdf, mock_vfio_environment):
        """Test that unbind() releases group lock."""
        with patch.object(VFIOBinder, "_check_security_context"):
            binder = VFIOBinder(valid_bdf, attach=False)
            binder._bound = True
            
            lock_released = {"value": False}
            
            def mock_release():
                lock_released["value"] = True
            
            with patch("pathlib.Path.exists", return_value=False), \
                 patch.object(binder, "_cleanup_device_ids"), \
                 patch.object(binder, "_release_group_lock", mock_release):
                
                binder.unbind()
                
                # Verify lock was released
                assert lock_released["value"]

    def test_context_manager_exit_cleans_up_on_success(
        self, valid_bdf, mock_vfio_environment
    ):
        """Test context manager cleanup on successful exit."""
        with patch.object(VFIOBinder, "_check_security_context"):
            binder = VFIOBinder(valid_bdf, attach=False)
            binder._bound = True
            binder._added_device_ids = [("10de", "1234")]
            
            cleanup_called = {"value": False}
            lock_released = {"value": False}
            
            def mock_cleanup():
                cleanup_called["value"] = True
                binder._added_device_ids.clear()
            
            def mock_release():
                lock_released["value"] = True
            
            with patch("pathlib.Path.exists", return_value=False), \
                 patch.object(binder, "_cleanup_device_ids", mock_cleanup), \
                 patch.object(binder, "_release_group_lock", mock_release):
                
                binder.__exit__(None, None, None)
                
                # Verify both cleanup and lock release
                assert cleanup_called["value"]
                assert lock_released["value"]

    def test_context_manager_exit_cleans_up_on_error(
        self, valid_bdf, mock_vfio_environment
    ):
        """Test context manager cleanup even when unbind fails."""
        with patch.object(VFIOBinder, "_check_security_context"):
            binder = VFIOBinder(valid_bdf, attach=False)
            binder._bound = True
            binder._added_device_ids = [("10de", "1234")]
            
            cleanup_called = {"value": False}
            lock_released = {"value": False}
            
            def mock_cleanup():
                cleanup_called["value"] = True
                binder._added_device_ids.clear()
            
            def mock_release():
                lock_released["value"] = True
            
            with patch("pathlib.Path.exists", return_value=True), \
                 patch(
                    "pathlib.Path.write_text",
                    side_effect=OSError("Unbind failed")
                 ), \
                 patch.object(binder, "_cleanup_device_ids", mock_cleanup), \
                 patch.object(binder, "_release_group_lock", mock_release):
                
                # Exit should handle the error and still clean up
                binder.__exit__(None, None, None)
                
                # Verify cleanup happened despite unbind failure
                assert cleanup_called["value"]
                assert lock_released["value"]

    def test_bind_to_vfio_tracks_added_device_ids(
        self, valid_bdf, mock_vfio_environment
    ):
        """Test that _bind_to_vfio tracks added device IDs."""
        with patch.object(VFIOBinder, "_check_security_context"):
            binder = VFIOBinder(valid_bdf, attach=False)
            
            with patch("pathlib.Path.exists", return_value=True), \
                 patch("pathlib.Path.write_text"), \
                 patch("pathlib.Path.read_text", return_value="0x10de\n"), \
                 patch.object(
                    binder._path_manager,
                    "get_vendor_device_id",
                    return_value=("10de", "1234")
                 ):
                
                try:
                    binder._bind_to_vfio()
                except VFIOBindError:
                    # Binding verification will fail, but we just want to check
                    # that IDs were tracked
                    pass
                
                # Verify device ID was tracked
                assert ("10de", "1234") in binder._added_device_ids
