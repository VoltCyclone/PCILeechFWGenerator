import os

"""MMIO capture process lifecycle tested exclusively with fake processes."""

import signal
import subprocess
from pathlib import Path

import pytest
from pcileechfwgenerator.device_clone import mmio_tracer as module

TRACE = (
    "REGISTER_OBSERVER_V1\n"
    "map,123,10,,0xffffc90000100000,,0x80000000,4096,,\n"
    "read_enter,123,11,4,0xffffc90000100010,,,,11,\n"
    "read_return,123,12,4,0xffffc90000100010,85,,,11,ok\n"
    "write,456,13,4,0xffffc90000200000,85,,,,\n"
)


class FakeProcess:
    def __init__(
        self,
        *,
        stdout,
        stderr,
        text,
        trace=TRACE,
        diagnostic="",
        early=None,
        stubborn=False,
        final_code=-signal.SIGTERM,
        **kwargs,
    ):
        self.output_path = Path(stdout.name)
        self.error_path = Path(stderr.name)
        stdout.write(trace)
        stdout.flush()
        stderr.write(diagnostic)
        stderr.flush()
        self.returncode = early
        self.stubborn = stubborn
        self.final_code = final_code
        self.terminated = self.killed = self.reaped = False
        self.poll_count = 0
        self.exit_on_poll = None

    def poll(self):
        self.poll_count += 1
        if self.exit_on_poll == self.poll_count:
            self.returncode = 1
        return self.returncode

    def terminate(self):
        self.terminated = True
        if not self.stubborn:
            self.returncode = self.final_code

    def kill(self):
        self.killed = True
        self.returncode = -signal.SIGKILL

    def wait(self, timeout=None):
        if self.returncode is None:
            raise subprocess.TimeoutExpired("fake bpftrace", timeout)
        self.reaped = True
        return self.returncode


@pytest.fixture
def capture(monkeypatch):
    tracer = object.__new__(module.MmioTracer)
    tracer.device_bdf = "0000:03:00.0"
    monkeypatch.setattr(tracer, "_get_bar_info", lambda index: (0x80000000, 4096))
    instances = []
    options = {}
    clock = [0.0]
    monkeypatch.setattr(module.time, "monotonic", lambda: clock[0])
    monkeypatch.setattr(module.time, "monotonic_ns", lambda: int(clock[0] * 1e9))
    monkeypatch.setattr(
        module.time, "sleep", lambda duration: clock.__setitem__(0, clock[0] + duration)
    )

    def popen(argv, **kwargs):
        assert argv[:4] == ["bpftrace", "-q", "-B", "line"]
        assert "shell" not in kwargs
        proc = FakeProcess(**kwargs, **options)
        instances.append(proc)
        return proc

    monkeypatch.setattr(module.subprocess, "Popen", popen)
    return tracer, options, instances


def assert_clean(proc):
    assert proc.reaped
    assert not proc.output_path.exists() and not proc.error_path.exists()


def test_capture_waits_for_readiness_and_reports_attribution_counts(
    capture, monkeypatch
):
    tracer, options, instances = capture
    triggered = []

    def trigger():
        assert "REGISTER_OBSERVER_V1" in instances[0].output_path.read_text()
        assert instances[0].poll_count > 0
        triggered.append(True)

    monkeypatch.setattr(tracer, "_trigger_driver_rebind", trigger)
    trace = tracer.capture_probe_trace(2, 0.1, trigger_rebind=True)
    assert triggered == [True]
    assert trace.bar_index == 2 and trace.duration_ns >= 100000000
    assert trace.accesses[0].offset == 0x10 and trace.accesses[0].value == 85
    assert trace.observer_result.observations[0].ref.bar_index == 2
    assert trace.dropped_events == trace.unattributed_accesses == 1
    assert trace.coverage_notes
    assert instances[0].terminated
    assert_clean(instances[0])


@pytest.mark.parametrize(
    "options,match",
    [
        (
            {"trace": "", "early": 1, "diagnostic": "No probes to attach"},
            "before readiness",
        ),
        ({"diagnostic": "WARNING: ioremap is not traceable"}, "collector diagnostics"),
        ({"trace": ""}, "readiness timeout"),
        ({"stubborn": True}, "failed to stop cleanly"),
        ({"final_code": 2}, "failed to stop cleanly"),
    ],
)
def test_capture_failures_are_explicit_and_child_is_reaped(capture, options, match):
    tracer, settings, instances = capture
    settings.update(options)
    tracer.READY_TIMEOUT_SEC = 0.02
    with pytest.raises(RuntimeError, match=match):
        tracer.capture_probe_trace(0, 0.1)
    assert_clean(instances[0])


