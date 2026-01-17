import json
import logging
import shutil
import sys
import tempfile
import textwrap
from pathlib import Path
from unittest import mock

import pytest

# Add project root to Python path for direct test execution
project_root = Path(__file__).parent.parent.resolve()
if str(project_root) not in sys.path:
    sys.path.insert(0, str(project_root))

from pcileechfwgenerator.file_management.file_manager import FileManager

# ============================================================================
# Fixtures
# ============================================================================


@pytest.fixture
def temp_dir():
    """Create a temporary directory for test outputs."""
    with tempfile.TemporaryDirectory() as tmpdir:
        yield Path(tmpdir)


@pytest.fixture
def mock_logger():
    """Create a mock logger."""
    return mock.MagicMock(spec=logging.Logger)


@pytest.fixture
def file_manager(temp_dir):
    """Create a FileManager instance."""
    return FileManager(output_dir=temp_dir)


@pytest.fixture
def populated_output_dir(temp_dir):
    """Create a populated output directory with various file types."""
    # Create source directory with files
    src_dir = temp_dir / "src"
    src_dir.mkdir()

    # Create some source files
    (src_dir / "test.sv").write_text("// SystemVerilog file")
    (src_dir / "test.v").write_text("// Verilog file")
    (src_dir / "test_pkg.svh").write_text("// Package file")

    # Create IP directory with files
    ip_dir = temp_dir / "ip"
    ip_dir.mkdir()
    (ip_dir / "test.xci").write_text("# IP file")

    # Create constraints directory
    constraints_dir = temp_dir / "constraints"
    constraints_dir.mkdir()
    (constraints_dir / "test.xdc").write_text("# Constraint file")

    # Create some output files
    script_content = textwrap.dedent(
        """
        # Primary build script for PCILeech generator
        set_property CONFIG.Device_ID 0x1234 [current_design]
        set_property CONFIG.Vendor_ID 0x10EE [current_design]
        synth_design -top pcileech_top
        launch_runs synth_1
        launch_runs impl_1
        write_bitstream -force test.bit
        write_cfgmem -format hex -size 32 -interface SPIx4 -loadbit "up 0x00000000 test.bit" test.hex
        """
    )
    (temp_dir / "build_firmware.tcl").write_text(script_content * 20)
    (temp_dir / "test.bit").write_bytes(b"0" * (1024 * 1024 + 512))  # ~1 MB
    (temp_dir / "test.mcs").write_bytes(b"1" * (512 * 1024))  # ~0.5 MB
    (temp_dir / "test.ltx").write_text("debug data")
    (temp_dir / "timing.rpt").write_text("timing report")
    (temp_dir / "utilization.rpt").write_text("utilization report")

    # Create some intermediate files to clean
    (temp_dir / "intermediate.json").write_text('{"temp": "data"}')
    (temp_dir / "test.jou").write_text("journal")
    (temp_dir / ".Xil").mkdir()
    (temp_dir / ".Xil" / "temp.txt").write_text("temp file")

    return temp_dir


# ============================================================================
# Test FileManager Class Initialization
# ============================================================================


def test_file_manager_init(temp_dir):
    """Test FileManager initialization."""
    manager = FileManager(output_dir=temp_dir)

    assert manager.output_dir == temp_dir
    assert manager.min_bitstream_size_mb == 0.5
    assert manager.max_bitstream_size_mb == 10.0


def test_file_manager_init_custom_sizes(temp_dir):
    """Test FileManager initialization with custom size limits."""
    manager = FileManager(
        output_dir=temp_dir, min_bitstream_size_mb=1.0, max_bitstream_size_mb=20.0
    )

    assert manager.output_dir == temp_dir
    assert manager.min_bitstream_size_mb == 1.0
    assert manager.max_bitstream_size_mb == 20.0


# ============================================================================
# Test Directory Structure Creation
# ============================================================================


def test_create_pcileech_structure_default(file_manager):
    """Test create_pcileech_structure with default parameters."""
    directories = file_manager.create_pcileech_structure()

    assert "src" in directories
    assert "ip" in directories
    assert directories["src"].exists()
    assert directories["ip"].exists()
    assert directories["src"].is_dir()
    assert directories["ip"].is_dir()


def test_create_pcileech_structure_custom_names(file_manager):
    """Test create_pcileech_structure with custom directory names."""
    directories = file_manager.create_pcileech_structure(
        src_dir="sources", ip_dir="intellectual_property"
    )

    assert "src" in directories
    assert "ip" in directories
    assert (file_manager.output_dir / "sources").exists()
    assert (file_manager.output_dir / "intellectual_property").exists()


# ============================================================================
# Test File Writing Operations
# ============================================================================


