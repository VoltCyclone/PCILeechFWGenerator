#!/usr/bin/env python3
"""
PCILeech Firmware Generator - Main Package

This package provides the core functionality for generating PCILeech firmware
with a simplified import structure to reduce complexity.
"""

# Version information
from .__version__ import __version__

# CLI functionality
from .cli import BuildConfig, VFIOBinder, flash_firmware

# Device cloning functionality - flattened imports
from .device_clone import BehaviorProfile  # Device clone suite
from .device_clone import (BehaviorProfiler, ConfigSpaceManager,
                           DeviceConfigManager, DeviceConfiguration,
                           PCILeechGenerationConfig, PCILeechGenerator,
                           get_board_info, validate_board)
# Core exceptions
from .exceptions import (BuildError, ConfigurationError, PCILeechError,
                         PCILeechGenerationError, TemplateError,
                         ValidationError)
# File management
from .file_management import (DonorDumpManager, FileManager, OptionROMManager,
                              RepoManager)
# Import utilities
from .import_utils import safe_import
# PCI capability handling - now at top level
from .pci_capability import (CapabilityProcessor, CapabilityWalker,
                             ConfigSpace, PCICapabilityID, PCIExtCapabilityID)
# Utility functions from specific utility modules
from .string_utils import generate_sv_header_comment  # String utilities
from .string_utils import (generate_hex_header_comment, log_error_safe,
                           log_info_safe, log_warning_safe, safe_format)
# Templating functionality
from .templating import AdvancedSVGenerator, TCLBuilder, TemplateRenderer
# Vivado handling
from .vivado_handling import VivadoErrorReporter

__all__ = [
    # Version
    "__version__",
    # Exceptions
    "PCILeechError",
    "PCILeechGenerationError",
    "BuildError",
    "ValidationError",
    "TemplateError",
    "ConfigurationError",
    # Utilities
    "safe_format",
    "log_info_safe",
    "log_error_safe",
    "log_warning_safe",
    "generate_sv_header_comment",
    "generate_hex_header_comment",
    "safe_import",
    # Device cloning
    "get_board_info",
    "validate_board",
    "DeviceConfiguration",
    "DeviceConfigManager",
    "ConfigSpaceManager",
    "BehaviorProfiler",
    "BehaviorProfile",
    "PCILeechGenerator",
    "PCILeechGenerationConfig",
    # PCI capabilities
    "ConfigSpace",
    "CapabilityWalker",
    "CapabilityProcessor",
    "PCICapabilityID",
    "PCIExtCapabilityID",
    # Templating
    "TemplateRenderer",
    "AdvancedSVGenerator",
    "TCLBuilder",
    # CLI
    "VFIOBinder",
    "BuildConfig",
    "flash_firmware",
    # File management
    "FileManager",
    "DonorDumpManager",
    "OptionROMManager",
    "RepoManager",
    # Vivado
    "VivadoErrorReporter",
]
