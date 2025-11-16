#!/usr/bin/env python3
"""Utilities for repairing Vivado IP artifacts in the datastore."""

from __future__ import annotations

from pathlib import Path
from stat import S_IWGRP, S_IWOTH, S_IWUSR
from typing import Dict, List

from src.log_config import get_logger
from src.string_utils import (
    log_debug_safe,
    log_info_safe,
    log_warning_safe,
    safe_format,
)

LOCK_SUFFIXES = (".lck", ".lock")
IP_FILE_SUFFIXES = (".xci", ".xcix")


def _discover_ip_dirs(root: Path) -> List[Path]:
    """Return every `ip` directory rooted at *root*."""
    ip_dirs: List[Path] = []
    visited = set()

    direct = root / "ip"
    if direct.is_dir():
        ip_dirs.append(direct)
        visited.add(direct.resolve())

    for candidate in root.rglob("ip"):
        if not candidate.is_dir():
            continue
        resolved = candidate.resolve()
        if resolved in visited:
            continue
        ip_dirs.append(candidate)
        visited.add(resolved)

    return ip_dirs


def _remove_lock_files(ip_dir: Path, prefix: str, logger) -> int:
    removed = 0
    for suffix in LOCK_SUFFIXES:
        for lock_file in ip_dir.rglob(f"*{suffix}"):
            try:
                lock_file.unlink()
                removed += 1
                log_info_safe(
                    logger,
                    safe_format(
                        "Removed stale lock file: {path}",
                        path=str(lock_file),
                    ),
                    prefix=prefix,
                )
            except FileNotFoundError:
                continue
            except Exception as exc:  # pragma: no cover - unexpected fs error
                log_warning_safe(
                    logger,
                    safe_format(
                        "Unable to remove lock file {path}: {err}",
                        path=str(lock_file),
                        err=str(exc),
                    ),
                    prefix=prefix,
                )
    return removed


def _ensure_writable(ip_dir: Path, prefix: str, logger) -> int:
    repaired = 0
    for suffix in IP_FILE_SUFFIXES:
        for ip_file in ip_dir.rglob(f"*{suffix}"):
            try:
                current_mode = ip_file.stat().st_mode
                desired_mode = current_mode | S_IWUSR | S_IWGRP | S_IWOTH
                if current_mode != desired_mode:
                    ip_file.chmod(desired_mode)
                    repaired += 1
                    log_debug_safe(
                        logger,
                        safe_format(
                            "Ensured write access for IP file: {path}",
                            path=str(ip_file),
                        ),
                        prefix=prefix,
                    )
            except FileNotFoundError:
                continue
            except PermissionError as exc:
                log_warning_safe(
                    logger,
                    safe_format(
                        "Permission error updating {path}: {err}",
                        path=str(ip_file),
                        err=str(exc),
                    ),
                    prefix=prefix,
                )
            except Exception as exc:  # pragma: no cover - unexpected fs error
                log_warning_safe(
                    logger,
                    safe_format(
                        "Failed to adjust permissions for {path}: {err}",
                        path=str(ip_file),
                        err=str(exc),
                    ),
                    prefix=prefix,
                )
    return repaired


def repair_ip_artifacts(
    output_root: Path,
    logger=None,
    prefix: str = "VIVADO",
) -> Dict[str, int]:
    """Remove stale lock files and ensure Vivado IP files are writable."""
    logger = logger or get_logger(__name__)
    root = Path(output_root)
    if not root.exists():
        log_debug_safe(
            logger,
            safe_format(
                "Output root not found for IP repair: {path}",
                path=str(root),
            ),
            prefix=prefix,
        )
        return {"ip_dirs": 0, "locks_removed": 0, "files_repaired": 0}

    ip_dirs = _discover_ip_dirs(root)
    if not ip_dirs:
        log_debug_safe(
            logger,
            safe_format("No IP directories discovered under {path}", path=str(root)),
            prefix=prefix,
        )
        return {"ip_dirs": 0, "locks_removed": 0, "files_repaired": 0}

    total_locks = 0
    total_repaired = 0
    for ip_dir in ip_dirs:
        total_locks += _remove_lock_files(ip_dir, prefix, logger)
        total_repaired += _ensure_writable(ip_dir, prefix, logger)

    log_info_safe(
        logger,
        safe_format(
            "IP artifact repair complete → dirs={dirs} locks={locks} files={files}",
            dirs=len(ip_dirs),
            locks=total_locks,
            files=total_repaired,
        ),
        prefix=prefix,
    )
    return {
        "ip_dirs": len(ip_dirs),
        "locks_removed": total_locks,
        "files_repaired": total_repaired,
    }
