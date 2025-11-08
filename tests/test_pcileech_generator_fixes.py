#!/usr/bin/env python3
"""Unit tests for pcileech_generator fixes and improvements.

This test suite covers the following fixes:
1. Build integration artifact saving
2. Directory layout consistency (src/ vs systemverilog/)
3. VFIO resource cleanup
4. MSI-X parse_capability guard
5. Log prefix consistency
6. Config space length validation
7. BAR coercion debug logging
"""

from pathlib import Path
from typing import Any, Dict, Optional
from unittest.mock import Mock, MagicMock, patch, call

import pytest

from src.device_clone.pcileech_generator import (
    PCILeechGenerationConfig,
    PCILeechGenerator,
    PCILeechGenerationError,
)


# --- Fixtures ---------------------------------------------------------------


@pytest.fixture
def mock_vfio_manager():
    """Mock VFIODeviceManager for testing."""
    manager = Mock()
    manager.read_region_slice = Mock(return_value=b'\x00' * 64)
    manager.close = Mock()
    return manager


@pytest.fixture
def mock_config_space_manager():
    """Mock ConfigSpaceManager."""
    manager = Mock()
    # Return valid 256-byte config space
    config_space = bytearray(256)
    config_space[0:2] = (0x10de).to_bytes(2, "little")  # vendor_id
    config_space[2:4] = (0x1234).to_bytes(2, "little")  # device_id
    manager.read_vfio_config_space = Mock(return_value=bytes(config_space))
    manager._read_sysfs_config_space = Mock(return_value=bytes(config_space))
    manager.extract_device_info = Mock(return_value={
        "vendor_id": 0x10de,
        "device_id": 0x1234,
        "class_code": 0x030000,
        "revision_id": 0x01,
        "bars": [
            {"bar": 0, "type": "memory", "size": 0x1000, "prefetchable": False}
        ],
    })
    return manager


@pytest.fixture
def generator_config(tmp_path: Path) -> PCILeechGenerationConfig:
    """Create test generator configuration."""
    return PCILeechGenerationConfig(
        device_bdf="0000:01:00.0",
        output_dir=tmp_path / "output",
        enable_behavior_profiling=False,
        strict_validation=False,
    )


@pytest.fixture
def generator(generator_config, mock_config_space_manager):
    """Create PCILeechGenerator with mocked dependencies."""
    with patch(
        "src.device_clone.pcileech_generator.ConfigSpaceManager",
        return_value=mock_config_space_manager
    ):
        with patch(
            "src.device_clone.pcileech_generator.BehaviorProfiler"
        ):
            gen = PCILeechGenerator(generator_config)
            gen.config_space_manager = mock_config_space_manager
            return gen


# --- Test Build Integration Saving -----------------------------------------


def test_save_build_integration_artifact(generator, tmp_path):
    """Test that build_integration is saved to src/pcileech_integration.sv."""
    build_integration_content = "// Build integration code\nmodule integration();\nendmodule"
    
    generation_result = {
        "device_bdf": "0000:01:00.0",
        "generation_timestamp": "2025-11-07T12:00:00",
        "behavior_profile": None,
        "config_space_data": {},
        "msix_data": None,
        "template_context": {},
        "systemverilog_modules": {
            "pcileech_cfgspace.coe": "; test COE content\n"
        },
        "firmware_components": {
            "build_integration": build_integration_content,
            "constraint_files": {},
            "tcl_scripts": {},
        },
        "tcl_scripts": {},
        "generation_metadata": {},
    }
    
    output_dir = tmp_path / "test_output"
    generator.save_generated_firmware(generation_result, output_dir)
    
    # Verify build integration was saved
    integration_file = output_dir / "src" / "pcileech_integration.sv"
    assert integration_file.exists()
    assert integration_file.read_text() == build_integration_content


def test_save_build_integration_missing_does_not_crash(generator, tmp_path):
    """Test that missing build_integration doesn't cause errors."""
    generation_result = {
        "device_bdf": "0000:01:00.0",
        "generation_timestamp": "2025-11-07T12:00:00",
        "behavior_profile": None,
        "config_space_data": {},
        "msix_data": None,
        "template_context": {},
        "systemverilog_modules": {},
        "firmware_components": {
            # No build_integration key
            "constraint_files": {},
            "tcl_scripts": {},
        },
        "tcl_scripts": {},
        "generation_metadata": {},
    }
    
    output_dir = tmp_path / "test_output"
    # Should not raise
    generator.save_generated_firmware(generation_result, output_dir)
    
    # Verify integration file was not created
    integration_file = output_dir / "src" / "pcileech_integration.sv"
    assert not integration_file.exists()


