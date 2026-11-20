"""Passive native-driver observation through the kernel's mmiotrace tracer.

mmiotrace catches every CPU access to memory mapped while it is active,
including inline ``readl``/``writel`` accessors that kprobe-based tracing
cannot see. The capture enables the tracer, re-probes the donor's native
driver (optionally resetting the function in between, so first reads show
post-reset values) and records its initialization traffic. Nothing here
writes to the device; the driver does, exactly as it would on the target.

Record formats (kernel ``trace_mmiotrace.c``, version 20070824)::

    R|W <width> <sec.usec> <map id> <phys> <value> <pc> <pid>
    MAP <sec.usec> <map id> <phys> <virt> <len> <pc> <pid>
    UNMAP <sec.usec> <map id> <pc> <pid>
    UNKNOWN <sec.usec> <map id> <phys> <data> <pc> <pid>
    MARK <sec.usec> <text>          ("Lost N events." reports overruns)

Integers are parsed with base auto-detection (map ids/widths are decimal,
addresses/values ``0x`` hex). While tracing, the kernel takes all CPUs but
one offline; the capture is therefore kept short.
"""

from __future__ import annotations

import re
import threading
import time
from collections.abc import Callable, Iterable, Sequence
from pathlib import Path

from pcileechfwgenerator.device_clone.register_evidence import (
    Observation,
    RegisterRef,
    width_mask,
)
from pcileechfwgenerator.host_collect.register_observer import (
    BarResource,
    ObserverResult,
)
from pcileechfwgenerator.log_config import get_logger
from pcileechfwgenerator.string_utils import (
    log_info_safe,
    log_warning_safe,
    safe_format,
)

logger = get_logger(__name__)

TRACEFS_CANDIDATES = (Path("/sys/kernel/tracing"), Path("/sys/kernel/debug/tracing"))
_LOST = re.compile(r"lost\s+(\d+)\s+events", re.IGNORECASE)
_IGNORED = frozenset({"VERSION", "LSPCI", "PCIDEV", "UNMAP"})


class ObserverUnavailable(RuntimeError):
    """Passive observation cannot run here. A coverage gap, not a failure."""


def _timestamp_ns(text: str) -> int:
    seconds, _, fraction = text.partition(".")
    if not seconds.isdigit() or (fraction and not fraction.isdigit()):
        raise ValueError(text)
    return int(seconds) * 1_000_000_000 + int((fraction + "000000000")[:9])


def parse_mmiotrace(
    lines: Iterable[str], bars: Sequence[BarResource], *, state_epoch: int = 0
) -> ObserverResult:
    """Turn an mmiotrace dump into native-driver observations of ``bars``.

    Accesses to other devices are ignored (the tracer is system-wide).
    Accesses that straddle a BAR boundary or are misaligned, UNKNOWN
    (undecoded) accesses inside a BAR, overruns and malformed lines are
    counted so the capture is reported incomplete rather than trusted.
    """
    result = ObserverResult()
    last_ns = -1
    phase = "probe"
    for raw in lines:
        tokens = raw.split()
        if not tokens:
            continue
        kind = tokens[0]
        if kind == "MARK" and EXERCISE_MARK in raw:
            # Accesses after this point were provoked by the exercises, not
            # the probe: never taken as post-reset initial values.
            phase = "exercise"
            continue
        if kind == "MARK" or "lost" in raw.lower():
            match = _LOST.search(raw)
            if match:
                result.lost_events += int(match.group(1))
            elif kind != "MARK":
                result.lost_events += 1
            continue
        if kind in _IGNORED:
            continue
        if kind == "MAP":
            try:
                start, length = int(tokens[3], 0), int(tokens[5], 0)
            except (IndexError, ValueError):
                result.malformed_lines += 1
                continue
            for bar in bars:
                if not (
                    start < bar.physical_start + bar.size
                    and bar.physical_start < start + length
                ):
                    continue
                if bar.bar_index not in result.mapped_bars:
                    result.mapped_bars.append(bar.bar_index)
                if start & 0xFFF:
                    # kmmio looks probes up by page base; accesses through a
                    # mapping that starts mid-page can go unrecorded.
                    result.capture_incomplete += 1
                    result.notes.append(
                        f"BAR{bar.bar_index} mapped at unaligned {start:#x}"
                    )
            continue
        try:
            if kind in ("R", "W"):
                width = int(tokens[1], 0)
                stamp = _timestamp_ns(tokens[2])
                physical = int(tokens[4], 0)
                value = int(tokens[5], 0)
            elif kind == "UNKNOWN":
                stamp = _timestamp_ns(tokens[1])
                physical = int(tokens[3], 0)
                width = 1
                value = 0
            else:
                result.malformed_lines += 1
                continue
        except (IndexError, ValueError):
            result.malformed_lines += 1
            continue
        hit = [
            bar
            for bar in bars
            if bar.physical_start <= physical < bar.physical_start + bar.size
        ]
        if not hit:
            continue  # another device's mapping
        bar = hit[0]
        if kind == "UNKNOWN":
            result.discarded_observations += 1
            continue
        offset = physical - bar.physical_start
        if (
            width not in (1, 2, 4, 8)
            or offset % width
            or offset + width > bar.size
            or value > width_mask(width)
        ):
            result.unattributed_accesses += 1
            continue
        if stamp < last_ns:
            result.out_of_order_events += 1
            continue
        last_ns = stamp
        ref = RegisterRef(
            space="mem_bar",
            bar_index=bar.bar_index,
            offset=offset,
            width=width,
            field_mask=width_mask(width),
        )
        fields = dict(
            sequence_id=len(result.observations),
            monotonic_ns=stamp,
            operation="read" if kind == "R" else "write",
            ref=ref,
            completion="ok",
            source="native_driver",
            # The faulting CPU instruction itself was decoded: a physical
            # access by the driver, not a mediated or emulated one.
            mediation="physical",
            state_epoch=state_epoch,
            phase=phase,
        )
        fields["returned_value" if kind == "R" else "attempted_value"] = value
        result.observations.append(Observation(**fields))
    return result


