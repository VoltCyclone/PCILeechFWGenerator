"""
Centralized metadata generation for PCILeech Firmware Generator.

This module provides a single source of truth for all generation metadata,
ensuring consistency across the codebase.
"""

from datetime import datetime
import os

from src.string_utils import utc_timestamp
from typing import Any, Dict, List, Optional


# Internal package version resolution to avoid cyclic imports


def _get_package_version() -> str:
    """
    Get the package version dynamically.

    Tries multiple methods to get the version:
    1. From __version__.py in the src directory
    2. From setuptools_scm if available
    3. From importlib.metadata
    4. Falls back to a default version

    Returns:
        str: The package version
    """
    DEFAULT_VERSION = "0.5.0"
    import logging
    from pathlib import Path

    # Try __version__.py first
    try:
        src_dir = Path(__file__).parent.parent
        version_file = src_dir / "__version__.py"

        if version_file.exists():
            version_dict: Dict[str, str] = {}
            with open(version_file, "r") as f:
                exec(f.read(), version_dict)
            if "__version__" in version_dict:
                return version_dict["__version__"]
    except Exception as e:
        logging.debug(f"Error reading __version__.py: {e}")

    # Try setuptools_scm
    try:
        from setuptools_scm import get_version  # type: ignore

        return get_version(root="../..")
    except Exception as e:
        logging.debug(f"Error getting version from setuptools_scm: {e}")

    # Try importlib.metadata (Python 3.8+)
    try:
        from importlib.metadata import version

        return version("PCILeechFWGenerator")
    except Exception as e:
        logging.debug(f"Error getting version from importlib.metadata: {e}")

    return DEFAULT_VERSION


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
    generator_version = _get_package_version()

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
