#!/usr/bin/env python3
"""
Template Validation System

Validates that required template files and IP cores exist before build.
"""

import json

from pathlib import Path
from typing import Dict, List, Optional, Set, Tuple

from src.string_utils import (
    log_info_safe,
    log_warning_safe,
    log_error_safe,
    safe_format
)


class TemplateValidationError(Exception):
    """Raised when template validation fails."""
    pass


class TemplateValidator:
    """Validates template files and IP cores."""

    # Required IP cores for PCILeech builds
    REQUIRED_IP_CORES = {
        "fifo_64_64_clk2_comrx.xci": "Communication RX FIFO",
        "fifo_64_64_clk1_fifocmd.xci": "Command FIFO",
        "fifo_256_32_clk2_comtx.xci": "Communication TX FIFO",
        "pcie_7x_0.xci": "PCIe IP core",
        "bram_pcie_cfgspace.xci": "Configuration space BRAM",
        "bram_bar_zero4k.xci": "BAR zeroing BRAM",
    }

    # Required SystemVerilog modules
    REQUIRED_MODULES = {
        "pcileech_header.svh": "PCILeech header definitions",
        "pcileech_pcie_a7.sv": "PCIe interface",
        "pcileech_pcie_cfg_a7.sv": "PCIe configuration",
        "pcileech_pcie_tlp_a7.sv": "TLP processing",
        "pcileech_com.sv": "Communication module",
        "pcileech_fifo.sv": "FIFO interfaces",
        "pcileech_mux.sv": "Multiplexer",
        "pcileech_ft601.sv": "FT601 interface",
    }

    # Required constraint files
    REQUIRED_CONSTRAINTS = {
        ".xdc": "Xilinx Design Constraints",
    }

    def __init__(self, repo_root: Path, logger=None):
        """Initialize validator."""
        self.repo_root = Path(repo_root)
        self.logger = logger
        self._validation_cache: Dict[str, bool] = {}

    def validate_board_template(
        self,
        board_name: str,
        board_path: Optional[Path] = None
    ) -> Tuple[bool, List[str]]:
        """
        Validate all required files for a board template.

        Args:
            board_name: Name of the board
            board_path: Optional path to board directory

        Returns:
            Tuple of (is_valid, list_of_warnings)
        """
        warnings = []

        if board_path is None:
            board_path = self._find_board_path(board_name)

        if not board_path or not board_path.exists():
            warnings.append(f"Board directory not found: {board_name}")
            return False, warnings

        # Validate IP cores
        ip_valid, ip_warnings = self._validate_ip_cores(board_path)
        warnings.extend(ip_warnings)

        # Validate SystemVerilog modules
        sv_valid, sv_warnings = self._validate_systemverilog_modules(board_path)
        warnings.extend(sv_warnings)

        # Validate constraint files
        constraint_ok, constraint_warnings = self._validate_constraints(board_path)
        warnings.extend(constraint_warnings)

        # Validate build scripts
        script_valid, script_warnings = self._validate_build_scripts(board_path)
        warnings.extend(script_warnings)

        is_valid = all([ip_valid, sv_valid, constraint_ok, script_valid])

        if is_valid:
            log_info_safe(
                self.logger,
                safe_format(
                    "Board template validation passed for {board}",
                    board=board_name
                ),
                prefix="TEMPLATE"
            )
        else:
            log_warning_safe(
                self.logger,
                safe_format(
                    "Board template validation failed for {board}",
                    board=board_name
                ),
                prefix="TEMPLATE"
            )

        return is_valid, warnings

    def _validate_ip_cores(self, board_path: Path) -> Tuple[bool, List[str]]:
        """Validate IP cores exist."""
        warnings = []
        missing = []

        # Look for IP cores in common locations
        search_paths = [
            board_path / "ip",
            board_path / "ip_repo",
            self.repo_root / "ip",
            self.repo_root / "common" / "ip",
        ]

        found_cores: Set[str] = set()

        for search_path in search_paths:
            if not search_path.exists():
                continue

            for xci_file in search_path.glob("*.xci"):
                found_cores.add(xci_file.name)

        # Check for required cores
        for core_name, description in self.REQUIRED_IP_CORES.items():
            if core_name not in found_cores:
                missing.append(f"{core_name} ({description})")

        if missing:
            warnings.append(f"Missing IP cores: {', '.join(missing)}")
            log_warning_safe(
                self.logger,
                safe_format(
                    "Missing IP cores: {cores}",
                    cores=", ".join(missing)
                ),
                prefix="TEMPLATE"
            )
        else:
            log_info_safe(
                self.logger,
                safe_format(
                    "Found {count} required IP cores",
                    count=len(self.REQUIRED_IP_CORES)
                ),
                prefix="TEMPLATE"
            )

        return len(missing) == 0, warnings

    def _validate_systemverilog_modules(
        self, board_path: Path
    ) -> Tuple[bool, List[str]]:
        """Validate SystemVerilog modules exist."""
        warnings = []
        missing = []

        # Look for modules in common locations
        search_paths = [
            board_path / "src",
            board_path / "hdl",
            self.repo_root / "src",
            self.repo_root / "common" / "src",
        ]

        found_modules: Set[str] = set()

        for search_path in search_paths:
            if not search_path.exists():
                continue

            for sv_file in search_path.glob("*.sv"):
                found_modules.add(sv_file.name)
            for svh_file in search_path.glob("*.svh"):
                found_modules.add(svh_file.name)

        # Check for required modules
        for module_name, description in self.REQUIRED_MODULES.items():
            if module_name not in found_modules:
                missing.append(f"{module_name} ({description})")

        if missing:
            warnings.append(f"Missing SystemVerilog modules: {', '.join(missing)}")
            log_warning_safe(
                self.logger,
                safe_format(
                    "Missing SystemVerilog modules: {modules}",
                    modules=", ".join(missing)
                ),
                prefix="TEMPLATE"
            )
        else:
            log_info_safe(
                self.logger,
                safe_format(
                    "Found {count} required SystemVerilog modules",
                    count=len(self.REQUIRED_MODULES)
                ),
                prefix="TEMPLATE"
            )

        return len(missing) == 0, warnings

    def _validate_constraints(self, board_path: Path) -> Tuple[bool, List[str]]:
        """Validate constraint files exist."""
        warnings = []
        missing = []

        # Look for constraint files
        search_paths = [
            board_path / "constraints",
            board_path / "constrs",
            board_path / "xdc",
        ]

        found_constraints: Set[str] = set()

        for search_path in search_paths:
            if not search_path.exists():
                continue

            for xdc_file in search_path.glob("*.xdc"):
                found_constraints.add(xdc_file.name)

        # At least one constraint file should exist
        if not found_constraints:
            warnings.append("No constraint files (.xdc) found")
            log_warning_safe(
                self.logger,
                "No constraint files found for board",
                prefix="TEMPLATE"
            )
        else:
            log_info_safe(
                self.logger,
                safe_format(
                    "Found {count} constraint files",
                    count=len(found_constraints)
                ),
                prefix="TEMPLATE"
            )

        return len(found_constraints) > 0, warnings

    def _validate_build_scripts(self, board_path: Path) -> Tuple[bool, List[str]]:
        """Validate build scripts exist."""
        warnings = []

        # Look for build scripts
        search_paths = [
            board_path / "scripts",
            board_path / "tcl",
            board_path,
        ]

        found_scripts = []

        for search_path in search_paths:
            if not search_path.exists():
                continue

            for tcl_file in search_path.glob("*.tcl"):
                found_scripts.append(tcl_file.name)

        # At least one build script should exist
        if not found_scripts:
            warnings.append("No build scripts (.tcl) found")
            log_warning_safe(
                self.logger,
                "No build scripts found for board",
                prefix="TEMPLATE"
            )
        else:
            log_info_safe(
                self.logger,
                safe_format(
                    "Found {count} build scripts",
                    count=len(found_scripts)
                ),
                prefix="TEMPLATE"
            )

        return len(found_scripts) > 0, warnings

    def _find_board_path(self, board_name: str) -> Optional[Path]:
        """Find board directory in repository."""
        # Common board directory patterns
        search_patterns = [
            self.repo_root / board_name,
            self.repo_root / "boards" / board_name,
            self.repo_root / "Boards" / board_name,
            self.repo_root / "hardware" / board_name,
        ]

        for pattern in search_patterns:
            if pattern.exists() and pattern.is_dir():
                return pattern

        return None

    def validate_ip_core(self, core_name: str) -> bool:
        """
        Validate a specific IP core exists.

        Args:
            core_name: Name of the IP core (.xci file)

        Returns:
            True if core exists
        """
        cache_key = f"ip_core:{core_name}"
        if cache_key in self._validation_cache:
            return self._validation_cache[cache_key]

        # Search for the core
        search_paths = [
            self.repo_root / "ip",
            self.repo_root / "common" / "ip",
            self.repo_root / "ip_repo",
        ]

        found = False
        for search_path in search_paths:
            if (search_path / core_name).exists():
                found = True
                break

        self._validation_cache[cache_key] = found
        return found

    def generate_validation_report(
        self,
        board_name: str,
        output_path: Optional[Path] = None
    ) -> Dict:
        """
        Generate comprehensive validation report.

        Args:
            board_name: Name of the board
            output_path: Optional path to save report

        Returns:
            Validation report dictionary
        """
        is_valid, warnings = self.validate_board_template(board_name)

        report = {
            "board_name": board_name,
            "timestamp": __import__('time').time(),
            "is_valid": is_valid,
            "warnings": warnings,
            "validation_summary": {
                "ip_cores_valid": self._validate_ip_cores(
                    self._find_board_path(board_name) or Path(".")
                )[0],
                "systemverilog_valid": self._validate_systemverilog_modules(
                    self._find_board_path(board_name) or Path(".")
                )[0],
                "constraints_valid": self._validate_constraints(
                    self._find_board_path(board_name) or Path(".")
                )[0],
                "scripts_valid": self._validate_build_scripts(
                    self._find_board_path(board_name) or Path(".")
                )[0],
            },
            "required_files": {
                "ip_cores": list(self.REQUIRED_IP_CORES.keys()),
                "systemverilog_modules": list(self.REQUIRED_MODULES.keys()),
            }
        }

        if output_path:
            with open(output_path, 'w') as f:
                json.dump(report, f, indent=2)

        return report


def create_template_validator(repo_root: Path, logger=None) -> TemplateValidator:
    """Create a template validator instance."""
    return TemplateValidator(repo_root, logger)
