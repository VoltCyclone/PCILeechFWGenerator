#!/usr/bin/env python3
"""
PCILeech Build Integration Module

This module integrates the dynamic board discovery and template discovery
with the Vivado build process, ensuring that builds use the latest
templates and configurations from the pcileech-fpga repository.
"""

import logging
import shutil
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

from ..file_management.board_discovery import BoardDiscovery, get_board_config
from ..file_management.repo_manager import RepoManager
from ..file_management.template_discovery import TemplateDiscovery
from ..string_utils import log_error_safe, log_info_safe, log_warning_safe, safe_format
from ..templating.tcl_builder import BuildContext, TCLBuilder

logger = logging.getLogger(__name__)


class PCILeechBuildIntegration:
    """Integrates pcileech-fpga repository with the build process."""

    def __init__(self, output_dir: Path, repo_root: Optional[Path] = None):
        """
        Initialize the build integration.

        Args:
            output_dir: Output directory for build artifacts
            repo_root: Optional repository root path
        """
        self.output_dir = Path(output_dir)
        self.repo_root = repo_root or RepoManager.ensure_repo()
        self.output_dir.mkdir(parents=True, exist_ok=True)

        # Initialize components
        self.board_discovery = BoardDiscovery()
        self.template_discovery = TemplateDiscovery()

        # Cache discovered boards
        self._boards_cache = None
        self.prefix = "BUILD"

    def get_available_boards(self) -> Dict[str, Dict]:
        """
        Get all available boards from the repository.

        Returns:
            Dictionary mapping board names to configurations
        """
        if self._boards_cache is None:
            self._boards_cache = self.board_discovery.discover_boards(self.repo_root)
        return self._boards_cache

    def prepare_build_environment(self, board_name: str) -> Dict[str, Any]:
        """
        Prepare the build environment for a specific board.

        Args:
            board_name: Name of the board to build for

        Returns:
            Dictionary containing build configuration and paths

        Raises:
            ValueError: If board is not found
        """
        # Get board configuration
        boards = self.get_available_boards()
        if board_name not in boards:
            raise ValueError(
                safe_format(
                    "Board '{board_name}' not found. Available: {available_boards}",
                    board_name=board_name,
                    available_boards=", ".join(boards.keys()),
                )
            )

        board_config = boards[board_name]
        log_info_safe(
            logger,
            "Preparing build environment for {board_name}",
            board_name=board_name,
            prefix=self.prefix,
        )

        # Create board-specific output directory
        board_output_dir = self.output_dir / board_name
        board_output_dir.mkdir(parents=True, exist_ok=True)

        # Copy templates from repository
        templates = self.template_discovery.copy_board_templates(
            board_name, board_output_dir / "templates", self.repo_root
        )

        # Copy XDC files
        xdc_files = self._copy_xdc_files(board_name, board_output_dir / "constraints")

        # Copy source files from repository
        src_files = self._copy_source_files(board_name, board_output_dir / "src")

        # Get or create build scripts
        build_scripts = self._prepare_build_scripts(
            board_name, board_config, board_output_dir
        )

        return {
            "board_name": board_name,
            "board_config": board_config,
            "output_dir": board_output_dir,
            "templates": templates,
            "xdc_files": xdc_files,
            "src_files": src_files,
            "build_scripts": build_scripts,
        }

    def _copy_xdc_files(self, board_name: str, output_dir: Path) -> List[Path]:
        """Copy XDC constraint files for the board."""
        output_dir.mkdir(parents=True, exist_ok=True)
        copied_files = []

        try:
            xdc_files = RepoManager.get_xdc_files(board_name, repo_root=self.repo_root)
            for xdc_file in xdc_files:
                dest_path = output_dir / xdc_file.name
                shutil.copy2(xdc_file, dest_path)
                copied_files.append(dest_path)
                log_info_safe(
                    logger,
                    safe_format(
                        "Copied XDC file: {xdc_file_name}", xdc_file_name=xdc_file.name
                    ),
                    prefix=self.prefix,
                )
        except Exception as e:
            log_warning_safe(
                logger,
                safe_format("Failed to copy XDC files: {error}", error=e),
                prefix=self.prefix,
            )

        return copied_files

    def _copy_source_files(self, board_name: str, output_dir: Path) -> List[Path]:
        """Copy source files from the repository."""
        output_dir.mkdir(parents=True, exist_ok=True)
        copied_files = []

        # Get source files from template discovery
        src_files = self.template_discovery.get_source_files(board_name, self.repo_root)

        for src_file in src_files:
            try:
                # Preserve directory structure
                board_path = RepoManager.get_board_path(
                    board_name, repo_root=self.repo_root
                )
                relative_path = src_file.relative_to(board_path)
                dest_path = output_dir / relative_path

                dest_path.parent.mkdir(parents=True, exist_ok=True)
                shutil.copy2(src_file, dest_path)
                copied_files.append(dest_path)

            except Exception as e:
                log_warning_safe(
                    logger,
                    safe_format(
                        "Failed to copy source file {src_file}: {error}",
                        src_file=str(src_file),
                        error=e,
                    ),
                    prefix=self.prefix,
                )

        # Also copy core PCILeech files
        core_files = self.template_discovery.get_pcileech_core_files(self.repo_root)
        for filename, filepath in core_files.items():
            dest_path = output_dir / filename
            try:
                shutil.copy2(filepath, dest_path)
                copied_files.append(dest_path)
                log_info_safe(
                    logger,
                    safe_format("Copied core file: {filename}", filename=filename),
                    prefix=self.prefix,
                )
            except Exception as e:
                log_warning_safe(
                    logger,
                    safe_format(
                        "Failed to copy core file {filename}: {error}",
                        filename=filename,
                        error=e,
                    ),
                    prefix=self.prefix,
                )

        return copied_files

    def _prepare_build_scripts(
        self, board_name: str, board_config: Dict, output_dir: Path
    ) -> Dict[str, Path]:
        """Prepare Vivado build scripts."""
        scripts_dir = output_dir / "scripts"
        scripts_dir.mkdir(parents=True, exist_ok=True)

        build_scripts = {}

        # Check if board has its own build script
        existing_script = self.template_discovery.get_vivado_build_script(
            board_name, self.repo_root
        )

        if existing_script:
            # Copy and adapt existing script
            dest_path = scripts_dir / existing_script.name
            shutil.copy2(existing_script, dest_path)

            # Adapt script content if needed
            content = dest_path.read_text()
            adapted_content = self.template_discovery.adapt_template_for_board(
                content, board_config
            )
            dest_path.write_text(adapted_content)

            build_scripts["main"] = dest_path
            log_info_safe(
                logger,
                safe_format(
                    "Using existing build script: {script_name}",
                    script_name=existing_script.name,
                ),
                prefix=self.prefix,
            )
        else:
            # Generate build scripts using TCLBuilder
            log_info_safe(
                logger, "Generating build scripts using TCLBuilder", prefix=self.prefix
            )
            build_scripts.update(
                self._generate_build_scripts(board_config, scripts_dir)
            )

        return build_scripts

    def _generate_build_scripts(
        self, board_config: Dict, output_dir: Path
    ) -> Dict[str, Path]:
        """Generate build scripts using TCLBuilder."""
        try:
            # Create TCL builder instance
            tcl_builder = TCLBuilder(output_dir=output_dir)

            # Create build context from board config
            context = BuildContext(
                board_name=board_config["name"],
                fpga_part=board_config["fpga_part"],
                fpga_family=board_config["fpga_family"],
                pcie_ip_type=board_config["pcie_ip_type"],
                max_lanes=board_config.get("max_lanes", 1),
                supports_msi=board_config.get("supports_msi", True),
                supports_msix=board_config.get("supports_msix", False),
                project_name=f"pcileech_{board_config['name']}",
                output_dir=str(output_dir.parent),
            )

            # Generate scripts
            scripts = {}

            # Project setup script
            project_script = tcl_builder.build_pcileech_project_script(context)
            project_path = output_dir / "vivado_generate_project.tcl"
            project_path.write_text(project_script)
            scripts["project"] = project_path

            # Build script
            build_script = tcl_builder.build_pcileech_build_script(context)
            build_path = output_dir / "vivado_build.tcl"
            build_path.write_text(build_script)
            scripts["build"] = build_path

            return scripts

        except Exception as e:
            log_error_safe(
                logger,
                safe_format(
                    "Failed to generate build scripts: {error}",
                    error=e,
                ),
                prefix=self.prefix,
            )
            return {}

    def create_unified_build_script(
        self, board_name: str, device_config: Optional[Dict] = None
    ) -> Path:
        """
        Create a unified build script that incorporates all necessary steps.

        Args:
            board_name: Name of the board
            device_config: Optional device-specific configuration

        Returns:
            Path to the unified build script
        """
        # Prepare build environment
        build_env = self.prepare_build_environment(board_name)

        # Create unified script
        script_path = build_env["output_dir"] / "build_all.tcl"

        board_config = build_env.get("board_config", {})
        fpga_part = board_config.get("fpga_part", "<MISSING_FPGA_PART>")
        project_name = safe_format("pcileech_{board_name}", board_name=board_name)

        script_content = safe_format(
            """
# PCILeech Unified Build Script for {board_name}
# Generated by PCILeechBuildIntegration

puts "Starting PCILeech build for board: {board_name}"
puts "FPGA Part: {fpga_part}"

# Set project parameters
set PROJECT_NAME "{project_name}"
set PROJECT_DIR "./vivado_project"
set OUTPUT_DIR "./output"
set FPGA_PART "{fpga_part}"

# Create project directory
file mkdir $PROJECT_DIR
file mkdir $OUTPUT_DIR

# Source the project generation script if it exists
if {{[file exists "scripts/vivado_generate_project.tcl"]}} {{
    puts "Sourcing project generation script..."
    source "scripts/vivado_generate_project.tcl"
}} else {{
    puts "Creating project manually..."
    create_project $PROJECT_NAME $PROJECT_DIR -part $FPGA_PART -force
}}

# Add source files
puts "Adding source files..."
""",
            board_name=board_name,
            fpga_part=fpga_part,
            project_name=project_name,
        )

        # Add source files
        script_content += "\n# Add source files\n"
        script_content += 'puts "Adding source files..."\n'
        for src_file in build_env["src_files"]:
            # Convert to absolute path to avoid path resolution issues in Vivado
            abs_path = Path(src_file).resolve()
            script_content += f'add_files -norecurse "{abs_path}"\n'

        # Ensure all .sv files are treated as SystemVerilog
        script_content += (
            "set sv_in_proj [get_files -of_objects [get_filesets sources_1] *.sv]\n"
            "if {[llength $sv_in_proj] > 0} {\n"
            '    puts "Setting file type=SystemVerilog for [llength $sv_in_proj] .sv files"\n'
            "    set_property file_type SystemVerilog $sv_in_proj\n"
            "    foreach sv_file $sv_in_proj {\n"
            '        puts "  -> File type now: [get_property file_type [get_files $sv_file]] ($sv_file)"\n'
            "    }\n"
            "}\n"
        )

        # Refresh compile order after file-type changes
        script_content += "update_compile_order -fileset sources_1\n"

        # Add constraints
        script_content += "\n# Add constraint files\n"
        script_content += 'puts "Adding constraint files..."\n'
        for xdc_file in build_env["xdc_files"]:
            # Convert to absolute path to avoid path resolution issues in Vivado
            abs_path = Path(xdc_file).resolve()
            script_content += f'add_files -fileset constrs_1 -norecurse "{abs_path}"\n'

        # Add synthesis and implementation
        script_content += safe_format(
            """
# Configure run concurrency
set RUN_JOBS 8
if {{[info exists ::env(VIVADO_RUN_JOBS)] && $::env(VIVADO_RUN_JOBS) > 0}} {{
    set RUN_JOBS $::env(VIVADO_RUN_JOBS)
}}

# Run synthesis
puts "Running synthesis with $RUN_JOBS job(s)..."
launch_runs synth_1 -jobs $RUN_JOBS
wait_on_run synth_1

# Check synthesis results
if {{[get_property PROGRESS [get_runs synth_1]] != "100%"}} {{
    error "Synthesis failed"
}}

# Run implementation
puts "Running implementation with $RUN_JOBS job(s)..."
launch_runs impl_1 -to_step write_bitstream -jobs $RUN_JOBS
wait_on_run impl_1

# Check implementation results
if {{[get_property PROGRESS [get_runs impl_1]] != "100%"}} {{
    error "Implementation failed"
}}

# Copy bitstream to output directory
set BITSTREAM_DIR [get_property DIRECTORY [get_runs impl_1]]
set BITSTREAM_NAME [get_property top [current_fileset]].bit
set BITSTREAM_FILE [file join $BITSTREAM_DIR $BITSTREAM_NAME]

if {{![file exists $BITSTREAM_FILE]}} {{
    error [format "Bitstream not found at %s" $BITSTREAM_FILE]
}}

if {{[catch {{file copy -force $BITSTREAM_FILE $OUTPUT_DIR/}} copy_error]}} {{
    error [format "Failed to copy bitstream: %s" $copy_error]
}}

puts "Build completed successfully!"
puts "Bitstream location: $OUTPUT_DIR/$BITSTREAM_NAME"
"""
        )

        script_path.write_text(script_content)

        return script_path

    def validate_board_compatibility(
        self, board_name: str, device_config: Dict
    ) -> Tuple[bool, List[str]]:
        """
        Validate if a board is compatible with the device configuration.

        Args:
            board_name: Name of the board
            device_config: Device configuration to validate against

        Returns:
            Tuple of (is_compatible, list_of_warnings)
        """
        warnings = []
        board_config = get_board_config(board_name, self.repo_root)

        # Check MSI-X support
        if device_config.get("requires_msix", False) and not board_config.get(
            "supports_msix", False
        ):
            warnings.append(
                safe_format(
                    "Board {board_name} does not support MSI-X but device requires it",
                    board_name=board_name,
                )
            )

        # Check PCIe lanes
        device_lanes = device_config.get("pcie_lanes", 1)
        board_lanes = board_config.get("max_lanes", 1)
        if device_lanes > board_lanes:
            warnings.append(
                safe_format(
                    "Device requires {device_lanes} PCIe lanes but board supports only {board_lanes}",
                    device_lanes=device_lanes,
                    board_lanes=board_lanes,
                )
            )

        # Check FPGA resources (simplified check)
        if board_config.get("fpga_family") == "7series" and device_config.get(
            "requires_ultrascale", False
        ):
            warnings.append(
                "Device requires UltraScale features but board has 7-series FPGA"
            )

        is_compatible = len(warnings) == 0
        return is_compatible, warnings


def integrate_pcileech_build(
    board_name: str,
    output_dir: Path,
    device_config: Optional[Dict] = None,
    repo_root: Optional[Path] = None,
    prefix: str = "BUILD",
) -> Path:
    """
    Convenience function to integrate PCILeech build for a specific board.

    Args:
        board_name: Name of the board
        output_dir: Output directory for build artifacts
        device_config: Optional device-specific configuration
        repo_root: Optional repository root path

    Returns:
        Path to the unified build script
    """
    integration = PCILeechBuildIntegration(output_dir, repo_root)

    # Validate compatibility if device config provided
    if device_config:
        is_compatible, warnings = integration.validate_board_compatibility(
            board_name, device_config
        )
        if warnings:
            for warning in warnings:
                log_warning_safe(logger, safe_format(warning), prefix=prefix)
        if not is_compatible:
            log_error_safe(
                logger,
                safe_format(
                    "Board {board_name} is not compatible with device configuration",
                    board_name=board_name,
                ),
                prefix=prefix,
            )

    return integration.create_unified_build_script(board_name, device_config)