def test_rebind_exception_terminates_and_reaps(capture, monkeypatch):
    tracer, options, instances = capture

    def trigger():
        raise OSError("fake workload failure")

    monkeypatch.setattr(tracer, "_trigger_driver_rebind", trigger)
    with pytest.raises(OSError, match="fake workload failure"):
        tracer.capture_probe_trace(0, 0.1, trigger_rebind=True)
    assert instances[0].terminated
    assert_clean(instances[0])


def test_early_exit_after_ready_is_not_an_empty_success(capture, monkeypatch):
    tracer, options, instances = capture

    def trigger():
        instances[0].returncode = 0

    monkeypatch.setattr(tracer, "_trigger_driver_rebind", trigger)
    with pytest.raises(RuntimeError, match="during capture"):
        tracer.capture_probe_trace(0, 0.1, trigger_rebind=True)
    assert_clean(instances[0])


def test_stderr_during_capture_is_not_silent(capture, monkeypatch):
    tracer, options, instances = capture

    def trigger():
        instances[0].error_path.write_text("Lost 5 events")

    monkeypatch.setattr(tracer, "_trigger_driver_rebind", trigger)
    trace = tracer.capture_probe_trace(0, 0.1, trigger_rebind=True)
    assert trace.accesses == []
    assert trace.observer_result.lost_events == 5
    assert trace.observer_result.discarded_observations == 1
    assert trace.dropped_events == 7  # five lost, one discarded, one unattributed
    assert_clean(instances[0])


def test_parser_file_errors_propagate_and_old_pointer_format_is_not_attributed(
    tmp_path,
):
    tracer = object.__new__(module.MmioTracer)
    path = tmp_path / "trace.csv"
    with pytest.raises(FileNotFoundError):
        tracer._parse_trace_output(str(path), 0x80000000, 4096)
    path.write_text("10,R,4,0xffffc90000100010,85\n")
    assert tracer._parse_trace_output(str(path), 0x80000000, 4096) == []
    path.write_text(TRACE)
    assert tracer._parse_trace_output(str(path), 0x80000000, 4096)[0].offset == 16


def test_script_saves_entry_arguments_and_cleans_maps():
    script = module.MmioTracer.BPFTRACE_SCRIPT
    for width in (1, 2, 4):
        bits = width * 8
        assert f"kprobe:ioread{bits}" in script
        body = script.split(f"kretprobe:ioread{bits}", 1)[1].split(
            f"kprobe:iowrite{bits}", 1
        )[0]
        assert "arg0" not in body and "arg1" not in body
        assert f"@ra{width}[tid, $d]" in body
        assert "delete(" in body
    assert "kprobe:ioremap" in script and "kprobe:iounmap" in script
    assert "END {" in script and "clear(@rd1)" in script
    assert "nsecs(monotonic)" not in script  # unsupported on bpftrace < 0.20


def test_popen_failure_cleans_temporary_files(capture, monkeypatch):
    tracer, options, instances = capture
    paths = []

    def fail(argv, **kwargs):
        paths.extend(
            [Path(argv[-1]), Path(kwargs["stdout"].name), Path(kwargs["stderr"].name)]
        )
        raise OSError("fake spawn failure")

    monkeypatch.setattr(module.subprocess, "Popen", fail)
    with pytest.raises(OSError, match="spawn failure"):
        tracer.capture_probe_trace(0, 0.1)
    assert paths and not any(path.exists() for path in paths)


def test_keyboard_interrupt_after_ready_cleans_up(capture, monkeypatch):
    tracer, options, instances = capture

    def interrupt():
        raise KeyboardInterrupt

    monkeypatch.setattr(tracer, "_trigger_driver_rebind", interrupt)
    with pytest.raises(KeyboardInterrupt):
        tracer.capture_probe_trace(0, 0.1, trigger_rebind=True)
    assert instances[0].terminated
    assert_clean(instances[0])


