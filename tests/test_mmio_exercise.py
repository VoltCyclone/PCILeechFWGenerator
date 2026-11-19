"""Device exercises run inside the mmiotrace window (fake sysfs only)."""

import time
from types import SimpleNamespace

import pytest
from pcileechfwgenerator.host_collect import exercise
from pcileechfwgenerator.host_collect.exercise import (
    BlockRead,
    ExerciseReport,
    NetworkLink,
    RuntimePower,
    SensorReads,
    device_exercise,
)


def _deadline(sec=5.0):
    return time.monotonic() + sec


@pytest.fixture
def no_wait(monkeypatch, tmp_path):
    monkeypatch.setattr(exercise, "LINK_SETTLE_SEC", 0.0)
    monkeypatch.setattr(exercise, "SUSPEND_WAIT_SEC", 0.3)
    monkeypatch.setattr(exercise, "RESUME_WAIT_SEC", 0.3)
    monkeypatch.setattr(exercise, "_settle_udev", lambda: None)
    monkeypatch.setattr(exercise, "NETWORKD_LINKS", tmp_path / "networkd")
    sysnet = tmp_path / "procsys"
    (sysnet / "ipv6" / "conf" / "eno4").mkdir(parents=True)
    (sysnet / "ipv6" / "conf" / "eno4" / "disable_ipv6").write_text("0")
    monkeypatch.setattr(exercise, "PROC_SYS_NET", sysnet)
    return sysnet


# --- network ---------------------------------------------------------------


class FakeIp:
    """ip(8) stand-in that flips IFF_UP in the fake sysfs flags file."""

    def __init__(
        self,
        net,
        addresses="",
        stick_up=False,
        slaac="",
        sticky_addr=False,
        up_times_out=False,
        sysnet=None,
    ):
        self.net, self.addresses, self.stick_up = net, addresses, stick_up
        self.slaac, self.sticky_addr, self.up_times_out = (
            slaac,
            sticky_addr,
            up_times_out,
        )
        self.sysnet, self.ipv6_during_up = sysnet, None
        self.calls = []

    def __call__(self, argv, **kwargs):
        import subprocess

        self.calls.append(argv[1:])
        if argv[1:3] == ["link", "set"]:
            flags = int((self.net / "flags").read_text(), 16)
            if argv[-1] == "up":
                if self.sysnet:
                    self.ipv6_during_up = (
                        self.sysnet / "ipv6/conf/eno4/disable_ipv6"
                    ).read_text()
                flags |= 1
                if self.slaac:
                    self.addresses = self.slaac  # autoconfig happened
                if self.up_times_out:
                    (self.net / "flags").write_text(hex(flags))
                    raise subprocess.TimeoutExpired(argv, 5)
            elif not self.stick_up:
                flags &= ~1
            (self.net / "flags").write_text(hex(flags))
        if argv[1:3] == ["addr", "flush"] and not self.sticky_addr:
            self.addresses = ""
        out = self.addresses if argv[1:4] == ["-o", "addr", "show"] else ""
        return SimpleNamespace(returncode=0, stdout=out)


def _nic(tmp_path, flags="0x1002"):
    device = tmp_path / "dev0"
    net = device / "net" / "eno4"
    (net / "statistics").mkdir(parents=True)
    (net / "flags").write_text(flags)
    (net / "ifindex").write_text("3")
    (net / "statistics" / "rx_packets").write_text("0")
    return device, net


def _with_ip(monkeypatch, fake):
    monkeypatch.setattr(
        exercise.shutil,
        "which",
        lambda name: None if name == "nmcli" else f"/usr/bin/{name}",
    )
    monkeypatch.setattr(exercise.subprocess, "run", fake)


def test_down_unused_interface_is_brought_up_and_restored(
    tmp_path, monkeypatch, no_wait
):
    device, net = _nic(tmp_path)
    fake = FakeIp(net, sysnet=no_wait)
    _with_ip(monkeypatch, fake)
    report = ExerciseReport()
    NetworkLink().run(device, report, _deadline())
    assert ["link", "set", "dev", "eno4", "up"] in fake.calls
    assert ["link", "set", "dev", "eno4", "down"] in fake.calls
    assert report.ran and not report.not_restored
    assert int((net / "flags").read_text(), 16) & 1 == 0
    # IPv6 off while up (no DAD/RS/SLAAC), restored afterwards.
    assert fake.ipv6_during_up == "1"
    assert (no_wait / "ipv6/conf/eno4/disable_ipv6").read_text() == "0"


