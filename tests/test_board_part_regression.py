#!/usr/bin/env python3
"""
Regression tests for FPGA part mappings to prevent Vivado part-not-found issues.

These tests ensure the Artix-7 100T board maps to the correct FGG package
string and never the incorrect FFG package variant which Vivado rejects.
"""

import re

from src.device_clone import constants
from src.file_management.board_discovery import BoardDiscovery


def test_constants_100t_part_is_fgg_484():
    part = constants.BOARD_PARTS.get("100t")
    assert part is not None, "Missing 100t mapping in BOARD_PARTS"
    assert part.lower() == "xc7a100tfgg484-1", part
    # Guard against common typo
    assert "ffg" not in part.lower(), "Incorrect package 'ffg' present in 100t mapping"


def test_constants_pcileech_100t484_x1_is_fgg_484():
    part = constants.BOARD_PARTS.get("pcileech_100t484_x1")
    assert part is not None, "Missing pcileech_100t484_x1 mapping in BOARD_PARTS"
    assert part.lower() == "xc7a100tfgg484-1", part
    assert (
        "ffg" not in part.lower()
    ), "Incorrect package 'ffg' present in 100t board mapping"


def test_board_discovery_captain_dma_100t_uses_fgg(monkeypatch, tmp_path):
    # Build a fake repo structure with CaptainDMA/100t484-1 directory
    repo_root = tmp_path / "pcileech-fpga"
    boards_root = repo_root / "CaptainDMA" / "100t484-1"
    boards_root.mkdir(parents=True, exist_ok=True)

    # Monkeypatch RepoManager.ensure_repo to return our fake repo
    from src.file_management import repo_manager

    def _ensure_repo_stub():
        return repo_root

    monkeypatch.setattr(
        repo_manager.RepoManager, "ensure_repo", staticmethod(_ensure_repo_stub)
    )

    boards = BoardDiscovery.discover_boards()

    # Expect the canonical board name to be discovered
    assert "pcileech_100t484_x1" in boards
    cfg = boards["pcileech_100t484_x1"]
    part = cfg.get("fpga_part")

    assert part is not None, "Discovered 100T board missing fpga_part"
    assert part.lower() == "xc7a100tfgg484-1", part
    assert (
        "ffg" not in part.lower()
    ), "Incorrect package 'ffg' present in discovered board mapping"

    # Sanity: family detection should classify as 7series
    assert cfg.get("fpga_family") == "7series"
