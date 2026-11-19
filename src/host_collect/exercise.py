"""Reversible driver workloads run while the native driver is being traced.

A probe alone shows only the driver's initialization. Most runtime
programming (rings, interrupt enables, link setup, command queues, power
transitions) happens when the device is *used*. Each exerciser makes the
driver do that work through its normal interfaces, then undoes what it
changed, all inside the trace window so the undo is observed too.

Every exerciser checks its own safety preconditions first and skips with a
note when they do not hold. Nothing here writes to the device directly or
writes any data to storage. Selected by PCI class; the generic ones apply to
every device. Disabled with ``--no-mmio-exercise`` (environment variable
``PCILEECH_MMIO_EXERCISE=0``).
"""

from __future__ import annotations

import os
import shutil

# argv-list invocations of ip(8) and udevadm only.
import subprocess  # nosec B404
import time
from collections.abc import Callable
from dataclasses import dataclass, field
from pathlib import Path

from pcileechfwgenerator.log_config import get_logger
from pcileechfwgenerator.string_utils import (
    log_info_safe,
    log_warning_safe,
    safe_format,
)

logger = get_logger(__name__)

EXERCISE_ENV = "PCILEECH_MMIO_EXERCISE"
SYSFS_BLOCK = Path("/sys/block")
DEV_ROOT = Path("/dev")
IFF_UP = 0x1
PROC_SYS_NET = Path("/proc/sys/net")
NETWORKD_LINKS = Path("/run/systemd/netif/links")
COMMAND_SEC = 5.0
NETWORKD_SETTLE_SEC = 5.0
# Per-exerciser time budget; the whole run is also bounded by the caller.
LINK_SETTLE_SEC = 3.0
SUSPEND_WAIT_SEC = 3.0
RESUME_WAIT_SEC = 3.0
BLOCK_READ_BYTES = 1 << 20
BLOCK_READ_POINTS = 4


def exercise_enabled() -> bool:
    return os.environ.get(EXERCISE_ENV, "1").strip().lower() not in (
        "0",
        "false",
        "no",
        "off",
    )


@dataclass
class ExerciseReport:
    """What ran, what was skipped (and why), and anything left changed."""

    ran: list[str] = field(default_factory=list)
    skipped: list[str] = field(default_factory=list)
    not_restored: list[str] = field(default_factory=list)

    def notes(self) -> list[str]:
        return (
            [f"exercised: {item}" for item in self.ran]
            + [f"exercise skipped: {item}" for item in self.skipped]
            + [f"exercise NOT RESTORED: {item}" for item in self.not_restored]
        )


class Exerciser:
    """One reversible workload. ``run`` must leave the host as it found it,
    or record what it could not restore in ``report.not_restored``."""

    name = "exerciser"

    def applies(self, device: Path, class_code: int) -> bool:
        raise NotImplementedError

    def run(self, device: Path, report: ExerciseReport, deadline: float) -> None:
        raise NotImplementedError


def _read(path: Path) -> str:
    return path.read_text().strip()


def _settle_udev() -> None:
    """Wait (bounded) for interface renames / block nodes after a probe."""
    udevadm = shutil.which("udevadm")
    if udevadm:
        _run([udevadm, "settle", "--timeout=3"])