def test_autoconfigured_address_is_flushed(tmp_path, monkeypatch, no_wait):
    device, net = _nic(tmp_path)
    fake = FakeIp(net, slaac="3: eno4 inet 10.9.9.9/24")
    _with_ip(monkeypatch, fake)
    report = ExerciseReport()
    NetworkLink().run(device, report, _deadline())
    assert ["addr", "flush", "dev", "eno4"] in fake.calls
    assert not report.not_restored


def test_address_that_cannot_be_removed_is_reported(tmp_path, monkeypatch, no_wait):
    device, net = _nic(tmp_path)
    _with_ip(
        monkeypatch, FakeIp(net, slaac="3: eno4 inet 10.9.9.9/24", sticky_addr=True)
    )
    report = ExerciseReport()
    NetworkLink().run(device, report, _deadline())
    assert any("differ from before" in n for n in report.not_restored)


def test_link_up_timeout_still_brings_it_down(tmp_path, monkeypatch, no_wait):
    device, net = _nic(tmp_path)
    fake = FakeIp(net, up_times_out=True)
    _with_ip(monkeypatch, fake)
    report = ExerciseReport()
    NetworkLink().run(device, report, _deadline())
    assert ["link", "set", "dev", "eno4", "down"] in fake.calls
    assert int((net / "flags").read_text(), 16) & 1 == 0
    assert any("link up failed" in s for s in report.skipped)


def test_interface_managed_by_networkd_is_skipped(tmp_path, monkeypatch, no_wait):
    device, net = _nic(tmp_path)
    links = tmp_path / "networkd"
    links.mkdir()
    (links / "3").write_text("ADMIN_STATE=configured\n")
    fake = FakeIp(net)
    _with_ip(monkeypatch, fake)
    report = ExerciseReport()
    NetworkLink().run(device, report, _deadline())
    assert not any(c[:2] == ["link", "set"] for c in fake.calls)
    assert any("systemd-networkd" in s for s in report.skipped)


@pytest.mark.parametrize(
    "setup,why",
    [
        (lambda net: (net / "flags").write_text("0x1003"), "already up"),
        (lambda net: (net / "master").mkdir(), "bond/bridge"),
        (lambda net: (net / "upper_eno4.90").mkdir(), "VLAN"),
        (None, "has addresses"),
    ],
)
def test_interface_in_use_is_not_touched(tmp_path, monkeypatch, no_wait, setup, why):
    device, net = _nic(tmp_path)
    fake = FakeIp(net, addresses="" if setup else "3: eno4 inet 10.0.0.2/24")
    if setup:
        setup(net)
    _with_ip(monkeypatch, fake)
    report = ExerciseReport()
    NetworkLink().run(device, report, _deadline())
    assert not any(c[:2] == ["link", "set"] for c in fake.calls)
    assert any(why in s for s in report.skipped)


def test_interface_left_up_is_reported(tmp_path, monkeypatch, no_wait):
    device, net = _nic(tmp_path)
    _with_ip(monkeypatch, FakeIp(net, stick_up=True))
    report = ExerciseReport()
    NetworkLink().run(device, report, _deadline())
    assert report.not_restored == ["eno4 left up"]


# --- runtime PM ----------------------------------------------------------


def _power(tmp_path, control="on", delay="100"):
    device = tmp_path / "dev0"
    power = device / "power"
    power.mkdir(parents=True)
    (device / "config").write_bytes(b"\x86\x80" + b"\0" * 62)
    (power / "control").write_text(control)
    (power / "autosuspend_delay_ms").write_text(delay)
    (power / "runtime_status").write_text("active")
    return device, power


def test_runtime_suspend_resume_restores_settings(tmp_path, no_wait, monkeypatch):
    device, power = _power(tmp_path)
    real_read = exercise._read

    def read(path):  # the kernel suspends once control is "auto"
        if path.name == "runtime_status" and real_read(power / "control") == "auto":
            return "suspended"
        return real_read(path)

    monkeypatch.setattr(exercise, "_read", read)
    report = ExerciseReport()
    RuntimePower().run(device, report, _deadline())
    assert report.ran == ["runtime suspend/resume"]
    assert (power / "control").read_text() == "on"
    assert (power / "autosuspend_delay_ms").read_text() == "100"
    assert not report.not_restored


