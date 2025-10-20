#!/usr/bin/env python3
"""
Dynamic Board Discovery Module

This module provides functionality to dynamically discover available boards
from the cloned pcileech-fpga repository, eliminating the need for hardcoded
board configurations.
"""

import json
import re
from pathlib import Path
from typing import Dict, List, Optional, Set, Tuple

from ..log_config import get_logger
from ..string_utils import (log_debug_safe, log_error_safe, log_info_safe,
                            log_warning_safe)
from .repo_manager import RepoManager

logger = get_logger(__name__)


class BoardDiscovery:
    """Dynamically discover and analyze boards from pcileech-fpga repository."""

    # Known board directory patterns in pcileech-fpga
    BOARD_DIR_PATTERNS = {
        # Legacy boards
        "PCIeSquirrel": {"name": "35t", "fpga_part": "xc7a35tfgg484-2"},
        "PCIeEnigmaX1": {"name": "75t", "fpga_part": "xc7a75tfgg484-2"},
        "XilinxZDMA": {"name": "100t", "fpga_part": "xczu3eg-sbva484-1-e"},
        # Modern boards with more specific patterns
        "EnigmaX1": {"name": "pcileech_enigma_x1", "fpga_part": "xc7a75tfgg484-2"},
        "pciescreamer": {
            "name": "pcileech_pciescreamer_xc7a35",
            "fpga_part": "xc7a35tcsg324-2",
        },
    }

    # CaptainDMA boards have a special structure
    CAPTAINDMA_BOARDS = {
        "75t484_x1": {"fpga_part": "xc7a75tfgg484-2", "max_lanes": 1},
        "35t484_x1": {"fpga_part": "xc7a35tfgg484-2", "max_lanes": 1},
        "35t325_x4": {"fpga_part": "xc7a35tcsg324-2", "max_lanes": 4},
        "35t325_x1": {"fpga_part": "xc7a35tcsg324-2", "max_lanes": 1},
        "100t484-1": {
            # CaptainDMA 100T board uses Artix-7 100T in FGG484 package
            "fpga_part": "xc7a100tfgg484-1",
            "max_lanes": 1,
            "canonical_name": "pcileech_100t484_x1",
        },
    }

    # PCIe reference clock IBUFDS_GTE2 LOC constraints for 7-series boards
    # Maps board name to IBUFDS_GTE2 site location
    PCIE_REFCLK_LOC_MAP = {
        # Artix-7 75T boards (FGG484 package)
        "pcileech_enigma_x1": "IBUFDS_GTE2_X0Y1",
        "pcileech_75t484_x1": "IBUFDS_GTE2_X0Y1",
        "75t": "IBUFDS_GTE2_X0Y1",
        # Artix-7 35T boards (FGG484 package)
        "pcileech_35t484_x1": "IBUFDS_GTE2_X0Y0",
        "pcileech_squirrel": "IBUFDS_GTE2_X0Y0",
        "35t": "IBUFDS_GTE2_X0Y0",
        # Artix-7 35T boards (CSG324 package)
        "pcileech_35t325_x4": "IBUFDS_GTE2_X0Y0",
        "pcileech_35t325_x1": "IBUFDS_GTE2_X0Y0",
        "pcileech_pciescreamer_xc7a35": "IBUFDS_GTE2_X0Y0",
        # Artix-7 100T boards (FGG484 package)
        "pcileech_100t484_x1": "IBUFDS_GTE2_X0Y1",
        "100t": "IBUFDS_GTE2_X0Y1",
    }

    @classmethod
    def discover_boards(cls, repo_root: Optional[Path] = None) -> Dict[str, Dict]:
        """
        Discover all available boards from the pcileech-fpga repository.

        Args:
            repo_root: Optional repository root path (will clone if not provided)

        Returns:
            Dictionary mapping board names to their configurations
        """
        if repo_root is None:
            repo_root = RepoManager.ensure_repo()

        boards = {}

        # Discover standard boards
        for dir_name, config in cls.BOARD_DIR_PATTERNS.items():
            board_path = repo_root / dir_name
            if board_path.exists() and board_path.is_dir():
                board_name = config["name"]
                boards[board_name] = cls._analyze_board(board_path, config)
                log_info_safe(
                    logger,
                    "Discovered board: {board_name} at {board_path}",
                    board_name=board_name,
                    board_path=board_path,
                )

        # Discover CaptainDMA boards
        captaindma_root = repo_root / "CaptainDMA"
        if captaindma_root.exists() and captaindma_root.is_dir():
            for subdir, config in cls.CAPTAINDMA_BOARDS.items():
                board_path = captaindma_root / subdir
                if board_path.exists() and board_path.is_dir():
                    # Use canonical name if specified, otherwise construct standard name
                    board_name = config.get(
                        "canonical_name", f"pcileech_{subdir.replace('-', '_')}"
                    )
                    boards[board_name] = cls._analyze_board(
                        board_path, {"name": board_name, **config}
                    )
                    log_info_safe(
                        logger,
                        "Discovered CaptainDMA board: {board_name} at {board_path}",
                        board_name=board_name,
                        board_path=board_path,
                    )

        # Discover any additional boards by scanning for vivado project files
        additional_boards = cls._scan_for_additional_boards(repo_root, boards)
        boards.update(additional_boards)

        return boards

    @classmethod
    def _analyze_board(cls, board_path: Path, base_config: Dict) -> Dict:
        """
        Analyze a board directory to extract configuration details.

        Args:
            board_path: Path to the board directory
            base_config: Base configuration for the board

        Returns:
            Complete board configuration
        """
        config = base_config.copy()

        # Detect FPGA family from part number
        fpga_part = config.get("fpga_part", "")
        config["fpga_family"] = cls._detect_fpga_family(fpga_part)

        # Detect PCIe IP type
        config["pcie_ip_type"] = cls._detect_pcie_ip_type(board_path, fpga_part)

        # Scan for source files
        config["src_files"] = cls._find_source_files(board_path)
        config["ip_files"] = cls._find_ip_files(board_path)
        config["xdc_files"] = cls._find_constraint_files(board_path)
        config["coe_files"] = cls._find_coefficient_files(board_path)

        # Detect capabilities from source files
        capabilities = cls._detect_capabilities(board_path, config["src_files"])
        config.update(capabilities)

        # Set default values if not already present
        config.setdefault("max_lanes", 1)
        config.setdefault("supports_msi", True)
        config.setdefault("supports_msix", False)

        # Add PCIe reference clock LOC constraint for 7-series boards
        board_name = config.get("name", "")
        if board_name in cls.PCIE_REFCLK_LOC_MAP:
            config["pcie_refclk_loc"] = cls.PCIE_REFCLK_LOC_MAP[board_name]
        elif config["fpga_family"] == "7series":
            # Default to X0Y0 for unknown 7-series boards
            config["pcie_refclk_loc"] = "IBUFDS_GTE2_X0Y0"
            log_warning_safe(
                logger,
                safe_format("No PCIe refclk LOC mapping for board '{board}', using default: IBUFDS_GTE2_X0Y0", board=board_name)
            )

        return config

    @classmethod
    def _detect_fpga_family(cls, fpga_part: str) -> str:
        """Detect FPGA family from part number."""
        fpga_part_lower = fpga_part.lower()

        if any(
            fpga_part_lower.startswith(prefix)
            for prefix in ["xc7a", "xc7k", "xc7v", "xc7z"]
        ):
            return "7series"
        elif any(fpga_part_lower.startswith(prefix) for prefix in ["xcku", "xcvu"]):
            return "ultrascale"
        elif fpga_part_lower.startswith("xczu"):
            return "ultrascale_plus"
        else:
            return "7series"  # Default fallback

    @classmethod
    def _detect_pcie_ip_type(cls, board_path: Path, fpga_part: str) -> str:
        """Detect PCIe IP type based on board files and FPGA part."""
        # Check for specific IP files
        ip_indicators = {
            "pcie_axi": ["pcie_axi", "axi_pcie"],
            "pcie_7x": ["pcie_7x", "pcie7x"],
            "pcie_ultrascale": ["pcie_ultrascale", "xdma", "qdma"],
        }

        # Scan for IP files
        for ip_type, patterns in ip_indicators.items():
            for pattern in patterns:
                if any(board_path.rglob(f"*{pattern}*")):
                    return ip_type

        # Fallback based on FPGA part
        if "xc7a35t" in fpga_part:
            return "axi_pcie"
        elif "xczu" in fpga_part:
            return "pcie_ultrascale"
        else:
            return "pcie_7x"

    @classmethod
    def _find_source_files(cls, board_path: Path) -> List[str]:
        """Find SystemVerilog/Verilog source files."""
        src_dirs = [
            board_path,
            board_path / "src",
            board_path / "rtl",
            board_path / "hdl",
        ]
        files = []

        for src_dir in src_dirs:
            if src_dir.exists():
                files.extend([f.name for f in src_dir.glob("*.sv")])
                files.extend([f.name for f in src_dir.glob("*.v")])

        # Remove duplicates while preserving order
        seen = set()
        unique_files = []
        for f in files:
            if f not in seen:
                seen.add(f)
                unique_files.append(f)

        return unique_files

    @classmethod
    def _find_ip_files(cls, board_path: Path) -> List[str]:
        """Find IP core files."""
        ip_dirs = [board_path, board_path / "ip", board_path / "ips"]
        files = []

        for ip_dir in ip_dirs:
            if ip_dir.exists():
                files.extend([f.name for f in ip_dir.glob("*.xci")])
                files.extend([f.name for f in ip_dir.glob("*.xcix")])

        return list(set(files))

    @classmethod
    def _find_constraint_files(cls, board_path: Path) -> List[str]:
        """Find constraint files."""
        xdc_dirs = [
            board_path,
            board_path / "constraints",
            board_path / "xdc",
            board_path / "src",
        ]
        files = []

        for xdc_dir in xdc_dirs:
            if xdc_dir.exists():
                files.extend([f.name for f in xdc_dir.glob("*.xdc")])

        return list(set(files))

    @classmethod
    def _find_coefficient_files(cls, board_path: Path) -> List[str]:
        """Find coefficient files."""
        coe_dirs = [
            board_path,
            board_path / "coe",
            board_path / "coefficients",
            board_path / "src",
        ]
        files = []

        for coe_dir in coe_dirs:
            if coe_dir.exists():
                files.extend([f.name for f in coe_dir.glob("*.coe")])

        return list(set(files))

    @classmethod
    def _detect_capabilities(cls, board_path: Path, src_files: List[str]) -> Dict:
        """Detect board capabilities from source files."""
        capabilities = {
            "supports_msi": False,
            "supports_msix": False,
            "has_dma": False,
            "has_option_rom": False,
        }

        # Check source file names and content
        msix_patterns = ["msix", "msi_x", "msi-x"]
        msi_patterns = ["msi", "interrupt"]
        dma_patterns = ["dma", "tlp", "bar_controller"]
        rom_patterns = ["option_rom", "expansion_rom", "rom_bar"]

        for src_file in src_files:
            src_lower = src_file.lower()

            # Check MSI-X support
            if any(pattern in src_lower for pattern in msix_patterns):
                capabilities["supports_msix"] = True
                capabilities["supports_msi"] = True  # MSI-X implies MSI
            # Check MSI support
            elif any(pattern in src_lower for pattern in msi_patterns):
                capabilities["supports_msi"] = True

            # Check DMA support
            if any(pattern in src_lower for pattern in dma_patterns):
                capabilities["has_dma"] = True

            # Check Option ROM support
            if any(pattern in src_lower for pattern in rom_patterns):
                capabilities["has_option_rom"] = True

        # Also check file contents for more accurate detection
        src_dirs = [board_path, board_path / "src", board_path / "rtl"]
        for src_dir in src_dirs:
            if src_dir.exists():
                for sv_file in src_dir.glob("*.sv"):
                    try:
                        content = sv_file.read_text(
                            encoding="utf-8", errors="ignore"
                        ).lower()
                        if "msix" in content or "msi_x" in content:
                            capabilities["supports_msix"] = True
                            capabilities["supports_msi"] = True
                        elif "msi" in content and "interrupt" in content:
                            capabilities["supports_msi"] = True
                    except Exception:
                        pass  # Ignore read errors

        return capabilities

    @classmethod
    def _scan_for_additional_boards(
        cls, repo_root: Path, existing_boards: Dict[str, Dict]
    ) -> Dict[str, Dict]:
        """Scan for additional boards not covered by known patterns."""
        additional_boards = {}

        # Look for directories containing vivado project files or build scripts
        project_indicators = [
            "*.xpr",
            "build.tcl",
            "generate_project.tcl",
            "vivado_generate_project.tcl",
        ]

        for indicator in project_indicators:
            for project_file in repo_root.rglob(indicator):
                board_dir = project_file.parent

                # Skip if already discovered or in a subdirectory of a known board
                if any(
                    str(board_dir).startswith(str(repo_root / existing))
                    for existing in existing_boards
                ):
                    continue

                # Extract board name from directory
                board_name = board_dir.name.lower().replace("-", "_")
                if (
                    board_name not in existing_boards
                    and board_name not in additional_boards
                ):
                    # Try to extract FPGA part from project file
                    fpga_part = cls._extract_fpga_part_from_project(project_file)
                    if fpga_part:
                        additional_boards[board_name] = cls._analyze_board(
                            board_dir, {"name": board_name, "fpga_part": fpga_part}
                        )
                        log_info_safe(
                            logger,
                            "Discovered additional board: {board_name} at {board_dir}",
                            board_name=board_name,
                            board_dir=board_dir,
                        )

        return additional_boards

    @classmethod
    def _extract_fpga_part_from_project(cls, project_file: Path) -> Optional[str]:
        """Extract FPGA part number from project file."""
        try:
            content = project_file.read_text(encoding="utf-8", errors="ignore")

            # Look for part number patterns
            part_patterns = [
                r'part["\s]*[:=]\s*["\'](xc[^"\']+)["\']',
                r'PART["\s]*[:=]\s*["\'](xc[^"\']+)["\']',
                r'set_property\s+PART\s+["\'](xc[^"\']+)["\']',
                r'<Option Name="Part".*?Val="(xc[^"]+)"',
            ]

            for pattern in part_patterns:
                match = re.search(pattern, content, re.IGNORECASE)
                if match:
                    return match.group(1)
        except Exception:
            pass

        return None

    @classmethod
    def get_board_display_info(
        cls, boards: Dict[str, Dict]
    ) -> List[Tuple[str, Dict[str, str]]]:
        """
        Generate display information for discovered boards.

        Args:
            boards: Dictionary of discovered boards

        Returns:
            List of tuples (board_name, display_info) suitable for UI display
        """
        display_info = []

        # Recommended boards (based on common usage and features)
        recommended_boards = {"pcileech_75t484_x1", "pcileech_35t325_x4"}

        for board_name, config in boards.items():
            info = {
                "display_name": cls._format_display_name(board_name),
                "description": cls._generate_description(config),
                "is_recommended": board_name in recommended_boards,
            }
            display_info.append((board_name, info))

        # Sort with recommended boards first
        display_info.sort(key=lambda x: (not x[1]["is_recommended"], x[0]))

        return display_info

    @classmethod
    def _format_display_name(cls, board_name: str) -> str:
        """Format board name for display."""
        # Special cases
        special_names = {
            "35t": "35T Legacy Board",
            "75t": "75T Legacy Board",
            "100t": "100T Legacy Board",
            "pcileech_75t484_x1": "CaptainDMA 75T",
            "pcileech_35t484_x1": "CaptainDMA 35T x1",
            "pcileech_35t325_x4": "CaptainDMA 35T x4",
            "pcileech_35t325_x1": "CaptainDMA 35T x1 (325)",
            "pcileech_100t484_x1": "CaptainDMA 100T",
            "pcileech_enigma_x1": "CaptainDMA Enigma x1",
            "pcileech_squirrel": "CaptainDMA Squirrel",
            "pcileech_pciescreamer_xc7a35": "PCIeScreamer XC7A35",
        }

        if board_name in special_names:
            return special_names[board_name]

        # Generic formatting
        name = board_name.replace("pcileech_", "").replace("_", " ").title()
        return name

    @classmethod
    def _generate_description(cls, config: Dict) -> str:
        """Generate board description from configuration."""
        parts = []

        # Add FPGA info
        if "fpga_part" in config:
            parts.append(f"FPGA: {config['fpga_part']}")

        # Add capabilities
        caps = []
        if config.get("supports_msix"):
            caps.append("MSI-X")
        elif config.get("supports_msi"):
            caps.append("MSI")

        if config.get("has_dma"):
            caps.append("DMA")

        if config.get("has_option_rom"):
            caps.append("Option ROM")

        if caps:
            parts.append(f"Features: {', '.join(caps)}")

        # Add lane info
        if "max_lanes" in config and config["max_lanes"] > 1:
            parts.append(f"PCIe x{config['max_lanes']}")

        return " | ".join(parts) if parts else ""

    @classmethod
    def export_board_config(cls, boards: Dict[str, Dict], output_file: Path) -> None:
        """
        Export discovered board configurations to a JSON file.

        Args:
            boards: Dictionary of discovered boards
            output_file: Path to output JSON file
        """
        # Convert Path objects to strings for JSON serialization
        export_data = {}
        for board_name, config in boards.items():
            export_config = config.copy()
            # Convert lists to ensure they're JSON serializable
            for key in ["src_files", "ip_files", "xdc_files", "coe_files"]:
                if key in export_config:
                    export_config[key] = list(export_config[key])
            export_data[board_name] = export_config

        output_file.parent.mkdir(parents=True, exist_ok=True)
        with open(output_file, "w") as f:
            json.dump(export_data, f, indent=2, sort_keys=True)

        log_info_safe(
            logger,
            "Exported {count} board configurations to {output_file}",
            count=len(boards),
            output_file=output_file,
        )


def discover_all_boards(repo_root: Optional[Path] = None) -> Dict[str, Dict]:
    """
    Convenience function to discover all boards from the repository.

    Args:
        repo_root: Optional repository root path

    Returns:
        Dictionary mapping board names to their configurations
    """
    return BoardDiscovery.discover_boards(repo_root)


def get_board_config(board_name: str, repo_root: Optional[Path] = None) -> Dict:
    """
    Get configuration for a specific board.

    Args:
        board_name: Name of the board
        repo_root: Optional repository root path

    Returns:
        Board configuration dictionary

    Raises:
        KeyError: If board is not found
    """
    boards = discover_all_boards(repo_root)
    if board_name not in boards:
        raise KeyError(
            f"Board '{board_name}' not found. Available boards: {', '.join(boards.keys())}"
        )
    return boards[board_name]
