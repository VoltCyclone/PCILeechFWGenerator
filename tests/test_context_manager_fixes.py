#!/usr/bin/env python3
"""
Unit tests for context manager fixes.

This test suite covers the fixes for:
1. VFIOBinder.__exit__() missing required parameters
2. PCILeechContext.__exit__() missing required parameters
3. Pre-collected config space data handling in containers

Tests ensure that context managers properly implement the Python context
manager protocol and handle exceptions correctly.
"""

import json
import os
import sys
import tempfile
from pathlib import Path
from unittest.mock import MagicMock, Mock, mock_open, patch

import pytest

from src.cli.vfio_handler import VFIOBinder
from src.device_clone.pcileech_context import VFIODeviceManager
from src.device_clone.pcileech_generator import (PCILeechGenerationConfig,
                                                  PCILeechGenerator)
from src.exceptions import VFIOBindError


class TestVFIOBinderContextManager:
    """Test VFIOBinder context manager implementation."""

    @pytest.fixture
    def valid_bdf(self):
        """Provide a valid BDF for testing."""
        return "0000:01:00.0"

    @pytest.fixture
    def mock_vfio_paths(self, valid_bdf):
        """Mock VFIO system paths."""
        with patch("os.geteuid", return_value=0):
            with patch("pathlib.Path.exists", return_value=True):
                with patch("pathlib.Path.is_symlink", return_value=True):
                    vfio_path = Path(
                        f"/sys/bus/pci/drivers/vfio-pci/{valid_bdf}"
                    )
                    with patch("pathlib.Path.resolve", return_value=vfio_path):
                        yield

    def test_vfio_binder_exit_accepts_exception_info(
        self, valid_bdf, mock_vfio_paths
    ):
        """Test that __exit__ accepts the required exception parameters."""
        with patch("src.cli.vfio_handler._get_iommu_group", return_value="42"):
            binder = VFIOBinder(valid_bdf)
            binder._bound = False  # Avoid actual binding

            # Test that __exit__ can be called with exception info
            try:
                exc_type = ValueError
                exc_val = ValueError("test exception")
                exc_tb = None
                
                # Should not raise TypeError about argument count
                result = binder.__exit__(exc_type, exc_val, exc_tb)
                
                # __exit__ should return None (or False) to propagate exceptions
                assert result is None or result is False
                
            except TypeError as e:
                pytest.fail(f"__exit__ raised TypeError: {e}")

    def test_vfio_binder_context_manager_with_no_exception(
        self, valid_bdf, mock_vfio_paths
    ):
        """Test context manager without exceptions."""
        with patch("src.cli.vfio_handler._get_iommu_group", return_value="42"):
            with patch.object(VFIOBinder, "bind"):
                with patch.object(VFIOBinder, "unbind"):
                    binder = VFIOBinder(valid_bdf)
                    
                    # Enter context
                    entered = binder.__enter__()
                    assert entered is binder
                    
                    # Exit without exception
                    result = binder.__exit__(None, None, None)
                    assert result is None or result is False

    def test_vfio_binder_context_manager_with_exception(
        self, valid_bdf, mock_vfio_paths
    ):
        """Test context manager handles exceptions during usage."""
        with patch("src.cli.vfio_handler._get_iommu_group", return_value="42"):
            with patch.object(VFIOBinder, "bind"):
                with patch.object(VFIOBinder, "unbind") as mock_unbind:
                    binder = VFIOBinder(valid_bdf)
                    binder._bound = True
                    
                    try:
                        with binder:
                            raise ValueError(
                                "Simulated error during VFIO operations"
                            )
                    except ValueError:
                        pass  # Expected
                    
                    # Verify unbind was attempted during cleanup
                    mock_unbind.assert_called_once()

    def test_vfio_binder_cleanup_on_exception(self, valid_bdf, mock_vfio_paths):
        """Test that cleanup happens even if unbind fails."""
        with patch("src.cli.vfio_handler._get_iommu_group", return_value="42"):
            with patch.object(VFIOBinder, "bind"):
                with patch.object(
                    VFIOBinder, "unbind", side_effect=Exception("Unbind failed")
                ):
                    binder = VFIOBinder(valid_bdf)
                    binder._bound = True
                    
                    # Should not raise during __exit__ even if unbind fails
                    result = binder.__exit__(None, None, None)
                    assert result is None or result is False