def test_device_lost_after_resume_is_reported(tmp_path, no_wait):
    device, power = _power(tmp_path)
    (device / "config").write_bytes(b"\xff\xff" + b"\xff" * 62)  # gone (D3cold)
    report = ExerciseReport()
    RuntimePower().run(device, report, _deadline())
    assert any("did not resume" in n for n in report.not_restored)


def test_device_link_consumer_is_not_suspended(tmp_path, no_wait):
    device, power = _power(tmp_path)
    (device / "supplier:pci:0000:86:00.0").mkdir()
    report = ExerciseReport()
    RuntimePower().run(device, report, _deadline())
    assert any("supplier link" in s for s in report.skipped)
    assert (power / "control").read_text() == "on"


def test_device_that_never_suspends_is_skipped_and_restored(tmp_path, no_wait):
    device, power = _power(tmp_path, control="auto", delay="2000")
    report = ExerciseReport()
    RuntimePower().run(device, report, _deadline())
    assert any("did not suspend" in s for s in report.skipped)
    assert (power / "control").read_text() == "auto"
    assert (power / "autosuspend_delay_ms").read_text() == "2000"


# --- block and sensors --------------------------------------------------


def test_block_read_reads_only_disks_of_this_device(tmp_path, monkeypatch):
    device = tmp_path / "sys" / "dev0"
    disk = device / "nvme" / "nvme0" / "nvme0n1"
    disk.mkdir(parents=True)
    other = tmp_path / "sys" / "dev1" / "sda"
    other.mkdir(parents=True)
    block = tmp_path / "sys" / "block"
    block.mkdir()
    (block / "nvme0n1").symlink_to(disk)
    (block / "sda").symlink_to(other)
    dev = tmp_path / "dev"
    dev.mkdir()
    (dev / "nvme0n1").write_bytes(b"\0" * 8192)
    monkeypatch.setattr(exercise, "SYSFS_BLOCK", block)
    monkeypatch.setattr(exercise, "DEV_ROOT", dev)
    opened = []
    real_open = exercise.os.open

    def tracking_open(path, flags, *args):
        opened.append((str(path), flags))
        return real_open(path, flags, *args)

    monkeypatch.setattr(exercise.os, "open", tracking_open)
    report = ExerciseReport()
    BlockRead().run(device, report, _deadline())
    assert report.ran == ["block read nvme0n1"]
    assert all(path.endswith("nvme0n1") for path, _ in opened)
    assert all(
        not flags & (exercise.os.O_WRONLY | exercise.os.O_RDWR) for _, flags in opened
    )


def test_sensor_reads(tmp_path):
    device = tmp_path / "dev0"
    sensor = device / "hwmon" / "hwmon3"
    sensor.mkdir(parents=True)
    (sensor / "temp1_input").write_text("41000")
    report = ExerciseReport()
    SensorReads().run(device, report, _deadline())
    assert report.ran == ["sensor reads (1)"]


# --- selection ---------------------------------------------------------


class Recorder(exercise.Exerciser):
    def __init__(self, name, cls=None, boom=False):
        self.name, self.cls, self.boom, self.ran = name, cls, boom, False

    def applies(self, device, class_code):
        return self.cls is None or class_code >> 16 == self.cls

    def run(self, device, report, deadline):
        self.ran = True
        if self.boom:
            raise RuntimeError("broken workload")
        report.ran.append(self.name)


def test_exercisers_selected_by_class_and_isolated(tmp_path, no_wait):
    device = tmp_path / "dev0"
    device.mkdir()
    (device / "class").write_text("0x010802\n")  # NVMe
    net, block, broken, generic = (
        Recorder("net", 0x02),
        Recorder("block", 0x01),
        Recorder("broken", boom=True),
        Recorder("generic"),
    )
    report = device_exercise(device, (net, block, broken, generic))(_deadline())
    assert not net.ran and block.ran and generic.ran
    assert report.ran == ["block", "generic"]
    assert any("broken workload" in s for s in report.skipped)


def test_exercise_can_be_disabled_by_environment(monkeypatch):
    monkeypatch.setenv(exercise.EXERCISE_ENV, "0")
    assert not exercise.exercise_enabled()
    monkeypatch.delenv(exercise.EXERCISE_ENV)
    assert exercise.exercise_enabled()


