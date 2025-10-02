#!/usr/bin/env python3
"""Repository Manager

This utility clones, updates, and queries board‑specific files from the
`pcileech-fpga` repository.  It is written to be imported by other tools but can
also be executed directly to verify that the repository is present on the local
It provides methods to ensure the repository is cloned, check for updates, and
retrieve board paths and XDC files for various PCILeech boards.
"""
from __future__ import annotations

import datetime as _dt
import os as _os
import shutil as _shutil
import subprocess as _sp
import time as _time
from pathlib import Path
from typing import List, Optional

from ..log_config import get_logger
from ..string_utils import (log_debug_safe, log_error_safe, log_info_safe,
                            log_warning_safe, safe_format, utc_timestamp)

###############################################################################
# Configuration constants - override with environment vars if desired.
###############################################################################

DEFAULT_REPO_URL = _os.environ.get(
    "PCILEECH_FPGA_REPO_URL", "https://github.com/ufrisk/pcileech-fpga.git"
)
CACHE_DIR = Path(
    _os.environ.get(
        "PCILEECH_REPO_CACHE",
        _os.path.expanduser("~/.cache/pcileech-fw-generator/repos"),
    )
)
REPO_DIR = CACHE_DIR / "pcileech-fpga"
UPDATE_INTERVAL_DAYS = 7

###############################################################################
# Logging setup
###############################################################################

_logger = get_logger(__name__)

###############################################################################
# Helper utilities
###############################################################################


def _run(
    cmd: List[str], *, cwd: Optional[Path] = None, env: Optional[dict] = None
) -> _sp.CompletedProcess:
    """Run *cmd* and return the completed process, raising on error."""
    log_debug_safe(_logger, "Running {cmd} (cwd={cwd})", cmd=cmd, cwd=cwd)
    return _sp.run(
        cmd,
        cwd=str(cwd) if cwd else None,
        env=env,
        check=True,
        text=True,
    )


def _git_available() -> bool:
    """Return *True* if ``git`` is callable in the PATH."""
    try:
        _run(["git", "--version"], env={**_os.environ, "GIT_TERMINAL_PROMPT": "0"})
        return True
    except Exception:
        return False


###############################################################################
# Public API
###############################################################################


