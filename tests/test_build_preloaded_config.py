"""
Unit tests for preloaded config space functionality in build.py

Tests that config space collected on the host is properly passed to
the PCILeechGenerator to avoid redundant VFIO binding in containers.
"""

import json
import os
import sys
import tempfile
from pathlib import Path
from unittest import mock

import pytest

# Add project root to Python path
project_root = Path(__file__).parent.parent.resolve()
if str(project_root) not in sys.path:
    sys.path.insert(0, str(project_root))

from src.build import BuildConfiguration, FirmwareBuilder


@pytest.fixture
def temp_dir():
    """Create a temporary directory for test outputs."""
    with tempfile.TemporaryDirectory() as tmpdir:
        yield Path(tmpdir)


@pytest.fixture
def valid_bdf():
    """Return a valid BDF string."""
    return "0000:63:00.0"


@pytest.fixture
def valid_board():
    """Return a valid board name."""
    return "pcileech_100t484_x1"


@pytest.fixture
def mock_config_space():
    """Generate mock config space bytes (256 bytes minimum)."""
    # Create realistic config space with proper header
    config_space = bytearray(4096)  # Extended config space
    
    # Standard PCI header
    config_space[0x00:0x02] = b'\x12\x19'  # Vendor ID: 0x1912
    config_space[0x02:0x04] = b'\x14\x00'  # Device ID: 0x0014
    config_space[0x04:0x06] = b'\x02\x00'  # Command
    config_space[0x06:0x08] = b'\x10\x00'  # Status
    config_space[0x08] = 0x03              # Revision ID
    config_space[0x09:0x0C] = b'\x30\x03\x0c'  # Class code: 0x0c0330 (USB)
    config_space[0x0E] = 0x00              # Header type
    
    # BAR 0 - 64-bit MMIO
    config_space[0x10:0x14] = b'\x04\x00\x00\xa4'  # BAR0 lower
    config_space[0x14:0x18] = b'\x00\x00\x00\x00'  # BAR0 upper
    
    return bytes(config_space)


@pytest.fixture
def device_context_file(temp_dir, mock_config_space):
    """Create a device_context.json file with preloaded config space."""
    context_data = {
        "bdf": "0000:63:00.0",
        "config_space_hex": mock_config_space.hex(),
        "device_info": {
            "vendor_id": "0x1912",
            "device_id": "0x0014",
            "class_code": "0x0c0330",
            "revision_id": "0x03",
            "command": "0x0002",
            "status": "0x0010",
            "header_type": "0x00",
            "subsystem_vendor": "0x1912",
            "subsystem_device": "0x0014",
            "cache_line_size": "16",
            "latency_timer": "0",
            "bist": "0x00",
        },
        "msix_data": {
            "preloaded": True,
            "num_vectors": 8,
            "table_bir": 0,
            "table_offset": "0x1000",
            "pba_bir": 0,
            "pba_offset": "0x1080",
        },
        "collection_metadata": {
            "collected_at": 1234567890.0,
            "config_space_size": len(mock_config_space),
            "has_msix": True,
            "collector_version": "1.0"
        }
    }
    
    context_file = temp_dir / "device_context.json"
    with open(context_file, "w") as f:
        json.dump(context_data, f, indent=2)
    
    return context_file


