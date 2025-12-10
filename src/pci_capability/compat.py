#!/usr/bin/env python3
"""
PCI Capability Backward Compatibility Layer

This module provides backward compatibility functions that maintain the same
function signatures as the original pci_capability.py module while using
the new modular implementation internally.
"""

import logging

from typing import Dict, List, Optional

from ..string_utils import (log_debug_safe, log_error_safe, log_info_safe,
                            log_warning_safe, safe_format)

from .core import CapabilityWalker, ConfigSpace

from .processor import CapabilityProcessor

from .rules import RuleEngine

from .types import CapabilityType, EmulationCategory, PatchInfo, PruningAction

logger = logging.getLogger(__name__)


def find_cap(cfg: str, cap_id: int) -> Optional[int]:
    """
    Find a standard capability in the PCI configuration space.

    Args:
        cfg: Configuration space as a hex string
        cap_id: Capability ID to find

    Returns:
        Offset of the capability in the configuration space, or None if not found
    """
    try:
        config_space = ConfigSpace(cfg)
        walker = CapabilityWalker(config_space)

        cap_info = walker.find_capability(cap_id, CapabilityType.STANDARD)
        return cap_info.offset if cap_info else None

    except (ValueError, IndexError) as e:
        # Expected when capability not present or config space incomplete
        log_debug_safe(
            logger,
            safe_format(
                "Capability 0x{cap_id:02x} not found: {e}",
                cap_id=cap_id,
                e=e,
            ),
            prefix="PCI_CAP",
        )
        return None


def find_ext_cap(cfg: str, cap_id: int) -> Optional[int]:
    """
    Find an extended capability in the PCI Express configuration space.

    Args:
        cfg: Configuration space as a hex string
        cap_id: Extended Capability ID to find

    Returns:
        Offset of the extended capability in the configuration space, or None if not found
    """
    try:
        config_space = ConfigSpace(cfg)
        walker = CapabilityWalker(config_space)

        cap_info = walker.find_capability(cap_id, CapabilityType.EXTENDED)
        return cap_info.offset if cap_info else None

    except (ValueError, IndexError) as e:
        # Expected when extended capability not present or config space incomplete
        log_debug_safe(
            logger,
            safe_format(
                "Extended capability 0x{cap_id:04x} not found: {e}",
                cap_id=cap_id,
                e=e,
            ),
            prefix="PCI_CAP",
        )
        return None


def get_all_capabilities(cfg: str) -> Dict[int, Dict]:
    """
    Get all standard capabilities in the PCI configuration space.

    Args:
        cfg: Configuration space as a hex string

    Returns:
        Dictionary mapping capability offsets to capability information
    """
    capabilities = {}

    try:
        config_space = ConfigSpace(cfg)
        walker = CapabilityWalker(config_space)

        for cap_info in walker.walk_standard_capabilities():
            capabilities[cap_info.offset] = {
                "offset": cap_info.offset,
                "id": cap_info.cap_id,
                "next_ptr": cap_info.next_ptr,
                "type": "standard",
                "name": cap_info.name,
            }

    except (ValueError, IndexError) as e:
        log_error_safe(
            logger,
            safe_format(
                "Error getting standard capabilities: {e}",
                e=e,
            ),            
            prefix="PCI_CAP",
        )

    return capabilities


def get_all_ext_capabilities(cfg: str) -> Dict[int, Dict]:
    """
    Get all extended capabilities in the PCI Express configuration space.

    Args:
        cfg: Configuration space as a hex string

    Returns:
        Dictionary mapping capability offsets to capability information
    """
    ext_capabilities = {}

    try:
        config_space = ConfigSpace(cfg)
        walker = CapabilityWalker(config_space)

        for cap_info in walker.walk_extended_capabilities():
            ext_capabilities[cap_info.offset] = {
                "offset": cap_info.offset,
                "id": cap_info.cap_id,
                "version": cap_info.version,
                "next_ptr": cap_info.next_ptr,
                "type": "extended",
                "name": cap_info.name,
            }

    except (ValueError, IndexError) as e:
        log_error_safe(
            logger,
            safe_format(
                "Error getting extended capabilities: {e}",
                e=e,
            ),
            prefix="PCI_CAP",
        )

    return ext_capabilities


def categorize_capabilities(
    capabilities: Dict[int, Dict],
) -> Dict[int, "EmulationCategory"]:
    """
    Categorize capabilities based on emulation feasibility (compatibility version).

    Returns:
        Dictionary mapping capability offsets to emulation categories
    """
    from .types import CapabilityInfo, CapabilityType
    from .utils import categorize_capabilities as utils_categorize_capabilities

    # Convert old format to new CapabilityInfo format
    cap_infos = {}
    for offset, cap_dict in capabilities.items():
        cap_type = (
            CapabilityType.STANDARD
            if cap_dict["type"] == "standard"
            else CapabilityType.EXTENDED
        )
        version = cap_dict.get("version", 0)

        cap_info = CapabilityInfo(
            offset=offset,
            cap_id=cap_dict["id"],
            cap_type=cap_type,
            next_ptr=cap_dict["next_ptr"],
            name=cap_dict["name"],
            version=version,
        )
        cap_infos[offset] = cap_info

    return utils_categorize_capabilities(cap_infos)


