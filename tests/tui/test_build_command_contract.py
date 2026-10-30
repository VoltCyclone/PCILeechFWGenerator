"""Exercise TUI commands against real parsers without touching donor hardware."""

import subprocess
import sys
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest
from pcileechfwgenerator.build import parse_args as parse_stage_two
from pcileechfwgenerator.tui.core.build_orchestrator import BuildOrchestrator
from pcileechfwgenerator.tui.models.configuration import BuildConfiguration

import pcileech


@pytest.mark.asyncio
async def test_default_command_is_accepted_by_its_actual_parser():
    orchestrator = BuildOrchestrator()
    orchestrator._run_shell = AsyncMock(return_value=subprocess.CompletedProcess([], 0))
    config = BuildConfiguration(board_type="pcileech_75t484_x1", local_build=True)
    await orchestrator._run_vivado_synthesis(
        SimpleNamespace(bdf="0000:03:00.0"), config
    )
    command = orchestrator._run_shell.call_args.args[0]
    if command[1:3] == ["-m", "pcileechfwgenerator.pcileech_main"]:
        args = pcileech.create_parser().parse_args(command[3:])
        assert args.command == "build"
    else:
        # The previous TUI invokes the stage-two script directly. Its actual
        # parser rejects all four trailing flags in the default command.
        parse_stage_two(command[2:])
    assert command[0] == sys.executable


@pytest.mark.asyncio
async def test_unsupported_board_check_override_stops_before_launch():
    orchestrator = BuildOrchestrator()
    orchestrator._run_shell = AsyncMock(return_value=subprocess.CompletedProcess([], 0))
    config = BuildConfiguration(
        board_type="pcileech_75t484_x1", local_build=True, skip_board_check=True
    )
    with pytest.raises(ValueError, match="skip_board_check"):
        await orchestrator._run_vivado_synthesis(
            SimpleNamespace(bdf="0000:03:00.0"), config
        )
    orchestrator._run_shell.assert_not_called()


@pytest.mark.parametrize("local", [False, True])
def test_real_parser_receives_mode_paths_and_logging(tmp_path, local):
    from pcileechfwgenerator.tui.core.build_contract import build_command

    donor = tmp_path / "donor settings.json"
    donor.write_text("{}")
    config = BuildConfiguration(
        board_type="pcileech_75t484_x1",
        local_build=local,
        output_directory=str(tmp_path / "output files"),
        datastore_directory=str(tmp_path / "saved capture"),
        donor_info_file=str(donor),
        debug_mode=True,
    )
    before = config.to_dict()
    argv = build_command("0000:03:00.0", config)
    parsed = pcileech.create_parser().parse_args(argv[3:])
    assert argv[:3] == [sys.executable, "-m", "pcileechfwgenerator.pcileech_main"]
    assert parsed.container_mode == ("local" if local else "container")
    assert parsed.output == config.output_directory
    assert parsed.datastore == config.datastore_directory
    assert parsed.donor_template == str(donor)
    assert parsed.verbose is True
    assert parsed.profile == 0
    assert config.to_dict() == before


def test_profile_and_quiet_reach_real_parser():
    from pcileechfwgenerator.tui.core.build_contract import build_command

    config = BuildConfiguration(
        board_type="pcileech_75t484_x1",
        local_build=True,
        behavior_profiling=True,
        profile_duration=45.5,
        enable_logging=False,
    )
    parsed = pcileech.create_parser().parse_args(
        build_command("0000:03:00.0", config)[3:]
    )
    assert parsed.profile == 45.5
    assert parsed.quiet is True
    assert (
        parse_stage_two(
            [
                "--bdf",
                parsed.bdf,
                "--board",
                parsed.board,
                "--profile",
                str(parsed.profile),
            ]
        ).profile
        == 45.5
    )