def test_write_to_src_directory(file_manager):
    """Test write_to_src_directory."""
    content = "// Test SystemVerilog content"
    filename = "test.sv"

    result_path = file_manager.write_to_src_directory(filename, content)

    assert result_path.exists()
    assert result_path.name == filename
    assert result_path.read_text() == content
    assert result_path.parent.name == "src"


def test_write_to_src_directory_creates_parent(file_manager):
    """Test write_to_src_directory creates parent directory if needed."""
    content = "# Test content"
    filename = "subdir/test.txt"

    # Create nested directory manually; helper only creates root src directory
    nested_dir = file_manager.output_dir / "src" / "subdir"
    nested_dir.mkdir(parents=True, exist_ok=True)

    result_path = file_manager.write_to_src_directory(filename, content)

    assert result_path.exists()
    assert result_path.read_text() == content
    assert result_path.parent.name == "subdir"
    assert result_path.parent.parent.name == "src"


def test_write_to_ip_directory(file_manager):
    """Test write_to_ip_directory."""
    content = "# IP configuration"
    filename = "test.xci"

    result_path = file_manager.write_to_ip_directory(filename, content)

    assert result_path.exists()
    assert result_path.name == filename
    assert result_path.read_text() == content
    assert result_path.parent.name == "ip"


def test_write_to_ip_directory_creates_parent(file_manager):
    """Test write_to_ip_directory creates parent directory if needed."""
    content = "# Test IP content"
    filename = "cores/test.xci"

    nested_dir = file_manager.output_dir / "ip" / "cores"
    nested_dir.mkdir(parents=True, exist_ok=True)

    result_path = file_manager.write_to_ip_directory(filename, content)

    assert result_path.exists()
    assert result_path.read_text() == content
    assert result_path.parent.name == "cores"
    assert result_path.parent.parent.name == "ip"


# ============================================================================
# Test Cleanup Operations
# ============================================================================


def test_cleanup_intermediate_files_basic(populated_output_dir):
    """Test cleanup_intermediate_files with basic file removal."""
    manager = FileManager(output_dir=populated_output_dir)

    preserved_files = manager.cleanup_intermediate_files()

    # Check that important files are preserved
    assert any("build_firmware.tcl" in f for f in preserved_files)
    assert any("test.bit" in f for f in preserved_files)
    assert any("test.mcs" in f for f in preserved_files)
    assert any("test.ltx" in f for f in preserved_files)
    assert any("timing.rpt" in f for f in preserved_files)

    # Check that intermediate files are cleaned
    assert not (populated_output_dir / "intermediate.json").exists()
    assert not (populated_output_dir / "test.jou").exists()
    assert not (populated_output_dir / ".Xil").exists()


def test_cleanup_intermediate_files_permission_error(populated_output_dir):
    """Test cleanup_intermediate_files handles permission errors."""
    manager = FileManager(output_dir=populated_output_dir)

    # Create a directory that can't be removed
    protected_dir = populated_output_dir / "protected"
    protected_dir.mkdir()

    with mock.patch("shutil.rmtree", side_effect=PermissionError("Access denied")):
        preserved_files = manager.cleanup_intermediate_files()

        # Should still work and preserve files
        assert len(preserved_files) > 0


def test_cleanup_intermediate_files_file_not_found(populated_output_dir):
    """Test cleanup_intermediate_files handles missing files."""
    manager = FileManager(output_dir=populated_output_dir)

    # Create a file that gets deleted during iteration
    temp_file = populated_output_dir / "temp.json"
    temp_file.write_text('{"temp": true}')

    with mock.patch("shutil.rmtree", side_effect=FileNotFoundError("Not found")):
        preserved_files = manager.cleanup_intermediate_files()

        # Should still work
        assert len(preserved_files) > 0


def test_cleanup_intermediate_files_unlink_error(populated_output_dir):
    """Test cleanup_intermediate_files handles file unlink errors."""
    manager = FileManager(output_dir=populated_output_dir)

    with mock.patch("pathlib.Path.unlink", side_effect=Exception("Unlink failed")):
        preserved_files = manager.cleanup_intermediate_files()

        # Should still work and preserve files
        assert len(preserved_files) > 0


# ============================================================================
# Test Output Validation
# ============================================================================


def test_validate_final_outputs_full_vivado_build(populated_output_dir):
    """Test validate_final_outputs for full Vivado build."""
    manager = FileManager(output_dir=populated_output_dir)

    results = manager.validate_final_outputs()

    assert results["build_mode"] == "full_vivado"
    assert results["validation_status"] == "success_full_build"
    assert results["bitstream_info"] is not None
    assert results["tcl_file_info"] is not None
    assert results["flash_file_info"] is not None
    assert results["debug_file_info"] is not None
    assert len(results["reports_info"]) == 2
    assert len(results["checksums"]) >= 3