def find_tracefs() -> Path:
    for candidate in TRACEFS_CANDIDATES:
        if (candidate / "available_tracers").exists():
            return candidate
    raise ObserverUnavailable("tracefs is not mounted")


# Host interfaces, module-level so tests can point them at fixtures.
CPU_ONLINE = Path("/sys/devices/system/cpu/online")
CPU_DIR = Path("/sys/devices/system/cpu")
LOCKDOWN = Path("/sys/kernel/security/lockdown")
KMSG = Path("/dev/kmsg")
TRACER_JOURNAL = "mmiotrace-active.json"
# The tracer and the CPU set are global: one lock per host, independent of
# where journals or artifacts are written.
HOST_LOCK = Path("/run/pcileech-mmiotrace.lock")
BOOT_ID = Path("/proc/sys/kernel/random/boot_id")
EXERCISE_MARK = "pcileech:exercise-start"
# Resets confined to this function. Bus/slot resets are never used.
SAFE_RESET_METHODS = frozenset({"flr", "af_flr", "pm"})
# Any kmmio message is a fault (it only logs problems); mmiotrace's own
# status lines ("enabled CPU1", "MMIO tracing disabled") are normal.
_KMSG_TROUBLE = re.compile(
    r"kmmio|recursive probe hit|secondary hit|unable to set page fault|"
    r"mmiotrace has lost events|mmiotrace:.*(error|cannot|unable|fail|lost)",
    re.IGNORECASE,
)
# Extra trace time allowed for exercising the device after its probe.
EXERCISE_BUDGET_SEC = 20.0
# Slack for a step already running when the budget ends (bounded commands).
EXERCISE_GRACE_SEC = 5.0
# Hard limits on one capture. The probe runs synchronously in the kernel;
# past the deadline the tracer is stopped regardless and the trace is
# marked incomplete.
PROBE_DEADLINE_SEC = 60.0
MAX_TRACE_BYTES = 64 << 20


class HostRestorationError(RuntimeError):
    """The capture could not return the host to its prior state.

    The tracer may still be active with CPUs offline, or the donor's driver
    may not be re-bound. Collection must stop; the tracer journal lets the
    next run recover.
    """


def _cpu_set(text: str) -> set[int]:
    cpus: set[int] = set()
    for part in text.strip().split(","):
        if not part:
            continue
        low, _, high = part.partition("-")
        cpus.update(range(int(low), int(high or low) + 1))
    return cpus


def _set_cpu_online(cpu: int) -> None:
    (CPU_DIR / f"cpu{cpu}" / "online").write_text("1")


