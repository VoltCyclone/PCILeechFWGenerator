#!/usr/bin/env python3
"""Tests for the PCILeech Build Integration module.

The unittest class covers board metadata, script helpers and compatibility
checks with the board repository mocked. Everything that stages artifacts or
writes the unified build script runs against a real Stage 2 output tree built
from the ``pcileech_squirrel`` board (see tests/test_msix_artifact_staging.py):
Stage 3 consumes only ``stage2_artifacts.json`` and never copies stock board
files, so those paths cannot be meaningfully exercised against mocked repos.
"""

import json
import shutil
import unittest
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest
from pcileechfwgenerator.utils.file_manifest import FileManifestTracker
from pcileechfwgenerator.vivado_handling import pcileech_build_integration as pbi
from pcileechfwgenerator.vivado_handling.build_evidence import (
    BUILD_QUALITY_TCL,
    verify_build_manifest,
)
from pcileechfwgenerator.vivado_handling.pcileech_build_integration import (
    CFGSPACE_COE,
    DONOR_IP_OVERRIDE_NAME,
    STAGE2_MANIFEST_NAME,
    WRITEMASK_COE,
    BuildArtifactError,
    PCILeechBuildIntegration,
    integrate_pcileech_build,
    sha256_file,
)

from tests.test_msix_artifact_staging import (
    _HAVE_BOARD,
    BOARD,
    CONTROLLER,
    MARKER,
    _forbid_stock_lookup,
    _integration,
    _stage2,
)


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
        self.repo_manager_patch = patch(
            "pcileechfwgenerator.vivado_handling.pcileech_build_integration.RepoManager"
        )
        self.path_mkdir_patch = patch("pathlib.Path.mkdir")

        # Start patches
        self.mock_board_discovery = self.board_discovery_patch.start()
        self.mock_repo_manager = self.repo_manager_patch.start()
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
        self.mock_board_discovery.discover_boards.return_value = self.sample_boards

    def tearDown(self):
        """Tear down test environment."""
        self.board_discovery_patch.stop()
        self.repo_manager_patch.stop()
        self.path_mkdir_patch.stop()

    @patch(
        "pcileechfwgenerator.vivado_handling.pcileech_build_integration.Path.write_text"
    )
    @patch(
        "pcileechfwgenerator.vivado_handling.pcileech_build_integration.shutil.copy2"
    )
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
            args, kwargs = mock_integration_class.call_args
            self.assertEqual(args, (self.output_dir, None))
            self.assertIn("manifest_tracker", kwargs)
            mock_integration.create_unified_build_script.assert_called_once_with(
                "artix7", None, vivado_jobs=None
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


# ---------------------------------------------------------------------------
# Stage 3 staging and unified script against a real Stage 2 output tree.
#
# These replace earlier mock-only tests that encoded the removed behaviour:
# copying stock board RTL/XDC/IP from the repository and logging-and-skipping
# per-file copy failures. Stage 3 now stages exactly the manifest's artifact
# set and every staging failure is fatal.
# ---------------------------------------------------------------------------

needs_board = pytest.mark.skipif(
    not _HAVE_BOARD, reason="voltcyclone-fpga submodule not initialized"
)


@pytest.fixture
def staged(tmp_path, monkeypatch):
    """A finalized Stage 2 tree; stock board lookups fail from here on."""
    hashes = _stage2(tmp_path)
    _forbid_stock_lookup(monkeypatch)
    manifest = json.loads((tmp_path / STAGE2_MANIFEST_NAME).read_text())
    return tmp_path, manifest, hashes


@needs_board
def test_prepare_build_environment_stages_manifest_set(staged):
    out, manifest, hashes = staged
    # Leftovers from an earlier run are present but not in the manifest.
    (out / "src" / "stale_previous_run.sv").write_text("module stale; endmodule\n")
    (out / "constraints" / "stale.xdc").write_text("# stale\n")

    env = _integration(out).prepare_build_environment(BOARD)
    board_dir = out / BOARD

    assert env["board_name"] == BOARD
    assert env["output_dir"] == board_dir
    assert env["board_config"]["fpga_part"]
    # No stock templates or stock build scripts are staged any more.
    assert env["templates"] == {}
    assert env["build_scripts"] == {}
    assert env["stage2_manifest"]["board"] == BOARD

    assert sorted(p.name for p in env["src_files"]) == sorted(manifest["sources"])
    assert all(p.parent == board_dir / "src" for p in env["src_files"])
    assert [p.name for p in env["xdc_files"]] == manifest["constraints"]
    assert all(p.parent == board_dir / "constraints" for p in env["xdc_files"])
    ip_names = sorted(p.name for p in env["ip_files"])
    assert ip_names == sorted(manifest["ip"]["xci"] + manifest["ip"]["memories"])

    assert not (board_dir / "src" / "stale_previous_run.sv").exists()
    assert not (board_dir / "constraints" / "stale.xdc").exists()
    for name, digest in hashes.items():
        assert sha256_file(board_dir / "ip" / name) == digest
    assert MARKER in (board_dir / "src" / CONTROLLER).read_text()


@needs_board
def test_copy_xdc_files_stages_only_manifest_constraints(staged):
    out, manifest, _ = staged
    (out / "constraints" / "extra.xdc").write_text("# not recorded\n")
    dest = out / "elsewhere" / "constraints"

    result = _integration(out)._copy_xdc_files(BOARD, dest)

    assert result == [dest / n for n in manifest["constraints"]]
    for p in result:
        assert p.read_bytes() == (out / "constraints" / p.name).read_bytes()
    assert not (dest / "extra.xdc").exists()


@needs_board
def test_copy_source_files_stages_generated_set_flat(staged):
    """Sources land flat in <board>/src (no src/src nesting, issue #524)."""
    out, manifest, _ = staged
    board_dir = out / BOARD

    result = _integration(out)._copy_source_files(BOARD, board_dir)

    assert sorted(p.name for p in result) == sorted(manifest["sources"])
    for p in result:
        assert p.parent == board_dir / "src"
        assert "src/src" not in p.as_posix()
        assert p.read_bytes() == (out / "src" / p.name).read_bytes()
    # The generated controller, not the stock one, is what gets staged.
    assert MARKER in (board_dir / "src" / CONTROLLER).read_text()


@needs_board
def test_copy_source_files_rejects_incomplete_source_set(staged):
    """An empty or header-less source set is a generation failure."""
    out, manifest, _ = staged
    manifest["sources"] = []
    (out / STAGE2_MANIFEST_NAME).write_text(json.dumps(manifest))

    with pytest.raises(BuildArtifactError, match="pcileech_header.svh"):
        _integration(out)._copy_source_files(BOARD, out / BOARD)
    assert not (out / BOARD / "src").exists()


@needs_board
def test_copy_source_files_collision_is_fatal(staged):
    """A second copy onto an already-staged destination aborts staging."""
    out, _, _ = staged
    integ = _integration(out)
    integ.file_manifest = FileManifestTracker(logger=pbi.logger)

    integ._copy_source_files(BOARD, out / BOARD)
    with pytest.raises(BuildArtifactError, match="map to"):
        integ._copy_source_files(BOARD, out / BOARD)


@needs_board
def test_copy_source_files_io_error_is_fatal(staged, monkeypatch):
    """One unreadable source aborts staging instead of building a subset."""
    out, manifest, _ = staged
    victim = sorted(manifest["sources"])[1]
    real_copy2 = shutil.copy2

    def _copy2(src, dst, *a, **k):
        if Path(src).name == victim:
            raise PermissionError("Permission denied")
        return real_copy2(src, dst, *a, **k)

    monkeypatch.setattr(pbi.shutil, "copy2", _copy2)
    with pytest.raises(BuildArtifactError, match="Permission denied"):
        _integration(out)._copy_source_files(BOARD, out / BOARD)


@needs_board
def test_prepare_build_environment_fail_fast_no_ip(staged):
    """The no-IP gate still aborts even with a valid manifest."""
    out, _, _ = staged
    integ = _integration(out)
    integ._copy_ip_files = MagicMock(return_value=[])

    with pytest.raises(SystemExit) as exc:
        integ.prepare_build_environment(BOARD)
    assert exc.value.code == 2
    integ._copy_ip_files.assert_called_once()


@needs_board
def test_prepare_build_environment_without_manifest_does_not_copy(
    tmp_path, monkeypatch
):
    _forbid_stock_lookup(monkeypatch)
    with pytest.raises(BuildArtifactError, match="manifest not found"):
        _integration(tmp_path).prepare_build_environment(BOARD)
    assert not (tmp_path / BOARD).exists()


@needs_board
def test_create_unified_build_script(staged):
    out, manifest, _ = staged
    integ = _integration(out)
    fpga_part = integ.get_available_boards()[BOARD]["fpga_part"]

    script = integ.create_unified_build_script(BOARD)

    board_dir = out / BOARD
    assert script == board_dir / "build_all.tcl"
    text = script.read_text()
    assert f"PCILeech Unified Build Script for {BOARD}" in text
    assert f"FPGA Part: {fpga_part}" in text
    assert "create_project $PROJECT_NAME $PROJECT_DIR -part $FPGA_PART -force" in text
    # No stock project script is sourced.
    assert "vivado_generate_project" not in text

    # Donor override is staged and sourced unconditionally, after IP import
    # and before the pre-synthesis generate_target.
    assert (board_dir / DONOR_IP_OVERRIDE_NAME).read_bytes() == (
        out / DONOR_IP_OVERRIDE_NAME
    ).read_bytes()
    src_line = f'source "{DONOR_IP_OVERRIDE_NAME}"'
    assert text.index("update_ip_catalog -rebuild") < text.index(src_line)
    assert text.index(src_line) < text.index("generate_target all [get_ips *]")

    # Build quality gates run on the implemented design, before export.
    quality = BUILD_QUALITY_TCL.strip().splitlines()[0]
    assert text.index("wait_on_run impl_1") < text.index(quality)
    assert text.index(quality) < text.index("# Copy bitstream to output directory")

    # build_inputs.json records exactly the staged inputs plus the script.
    inputs = json.loads((board_dir / "build_inputs.json").read_text())
    assert inputs["board"] == BOARD and inputs["part"] == fpga_part
    recorded = {a["path"] for a in inputs["artifacts"]}
    expected = (
        {f"src/{n}" for n in manifest["sources"]}
        | {f"ip/{n}" for n in manifest["ip"]["xci"] + manifest["ip"]["memories"]}
        | {f"constraints/{n}" for n in manifest["constraints"]}
        | {DONOR_IP_OVERRIDE_NAME, "build_all.tcl"}
    )
    assert recorded == expected
    verify_build_manifest(script)


@needs_board
def test_build_inputs_detect_post_generation_edits(staged):
    out, _, _ = staged
    script = _integration(out).create_unified_build_script(BOARD)
    (out / BOARD / "ip" / CFGSPACE_COE).write_text("; tampered\n")
    with pytest.raises(ValueError, match="pcileech_cfgspace.coe"):
        verify_build_manifest(script)


@needs_board
def test_unified_build_script_adds_each_source_once_with_relative_paths(staged):
    """Regression for #524: no duplicates, no src/src, no absolute paths."""
    out, manifest, _ = staged
    script = _integration(out).create_unified_build_script(BOARD)
    text = script.read_text()

    for name in manifest["sources"]:
        assert text.count(f'add_files -norecurse "src/{name}"') == 1
    assert text.count('add_files -norecurse "src/') == len(manifest["sources"])
    assert 'add_files -norecurse "src/src/' not in text
    for name in manifest["constraints"]:
        assert f'add_files -fileset constrs_1 -norecurse "constraints/{name}"' in text
    assert str(out) not in text
    assert str(out.resolve()) not in text
    assert "/root/" not in text


@needs_board
def test_unified_build_script_sets_working_directory(staged):
    out, _, _ = staged
    text = _integration(out).create_unified_build_script(BOARD).read_text()
    assert "cd [file dirname [info script]]" in text
    assert 'puts "Working directory: [pwd]"' in text
    assert text.index("cd [file dirname [info script]]") < text.index("add_files")


@needs_board
def test_unified_build_script_imports_explicit_relative_ip_list(staged):
    """IP is imported from an explicit relative list, not a directory glob."""
    out, manifest, _ = staged
    (out / "ip" / "stale_ip.xci").write_bytes(
        (out / "ip" / "pcie_7x_0.xci").read_bytes()
    )
    text = _integration(out).create_unified_build_script(BOARD).read_text()

    for name in manifest["ip"]["xci"]:
        assert f'[file normalize "ip/{name}"]' in text
    assert "stale_ip" not in text
    assert "glob -nocomplain" not in text
    assert f"./{BOARD}/ip" not in text
    assert 'error "No staged IP definitions to import"' in text
    assert (
        'error "Failed to import IP $ip_name without modifying staged inputs: $import_err"'
        in text
    )
    assert "read_ip $ip_file" not in text
    assert "set generated_memory_names [list " in text
    # Memories travel with their XCI via import_files; the generated copies
    # are what sits next to it.
    assert WRITEMASK_COE in manifest["ip"]["memories"]


@needs_board
def test_postroute_checkpoint_is_qualified_before_bitstream(staged):
    out, _, _ = staged
    script = _integration(out).create_unified_build_script(BOARD).read_text()
    enable = script.index(
        "set_property STEPS.POST_ROUTE_PHYS_OPT_DESIGN.IS_ENABLED true"
    )
    optimize = script.index("-to_step {phys_opt_design (Post-Route)}")
    quality = script.index("open_run impl_1")
    publish = script.index("-to_step write_bitstream")
    assert enable < optimize < quality < publish
    assert "phys_opt_design (Post-Route) Complete" in script
    assert "STEPS.POST_ROUTE_PHYS_OPT_DESIGN.ARGS.DIRECTIVE Explore" in script