class TestVFIODeviceManagerContextManager:
    """Test VFIODeviceManager context manager implementation."""

    def test_vfio_device_manager_exit_accepts_exception_info(self):
        """Test that VFIODeviceManager.__exit__ accepts required parameters."""
        import logging
        context = VFIODeviceManager(
            device_bdf="0000:01:00.0",
            logger=logging.getLogger("test"),
        )
        
        # Test that __exit__ can be called with exception info
        try:
            exc_type = ValueError
            exc_val = ValueError("test exception")
            exc_tb = None
            
            # Should not raise TypeError about argument count
            result = context.__exit__(exc_type, exc_val, exc_tb)
            
            # __exit__ should return None (or False) to propagate exceptions
            assert result is None or result is False
            
        except TypeError as e:
            pytest.fail(f"__exit__ raised TypeError: {e}")

    def test_vfio_device_manager_with_no_exception(self):
        """Test VFIODeviceManager as context manager without exceptions."""
        import logging
        context = VFIODeviceManager(
            device_bdf="0000:01:00.0",
            logger=logging.getLogger("test"),
        )
        
        with patch.object(context, "close") as mock_close:
            # Enter context
            entered = context.__enter__()
            assert entered is context
            
            # Exit without exception
            result = context.__exit__(None, None, None)
            assert result is None or result is False
            
            # Verify close was called
            mock_close.assert_called_once()

    def test_vfio_device_manager_with_exception(self):
        """Test VFIODeviceManager handles exceptions during usage."""
        import logging
        context = VFIODeviceManager(
            device_bdf="0000:01:00.0",
            logger=logging.getLogger("test"),
        )
        
        with patch.object(context, "close") as mock_close:
            try:
                with context:
                    raise ValueError("Simulated error during context usage")
            except ValueError:
                pass  # Expected
            
            # Verify close was still called during cleanup
            mock_close.assert_called_once()


class TestPreCollectedConfigSpaceHandling:
    """Test pre-collected config space data handling in containers."""

    @pytest.fixture
    def mock_device_context(self):
        """Create a mock device context JSON."""
        return {
            "bdf": "0000:01:00.0",
            "config_space_hex": "86801234" + "00" * 252,  # Valid config space
            "device_info": {
                "vendor_id": "0x8086",
                "device_id": "0x1234",
                "class_code": "0x020000",
            },
            "msix_data": {
                "preloaded": True,
                "msix_info": {
                    "table_size": 4,
                    "table_bir": 0,
                    "table_offset": 0x1000,
                },
            },
        }

    @pytest.fixture
    def temp_context_file(self, mock_device_context):
        """Create a temporary device context file."""
        with tempfile.NamedTemporaryFile(
            mode="w", suffix=".json", delete=False
        ) as f:
            json.dump(mock_device_context, f)
            temp_path = f.name
        
        yield temp_path
        
        # Cleanup
        try:
            os.unlink(temp_path)
        except FileNotFoundError:
            pass

    def test_pcileech_generator_uses_precollected_config_space(
        self, mock_device_context, temp_context_file
    ):
        """Test that PCILeechGenerator uses pre-collected config space."""
        config = PCILeechGenerationConfig(
            device_bdf="0000:01:00.0",
            board="test_board",
            output_dir=Path("/tmp/test_output"),
        )
        
        with patch.dict(os.environ, {"DEVICE_CONTEXT_PATH": temp_context_file}):
            generator = PCILeechGenerator(config)
            
            # Mock the config_space_manager to verify it's not called
            with patch.object(
                generator.config_space_manager,
                "read_vfio_config_space",
                side_effect=Exception("Should not be called!"),
            ):
                # Should use pre-collected data, not call VFIO
                result = generator._analyze_configuration_space()
                
                # Verify we got config space data
                assert "raw_config_space" in result
                assert len(result["raw_config_space"]) > 0

    def test_pcileech_generator_fallback_to_vfio_when_no_precollected(self):
        """Test fallback to VFIO when pre-collected data is not available."""
        config = PCILeechGenerationConfig(
            device_bdf="0000:01:00.0",
            board="test_board",
            output_dir=Path("/tmp/test_output"),
        )
        
        # No DEVICE_CONTEXT_PATH set
        with patch.dict(os.environ, {}, clear=True):
            if "DEVICE_CONTEXT_PATH" in os.environ:
                del os.environ["DEVICE_CONTEXT_PATH"]
            
            generator = PCILeechGenerator(config)
            
            # Mock VFIO config space reading
            mock_config_space = b"\x86\x80\x12\x34" + b"\x00" * 252
            with patch.object(
                generator.config_space_manager,
                "read_vfio_config_space",
                return_value=mock_config_space,
            ):
                result = generator._analyze_configuration_space()
                
                # Verify we got config space data from VFIO
                assert "raw_config_space" in result
                assert result["raw_config_space"] == mock_config_space

    def test_pcileech_generator_handles_invalid_precollected_data(
        self, temp_context_file
    ):
        """Test handling of invalid pre-collected data."""
        # Overwrite with invalid JSON
        with open(temp_context_file, "w") as f:
            f.write('{"invalid": "data"}')  # Missing config_space_hex
        
        config = PCILeechGenerationConfig(
            device_bdf="0000:01:00.0",
            board="test_board",
            output_dir=Path("/tmp/test_output"),
        )
        
        with patch.dict(os.environ, {"DEVICE_CONTEXT_PATH": temp_context_file}):
            generator = PCILeechGenerator(config)
            
            # Should fall back to VFIO when pre-collected data is invalid
            mock_config_space = b"\x86\x80\x12\x34" + b"\x00" * 252
            with patch.object(
                generator.config_space_manager,
                "read_vfio_config_space",
                return_value=mock_config_space,
            ):
                result = generator._analyze_configuration_space()
                
                # Verify we got config space data from VFIO fallback
                assert "raw_config_space" in result
                assert result["raw_config_space"] == mock_config_space

    def test_device_context_path_env_variable_respected(self, mock_device_context):
        """Test that DEVICE_CONTEXT_PATH environment variable is respected."""
        with tempfile.NamedTemporaryFile(
            mode="w", suffix=".json", delete=False
        ) as f:
            json.dump(mock_device_context, f)
            custom_path = f.name
        
        try:
            config = PCILeechGenerationConfig(
                device_bdf="0000:01:00.0",
                board="test_board",
                output_dir=Path("/tmp/test_output"),
            )
            
            with patch.dict(os.environ, {"DEVICE_CONTEXT_PATH": custom_path}):
                generator = PCILeechGenerator(config)
                
                # Verify the custom path is used
                result = generator._analyze_configuration_space()
                assert "raw_config_space" in result
                
        finally:
            os.unlink(custom_path)


