#!/usr/bin/env python3
"""MMIO trace capture for learning BAR register maps dynamically.

This module captures MMIO read/write operations during device driver probe
using eBPF (bpftrace) to build device-specific register models.
"""

import errno
import math
import os
import re
import shutil
import signal
import stat
import subprocess
import tempfile
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Callable, List, NoReturn, Optional

from pcileechfwgenerator.host_collect.register_observer import (
    READY_MARKER,
    BarResource,
    ObserverResult,
    parse_trace_records,
)
from pcileechfwgenerator.log_config import get_logger
from pcileechfwgenerator.string_utils import (
    log_debug_safe,
    log_error_safe,
    log_info_safe,
    safe_format,
)

logger = get_logger(__name__)


@dataclass
class MmioAccess:
    """Single MMIO read or write operation."""

    timestamp_ns: int
    operation: str  # "R" or "W"
    width: int  # 1, 2, or 4 bytes
    offset: int  # BAR-relative offset
    value: Optional[int] = None  # Value read or written
    completion: str = "ok"
    absent_reason: Optional[str] = None
    duration_ns: Optional[int] = None


@dataclass
class MmioTrace:
    """Complete MMIO trace for a single BAR."""

    bar_index: int
    bar_base_addr: int
    bar_size: int
    accesses: List[MmioAccess] = field(default_factory=list)
    duration_ns: int = 0
    dropped_events: int = 0
    unattributed_accesses: int = 0
    observer_result: Optional[ObserverResult] = None
    coverage_notes: List[str] = field(default_factory=list)


