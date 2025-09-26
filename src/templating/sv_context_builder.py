"""Context builder for SystemVerilog generation."""

import logging

from typing import Any, Dict, List

from src.string_utils import log_warning_safe, log_error_safe, safe_format

from src.utils.validation_constants import SV_FILE_HEADER

from ..utils.unified_context import (
    DEFAULT_TIMING_CONFIG,
    MSIX_DEFAULT,
    PCILEECH_DEFAULT,
    TemplateObject,
    normalize_config_to_dict,
)
from .sv_constants import SV_CONSTANTS

from .template_renderer import TemplateRenderError


class SVContextBuilder:
    """Builds and manages template contexts for SystemVerilog generation."""

    def __init__(self, logger: logging.Logger):
        """Initialize the context builder."""
        self.logger = logger
        self.constants = SV_CONSTANTS
        self._context_cache = {}

    def build_enhanced_context(
        self,
        template_context: Dict[str, Any],
        power_config: Any,
        error_config: Any,
        perf_config: Any,
        device_config: Any,
    ) -> Dict[str, Any]:
        """
        Build enhanced context for template rendering.

        This method consolidates the complex context building logic into
        smaller, manageable pieces with better performance and maintainability.

        Args:
            template_context: Original template context
            power_config: Power management configuration
            error_config: Error handling configuration
            perf_config: Performance configuration
            device_config: Device-specific configuration

        Returns:
            Enhanced context dictionary
        """
        # Start with base context
        enhanced_context = self._create_base_context(template_context)

        # Extract and normalize device config
        device_config_dict = self._normalize_device_config(
            template_context.get("device_config", {})
        )

        # Add device identification
        self._add_device_identification(enhanced_context, device_config_dict)

        # Add configuration objects
        self._add_configuration_objects(
            enhanced_context, template_context, device_config_dict
        )

        # Add power, error, and performance contexts
        self._add_feature_contexts(
            enhanced_context, power_config, error_config, perf_config
        )

        # Add device-specific settings
        self._add_device_settings(enhanced_context, device_config, device_config_dict)

        # Add template helpers and utilities
        self._add_template_helpers(enhanced_context)

        # Add compatibility fields
        self._add_compatibility_fields(enhanced_context, template_context)

        return enhanced_context

    def build_power_management_context(self, power_config: Any) -> Dict[str, Any]:
        """Build power management context with validation."""
        if not power_config:
            raise ValueError("Power management configuration cannot be None")

        # Validate and extract transition cycles
        transition_cycles = self._extract_transition_cycles(power_config)

        # Validate required fields
        required_fields = ["clk_hz", "transition_timeout_ns"]
        for field in required_fields:
            if not hasattr(power_config, field) or getattr(power_config, field) is None:
                raise ValueError(
                    safe_format("Power management {field} cannot be None", field=field)
                )
        return {
            "clk_hz": power_config.clk_hz,
            "transition_timeout_ns": power_config.transition_timeout_ns,
            "enable_pme": power_config.enable_pme,
            "enable_wake_events": power_config.enable_wake_events,
            "transition_cycles": transition_cycles,
            "has_interface_signals": getattr(
                power_config, "has_interface_signals", False
            ),
        }

    def build_performance_context(self, perf_config: Any) -> Dict[str, Any]:
        """Build performance monitoring context with validation."""
        if not perf_config:
            raise ValueError("Performance configuration cannot be None")

        required_fields = [
            "counter_width",
            "enable_bandwidth_monitoring",
            "enable_latency_tracking",
            "enable_error_rate_tracking",
            "sampling_period",
        ]

        self._validate_required_fields(perf_config, required_fields, "performance")

        return {
            "counter_width": perf_config.counter_width,
            "enable_bandwidth": perf_config.enable_bandwidth_monitoring,
            "enable_latency": perf_config.enable_latency_tracking,
            "enable_error_rate": perf_config.enable_error_rate_tracking,
            "sample_period": perf_config.sampling_period,
        }

    def build_error_handling_context(self, error_config: Any) -> Dict[str, Any]:
        """Build error handling context with validation."""
        if not error_config:
            raise ValueError("Error handling configuration cannot be None")

        required_fields = [
            "enable_error_detection",
            "enable_error_logging",
            "enable_auto_retry",
            "max_retry_count",
            "error_recovery_cycles",
            "error_log_depth",
        ]

        self._validate_required_fields(error_config, required_fields, "error handling")

        return {
            "enable_error_detection": error_config.enable_error_detection,
            "enable_logging": error_config.enable_error_logging,
            "enable_auto_retry": error_config.enable_auto_retry,
            "max_retry_count": error_config.max_retry_count,
            "recovery_cycles": error_config.error_recovery_cycles,
            "error_log_depth": error_config.error_log_depth,
        }

    def _create_base_context(self, template_context: Dict[str, Any]) -> Dict[str, Any]:
        """Create base context with essential fields."""
        return {
            # Critical security field - no fallback allowed
            "device_signature": template_context["device_signature"],
            # Header for generated files
            "header": SV_FILE_HEADER,
            # Basic settings
            # Use distributed RAM FIFO by default to avoid vendor IP dependency
            "fifo_type": "distributed",
            "fifo_depth": self.constants.DEFAULT_FIFO_DEPTH,
            "data_width": self.constants.DEFAULT_DATA_WIDTH,
            "fpga_family": self.constants.DEFAULT_FPGA_FAMILY,
        }

    def _normalize_device_config(self, device_config: Any) -> Dict[str, Any]:
        """Normalize device configuration to dictionary."""
        if isinstance(device_config, TemplateObject):
            return dict(device_config)
        elif isinstance(device_config, dict):
            return device_config
        elif hasattr(device_config, "__dict__"):
            return vars(device_config)
        else:
            raise TemplateRenderError(
                f"Cannot normalize device_config of type {type(device_config).__name__}"
            )

    def _add_device_identification(
        self, context: Dict[str, Any], device_config: Dict[str, Any]
    ) -> None:
        """Add device identification fields to context."""
        # Extract vendor and device IDs - no fallbacks allowed
        vendor_id = device_config.get("vendor_id")
        device_id = device_config.get("device_id")

        if not vendor_id or not device_id:
            # Fail fast per repository conventions; use class logger and top-level imports
            log_error_safe(
                self.logger,
                safe_format(
                    "Missing required device identifiers: vendor_id={vid}, device_id={did}",
                    vid=vendor_id,
                    did=device_id,
                ),
                prefix="TEMPLATING",
            )
            # Do not terminate the process here; raise a template error to be handled upstream
            raise TemplateRenderError(
                safe_format(
                    "Missing required device identifiers: vendor_id={vid}, device_id={did}",
                    vid=str(vendor_id),
                    did=str(device_id),
                )
            )

        # Add string versions
        context["vendor_id"] = vendor_id
        context["device_id"] = device_id
        context["vendor_id_hex"] = vendor_id
        context["device_id_hex"] = device_id

        # Add integer versions for formatting
        context["vendor_id_int"] = self._safe_hex_to_int(vendor_id)
        context["device_id_int"] = self._safe_hex_to_int(device_id)

        # Also add to device_config for consistency
        device_config["vendor_id_int"] = context["vendor_id_int"]
        device_config["device_id_int"] = context["device_id_int"]

    def _add_configuration_objects(
        self,
        context: Dict[str, Any],
        template_context: Dict[str, Any],
        device_config_dict: Dict[str, Any],
    ) -> None:
        """Add configuration objects as TemplateObjects."""
        # Normalize configurations using unified context helper
        pcfg_dict = normalize_config_to_dict(
            template_context.get("pcileech_config"), default=PCILEECH_DEFAULT
        )
        mcfg_dict = normalize_config_to_dict(
            template_context.get("msix_config"), default=MSIX_DEFAULT
        )
        tcfg_dict = normalize_config_to_dict(
            template_context.get("timing_config"), default=DEFAULT_TIMING_CONFIG
        )

        # Convert to TemplateObjects for attribute access in templates
        context["device_config"] = self._ensure_template_object(device_config_dict)
        context["msix_config"] = self._ensure_template_object(mcfg_dict)
        context["pcileech_config"] = self._ensure_template_object(pcfg_dict)
        context["timing_config"] = self._ensure_template_object(tcfg_dict)

        # Add other configuration objects
        for config_name in [
            "bar_config",
            "board_config",
            "interrupt_config",
            "config_space_data",
            "generation_metadata",
        ]:
            config_value = template_context.get(config_name, {})
            context[config_name] = self._ensure_template_object(config_value)

    def _add_feature_contexts(
        self,
        context: Dict[str, Any],
        power_config: Any,
        error_config: Any,
        perf_config: Any,
    ) -> None:
        """Add feature-specific contexts."""
        # Add power management context
        try:
            power_ctx = self.build_power_management_context(power_config)
            context["power_management"] = self._ensure_template_object(power_ctx)
            context["power_config"] = self._ensure_template_object(vars(power_config))
        except Exception as e:
            log_warning_safe(
                self.logger,
                safe_format(f"Failed to build power context: {e}"),
                prefix="TEMPLATING",
            )
            context["power_management"] = TemplateObject(
                {"has_interface_signals": False}
            )
            context["power_config"] = TemplateObject({"enable_power_management": False})

        # Add error handling context
        try:
            error_ctx = self.build_error_handling_context(error_config)
            context["error_handling"] = self._ensure_template_object(error_ctx)
            context["error_config"] = self._ensure_template_object(vars(error_config))
        except Exception as e:
            log_warning_safe(
                self.logger,
                safe_format(
                    "Failed to build error handling context: {error}", error=str(e)
                ),
                prefix="TEMPLATING",
            )
            context["error_handling"] = TemplateObject({"enable_error_logging": False})
            context["error_config"] = TemplateObject({"enable_error_detection": False})

        # Add performance context
        try:
            perf_ctx = self.build_performance_context(perf_config)
            context["performance_counters"] = self._ensure_template_object(perf_ctx)
            context["perf_config"] = self._ensure_template_object(vars(perf_config))
        except Exception as e:
            log_warning_safe(
                self.logger,
                safe_format(
                    "Failed to build performance context: {error}", error=str(e)
                ),
                prefix="TEMPLATING",
            )
            context["performance_counters"] = TemplateObject({})
            context["perf_config"] = TemplateObject({})

    def _add_device_settings(
        self,
        context: Dict[str, Any],
        device_config: Any,
        device_config_dict: Dict[str, Any],
    ) -> None:
        """Add device-specific settings to context."""
        # Device type and class
        context["device_type"] = device_config_dict.get(
            "device_type",
            getattr(device_config, "device_type", DeviceType.GENERIC).value,
        )
        context["device_class"] = device_config_dict.get(
            "device_class",
            getattr(device_config, "device_class", DeviceClass.CONSUMER).value,
        )

        # Feature flags
        context["enable_scatter_gather"] = getattr(
            device_config,
            "enable_scatter_gather",
            getattr(device_config, "enable_dma", True),
        )
        context["enable_interrupt"] = (
            getattr(context.get("interrupt_config", None), "vectors", 0) > 0
        )
        context["enable_clock_crossing"] = True
        context["enable_performance_counters"] = True
        context["enable_error_detection"] = True
        context["enable_custom_config"] = True

        # Device info object
        context["device"] = TemplateObject(
            {
                "msi_vectors": int(context.get("msi_vectors", 0)),
                "num_sources": int(context.get("num_sources", 1)),
                # No fallback device ID - should use actual device_id from context
                "FALLBACK_DEVICE_ID": device_config_dict["device_id"],
            }
        )

    def _add_template_helpers(self, context: Dict[str, Any]) -> None:
        """Add helper functions and utilities for templates."""
        # Add Python builtins
        context["getattr"] = getattr

        # Add log2 helper without nested def to satisfy linters
        context["log2"] = lambda x: (
            (x.bit_length() - 1) if isinstance(x, int) and x > 0 else 0
        )

    def _add_compatibility_fields(
        self, context: Dict[str, Any], template_context: Dict[str, Any]
    ) -> None:
        """Add fields for backward compatibility."""
        # MSI-X related fields
        msix_config = context.get("msix_config", {})
        context["NUM_MSIX"] = self._safe_get_int(msix_config, "num_vectors", 0)
        context["msix_table_bir"] = self._safe_get_int(msix_config, "table_bir", 0)
        context["msix_table_offset"] = self._safe_get_int(
            msix_config, "table_offset", 0x1000
        )
        context["msix_pba_bir"] = self._safe_get_int(msix_config, "pba_bir", 0)
        context["msix_pba_offset"] = self._safe_get_int(
            msix_config, "pba_offset", 0x2000
        )

        # Legacy templates expect uppercase aliases sourced from donor data.
        # Publish both canonical snake_case fields and uppercase copies so
        # strict Jinja rendering succeeds without guessing.
        context.setdefault("MSIX_TABLE_BIR", context["msix_table_bir"])
        context.setdefault("MSIX_TABLE_OFFSET", context["msix_table_offset"])
        context.setdefault("MSIX_PBA_BIR", context["msix_pba_bir"])
        context.setdefault("MSIX_PBA_OFFSET", context["msix_pba_offset"])
        context.setdefault("NUM_MSIX", context["NUM_MSIX"])

        # Table/PBA combined fields
        context["table_offset_bir"] = context["msix_table_bir"] | (
            context["msix_table_offset"] & ~0x7
        )
        context["pba_offset_bir"] = context["msix_pba_bir"] | (
            context["msix_pba_offset"] & ~0x7
        )

        # Other compatibility fields
        context["msi_vectors"] = int(template_context.get("msi_vectors", 0))
        context["max_payload_size"] = int(template_context.get("max_payload_size", 256))
        context["enable_perf_counters"] = bool(
            context.get("enable_performance_counters", False)
        )
        context["enable_error_logging"] = bool(
            context.get("error_handling", {}).get("enable_error_logging", False)
        )

        # Ensure device_config exposes critical attributes expected by SV templates.
        # Prefer dynamic values already present in the composed context; fall back to
        # conservative defaults from pcileech_config when missing. This avoids
        # Jinja AttributeError during strict rendering while keeping values
        # donor-derived when available.
        try:
            dc = context.get("device_config")
            pcfg = context.get("pcileech_config")

            # Derive max payload size in priority order:
            # 1) top-level context["max_payload_size"] (already coerced to int)
            # 2) pcileech_config.max_payload_size
            # 3) static conservative default (256)
            derived_mps = (
                int(context.get("max_payload_size", 0))
                or (int(getattr(pcfg, "max_payload_size", 0)) if pcfg else 0)
                or 256
            )

            # Derive MSI(-X) vectors in priority order:
            # 1) msix_config.num_vectors when supported (>0)
            # 2) top-level context["msi_vectors"]
            # 3) conservative default (0)
            msix_vectors = self._safe_get_int(msix_config, "num_vectors", 0)
            derived_vectors = (
                msix_vectors if msix_vectors > 0 else int(context.get("msi_vectors", 0))
            )

            # Populate device_config defaults only if missing to preserve
            # upstream donor-provided values.
            if isinstance(dc, TemplateObject):
                if not hasattr(dc, "max_payload_size"):
                    dc.setdefault("max_payload_size", int(derived_mps))
                if not hasattr(dc, "msi_vectors"):
                    dc.setdefault("msi_vectors", int(derived_vectors))
        except Exception:
            # Never let compatibility propagation break context building
            pass

        # BAR and config space
        bar_config = template_context.get("bar_config", {}) or {}
        aperture_size = self._safe_get_int(bar_config, "aperture_size", 0x1000)
        context.setdefault("BAR_APERTURE_SIZE", aperture_size)

        # Default to enabling byte enables unless explicitly disabled.
        # Legacy controller relies on this flag for write strobes, so fall
        # back to True when donor data omits the override.
        use_byte_enables = bool(bar_config.get("use_byte_enables", True))
        context.setdefault("USE_BYTE_ENABLES", use_byte_enables)

        # Derive BAR window layout in 4KB pages. Clamp to avoid negative values
        # if the aperture is undersized in test fixtures.
        aperture_pages = max(1, aperture_size // 0x1000)
        config_shadow_page = max(0, aperture_pages - 1)
        custom_window_page = max(0, aperture_pages - 2)

        context.setdefault(
            "CONFIG_SHDW_HI",
            bar_config.get("config_shadow_base", config_shadow_page),
        )
        context.setdefault(
            "CUSTOM_WIN_BASE",
            bar_config.get("custom_window_base", custom_window_page),
        )

        context["bar"] = template_context.get("bar", [])
        context["config_space"] = {
            "vendor_id": context["vendor_id"],
            "device_id": context["device_id"],
            "class_code": template_context.get(
                "class_code", self.constants.DEFAULT_CLASS_CODE
            ),
            "revision_id": template_context.get(
                "revision_id", self.constants.DEFAULT_REVISION_ID
            ),
        }

    def _ensure_template_object(self, obj: Any) -> TemplateObject:
        """Convert any object to TemplateObject for consistent template access."""
        if isinstance(obj, TemplateObject):
            return obj
        elif isinstance(obj, dict):
            return TemplateObject(self._clean_dict_keys(obj))
        elif hasattr(obj, "__dict__"):
            return TemplateObject(self._clean_dict_keys(vars(obj)))
        else:
            return TemplateObject({})

    def _clean_dict_keys(self, d: Dict[Any, Any]) -> Dict[str, Any]:
        """Clean dictionary keys to ensure they are strings."""
        cleaned = {}
        for key, value in d.items():
            # Convert key to string
            if isinstance(key, str):
                clean_key = key
            elif hasattr(key, "name"):
                clean_key = key.name
            elif hasattr(key, "value"):
                clean_key = str(key.value)
            else:
                clean_key = str(key)

            # Convert enum values
            if hasattr(value, "value"):
                clean_value = value.value
            elif hasattr(value, "name"):
                clean_value = value.name
            else:
                clean_value = value

            cleaned[clean_key] = clean_value

        return cleaned

    def _extract_transition_cycles(self, power_config: Any) -> Dict[str, int]:
        """Extract and validate transition cycles from power config."""
        transition_cycles = power_config.transition_cycles
        if not transition_cycles:
            raise ValueError("Power management transition_cycles cannot be None")

        required_fields = ["d0_to_d1", "d1_to_d0", "d0_to_d3", "d3_to_d0"]

        if hasattr(transition_cycles, "__dict__"):
            # Object with attributes
            tc_dict = {}
            for field in required_fields:
                if not hasattr(transition_cycles, field):
                    raise ValueError(f"Missing transition cycle field: {field}")
                tc_dict[field] = getattr(transition_cycles, field)
            return tc_dict
        elif isinstance(transition_cycles, dict):
            # Dictionary
            missing = [f for f in required_fields if f not in transition_cycles]
            if missing:
                raise ValueError(
                    f"Missing transition cycle fields: {', '.join(missing)}"
                )
            return transition_cycles
        else:
            raise ValueError(
                f"Invalid transition_cycles type: {type(transition_cycles).__name__}"
            )

    def _validate_required_fields(
        self, config: Any, fields: List[str], config_name: str
    ) -> None:
        """Validate that required fields exist in configuration."""
        missing = []
        for field in fields:
            if not hasattr(config, field) or getattr(config, field) is None:
                missing.append(field)

        if missing:
            raise ValueError(
                safe_format(
                    f"Missing required {config_name} fields: {', '.join(missing)}"
                )
            )

    def _safe_hex_to_int(self, value: Any) -> int:
        """Safely convert hex string to integer."""
        try:
            if isinstance(value, str):
                return int(value, 16) if value else 0
            return int(value) if value is not None else 0
        except Exception:
            return 0

    def _safe_get_int(self, obj: Any, key: str, default: int) -> int:
        """Safely get integer value from object or dict."""
        try:
            if isinstance(obj, dict):
                return int(obj.get(key, default))
            elif hasattr(obj, key):
                return int(getattr(obj, key, default))
            return default
        except Exception:
            return default


# Import at the end to avoid circular dependency
from src.device_clone.device_config import DeviceClass, DeviceType
