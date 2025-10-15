#!/usr/bin/env python3
import types
from pathlib import Path
from types import SimpleNamespace
from typing import Any, cast

import pytest

import src.cli.container as container
from src.cli.container import (
    BuildConfig,
    _build_podman_command,
    build_image,
    check_podman_available,
    image_exists,
    prompt_user_for_local_build,
    require_podman,
    run_build,
    run_local_build,
)
from src.exceptions import BuildError, ConfigurationError


class DummyShell:
    def __init__(self, output="", should_raise: Exception | None = None):
        self._output = output
        self._raise = should_raise

    def run(self, *parts: str, timeout: int = 30, cwd: str | None = None) -> str:
        if self._raise:
            raise self._raise
        return self._output


def test_resolve_image_parts_static():
    cfg = BuildConfig(bdf="0000:03:00.0", board="pcileech_35t325_x4")
    img, tag = cfg.resolve_image_parts()
    assert (img, tag) == (cfg.container_image, cfg.container_tag)


def test_resolve_image_parts_dynamic(monkeypatch):
    cfg = BuildConfig(
        bdf="0000:03:00.0",
        board="pcileech_35t325_x4",
        dynamic_image=True,
        advanced_sv=True,
        enable_variance=True,
    )
    monkeypatch.setattr(BuildConfig, "_get_project_version", lambda self: "1.2.3")
    img, tag = cfg.resolve_image_parts()
    assert img == cfg.container_image
    assert tag == "1.2.3-adv-var"


def test_resolve_image_parts_tag_truncation(monkeypatch):
    long_ver = "a" * 200
    cfg = BuildConfig(
        bdf="0000:03:00.0", board="pcileech_35t325_x4", dynamic_image=True
    )
    monkeypatch.setattr(BuildConfig, "_get_project_version", lambda self: long_ver)
    _, tag = cfg.resolve_image_parts()
    assert len(tag) == 128


def test_check_podman_available_absent(monkeypatch):
    monkeypatch.setattr(container.shutil, "which", lambda _: None)
    assert check_podman_available() is False


def test_check_podman_available_present_ok(monkeypatch):
    monkeypatch.setattr(container.shutil, "which", lambda _: "/usr/bin/podman")
    monkeypatch.setattr(
        container, "Shell", lambda: DummyShell(output="podman version 4")
    )
    assert check_podman_available() is True


def test_check_podman_available_present_but_fails(monkeypatch):
    monkeypatch.setattr(container.shutil, "which", lambda _: "/usr/bin/podman")
    monkeypatch.setattr(
        container,
        "Shell",
        lambda: DummyShell(should_raise=RuntimeError("Cannot connect to Podman")),
    )
    assert check_podman_available() is False


def test_require_podman_not_found(monkeypatch):
    monkeypatch.setattr(container.shutil, "which", lambda _: None)
    with pytest.raises(ConfigurationError):
        require_podman()


def test_image_exists_true(monkeypatch):
    monkeypatch.setattr(
        container,
        "Shell",
        lambda: DummyShell(output="pcileechfwgenerator:latest\nother:tag"),
    )
    assert image_exists("pcileechfwgenerator:latest") is True


def test_image_exists_connection_refused(monkeypatch):
    monkeypatch.setattr(
        container,
        "Shell",
        lambda: DummyShell(should_raise=RuntimeError("Cannot connect to Podman")),
    )
    assert image_exists("pcileechfwgenerator:latest") is False


def test_image_exists_other_error_bubbles(monkeypatch):
    monkeypatch.setattr(
        container,
        "Shell",
        lambda: DummyShell(should_raise=RuntimeError("weird error")),
    )
    with pytest.raises(RuntimeError):
        image_exists("pcileechfwgenerator:latest")


def test_build_image_validation_and_success(monkeypatch):
    # Invalid name
    with pytest.raises(ConfigurationError):
        build_image("UpperCase", "latest")
    # Invalid tag
    with pytest.raises(ConfigurationError):
        build_image("valid-name", "bad tag")
    # Valid invocation should call subprocess.run
    called = {}

    def fake_run(cmd, check):
        called["cmd"] = cmd
        called["check"] = check
        return 0

    monkeypatch.setattr(container, "subprocess", SimpleNamespace(run=fake_run))
    build_image("valid-name", "tag-1")
    assert called["cmd"][0:2] == ["podman", "build"]
    assert "valid-name:tag-1" in called["cmd"]
    assert called["check"] is True


def test_build_podman_command_mounts(monkeypatch, tmp_path: Path):
    cfg = BuildConfig(bdf="0000:03:00.0", board="pcileech_35t325_x4")
    # Force kernel headers and debugfs to appear as present
    real_exists = Path.exists

    def fake_exists(p: Path):
        p_str = str(p)
        if p_str.startswith("/lib/modules/") and p_str.endswith("/build"):
            return True
        if p_str == "/sys/kernel/debug":
            return True
        return real_exists(p)

    monkeypatch.setattr(container, "Path", container.Path)
    monkeypatch.setattr(container.Path, "exists", fake_exists, raising=False)
    cmd = _build_podman_command(cfg, "/dev/vfio/12", tmp_path)
    assert "-v" in cmd
    mounts = [cmd[i + 1] for i, v in enumerate(cmd) if v == "-v"]
    assert any(":/kernel-headers" in m for m in mounts)
    assert any(m.startswith("/sys/kernel/debug:/sys/kernel/debug") for m in mounts)