def test_validate_final_outputs_tcl_only_build(temp_dir):
    """Test validate_final_outputs for TCL-only build."""
    manager = FileManager(output_dir=temp_dir)

    # Create only TCL file
    tcl_content = textwrap.dedent(
        """
        # Build script with device config
        set_property CONFIG.Device_ID 0x1234 [current_design]
        set_property CONFIG.Vendor_ID 0x10EE [current_design]
        source 02_ip_config.tcl
        launch_runs synth_1
        launch_runs impl_1
        write_cfgmem -format hex -size 32 -interface SPIx4 -loadbit "up 0x00000000 test.bit" test.hex
        """
    )
    # Repeat script to ensure file size exceeds 1KB threshold
    (temp_dir / "build_firmware.tcl").write_text(tcl_content * 15)

    results = manager.validate_final_outputs()

    assert results["build_mode"] == "tcl_only"
    assert results["validation_status"] == "success_tcl_ready"
    assert results["tcl_file_info"] is not None
    assert results["tcl_file_info"]["has_device_config"] is True
    assert results["tcl_file_info"]["has_synthesis"] is True
    assert results["tcl_file_info"]["has_implementation"] is True
    assert results["tcl_file_info"]["has_hex_generation"] is True


def test_validate_final_outputs_no_files(temp_dir):
    """Test validate_final_outputs with no output files."""
    manager = FileManager(output_dir=temp_dir)

    results = manager.validate_final_outputs()

    assert results["build_mode"] == "tcl_only"
    assert results["validation_status"] == "failed_no_tcl"
    assert results["bitstream_info"] is None
    assert results["tcl_file_info"] is None


def test_validate_final_outputs_small_bitstream(temp_dir):
    """Test validate_final_outputs with unusually small bitstream."""
    manager = FileManager(output_dir=temp_dir)

    # Create small bitstream file
    (temp_dir / "test.bit").write_text("small")
    (temp_dir / "build_firmware.tcl").write_text(
        textwrap.dedent(
            """
            set_property CONFIG.Device_ID 0x1234 [current_design]
            set_property CONFIG.Vendor_ID 0x10EE [current_design]
            launch_runs synth_1
            launch_runs impl_1
            write_cfgmem -format hex -size 32 -interface SPIx4 -loadbit "up 0x00000000 test.bit" test.hex
            """
        )
        * 10
    )

    results = manager.validate_final_outputs()

    assert results["validation_status"] == "warning_small_bitstream"


def test_validate_final_outputs_incomplete_tcl(temp_dir):
    """Test validate_final_outputs with incomplete TCL script."""
    manager = FileManager(output_dir=temp_dir)

    # Create TCL file without device config
    (temp_dir / "build_firmware.tcl").write_text(
        "# Incomplete TCL\nlaunch_runs synth_1"
    )

    results = manager.validate_final_outputs()

    assert results["validation_status"] == "warning_missing_hex"


def test_validate_final_outputs_missing_hex_generation(temp_dir):
    """Test validate_final_outputs with TCL missing hex generation."""
    manager = FileManager(output_dir=temp_dir)

    # Create TCL file with device config but no hex generation
    tcl_content = """
set device_id "1234"
set vendor_id "10EE"
launch_runs synth_1
launch_runs impl_1
"""
    (temp_dir / "build_firmware.tcl").write_text(tcl_content)

    results = manager.validate_final_outputs()

    assert results["validation_status"] == "warning_missing_hex"


def test_validate_final_outputs_validation_error(temp_dir):
    """Test validate_final_outputs handles exceptions."""
    manager = FileManager(output_dir=temp_dir)

    # Mock an exception during validation
    with mock.patch("pathlib.Path.glob", side_effect=Exception("Validation error")):
        results = manager.validate_final_outputs()

        assert results["validation_status"] == "error"


# ============================================================================
# Test Report Type Determination
# ============================================================================


def test_determine_report_type_timing():
    """Test _determine_report_type for timing reports."""
    manager = FileManager(output_dir=Path("/tmp"))

    assert manager._determine_report_type("timing_summary.rpt") == "timing_analysis"
    assert manager._determine_report_type("post_route_timing.rpt") == "timing_analysis"


def test_determine_report_type_utilization():
    """Test _determine_report_type for utilization reports."""
    manager = FileManager(output_dir=Path("/tmp"))

    assert manager._determine_report_type("utilization.rpt") == "resource_utilization"
    assert (
        manager._determine_report_type("resource_utilization.rpt")
        == "resource_utilization"
    )


