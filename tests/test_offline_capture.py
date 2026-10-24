"""Offline fixtures are synthetic test data, never collected production inputs."""

import json

import pytest
from pcileechfwgenerator.host_collect.snapshot import SnapshotError

import pcileech
from tests.snapshot_helpers import make_snapshot, offline_fixture


def test_explicit_offline_build_parser(tmp_path):
    args = pcileech.create_parser().parse_args(
        [
            "build",
            "--from-capture",
            str(tmp_path),
            "--board",
            "pcileech_75t484_x1",
            "--templates-only",
            "--local",
        ]
    )
    assert args.from_capture == str(tmp_path)
    assert args.bdf is None


def test_inconsistent_bar_length_rejected(tmp_path):
    from pcileechfwgenerator.host_collect.offline import load_offline_capture

    offline_fixture(tmp_path, size=4095)
    with pytest.raises(SnapshotError, match="BAR"):
        load_offline_capture(tmp_path)


def test_inconsistent_msix_metadata_rejected(tmp_path):
    from pcileechfwgenerator.host_collect.offline import load_offline_capture

    offline_fixture(tmp_path, msix=True)
    path = tmp_path / "msix_data.json"
    data = json.loads(path.read_text())
    data["msix_info"]["table_size"] = 5
    path.write_text(json.dumps(data))
    with pytest.raises(SnapshotError, match="MSI-X"):
        load_offline_capture(tmp_path)


@pytest.mark.parametrize("legacy", [None, "context", "msix"])
def test_offline_render_without_any_hardware_access(tmp_path, monkeypatch, legacy):
    import builtins
    import io
    import os
    import subprocess

    from pcileechfwgenerator.build import (
        ConfigurationManager,
        FirmwareBuilder,
        parse_args,
    )

    source = tmp_path / "capture"
    offline_fixture(source, size=16384, msix=True)
    inputs = {p.name: p.read_bytes() for p in source.iterdir()}
    output = tmp_path / "rendered"
    accesses = []

    def guarded(original):
        def wrapped(path, *args, **kwargs):
            value = (
                os.fspath(path) if isinstance(path, (str, bytes, os.PathLike)) else ""
            )
            if isinstance(value, bytes):
                value = value.decode()
            if value.startswith(("/sys/", "/dev/vfio", "/proc/", "/sys")):
                accesses.append(value)
                raise AssertionError("offline path touched hardware")
            return original(path, *args, **kwargs)

        return wrapped

    for module, name in (
        (builtins, "open"),
        (io, "open"),
        (os, "open"),
        (os, "stat"),
        (os, "lstat"),
        (os, "listdir"),
    ):
        monkeypatch.setattr(module, name, guarded(getattr(module, name)))
    original_process = subprocess.Popen

    def forbidden_process(*args, **kwargs):
        # setuptools-scm reads local Git metadata for the package version.
        if args and isinstance(args[0], (list, tuple)) and args[0][0] == "git":
            return original_process(*args, **kwargs)
        accesses.append(str(args))
        raise AssertionError("offline renderer launched a subprocess")

    monkeypatch.setattr(subprocess, "Popen", forbidden_process)
    source_args = ["--from-capture", str(source)]
    if legacy:
        key, name = (
            ("DEVICE_CONTEXT_PATH", "device_context.json")
            if legacy == "context"
            else ("MSIX_DATA_PATH", "msix_data.json")
        )
        monkeypatch.setenv(key, str(source / name))
        source_args = ["--host-context-only", "--profile", "0", "--bdf", "0000:03:00.0"]
    args = parse_args(
        [*source_args, "--board", "pcileech_75t484_x1", "--output", str(output)]
    )
    config = ConfigurationManager().create_from_args(args)
    artifacts = FirmwareBuilder(config).build()
    assert artifacts
    assert any(path.suffix == ".coe" for path in output.rglob("*"))
    provenance = json.loads((output / "device_info.json").read_text())["input_capture"]
    assert (
        provenance["capture_id"]
        == json.loads(inputs["device_context.json"])["capture"]["capture_id"]
    )
    assert provenance["bar_content_source"] == "derived, not sampled"
    assert provenance["msix_table_entries_captured"] is False
    assert not accesses
    assert {p.name: p.read_bytes() for p in source.iterdir()} == inputs