class MmioTracer:
    """Capture MMIO traffic during device operations using eBPF."""

    # Entry arguments are saved by tid and nesting depth, never read at return.
    BPFTRACE_SCRIPT = r"""
BEGIN { printf("REGISTER_OBSERVER_V1\n"); }

kprobe:ioread8
{
  @rd1[tid] = @rd1[tid] + 1;
  $d = @rd1[tid];
  @ra1[tid, $d] = arg0;
  @ri1[tid, $d] = nsecs;
  printf("read_enter,%llu,%llu,1,0x%llx,,,,%llu,\n",
         tid, @ri1[tid, $d], @ra1[tid, $d], @ri1[tid, $d]);
}
kretprobe:ioread8
{
  $d = @rd1[tid];
  printf("read_return,%llu,%llu,1,0x%llx,%llu,,,%llu,ok\n",
         tid, nsecs, @ra1[tid, $d], (uint64)(uint8)retval, @ri1[tid, $d]);
  delete(@ra1[tid, $d]);
  delete(@ri1[tid, $d]);
  if ($d > 1) { @rd1[tid] = $d - 1; } else { delete(@rd1[tid]); }
}
kprobe:iowrite8
{
  printf("write,%llu,%llu,1,0x%llx,%llu,,,,\n",
         tid, nsecs, arg1, (uint64)(uint8)arg0);
}

kprobe:ioread16
{
  @rd2[tid] = @rd2[tid] + 1;
  $d = @rd2[tid];
  @ra2[tid, $d] = arg0;
  @ri2[tid, $d] = nsecs;
  printf("read_enter,%llu,%llu,2,0x%llx,,,,%llu,\n",
         tid, @ri2[tid, $d], @ra2[tid, $d], @ri2[tid, $d]);
}
kretprobe:ioread16
{
  $d = @rd2[tid];
  printf("read_return,%llu,%llu,2,0x%llx,%llu,,,%llu,ok\n",
         tid, nsecs, @ra2[tid, $d], (uint64)(uint16)retval, @ri2[tid, $d]);
  delete(@ra2[tid, $d]);
  delete(@ri2[tid, $d]);
  if ($d > 1) { @rd2[tid] = $d - 1; } else { delete(@rd2[tid]); }
}
kprobe:iowrite16
{
  printf("write,%llu,%llu,2,0x%llx,%llu,,,,\n",
         tid, nsecs, arg1, (uint64)(uint16)arg0);
}

kprobe:ioread32
{
  @rd4[tid] = @rd4[tid] + 1;
  $d = @rd4[tid];
  @ra4[tid, $d] = arg0;
  @ri4[tid, $d] = nsecs;
  printf("read_enter,%llu,%llu,4,0x%llx,,,,%llu,\n",
         tid, @ri4[tid, $d], @ra4[tid, $d], @ri4[tid, $d]);
}
kretprobe:ioread32
{
  $d = @rd4[tid];
  printf("read_return,%llu,%llu,4,0x%llx,%llu,,,%llu,ok\n",
         tid, nsecs, @ra4[tid, $d], (uint64)(uint32)retval, @ri4[tid, $d]);
  delete(@ra4[tid, $d]);
  delete(@ri4[tid, $d]);
  if ($d > 1) { @rd4[tid] = $d - 1; } else { delete(@rd4[tid]); }
}
kprobe:iowrite32
{
  printf("write,%llu,%llu,4,0x%llx,%llu,,,,\n",
         tid, nsecs, arg1, (uint64)(uint32)arg0);
}

// This backend supports the ioremap/iounmap path only. Other mapping APIs
// and mappings that predate attachment remain unattributed.
kprobe:ioremap
{
  @md[tid] = @md[tid] + 1;
  $d = @md[tid];
  @mp[tid, $d] = arg0;
  @ms[tid, $d] = arg1;
}
kretprobe:ioremap
{
  $d = @md[tid];
  if ($d > 0 && retval != 0) {
    printf("map,%llu,%llu,,0x%llx,,0x%llx,%llu,,\n",
           tid, nsecs, retval, @mp[tid, $d], @ms[tid, $d]);
  }
  delete(@mp[tid, $d]);
  delete(@ms[tid, $d]);
  if ($d > 1) { @md[tid] = $d - 1; } else { delete(@md[tid]); }
}
kprobe:iounmap
{
  printf("unmap,%llu,%llu,,0x%llx,,,,,\n", tid, nsecs, arg0);
}
END {
  clear(@rd1); clear(@ra1); clear(@ri1);
  clear(@rd2); clear(@ra2); clear(@ri2);
  clear(@rd4); clear(@ra4); clear(@ri4);
  clear(@md); clear(@mp); clear(@ms);
}
"""

    READY_TIMEOUT_SEC = 10.0

    def __init__(self, device_bdf: str):
        """Initialize MMIO tracer for a specific device.

        Args:
            device_bdf: PCI device BDF (e.g., "0000:03:00.0")

        Raises:
            PermissionError: If not running with sufficient privileges
            RuntimeError: If bpftrace is not available
        """
        self.device_bdf = device_bdf
        self._check_privileges()
        self._check_bpftrace()

    def _check_privileges(self) -> None:
        """Verify we have required privileges for eBPF tracing."""
        if os.geteuid() != 0:
            log_error_safe(
                logger,
                "MMIO trace capture requires root privileges (eBPF/bpftrace access)",
            )
            raise PermissionError(
                "Run with sudo or grant CAP_BPF+CAP_PERFMON capabilities"
            )

    def _check_bpftrace(self) -> None:
        """Verify bpftrace is available."""
        if not shutil.which("bpftrace"):
            log_error_safe(
                logger,
                "bpftrace not found; install via: apt install bpftrace (Debian/Ubuntu) "
                "or dnf install bpftrace (Fedora/RHEL)",
            )
            raise RuntimeError("bpftrace required for MMIO trace capture")

    def _get_bar_info(self, bar_index: int) -> tuple[int, int]:
        """Get BAR base address and size from sysfs.

        Args:
            bar_index: BAR index (0-5)

        Returns:
            Tuple of (base_address, size)

        Raises:
            RuntimeError: If BAR info cannot be read
        """
        if not 0 <= bar_index <= 5:
            raise ValueError("bar_index must be between 0 and 5")
        resource_file = Path(f"/sys/bus/pci/devices/{self.device_bdf}/resource")
        if not resource_file.exists():
            raise RuntimeError(
                safe_format("Device {bdf} not found in sysfs", bdf=self.device_bdf)
            )

        try:
            with open(resource_file, "r") as f:
                lines = f.readlines()
                if bar_index >= len(lines):
                    raise RuntimeError(
                        safe_format("BAR{idx} not present", idx=bar_index)
                    )

                line = lines[bar_index].strip()
                parts = line.split()
                if len(parts) < 3:
                    raise RuntimeError(
                        safe_format("Invalid resource line for BAR{idx}", idx=bar_index)
                    )

                start = int(parts[0], 16)
                end = int(parts[1], 16)
                flags = int(parts[2], 16)
                if flags & 0x100:  # IORESOURCE_IO: not a memory-mapped BAR.
                    raise RuntimeError("I/O-port BAR tracing is unsupported")
                size = end - start + 1 if end > start else 0

                return start, size

        except (IOError, ValueError) as e:
            raise RuntimeError(
                safe_format(
                    "Failed to read BAR{idx} info: {err}", idx=bar_index, err=str(e)
                )
            )

    def capture_probe_trace(
        self,
        bar_index: int,
        duration_sec: float = 5.0,
        trigger_rebind: bool = False,
    ) -> MmioTrace:
        """Capture MMIO trace during device operation.

        Args:
            bar_index: Which BAR to trace (0-5)
            duration_sec: How long to capture (seconds)
            trigger_rebind: If True, unbind/rebind driver to trigger probe

        Returns:
            MmioTrace with captured accesses

        Raises:
            RuntimeError: If trace capture fails
        """
        if not math.isfinite(duration_sec) or duration_sec <= 0:
            raise ValueError("duration_sec must be finite and positive")
        bar_base, bar_size = self._get_bar_info(bar_index)

        if bar_size == 0:
            log_info_safe(
                logger,
                safe_format("BAR{idx} has zero size, skipping trace", idx=bar_index),
                prefix="MMIO",
            )
            return MmioTrace(
                bar_index=bar_index,
                bar_base_addr=bar_base,
                bar_size=bar_size,
                accesses=[],
            )

        log_info_safe(
            logger,
            safe_format(
                "Capturing MMIO trace for BAR{idx} (base=0x{base:X}, size=0x{size:X})",
                idx=bar_index,
                base=bar_base,
                size=bar_size,
            ),
            prefix="MMIO",
        )

        # File-backed stdout/stderr cannot deadlock when either pipe fills.
        # The temporary directory and all file handles survive until child reap.
        with tempfile.TemporaryDirectory(prefix="pcileech-observer-") as directory:
            script_path = Path(directory) / "capture.bt"
            output_path = Path(directory) / "trace.csv"
            error_path = Path(directory) / "stderr.txt"
            script_path.write_text(self.BPFTRACE_SCRIPT)
            with output_path.open("w") as output, error_path.open("w+") as errors:
                proc = subprocess.Popen(
                    ["bpftrace", "-q", "-B", "line", str(script_path)],
                    stdout=output,
                    stderr=errors,
                    text=True,
                )
                try:
                    deadline = time.monotonic() + self.READY_TIMEOUT_SEC
                    with output_path.open() as reader:
                        ready = False
                        while not ready:
                            if proc.poll() is not None:
                                self._raise_collector_error(
                                    proc, error_path, "exited before readiness"
                                )
                            ready = any(line.strip() == READY_MARKER for line in reader)
                            if not ready:
                                if time.monotonic() >= deadline:
                                    raise RuntimeError("bpftrace readiness timeout")
                                time.sleep(0.01)
                    self._collector_losses(proc, error_path)
                    start_time = time.monotonic_ns()
                    if trigger_rebind:
                        self._trigger_driver_rebind()
                    deadline = time.monotonic() + duration_sec
                    while time.monotonic() < deadline:
                        if proc.poll() is not None:
                            self._raise_collector_error(
                                proc, error_path, "exited during capture"
                            )
                        time.sleep(min(0.05, max(0, deadline - time.monotonic())))
                    if proc.poll() is not None:
                        self._raise_collector_error(
                            proc, error_path, "exited during capture"
                        )
                    forced = self._stop_collector(proc)
                    end_time = time.monotonic_ns()
                    if forced or proc.returncode not in (0, -signal.SIGTERM):
                        self._raise_collector_error(
                            proc, error_path, "failed to stop cleanly"
                        )
                    # Missing symbols/partial attachment fail explicitly. Loss
                    # diagnostics without an ordered position discard evidence.
                    stderr_losses = self._collector_losses(proc, error_path)
                    with output_path.open() as stream:
                        result = parse_trace_records(
                            stream,
                            [
                                BarResource(
                                    bar_index=bar_index,
                                    physical_start=bar_base,
                                    size=bar_size,
                                )
                            ],
                        )
                    if stderr_losses:
                        result.lost_events += stderr_losses
                        result.discarded_observations += len(result.observations)
                        result.observations.clear()
                    return MmioTrace(
                        bar_index=bar_index,
                        bar_base_addr=bar_base,
                        bar_size=bar_size,
                        accesses=self._compat_accesses(result),
                        duration_ns=end_time - start_time,
                        dropped_events=result.dropped_events,
                        unattributed_accesses=result.unattributed_accesses,
                        observer_result=result,
                        coverage_notes=[
                            "Only ioread/iowrite 8/16/32 and ioremap/iounmap "
                            "are instrumented.",
                            "Pre-existing mappings, other mapping APIs, "
                            "inline/raw/relaxed accessors and I/O-port BARs "
                            "are unsupported; coverage is partial.",
                            "Writes are attempts; physical completion and "
                            "writability are unknown.",
                            "A triggered rebind traces probe() only; driver "
                            "setup deferred to work queues may fall outside "
                            "the capture window.",
                            "Unordered stderr loss markers discard all observations.",
                        ],
                    )
                finally:
                    self._stop_collector(proc)

    @staticmethod
    def _stop_collector(proc: subprocess.Popen) -> bool:
        """Terminate and reap, escalating to kill after a bounded wait."""
        if proc.poll() is None:
            try:
                proc.terminate()
            except ProcessLookupError:
                pass  # Exited between poll and signal; still reap below.
        try:
            proc.wait(timeout=5.0)
            return False
        except subprocess.TimeoutExpired:
            proc.kill()
            proc.wait()
            return True

    @staticmethod
    def _collector_losses(proc: subprocess.Popen, error_path: Path) -> int:
        """Count recognizable losses; every other diagnostic is a capture error."""
        count = 0
        for line in error_path.read_text().splitlines():
            if not line.strip():
                continue
            match = re.fullmatch(
                r"Lost ([1-9][0-9]*) events(?:\s.*)?", line.strip(), re.I
            )
            if match is None:
                MmioTracer._raise_collector_error(
                    proc, error_path, "collector diagnostics"
                )
            count += int(match[1])
        return count

    @staticmethod
    def _raise_collector_error(
        proc: subprocess.Popen, error_path: Path, reason: str
    ) -> NoReturn:
        raise RuntimeError(
            safe_format(
                "bpftrace {reason} (exit={code}): {stderr}",
                reason=reason,
                code=proc.poll(),
                stderr=error_path.read_text().strip(),
            )
        )

    @staticmethod
    def _compat_accesses(result: ObserverResult) -> List[MmioAccess]:
        return [
            MmioAccess(
                timestamp_ns=o.monotonic_ns,
                operation="R" if o.operation == "read" else "W",
                width=o.ref.width,
                offset=o.ref.offset,
                value=o.returned_value if o.operation == "read" else o.attempted_value,
                completion=o.completion,
                absent_reason=o.absent_reason,
                duration_ns=o.duration_ns,
            )
            for o in result.observations
        ]

    def _parse_trace_output(
        self, output_path: str, bar_base: int, bar_size: int
    ) -> List[MmioAccess]:
        """Compatibility adapter; only mapped accesses become BAR offsets.

        File errors propagate. Callers needing integrity counts should use the
        observer_result on capture_probe_trace's MmioTrace or the pure parser.
        """
        with open(output_path) as stream:
            result = parse_trace_records(
                stream,
                [
                    BarResource(
                        bar_index=0,
                        physical_start=bar_base,
                        size=bar_size,
                    )
                ],
            )
        return self._compat_accesses(result)

    # --- sysfs primitives (roots overridable in tests) ----------------------

    SYSFS_ROOT = Path("/sys")
    PROC_ROOT = Path("/proc")
    DEV_ROOT = Path("/dev")
    EPROBE_DEFER = 517
    DEFERRED_PROBE_WAIT_SEC = 5.0

    def _device_dir(self) -> Path:
        return self.SYSFS_ROOT / "bus" / "pci" / "devices" / self.device_bdf

    def _current_driver(self) -> Optional[str]:
        driver_path = self._device_dir() / "driver"
        if not driver_path.is_symlink() and not driver_path.exists():
            return None
        return Path(os.readlink(driver_path)).name

    def _read_text(self, path: Path) -> str:
        try:
            return path.read_text().strip()
        except OSError:
            return ""

    def _sysfs_write(self, path: str, value: str) -> None:
        with open(path, "w") as stream:
            stream.write(value)

    def _in_use_reasons(self, driver: str) -> List[str]:
        """Why unbinding this device would break the host; empty when safe.

        The kernel never refuses an unbind of a busy device: remove() tears
        it down. Rebinding restores the driver, not mounts, addresses, VFs or
        display state, so any of those makes the rebind unsafe.
        """
        device = self._device_dir()
        reasons: List[str] = []

        def state(path: Path) -> str:
            """A safety file; absent is normal, unreadable is unverifiable."""
            try:
                return path.read_text().strip()
            except FileNotFoundError:
                return ""
            except OSError as error:
                reasons.append(
                    safe_format("cannot verify {p}: {e}", p=str(path), e=error)
                )
                return ""

        override = state(device / "driver_override")
        if override not in ("", "(null)", driver):
            reasons.append(
                safe_format(
                    "driver_override is {ovr}, so {drv} could not re-bind",
                    ovr=override,
                    drv=driver,
                )
            )
        if state(device / "sriov_numvfs") not in ("", "0"):
            reasons.append("SR-IOV virtual functions are enabled")
        if state(device / "boot_vga") == "1" or any((device / "drm").glob("card*")):
            reasons.append("device drives a display (boot VGA or DRM card)")
        try:
            own_ns = os.readlink(self.PROC_ROOT / "self" / "ns" / "pid")
        except OSError:
            own_ns = ""
        if own_ns != self.INIT_PID_NS:
            # In a container with its own /proc, other processes' open device
            # nodes and mounts are invisible: nothing can be verified.
            reasons.append(
                "cannot see host processes (not in the initial PID namespace)"
            )
        try:
            class_code = int(state(device / "class") or "0", 16)
        except ValueError:
            class_code = 0
        host_bus = self.BUS_HOST_CLASSES.get(class_code >> 8)
        if host_bus:
            reasons.append(
                safe_format(
                    "{what} hosts other devices; their users cannot be verified",
                    what=host_bus,
                )
            )
        nets = sorted((device / "net").glob("*"))
        # Interfaces of child devices (e.g. behind a bus the device hosts).
        nets += sorted(n for n in device.glob("**/net/*") if n.parent.parent != device)
        if state(device / "class").startswith("0x02") and not nets:
            # A bound network driver always registers a netdev; none visible
            # here means it lives in another network namespace (container).
            reasons.append(
                "network interface not visible (moved to another namespace?)"
            )
        for net in nets:
            uppers = sorted(p.name for p in net.glob("upper_*"))
            if uppers:
                reasons.append(
                    safe_format(
                        "network interface {name} has upper devices ({u})",
                        name=net.name,
                        u=", ".join(u[6:] for u in uppers),
                    )
                )
            # Administrative state (IFF_UP), not carrier: an interface that
            # is configured up but has no link is still in use.
            try:
                up = int(state(net / "flags") or "0x1", 16) & 0x1
            except ValueError:
                up = 1
            if up:
                reasons.append(
                    safe_format("network interface {name} is up", name=net.name)
                )
            else:
                configured = self._configured_interface(net)
                if configured:
                    # Unbind destroys the netdev; the rebind does not restore
                    # its addresses, routes, MAC or MTU.
                    reasons.append(
                        safe_format(
                            "network interface {name} is configured ({why})",
                            name=net.name,
                            why=configured,
                        )
                    )
        mounted = self._mounted_devices(reasons)
        swaps = state(self.PROC_ROOT / "swaps")
        reasons.extend(self._open_device_nodes(device))
        backed = self._kernel_block_users(reasons)
        for disk_dir in self._block_devices(device, reasons):
            parts = [disk_dir] + [
                d for d in disk_dir.iterdir() if (d / "partition").exists()
            ]
            for part in parts:
                try:
                    major, minor = self._read_text(part / "dev").split(":")
                    number = os.makedev(int(major), int(minor))
                except ValueError:
                    reasons.append(
                        safe_format("cannot verify block device {n}", n=part.name)
                    )
                    number = None
                for user in backed.get(number, ()):
                    reasons.append(
                        safe_format("block device {n} backs {u}", n=part.name, u=user)
                    )
                if any((part / "holders").glob("*")):
                    reasons.append(
                        safe_format("block device {n} has holders", n=part.name)
                    )
                if self._read_text(part / "dev") in mounted:
                    reasons.append(
                        safe_format("block device {n} is mounted", n=part.name)
                    )
                if f"/dev/{part.name} " in swaps + " ":
                    reasons.append(safe_format("block device {n} is swap", n=part.name))
                claim = self._block_claim(part.name)
                if claim:
                    reasons.append(claim)
        return reasons

    def _block_devices(self, device: Path, reasons: List[str]) -> List[Path]:
        """Disks backed by this PCI function, on any bus (SCSI, NVMe, ...).

        Found from ``/sys/block``: each entry resolves into the device tree
        of the controller that provides it. NVMe namespaces live at
        ``nvme/nvmeX/nvmeXnY``, not under a ``block`` directory, so a walk
        for ``block`` alone misses them.
        """
        found: dict = {}
        try:
            root = device.resolve()
            for entry in sorted((self.SYSFS_ROOT / "block").iterdir()):
                target = entry.resolve()
                if root in target.parents:
                    found[target] = target
                    continue
                # NVMe native multipath: the visible disk (nvmeXnY) lives under
                # /sys/devices/virtual/nvme-subsystem; its per-controller
                # paths link back to the PCI function.
                for path in (entry / "multipath").glob("*"):
                    if root in path.resolve().parents:
                        found[target] = target
                        break
        except OSError as error:
            reasons.append(safe_format("cannot verify block devices: {e}", e=error))
        heads = {p.name for p in found}
        for path_node in device.glob("nvme/nvme*/nvme*c*n*"):
            # Hidden path node nvme<subsys>c<ctrl>n<ns> belongs to the head
            # nvme<subsys>n<ns> (kernel naming; the head's multipath/ link
            # directory only exists from 6.14).
            ctrl, _, ns = path_node.name.partition("c")
            head = ctrl + "n" + ns.partition("n")[2]
            head_dir = self.SYSFS_ROOT / "block" / head
            if head not in heads and head_dir.exists():
                try:
                    target = head_dir.resolve()
                    found[target] = target
                    heads.add(head)
                except OSError:
                    pass
            if head not in heads:
                reasons.append(
                    safe_format(
                        "cannot verify multipath namespace {p}", p=path_node.name
                    )
                )
        for walk_root, dirs, _files in os.walk(device, followlinks=False):
            if Path(walk_root).name == "block":
                for disk in dirs:
                    path = (Path(walk_root) / disk).resolve()
                    found.setdefault(path, path)
                dirs[:] = []
        return list(found.values())

    @staticmethod
    def _block_number(path: str) -> Optional[int]:
        """Device number of a block device path (None for other files)."""
        st = os.stat(path)
        return st.st_rdev if stat.S_ISBLK(st.st_mode) else None

    def _kernel_block_users(self, reasons: List[str]) -> dict:
        """Kernel users holding block devices without a claim or an fd,
        keyed by device number (any path spelling: by-id, by-path, ...).

        A loop device keeps its backing device open non-exclusively (no
        holder link, no process fd once losetup exits); an NVMe-oF target
        namespace opens its device_path the same way.
        """
        users: dict = {}

        def add(target: str, user: str, must_resolve: bool) -> None:
            try:
                rdev = self._block_number(target)
            except OSError as error:
                if must_resolve or target.startswith("/dev/"):
                    reasons.append(
                        safe_format(
                            "cannot resolve {t} used by {u}: {e}",
                            t=target,
                            u=user,
                            e=error,
                        )
                    )
                return  # a regular file: its filesystem is checked as a mount
            if rdev is not None:
                users.setdefault(rdev, []).append(user)

        for backing in (self.SYSFS_ROOT / "block").glob("loop*/loop/backing_file"):
            target = self._read_text(backing)
            if target and not target.endswith(" (deleted)"):
                add(target, backing.parents[1].name, False)
        nvmet = self.SYSFS_ROOT / "kernel" / "config" / "nvmet" / "subsystems"
        for ns in nvmet.glob("*/namespaces/*"):
            if self._read_text(ns / "enable") == "1":
                target = self._read_text(ns / "device_path")
                if target:
                    add(target, "an NVMe-oF target", True)
        return users

    def _open_device_nodes(self, device: Path) -> List[str]:
        """Processes holding this device's char/block nodes open.

        Covers sound, DRM, video, serial, hidraw and the like: every node the
        device exposes has a ``dev`` (major:minor) file in its sysfs subtree.
        An unbind would pull the node out from under the process.
        """
        numbers = set()
        for dev_file in device.glob("**/dev"):
            try:
                major, minor = dev_file.read_text().strip().split(":")
                numbers.add(os.makedev(int(major), int(minor)))
            except (OSError, ValueError):
                continue
        # sysfs BAR files (resourceN, resourceN_wc) opened or mmapped by a
        # userspace driver are regular files: matched by path instead.
        try:
            sysfs_dir = str(device.resolve())
        except OSError:
            sysfs_dir = str(device)
        holders: dict = {}
        unverifiable: List[str] = []
        for fd_dir in [
            *self.PROC_ROOT.glob("[0-9]*/fd"),
            # mmaps whose fd was closed (e.g. a GPU or BAR mapping).
            *self.PROC_ROOT.glob("[0-9]*/map_files"),
        ]:
            try:
                fds = list(fd_dir.iterdir())
            except FileNotFoundError:
                continue  # process exited
            except OSError as error:
                unverifiable.append(
                    safe_format(
                        "cannot verify files of pid {pid}: {e}",
                        pid=fd_dir.parent.name,
                        e=error,
                    )
                )
                continue
            for fd in fds:
                try:
                    link = os.readlink(fd)
                except OSError:
                    link = ""
                if link.startswith(sysfs_dir + "/resource"):
                    hit = True
                else:
                    try:
                        st = os.stat(fd)
                    except FileNotFoundError:
                        continue  # fd closed or process exited
                    except OSError as error:
                        unverifiable.append(
                            safe_format(
                                "cannot verify an open file of pid {pid}: {e}",
                                pid=fd_dir.parent.name,
                                e=error,
                            )
                        )
                        continue
                    hit = (stat.S_ISCHR(st.st_mode) or stat.S_ISBLK(st.st_mode)) and (
                        st.st_rdev in numbers
                    )
                if hit:
                    try:
                        comm = (fd_dir.parent / "comm").read_text().strip()
                    except OSError:
                        comm = "?"
                    holders[fd_dir.parent.name] = comm
        return [
            safe_format("device node open by {comm} (pid {pid})", comm=c, pid=pid)
            for pid, c in sorted(holders.items())
        ] + unverifiable[:3]

    # Classes whose function hosts another bus: unbinding removes every child
    # device, whose kernel users (USB network/storage/input, I2C sensors,
    # Thunderbolt tunnels) need not show up as fds, mounts or claims.
    BUS_HOST_CLASSES = {
        0x0604: "PCI bridge",
        0x0C03: "USB controller",
        0x0C05: "SMBus controller",
        0x0C0A: "Thunderbolt/USB4 controller",
        0x0C80: "serial bus controller",
    }
    # Initial PID namespace inode (PROC_PID_INIT_INO).
    INIT_PID_NS = "pid:[4026531836]"

    def _configured_interface(self, net: Path) -> Optional[str]:
        """Configuration a down interface carries that a rebind would lose."""
        if self._read_text(net / "addr_assign_type") not in ("", "0"):
            return "MAC address set administratively"
        mtu = self._read_text(net / "mtu")
        if mtu not in ("", "1500"):
            return safe_format("MTU {m}", m=mtu)
        ip = shutil.which("ip")
        if not ip:
            return "cannot verify addresses (ip(8) not found)"
        for what in ("addr", "route"):
            try:
                shown = subprocess.run(  # nosec B603
                    [ip, "-o", what, "show", "dev", net.name],
                    check=False,
                    capture_output=True,
                    text=True,
                    timeout=5,
                )
            except (OSError, subprocess.SubprocessError) as error:
                return safe_format("cannot verify {w}: {e}", w=what, e=error)
            if shown.returncode != 0:
                return safe_format("cannot verify {w}", w=what)
            if shown.stdout.strip():
                return safe_format("has {w} entries", w=what)
        return None

    def _hold_block_claims(self) -> List[int]:
        """Exclusive read-only opens of every disk/partition of the device."""
        reasons: List[str] = []
        names = []
        for disk in self._block_devices(self._device_dir(), reasons):
            names.append(disk.name)
            names.extend(d.name for d in disk.iterdir() if (d / "partition").exists())
        fds: List[int] = []
        for name in names:
            try:
                fds.append(
                    os.open(
                        self.DEV_ROOT / name, os.O_RDONLY | os.O_EXCL | os.O_NONBLOCK
                    )
                )
            except OSError as error:
                reasons.append(
                    safe_format("block device {n} cannot be held: {e}", n=name, e=error)
                )
        if reasons:
            for fd in fds:
                os.close(fd)
            raise RuntimeError(
                safe_format(
                    "Refusing to rebind {bdf}: {why}",
                    bdf=self.device_bdf,
                    why="; ".join(reasons),
                )
            )
        return fds

    def _block_claim(self, name: str) -> Optional[str]:
        """Why a block device is in use, via an exclusive open; None if free.

        ZFS vdevs, md members, device-mapper/LVM, bcache and mounts all
        claim the device exclusively without necessarily showing up as
        holders or mounts. O_EXCL without O_CREAT on a block device fails
        with EBUSY while any such claim exists; read-only and side-effect
        free. A missing node (e.g. in a container) cannot be verified.
        """
        path = self.DEV_ROOT / name
        try:
            fd = os.open(path, os.O_RDONLY | os.O_EXCL | os.O_NONBLOCK)
        except OSError as error:
            if error.errno == errno.EBUSY:
                return safe_format(
                    "block device {n} is claimed (filesystem, pool, RAID or "
                    "device mapper)",
                    n=name,
                )
            return safe_format("cannot verify block device {n}: {e}", n=name, e=error)
        os.close(fd)
        return None

    def _mounted_devices(self, reasons: List[str]) -> set:
        """Device numbers mounted in any mount namespace on the host.

        A container or service can mount the donor's disk in a namespace
        this process does not see; one mountinfo per namespace covers them.
        """
        mounted: set = set()
        seen_namespaces: set = set()
        readable = False
        for proc in sorted(self.PROC_ROOT.glob("[0-9]*")):
            try:
                namespace = os.readlink(proc / "ns" / "mnt")
            except OSError:
                continue
            if namespace in seen_namespaces:
                continue
            try:
                text = (proc / "mountinfo").read_text()
            except OSError:
                continue
            seen_namespaces.add(namespace)
            readable = True
            for line in text.splitlines():
                fields = line.split()
                if len(fields) > 2:
                    mounted.add(fields[2])
        try:
            own = (self.PROC_ROOT / "self" / "mountinfo").read_text()
            readable = True
            for line in own.splitlines():
                fields = line.split()
                if len(fields) > 2:
                    mounted.add(fields[2])
        except OSError:
            pass
        if not readable:
            reasons.append("cannot verify mounts: no mountinfo readable")
        return mounted

    def _bind(self, driver_dir: str) -> None:
        """Bind the original driver; a deferred probe is waited out briefly."""
        try:
            self._sysfs_write(f"{driver_dir}/bind", self.device_bdf)
        except OSError as e:
            if e.errno != self.EPROBE_DEFER:
                raise
            deadline = time.monotonic() + self.DEFERRED_PROBE_WAIT_SEC
            while self._current_driver() is None:
                if time.monotonic() >= deadline:
                    raise OSError(
                        e.errno, "probe deferred and not completed in time"
                    ) from e
                time.sleep(0.1)

    @classmethod
    def rebinder(cls, device_bdf: str) -> "MmioTracer":
        """An instance used only for the guarded native-driver rebind.

        Skips the bpftrace checks; the mmiotrace observer drives the rebind
        but traces through the kernel's mmiotrace tracer instead.
        """
        tracer = object.__new__(cls)
        tracer.device_bdf = device_bdf
        return tracer

    def _trigger_driver_rebind(
        self, after_unbind: Optional[Callable[[], None]] = None
    ) -> None:
        """Unbind and re-probe the native driver so its probe sequence is traced.

        Refuses devices whose unbind would break the host (mounted storage,
        an up network interface, a display, SR-IOV VFs, an override that would
        block the re-bind). The original binding is journaled in the shared
        binding-journal directory before the unbind, so a crash blocks later
        sessions on this device. The exact original driver is re-bound (no bus
        rescan) in ``finally``, so failure or cancellation still restores, and
        the final binding is verified; the journal is cleared only then.
        Restoration means the driver binding only: runtime configuration
        established after the original probe is not replayed.
        A device on vfio-pci, or with no driver, is not touched.
        """
        from pcileechfwgenerator.cli.vfio_handler import (
            begin_binding_journal,
            clear_binding_journal,
        )
        from pcileechfwgenerator.exceptions import VFIOBindError
        from pcileechfwgenerator.host_collect.register_learning import (
            default_journal_directory,
        )

        journals = default_journal_directory() / "bindings"
        from pcileechfwgenerator.cli.vfio_handler import binding_journal_path

        if binding_journal_path(journals, self.device_bdf).exists():
            # An earlier session crashed mid-rebind (possibly leaving no
            # driver): a restoration incident, never a skipped observation.
            raise RuntimeError(
                safe_format(
                    "Unresolved binding incident for {bdf}: restore the driver "
                    "binding, then remove its journal in {dir}",
                    bdf=self.device_bdf,
                    dir=str(journals),
                )
            )
        original = self._current_driver()
        if original is None or original == "vfio-pci":
            log_debug_safe(
                logger,
                safe_format(
                    "No native driver bound ({drv}); skipping rebind",
                    drv=original or "none",
                ),
                prefix="MMIO",
            )
            return
        reasons = self._in_use_reasons(original)
        if reasons:
            raise RuntimeError(
                safe_format(
                    "Refusing to rebind {bdf}: {why}",
                    bdf=self.device_bdf,
                    why="; ".join(reasons),
                )
            )
        try:
            begin_binding_journal(
                journals,
                self.device_bdf,
                {"original_driver": original, "holder": "mmio_tracer"},
            )
        except (VFIOBindError, OSError) as e:
            raise RuntimeError(
                safe_format("Refusing to rebind: {err}", err=str(e))
            ) from e
        log_info_safe(
            logger,
            safe_format(
                "Triggering {drv} rebind for {bdf}", drv=original, bdf=self.device_bdf
            ),
            prefix="MMIO",
        )
        driver_dir = str(self.SYSFS_ROOT / "bus" / "pci" / "drivers" / original)
        errors = []
        # Exclusive opens held through the unbind: a mount, pool import or
        # RAID assembly cannot claim a disk between the check and the unbind.
        try:
            claims = self._hold_block_claims()
        except RuntimeError:
            clear_binding_journal(journals, self.device_bdf)
            raise
        try:
            try:
                self._sysfs_write(f"{driver_dir}/unbind", self.device_bdf)
            finally:
                for fd in claims:
                    os.close(fd)
            time.sleep(0.5)
            if after_unbind is not None:
                after_unbind()
        except (IOError, OSError) as e:
            errors.append(safe_format("unbind: {err}", err=e))
        finally:
            if self._current_driver() is None:
                try:
                    self._bind(driver_dir)
                except (IOError, OSError) as e:
                    errors.append(safe_format("bind {drv}: {err}", drv=original, err=e))
            final = self._current_driver()
            if final != original:
                # The journal stays: later sessions on this device are blocked
                # until an operator restores the binding.
                raise RuntimeError(
                    safe_format(
                        "Driver rebind for {bdf} left it on {final} (expected "
                        "{orig}){why}. Rebind the device manually, then remove "
                        "the binding journal in {dir}.",
                        bdf=self.device_bdf,
                        final=final or "no driver",
                        orig=original,
                        why=(": " + "; ".join(errors)) if errors else "",
                        dir=str(journals),
                    )
                )
            clear_binding_journal(journals, self.device_bdf)
        if errors:
            raise RuntimeError(
                safe_format("Driver rebind failed: {err}", err="; ".join(errors))
            )