def test_no_activity_before_ready(capture, monkeypatch):
    tracer, options, instances = capture
    options["trace"] = ""
    tracer.READY_TIMEOUT_SEC = 0.02
    calls = []
    monkeypatch.setattr(tracer, "_trigger_driver_rebind", lambda: calls.append(True))
    with pytest.raises(RuntimeError, match="readiness timeout"):
        tracer.capture_probe_trace(0, 0.1, trigger_rebind=True)
    assert calls == []
    assert_clean(instances[0])


def test_io_bar_is_rejected_using_fake_sysfs(monkeypatch):
    from unittest.mock import mock_open

    tracer = object.__new__(module.MmioTracer)
    tracer.device_bdf = "0000:03:00.0"
    monkeypatch.setattr(Path, "exists", lambda self: True)
    monkeypatch.setattr(
        "builtins.open", mock_open(read_data="0x0000c000 0x0000c01f 0x00000101\n")
    )
    with pytest.raises(RuntimeError, match="I/O-port BAR"):
        tracer._get_bar_info(0)


@pytest.mark.parametrize("duration", [0, -1, float("inf"), float("nan")])
def test_invalid_duration_never_starts_collector(capture, duration):
    tracer, options, instances = capture
    with pytest.raises(ValueError, match="duration_sec"):
        tracer.capture_probe_trace(0, duration)
    assert instances == []


# --- The optional rebind restores and verifies the exact driver -------


class _DriverState:
    def __init__(self, driver="e1000e"):
        self.driver = driver
        self.fail = set()
        self.writes = []


def _rebind_tracer(monkeypatch, state, root=None):
    tracer = object.__new__(module.MmioTracer)
    tracer.device_bdf = "0000:03:00.0"
    if root is not None:
        monkeypatch.setattr(module.MmioTracer, "SYSFS_ROOT", root / "sys")
        monkeypatch.setattr(module.MmioTracer, "PROC_ROOT", root / "proc")
        monkeypatch.setattr(module.MmioTracer, "DEV_ROOT", root / "dev")
        (root / "sys" / "block").mkdir(parents=True, exist_ok=True)
        mountinfo = root / "proc" / "self" / "mountinfo"
        if not mountinfo.exists():
            mountinfo.parent.mkdir(parents=True, exist_ok=True)
            mountinfo.write_text("")
        pid_ns = root / "proc" / "self" / "ns" / "pid"
        if not pid_ns.is_symlink():
            pid_ns.parent.mkdir(parents=True, exist_ok=True)
            pid_ns.symlink_to(module.MmioTracer.INIT_PID_NS)
        # Down interfaces carry no configuration unless a test says so.
        monkeypatch.setattr(
            module.MmioTracer, "_configured_interface", lambda self, net: None
        )
    monkeypatch.setattr(module.time, "sleep", lambda _s: None)

    def write(path, value):
        state.writes.append(path)
        if path.endswith("/unbind"):
            if "unbind" in state.fail:
                raise OSError("injected unbind")
            state.driver = None
            if "interrupt" in state.fail:
                raise KeyboardInterrupt
        elif path.endswith("/bind"):
            if "bind" in state.fail:
                raise OSError("injected bind")
            if "defer" in state.fail:
                state.fail.discard("defer")
                state.deferred = path.split("/")[-2]
                raise OSError(517, "probe deferred")
            state.driver = path.split("/")[-2]
        else:
            raise AssertionError("unexpected sysfs write " + path)

    def current():
        if getattr(state, "deferred", None):
            state.driver, state.deferred = state.deferred, None
            return None  # first poll: still deferred
        return state.driver

    monkeypatch.setattr(tracer, "_current_driver", current)
    monkeypatch.setattr(tracer, "_sysfs_write", write)
    return tracer


def test_rebind_restores_exact_driver_without_rescan(monkeypatch, tmp_path):
    state = _DriverState()
    _rebind_tracer(monkeypatch, state, tmp_path)._trigger_driver_rebind()
    assert state.driver == "e1000e"
    assert state.writes == [
        str(tmp_path / "sys/bus/pci/drivers/e1000e/unbind"),
        str(tmp_path / "sys/bus/pci/drivers/e1000e/bind"),
    ]
    assert not any("rescan" in w for w in state.writes)


def test_rebind_failure_reports_final_binding(monkeypatch, tmp_path):
    state = _DriverState()
    state.fail.add("bind")
    with pytest.raises(RuntimeError, match="left it on no driver.*injected bind"):
        _rebind_tracer(monkeypatch, state, tmp_path)._trigger_driver_rebind()