def test_determine_report_type_power():
    """Test _determine_report_type for power reports."""
    manager = FileManager(output_dir=Path("/tmp"))

    assert manager._determine_report_type("power.rpt") == "power_analysis"
    assert manager._determine_report_type("power_report.rpt") == "power_analysis"


def test_determine_report_type_drc():
    """Test _determine_report_type for DRC reports."""
    manager = FileManager(output_dir=Path("/tmp"))

    assert manager._determine_report_type("drc.rpt") == "design_rule_check"
    assert manager._determine_report_type("design_rule_check.rpt") == "general"


def test_determine_report_type_general():
    """Test _determine_report_type for general reports."""
    manager = FileManager(output_dir=Path("/tmp"))

    assert manager._determine_report_type("custom.rpt") == "general"
    assert manager._determine_report_type("unknown_report.rpt") == "general"


# ============================================================================
# Test Project File Generation
# ============================================================================


def test_generate_project_file():
    """Test generate_project_file."""
    manager = FileManager(output_dir=Path("/tmp"))

    device_info = {"vendor_id": "10EE", "device_id": "1234", "revision_id": "01"}
    board = "vc707"

    project_file = manager.generate_project_file(device_info, board)

    assert project_file["project_name"] is not None
    assert project_file["board"] == board
    assert project_file["device_info"] == device_info
    assert "build_timestamp" in project_file
    assert "build_version" in project_file
    assert "features" in project_file
    assert project_file["features"]["advanced_sv"] is False
    assert project_file["features"]["manufacturing_variance"] is False
    assert project_file["features"]["behavior_profiling"] is False


# ============================================================================
# Test File Manifest Generation
# ============================================================================


def test_generate_file_manifest_complete(populated_output_dir):
    """Test generate_file_manifest with complete file set."""
    manager = FileManager(output_dir=populated_output_dir)

    device_info = {"vendor_id": "10EE", "device_id": "1234"}
    board = "vc707"

    # Create top-level files that manifest scanner can detect
    (populated_output_dir / "device_config.sv").write_text(
        "module device_config; endmodule"
    )
    (populated_output_dir / "pcileech_top.sv").write_text(
        "module pcileech_top; endmodule"
    )
    (populated_output_dir / "support.v").write_text("module support; endmodule")
    (populated_output_dir / "constraints.xdc").write_text("create_clock")
    (populated_output_dir / "build_script.tcl").write_text("# build script")

    manifest = manager.generate_file_manifest(device_info, board)

    assert manifest["project_info"]["device"] == "10EE:1234"
    assert manifest["project_info"]["board"] == board
    assert "generated_at" in manifest["project_info"]

    assert set(manifest["files"]["systemverilog"]) == {
        "device_config.sv",
        "pcileech_top.sv",
    }
    assert manifest["files"]["verilog"] == ["support.v"]
    assert manifest["files"]["constraints"] == ["constraints.xdc"]
    assert set(manifest["files"]["tcl_scripts"]) == {
        "build_firmware.tcl",
        "build_script.tcl",
    }

    assert manifest["validation"]["required_files_present"] is True
    assert manifest["validation"]["top_module_identified"] is True
    assert manifest["validation"]["build_script_ready"] is True


def test_generate_file_manifest_minimal(temp_dir):
    """Test generate_file_manifest with minimal files."""
    manager = FileManager(output_dir=temp_dir)

    device_info = {"vendor_id": "10EE", "device_id": "1234"}
    board = "vc707"

    manifest = manager.generate_file_manifest(device_info, board)

    assert manifest["validation"]["required_files_present"] is False
    assert manifest["validation"]["top_module_identified"] is False
    assert manifest["validation"]["build_script_ready"] is False


# ============================================================================
# Test PCILeech Source Copying
# ============================================================================


