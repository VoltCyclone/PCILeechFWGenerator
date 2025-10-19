"""Context builder for SystemVerilog generation."""

import logging
from typing import Any, Dict, List, Union

from src.device_clone.identifier_normalizer import IdentifierNormalizer
from src.device_clone.overlay_utils import compute_sparse_hash_table_size
from src.string_utils import log_error_safe, log_warning_safe, safe_format
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

# Module-level defaults are sourced from SV_CONSTANTS to avoid drift
CFG_SPACE_DEFAULT_SIZE: int = SV_CONSTANTS.CONFIG_SPACE_DEFAULT_SIZE
EXT_CAP_PTR_DEFAULT: int = SV_CONSTANTS.EXTENDED_CAP_PTR_DEFAULT


class SVContextBuilder:
    """Builds and manages template contexts for SystemVerilog generation."""

    def __init__(self, logger: logging.Logger):
        """Initialize the context builder."""
        self.logger = logger
        self.constants = SV_CONSTANTS
        self._context_cache = {}
        self.prefix = "TEMPLATING"

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

        # Add donor-provided device serial number (used for cfg_dsn wiring)
        self._add_device_serial_number(enhanced_context, template_context)

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
                safe_format(
                    "Cannot normalize device_config of type {type}",
                    type=type(device_config).__name__,
                )
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
                prefix=self.prefix,
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

        # Extract IEEE OUI from vendor ID
        self._add_vendor_oui(context, context["vendor_id_int"])

    def _add_vendor_oui(self, context: Dict[str, Any], vendor_id_int: int) -> None:
        """
        Extract IEEE OUI (Organizationally Unique Identifier) from vendor ID.
        
        The OUI is typically the lower 24 bits of the vendor ID.
        This is used in DSN construction per PCIe specification.
        
        Args:
            context: Context dictionary to update
            vendor_id_int: Vendor ID as integer
        """
        # OUI is 24-bit identifier from vendor ID
        oui = vendor_id_int & 0xFFFFFF
        
        context["vendor_oui"] = oui
        context["vendor_oui_hex"] = safe_format("0x{value:06X}", value=oui)
        context["pci_exp_ep_oui"] = oui  # Match reference implementation naming

    def _add_device_serial_number(
        self, context: Dict[str, Any], template_context: Dict[str, Any]
    ) -> None:
        """Extract and normalize the donor device serial number (DSN)."""

        serial_candidate = self._extract_device_serial_number(template_context)
        serial_int = self._safe_hex_to_int(serial_candidate)

        # Normalize to 64-bit range
        serial_int &= 0xFFFFFFFFFFFFFFFF

        if serial_int == 0:
            log_warning_safe(
                self.logger,
                safe_format(
                    "Device serial number unavailable; cfg_dsn will default to 0"
                ),
                prefix=self.prefix,
            )

        context["device_serial_number_int"] = serial_int
        context["device_serial_number_hex"] = safe_format(
            "0x{value:016X}", value=serial_int
        )
        context["device_serial_number_valid"] = bool(serial_int)
        context["device_serial_number_hi"] = (serial_int >> 32) & 0xFFFFFFFF
        context["device_serial_number_lo"] = serial_int & 0xFFFFFFFF

        # Semantic decomposition of DSN into OUI + serial components
        # Per PCIe spec: DSN lower 32 bits often contain OUI in bits [23:0]
        self._add_dsn_semantic_fields(context, serial_int)

    def _add_dsn_semantic_fields(
        self, context: Dict[str, Any], dsn_value: int
    ) -> None:
        """
        Decompose DSN into semantic components for SystemVerilog defines.
        
        Per PCIe specification, DSN is typically composed as:
        - Upper 32 bits: Device-specific serial number
        - Lower 32 bits: Often contains OUI in bits [23:0] + additional data
        
        This matches the reference implementation pattern:
            `define PCI_EXP_EP_DSN_2 32'h00000001
            `define PCI_EXP_EP_DSN_1 {{ 8'h1 }, `PCI_EXP_EP_OUI }
        
        Args:
            context: Context dictionary to update
            dsn_value: 64-bit DSN value
        """
        # Extract semantic components
        dsn_upper = (dsn_value >> 32) & 0xFFFFFFFF  # Upper 32 bits
        dsn_lower = dsn_value & 0xFFFFFFFF           # Lower 32 bits
        
        # Extract OUI from lower 32 bits (typically bits [23:0])
        dsn_oui = dsn_lower & 0xFFFFFF
        
        # Extract extension field from lower 32 bits (typically bits [31:24])
        dsn_ext = (dsn_lower >> 24) & 0xFF
        
        # Add fields for SystemVerilog template use
        context["dsn_upper_32"] = dsn_upper
        context["dsn_lower_32"] = dsn_lower
        context["dsn_oui"] = dsn_oui
        context["dsn_oui_hex"] = safe_format("0x{value:06X}", value=dsn_oui)
        context["dsn_extension"] = dsn_ext
        context["dsn_extension_hex"] = safe_format("0x{value:02X}", value=dsn_ext)
        
        # Add PCI Express endpoint define names (match reference implementation)
        context["pci_exp_ep_dsn_2"] = dsn_upper
        context["pci_exp_ep_dsn_1"] = dsn_lower
        context["pci_exp_ep_dsn_2_hex"] = safe_format("32'h{value:08X}", value=dsn_upper)
        context["pci_exp_ep_dsn_1_hex"] = safe_format("32'h{value:08X}", value=dsn_lower)

    def _extract_device_serial_number(self, template_context: Dict[str, Any]) -> Any:
        """Gather DSN candidates from the template context."""

        candidates = []

        # Top-level fields
        for key in (
            "device_serial_number_int",
            "device_serial_number",
            "dsn",
        ):
            if key in template_context:
                candidates.append(template_context.get(key))

        # Generation metadata often carries donor-specific identifiers
        metadata = template_context.get("generation_metadata") or {}
        for key in (
            "device_serial_number_int",
            "device_serial_number",
            "dsn",
            "dsn_value",
        ):
            if key in metadata:
                candidates.append(metadata.get(key))

        # Config space (raw or processed) may expose DSN values
        config_space = (
            template_context.get("config_space")
            or template_context.get("config_space_data")
            or {}
        )
        for key in (
            "device_serial_number",
            "device_serial",
            "dsn",
            "dsn_value",
        ):
            if isinstance(config_space, dict) and key in config_space:
                candidates.append(config_space.get(key))

        # Extended capabilities frequently store DSN as hi/lo words
        extended_caps = {}
        if isinstance(config_space, dict):
            extended_caps = config_space.get("extended_capabilities") or {}

        for source in (
            template_context,
            metadata,
            config_space if isinstance(config_space, dict) else {},
            extended_caps if isinstance(extended_caps, dict) else {},
        ):
            if not isinstance(source, dict):
                continue

            hi = source.get("dsn_hi") or source.get("device_serial_hi")
            lo = source.get("dsn_lo") or source.get("device_serial_lo")
            if hi is not None and lo is not None:
                try:
                    hi_int = self._safe_hex_to_int(hi)
                    lo_int = self._safe_hex_to_int(lo)
                    return (hi_int << 32) | (lo_int & 0xFFFFFFFF)
                except Exception as e:
                    log_error_safe(
                        self.logger,
                        safe_format(
                            "Failed to parse DSN hi/lo parts: {error}", error=str(e)
                        ),
                        prefix=self.prefix,
                    )
                    pass

            composite = source.get("device_serial_number")
            if isinstance(composite, dict):
                hi_val = composite.get("hi") or composite.get("dsn_hi")
                lo_val = composite.get("lo") or composite.get("dsn_lo")
                value = composite.get("value")
                if hi_val is not None and lo_val is not None:
                    try:
                        hi_int = self._safe_hex_to_int(hi_val)
                        lo_int = self._safe_hex_to_int(lo_val)
                        return (hi_int << 32) | (lo_int & 0xFFFFFFFF)
                    except Exception as e:
                        log_error_safe(
                            self.logger,
                            safe_format(
                                "Failed to parse DSN hi/lo parts: {error}", error=str(e)
                            ),
                            prefix=self.prefix,
                        )
                        pass
                if value is not None:
                    candidates.append(value)

        for candidate in candidates:
            if candidate is None:
                continue
            try:
                return self._safe_hex_to_int(candidate)
            except Exception:
                continue

        return 0

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
                safe_format("Failed to build power context: {e}", e=str(e)),
                prefix=self.prefix,
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
                prefix=self.prefix,
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
                prefix=self.prefix,
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
        context["enable_clock_crossing"] = SV_CONSTANTS.DEFAULT_ENABLE_CLOCK_CROSSING
        context["enable_performance_counters"] = (
            SV_CONSTANTS.DEFAULT_ENABLE_PERF_COUNTERS
        )
        context["enable_error_detection"] = SV_CONSTANTS.DEFAULT_ENABLE_ERROR_DETECTION
        context["enable_custom_config"] = SV_CONSTANTS.DEFAULT_ENABLE_CUSTOM_CONFIG

        # Device info object
        context["device"] = TemplateObject(
            {
                "msi_vectors": int(
                    context.get("msi_vectors", SV_CONSTANTS.DEFAULT_MSI_VECTORS)
                ),
                "num_sources": int(
                    context.get("num_sources", SV_CONSTANTS.DEFAULT_NUM_SOURCES)
                ),
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

    # no-dd-sa
    def _add_compatibility_fields(
        self, context: Dict[str, Any], template_context: Dict[str, Any]
    ) -> None:
        """Add fields for backward compatibility."""
        # MSI-X related fields
        msix_config = context.get("msix_config", {})
        context["NUM_MSIX"] = self._safe_get_int(
            msix_config, "num_vectors", SV_CONSTANTS.DEFAULT_NUM_MSIX
        )
        context["msix_table_bir"] = self._safe_get_int(
            msix_config, "table_bir", SV_CONSTANTS.DEFAULT_MSIX_TABLE_BIR
        )
        context["msix_table_offset"] = self._safe_get_int(
            msix_config, "table_offset", SV_CONSTANTS.DEFAULT_MSIX_TABLE_OFFSET
        )
        context["msix_pba_bir"] = self._safe_get_int(
            msix_config, "pba_bir", SV_CONSTANTS.DEFAULT_MSIX_PBA_BIR
        )
        context["msix_pba_offset"] = self._safe_get_int(
            msix_config, "pba_offset", SV_CONSTANTS.DEFAULT_MSIX_PBA_OFFSET
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
        context["table_offset_bir"] = context["msix_table_bir"] | self._align_down(
            context["msix_table_offset"], SV_CONSTANTS.MSIX_ALIGNMENT_BYTES
        )
        context["pba_offset_bir"] = context["msix_pba_bir"] | self._align_down(
            context["msix_pba_offset"], SV_CONSTANTS.MSIX_ALIGNMENT_BYTES
        )

        # Other compatibility fields
        context["msi_vectors"] = int(
            template_context.get("msi_vectors", SV_CONSTANTS.DEFAULT_MSI_VECTORS)
        )
        context["max_payload_size"] = int(
            template_context.get("max_payload_size", SV_CONSTANTS.DEFAULT_MPS_BYTES)
        )
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
            derived_mps = int(
                int(context.get("max_payload_size", 0))
                or (int(getattr(pcfg, "max_payload_size", 0)) if pcfg else 0)
                or SV_CONSTANTS.DEFAULT_MPS_BYTES
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
        aperture_size = self._safe_get_int(
            bar_config, "aperture_size", SV_CONSTANTS.DEFAULT_BAR_APERTURE_SIZE
        )
        context.setdefault("BAR_APERTURE_SIZE", aperture_size)

        # Default to enabling byte enables unless explicitly disabled.
        # Legacy controller relies on this flag for write strobes, so fall
        # back to True when donor data omits the override.
        use_byte_enables = bool(
            bar_config.get("use_byte_enables", SV_CONSTANTS.DEFAULT_USE_BYTE_ENABLES)
        )
        context.setdefault("USE_BYTE_ENABLES", use_byte_enables)

        # Derive BAR window layout in 4KB pages. Clamp to avoid negative values
        # if the aperture is undersized in test fixtures.
        aperture_pages = max(1, aperture_size // SV_CONSTANTS.PAGE_SIZE_BYTES)
        config_shadow_page = max(
            0, aperture_pages - SV_CONSTANTS.CONFIG_SHADOW_PAGE_FROM_END
        )
        custom_window_page = max(
            0, aperture_pages - SV_CONSTANTS.CUSTOM_WINDOW_PAGE_FROM_END
        )

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

        # ------------------------------------------------------------------
        # Config-space shadow template compatibility
        # - Provide CONFIG_SPACE_SIZE (bytes) with a safe default (256)
        # - Ensure OVERLAY_MAP exists (mapping of reg->mask) with {} default
        # - Provide OVERLAY_ENTRIES as an integer count (not a list)
        # - Publish uppercase aliases for extended capability pointers
        #   (defaults to 0x100 = 256) without fabricating donor-unique values
        # ------------------------------------------------------------------

        # avoids strict-undefined errors in cfg_shadow.sv.j2.
        context.setdefault("DUAL_PORT", SV_CONSTANTS.DEFAULT_DUAL_PORT)

        # Derive CONFIG_SPACE_SIZE from explicit config_space_data when provided,
        # otherwise use a conservative default of 256 bytes.
        cfg_space_size = CFG_SPACE_DEFAULT_SIZE
        try:
            cs_data = template_context.get("config_space_data") or {}
            if isinstance(cs_data, dict):
                data_blob = cs_data.get("data")
                if isinstance(data_blob, (bytes, bytearray)):
                    cfg_space_size = max(cfg_space_size, len(data_blob))
                elif isinstance(data_blob, list):
                    # List of byte values
                    cfg_space_size = max(cfg_space_size, len(data_blob))
        except Exception:
            # Keep safe default on any parsing issue
            pass

        context.setdefault("CONFIG_SPACE_SIZE", int(cfg_space_size))

        # Overlay defaults: prefer an explicit map if provided by callers; avoid
        # making up dynamic, donor-unique values here.
        overlay_map = template_context.get("OVERLAY_MAP") or {}
        # Normalize non-dict/sequence inputs to an empty map
        if not isinstance(overlay_map, (dict, list, tuple)):
            overlay_map = {}

        # Count entries for the SV parameter. For sequences, treat each item as
        # an entry; for mappings, use number of keys. Fallback to 0.
        if isinstance(overlay_map, dict):
            overlay_entries = len(overlay_map.keys())
        elif isinstance(overlay_map, (list, tuple)):
            overlay_entries = len(overlay_map)
        else:
            overlay_entries = 0

        # Only set if not already a valid integer to avoid clobbering explicit test inputs
        if not isinstance(context.get("OVERLAY_ENTRIES"), int):
            context["OVERLAY_ENTRIES"] = int(overlay_entries)
        context.setdefault(
            "OVERLAY_MAP",
            overlay_map if isinstance(overlay_map, (dict, list, tuple)) else {},
        )

        def _coerce_toggle(value: Union[str, int, bool, None], default: int) -> int:
            if value is None:
                return default
            if isinstance(value, str):
                normalized = value.strip().lower()
                if normalized in {"0", "false", "off", "no"}:
                    return 0
                if normalized in {"1", "true", "on", "yes"}:
                    return 1
            try:
                return int(bool(value))
            except Exception:
                return default

        sparse_toggle = template_context.get("ENABLE_SPARSE_MAP")
        sparse_default = _coerce_toggle(
            sparse_toggle, int(context.get("OVERLAY_ENTRIES", 0) > 0)
        )
        context.setdefault("ENABLE_SPARSE_MAP", sparse_default)

        bit_toggle = template_context.get("ENABLE_BIT_TYPES")
        bit_default = _coerce_toggle(bit_toggle, 1)
        context.setdefault("ENABLE_BIT_TYPES", bit_default)

        hash_size = template_context.get("HASH_TABLE_SIZE")
        if not isinstance(hash_size, int) or hash_size <= 0:
            hash_size = compute_sparse_hash_table_size(
                int(context.get("OVERLAY_ENTRIES", 0))
            )
        context.setdefault("HASH_TABLE_SIZE", hash_size)

        # Uppercase aliases for extended capability pointers used by cfg_shadow
        try:
            dc = context.get("device_config", {})
            # device_config may be a TemplateObject/dict; getattr will work with TemplateObject
            ext_ptr = (
                int(getattr(dc, "ext_cfg_cap_ptr", EXT_CAP_PTR_DEFAULT))
                if not isinstance(dc, dict)
                else int(dc.get("ext_cfg_cap_ptr", EXT_CAP_PTR_DEFAULT))
            )
            ext_xp_ptr = (
                int(getattr(dc, "ext_cfg_xp_cap_ptr", EXT_CAP_PTR_DEFAULT))
                if not isinstance(dc, dict)
                else int(dc.get("ext_cfg_xp_cap_ptr", EXT_CAP_PTR_DEFAULT))
            )
            context.setdefault("EXT_CFG_CAP_PTR", ext_ptr)
            context.setdefault("EXT_CFG_XP_CAP_PTR", ext_xp_ptr)
        except Exception:
            # Defaults already cover typical cases; do not fail context build
            context.setdefault("EXT_CFG_CAP_PTR", EXT_CAP_PTR_DEFAULT)
            context.setdefault("EXT_CFG_XP_CAP_PTR", EXT_CAP_PTR_DEFAULT)

        # Normalize out-of-range sentinel so templates can drive error markers dynamically
        sentinel_candidates: List[Any] = []
        sentinel_candidates.append(template_context.get("OUT_OF_RANGE_SENTINEL"))
        sentinel_candidates.append(template_context.get("out_of_range_sentinel"))

        cfg_shadow_ctx = template_context.get("cfg_shadow")
        if isinstance(cfg_shadow_ctx, dict):
            sentinel_candidates.append(cfg_shadow_ctx.get("OUT_OF_RANGE_SENTINEL"))
            sentinel_candidates.append(cfg_shadow_ctx.get("out_of_range_sentinel"))

        sentinel_value = next(
            (value for value in sentinel_candidates if value not in (None, "")),
            None,
        )

        fallback_sentinel = self.constants.DEFAULT_OUT_OF_RANGE_SENTINEL
        if sentinel_value is None:
            sentinel_value = fallback_sentinel

        normalized_sentinel = IdentifierNormalizer.normalize_hex(sentinel_value, 8)

        if normalized_sentinel == "00000000" and str(
            sentinel_value
        ).strip().lower() not in {
            "0",
            "0x0",
            "00000000",
        }:
            log_warning_safe(
                self.logger,
                safe_format(
                    "Invalid OUT_OF_RANGE_SENTINEL override '{value}'; defaulting to {default}",
                    value=sentinel_value,
                    default=fallback_sentinel,
                ),
                prefix=self.prefix,
            )
            normalized_sentinel = IdentifierNormalizer.normalize_hex(
                fallback_sentinel, 8
            )

        context.setdefault("OUT_OF_RANGE_SENTINEL", normalized_sentinel.upper())

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
                    raise ValueError(
                        safe_format(
                            "Missing transition cycle field: {field}", field=field
                        )
                    )
                tc_dict[field] = getattr(transition_cycles, field)
            return tc_dict
        elif isinstance(transition_cycles, dict):
            # Dictionary
            missing = [f for f in required_fields if f not in transition_cycles]
            if missing:
                raise ValueError(
                    safe_format(
                        "Missing transition cycle fields: {fields}",
                        fields=", ".join(missing),
                    )
                )
            return transition_cycles
        else:
            raise ValueError(
                safe_format(
                    "Invalid transition_cycles type: {type_name}",
                    type_name=type(transition_cycles).__name__,
                )
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
                    "Missing required {config_name} fields: {fields}",
                    config_name=config_name,
                    fields=", ".join(missing),
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

    def _align_down(self, value: int, alignment: int) -> int:
        """Align value down to the given power-of-two alignment."""
        try:
            if alignment <= 0:
                return int(value)
            return int(value) & ~(int(alignment) - 1)
        except Exception:
            return int(value) if isinstance(value, int) else 0


# Import at the end to avoid circular dependency
from src.device_clone.device_config import DeviceClass, DeviceType
