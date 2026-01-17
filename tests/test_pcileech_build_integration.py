#!/usr/bin/env python3
"""Tests for the PCILeech Build Integration module."""

import sys
import unittest
from pathlib import Path
from unittest.mock import MagicMock, call, patch

# Add project root to path for imports
sys.path.insert(0, str(Path(__file__).parent.parent))

from pcileechfwgenerator.vivado_handling.pcileech_build_integration import (
    PCILeechBuildIntegration, integrate_pcileech_build)


class TestPCILeechBuildIntegration(unittest.TestCase):
    """Test cases for PCILeechBuildIntegration class."""

    def setUp(self):
        """Set up test environment."""
        self.output_dir = Path("/tmp/test_output")
        self.repo_root = Path("/tmp/test_repo")

        # Common patches
        self.board_discovery_patch = patch(
            "pcileechfwgenerator.vivado_handling.pcileech_build_integration.BoardDiscovery"
        )
        self.template_discovery_patch = patch(
            "pcileechfwgenerator.vivado_handling.pcileech_build_integration.TemplateDiscovery"
        )
        self.repo_manager_patch = patch(
            "pcileechfwgenerator.vivado_handling.pcileech_build_integration.RepoManager"
        )
        self.tcl_builder_patch = patch(
            "pcileechfwgenerator.vivado_handling.pcileech_build_integration.TCLBuilder"
        )
        self.path_mkdir_patch = patch("pathlib.Path.mkdir")

        # Start patches
        self.mock_board_discovery = self.board_discovery_patch.start()
        self.mock_template_discovery = self.template_discovery_patch.start()
        self.mock_repo_manager = self.repo_manager_patch.start()
        self.mock_tcl_builder = self.tcl_builder_patch.start()
        self.mock_path_mkdir = self.path_mkdir_patch.start()

        # Setup mock return values
        self.mock_repo_manager.ensure_repo.return_value = self.repo_root

        # Sample board data
        self.sample_boards = {
            "artix7": {
                "name": "artix7",
                "fpga_part": "xc7a35t",
                "fpga_family": "7series",
                "pcie_ip_type": "pcie_7x",
                "max_lanes": 1,
                "supports_msi": True,
                "supports_msix": False,
            },
            "ultrascale": {
                "name": "ultrascale",
                "fpga_part": "xcvu9p",
                "fpga_family": "ultrascale+",
                "pcie_ip_type": "pcie_ultra",
                "max_lanes": 8,
                "supports_msi": True,
                "supports_msix": True,
            },
        }

        # Configure board discovery mock (class method, not instance method)
        self.mock_board_discovery.discover_boards.return_value = (
            self.sample_boards
        )

    def tearDown(self):
        """Tear down test environment."""
        self.board_discovery_patch.stop()
        self.template_discovery_patch.stop()
        self.repo_manager_patch.stop()
        self.tcl_builder_patch.stop()
        self.path_mkdir_patch.stop()

    @patch("pcileechfwgenerator.vivado_handling.pcileech_build_integration.Path.write_text")
    @patch("pcileechfwgenerator.vivado_handling.pcileech_build_integration.shutil.copy2")
    def test_init(self, mock_copy2, mock_write_text):
        """Test initialization of PCILeechBuildIntegration."""
        integration = PCILeechBuildIntegration(self.output_dir, self.repo_root)

        # Check attributes
        self.assertEqual(integration.output_dir, self.output_dir)
        self.assertEqual(integration.repo_root, self.repo_root)

        # No longer expecting instantiation calls (classes used directly now)

        # Check directory creation
        self.mock_path_mkdir.assert_called_with(parents=True, exist_ok=True)

    def test_get_available_boards(self):
        """Test getting available boards."""
        integration = PCILeechBuildIntegration(self.output_dir, self.repo_root)

        # First call should discover boards
        boards = integration.get_available_boards()
        self.assertEqual(boards, self.sample_boards)
        self.mock_board_discovery.discover_boards.assert_called_once_with(
            self.repo_root
        )

        # Second call should use cache
        self.mock_board_discovery.discover_boards.reset_mock()
        boards_cached = integration.get_available_boards()
        self.assertEqual(boards_cached, self.sample_boards)
        self.mock_board_discovery.discover_boards.assert_not_called()

    def test_prepare_build_environment_invalid_board(self):
        """Test preparing build environment with invalid board."""
        integration = PCILeechBuildIntegration(self.output_dir, self.repo_root)

        with self.assertRaises(ValueError) as context:
            integration.prepare_build_environment("nonexistent_board")

        self.assertIn("Board 'nonexistent_board' not found", str(context.exception))

    @patch("pcileechfwgenerator.vivado_handling.pcileech_build_integration.shutil.copy2")
    def test_prepare_build_environment_valid_board(self, mock_copy2):
        """Test preparing build environment with valid board."""
        integration = PCILeechBuildIntegration(self.output_dir, self.repo_root)

        # Setup mock returns for sub-methods
        integration._copy_xdc_files = MagicMock(return_value=[Path("/tmp/test.xdc")])
        integration._copy_source_files = MagicMock(return_value=[Path("/tmp/test.v")])
        integration._copy_ip_files = MagicMock(return_value=[Path("/tmp/ip/pcie_7x_0.xci")])
        integration._prepare_build_scripts = MagicMock(
            return_value={"main": Path("/tmp/build.tcl")}
        )

        self.mock_template_discovery.copy_board_templates.return_value = [
            "template1.v"
        ]

        # Call the method
        result = integration.prepare_build_environment("artix7")

        # Check the result structure
        self.assertEqual(result["board_name"], "artix7")
        self.assertEqual(result["board_config"], self.sample_boards["artix7"])
        self.assertEqual(result["output_dir"], self.output_dir / "artix7")
        self.assertEqual(result["templates"], ["template1.v"])
        self.assertEqual(result["xdc_files"], [Path("/tmp/test.xdc")])
        self.assertEqual(result["src_files"], [Path("/tmp/test.v")])
        self.assertEqual(result["ip_files"], [Path("/tmp/ip/pcie_7x_0.xci")])
        self.assertEqual(result["build_scripts"], {"main": Path("/tmp/build.tcl")})

        # Verify method calls (class method now)
        self.mock_template_discovery.copy_board_templates.assert_called_once()
        integration._copy_xdc_files.assert_called_once()
        integration._copy_source_files.assert_called_once()
        integration._copy_ip_files.assert_called_once()
        integration._prepare_build_scripts.assert_called_once()

    


    @patch("pcileechfwgenerator.vivado_handling.pcileech_build_integration.shutil.copy2")
    def test_copy_xdc_files(self, mock_copy2):
        """Test copying XDC files."""
        integration = PCILeechBuildIntegration(self.output_dir, self.repo_root)

        # Setup mock XDC files
        xdc_files = [Path("/tmp/test_repo/boards/artix7/constraints/pins.xdc")]
        self.mock_repo_manager.get_xdc_files.return_value = xdc_files

        # Call the method
        output_dir = Path("/tmp/output/constraints")
        result = integration._copy_xdc_files("artix7", output_dir)

        # Check results
        self.assertEqual(len(result), 1)
        self.assertEqual(result[0], output_dir / "pins.xdc")

        # Verify method calls
        self.mock_repo_manager.get_xdc_files.assert_called_once_with(
            "artix7", repo_root=self.repo_root
        )
        mock_copy2.assert_called_once_with(xdc_files[0], output_dir / "pins.xdc")

    


    @patch("pcileechfwgenerator.vivado_handling.pcileech_build_integration.shutil.copy2")
    def test_copy_source_files(self, mock_copy2):
        """Test copying source files."""
        integration = PCILeechBuildIntegration(self.output_dir, self.repo_root)

        # Setup mock source files (class methods now, not instance methods)
        src_files = [Path("/tmp/test_repo/boards/artix7/src/top.v")]
        self.mock_template_discovery.get_source_files.return_value = src_files

        # Setup mock core files (class methods now, not instance methods)
        core_files = {"pcileech_core.v": Path("/tmp/test_repo/common/pcileech_core.v")}
        self.mock_template_discovery.get_pcileech_core_files.return_value = (
            core_files
        )

        # Call the method - pass base output dir, files will go to output_dir/src/
        output_dir = Path("/tmp/output")
        result = integration._copy_source_files("artix7", output_dir)

        # Check results - should have 2 files (1 source + 1 core)
        self.assertEqual(len(result), 2)

        # Verify files are copied to src/ subdirectory with flat structure
        expected_calls = [
            call(src_files[0], output_dir / "src" / "top.v"),
            call(core_files["pcileech_core.v"], output_dir / "src" / "pcileech_core.v"),
        ]
        mock_copy2.assert_has_calls(expected_calls, any_order=True)

        # Verify method calls (class methods now)
        self.mock_template_discovery.get_source_files.assert_called_once_with(
            "artix7", self.repo_root
        )
        self.mock_template_discovery.get_pcileech_core_files.assert_called_once_with(
            self.repo_root
        )
        # Note: get_board_path is no longer called with flat structure design
        self.assertEqual(mock_copy2.call_count, 2)

    @patch("pcileechfwgenerator.vivado_handling.pcileech_build_integration.shutil.copy2")
    def test_copy_source_files_no_nested_src_directory(self, mock_copy2):
        """Test that source files are NOT copied to nested src/src/ directory structure.
        
        Regression test for bug where files were being copied to board_output_dir/src/src/
        instead of board_output_dir/src/, causing duplicate module definitions in Vivado.
        
        GitHub Issue: #524
        """
        integration = PCILeechBuildIntegration(self.output_dir, self.repo_root)

        # Setup mock source files with src/ in their path (realistic scenario)
        board_path = Path("/tmp/test_repo/boards/pcileech_100t484_x1")
        src_files = [
            board_path / "src" / "pcileech_fifo.sv",
            board_path / "src" / "pcileech_mux.sv",
            board_path / "src" / "pcileech_100t484_x1_top.sv",
        ]
        self.mock_template_discovery.get_source_files.return_value = src_files
        self.mock_template_discovery.get_pcileech_core_files.return_value = {}
        self.mock_repo_manager.get_board_path.return_value = board_path

        # Call the method - output_dir should NOT have /src appended
        # This is the fix: pass board_output_dir directly, not board_output_dir / "src"
        board_output_dir = Path("/tmp/output/pcileech_100t484_x1")
        result = integration._copy_source_files("pcileech_100t484_x1", board_output_dir)

        # Verify files are copied to the correct paths (NOT nested src/src/)
        expected_calls = [
            call(src_files[0], board_output_dir / "src" / "pcileech_fifo.sv"),
            call(src_files[1], board_output_dir / "src" / "pcileech_mux.sv"),
            call(src_files[2], board_output_dir / "src" / "pcileech_100t484_x1_top.sv"),
        ]
        mock_copy2.assert_has_calls(expected_calls, any_order=True)

        # Verify NO files were copied to nested src/src/ directory
        for call_args in mock_copy2.call_args_list:
            dest_path = call_args[0][1]
            path_parts = Path(dest_path).parts
            # Check for consecutive 'src' parts indicating nested structure
            for i in range(len(path_parts) - 1):
                if path_parts[i] == 'src' and path_parts[i + 1] == 'src':
                    self.fail(f"Found nested src/src/ structure in path: {dest_path}")

        # Verify results contain correct number of files
        self.assertEqual(len(result), 3)
        
        # Verify all result paths have exactly one 'src' in their hierarchy
        for result_path in result:
            src_count = str(result_path).count('/src/')
            self.assertEqual(src_count, 1, 
                           f"Path should have exactly one 'src' directory, found {src_count}: {result_path}")

    @patch("pcileechfwgenerator.vivado_handling.pcileech_build_integration.shutil.copy2")
    def test_copy_source_files_filename_collision(self, mock_copy2):
        """Test that files with same name from different paths overwrite correctly.
        
        Edge case: When repository has files with identical names in different directories,
        the flat structure means the last one wins (proper behavior for flat deduplication).
        """
        integration = PCILeechBuildIntegration(self.output_dir, self.repo_root)

        # Setup files with same name from different source paths
        src_files = [
            Path("/tmp/test_repo/boards/board1/src/common.sv"),
            Path("/tmp/test_repo/boards/board1/rtl/common.sv"),  # Same filename
        ]
        self.mock_template_discovery.get_source_files.return_value = src_files
        self.mock_template_discovery.get_pcileech_core_files.return_value = {}

        board_output_dir = Path("/tmp/output/board1")
        result = integration._copy_source_files("board1", board_output_dir)

        # Both files should be copied (last one overwrites)
        self.assertEqual(len(result), 2)
        
        # Both should target the same destination path
        expected_dest = board_output_dir / "src" / "common.sv"
        for call_args in mock_copy2.call_args_list:
            dest_path = call_args[0][1]
            self.assertEqual(dest_path, expected_dest,
                           "All files with same name should map to same destination")

    @patch("pcileechfwgenerator.vivado_handling.pcileech_build_integration.shutil.copy2")
    def test_copy_source_files_with_manifest_tracker_duplicate_prevention(self, mock_copy2):
        """Test that manifest tracker prevents actual duplicate file copies."""
        # Create a mock manifest tracker that rejects the second file
        mock_manifest = MagicMock()
        mock_manifest.add_copy_operation.side_effect = [True, False]  # Accept first, reject second
        
        integration = PCILeechBuildIntegration(
            self.output_dir, 
            self.repo_root,
            manifest_tracker=mock_manifest
        )

        src_files = [
            Path("/tmp/test_repo/boards/board1/src/file1.sv"),
            Path("/tmp/test_repo/boards/board1/src/file2.sv"),
        ]
        self.mock_template_discovery.get_source_files.return_value = src_files
        self.mock_template_discovery.get_pcileech_core_files.return_value = {}

        result = integration._copy_source_files("board1", Path("/tmp/output/board1"))

        # Only first file should be copied (second rejected by manifest)
        self.assertEqual(len(result), 1)
        self.assertEqual(mock_copy2.call_count, 1)
        self.assertEqual(result[0].name, "file1.sv")

    @patch("pcileechfwgenerator.vivado_handling.pcileech_build_integration.shutil.copy2")
    def test_copy_source_files_empty_source_list(self, mock_copy2):
        """Test graceful handling when no source files are found."""
        integration = PCILeechBuildIntegration(self.output_dir, self.repo_root)

        # Setup empty source and core file lists
        self.mock_template_discovery.get_source_files.return_value = []
        self.mock_template_discovery.get_pcileech_core_files.return_value = {}

        board_output_dir = Path("/tmp/output/empty_board")
        result = integration._copy_source_files("empty_board", board_output_dir)

        # Should return empty list without errors
        self.assertEqual(len(result), 0)
        mock_copy2.assert_not_called()

    @patch("pcileechfwgenerator.vivado_handling.pcileech_build_integration.shutil.copy2")
    def test_copy_source_files_io_error_continues(self, mock_copy2):
        """Test that IO errors on individual files don't stop the entire copy operation."""
        integration = PCILeechBuildIntegration(self.output_dir, self.repo_root)

        src_files = [
            Path("/tmp/test_repo/boards/board1/src/good_file.sv"),
            Path("/tmp/test_repo/boards/board1/src/bad_file.sv"),
            Path("/tmp/test_repo/boards/board1/src/another_good.sv"),
        ]
        
        # Make second file fail, others succeed
        def copy_side_effect(src, dst):
            if "bad_file" in str(src):
                raise IOError("Permission denied")
            return None
        
        mock_copy2.side_effect = copy_side_effect
        
        self.mock_template_discovery.get_source_files.return_value = src_files
        self.mock_template_discovery.get_pcileech_core_files.return_value = {}

        board_output_dir = Path("/tmp/output/board1")
        result = integration._copy_source_files("board1", board_output_dir)

        # Should have copied 2 out of 3 files (skipping the failed one)
        self.assertEqual(len(result), 2)
        self.assertEqual(mock_copy2.call_count, 3)  # All attempted
        
        # Verify the successful files are in results
        result_names = {path.name for path in result}
        self.assertIn("good_file.sv", result_names)
        self.assertIn("another_good.sv", result_names)
        self.assertNotIn("bad_file.sv", result_names)

    @patch("pcileechfwgenerator.vivado_handling.pcileech_build_integration.Path.read_text")
    @patch("pcileechfwgenerator.vivado_handling.pcileech_build_integration.Path.write_text")
    @patch("pcileechfwgenerator.vivado_handling.pcileech_build_integration.shutil.copy2")
    def test_prepare_build_scripts_existing(
        self, mock_copy2, mock_write_text, mock_read_text
    ):
        """Test preparing build scripts with existing script."""
        integration = PCILeechBuildIntegration(self.output_dir, self.repo_root)

        # Setup mock existing script (class method now)
        existing_script = Path("/tmp/test_repo/boards/artix7/build.tcl")
        self.mock_template_discovery.get_vivado_build_script.return_value = (
            existing_script
        )

        # Setup mock read/adapt (class method now)
        mock_read_text.return_value = "# Original TCL content"
        self.mock_template_discovery.adapt_template_for_board.return_value = (
            "# Adapted TCL content"
        )

        # Call the method
        board_config = self.sample_boards["artix7"]
        output_dir = Path("/tmp/output/artix7")
        result = integration._prepare_build_scripts("artix7", board_config, output_dir)

        # Check results
        self.assertIn("main", result)
        self.assertEqual(result["main"], output_dir / "scripts" / existing_script.name)

        # Verify method calls (class methods now)
        self.mock_template_discovery.get_vivado_build_script.assert_called_once_with(
            "artix7", self.repo_root
        )
        mock_copy2.assert_called_once_with(
            existing_script, output_dir / "scripts" / existing_script.name
        )
        mock_read_text.assert_called_once()
        self.mock_template_discovery.adapt_template_for_board.assert_called_once_with(
            "# Original TCL content", board_config
        )
        mock_write_text.assert_called_once_with("# Adapted TCL content")

    


    @patch("pcileechfwgenerator.vivado_handling.pcileech_build_integration.Path.write_text")
    def test_prepare_build_scripts_generated(self, mock_write_text):
        """Test preparing build scripts with generated scripts."""
        integration = PCILeechBuildIntegration(self.output_dir, self.repo_root)

        # Setup mock - no existing script (class method now)
        self.mock_template_discovery.get_vivado_build_script.return_value = None

        # Setup mock for TCL builder
        mock_tcl_instance = self.mock_tcl_builder.return_value
        mock_tcl_instance.build_pcileech_project_script.return_value = (
            "# Project script"
        )
        mock_tcl_instance.build_pcileech_build_script.return_value = "# Build script"

        # Call the method
        board_config = self.sample_boards["artix7"]
        output_dir = Path("/tmp/output/artix7")
        result = integration._prepare_build_scripts("artix7", board_config, output_dir)

        # Check results
        self.assertIn("project", result)
        self.assertIn("build", result)

        # Verify method calls (class method now)
        self.mock_template_discovery.get_vivado_build_script.assert_called_once_with(
            "artix7", self.repo_root
        )
        self.mock_tcl_builder.assert_called_once_with(output_dir=output_dir / "scripts")

    @patch("pcileechfwgenerator.vivado_handling.pcileech_build_integration.Path.write_text")
    def test_unified_build_script_no_duplicate_source_files(self, mock_write_text):
        """Test that unified build script does not add duplicate source files.
        
        Regression test for bug where files from both src/ and src/src/ were added
        to the Vivado project, causing duplicate module definition errors.
        
        GitHub Issue: #524
        """
        integration = PCILeechBuildIntegration(self.output_dir, self.repo_root)
        
        # Create realistic source file paths
        board_dir = Path("/tmp/output/pcileech_100t484_x1")
        src_files = [
            board_dir / "src" / "pcileech_fifo.sv",
            board_dir / "src" / "pcileech_mux.sv",
            board_dir / "src" / "pcileech_100t484_x1_top.sv",
            board_dir / "src" / "pcileech_pcie_tlp_a7.sv",
        ]
        
        # Mock the prepare_build_environment
        integration.prepare_build_environment = MagicMock(return_value={
            "board_name": "pcileech_100t484_x1",
            "board_config": {
                "name": "pcileech_100t484_x1",
                "fpga_part": "xc7a100tfgg484-1",
                "fpga_family": "7series",
                "pcie_ip_type": "pcie_7x",
            },
            "output_dir": board_dir,
            "src_files": src_files,
            "xdc_files": [],
            "ip_files": [],
        })
        
        # Call create_unified_build_script and capture the script content
        script_path = integration.create_unified_build_script("pcileech_100t484_x1")
        script_content = mock_write_text.call_args[0][0]
        
        # Verify each file is added exactly once
        for src_file in src_files:
            filename = src_file.name
            # Count occurrences of add_files commands for this specific file
            add_files_pattern = f'add_files -norecurse "src/{filename}"'
            count = script_content.count(add_files_pattern)
            self.assertEqual(count, 1, 
                           f"File {filename} should be added exactly once, found {count} times")
        
        # Verify NO files are added from src/src/ (nested structure)
        self.assertNotIn('add_files -norecurse "src/src/', script_content,
                        "Script should not reference nested src/src/ directory structure")
        
        # Verify files ARE added from src/ (correct structure)
        self.assertIn('add_files -norecurse "src/pcileech_fifo.sv"', script_content,
                     "Files should be added from src/ directory")
        
        # Additional check: verify the file deduplication logic is working
        # Count total add_files commands for source files
        add_files_count = script_content.count('add_files -norecurse "src/')
        self.assertEqual(add_files_count, len(src_files),
                        f"Should have exactly {len(src_files)} add_files commands, found {add_files_count}")
        
        # Verify write_text was called once (create_unified_build_script writes build_all.tcl)
        self.assertEqual(mock_write_text.call_count, 1)

    


    @patch("pcileechfwgenerator.vivado_handling.pcileech_build_integration.Path.write_text")
    def test_create_unified_build_script(self, mock_write_text):
        """Test creating unified build script."""
        integration = PCILeechBuildIntegration(self.output_dir, self.repo_root)

        # Mock prepare_build_environment
        mock_build_env = {
            "board_name": "artix7",
            "board_config": self.sample_boards["artix7"],
            "output_dir": self.output_dir / "artix7",
            "templates": ["template1.v"],
            "xdc_files": [self.output_dir / "artix7" / "constraints" / "pins.xdc"],
            "src_files": [self.output_dir / "artix7" / "src" / "top.v"],
            "ip_files": [self.output_dir / "artix7" / "ip" / "pcie_7x_0.xci"],
            "build_scripts": {
                "main": self.output_dir / "artix7" / "scripts" / "build.tcl"
            },
        }
        integration.prepare_build_environment = MagicMock(return_value=mock_build_env)

        # Call the method
        result = integration.create_unified_build_script("artix7")

        # Check results
        self.assertEqual(result, self.output_dir / "artix7" / "build_all.tcl")

        # Verify method calls
        integration.prepare_build_environment.assert_called_once_with("artix7")
        mock_write_text.assert_called_once()
        tcl_content = mock_write_text.call_args[0][0]
        self.assertIn("PCILeech Unified Build Script for artix7", tcl_content)
        self.assertIn("FPGA Part: xc7a35t", tcl_content)

    


    def test_prepare_build_environment_fail_fast_no_ip(self):
        """Ensure build aborts with SystemExit when no IP definition files are found."""
        integration = PCILeechBuildIntegration(self.output_dir, self.repo_root)
        # Mock successful discovery of board and sources/constraints but empty IP list
        integration._copy_xdc_files = MagicMock(return_value=[Path("/tmp/test.xdc")])
        integration._copy_source_files = MagicMock(return_value=[Path("/tmp/test.v")])
        integration._copy_ip_files = MagicMock(return_value=[])  # trigger fail-fast
        integration._prepare_build_scripts = MagicMock(return_value={"main": Path("/tmp/build.tcl")})
        with self.assertRaises(SystemExit) as ctx:
            integration.prepare_build_environment("artix7")
        self.assertEqual(ctx.exception.code, 2)
        integration._copy_ip_files.assert_called_once()

    def test_unified_build_script_sets_working_directory(self):
        """Test that build_all.tcl changes to board directory for path resolution."""
        integration = PCILeechBuildIntegration(self.output_dir, self.repo_root)
        
        # Mock the prepare_build_environment to return minimal data
        integration.prepare_build_environment = MagicMock(return_value={
            "board_name": "artix7",
            "board_config": self.sample_boards["artix7"],
            "output_dir": self.output_dir / "artix7",
            "src_files": [self.output_dir / "artix7" / "src" / "test.sv"],
            "xdc_files": [self.output_dir / "artix7" / "constraints" / "test.xdc"],
            "ip_files": [self.output_dir / "artix7" / "ip" / "test.xci"],
        })
        
        # Mock write_text to capture the content without actual file write
        with patch("pathlib.Path.write_text") as mock_write:
            # Create the build script
            script_path = integration.create_unified_build_script("artix7")
            
            # Get the content that was written
            self.assertTrue(mock_write.called, "write_text should have been called")
            script_content = mock_write.call_args[0][0]
        
        # Verify the script changes to board directory
        self.assertIn("cd [file dirname [info script]]", script_content,
                     "Script should change to board directory for path resolution")
        self.assertIn('puts "Working directory: [pwd]"', script_content,
                     "Script should print working directory for debugging")

    def test_unified_build_script_uses_relative_paths_for_sources(self):
        """Test that source files use relative paths, not absolute paths."""
        integration = PCILeechBuildIntegration(self.output_dir, self.repo_root)
        
        # Create test paths
        board_dir = self.output_dir / "artix7"
        src_file = board_dir / "src" / "test_module.sv"
        
        # Mock the prepare_build_environment
        integration.prepare_build_environment = MagicMock(return_value={
            "board_name": "artix7",
            "board_config": self.sample_boards["artix7"],
            "output_dir": board_dir,
            "src_files": [src_file],
            "xdc_files": [],
            "ip_files": [],
        })
        
        # Mock write_text to capture the content
        with patch("pathlib.Path.write_text") as mock_write:
            script_path = integration.create_unified_build_script("artix7")
            script_content = mock_write.call_args[0][0]
        
        # Verify relative path is used, not absolute path
        self.assertIn('add_files -norecurse "src/test_module.sv"', script_content,
                     "Source files should use relative paths from board directory")
        # Ensure absolute paths are NOT used
        self.assertNotIn(str(src_file), script_content,
                        "Source files should not use absolute paths")

    def test_unified_build_script_uses_relative_paths_for_constraints(self):
        """Test that constraint files use relative paths, not absolute paths."""
        integration = PCILeechBuildIntegration(self.output_dir, self.repo_root)
        
        # Create test paths
        board_dir = self.output_dir / "artix7"
        xdc_file = board_dir / "constraints" / "timing.xdc"
        
        # Mock the prepare_build_environment
        integration.prepare_build_environment = MagicMock(return_value={
            "board_name": "artix7",
            "board_config": self.sample_boards["artix7"],
            "output_dir": board_dir,
            "src_files": [],
            "xdc_files": [xdc_file],
            "ip_files": [],
        })
        
        # Mock write_text to capture the content
        with patch("pathlib.Path.write_text") as mock_write:
            script_path = integration.create_unified_build_script("artix7")
            script_content = mock_write.call_args[0][0]
        
        # Verify relative path is used
        self.assertIn('add_files -fileset constrs_1 -norecurse "constraints/timing.xdc"', 
                     script_content,
                     "Constraint files should use relative paths from board directory")
        # Ensure absolute paths are NOT used
        self.assertNotIn(str(xdc_file), script_content,
                        "Constraint files should not use absolute paths")

    def test_unified_build_script_uses_relative_ip_directory(self):
        """Test that IP files directory uses relative path after cd command."""
        integration = PCILeechBuildIntegration(self.output_dir, self.repo_root)
        
        # Mock the prepare_build_environment
        integration.prepare_build_environment = MagicMock(return_value={
            "board_name": "pcileech_100t484_x1",
            "board_config": self.sample_boards["artix7"],
            "output_dir": self.output_dir / "pcileech_100t484_x1",
            "src_files": [],
            "xdc_files": [],
            "ip_files": [self.output_dir / "pcileech_100t484_x1" / "ip" / "test.xci"],
        })
        
        # Mock write_text to capture the content
        with patch("pathlib.Path.write_text") as mock_write:
            script_path = integration.create_unified_build_script("pcileech_100t484_x1")
            script_content = mock_write.call_args[0][0]
        
        # After cd to board directory, IP path should be relative
        self.assertIn('set ip_dir [file normalize "./ip"]', script_content,
                     "IP directory should use relative path './ip' after cd to board directory")
        
        # Should NOT use board name in path since we're already in board directory
        self.assertNotIn('./pcileech_100t484_x1/ip', script_content,
                        "IP path should not include board name after cd to board directory")

    def test_unified_build_script_path_resolution_integration(self):
        """Integration test: verify complete path resolution in build script."""
        integration = PCILeechBuildIntegration(self.output_dir, self.repo_root)
        
        # Create realistic test paths
        board_name = "pcileech_100t484_x1"
        board_dir = self.output_dir / board_name
        
        # Mock the prepare_build_environment with realistic paths
        integration.prepare_build_environment = MagicMock(return_value={
            "board_name": board_name,
            "board_config": {
                "name": board_name,
                "fpga_part": "xc7a100tfgg484-1",
                "fpga_family": "7series",
                "pcie_ip_type": "pcie_7x",
            },
            "output_dir": board_dir,
            "src_files": [
                board_dir / "src" / "pcileech_100t484_x1_top.sv",
                board_dir / "src" / "pcileech_com.sv",
            ],
            "xdc_files": [
                board_dir / "constraints" / "pcileech_100t484_x1_captaindma_100t.xdc",
            ],
            "ip_files": [
                board_dir / "ip" / "fifo_64_64_clk2_comrx.xci",
                board_dir / "ip" / "pcie_7x_0.xci",
            ],
        })
        
        # Mock write_text to capture the content
        with patch("pathlib.Path.write_text") as mock_write:
            script_path = integration.create_unified_build_script(board_name)
            script_content = mock_write.call_args[0][0]
        
        # Verify all path resolution aspects
        assertions = [
            ("cd [file dirname [info script]]", "Must change to board directory first"),
            ('set ip_dir [file normalize "./ip"]', "IP dir must be relative after cd"),
            ('"src/pcileech_100t484_x1_top.sv"', "Source files must use relative paths"),
            ('"src/pcileech_com.sv"', "All source files must be relative"),
            ('"constraints/pcileech_100t484_x1_captaindma_100t.xdc"', 
             "Constraint files must use relative paths"),
        ]
        
        for expected_text, error_msg in assertions:
            self.assertIn(expected_text, script_content, error_msg)
        
        # Verify NO absolute paths are used (common mistake that caused the bug)
        self.assertNotIn("/root/", script_content,
                        "Script must not contain absolute container paths")
        self.assertNotIn("/tmp/", script_content,
                        "Script must not contain absolute test paths")

    


    def test_validate_board_compatibility(self):
        """Test validating board compatibility."""
        integration = PCILeechBuildIntegration(self.output_dir, self.repo_root)

        # Mock get_board_config
        with patch(
            "pcileechfwgenerator.vivado_handling.pcileech_build_integration.get_board_config"
        ) as mock_get_board_config:
            mock_get_board_config.return_value = self.sample_boards["artix7"]

            # Test case 1: Compatible configuration
            device_config = {
                "pcie_lanes": 1,
                "requires_msix": False,
                "requires_ultrascale": False,
            }
            is_compatible, warnings = integration.validate_board_compatibility(
                "artix7", device_config
            )
            self.assertTrue(is_compatible)
            self.assertEqual(len(warnings), 0)

            # Test case 2: Incompatible - requires MSI-X
            device_config = {
                "pcie_lanes": 1,
                "requires_msix": True,
                "requires_ultrascale": False,
            }
            is_compatible, warnings = integration.validate_board_compatibility(
                "artix7", device_config
            )
            self.assertFalse(is_compatible)
            self.assertEqual(len(warnings), 1)

            # Test case 3: Multiple incompatibilities
            device_config = {
                "pcie_lanes": 4,
                "requires_msix": True,
                "requires_ultrascale": True,
            }
            is_compatible, warnings = integration.validate_board_compatibility(
                "artix7", device_config
            )
            self.assertFalse(is_compatible)
            self.assertEqual(len(warnings), 3)

    


    @patch("pcileechfwgenerator.vivado_handling.pcileech_build_integration.logger")
    def test_integrate_pcileech_build(self, mock_logger):
        """Test integrate_pcileech_build function."""
        # Mock PCILeechBuildIntegration
        with patch(
            "pcileechfwgenerator.vivado_handling.pcileech_build_integration.PCILeechBuildIntegration"
        ) as mock_integration_class:
            mock_integration = mock_integration_class.return_value
            mock_integration.create_unified_build_script.return_value = Path(
                "/tmp/output/artix7/build_all.tcl"
            )
            mock_integration.validate_board_compatibility.return_value = (True, [])

            # Call the function without device config
            result = integrate_pcileech_build("artix7", self.output_dir)

            # Check results
            self.assertEqual(result, Path("/tmp/output/artix7/build_all.tcl"))

            # Verify method calls - validate_board_compatibility should NOT be called when device_config is None
            mock_integration_class.assert_called_once_with(self.output_dir, None)
            mock_integration.create_unified_build_script.assert_called_once_with(
                "artix7", None
            )
            mock_integration.validate_board_compatibility.assert_not_called()

            # Test with device config
            mock_integration_class.reset_mock()
            mock_integration.reset_mock()

            device_config = {"requires_msix": True}
            mock_integration.validate_board_compatibility.return_value = (
                False,
                ["Warning 1"],
            )

            result = integrate_pcileech_build("artix7", self.output_dir, device_config)

            # When device_config is provided, validate_board_compatibility should be called
            mock_integration.validate_board_compatibility.assert_called_once_with(
                "artix7", device_config
            )
            # Check that the warning was logged (with formatted message)
            mock_logger.warning.assert_called_once()
            warning_call_args = mock_logger.warning.call_args[0][0]
            self.assertIn("Warning 1", warning_call_args)
            mock_logger.error.assert_called_once()


if __name__ == "__main__":
    unittest.main()