def _online_cpus(expected: set[int]) -> bool:
    """Bring back CPUs mmiotrace left offline; True when the set matches."""
    for cpu in sorted(expected - _cpu_set(CPU_ONLINE.read_text())):
        try:
            _set_cpu_online(cpu)
        except OSError:
            pass  # the set comparison below reports any CPU left offline
    return _cpu_set(CPU_ONLINE.read_text()) == expected


def _set_tracing_on(tracefs: Path, value: str | None) -> None:
    """Restore the global ring-buffer switch (None: leave as is)."""
    if value is not None:
        (tracefs / "tracing_on").write_text(value)


def _stop_tracer(
    tracefs: Path, cpus_before: set[int], tracing_on: str | None = None
) -> str | None:
    """Disable mmiotrace, restore CPUs and tracing_on; a problem or None.

    Every step is attempted even when an earlier one fails, and any failure
    (including an unexpected exception) is reported, never raised.
    """
    problems = []
    disabled = False
    for _ in range(5):
        try:
            (tracefs / "current_tracer").write_text("nop")
            if (tracefs / "current_tracer").read_text().strip() == "nop":
                disabled = True
                break
        except Exception:  # noqa: BLE001 - reported below
            pass
        time.sleep(0.2)
    if not disabled:
        problems.append("mmiotrace could not be disabled")
    try:
        if not _online_cpus(cpus_before):
            problems.append("CPUs taken offline by mmiotrace were not all restored")
    except Exception as error:  # noqa: BLE001
        problems.append(f"CPU state could not be restored or verified: {error}")
    try:
        _set_tracing_on(tracefs, tracing_on)
    except Exception as error:  # noqa: BLE001
        problems.append(f"tracing_on could not be restored: {error}")
    return "; ".join(problems) or None


def recover_stale_tracer(tracefs: Path, journal_dir: Path) -> list[str]:
    """Undo a capture that was killed while mmiotrace was active.

    mmiotrace keeps every CPU but one offline until it is disabled. A journal
    left by this tool means the tracer state is ours to restore. An active
    mmiotrace without our journal belongs to someone else and is not touched.
    """
    import json

    journal = journal_dir / TRACER_JOURNAL
    current = (tracefs / "current_tracer").read_text().strip()
    if journal.exists():
        tracing_on = None
        record: dict = {}
        try:
            record = json.loads(journal.read_text())
            cpus = set(record["cpus_online"])
            tracing_on = record.get("tracing_on")
        except (OSError, ValueError, KeyError, TypeError, AttributeError):
            cpus = _cpu_set(CPU_ONLINE.read_text())
        if record.get("incident"):
            # A worker (rebind or exercise undo) outlived its capture: the
            # device state is unknown until an operator checks it.
            raise HostRestorationError(
                f"An earlier mmiotrace capture did not finish restoring the "
                f"host: {record['incident']}. Verify the donor and its driver, "
                f"then remove {journal}."
            )
        boot = _boot_id()
        if record.get("boot_id") and boot and record["boot_id"] != boot:
            # A reboot reset the tracer and brought every CPU back.
            journal.unlink()
            return ["discarded a tracer journal from a previous boot"]
        if current == "mmiotrace":
            problem = _stop_tracer(tracefs, cpus, tracing_on)
        else:
            problem = None
            try:
                if not _online_cpus(cpus):
                    problem = "CPUs from the interrupted capture are still offline"
            except Exception as error:  # noqa: BLE001
                problem = f"CPU state could not be restored: {error}"
            if current == "nop":
                try:
                    _set_tracing_on(tracefs, tracing_on)
                except OSError as error:
                    problem = problem or f"tracing_on could not be restored: {error}"
            # else: another tracing session owns tracing_on now; leave it.
        if problem:
            raise HostRestorationError(
                f"An interrupted mmiotrace capture left the host degraded: "
                f"{problem}. Run `echo nop > {tracefs}/current_tracer`, bring "
                f"CPUs online, then remove {journal}."
            )
        journal.unlink()
        log_warning_safe(
            logger,
            "Recovered tracer state from an interrupted mmiotrace capture",
            prefix="MMIO",
        )
        return ["recovered tracer state left by an interrupted capture"]
    if current == "mmiotrace":
        log_warning_safe(
            logger,
            safe_format(
                "mmiotrace is active from another session and keeps CPUs "
                "offline; not touching it (echo nop > {p}/current_tracer)",
                p=str(tracefs),
            ),
            prefix="MMIO",
        )
    return []


