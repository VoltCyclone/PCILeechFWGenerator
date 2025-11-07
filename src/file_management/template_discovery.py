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

from ..error_utils import extract_root_cause
from ..exceptions import (
    FileOperationError,
    RepositoryError,
    TemplateNotFoundError,
)

from ..log_config import get_logger

from ..string_utils import (
    log_debug_safe,
    log_error_safe,
    log_info_safe,
    log_warning_safe,
    safe_format,
)
from .repo_manager import RepoManager

logger = get_logger(__name__)


class TemplateDiscovery:
    """Discover and manage templates from pcileech-fpga repository."""

    # Known template patterns in pcileech-fpga
    TEMPLATE_PATTERNS = {
        "vivado_tcl": ["*.tcl", "build/*.tcl", "scripts/*.tcl"],
        "systemverilog": [
            "*.sv", "*.svh",
            "src/*.sv", "src/*.svh",
            "rtl/*.sv", "rtl/*.svh",
            "hdl/*.sv", "hdl/*.svh"
        ],
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

        Raises:
            RepositoryError: If board path cannot be accessed or repo unavailable
        """
        # Validate inputs
        if not board_name or not isinstance(board_name, str):
            raise RepositoryError(
                safe_format(
                    "Invalid board_name: must be non-empty string, got {type}",
                    type=type(board_name).__name__,
                )
            )

        if repo_root is None:
            try:
                repo_root = RepoManager.ensure_repo()
            except Exception as e:
                root_cause = extract_root_cause(e)
                raise RepositoryError(
                    safe_format(
                        "Failed to access repository for board {board}",
                        board=board_name,
                    ),
                    root_cause=root_cause,
                ) from e

        # Get board path with proper error handling
        try:
            board_path = RepoManager.get_board_path(board_name, repo_root=repo_root)
        except RuntimeError as e:
            root_cause = extract_root_cause(e)
            log_error_safe(
                logger,
                safe_format(
                    "Board path unavailable for {board}: {cause}",
                    board=board_name,
                    cause=root_cause,
                ),
                prefix="TEMPLATE_DISCOVERY",
            )
            raise RepositoryError(
                safe_format(
                    "Board {board} not found in repository",
                    board=board_name,
                ),
                root_cause=root_cause,
            ) from e

        if not board_path.exists():
            raise RepositoryError(
                safe_format(
                    "Board directory does not exist: {path}",
                    path=board_path,
                )
            )

        templates = {}

        # Discover templates by type
        for template_type, patterns in cls.TEMPLATE_PATTERNS.items():
            template_files = []
            for pattern in patterns:
                try:
                    template_files.extend(board_path.glob(pattern))
                except Exception as e:
                    log_warning_safe(
                        logger,
                        safe_format(
                            "Failed to glob pattern {pattern} in {path}: {error}",
                            pattern=pattern,
                            path=board_path,
                            error=e,
                        ),
                        prefix="TEMPLATE_DISCOVERY",
                    )
                    continue

            if template_files:
                templates[template_type] = template_files
                log_info_safe(
                    logger,
                    safe_format(
                        "Found {count} {type} templates for {board}",
                        count=len(template_files),
                        type=template_type,
                        board=board_name,
                    ),
                    prefix="TEMPLATE_DISCOVERY",
                )

        if not templates:
            log_warning_safe(
                logger,
                safe_format(
                    "No templates found for board {board} in {path}",
                    board=board_name,
                    path=board_path,
                ),
                prefix="TEMPLATE_DISCOVERY",
            )

        return templates

    @classmethod
    def get_vivado_build_script(
        cls, board_name: str, repo_root: Optional[Path] = None
    ) -> Path:
        """
        Get the main Vivado build script for a board.

        Args:
            board_name: Name of the board
            repo_root: Optional repository root path

        Returns:
            Path to the build script

        Raises:
            TemplateNotFoundError: If no build script found for the board
        """
        templates = cls.discover_templates(board_name, repo_root)
        tcl_scripts = templates.get("vivado_tcl", [])

        if not tcl_scripts:
            raise TemplateNotFoundError(
                safe_format(
                    "No Vivado TCL scripts found for board {board}",
                    board=board_name,
                )
            )

        # Look for common build script names (in priority order)
        build_script_names = [
            "vivado_build.tcl",
            "build.tcl",
            "generate_project.tcl",
            "vivado_generate_project.tcl",
            "create_project.tcl",
        ]

        for script_name in build_script_names:
            for script in tcl_scripts:
                if script.name == script_name:
                    log_info_safe(
                        logger,
                        safe_format(
                            "Using build script: {script}",
                            script=script.name,
                        ),
                        prefix="TEMPLATE_DISCOVERY",
                    )
                    return script

        # If no standard name found, return the first TCL script
        log_warning_safe(
            logger,
            safe_format(
                "No standard build script found; using first available: {script}",
                script=tcl_scripts[0].name,
            ),
            prefix="TEMPLATE_DISCOVERY",
        )
        return tcl_scripts[0]

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

        Raises:
            TemplateNotFoundError: If no source files found for the board
        """
        templates = cls.discover_templates(board_name, repo_root)
        source_files = []

        # Combine SystemVerilog and Verilog files
        source_files.extend(templates.get("systemverilog", []))
        source_files.extend(templates.get("verilog", []))

        if not source_files:
            raise TemplateNotFoundError(
                safe_format(
                    "No source files (.sv, .svh, .v) found for board {board}",
                    board=board_name,
                )
            )

        log_info_safe(
            logger,
            safe_format(
                "Found {count} source files for {board}",
                count=len(source_files),
                board=board_name,
            ),
            prefix="TEMPLATE_DISCOVERY",
        )

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

        Raises:
            FileOperationError: If directory creation or file copy fails
            RepositoryError: If templates cannot be discovered
        """
        templates = cls.discover_templates(board_name, repo_root)
        copied_templates = {}

        # Validate output directory is writable
        try:
            output_dir.mkdir(parents=True, exist_ok=True)
        except Exception as e:
            root_cause = extract_root_cause(e)
            raise FileOperationError(
                safe_format(
                    "Cannot create output directory: {path}",
                    path=output_dir,
                ),
                root_cause=root_cause,
            ) from e

        if not templates:
            log_warning_safe(
                logger,
                safe_format(
                    "No templates to copy for board {board}",
                    board=board_name,
                ),
                prefix="TEMPLATE_DISCOVERY",
            )
            return copied_templates

        # Get board path once for efficiency
        try:
            board_path = RepoManager.get_board_path(board_name, repo_root=repo_root)
        except RuntimeError as e:
            root_cause = extract_root_cause(e)
            raise RepositoryError(
                safe_format("Board path unavailable: {board}", board=board_name),
                root_cause=root_cause,
            ) from e

        for template_type, template_files in templates.items():
            copied_files = []

            # Create subdirectory for each template type
            type_dir = output_dir / template_type
            try:
                type_dir.mkdir(exist_ok=True)
            except Exception as e:
                log_error_safe(
                    logger,
                    safe_format(
                        "Failed to create type directory {dir}: {error}",
                        dir=type_dir,
                        error=e,
                    ),
                    prefix="TEMPLATE_DISCOVERY",
                )
                continue

            for template_file in template_files:
                # Preserve relative path structure
                try:
                    relative_path = template_file.relative_to(board_path)
                    dest_path = type_dir / relative_path

                    # Create parent directories
                    dest_path.parent.mkdir(parents=True, exist_ok=True)

                    # Copy file with metadata preservation
                    shutil.copy2(template_file, dest_path)
                    copied_files.append(dest_path)

                    log_debug_safe(
                        logger,
                        safe_format(
                            "Copied: {src} -> {dst}",
                            src=template_file.name,
                            dst=dest_path,
                        ),
                        prefix="TEMPLATE_DISCOVERY",
                    )

                except Exception as e:
                    root_cause = extract_root_cause(e)
                    log_warning_safe(
                        logger,
                        safe_format(
                            "Failed to copy {file}: {cause}",
                            file=template_file.name,
                            cause=root_cause,
                        ),
                        prefix="TEMPLATE_DISCOVERY",
                    )

            if copied_files:
                copied_templates[template_type] = copied_files
                log_info_safe(
                    logger,
                    safe_format(
                        "Copied {count} {type} templates to {dir}",
                        count=len(copied_files),
                        type=template_type,
                        dir=type_dir,
                    ),
                    prefix="TEMPLATE_DISCOVERY",
                )

        if not copied_templates:
            log_warning_safe(
                logger,
                safe_format(
                    "No templates successfully copied for board {board}",
                    board=board_name,
                ),
                prefix="TEMPLATE_DISCOVERY",
            )

        return copied_templates

    @classmethod
    def get_template_content(
        cls,
        board_name: str,
        template_name: str,
        template_type: Optional[str] = None,
        repo_root: Optional[Path] = None,
    ) -> str:
        """
        Get the content of a specific template file.

        Args:
            board_name: Name of the board
            template_name: Name of the template file
            template_type: Optional template type to narrow search
            repo_root: Optional repository root path

        Returns:
            Template content as string

        Raises:
            TemplateNotFoundError: If template file not found or cannot be read
        """
        # Validate inputs
        if not template_name or not isinstance(template_name, str):
            raise TemplateNotFoundError(
                safe_format(
                    "Invalid template_name: must be non-empty string, got {type}",
                    type=type(template_name).__name__,
                )
            )

        templates = cls.discover_templates(board_name, repo_root)

        # Search in specific type or all types
        search_types = [template_type] if template_type else templates.keys()

        for t_type in search_types:
            if t_type in templates:
                for template_file in templates[t_type]:
                    if template_file.name == template_name:
                        try:
                            content = template_file.read_text(encoding="utf-8")
                            log_debug_safe(
                                logger,
                                safe_format(
                                    "Read template {name}: {size} bytes",
                                    name=template_name,
                                    size=len(content),
                                ),
                                prefix="TEMPLATE_DISCOVERY",
                            )
                            return content
                        except Exception as e:
                            root_cause = extract_root_cause(e)
                            log_error_safe(
                                logger,
                                safe_format(
                                    "Failed to read {file}: {cause}",
                                    file=template_file,
                                    cause=root_cause,
                                ),
                                prefix="TEMPLATE_DISCOVERY",
                            )
                            raise TemplateNotFoundError(
                                safe_format(
                                    "Cannot read template {name}",
                                    name=template_name,
                                ),
                                root_cause=root_cause,
                            ) from e

        # Template not found in any searched type
        search_desc = f"type '{template_type}'" if template_type else "any type"
        raise TemplateNotFoundError(
            safe_format(
                "Template {name} not found in {search} for board {board}",
                name=template_name,
                search=search_desc,
                board=board_name,
            )
        )

    @classmethod
    def merge_with_local_templates(
        cls,
        board_name: str,
        local_template_dir: Path,
        output_dir: Path,
        repo_root: Optional[Path] = None,
    ) -> None:
        """
        Merge repository templates with local templates, with local
        taking precedence.

        Args:
            board_name: Name of the board
            local_template_dir: Directory containing local templates
            output_dir: Directory to write merged templates
            repo_root: Optional repository root path

        Raises:
            FileOperationError: If template merging fails
            RepositoryError: If repository templates cannot be accessed
        """
        # Validate local template directory
        if not isinstance(local_template_dir, Path):
            raise FileOperationError(
                safe_format(
                    "local_template_dir must be Path, got {type}",
                    type=type(local_template_dir).__name__,
                )
            )

        # First copy repository templates
        repo_templates = cls.copy_board_templates(board_name, output_dir, repo_root)

        # Then overlay local templates if they exist
        if not local_template_dir.exists():
            log_debug_safe(
                logger,
                safe_format(
                    "Local template dir does not exist: {path}",
                    path=local_template_dir,
                ),
                prefix="TEMPLATE_DISCOVERY",
            )
            return

        log_info_safe(
            logger,
            safe_format(
                "Overlaying local templates from {dir}",
                dir=local_template_dir,
            ),
            prefix="TEMPLATE_DISCOVERY",
        )

        overlay_count = 0
        try:
            for local_file in local_template_dir.rglob("*"):
                if not local_file.is_file():
                    continue

                try:
                    relative_path = local_file.relative_to(local_template_dir)
                    dest_path = output_dir / relative_path

                    # Create parent directories
                    dest_path.parent.mkdir(parents=True, exist_ok=True)

                    # Copy local file (overwriting if exists)
                    shutil.copy2(local_file, dest_path)
                    overlay_count += 1

                    log_debug_safe(
                        logger,
                        safe_format(
                            "Overlaid: {path}",
                            path=relative_path,
                        ),
                        prefix="TEMPLATE_DISCOVERY",
                    )

                except Exception as e:
                    root_cause = extract_root_cause(e)
                    log_warning_safe(
                        logger,
                        safe_format(
                            "Failed to overlay {file}: {cause}",
                            file=local_file.name,
                            cause=root_cause,
                        ),
                        prefix="TEMPLATE_DISCOVERY",
                    )

        except Exception as e:
            root_cause = extract_root_cause(e)
            raise FileOperationError(
                safe_format(
                    "Failed to traverse local templates in {dir}",
                    dir=local_template_dir,
                ),
                root_cause=root_cause,
            ) from e

        log_info_safe(
            logger,
            safe_format(
                "Overlaid {count} local template files",
                count=overlay_count,
            ),
            prefix="TEMPLATE_DISCOVERY",
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

        Raises:
            RepositoryError: If repository cannot be accessed
        """
        if repo_root is None:
            try:
                repo_root = RepoManager.ensure_repo()
            except Exception as e:
                root_cause = extract_root_cause(e)
                raise RepositoryError(
                    "Failed to access repository for core files",
                    root_cause=root_cause,
                ) from e

        if not repo_root.exists():
            raise RepositoryError(
                safe_format(
                    "Repository root does not exist: {path}",
                    path=repo_root,
                )
            )

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
            "pcileech_header.svh",
            "tlp_pkg.svh",
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
                if not search_dir.exists():
                    continue

                # Direct search (faster)
                file_path = search_dir / filename
                if file_path.exists() and file_path.is_file():
                    core_files[filename] = file_path
                    log_debug_safe(
                        logger,
                        safe_format(
                            "Found core file: {file} at {path}",
                            file=filename,
                            path=file_path,
                        ),
                        prefix="TEMPLATE_DISCOVERY",
                    )
                    break

                # Recursive search if not found directly
                try:
                    matches = list(search_dir.rglob(filename))
                    if matches:
                        core_files[filename] = matches[0]
                        log_debug_safe(
                            logger,
                            safe_format(
                                "Found core file (recursive): {file} at {path}",
                                file=filename,
                                path=matches[0],
                            ),
                            prefix="TEMPLATE_DISCOVERY",
                        )
                        break
                except Exception as e:
                    log_debug_safe(
                        logger,
                        safe_format(
                            "Failed to search {dir} for {file}: {error}",
                            dir=search_dir,
                            file=filename,
                            error=e,
                        ),
                        prefix="TEMPLATE_DISCOVERY",
                    )

        log_info_safe(
            logger,
            safe_format(
                "Found {count} of {total} core PCILeech files",
                count=len(core_files),
                total=len(common_files),
            ),
            prefix="TEMPLATE_DISCOVERY",
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

        Raises:
            ValueError: If template_content or board_config is invalid
        """
        # Validate inputs
        if not isinstance(template_content, str):
            raise ValueError(
                safe_format(
                    "template_content must be str, got {type}",
                    type=type(template_content).__name__,
                )
            )

        if not isinstance(board_config, dict):
            raise ValueError(
                safe_format(
                    "board_config must be dict, got {type}",
                    type=type(board_config).__name__,
                )
            )

        if not template_content:
            log_warning_safe(
                logger,
                "Empty template_content provided for adaptation",
                prefix="TEMPLATE_DISCOVERY",
            )
            return template_content

        # Simple placeholder replacement for common patterns
        # Note: Only non-donor-unique values should be templated here
        replacements = {
            "${FPGA_PART}": str(board_config.get("fpga_part", "")),
            "${FPGA_FAMILY}": str(board_config.get("fpga_family", "")),
            "${PCIE_IP_TYPE}": str(board_config.get("pcie_ip_type", "")),
            "${MAX_LANES}": str(board_config.get("max_lanes", 1)),
            "${BOARD_NAME}": str(board_config.get("name", "")),
        }

        adapted_content = template_content
        replacement_count = 0

        for placeholder, value in replacements.items():
            if placeholder in adapted_content:
                adapted_content = adapted_content.replace(placeholder, value)
                replacement_count += 1
                log_debug_safe(
                    logger,
                    safe_format(
                        "Replaced {placeholder} with {value}",
                        placeholder=placeholder,
                        value=value,
                    ),
                    prefix="TEMPLATE_DISCOVERY",
                )

        if replacement_count > 0:
            log_info_safe(
                logger,
                safe_format(
                    "Adapted template with {count} replacements",
                    count=replacement_count,
                ),
                prefix="TEMPLATE_DISCOVERY",
            )

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
