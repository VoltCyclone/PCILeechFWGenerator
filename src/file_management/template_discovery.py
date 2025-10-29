#!/usr/bin/env python3
"""
Template Discovery Module

This module provides functionality to discover and use templates from the
cloned pcileech-fpga repository, allowing the build process to use the
latest templates from the upstream repository.
"""

import shutil
from pathlib import Path
from typing import Any, Dict, List, Optional

from ..log_config import get_logger
from ..string_utils import (log_debug_safe, log_error_safe, log_info_safe,
                            log_warning_safe)
from .repo_manager import RepoManager

logger = get_logger(__name__)


class TemplateDiscovery:
    """Discover and manage templates from pcileech-fpga repository."""

    # Known template patterns in pcileech-fpga
    TEMPLATE_PATTERNS = {
        "vivado_tcl": ["*.tcl", "build/*.tcl", "scripts/*.tcl"],
        "systemverilog": ["*.sv", "src/*.sv", "rtl/*.sv", "hdl/*.sv"],
        "verilog": ["*.v", "src/*.v", "rtl/*.v", "hdl/*.v"],
        "constraints": ["*.xdc", "constraints/*.xdc", "xdc/*.xdc"],
        "ip_config": ["*.xci", "ip/*.xci", "ips/*.xci"],
    }

    @classmethod
    def discover_templates(
        cls, board_name: str, repo_root: Optional[Path] = None
    ) -> Dict[str, List[Path]]:
        """
        Discover all templates for a specific board from the repository.

        Args:
            board_name: Name of the board to discover templates for
            repo_root: Optional repository root path

        Returns:
            Dictionary mapping template types to lists of template paths
        """
        if repo_root is None:
            repo_root = RepoManager.ensure_repo()

        # Get board path
        try:
            board_path = RepoManager.get_board_path(board_name, repo_root=repo_root)
        except RuntimeError as e:
            log_error_safe(
                logger,
                "Failed to get board path for {board_name}: {error}",
                board_name=board_name,
                error=e,
            )
            return {}

        templates = {}

        # Discover templates by type
        for template_type, patterns in cls.TEMPLATE_PATTERNS.items():
            template_files = []
            for pattern in patterns:
                template_files.extend(board_path.glob(pattern))

            if template_files:
                templates[template_type] = template_files
                log_info_safe(
                    logger,
                    "Found {count} {template_type} templates for {board_name}",
                    count=len(template_files),
                    template_type=template_type,
                    board_name=board_name,
                )

        return templates

    @classmethod
    def get_vivado_build_script(
        cls, board_name: str, repo_root: Optional[Path] = None
    ) -> Optional[Path]:
        """
        Get the main Vivado build script for a board.

        Args:
            board_name: Name of the board
            repo_root: Optional repository root path

        Returns:
            Path to the build script, or None if not found
        """
        templates = cls.discover_templates(board_name, repo_root)
        tcl_scripts = templates.get("vivado_tcl", [])

        # Look for common build script names
        build_script_names = [
            "vivado_build.tcl",
            "build.tcl",
            "generate_project.tcl",
            "vivado_generate_project.tcl",
            "create_project.tcl",
        ]

        for script in tcl_scripts:
            if script.name in build_script_names:
                return script

        # If no standard name found, return the first TCL script
        return tcl_scripts[0] if tcl_scripts else None

    @classmethod
    def get_source_files(
        cls, board_name: str, repo_root: Optional[Path] = None
    ) -> List[Path]:
        """
        Get all source files (SystemVerilog/Verilog) for a board.

        Args:
            board_name: Name of the board
            repo_root: Optional repository root path

        Returns:
            List of source file paths
        """
        templates = cls.discover_templates(board_name, repo_root)
        source_files = []

        # Combine SystemVerilog and Verilog files
        source_files.extend(templates.get("systemverilog", []))
        source_files.extend(templates.get("verilog", []))

        return source_files

    @classmethod
    def copy_board_templates(
        cls, board_name: str, output_dir: Path, repo_root: Optional[Path] = None
    ) -> Dict[str, List[Path]]:
        """
        Copy all templates for a board to the output directory.

        Args:
            board_name: Name of the board
            output_dir: Directory to copy templates to
            repo_root: Optional repository root path

        Returns:
            Dictionary mapping template types to lists of copied file paths
        """
        templates = cls.discover_templates(board_name, repo_root)
        copied_templates = {}

        # Create output directory structure
        output_dir.mkdir(parents=True, exist_ok=True)

        for template_type, template_files in templates.items():
            copied_files = []

            # Create subdirectory for each template type
            type_dir = output_dir / template_type
            type_dir.mkdir(exist_ok=True)

            for template_file in template_files:
                # Preserve relative path structure
                try:
                    board_path = RepoManager.get_board_path(
                        board_name, repo_root=repo_root
                    )
                    relative_path = template_file.relative_to(board_path)
                    dest_path = type_dir / relative_path

                    # Create parent directories
                    dest_path.parent.mkdir(parents=True, exist_ok=True)

                    # Copy file
                    shutil.copy2(template_file, dest_path)
                    copied_files.append(dest_path)

                except Exception as e:
                    log_warning_safe(
                        logger,
                        "Failed to copy template {template_file}: {error}",
                        template_file=template_file,
                        error=e,
                    )

            if copied_files:
                copied_templates[template_type] = copied_files
                log_info_safe(
                    logger,
                    "Copied {count} {template_type} templates to {type_dir}",
                    count=len(copied_files),
                    template_type=template_type,
                    type_dir=type_dir,
                )

        return copied_templates

    @classmethod
    def get_template_content(
        cls,
        board_name: str,
        template_name: str,
        template_type: Optional[str] = None,
        repo_root: Optional[Path] = None,
    ) -> Optional[str]:
        """
        Get the content of a specific template file.

        Args:
            board_name: Name of the board
            template_name: Name of the template file
            template_type: Optional template type to narrow search
            repo_root: Optional repository root path

        Returns:
            Template content as string, or None if not found
        """
        templates = cls.discover_templates(board_name, repo_root)

        # Search in specific type or all types
        search_types = [template_type] if template_type else templates.keys()

        for t_type in search_types:
            if t_type in templates:
                for template_file in templates[t_type]:
                    if template_file.name == template_name:
                        try:
                            return template_file.read_text(encoding="utf-8")
                        except Exception as e:
                            log_error_safe(
                                logger,
                                "Failed to read template {template_file}: {error}",
                                template_file=template_file,
                                error=e,
                            )
                            return None

        return None

    @classmethod
    def merge_with_local_templates(
        cls,
        board_name: str,
        local_template_dir: Path,
        output_dir: Path,
        repo_root: Optional[Path] = None,
    ) -> None:
        """
        Merge repository templates with local templates, with local taking precedence.

        Args:
            board_name: Name of the board
            local_template_dir: Directory containing local templates
            output_dir: Directory to write merged templates
            repo_root: Optional repository root path
        """
        # First copy repository templates
        repo_templates = cls.copy_board_templates(board_name, output_dir, repo_root)

        # Then overlay local templates
        if local_template_dir.exists():
            log_info_safe(
                logger,
                "Overlaying local templates from {local_template_dir}",
                local_template_dir=local_template_dir,
            )

            for local_file in local_template_dir.rglob("*"):
                if local_file.is_file():
                    relative_path = local_file.relative_to(local_template_dir)
                    dest_path = output_dir / relative_path

                    # Create parent directories
                    dest_path.parent.mkdir(parents=True, exist_ok=True)

                    # Copy local file (overwriting if exists)
                    shutil.copy2(local_file, dest_path)
                    log_debug_safe(
                        logger,
                        "Overlaid local template: {relative_path}",
                        relative_path=relative_path,
                    )

    @classmethod
    def get_pcileech_core_files(
        cls, repo_root: Optional[Path] = None
    ) -> Dict[str, Path]:
        """
        Get paths to core PCILeech files that are common across boards.

        Args:
            repo_root: Optional repository root path

        Returns:
            Dictionary mapping core file names to their paths
        """
        if repo_root is None:
            repo_root = RepoManager.ensure_repo()

        core_files = {}

        # Look for common PCILeech core files
        common_files = [
            "pcileech_tlps128_bar_controller.sv",
            "pcileech_tlps128_bar_controller_template.sv",
            "pcileech_fifo.sv",
            "pcileech_mux.sv",
            "pcileech_com.sv",
            "pcileech_pcie_cfg_a7.sv",
            "pcileech_pcie_cfg_us.sv",
            "pcileech.svh",
            "tlp_pkg.svh",  # TLP package definitions
            "bar_controller.sv",
            "cfg_shadow.sv",
            "pcileech_pcie_tlp_a7.sv",
        ]

        # Search in common locations
        search_dirs = [
            repo_root,
            repo_root / "common",
            repo_root / "shared",
            repo_root / "pcileech_shared",
        ]

        for filename in common_files:
            for search_dir in search_dirs:
                if search_dir.exists():
                    # Direct search
                    file_path = search_dir / filename
                    if file_path.exists():
                        core_files[filename] = file_path
                        break

                    # Recursive search
                    matches = list(search_dir.rglob(filename))
                    if matches:
                        core_files[filename] = matches[0]
                        break

        log_info_safe(
            logger, "Found {count} core PCILeech files", count=len(core_files)
        )
        return core_files

    @classmethod
    def adapt_template_for_board(
        cls, template_content: str, board_config: Dict[str, Any]
    ) -> str:
        """
        Adapt a template's content for a specific board configuration.

        Args:
            template_content: Original template content
            board_config: Board configuration dictionary

        Returns:
            Adapted template content
        """
        # Simple placeholder replacement for common patterns
        replacements = {
            "${FPGA_PART}": board_config.get("fpga_part", ""),
            "${FPGA_FAMILY}": board_config.get("fpga_family", ""),
            "${PCIE_IP_TYPE}": board_config.get("pcie_ip_type", ""),
            "${MAX_LANES}": str(board_config.get("max_lanes", 1)),
            "${BOARD_NAME}": board_config.get("name", ""),
        }

        adapted_content = template_content
        for placeholder, value in replacements.items():
            adapted_content = adapted_content.replace(placeholder, value)

        return adapted_content


def discover_board_templates(
    board_name: str, repo_root: Optional[Path] = None
) -> Dict[str, List[Path]]:
    """
    Convenience function to discover templates for a board.

    Args:
        board_name: Name of the board
        repo_root: Optional repository root path

    Returns:
        Dictionary mapping template types to lists of template paths
    """
    return TemplateDiscovery.discover_templates(board_name, repo_root)


def copy_templates_for_build(
    board_name: str,
    output_dir: Path,
    local_template_dir: Optional[Path] = None,
    repo_root: Optional[Path] = None,
) -> None:
    """
    Copy and merge templates for a build.

    Args:
        board_name: Name of the board
        output_dir: Output directory for templates
        local_template_dir: Optional local template directory to overlay
        repo_root: Optional repository root path
    """
    if local_template_dir:
        TemplateDiscovery.merge_with_local_templates(
            board_name, local_template_dir, output_dir, repo_root
        )
    else:
        TemplateDiscovery.copy_board_templates(board_name, output_dir, repo_root)