def _boot_id() -> str | None:
    try:
        return BOOT_ID.read_text().strip() or None
    except OSError:
        return None


def _events_enabled(tracefs: Path) -> bool | None:
    """Whether trace events are enabled (None: unknown)."""
    try:
        return bool((tracefs / "set_event").read_text().strip())
    except OSError:
        return None


def _write_incident(journal: Path, incident: str) -> None:
    """Mark the tracer journal so later captures refuse until checked."""
    import json

    try:
        record = json.loads(journal.read_text()) if journal.exists() else {}
    except (OSError, ValueError):
        record = {}
    record["incident"] = incident
    record.setdefault("boot_id", _boot_id())
    journal.write_text(json.dumps(record))


def _lockdown_mode() -> str | None:
    try:
        match = re.search(r"\[(\w+)\]", LOCKDOWN.read_text())
    except OSError:
        return None
    return match.group(1) if match else None


class _KernelLog:
    """New kernel log records since construction (non-blocking /dev/kmsg)."""

    def __init__(self) -> None:
        import os

        self._os = os
        self.fd: int | None = None
        try:
            self.fd = os.open(KMSG, os.O_RDONLY | os.O_NONBLOCK)
            os.lseek(self.fd, 0, os.SEEK_END)
        except OSError:
            self.close()

    def troubles(self) -> list[str] | None:
        """Tracer fault lines, or None when the log cannot be read."""
        if self.fd is None:
            return None
        found = []
        while True:
            try:
                record = self._os.read(self.fd, 8192)
            except BlockingIOError:
                break
            except OSError as error:
                if error.errno == 32:  # EPIPE: records overwritten, maybe ours
                    found.append("kernel log records were overwritten")
                    continue
                return None
            if not record:
                break
            text = record.decode(errors="replace").split(";", 1)[-1].strip()
            if _KMSG_TROUBLE.search(text):
                found.append(text)
        return found

    def close(self) -> None:
        if self.fd is not None:
            self._os.close(self.fd)
            self.fd = None


def _reset_function(device: Path, state: dict) -> Callable[[], None]:
    """Reset the unbound function, so the probe sees post-reset values.

    Only when every enabled reset method is confined to this function
    (FLR, AF FLR, PM); the kernel falls through methods in order, so a listed
    bus or slot reset could otherwise run. Outcome lands in ``state``.
    """

    def reset() -> None:
        state["performed"] = False
        methods_file, reset_file = device / "reset_method", device / "reset"
        try:
            methods = methods_file.read_text().split()
        except OSError:
            methods = []
        if not reset_file.exists() or not methods:
            state["note"] = "no function reset available"
        elif not set(methods) <= SAFE_RESET_METHODS:
            state["note"] = "reset methods " + " ".join(methods) + " not confined"
        else:
            try:
                reset_file.write_text("1")
                state["performed"] = True
                return
            except OSError as error:
                state["note"] = f"function reset failed: {error}"
        log_info_safe(
            logger,
            safe_format("Tracing without a reset: {why}", why=state["note"]),
            prefix="MMIO",
        )

    return reset


def _exit_on_sigterm(_signum, _frame) -> None:
    raise SystemExit(143)


def _drain(fd: int, chunks: list[bytes], stop: threading.Event, status: dict) -> None:
    """Read trace_pipe without blocking until stopped and empty.

    ``status["done"]`` is set only on a clean finish; ``status["error"]``
    records why reading ended early (I/O error, size limit).
    """
    import os
    import select

    total = 0
    while True:
        if not stop.is_set():
            select.select([fd], [], [], 0.1)
        try:
            data = os.read(fd, 65536)
        except BlockingIOError:
            data = b""
        except OSError as error:
            status["error"] = f"trace_pipe read failed: {error}"
            return
        if data:
            total += len(data)
            if total > MAX_TRACE_BYTES:
                status["error"] = "trace exceeded the size limit"
                return
            chunks.append(data)
        elif stop.is_set():
            status["done"] = True
            return
        else:
            time.sleep(0.05)