# --- inside the capture -------------------------------------------------


def test_exercise_runs_inside_the_trace_and_reports(tmp_path, monkeypatch):
    from pcileechfwgenerator.host_collect import mmiotrace

    cpus = tmp_path / "cpu"
    cpus.mkdir()
    (cpus / "online").write_text("0\n")
    monkeypatch.setattr(mmiotrace, "CPU_ONLINE", cpus / "online")
    root = tmp_path / "tracing"
    root.mkdir()
    (root / "available_tracers").write_text("nop mmiotrace\n")
    (root / "set_event").write_text("")
    (root / "current_tracer").write_text("nop\n")
    (root / "tracing_on").write_text("1\n")
    (root / "trace_pipe").write_text(
        "MAP 0.1 1 0xf7e00000 0xffff 0x4000 0x0 0\n" "R 4 0.2 1 0xf7e00000 0x0 0x0 0\n"
    )
    seen = []

    def run_exercise(deadline):
        seen.append((root / "current_tracer").read_text().strip())
        report = ExerciseReport(
            ran=["fake"], skipped=["block read: no disk"], not_restored=["eno4 left up"]
        )
        return report

    result = mmiotrace.capture_native_probe(
        "0000:03:00.0",
        [mmiotrace.BarResource(bar_index=0, physical_start=0xF7E00000, size=0x4000)],
        duration_sec=0,
        tracefs=root,
        rebind=lambda after: None,
        journal_dir=tmp_path / "j",
        exercise=run_exercise,
    )
    assert seen == ["mmiotrace"]
    assert "exercised: fake" in result.notes
    assert result.host_changes == ["eno4 left up"]
    assert result.exercises_run == ["fake"]
    assert result.exercises_skipped == ["block read: no disk"]


def test_overrunning_exercise_cannot_keep_cpus_offline(tmp_path, monkeypatch):
    import threading

    from pcileechfwgenerator.host_collect import mmiotrace

    cpus = tmp_path / "cpu"
    cpus.mkdir()
    (cpus / "online").write_text("0\n")
    monkeypatch.setattr(mmiotrace, "CPU_ONLINE", cpus / "online")
    monkeypatch.setattr(mmiotrace, "EXERCISE_BUDGET_SEC", 0.1)
    monkeypatch.setattr(mmiotrace, "EXERCISE_GRACE_SEC", 0.1)
    root = tmp_path / "tracing"
    root.mkdir()
    (root / "available_tracers").write_text("nop mmiotrace\n")
    (root / "set_event").write_text("")
    (root / "current_tracer").write_text("nop\n")
    (root / "tracing_on").write_text("1\n")
    (root / "trace_pipe").write_text("MAP 0.1 1 0xf7e00000 0xffff 0x4000 0x0 0\n")
    release = threading.Event()
    tracer_when_done = []

    def stuck(deadline):
        release.wait(5)  # e.g. a read stuck in a disk timeout
        tracer_when_done.append((root / "current_tracer").read_text().strip())
        return ExerciseReport(ran=["late"])

    threading.Timer(0.6, release.set).start()
    # An undo that outlives every bounded wait is a restoration incident:
    # it blocks the build, and later captures refuse until it is checked.
    with pytest.raises(mmiotrace.HostRestorationError, match="did not finish"):
        mmiotrace.capture_native_probe(
            "0000:03:00.0",
            [
                mmiotrace.BarResource(
                    bar_index=0, physical_start=0xF7E00000, size=0x4000
                )
            ],
            duration_sec=0,
            tracefs=root,
            rebind=lambda after: None,
            journal_dir=tmp_path / "j",
            exercise=stuck,
        )
    assert (root / "current_tracer").read_text().strip() == "nop"
    journal = tmp_path / "j" / "tracer" / mmiotrace.TRACER_JOURNAL
    assert "incident" in journal.read_text()
    release.wait(5)
    deadline = time.monotonic() + 2
    while not tracer_when_done and time.monotonic() < deadline:
        time.sleep(0.05)
    assert tracer_when_done == ["nop"]  # tracer was stopped before it finished


