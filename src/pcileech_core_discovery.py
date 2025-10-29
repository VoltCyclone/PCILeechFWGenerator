#!/usr/bin/env python3
"""
Enhanced PCILeech Core File Discovery

This utility helps ensure that all necessary PCILeech core files are discovered
and available for builds, preventing the "missing pcileech.svh" issue.

The discovery process is now board-aware, meaning it will:
1. Filter files based on the target board's capabilities (MSI-X, Option ROM, etc.)
2. Search board-specific directories first when a board is specified
3. Use board-appropriate FPGA family files (7-series vs UltraScale)
4. Prioritize files from the configured board's directory structure

This ensures that only relevant files are discovered and used for the specific
target board configuration.
"""

import logging
from pathlib import Path
from typing import Dict, List, Optional

from .file_management.repo_manager import RepoManager
from .file_management.template_discovery import TemplateDiscovery
from .string_utils import log_info_safe, log_warning_safe

logger = logging.getLogger(__name__)

# Critical files that must be present for PCILeech builds
CRITICAL_PCILEECH_FILES = {
    # Core SystemVerilog files
    "pcileech_tlps128_bar_controller.sv",
    "pcileech_fifo.sv",
    "pcileech_mux.sv",
    "pcileech_com.sv",
    # Header files (critical for compilation) - note actual naming in pcileech-fpga repo
    "pcileech_header.svh",  # This is the actual file name in pcileech-fpga
    "tlp_pkg.svh",
    "bar_layout_pkg.svh",
    # Configuration and management files
    "pcileech_tlps128_cfgspace_shadow.sv",  # This is the actual file name
    # PCIe-specific implementations
    "pcileech_pcie_cfg_a7.sv",
    "pcileech_pcie_tlp_a7.sv",
}

# Optional files that enhance functionality but aren't critical
OPTIONAL_PCILEECH_FILES = {
    "pcileech_pcie_cfg_us.sv",
    "pcileech_pcie_tlp_us.sv",
    "msix_capability_registers.sv",
    "msix_implementation.sv",
    "msix_table.sv",
    "option_rom_bar_window.sv",
    "option_rom_spi_flash.sv",
}


