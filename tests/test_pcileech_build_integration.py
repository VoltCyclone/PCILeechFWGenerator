#!/usr/bin/env python3
"""Tests for the PCILeech Build Integration module."""

import sys
import unittest
from pathlib import Path
from unittest.mock import MagicMock, call, patch

# Add project root to path for imports
sys.path.insert(0, str(Path(__file__).parent.parent))

from src.vivado_handling.pcileech_build_integration import (
    PCILeechBuildIntegration, integrate_pcileech_build)


class TestPCILeechBuildIntegration(unittest.TestCase):
    """Test cases for PCILeechBuildIntegration class."""

    def setUp(self):
        """Set up test environment."""
        self.output_dir = Path("/tmp/test_output")
        self.repo_root = Path("/tmp/test_repo")

        # Common patches
        self.board_discovery_patch = patch(
            "src.vivado_handling.pcileech_build_integration.BoardDiscovery"
        )
        self.template_discovery_patch = patch(
            "src.vivado_handling.pcileech_build_integration.TemplateDiscovery"
        )
        self.repo_manager_patch = patch(
            "src.vivado_handling.pcileech_build_integration.RepoManager"
        )
        self.tcl_builder_patch = patch(
            "src.vivado_handling.pcileech_build_integration.TCLBuilder"
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

    @patch("src.vivado_handling.pcileech_build_integration.Path.write_text")
    @patch("src.vivado_handling.pcileech_build_integration.shutil.copy2")
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

    @patch("src.vivado_handling.pcileech_build_integration.shutil.copy2")
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

    


    @patch("src.vivado_handling.pcileech_build_integration.shutil.copy2")
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

    


    @patch("src.vivado_handling.pcileech_build_integration.shutil.copy2")
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

        # Setup mock board path
        self.mock_repo_manager.get_board_path.return_value = Path(
            "/tmp/test_repo/boards/artix7"
        )

        # Call the method
        output_dir = Path("/tmp/output/src")
        result = integration._copy_source_files("artix7", output_dir)

        # Check results
        self.assertEqual(len(result), 2)

        # Verify method calls (class methods now)
        self.mock_template_discovery.get_source_files.assert_called_once_with(
            "artix7", self.repo_root
        )
        self.mock_template_discovery.get_pcileech_core_files.assert_called_once_with(
            self.repo_root
        )
        self.mock_repo_manager.get_board_path.assert_called_once_with(
            "artix7", repo_root=self.repo_root
        )
        self.assertEqual(mock_copy2.call_count, 2)

    


    @patch("src.vivado_handling.pcileech_build_integration.Path.read_text")
    @patch("src.vivado_handling.pcileech_build_integration.Path.write_text")
    @patch("src.vivado_handling.pcileech_build_integration.shutil.copy2")
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

    


    @patch("src.vivado_handling.pcileech_build_integration.Path.write_text")
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
        mock_tcl_instance.build_pcileech_project_script.assert_called_once()
        mock_tcl_instance.build_pcileech_build_script.assert_called_once()
        self.assertEqual(mock_write_text.call_count, 2)

    


    @patch("src.vivado_handling.pcileech_build_integration.Path.write_text")
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

    


    def test_validate_board_compatibility(self):
        """Test validating board compatibility."""
        integration = PCILeechBuildIntegration(self.output_dir, self.repo_root)

        # Mock get_board_config
        with patch(
            "src.vivado_handling.pcileech_build_integration.get_board_config"
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

    


    @patch("src.vivado_handling.pcileech_build_integration.logger")
    def test_integrate_pcileech_build(self, mock_logger):
        """Test integrate_pcileech_build function."""
        # Mock PCILeechBuildIntegration
        with patch(
            "src.vivado_handling.pcileech_build_integration.PCILeechBuildIntegration"
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
