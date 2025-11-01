"""
Light‑weight helpers for locating and invoking Xilinx Vivado

"""

from __future__ import annotations

import logging
import os
import platform
import shutil
import subprocess
import sys
import time
from pathlib import Path
from typing import Dict, List, Optional, Union

from string_utils import (
    safe_format,
    log_info_safe,
    log_warning_safe,
    log_debug_safe,
)

LOG = logging.getLogger(__name__)

# ───────────────────────── Constants ──────────────────────────
IS_LINUX = platform.system().lower() == "linux"

DEFAULT_BASES: List[Path] = []
if IS_LINUX:
    DEFAULT_BASES = [
        Path("/opt/Xilinx/Vivado"),
        Path("/tools/Xilinx/Vivado"),
        Path("/usr/local/Xilinx/Vivado"),
        Path.home() / "Xilinx" / "Vivado",
    ]
    ## OSX isnt really supported this is for unit tests
elif platform.system().lower() == "darwin":
    DEFAULT_BASES = [
        Path("/Applications/Xilinx/Vivado"),
        Path("/tools/Xilinx/Vivado"),
        Path("/usr/local/Xilinx/Vivado"),
        Path.home() / "Xilinx" / "Vivado",
    ]
else:
    raise RuntimeError("Vivado utilities are only supported on Linux.")


TOOLS_ROOT = Path("/tools/Xilinx")  # pattern: /tools/Xilinx/<version>/Vivado

# ───────────────────────── Internals ──────────────────────────

def _iter_candidate_dirs():
    """Yield all plausible Vivado install roots.*Not* the *bin* dir."""
    # 1) PATH — fast path
    if vivado := shutil.which("vivado"):
        yield Path(vivado).parent.parent  # bin/ -> Vivado/

    # 2) Environment variable
    if env := os.getenv("XILINX_VIVADO"):
        yield Path(env)

    # 3) Standard locations
    yield from DEFAULT_BASES

    # 4) /tools/Xilinx/<ver>/Vivado pattern
    if TOOLS_ROOT.exists():
        for child in TOOLS_ROOT.iterdir():
            if child.is_dir() and child.name[0].isdigit() and "." in child.name:
                candidate = child / "Vivado"
                yield candidate


def _vivado_executable(dir_: Path) -> Optional[Path]:
    """Return the vivado executable inside *dir_* if it exists."""
    exe = dir_ / "bin" / "vivado"
    return exe if exe.is_file() else None


def _VIVADOct_version(dir_: Path) -> str:
    """Infer version string from directory name (fallback to runtime query)."""
    for part in dir_.parts:
        if part[0].isdigit() and "." in part:
            return part
    return "unknown"


# ───────────────────────── Public API ──────────────────────────


def find_vivado_installation(
    manual_path: Optional[str] = None,
) -> Optional[Dict[str, str]]:
    """Return a dict with keys *path, bin_path, executable, version* or *None*.

    Args:
        manual_path: Optional manual path to Vivado installation directory
    """

    # First, check if manual path is specified via parameter (takes highest priority)
    if manual_path:
        manual_path_obj = Path(manual_path)
        if manual_path_obj.exists() and manual_path_obj.is_dir():
            exe = _vivado_executable(manual_path_obj)
            if exe:
                version = get_vivado_version(str(exe)) or _VIVADOct_version(
                    manual_path_obj
                )
                log_info_safe(
                    LOG,
                    safe_format("Using manually specified Vivado installation"),
                    prefix="VIVADO",
                )
                return {
                    "path": str(manual_path_obj),
                    "bin_path": str(manual_path_obj / "bin"),
                    "executable": str(exe),
                    "version": version,
                }
            else:
                log_warning_safe(
                    LOG,
                    safe_format(
                        "Manual Vivado path specified but vivado executable not found: {path}",
                        path=manual_path,
                    ),
                    prefix="VIVADO",
                )
        else:
            log_warning_safe(
                LOG,
                safe_format(
                    "Manual Vivado path specified but directory doesn't exist: {path}",
                    path=manual_path,
                ),
                prefix="VIVADO",
            )

    # Fallback to automatic VIVADOction
    for root in _iter_candidate_dirs():
        exe = _vivado_executable(root)
        if not exe:
            continue
        version = get_vivado_version(str(exe)) or _VIVADOct_version(root)
        log_debug_safe(
            LOG,
            safe_format(
                "Vivado candidate: {exe} (v{version})", exe=exe, version=version
                ),
            prefix="VIVADO",
        )
        return {
            "path": str(root),
            "bin_path": str(root / "bin"),
            "executable": str(exe),
            "version": version,
        }

    log_warning_safe(
        LOG,
        safe_format(
            "Vivado installation not found. Use --vivado-path to specify manual installation path."
        ),
        prefix="VIVADO",
    )
    return None