def test_copy_pcileech_sources_success(temp_dir):
    """Test copy_pcileech_sources with successful copy."""
    manager = FileManager(output_dir=temp_dir)

    repo_root = temp_dir / "repo"
    board_path = repo_root / "boards" / "vc707"
    board_path.mkdir(parents=True)
    (board_path / "board_top.sv").write_text("module board_top; endmodule")
    (board_path / "support.v").write_text("module support; endmodule")
    (board_path / "board_pkg.svh").write_text("package board_pkg; endpackage")
    # Add header file to test .svh file copying
    (board_path / "pcileech_header.svh").write_text("`define HEADER_INCLUDED")

    constraints_dir = repo_root / "constraints" / "vc707"
    constraints_dir.mkdir(parents=True)
    xdc_file = constraints_dir / "board.xdc"
    xdc_file.write_text("create_clock")

    from pcileechfwgenerator.file_management import file_manager as fm_module

    local_pcileech_dir = (
        Path(fm_module.__file__).resolve().parent.parent.parent / "pcileech"
    )
    original_exists = Path.exists

    def fake_exists(self):
        if self == local_pcileech_dir:
            return False
        return original_exists(self)

    with mock.patch(
        "pcileechfwgenerator.file_management.repo_manager.RepoManager.ensure_repo",
        return_value=repo_root,
    ), mock.patch(
        "pcileechfwgenerator.file_management.repo_manager.RepoManager.get_board_path",
        return_value=board_path,
    ), mock.patch(
        "pcileechfwgenerator.file_management.repo_manager.RepoManager.get_xdc_files",
        return_value=[xdc_file],
    ), mock.patch(
        "pathlib.Path.exists", new=fake_exists
    ):
        result = manager.copy_pcileech_sources("vc707")

    assert sorted(Path(p).name for p in result["systemverilog"]) == ["board_top.sv"]
    assert sorted(Path(p).name for p in result["verilog"]) == ["support.v"]
    # Verify both package files and header files are copied
    assert sorted(Path(p).name for p in result["packages"]) == [
        "board_pkg.svh",
        "pcileech_header.svh",
    ]
    assert sorted(Path(p).name for p in result["constraints"]) == ["board.xdc"]
    
    # Verify header file exists in output
    output_header = temp_dir / "src" / "pcileech_header.svh"
    assert output_header.exists(), "Header file should be copied to output"
    assert "`define HEADER_INCLUDED" in output_header.read_text()


def test_copy_pcileech_sources_repo_import_error(temp_dir):
    """Test copy_pcileech_sources handles repo manager import error."""
    manager = FileManager(output_dir=temp_dir)

    with mock.patch(
        "pcileechfwgenerator.file_management.repo_manager.RepoManager.ensure_repo",
        side_effect=ImportError("No repo manager"),
    ):
        result = manager.copy_pcileech_sources("vc707")

        # Should return empty result
        assert all(len(files) == 0 for files in result.values())


def test_copy_pcileech_sources_copy_error(temp_dir):
    """Test copy_pcileech_sources handles copy errors."""
    manager = FileManager(output_dir=temp_dir)

    with mock.patch(
        "pcileechfwgenerator.file_management.repo_manager.RepoManager.ensure_repo",
        side_effect=Exception("Repo error"),
    ):
        result = manager.copy_pcileech_sources("vc707")

        # Should return empty result
        assert all(len(files) == 0 for files in result.values())


# ============================================================================
# Test Source File List Generation
# ============================================================================


def test_get_source_file_lists_complete(populated_output_dir):
    """Test get_source_file_lists with complete file structure."""
    manager = FileManager(output_dir=populated_output_dir)

    file_lists = manager.get_source_file_lists()

    assert len(file_lists["systemverilog_files"]) == 1
    assert len(file_lists["verilog_files"]) == 1
    assert len(file_lists["constraint_files"]) == 1
    assert len(file_lists["package_files"]) == 1
    assert len(file_lists["ip_files"]) == 1

    assert file_lists["systemverilog_files"][0] == "src/test.sv"
    assert file_lists["verilog_files"][0] == "src/test.v"
    assert file_lists["constraint_files"][0] == "constraints/test.xdc"
    assert file_lists["package_files"][0] == "src/test_pkg.svh"
    assert file_lists["ip_files"][0] == "ip/test.xci"


def test_get_source_file_lists_empty(temp_dir):
    """Test get_source_file_lists with empty directory."""
    manager = FileManager(output_dir=temp_dir)

    file_lists = manager.get_source_file_lists()

    assert all(len(files) == 0 for files in file_lists.values())


# ============================================================================
# Test Final Output Information Printing
# ============================================================================


def test_print_final_output_info_success_full_build(populated_output_dir):
    """Test print_final_output_info for successful full build."""
    manager = FileManager(output_dir=populated_output_dir)

    validation_results = manager.validate_final_outputs()

    # Should not raise exception
    manager.print_final_output_info(validation_results)


def test_print_final_output_info_tcl_only_build(temp_dir):
    """Test print_final_output_info for TCL-only build."""
    manager = FileManager(output_dir=temp_dir)

    # Create TCL file
    (temp_dir / "build_firmware.tcl").write_text("# TCL build script")

    validation_results = manager.validate_final_outputs()

    # Should not raise exception
    manager.print_final_output_info(validation_results)


def test_print_final_output_info_failed_build(temp_dir):
    """Test print_final_output_info for failed build."""
    manager = FileManager(output_dir=temp_dir)

    validation_results = {
        "build_mode": "tcl_only",
        "validation_status": "failed_no_tcl",
        "bitstream_info": None,
        "tcl_file_info": None,
        "flash_file_info": None,
        "debug_file_info": None,
        "reports_info": [],
        "checksums": {},
        "file_sizes": {},
    }

    # Should not raise exception
    manager.print_final_output_info(validation_results)