def rewrite_config(directory, mutate):
    """Re-sign fixture bytes so structural checks, rather than SHA mismatch, run."""
    path = directory / "device_context.json"
    context = json.loads(path.read_text())
    config = bytearray.fromhex(context["config_space_hex"])
    mutate(config)
    sidecar = json.loads((directory / "msix_data.json").read_text())
    return make_snapshot(
        directory,
        bytes(config),
        context={
            "offline_resources": context["offline_resources"],
        },
        msix_info=sidecar["msix_info"],
    )


@pytest.mark.parametrize("size", [-1, True, "4096", 3, 4095, 1 << 33])
def test_invalid_bar_lengths_fail_before_output(tmp_path, size):
    from pcileechfwgenerator.host_collect.offline import import_capture

    source, target = tmp_path / "source", tmp_path / "imported"
    offline_fixture(source, size=size)
    with pytest.raises(SnapshotError):
        import_capture(source, target)
    assert not target.exists()


@pytest.mark.parametrize("length", [64, 128, 255, 257, 1024])
def test_nonstandard_config_lengths_rejected(tmp_path, length):
    from pcileechfwgenerator.host_collect.offline import load_offline_capture

    offline_fixture(tmp_path)

    def resize(config):
        config[:] = config[:length] + bytes(max(0, length - len(config)))

    rewrite_config(tmp_path, resize)
    with pytest.raises(SnapshotError, match="256 or 4096"):
        load_offline_capture(tmp_path)


@pytest.mark.parametrize("offset,value", [(0, 0), (0, 0xFFFF), (2, 0), (2, 0xFFFF)])
def test_invalid_device_ids_rejected(tmp_path, offset, value):
    from pcileechfwgenerator.host_collect.offline import load_offline_capture

    offline_fixture(tmp_path)

    def mutate(config):
        config[offset : offset + 2] = value.to_bytes(2, "little")

    rewrite_config(tmp_path, mutate)
    with pytest.raises(SnapshotError, match="device IDs"):
        load_offline_capture(tmp_path)


@pytest.mark.parametrize(
    "kind", ["misaligned", "reserved_type", "last_64", "upper_occupied"]
)
def test_inconsistent_bar_layout_rejected(tmp_path, kind):
    from pcileechfwgenerator.host_collect.offline import load_offline_capture

    offline_fixture(tmp_path)

    def mutate(config):
        value = {
            "misaligned": 0x80000010,
            "reserved_type": 0x80000002,
            "last_64": 0x80000004,
            "upper_occupied": 0x80000004,
        }[kind]
        offset = 36 if kind == "last_64" else 16
        config[offset : offset + 4] = value.to_bytes(4, "little")

    rewrite_config(tmp_path, mutate)
    if kind in ("last_64", "upper_occupied"):
        path = tmp_path / "device_context.json"
        data = json.loads(path.read_text())
        data["offline_resources"]["bar_sizes"][5 if kind == "last_64" else 1] = 4096
        path.write_text(json.dumps(data))
    with pytest.raises(SnapshotError, match="BAR"):
        load_offline_capture(tmp_path)


@pytest.mark.parametrize("size", [256, 4096])
def test_valid_capture_and_64bit_bar(tmp_path, size):
    from pcileechfwgenerator.host_collect.offline import (
        captured_bars,
        load_offline_capture,
    )

    offline_fixture(tmp_path, size=16384)

    def mutate(config):
        config.extend(bytes(size - len(config)))
        config[16:20] = (0x80000004).to_bytes(4, "little")
        config[20:24] = (1).to_bytes(4, "little")

    rewrite_config(tmp_path, mutate)
    snapshot = load_offline_capture(tmp_path)
    bar = captured_bars(snapshot)[0]
    assert bar.is_64bit and bar.address == 0x180000000 and bar.size == 16384


@pytest.mark.parametrize(
    "change",
    [
        "table_overrun",
        "pba_overrun",
        "overlap",
        "invalid_bir",
        "cycle",
        "unaligned",
        "truncated",
    ],
)
def test_invalid_msix_layout(tmp_path, change):
    from pcileechfwgenerator.device_clone.msix_capability import parse_msix_capability
    from pcileechfwgenerator.host_collect.offline import load_offline_capture

    offline_fixture(tmp_path, size=16384, msix=True)

    def mutate(config):
        if change == "table_overrun":
            config[0x44:0x48] = (16384 - 8).to_bytes(4, "little")
        elif change == "pba_overrun":
            config[0x48:0x4C] = (16384).to_bytes(4, "little")
        elif change == "overlap":
            config[0x48:0x4C] = (0x1000).to_bytes(4, "little")
        elif change == "invalid_bir":
            config[0x44:0x48] = (0x1006).to_bytes(4, "little")
        elif change == "cycle":
            config[0x41] = 0x40
        elif change == "unaligned":
            config[0x34] = 0x41
        else:
            config[0x34] = 0xFC
            config[0xFC] = 0x11

    rewrite_config(tmp_path, mutate)
    if change in ("table_overrun", "pba_overrun", "overlap", "invalid_bir"):
        context = json.loads((tmp_path / "device_context.json").read_text())
        path = tmp_path / "msix_data.json"
        data = json.loads(path.read_text())
        data["msix_info"] = parse_msix_capability(context["config_space_hex"])
        path.write_text(json.dumps(data))
    with pytest.raises(SnapshotError, match="MSI-X|capability"):
        load_offline_capture(tmp_path)