class TestPreloadedConfigSpace:
    """Tests for preloaded config space functionality."""

    def test_load_preloaded_config_space_success(
        self, temp_dir, valid_bdf, valid_board, device_context_file, mock_config_space
    ):
        """Test that preloaded config space is loaded from device_context.json."""
        # Set environment variable to point to the device context file
        with mock.patch.dict(os.environ, {"DEVICE_CONTEXT_PATH": str(device_context_file)}):
            config = BuildConfiguration(
                bdf=valid_bdf,
                board=valid_board,
                output_dir=temp_dir,
            )
            
            builder = FirmwareBuilder(config)
            
            # Call the private method to load preloaded config space
            loaded_config_space = builder._load_preloaded_config_space()
            
            # Verify config space was loaded
            assert loaded_config_space is not None
            assert isinstance(loaded_config_space, bytes)
            assert len(loaded_config_space) == len(mock_config_space)
            assert loaded_config_space == mock_config_space

    def test_load_preloaded_config_space_missing_file(
        self, temp_dir, valid_bdf, valid_board
    ):
        """Test that missing device context file returns None."""
        # Set environment variable to non-existent file
        with mock.patch.dict(os.environ, {"DEVICE_CONTEXT_PATH": "/nonexistent/file.json"}):
            config = BuildConfiguration(
                bdf=valid_bdf,
                board=valid_board,
                output_dir=temp_dir,
            )
            
            builder = FirmwareBuilder(config)
            loaded_config_space = builder._load_preloaded_config_space()
            
            # Should return None gracefully
            assert loaded_config_space is None

    def test_load_preloaded_config_space_invalid_hex(
        self, temp_dir, valid_bdf, valid_board
    ):
        """Test that invalid hex string in device context returns None."""
        context_file = temp_dir / "bad_context.json"
        with open(context_file, "w") as f:
            json.dump({"config_space_hex": "not_valid_hex"}, f)
        
        with mock.patch.dict(os.environ, {"DEVICE_CONTEXT_PATH": str(context_file)}):
            config = BuildConfiguration(
                bdf=valid_bdf,
                board=valid_board,
                output_dir=temp_dir,
            )
            
            builder = FirmwareBuilder(config)
            loaded_config_space = builder._load_preloaded_config_space()
            
            # Should return None on error
            assert loaded_config_space is None

    def test_preloaded_config_space_passed_to_generator(
        self, temp_dir, valid_bdf, valid_board, device_context_file, 
        mock_config_space
    ):
        """Test that preloaded config space is passed to PCILeechGenerator."""
        context_path = str(device_context_file)
        with mock.patch.dict(os.environ, {"DEVICE_CONTEXT_PATH": context_path}):
            config = BuildConfiguration(
                bdf=valid_bdf,
                board=valid_board,
                output_dir=temp_dir,
            )
            
            # Mock PCILeechGenerator at import location within FirmwareBuilder
            patch_path = "src.device_clone.pcileech_generator.PCILeechGenerator"
            with mock.patch(patch_path) as MockGenerator:
                builder = FirmwareBuilder(config)
                
                # Verify generator was initialized with preloaded config space
                MockGenerator.assert_called_once()
                call_args = MockGenerator.call_args
                
                # Check config passed to generator has preloaded_config_space
                generator_config = call_args[0][0]  # First positional arg
                assert hasattr(generator_config, "preloaded_config_space")
                assert generator_config.preloaded_config_space == mock_config_space

    def test_preloaded_config_space_none_when_not_available(
        self, temp_dir, valid_bdf, valid_board
    ):
        """Test that None is passed when no preloaded config space available."""
        # Ensure no device context path is set
        env_without_context = {
            k: v for k, v in os.environ.items()
            if k != "DEVICE_CONTEXT_PATH"
        }
        
        with mock.patch.dict(os.environ, env_without_context, clear=True):
            config = BuildConfiguration(
                bdf=valid_bdf,
                board=valid_board,
                output_dir=temp_dir,
            )
            
            patch_path = "src.device_clone.pcileech_generator.PCILeechGenerator"
            with mock.patch(patch_path) as MockGenerator:
                builder = FirmwareBuilder(config)
                
                # Verify generator initialized with None for preloaded config
                MockGenerator.assert_called_once()
                call_args = MockGenerator.call_args
                
                generator_config = call_args[0][0]
                assert hasattr(generator_config, "preloaded_config_space")
                assert generator_config.preloaded_config_space is None

    def test_config_space_hex_format_validation(
        self, temp_dir, valid_bdf, valid_board, mock_config_space
    ):
        """Test that various hex formats are handled correctly."""
        test_cases = [
            # (hex_string, should_succeed)
            (mock_config_space.hex(), True),  # Lowercase hex
            (mock_config_space.hex().upper(), True),  # Uppercase hex
            ("", False),  # Empty string
            ("zzzz", False),  # Invalid hex chars
        ]
        
        for hex_string, should_succeed in test_cases:
            context_file = temp_dir / f"context_{hex_string[:8]}.json"
            with open(context_file, "w") as f:
                json.dump({"config_space_hex": hex_string}, f)
            
            context_path = str(context_file)
            with mock.patch.dict(
                os.environ, {"DEVICE_CONTEXT_PATH": context_path}
            ):
                config = BuildConfiguration(
                    bdf=valid_bdf,
                    board=valid_board,
                    output_dir=temp_dir,
                )
                
                builder = FirmwareBuilder(config)
                loaded = builder._load_preloaded_config_space()
                
                if should_succeed:
                    assert loaded is not None
                    assert isinstance(loaded, bytes)
                else:
                    assert loaded is None

    def test_config_space_too_small(
        self, temp_dir, valid_bdf, valid_board
    ):
        """Test that config space smaller than 64 bytes is rejected."""
        # Create a config space that's too small (32 bytes)
        small_config = bytearray(32)
        context_file = temp_dir / "context_small.json"
        
        with open(context_file, "w") as f:
            json.dump({"config_space_hex": small_config.hex()}, f)
        
        with mock.patch.dict(
            os.environ, {"DEVICE_CONTEXT_PATH": str(context_file)}
        ):
            config = BuildConfiguration(
                bdf=valid_bdf,
                board=valid_board,
                output_dir=temp_dir,
            )
            
            builder = FirmwareBuilder(config)
            loaded = builder._load_preloaded_config_space()
            
            # Should return None due to size check
            assert loaded is None

    def test_malformed_json_handling(
        self, temp_dir, valid_bdf, valid_board
    ):
        """Test that malformed JSON is handled gracefully."""
        context_file = temp_dir / "context_malformed.json"
        
        # Write invalid JSON
        with open(context_file, "w") as f:
            f.write("{invalid json content")
        
        with mock.patch.dict(
            os.environ, {"DEVICE_CONTEXT_PATH": str(context_file)}
        ):
            config = BuildConfiguration(
                bdf=valid_bdf,
                board=valid_board,
                output_dir=temp_dir,
            )
            
            builder = FirmwareBuilder(config)
            loaded = builder._load_preloaded_config_space()
            
            # Should return None gracefully without crashing
            assert loaded is None


class TestPreloadedConfigSpaceIntegration:
    """Integration tests verifying end-to-end preload behavior."""

    def test_generator_uses_preloaded_config_not_vfio(
        self, temp_dir, valid_bdf, valid_board, device_context_file, 
        mock_config_space
    ):
        """Test generator uses preloaded config, not VFIO binding."""
        context_path = str(device_context_file)
        with mock.patch.dict(os.environ, {"DEVICE_CONTEXT_PATH": context_path}):
            config = BuildConfiguration(
                bdf=valid_bdf,
                board=valid_board,
                output_dir=temp_dir,
            )
            
            # Mock the PCILeechGenerator class
            patch_path = "src.device_clone.pcileech_generator.PCILeechGenerator"
            with mock.patch(patch_path) as MockGenerator:
                mock_gen_instance = mock.MagicMock()
                MockGenerator.return_value = mock_gen_instance
                
                builder = FirmwareBuilder(config)
                
                # Verify generator received preloaded config space
                call_args = MockGenerator.call_args[0][0]
                assert call_args.preloaded_config_space == mock_config_space