def test_cancellation_after_unbind_still_restores(monkeypatch, tmp_path):
    state = _DriverState()
    state.fail.add("interrupt")
    with pytest.raises(KeyboardInterrupt):
        _rebind_tracer(monkeypatch, state, tmp_path)._trigger_driver_rebind()
    assert state.driver == "e1000e"


def test_failed_unbind_is_reported_and_driver_untouched(monkeypatch, tmp_path):
    state = _DriverState()
    state.fail.add("unbind")
    with pytest.raises(RuntimeError, match="injected unbind"):
        _rebind_tracer(monkeypatch, state, tmp_path)._trigger_driver_rebind()
    assert state.driver == "e1000e"
    assert state.writes == [str(tmp_path / "sys/bus/pci/drivers/e1000e/unbind")]


@pytest.mark.parametrize("driver", [None, "vfio-pci"])
def test_rebind_skips_devices_without_a_native_driver(monkeypatch, tmp_path, driver):
    state = _DriverState(driver)
    _rebind_tracer(monkeypatch, state, tmp_path)._trigger_driver_rebind()
    assert state.writes == [] and state.driver == driver


def _device(root, **files):
    device = root / "sys" / "bus" / "pci" / "devices" / "0000:03:00.0"
    device.mkdir(parents=True, exist_ok=True)
    for name, text in files.items():
        path = device / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(text)
    return device


@pytest.mark.parametrize(
    "files,reason",
    [
        ({"driver_override": "vfio-pci\n"}, "driver_override is vfio-pci"),
        ({"sriov_numvfs": "4\n"}, "SR-IOV"),
        ({"boot_vga": "1\n"}, "display"),
        ({"drm/card0/dev": "226:0"}, "display"),
        ({"net/eth0/flags": "0x1003\n"}, "eth0 is up"),
        # Configured up without carrier is still in use.
        (
            {"net/eth0/flags": "0x1003\n", "net/eth0/operstate": "down\n"},
            "eth0 is up",
        ),
        ({"sriov_numvfs/unreadable": ""}, "cannot verify"),
        ({"nvme/nvme0/block/nvme0n1/holders/dm-0": ""}, "nvme0n1 has holders"),
        (
            {
                "nvme/nvme0/block/nvme0n1/dev": "259:0",
                "nvme/nvme0/block/nvme0n1/nvme0n1p2/partition": "2",
                "nvme/nvme0/block/nvme0n1/nvme0n1p2/dev": "259:2",
            },
            "nvme0n1p2 is mounted",
        ),
    ],
)
def test_rebind_refuses_devices_in_use(monkeypatch, tmp_path, files, reason):
    _device(tmp_path, **files)
    proc = tmp_path / "proc" / "self"
    proc.mkdir(parents=True)
    (proc / "mountinfo").write_text("36 1 259:2 / / rw - ext4 /dev/nvme0n1p2 rw\n")
    state = _DriverState()
    with pytest.raises(RuntimeError, match=reason):
        _rebind_tracer(monkeypatch, state, tmp_path)._trigger_driver_rebind()
    assert state.writes == [] and state.driver == "e1000e"


def test_idle_device_with_matching_override_is_rebound(monkeypatch, tmp_path):
    _device(
        tmp_path,
        driver_override="(null)\n",
        sriov_numvfs="0\n",
        boot_vga="0\n",
        **{"net/eth0/flags": "0x1002\n"},
    )
    state = _DriverState()
    _rebind_tracer(monkeypatch, state, tmp_path)._trigger_driver_rebind()
    assert state.driver == "e1000e"


def test_mount_in_another_namespace_refuses_rebind(monkeypatch, tmp_path):
    _device(
        tmp_path,
        **{
            "nvme/nvme0/block/nvme0n1/dev": "259:0",
        },
    )
    other = tmp_path / "proc" / "4242"
    (other / "ns").mkdir(parents=True)
    (other / "ns" / "mnt").symlink_to("mnt:[4026532999]")
    (other / "mountinfo").write_text("50 1 259:0 / /data rw - xfs /dev/nvme0n1 rw\n")
    state = _DriverState()
    with pytest.raises(RuntimeError, match="nvme0n1 is mounted"):
        _rebind_tracer(monkeypatch, state, tmp_path)._trigger_driver_rebind()
    assert state.writes == []