class NetworkLink(Exerciser):
    """Bring an unused interface up (driver open path), then down again.

    Link-up can trigger autoconfiguration. IPv6 is disabled on the interface
    for the duration (no link-local DAD, router solicitation or SLAAC), and
    interfaces a network manager may act on are skipped. Addresses and routes
    are compared with a pre-up snapshot afterwards; anything new is flushed,
    and what cannot be removed is reported.
    """

    name = "network link up/down"

    def applies(self, device: Path, class_code: int) -> bool:
        return class_code >> 16 == 0x02

    def run(self, device: Path, report: ExerciseReport, deadline: float) -> None:
        ip = shutil.which("ip")
        if not ip:
            report.skipped.append(f"{self.name}: ip(8) not found")
            return
        interfaces = sorted((device / "net").glob("*"))
        if not interfaces:
            report.skipped.append(f"{self.name}: no network interface")
            return
        for net in interfaces:
            if time.monotonic() >= deadline:
                report.skipped.append(f"{self.name} {net.name}: out of time")
                continue
            why = self._unsafe(net, ip)
            if why:
                report.skipped.append(f"{self.name} {net.name}: {why}")
                continue
            self._cycle(net, ip, report, deadline)

    def _cycle(
        self, net: Path, ip: str, report: ExerciseReport, deadline: float
    ) -> None:
        name = net.name
        before = self._snapshot(ip, name)
        ipv6 = PROC_SYS_NET / "ipv6" / "conf" / name / "disable_ipv6"
        ipv6_was = None
        try:
            if ipv6.exists():
                ipv6_was = ipv6.read_text().strip()
                ipv6.write_text("1")
            up = _run([ip, "link", "set", "dev", name, "up"])
            if up is None or up.returncode != 0:
                report.skipped.append(f"{self.name} {name}: link up failed")
            else:
                end = min(deadline, time.monotonic() + LINK_SETTLE_SEC)
                while time.monotonic() < end:
                    time.sleep(0.2)
                # Statistics reads go through the driver's stats callback.
                for stat in sorted((net / "statistics").glob("*")):
                    try:
                        stat.read_text()
                    except OSError:
                        pass
                report.ran.append(f"{self.name} {name}")
        finally:
            down = _run([ip, "link", "set", "dev", name, "down"])
            if down is None or down.returncode != 0:
                report.not_restored.append(f"{name}: link down failed")
            try:
                if int(_read(net / "flags"), 16) & IFF_UP:
                    report.not_restored.append(f"{name} left up")
            except (OSError, ValueError) as error:
                report.not_restored.append(f"{name}: state unverifiable: {error}")
            after = self._snapshot(ip, name)
            if after is None or before is None or after != before:
                _run([ip, "addr", "flush", "dev", name])
                _run([ip, "route", "flush", "dev", name])
                if self._snapshot(ip, name) != before:
                    report.not_restored.append(
                        f"{name}: addresses/routes differ from before"
                    )
            if ipv6_was is not None:
                try:
                    ipv6.write_text(ipv6_was)
                except OSError as error:
                    report.not_restored.append(
                        f"{name}: disable_ipv6={ipv6_was} not restored: {error}"
                    )

    @staticmethod
    def _snapshot(ip: str, name: str) -> tuple[str, str] | None:
        addrs = _run([ip, "-o", "addr", "show", "dev", name])
        routes = _run([ip, "-o", "route", "show", "dev", name])
        if addrs is None or routes is None or addrs.returncode or routes.returncode:
            return None
        return addrs.stdout.strip(), routes.stdout.strip()

    @staticmethod
    def _unsafe(net: Path, ip: str) -> str | None:
        try:
            if int(_read(net / "flags"), 16) & IFF_UP:
                return "already up (in use)"
        except (OSError, ValueError) as error:
            return f"cannot read flags: {error}"
        if (net / "master").exists() or any(net.glob("upper_*")):
            return "part of a bond/bridge/VLAN"
        shown = _run([ip, "-o", "addr", "show", "dev", net.name])
        if shown is None or shown.returncode != 0:
            return "cannot list addresses"
        if shown.stdout.strip():
            return "has addresses configured"
        manager = _managed_by(net)
        if manager:
            return f"managed by {manager} (link-up could start DHCP)"
        return None


def _run(argv: list[str]):
    """Bounded argv-list command; None when it could not run or timed out."""
    try:
        return subprocess.run(  # nosec B603
            argv, check=False, capture_output=True, text=True, timeout=COMMAND_SEC
        )
    except (OSError, subprocess.SubprocessError):
        return None


def _managed_by(net: Path) -> str | None:
    """A network manager that would configure this interface on link-up."""
    try:
        ifindex = _read(net / "ifindex")
    except OSError:
        return "unknown (ifindex unreadable)"
    networkd = NETWORKD_LINKS / ifindex
    if NETWORKD_LINKS.is_dir():
        # networkd is running. A freshly probed netdev has no state file, or
        # is "pending"/"initialized", until networkd matches it against its
        # configuration; wait for the decision, and skip if none comes.
        end = time.monotonic() + NETWORKD_SETTLE_SEC
        while True:
            try:
                text = networkd.read_text()
            except FileNotFoundError:
                text = ""
            except OSError:
                return "systemd-networkd (state unreadable)"
            state = next(
                (
                    line.split("=", 1)[1].strip()
                    for line in text.splitlines()
                    if line.startswith("ADMIN_STATE=")
                ),
                "",
            )
            if state not in ("pending", "initialized", "") or time.monotonic() >= end:
                break
            time.sleep(0.2)
        if state != "unmanaged":
            return f"systemd-networkd (admin state {state or 'unknown'})"
    nmcli = shutil.which("nmcli")
    if nmcli:
        shown = _run([nmcli, "-t", "-f", "GENERAL.STATE", "device", "show", net.name])
        if shown is None or shown.returncode != 0:
            # NetworkManager may be running and simply not answering yet.
            return "NetworkManager (state unknown)"
        if "unmanaged" not in shown.stdout:
            return "NetworkManager"
    return None