def get_vivado_search_paths() -> List[str]:
    """Return *human‑readable* list of search locations (for diagnostics)."""
    paths: List[str] = ["System PATH"]
    paths.extend(str(p) for p in DEFAULT_BASES)
    if TOOLS_ROOT.exists():
        paths.append("/tools/Xilinx/<version>/Vivado")
    paths.append(f"XILINX_VIVADO={os.getenv('XILINX_VIVADO', '<not set>')}")
    return paths


def get_vivado_version(vivado_exec: str) -> str:
    """Call *vivado -version* with a 5‑second timeout to parse the version."""
    try:
        res = subprocess.run(
            [vivado_exec, "-version"],
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
            timeout=5,
            check=False,
        )
        if res.returncode == 0:
            for line in res.stdout.splitlines():
                if "vivado" in line.lower() and "v" in line:
                    for tok in line.split():
                        if tok.startswith("v") and "." in tok:
                            return tok.lstrip("v")
    except (subprocess.SubprocessError, FileNotFoundError, PermissionError):
        pass
    return "unknown"


def run_vivado_command(
    args: Union[str, List[str]],
    *,
    tcl_file: Optional[str] = None,
    cwd: Optional[Union[str, Path]] = None,
    timeout: Optional[int] = None,
    use_discovered: bool = True,
    enable_error_reporting: bool = True,
) -> subprocess.CompletedProcess:
    """Invoke Vivado with *args* (string or list). Enhanced with error reporting."""
    exe: Optional[str] = None
    if use_discovered:
        info = find_vivado_installation()
        if info:
            exe = info["executable"]
    exe = exe or shutil.which("vivado")
    if not exe:
        raise FileNotFoundError(
            "Vivado executable not found. Ensure it is in PATH or set XILINX_VIVADO."
        )

    cmd: List[str] = [exe]
    cmd.extend(args.split() if isinstance(args, str) else args)
    if tcl_file:
        cmd.extend(["-source", str(tcl_file)])

    log_info_safe(
        LOG,
        safe_format("Running: {cmd}", cmd=" ".join(cmd)),
        prefix="RUN"
    )

    if enable_error_reporting:
        try:
            # Use lazy/dynamic import to avoid circular dependency
            # Import within the function scope only when needed
            import importlib

            vivado_error_reporter_module = importlib.import_module(
                ".vivado_error_reporter", package="src.vivado_handling"
            )
            VivadoErrorReporter = getattr(
                vivado_error_reporter_module, "VivadoErrorReporter"
            )

            # Run with enhanced error reporting
            process = subprocess.Popen(
                cmd,
                stdout=subprocess.PIPE,
                stderr=subprocess.STDOUT,
                text=True,
                cwd=cwd,
            )

            reporter = VivadoErrorReporter(use_colors=True)
            return_code, errors, warnings = reporter.monitor_vivado_process(process)

            # Generate report if there were issues
            if errors or warnings:
                output_dir = Path(cwd) if cwd else Path(".")
                report = reporter.generate_error_report(
                    errors,
                    warnings,
                    "Vivado Command",
                    output_dir / "vivado_error_report.txt",
                )
                reporter.print_summary(errors, warnings)

            # Create a CompletedProcess-like object
            result = subprocess.CompletedProcess(
                cmd,
                return_code,
                stdout="",
                stderr="",  # Output was already printed by monitor
            )

            if return_code != 0:
                result.check_returncode()

            return result

        except ImportError:
            log_warning_safe(
                LOG,
                safe_format(
                    "Error reporter not available"
                ),
                prefix="RUN"
            )

    # Fallback to standard execution
    return subprocess.run(
        cmd,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
        cwd=cwd,
        timeout=timeout,
        check=True,
    )


def get_vivado_executable() -> Optional[str]:
    """Return the Vivado binary path or *None*."""
    info = find_vivado_installation()
    return info["executable"] if info else None


# ───────────────────────── Diagnostics ─────────────────────────


def debug_vivado_search() -> None:
    """Pretty print search logic and VIVADOction results (stdout‑only)."""
    print("# Vivado VIVADOction report ({}):".format(time.strftime("%F %T")))
    print("Search order:")
    for p in get_vivado_search_paths():
        print("  •", p)
    print()
    info = find_vivado_installation()
    if info:
        print("✔ Vivado found ->")
        for k, v in info.items():
            print(f"    {k:10}: {v}")
    else:
        print("✘ Vivado not located — check PATH or XILINX_VIVADO.")


if __name__ == "__main__":
    debug_vivado_search()