def capture_native_probe(
    bdf: str,
    bars: Sequence[BarResource],
    *,
    duration_sec: float = 5.0,
    tracefs: Path | None = None,
    rebind: Callable[[Callable[[], None]], None] | None = None,
    sysfs_device: Path | None = None,
    journal_dir: Path | None = None,
    exercise: Callable[[float], object] | None = None,
) -> ObserverResult:
    """Trace the native driver re-probing the donor.

    Raises ObserverUnavailable when tracing cannot run, before the tracer is
    enabled wherever possible (no mmiotrace, lockdown, tracer busy, no native
    driver, device in use); the caller records a gap and the build serves the
    read-only fallback. Raises HostRestorationError when the host may be left
    degraded (tracer still on, CPUs offline, driver not restored); the
    tracer journal then lets the next run recover.
    """
    from pcileechfwgenerator.host_collect.register_learning import (
        default_journal_directory,
    )

    if not bars:
        raise ObserverUnavailable("no memory BARs to observe")
    try:
        tracefs = tracefs or find_tracefs()
        available = (tracefs / "available_tracers").read_text().split()
    except OSError as error:
        raise ObserverUnavailable(f"tracefs unreadable: {error}") from None
    if "mmiotrace" not in available:
        raise ObserverUnavailable("kernel lacks mmiotrace (CONFIG_MMIOTRACE)")
    lockdown = _lockdown_mode()
    if lockdown not in (None, "none"):
        raise ObserverUnavailable(f"kernel lockdown ({lockdown}) disables mmiotrace")
    # The tracer journal lives apart from experiment journals, and a host-wide
    # lock makes every journal found under it abandoned by a dead process.
    journal_dir = Path(journal_dir or default_journal_directory()) / "tracer"
    journal_dir.mkdir(parents=True, exist_ok=True)
    with _HostLock(HOST_LOCK):
        return _locked_capture(
            bdf,
            bars,
            duration_sec=duration_sec,
            tracefs=tracefs,
            rebind=rebind,
            device=sysfs_device or Path("/sys/bus/pci/devices") / bdf,
            journal_dir=journal_dir,
            exercise=exercise,
        )