def test_print_final_output_info_banner_render_error(temp_dir):
    """Test print_final_output_info handles banner rendering errors."""
    manager = FileManager(output_dir=temp_dir)

    validation_results = {
        "build_mode": "tcl_only",
        "validation_status": "success_tcl_ready",
        "tcl_file_info": {
            "filename": "test.tcl",
            "size_kb": 1.0,
            "size_bytes": 1024,
            "sha256": "abcd",
            "has_device_config": True,
            "has_synthesis": True,
            "has_implementation": True,
            "has_hex_generation": True,
        },
        "bitstream_info": None,
        "flash_file_info": None,
        "debug_file_info": None,
        "reports_info": [],
        "checksums": {},
        "file_sizes": {},
    }

    # Mock format_kv_table to raise exception
    with mock.patch(
        "pcileechfwgenerator.file_management.file_manager.format_kv_table",
        side_effect=Exception("Render error"),
    ):
        # Should not raise exception
        manager.print_final_output_info(validation_results)


# ============================================================================
# Bug #528 Regression Tests: Device ID/Vendor ID in .coe files
# ============================================================================
# These tests validate that generated .coe files (containing device IDs)
# properly overwrite template .coe files from the voltcyclone-fpga library.
# Without this fix, Vivado would use template files with Xilinx default IDs
# instead of the donor device IDs.


@pytest.fixture
def mock_voltcyclone_repo(temp_dir):
    """
    Create a mock voltcyclone-fpga repository structure with template .coe files.
    Simulates: lib/voltcyclone-fpga/CaptainDMA/75t484_x1/ip/
    """
    repo_root = temp_dir / "lib" / "voltcyclone-fpga"
    board_path = repo_root / "CaptainDMA" / "75t484_x1"
    ip_dir = board_path / "ip"
    ip_dir.mkdir(parents=True, exist_ok=True)
    
    # Create template .coe files with DEFAULT device IDs (Xilinx default: 0x10ee:0x0666)
    template_cfgspace_coe = ip_dir / "pcileech_cfgspace.coe"
    template_cfgspace_coe.write_text(textwrap.dedent("""
        memory_initialization_radix=16;
        memory_initialization_vector=
        ee106610,  ; Device ID: 0x0666, Vendor ID: 0x10ee (XILINX DEFAULT)
        00000000,
        00000000,
        00000000;
    """).strip())
    
    template_writemask_coe = ip_dir / "pcileech_cfgspace_writemask.coe"
    template_writemask_coe.write_text(textwrap.dedent("""
        memory_initialization_radix=16;
        memory_initialization_vector=
        FFFFFFFF,
        00000000;
    """).strip())
    
    # Create a .xci file (IP core definition)
    xci_file = ip_dir / "bram_pcie_cfgspace.xci"
    xci_file.write_text(textwrap.dedent("""
        <?xml version="1.0" encoding="UTF-8"?>
        <spirit:design xmlns:spirit="http://www.spiritconsortium.org/XMLSchema/SPIRIT/1685-2009">
          <spirit:componentInstances>
            <spirit:componentInstance>
              <spirit:instanceName>bram_pcie_cfgspace</spirit:instanceName>
              <spirit:componentRef spirit:vendor="xilinx.com" spirit:library="ip" spirit:name="blk_mem_gen"/>
              <spirit:configurableElementValues>
                <spirit:configurableElementValue spirit:referenceId="Coe_File">pcileech_cfgspace.coe</spirit:configurableElementValue>
              </spirit:configurableElementValues>
            </spirit:componentInstance>
          </spirit:componentInstances>
        </spirit:design>
    """).strip())
    
    return {
        "repo_root": repo_root,
        "board_path": board_path,
        "ip_dir": ip_dir,
        "template_cfgspace": template_cfgspace_coe,
        "template_writemask": template_writemask_coe,
        "xci_file": xci_file,
    }


@pytest.fixture
def mock_generated_coe_files(temp_dir):
    """
    Create mock generated .coe files with DONOR device IDs.
    Simulates: output/src/pcileech_cfgspace.coe
    These files should contain the actual donor device's IDs (e.g., RTL8111: 0x10ec:0x8161)
    """
    src_dir = temp_dir / "output" / "src"
    src_dir.mkdir(parents=True, exist_ok=True)
    
    # Generated .coe with ACTUAL device IDs from donor device
    generated_cfgspace_coe = src_dir / "pcileech_cfgspace.coe"
    generated_cfgspace_coe.write_text(textwrap.dedent("""
        memory_initialization_radix=16;
        memory_initialization_vector=
        ec106181,  ; Device ID: 0x8161, Vendor ID: 0x10ec (RTL8111/8168 DONOR DEVICE)
        01000000,
        00000000,
        00000000;
    """).strip())
    
    generated_writemask_coe = src_dir / "pcileech_cfgspace_writemask.coe"
    generated_writemask_coe.write_text(textwrap.dedent("""
        memory_initialization_radix=16;
        memory_initialization_vector=
        FFFFFFFF,
        00000000;
    """).strip())
    
    return {
        "src_dir": src_dir,
        "generated_cfgspace": generated_cfgspace_coe,
        "generated_writemask": generated_writemask_coe,
    }


