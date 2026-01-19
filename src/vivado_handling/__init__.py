#!/usr/bin/env python3
"""
Vivado Application Handling Module

This module contains utilities and tools for working with Xilinx Vivado:
- vivado_utils: Core utilities for finding and running Vivado
- vivado_error_reporter: Enhanced error reporting and monitoring
- vivado_build_with_errors: Build script with comprehensive error handling
- pcileech_build_integration: Integration with pcileech-fpga repository
"""

from .pcileech_build_integration import (
    PCILeechBuildIntegration,
    integrate_pcileech_build,
)
from .vivado_error_reporter import (
    VivadoErrorReporter,
    create_enhanced_vivado_runner,
    run_vivado_with_error_reporting,
)
from .vivado_runner import VivadoRunner, create_vivado_runner
from .vivado_utils import (
    debug_vivado_search,
    find_vivado_installation,
    get_vivado_executable,
    get_vivado_version,
    run_vivado_command,
)

__all__ = [
    # vivado_utils exports
    "find_vivado_installation",
    "get_vivado_executable",
    "get_vivado_version",
    "run_vivado_command",
    "debug_vivado_search",
    # vivado_error_reporter exports
    "VivadoErrorReporter",
    "run_vivado_with_error_reporting",
    "create_enhanced_vivado_runner",
    # vivado_runner exports (new simplified interface)
    "VivadoRunner",
    "create_vivado_runner",
    # pcileech_build_integration exports
    "PCILeechBuildIntegration",
    "integrate_pcileech_build",
]