def discover_pcileech_files(
    board_name: Optional[str] = None, repo_root: Optional[Path] = None
) -> Dict[str, Path]:
    """
    Enhanced discovery of PCILeech files with validation, scoped to the configured board.

    Args:
        board_name: Specific PCILeech board to scope the search to
        repo_root: Repository root path (defaults to current working directory for local development)

    Returns:
        Dictionary of discovered files with validation status
    """
    if repo_root is None:
        # For local development, use current directory instead of cached repo
        repo_root = Path.cwd()

    # Also get the cached repo for fallback searches
    cached_repo_root = RepoManager.ensure_repo()

    # Get board-specific configuration if provided
    board_config = None
    if board_name:
        try:
            from .device_clone.board_config import get_pcileech_board_config

            board_config = get_pcileech_board_config(board_name, cached_repo_root)
            log_info_safe(
                logger,
                "Scoping PCILeech file discovery to board: {board_name} ({fpga_part})",
                board_name=board_name,
                fpga_part=board_config.get("fpga_part", "unknown"),
            )
        except Exception as e:
            log_warning_safe(
                logger,
                "Failed to get board config for {board_name}: {error}, using generic search",
                board_name=board_name,
                error=str(e),
            )

    discovered_files = {}

    # First, prioritize local files by searching all critical and optional files
    # Filter files based on board capabilities if board config is available
    if board_config:
        target_files = set(CRITICAL_PCILEECH_FILES)

        # Add optional files based on board capabilities
        fpga_family = board_config.get("fpga_family", "7series")
        pcie_ip_type = board_config.get("pcie_ip_type", "pcie_7x")

        # Add family-specific files
        if fpga_family == "ultrascale" or fpga_family == "ultrascale_plus":
            target_files.update(
                [
                    "pcileech_pcie_cfg_us.sv",
                    "pcileech_pcie_tlp_us.sv",
                ]
            )

        # Add MSI-X files if supported
        if board_config.get("supports_msix", False):
            target_files.update(
                [
                    "msix_capability_registers.sv",
                    "msix_implementation.sv",
                    "msix_table.sv",
                ]
            )

        # Add Option ROM files if supported
        if board_config.get("has_option_rom", False):
            target_files.update(
                [
                    "option_rom_bar_window.sv",
                    "option_rom_spi_flash.sv",
                ]
            )

        log_info_safe(
            logger,
            "Board-filtered search: {count} files for {board_name} ({fpga_family}, {pcie_ip})",
            count=len(target_files),
            board_name=board_name,
            fpga_family=fpga_family,
            pcie_ip=pcie_ip_type,
        )
    else:
        target_files = CRITICAL_PCILEECH_FILES | OPTIONAL_PCILEECH_FILES
        log_info_safe(
            logger, "Generic search: {count} target files", count=len(target_files)
        )

    # If board configuration is available, use board-specific search paths
    if board_config:
        search_roots = [repo_root]  # Local development first

        # Add board-specific paths from cached repo
        if board_name:  # Only proceed if board_name is not None
            try:
                board_path = RepoManager.get_board_path(
                    board_name, repo_root=cached_repo_root
                )
                search_roots.append(board_path)
                log_info_safe(
                    logger,
                    "Added board-specific search path: {board_path}",
                    board_path=str(board_path),
                )
            except Exception as e:
                log_warning_safe(
                    logger,
                    "Failed to get board path for {board_name}: {error}",
                    board_name=board_name,
                    error=str(e),
                )
                search_roots.append(cached_repo_root)  # Fallback to general search
        else:
            search_roots.append(cached_repo_root)
    else:
        search_roots = [repo_root, cached_repo_root]

    for target_file in target_files:
        found_path = None

        # Search in each root, prioritizing board-specific locations
        for search_root in search_roots:
            found_path = _enhanced_file_search(search_root, target_file)
            if found_path:
                break

        if found_path:
            discovered_files[target_file] = found_path
            log_info_safe(
                logger,
                "Found {file} at {path}",
                file=target_file,
                path=str(found_path),
            )

    # Use existing discovery mechanism for any files we haven't found yet (from cached repo)
    core_files = TemplateDiscovery.get_pcileech_core_files(cached_repo_root)

    # Only add cached files if we don't already have versions
    for filename, filepath in core_files.items():
        if filename not in discovered_files:
            discovered_files[filename] = filepath
            log_info_safe(
                logger,
                "Added cached file {file} at {path}",
                file=filename,
                path=str(filepath),
            )

    # Report discovery statistics
    critical_found = len(CRITICAL_PCILEECH_FILES & set(discovered_files.keys()))
    optional_found = len(OPTIONAL_PCILEECH_FILES & set(discovered_files.keys()))

    log_info_safe(
        logger,
        "PCILeech file discovery: {critical}/{total_critical} critical, {optional} optional",
        critical=critical_found,
        total_critical=len(CRITICAL_PCILEECH_FILES),
        optional=optional_found,
    )

    # Search for missing critical files in cached repo as fallback
    missing_critical = CRITICAL_PCILEECH_FILES - set(discovered_files.keys())
    if missing_critical:
        log_info_safe(
            logger,
            "Searching cached repo for {count} remaining critical files",
            count=len(missing_critical),
        )

        for missing_file in missing_critical:
            found_path = _enhanced_file_search(cached_repo_root, missing_file)
            if found_path:
                discovered_files[missing_file] = found_path
                log_info_safe(
                    logger,
                    "Found cached fallback {file} at {path}",
                    file=missing_file,
                    path=str(found_path),
                )

    # Report final missing critical files
    final_missing_critical = CRITICAL_PCILEECH_FILES - set(discovered_files.keys())
    if final_missing_critical:
        log_warning_safe(
            logger,
            "Still missing critical PCILeech files: {files}",
            files=list(final_missing_critical),
        )

    return discovered_files


