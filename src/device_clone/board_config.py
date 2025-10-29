#!/usr/bin/env python3
"""
Board Configuration Module

Centralized board-to-FPGA mapping and configuration with dynamic discovery
from the pcileech-fpga repository.
"""

import logging

from pathlib import Path

from typing import Any, Dict, List, Optional

from src.file_management.board_discovery import (BoardDiscovery,
                                                 discover_all_boards)

from src.string_utils import log_debug_safe, log_info_safe

logger = logging.getLogger(__name__)

# Cache for discovered boards
_board_cache: Optional[Dict[str, Dict]] = None
_cache_repo_root: Optional[Path] = None


def _ensure_board_cache(repo_root: Optional[Path] = None) -> Dict[str, Dict]:
    """Ensure board cache is populated from repository."""
    global _board_cache, _cache_repo_root

    # Invalidate cache if repo_root changed
    if repo_root != _cache_repo_root:
        _board_cache = None
        _cache_repo_root = repo_root

    if _board_cache is None:
        log_info_safe(logger, "Discovering boards from pcileech-fpga repository...")
        _board_cache = discover_all_boards(repo_root)
        log_info_safe(logger, "Discovered {count} boards", count=len(_board_cache))

    return _board_cache


# FPGA family detection patterns (kept for compatibility)
FPGA_FAMILY_PATTERNS = {
    "7series": ["xc7a", "xc7k", "xc7v", "xc7z"],
    "ultrascale": ["xcku", "xcvu", "xczu"],
    "ultrascale_plus": ["xcku", "xcvu", "xczu"],  # UltraScale+ uses same prefixes
}


def get_fpga_part(board: str, repo_root: Optional[Path] = None) -> str:
    """
    Get FPGA part number for a given board.

    Args:
        board: Board name
        repo_root: Optional repository root path

    Returns:
        FPGA part number string

    Raises:
        KeyError: If board is not found
    """
    boards = _ensure_board_cache(repo_root)
    if board not in boards:
        raise KeyError(
            f"Board '{board}' not found. Available boards: {', '.join(boards.keys())}"
        )

    fpga_part = boards[board]["fpga_part"]
    log_debug_safe(
        logger,
        "Board {board} mapped to FPGA part {fpga_part}",
        board=board,
        fpga_part=fpga_part,
        prefix="BOARD",
    )
    return fpga_part


def get_fpga_family(fpga_part: str) -> str:
    """
    Determine FPGA family from part number.

    Args:
        fpga_part: FPGA part number

    Returns:
        FPGA family string
    """
    fpga_part_lower = fpga_part.lower()

    for family, patterns in FPGA_FAMILY_PATTERNS.items():
        for pattern in patterns:
            if fpga_part_lower.startswith(pattern):
                return family

    # Default to 7-series for unknown parts
    return "7series"


def get_pcie_ip_type(fpga_part: str) -> str:
    """
    Determine appropriate PCIe IP core type based on FPGA part.

    Args:
        fpga_part: FPGA part number

    Returns:
        PCIe IP type string
    """
    if "xc7a35t" in fpga_part:
        return "axi_pcie"
    elif "xczu" in fpga_part:
        return "pcie_ultrascale"
    else:
        return "pcie_7x"


def get_pcileech_board_config(
    board: str, repo_root: Optional[Path] = None
) -> Dict[str, Any]:
    """
    Get PCILeech-specific board configuration.

    Args:
        board: Board name
        repo_root: Optional repository root path

    Returns:
        PCILeech board configuration dictionary

    Raises:
        KeyError: If board is not found in PCILeech configurations
    """
    boards = _ensure_board_cache(repo_root)
    if board not in boards:
        raise KeyError(f"PCILeech board configuration not found for: {board}")

    return boards[board]


def get_board_info(board: str, repo_root: Optional[Path] = None) -> Dict[str, str]:
    """
    Get comprehensive board information.

    Args:
        board: Board name
        repo_root: Optional repository root path

    Returns:
        Dictionary with board configuration
    """
    boards = _ensure_board_cache(repo_root)
    if board not in boards:
        raise KeyError(f"Board '{board}' not found")

    config = boards[board]
    return {
        "name": board,
        "fpga_part": config["fpga_part"],
        "fpga_family": config["fpga_family"],
        "pcie_ip_type": config["pcie_ip_type"],
    }


def validate_board(board: str, repo_root: Optional[Path] = None) -> bool:
    """
    Validate if board is supported.

    Args:
        board: Board name
        repo_root: Optional repository root path

    Returns:
        True if board is supported
    """
    boards = _ensure_board_cache(repo_root)
    return board in boards


def list_supported_boards(repo_root: Optional[Path] = None) -> List[str]:
    """
    Get list of all supported boards.

    Args:
        repo_root: Optional repository root path

    Returns:
        List of supported board names
    """
    boards = _ensure_board_cache(repo_root)
    return list(boards.keys())


def get_board_display_info(
    board: str, repo_root: Optional[Path] = None
) -> Dict[str, Any]:
    """
    Get display information for a board.

    Args:
        board: Board name
        repo_root: Optional repository root path

    Returns:
        Dictionary with display information (display_name, description, is_recommended)
    """
    boards = _ensure_board_cache(repo_root)
    display_info = BoardDiscovery.get_board_display_info(boards)

    for board_name, info in display_info:
        if board_name == board:
            return info

    # Fallback for unknown boards
    return {
        "display_name": board,
        "description": "",
        "is_recommended": False,
    }


def list_boards_with_recommendations(
    repo_root: Optional[Path] = None,
) -> List[tuple[str, Dict[str, Any]]]:
    """
    Get list of boards with their display information, ordered by recommendation.

    Args:
        repo_root: Optional repository root path

    Returns:
        List of tuples (board_name, display_info) with recommended boards first
    """
    boards = _ensure_board_cache(repo_root)
    return BoardDiscovery.get_board_display_info(boards)


# Compatibility layer for legacy code
def get_board_fpga_mapping(repo_root: Optional[Path] = None) -> Dict[str, str]:
    """
    Get mapping of board names to FPGA parts.

    Args:
        repo_root: Optional repository root path

    Returns:
        Dictionary mapping board names to FPGA part numbers
    """
    boards = _ensure_board_cache(repo_root)
    return {name: config["fpga_part"] for name, config in boards.items()}


# Export discovered boards to static configuration (for debugging/caching)
def export_discovered_boards(
    output_file: Path, repo_root: Optional[Path] = None
) -> None:
    """
    Export discovered board configurations to a file.

    Args:
        output_file: Path to output file
        repo_root: Optional repository root path
    """
    boards = _ensure_board_cache(repo_root)
    BoardDiscovery.export_board_config(boards, output_file)
