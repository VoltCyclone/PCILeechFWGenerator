#!/usr/bin/env python3
"""
Fallback Manager Interface for Template Variables

This module provides a user-friendly interface for managing fallback values
for template variables, with a focus on safety and documentation.
"""

import argparse
import json
import logging
import os
import sys
from pathlib import Path
from typing import Any, Dict, List, Optional, Set

import yaml

# Add parent directory to path for imports
sys.path.insert(0, str(Path(__file__).parent.parent.parent))

from src.device_clone.fallback_manager import FallbackManager
from src.string_utils import log_error_safe, log_info_safe, log_warning_safe

logger = logging.getLogger(__name__)

# Path to fallback configuration YAML file
DEFAULT_FALLBACK_CONFIG = Path("configs/fallbacks.yaml")
# Path to export missing context data
DEFAULT_CONTEXT_EXPORT = Path("output/missing_context.yaml")


class FallbackInterface:
    """User-friendly interface for the FallbackManager."""

    def __init__(
        self,
        fallback_config: Optional[Path] = None,
        export_path: Optional[Path] = None,
        verbose: bool = False,
    ):
        """
        Initialize the fallback interface.

        Args:
            fallback_config: Path to fallback configuration YAML
            export_path: Path to export missing context data
            verbose: Enable verbose logging
        """
        self.fallback_config = fallback_config or DEFAULT_FALLBACK_CONFIG
        self.export_path = export_path or DEFAULT_CONTEXT_EXPORT
        self.verbose = verbose
        self.setup_logger()

        # Initialize fallback manager with config path
        self.fallback_manager = FallbackManager(
            str(self.fallback_config) if self.fallback_config.exists() else None
        )

    def setup_logger(self):
        """Set up logging."""
        handler = logging.StreamHandler()
        formatter = logging.Formatter("%(levelname)s - %(message)s")
        handler.setFormatter(formatter)
        logger.addHandler(handler)
        logger.setLevel(logging.INFO if self.verbose else logging.WARNING)

    def load_fallbacks(self) -> bool:
        """
        Load fallbacks from the configuration file.

        Returns:
            True if successful, False otherwise

        Note: This is kept for backward compatibility. The FallbackManager now loads
        its own configuration in the constructor if a config path is provided.
        """
        if not self.fallback_config.exists():
            logger.warning(f"Fallback configuration not found: {self.fallback_config}")
            return False

        # Use the FallbackManager's built-in config loading functionality
        result = self.fallback_manager.load_from_config(str(self.fallback_config))
        if result:
            logger.info(f"Loaded fallbacks from {self.fallback_config}")
        return result

    def validate_context(self, template_context: Dict[str, Any]) -> bool:
        """
        Validate a template context and export missing data on failure.

        Args:
            template_context: The template context to validate

        Returns:
            True if validation passed, False otherwise
        """
        # First apply fallbacks
        template_context = self.fallback_manager.apply_fallbacks(template_context)

        # Then validate critical variables
        if not self.fallback_manager.validate_critical_variables(template_context):
            logger.error("Validation failed: Missing critical variables")
            self._export_missing_context(template_context)
            return False

        logger.info("Template context validation passed")
        return True

    def validate_templates(self, template_dir: str, pattern: str = "*.j2") -> bool:
        """
        Validate templates to ensure they don't use critical variables directly.

        This is a security check to ensure templates don't directly access
        variables that should only come from hardware (like device IDs).

        Args:
            template_dir: Directory containing template files
            pattern: File pattern to match templates

        Returns:
            True if validation passed, False otherwise
        """
        logger.info(
            f"Validating templates in {template_dir} for critical variable usage"
        )
        result = self.fallback_manager.validate_templates_for_critical_vars(
            template_dir, pattern
        )

        if result:
            logger.info(
                "Template security validation passed: No critical variables used directly"
            )
        else:
            logger.error(
                "Template security validation FAILED: Critical variables used directly"
            )
            logger.error(
                "This is a security risk - hardware-only values must not be in templates"
            )

        return result

    def _export_missing_context(self, template_context: Dict[str, Any]) -> None:
        """
        Export missing context data to a YAML file.

        Args:
            template_context: The template context with missing data
        """
        # Ensure output directory exists
        self.export_path.parent.mkdir(parents=True, exist_ok=True)

        # Sanitize template_context: recursively remove any sensitive/hardware-only
        # fields so the exported YAML never contains device/vendor IDs or BARs.
        def _sanitize_context(ctx: Dict[str, Any]) -> Dict[str, Any]:
            if not isinstance(ctx, dict):
                return ctx
            out: Dict[str, Any] = {}
            for k, v in ctx.items():
                if self._is_sensitive_var(k):
                    log_warning_safe(
                        logger,
                        "Removed sensitive field from export: {key}",
                        prefix="FALLBACK",
                        key=k,
                    )
                    continue
                if isinstance(v, dict):
                    out[k] = _sanitize_context(v)
                else:
                    out[k] = v
            return out

        sanitized_context = _sanitize_context(template_context or {})

        # Prepare export data with descriptions
        export_data = {
            "template_context": sanitized_context,
            "missing_critical_variables": [],
            "sensitive_missing": [],
            # Provide users with a safe fallbacks template they may copy into
            # configs/fallbacks.yaml if they want persistent, non-sensitive fallbacks
            "fallbacks_template": self.fallback_manager.get_exposable_fallbacks(),
            "instructions": """
# PCILeech Template Context
# ------------------------
# This file contains the current template context that was missing critical variables.
# Please fill in the missing values in the sections below and save this file.
# 
# IMPORTANT SECURITY NOTE:
# - Do NOT create fallbacks for device IDs or other critical values
# - These should come directly from the hardware
# 
# HOW TO USE:
# 1. Fill in the missing values in the 'missing_critical_variables' section
# 2. Save this file
# 3. Run the generator again with --context-file=this_file.yaml
""",
        }

        # Use centralized helper from FallbackManager
        _is_sensitive = self.fallback_manager.is_sensitive_var

        # Add missing critical variables (but don't export sensitive/hardware-only fields)
        for var_name in self.fallback_manager._critical_vars:
            if "." in var_name:
                # Handle nested variables
                parts = var_name.split(".")
                current = template_context
                missing = False

                # Check if variable is missing
                for part in parts[:-1]:
                    if part not in current or not isinstance(current[part], dict):
                        missing = True
                        break
                    current = current[part]

                if missing or parts[-1] not in current:
                    if _is_sensitive(var_name):
                        export_data["sensitive_missing"].append(var_name)
                    else:
                        export_data["missing_critical_variables"].append(
                            {
                                "name": var_name,
                                "value": "",
                                "description": self._get_variable_description(var_name),
                            }
                        )
            else:
                # Handle top-level variables
                if var_name not in template_context:
                    if _is_sensitive(var_name):
                        export_data["sensitive_missing"].append(var_name)
                    else:
                        export_data["missing_critical_variables"].append(
                            {
                                "name": var_name,
                                "value": "",
                                "description": self._get_variable_description(var_name),
                            }
                        )

        # Write to file
        with open(self.export_path, "w") as f:
            yaml.dump(export_data, f, default_flow_style=False, sort_keys=False)

        logger.info(f"Exported missing context data to {self.export_path}")
        # Also print a short user-facing message so CLI users immediately see the export
        try:
            print(f"Missing context exported: {self.export_path}")
        except Exception:
            # Avoid breaking CLI on print errors; logging already recorded the path
            pass

    def _get_variable_description(self, var_name: str) -> str:
        """
        Get a user-friendly description for a variable.

        Args:
            var_name: Variable name

        Returns:
            Description string
        """
        descriptions = {
            "device.revision_id": "PCI Revision ID - CRITICAL: MUST COME FROM HARDWARE - DO NOT MODIFY",
            "device.class_code": "PCI Class Code - CRITICAL: MUST COME FROM HARDWARE - DO NOT MODIFY",
            "device.subsys_vendor_id": "PCI Subsystem Vendor ID - CRITICAL: MUST COME FROM HARDWARE - DO NOT MODIFY",
            "device.subsys_device_id": "PCI Subsystem Device ID - CRITICAL: MUST COME FROM HARDWARE - DO NOT MODIFY",
            "board.name": "FPGA board name (e.g. pcileech_100t484_x1)",
            "board.fpga_part": "FPGA part number (e.g. xczu3eg-sbva484-1-e)",
            "board.fpga_family": "FPGA family (e.g. 7series, ultrascale)",
            "board.pcie_ip_type": "PCIe IP type (e.g. pcie7x, ultrascale)",
            "sys_clk_freq_mhz": "System clock frequency in MHz (usually 100)",
        }

        return descriptions.get(var_name, "No description available")

    # local alias to the manager's helper (keeps previous API used in some places)
    def _is_sensitive_var(self, var_name: str) -> bool:
        return self.fallback_manager.is_sensitive_var(var_name)

    def load_context_file(self, context_file: Path) -> Optional[Dict[str, Any]]:
        """
        Load a context file with user-provided values.

        Args:
            context_file: Path to context file

        Returns:
            Template context dictionary or None if failed
        """
        if not context_file.exists():
            logger.error(f"Context file not found: {context_file}")
            return None

        try:
            with open(context_file, "r") as f:
                data = yaml.safe_load(f)

            if not data or "template_context" not in data:
                logger.error("Invalid context file format")
                return None

            # Apply values from missing_critical_variables section
            context = data["template_context"]

            for var_info in data.get("missing_critical_variables", []):
                var_name = var_info.get("name")
                value = var_info.get("value")

                if not var_name or not value:
                    continue

                # Don't allow loading sensitive/hardware-only fields from the YAML
                if self._is_sensitive_var(var_name):
                    log_warning_safe(
                        logger,
                        "Ignored sensitive field in context file: {var_name}",
                        prefix="FALLBACK",
                        var_name=var_name,
                    )
                    continue

                if "." in var_name:
                    # Handle nested variables
                    parts = var_name.split(".")
                    current = context

                    # Navigate to the nested object
                    for i, part in enumerate(parts[:-1]):
                        if part not in current or not isinstance(current[part], dict):
                            current[part] = {}
                        current = current[part]

                    # Set the value
                    current[parts[-1]] = value
                else:
                    # Handle top-level variables
                    context[var_name] = value

            # After applying missing_critical_variables, strip any sensitive keys
            # that may exist in the provided template_context section.
            def _sanitize_inplace(ctx: Dict[str, Any]) -> None:
                if not isinstance(ctx, dict):
                    return
                for key in list(ctx.keys()):
                    if self._is_sensitive_var(key):
                        log_warning_safe(
                            logger,
                            "Removed sensitive field from loaded context: {key}",
                            prefix="FALLBACK",
                            key=key,
                        )
                        del ctx[key]
                        continue
                    val = ctx.get(key)
                    if isinstance(val, dict):
                        _sanitize_inplace(val)

            _sanitize_inplace(context)

            logger.info(f"Loaded context from {context_file}")
            return context

        except Exception as e:
            logger.error(f"Error loading context file: {e}")
            return None


