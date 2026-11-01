import json
import os
from types import SimpleNamespace

import pytest

from pcileechfwgenerator.exceptions import ConfigurationError
from tests.snapshot_helpers import make_snapshot
from tests.snapshot_helpers import offline_fixture

from pcileechfwgenerator.build import (
    BuildConfiguration,
    ConfigurationManager,
    FirmwareBuilder,
    MSIXManager,
)


def _make_args(tmp_path, profile=0):
    return SimpleNamespace(
        bdf='0000:03:00.0',
        board='pcileech_100t484_x1',
        output=str(tmp_path),
        profile=profile,
        preload_msix=True,
        output_template=None,
        donor_template=None,
        vivado_path=None,
        vivado_jobs=4,
        vivado_timeout=3600,
        enable_error_injection=False,
    )


class TestDisableVFIOConfig:
    def test_env_flags_drive_disable_vfio(self, monkeypatch, tmp_path):
        cm = ConfigurationManager()

        # baseline: no env -> disabled False
        args = _make_args(tmp_path, profile=10)
        cfg = cm.create_from_args(args)
        assert cfg.disable_vfio is False
        assert cfg.enable_profiling is True
        assert cfg.profile_duration == 10

        # explicit disable via PCILEECH_DISABLE_VFIO
        monkeypatch.setenv('PCILEECH_DISABLE_VFIO', '1')
        args = _make_args(tmp_path, profile=10)
        cfg = cm.create_from_args(args)
        assert cfg.disable_vfio is True
        assert cfg.enable_profiling is False
        assert cfg.profile_duration == 0
        monkeypatch.delenv('PCILEECH_DISABLE_VFIO', raising=False)

        # host-context-only flag
        monkeypatch.setenv('PCILEECH_HOST_CONTEXT_ONLY', 'true')
        args = _make_args(tmp_path, profile=10)
        cfg = cm.create_from_args(args)
        assert cfg.disable_vfio is True
        assert cfg.enable_profiling is False
        assert cfg.profile_duration == 0
        monkeypatch.delenv('PCILEECH_HOST_CONTEXT_ONLY', raising=False)

        # presence of DEVICE_CONTEXT_PATH also implies disable
        monkeypatch.setenv(
            'DEVICE_CONTEXT_PATH', str(tmp_path / 'device_context.json')
        )
        args = _make_args(tmp_path, profile=10)
        cfg = cm.create_from_args(args)
        assert cfg.disable_vfio is True
        assert cfg.enable_profiling is False
        assert cfg.profile_duration == 0
        monkeypatch.delenv('DEVICE_CONTEXT_PATH', raising=False)

    def test_init_components_fail_without_host_data(self, monkeypatch, tmp_path):
        # Force disable and point to missing context file
        monkeypatch.setenv('PCILEECH_DISABLE_VFIO', '1')
        monkeypatch.setenv('DEVICE_CONTEXT_PATH', str(tmp_path / 'missing.json'))

        cfg = BuildConfiguration(
            bdf='0000:03:00.0',
            board='pcileech_100t484_x1',
            output_dir=tmp_path,
            enable_profiling=False,
            preload_msix=True,
            profile_duration=0,
            parallel_writes=True,
            max_workers=2,
            output_template=None,
            donor_template=None,
            vivado_path=None,
            vivado_jobs=1,
            vivado_timeout=60,
            enable_error_injection=False,
            disable_vfio=True,
        )

        # Constructor invokes _init_components which should fail fast
        with pytest.raises(Exception):
            FirmwareBuilder(cfg)


class TestPreloadedConfigSpace:

    def test_legacy_nested_context_is_rejected(self, monkeypatch, tmp_path):
        # Prepare minimal device_context.json with nested
        # template_context.config_space_hex
        hex_str = '00' * 128  # 128 bytes
        ctx_path = tmp_path / 'device_context.json'
        ctx_path.write_text(
            json.dumps({'template_context': {'config_space_hex': hex_str}})
        )
        monkeypatch.setenv('DEVICE_CONTEXT_PATH', str(ctx_path))

        # Build a FirmwareBuilder with VFIO allowed (so it doesn't fail early)
        cfg = BuildConfiguration(
            bdf='0000:03:00.0',
            board='pcileech_100t484_x1',
            output_dir=tmp_path,
            enable_profiling=False,
            preload_msix=False,
            profile_duration=0,
            parallel_writes=True,
            max_workers=1,
            output_template=None,
            donor_template=None,
            vivado_path=None,
            vivado_jobs=1,
            vivado_timeout=60,
            enable_error_injection=False,
            disable_vfio=False,
        )
        with pytest.raises(ConfigurationError, match="legacy"):
            FirmwareBuilder(cfg)