def test_bug528_coe_files_overwrite_template_with_device_ids(
    temp_dir, mock_voltcyclone_repo, mock_generated_coe_files
):
    """
    Regression test for bug #528: Verify generated .coe files overwrite template files.
    
    This is the CRITICAL test that validates the fix. Without this behavior,
    Vivado would use template .coe files (with Xilinx default IDs) instead of
    the generated ones (with donor device IDs).
    """
    output_dir = temp_dir / "output"
    file_manager = FileManager(output_dir=output_dir)
    
    # Mock RepoManager to return our mock repository
    with mock.patch("pcileechfwgenerator.file_management.repo_manager.RepoManager") as mock_repo_manager:
        mock_repo_manager.get_board_path.return_value = mock_voltcyclone_repo["board_path"]
        
        # Execute: Copy IP files (this should copy templates then overwrite with generated)
        copied_files = file_manager.copy_ip_files(board="CaptainDMA_75t")
        
        # Verify: Files were copied
        assert len(copied_files) > 0
        
        # CRITICAL ASSERTION: Verify output/ip/pcileech_cfgspace.coe contains DONOR device IDs
        output_cfgspace = output_dir / "ip" / "pcileech_cfgspace.coe"
        assert output_cfgspace.exists(), "pcileech_cfgspace.coe should exist in output/ip/"
        
        content = output_cfgspace.read_text()
        
        # The file should contain the DONOR device IDs (0x10ec:0x8161), not defaults (0x10ee:0x0666)
        assert "8161" in content, "Output .coe should contain donor device ID 0x8161 (RTL8111)"
        assert "10ec" in content, "Output .coe should contain donor vendor ID 0x10ec (Realtek)"
        
        # Should NOT contain Xilinx default IDs
        assert "0666" not in content, "Output .coe should NOT contain Xilinx default device ID"
        assert "ee10" not in content, "Output .coe should NOT contain Xilinx vendor ID (reversed bytes)"


def test_bug528_coe_files_copied_to_ip_directory_not_just_src(
    temp_dir, mock_voltcyclone_repo, mock_generated_coe_files
):
    """
    Regression test: Ensure .coe files end up in output/ip/ where Vivado expects them.
    
    Before the fix, generated .coe files stayed in output/src/ and Vivado used
    template files from lib/voltcyclone-fpga/, resulting in wrong device IDs.
    """
    output_dir = temp_dir / "output"
    file_manager = FileManager(output_dir=output_dir)
    
    with mock.patch("pcileechfwgenerator.file_management.repo_manager.RepoManager") as mock_repo_manager:
        mock_repo_manager.get_board_path.return_value = mock_voltcyclone_repo["board_path"]
        
        file_manager.copy_ip_files(board="CaptainDMA_75t")
        
        # Verify generated files exist in BOTH locations
        assert (output_dir / "src" / "pcileech_cfgspace.coe").exists()
        assert (output_dir / "ip" / "pcileech_cfgspace.coe").exists()
        
        # The IP directory version should have the device IDs (it's what Vivado uses)
        ip_coe_content = (output_dir / "ip" / "pcileech_cfgspace.coe").read_text()
        assert "8161" in ip_coe_content, "output/ip/ version must have donor device IDs"


def test_bug528_xci_files_copied_but_not_overwritten(
    temp_dir, mock_voltcyclone_repo, mock_generated_coe_files
):
    """
    Verify that .xci files (IP core definitions) are copied but not overwritten.
    
    Only .coe files should be overwritten with generated versions. XCI files
    are IP core definitions from the library and should not be modified.
    """
    output_dir = temp_dir / "output"
    file_manager = FileManager(output_dir=output_dir)
    
    with mock.patch("pcileechfwgenerator.file_management.repo_manager.RepoManager") as mock_repo_manager:
        mock_repo_manager.get_board_path.return_value = mock_voltcyclone_repo["board_path"]
        
        file_manager.copy_ip_files(board="CaptainDMA_75t")
        
        # Verify .xci file was copied
        output_xci = output_dir / "ip" / "bram_pcie_cfgspace.xci"
        assert output_xci.exists()
        
        # Verify it still references the .coe file
        xci_content = output_xci.read_text()
        assert "pcileech_cfgspace.coe" in xci_content
        assert "blk_mem_gen" in xci_content


