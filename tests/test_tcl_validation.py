#!/usr/bin/env python3
"""
Test Suite for TCL Validation and Workspace Cleaning

Tests the new validation script generation and workspace cleaning functionality
added to prevent "no top module" and stale project errors.
"""

import sys
import unittest
from pathlib import Path

# Add parent directory to path for imports
sys.path.insert(0, str(Path(__file__).parent.parent))

from src.templating.tcl_builder import (  # noqa: E402
    BuildContext,
    TCLScriptType,
)


class TestTCLValidationEnhancements(unittest.TestCase):
    """Test validation and workspace cleaning enhancements."""

    def test_validate_project_script_type_exists(self):
        """Test that VALIDATE_PROJECT script type is defined."""
        self.assertTrue(hasattr(TCLScriptType, "VALIDATE_PROJECT"))
        self.assertEqual(TCLScriptType.VALIDATE_PROJECT.value, "validate_project")

    def test_validate_project_template_exists(self):
        """Test that validation template file exists."""
        root = Path(__file__).parent.parent
        template_path = root / "src" / "templates" / "tcl" / "validate_project.j2"
        self.assertTrue(
            template_path.exists(),
            f"Validation template should exist at {template_path}",
        )

    def test_template_files_exist(self):
        """Test that all modified template files exist."""
        root = Path(__file__).parent.parent
        templates_dir = root / "src" / "templates" / "tcl"

        required_templates = [
            "master_build.j2",
            "pcileech_project_setup.j2",
            "pcileech_sources.j2",
            "synthesis.j2",
            "validate_project.j2",
        ]

        for template in required_templates:
            template_path = templates_dir / template
            self.assertTrue(
                template_path.exists(),
                f"Template {template} should exist at {template_path}",
            )

    def test_build_context_validation(self):
        """Test that BuildContext still enforces donor-uniqueness."""
        context = BuildContext(
            board_name="test_board",
            fpga_part="xc7a35tcsg324-2",
            fpga_family="Artix-7",
            pcie_ip_type="7x",
            max_lanes=4,
            supports_msi=True,
            supports_msix=False,
        )

        # Should raise ValueError without donor IDs
        with self.assertRaises(ValueError) as cm:
            context.to_template_context()

        # Should mention donor-unique firmware
        self.assertIn("donor-unique", str(cm.exception).lower())

    def test_build_context_with_valid_ids(self):
        """Test that BuildContext works with proper donor IDs."""
        context = BuildContext(
            board_name="test_board",
            fpga_part="xc7a35tcsg324-2",
            fpga_family="Artix-7",
            pcie_ip_type="7x",
            max_lanes=4,
            supports_msi=True,
            supports_msix=False,
            vendor_id=0x10EC,
            device_id=0x8168,
            revision_id=0x15,
            class_code=0x020000,
            pcie_max_link_speed_code=2,
            pcie_max_link_width=4,
        )

        # Should not raise with valid donor IDs
        template_context = context.to_template_context()
        self.assertIsNotNone(template_context)
        self.assertIsInstance(template_context, dict)


class TestTemplateContentValidation(unittest.TestCase):
    """Test that template files contain expected validation content."""

    def setUp(self):
        """Set up paths."""
        self.root = Path(__file__).parent.parent
        self.templates_dir = self.root / "src" / "templates" / "tcl"

    def test_master_build_has_clean_workspace(self):
        """Test that master_build.j2 includes workspace cleaning."""
        template_path = self.templates_dir / "master_build.j2"
        if not template_path.exists():
            self.skipTest("Template not found")

        content = template_path.read_text()

        # Should have clean_workspace function
        self.assertIn("clean_workspace", content)

        # Should remove project directory
        self.assertIn("vivado_project", content)

        # Should have die proc with helpful errors
        self.assertIn("proc die", content)
        self.assertIn("Common causes", content)

    def test_project_setup_validates_creation(self):
        """Test that project setup validates project was created."""
        template_path = self.templates_dir / "pcileech_project_setup.j2"
        if not template_path.exists():
            self.skipTest("Template not found")

        content = template_path.read_text()

        # Should check file exists after creation
        self.assertIn("file exists", content)

        # Should validate FPGA part
        self.assertIn("get_property PART", content)

    def test_sources_validates_files_added(self):
        """Test that sources script validates files were added."""
        template_path = self.templates_dir / "pcileech_sources.j2"
        if not template_path.exists():
            self.skipTest("Template not found")

        content = template_path.read_text()

        # Should validate source files exist
        self.assertIn("llength", content)
        self.assertIn("source_files", content)

        # Should set top module
        self.assertIn("set_property top", content)

        # Should disable auto-set
        self.assertIn("top_auto_set 0", content)

    def test_synthesis_validates_before_start(self):
        """Test that synthesis validates before starting."""
        template_path = self.templates_dir / "synthesis.j2"
        if not template_path.exists():
            self.skipTest("Template not found")

        content = template_path.read_text()

        # Should validate before synthesis
        self.assertIn("Validating project", content)

        # Should check top module
        self.assertIn("get_property top", content)

    def test_validation_script_has_checks(self):
        """Test that validation script includes all necessary checks."""
        template_path = self.templates_dir / "validate_project.j2"
        if not template_path.exists():
            self.skipTest("Template not found")

        content = template_path.read_text()

        # Should check project file
        self.assertIn("project_file", content)

        # Should check top module
        self.assertIn("top_module", content)

        # Should check source files
        self.assertIn("source_files", content)

        # Should track errors/warnings
        self.assertIn("validation_errors", content)
        self.assertIn("validation_warnings", content)

        # Should exit with codes
        self.assertIn("exit 1", content)
        self.assertIn("exit 0", content)


if __name__ == "__main__":
    unittest.main()