@pytest.mark.parametrize("legacy", ["unversioned", "identity_only", "missing_sidecar"])
def test_legacy_capture_errors_are_actionable_and_do_not_create_outputs(
    tmp_path, legacy
):
    from pcileechfwgenerator.host_collect.offline import import_capture

    source, output = tmp_path / "source", tmp_path / "output"
    if legacy == "identity_only":
        make_snapshot(source)
    else:
        offline_fixture(source)
        if legacy == "unversioned":
            (source / "device_context.json").write_text('{"config_space_hex": "00"}')
        else:
            (source / "msix_data.json").unlink()
    with pytest.raises(SnapshotError, match="Collect|capture collect"):
        import_capture(source, output)
    assert not output.exists()


def test_import_preserves_capture_identity_and_source_and_refuses_overwrite(tmp_path):
    from pcileechfwgenerator.host_collect.offline import (
        import_capture,
        load_offline_capture,
    )

    source, target = tmp_path / "source", tmp_path / "target with spaces"
    offline_fixture(source, size=16384, msix=True)
    before = {path.name: path.read_bytes() for path in source.iterdir()}
    first = load_offline_capture(source)
    copied = import_capture(source, target)
    assert first.identity == copied.identity == load_offline_capture(target).identity
    assert before == {path.name: path.read_bytes() for path in source.iterdir()}
    assert first.context == json.loads((target / "device_context.json").read_text())
    after = {path.name: path.read_bytes() for path in target.iterdir()}
    with pytest.raises(SnapshotError, match="already contains"):
        import_capture(source, target)
    assert after == {path.name: path.read_bytes() for path in target.iterdir()}


def test_capture_cli_validate_import_and_offline_build_skip_host_checks(
    tmp_path, monkeypatch
):
    import sys
    from unittest.mock import Mock

    source, target, output = (
        tmp_path / "source",
        tmp_path / "target",
        tmp_path / "output",
    )
    offline_fixture(source)
    for name in (
        "check_sudo",
        "check_vfio_requirements",
        "check_and_install_requirements",
        "run_host_collect",
    ):
        monkeypatch.setattr(
            pcileech, name, Mock(side_effect=AssertionError("hardware path"))
        )
    monkeypatch.setattr(pcileech.platform, "system", lambda: "Darwin")
    monkeypatch.setattr(
        sys, "argv", ["pcileech", "capture", "validate", "--input", str(source)]
    )
    assert pcileech.main() == 0
    monkeypatch.setattr(
        sys,
        "argv",
        [
            "pcileech",
            "capture",
            "import",
            "--input",
            str(source),
            "--output",
            str(target),
        ],
    )
    assert pcileech.main() == 0
    render = Mock(return_value=0)
    vivado = Mock(return_value=0)
    monkeypatch.setattr(pcileech, "run_local_templating", render)
    monkeypatch.setattr(pcileech, "run_host_vivado", vivado)
    monkeypatch.setattr(
        sys,
        "argv",
        [
            "pcileech",
            "build",
            "--from-capture",
            str(target),
            "--board",
            "pcileech_75t484_x1",
            "--templates-only",
            "--output",
            str(output),
        ],
    )
    assert pcileech.main() == 0
    assert render.call_args.args[0].bdf == "0000:03:00.0"
    vivado.assert_not_called()
    for name in (
        "check_sudo",
        "check_vfio_requirements",
        "check_and_install_requirements",
        "run_host_collect",
    ):
        getattr(pcileech, name).assert_not_called()


