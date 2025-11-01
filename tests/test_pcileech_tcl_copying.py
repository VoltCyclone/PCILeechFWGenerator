#!/usr/bin/env python3
"""Tests for PCILeech TCL script copying from submodule.

This test suite validates that TCL scripts are correctly copied from the
voltcyclone-fpga submodule rather than being generated from templates.
This is the correct architecture where only .coe overlay files are generated.
"""
from pathlib import Path
from unittest import mock

import pytest

from src.device_clone.pcileech_generator import (
    PCILeechGenerationConfig,
    PCILeechGenerationError,
    PCILeechGenerator,
)


@pytest.fixture
def generator(tmp_path: Path) -> PCILeechGenerator:
    """Create a PCILeechGenerator instance with test configuration."""
    cfg = PCILeechGenerationConfig(
        device_bdf="0000:00:00.0",
        enable_behavior_profiling=False,
        strict_validation=False,
        output_dir=tmp_path / "out",
    )
    return PCILeechGenerator(cfg)


def test_copy_tcl_scripts_success(generator: PCILeechGenerator, tmp_path: Path):
    """Test successful copying of TCL scripts from submodule."""
    template_context = {
        "board_name": "pcileech_100t484_x1",
        "output_dir": str(tmp_path / "out"),
    }
    
    # Mock FileManager
    with mock.patch(
        "src.file_management.file_manager.FileManager"
    ) as mock_fm_cls:
        mock_fm = mock.MagicMock()
        mock_fm_cls.return_value = mock_fm
        
        # Simulate TCL scripts being copied
        mock_fm.copy_vivado_tcl_scripts.return_value = [
            Path("/out/tcl/vivado_generate_project.tcl"),
            Path("/out/tcl/vivado_build.tcl"),
        ]
        
        result = generator._copy_tcl_scripts(template_context)
        
        # Verify FileManager was called with correct board
        mock_fm.copy_vivado_tcl_scripts.assert_called_once_with(
            board="pcileech_100t484_x1"
        )
        
        # Verify result structure
        assert isinstance(result, dict)
        assert len(result) == 2
        assert "vivado_generate_project.tcl" in result
        assert "vivado_build.tcl" in result
        assert result["vivado_generate_project.tcl"].endswith(
            "vivado_generate_project.tcl"
        )


def test_copy_tcl_scripts_uses_board_fallback(
    generator: PCILeechGenerator, tmp_path: Path
):
    """Test that 'board' key is used as fallback when 'board_name' is missing."""
    template_context = {
        "board": "ac701_ft601",  # No board_name, just board
        "output_dir": str(tmp_path / "out"),
    }
    
    with mock.patch(
        "src.file_management.file_manager.FileManager"
    ) as mock_fm_cls:
        mock_fm = mock.MagicMock()
        mock_fm_cls.return_value = mock_fm
        mock_fm.copy_vivado_tcl_scripts.return_value = [
            Path("/out/tcl/vivado_build.tcl")
        ]
        
        result = generator._copy_tcl_scripts(template_context)
        
        # Verify fallback board name was used
        mock_fm.copy_vivado_tcl_scripts.assert_called_once_with(board="ac701_ft601")
        assert "vivado_build.tcl" in result


def test_copy_tcl_scripts_missing_board_raises(
    generator: PCILeechGenerator, tmp_path: Path
):
    """Test that missing board name raises PCILeechGenerationError."""
    template_context = {
        "output_dir": str(tmp_path / "out"),
        # No board_name or board key
    }
    
    with pytest.raises(
        PCILeechGenerationError,
        match=r"Cannot copy TCL scripts.*board name not specified",
    ):
        generator._copy_tcl_scripts(template_context)


def test_copy_tcl_scripts_file_manager_error(
    generator: PCILeechGenerator, tmp_path: Path
):
    """Test that FileManager errors are properly wrapped."""
    template_context = {
        "board_name": "pcileech_100t484_x1",
        "output_dir": str(tmp_path / "out"),
    }
    
    with mock.patch(
        "src.file_management.file_manager.FileManager"
    ) as mock_fm_cls:
        mock_fm = mock.MagicMock()
        mock_fm_cls.return_value = mock_fm
        
        # Simulate FileManager error
        mock_fm.copy_vivado_tcl_scripts.side_effect = RuntimeError(
            "Submodule not initialized"
        )
        
        with pytest.raises(
            PCILeechGenerationError,
            match=r"Failed to copy TCL scripts.*Submodule not initialized",
        ):
            generator._copy_tcl_scripts(template_context)


def test_copy_tcl_scripts_uses_output_dir_from_context(
    generator: PCILeechGenerator, tmp_path: Path
):
    """Test that output_dir from context overrides config."""
    custom_output = tmp_path / "custom_out"
    template_context = {
        "board_name": "pcileech_100t484_x1",
        "output_dir": str(custom_output),
    }
    
    with mock.patch(
        "src.file_management.file_manager.FileManager"
    ) as mock_fm_cls:
        mock_fm = mock.MagicMock()
        mock_fm_cls.return_value = mock_fm
        mock_fm.copy_vivado_tcl_scripts.return_value = []
        
        generator._copy_tcl_scripts(template_context)
        
        # Verify FileManager was initialized with custom output dir
        mock_fm_cls.assert_called_once_with(output_dir=custom_output)


def test_copy_tcl_scripts_falls_back_to_config_output_dir(
    generator: PCILeechGenerator, tmp_path: Path
):
    """Test that config output_dir is used when not in context."""
    template_context = {
        "board_name": "pcileech_100t484_x1",
        # No output_dir in context
    }
    
    with mock.patch(
        "src.file_management.file_manager.FileManager"
    ) as mock_fm_cls:
        mock_fm = mock.MagicMock()
        mock_fm_cls.return_value = mock_fm
        mock_fm.copy_vivado_tcl_scripts.return_value = []
        
        generator._copy_tcl_scripts(template_context)
        
        # Verify FileManager was initialized with config output dir
        called_output_dir = mock_fm_cls.call_args[1]["output_dir"]
        assert str(called_output_dir) == str(generator.config.output_dir)


def test_firmware_components_includes_tcl_scripts(
    generator: PCILeechGenerator, tmp_path: Path
):
    """Test that _generate_firmware_components includes TCL scripts."""
    template_context = {
        "board_name": "pcileech_100t484_x1",
        "output_dir": str(tmp_path / "out"),
        "vendor_id": "0x1234",
        "device_id": "0x5678",
        "config_space_hex": "00" * 256,
    }
    
    # Mock all the dependencies
    with mock.patch.object(
        generator, "_generate_build_integration", return_value="# Build integration"
    ), mock.patch.object(
        generator, "_copy_constraint_files", return_value={}
    ), mock.patch.object(
        generator, "_generate_config_space_hex", return_value="00" * 256
    ), mock.patch.object(
        generator, "_generate_writemask_coe", return_value=None
    ), mock.patch(
        "src.file_management.file_manager.FileManager"
    ) as mock_fm_cls:
        mock_fm = mock.MagicMock()
        mock_fm_cls.return_value = mock_fm
        mock_fm.copy_vivado_tcl_scripts.return_value = [
            Path("/out/tcl/vivado_build.tcl")
        ]
        
        components = generator._generate_firmware_components(template_context)
        
        # Verify tcl_scripts is in firmware components
        assert "tcl_scripts" in components
        assert isinstance(components["tcl_scripts"], dict)
        assert "vivado_build.tcl" in components["tcl_scripts"]