class TestMSIXManagerDisable:
    def test_msix_preload_reads_file_only_when_disabled(self, monkeypatch, tmp_path):
        # Disable VFIO
        monkeypatch.setenv('PCILEECH_DISABLE_VFIO', '1')
        # Create msix_data.json with msix_info
        msix_info = {
            'table_size': 8,
            'table_bir': 0,
            'table_offset': 0x1000,
            'pba_bir': 0,
            'pba_offset': 0x1080,
            'enabled': True,
            'function_mask': 0,
        }
        msix_path = tmp_path / 'msix_data.json'
        make_snapshot(tmp_path, msix_info=msix_info)
        monkeypatch.setenv('MSIX_DATA_PATH', str(msix_path))
        m = MSIXManager(bdf='0000:03:00.0')

        # Ensure sysfs path isn't accidentally used by making
        # _read_config_space fail if called
        def boom(_):
            raise AssertionError(
                '_read_config_space should not be called in disable_vfio mode'
            )

        m._read_config_space = boom  # type: ignore
        data = m.preload_data()
        assert data.preloaded is True
        assert data.msix_info is not None
        assert data.msix_info.get('table_size') == 8

    def test_msix_preload_returns_empty_when_disabled_and_no_file(self, monkeypatch):
        monkeypatch.setenv('PCILEECH_DISABLE_VFIO', '1')
        monkeypatch.delenv('MSIX_DATA_PATH', raising=False)
        m = MSIXManager(bdf='0000:03:00.0')
        data = m.preload_data()
        assert data.preloaded is False


class TestProfilingSkip:
    def test_generate_profile_skips_when_disabled(self, monkeypatch, tmp_path):
        cfg = BuildConfiguration(
            bdf="0000:03:00.0",
            board="pcileech_100t484_x1",
            output_dir=tmp_path / "artifacts",
            enable_profiling=False,
            preload_msix=False,
            profile_duration=10,
            parallel_writes=True,
            max_workers=1,
            output_template=None,
            donor_template=None,
            vivado_path=None,
            vivado_jobs=1,
            vivado_timeout=60,
            enable_error_injection=False,
            disable_vfio=True,
        )
        # Create a builder; _init_components will fail if it can't find host data.
        # Avoid that by providing a minimal device_context.json with config_space_hex
        ctx = offline_fixture(tmp_path / "capture")
        monkeypatch.setenv('DEVICE_CONTEXT_PATH', str(ctx))
        # The env-var capture path models the Stage 2 container.
        import pcileechfwgenerator.build as build

        monkeypatch.setattr(build, "_in_container", lambda: True)

        fb = FirmwareBuilder(cfg)

        # Ensure capture is not called
        called = {'hit': False}

        def fake_capture(**_):
            called['hit'] = True
            raise AssertionError('should not be called')

        fb.profiler.capture_behavior_profile = fake_capture  # type: ignore
        # The disable guard also wins if a caller later flips this flag.
        cfg.enable_profiling = True
        fb._generate_profile()
        assert called['hit'] is False


class TestGeneratorConfig:
    def test_generator_strict_vfio_false_when_disabled(self, monkeypatch, tmp_path):
        ctx = offline_fixture(tmp_path / "capture")
        monkeypatch.setenv('DEVICE_CONTEXT_PATH', str(ctx))
        # The env-var capture path models the Stage 2 container.
        import pcileechfwgenerator.build as build

        monkeypatch.setattr(build, "_in_container", lambda: True)

        cfg = BuildConfiguration(
            bdf="0000:03:00.0",
            board="pcileech_100t484_x1",
            output_dir=tmp_path / "artifacts",
            enable_profiling=False,
            preload_msix=False,
            profile_duration=0,
            parallel_writes=True,
            max_workers=1,
            output_template=None,
            donor_template=None,
            vivado_path=None,
            vivado_jobs=1,
            vivado_timeout=60,
            enable_error_injection=False,
            disable_vfio=True,
        )
        fb = FirmwareBuilder(cfg)
        assert hasattr(fb.gen.config, 'strict_vfio')
        assert getattr(fb.gen.config, 'strict_vfio') is False