@pytest.mark.parametrize(
    "extra",
    [["--profile", "1", "--local"], ["--host-collect-only"], ["--force-recapture"]],
)
def test_conflicting_offline_options_fail_before_output(tmp_path, monkeypatch, extra):
    from unittest.mock import Mock

    source = tmp_path / "source"
    offline_fixture(source)
    render = Mock()
    monkeypatch.setattr(pcileech, "run_local_templating", render)
    args = pcileech.create_parser().parse_args(
        [
            "build",
            "--from-capture",
            str(source),
            "--board",
            "pcileech_75t484_x1",
            "--templates-only",
            *extra,
        ]
    )
    assert pcileech.handle_build(args) == 1
    render.assert_not_called()


def test_complete_collector_reads_resources_and_publishes_valid_pair(
    tmp_path, monkeypatch
):
    from pathlib import Path

    from pcileechfwgenerator.host_collect.collector import HostCollector
    from pcileechfwgenerator.host_collect.offline import load_offline_capture

    fixture_path = offline_fixture(tmp_path / "fixture")
    config = bytes.fromhex(json.loads(fixture_path.read_text())["config_space_hex"])
    monkeypatch.setattr(HostCollector, "_read_config_space", lambda self: config)
    original = Path.read_text

    def resource(path, *args, **kwargs):
        if str(path) == "/sys/bus/pci/devices/0000:03:00.0/resource":
            return "80000000 80000fff 200\n" + "0 0 0\n" * 5
        return original(path, *args, **kwargs)

    monkeypatch.setattr(Path, "read_text", resource)
    target = tmp_path / "collected"
    assert HostCollector("0000:03:00.0", target, complete_capture=True).run() == 0
    assert (
        load_offline_capture(target).context["offline_resources"]["bar_sizes"][0]
        == 4096
    )


@pytest.mark.parametrize("entry", ["unified", "stage2"])
@pytest.mark.parametrize(
    "conflict",
    ["nested_output", "template_in_capture", "large_bar", "no_memory_bar", "wrong_bdf"],
)
def test_render_preflight_preserves_input_and_creates_no_output(
    tmp_path, entry, conflict
):
    from pcileechfwgenerator.build import (
        ConfigurationManager,
        FirmwareBuilder,
        parse_args,
    )
    from pcileechfwgenerator.exceptions import ConfigurationError

    source, output = tmp_path / "source", tmp_path / "output"
    offline_fixture(
        source, size=(32 * 1024 * 1024 if conflict == "large_bar" else 4096)
    )
    if conflict == "no_memory_bar":
        rewrite_config(
            source,
            lambda config: config.__setitem__(
                slice(16, 20), (0x80000001).to_bytes(4, "little")
            ),
        )
    if conflict == "nested_output":
        output = source / "output"
    extra = []
    if conflict == "template_in_capture":
        extra = [
            "--generate-donor-template" if entry == "unified" else "--output-template",
            str(source / "new.json"),
        ]
    if conflict == "wrong_bdf":
        extra = ["--bdf", "0000:04:00.0"]
    before = {path.name: path.read_bytes() for path in source.iterdir()}
    argv = [
        "--from-capture",
        str(source),
        "--board",
        "pcileech_75t484_x1",
        "--output",
        str(output),
        *extra,
    ]
    if entry == "unified":
        args = pcileech.create_parser().parse_args(["build", "--templates-only", *argv])
        assert pcileech.handle_build(args) == 1
    else:
        with pytest.raises(ConfigurationError):
            FirmwareBuilder(ConfigurationManager().create_from_args(parse_args(argv)))
    assert not output.exists()
    assert before == {path.name: path.read_bytes() for path in source.iterdir()}


def test_container_offline_capture_is_read_only_and_uses_real_parser(
    tmp_path, monkeypatch
):
    import logging
    from unittest.mock import Mock

    from pcileechfwgenerator.build import parse_args

    source, output = tmp_path / "source", tmp_path / "output"
    args = pcileech.create_parser().parse_args(
        [
            "build",
            "--from-capture",
            str(source),
            "--bdf",
            "0000:03:00.0",
            "--board",
            "pcileech_75t484_x1",
            "--templates-only",
            "--container-mode",
            "container",
        ]
    )
    run = Mock(return_value=Mock(returncode=1))
    monkeypatch.setattr(
        pcileech, "_ensure_container_image", lambda *args, **kwargs: True
    )
    monkeypatch.setattr(pcileech.subprocess, "run", run)
    assert (
        pcileech._run_in_container(
            args, "podman", source, output, logging.getLogger(__name__)
        )
        == 1
    )
    command = run.call_args.args[0]
    assert f"{source}:/datastore:ro" in command
    parsed = parse_args(command[command.index("src.build") + 1 :])
    assert parsed.from_capture == "/datastore"
    assert parsed.profile == 0
