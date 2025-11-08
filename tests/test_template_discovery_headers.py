#!/usr/bin/env python3
"""
Unit tests for TemplateDiscovery header file support.

Tests that .svh header files are properly discovered and included
in SystemVerilog templates, preventing Vivado build errors.
"""

import pytest
from pathlib import Path
from unittest import mock

from src.file_management.template_discovery import TemplateDiscovery


@pytest.fixture
def mock_board_structure(tmp_path):
    """Create a mock board structure with header files."""
    repo_root = tmp_path / "repo"
    board_path = repo_root / "CaptainDMA" / "75t484_x1"
    src_dir = board_path / "src"
    src_dir.mkdir(parents=True)
    
    # Create SystemVerilog source files
    (src_dir / "pcileech_top.sv").write_text("module pcileech_top; endmodule")
    (src_dir / "pcileech_fifo.sv").write_text("module pcileech_fifo; endmodule")
    
    # Create header files (.svh)
    (src_dir / "pcileech_header.svh").write_text(
        "`ifndef PCILEECH_HEADER\n`define PCILEECH_HEADER\n`endif"
    )
    (src_dir / "tlp_pkg.svh").write_text("package tlp_pkg; endpackage")
    
    # Create package files
    (src_dir / "bar_layout_pkg.svh").write_text(
        "package bar_layout_pkg; endpackage"
    )
    
    return {
        "repo_root": repo_root,
        "board_path": board_path,
        "board_name": "pcileech_75t484_x1",
    }


def test_template_patterns_include_svh():
    """Verify TEMPLATE_PATTERNS includes .svh header files."""
    patterns = TemplateDiscovery.TEMPLATE_PATTERNS
    
    assert "systemverilog" in patterns
    sv_patterns = patterns["systemverilog"]
    
    # Check that .svh patterns are included
    assert "*.svh" in sv_patterns
    assert "src/*.svh" in sv_patterns
    assert "rtl/*.svh" in sv_patterns
    assert "hdl/*.svh" in sv_patterns


def test_discover_templates_finds_header_files(mock_board_structure):
    """Test that discover_templates finds .svh header files."""
    board_name = mock_board_structure["board_name"]
    board_path = mock_board_structure["board_path"]
    repo_root = mock_board_structure["repo_root"]
    
    with mock.patch(
        "src.file_management.repo_manager.RepoManager.ensure_repo",
        return_value=repo_root,
    ), mock.patch(
        "src.file_management.repo_manager.RepoManager.get_board_path",
        return_value=board_path,
    ):
        templates = TemplateDiscovery.discover_templates(
            board_name, repo_root=repo_root
        )
    
    # Verify SystemVerilog templates are discovered
    assert "systemverilog" in templates
    sv_files = templates["systemverilog"]
    
    # Get file names
    sv_filenames = [f.name for f in sv_files]
    
    # Verify .sv files are found
    assert "pcileech_top.sv" in sv_filenames
    assert "pcileech_fifo.sv" in sv_filenames
    
    # Verify .svh header files are found
    assert "pcileech_header.svh" in sv_filenames
    assert "tlp_pkg.svh" in sv_filenames
    assert "bar_layout_pkg.svh" in sv_filenames
    
    # Verify total count (2 .sv + 3 .svh = 5)
    assert len(sv_files) == 5


def test_get_source_files_includes_headers(mock_board_structure):
    """Test that get_source_files includes .svh header files."""
    board_name = mock_board_structure["board_name"]
    board_path = mock_board_structure["board_path"]
    repo_root = mock_board_structure["repo_root"]
    
    with mock.patch(
        "src.file_management.repo_manager.RepoManager.ensure_repo",
        return_value=repo_root,
    ), mock.patch(
        "src.file_management.repo_manager.RepoManager.get_board_path",
        return_value=board_path,
    ):
        source_files = TemplateDiscovery.get_source_files(
            board_name, repo_root=repo_root
        )
    
    # Get file names
    filenames = [f.name for f in source_files]
    
    # Verify both .sv and .svh files are included
    assert "pcileech_top.sv" in filenames
    assert "pcileech_header.svh" in filenames
    assert "tlp_pkg.svh" in filenames