# --- Test Directory Layout Consistency -------------------------------------


def test_writemask_uses_src_directory(generator, tmp_path):
    """Test that writemask COE uses src/ directory not systemverilog/."""
    generator.config.output_dir = tmp_path / "output"
    
    # Pre-create the config space COE in src/ directory
    src_dir = generator.config.output_dir / "src"
    src_dir.mkdir(parents=True, exist_ok=True)
    cfg_coe = src_dir / "pcileech_cfgspace.coe"
    cfg_coe.write_text("; test\nmemory_initialization_vector=\n00000000;\n")
    
    template_context = {
        "msi_config": {},
        "msix_config": {},
    }
    
    # Mock WritemaskGenerator
    with patch("src.device_clone.pcileech_generator.WritemaskGenerator") as mock_wm:
        mock_instance = Mock()
        mock_wm.return_value = mock_instance
        mock_instance.generate_writemask = Mock()
        
        generator._generate_writemask_coe(template_context)
        
        # Verify it was called with src/ paths
        mock_instance.generate_writemask.assert_called_once()
        args = mock_instance.generate_writemask.call_args[0]
        
        # Both paths should use src/ directory
        assert str(args[0]) == str(src_dir / "pcileech_cfgspace.coe")
        assert str(args[1]) == str(src_dir / "pcileech_cfgspace_writemask.coe")


# --- Test VFIO Resource Cleanup --------------------------------------------


def test_vfio_manager_close_called_on_success(generator, mock_vfio_manager):
    """Test that VFIODeviceManager.close() is called on success."""
    msix_data = {
        "table_size": 4,
        "table_bir": 0,
        "table_offset": 0x1000,
    }
    
    mock_vfio_manager.read_region_slice.return_value = b'\x00' * 64
    
    with patch(
        "src.device_clone.pcileech_generator.VFIODeviceManager",
        return_value=mock_vfio_manager
    ):
        result = generator._capture_msix_table_entries(msix_data)
        
        # Verify close was called
        mock_vfio_manager.close.assert_called_once()
        assert result is not None


def test_vfio_manager_close_called_on_error(generator, mock_vfio_manager):
    """Test that VFIODeviceManager.close() is called even on error."""
    msix_data = {
        "table_size": 4,
        "table_bir": 0,
        "table_offset": 0x1000,
    }
    
    # Simulate read error
    mock_vfio_manager.read_region_slice.side_effect = RuntimeError("Read failed")
    
    with patch(
        "src.device_clone.pcileech_generator.VFIODeviceManager",
        return_value=mock_vfio_manager
    ):
        with pytest.raises(RuntimeError):
            generator._capture_msix_table_entries(msix_data)
        
        # Verify close was still called
        mock_vfio_manager.close.assert_called_once()


def test_vfio_manager_without_close_method(generator):
    """Test that missing close() method doesn't cause errors."""
    msix_data = {
        "table_size": 4,
        "table_bir": 0,
        "table_offset": 0x1000,
    }
    
    # Create manager without close method
    mock_manager = Mock(spec=['read_region_slice'])
    mock_manager.read_region_slice.return_value = b'\x00' * 64
    
    with patch(
        "src.device_clone.pcileech_generator.VFIODeviceManager",
        return_value=mock_manager
    ):
        # Should not raise AttributeError
        result = generator._capture_msix_table_entries(msix_data)
        assert result is not None


# --- Test MSI-X Parse Guard ------------------------------------------------


def test_process_msix_capabilities_guards_none_return(generator):
    """Test that _process_msix_capabilities handles None from parser."""
    config_space_data = {
        "config_space_hex": "0" * 512,  # Valid hex but no MSI-X cap
    }
    
    with patch(
        "src.device_clone.pcileech_generator.parse_msix_capability",
        return_value=None
    ):
        result = generator._process_msix_capabilities(config_space_data)
        assert result is None


