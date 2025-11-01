#!/usr/bin/env python3
"""Repository Manager

This utility provides access to board-specific files from the
`voltcyclone-fpga` git submodule mounted at lib/voltcyclone-fpga.

It provides methods to ensure the submodule is initialized, check for updates,
and retrieve board paths and XDC files for various PCILeech boards.
"""
from __future__ import annotations

import os as _os

import subprocess as _sp

from pathlib import Path

from typing import List, Optional

from ..log_config import get_logger

from ..string_utils import (log_debug_safe, log_error_safe, log_info_safe,
                            log_warning_safe, safe_format)

###############################################################################
# Configuration constants
###############################################################################

# Git submodule path - single source of truth
_REPO_ROOT = Path(__file__).resolve().parent.parent.parent
SUBMODULE_PATH = _REPO_ROOT / "lib" / "voltcyclone-fpga"
REPO_URL = "https://github.com/VoltCyclone/voltcyclone-fpga.git"

###############################################################################
# Logging setup
###############################################################################

_logger = get_logger(__name__)

###############################################################################
# Helper utilities
###############################################################################


def _run(
    cmd: List[str], 
    *, 
    cwd: Optional[Path] = None, 
    env: Optional[dict] = None,
    capture_output: bool = False,
    suppress_output: bool = False
) -> _sp.CompletedProcess:
    """Run *cmd* and return the completed process, raising on error.
    
    Args:
        cmd: Command and arguments to run
        cwd: Working directory for command
        env: Environment variables
        capture_output: If True, capture stdout/stderr
        suppress_output: If True, suppress all output (validation checks)
    """
    log_debug_safe(_logger,
                   "Running {cmd} (cwd={cwd})",
                   cmd=cmd,
                   cwd=cwd,
                   prefix="GIT"
                   )
    
    kwargs = {
        "cwd": str(cwd) if cwd else None,
        "env": env,
        "check": True,
        "text": True,
    }
    
    if capture_output:
        kwargs["capture_output"] = True
    elif suppress_output:
        # Suppress both stdout and stderr for validation checks
        kwargs["stdout"] = _sp.DEVNULL
        kwargs["stderr"] = _sp.DEVNULL
    
    return _sp.run(cmd, **kwargs)


def _git_available() -> bool:
    """Return *True* if ``git`` is callable in the PATH."""
    try:
        # Suppress output to avoid noise in logs during validation
        _run(
            ["git", "--version"], 
            env={**_os.environ, "GIT_TERMINAL_PROMPT": "0"},
            suppress_output=True
        )
        return True
    except Exception:
        return False


###############################################################################
# Public API
###############################################################################


