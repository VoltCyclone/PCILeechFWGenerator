#!/usr/bin/env python3
"""Unit tests for the RepoManager helper (single-path policy)."""

from pathlib import Path

import pytest

from pcileechfwgenerator.file_management import repo_manager


def _seed_minimal_submodule(root: Path) -> None:
    """Create minimal valid submodule structure with .git and board dirs."""
    (root / ".git").mkdir(parents=True, exist_ok=True)
    # Required top-level board directories
    for board in ("CaptainDMA", "EnigmaX1", "PCIeSquirrel"):
        (root / board).mkdir(parents=True, exist_ok=True)


def test_ensure_repo_valid_submodule(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """ensure_repo returns path when submodule structure is valid."""
    assets = tmp_path / "voltcyclone-fpga"
    _seed_minimal_submodule(assets)
    monkeypatch.setattr(repo_manager, "SUBMODULE_PATH", assets)
    # Force repo validation to pass (empty .git won't satisfy git normally)
    monkeypatch.setattr(repo_manager.RepoManager, "_is_valid_repo", classmethod(lambda cls, p: True))
    resolved = repo_manager.RepoManager.ensure_repo()
    assert resolved == assets


def test_ensure_repo_missing_git(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """Missing .git directory should raise RuntimeError."""
    pytest.skip("Git validation is platform-dependent and may detect container mode")
    assets = tmp_path / "voltcyclone-fpga"
    # Create required board dirs but omit .git
    for board in ("CaptainDMA", "EnigmaX1", "PCIeSquirrel"):
        (assets / board).mkdir(parents=True, exist_ok=True)
    monkeypatch.setattr(repo_manager, "SUBMODULE_PATH", assets)
    with pytest.raises(RuntimeError):
        repo_manager.RepoManager.ensure_repo()


def test_ensure_repo_incomplete_boards(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """Missing one of required board directories triggers failure."""
    assets = tmp_path / "voltcyclone-fpga"
    (assets / ".git").mkdir(parents=True, exist_ok=True)
    # Only two of the three required boards
    for board in ("CaptainDMA", "EnigmaX1"):
        (assets / board).mkdir(parents=True, exist_ok=True)
    monkeypatch.setattr(repo_manager, "SUBMODULE_PATH", assets)
    monkeypatch.setattr(repo_manager.RepoManager, "_is_valid_repo", classmethod(lambda cls, p: True))
    with pytest.raises(RuntimeError):
        repo_manager.RepoManager.ensure_repo()


def test_is_repository_accessible_unknown_board_chains_cause(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A bad board_type must produce a chained RuntimeError that names the
    board and preserves the underlying exception via __cause__."""
    assets = tmp_path / "voltcyclone-fpga"
    _seed_minimal_submodule(assets)
    monkeypatch.setattr(
        repo_manager.RepoManager,
        "_is_valid_repo",
        classmethod(lambda cls, p: True),
    )

    underlying = FileNotFoundError("no such board")

    def _boom(*_args, **_kwargs):
        raise underlying

    monkeypatch.setattr(repo_manager.RepoManager, "get_board_path", _boom)

    with pytest.raises(RuntimeError) as excinfo:
        repo_manager.is_repository_accessible(
            "Nonexistent_Board", repo_root=assets
        )

    msg = str(excinfo.value)
    # The board name and underlying cause must both survive the
    # double-wrap (inner "Board not found" + outer "Repository not accessible").
    assert "Nonexistent_Board" in msg
    # Walk the __cause__ chain to the original FileNotFoundError.
    cause = excinfo.value.__cause__
    causes = []
    while cause is not None:
        causes.append(cause)
        cause = cause.__cause__
    assert underlying in causes


def test_is_repository_accessible_success(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Happy path: returns True when repo is valid and the board resolves."""
    assets = tmp_path / "voltcyclone-fpga"
    _seed_minimal_submodule(assets)
    monkeypatch.setattr(
        repo_manager.RepoManager,
        "_is_valid_repo",
        classmethod(lambda cls, p: True),
    )
    monkeypatch.setattr(
        repo_manager.RepoManager,
        "get_board_path",
        classmethod(lambda cls, board, repo_root=None: assets / board),
    )
    assert (
        repo_manager.is_repository_accessible("CaptainDMA", repo_root=assets)
        is True
    )