def test_prompt_user_for_local_build_non_interactive(monkeypatch):
    monkeypatch.setenv("CI", "1")
    assert prompt_user_for_local_build() is False


def test_prompt_user_for_local_build_yes(monkeypatch):
    monkeypatch.delenv("CI", raising=False)
    inputs = iter(["y"])  # immediate yes
    monkeypatch.setattr("builtins.input", lambda _: next(inputs))
    assert prompt_user_for_local_build() is True


def test_prompt_user_for_local_build_no(monkeypatch):
    monkeypatch.delenv("CI", raising=False)
    inputs = iter(["n"])  # immediate no
    monkeypatch.setattr("builtins.input", lambda _: next(inputs))
    assert prompt_user_for_local_build() is False


def test_run_local_build_success(monkeypatch, tmp_path: Path):
    # Ensure we don't litter the repo root
    monkeypatch.chdir(tmp_path)

    # Provide a fake build module with main returning 0
    fake_build = cast(Any, types.ModuleType("src.build"))

    def fake_main(args):
        # Expect bdf/board flags present
        assert "--bdf" in args and "--board" in args
        return 0

    fake_build.main = fake_main
    # Make relative import from src.cli.container work
    import sys

    sys.modules["src.build"] = fake_build

    cfg = BuildConfig(bdf="0000:03:00.0", board="pcileech_35t325_x4")
    run_local_build(cfg)
    assert (tmp_path / "output").exists()


def test_run_local_build_failure(monkeypatch, tmp_path: Path):
    monkeypatch.chdir(tmp_path)
    fake_build = cast(Any, types.ModuleType("src.build"))
    fake_build.main = lambda args: 5  # non-zero exit
    import sys

    sys.modules["src.build"] = fake_build
    cfg = BuildConfig(bdf="0000:03:00.0", board="pcileech_35t325_x4")
    with pytest.raises(BuildError):
        run_local_build(cfg)


def test_run_build_non_interactive_no_podman(monkeypatch):
    monkeypatch.setenv("CI", "1")
    monkeypatch.setattr(container, "check_podman_available", lambda: False)
    with pytest.raises(SystemExit) as se:
        run_build(BuildConfig(bdf="0000:03:00.0", board="pcileech_35t325_x4"))
    assert se.value.code == 2


def test_run_build_interactive_local_yes(monkeypatch):
    calls = {"local": 0}
    monkeypatch.delenv("CI", raising=False)
    monkeypatch.setattr(container, "check_podman_available", lambda: False)
    monkeypatch.setattr(container, "prompt_user_for_local_build", lambda: True)

    def fake_local(cfg):
        calls["local"] += 1

    monkeypatch.setattr(container, "run_local_build", fake_local)
    run_build(BuildConfig(bdf="0000:03:00.0", board="pcileech_35t325_x4"))
    assert calls["local"] == 1


def test_run_build_happy_path_with_podman(monkeypatch, tmp_path: Path):
    # Chdir to tmp to avoid creating output in repo
    monkeypatch.chdir(tmp_path)

    # Podman path
    monkeypatch.setattr(container, "check_podman_available", lambda: True)
    monkeypatch.setattr(container, "require_podman", lambda: None)
    monkeypatch.setattr(container, "image_exists", lambda _: True)

    # Fake MSIXManager
    fake_build = cast(Any, types.ModuleType("src.build"))

    class FakeMSIXManager:
        def __init__(self, bdf, logger=None):
            pass

        def preload_data(self):
            return SimpleNamespace(
                preloaded=True, msix_info={"n": 1}, config_space_hex="00"
            )

    fake_build.MSIXManager = FakeMSIXManager
    import sys

    sys.modules["src.build"] = fake_build

    # Stub VFIOBinder context manager

    class FakeBinder:
        def __init__(self, bdf, attach=False):
            pass

        def __enter__(self):
            return self

        def __exit__(self, exc_type, exc, tb):
            return False

    monkeypatch.setattr(container, "VFIOBinder", FakeBinder)

    # iommu group
    monkeypatch.setattr("src.cli.container._get_iommu_group", lambda bdf: 12)

    # Prevent long or real command; ensure it's invoked
    ran = {"called": 0}

    def fake_run(cmd, check):
        ran["called"] += 1
        return 0

    monkeypatch.setattr(
        container, "_build_podman_command", lambda cfg, grp, out: ["echo", "ok"]
    )
    monkeypatch.setattr(container, "subprocess", SimpleNamespace(run=fake_run))

    run_build(BuildConfig(bdf="0000:03:00.0", board="pcileech_35t325_x4"))
    assert ran["called"] == 1