class BlockRead(Exerciser):
    """Read a few spread chunks from each disk (I/O submission path).

    Read-only, page cache bypassed where possible. The rebind refuses any
    device with a claimed disk, so these disks are unused.
    """

    name = "block read"

    def applies(self, device: Path, class_code: int) -> bool:
        return class_code >> 16 == 0x01

    def run(self, device: Path, report: ExerciseReport, deadline: float) -> None:
        disks = self._disks(device)
        if not disks:
            report.skipped.append(f"{self.name}: no disk appeared")
            return
        for disk in disks:
            if time.monotonic() >= deadline:
                report.skipped.append(f"{self.name} {disk}: out of time")
                continue
            claimed = self._claimed(disk)
            if claimed:
                # udev auto-activation (md, LVM) claimed it after the rebind.
                report.skipped.append(f"{self.name} {disk}: {claimed}")
                report.not_restored.append(
                    f"{disk} was claimed after the rebind ({claimed})"
                )
                continue
            try:
                self._read_points(disk)
                report.ran.append(f"{self.name} {disk}")
            except OSError as error:
                report.skipped.append(f"{self.name} {disk}: {error}")

    @staticmethod
    def _disks(device: Path) -> list[str]:
        root = device.resolve()
        found = []
        try:
            for entry in sorted(SYSFS_BLOCK.iterdir()):
                paths = [entry, *(entry / "multipath").glob("*")]
                if any(root in p.resolve().parents for p in paths):
                    found.append(entry.name)
        except OSError:
            pass
        return found

    @staticmethod
    def _claimed(name: str) -> str | None:
        import errno

        try:
            fd = os.open(DEV_ROOT / name, os.O_RDONLY | os.O_EXCL | os.O_NONBLOCK)
        except OSError as error:
            if error.errno == errno.EBUSY:
                return "claimed by another user"
            return f"cannot open: {error}"
        os.close(fd)
        return None

    @staticmethod
    def _read_points(name: str) -> None:
        import mmap

        path = DEV_ROOT / name
        flags = os.O_RDONLY | getattr(os, "O_DIRECT", 0)
        try:
            fd = os.open(path, flags)
        except OSError:
            fd = os.open(path, os.O_RDONLY)
        try:
            size = os.lseek(fd, 0, os.SEEK_END)
            buffer = mmap.mmap(-1, BLOCK_READ_BYTES)  # page-aligned for O_DIRECT
            try:
                span = max(size - BLOCK_READ_BYTES, 0)
                for i in range(BLOCK_READ_POINTS):
                    offset = (span * i // max(BLOCK_READ_POINTS - 1, 1)) & ~0xFFF
                    os.preadv(fd, [buffer], offset)
            finally:
                buffer.close()
        finally:
            os.close(fd)


class RuntimePower(Exerciser):
    """Runtime-suspend and resume the device (driver PM callbacks).

    Skipped for device-link consumers: suspending one can take its supplier
    (e.g. a GPU behind its HDMI audio function) down with it. After resume
    the device must report active and answer config reads.
    """

    name = "runtime suspend/resume"

    def applies(self, device: Path, class_code: int) -> bool:
        return (device / "power" / "control").exists()

    def run(self, device: Path, report: ExerciseReport, deadline: float) -> None:
        if any(device.glob("supplier:*")):
            report.skipped.append(f"{self.name}: device has a supplier link")
            return
        power = device / "power"
        delay_file = power / "autosuspend_delay_ms"
        try:
            control = _read(power / "control")
        except OSError as error:
            report.skipped.append(f"{self.name}: {error}")
            return
        try:
            delay = _read(delay_file) if delay_file.exists() else None
        except OSError:
            # The kernel returns EIO here when the driver does not use
            # autosuspend; it then suspends as soon as it is idle.
            delay = None
        try:
            if delay is not None:
                delay_file.write_text("0")
            (power / "control").write_text("auto")
            end = min(deadline, time.monotonic() + SUSPEND_WAIT_SEC)
            suspended = False
            while time.monotonic() < end:
                if _read(power / "runtime_status") == "suspended":
                    suspended = True
                    break
                time.sleep(0.1)
            (power / "control").write_text("on")  # forces a resume
            if suspended:
                report.ran.append(self.name)
            else:
                report.skipped.append(f"{self.name}: device did not suspend")
        except OSError as error:
            report.skipped.append(f"{self.name}: {error}")
        finally:
            # Delay first: restoring control=auto with delay 0 would suspend
            # again at once.
            for name, value in (("autosuspend_delay_ms", delay), ("control", control)):
                if value is None:
                    continue
                try:
                    (power / name).write_text(value)
                    if _read(power / name) != value:
                        raise OSError("value did not stick")
                except OSError as error:
                    report.not_restored.append(f"power/{name}={value}: {error}")
            if not self._responsive(device, power):
                report.not_restored.append(
                    "device did not resume (config space unreadable or not active)"
                )

    @staticmethod
    def _responsive(device: Path, power: Path) -> bool:
        end = time.monotonic() + RESUME_WAIT_SEC
        while True:
            try:
                status = _read(power / "runtime_status")
                with (device / "config").open("rb") as stream:
                    vendor = stream.read(2)
            except OSError:
                status, vendor = "", b""
            # A control=auto device may legitimately be suspended again.
            alive = vendor not in (b"", b"\xff\xff")
            if alive and status in ("active", "suspended", "unsupported"):
                return True
            if time.monotonic() >= end:
                return False
            time.sleep(0.1)


class SensorReads(Exerciser):
    """Read the device's own hwmon sensors (driver register/command reads)."""

    name = "sensor reads"

    def applies(self, device: Path, class_code: int) -> bool:
        return True

    def run(self, device: Path, report: ExerciseReport, deadline: float) -> None:
        inputs = sorted(device.glob("**/hwmon/hwmon*/*_input"))
        if not inputs:
            report.skipped.append(f"{self.name}: no hwmon sensors")
            return
        for sensor in inputs[:64]:
            try:
                sensor.read_text()
            except OSError:
                pass
        report.ran.append(f"{self.name} ({len(inputs[:64])})")


# Order matters: use the device first, power-cycle it last.
EXERCISERS: tuple[Exerciser, ...] = (
    NetworkLink(),
    BlockRead(),
    SensorReads(),
    RuntimePower(),
)


def device_exercise(
    device: Path, exercisers: tuple[Exerciser, ...] = EXERCISERS
) -> Callable[[float], ExerciseReport]:
    """Callback run inside the trace window, after the probe; ``deadline`` is
    a ``time.monotonic()`` bound for the whole run."""

    def run(deadline: float) -> ExerciseReport:
        report = ExerciseReport()
        try:
            class_code = int(_read(device / "class"), 16)
        except (OSError, ValueError) as error:
            report.skipped.append(f"device class unreadable: {error}")
            return report
        try:
            _settle_udev()
        except Exception as error:  # never lose the trace over udev
            report.skipped.append(f"udev settle: {error}")
        for exerciser in exercisers:
            if not exerciser.applies(device, class_code):
                continue
            if time.monotonic() >= deadline:
                report.skipped.append(f"{exerciser.name}: out of time")
                continue
            try:
                exerciser.run(device, report, deadline)
            except Exception as error:  # one failing workload never stops capture
                report.skipped.append(f"{exerciser.name}: {error}")
        for item in report.not_restored:
            log_warning_safe(
                logger,
                safe_format("Exercise left the host changed: {item}", item=item),
                prefix="MMIO",
            )
        log_info_safe(
            logger,
            safe_format(
                "Exercise: ran {ran}; skipped {skipped}",
                ran=", ".join(report.ran) or "nothing",
                skipped=len(report.skipped),
            ),
            prefix="MMIO",
        )
        return report

    return run
