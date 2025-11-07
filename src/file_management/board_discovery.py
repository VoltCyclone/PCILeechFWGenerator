#!/usr/bin/env python3
"""
Board Discovery Module

This module provides functionality to discover and analyze available boards
from the voltcyclone-fpga git submodule, extracting board capabilities
and configurations dynamically.
"""

import json
import os
from pathlib import Path
from typing import Dict, List, Optional, Tuple

from ..log_config import get_logger
from ..string_utils import (log_debug_safe, log_error_safe, log_info_safe,
                            log_warning_safe, safe_format)
from .repo_manager import RepoManager

logger = get_logger(__name__)


class BoardDiscovery:
    """Discover and analyze boards from voltcyclone-fpga submodule.
    
    This class should NOT be instantiated - use class methods only.
    """
    
    def __new__(cls, *args, **kwargs):
        raise TypeError(
            "BoardDiscovery may not be instantiated; call class methods only"
        )

    # Board configurations - aligned with RepoManager.get_board_path()
    # These match the actual directory structure in voltcyclone-fpga submodule
    BOARD_CONFIGS = {
        # Legacy name mappings
        "35t": {
            "dir": "PCIeSquirrel",
            "fpga_part": "xc7a35tfgg484-2",
            "max_lanes": 1,
        },
        "75t": {
            "dir": "EnigmaX1",
            "fpga_part": "xc7a75tfgg484-2",
            "max_lanes": 1,
        },
        "100t": {
            "dir": "ZDMA",
            "fpga_part": "xczu3eg-sbva484-1-e",
            "max_lanes": 1,
        },
        # Modern board names
        "pcileech_enigma_x1": {
            "dir": "EnigmaX1",
            "fpga_part": "xc7a75tfgg484-2",
            "max_lanes": 1,
        },
        "pcileech_squirrel": {
            "dir": "PCIeSquirrel",
            "fpga_part": "xc7a35tfgg484-2",
            "max_lanes": 1,
        },
        "pcileech_pciescreamer_xc7a35": {
            "dir": "pciescreamer",
            "fpga_part": "xc7a35tcsg324-2",
            "max_lanes": 1,
        },
        # CaptainDMA boards
        "pcileech_75t484_x1": {
            "dir": "CaptainDMA/75t484_x1",
            "fpga_part": "xc7a75tfgg484-2",
            "max_lanes": 1,
        },
        "pcileech_35t484_x1": {
            "dir": "CaptainDMA/35t484_x1",
            "fpga_part": "xc7a35tfgg484-2",
            "max_lanes": 1,
        },
        "pcileech_35t325_x4": {
            "dir": "CaptainDMA/35t325_x4",
            "fpga_part": "xc7a35tcsg324-2",
            "max_lanes": 4,
        },
        "pcileech_35t325_x1": {
            "dir": "CaptainDMA/35t325_x1",
            "fpga_part": "xc7a35tcsg324-2",
            "max_lanes": 1,
        },
        "pcileech_100t484_x1": {
            "dir": "CaptainDMA/100t484-1",
            "fpga_part": "xc7a100tfgg484-1",
            "max_lanes": 1,
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
        Discover all available boards from the voltcyclone-fpga submodule.

        Args:
            repo_root: Optional repository root path (uses submodule if not provided)

        Returns:
            Dictionary mapping board names to their configurations
        """
        if repo_root is None:
            repo_root = RepoManager.ensure_repo()

        boards = {}

        # Iterate through known board configurations
        for board_name, config in cls.BOARD_CONFIGS.items():
            board_path = repo_root / config["dir"]
            if board_path.exists() and board_path.is_dir():
                boards[board_name] = cls._analyze_board(
                    board_name, board_path, config
                )
                log_debug_safe(
                    logger,
                    safe_format(
                        "Discovered board: {name} at {path}",
                        name=board_name,
                        path=board_path
                    ),
                    prefix="BOARDS"
                )
            else:
                log_warning_safe(
                    logger,
                    safe_format(
                        "Board '{name}' directory not found at {path}",
                        name=board_name,
                        path=board_path
                    ),
                    prefix="BOARDS"
                )

        log_info_safe(
            logger,
            safe_format(
                "Discovered {count} boards from submodule",
                count=len(boards)
            ),
            prefix="BOARDS"
        )

        return boards

    @classmethod
    def _analyze_board(
        cls, board_name: str, board_path: Path, base_config: Dict
    ) -> Dict:
        """
        Analyze a board directory to extract configuration details.

        Args:
            board_name: Name identifier for the board
            board_path: Path to the board directory
            base_config: Base configuration for the board

        Returns:
            Complete board configuration
        """
        config = base_config.copy()
        config["name"] = board_name

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
        config.setdefault("supports_msi", True)
        config.setdefault("supports_msix", False)

        # Add PCIe reference clock LOC constraint for 7-series boards
        if board_name in cls.PCIE_REFCLK_LOC_MAP:
            config["pcie_refclk_loc"] = cls.PCIE_REFCLK_LOC_MAP[board_name]
        elif config["fpga_family"] == "7series":
            # Default to X0Y0 for unknown 7-series boards
            config["pcie_refclk_loc"] = "IBUFDS_GTE2_X0Y0"
            log_warning_safe(
                logger,
                safe_format(
                    "No PCIe refclk LOC mapping for '{board}', "
                    "using default: IBUFDS_GTE2_X0Y0",
                    board=board_name
                ),
                prefix="BOARDS"
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
                files.extend(sorted(f.name for f in src_dir.glob("*.sv")))
                files.extend(sorted(f.name for f in src_dir.glob("*.v")))

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
                files.extend(sorted(f.name for f in ip_dir.glob("*.xci")))
                files.extend(sorted(f.name for f in ip_dir.glob("*.xcix")))

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
                files.extend(sorted(f.name for f in xdc_dir.glob("*.xdc")))

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
                files.extend(sorted(f.name for f in coe_dir.glob("*.coe")))

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
        
        # Atomic write: write to temp file and replace
        tmp = output_file.with_suffix(output_file.suffix + ".tmp")
        with open(tmp, "w", encoding="utf-8") as f:
            json.dump(export_data, f, indent=2, sort_keys=True)
            f.flush()
            os.fsync(f.fileno())
        tmp.replace(output_file)

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
        available = ", ".join(boards.keys())
        raise KeyError(
            safe_format(
                "Board '{name}' not found. Available boards: {avail}",
                name=board_name,
                avail=available
            )
        )
    return boards[board_name]