def test_bug528_behavior_when_no_generated_coe_files_exist(
    temp_dir, mock_voltcyclone_repo
):
    """
    Verify graceful behavior when no generated .coe files exist in output/src/.
    
    This can happen during initial setup or if SystemVerilog generation hasn't
    run yet. In this case, template .coe files should still be copied.
    """
    output_dir = temp_dir / "output"
    file_manager = FileManager(output_dir=output_dir)
    
    # Don't create generated .coe files - output/src/ is empty
    
    with mock.patch("pcileechfwgenerator.file_management.repo_manager.RepoManager") as mock_repo_manager:
        mock_repo_manager.get_board_path.return_value = mock_voltcyclone_repo["board_path"]
        
        copied_files = file_manager.copy_ip_files(board="CaptainDMA_75t")
        
        # Should still copy template files
        assert len(copied_files) > 0
        
        # Should have template .coe files (with Xilinx default IDs)
        output_cfgspace = output_dir / "ip" / "pcileech_cfgspace.coe"
        assert output_cfgspace.exists()
        
        content = output_cfgspace.read_text()
        # Should contain Xilinx defaults (since no generated files to overwrite with)
        assert "0666" in content or "ee10" in content


def test_bug528_multiple_coe_files_all_overwritten(
    temp_dir, mock_voltcyclone_repo, mock_generated_coe_files
):
    """
    Verify that ALL .coe files are overwritten, not just pcileech_cfgspace.coe.
    
    There are typically multiple .coe files:
    - pcileech_cfgspace.coe (configuration space)
    - pcileech_cfgspace_writemask.coe (write protection masks)
    - pcileech_bar_zero4k.coe (BAR0 initialization)
    
    All should be overwritten if generated versions exist.
    """
    output_dir = temp_dir / "output"
    file_manager = FileManager(output_dir=output_dir)
    
    with mock.patch("pcileechfwgenerator.file_management.repo_manager.RepoManager") as mock_repo_manager:
        mock_repo_manager.get_board_path.return_value = mock_voltcyclone_repo["board_path"]
        
        file_manager.copy_ip_files(board="CaptainDMA_75t")
        
        # Both .coe files should exist in output/ip/
        assert (output_dir / "ip" / "pcileech_cfgspace.coe").exists()
        assert (output_dir / "ip" / "pcileech_cfgspace_writemask.coe").exists()


def test_bug528_generated_coe_files_not_lost_after_ip_copy(
    temp_dir, mock_voltcyclone_repo, mock_generated_coe_files
):
    """
    Verify that copying IP files doesn't delete generated .coe files from src/.
    
    Generated .coe files in output/src/ should remain intact after being
    copied to output/ip/. They may be needed for validation or debugging.
    """
    output_dir = temp_dir / "output"
    file_manager = FileManager(output_dir=output_dir)
    
    with mock.patch("pcileechfwgenerator.file_management.repo_manager.RepoManager") as mock_repo_manager:
        mock_repo_manager.get_board_path.return_value = mock_voltcyclone_repo["board_path"]
        
        # Store original content
        original_src_content = mock_generated_coe_files["generated_cfgspace"].read_text()
        
        file_manager.copy_ip_files(board="CaptainDMA_75t")
        
        # Verify original files still exist in src/
        assert mock_generated_coe_files["generated_cfgspace"].exists()
        assert mock_generated_coe_files["generated_cfgspace"].read_text() == original_src_content


def test_bug528_device_ids_byte_order_preserved(
    temp_dir, mock_voltcyclone_repo, mock_generated_coe_files
):
    """
    Verify that device ID byte order is preserved during copy.
    
    PCIe configuration space stores device/vendor IDs in little-endian format.
    The copy operation should preserve the exact byte order from generated files.
    """
    output_dir = temp_dir / "output"
    file_manager = FileManager(output_dir=output_dir)
    
    with mock.patch("pcileechfwgenerator.file_management.repo_manager.RepoManager") as mock_repo_manager:
        mock_repo_manager.get_board_path.return_value = mock_voltcyclone_repo["board_path"]
        
        # Store original generated content
        original_content = mock_generated_coe_files["generated_cfgspace"].read_text()
        
        file_manager.copy_ip_files(board="CaptainDMA_75t")
        
        # Verify output/ip/ version has identical content
        output_content = (output_dir / "ip" / "pcileech_cfgspace.coe").read_text()
        assert output_content == original_content, "Byte order must be preserved exactly"