class RepoManager:
    """Utility class - no instantiation necessary."""
    
    # ---------------------------------------------------------------------
    # Entry points
    # ---------------------------------------------------------------------
    
    @classmethod
    def ensure_repo(cls) -> Path:
        """Ensure voltcyclone-fpga assets exist and return their path.

        Prefers a full git submodule but accepts read-only vendor payloads
        that ship without git metadata when the expected board directories
        are present.

        Raises:
            RuntimeError: If no firmware assets are available
        """
        if not SUBMODULE_PATH.exists():
            raise RuntimeError(
                safe_format(
                    "voltcyclone-fpga submodule not found at {path}. "
                    "Initialize with: git submodule update --init --recursive",
                    path=SUBMODULE_PATH,
                )
            )

        if cls._is_valid_repo(SUBMODULE_PATH):
            log_debug_safe(
                _logger,
                "Using voltcyclone-fpga submodule at {path}",
                path=SUBMODULE_PATH,
                prefix="REPO",
            )
            return SUBMODULE_PATH

        if cls._has_vendored_payload(SUBMODULE_PATH):
            log_warning_safe(
                _logger,
                "voltcyclone-fpga assets missing git metadata; using vendored copy",
                prefix="REPO",
            )
            return SUBMODULE_PATH

        raise RuntimeError(
            safe_format(
                "voltcyclone-fpga assets at {path} are unavailable or incomplete. "
                "Reinitialize with: git submodule update --init --recursive",
                path=SUBMODULE_PATH,
            )
        )

    @classmethod
    def update_submodule(cls) -> None:
        """Update the voltcyclone-fpga submodule to latest upstream changes.

        Raises:
            RuntimeError: If git is not available or update fails
        """
        if not _git_available():
            raise RuntimeError("git executable not available for submodule update")
        
        log_info_safe(_logger, "Updating voltcyclone-fpga submodule...")
        
        try:
            # Update submodule to latest commit from tracked branch
            _run(
                ["git", "submodule", "update", "--remote", "--merge", 
                 "lib/voltcyclone-fpga"],
                cwd=_REPO_ROOT,
            )
            log_info_safe(_logger, "Submodule updated successfully")
        except Exception as exc:
            log_error_safe(
                _logger,
                safe_format("Submodule update failed: {error}", error=exc),
                prefix="REPO"
            )
            raise RuntimeError(
                "Failed to update voltcyclone-fpga submodule"
            ) from exc

    @classmethod
    def get_board_path(
        cls, board_type: str, *, repo_root: Optional[Path] = None
    ) -> Path:
        repo_root = repo_root or cls.ensure_repo()
        mapping = {
            "35t": repo_root / "PCIeSquirrel",
            "75t": repo_root / "EnigmaX1",
            "100t": repo_root / "ZDMA",
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
                safe_format(
                    "No .xdc files found for board '{board_type}' in {board_dir}",
                    board_type=board_type, board_dir=board_dir
                )
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
        git_dir = path / ".git"
        if not git_dir.exists():
            return False

        if not _git_available():
            return True

        try:
            # Suppress output to avoid "fatal: not a git repository" errors
            # when .git points to unavailable submodule metadata in containers
            _run(
                ["git", "rev-parse", "--git-dir"], 
                cwd=path, 
                suppress_output=True
            )
            return True
        except Exception:
            return False

    @classmethod
    def _has_vendored_payload(cls, path: Path) -> bool:
        """Return True when required board assets exist without git metadata."""
        expected = [
            path / "CaptainDMA",
            path / "EnigmaX1",
            path / "PCIeSquirrel",
        ]
        missing = [p for p in expected if not p.exists()]
        if missing:
            return False
        # At least one XDC file should exist beneath the tree
        for root in expected:
            if any(root.rglob("*.xdc")):
                return True
        return False


###############################################################################
# Convenience functions for external access
###############################################################################


def get_repo_manager() -> type[RepoManager]:
    """Return the RepoManager class for external use."""
    return RepoManager


def get_xdc_files(
    board_type: str, *, repo_root: Optional[Path] = None
) -> List[Path]:
    """Wrapper function to get XDC files for a board type.

    Args:
        board_type: The board type to get XDC files for
        repo_root: Optional repository root path (defaults to submodule)

    Returns:
        List[Path]: List of XDC file paths
    """
    return RepoManager.get_xdc_files(board_type, repo_root=repo_root)


def read_combined_xdc(
    board_type: str, *, repo_root: Optional[Path] = None
) -> str:
    """Wrapper function to read combined XDC content for a board type.

    Args:
        board_type: The board type to read XDC content for
        repo_root: Optional repository root path (defaults to submodule)

    Returns:
        str: Combined XDC content
    """
    return RepoManager.read_combined_xdc(board_type, repo_root=repo_root)


def is_repository_accessible(
    board_type: Optional[str] = None, *, repo_root: Optional[Path] = None
) -> bool:
    """Check submodule accessibility; optionally verify specific board exists.

    Args:
        board_type: Optional board type to check for specific board
        repo_root: Optional repository root path (defaults to submodule)

    Returns:
        bool: True if submodule is accessible (and board exists if specified)
    """
    try:
        if repo_root is None:
            repo_root = RepoManager.ensure_repo()

        # Check if repo is valid
        if not RepoManager._is_valid_repo(repo_root):
            raise RuntimeError("Repository not valid")

        # If board_type specified, check if that board is accessible
        if board_type is not None:
            try:
                RepoManager.get_board_path(board_type, repo_root=repo_root)
            except:
                raise RuntimeError("Board not found")
                
        return True
    except Exception:
        raise RuntimeError("Repository not accessible")
