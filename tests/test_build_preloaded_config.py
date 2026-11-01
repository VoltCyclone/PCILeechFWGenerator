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

from pcileechfwgenerator.build import BuildConfiguration, FirmwareBuilder
from pcileechfwgenerator.exceptions import ConfigurationError
from tests.snapshot_helpers import make_snapshot

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
    return make_snapshot(temp_dir, mock_config_space, bdf="0000:63:00.0")


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
        """Test that explicitly missing device context stops the build."""
        # Set environment variable to non-existent file
        with mock.patch.dict(os.environ, {"DEVICE_CONTEXT_PATH": "/nonexistent/file.json"}):
            config = BuildConfiguration(
                bdf=valid_bdf,
                board=valid_board,
                output_dir=temp_dir,
            )

            with pytest.raises(ConfigurationError):
                FirmwareBuilder(config)

    def test_load_preloaded_config_space_invalid_hex(
        self, temp_dir, valid_bdf, valid_board
    ):
        """Test that invalid hex stops the build."""
        context_file = temp_dir / "bad_context.json"
        with open(context_file, "w") as f:
            json.dump({"config_space_hex": "not_valid_hex"}, f)

        with mock.patch.dict(os.environ, {"DEVICE_CONTEXT_PATH": str(context_file)}):
            config = BuildConfiguration(
                bdf=valid_bdf,
                board=valid_board,
                output_dir=temp_dir,
            )

            with pytest.raises(ConfigurationError):
                FirmwareBuilder(config)

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
            patch_path = "pcileechfwgenerator.device_clone.pcileech_generator.PCILeechGenerator"
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

            patch_path = "pcileechfwgenerator.device_clone.pcileech_generator.PCILeechGenerator"
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
            context_file = make_snapshot(temp_dir, mock_config_space, bdf=valid_bdf)
            payload = json.loads(context_file.read_text())
            payload["config_space_hex"] = hex_string
            context_file.write_text(json.dumps(payload))
            with mock.patch.dict(
                os.environ, {"DEVICE_CONTEXT_PATH": str(context_file)}
            ):
                config = BuildConfiguration(
                    bdf=valid_bdf, board=valid_board, output_dir=temp_dir
                )
                if should_succeed:
                    builder = FirmwareBuilder(config)
                    assert builder._load_preloaded_config_space() == mock_config_space
                else:
                    with pytest.raises(ConfigurationError):
                        FirmwareBuilder(config)

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

            with pytest.raises(ConfigurationError):
                FirmwareBuilder(config)

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

            with pytest.raises(ConfigurationError):
                FirmwareBuilder(config)


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
            patch_path = "pcileechfwgenerator.device_clone.pcileech_generator.PCILeechGenerator"
            with mock.patch(patch_path) as MockGenerator:
                mock_gen_instance = mock.MagicMock()
                MockGenerator.return_value = mock_gen_instance
                
                builder = FirmwareBuilder(config)
                
                # Verify generator received preloaded config space
                call_args = MockGenerator.call_args[0][0]
                assert call_args.preloaded_config_space == mock_config_space


def test_host_context_device_config_construction(
    temp_dir, valid_bdf, valid_board
):
    """Test that device_config is constructed from host context when missing."""
    # Host capture has raw IDs and no rendered device_config yet.
    raw = bytearray(4096)
    raw[:4] = bytes.fromhex("12191400")
    raw[8:12] = bytes.fromhex("a130030c")
    raw[44:48] = bytes.fromhex("34127856")
    from pcileechfwgenerator.host_collect.snapshot import device_identity

    # The host-context path needs a rendered template_context to skip the
    # generator; a plain identity capture is rendered instead.
    template_context = {"config_space_hex": bytes(raw).hex(), **device_identity(raw)}
    context_file = make_snapshot(
        temp_dir,
        bytes(raw),
        bdf=valid_bdf,
        context={"template_context": template_context},
    )

    with mock.patch.dict(
        os.environ, {"DEVICE_CONTEXT_PATH": str(context_file)}
    ):
        config = BuildConfiguration(
            bdf=valid_bdf,
            board=valid_board,
            output_dir=temp_dir,
        )

        # Mock all the necessary components
        with mock.patch(
            "pcileechfwgenerator.device_clone.pcileech_generator.PCILeechGenerator"
        ) as mock_gen_class, mock.patch(
            "pcileechfwgenerator.build.FirmwareBuilder._validate_board_template"
        ), mock.patch(
            "pcileechfwgenerator.build.FirmwareBuilder._generate_firmware"
        ), mock.patch(
            "pcileechfwgenerator.build.FirmwareBuilder._write_modules"
        ), mock.patch(
            "pcileechfwgenerator.build.FirmwareBuilder._generate_profile"
        ), mock.patch(
            "pcileechfwgenerator.build.FirmwareBuilder._generate_tcl_scripts"
        ), mock.patch(
            "pcileechfwgenerator.build.FirmwareBuilder._write_xdc_files"
        ), mock.patch(
            "pcileechfwgenerator.build.FirmwareBuilder._save_device_info"
        ), mock.patch(
            "pcileechfwgenerator.build.FirmwareBuilder._run_post_build_validation"
        ), mock.patch(
            "pcileechfwgenerator.build.FirmwareBuilder._save_file_manifest"
        ), mock.patch(
            "pcileechfwgenerator.file_management.file_manager.FileManager"
        ) as mock_fm_class:

            mock_gen = mock.MagicMock()
            mock_gen_class.return_value = mock_gen
            mock_fm = mock.MagicMock()
            mock_fm_class.return_value = mock_fm
            mock_fm.list_artifacts.return_value = []

            builder = FirmwareBuilder(config)

            # Capture the generation_result created in build()
            original_store_device_config = builder._store_device_config
            captured_result = None

            def capture_result(result):
                nonlocal captured_result
                captured_result = result
                return original_store_device_config(result)

            builder._store_device_config = capture_result

            # Run build
            builder.build()

            # Verify device_config was constructed in template_context
            assert captured_result is not None
            assert "template_context" in captured_result
            template_context = captured_result["template_context"]
            assert "device_config" in template_context

            device_config = template_context["device_config"]
            assert device_config["vendor_id"] == "1912"
            assert device_config["device_id"] == "0014"
            assert device_config["class_code"] == "0c0330"
            assert device_config["revision_id"] == "a1"
            assert device_config["subsystem_vendor_id"] == "1234"
            assert device_config["subsystem_device_id"] == "5678"
            assert device_config["device_bdf"] == valid_bdf
            assert device_config["enable_perf_counters"] == False
            assert device_config["enable_advanced_features"] == False
            assert device_config["enable_error_injection"] == False
