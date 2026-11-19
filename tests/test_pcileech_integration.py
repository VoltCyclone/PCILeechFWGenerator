#!/usr/bin/env python3
"""
Unit tests for PCILeech FPGA integration with dynamic board discovery
and template loading from the cloned repository.
"""

import shutil
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

import pytest
from pcileechfwgenerator.file_management.board_discovery import (
    BoardDiscovery,
    discover_all_boards,
)
from pcileechfwgenerator.file_management.repo_manager import RepoManager
from pcileechfwgenerator.file_management.template_discovery import TemplateDiscovery
from pcileechfwgenerator.vivado_handling.build_evidence import verify_build_manifest
from pcileechfwgenerator.vivado_handling.pcileech_build_integration import (
    DONOR_IP_OVERRIDE_NAME,
    BuildArtifactError,
    PCILeechBuildIntegration,
)

from tests.test_msix_artifact_staging import _HAVE_BOARD
from tests.test_msix_artifact_staging import BOARD as STAGED_BOARD
from tests.test_msix_artifact_staging import _stage2


@pytest.mark.hardware  # Requires external repo; skip in CI unit runs.
class TestPCILeechIntegration(unittest.TestCase):
    """Test suite for PCILeech integration components."""

    @classmethod
    def setUpClass(cls):
        """Set up test fixtures that are shared across all tests."""
        # Ensure repository is available for tests
        cls.repo_path = RepoManager.ensure_repo()

    def setUp(self):
        """Set up test fixtures for each test."""
        # Create a temporary directory for test outputs
        self.test_dir = Path(tempfile.mkdtemp())

    def tearDown(self):
        """Clean up after each test."""
        # Remove temporary directory
        if self.test_dir.exists():
            shutil.rmtree(self.test_dir)

    def test_repository_cloning(self):
        """Test that the pcileech-fpga repository can be cloned."""
        # Test repository cloning/verification
        repo_path = RepoManager.ensure_repo()
        self.assertIsNotNone(repo_path)
        self.assertTrue(repo_path.exists())

        # Check if it's a valid git repository
        git_dir = repo_path / ".git"
        self.assertTrue(git_dir.exists(), "Repository should contain .git directory")

    def test_board_discovery(self):
        """Test dynamic board discovery from the repository."""
        # Discover boards
        boards = discover_all_boards()

        # Verify we found boards
        self.assertGreater(len(boards), 0, "Should discover at least one board")

        # Check that each board has required attributes
        for board_name, config in boards.items():
            self.assertIn(
                "fpga_part", config, f"Board {board_name} should have fpga_part"
            )
            self.assertIn(
                "fpga_family", config, f"Board {board_name} should have fpga_family"
            )
            self.assertIn(
                "pcie_ip_type", config, f"Board {board_name} should have pcie_ip_type"
            )

        # Test specific board retrieval if known board exists
        known_boards = [
            "pcileech_75t484_x1",
            "pcileech_35t325_x1",
            "pcileech_enigma_x1",
        ]
        found_known_board = False
        for board in known_boards:
            if board in boards:
                found_known_board = True
                break
        self.assertTrue(
            found_known_board,
            f"Should find at least one known board from {known_boards}",
        )

    def test_template_discovery(self):
        """Test template discovery for boards."""
        # Get available boards
        boards = discover_all_boards()
        self.assertGreater(
            len(boards), 0, "Need at least one board for template testing"
        )

        # Test with first available board
        test_board = list(boards.keys())[0]

        # Discover templates
        templates = TemplateDiscovery.discover_templates(test_board)
        self.assertIsInstance(templates, dict)

        # Check for expected template categories
        possible_categories = [
            "vivado_tcl",
            "systemverilog",
            "verilog",
            "constraints",
            "ip_config",
        ]
        found_categories = [cat for cat in possible_categories if cat in templates]
        self.assertGreater(
            len(found_categories),
            0,
            f"Should find at least one template category for {test_board}",
        )

        # Verify template files exist
        for category, files in templates.items():
            for file_path in files:
                self.assertTrue(
                    file_path.exists(), f"Template file {file_path} should exist"
                )

    def test_build_integration_initialization(self):
        """Test build integration initialization."""
        # Create build integration instance
        integration = PCILeechBuildIntegration(self.test_dir)

        # Verify initialization
        self.assertEqual(integration.output_dir, self.test_dir)
        self.assertIsNotNone(integration.repo_root)
        self.assertTrue(integration.repo_root.exists())

        # Get available boards
        boards = integration.get_available_boards()
        self.assertIsInstance(boards, dict)
        self.assertGreater(len(boards), 0)

    def test_build_environment_requires_stage2_artifacts(self):
        """Stage 3 refuses a board whose output tree Stage 2 never finalized.

        Previously this copied stock board files into an empty output
        directory; that fallback is gone by design.
        """
        integration = PCILeechBuildIntegration(self.test_dir)
        boards = integration.get_available_boards()
        if not boards:
            self.skipTest("No boards available for testing")

        test_board = list(boards.keys())[0]
        with self.assertRaises(BuildArtifactError):
            integration.prepare_build_environment(test_board)
        self.assertFalse((self.test_dir / test_board).exists())

    def test_build_environment_preparation(self):
        """Prepare the build environment from a real finalized Stage 2 tree."""
        if not _HAVE_BOARD:
            self.skipTest("voltcyclone-fpga submodule not initialized")
        _stage2(self.test_dir)
        integration = PCILeechBuildIntegration(self.test_dir)

        build_env = integration.prepare_build_environment(STAGED_BOARD)

        for key in (
            "board_name",
            "board_config",
            "output_dir",
            "templates",
            "xdc_files",
            "src_files",
            "ip_files",
            "build_scripts",
            "stage2_manifest",
        ):
            self.assertIn(key, build_env)
        self.assertTrue(build_env["output_dir"].exists())

        manifest = build_env["stage2_manifest"]
        self.assertEqual(
            sorted(p.name for p in build_env["src_files"]),
            sorted(manifest["sources"]),
        )
        self.assertEqual(
            [p.name for p in build_env["xdc_files"]], manifest["constraints"]
        )
        for p in [*build_env["src_files"], *build_env["xdc_files"]]:
            self.assertTrue(p.is_file())
            self.assertTrue(p.is_relative_to(build_env["output_dir"]))

    def test_unified_build_script_creation(self):
        """Create the unified build script from a real finalized Stage 2 tree."""
        if not _HAVE_BOARD:
            self.skipTest("voltcyclone-fpga submodule not initialized")
        _stage2(self.test_dir)
        integration = PCILeechBuildIntegration(self.test_dir)

        build_script = integration.create_unified_build_script(STAGED_BOARD)

        self.assertTrue(build_script.exists())
        self.assertEqual(build_script.name, "build_all.tcl")
        content = build_script.read_text()
        self.assertIn("PCILeech", content)
        self.assertIn(STAGED_BOARD, content)
        self.assertIn("create_project", content)
        self.assertIn("add_files", content)
        self.assertIn(f'source "{DONOR_IP_OVERRIDE_NAME}"', content)
        self.assertNotIn("vivado_generate_project", content)
        self.assertTrue((build_script.parent / "build_inputs.json").is_file())
        verify_build_manifest(build_script)

    def test_board_compatibility_validation(self):
        """Test board compatibility validation."""
        integration = PCILeechBuildIntegration(self.test_dir)
        boards = integration.get_available_boards()

        if not boards:
            self.skipTest("No boards available for testing")

        # Test device configuration
        device_config = {
            "requires_msix": True,
            "pcie_lanes": 4,
            "requires_ultrascale": False,
        }

        # Test first board
        test_board = list(boards.keys())[0]
        is_compatible, warnings = integration.validate_board_compatibility(
            test_board, device_config
        )

        # Results depend on board capabilities
        self.assertIsInstance(is_compatible, bool)
        self.assertIsInstance(warnings, list)

        # If not compatible, should have warnings
        if not is_compatible:
            self.assertGreater(len(warnings), 0)

    def test_core_files_discovery(self):
        """Test discovery of core PCILeech files."""
        core_files = TemplateDiscovery.get_pcileech_core_files()

        # Should find at least some core files
        self.assertIsInstance(core_files, dict)

        # Check for common core files
        common_files = [
            "pcileech_tlps128_bar_controller.sv",
            "pcileech_fifo.sv",
            "pcileech_mux.sv",
            "pcileech_com.sv",
        ]

        found_count = sum(1 for f in common_files if f in core_files)
        self.assertGreater(
            found_count, 0, f"Should find at least one core file from {common_files}"
        )

        # Verify found files exist
        for filename, filepath in core_files.items():
            self.assertTrue(
                filepath.exists(), f"Core file {filename} at {filepath} should exist"
            )

    def test_template_adaptation(self):
        """Test template content adaptation."""
        # Test template adaptation
        template_content = """
        module test_module #(
            parameter FPGA_PART = "${FPGA_PART}",
            parameter BOARD_NAME = "${BOARD_NAME}"
        );
        """

        board_config = {
            "name": "test_board",
            "fpga_part": "xc7a35tcsg324-2",
            "fpga_family": "7series",
            "pcie_ip_type": "pcie_7x",
            "max_lanes": 1,
        }

        adapted = TemplateDiscovery.adapt_template_for_board(
            template_content, board_config
        )

        # Verify placeholders were replaced
        self.assertNotIn("${FPGA_PART}", adapted)
        self.assertNotIn("${BOARD_NAME}", adapted)
        self.assertIn("xc7a35tcsg324-2", adapted)
        self.assertIn("test_board", adapted)

    @patch("pcileechfwgenerator.file_management.repo_manager.RepoManager.ensure_repo")
    def test_repository_error_handling(self, mock_ensure_repo):
        """Test error handling when repository operations fail."""
        # Simulate repository error
        mock_ensure_repo.side_effect = RuntimeError("Git operation failed")

        # Board discovery should handle the error gracefully
        with self.assertRaises(RuntimeError):
            discover_all_boards()

    def test_invalid_board_handling(self):
        """Test handling of invalid board names."""
        integration = PCILeechBuildIntegration(self.test_dir)

        # Test with invalid board name
        with self.assertRaises(ValueError):
            integration.prepare_build_environment("invalid_board_name_xyz")

    def test_board_display_info(self):
        """Test board display information generation."""
        boards = discover_all_boards()
        if not boards:
            self.skipTest("No boards available for testing")

        display_info = BoardDiscovery.get_board_display_info(boards)

        # Verify display info structure
        self.assertIsInstance(display_info, list)
        for board_name, info in display_info:
            self.assertIn("display_name", info)
            self.assertIn("description", info)
            self.assertIn("is_recommended", info)
            self.assertIsInstance(info["is_recommended"], bool)


