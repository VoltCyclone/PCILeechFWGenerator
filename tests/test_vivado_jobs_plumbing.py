"""The selected Vivado job count reaches the generated launch_runs Tcl."""

import logging
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock

import pytest
from pcileechfwgenerator import build, vivado_handling
from pcileechfwgenerator.vivado_handling import pcileech_build_integration as pbi
from pcileechfwgenerator.vivado_handling.vivado_runner import (
    VivadoIntegrationError,
    VivadoRunner,
)

import pcileech
from tests.test_pcileech_build_integration import (  # noqa: F401  (fixture)
    BOARD,
    _integration,
    needs_board,
    staged,
)


def test_run_jobs_tcl_fixes_selected_count():
    assert pbi.run_jobs_tcl(4) == "set RUN_JOBS 4"
    assert pbi.run_jobs_tcl(1) == "set RUN_JOBS 1"


def test_run_jobs_tcl_default_is_unchanged():
    tcl = pbi.run_jobs_tcl(None)
    assert tcl.startswith("set RUN_JOBS 8\n")
    assert "::env(VIVADO_RUN_JOBS)" in tcl


@pytest.mark.parametrize("bad", [0, -1, True, "4", 2.0])
def test_job_count_must_be_positive_integer(bad):
    with pytest.raises(ValueError, match="positive integer"):
        pbi.run_jobs_tcl(bad)
    with pytest.raises(ValueError, match="positive integer"):
        VivadoRunner("board", Path("out"), "/opt/Vivado", vivado_jobs=bad)


@needs_board
def test_unified_script_uses_selected_jobs(staged):
    out, _, _ = staged
    text = (
        _integration(out).create_unified_build_script(BOARD, vivado_jobs=4).read_text()
    )
    assert "set RUN_JOBS 4\n" in text
    assert "set RUN_JOBS 8" not in text
    assert "VIVADO_RUN_JOBS" not in text
    assert text.count("-jobs $RUN_JOBS") == 3


@needs_board
def test_unified_script_default_jobs_compatible(staged):
    out, _, _ = staged
    text = _integration(out).create_unified_build_script(BOARD).read_text()
    assert "set RUN_JOBS 8\n" in text
    assert "::env(VIVADO_RUN_JOBS)" in text


def test_integrate_forwards_jobs(tmp_path, monkeypatch):
    created = Mock()
    created.return_value.create_unified_build_script.return_value = tmp_path / "x"
    monkeypatch.setattr(pbi, "PCILeechBuildIntegration", created)
    pbi.integrate_pcileech_build("board", tmp_path, vivado_jobs=4)
    created.return_value.create_unified_build_script.assert_called_once_with(
        "board", None, vivado_jobs=4
    )


def _capture_integration(monkeypatch):
    calls = []

    def integrate(board, output, **kwargs):
        calls.append(kwargs)
        raise RuntimeError("stop after capture")

    monkeypatch.setattr(pbi, "integrate_pcileech_build", integrate)
    return calls


def test_runner_local_build_passes_jobs(tmp_path, monkeypatch):
    calls = _capture_integration(monkeypatch)
    runner = VivadoRunner("board", tmp_path, "/opt/Vivado", vivado_jobs=4)
    monkeypatch.setattr(runner, "_prepare_ip_artifacts", lambda: None)
    monkeypatch.setattr(runner, "_is_running_in_container", lambda: False)
    with pytest.raises(VivadoIntegrationError):
        runner.run()
    assert calls[0]["vivado_jobs"] == 4


def test_runner_host_handoff_passes_jobs(tmp_path, monkeypatch):
    calls = _capture_integration(monkeypatch)
    runner = VivadoRunner("board", tmp_path, "/opt/Vivado", vivado_jobs=4)
    with pytest.raises(VivadoIntegrationError):
        runner._run_vivado_on_host()
    assert calls[0]["vivado_jobs"] == 4


def test_runner_default_leaves_jobs_unselected(tmp_path, monkeypatch):
    calls = _capture_integration(monkeypatch)
    with pytest.raises(VivadoIntegrationError):
        VivadoRunner("board", tmp_path, "/opt/Vivado")._run_vivado_on_host()
    assert calls[0]["vivado_jobs"] is None


def _cli_args(*extra):
    return pcileech.create_parser().parse_args(
        ["build", "--bdf", "0000:03:00.0", "--board", "pcileech_75t484_x1", *extra]
    )


def test_cli_build_passes_selected_jobs_to_runner(tmp_path, monkeypatch):
    args = _cli_args("--vivado-jobs", "4", "--output", str(tmp_path))
    args.vivado_path = str(tmp_path / "Vivado")
    runner = Mock()
    monkeypatch.setattr(vivado_handling, "VivadoRunner", runner)
    assert pcileech.run_host_vivado(args) == 0
    assert runner.call_args.kwargs["vivado_jobs"] == 4


def test_cli_build_default_jobs_is_four():
    assert _cli_args().vivado_jobs == 4


@pytest.mark.parametrize("bad", ["0", "-2", "many"])
def test_cli_rejects_non_positive_jobs(bad):
    with pytest.raises(SystemExit):
        _cli_args("--vivado-jobs", bad)


def test_firmware_builder_passes_config_jobs(tmp_path, monkeypatch):
    install = tmp_path / "Vivado"
    (install / "bin").mkdir(parents=True)
    (install / "bin" / "vivado").write_text("")
    runner = Mock()
    monkeypatch.setattr(vivado_handling, "VivadoRunner", runner)
    builder = SimpleNamespace(
        config=build.BuildConfiguration(
            bdf="0000:03:00.0",
            board="board",
            output_dir=tmp_path,
            vivado_path=str(install),
            vivado_jobs=4,
        ),
        logger=logging.getLogger(__name__),
        _device_config=None,
    )
    build.FirmwareBuilder.run_vivado(builder)
    assert runner.call_args.kwargs["vivado_jobs"] == 4
    runner.return_value.run.assert_called_once()
