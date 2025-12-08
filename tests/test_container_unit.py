#!/usr/bin/env python3
"""
Unit tests for src/cli/container.py

NOTE: run_build() is DEPRECATED. These tests verify the deprecation warning
and existing functionality for backwards compatibility only.

New code should use pcileech.py's unified 3-stage flow instead.
See tests/test_unified_flow_orchestration.py for the preferred approach.
"""
import types
import warnings
import os
import pytest
from pathlib import Path
from types import SimpleNamespace
from typing import Any, cast

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
    monkeypatch.delenv("NO_INTERACTIVE", raising=False)
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


def test_run_build_shows_deprecation_warning(monkeypatch):
    """Verify run_build() emits a deprecation warning."""
    cfg = BuildConfig(
        bdf="0000:03:00.0",
        board="pcileech_35t325_x1",
    )
    
    # Mock to prevent actual execution
    monkeypatch.setattr(container, "check_podman_available", lambda: False)
    monkeypatch.setattr(os, "environ", {"CI": "1"})  # Non-interactive
    
    with warnings.catch_warnings(record=True) as w:
        warnings.simplefilter("always")  # Capture all warnings
        
        try:
            container.run_build(cfg)
        except SystemExit:
            pass  # Expected
        
        # Verify deprecation warning was issued
        assert len(w) >= 1
        dep_warnings = [
            warning for warning in w
            if issubclass(warning.category, DeprecationWarning)
        ]
        assert any(
            "run_build() is deprecated" in str(warning.message)
            for warning in dep_warnings
        )


def test_run_build_non_interactive_no_podman(monkeypatch):
    monkeypatch.setenv("CI", "1")
    monkeypatch.setattr(container, "check_podman_available", lambda: False)
    with pytest.raises(SystemExit) as se:
        run_build(BuildConfig(bdf="0000:03:00.0", board="pcileech_35t325_x4"))
    assert se.value.code == 2


def test_run_build_interactive_local_yes(monkeypatch):
    calls = {"local": 0}
    monkeypatch.delenv("CI", raising=False)
    monkeypatch.delenv("NO_INTERACTIVE", raising=False)
    monkeypatch.setattr(container, "check_podman_available", lambda: False)
    monkeypatch.setattr(container, "prompt_user_for_local_build", lambda: True)

    def fake_local(cfg):
        calls["local"] += 1

    monkeypatch.setattr(container, "run_local_build", fake_local)
    run_build(BuildConfig(bdf="0000:03:00.0", board="pcileech_35t325_x4"))
    assert calls["local"] == 1


def test_run_build_restores_driver_on_success(monkeypatch, tmp_path: Path):
    """Test original driver restored after successful container build."""
    calls = {"get_driver": 0, "restore": 0}

    monkeypatch.chdir(tmp_path)
    monkeypatch.setattr(container, "check_podman_available", lambda: True)
    monkeypatch.setattr(container, "require_podman", lambda: None)
    monkeypatch.setattr(container, "image_exists", lambda _: True)

    # Mock get_current_driver to return nvme

    def fake_get_current_driver(bdf):
        calls["get_driver"] += 1
        return "nvme"

    monkeypatch.setattr(container, "get_current_driver", fake_get_current_driver)

    # Mock restore_driver

    def fake_restore_driver(bdf, driver):
        calls["restore"] += 1
        assert bdf == "0000:03:00.0"
        assert driver == "nvme"

    monkeypatch.setattr(container, "restore_driver", fake_restore_driver)

    # Mock HostDeviceCollector at the import site (within container module)
    # to avoid triggering the problematic MSIXManager import


    class FakeHostDeviceCollector:
        def __init__(self, bdf, logger):
            pass

        def collect_device_context(self, out_dir):
            return {"device": "data"}

    # Mock the import by patching sys.modules before container tries to import it
    import sys
    fake_module = types.ModuleType('host_device_collector')
    fake_module.HostDeviceCollector = FakeHostDeviceCollector
    monkeypatch.setitem(sys.modules, 'src.cli.host_device_collector', fake_module)

    # Mock _get_iommu_group
    monkeypatch.setattr(container, "_get_iommu_group", lambda bdf: 12)

    # Mock Path.exists for preflight checks - skip VFIO checks

    def fake_path_exists(path_str):
        # Make /dev/vfio/vfio and group exist to skip preflight checks
        if "/dev/vfio" in str(path_str):
            return True
        return False

    monkeypatch.setattr("pathlib.Path.exists", fake_path_exists)

    # Mock subprocess.run for podman

    def fake_subprocess_run(cmd, **kwargs):
        assert "podman" in cmd[0]
        return SimpleNamespace(returncode=0, stdout="", stderr="")
    
    monkeypatch.setattr(container.subprocess, "run", fake_subprocess_run)
    
    # Run build
    cfg = BuildConfig(bdf="0000:03:00.0", board="pcileech_35t325_x4")
    run_build(cfg)
    
    # Verify driver was queried and restored
    assert calls["get_driver"] == 1
    assert calls["restore"] == 1