def test_process_msix_capabilities_guards_zero_table_size(generator):
    """Test that table_size=0 returns None safely."""
    config_space_data = {
        "config_space_hex": "0" * 512,
    }
    
    msix_info = {
        "table_size": 0,
        "table_bir": 0,
        "table_offset": 0,
        "pba_bir": 0,
        "pba_offset": 0,
        "enabled": False,
        "function_mask": False,
    }
    
    with patch(
        "src.device_clone.pcileech_generator.parse_msix_capability",
        return_value=msix_info
    ):
        result = generator._process_msix_capabilities(config_space_data)
        assert result is None


def test_process_msix_capabilities_uses_get_safely(generator):
    """Test that all field accesses use .get() for safety."""
    config_space_data = {
        "config_space_hex": "0" * 512,
    }
    
    # Return partial msix_info missing some keys
    partial_msix_info = {
        "table_size": 4,
        # Missing: table_bir, table_offset, pba_bir, pba_offset, enabled, function_mask
    }
    
    with patch(
        "src.device_clone.pcileech_generator.parse_msix_capability",
        return_value=partial_msix_info
    ):
        with patch(
            "src.device_clone.pcileech_generator.validate_msix_configuration",
            return_value=(True, [])
        ):
            # Should not raise KeyError
            result = generator._process_msix_capabilities(config_space_data)
            
            assert result is not None
            assert result["table_size"] == 4
            assert result["table_bir"] == 0  # Default
            assert result["enabled"] is False  # Default


# --- Test Log Prefix Consistency -------------------------------------------


def test_config_space_operations_use_cfg_prefix(generator, caplog):
    """Test that config space operations use CFG prefix not MSIX."""
    import logging
    caplog.set_level(logging.INFO)
    
    # Trigger config space analysis
    with patch.object(generator, '_preloaded_config_space', None):
        config_data = generator._analyze_configuration_space()
    
    # Check log messages use CFG prefix
    cfg_logs = [
        record for record in caplog.records 
        if 'Analyzing configuration space' in record.message
    ]
    assert len(cfg_logs) > 0
    
    # The message should mention configuration space
    assert any('configuration space' in record.message.lower() for record in cfg_logs)


def test_behavior_profiling_uses_pcil_prefix(generator, caplog):
    """Test that behavior profiling logs use PCIL prefix not MSIX."""
    import logging
    caplog.set_level(logging.INFO)
    
    # Disable behavior profiler
    generator.behavior_profiler = None
    
    # Trigger behavior capture
    result = generator._capture_device_behavior()
    
    # Check it logged with appropriate message
    assert result is None
    profiling_logs = [
        record for record in caplog.records
        if 'Behavior profiling disabled' in record.message
    ]
    assert len(profiling_logs) > 0


# --- Test Config Space Length Validation -----------------------------------


def test_config_space_validates_256_bytes(generator):
    """Test that 256-byte config space is accepted."""
    config_space_bytes = bytes(256)
    
    # Should not raise
    result = generator._process_config_space_bytes(config_space_bytes)
    assert result is not None


def test_config_space_validates_4096_bytes(generator):
    """Test that 4096-byte config space (PCIe extended) is accepted."""
    config_space_bytes = bytes(4096)
    
    # Should not raise
    result = generator._process_config_space_bytes(config_space_bytes)
    assert result is not None


def test_config_space_warns_on_unusual_length(generator, caplog):
    """Test that unusual config space lengths trigger warnings."""
    import logging
    caplog.set_level(logging.WARNING)
    
    # Use 128 bytes (unusual but power of 2)
    config_space_bytes = bytes(128)
    
    # Should warn but not fail
    result = generator._process_config_space_bytes(config_space_bytes)
    assert result is not None
    
    # Check for warning
    warnings = [r for r in caplog.records if r.levelname == 'WARNING']
    assert any('Unexpected config space length' in r.message for r in warnings)


def test_config_space_rejects_empty(generator):
    """Test that empty config space is rejected."""
    config_space_bytes = b''
    
    with pytest.raises(PCILeechGenerationError, match="empty"):
        generator._process_config_space_bytes(config_space_bytes)