def determine_pruning_actions(
    capabilities: Dict[int, Dict], categories: Dict[int, EmulationCategory]
) -> Dict[int, PruningAction]:
    """
    Determine pruning actions for each capability based on its category (compatibility version).

    Args:
        capabilities: Dictionary of capabilities
        categories: Dictionary mapping capability offsets to emulation categories

    Returns:
        Dictionary mapping capability offsets to pruning actions
    """
    from .types import CapabilityInfo, CapabilityType

    # Convert old format to new CapabilityInfo format
    cap_infos = {}
    for offset, cap_dict in capabilities.items():
        cap_type = (
            CapabilityType.STANDARD
            if cap_dict["type"] == "standard"
            else CapabilityType.EXTENDED
        )
        version = cap_dict.get("version", 0)

        cap_info = CapabilityInfo(
            offset=offset,
            cap_id=cap_dict["id"],
            cap_type=cap_type,
            next_ptr=cap_dict["next_ptr"],
            name=cap_dict["name"],
            version=version,
        )
        cap_infos[offset] = cap_info

    from .utils import \
        determine_pruning_actions as utils_determine_pruning_actions

    return utils_determine_pruning_actions(cap_infos, categories)


def prune_capabilities(cfg: str, actions: Dict[int, PruningAction]) -> str:
    """
    Prune capabilities in the configuration space based on the specified actions.

    Args:
        cfg: Configuration space as a hex string
        actions: Dictionary mapping capability offsets to pruning actions

    Returns:
        Modified configuration space as a hex string
    """
    try:
        config_space = ConfigSpace(cfg)

        # Apply the pruning operations using the new implementation
        from ._pruning import apply_pruning_actions

        apply_pruning_actions(config_space, actions)

        return config_space.to_hex()

    except (ValueError, IndexError) as e:
        log_error_safe(
            logger,
            safe_format(
                "Error pruning capabilities: {e}",
                e=e,
            ),
            prefix="PCI_CAP",
        )
        return cfg


def get_capability_patches(
    cfg: str, actions: Dict[int, PruningAction]
) -> List[PatchInfo]:
    """
    Get a list of patches that would be applied for capability modifications.

    Args:
        cfg: Configuration space as a hex string
        actions: Dictionary mapping capability offsets to pruning actions

    Returns:
        List of PatchInfo objects describing the changes that would be made
    """
    try:
        config_space = ConfigSpace(cfg)

        # Generate patches using the new implementation
        from ._pruning import generate_capability_patches

        return generate_capability_patches(config_space, actions)

    except (ValueError, IndexError) as e:
        log_error_safe(
            logger,
            safe_format(
                "Error generating capability patches: {e}",
                e=e,
            ),
            prefix="PCI_CAP",
        )
        return []


def prune_capabilities_by_rules(cfg: str) -> str:
    """
    Prune capabilities in the configuration space based on predefined rules.

    Args:
        cfg: Configuration space as a hex string

    Returns:
        Modified configuration space as a hex string
    """
    try:
        # Get all capabilities using new implementation
        config_space = ConfigSpace(cfg)
        walker = CapabilityWalker(config_space)
        all_caps = walker.get_all_capabilities()

        # Categorize and determine actions
        from .utils import categorize_capabilities as categorize_caps_new
        from .utils import determine_pruning_actions as determine_actions_new

        categories = categorize_caps_new(all_caps)
        actions = determine_actions_new(all_caps, categories)

        # Apply pruning
        from ._pruning import apply_pruning_actions

        apply_pruning_actions(config_space, actions)

        return config_space.to_hex()

    except (ValueError, IndexError) as e:
        log_error_safe(
            logger,
            safe_format(
                "Error pruning capabilities by rules: {e}",
                e=e,
            ),
            prefix="PCI_CAP",
        )
        return cfg