@pytest.mark.parametrize(
    "field,value",
    [
        ("device_type", "network"),
        ("optimization_level", "performance"),
        ("advanced_sv", True),
        ("enable_variance", True),
        ("enable_performance_counters", True),
        ("performance_counters", True),
        ("enable_error_counters", False),
        ("power_management", False),
        ("error_handling", False),
        ("flash_after_build", True),
        ("donor_dump", True),
        ("auto_install_headers", True),
        ("skip_board_check", True),
        ("disable_ftrace", True),
        ("custom_parameters", {"option": 1}),
        ("feature_flags", {"option": True}),
        ("compatibility_overrides", ["option"]),
    ],
)
@pytest.mark.asyncio
async def test_every_unsupported_override_stops_before_side_effects(field, value):
    orchestrator = BuildOrchestrator()
    orchestrator._run_shell = AsyncMock()
    config = BuildConfiguration(board_type="pcileech_75t484_x1", **{field: value})
    with pytest.raises(ValueError, match=field):
        await orchestrator.start_build(SimpleNamespace(bdf="0000:03:00.0"), config)
    orchestrator._run_shell.assert_not_called()
    assert not orchestrator.is_building()


@pytest.mark.parametrize(
    "settings,match",
    [
        ({"behavior_profiling": True}, "requires local_build"),
        ({"debug_mode": True, "enable_logging": False}, "enable_logging"),
        ({"donor_info_file": "/nonexistent/donor.json"}, "existing donor"),
        ({"board_type": "default"}, "Select a target board"),
    ],
)
def test_invalid_combinations_are_rejected(settings, match):
    from pcileechfwgenerator.tui.core.build_contract import build_command

    config = BuildConfiguration(**{"board_type": "pcileech_75t484_x1", **settings})
    with pytest.raises(ValueError, match=match):
        build_command("0000:03:00.0", config)


@pytest.mark.parametrize("duration", [float("nan"), float("inf"), -1, 3601])
def test_profile_duration_is_finite_and_bounded(duration):
    with pytest.raises(ValueError):
        BuildConfiguration(profile_duration=duration)
    with pytest.raises(SystemExit):
        pcileech.create_parser().parse_args(
            [
                "build",
                "--bdf",
                "0000:03:00.0",
                "--board",
                "pcileech_75t484_x1",
                "--profile",
                str(duration),
            ]
        )


@pytest.mark.asyncio
async def test_command_build_manager_delegates_and_preserves_existing_artifacts(
    tmp_path,
):
    from unittest.mock import MagicMock

    from pcileechfwgenerator.tui.core.build_manager import BuildManager

    output = tmp_path / "chosen output"
    output.mkdir()
    existing = output / "keep.txt"
    existing.write_text("keep")
    created = output / "new.bit"
    app = MagicMock()
    app.current_config = BuildConfiguration(board_type="pcileech_75t484_x1")

    async def build(device, config, callback):
        assert config.output_directory == str(output)
        assert config is not app.current_config
        created.write_bytes(b"test artifact")
        return True

    app.build_orchestrator.start_build = AsyncMock(side_effect=build)
    device = SimpleNamespace(bdf="0000:03:00.0", is_suitable=True)
    manager = BuildManager(app)
    success, artifacts = await manager.build_firmware(device, str(output))
    assert success
    assert artifacts == {"new.bit": str(created)}
    assert app.current_config.output_directory is None
    assert await manager.clean_build_artifacts(artifacts)
    assert existing.read_text() == "keep"
    assert not created.exists()


@pytest.mark.asyncio
@pytest.mark.parametrize("result", [False, RuntimeError("backend failed")])
async def test_command_build_manager_propagates_cancel_and_failure(tmp_path, result):
    from unittest.mock import MagicMock

    from pcileechfwgenerator.tui.core.build_manager import BuildManager

    app = MagicMock()
    app.current_config = BuildConfiguration(board_type="pcileech_75t484_x1")
    app.build_orchestrator.start_build = AsyncMock(
        side_effect=result if isinstance(result, Exception) else None,
        return_value=result,
    )
    success, error = await BuildManager(app).build_firmware(
        SimpleNamespace(bdf="0000:03:00.0", is_suitable=True), str(tmp_path)
    )
    assert not success
    assert error == (
        "Build failed: backend failed"
        if isinstance(result, Exception)
        else "Build cancelled"
    )


