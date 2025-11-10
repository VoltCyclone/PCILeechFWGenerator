#!/usr/bin/env python3
"""
Template Context Validator

This module ensures all template variables are properly defined with appropriate defaults,
preventing undefined variable errors in Jinja2 templates.
"""

import logging
import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Dict, List, Optional, Set

from src.string_utils import (
    log_debug_safe,
    log_error_safe,
    log_info_safe,
    log_warning_safe,
    safe_format,
)

logger = logging.getLogger(__name__)


@dataclass
class TemplateVariableRequirements:
    """Defines required and optional variables for a template."""

    template_name: str
    required_vars: Set[str] = field(default_factory=set)
    optional_vars: Set[str] = field(default_factory=set)
    default_values: Dict[str, Any] = field(default_factory=dict)

class TemplateContextValidator:
    """
    Validates and ensures all template variables are properly defined.

    This class addresses the issue where conditional template includes suggest
    that some template variables may be undefined. It ensures all required
    template variables are always defined in the template context.
    """

    prefix = "TMPL_VALID"

    # Define template variable requirements for each template type
    TEMPLATE_REQUIREMENTS = {
        # SystemVerilog templates
        "sv/*.sv.j2": {
            "required_vars": {
                "device_config",
                "board_config",
            },
            "optional_vars": {
                "supports_msix",
                "supports_msi",
                "variance_model",
                "power_config",
                "error_config",
                "perf_config",
                "enable_clock_crossing",
                "enable_custom_config",
                "device_specific_config",
                "enable_performance_counters",
                "enable_error_detection",
                "power_management",
                "error_handling",
                "performance_counters",
                "timing_config",
                "behavior_profile",
            },
            "default_values": {
                "supports_msix": False,
                "supports_msi": False,
                "enable_clock_crossing": False,
                "enable_custom_config": False,
                "device_specific_config": {},
                "enable_performance_counters": False,
                "enable_error_detection": False,
                "DUAL_PORT": False,
                "RESET_CLEAR": True,
                "USE_BYTE_ENABLES": True,
                "WRITE_PBA_ALLOWED": False,
                "INIT_TABLE": False,
                "INIT_PBA": False,
                "INIT_ROM": False,
                "ALLOW_ROM_WRITES": False,
                "ENABLE_SIGNATURE_CHECK": False,
                "USE_QSPI": False,
                "ENABLE_CACHE": False,
                "INIT_CACHE_VALID": False,
                "SIGNATURE_CHECK": False,
                "alignment_warning": None,
                "NUM_MSIX": 16,
                "BAR_APERTURE_SIZE": 4096,
            },
        },
        # TCL templates
        "tcl/*.j2": {
            "required_vars": {
                "board",
                "device",
            },
            "optional_vars": {
                "supports_msix",
                "supports_msi",
                "generated_xdc_path",
                "board_xdc_content",
                "top_module",
                "enable_power_opt",
                "enable_incremental",
                "constraint_files",
                "max_lanes",
                "batch_mode",
                "build",
            },
            "default_values": {
                "supports_msix": False,
                "supports_msi": False,
                "generated_xdc_path": None,
                "board_xdc_content": None,
                "top_module": "pcileech_top",
                "enable_power_opt": False,
                "enable_incremental": False,
                "constraint_files": [],
                "max_lanes": 1,
                "batch_mode": False,
                "build": {"jobs": 4},
            },
        },
        # Power management specific
        "sv/power_*.sv.j2": {
            "optional_vars": {
                "enable_wake_events",
                "enable_pme",
            },
            "default_values": {
                "enable_wake_events": False,
                "enable_pme": False,
            },
        },
        # Performance counter specific
        "sv/performance_counters.sv.j2": {
            "optional_vars": {
                "enable_transaction_counters",
                "enable_bandwidth_monitoring",
                "enable_error_rate_tracking",
                "enable_performance_grading",
                "enable_perf_outputs",
                "error_signals_available",
                "network_signals_available",
                "storage_signals_available",
                "graphics_signals_available",
                "generic_signals_available",
            },
            "default_values": {
                "enable_transaction_counters": False,
                "enable_bandwidth_monitoring": False,
                "enable_error_rate_tracking": False,
                "enable_performance_grading": False,
                "enable_perf_outputs": False,
                "error_signals_available": False,
                "network_signals_available": False,
                "storage_signals_available": False,
                "graphics_signals_available": False,
                "generic_signals_available": False,
            },
        },
        # MSI-X specific templates
        "sv/msix_*.sv.j2": {
            "required_vars": {
                "NUM_MSIX",
            },
            "optional_vars": {
                "RESET_CLEAR",
                "USE_BYTE_ENABLES",
                "WRITE_PBA_ALLOWED",
                "INIT_TABLE",
                "INIT_PBA",
                "alignment_warning",
            },
            "default_values": {
                "NUM_MSIX": 16,
                "RESET_CLEAR": True,
                "USE_BYTE_ENABLES": True,
                "WRITE_PBA_ALLOWED": False,
                "INIT_TABLE": False,
                "INIT_PBA": False,
                "alignment_warning": None,
            },
        },
        # Option ROM templates
        "sv/option_rom_*.sv.j2": {
            "optional_vars": {
                "ALLOW_ROM_WRITES",
                "INIT_ROM",
                "ENABLE_SIGNATURE_CHECK",
                "USE_QSPI",
                "ENABLE_CACHE",
                "RESET_CLEAR",
                "INIT_CACHE_VALID",
                "SIGNATURE_CHECK",
                "SPI_FAST_CMD",
                "QSPI_ONLY_CMD",
                "FLASH_ADDR_OFFSET",
            },
            "default_values": {
                "ALLOW_ROM_WRITES": False,
                "INIT_ROM": False,
                "ENABLE_SIGNATURE_CHECK": False,
                "USE_QSPI": False,
                "ENABLE_CACHE": False,
                "RESET_CLEAR": True,
                "INIT_CACHE_VALID": False,
                "SIGNATURE_CHECK": False,
                "SPI_FAST_CMD": "0Bh",
                "QSPI_ONLY_CMD": "EBh",
                "FLASH_ADDR_OFFSET": "24'h000000",
            },
        },
        # PCILeech-specific templates
        "*pcileech*.j2": {
            "required_vars": {
                "device_signature",  # CRITICAL: Required for security
                "device_config",
                # board_config required since PCILeech generates SystemVerilog
                "board_config",
                "config_space",
                "msix_config",
                "bar_config",
                "timing_config",
                "pcileech_config",
            },
            "optional_vars": {
                "pcileech_modules",
                "pcileech_command_timeout",
                "pcileech_buffer_size",
                "enable_dma_operations",
                "enable_interrupt_coalescing",
                "supports_msix",
                "supports_msi",
                "variance_model",
                "power_config",
                "error_config",
                "perf_config",
                "enable_clock_crossing",
                "enable_custom_config",
                "device_specific_config",
                "enable_performance_counters",
                "enable_error_detection",
                "power_management",
                "error_handling",
                "performance_counters",
                "behavior_profile",
                # BAR model learned data (may be absent -> fallback path)
                "bar_model",
                # Interrupt configuration (may be absent in baseline context)
                "interrupt_config",
            },
            "default_values": {
                "pcileech_modules": [],
                "pcileech_command_timeout": 1000,
                "pcileech_buffer_size": 4096,
                "enable_dma_operations": True,
                "enable_interrupt_coalescing": False,
                "supports_msix": False,
                "supports_msi": False,
                "enable_clock_crossing": False,
                "enable_custom_config": False,
                "device_specific_config": {},
                "enable_performance_counters": False,
                "enable_error_detection": False,
                # Provide explicit default None so StrictUndefined doesn't raise
                "bar_model": None,
                # Provide a safe default interrupt config so templates can check strategy
                "interrupt_config": {"strategy": "none"},
            },
        },
    }

    def __init__(self):
        """Initialize the template context validator."""
        self.template_cache: Dict[str, TemplateVariableRequirements] = {}
        self._template_mtime_cache: Dict[str, float] = {}
        self.prefix = "TMPL_VALID"

    def get_template_requirements(
        self, template_name: str
    ) -> TemplateVariableRequirements:
        """
        Get the variable requirements for a specific template.

        Args:
            template_name: Name of the template file

        Returns:
            TemplateVariableRequirements object with required/optional vars
            and defaults
        """
        # Check if template file has been modified since last cache
        template_path = Path(__file__).parent.parent / "templates" / template_name
        cache_valid = True

        if template_path.exists():
            current_mtime = template_path.stat().st_mtime
            cached_mtime = self._template_mtime_cache.get(template_name)

            if cached_mtime is None or current_mtime > cached_mtime:
                # Template has been modified, invalidate cache entry
                if template_name in self.template_cache:
                    log_debug_safe(
                        logger,
                        "Template '{tpl}' modified, invalidating cache",
                        tpl=template_name,
                        prefix=self.prefix,
                    )
                    del self.template_cache[template_name]
                self._template_mtime_cache[template_name] = current_mtime
                cache_valid = False

        if template_name in self.template_cache and cache_valid:
            return self.template_cache[template_name]

        requirements = TemplateVariableRequirements(template_name)

        # Match template name against patterns
        for pattern, config in self.TEMPLATE_REQUIREMENTS.items():
            if self._matches_pattern(template_name, pattern):
                requirements.required_vars.update(config.get("required_vars", set()))
                requirements.optional_vars.update(config.get("optional_vars", set()))
                requirements.default_values.update(config.get("default_values", {}))

        self.template_cache[template_name] = requirements
        return requirements

    def _matches_pattern(self, template_name: str, pattern: str) -> bool:
        """
        Check if a template name matches a pattern.

        Args:
            template_name: Template file name
            pattern: Pattern to match (supports wildcards)

        Returns:
            True if template matches pattern
        """
        # Convert glob pattern to regex
        regex_pattern = pattern.replace("*", ".*").replace("?", ".")
        regex_pattern = f".*{regex_pattern}$"  # Match end of string
        return bool(re.match(regex_pattern, template_name))

    def validate_and_complete_context(
        self,
        template_name: str,
        context: Dict[str, Any],
        strict: bool = True,
    ) -> Dict[str, Any]:
        """
        Strictly validate template context with security-first approach.

        This method enforces that all required template variables are explicitly
        defined in the context. It rejects incomplete context data and does not
        provide defaults for critical template variables.

        Args:
            template_name: Name of the template being rendered
            context: Original template context
            strict: Controls validation strictness (default: True for security)

        Returns:
            Validated context without adding defaults for required variables

        Raises:
            ValueError: If any required variables are missing or None
        """
        requirements = self.get_template_requirements(template_name)
        validated_context = context.copy()

        # Low-risk helper usage (no functional change) for TCL-specific
        # pruning & synthesis
        if template_name.startswith("tcl/"):
            self._prune_unused_tcl_requirements(
                template_name, requirements, logger_prefix=self.prefix
            )
            self._synthesize_device_if_needed(
                template_name,
                requirements,
                validated_context,
                logger_prefix=self.prefix,
            )

        # Detect variables that the template itself assigns via `{% set var = ... %}`
        # and treat them as implicitly present for validation purposes. This allows
        # templates to provide safe fallbacks without requiring the caller to
        # explicitly populate every variable.
        assigned_in_template: Set[str] = set()
        try:
            template_path = Path(__file__).parent.parent / "templates" / template_name
            if template_path.exists():
                content = template_path.read_text(encoding="utf-8")
                for m in re.finditer(
                    r"\{%-?\s*set\s+([A-Za-z_][A-Za-z0-9_]*)", content
                ):
                    assigned_in_template.add(m.group(1))
        except Exception:
            # If anything goes wrong reading the template, be conservative and
            # continue validation without assigned_in_template enhancements.
            assigned_in_template = set()

        # Check required variables with appropriate strictness
        validation_errors = []
        missing_required = []

        if strict:
            for var in requirements.required_vars:
                if var in assigned_in_template:
                    continue
                if var not in validated_context or validated_context[var] is None:
                    missing_required.append(var)
                    validation_errors.append(
                        safe_format("Missing required variable '{var}'", var=var)
                    )

            none_values = []
            for var, value in validated_context.items():
                if value is None:
                    none_values.append(var)
                    validation_errors.append(
                        safe_format("Variable '{var}' has None value", var=var)
                    )

            if validation_errors:
                error_msg = (
                    "SECURITY VIOLATION: Template '"
                    f"{template_name}' context validation failed:\n- "
                    + "\n- ".join(validation_errors)
                    + "\n\nExplicit initialization of all template variables is "
                    "required."
                )
                # Use standardized error logging wrapper
                log_error_safe(
                    logger,
                    "{msg}",
                    msg=error_msg,
                    prefix=self.prefix,
                )
                raise ValueError(error_msg)
        else:
            # In non-strict mode, only warn about missing required variables
            for var in requirements.required_vars:
                if var in assigned_in_template:
                    continue
                if var not in validated_context:
                    log_debug_safe(
                        logger,
                        "Required variable '{var}' missing from template '{tmpl}' context, will be handled by Jinja2",
                        prefix="TEMPLATE_VALIDATION",
                        var=var,
                        tmpl=template_name,
                    )

        if not strict:
            # Add defaults for optional variables only
            for var in requirements.optional_vars:
                if var not in validated_context:
                    if var in requirements.default_values:
                        validated_context[var] = requirements.default_values[var]
                        log_debug_safe(
                            logger,
                            (
                                "Added default value for optional variable "
                                f"'{var}' in template '{template_name}'"
                            ),
                            prefix="TEMPLATE",
                        )
            # Post-default normalization: ensure interrupt_config.strategy present
            if self._matches_pattern(template_name, "*pcileech*.j2"):
                intr = validated_context.get("interrupt_config")
                try:
                    if isinstance(intr, dict):
                        if "strategy" not in intr:
                            intr["strategy"] = "none"
                            log_debug_safe(
                                logger,
                                "Added default interrupt_config.strategy",
                                tpl=template_name,
                                prefix="TEMPLATE",
                            )
                    elif (
                        intr is not None
                        and getattr(intr, "strategy", None) is None
                    ):
                        validated_context["interrupt_config"] = {"strategy": "none"}
                        log_debug_safe(
                            logger,
                            "Wrapped interrupt_config object with default strategy",
                            tpl=template_name,
                            prefix="TEMPLATE",
                        )
                except Exception as e:  # Defensive: never fail normalization
                    log_debug_safe(
                        logger,
                        "Interrupt config normalization suppressed: {err}",
                        err=str(e),
                        prefix="TEMPLATE",
                    )
        else:

            invalid_optional = []
            for var in requirements.optional_vars:
                if var in validated_context and validated_context[var] is None:
                    invalid_optional.append(var)

            if invalid_optional:
                error_msg = (
                    "SECURITY VIOLATION: Template '"
                    f"{template_name}' has None values for optional variables: "
                    f"{', '.join(invalid_optional)}.\nExplicit initialization of "
                    "all template variables is required in strict mode."
                )
                log_error_safe(
                    logger,
                    "{msg}",
                    msg=error_msg,
                    prefix="TEMPLATE_VALIDATION",
                )
                raise ValueError(error_msg)

        return validated_context

    def _prune_unused_tcl_requirements(
        self,
        template_name: str,
        requirements: TemplateVariableRequirements,
        logger_prefix: str = "TEMPLATE",
    ) -> None:
        """Prune 'device'/'board' if not actually referenced by a TCL template.

        Safe subset refactor: identical logic to prior inline block, now with
        explicit logging when pruning occurs. Swallows analysis errors to avoid
        introducing new failure modes.
        """
        try:
            tpl_root = Path(__file__).parent.parent / "templates"
            template_path = tpl_root / template_name
            if not template_path.exists():  # Nothing to analyze
                return
            referenced = self.analyze_template_for_variables(template_path)
            for maybe_unused in ("device", "board"):
                if (
                    maybe_unused in requirements.required_vars
                    and maybe_unused not in referenced
                ):
                    # Copy-on-write to avoid mutating cached set shared across
                    # templates
                    original = set(requirements.required_vars)
                    requirements.required_vars = original
                    requirements.required_vars.discard(maybe_unused)
                    log_info_safe(
                        logger,
                        (
                            "Pruned unused required variable '{var}' from "
                            "template '{tpl}'"
                        ),
                        var=maybe_unused,
                        tpl=template_name,
                        prefix=logger_prefix,
                    )
        except Exception as e:  # Defensive: never fail pruning phase
            log_debug_safe(
                logger,
                "Suppressed pruning error for template '{tpl}': {err}",
                tpl=template_name,
                err=str(e),
                prefix=logger_prefix,
            )

    def _synthesize_device_if_needed(
        self,
        template_name: str,
        requirements: TemplateVariableRequirements,
        validated_context: Dict[str, Any],
        logger_prefix: str = "TEMPLATE",
    ) -> None:
        """Synthesize minimal 'device' object iff template requires+references it.

        Behavior mirrors the prior inline logic; any exception is swallowed to
        preserve original strict validation semantics.
        """
        try:
            if (
                "device" not in requirements.required_vars
                or "device" in validated_context
            ):
                return
            tpl_root = Path(__file__).parent.parent / "templates"
            template_path = tpl_root / template_name
            if not template_path.exists():
                return
            txt = template_path.read_text(encoding="utf-8")
            referenced = bool(
                re.search(r"\{\{[^}]*\bdevice\b", txt)
                or re.search(r"safe_attr\(\s*device\s*", txt)
                or re.search(r"\{%[^%]*\bdevice\b", txt)
            )
            if not referenced:
                original = set(requirements.required_vars)
                requirements.required_vars = original
                requirements.required_vars.discard("device")
                log_info_safe(
                    logger,
                    "Pruned unused required variable 'device' from template '{tpl}'",
                    tpl=template_name,
                    prefix=logger_prefix,
                )
                return
            from src.utils.unified_context import TemplateObject  # Lazy import
            source = (
                validated_context.get("device_config")
                or validated_context.get("config_space")
                or {}
            )
            rev = None
            if isinstance(source, dict):
                rev = source.get("revision_id")
                if rev is None and isinstance(source.get("registers"), dict):
                    rev = source.get("registers", {}).get("revision_id")
            if isinstance(source, dict):
                vendor_id = source.get("vendor_id")
                device_id = source.get("device_id")
                class_code = source.get("class_code")
                subsys_vendor_id = source.get("subsys_vendor_id") or source.get(
                    "subsystem_vendor_id"
                )
                subsys_device_id = source.get("subsys_device_id") or source.get(
                    "subsystem_device_id"
                )
            else:
                vendor_id = device_id = class_code = subsys_vendor_id = (
                    subsys_device_id
                ) = None
            validated_context["device"] = TemplateObject(
                {
                    "vendor_id": vendor_id,
                    "device_id": device_id,
                    "class_code": class_code,
                    "revision_id": rev,
                    "subsys_vendor_id": subsys_vendor_id,
                    "subsys_device_id": subsys_device_id,
                }
            )
            log_info_safe(
                logger,
                "Synthesized minimal 'device' object for template '{tpl}'",
                tpl=template_name,
                prefix=logger_prefix,
            )
        except Exception as e:
            log_debug_safe(
                logger,
                "Suppressed device synthesis error for template '{tpl}': {err}",
                tpl=template_name,
                err=str(e),
                prefix=logger_prefix,
            )

    def analyze_template_for_variables(self, template_path: Path) -> Set[str]:
        """
        Analyze a template file to find all referenced variables.

        Args:
            template_path: Path to the template file

        Returns:
            Set of variable names referenced in the template
        """
        variables = set()

        try:
            with open(template_path, "r") as f:
                content = f.read()

            # Find variables in {{ }} expressions
            var_pattern = r"\{\{\s*(\w+)(?:\.\w+)*\s*(?:\|[^}]+)?\s*\}\}"
            variables.update(re.findall(var_pattern, content))

            # Find variables in {% if %} conditions
            if_pattern = (
                r"\{%\s*if\s+(\w+)(?:\.\w+)*\s*(?:is\s+defined|is\s+undefined|%\})"
            )
            variables.update(re.findall(if_pattern, content))

            # Find variables in {% for %} loops
            for_pattern = r"\{%\s*for\s+\w+\s+in\s+(\w+)(?:\.\w+)*\s*%\}"
            variables.update(re.findall(for_pattern, content))

        except Exception as e:
            log_warning_safe(
                logger,
                "Failed to analyze template '{path}': {error}",
                path=template_path,
                error=str(e),
                prefix="TEMPLATE_ANALYSIS",
            )

        return variables

    def generate_context_documentation(self, template_name: str) -> str:
        """
        Generate documentation for the expected context of a template.

        Args:
            template_name: Name of the template

        Returns:
            Documentation string describing required/optional variables
        """
        requirements = self.get_template_requirements(template_name)

        doc_lines = [f"Template: {template_name}", "=" * 50, ""]

        if requirements.required_vars:
            doc_lines.append("Required Variables:")
            for var in sorted(requirements.required_vars):
                default = requirements.default_values.get(var, "No default")
                doc_lines.append(f"  - {var}: {default}")
            doc_lines.append("")

        if requirements.optional_vars:
            doc_lines.append("Optional Variables:")
            for var in sorted(requirements.optional_vars):
                default = requirements.default_values.get(var, "No default")
                doc_lines.append(f"  - {var}: {default}")
            doc_lines.append("")

        if requirements.default_values:
            doc_lines.append("Default Values:")
            for var, value in sorted(requirements.default_values.items()):
                doc_lines.append(f"  - {var} = {repr(value)}")

        return "\n".join(doc_lines)

    def clear_cache(self) -> None:
        """Clear all cached template requirements and modification times."""
        self.template_cache.clear()
        self._template_mtime_cache.clear()
        log_debug_safe(
            logger,
            "Cleared template requirements cache",
            prefix=self.prefix,
        )

    def invalidate_template(self, template_name: str) -> None:
        """Invalidate cache for a specific template."""
        if template_name in self.template_cache:
            del self.template_cache[template_name]
        if template_name in self._template_mtime_cache:
            del self._template_mtime_cache[template_name]
        log_debug_safe(
            logger,
            "Invalidated cache for template '{tpl}'",
            tpl=template_name,
            prefix=self.prefix,
        )