def _journal(monkeypatch, tmp_path):
    from pcileechfwgenerator.cli.vfio_handler import binding_journal_path
    from pcileechfwgenerator.host_collect.register_learning import JOURNAL_DIR_ENV

    monkeypatch.setenv(JOURNAL_DIR_ENV, str(tmp_path / "journals"))
    return binding_journal_path(tmp_path / "journals" / "bindings", "0000:03:00.0")


def test_existing_binding_journal_blocks_the_rebind(monkeypatch, tmp_path):
    journal = _journal(monkeypatch, tmp_path)
    journal.parent.mkdir(parents=True)
    journal.write_text("{}")
    state = _DriverState()
    with pytest.raises(RuntimeError, match="Unresolved binding incident"):
        _rebind_tracer(monkeypatch, state, tmp_path)._trigger_driver_rebind()
    assert state.writes == []


def test_journal_cleared_on_success_and_kept_on_failure(monkeypatch, tmp_path):
    journal = _journal(monkeypatch, tmp_path)
    _rebind_tracer(monkeypatch, _DriverState(), tmp_path)._trigger_driver_rebind()
    assert not journal.exists()
    state = _DriverState()
    state.fail.add("bind")
    with pytest.raises(RuntimeError, match="binding journal"):
        _rebind_tracer(monkeypatch, state, tmp_path)._trigger_driver_rebind()
    assert journal.exists()
    assert "mmio_tracer" in journal.read_text()


def test_deferred_probe_is_waited_out(monkeypatch, tmp_path):
    state = _DriverState()
    state.fail.add("defer")
    _rebind_tracer(monkeypatch, state, tmp_path)._trigger_driver_rebind()
    assert state.driver == "e1000e"


def _disk(tmp_path):
    _device(tmp_path, **{"nvme/nvme0/block/nvme0n1/dev": "259:0"})
    dev = tmp_path / "dev"
    dev.mkdir(exist_ok=True)
    (dev / "nvme0n1").write_text("")


def test_free_block_device_allows_rebind(monkeypatch, tmp_path):
    _disk(tmp_path)
    state = _DriverState()
    _rebind_tracer(monkeypatch, state, tmp_path)._trigger_driver_rebind()
    assert state.driver == "e1000e"


def test_exclusively_claimed_block_device_refuses_rebind(monkeypatch, tmp_path):
    """A ZFS vdev has no mount, holder or swap entry; only its claim shows."""
    import errno

    _disk(tmp_path)
    real_open = module.os.open

    def claimed(path, flags, *args):
        if str(path).endswith("/dev/nvme0n1") and flags & module.os.O_EXCL:
            raise OSError(errno.EBUSY, "Device or resource busy")
        return real_open(path, flags, *args)

    monkeypatch.setattr(module.os, "open", claimed)
    state = _DriverState()
    with pytest.raises(RuntimeError, match="nvme0n1 is claimed"):
        _rebind_tracer(monkeypatch, state, tmp_path)._trigger_driver_rebind()
    assert state.writes == []


def test_block_device_without_a_node_cannot_be_verified(monkeypatch, tmp_path):
    _device(tmp_path, **{"nvme/nvme0/block/nvme0n1/dev": "259:0"})
    state = _DriverState()
    with pytest.raises(RuntimeError, match="cannot verify block device nvme0n1"):
        _rebind_tracer(monkeypatch, state, tmp_path)._trigger_driver_rebind()


def test_nvme_namespace_outside_a_block_dir_is_checked(monkeypatch, tmp_path):
    """NVMe namespaces sit at nvme/nvme0/nvme0n1; found through /sys/block."""
    import errno

    _device(tmp_path, **{"nvme/nvme0/nvme0n1/dev": "259:0"})
    device = tmp_path / "sys" / "bus" / "pci" / "devices" / "0000:03:00.0"
    block = tmp_path / "sys" / "block"
    block.mkdir(parents=True, exist_ok=True)
    (block / "nvme0n1").symlink_to(device / "nvme" / "nvme0" / "nvme0n1")
    (device / "nvme" / "nvme0" / "nvme0n1" / "nvme0n1p1").mkdir()
    (device / "nvme" / "nvme0" / "nvme0n1" / "nvme0n1p1" / "partition").write_text("1")
    real_open = module.os.open

    def zfs_member(path, flags, *args):
        if str(path).endswith("/dev/nvme0n1p1") and flags & module.os.O_EXCL:
            raise OSError(errno.EBUSY, "Device or resource busy")
        return real_open(path, flags, *args)

    monkeypatch.setattr(module.os, "open", zfs_member)
    dev = tmp_path / "dev"
    dev.mkdir()
    (dev / "nvme0n1").write_text("")
    (dev / "nvme0n1p1").write_text("")
    state = _DriverState()
    with pytest.raises(RuntimeError, match="nvme0n1p1 is claimed"):
        _rebind_tracer(monkeypatch, state, tmp_path)._trigger_driver_rebind()
    assert state.writes == []