# Note: categorize_capabilities and determine_pruning_actions are defined above
# No aliases needed since the functions are already named correctly
def process_capabilities_enhanced(
    cfg: str,
    actions: Optional[List[PruningAction]] = None,
    rule_config_file: Optional[str] = None,
    device_context: Optional[Dict] = None,
) -> Dict:
    """
    Enhanced capability processing using Phase 2 functionality.

    This function provides access to the new Phase 2 capability processing
    while maintaining a simple interface for backward compatibility.

    Args:
        cfg: Configuration space as a hex string
        actions: List of pruning actions to apply (defaults to [REMOVE, MODIFY])
        rule_config_file: Optional path to rule configuration file
        device_context: Optional device context for rule evaluation

    Returns:
        Dictionary with processing results including:
        - capabilities_found: Number of capabilities discovered
        - categories: Dictionary of category counts
        - patches_created: Number of patches created
        - patches_applied: Number of patches applied
        - modified_config: Modified configuration space as hex string
        - errors: List of error messages
        - warnings: List of warning messages
    """
    try:
        # Initialize components
        config_space = ConfigSpace(cfg)
        rule_engine = RuleEngine()

        # Load custom rules if provided
        if rule_config_file:
            try:
                rule_engine.load_rules_from_file(rule_config_file)
                log_info_safe(
                    logger,
                    "Loaded custom rules from {rule_config_file}",
                    prefix="PCI_CAP",
                    rule_config_file=rule_config_file,
                )
            except Exception as e:
                log_warning_safe(
                    logger,
                    "Failed to load custom rules: {e}",
                    prefix="PCI_CAP",
                    e=e,
                )

        # Initialize processor
        processor = CapabilityProcessor(config_space, rule_engine)

        # Default actions if not provided
        if actions is None:
            actions = [PruningAction.REMOVE, PruningAction.MODIFY]

        # Process capabilities
        results = processor.process_capabilities(actions, device_context)

        # Add modified configuration space to results
        results["modified_config"] = config_space.to_hex()

        # Add capability summary
        summary = processor.get_capability_summary()
        results["capability_summary"] = summary

        return results

    except Exception as e:
        log_error_safe(
            logger,
            safe_format(
                "Enhanced capability processing failed: {e}",
                e=e,
            ),
            prefix="PCI_CAP",
        )
        return {
            "capabilities_found": 0,
            "categories": {},
            "patches_created": 0,
            "patches_applied": 0,
            "modified_config": cfg,  # Return original on error
            "errors": [str(e)],
            "warnings": [],
            "capability_summary": {},
        }


def categorize_capabilities_with_rules(
    cfg: str,
    rule_config_file: Optional[str] = None,
    device_context: Optional[Dict] = None,
) -> Dict[int, EmulationCategory]:
    """
    Categorize capabilities using the new rule engine.

    Args:
        cfg: Configuration space as a hex string
        rule_config_file: Optional path to rule configuration file
        device_context: Optional device context for rule evaluation

    Returns:
        Dictionary mapping capability offsets to EmulationCategory
    """
    try:
        config_space = ConfigSpace(cfg)
        rule_engine = RuleEngine()

        # Load custom rules if provided
        if rule_config_file:
            rule_engine.load_rules_from_file(rule_config_file)

        processor = CapabilityProcessor(config_space, rule_engine)
        return processor.categorize_all_capabilities(device_context)

    except Exception as e:
        log_error_safe(
            logger,
            safe_format(
                "Rule-based categorization failed: {e}",
                e=e,
            ),
            prefix="PCI_CAP",
        )
        return {}


def get_capability_patches_enhanced(
    cfg: str,
    actions: Optional[List[PruningAction]] = None,
    rule_config_file: Optional[str] = None,
    device_context: Optional[Dict] = None,
) -> List[PatchInfo]:
    """
    Get capability patches using the new patch engine.

    Args:
        cfg: Configuration space as a hex string
        actions: List of pruning actions to apply
        rule_config_file: Optional path to rule configuration file
        device_context: Optional device context for rule evaluation

    Returns:
        List of PatchInfo objects describing the patches
    """
    try:
        config_space = ConfigSpace(cfg)
        rule_engine = RuleEngine()

        # Load custom rules if provided
        if rule_config_file:
            rule_engine.load_rules_from_file(rule_config_file)

        processor = CapabilityProcessor(config_space, rule_engine)

        # Default actions if not provided
        if actions is None:
            actions = [PruningAction.REMOVE, PruningAction.MODIFY]

        # Process capabilities without applying patches
        processor.process_capabilities(
            actions,
            device_context,
            validate_patches=False
        )

        # Return patch information
        return processor.get_patch_info_list()

    except Exception as e:
        log_error_safe(
            logger,
            safe_format(
                "Enhanced patch generation failed: {e}",
                e=e,
            ),            
            prefix="PCI_CAP",
        )
        return []


def setup_logging(level: int = logging.INFO, log_file: Optional[str] = "generate.log") -> None:
    """
    Setup logging configuration (backward compatibility wrapper).

    This function provides backward compatibility by delegating to the
    centralized logging setup in log_config.py.

    Args:
        level: Logging level (default: INFO)
        log_file: Optional log file path (default: generate.log)
    """
    from ..log_config import setup_logging as main_setup_logging
    main_setup_logging(level=level, log_file=log_file)