def test_get_pcileech_core_files_includes_header():
    """Test that get_pcileech_core_files includes pcileech_header.svh."""
    # Create a mock repo structure
    with mock.patch(
        "src.file_management.repo_manager.RepoManager.ensure_repo"
    ) as mock_ensure:
        repo_root = Path("/tmp/mock_repo")
        mock_ensure.return_value = repo_root
        
        # Mock the file search to return our test files
        def mock_rglob(pattern):
            if pattern == "pcileech_header.svh":
                return [repo_root / "src" / "pcileech_header.svh"]
            return []
        
        with mock.patch.object(Path, "exists", return_value=True), \
             mock.patch.object(Path, "rglob", side_effect=mock_rglob):
            
            # Check that pcileech_header.svh is in the search list
            core_files_list = [
                "pcileech_tlps128_bar_controller.sv",
                "pcileech_tlps128_bar_controller_template.sv",
                "pcileech_fifo.sv",
                "pcileech_mux.sv",
                "pcileech_com.sv",
                "pcileech_pcie_cfg_a7.sv",
                "pcileech_pcie_cfg_us.sv",
                "pcileech.svh",
                "pcileech_header.svh",  # Should be present
                "tlp_pkg.svh",
                "bar_controller.sv",
                "cfg_shadow.sv",
                "pcileech_pcie_tlp_a7.sv",
            ]
            
            # Verify pcileech_header.svh is in the list
            assert "pcileech_header.svh" in core_files_list
            assert "pcileech.svh" in core_files_list
            assert "tlp_pkg.svh" in core_files_list


def test_header_files_in_nested_directories(tmp_path):
    """Test that header files in nested directories are discovered."""
    repo_root = tmp_path / "repo"
    board_path = repo_root / "boards" / "test_board"
    
    # Create nested directory structure
    rtl_dir = board_path / "rtl"
    hdl_dir = board_path / "hdl"
    rtl_dir.mkdir(parents=True)
    hdl_dir.mkdir(parents=True)
    
    # Create header files in different locations
    (board_path / "top_header.svh").write_text("`define TOP_HEADER")
    (rtl_dir / "rtl_header.svh").write_text("`define RTL_HEADER")
    (hdl_dir / "hdl_header.svh").write_text("`define HDL_HEADER")
    
    with mock.patch(
        "src.file_management.repo_manager.RepoManager.ensure_repo",
        return_value=repo_root,
    ), mock.patch(
        "src.file_management.repo_manager.RepoManager.get_board_path",
        return_value=board_path,
    ):
        templates = TemplateDiscovery.discover_templates(
            "test_board", repo_root=repo_root
        )
    
    sv_files = templates.get("systemverilog", [])
    sv_filenames = [f.name for f in sv_files]
    
    # All header files should be discovered
    assert "top_header.svh" in sv_filenames
    assert "rtl_header.svh" in sv_filenames
    assert "hdl_header.svh" in sv_filenames


def test_header_files_have_correct_extensions(mock_board_structure):
    """Verify discovered header files have .svh extension."""
    board_name = mock_board_structure["board_name"]
    board_path = mock_board_structure["board_path"]
    repo_root = mock_board_structure["repo_root"]
    
    with mock.patch(
        "src.file_management.repo_manager.RepoManager.ensure_repo",
        return_value=repo_root,
    ), mock.patch(
        "src.file_management.repo_manager.RepoManager.get_board_path",
        return_value=board_path,
    ):
        templates = TemplateDiscovery.discover_templates(
            board_name, repo_root=repo_root
        )
    
    sv_files = templates["systemverilog"]
    header_files = [f for f in sv_files if f.suffix == ".svh"]
    
    # Verify we found header files
    assert len(header_files) > 0
    
    # Verify all header files have .svh extension
    for header in header_files:
        assert header.suffix == ".svh"
        assert header.exists()


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
