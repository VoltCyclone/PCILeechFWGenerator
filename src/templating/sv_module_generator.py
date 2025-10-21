"""Module generator for SystemVerilog code generation - Config-only architecture."""

import logging
from functools import lru_cache
from typing import Any, Dict, List, Optional, Tuple

from src.exceptions import PCILeechGenerationError
from src.string_utils import (
    generate_sv_header_comment,
    log_debug_safe,
    log_error_safe,
    log_info_safe,
    log_warning_safe,
    safe_format,
)
from src.utils.attribute_access import get_attr_or_raise, has_attr, safe_get_attr

from .sv_constants import SV_CONSTANTS, SV_TEMPLATES, SV_VALIDATION
from .template_renderer import TemplateRenderer, TemplateRenderError


class SVModuleGenerator:
    """Handles SystemVerilog configuration generation with config-only architecture."""

    def __init__(
        self,
        renderer: TemplateRenderer,
        logger: logging.Logger,
        prefix: str = "SV_GEN",
    ):
        """Initialize the module generator.

        Args:
            renderer: Template renderer instance
            logger: Logger to use for output
            prefix: Log prefix for all messages from this generator
        """
        self.renderer = renderer
        self.logger = logger
        self.prefix = prefix
        self.templates = SV_TEMPLATES
        self.messages = SV_VALIDATION.ERROR_MESSAGES
        self._module_cache = {}

    def generate_config_modules(
        self, context: Dict[str, Any], behavior_profile: Optional[Any] = None
    ) -> Dict[str, str]:
        """
        Generate device configuration modules for PCILeech integration.

        Args:
            context: Enhanced template context
            behavior_profile: Optional behavior profile

        Returns:
            Dictionary of module name to generated code
        """
        log_info_safe(
            self.logger,
            "Generating device configuration modules",
            prefix=self.prefix,
        )

        modules = {}

        try:
            # Generate core configuration modules
            self._generate_config_modules(context, modules)

            # Generate MSI-X configuration if needed
            self._generate_msix_config_if_needed(context, modules)

            log_info_safe(
                self.logger,
                "Generated {count} configuration modules",
                prefix=self.prefix,
                count=len(modules),
            )

            return modules

        except Exception as e:
            log_error_safe(
                self.logger,
                safe_format(
                    "Configuration module generation failed: {error}",
                    error=str(e),
                ),
                prefix=self.prefix,
            )
            raise PCILeechGenerationError(
                f"Configuration module generation failed: {str(e)}"
            ) from e

    def generate_pcileech_modules(
        self, context: Dict[str, Any], behavior_profile: Optional[Any] = None
    ) -> Dict[str, str]:
        """
        Legacy method redirects to config-only generation.
        
        This maintains API compatibility while implementing config-only architecture.
        """
        return self.generate_config_modules(context, behavior_profile)

    def _generate_config_modules(
        self, context: Dict[str, Any], modules: Dict[str, str]
    ) -> None:
        """Generate core configuration modules."""
        # Ensure header is in context for templates that need it
        if "header" not in context:
            context = dict(context)  # Make a copy to avoid modifying original
            context["header"] = generate_sv_header_comment(
                "PCILeech Device Configuration",
                generator="SVModuleGenerator",
                features="Device configuration parameters",
            )

        # Require donor-bound device identifiers; don't fabricate or allow None.
        device_cfg = context.get("device_config") or {}
        device_obj = context.get("device") or {}
        vid = device_obj.get("vendor_id") or device_cfg.get("vendor_id")
        did = device_obj.get("device_id") or device_cfg.get("device_id")

        if not vid or not did:
            error_msg = safe_format(
                "Missing required device identifiers: vendor_id={vid}, device_id={did}",
                vid=str(vid),
                did=str(did),
            )
            log_error_safe(
                self.logger,
                error_msg,
                prefix=self.prefix,
            )
            raise TemplateRenderError(error_msg)

        # Normalize device context
        if (
            "device" not in context
            or context.get("device") is None
            or context.get("device", {}).get("vendor_id") != vid
            or context.get("device", {}).get("device_id") != did
        ):
            context = dict(context)  # Make a shallow copy before modification
            context["device"] = {"vendor_id": vid, "device_id": did}

        # Device configuration module (pure parameters)
        log_debug_safe(
            self.logger,
            "Rendering configuration module: device_config.sv",
            prefix=self.prefix,
        )
        modules["device_config"] = self.renderer.render_template(
            self.templates.DEVICE_CONFIG, context
        )

        # Configuration space COE file for memory initialization
        log_debug_safe(
            self.logger, 
            "Rendering configuration: pcileech_cfgspace.coe", 
            prefix=self.prefix
        )
        modules["pcileech_cfgspace.coe"] = self.renderer.render_template(
            self.templates.PCILEECH_CFGSPACE, context
        )

    def _generate_msix_config_if_needed(
        self, context: Dict[str, Any], modules: Dict[str, str]
    ) -> None:
        """Generate MSI-X configuration if device supports it."""
        msix_config = safe_get_attr(context, "msix_config", {})
        if not msix_config:
            return

        # Check if MSI-X is supported
        is_supported = safe_get_attr(msix_config, "is_supported", False)
        num_vectors = safe_get_attr(msix_config, "num_vectors", 0)

        if not is_supported and num_vectors == 0:
            return

        log_debug_safe(
            self.logger,
            safe_format(
                "Generating MSI-X configuration (vectors={vectors})",
                vectors=num_vectors,
            ),
            prefix=self.prefix,
        )

        # Generate MSI-X table initialization data
        self._generate_msix_table_init(context, modules)

    def _generate_msix_table_init(
        self, context: Dict[str, Any], modules: Dict[str, str]
    ) -> None:
        """Generate MSI-X table initialization data."""
        msix_data = safe_get_attr(context, "msix_data")
        if not msix_data:
            log_warning_safe(
                self.logger,
                "MSI-X supported but no table data provided",
                prefix=self.prefix,
            )
            return

        # If hex data is provided, use it directly
        table_init_hex = safe_get_attr(msix_data, "table_init_hex")
        if table_init_hex:
            modules["msix_table_init.hex"] = table_init_hex
            log_info_safe(
                self.logger,
                safe_format(
                    "Generated MSI-X table init (size={size} bytes)",
                    size=len(table_init_hex) // 2,
                ),
                prefix=self.prefix,
            )

    @lru_cache(maxsize=128)
    def get_config_parameters(
        self, device_type: str, device_class: str
    ) -> Dict[str, Any]:
        """
        Get device-specific configuration parameters.

        Args:
            device_type: Device type value
            device_class: Device class value

        Returns:
            Dictionary of configuration parameters
        """
        # Base configuration parameters
        config = {
            "device_type": device_type,
            "device_class": device_class,
        }

        # Add device-specific defaults based on type
        if device_type == "GPU":
            config.update({
                "default_bar_sizes": [0x1000000, 0x8000000, 0x2000000, 0, 0, 0],
                "supports_msix": True,
                "default_msix_vectors": 16,
            })
        elif device_type == "NIC":
            config.update({
                "default_bar_sizes": [0x100000, 0x100000, 0, 0, 0, 0],
                "supports_msix": True,
                "default_msix_vectors": 32,
            })
        elif device_type == "STORAGE":
            config.update({
                "default_bar_sizes": [0x4000, 0, 0, 0, 0, 0],
                "supports_msix": True,
                "default_msix_vectors": 8,
            })
        else:
            config.update({
                "default_bar_sizes": [0x1000, 0, 0, 0, 0, 0],
                "supports_msix": False,
                "default_msix_vectors": 0,
            })

        return config
