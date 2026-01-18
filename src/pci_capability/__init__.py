#!/usr/bin/env python3
"""
PCI Capability Analysis and Pruning - Modular Implementation

This package provides functionality to analyze and prune PCI capabilities
in the configuration space of a donor device. It supports both standard
and extended capabilities, and implements specific pruning rules for
capabilities that cannot be faithfully emulated.

The modular design provides:
- Efficient bytearray-based configuration space handling
- Unified capability walking for both standard and extended capabilities
- Clean separation of constants, types, and core functionality
- Backward compatibility with existing API

Modules:
    constants: PCI register offsets, bit masks, and capability-specific constants
    types: Type definitions and enums (PCICapabilityID, PCIExtCapabilityID, etc.)
    core: Core abstractions (ConfigSpace, CapabilityWalker)
    utils: Utility functions for capability analysis
    rules: Data-driven rule engine for capability categorization
    patches: Binary patch engine for efficient configuration space modifications
    msix: MSI-X capability handler with specialized operations
    processor: Main capability processor orchestrating all operations
    compat: Backward compatibility layer for existing code
"""

from .compat import (  # Backward compatibility functions; Enhanced Phase 2 compatibility functions
    categorize_capabilities,
    categorize_capabilities_with_rules,
    determine_pruning_actions,
    find_cap,
    find_ext_cap,
    get_all_capabilities,
    get_all_ext_capabilities,
    get_capability_patches,
    get_capability_patches_enhanced,
    process_capabilities_enhanced,
    prune_capabilities,
    prune_capabilities_by_rules,
    setup_logging,
)

# Import key classes and functions for easy access
from .core import CapabilityWalker, ConfigSpace
from .msix import MSIXCapabilityHandler
from .patches import BinaryPatch, PatchEngine
from .processor import CapabilityProcessor

# Phase 2 - Core functionality imports
from .rules import CapabilityRule, RuleEngine
from .types import (
    CapabilityInfo,
    CapabilityType,
    EmulationCategory,
    PatchInfo,
    PCICapabilityID,
    PCIExtCapabilityID,
    PruningAction,
)

__all__ = [
    # Core classes
    "ConfigSpace",
    "CapabilityWalker",
    # Types and enums
    "PCICapabilityID",
    "PCIExtCapabilityID",
    "EmulationCategory",
    "PruningAction",
    "PatchInfo",
    "CapabilityInfo",
    "CapabilityType",
    # Phase 2 - Core functionality classes
    "CapabilityRule",
    "RuleEngine",
    "BinaryPatch",
    "PatchEngine",
    "MSIXCapabilityHandler",
    "CapabilityProcessor",
    # Backward compatibility functions
    "find_cap",
    "find_ext_cap",
    "get_all_capabilities",
    "get_all_ext_capabilities",
    "categorize_capabilities",
    "determine_pruning_actions",
    "prune_capabilities",
    "get_capability_patches",
    "prune_capabilities_by_rules",
    "setup_logging",
    # Enhanced Phase 2 compatibility functions
    "process_capabilities_enhanced",
    "categorize_capabilities_with_rules",
    "get_capability_patches_enhanced",
]