def _enhanced_file_search(repo_root: Path, filename: str) -> Optional[Path]:
    """
    Enhanced search for a specific file in the repository.

    Args:
        repo_root: Repository root path
        filename: Name of file to find

    Returns:
        Path to file if found, None otherwise
    """
    # Extended search directories - prioritize local pcileech directory first
    search_dirs = [
        # Local development files first (highest priority)
        repo_root / "pcileech",
        repo_root / "pcileech" / "rtl",
        repo_root / "src" / "templates" / "sv",
        # Repository root and common directories
        repo_root,
        repo_root / "common",
        repo_root / "shared",
        repo_root / "src",
        repo_root / "pcileech_shared",
        repo_root / "pcileech" / "src",
    ]

    # Also search in board directories for shared files
    boards_dir = repo_root / "boards"
    if boards_dir.exists():
        search_dirs.extend(
            [
                boards_dir / "common",
                boards_dir / "shared",
            ]
        )

    # If this is a board-specific search (from pcileech-fpga repo), add board-specific patterns
    # Check for CaptainDMA structure or any board-specific patterns
    repo_str = str(repo_root).lower()
    is_board_specific = (
        "captaindma" in repo_str
        or "pcileech-fpga" in repo_str
        or any(
            pattern in repo_str
            for pattern in [
                "35t484",
                "75t484",
                "100t484",
                "35t325",
                "enigma",
                "squirrel",
                "pciescreamer",
            ]
        )
    )

    if is_board_specific:
        # Add board-specific search patterns for pcileech-fpga repo structure
        search_dirs.extend(
            [
                repo_root / "src",
                repo_root / "rtl",
                repo_root / "hdl",
                repo_root / "ip",
                repo_root / "shared",
                repo_root / "pcileech",
            ]
        )

    for search_dir in search_dirs:
        if not search_dir.exists():
            continue

        # Direct search
        direct_path = search_dir / filename
        if direct_path.exists():
            return direct_path

        # Recursive search
        matches = list(search_dir.rglob(filename))
        if matches:
            # Return the first match, but prefer matches in more specific directories
            matches.sort(
                key=lambda p: (
                    # Prefer files in more specific paths (deeper = more specific)
                    -len(p.parts),
                    # Prefer files with "pcileech" in the path
                    0 if "pcileech" in str(p).lower() else 1,
                    # Prefer .svh files over .sv files for headers
                    0 if filename.endswith(".svh") and str(p).endswith(".svh") else 1,
                    str(p),
                )
            )
            return matches[0]

    return None


def validate_pcileech_environment(discovered_files: Dict[str, Path]) -> List[str]:
    """
    Validate that the discovered PCILeech environment is complete.

    Args:
        discovered_files: Dictionary of discovered files

    Returns:
        List of validation warnings/errors
    """
    issues = []

    # Check for critical missing files
    missing_critical = CRITICAL_PCILEECH_FILES - set(discovered_files.keys())
    if missing_critical:
        issues.append(f"Missing critical files: {list(missing_critical)}")

    # Validate file accessibility
    for filename, filepath in discovered_files.items():
        if not filepath.exists():
            issues.append(f"File not accessible: {filename} at {filepath}")
        elif not filepath.is_file():
            issues.append(f"Path is not a file: {filename} at {filepath}")

    # Check for required header file combinations (support both old and new naming)
    header_files_found = {
        "pcileech.svh",
        "pcileech_header.svh",
        "tlp_pkg.svh",
        "bar_layout_pkg.svh",
    } & set(discovered_files.keys())

    if not header_files_found:
        issues.append("No PCILeech header files found - builds will likely fail")
    elif (
        "tlp_pkg.svh" not in discovered_files
        and "bar_layout_pkg.svh" not in discovered_files
    ):
        issues.append(
            "Missing critical package header files (tlp_pkg.svh, bar_layout_pkg.svh)"
        )

    return issues


if __name__ == "__main__":
    # Test the discovery system
    import sys

    # Allow passing board name as command line argument
    board_name = sys.argv[1] if len(sys.argv) > 1 else None

    print(
        f"Testing PCILeech file discovery{f' for board: {board_name}' if board_name else ''}..."
    )

    try:
        files = discover_pcileech_files(board_name=board_name)
        issues = validate_pcileech_environment(files)

        print(f"\nDiscovered {len(files)} PCILeech files:")
        for name, path in sorted(files.items()):
            print(f"  {name}: {path}")

        if issues:
            print(f"\nValidation issues:")
            for issue in issues:
                print(f"  - {issue}")
        else:
            print("\n✓ PCILeech environment validation passed")

    except Exception as e:
        print(f"Error: {e}")
        import traceback

        traceback.print_exc()
