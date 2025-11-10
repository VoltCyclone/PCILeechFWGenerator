#!/usr/bin/env python3
"""MMIO trace capture for learning BAR register maps dynamically.

This module captures MMIO read/write operations during device driver probe
using eBPF (bpftrace) to build device-specific register models.
"""

import os
import re
import shutil
import subprocess
import tempfile
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import List, Optional

from src.log_config import get_logger
from src.string_utils import (
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


@dataclass
class MmioTrace:
    """Complete MMIO trace for a single BAR."""

    bar_index: int
    bar_base_addr: int
    bar_size: int
    accesses: List[MmioAccess] = field(default_factory=list)
    duration_ns: int = 0


class MmioTracer:
    """Capture MMIO traffic during device operations using eBPF."""

    # bpftrace script template for MMIO tracing
    BPFTRACE_SCRIPT = """
BEGIN
{
  printf("ts,op,width,addr,val\\n");
}

kretprobe:ioread8
{
  printf("%llu,R,1,%p,%d\\n", nsecs, arg0, retval);
}

kprobe:iowrite8
{
  printf("%llu,W,1,%p,%d\\n", nsecs, arg1, arg0);
}

kretprobe:ioread16
{
  printf("%llu,R,2,%p,%d\\n", nsecs, arg0, retval);
}

kprobe:iowrite16
{
  printf("%llu,W,2,%p,%d\\n", nsecs, arg1, arg0);
}

kretprobe:ioread32
{
  printf("%llu,R,4,%p,%d\\n", nsecs, arg0, retval);
}

kprobe:iowrite32
{
  printf("%llu,W,4,%p,%d\\n", nsecs, arg1, arg0);
}
"""

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
        resource_file = Path(f"/sys/bus/pci/devices/{self.device_bdf}/resource")
        if not resource_file.exists():
            raise RuntimeError(
                safe_format(
                    "Device {bdf} not found in sysfs", bdf=self.device_bdf
                )
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
                        safe_format(
                            "Invalid resource line for BAR{idx}", idx=bar_index)
                    )

                start = int(parts[0], 16)
                end = int(parts[1], 16)
                size = end - start + 1 if end > start else 0

                return start, size

        except (IOError, ValueError) as e:
            raise RuntimeError(
                safe_format(
                    "Failed to read BAR{idx} info: {err}", idx=bar_index, err=str(e))
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

        # Create temp file for bpftrace script
        with tempfile.NamedTemporaryFile(mode="w", suffix=".bt", delete=False) as f:
            f.write(self.BPFTRACE_SCRIPT)
            script_path = f.name

        try:
            # Start bpftrace in background
            with tempfile.NamedTemporaryFile(mode="w+", suffix=".csv", delete=False) as outfile:
                output_path = outfile.name

            start_time = time.time_ns()

            cmd = ["bpftrace", script_path]
            log_debug_safe(
                logger,
                safe_format("Starting bpftrace: {cmd}", cmd=" ".join(cmd)),
                prefix="MMIO",
            )

            proc = subprocess.Popen(
                cmd,
                stdout=open(output_path, "w"),
                stderr=subprocess.PIPE,
                text=True,
            )

            # Give bpftrace time to attach probes
            time.sleep(1.0)

            # Optionally trigger driver rebind
            if trigger_rebind:
                self._trigger_driver_rebind()

            # Wait for capture duration
            time.sleep(duration_sec)

            # Stop bpftrace
            proc.terminate()
            try:
                proc.wait(timeout=5.0)
            except subprocess.TimeoutExpired:
                proc.kill()
                proc.wait()

            end_time = time.time_ns()

            # Parse captured trace
            accesses = self._parse_trace_output(output_path, bar_base, bar_size)

            log_info_safe(
                logger,
                safe_format(
                    "Captured {count} MMIO accesses for BAR{idx}",
                    count=len(accesses),
                    idx=bar_index,
                ),
                prefix="MMIO",
            )

            return MmioTrace(
                bar_index=bar_index,
                bar_base_addr=bar_base,
                bar_size=bar_size,
                accesses=accesses,
                duration_ns=end_time - start_time,
            )

        finally:
            # Cleanup temp files
            Path(script_path).unlink(missing_ok=True)
            Path(output_path).unlink(missing_ok=True)

    def _parse_trace_output(
        self, output_path: str, bar_base: int, bar_size: int
    ) -> List[MmioAccess]:
        """Parse bpftrace CSV output into MmioAccess list.

        Args:
            output_path: Path to CSV file
            bar_base: BAR base address
            bar_size: BAR size

        Returns:
            List of MmioAccess objects within BAR range
        """
        accesses = []
        addr_pattern = re.compile(r"0x([0-9a-fA-F]+)")

        try:
            with open(output_path, "r") as f:
                for line_num, line in enumerate(f, 1):
                    line = line.strip()
                    if not line or line.startswith("ts,"):
                        continue  # Skip header

                    parts = line.split(",")
                    if len(parts) < 5:
                        log_debug_safe(
                            logger,
                            safe_format("Skipping malformed line {num}: {line}",
                                        num=line_num, line=line),
                            prefix="MMIO",
                        )
                        continue

                    try:
                        ts = int(parts[0])
                        op = parts[1]
                        width = int(parts[2])
                        addr_str = parts[3]
                        val = int(parts[4])

                        # Extract hex address
                        match = addr_pattern.search(addr_str)
                        if not match:
                            continue

                        addr = int(match.group(1), 16)

                        # Convert to BAR-relative offset
                        offset = addr - bar_base

                        # Filter to BAR range
                        if 0 <= offset < bar_size:
                            accesses.append(
                                MmioAccess(
                                    timestamp_ns=ts,
                                    operation=op,
                                    width=width,
                                    offset=offset,
                                    value=val,
                                )
                            )

                    except (ValueError, IndexError) as e:
                        log_debug_safe(
                            logger,
                            safe_format(
                                "Failed to parse line {num}: {err}",
                                num=line_num,
                                err=str(e),
                            ),
                            prefix="MMIO",
                        )
                        continue

        except IOError as e:
            log_error_safe(
                logger,
                safe_format("Failed to read trace output: {err}", err=str(e)),
            )

        return accesses

    def _trigger_driver_rebind(self) -> None:
        """Trigger driver unbind/rebind to capture probe sequence.

        This is optional and may fail if device is in use.
        """
        log_info_safe(
            logger,
            safe_format("Triggering driver rebind for {bdf}", bdf=self.device_bdf),
            prefix="MMIO",
        )

        driver_path = Path(f"/sys/bus/pci/devices/{self.device_bdf}/driver")
        if not driver_path.exists():
            log_debug_safe(
                logger, "No driver bound, skipping rebind", prefix="MMIO"
            )
            return

        try:
            # Unbind
            with open(driver_path / "unbind", "w") as f:
                f.write(self.device_bdf)

            time.sleep(0.5)

            # Rebind via rescan
            with open("/sys/bus/pci/rescan", "w") as f:
                f.write("1")

            time.sleep(1.0)

        except (IOError, OSError) as e:
            log_debug_safe(
                logger,
                safe_format(
                    "Driver rebind failed (may be in use): {err}", err=str(e)),
                prefix="MMIO",
            )
