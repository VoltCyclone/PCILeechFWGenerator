#!/usr/bin/env python3
"""
Regression tests for FPGA part mappings to prevent Vivado part-not-found issues.

These tests ensure the Artix-7 100T board maps to the correct FGG package
string and never the incorrect FFG package variant which Vivado rejects.
"""

import re

from pcileechfwgenerator.device_clone import constants
from pcileechfwgenerator.file_management.board_discovery import BoardDiscovery


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
    from pcileechfwgenerator.file_management import repo_manager

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


def test_constants_pcileech_100t484_m2_matches_full_size_100t():
    """The M2 100T alias must mirror the full-size CaptainDMA 100T part."""
    m2 = constants.BOARD_PARTS.get("pcileech_100t484_m2")
    full = constants.BOARD_PARTS.get("pcileech_100t484_x1")
    assert m2 is not None, "Missing pcileech_100t484_m2 mapping in BOARD_PARTS"
    assert m2.lower() == "xc7a100tfgg484-1", m2
    assert "ffg" not in m2.lower(), "Incorrect package 'ffg' in M2 100T mapping"
    assert m2 == full, "M2 100T alias must use the same part as the full-size 100T"
    assert "pcileech_100t484_m2" in constants.BOARD_FALLBACKS


def test_board_discovery_m2_100t_aliases_full_size(monkeypatch, tmp_path):
    """Both 100T keys must resolve from the shared 100t484-1 directory with
    identical discovery settings and reference-clock placement."""
    repo_root = tmp_path / "pcileech-fpga"
    (repo_root / "CaptainDMA" / "100t484-1").mkdir(parents=True, exist_ok=True)

    from pcileechfwgenerator.file_management import repo_manager

    monkeypatch.setattr(
        repo_manager.RepoManager,
        "ensure_repo",
        staticmethod(lambda: repo_root),
    )

    boards = BoardDiscovery.discover_boards()

    # The alias is discovered alongside the full-size board.
    assert "pcileech_100t484_m2" in boards
    m2 = boards["pcileech_100t484_m2"]
    full = boards["pcileech_100t484_x1"]
    assert m2.get("fpga_part") == full.get("fpga_part") == "xc7a100tfgg484-1"
    assert m2.get("max_lanes") == full.get("max_lanes") == 1
    assert m2.get("fpga_family") == "7series"

    # Static config: shared directory and reference-clock LOC.
    assert (
        BoardDiscovery.BOARD_CONFIGS["pcileech_100t484_m2"]["dir"]
        == BoardDiscovery.BOARD_CONFIGS["pcileech_100t484_x1"]["dir"]
    )
    assert (
        BoardDiscovery.PCIE_REFCLK_LOC_MAP["pcileech_100t484_m2"]
        == BoardDiscovery.PCIE_REFCLK_LOC_MAP["pcileech_100t484_x1"]
    )


def test_repo_manager_m2_100t_resolves_to_full_size_dir(tmp_path):
    """get_board_path() must point the alias at the full-size 100T directory."""
    from pcileechfwgenerator.file_management.repo_manager import RepoManager

    (tmp_path / "CaptainDMA" / "100t484-1").mkdir(parents=True, exist_ok=True)
    m2 = RepoManager.get_board_path("pcileech_100t484_m2", repo_root=tmp_path)
    full = RepoManager.get_board_path("pcileech_100t484_x1", repo_root=tmp_path)
    assert m2 == full == tmp_path / "CaptainDMA" / "100t484-1"


def test_tui_configuration_accepts_m2_100t():
    """The TUI board validator must accept the new alias key."""
    from pcileechfwgenerator.tui.models.configuration import VALID_BOARD_TYPES

    assert "pcileech_100t484_m2" in VALID_BOARD_TYPES
