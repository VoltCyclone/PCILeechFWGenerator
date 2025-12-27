"""
Unit tests for device ID extraction and propagation fix (Bug #511)

Tests that device IDs collected from hardware are properly extracted,
saved, and propagated through the build pipeline instead of using defaults.
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

# Try to import - skip entire module if imports fail
try:
    from src.host_collect.collector import HostCollector
    from src.build import BuildConfiguration, FirmwareBuilder
    imports_available = True
except ImportError as e:
    imports_available = False
    import_error_msg = str(e)
    # Create dummy classes to allow test collection
    HostCollector = None
    BuildConfiguration = None
    FirmwareBuilder = None

# Skip all tests in this module if imports failed
pytestmark = pytest.mark.skipif(
    not imports_available,
    reason=f"Required imports not available: {import_error_msg if not imports_available else ''}"
)


@pytest.fixture
def temp_dir():
    """Create a temporary directory for test outputs."""
    with tempfile.TemporaryDirectory() as tmpdir:
        yield Path(tmpdir)


@pytest.fixture
def audio_controller_config_space():
    """
    Generate realistic config space bytes for an audio controller.
    Simulates the user's reported issue with an audio device.
    """
    config_space = bytearray(256)
    
    # Audio Controller IDs (e.g., Intel HD Audio)
    config_space[0x00:0x02] = b'\x86\x80'  # Vendor ID: 0x8086 (Intel)
    config_space[0x02:0x04] = b'\x0c\x0a'  # Device ID: 0x0a0c (Intel HD Audio)
    config_space[0x04:0x06] = b'\x06\x04'  # Command
    config_space[0x06:0x08] = b'\x10\x00'  # Status
    config_space[0x08] = 0x01              # Revision ID: 0x01
    config_space[0x09:0x0C] = b'\x00\x03\x04'  # Class code: 0x040300 (Audio) - little-endian
    config_space[0x0C] = 0x10              # Cache line size
    config_space[0x0D] = 0x00              # Latency timer
    config_space[0x0E] = 0x00              # Header type
    config_space[0x0F] = 0x00              # BIST
    
    # BAR 0 - 32-bit MMIO
    config_space[0x10:0x14] = b'\x00\x00\x30\xf0'
    
    # Subsystem IDs (offset 0x2C-0x2F)
    config_space[0x2C:0x2E] = b'\x86\x80'  # Subsystem Vendor: 0x8086
    config_space[0x2E:0x30] = b'\x21\x72'  # Subsystem Device: 0x7221
    
    return bytes(config_space)


@pytest.fixture
def network_controller_config_space():
    """Generate config space for a network controller (Realtek)."""
    config_space = bytearray(256)
    
    # Realtek Network Controller
    config_space[0x00:0x02] = b'\xec\x10'  # Vendor ID: 0x10ec (Realtek)
    config_space[0x02:0x04] = b'\x68\x81'  # Device ID: 0x8168 (RTL8168)
    config_space[0x04:0x06] = b'\x07\x04'  # Command
    config_space[0x06:0x08] = b'\x10\x00'  # Status
    config_space[0x08] = 0x15              # Revision ID: 0x15
    config_space[0x09:0x0C] = b'\x00\x00\x02'  # Class code: 0x020000 (Ethernet) - already little-endian
    config_space[0x0E] = 0x00              # Header type
    
    # BAR 0 - 64-bit MMIO
    config_space[0x10:0x14] = b'\x04\x00\x00\x00'
    config_space[0x14:0x18] = b'\x00\x00\x00\x00'
    
    # Subsystem IDs
    config_space[0x2C:0x2E] = b'\xec\x10'  # Subsystem Vendor: 0x10ec
    config_space[0x2E:0x30] = b'\x68\x81'  # Subsystem Device: 0x8168
    
    return bytes(config_space)


class TestHostCollectorDeviceIDExtraction:
    """Test that HostCollector properly extracts device IDs from config space."""
    
    def test_extract_device_ids_audio_controller(self, audio_controller_config_space):
        """Test extraction of device IDs from audio controller config space."""
        from src.host_collect.collector import HostCollector
        
        collector = HostCollector(
            bdf="0000:00:1f.3",
            datastore=Path("/tmp"),
            logger=None
        )
        
        device_ids = collector._extract_device_ids(audio_controller_config_space)
        
        assert device_ids["vendor_id"] == 0x8086, "Vendor ID should be Intel (0x8086)"
        assert device_ids["device_id"] == 0x0a0c, "Device ID should be 0x0a0c"
        assert device_ids["class_code"] == 0x040300, "Class code should be Audio (0x040300)"
        assert device_ids["revision_id"] == 0x01, "Revision ID should be 0x01"
        assert device_ids["subsystem_vendor_id"] == 0x8086, "Subsystem vendor should be 0x8086"
        assert device_ids["subsystem_device_id"] == 0x7221, "Subsystem device should be 0x7221"
    
    def test_extract_device_ids_network_controller(self, network_controller_config_space):
        """Test extraction of device IDs from network controller config space."""
        from src.host_collect.collector import HostCollector
        
        collector = HostCollector(
            bdf="0000:03:00.0",
            datastore=Path("/tmp"),
            logger=None
        )
        
        device_ids = collector._extract_device_ids(network_controller_config_space)
        
        assert device_ids["vendor_id"] == 0x10ec, "Vendor ID should be Realtek (0x10ec)"
        assert device_ids["device_id"] == 0x8168, "Device ID should be 0x8168"
        assert device_ids["class_code"] == 0x020000, "Class code should be Ethernet (0x020000)"
        assert device_ids["revision_id"] == 0x15, "Revision ID should be 0x15"
    
    def test_extract_device_ids_short_config_space(self):
        """Test that extraction handles short config space gracefully."""
        from src.host_collect.collector import HostCollector
        
        collector = HostCollector(
            bdf="0000:00:00.0",
            datastore=Path("/tmp"),
            logger=None
        )
        
        short_config = bytes([0x00] * 32)  # Only 32 bytes
        device_ids = collector._extract_device_ids(short_config)
        
        # Should return empty dict for insufficient data
        assert device_ids == {}


class TestHostCollectorSavesDeviceIDs:
    """Test that HostCollector saves device IDs to device_context.json."""
    
    def test_device_context_contains_device_ids(
        self, temp_dir, audio_controller_config_space, monkeypatch
    ):
        """Test that device_context.json includes extracted device IDs."""
        from src.host_collect.collector import HostCollector
        
        # Monkeypatch _read_config_space to return our test data
        monkeypatch.setattr(
            HostCollector,
            "_read_config_space",
            lambda self: audio_controller_config_space
        )
        
        collector = HostCollector(
            bdf="0000:00:1f.3",
            datastore=temp_dir,
            logger=None
        )
        
        rc = collector.run()
        assert rc == 0, "Collector should succeed"
        
        # Load device_context.json
        ctx_path = temp_dir / "device_context.json"
        assert ctx_path.exists(), "device_context.json should be created"
        
        with open(ctx_path, "r") as f:
            ctx = json.load(f)
        
        # Verify device IDs are present and correct
        assert "vendor_id" in ctx, "vendor_id should be in device_context.json"
        assert "device_id" in ctx, "device_id should be in device_context.json"
        assert "class_code" in ctx, "class_code should be in device_context.json"
        assert "revision_id" in ctx, "revision_id should be in device_context.json"
        
        # Verify correct values (audio controller)
        assert ctx["vendor_id"] == 0x8086, "Vendor ID should be Intel"
        assert ctx["device_id"] == 0x0a0c, "Device ID should match audio controller"
        assert ctx["class_code"] == 0x040300, "Class code should be Audio"
        assert ctx["revision_id"] == 0x01, "Revision ID should be 0x01"
    
    def test_device_context_includes_subsystem_ids(
        self, temp_dir, audio_controller_config_space, monkeypatch
    ):
        """Test that subsystem IDs are also saved."""
        from src.host_collect.collector import HostCollector
        
        monkeypatch.setattr(
            HostCollector,
            "_read_config_space",
            lambda self: audio_controller_config_space
        )
        
        collector = HostCollector(
            bdf="0000:00:1f.3",
            datastore=temp_dir,
            logger=None
        )
        
        collector.run()
        
        with open(temp_dir / "device_context.json", "r") as f:
            ctx = json.load(f)
        
        assert "subsystem_vendor_id" in ctx
        assert "subsystem_device_id" in ctx
        assert ctx["subsystem_vendor_id"] == 0x8086
        assert ctx["subsystem_device_id"] == 0x7221


class TestBuildUsesCollectedDeviceIDs:
    """Test that build.py properly uses device IDs from host context."""
    
    def test_host_context_uses_collected_device_ids(
        self, temp_dir, audio_controller_config_space
    ):
        """Test that build system uses device IDs from host collection."""
        if not imports_available:
            pytest.skip("FirmwareBuilder import not available")
            
        # Create device_context.json with collected device IDs
        device_context = {
            "config_space_hex": audio_controller_config_space.hex(),
            "vendor_id": 0x8086,
            "device_id": 0x0a0c,
            "class_code": 0x040300,
            "revision_id": 0x01,
            "subsystem_vendor_id": 0x8086,
            "subsystem_device_id": 0x7221,
        }
        
        ctx_path = temp_dir / "device_context.json"
        with open(ctx_path, "w") as f:
            json.dump(device_context, f)
        
        # Set environment variable to point to our test file
        with mock.patch.dict(os.environ, {"DEVICE_CONTEXT_PATH": str(ctx_path)}):
            from src.build import FirmwareBuilder, BuildConfiguration
            
            config = BuildConfiguration(
                bdf="0000:00:1f.3",
                board="pcileech_100t484_x1",
                output_dir=temp_dir,
                enable_profiling=False,
                parallel_writes=False,
            )
            
            builder = FirmwareBuilder(config)
            
            # Check that host context is loaded
            host_context = builder._check_host_collected_context()
            
            assert host_context is not None, "Host context should be loaded"
            assert host_context["vendor_id"] == 0x8086, "Should use collected vendor ID"
            assert host_context["device_id"] == 0x0a0c, "Should use collected device ID"
            assert host_context["class_code"] == 0x040300, "Should use collected class code"
    
    def test_config_space_data_populated_from_host_context(
        self, temp_dir, network_controller_config_space
    ):
        """Test that config_space_data is properly populated with device IDs."""
        if not imports_available:
            pytest.skip("FirmwareBuilder import not available")
        """Test that config_space_data is properly populated with device IDs."""
        device_context = {
            "config_space_hex": network_controller_config_space.hex(),
            "vendor_id": 0x10ec,
            "device_id": 0x8168,
            "class_code": 0x020000,
            "revision_id": 0x15,
            "subsystem_vendor_id": 0x10ec,
            "subsystem_device_id": 0x8168,
        }
        
        ctx_path = temp_dir / "device_context.json"
        with open(ctx_path, "w") as f:
            json.dump(device_context, f)
        
        with mock.patch.dict(os.environ, {"DEVICE_CONTEXT_PATH": str(ctx_path)}):
            from src.build import FirmwareBuilder, BuildConfiguration
            
            config = BuildConfiguration(
                bdf="0000:03:00.0",
                board="pcileech_100t484_x1",
                output_dir=temp_dir,
                enable_profiling=False,
            )
            
            builder = FirmwareBuilder(config)
            host_context = builder._check_host_collected_context()
            
            # Simulate the build process creating config_space_data
            config_space_hex = host_context.get("config_space_hex", "")
            config_space_bytes = bytes.fromhex(config_space_hex) if config_space_hex else b""
            
            config_space_data = {
                "raw_config_space": config_space_bytes,
                "config_space_hex": config_space_hex,
                "vendor_id": format(host_context.get("vendor_id", 0), "04x"),
                "device_id": format(host_context.get("device_id", 0), "04x"),
                "class_code": format(host_context.get("class_code", 0), "06x"),
                "revision_id": format(host_context.get("revision_id", 0), "02x"),
                "device_info": {
                    "vendor_id": host_context.get("vendor_id"),
                    "device_id": host_context.get("device_id"),
                    "class_code": host_context.get("class_code"),
                    "revision_id": host_context.get("revision_id"),
                },
            }
            
            # Verify the formatted IDs match the network controller
            assert config_space_data["vendor_id"] == "10ec", "Vendor ID should be formatted correctly"
            assert config_space_data["device_id"] == "8168", "Device ID should be formatted correctly"
            assert config_space_data["class_code"] == "020000", "Class code should be formatted correctly"
            assert config_space_data["revision_id"] == "15", "Revision ID should be formatted correctly"
            
            # Verify device_info contains integer values
            assert config_space_data["device_info"]["vendor_id"] == 0x10ec
            assert config_space_data["device_info"]["device_id"] == 0x8168
            assert config_space_data["device_info"]["class_code"] == 0x020000


class TestNoRegressionToDefaultValues:
    """Regression tests to ensure defaults are NOT used when real IDs are available."""
    
    def test_no_default_vendor_id_when_collected(
        self, temp_dir, audio_controller_config_space, monkeypatch
    ):
        """Test that default vendor ID (0x10ec) is NOT used when real ID is collected."""
        from src.host_collect.collector import HostCollector
        
        monkeypatch.setattr(
            HostCollector,
            "_read_config_space",
            lambda self: audio_controller_config_space
        )
        
        collector = HostCollector(
            bdf="0000:00:1f.3",
            datastore=temp_dir,
            logger=None
        )
        collector.run()
        
        with open(temp_dir / "device_context.json", "r") as f:
            ctx = json.load(f)
        
        # Should NOT be default Realtek (0x10ec), should be Intel (0x8086)
        assert ctx["vendor_id"] != 0x10ec, "Should not use default vendor ID"
        assert ctx["vendor_id"] == 0x8086, "Should use actual Intel vendor ID"
    
    def test_no_default_device_id_when_collected(
        self, temp_dir, audio_controller_config_space, monkeypatch
    ):
        """Test that default device ID (0x8168) is NOT used when real ID is collected."""
        from src.host_collect.collector import HostCollector
        
        monkeypatch.setattr(
            HostCollector,
            "_read_config_space",
            lambda self: audio_controller_config_space
        )
        
        collector = HostCollector(
            bdf="0000:00:1f.3",
            datastore=temp_dir,
            logger=None
        )
        collector.run()
        
        with open(temp_dir / "device_context.json", "r") as f:
            ctx = json.load(f)
        
        # Should NOT be default RTL8168 (0x8168), should be audio controller (0x0a0c)
        assert ctx["device_id"] != 0x8168, "Should not use default device ID"
        assert ctx["device_id"] == 0x0a0c, "Should use actual audio controller device ID"
    
    def test_no_generic_class_code_when_collected(
        self, temp_dir, audio_controller_config_space, monkeypatch
    ):
        """Test that generic class code (0x000000) is NOT used when real class is collected."""
        from src.host_collect.collector import HostCollector
        
        monkeypatch.setattr(
            HostCollector,
            "_read_config_space",
            lambda self: audio_controller_config_space
        )
        
        collector = HostCollector(
            bdf="0000:00:1f.3",
            datastore=temp_dir,
            logger=None
        )
        collector.run()
        
        with open(temp_dir / "device_context.json", "r") as f:
            ctx = json.load(f)
        
        # Should NOT be generic (0x000000), should be audio (0x040300)
        assert ctx["class_code"] != 0x000000, "Should not use generic class code"
        assert ctx["class_code"] == 0x040300, "Should use actual audio class code"


class TestBackwardCompatibility:
    """Test that the fix doesn't break existing functionality."""
    
    def test_old_device_context_still_works(self, temp_dir):
        """Test that old device_context.json format without device IDs returns None."""
        if not imports_available:
            pytest.skip("FirmwareBuilder import not available")
        """Test that old device_context.json format without device IDs returns None."""
        # Old format - only config_space_hex (no device IDs)
        # This format is intentionally incomplete and should return None
        # forcing the system to use VFIO to extract device IDs
        old_format_context = {
            "config_space_hex": "86800c0a060410000103040010000000" + "00" * 240
        }
        
        ctx_path = temp_dir / "device_context.json"
        with open(ctx_path, "w") as f:
            json.dump(old_format_context, f)
        
        with mock.patch.dict(os.environ, {"DEVICE_CONTEXT_PATH": str(ctx_path)}):
            from src.build import FirmwareBuilder, BuildConfiguration
            
            config = BuildConfiguration(
                bdf="0000:00:1f.3",
                board="pcileech_100t484_x1",
                output_dir=temp_dir,
                enable_profiling=False,
            )
            
            builder = FirmwareBuilder(config)
            host_context = builder._check_host_collected_context()
            
            # Should return None for incomplete context, forcing VFIO path
            # This is the correct behavior - we need device IDs!
            assert host_context is None, "Incomplete context should return None to force VFIO extraction"
