"""Unverified offline builds need explicit, visible risk acceptance."""

import io
import logging
from unittest.mock import Mock

import pytest
from pcileechfwgenerator.exceptions import ConfigurationError
from pcileechfwgenerator.host_collect.offline_notice import (
    ACCEPT_FLAG,
    OFFLINE_BUILD_WARNING,
    confirm_offline_build,
)

import pcileech
from tests.snapshot_helpers import offline_fixture


class _Tty(io.StringIO):
    def isatty(self):
        return True


def test_non_interactive_without_flag_is_refused():
    err = io.StringIO()
    with pytest.raises(ConfigurationError, match=ACCEPT_FLAG):
        confirm_offline_build(False, stdin=io.StringIO(), stderr=err)
    assert "UNVERIFIED OFFLINE FIRMWARE BUILD" in err.getvalue()


def test_flag_accepts_and_still_warns():
    err = io.StringIO()
    confirm_offline_build(True, stdin=io.StringIO(), stderr=err)
    assert OFFLINE_BUILD_WARNING in err.getvalue()
    assert "risk accepted" in err.getvalue()


def test_forwarded_acceptance_skips_repeat_banner():
    err = io.StringIO()
    confirm_offline_build(True, warn=False, stdin=io.StringIO(), stderr=err)
    assert "UNVERIFIED" not in err.getvalue()
    assert "risk accepted" in err.getvalue()


@pytest.mark.parametrize("answer", ["y\n", "YES\n", " y \n"])
def test_terminal_yes_accepts(answer):
    confirm_offline_build(False, stdin=_Tty(answer), stderr=io.StringIO())


@pytest.mark.parametrize("answer", ["\n", "n\n", "no\n", "yy\n", ""])
def test_terminal_anything_else_declines(answer):
    err = io.StringIO()
    with pytest.raises(ConfigurationError, match="cancelled"):
        confirm_offline_build(False, stdin=_Tty(answer), stderr=err)
    assert "[y/N]" in err.getvalue()


def _offline_args(tmp_path, *extra):
    offline_fixture(tmp_path / "capture")
    return pcileech.create_parser().parse_args(
        [
            "build",
            "--from-capture",
            str(tmp_path / "capture"),
            "--board",
            "pcileech_75t484_x1",
            "--templates-only",
            "--local",
            "--output",
            str(tmp_path / "out"),
            *extra,
        ]
    )


def test_cli_offline_build_refused_without_acceptance(tmp_path, monkeypatch, capsys):
    render = Mock(return_value=0)
    monkeypatch.setattr(pcileech, "run_local_templating", render)
    assert pcileech.handle_build(_offline_args(tmp_path)) == 1
    render.assert_not_called()
    assert "UNVERIFIED OFFLINE FIRMWARE BUILD" in capsys.readouterr().err


def test_cli_offline_build_prompt_yes_proceeds(tmp_path, monkeypatch):
    render = Mock(return_value=0)
    monkeypatch.setattr(pcileech, "run_local_templating", render)
    monkeypatch.setattr("sys.stdin", _Tty("y\n"))
    assert pcileech.handle_build(_offline_args(tmp_path)) == 0
    assert render.call_args.args[0].accept_offline_risk is True


def test_cli_offline_build_flag_proceeds(tmp_path, monkeypatch):
    render = Mock(return_value=0)
    monkeypatch.setattr(pcileech, "run_local_templating", render)
    assert pcileech.handle_build(_offline_args(tmp_path, ACCEPT_FLAG)) == 0
    render.assert_called_once()


@pytest.mark.parametrize("accepted", [True, False])
def test_container_stage_gets_acceptance_and_machine_id(
    tmp_path, monkeypatch, accepted
):
    from pcileechfwgenerator.build import parse_args

    args = _offline_args(tmp_path)
    args.container_mode, args.local, args.bdf = "container", False, "0000:03:00.0"
    args.accept_offline_risk = accepted
    run = Mock(return_value=Mock(returncode=1))
    monkeypatch.setattr(pcileech, "_ensure_container_image", lambda *a, **k: True)
    monkeypatch.setattr(pcileech.subprocess, "run", run)
    pcileech._run_in_container(
        args, "podman", tmp_path / "capture", tmp_path / "out", logging.getLogger()
    )
    command = run.call_args.args[0]
    assert "/etc/machine-id:/etc/machine-id:ro" in command
    parsed = parse_args(command[command.index("src.build") + 1 :])
    assert parsed.accept_offline_risk is accepted


def test_library_offline_config_requires_acceptance(tmp_path):
    from pcileechfwgenerator.build import BuildConfiguration, FirmwareBuilder

    offline_fixture(tmp_path / "capture")
    config = BuildConfiguration(
        bdf="0000:03:00.0",
        board="pcileech_75t484_x1",
        output_dir=tmp_path / "out",
        enable_profiling=False,
        from_capture=tmp_path / "capture",
    )
    with pytest.raises(ConfigurationError, match="accepting the unverified-build risk"):
        FirmwareBuilder(config)
