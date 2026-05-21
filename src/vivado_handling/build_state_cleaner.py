#!/usr/bin/env python3
"""Pre-build wipe of Vivado-generated state from a prior run.

Vivado leaves a project tree (``vivado_project/``) and scratch directories
(``.Xil/``) under the output dir.  Re-running in the same dir causes Vivado
to re-open the prior project, producing a cascade of "IP locked", "file
already in project", and "no reference checkpoint for incremental compile"
warnings — and can mask source-level changes by serving cached IP outputs.
"""
from __future__ import annotations

import shutil
from pathlib import Path
from typing import Dict, Iterable

from pcileechfwgenerator.log_config import get_logger
from pcileechfwgenerator.string_utils import (
    log_debug_safe,
    log_info_safe,
    log_warning_safe,
    safe_format,
)


def _remove(path: Path, *, is_dir: bool, prefix: str, logger) -> int:
    """Delete *path*.  Returns 1 on success, 0 on missing/failure."""
    try:
        if is_dir:
            shutil.rmtree(path)
        else:
            path.unlink()
    except FileNotFoundError:
        return 0
    except OSError as exc:
        log_warning_safe(
            logger,
            safe_format("Failed to remove {path}: {err}", path=str(path), err=str(exc)),
            prefix=prefix,
        )
        return 0

    log_fn = log_info_safe if is_dir else log_debug_safe
    log_fn(
        logger,
        safe_format("Removed stale: {path}", path=str(path)),
        prefix=prefix,
    )
    return 1


def _iter_xil_dirs(root: Path) -> Iterable[Path]:
    """Yield every ``.Xil`` directory under *root*, de-duped via resolved path
    so a symlink loop can't cause repeat visits."""
    seen: set[Path] = set()
    for candidate in root.rglob(".Xil"):
        if not candidate.is_dir():
            continue
        resolved = candidate.resolve()
        if resolved in seen:
            continue
        seen.add(resolved)
        yield candidate


def clean_stale_build_state(
    output_dir: Path,
    *,
    logger=None,
    prefix: str = "BUILD",
) -> Dict[str, int]:
    """Wipe Vivado-generated state from a prior build.  Idempotent.

    Removed: ``vivado_project/``; every ``.Xil/`` under the tree;
    top-level ``*.jou`` and ``*.str``.

    Preserved: everything else — sources, IP, constraints, bitstreams,
    reports, logs, scripts, manifests, and any user-placed files.

    Returns counts: ``vivado_project_removed``, ``xil_dirs_removed``,
    ``files_removed``.
    """
    logger = logger or get_logger(__name__)
    summary = {
        "vivado_project_removed": 0,
        "xil_dirs_removed": 0,
        "files_removed": 0,
    }

    root = Path(output_dir)
    if not root.is_dir():
        log_debug_safe(
            logger,
            safe_format("No output dir to clean: {path}", path=str(root)),
            prefix=prefix,
        )
        return summary

    project_dir = root / "vivado_project"
    if project_dir.is_dir():
        summary["vivado_project_removed"] = _remove(
            project_dir, is_dir=True, prefix=prefix, logger=logger
        )

    for xil_dir in _iter_xil_dirs(root):
        summary["xil_dirs_removed"] += _remove(
            xil_dir, is_dir=True, prefix=prefix, logger=logger
        )

    for pattern in ("*.jou", "*.str"):
        for stale in root.glob(pattern):
            if stale.is_file():
                summary["files_removed"] += _remove(
                    stale, is_dir=False, prefix=prefix, logger=logger
                )

    if any(summary.values()):
        log_info_safe(
            logger,
            safe_format(
                "Cleaned stale build state: project={proj} xil={xil} files={files}",
                proj=summary["vivado_project_removed"],
                xil=summary["xil_dirs_removed"],
                files=summary["files_removed"],
            ),
            prefix=prefix,
        )
    else:
        log_debug_safe(logger, "No stale build state found.", prefix=prefix)

    return summary