def _legacy_capture(tmp_path, monkeypatch, *, in_container, bar_size=64 << 20):
    """A container-style host capture exposed only through the legacy env vars."""
    import pcileechfwgenerator.build as build

    capture = tmp_path / "capture"
    offline_fixture(capture, size=bar_size)
    monkeypatch.setenv("DEVICE_CONTEXT_PATH", str(capture / "device_context.json"))
    monkeypatch.setenv("MSIX_DATA_PATH", str(capture / "msix_data.json"))
    monkeypatch.setattr(build, "_in_container", lambda: in_container)
    return capture


def _cfg(tmp_path, **overrides):
    values = dict(
        bdf="0000:03:00.0",
        board="pcileech_75t484_x1",
        output_dir=tmp_path / "out",
        enable_profiling=False,
        disable_vfio=True,
    )
    values.update(overrides)
    return BuildConfiguration(**values)


def test_container_path_renders_from_capture_without_offline_safeguards(
    tmp_path, monkeypatch
):
    _legacy_capture(tmp_path, monkeypatch, in_container=True, bar_size=4096)
    fb = FirmwareBuilder(_cfg(tmp_path))
    assert fb.config.offline_input is False
    assert fb.config.offline_risk_accepted is False  # no prompt for verified input
    assert fb.offline_capture is not None
    assert fb.capture_mode == "container"


def test_container_path_large_bar_points_to_local_mode(tmp_path, monkeypatch):
    _legacy_capture(tmp_path, monkeypatch, in_container=True)  # 64 MiB BAR
    with pytest.raises(ConfigurationError, match="--container-mode local"):
        FirmwareBuilder(_cfg(tmp_path))


def test_legacy_container_path_still_validates_capture(tmp_path, monkeypatch):
    capture = _legacy_capture(tmp_path, monkeypatch, in_container=True)
    (capture / "msix_data.json").unlink()
    with pytest.raises(ConfigurationError):
        FirmwareBuilder(_cfg(tmp_path))


def test_legacy_env_path_refused_outside_container(tmp_path, monkeypatch):
    _legacy_capture(tmp_path, monkeypatch, in_container=False)
    with pytest.raises(ConfigurationError, match="--from-capture"):
        FirmwareBuilder(_cfg(tmp_path))


def test_explicit_from_capture_sets_offline_input_and_limits(tmp_path):
    capture = tmp_path / "capture"
    offline_fixture(capture, size=64 << 20)
    with pytest.raises(ConfigurationError, match="16 MiB"):
        FirmwareBuilder(
            _cfg(tmp_path, from_capture=capture, offline_risk_accepted=True)
        )


def test_from_capture_always_marks_offline_input(tmp_path):
    capture = tmp_path / "capture"
    offline_fixture(capture)
    fb = FirmwareBuilder(
        _cfg(tmp_path, from_capture=capture, offline_risk_accepted=True)
    )
    assert fb.config.offline_input is True
    assert fb.offline_capture is not None


def test_create_from_args_sets_offline_input_only_for_from_capture(tmp_path):
    from pcileechfwgenerator.build import parse_args

    capture = tmp_path / "capture"
    offline_fixture(capture)
    out = str(tmp_path / "out")
    offline = ConfigurationManager().create_from_args(
        parse_args(
            [
                "--from-capture",
                str(capture),
                "--accept-offline-risk",
                "--board",
                "pcileech_75t484_x1",
                "--output",
                out,
            ]
        )
    )
    live = ConfigurationManager().create_from_args(
        parse_args(
            [
                "--bdf",
                "0000:03:00.0",
                "--board",
                "pcileech_75t484_x1",
                "--output",
                out,
                "--profile",
                "0",
            ]
        )
    )
    assert offline.offline_input is True
    assert live.offline_input is False
