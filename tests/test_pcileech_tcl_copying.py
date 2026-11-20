#!/usr/bin/env python3
"""Board file staging in PCILeechGenerator._copy_tcl_scripts.

The step copies only the board IP definitions. The board's own
``vivado_*.tcl`` project scripts are not staged: the unified
``build_all.tcl`` builds the Stage 2 artifact set, and a staged board script
could not run against it. The components key keeps its historical
``tcl_scripts`` name.
"""

from pathlib import Path
from unittest import mock

import pytest
from pcileechfwgenerator.device_clone.pcileech_generator import (
    PCILeechGenerationConfig,
    PCILeechGenerationError,
    PCILeechGenerator,
)
from pcileechfwgenerator.file_management.file_manager import FileManager

FM = "pcileechfwgenerator.file_management.file_manager.FileManager"


@pytest.fixture
def generator(tmp_path: Path) -> PCILeechGenerator:
    cfg = PCILeechGenerationConfig(
        device_bdf="0000:00:00.0",
        enable_behavior_profiling=False,
        strict_validation=False,
        output_dir=tmp_path / "out",
    )
    return PCILeechGenerator(cfg)


def _fm(mock_fm_cls, ips=()):
    mock_fm = mock.MagicMock(spec=FileManager)
    mock_fm_cls.return_value = mock_fm
    mock_fm.copy_ip_files.return_value = [Path(p) for p in ips]
    return mock_fm


def test_board_project_scripts_are_not_a_file_manager_operation():
    assert not hasattr(FileManager, "copy_vivado_tcl_scripts")


def test_copies_ip_files_only(generator: PCILeechGenerator, tmp_path: Path):
    context = {"board_name": "pcileech_100t484_x1", "output_dir": str(tmp_path / "out")}
    with mock.patch(FM) as mock_fm_cls:
        mock_fm = _fm(mock_fm_cls, ["/out/ip/pcie_7x_0.xci"])
        result = generator._copy_tcl_scripts(context)

    mock_fm.copy_ip_files.assert_called_once_with(board="pcileech_100t484_x1")
    assert result == {"pcie_7x_0.xci": "/out/ip/pcie_7x_0.xci"}


def test_uses_board_fallback(generator: PCILeechGenerator, tmp_path: Path):
    context = {"board": "ac701_ft601", "output_dir": str(tmp_path / "out")}
    with mock.patch(FM) as mock_fm_cls:
        mock_fm = _fm(mock_fm_cls)
        generator._copy_tcl_scripts(context)
    mock_fm.copy_ip_files.assert_called_once_with(board="ac701_ft601")


def test_missing_board_raises(generator: PCILeechGenerator, tmp_path: Path):
    with pytest.raises(PCILeechGenerationError, match="board name not specified"):
        generator._copy_tcl_scripts({"output_dir": str(tmp_path / "out")})


def test_file_manager_error_is_wrapped(generator: PCILeechGenerator, tmp_path: Path):
    context = {"board_name": "pcileech_100t484_x1", "output_dir": str(tmp_path / "out")}
    with mock.patch(FM) as mock_fm_cls:
        _fm(mock_fm_cls).copy_ip_files.side_effect = RuntimeError(
            "Submodule not initialized"
        )
        with pytest.raises(
            PCILeechGenerationError,
            match=r"Failed to copy IP files.*Submodule not initialized",
        ):
            generator._copy_tcl_scripts(context)


def test_uses_output_dir_from_context(generator: PCILeechGenerator, tmp_path: Path):
    custom = tmp_path / "custom_out"
    with mock.patch(FM) as mock_fm_cls:
        _fm(mock_fm_cls)
        generator._copy_tcl_scripts(
            {"board_name": "pcileech_100t484_x1", "output_dir": str(custom)}
        )
    mock_fm_cls.assert_called_once_with(output_dir=custom)


def test_falls_back_to_config_output_dir(generator: PCILeechGenerator):
    with mock.patch(FM) as mock_fm_cls:
        _fm(mock_fm_cls)
        generator._copy_tcl_scripts({"board_name": "pcileech_100t484_x1"})
    called = mock_fm_cls.call_args[1]["output_dir"]
    assert str(called) == str(generator.config.output_dir)


def test_real_board_stages_no_project_scripts(tmp_path: Path):
    from pcileechfwgenerator.file_management.repo_manager import RepoManager

    try:
        RepoManager.ensure_repo()
    except Exception:  # pragma: no cover - submodule missing
        pytest.skip("needs voltcyclone-fpga")
    out = tmp_path / "out"
    fm = FileManager(output_dir=out)
    fm.create_pcileech_structure()
    fm.copy_ip_files(board="pcileech_100t484_x1")
    assert not list(out.rglob("vivado_*.tcl"))
