#!/usr/bin/env python3
"""
File Management Module

Handles file operations, cleanup, and validation for PCILeech firmware building.
"""

import fnmatch

import hashlib

import logging

import shutil

import time

from pathlib import Path

from typing import Any, Dict, List

from ..__version__ import __version__

from ..device_clone.constants import (PCILEECH_BUILD_SCRIPT,
                                      PCILEECH_PROJECT_SCRIPT)

from ..string_utils import (format_kv_table, get_project_name, log_debug_safe,
                            log_error_safe, log_info_safe, log_warning_safe,
                            safe_format, safe_print_format)

logger = logging.getLogger(__name__)


class FileManager:
    """Manages file operations for PCILeech firmware building."""

    def __init__(
        self,
        output_dir: Path,
        min_bitstream_size_mb: float = 0.5,
        max_bitstream_size_mb: float = 10.0,
    ):
        self.output_dir = output_dir
        self.min_bitstream_size_mb = min_bitstream_size_mb
        self.max_bitstream_size_mb = max_bitstream_size_mb

    def create_pcileech_structure(
        self, src_dir: str = "src", ip_dir: str = "ip"
    ) -> Dict[str, Path]:
        """
        Create PCILeech directory structure with src/ and ip/ directories.

        Args:
            src_dir: Name of the source directory (default: "src")
            ip_dir: Name of the IP directory (default: "ip")

        Returns:
            Dictionary mapping directory names to Path objects
        """
        directories = {}

        # Create source directory
        src_path = self.output_dir / src_dir
        src_path.mkdir(parents=True, exist_ok=True)
        directories["src"] = src_path

        # Create IP directory
        ip_path = self.output_dir / ip_dir
        ip_path.mkdir(parents=True, exist_ok=True)
        directories["ip"] = ip_path

        log_info_safe(
            logger, "Created PCILeech directory structure", prefix="FILEMGR"
        )
        log_info_safe(
            logger,
            safe_format("  Source directory: {src_path}", src_path=src_path),
            prefix="FILEMGR",
        )
        log_info_safe(
            logger,
            safe_format("  IP directory: {ip_path}", ip_path=ip_path),
            prefix="FILEMGR",
        )

        return directories

    def write_to_src_directory(
        self, filename: str, content: str, src_dir: str = "src"
    ) -> Path:
        """
        Write content to a file in the PCILeech src directory.

        Args:
            filename: Name of the file to write
            content: Content to write to the file
            src_dir: Name of the source directory (default: "src")

        Returns:
            Path to the written file
        """
        src_path = self.output_dir / src_dir
        src_path.mkdir(parents=True, exist_ok=True)

        file_path = src_path / filename
        with open(file_path, "w") as f:
            f.write(content)

        log_info_safe(
            logger,
            safe_format("Written file to src directory: {filename}", filename=filename),
            prefix="FILEMGR",
        )
        return file_path

    def write_to_ip_directory(
        self, filename: str, content: str, ip_dir: str = "ip"
    ) -> Path:
        """
        Write content to a file in the PCILeech ip directory.

        Args:
            filename: Name of the file to write
            content: Content to write to the file
            ip_dir: Name of the IP directory (default: "ip")

        Returns:
            Path to the written file
        """
        ip_path = self.output_dir / ip_dir
        ip_path.mkdir(parents=True, exist_ok=True)

        file_path = ip_path / filename
        with open(file_path, "w") as f:
            f.write(content)

        log_info_safe(
            logger,
            safe_format("Written file to ip directory: {filename}", filename=filename),
            prefix="FILEMGR",
        )
        return file_path

    def cleanup_intermediate_files(self) -> List[str]:
        """Clean up intermediate files, keeping only final outputs and logs."""
        preserved_files = []
        cleaned_files = []

        # Patterns for files to preserve
        preserve_patterns = [
            "*.bit",   # Final bitstream
            "*.mcs",   # Flash memory file
            "*.ltx",   # Debug probes
            "*.dcp",   # Design checkpoint
            "*.log",   # Log files
            "*.rpt",   # Report files
            "*.tcl",   # TCL scripts
            "*.sv",    # SystemVerilog source files
            "*.v",     # Verilog source files
            "*.xdc",   # Constraint files
            "*.hex",   # Hex memory files
        ]

        # Patterns for files/directories to clean
        cleanup_patterns = [
            "vivado_project/",   # Vivado project directory
            "project_dir/",      # Alternative project directory
            "*.json",            # JSON intermediate files
            "*.jou",             # Vivado journal files
            "*.str",             # Vivado strategy files
            ".Xil/",             # Xilinx temporary directory
        ]

        log_info_safe(
            logger, "Starting cleanup of intermediate files...", prefix="FILEMGR"
        )

        try:
            all_files = list(self.output_dir.rglob("*"))

            for file_path in all_files:
                # Check if file should be preserved
                if self._should_preserve_file(file_path, preserve_patterns):
                    preserved_files.append(str(file_path))
                    continue

                # Check if file should be cleaned
                if self._should_cleanup_path(file_path, cleanup_patterns):
                    if self._cleanup_path(file_path):
                        cleaned_files.append(str(file_path))

            log_info_safe(
                logger,
                "Cleanup completed: preserved {preserved_count} files, cleaned {cleaned_count} items",
                prefix="FILEMGR",
                preserved_count=len(preserved_files),
                cleaned_count=len(cleaned_files),
            )

        except OSError as e:
            log_error_safe(
                logger,
                safe_format(
                    "Filesystem error during cleanup: {error}",
                    error=str(e)
                ),
                prefix="FILEMGR",
            )
        except Exception as e:
            log_error_safe(
                logger,
                safe_format("Unexpected error during cleanup: {error}", error=str(e)),
                prefix="FILEMGR",
            )

        return preserved_files

    def _should_preserve_file(self, file_path: Path, patterns: List[str]) -> bool:
        """Check if file matches preservation patterns."""
        return any(fnmatch.fnmatch(file_path.name, pattern) for pattern in patterns)

    def _should_cleanup_path(self, file_path: Path, patterns: List[str]) -> bool:
        """Check if path matches cleanup patterns."""
        for pattern in patterns:
            if pattern.endswith("/"):
                # Directory pattern
                if file_path.is_dir() and fnmatch.fnmatch(file_path.name + "/", pattern):
                    return True
            else:
                # File pattern
                if file_path.is_file() and fnmatch.fnmatch(file_path.name, pattern):
                    return True
        return False

    def _cleanup_path(self, file_path: Path) -> bool:
        """Clean up a file or directory, return True if successful."""
        try:
            if file_path.is_dir():
                shutil.rmtree(file_path)
                log_info_safe(
                    logger,
                    safe_format("Cleaned directory: {name}", name=file_path.name),
                    prefix="FILEMGR",
                )
            else:
                file_path.unlink()
                log_debug_safe(
                    logger,
                    safe_format("Cleaned file: {name}", name=file_path.name),
                    prefix="FILEMGR",
                )
            return True
        except PermissionError as e:
            log_warning_safe(
                logger,
                safe_format(
                    "Permission denied cleaning {name}: {error}",
                    name=file_path.name,
                    error=str(e),
                ),
                prefix="FILEMGR",
            )
            return False
        except FileNotFoundError:
            # File already removed, consider success
            log_debug_safe(
                logger,
                safe_format("Path already removed: {name}", name=file_path.name),
                prefix="FILEMGR",
            )
            return True
        except OSError as e:
            log_warning_safe(
                logger,
                safe_format(
                    "OS error cleaning {name}: {error}",
                    name=file_path.name,
                    error=str(e),
                ),
                prefix="FILEMGR",
            )
            return False
        except Exception as e:
            log_error_safe(
                logger,
                safe_format(
                    "Unexpected error cleaning {name}: {error}",
                    name=file_path.name,
                    error=str(e),
                ),
                prefix="FILEMGR",
            )
            return False

    def validate_final_outputs(self) -> Dict[str, Any]:
        """Validate and provide information about final output files."""
        validation_results = {
            "bitstream_info": None,
            "flash_file_info": None,
            "debug_file_info": None,
            "tcl_file_info": None,
            "reports_info": [],
            "validation_status": "unknown",
            "file_sizes": {},
            "checksums": {},
            "build_mode": "unknown",
        }

        try:
            # Check for TCL build file (main output when Vivado not available)
            tcl_files = self._find_tcl_build_script()
            
            if tcl_files:
                tcl_file = tcl_files[0]
                file_size = tcl_file.stat().st_size

                with open(tcl_file, "r") as f:
                    content = f.read()
                    file_hash = hashlib.sha256(content.encode()).hexdigest()

                # Check if TCL script contains hex generation commands
                has_hex_generation = (
                    "write_cfgmem" in content
                    and "format hex" in content
                    and ".hex" in content
                ) or "07_bitstream.tcl" in content

                # For master build scripts, check for sourcing of individual scripts
                # rather than direct commands
                has_device_config = (
                    "CONFIG.Device_ID" in content
                    or "02_ip_config.tcl" in content
                    or "Device:" in content
                )

                has_synthesis = (
                    "launch_runs synth_1" in content or "05_synthesis.tcl" in content
                )

                has_implementation = (
                    "launch_runs impl_1" in content
                    or "06_implementation.tcl" in content
                )

                validation_results["tcl_file_info"] = {
                    "filename": tcl_file.name,
                    "size_bytes": file_size,
                    "size_kb": round(file_size / 1024, 2),
                    "sha256": file_hash,
                    "has_device_config": has_device_config,
                    "has_synthesis": has_synthesis,
                    "has_implementation": has_implementation,
                    "has_hex_generation": has_hex_generation,
                }
                validation_results["file_sizes"][tcl_file.name] = file_size
                validation_results["checksums"][tcl_file.name] = file_hash

                # Check for actual hex files (only if Vivado was run)
                hex_files = list(self.output_dir.glob("*.hex"))
                if hex_files:
                    hex_file = hex_files[0]
                    hex_size = hex_file.stat().st_size
                    # Create a new dictionary with the updated hex_file info
                    tcl_info = validation_results.get("tcl_file_info", {})
                    if isinstance(tcl_info, dict):
                        # Create a new dict with all existing values
                        tcl_info_updated = dict(tcl_info)
                        tcl_info_updated["hex_file"] = {
                            "filename": hex_file.name,
                            "size_bytes": hex_size,
                            "size_kb": round(hex_size / 1024, 2),
                        }
                        # Replace the entire tcl_file_info dict
                        validation_results["tcl_file_info"] = tcl_info_updated
                    validation_results["file_sizes"][hex_file.name] = hex_size
                else:
                    # Check if hex generation commands are present
                    tcl_info = validation_results.get("tcl_file_info", {})
                    if isinstance(tcl_info, dict):
                        tcl_info_updated = dict(tcl_info)
                        tcl_info_updated["hex_file"] = has_hex_generation
                        validation_results["tcl_file_info"] = tcl_info_updated

            # Check for bitstream file (only if Vivado was run)
            bitstream_files = list(self.output_dir.glob("*.bit"))
            if bitstream_files:
                bitstream_file = bitstream_files[0]
                file_size = bitstream_file.stat().st_size

                # Calculate checksum
                with open(bitstream_file, "rb") as f:
                    file_hash = hashlib.sha256(f.read()).hexdigest()

                validation_results["bitstream_info"] = {
                    "filename": bitstream_file.name,
                    "size_bytes": file_size,
                    "size_mb": round(file_size / (1024 * 1024), 2),
                    "sha256": file_hash,
                    "created": bitstream_file.stat().st_mtime,
                }
                validation_results["file_sizes"][bitstream_file.name] = file_size
                validation_results["checksums"][bitstream_file.name] = file_hash
                validation_results["build_mode"] = "full_vivado"
            else:
                validation_results["build_mode"] = "tcl_only"

            # Check for MCS flash file
            mcs_files = list(self.output_dir.glob("*.mcs"))
            if mcs_files:
                mcs_file = mcs_files[0]
                file_size = mcs_file.stat().st_size

                with open(mcs_file, "rb") as f:
                    file_hash = hashlib.sha256(f.read()).hexdigest()

                validation_results["flash_file_info"] = {
                    "filename": mcs_file.name,
                    "size_bytes": file_size,
                    "size_mb": round(file_size / (1024 * 1024), 2),
                    "sha256": file_hash,
                }
                validation_results["file_sizes"][mcs_file.name] = file_size
                validation_results["checksums"][mcs_file.name] = file_hash

            # Check for debug file
            ltx_files = list(self.output_dir.glob("*.ltx"))
            if ltx_files:
                ltx_file = ltx_files[0]
                file_size = ltx_file.stat().st_size

                validation_results["debug_file_info"] = {
                    "filename": ltx_file.name,
                    "size_bytes": file_size,
                }
                validation_results["file_sizes"][ltx_file.name] = file_size

            # Check for report files
            report_files = list(self.output_dir.glob("*.rpt"))
            for report_file in report_files:
                file_size = report_file.stat().st_size
                validation_results["reports_info"].append(
                    {
                        "filename": report_file.name,
                        "size_bytes": file_size,
                        "type": self._determine_report_type(report_file.name),
                    }
                )
                validation_results["file_sizes"][report_file.name] = file_size

            # Determine overall validation status
            validation_results["validation_status"] = self._determine_validation_status(
                validation_results
            )

        except OSError as e:
            log_error_safe(
                logger,
                "Filesystem error during validation: {error}",
                prefix="FILEMGR",
                error=str(e),
            )
            validation_results["validation_status"] = "error"
        except Exception as e:
            log_error_safe(
                logger,
                "Unexpected error during validation: {error}",
                prefix="FILEMGR",
                error=str(e),
            )
            validation_results["validation_status"] = "error"

        return validation_results

    def _find_tcl_build_script(self) -> List[Path]:
        """Find TCL build script, checking both legacy and current naming conventions."""
        # Check legacy names for backward compatibility
        for legacy_name in ["build_firmware.tcl", "build_all.tcl"]:
            tcl_files = list(self.output_dir.glob(legacy_name))
            if tcl_files:
                return tcl_files
        
        # Check current PCILeech script names
        for script_name in [PCILEECH_BUILD_SCRIPT, PCILEECH_PROJECT_SCRIPT]:
            tcl_files = list(self.output_dir.glob(script_name))
            if tcl_files:
                return tcl_files
        
        return []

    def _determine_validation_status(self, validation_results: Dict[str, Any]) -> str:
        """Determine overall validation status based on build outputs."""
        if not validation_results.get("tcl_file_info"):
            return "failed_no_tcl"
        
        if validation_results["build_mode"] == "full_vivado":
            return self._validate_full_vivado_build(validation_results)
        
        return self._validate_tcl_only_build(validation_results)

    def _validate_full_vivado_build(self, validation_results: Dict[str, Any]) -> str:
        """Validate full Vivado build outputs."""
        bitstream_info = validation_results.get("bitstream_info", {})
        
        if not bitstream_info:
            return "failed_no_bitstream"
        
        if isinstance(bitstream_info, dict):
            size_bytes = bitstream_info.get("size_bytes", 0)
            if size_bytes > 1_000_000:  # > 1MB
                return "success_full_build"
        
        return "warning_small_bitstream"

    def _validate_tcl_only_build(self, validation_results: Dict[str, Any]) -> str:
        """Validate TCL-only build outputs."""
        tcl_info = validation_results.get("tcl_file_info", {})
        
        if not isinstance(tcl_info, dict):
            return "warning_incomplete_tcl"
        
        has_device_config = tcl_info.get("has_device_config", False)
        size_bytes = tcl_info.get("size_bytes", 0)
        has_hex_generation = tcl_info.get("has_hex_generation", False)
        
        if not has_hex_generation:
            return "warning_missing_hex"
        
        if has_device_config and size_bytes > 1000:
            return "success_tcl_ready"
        
        return "warning_incomplete_tcl"

    def _determine_report_type(self, filename: str) -> str:
        """Determine the type of report based on filename."""
        filename_lower = filename.lower()
        
        report_types = {
            "timing": "timing_analysis",
            "utilization": "resource_utilization",
            "power": "power_analysis",
            "drc": "design_rule_check",
        }
        
        for keyword, report_type in report_types.items():
            if keyword in filename_lower:
                return report_type
        
        return "general"

    def generate_project_file(
        self, device_info: Dict[str, Any], board: str
    ) -> Dict[str, Any]:
        """Generate project configuration file."""
        return {
            "project_name": get_project_name(),
            "board": board,
            "device_info": device_info,
            "build_timestamp": time.time(),
            "build_version": __version__,
            "features": {
                "advanced_sv": False,  # Will be updated by caller if needed
                "manufacturing_variance": False,  # Will be updated by caller if needed
                "behavior_profiling": False,  # Will be updated by caller if needed
            },
        }

    def generate_file_manifest(
        self, device_info: Dict[str, Any], board: str
    ) -> Dict[str, Any]:
        """Generate a manifest of all files for verification."""
        manifest = {
            "project_info": {
                "device": f"{device_info['vendor_id']}:{device_info['device_id']}",
                "board": board,
                "generated_at": time.strftime("%Y-%m-%d %H:%M:%S"),
            },
            "files": {
                "systemverilog": [],
                "verilog": [],
                "constraints": [],
                "tcl_scripts": [],
                "generated": [],
            },
            "validation": {
                "required_files_present": True,
                "top_module_identified": False,
                "build_script_ready": False,
            },
        }

        # Check for files in output directory
        output_files = list(self.output_dir.glob("*"))

        for file_path in output_files:
            if file_path.suffix == ".sv":
                manifest["files"]["systemverilog"].append(file_path.name)
                if "top" in file_path.name.lower():
                    manifest["validation"]["top_module_identified"] = True
            elif file_path.suffix == ".v":
                manifest["files"]["verilog"].append(file_path.name)
            elif file_path.suffix == ".xdc":
                manifest["files"]["constraints"].append(file_path.name)
            elif file_path.suffix == ".tcl":
                manifest["files"]["tcl_scripts"].append(file_path.name)
                if "build" in file_path.name:
                    manifest["validation"]["build_script_ready"] = True
            elif file_path.suffix == ".json":
                manifest["files"]["generated"].append(file_path.name)

        # Validate required files
        required_files = ["device_config.sv", "pcileech_top.sv"]
        manifest["validation"]["required_files_present"] = all(
            f.lower() in [file.lower() for file in manifest["files"]["systemverilog"]]
            for f in required_files
        )

        return manifest

    def copy_pcileech_sources(self, board: str) -> Dict[str, List[str]]:
        """Copy PCILeech source files to output directory."""
        copied_files = {
            "systemverilog": [],
            "verilog": [],
            "packages": [],
            "constraints": [],
            "ip_files": [],
        }

        try:
            # Import repo manager
            from ..file_management.repo_manager import RepoManager

            # Ensure PCILeech repository is available
            repo_path = RepoManager.ensure_repo()
            log_info_safe(
                logger,
                "Using PCILeech repository at: {repo_path}",
                prefix="FILEMGR",
                repo_path=repo_path,
            )

            # Get board-specific path
            board_path = RepoManager.get_board_path(board, repo_root=repo_path)
            log_info_safe(
                logger,
                safe_format("Board path: {board_path}", board_path=board_path),
                prefix="FILEMGR",
            )

            # Create source directory structure
            src_dir = self.output_dir / "src"
            src_dir.mkdir(parents=True, exist_ok=True)

            # Copy board-specific source files
            if board_path.exists():
                # Look for SystemVerilog/Verilog files in board directory
                for pattern in ["*.sv", "*.v"]:
                    for src_file in board_path.rglob(pattern):
                        if src_file.is_file():
                            dest_file = src_dir / src_file.name
                            shutil.copy2(src_file, dest_file)

                            if src_file.suffix == ".sv":
                                copied_files["systemverilog"].append(str(dest_file))
                            else:
                                copied_files["verilog"].append(str(dest_file))

                            log_info_safe(
                                logger,
                                "Copied source file: {src_name}",
                                prefix="FILEMGR",
                                src_name=src_file.name,
                            )

                # Copy header and package files (avoid duplicates)
                copied_names = set()
                self._copy_package_files(
                    board_path, src_dir, copied_files, copied_names
                )

            # Copy local PCILeech files from project directory
            local_pcileech_dir = Path(__file__).parent.parent.parent / "pcileech"
            if local_pcileech_dir.exists():
                log_info_safe(
                    logger,
                    safe_format(
                        "Copying local PCILeech files from: {local_dir}",
                        local_dir=local_pcileech_dir,
                    ),
                    prefix="FILEMGR",
                )

                # Copy package files
                for pkg_file in local_pcileech_dir.glob("*.svh"):
                    dest_file = src_dir / pkg_file.name
                    shutil.copy2(pkg_file, dest_file)
                    copied_files["packages"].append(str(dest_file))
                    log_info_safe(
                        logger,
                        safe_format(
                            "Copied local package: {pkg_name}",
                            pkg_name=pkg_file.name
                        ),
                        prefix="FILEMGR",
                    )

                # Copy RTL files
                rtl_dir = local_pcileech_dir / "rtl"
                if rtl_dir.exists():
                    for rtl_file in rtl_dir.glob("*.sv"):
                        dest_file = src_dir / rtl_file.name
                        shutil.copy2(rtl_file, dest_file)
                        copied_files["systemverilog"].append(str(dest_file))
                        log_info_safe(
                            logger,
                            safe_format(
                                "Copied local RTL: {rtl_name}",
                                rtl_name=rtl_file.name
                            ),
                            prefix="FILEMGR",
                        )

            # Copy constraint files using repo manager
            try:
                xdc_files = RepoManager.get_xdc_files(board, repo_root=repo_path)
                constraints_dir = self.output_dir / "constraints"
                constraints_dir.mkdir(parents=True, exist_ok=True)

                for xdc_file in xdc_files:
                    dest_file = constraints_dir / xdc_file.name
                    shutil.copy2(xdc_file, dest_file)
                    copied_files["constraints"].append(str(dest_file))
                    log_info_safe(
                        logger,
                        safe_format(
                            "Copied constraint file: {xdc_name}",
                            xdc_name=xdc_file.name
                        ),
                        prefix="FILEMGR",
                    )

            except FileNotFoundError as e:
                log_warning_safe(
                    logger,
                    safe_format(
                        "Constraint files not found for board {board}: {error}",
                        board=board,
                        error=str(e)
                    ),
                    prefix="FILEMGR",
                )
            except (OSError, IOError) as e:
                log_warning_safe(
                    logger,
                    safe_format(
                        "Could not copy constraint files: {error}",
                        error=str(e)
                    ),
                    prefix="FILEMGR",
                )
            except Exception as e:
                log_error_safe(
                    logger,
                    safe_format(
                        "Unexpected error copying constraint files: {error}",
                        error=str(e)
                    ),
                    prefix="FILEMGR",
                )

            # Log summary
            total_files = sum(len(files) for files in copied_files.values())
            log_info_safe(
                logger,
                safe_format(
                    "Successfully copied {total_files} PCILeech source files",
                    total_files=total_files,
                ),
                prefix="FILEMGR",
            )

        except ImportError as e:
            log_error_safe(
                logger,
                safe_format(
                    "Could not import repo manager module: {error}",
                    error=str(e)
                ),
                prefix="FILEMGR",
            )
        except FileNotFoundError as e:
            log_error_safe(
                logger,
                safe_format(
                    "Source files not found for board {board}: {error}",
                    board=board,
                    error=str(e)
                ),
                prefix="FILEMGR",
            )
        except (OSError, IOError) as e:
            log_error_safe(
                logger,
                safe_format(
                    "Filesystem error copying PCILeech sources: {error}",
                    error=str(e)
                ),
                prefix="FILEMGR",
            )
        except Exception as e:
            log_error_safe(
                logger,
                safe_format(
                    "Unexpected error copying PCILeech sources: {error}",
                    error=str(e)
                ),
                prefix="FILEMGR",
            )

        return copied_files

    def _copy_package_files(
        self,
        board_path: Path,
        src_dir: Path,
        copied_files: Dict[str, List[str]],
        copied_names: set,
    ) -> None:
        """Copy header and package files from board path to source directory."""
        # Copy header files (.svh)
        for header_file in board_path.rglob("*.svh"):
            if header_file.is_file() and header_file.name not in copied_names:
                dest_file = src_dir / header_file.name
                shutil.copy2(header_file, dest_file)
                copied_files["packages"].append(str(dest_file))
                copied_names.add(header_file.name)
                log_info_safe(
                    logger,
                    safe_format("Copied header file: {name}", name=header_file.name),
                    prefix="FILEMGR",
                )

        # Copy package files (*_pkg.sv*)
        for pkg_file in board_path.rglob("*_pkg.sv*"):
            if pkg_file.is_file() and pkg_file.name not in copied_names:
                dest_file = src_dir / pkg_file.name
                shutil.copy2(pkg_file, dest_file)
                copied_files["packages"].append(str(dest_file))
                copied_names.add(pkg_file.name)
                log_info_safe(
                    logger,
                    safe_format("Copied package file: {name}", name=pkg_file.name),
                    prefix="FILEMGR",
                )

    def copy_vivado_tcl_scripts(self, board: str) -> List[Path]:
        """Copy Vivado TCL scripts from submodule to output directory.
        
        Copies the static TCL scripts (vivado_generate_project.tcl, vivado_build.tcl)
        from lib/voltcyclone-fpga/<board>/ to the output directory.
        
        Args:
            board: Board name (e.g., 'pciescreamer', 'ac701_ft601')
            
        Returns:
            List of copied TCL script paths
            
        Raises:
            FileNotFoundError: If board directory or TCL scripts not found
        """
        copied_scripts = []
        
        try:
            from ..file_management.repo_manager import RepoManager
            
            # Get repository and board paths
            repo_path = RepoManager.ensure_repo()
            board_path = RepoManager.get_board_path(board, repo_root=repo_path)
            
            if not board_path.exists():
                error_msg = safe_format(
                    "Board directory not found: {board_path}",
                    board_path=board_path
                )
                log_error_safe(logger, error_msg, prefix="FILEMGR")
                raise FileNotFoundError(error_msg)
            
            log_info_safe(
                logger,
                safe_format(
                    "Copying Vivado TCL scripts for board: {board}",
                    board=board
                ),
                prefix="FILEMGR",
            )
            
            # Copy all vivado_*.tcl files
            tcl_pattern = "vivado_*.tcl"
            tcl_files = list(board_path.glob(tcl_pattern))
            
            if not tcl_files:
                log_warning_safe(
                    logger,
                    safe_format(
                        "No TCL scripts found matching {pattern} in {board_path}",
                        pattern=tcl_pattern,
                        board_path=board_path
                    ),
                    prefix="FILEMGR",
                )
                return copied_scripts
            
            for tcl_file in tcl_files:
                dest_file = self.output_dir / tcl_file.name
                shutil.copy2(tcl_file, dest_file)
                copied_scripts.append(dest_file)
                
                log_info_safe(
                    logger,
                    safe_format(
                        "  Copied TCL script: {script_name}",
                        script_name=tcl_file.name
                    ),
                    prefix="FILEMGR",
                )
            
            log_info_safe(
                logger,
                safe_format(
                    "Successfully copied {count} TCL scripts",
                    count=len(copied_scripts)
                ),
                prefix="FILEMGR",
            )
            
        except FileNotFoundError as e:
            log_error_safe(
                logger,
                safe_format(
                    "TCL scripts not found for board {board}: {error}",
                    board=board,
                    error=str(e)
                ),
                prefix="FILEMGR",
            )
            raise
        except (OSError, IOError) as e:
            log_error_safe(
                logger,
                safe_format(
                    "Filesystem error copying TCL scripts for {board}: {error}",
                    board=board,
                    error=str(e)
                ),
                prefix="FILEMGR",
            )
            raise
        except Exception as e:
            log_error_safe(
                logger,
                safe_format(
                    "Unexpected error copying TCL scripts for {board}: {error}",
                    board=board,
                    error=str(e)
                ),
                prefix="FILEMGR",
            )
            raise
        
        return copied_scripts

    def copy_ip_files(self, board: str) -> List[Path]:
        """Copy IP files (.coe, .xci) from voltcyclone-fpga submodule to output.
        
        This method copies IP files from the lib/voltcyclone-fpga submodule
        to support Vivado builds that reference these files.
        
        Args:
            board: Board name (e.g., 'pcileech_squirrel')
            
        Returns:
            List of copied IP file paths
            
        Raises:
            FileNotFoundError: If IP files are not found
        """
        try:
            from src.file_management.repo_manager import RepoManager
            
            # Get board path from repo manager
            board_path = RepoManager.get_board_path(board)
            if not board_path:
                raise FileNotFoundError(
                    f"Board '{board}' not found in voltcyclone-fpga"
                )
            
            # Create IP directory in output
            ip_dir = self.output_dir / "ip"
            ip_dir.mkdir(parents=True, exist_ok=True)
            
            # Look for IP files in board's ip directory
            board_ip_dir = board_path / "ip"
            copied_files = []
            
            if board_ip_dir.exists():
                # Copy all IP files (.coe, .xci)
                for pattern in ["*.coe", "*.xci"]:
                    for ip_file in board_ip_dir.glob(pattern):
                        if ip_file.is_file():
                            dest_file = ip_dir / ip_file.name
                            shutil.copy2(ip_file, dest_file)
                            copied_files.append(dest_file)
                            
                            log_info_safe(
                                logger,
                                safe_format(
                                    "  Copied IP file: {ip_name}",
                                    ip_name=ip_file.name
                                ),
                                prefix="FILEMGR",
                            )
                
                log_info_safe(
                    logger,
                    safe_format(
                        "Successfully copied {count} IP files",
                        count=len(copied_files)
                    ),
                    prefix="FILEMGR",
                )
            else:
                log_warning_safe(
                    logger,
                    safe_format(
                        "No IP directory found for board {board} at {path}",
                        board=board,
                        path=board_ip_dir
                    ),
                    prefix="FILEMGR",
                )
            
            return copied_files
            
        except FileNotFoundError as e:
            log_error_safe(
                logger,
                safe_format(
                    "IP files not found for board {board}: {error}",
                    board=board,
                    error=str(e)
                ),
                prefix="FILEMGR",
            )
            raise
        except (OSError, IOError) as e:
            log_error_safe(
                logger,
                safe_format(
                    "Filesystem error copying IP files for {board}: {error}",
                    board=board,
                    error=str(e)
                ),
                prefix="FILEMGR",
            )
            raise
        except Exception as e:
            log_error_safe(
                logger,
                safe_format(
                    "Unexpected error copying IP files for {board}: {error}",
                    board=board,
                    error=str(e)
                ),
                prefix="FILEMGR",
            )
            raise

    def get_source_file_lists(self) -> Dict[str, List[str]]:
        """Get lists of source files in the output directory for TCL generation."""
        file_lists = {
            "systemverilog_files": [],
            "verilog_files": [],
            "constraint_files": [],
            "package_files": [],
            "ip_files": [],
        }

        # Scan source directory
        src_dir = self.output_dir / "src"
        if src_dir.exists():
            # SystemVerilog files
            for sv_file in src_dir.glob("*.sv"):
                file_lists["systemverilog_files"].append(f"src/{sv_file.name}")

            # Verilog files
            for v_file in src_dir.glob("*.v"):
                file_lists["verilog_files"].append(f"src/{v_file.name}")

            # Package files
            for pkg_file in src_dir.glob("*_pkg.sv*"):
                file_lists["package_files"].append(f"src/{pkg_file.name}")

        # Scan constraints directory
        constraints_dir = self.output_dir / "constraints"
        if constraints_dir.exists():
            for xdc_file in constraints_dir.glob("*.xdc"):
                file_lists["constraint_files"].append(f"constraints/{xdc_file.name}")

        # Scan IP directory
        ip_dir = self.output_dir / "ip"
        if ip_dir.exists():
            for ip_file in ip_dir.glob("*"):
                if ip_file.is_file():
                    file_lists["ip_files"].append(f"ip/{ip_file.name}")

        return file_lists

    def print_final_output_info(self, validation_results: Dict[str, Any]):
        """Print detailed information about final output files."""
        log_info_safe(logger, "=" * 80, prefix="FILEMGR")
        log_info_safe(logger, "FINAL BUILD OUTPUT VALIDATION", prefix="FILEMGR")
        log_info_safe(logger, "=" * 80, prefix="FILEMGR")

        status = validation_results["validation_status"]
        self._log_build_status(status)

        build_mode = validation_results["build_mode"]
        log_info_safe(
            logger,
            "BUILD MODE: {build_mode}",
            prefix="FILEMGR",
            build_mode=build_mode.replace("_", " ").title(),
        )

        # Pretty summary table using centralized string utilities
        try:
            build_mode = validation_results["build_mode"]
            status_map = {
                "success_full_build": ("SUCCESS", "Full Vivado Build", "✅"),
                "success_tcl_ready": ("SUCCESS", "TCL Build Script Ready", "✅"),
                "warning_small_bitstream": (
                    "WARNING",
                    "Bitstream unusually small",
                    "⚠️",
                ),
                "warning_incomplete_tcl": (
                    "WARNING",
                    "TCL script may be incomplete",
                    "⚠️",
                ),
                "warning_missing_hex": (
                    "WARNING",
                    "No hex file generation in TCL",
                    "⚠️",
                ),
                "failed_no_bitstream": ("FAILED", "No bitstream generated", "❌"),
                "failed_no_tcl": ("FAILED", "No TCL script generated", "❌"),
            }

            level, desc, icon = status_map.get(
                status, ("ERROR", "Validation failed", "❌")
            )
            status_label = safe_format(
                "{icon} {level} - {desc}", icon=icon, level=level, desc=desc
            )

            build_mode_label = build_mode.replace("_", " ").title()

            tcl_info = validation_results.get("tcl_file_info") or {}
            bit_info = validation_results.get("bitstream_info") or {}
            flash_info = validation_results.get("flash_file_info") or {}
            dbg_info = validation_results.get("debug_file_info") or {}
            reports = validation_results.get("reports_info") or []
            checksums = validation_results.get("checksums") or {}

            def _size_label_bytes(size_bytes: int) -> str:
                try:
                    if size_bytes is None:
                        return "—"
                    if size_bytes < 1024:
                        return safe_format("{n} B", n=size_bytes)
                    if size_bytes < 1024 * 1024:
                        return safe_format("{n:.1f} KB", n=size_bytes / 1024)
                    return safe_format("{n:.2f} MB", n=size_bytes / (1024 * 1024))
                except Exception:
                    return "—"

            # Prepare rows for summary table
            s_val = dbg_info.get("size_bytes") if isinstance(dbg_info, dict) else None
            dbg_size_str = "—"
            if isinstance(s_val, (int, float)):
                try:
                    dbg_size_str = _size_label_bytes(int(s_val))
                except Exception:
                    dbg_size_str = "—"

            rows = [
                ("Status", status_label),
                ("Mode", build_mode_label),
                (
                    "TCL Script",
                    safe_format(
                        "{present}{detail}",
                        present=("present" if tcl_info else "missing"),
                        detail=(
                            safe_format(
                                "  (\u2192 {fname}, {kb:.0f} KB)",
                                fname=tcl_info.get("filename", ""),
                                kb=tcl_info.get("size_kb", 0),
                            )
                            if tcl_info
                            else ""
                        ),
                    ),
                ),
                (
                    "Bitstream",
                    (
                        "—"
                        if not bit_info
                        else safe_format(
                            "{mb:.2f} MB (→ {fname})",
                            mb=bit_info.get("size_mb", 0.0),
                            fname=bit_info.get("filename", ""),
                        )
                    ),
                ),
                (
                    "Flash (.mcs)",
                    (
                        "—"
                        if not flash_info
                        else safe_format(
                            "{mb:.2f} MB (→ {fname})",
                            mb=flash_info.get("size_mb", 0.0),
                            fname=flash_info.get("filename", ""),
                        )
                    ),
                ),
                (
                    "Debug (.ltx)",
                    dbg_size_str
                    + (
                        safe_format("  (→ {f})", f=dbg_info.get("filename", ""))
                        if dbg_info
                        else ""
                    ),
                ),
                ("Reports", str(len(reports))),
                ("Checksums", str(len(checksums))),
            ]

            banner = format_kv_table(rows, title="Build Output Summary")
            for line in banner.splitlines():
                safe_print_format(line, prefix="FILEMGR")
        except KeyError as e:
            log_warning_safe(
                logger,
                "Missing expected key in validation results: {err}",
                prefix="FILEMGR",
                err=str(e),
            )
        except (TypeError, ValueError) as e:
            log_warning_safe(
                logger,
                "Invalid validation result format: {err}",
                prefix="FILEMGR",
                err=str(e),
            )
        except Exception as e:
            log_debug_safe(
                logger,
                "Failed to render summary banner: {err}",
                prefix="FILEMGR",
                err=str(e),
            )

        # TCL file information (always show if present)
        if validation_results.get("tcl_file_info"):
            info = validation_results["tcl_file_info"]
            safe_print_format("\n📜 BUILD SCRIPT:", prefix="FILEMGR")
            safe_print_format(
                "   File: {filename}", prefix="FILEMGR", filename=info["filename"]
            )
            safe_print_format(
                "   Size: {size_kb} KB ({size_bytes:,} bytes)",
                prefix="FILEMGR",
                size_kb=info["size_kb"],
                size_bytes=info["size_bytes"],
            )
            safe_print_format(
                "   SHA256: {sha256}...",
                prefix="FILEMGR",
                sha256=info["sha256"][:16],
            )

            # TCL script validation
            features = []
            if info["has_device_config"]:
                features.append("✅ Device-specific configuration")
            else:
                features.append("❌ Missing device configuration")

            if info["has_synthesis"]:
                features.append("✅ Synthesis commands")
            else:
                features.append("⚠️  No synthesis commands")

            if info["has_implementation"]:
                features.append("✅ Implementation commands")
            else:
                features.append("⚠️  No implementation commands")

            if info.get("has_hex_generation", False):
                features.append("✅ Hex file generation commands")
            else:
                features.append("⚠️  No hex file generation commands")

            safe_print_format("   Features:", prefix="FILEMGR")
            for feature in features:
                safe_print_format(
                    "     {feature}", prefix="FILEMGR", feature=feature
                )

        # Bitstream information (only if Vivado was run)
        if validation_results.get("bitstream_info"):
            info = validation_results["bitstream_info"]
            safe_print_format("\n📁 BITSTREAM FILE:", prefix="FILEMGR")
            safe_print_format(
                "   File: {filename}", prefix="FILEMGR", filename=info["filename"]
            )
            safe_print_format(
                "   Size: {size_mb} MB ({size_bytes:,} bytes)",
                prefix="FILEMGR",
                size_mb=info["size_mb"],
                size_bytes=info["size_bytes"],
            )
            safe_print_format(
                "   SHA256: {sha256}...",
                prefix="FILEMGR",
                sha256=info["sha256"][:16],
            )

            # Validate bitstream size
            if info["size_mb"] < self.min_bitstream_size_mb:
                safe_print_format(
                    "   ⚠️  WARNING: Bitstream is very small (less than {min_size} MB), may be incomplete",
                    prefix="FILEMGR",
                    min_size=self.min_bitstream_size_mb,
                )
            elif info["size_mb"] > self.max_bitstream_size_mb:
                safe_print_format(
                    "   ⚠️  WARNING: Bitstream is very large (greater than {max_size} MB), check for issues",
                    prefix="FILEMGR",
                    max_size=self.max_bitstream_size_mb,
                )
            else:
                safe_print_format(
                    "   ✅ Bitstream size looks normal", prefix="FILEMGR"
                )

        # Flash file information
        if validation_results.get("flash_file_info"):
            info = validation_results["flash_file_info"]
            safe_print_format("\n💾 FLASH FILE:", prefix="FILEMGR")
            safe_print_format(
                "   File: {filename}", prefix="FILEMGR", filename=info["filename"]
            )
            safe_print_format(
                "   Size: {size_mb} MB ({size_bytes:,} bytes)",
                prefix="FILEMGR",
                size_mb=info["size_mb"],
                size_bytes=info["size_bytes"],
            )
            safe_print_format(
                "   SHA256: {sha256}...",
                prefix="FILEMGR",
                sha256=info["sha256"][:16],
            )

        # Debug file information
        if validation_results.get("debug_file_info"):
            info = validation_results["debug_file_info"]
            safe_print_format("\n🔍 DEBUG FILE:", prefix="FILEMGR")
            safe_print_format(
                "   File: {filename}", prefix="FILEMGR", filename=info["filename"]
            )
            safe_print_format(
                "   Size: {size_bytes:,} bytes",
                prefix="FILEMGR",
                size_bytes=info["size_bytes"],
            )

        # Report files
        if validation_results.get("reports_info"):
            safe_print_format("\n📊 ANALYSIS REPORTS:", prefix="FILEMGR")
            for report in validation_results["reports_info"]:
                safe_print_format(
                    "   {filename} ({report_type}) - {size_bytes:,} bytes",
                    prefix="FILEMGR",
                    filename=report["filename"],
                    report_type=report["type"],
                    size_bytes=report["size_bytes"],
                )

        # File checksums
        if validation_results.get("checksums"):
            safe_print_format(
                "\n🔐 FILE CHECKSUMS (for verification):", prefix="FILEMGR"
            )
            for filename, checksum in validation_results["checksums"].items():
                safe_print_format(
                    "   {filename}: {checksum}...",
                    prefix="FILEMGR",
                    filename=filename,
                    checksum=checksum[:16],
                )

        safe_print_format("\n" + "=" * 80, prefix="FILEMGR")
        if build_mode == "tcl_only":
            safe_print_format(
                "TCL build script is ready! Run with Vivado to generate bitstream.",
                prefix="FILEMGR",
            )
        else:
            safe_print_format(
                "Build output files are ready for deployment!", prefix="FILEMGR"
            )
        safe_print_format("=" * 80 + "\n", prefix="FILEMGR")

    def _log_build_status(self, status: str) -> None:
        """Log build status message based on validation status."""
        status_messages = {
            "success_full_build": (
                log_info_safe,
                "BUILD STATUS: SUCCESS (Full Vivado Build)",
            ),
            "success_tcl_ready": (
                log_info_safe,
                "BUILD STATUS: SUCCESS (TCL Build Script Ready)",
            ),
            "warning_small_bitstream": (
                log_warning_safe,
                "BUILD STATUS: WARNING - Bitstream file is unusually small",
            ),
            "warning_incomplete_tcl": (
                log_warning_safe,
                "BUILD STATUS: WARNING - TCL script may be incomplete",
            ),
            "warning_missing_hex": (
                log_warning_safe,
                "BUILD STATUS: WARNING - No hex file generated in TCL script",
            ),
            "failed_no_bitstream": (
                log_error_safe,
                "BUILD STATUS: FAILED - No bitstream file generated",
            ),
            "failed_no_tcl": (
                log_error_safe,
                "BUILD STATUS: FAILED - No TCL build script generated",
            ),
        }

        log_func, message = status_messages.get(
            status, (log_error_safe, "BUILD STATUS: ERROR - Validation failed")
        )
        log_func(logger, message, prefix="FILEMGR")