class _HostLock:
    """Exclusive, non-blocking flock; released by the kernel on process death."""

    def __init__(self, path: Path) -> None:
        self.path = path
        self.fd: int | None = None

    def __enter__(self) -> "_HostLock":
        import fcntl
        import os

        try:
            self.fd = os.open(self.path, os.O_RDWR | os.O_CREAT, 0o600)
        except OSError as error:
            raise ObserverUnavailable(f"host capture lock unavailable: {error}")
        try:
            fcntl.flock(self.fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except OSError:
            os.close(self.fd)
            self.fd = None
            raise ObserverUnavailable("another mmiotrace capture is running")
        return self

    def __exit__(self, *_exc) -> None:
        import os

        if self.fd is not None:
            os.close(self.fd)  # closing releases the lock
            self.fd = None


def _locked_capture(
    bdf: str,
    bars: Sequence[BarResource],
    *,
    duration_sec: float,
    tracefs: Path,
    rebind: Callable[[Callable[[], None]], None] | None,
    device: Path,
    journal_dir: Path,
    exercise: Callable[[float], object] | None = None,
) -> ObserverResult:
    import json
    import os
    import signal

    from pcileechfwgenerator.device_clone.mmio_tracer import MmioTracer

    notes = recover_stale_tracer(tracefs, journal_dir)
    current = (tracefs / "current_tracer").read_text().strip()
    if current != "nop":
        raise ObserverUnavailable(f"tracer {current!r} is already active")
    events = _events_enabled(tracefs)
    if events is not False:
        # Enabling mmiotrace resets the shared ring buffer: another session's
        # event trace would be destroyed.
        raise ObserverUnavailable(
            "trace events are enabled (another tracing session)"
            if events
            else "cannot verify that tracefs is unused (set_event unreadable)"
        )
    if rebind is None:
        tracer = MmioTracer.rebinder(bdf)
        driver = tracer._current_driver()
        if driver in (None, "vfio-pci"):
            raise ObserverUnavailable("no native driver is bound to the donor")
        reasons = tracer._in_use_reasons(driver)
        if reasons:
            raise ObserverUnavailable("donor in use: " + "; ".join(reasons))

        def rebind(after_unbind: Callable[[], None]) -> None:
            tracer._trigger_driver_rebind(after_unbind=after_unbind)

    try:
        cpus_before = _cpu_set(CPU_ONLINE.read_text())
        # mmiotrace writes into the global ring buffer: with tracing_on=0
        # every record is discarded ("mmiotrace has lost events").
        tracing_on = (tracefs / "tracing_on").read_text().strip()
    except (OSError, ValueError) as error:
        raise ObserverUnavailable(f"tracer state unreadable: {error}") from None
    journal = journal_dir / TRACER_JOURNAL
    with journal.open("x") as stream:
        json.dump(
            {
                "bdf": bdf,
                "pid": os.getpid(),
                "cpus_online": sorted(cpus_before),
                "tracing_on": tracing_on,
                "boot_id": _boot_id(),
            },
            stream,
        )
        stream.flush()
        os.fsync(stream.fileno())
    reset_state: dict = {"performed": None}
    incomplete: list[str] = []
    chunks: list[bytes] = []
    reader_status: dict = {}
    rebind_status: dict = {}
    exercise_status: dict = {}
    exerciser = None
    skipped_exercise: list[str] = []
    restore: dict = {}
    kernel_log = _KernelLog()
    previous_term = None
    if threading.current_thread() is threading.main_thread():
        # SIGTERM's default action skips finally blocks; make it unwind.
        previous_term = signal.signal(signal.SIGTERM, _exit_on_sigterm)

    def run_rebind() -> None:
        try:
            rebind(_reset_function(device, reset_state))
        except BaseException as error:  # reported to the capture thread
            rebind_status["error"] = error

    worker = threading.Thread(target=run_rebind, daemon=True)
    traced_probe_ok = False
    try:
        try:
            _set_tracing_on(tracefs, "1")
            (tracefs / "current_tracer").write_text("mmiotrace")
        except OSError as error:
            raise ObserverUnavailable(f"cannot enable mmiotrace: {error}") from None
        try:
            if len(_cpu_set(CPU_ONLINE.read_text())) != 1:
                incomplete.append("CPUs stayed online while tracing")
        except (OSError, ValueError):
            incomplete.append("CPU state unreadable while tracing")
        fd = os.open(tracefs / "trace_pipe", os.O_RDONLY | os.O_NONBLOCK)
        try:
            stop = threading.Event()
            reader = threading.Thread(
                target=_drain, args=(fd, chunks, stop, reader_status), daemon=True
            )
            reader.start()
        except BaseException:
            os.close(fd)  # the finally below that closes it is not reached
            raise
        try:
            # The probe runs synchronously in the kernel and can stall; the
            # tracer (and its offlined CPUs) must not wait on it unbounded.
            worker.start()
            worker.join(PROBE_DEADLINE_SEC)
            if worker.is_alive():
                incomplete.append("driver probe exceeded the capture deadline")
            elif "error" not in rebind_status:
                traced_probe_ok = True
            if "error" in rebind_status and not worker.is_alive():
                rebind_error = rebind_status["error"]
                if isinstance(rebind_error, RuntimeError) and "Refusing" in str(
                    rebind_error
                ):
                    raise ObserverUnavailable(str(rebind_error)) from None
                raise HostRestorationError(
                    f"driver rebind failed; the donor may be unbound: {rebind_error}"
                ) from rebind_error
            if traced_probe_ok:
                end = time.monotonic() + duration_sec
                if exercise is not None:
                    try:
                        (tracefs / "trace_marker").write_text(EXERCISE_MARK)
                    except OSError as error:
                        # Without the marker, exercise reads would pass for
                        # post-reset values: do not exercise.
                        skipped_exercise.append(
                            f"device exercises: trace marker unavailable ({error})"
                        )
                        exercise = None
                if exercise is not None:
                    # Use the device through its driver, inside the trace,
                    # bounded: the host runs on one CPU until the tracer stops.
                    exerciser = threading.Thread(
                        target=lambda: exercise_status.update(
                            report=exercise(time.monotonic() + EXERCISE_BUDGET_SEC)
                        ),
                        daemon=True,
                    )
                    exerciser.start()
                    exerciser.join(EXERCISE_BUDGET_SEC + EXERCISE_GRACE_SEC)
                    if exerciser.is_alive():
                        incomplete.append("device exercise exceeded its budget")
                time.sleep(max(0.0, end - time.monotonic()))
        finally:
            # The pipe must be closed before the tracer can be switched.
            stop.set()
            reader.join(timeout=5.0)
            if reader.is_alive():
                incomplete.append("trace reader did not finish")
            elif not reader_status.get("done"):
                incomplete.append(reader_status.get("error", "trace read ended"))
            os.close(fd)
    finally:
        problem = _stop_tracer(tracefs, cpus_before, tracing_on)
        incident = None
        try:
            # Tracer is off and CPUs are back. Workers still running keep
            # the host lock held while they finish their own restoration.
            if worker.is_alive():
                worker.join(PROBE_DEADLINE_SEC)
                if worker.is_alive():
                    incident = "driver rebind did not finish"
            if exerciser is not None and exerciser.is_alive():
                exerciser.join(EXERCISE_BUDGET_SEC * 3)
                if exerciser.is_alive():
                    incident = "device exercise did not finish its undo"
            if (
                incident is None
                and problem is None
                and traced_probe_ok
                and len(cpus_before) > 1
            ):
                restore.update(_restoring_reprobe(rebind))
                incident = restore.get("incident")
        finally:
            if previous_term is not None:
                signal.signal(signal.SIGTERM, previous_term)
        if problem is None and incident is None:
            journal.unlink(missing_ok=True)
        else:
            if incident is not None:
                _write_incident(journal, incident)
            log_warning_safe(
                logger,
                safe_format(
                    "{p}; journal kept at {j}",
                    p="; ".join(x for x in (problem, incident) if x),
                    j=str(journal),
                ),
                prefix="MMIO",
            )
            # Raised here so it replaces (and chains) any error in flight:
            # a degraded host must never surface as a harmless gap.
            raise HostRestorationError(
                "; ".join(x for x in (problem, incident) if x)
                + f"; the next capture refuses or recovers via {journal}"
            )
    if "error" in rebind_status:
        raise HostRestorationError(
            f"driver rebind failed after the deadline: {rebind_status['error']}"
        )
    troubles = kernel_log.troubles()
    kernel_log.close()
    if troubles is None:
        incomplete.append("kernel log unreadable; tracer faults unverifiable")
    elif troubles:
        incomplete.append("tracer faults in kernel log: " + "; ".join(troubles[:3]))
    text = b"".join(chunks).decode(errors="replace")
    result = parse_mmiotrace(text.splitlines(), bars)
    if not result.mapped_bars:
        raise ObserverUnavailable(
            "no donor BAR mapping was traced (lockdown, nommiotrace, or the "
            "driver did not map a BAR during probe)"
        )
    result.capture_incomplete += len(incomplete)
    result.notes.extend(notes + incomplete)
    if reset_state.get("note"):
        result.notes.append(reset_state["note"])
    result.reset_performed = reset_state["performed"]
    if len(cpus_before) > 1:
        result.notes.append(
            "learned queue/vector registers reflect a one-CPU configuration"
        )
    if restore.get("refused"):
        result.host_changes.append(restore["refused"])
    result.exercises_skipped.extend(skipped_exercise)
    exercise_report = exercise_status.get("report")
    if exercise_report is not None:
        result.notes.extend(exercise_report.notes())
        result.host_changes.extend(exercise_report.not_restored)
        result.exercises_skipped.extend(exercise_report.skipped)
        result.exercises_run.extend(exercise_report.ran)
    log_info_safe(
        logger,
        safe_format(
            "mmiotrace: {n} donor accesses, {d} dropped",
            n=len(result.observations),
            d=result.dropped_events,
        ),
        prefix="MMIO",
    )
    return result


def _restoring_reprobe(rebind: Callable[[Callable[[], None]], None]) -> dict:
    """Re-probe once, untraced, with every CPU online.

    Drivers size per-CPU resources (queues, vectors) at probe time and the
    traced probe ran on one CPU. Same guarded rebind (in-use checks, binding
    journal), bounded by the probe deadline. A refusal leaves the driver
    bound and is reported; any other failure is a restoration incident.
    """
    status: dict = {}

    def run() -> None:
        try:
            rebind(lambda: None)
        except BaseException as error:  # reported below
            status["error"] = error

    thread = threading.Thread(target=run, daemon=True)
    thread.start()
    thread.join(PROBE_DEADLINE_SEC)
    if thread.is_alive():
        thread.join(PROBE_DEADLINE_SEC)
        if thread.is_alive():
            return {"incident": "restoring re-probe did not finish"}
    error = status.get("error")
    if error is None:
        return {}
    if isinstance(error, RuntimeError) and str(error).startswith("Refusing"):
        return {
            "refused": "driver keeps its one-CPU probe configuration (restoring "
            f"re-probe refused: {error}); re-bind it to restore per-CPU "
            "queues/vectors"
        }
    return {"incident": f"restoring re-probe failed: {error}"}


def bar_resources(resource_text: str, config: bytes) -> list[BarResource]:
    """Memory BARs with physical starts, from the sysfs ``resource`` file."""
    bars = []
    for index, row in enumerate(resource_text.splitlines()[:6]):
        start, end, flags = (int(v, 16) for v in row.split())
        if start == end == 0 or not flags & 0x200 or config[16 + index * 4] & 1:
            continue
        bars.append(
            BarResource(bar_index=index, physical_start=start, size=end - start + 1)
        )
    return bars


def msix_ranges(config: bytes) -> list[tuple[int, int, int]]:
    """``(bar, start, end)`` of the MSI-X table and PBA, if advertised.

    These have their own semantics (vector masking, pending bits, interrupt
    delivery) and are served by the MSI-X implementation, never as learned
    generic registers. Uses the same strict parser as the build-side layout
    (``msix_layout``), so capture exclusion and build ownership cannot diverge.
    A capture whose capability chain cannot be read fails rather than
    excluding a guessed window, or none.
    """
    from pcileechfwgenerator.device_clone.msix_layout import (
        MsixLayoutError,
        pba_size_bytes,
        read_msix_capability,
    )

    try:
        cap = read_msix_capability(config)
    except MsixLayoutError as error:
        raise ObserverUnavailable(f"MSI-X layout unreadable: {error}") from None
    if cap is None:
        return []
    table, pba = cap.table_offset, cap.pba_offset
    return [
        (cap.table_bir, table, table + 16 * cap.table_size),
        (cap.pba_bir, pba, pba + pba_size_bytes(cap.table_size)),
    ]


def drop_reserved_ranges(
    result: ObserverResult, ranges: Sequence[tuple[int, int, int]]
) -> None:
    """Remove accesses inside reserved ranges (MSI-X) from the evidence.

    The generated BAR has no MSI-X table/PBA implementation: these ranges
    are reported as unserved, not handed to anything.
    """
    if not ranges:
        return
    for bar, start, end in ranges:
        result.excluded_ranges.append(
            f"MSI-X table/PBA BAR{bar} [{start:#x}, {end:#x}) is not served "
            "(no MSI-X table implementation); vector programming there is "
            "not emulated"
        )

    def reserved(obs: Observation) -> bool:
        ref = obs.ref
        return any(
            ref.bar_index == bar and ref.offset < end and start < ref.offset + ref.width
            for bar, start, end in ranges
        )

    kept = [o for o in result.observations if not reserved(o)]
    if len(kept) != len(result.observations):
        result.notes.append(
            f"{len(result.observations) - len(kept)} MSI-X table/PBA accesses "
            "excluded from the evidence"
        )
    result.observations[:] = kept


def native_probe_observer(
    bdf: str, *, duration_sec: float = 5.0, exercise: bool | None = None
) -> Callable[[object, int], ObserverResult]:
    """Session passive-observer callback for one donor (Stage 1 only)."""

    def observe(_discovery: object, epoch: int) -> ObserverResult:
        device = Path("/sys/bus/pci/devices") / bdf
        try:
            with (device / "config").open("rb") as stream:
                config = stream.read(256)
            bars = bar_resources((device / "resource").read_text(), config[:64])
        except (OSError, ValueError) as error:
            raise ObserverUnavailable(f"BAR resources unreadable: {error}") from None
        from pcileechfwgenerator.host_collect.exercise import (
            device_exercise,
            exercise_enabled,
        )

        use = exercise_enabled() if exercise is None else exercise
        result = capture_native_probe(
            bdf,
            bars,
            duration_sec=duration_sec,
            exercise=device_exercise(device) if use else None,
        )
        drop_reserved_ranges(result, msix_ranges(config))
        if epoch:
            result.observations[:] = [
                o.model_copy(update={"state_epoch": epoch}) for o in result.observations
            ]
        return result

    return observe
