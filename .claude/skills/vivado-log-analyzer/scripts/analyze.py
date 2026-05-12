#!/usr/bin/env python3
"""
Vivado log analyzer for PCILeechFWGenerator.

Extracts only the actionable lines from Vivado synthesis/implementation logs
and reports. Designed to keep failure diagnoses small enough to fit in an LLM
context window.

Usage:
    analyze.py <path>

<path> can be:
    - a single .log or .rpt file
    - a Vivado .runs/ directory (will scan synth_1 / impl_1)
    - any directory (will glob for *.log and *_summary.rpt below it)
"""

from __future__ import annotations

import re
import sys
from collections import defaultdict
from pathlib import Path
from typing import Iterable

MAX_LINES_PER_CATEGORY = 20
MAX_TOTAL_LINES = 80

ERROR_RE = re.compile(r"^(ERROR|CRITICAL WARNING):\s*\[([^\]]+)\]\s*(.*)$")
TIMING_FAIL_RE = re.compile(r"^\s*(WNS|WHS|WPWS|TNS|THS|TPWS)\s*\(ns\):\s*(-\d[\d.]*)")
UTIL_HIGH_RE = re.compile(
    r"^\|\s*(LUT|FF|BRAM Tile|RAMB36|RAMB18|DSP|URAM|IO)\s*\|.*\|\s*([0-9]{2,3}\.\d+)\s*\|"
)
DRC_RE = re.compile(r"^(?:CRITICAL WARNING|ERROR):\s*\[DRC\s+([A-Z0-9-]+)\]\s*(.*)$")
LICENSE_RE = re.compile(r"\b(Common 17-69|licensing|no valid license)\b", re.I)
MISSING_FILE_RE = re.compile(r"Cannot find|does not exist|no such file", re.I)


def iter_lines(path: Path) -> Iterable[str]:
    try:
        with path.open("r", encoding="utf-8", errors="replace") as fh:
            for line in fh:
                yield line.rstrip("\n")
    except OSError as exc:  # noqa: PERF203 — tiny outer loop
        print(f"# could not read {path}: {exc}", file=sys.stderr)


def collect_logs(root: Path) -> list[Path]:
    if root.is_file():
        return [root]
    patterns = [
        "vivado.log",
        "**/runme.log",
        "**/vivado.log",
        "**/*_timing_summary*.rpt",
        "**/*_utilization*.rpt",
        "**/*_drc*.rpt",
    ]
    found: set[Path] = set()
    for pat in patterns:
        for hit in root.glob(pat):
            if hit.is_file() and hit.stat().st_size > 0:
                found.add(hit)
    return sorted(found)


def categorize(line: str) -> tuple[str, str] | None:
    """Return (category, normalized_line) or None to skip."""
    m = ERROR_RE.match(line)
    if m:
        severity, code, msg = m.groups()
        # IP licensing → its own bucket so the fix is obvious
        if LICENSE_RE.search(line):
            return "ip-licensing", f"{severity} [{code}] {msg}"
        if code.startswith("Synth"):
            return "synthesis", f"{severity} [{code}] {msg}"
        if code.startswith("Place") or code.startswith("Route"):
            return "place-route", f"{severity} [{code}] {msg}"
        if code.startswith("Timing"):
            return "timing", f"{severity} [{code}] {msg}"
        if code.startswith("DRC"):
            return "drc", f"{severity} [{code}] {msg}"
        if MISSING_FILE_RE.search(msg):
            return "missing-files", f"{severity} [{code}] {msg}"
        return "other-errors", f"{severity} [{code}] {msg}"

    m = TIMING_FAIL_RE.match(line)
    if m:
        metric, ns = m.groups()
        return "timing", f"{metric} = {ns} ns (negative slack)"

    m = UTIL_HIGH_RE.match(line)
    if m:
        resource, pct = m.groups()
        if float(pct) >= 95.0:
            return "resource-exhaustion", f"{resource}: {pct}% utilization"

    return None


def main(argv: list[str]) -> int:
    if len(argv) != 2:
        print(__doc__.strip(), file=sys.stderr)
        return 2

    root = Path(argv[1]).expanduser().resolve()
    if not root.exists():
        print(f"path does not exist: {root}", file=sys.stderr)
        return 2

    logs = collect_logs(root)
    if not logs:
        print(f"no Vivado logs or reports found under {root}", file=sys.stderr)
        return 1

    buckets: dict[str, list[str]] = defaultdict(list)
    sources: dict[str, set[str]] = defaultdict(set)

    for log in logs:
        for line in iter_lines(log):
            hit = categorize(line)
            if hit is None:
                continue
            category, normalized = hit
            if normalized in buckets[category]:
                continue
            buckets[category].append(normalized)
            sources[category].add(log.name)

    if not buckets:
        print("# No Vivado ERROR / CRITICAL WARNING lines found.")
        print(f"# Scanned {len(logs)} file(s) under {root}.")
        print("# If the build did fail, check the run directory exit status or")
        print("# the bitstream/DRC report directly.")
        return 0

    order = [
        "ip-licensing",
        "missing-files",
        "synthesis",
        "place-route",
        "timing",
        "drc",
        "resource-exhaustion",
        "other-errors",
    ]

    total_emitted = 0
    out: list[str] = [f"# Vivado log summary — {len(logs)} file(s) scanned\n"]
    for cat in order:
        items = buckets.get(cat)
        if not items:
            continue
        src_list = ", ".join(sorted(sources[cat]))
        out.append(f"## {cat}  ({len(items)} unique, from: {src_list})")
        for line in items[:MAX_LINES_PER_CATEGORY]:
            out.append(f"  - {line}")
            total_emitted += 1
            if total_emitted >= MAX_TOTAL_LINES:
                out.append("  - ... (truncated; rerun the script on a narrower path)")
                print("\n".join(out))
                return 0
        if len(items) > MAX_LINES_PER_CATEGORY:
            out.append(
                f"  - ... {len(items) - MAX_LINES_PER_CATEGORY} more in this category"
            )
        out.append("")

    print("\n".join(out))
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv))
