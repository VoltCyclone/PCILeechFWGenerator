"""Real parser and stage-boundary checks; no VFIO, container or Vivado needed."""

import logging
import subprocess
from unittest.mock import Mock

import pytest
from pcileechfwgenerator import build

import pcileech


def parsed(*extra):
    return pcileech.create_parser().parse_args(
        ["build", "--bdf", "0000:03:00.0", "--board", "pcileech_75t484_x1", *extra]
    )


@pytest.mark.parametrize("runtime", ["podman", "docker"])
def test_container_command_is_accepted_by_actual_stage_two_parser(
    tmp_path, monkeypatch, runtime
):
    capture = tmp_path / "capture files"
    output = tmp_path / "artifact files"
    donor = tmp_path / "donor template.json"
    donor.write_text("{}")
    export = tmp_path / "template exports" / "export.json"
    args = parsed(
        "--container-mode",
        "container",
        "--datastore",
        str(capture),
        "--output",
        str(output),
        "--donor-template",
        str(donor),
        "--generate-donor-template",
        str(export),
        "--no-mmio-learning",
        "--force-recapture",
        "--enable-error-injection",
    )
    monkeypatch.setattr(pcileech, "_ensure_container_image", lambda *a, **kw: True)
    run = Mock(return_value=subprocess.CompletedProcess([], 0))
    monkeypatch.setattr(pcileech.subprocess, "run", run)
    assert (
        pcileech._run_in_container(args, runtime, capture, output, logging.getLogger())
        == 0
    )
    command = run.call_args.args[0]
    stage_two = build.parse_args(command[command.index("src.build") + 1 :])
    assert stage_two.output == "/artifacts"
    assert stage_two.profile == 0
    assert stage_two.donor_template == "/donor/template.json"
    assert stage_two.output_template == "/template-output/export.json"
    assert stage_two.no_mmio_learning and stage_two.force_recapture
    assert stage_two.enable_error_injection
    assert f"{output}:/artifacts" in command
    assert f"{donor}:/donor/template.json:ro" in command
    assert f"{export.parent}:/template-output" in command
    assert not any(
        arg.startswith("--device") or arg == "--privileged" for arg in command
    )


def test_selected_output_profile_and_mmio_options_reach_local_builder_and_vivado(
    tmp_path, monkeypatch
):
    from pcileechfwgenerator import vivado_handling

    output = tmp_path / "selected output"
    donor = tmp_path / "template.json"
    donor.write_text("{}")
    args = parsed(
        "--local",
        "--datastore",
        str(tmp_path / "capture"),
        "--output",
        str(output),
        "--profile",
        "45.5",
        "--donor-template",
        str(donor),
        "--no-mmio-learning",
        "--force-recapture",
    )
    monkeypatch.setattr(
        build,
        "make_vfio_decision",
        lambda *a, **kw: type("Decision", (), {"enabled": True})(),
    )
    monkeypatch.delenv("PCILEECH_PRODUCTION_DEFAULTS", raising=False)
    builder = Mock()
    builder.return_value.build.return_value = []
    monkeypatch.setattr(build, "FirmwareBuilder", builder)
    assert pcileech.run_local_templating(args) == 0
    cfg = builder.call_args.args[0]
    assert cfg.output_dir == output
    assert cfg.profile_duration == 45.5 and cfg.enable_profiling
    assert cfg.donor_template == str(donor)
    assert not cfg.enable_mmio_learning and cfg.force_recapture
    runner = Mock()
    monkeypatch.setattr(vivado_handling, "VivadoRunner", runner)
    args.vivado_path = str(tmp_path / "Vivado")
    assert pcileech.run_host_vivado(args) == 0
    assert runner.call_args.kwargs["output_dir"] == output
    runner.return_value.run.assert_called_once()


def test_local_profile_request_is_not_silently_disabled_by_environment(
    tmp_path, monkeypatch
):
    args = parsed("--local", "--output", str(tmp_path), "--profile", "45.5")
    monkeypatch.setattr(
        build,
        "make_vfio_decision",
        lambda *a, **kw: type("Decision", (), {"enabled": False})(),
    )
    builder = Mock()
    monkeypatch.setattr(build, "FirmwareBuilder", builder)
    with pytest.raises(ValueError, match="Effective profiling differs"):
        pcileech.run_local_templating(args)
    builder.assert_not_called()


@pytest.mark.parametrize("flag", ["--output", "--build-dir"])
def test_output_alias_and_default(tmp_path, flag):
    args = parsed(flag, str(tmp_path / "artifacts"))
    assert pcileech.build_output_directory(args) == tmp_path / "artifacts"
    assert (
        pcileech.build_output_directory(parsed("--datastore", str(tmp_path)))
        == tmp_path / "output"
    )


@pytest.mark.parametrize(
    "extra",
    [
        ["--advanced-sv"],
        ["--enable-variance"],
        ["--device-type", "network"],
        ["--container-mode", "container", "--profile", "1"],
    ],
)
def test_unsupported_cli_requests_fail_before_collection(monkeypatch, extra):
    collect = Mock()
    monkeypatch.setattr(pcileech, "run_host_collect", collect)
    assert pcileech.handle_build(parsed(*extra)) == 1
    collect.assert_not_called()


def test_profile_duration_reaches_generator_and_profile_artifact(tmp_path, monkeypatch):
    from pcileechfwgenerator.device_clone import behavior_profiler, pcileech_generator

    generator = Mock()
    profiler = Mock()
    monkeypatch.setattr(pcileech_generator, "PCILeechGenerator", generator)
    monkeypatch.setattr(behavior_profiler, "BehaviorProfiler", profiler)
    builder = object.__new__(build.FirmwareBuilder)
    builder.config = build.BuildConfiguration(
        bdf="0000:03:00.0",
        board="pcileech_75t484_x1",
        output_dir=tmp_path,
        profile_duration=45.5,
        enable_profiling=True,
    )
    builder.logger = logging.getLogger()
    builder.file_manager = Mock()
    builder._load_preloaded_config_space = Mock(return_value=None)
    builder._init_components()
    assert generator.call_args.args[0].behavior_capture_duration == 45.5
    builder._generate_profile()
    profiler.return_value.capture_behavior_profile.assert_called_once_with(
        duration=45.5
    )
    builder.file_manager.write_json.assert_called_once_with(
        "behavior_profile.json",
        profiler.return_value.capture_behavior_profile.return_value,
    )


@pytest.mark.parametrize("duration", ["nan", "inf", "-inf"])
def test_stage_two_rejects_nonfinite_duration_before_vfio(duration, monkeypatch):
    decision = Mock()
    monkeypatch.setattr(build, "make_vfio_decision", decision)
    args = build.parse_args(
        [
            "--bdf",
            "0000:03:00.0",
            "--board",
            "pcileech_75t484_x1",
            "--profile=" + duration,
        ]
    )
    with pytest.raises(build.ConfigurationError, match="finite"):
        build.ConfigurationManager().create_from_args(args)
    decision.assert_not_called()
