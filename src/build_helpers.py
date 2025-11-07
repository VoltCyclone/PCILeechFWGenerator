#!/usr/bin/env python3
"""
PCILeech Firmware Build - Helper Library

"""

from __future__ import annotations

import logging
import sys
from pathlib import Path
from typing import Any, Callable, Dict, List, Union

# Project logging helpers (use these instead of direct logger calls)
from src.string_utils import (
    log_debug_safe,
    log_error_safe,
    log_info_safe,
    log_warning_safe,
    safe_format,
)

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Path helpers
# ---------------------------------------------------------------------------


def add_src_to_path() -> None:
    """Ensure `<project‑root>/src` appears exactly once in sys.path at front.

    Removes any pre-existing duplicates (including symlink/relative variants)
    before inserting the canonical resolved path at position 0.
    """
    src = (Path(__file__).resolve().parent.parent / "src").resolve()
    if not src.exists():
        raise RuntimeError(f"Expected src directory not found: {src}")

    # Drop all entries resolving to the same path to guarantee idempotency
    normalized = src
    sys.path[:] = [p for p in sys.path if Path(p).resolve() != normalized]

    # Prepend canonical path
    sys.path.insert(0, str(src))
    log_debug_safe(
        logger, "Ensured {src} is first on PYTHONPATH", prefix="BUILD", src=src
    )


# ---------------------------------------------------------------------------
# PCIe IP core selection + FPGA strategy
# ---------------------------------------------------------------------------


def select_pcie_ip_core(fpga_part: str) -> str:
    """Return the canonical Xilinx IP core name for *fpga_part*."""
    part = fpga_part.lower()
    if part.startswith("xc7a35t"):
        return "axi_pcie"  # small Artix‑7
    if part.startswith("xc7a75t") or part.startswith("xc7k"):
        return "pcie_7x"  # larger Artix‑7 / Kintex‑7
    if part.startswith("xczu"):
        return "pcie_ultrascale"  # Zynq UltraScale+
    log_warning_safe(
        logger,
        safe_format(
            "Unknown FPGA part '{fpga_part}' - defaulting to pcie_7x",
            prefix="BUILD",
            fpga_part=fpga_part,
        ),
        prefix="BUILD",
    )
    return "pcie_7x"


def create_fpga_strategy_selector() -> Callable[[str], Dict[str, Any]]:
    """Return a *strategy(fpga_part) -> dict* chooser for per‑family params."""

    def artix35(_) -> Dict[str, Any]:
        return {
            "pcie_ip_type": "axi_pcie",
            "family": "artix7",
            "max_lanes": 4,
            "supports_msi": True,
            "supports_msix": False,
            "clock_constraints": "artix7_35t.xdc",
        }

    def artix75_or_kintex(_) -> Dict[str, Any]:
        fam = "kintex7" if _.startswith("xc7k") else "artix7"
        return {
            "pcie_ip_type": "pcie_7x",
            "family": fam,
            # 7-series lane support varies by family/package:
            # - Artix-7: up to x4
            # - Kintex-7: up to x8 (device/package dependent)
            # Default conservatively for Artix-7 to x4 to avoid invalid IP settings.
            "max_lanes": 8 if fam == "kintex7" else 4,
            "supports_msi": True,
            "supports_msix": True,
            "clock_constraints": f"{fam}.xdc",
        }

    def ultrascale(_) -> Dict[str, Any]:
        return {
            "pcie_ip_type": "pcie_ultrascale",
            "family": "zynq_ultrascale",
            "max_lanes": 16,
            "supports_msi": True,
            "supports_msix": True,
            "clock_constraints": "zynq_ultrascale.xdc",
        }

    strategies: Dict[str, Callable[[str], Dict[str, Any]]] = {
        "xc7a35t": artix35,
        "xc7a75t": artix75_or_kintex,
        "xc7k": artix75_or_kintex,
        "xczu": ultrascale,
    }

    def select(fpga_part: str) -> Dict[str, Any]:
        part = fpga_part.lower()
        for prefix, fn in strategies.items():
            if part.startswith(prefix):
                return fn(fpga_part)
        log_warning_safe(
            logger,
            safe_format(
                "No dedicated strategy for '{fpga_part}' - using generic defaults",
                prefix="BUILD",
                fpga_part=fpga_part,
            ),
            prefix="BUILD",
        )
        # Generic 7-series default: treat as Artix-7 constraints (x4 max)
        return artix75_or_kintex(fpga_part)

    return select


# ---------------------------------------------------------------------------
# TCL helpers
# ---------------------------------------------------------------------------


def write_tcl_file(
    content: str,
    file_path: Union[str, Path],
    tcl_files: List[str],
    description: str,
) -> None:
    """Write *content* to *file_path*, append to *tcl_files*, log success."""
    path = Path(file_path)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(content, encoding="utf-8")
    # Track by human-friendly description and by absolute path for consumers
    if description not in tcl_files:
        tcl_files.append(description)
    abs_path = str(path)
    if abs_path not in tcl_files:
        tcl_files.append(abs_path)
    log_info_safe(
        logger,
        safe_format("Generated {description}", description=description),
        prefix="BUILD",
    )


def batch_write_tcl_files(
    tcl_contents: Dict[str, str],
    output_dir: Union[str, Path],
    tcl_files: List[str],
    logger: logging.Logger,
) -> None:
    """Write many TCL files under *output_dir*.

    Raises on the first failure - strict mode implies partial writes are fatal.
    """
    out = Path(output_dir)
    successes = 0
    for name, content in tcl_contents.items():
        write_tcl_file(content, out / name, tcl_files, name)
        successes += 1
    log_info_safe(
        logger,
        safe_format(
            "Batch TCL write complete: {successes}/{total} files",
            prefix="BUILD",
            successes=successes,
            total=len(tcl_contents),
        ),
        prefix="BUILD",
    )


# ---------------------------------------------------------------------------
# Misc
# ---------------------------------------------------------------------------


def validate_fpga_part(fpga_part: str) -> bool:
    """Light sanity‑check for *fpga_part* strings."""
    prefixes = ("xc7a", "xc7k", "xc7v", "xczu", "xck", "xcvu")
    ok = bool(fpga_part) and fpga_part.lower().startswith(prefixes)
    if not ok:
        log_error_safe(
            logger,
            safe_format(
                "Invalid or unsupported FPGA part: {fpga_part}",
                prefix="BUILD",
                fpga_part=fpga_part,
            ),
            prefix="BUILD",
        )
    return ok