def test_run_build_restores_driver_on_failure(monkeypatch, tmp_path: Path):
    """Test that original driver is restored even when container build fails."""
    calls = {"get_driver": 0, "restore": 0}
    
    monkeypatch.chdir(tmp_path)
    monkeypatch.setattr(container, "check_podman_available", lambda: True)
    monkeypatch.setattr(container, "require_podman", lambda: None)
    monkeypatch.setattr(container, "image_exists", lambda _: False)

    # Mock get_current_driver

    def fake_get_current_driver(bdf):
        calls["get_driver"] += 1
        return "nvme"

    monkeypatch.setattr(container, "get_current_driver", fake_get_current_driver)

    # Mock restore_driver

    def fake_restore_driver(bdf, driver):
        calls["restore"] += 1
        assert driver == "nvme"

    monkeypatch.setattr(container, "restore_driver", fake_restore_driver)

    # Mock HostDeviceCollector to fail at the import site (within container module)
    # to avoid triggering the problematic MSIXManager import


    class FakeFailingCollector:
        def __init__(self, bdf, logger):
            raise BuildError("Collection failed")

    # Mock the import by patching sys.modules before container tries to import it
    import sys
    fake_module = types.ModuleType('host_device_collector')
    fake_module.HostDeviceCollector = FakeFailingCollector
    monkeypatch.setitem(sys.modules, 'src.cli.host_device_collector', fake_module)

    # Mock build_image to prevent it from being called

    def fake_build_image(image, tag):
        pass  # Should not be reached due to HostDeviceCollector failure

    monkeypatch.setattr(container, "build_image", fake_build_image)

    # Run build and expect it to fail
    cfg = BuildConfig(bdf="0000:03:00.0", board="pcileech_35t325_x4")
    with pytest.raises(BuildError):
        run_build(cfg)
    
    # Verify driver was still restored in finally block
    assert calls["get_driver"] == 1
    assert calls["restore"] == 1


def test_run_build_skips_restore_when_no_original_driver(
    monkeypatch, tmp_path: Path
):
    """Test that restore is skipped when device has no original driver."""
    calls = {"restore": 0}

    # Mock functions
    monkeypatch.setattr(container, "require_podman", lambda: None)
    monkeypatch.setattr(container, "image_exists", lambda _: True)
    monkeypatch.setattr(container, "check_podman_available", lambda: True)

    # Mock get_current_driver to return None (no driver)
    monkeypatch.setattr(container, "get_current_driver", lambda bdf: None)

    # Mock restore_driver - should never be called

    def fake_restore_driver(bdf, driver):
        calls["restore"] += 1

    monkeypatch.setattr(container, "restore_driver", fake_restore_driver)

    # Mock HostDeviceCollector at the import site (within container module)
    # to avoid triggering the problematic MSIXManager import


    class FakeHostDeviceCollector:
        def __init__(self, bdf, logger):
            pass

        def collect_device_context(self, out_dir):
            return {"device": "data"}

    # Mock the import by patching sys.modules before container tries to import it
    import sys
    fake_module = types.ModuleType('host_device_collector')
    fake_module.HostDeviceCollector = FakeHostDeviceCollector
    monkeypatch.setitem(sys.modules, 'src.cli.host_device_collector', fake_module)

    # Mock _get_iommu_group
    monkeypatch.setattr(container, "_get_iommu_group", lambda bdf: 12)

    # Mock Path.exists for preflight checks - only /dev/vfio/* paths exist
    def fake_path_exists(path_obj):
        p = str(path_obj)
        if "/dev/vfio" in p:
            return True
        return False
    monkeypatch.setattr("pathlib.Path.exists", fake_path_exists)

    # Mock subprocess.run for podman execution
    def fake_subprocess_run(cmd, **kwargs):
        # Support both shell string and argv list invocations.
        if isinstance(cmd, (list, tuple)):
            full = " ".join(str(p) for p in cmd)
        else:
            full = str(cmd)
        assert "podman" in full
        return types.SimpleNamespace(returncode=0, stdout="", stderr="")
    monkeypatch.setattr(container.subprocess, "run", fake_subprocess_run)

    # Run build (should succeed and not attempt restore since no original driver)
    cfg = BuildConfig(bdf="0000:03:00.0", board="pcileech_35t325_x4")
    run_build(cfg)
    assert calls["restore"] == 0