def test_open_device_node_refuses_rebind(monkeypatch, tmp_path):
    """e.g. a sound server holding /dev/snd/pcmC1D3p of the donor."""
    import os as _os
    import stat as _stat
    from types import SimpleNamespace

    _device(tmp_path, **{"sound/card1/pcmC1D3p/dev": "116:5"})
    proc = tmp_path / "proc" / "777"
    (proc / "fd").mkdir(parents=True)
    (proc / "comm").write_text("pipewire\n")
    (proc / "fd" / "9").write_text("")
    real_stat = module.os.stat

    def fake_stat(path, *args, **kwargs):
        if str(path).endswith("/777/fd/9"):
            return SimpleNamespace(
                st_mode=_stat.S_IFCHR | 0o600, st_rdev=_os.makedev(116, 5)
            )
        return real_stat(path, *args, **kwargs)

    monkeypatch.setattr(module.os, "stat", fake_stat)
    state = _DriverState()
    with pytest.raises(RuntimeError, match="open by pipewire"):
        _rebind_tracer(monkeypatch, state, tmp_path)._trigger_driver_rebind()
    assert state.writes == []


def _nvme_disk(tmp_path, *, multipath=False):
    """An NVMe namespace with a /dev node; optionally behind native multipath."""
    device = tmp_path / "sys" / "bus" / "pci" / "devices" / "0000:03:00.0"
    block = tmp_path / "sys" / "block"
    block.mkdir(parents=True, exist_ok=True)
    if multipath:
        _device(tmp_path, **{"nvme/nvme0/nvme0c0n1/dev": "0:0"})
        head = tmp_path / "sys" / "devices" / "virtual" / "nvme-subsystem" / "nvme0n1"
        (head / "multipath").mkdir(parents=True)
        (head / "dev").write_text("259:0")
        (head / "multipath" / "nvme0c0n1").symlink_to(
            device / "nvme" / "nvme0" / "nvme0c0n1"
        )
        (block / "nvme0n1").symlink_to(head)
    else:
        _device(tmp_path, **{"nvme/nvme0/nvme0n1/dev": "259:0"})
        (block / "nvme0n1").symlink_to(device / "nvme" / "nvme0" / "nvme0n1")
    dev = tmp_path / "dev"
    dev.mkdir(exist_ok=True)
    (dev / "nvme0n1").write_text("")


def test_multipath_namespace_is_checked(monkeypatch, tmp_path):
    """nvme_core.multipath=Y: the disk lives under nvme-subsystem, not the PCI
    function; a mount of it must still refuse the rebind."""
    _nvme_disk(tmp_path, multipath=True)
    proc = tmp_path / "proc" / "self"
    proc.mkdir(parents=True)
    (proc / "mountinfo").write_text("36 1 259:0 / / rw - ext4 /dev/nvme0n1 rw\n")
    state = _DriverState()
    with pytest.raises(RuntimeError, match="nvme0n1 is mounted"):
        _rebind_tracer(monkeypatch, state, tmp_path)._trigger_driver_rebind()
    assert state.writes == []


def test_multipath_path_without_a_known_head_is_unverifiable(monkeypatch, tmp_path):
    _device(tmp_path, **{"nvme/nvme0/nvme0c0n1/dev": "0:0"})
    state = _DriverState()
    with pytest.raises(RuntimeError, match="cannot verify multipath namespace"):
        _rebind_tracer(monkeypatch, state, tmp_path)._trigger_driver_rebind()