class RepoManager:
    """Utility class - no instantiation necessary."""

    def __new__(cls, *args, **kwargs):  # pragma: no cover - prevent misuse
        raise TypeError("RepoManager may not be instantiated; call class‑methods only")

    # ---------------------------------------------------------------------
    # Entry points
    # ---------------------------------------------------------------------

    @classmethod
    def ensure_repo(
        cls, *, repo_url: str = DEFAULT_REPO_URL, cache_dir: Path = CACHE_DIR
    ) -> Path:
        """Guarantee that a usable local clone exists and return its path."""
        cache_dir.mkdir(parents=True, exist_ok=True)
        repo_path = cache_dir / "pcileech-fpga"

        if cls._is_valid_repo(repo_path):
            cls._maybe_update(repo_path)
            return repo_path

        # Clean up anything that *looks* like a failed clone
        if repo_path.exists():
            log_warning_safe(
                _logger,
                safe_format(
                    "Removing invalid repo directory {repo_path}",
                    repo_path=repo_path,
                ),
                prefix="GIT",
            )
            _shutil.rmtree(repo_path, ignore_errors=True)

        cls._clone(repo_url, repo_path)
        return repo_path

    @classmethod
    def get_board_path(
        cls, board_type: str, *, repo_root: Optional[Path] = None
    ) -> Path:
        repo_root = repo_root or cls.ensure_repo()
        mapping = {
            "35t": repo_root / "PCIeSquirrel",
            "75t": repo_root / "PCIeEnigmaX1",
            "100t": repo_root / "XilinxZDMA",
            # CaptainDMA variants
            "pcileech_75t484_x1": repo_root / "CaptainDMA" / "75t484_x1",
            "pcileech_35t484_x1": repo_root / "CaptainDMA" / "35t484_x1",
            "pcileech_35t325_x4": repo_root / "CaptainDMA" / "35t325_x4",
            "pcileech_35t325_x1": repo_root / "CaptainDMA" / "35t325_x1",
            "pcileech_100t484_x1": repo_root / "CaptainDMA" / "100t484-1",
            # Other boards
            "pcileech_enigma_x1": repo_root / "EnigmaX1",
            "pcileech_squirrel": repo_root / "PCIeSquirrel",
            "pcileech_pciescreamer_xc7a35": repo_root / "pciescreamer",
        }
        try:
            path = mapping[board_type]
        except KeyError as exc:
            raise RuntimeError(
                (
                    "Unknown board type '{bt}'.  Known types: {known}".format(
                        bt=board_type, known=", ".join(mapping)
                    )
                )
            ) from exc
        if not path.exists():
            raise RuntimeError(
                (
                    "Board directory {p} does not exist.  Repository may be "
                    "incomplete."
                ).format(p=path)
            )
        return path

    @classmethod
    def get_xdc_files(
        cls, board_type: str, *, repo_root: Optional[Path] = None
    ) -> List[Path]:
        board_dir = cls.get_board_path(board_type, repo_root=repo_root)
        search_roots = [
            board_dir,
            board_dir / "src",
            board_dir / "constraints",
            board_dir / "xdc",
        ]
        xdc: list[Path] = []
        for root in search_roots:
            if root.exists():
                xdc.extend(root.glob("**/*.xdc"))
        if not xdc:
            raise RuntimeError(
                f"No .xdc files found for board '{board_type}' in {board_dir}"
            )
        # De‑duplicate whilst preserving order
        seen: set[Path] = set()
        uniq: list[Path] = []
        for p in xdc:
            if p not in seen:
                uniq.append(p)
                seen.add(p)
        return uniq

    @classmethod
    def read_combined_xdc(
        cls, board_type: str, *, repo_root: Optional[Path] = None
    ) -> str:
        files = cls.get_xdc_files(board_type, repo_root=repo_root)
        parts = [
            f"# XDC constraints for {board_type}",
            f"# Sources: {[f.name for f in files]}",
        ]
        for fp in files:
            # Use the file name or relative path safely
            try:
                relative_path = (
                    fp.relative_to(fp.parents[1]) if len(fp.parents) > 1 else fp.name
                )
            except (IndexError, ValueError):
                relative_path = fp.name
            parts.append(f"\n# ==== {relative_path} ====")
            parts.append(fp.read_text("utf‑8"))
        return "\n".join(parts)

    # ------------------------------------------------------------------
    # Internal helpers
    # ------------------------------------------------------------------

    @classmethod
    def _is_valid_repo(cls, path: Path) -> bool:
        """Check if path contains a valid git repository."""
        if not (path / ".git").exists():
            return False

        if not _git_available():
            # Best‑effort: assume OK when .git exists and git not available
            return True

        try:
            # Use git command to verify repository is valid
            _run(["git", "rev-parse", "--git-dir"], cwd=path)
            return True
        except Exception:
            return False

    @classmethod
    def _maybe_update(cls, path: Path) -> None:
        """Update repository if it's older than UPDATE_INTERVAL_DAYS."""
        stamp = path / ".last_update"
        need_update = True
        if stamp.exists():
            try:
                last = _dt.datetime.fromisoformat(stamp.read_text().strip())
                # Ensure both datetimes are timezone-aware for comparison
                now = _dt.datetime.now(_dt.timezone.utc)
                if last.tzinfo is None:
                    # If parsed datetime is naive, assume it's UTC
                    last = last.replace(tzinfo=_dt.timezone.utc)
                need_update = (now - last).days >= UPDATE_INTERVAL_DAYS
            except ValueError:
                pass  # treat malformed stamp as out‑of‑date
        if not need_update:
            log_debug_safe(
                _logger,
                "Repository {path} is fresh enough (last update {timestamp})",
                path=path,
                timestamp=stamp.read_text().strip(),
            )
            return

        log_info_safe(_logger, "Updating repo {path} ...", path=path)

        if not _git_available():
            log_warning_safe(
                _logger,
                safe_format("git executable not available - skipping update"),
                prefix="GIT",
            )
            return

        try:
            _run(["git", "-C", str(path), "pull", "--rebase", "--autostash"])
            stamp.write_text(utc_timestamp())
        except Exception as exc:
            log_warning_safe(
                _logger,
                safe_format("Git pull failed: {error}", error=exc),
                prefix="GIT",
            )

    # ------------------------------------------------------------------
    # Clone logic
    # ------------------------------------------------------------------

    @classmethod
    def _clone(cls, repo_url: str, dst: Path) -> None:
        """Clone repository using git command."""
        log_info_safe(
            _logger,
            safe_format("Cloning repo {url} to {dst}", url=repo_url, dst=dst),
            prefix="GIT",
        )

        if not _git_available():
            raise RuntimeError("git executable not available for cloning")

        attempts = 0
        delay = 2.0
        while attempts < 3:
            attempts += 1
            try:
                _run(
                    ["git", "clone", "--depth", "1", repo_url, str(dst)],
                    cwd=dst.parent,
                )
                (dst / ".last_update").write_text(utc_timestamp())
                return
            except Exception as exc:
                # Remove failed clone directory if it exists
                try:
                    _shutil.rmtree(dst, ignore_errors=True)
                except Exception:
                    pass  # Ignore errors during cleanup
                log_warning_safe(
                    _logger,
                    safe_format(
                        "Clone attempt {attempts} failed: {error}",
                        attempts=attempts,
                        error=exc,
                    ),
                    prefix="GIT",
                )
                if attempts >= 3:
                    raise RuntimeError(
                        safe_format(
                            "Failed to clone {url} after {attempts} attempts",
                            url=repo_url,
                            attempts=attempts,
                        )
                    ) from exc
                _time.sleep(delay)
                delay *= 2  # exponential back‑off