def test_fresh_builtin_profiles_are_supported_and_old_profiles_are_preserved(
    tmp_path, monkeypatch
):
    import json

    from pcileechfwgenerator.tui.core import config_manager
    from pcileechfwgenerator.tui.core.build_contract import validate_build_configuration

    monkeypatch.setattr(config_manager, "CACHE_DIR", tmp_path)
    monkeypatch.setattr(
        config_manager.ConfigManager, "_migrate_old_profiles", lambda self: None
    )
    manager = config_manager.ConfigManager()
    assert manager.create_default_profiles()
    files = list(manager.config_dir.glob("*.json"))
    assert len(files) == 4
    for path in files:
        data = json.loads(path.read_text())
        validate_build_configuration(
            BuildConfiguration.from_dict(data.get("config", data))
        )
    legacy = files[0]
    legacy.write_text('{"donor_dump": true}')
    assert manager.create_default_profiles()
    assert legacy.read_text() == '{"donor_dump": true}'


@pytest.mark.parametrize("with_output", [True, False])
def test_build_command_datastore_outside_output(tmp_path, monkeypatch, with_output):
    from pathlib import Path
    from pcileechfwgenerator.tui.core.build_contract import build_command

    monkeypatch.chdir(tmp_path)
    output = tmp_path / "out"
    config = BuildConfiguration(
        board_type="pcileech_75t484_x1",
        output_directory=str(output) if with_output else None,
    )
    argv = build_command("0000:03:00.0", config)
    ds = Path(argv[argv.index("--datastore") + 1])
    assert ds == tmp_path / "pcileech_datastore"
    assert output not in ds.parents and ds != output


@pytest.mark.parametrize("suffix", ["", "capture"])
def test_datastore_inside_output_is_rejected(tmp_path, suffix):
    from pcileechfwgenerator.tui.core.build_contract import build_command

    config = BuildConfiguration(
        board_type="pcileech_75t484_x1",
        output_directory=str(tmp_path),
        datastore_directory=str(tmp_path / suffix),
    )
    with pytest.raises(ValueError, match="datastore"):
        build_command("0000:03:00.0", config)


@pytest.mark.asyncio
async def test_undo_never_deletes_capture_files(tmp_path, monkeypatch):
    import threading
    from pathlib import Path
    from unittest.mock import MagicMock
    from pcileechfwgenerator.tui.core.build_manager import BuildManager

    output = tmp_path / "out"
    capture = output / "capture"
    capture.mkdir(parents=True)
    app = MagicMock()
    app.current_config = BuildConfiguration(
        board_type="pcileech_75t484_x1", datastore_directory=str(capture)
    )
    manager = BuildManager(app)
    manager.validate_configuration = AsyncMock(return_value=(True, []))
    main_thread = threading.get_ident()
    rglob = Path.rglob
    threads = []

    def tracked_rglob(path, pattern):
        threads.append(threading.get_ident())
        return rglob(path, pattern)

    monkeypatch.setattr(Path, "rglob", tracked_rglob)

    async def build(*args):
        (capture / "device_context.json").write_text("{}")
        (output / "new.bit").write_text("artifact")
        return True

    app.build_orchestrator.start_build = AsyncMock(side_effect=build)
    success, artifacts = await manager.build_firmware(
        SimpleNamespace(is_suitable=True), str(output)
    )
    assert success
    assert artifacts == {"new.bit": str(output / "new.bit")}
    assert len(threads) == 2 and all(t != main_thread for t in threads)
    assert await manager.clean_build_artifacts(artifacts)
    assert (capture / "device_context.json").exists()