@pytest.mark.parametrize(
    "setup,reason",
    [
        (
            lambda sys: (
                (sys / "block" / "loop0" / "loop").mkdir(parents=True),
                (sys / "block" / "loop0" / "loop" / "backing_file").write_text(
                    "/dev/nvme0n1\n"
                ),
            ),
            "backs loop0",
        ),
        (
            lambda sys: (
                (sys / "kernel/config/nvmet/subsystems/s/namespaces/1").mkdir(
                    parents=True
                ),
                (
                    sys / "kernel/config/nvmet/subsystems/s/namespaces/1/enable"
                ).write_text("1\n"),
                (
                    sys / "kernel/config/nvmet/subsystems/s/namespaces/1/device_path"
                ).write_text("/dev/nvme0n1\n"),
            ),
            "NVMe-oF target",
        ),
    ],
)
def test_kernel_block_users_refuse_rebind(monkeypatch, tmp_path, setup, reason):
    _nvme_disk(tmp_path)
    setup(tmp_path / "sys")
    _fake_block_numbers(monkeypatch)
    state = _DriverState()
    with pytest.raises(RuntimeError, match=reason):
        _rebind_tracer(monkeypatch, state, tmp_path)._trigger_driver_rebind()


def test_network_device_without_visible_interface_refuses(monkeypatch, tmp_path):
    _device(tmp_path, **{"class": "0x020000\n"})
    state = _DriverState()
    with pytest.raises(RuntimeError, match="another namespace"):
        _rebind_tracer(monkeypatch, state, tmp_path)._trigger_driver_rebind()


def test_interface_with_vlan_upper_refuses(monkeypatch, tmp_path):
    _device(
        tmp_path,
        **{
            "class": "0x020000\n",
            "net/eno4/flags": "0x1002\n",
            "net/eno4/upper_eno4.90/x": "",
        },
    )
    state = _DriverState()
    with pytest.raises(RuntimeError, match="upper devices"):
        _rebind_tracer(monkeypatch, state, tmp_path)._trigger_driver_rebind()


def test_mmapped_bar_resource_refuses_rebind(monkeypatch, tmp_path):
    """A userspace driver mmapping sysfs resource0 (fd already closed)."""
    _device(tmp_path, **{"resource0": ""})
    device = tmp_path / "sys" / "bus" / "pci" / "devices" / "0000:03:00.0"
    maps = tmp_path / "proc" / "4321" / "map_files"
    maps.mkdir(parents=True)
    (tmp_path / "proc" / "4321" / "comm").write_text("uio-app\n")
    (maps / "7f00-7f10").symlink_to(device.resolve() / "resource0")
    state = _DriverState()
    with pytest.raises(RuntimeError, match="open by uio-app"):
        _rebind_tracer(monkeypatch, state, tmp_path)._trigger_driver_rebind()


def _fake_block_numbers(monkeypatch, aliases=("/dev/nvme0n1",)):
    def number(path):
        if path in aliases:
            return os.makedev(259, 0)
        raise FileNotFoundError(path)

    monkeypatch.setattr(module.MmioTracer, "_block_number", staticmethod(number))


def test_nvmet_alias_path_is_matched_by_device_number(monkeypatch, tmp_path):
    """configfs keeps the spelling given (by-id); match the device, not text."""
    _nvme_disk(tmp_path)
    ns = tmp_path / "sys/kernel/config/nvmet/subsystems/s/namespaces/1"
    ns.mkdir(parents=True)
    (ns / "enable").write_text("1\n")
    by_id = "/dev/disk/by-id/nvme-Example_123"
    (ns / "device_path").write_text(by_id + "\n")
    _fake_block_numbers(monkeypatch, aliases=(by_id,))
    with pytest.raises(RuntimeError, match="NVMe-oF target"):
        _rebind_tracer(monkeypatch, _DriverState(), tmp_path)._trigger_driver_rebind()


def test_unresolvable_nvmet_path_refuses(monkeypatch, tmp_path):
    _nvme_disk(tmp_path)
    ns = tmp_path / "sys/kernel/config/nvmet/subsystems/s/namespaces/1"
    ns.mkdir(parents=True)
    (ns / "enable").write_text("1\n")
    (ns / "device_path").write_text("/dev/disk/by-id/elsewhere\n")
    _fake_block_numbers(monkeypatch, aliases=())
    with pytest.raises(RuntimeError, match="cannot resolve"):
        _rebind_tracer(monkeypatch, _DriverState(), tmp_path)._trigger_driver_rebind()