def test_slow_exercise_undo_is_awaited_after_the_tracer_stops(tmp_path, monkeypatch):
    import threading

    from pcileechfwgenerator.host_collect import mmiotrace

    cpus = tmp_path / "cpu"
    cpus.mkdir()
    (cpus / "online").write_text("0\n")
    monkeypatch.setattr(mmiotrace, "CPU_ONLINE", cpus / "online")
    monkeypatch.setattr(mmiotrace, "EXERCISE_BUDGET_SEC", 0.2)
    monkeypatch.setattr(mmiotrace, "EXERCISE_GRACE_SEC", 0.0)
    root = tmp_path / "tracing"
    root.mkdir()
    (root / "available_tracers").write_text("nop mmiotrace\n")
    (root / "set_event").write_text("")
    (root / "current_tracer").write_text("nop\n")
    (root / "tracing_on").write_text("1\n")
    (root / "trace_pipe").write_text("MAP 0.1 1 0xf7e00000 0xffff 0x4000 0x0 0\n")

    def slow(deadline):
        threading.Event().wait(0.35)  # past the budget, within the final wait
        return ExerciseReport(ran=["slow"])

    result = mmiotrace.capture_native_probe(
        "0000:03:00.0",
        [mmiotrace.BarResource(bar_index=0, physical_start=0xF7E00000, size=0x4000)],
        duration_sec=0,
        tracefs=root,
        rebind=lambda after: None,
        journal_dir=tmp_path / "j",
        exercise=slow,
    )
    assert any("exceeded its budget" in n for n in result.notes)
    assert result.exercises_run == ["slow"]
    assert not (tmp_path / "j" / "tracer" / mmiotrace.TRACER_JOURNAL).exists()


def test_networkd_pending_state_is_waited_out(tmp_path, monkeypatch, no_wait):
    """A re-probed netdev is 'pending' until networkd decides; unmanaged runs."""
    import threading

    device, net = _nic(tmp_path)
    links = tmp_path / "networkd"
    links.mkdir()
    (links / "3").write_text("ADMIN_STATE=pending\n")
    threading.Timer(
        0.3, lambda: (links / "3").write_text("ADMIN_STATE=unmanaged\n")
    ).start()
    fake = FakeIp(net)
    _with_ip(monkeypatch, fake)
    report = ExerciseReport()
    NetworkLink().run(device, report, _deadline())
    assert ["link", "set", "dev", "eno4", "up"] in fake.calls


def test_runtime_pm_without_autosuspend_still_runs(tmp_path, no_wait, monkeypatch):
    device, power = _power(tmp_path)
    (power / "autosuspend_delay_ms").unlink()
    (power / "autosuspend_delay_ms").mkdir()  # reads fail like the kernel's EIO
    real_read = exercise._read

    def read(path):
        if path.name == "runtime_status" and real_read(power / "control") == "auto":
            return "suspended"
        return real_read(path)

    monkeypatch.setattr(exercise, "_read", read)
    report = ExerciseReport()
    RuntimePower().run(device, report, _deadline())
    assert report.ran == ["runtime suspend/resume"] and not report.not_restored
    assert (power / "control").read_text() == "on"


def test_networkd_without_a_decision_is_skipped(tmp_path, monkeypatch, no_wait):
    """networkd running but no state file yet: unknown, so no link-up."""
    monkeypatch.setattr(exercise, "NETWORKD_SETTLE_SEC", 0.2)
    device, net = _nic(tmp_path)
    (tmp_path / "networkd").mkdir()
    fake = FakeIp(net)
    _with_ip(monkeypatch, fake)
    report = ExerciseReport()
    NetworkLink().run(device, report, _deadline())
    assert not any(c[:2] == ["link", "set"] for c in fake.calls)
    assert any("systemd-networkd" in s for s in report.skipped)


def test_unanswered_nmcli_is_treated_as_managed(tmp_path, monkeypatch, no_wait):
    device, net = _nic(tmp_path)
    fake = FakeIp(net)
    monkeypatch.setattr(exercise.shutil, "which", lambda name: f"/usr/bin/{name}")

    def run(argv, **kwargs):
        if argv[0].endswith("nmcli"):
            import subprocess

            raise subprocess.TimeoutExpired(argv, 5)
        return fake(argv, **kwargs)

    monkeypatch.setattr(exercise.subprocess, "run", run)
    report = ExerciseReport()
    NetworkLink().run(device, report, _deadline())
    assert not any(c[:2] == ["link", "set"] for c in fake.calls)
    assert any("NetworkManager (state unknown)" in s for s in report.skipped)