# Global instance for easy access
_validator = TemplateContextValidator()


def validate_template_context(
    template_name: str,
    context: Dict[str, Any],
    strict: bool = True,
) -> Dict[str, Any]:
    """
    Validate template context with security-first approach.

    This function enforces strict validation of all template variables,
    rejecting incomplete context data to prevent security issues from
    undefined template variables.

    Args:
        template_name: Name of the template being rendered
        context: Original template context
        strict: Controls validation strictness (default: True for security)

    Returns:
        Validated context without adding defaults for required variables

    Raises:
        ValueError: If any required variables are missing or None
    """
    return _validator.validate_and_complete_context(template_name, context, strict)


def get_template_requirements(template_name: str) -> TemplateVariableRequirements:
    """
    Get the variable requirements for a template.

    Args:
        template_name: Name of the template

    Returns:
        TemplateVariableRequirements object
    """
    return _validator.get_template_requirements(template_name)


def analyze_template_variables(template_path: Path) -> Set[str]:
    """
    Analyze a template file for referenced variables.

    Args:
        template_path: Path to the template file

    Returns:
        Set of variable names referenced in the template
    """
    return _validator.analyze_template_for_variables(template_path)


def clear_global_template_cache() -> None:
    """Clear the global template requirements cache."""
    _validator.clear_cache()


def invalidate_global_template(template_name: str) -> None:
    """Invalidate global cache for a specific template."""
    _validator.invalidate_template(template_name)