def test_multipath_head_found_by_kernel_naming(monkeypatch, tmp_path):
    """Linux < 6.14 has no head multipath/ directory: derive the head name."""
    _nvme_disk(tmp_path, multipath=True)
    head = tmp_path / "sys/devices/virtual/nvme-subsystem/nvme0n1"
    for link in (head / "multipath").iterdir():
        link.unlink()
    (head / "multipath").rmdir()
    proc = tmp_path / "proc" / "self"
    proc.mkdir(parents=True)
    (proc / "mountinfo").write_text("36 1 259:0 / / rw - ext4 /dev/nvme0n1 rw\n")
    with pytest.raises(RuntimeError, match="nvme0n1 is mounted"):
        _rebind_tracer(monkeypatch, _DriverState(), tmp_path)._trigger_driver_rebind()


@pytest.mark.parametrize(
    "files,reason",
    [
        ({"class": "0x0c0330\n"}, "USB controller hosts other devices"),
        ({"class": "0x060400\n"}, "PCI bridge hosts other devices"),
        (
            {"usb1/1-1/1-1:1.0/net/enx0/flags": "0x1003\n"},
            "enx0 is up",
        ),
    ],
)
def test_bus_hosts_and_descendant_interfaces_refuse(
    monkeypatch, tmp_path, files, reason
):
    _device(tmp_path, **files)
    with pytest.raises(RuntimeError, match=reason):
        _rebind_tracer(monkeypatch, _DriverState(), tmp_path)._trigger_driver_rebind()


def test_private_pid_namespace_refuses(monkeypatch, tmp_path):
    _device(tmp_path)
    pid_ns = tmp_path / "proc" / "self" / "ns" / "pid"
    pid_ns.parent.mkdir(parents=True)
    pid_ns.symlink_to("pid:[4026532999]")
    with pytest.raises(RuntimeError, match="initial PID namespace"):
        _rebind_tracer(monkeypatch, _DriverState(), tmp_path)._trigger_driver_rebind()


def test_unreadable_process_files_refuse(monkeypatch, tmp_path):
    _device(tmp_path)
    fd_dir = tmp_path / "proc" / "77" / "fd"
    fd_dir.mkdir(parents=True)
    real_iterdir = Path.iterdir

    def iterdir(self):
        if self == fd_dir:
            raise PermissionError(13, "Permission denied")
        return real_iterdir(self)

    monkeypatch.setattr(Path, "iterdir", iterdir)
    with pytest.raises(RuntimeError, match="cannot verify files of pid 77"):
        _rebind_tracer(monkeypatch, _DriverState(), tmp_path)._trigger_driver_rebind()


def test_down_interface_with_configuration_refuses(monkeypatch, tmp_path):
    _device(tmp_path, **{"net/eth0/flags": "0x1002\n", "net/eth0/mtu": "9000\n"})
    real = module.MmioTracer._configured_interface
    tracer = _rebind_tracer(monkeypatch, _DriverState(), tmp_path)
    monkeypatch.setattr(module.MmioTracer, "_configured_interface", real)
    with pytest.raises(RuntimeError, match="eth0 is configured"):
        tracer._trigger_driver_rebind()


def test_disk_claims_are_held_across_the_unbind(monkeypatch, tmp_path):
    _nvme_disk(tmp_path)
    held = []
    real_open, real_close = os.open, os.close
    opened = set()

    dev_root = str(tmp_path / "dev")

    def fake_open(path, flags, *a):
        if not str(path).startswith(dev_root):
            return real_open(path, flags, *a)
        fd = real_open(path, os.O_RDONLY)  # fake node: a regular file
        opened.add(fd)
        return fd

    def fake_close(fd):
        opened.discard(fd)
        real_close(fd)

    monkeypatch.setattr(module.os, "open", fake_open)
    monkeypatch.setattr(module.os, "close", fake_close)
    state = _DriverState()
    tracer = _rebind_tracer(monkeypatch, state, tmp_path)
    original_write = tracer._sysfs_write

    def write(path, value):
        if path.endswith("/unbind"):
            held.append(len(opened))
        original_write(path, value)

    tracer._sysfs_write = write
    tracer._trigger_driver_rebind()
    # An exclusive claim on the disk is open at the moment of the unbind,
    # and released afterwards.
    assert held == [1] and not opened
