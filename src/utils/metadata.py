"""
Centralized metadata generation for PCILeech Firmware Generator.

This module provides a single source of truth for all generation metadata,
ensuring consistency across the codebase.
"""

import os
from datetime import datetime
from typing import Any, Dict, List, Optional

from src.log_config import get_logger
from src.string_utils import log_debug_safe, utc_timestamp
from src.utils.version_resolver import get_package_version

# Internal package version resolution to avoid cyclic imports


def build_generation_metadata(
    device_bdf: str,
    device_signature: Optional[str] = None,
    device_class: Optional[str] = None,
    validation_level: Optional[str] = None,
    vendor_name: Optional[str] = None,
    device_name: Optional[str] = None,
    components_used: Optional[List[str]] = None,
    **kwargs,
) -> Dict[str, Any]:
    """
    Build standardized generation metadata.

    This is the single source of truth for all metadata generation across
    the PCILeech firmware generator. All other metadata generation should
    use this function to ensure consistency.

    Args:
        device_bdf: Device Bus:Device.Function identifier
        device_signature: Device signature string
        device_class: Device class type
        validation_level: Validation level used
        vendor_name: Human-readable vendor name
        device_name: Human-readable device name
        components_used: List of components used in generation
        **kwargs: Additional metadata fields

    Returns:
        Dictionary containing standardized generation metadata
    """
    # Get the canonical version
    generator_version = get_package_version()

    # Default components if not specified
    if components_used is None:
        components_used = [
            "BehaviorProfiler",
            "ConfigSpaceManager",
            "MSIXCapability",
            "PCILeechContextBuilder",
            "AdvancedSVGenerator",
            "TemplateRenderer",
        ]

    # Build base metadata
    # Allow test patching of datetime.now() in this module while still supporting
    # centralized utc override via BUILD_TIMESTAMP. If BUILD_TIMESTAMP is set we
    # trust it exactly (tests can also use it). Otherwise prefer the local
    # datetime.now().isoformat() so existing patches like
    # patch("src.utils.metadata.datetime") continue to work.
    ts_override = os.getenv("BUILD_TIMESTAMP")
    if ts_override:
        gen_ts = ts_override
    else:
        try:
            gen_ts = datetime.now().isoformat()
        except Exception:
            # Fallback to centralized helper
            gen_ts = utc_timestamp()

    metadata = {
        "generated_at": gen_ts,
        "generator_version": generator_version,
        "device_bdf": device_bdf,
        "components_used": components_used,
    }

    # Add optional fields if provided
    if device_signature:
        metadata["device_signature"] = device_signature
    if device_class:
        metadata["device_class"] = device_class
    if validation_level:
        metadata["validation_level"] = validation_level
    if vendor_name:
        metadata["vendor_name"] = vendor_name
    if device_name:
        metadata["device_name"] = device_name

    # Add any additional metadata from kwargs
    metadata.update(kwargs)

    return metadata


def build_config_metadata(
    device_bdf: str,
    enable_behavior_profiling: bool = False,
    enable_manufacturing_variance: bool = False,
    enable_advanced_features: bool = False,
    strict_validation: bool = True,
    **config_kwargs,
) -> Dict[str, Any]:
    """
    Build configuration-specific metadata.

    Args:
        device_bdf: Device BDF
        enable_behavior_profiling: Whether behavior profiling is enabled
        enable_manufacturing_variance: Whether manufacturing variance is enabled
        enable_advanced_features: Whether advanced features are enabled
        strict_validation: Whether strict validation is enabled
        **config_kwargs: Additional config fields

    Returns:
        Dictionary containing configuration metadata
    """
    config_metadata = {
        "device_bdf": device_bdf,
        "enable_behavior_profiling": enable_behavior_profiling,
        "enable_manufacturing_variance": enable_manufacturing_variance,
        "enable_advanced_features": enable_advanced_features,
        "strict_validation": strict_validation,
    }

    # Add additional config from kwargs
    config_metadata.update(config_kwargs)

    return build_generation_metadata(
        device_bdf=device_bdf,
        config=config_metadata,
    )
