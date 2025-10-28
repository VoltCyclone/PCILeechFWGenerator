#!/usr/bin/env python3
"""Unit tests for the RepoManager helper."""

from pathlib import Path

import pytest

from src.file_management import repo_manager


def _seed_vendored_payload(root: Path) -> None:
    """Populate *root* with the minimal vendored board assets."""
    (root / "CaptainDMA" / "100t484-1").mkdir(parents=True, exist_ok=True)
    (root / "CaptainDMA" / "100t484-1" / "pins.xdc").write_text("", encoding="ascii")

    for board in ("EnigmaX1", "PCIeSquirrel"):
        board_dir = root / board
        (board_dir / "constraints").mkdir(parents=True, exist_ok=True)
        (board_dir / "constraints" / "board.xdc").write_text("", encoding="ascii")


def test_ensure_repo_accepts_vendored_payload(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """RepoManager should accept vendored assets even without git metadata."""
    assets = tmp_path / "voltcyclone-fpga"
    _seed_vendored_payload(assets)

    monkeypatch.setattr(repo_manager, "SUBMODULE_PATH", assets)
    monkeypatch.setattr(repo_manager, "_git_available", lambda: False)

    resolved = repo_manager.RepoManager.ensure_repo()
    assert resolved == assets


def test_ensure_repo_rejects_incomplete_payload(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """Vendored payloads missing XDC coverage should still fail fast."""
    assets = tmp_path / "voltcyclone-fpga"
    # Create required top-level directories without any XDC files
    for board in ("CaptainDMA", "EnigmaX1", "PCIeSquirrel"):
        (assets / board).mkdir(parents=True, exist_ok=True)

    monkeypatch.setattr(repo_manager, "SUBMODULE_PATH", assets)
    monkeypatch.setattr(repo_manager, "_git_available", lambda: False)

    with pytest.raises(RuntimeError):
        repo_manager.RepoManager.ensure_repo()
