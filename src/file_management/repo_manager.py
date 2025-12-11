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
REPO_URL = "https://github.com/VoltCyclone/voltcyclone-fpga.git"


def _get_voltcyclone_fpga_path() -> Path:
    """Return the canonical voltcyclone-fpga submodule path.

    Single-path policy: we control the git repo and build; no fallbacks,
    environment overrides, or vendored payload acceptance. Fail fast if the
    expected submodule path is not present.
    """
    return _REPO_ROOT / "lib" / "voltcyclone-fpga"


# Compute the submodule path dynamically
SUBMODULE_PATH = _get_voltcyclone_fpga_path()

###############################################################################
# Logging setup
###############################################################################

_logger = get_logger(__name__)

###############################################################################
# Helper utilities
###############################################################################


def _is_container_env() -> bool:
    """Detect if running inside a container environment.
    
    Container environments don't have .git directories since COPY
    doesn't preserve git metadata. This affects validation logic.
    """
    return (
        _os.path.exists("/.dockerenv") or
        _os.path.exists("/run/.containerenv") or
        _os.environ.get("container") in ("podman", "docker") or
        _os.environ.get("PCILEECH_CONTAINER_MODE", "").lower() in ("1", "true", "yes") or
        _os.environ.get("PCILEECH_HOST_CONTEXT_ONLY", "").lower() in ("1", "true", "yes")
    )


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

    def __new__(cls, *args, **kwargs):  # pragma: no cover - prevent misuse
        raise TypeError(
            "RepoManager may not be instantiated; call class methods only"
        )

    # ---------------------------------------------------------------------
    # Entry points
    # ---------------------------------------------------------------------

    @classmethod
    
    def ensure_repo(cls) -> Path:
        """Ensure voltcyclone-fpga git submodule exists (single pathway).

        Requirements:
        - Path must exist.
        - Must be a valid git repository (submodule initialized) OR
          in container mode: must contain required board directories.
        - Must contain required board directories.

        Any deviation raises RuntimeError with remediation instructions.
        """
        if not SUBMODULE_PATH.exists():
            # Provide context-aware error message
            if _is_container_env():
                raise RuntimeError(safe_format(
                    "voltcyclone-fpga not found at {path}.\n"
                    "Container image may be corrupted or out of date.\n"
                    "Remediation: Rebuild the container image with:\n"
                    "  podman build -t pcileech-fwgen -f Containerfile .",
                    path=SUBMODULE_PATH,
                ))
            raise RuntimeError(safe_format(
                "Missing voltcyclone-fpga submodule at {path}.\n"
                "Remediation: git submodule update --init --recursive",
                path=SUBMODULE_PATH,
            ))

        if not cls._is_valid_repo(SUBMODULE_PATH):
            # Provide context-aware error message
            if _is_container_env():
                raise RuntimeError(safe_format(
                    "voltcyclone-fpga at {path} is incomplete or corrupted.\n"
                    "Container image may need to be rebuilt.\n"
                    "Remediation: Rebuild the container image with:\n"
                    "  podman build -t pcileech-fwgen -f Containerfile .",
                    path=SUBMODULE_PATH,
                ))
            raise RuntimeError(safe_format(
                "voltcyclone-fpga at {path} is not a valid git repository.\n"
                "Remediation: git submodule update --init --recursive",
                path=SUBMODULE_PATH,
            ))

        # Minimal required directories (validated again for explicit error)
        required_dirs = ["CaptainDMA", "EnigmaX1", "PCIeSquirrel"]
        missing = [d for d in required_dirs if not (SUBMODULE_PATH / d).exists()]
        if missing:
            raise RuntimeError(safe_format(
                "voltcyclone-fpga submodule incomplete; missing: {missing}.\n"
                "Remediation: git submodule update --init --recursive",
                missing=", ".join(missing),
            ))

        log_debug_safe(
            _logger,
            "Validated voltcyclone-fpga submodule at {path}",
            path=SUBMODULE_PATH,
            prefix="REPO",
        )
        return SUBMODULE_PATH

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
                xdc.extend(sorted(root.glob("**/*.xdc")))
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
        root = repo_root or cls.ensure_repo()
        
        def _safe_rel(fp: Path, root: Path) -> str:
            """Get safe relative path for display."""
            try:
                return str(fp.relative_to(root))
            except (ValueError, RuntimeError):
                return fp.name
        
        for fp in files:
            parts.append(f"\n# ==== {_safe_rel(fp, root)} ====")
            parts.append(fp.read_text(encoding="utf-8"))
        return "\n".join(parts)

    # ------------------------------------------------------------------
    # Internal helpers
    # ------------------------------------------------------------------

    @classmethod
    
    def _is_valid_repo(cls, path: Path) -> bool:
        """Check if path contains a valid voltcyclone-fpga repository.
        
        In container environments, .git directories are not preserved during
        COPY operations, so we validate by checking for required board directories
        instead. This is safe because the container builds clone the repo fresh.
        """
        # In container mode, skip git validation and check for required content
        if _is_container_env():
            # Validate by checking for required board directories
            required_dirs = ["CaptainDMA", "EnigmaX1", "PCIeSquirrel"]
            if all((path / d).exists() for d in required_dirs):
                log_debug_safe(
                    _logger,
                    "Container mode: voltcyclone-fpga validated by content (no .git required)",
                    prefix="REPO",
                )
                return True
            return False
        
        # Normal mode: require valid git repository
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
    
    def _has_vendored_payload(cls, path: Path) -> bool:  # Deprecated single-path policy
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