def main():
    """Command-line interface for the fallback manager."""
    parser = argparse.ArgumentParser(description="Fallback Manager Interface")
    parser.add_argument(
        "--config", "-c", type=str, help="Path to fallback configuration file"
    )
    parser.add_argument(
        "--export-path", "-e", type=str, help="Path to export missing context data"
    )
    parser.add_argument(
        "--context-file",
        type=str,
        help="Path to context file with user-provided values",
    )
    parser.add_argument(
        "--validate", type=str, help="Path to template context JSON to validate"
    )
    parser.add_argument(
        "--validate-templates",
        type=str,
        help="Path to template directory to validate for critical variable usage",
    )
    parser.add_argument(
        "--template-pattern",
        type=str,
        default="*.j2",
        help="File pattern for template validation (default: *.j2)",
    )
    parser.add_argument(
        "--verbose", "-v", action="store_true", help="Enable verbose output"
    )
    args = parser.parse_args()

    # Initialize interface
    config_path = Path(args.config) if args.config else None
    export_path = Path(args.export_path) if args.export_path else None
    interface = FallbackInterface(config_path, export_path, args.verbose)

    # Load fallbacks
    interface.load_fallbacks()

    # Load context file
    if args.context_file:
        context = interface.load_context_file(Path(args.context_file))
        if context:
            print("Context loaded successfully")
        else:
            print("Failed to load context")
            return 1

    # Validate template context
    if args.validate:
        try:
            with open(args.validate, "r") as f:
                context = json.load(f)

            if interface.validate_context(context):
                print("Validation passed")
                return 0
            else:
                print("Validation failed")
                return 1
        except Exception as e:
            print(f"Error validating context: {e}")
            return 1

    # Validate templates for critical variable usage
    if args.validate_templates:
        template_dir = Path(args.validate_templates)
        if not template_dir.exists() or not template_dir.is_dir():
            print(f"Error: Template directory not found: {template_dir}")
            return 1

        pattern = args.template_pattern
        if interface.validate_templates(str(template_dir), pattern):
            print(
                "Template security validation passed: No critical variables used directly"
            )
            return 0
        else:
            print(
                "Template security validation FAILED: Critical variables used directly"
            )
            print(
                "This is a security risk - hardware-only values must not be in templates"
            )
            return 1

    return 0


if __name__ == "__main__":
    sys.exit(main())