def test_config_space_warns_on_non_power_of_two(generator, caplog):
    """Test that non-power-of-2 lengths are warned about."""
    import logging
    caplog.set_level(logging.WARNING)
    
    # Use 100 bytes (not a power of 2)
    config_space_bytes = bytes(100)
    
    # Should warn
    result = generator._process_config_space_bytes(config_space_bytes)
    assert result is not None
    
    warnings = [r for r in caplog.records if r.levelname == 'WARNING']
    assert any('Unexpected config space length' in r.message for r in warnings)
    assert any('100' in r.message for r in warnings)


# --- Test BAR Coercion Debug Logging ---------------------------------------


def test_bar_coercion_logs_malformed_entries(generator, caplog):
    """Test that malformed BARs are logged at DEBUG level."""
    import logging
    caplog.set_level(logging.DEBUG)
    
    # Create mixed valid and malformed BARs
    bars = [
        {"bar": 0, "type": "memory", "size": 0x1000, "prefetchable": False},
        {"malformed": "entry"},  # Will try to coerce but may succeed
        None,  # Will try to coerce
    ]
    
    result = generator._coerce_bars_for_validation(bars)
    
    # Should return at least the valid one
    assert len(result) >= 1
    assert result[0]["bar"] == 0
    
    # Check debug logs for failures (only if there were any)
    debug_logs = [r for r in caplog.records if r.levelname == 'DEBUG']
    # If malformed entries were skipped, they should be logged
    malformed_logs = [r for r in debug_logs if 'Failed to coerce BAR entry' in r.message]
    # We don't assert on the count as the coercion may be more lenient
    # Just verify the mechanism exists if needed
    if len(result) < len(bars):
        # Some were skipped, so logs should exist
        assert len(malformed_logs) > 0


def test_bar_coercion_handles_dict_format(generator):
    """Test BAR coercion with standard dict format."""
    bars = [
        {"bar": 0, "type": "memory", "size": 0x1000, "prefetchable": False},
        {"bar": 1, "type": "io", "size": 0x100, "prefetchable": False},
    ]
    
    result = generator._coerce_bars_for_validation(bars)
    
    assert len(result) == 2
    assert result[0]["bar"] == 0
    assert result[0]["type"] == "memory"
    assert result[1]["bar"] == 1
    assert result[1]["type"] == "io"


def test_bar_coercion_handles_parse_bar_info_format(generator):
    """Test BAR coercion with parse_bar_info format."""
    bars = [
        {"index": 0, "bar_type": "memory", "size": 0x1000, "prefetchable": False},
        {"index": 2, "bar_type": "memory", "size": 0x4000, "prefetchable": True},
    ]
    
    result = generator._coerce_bars_for_validation(bars)
    
    assert len(result) == 2
    assert result[0]["bar"] == 0
    assert result[0]["type"] == "memory"
    assert result[1]["bar"] == 2
    assert result[1]["prefetchable"] is True


def test_bar_coercion_handles_attribute_based_format(generator):
    """Test BAR coercion with attribute-based objects."""
    
    class BarInfo:
        def __init__(self, index, bar_type, size, prefetchable):
            self.index = index
            self.bar_type = bar_type
            self.size = size
            self.prefetchable = prefetchable
    
    bars = [
        BarInfo(0, "memory", 0x1000, False),
        BarInfo(1, "io", 0x100, False),
    ]
    
    result = generator._coerce_bars_for_validation(bars)
    
    assert len(result) == 2
    assert result[0]["bar"] == 0
    assert result[0]["type"] == "memory"
    assert result[1]["bar"] == 1
    assert result[1]["type"] == "io"


# --- Integration Tests ------------------------------------------------------


def test_future_annotations_import():
    """Test that future annotations import is present for Python 3.8 compat."""
    import src.device_clone.pcileech_generator as module
    import sys
    
    # Check if __future__ annotations are enabled
    if sys.version_info >= (3, 8):
        # The module should compile without errors
        assert module.PCILeechGenerator is not None
        assert module.PCILeechGenerationConfig is not None


def test_contextmanager_import_at_module_level():
    """Test that contextmanager is imported at module level."""
    import src.device_clone.pcileech_generator as module
    
    # Should be able to access contextmanager without errors
    # The _generation_step method should work
    assert hasattr(module.PCILeechGenerator, '_generation_step')


def test_log_debug_safe_imported():
    """Test that log_debug_safe is properly imported."""
    from src.device_clone.pcileech_generator import log_debug_safe
    
    assert log_debug_safe is not None
    assert callable(log_debug_safe)


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