###############################################################################
# Convenience functions for external access
###############################################################################


def get_repo_manager() -> type[RepoManager]:
    """Return the RepoManager class for external use."""
    return RepoManager


def get_xdc_files(board_type: str, *, repo_root: Optional[Path] = None) -> List[Path]:
    """Wrapper function to get XDC files for a board type.

    Args:
        board_type: The board type to get XDC files for
        repo_root: Optional repository root path

    Returns:
        List[Path]: List of XDC file paths
    """
    return RepoManager.get_xdc_files(board_type, repo_root=repo_root)


def read_combined_xdc(board_type: str, *, repo_root: Optional[Path] = None) -> str:
    """Wrapper function to read combined XDC content for a board type.

    Args:
        board_type: The board type to read XDC content for
        repo_root: Optional repository root path

    Returns:
        str: Combined XDC content
    """
    return RepoManager.read_combined_xdc(board_type, repo_root=repo_root)


def is_repository_accessible(
    board_type: Optional[str] = None, *, repo_root: Optional[Path] = None
) -> bool:
    """Check repo accessibility; optionally verify a specific board exists.

    Args:
        board_type: Optional board type to check for specific board accessibility
        repo_root: Optional repository root path

    Returns:
        bool: True if repository is accessible (and board exists if specified)
    """
    try:
        if repo_root is None:
            repo_root = RepoManager.ensure_repo()

        # Check if repo is valid
        if not RepoManager._is_valid_repo(repo_root):
            return False

        # If board_type specified, check if that board is accessible
        if board_type is not None:
            try:
                RepoManager.get_board_path(board_type, repo_root=repo_root)
            except RuntimeError:
                return False

        return True
    except Exception:
        return False


###############################################################################
# CLI helper - "python repo_manager.py" sanity‑checks the repo.
###############################################################################

if __name__ == "__main__":
    import logging

    from ..log_config import setup_logging

    setup_logging(level=logging.INFO)
    try:
        path = RepoManager.ensure_repo()
        log_info_safe(_logger, "Repository ready at {path}", path=path)
    except Exception as exc:  # pragma: no cover - runtime feedback only
        log_error_safe(_logger, "Error: {error}", error=exc)
        raise SystemExit(1)