@pytest.mark.hardware  # Depends on cloned repo; skip in CI.
class TestBoardConfigIntegration(unittest.TestCase):
    """Test suite for board configuration with dynamic discovery."""

    def test_board_config_dynamic_loading(self):
        """Test that board configurations are loaded dynamically."""
        from pcileechfwgenerator.device_clone.board_config import (
            get_fpga_part,
            list_supported_boards,
            validate_board,
        )

        # Get supported boards
        boards = list_supported_boards()
        self.assertIsInstance(boards, list)
        self.assertGreater(len(boards), 0)

        # Test board validation
        if boards:
            test_board = boards[0]
            self.assertTrue(validate_board(test_board))
            self.assertFalse(validate_board("invalid_board_xyz"))

            # Test FPGA part retrieval
            fpga_part = get_fpga_part(test_board)
            self.assertIsInstance(fpga_part, str)
            self.assertTrue(fpga_part.startswith(("xc", "XC")))

    def test_board_recommendations(self):
        """Test board recommendation system."""
        from pcileechfwgenerator.device_clone.board_config import (
            list_boards_with_recommendations,
        )

        recommendations = list_boards_with_recommendations()
        self.assertIsInstance(recommendations, list)

        # Check structure
        for board_name, display_info in recommendations:
            self.assertIsInstance(board_name, str)
            self.assertIsInstance(display_info, dict)
            self.assertIn("display_name", display_info)
            self.assertIn("is_recommended", display_info)


if __name__ == "__main__":
    unittest.main()