class TestContextManagerEdgeCases:
    """Test edge cases in context manager implementations."""

    def test_vfio_binder_multiple_exceptions(self):
        """Test handling of multiple exceptions in context manager."""
        with patch("os.geteuid", return_value=0):
            with patch("src.cli.vfio_handler._get_iommu_group", return_value="42"):
                with patch("pathlib.Path.exists", return_value=True):
                    binder = VFIOBinder("0000:01:00.0")
                    binder._bound = True
                    
                    # Simulate exception during unbind
                    with patch.object(
                        binder, "unbind", side_effect=Exception("Unbind error")
                    ):
                        # Should handle exception in __exit__ gracefully
                        result = binder.__exit__(
                            ValueError, ValueError("Original error"), None
                        )
                        
                        # Should not suppress the original exception
                        assert result is None or result is False

    def test_context_manager_with_keyboard_interrupt(self):
        """Test context manager handles KeyboardInterrupt correctly."""
        import logging
        context = VFIODeviceManager(
            device_bdf="0000:01:00.0",
            logger=logging.getLogger("test"),
        )
        
        with patch.object(context, "close") as mock_close:
            # Simulate KeyboardInterrupt
            result = context.__exit__(KeyboardInterrupt, KeyboardInterrupt(), None)
            
            # Should still call close
            mock_close.assert_called_once()
            
            # Should not suppress KeyboardInterrupt
            assert result is None or result is False

    def test_context_manager_with_system_exit(self):
        """Test context manager handles SystemExit correctly."""
        import logging
        context = VFIODeviceManager(
            device_bdf="0000:01:00.0",
            logger=logging.getLogger("test"),
        )
        
        with patch.object(context, "close") as mock_close:
            # Simulate SystemExit
            result = context.__exit__(SystemExit, SystemExit(1), None)
            
            # Should still call close
            mock_close.assert_called_once()
            
            # Should not suppress SystemExit
            assert result is None or result is False


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
