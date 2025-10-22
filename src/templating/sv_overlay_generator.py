#!/usr/bin/env python3
"""
Overlay generator for device-specific configuration files.

This module generates ONLY overlay files (configuration space .coe files)
that contain device-specific data to be integrated with upstream
pcileech-fpga sources.

NO SystemVerilog modules are generated - those come from
lib/voltcyclone-fpga.
"""

import logging
from typing import Any, Dict, Optional

from src.exceptions import PCILeechGenerationError
from src.string_utils import (
    generate_tcl_header_comment,
    log_debug_safe,
    log_error_safe,
    log_info_safe,
    safe_format,
)

from .sv_constants import SV_VALIDATION
from .template_renderer import TemplateRenderer, TemplateRenderError


class SVOverlayGenerator:
    """Generates device-specific overlay configuration files (.coe).

    This class is responsible for generating ONLY the configuration space
    overlay files that contain donor device-specific data. All SystemVerilog
    HDL modules are sourced from the upstream pcileech-fpga repository.
    """

    def __init__(
        self,
        renderer: TemplateRenderer,
        logger: logging.Logger,
        prefix: str = "OVERLAY_GEN",
    ):
        """Initialize the overlay generator.

        Args:
            renderer: Template renderer instance
            logger: Logger to use for output
            prefix: Log prefix for all messages from this generator
        """
        self.renderer = renderer
        self.logger = logger
        self.prefix = prefix
        self.messages = SV_VALIDATION.ERROR_MESSAGES

    def generate_config_space_overlay(
        self, context: Dict[str, Any]
    ) -> Dict[str, str]:
        """
        Generate configuration space overlay files.

        This generates the .coe file that contains the donor device's
        configuration space, which will be loaded by the upstream
        pcileech-fpga HDL modules.

        Args:
            context: Enhanced template context with donor device data

        Returns:
            Dictionary mapping filename to generated content
            Example: {"pcileech_cfgspace.coe": <content>}

        Raises:
            PCILeechGenerationError: If generation fails
        """
        log_info_safe(
            self.logger,
            "Generating configuration space overlay",
            prefix=self.prefix,
        )

        overlays = {}

        try:
            # Validate required context
            self._validate_context(context)

            # Ensure header is in context for template
            context_with_header = self._prepare_context(context)

            # Generate config space .coe file
            config_space_coe = self._generate_config_space_coe(
                context_with_header
            )
            overlays["pcileech_cfgspace.coe"] = config_space_coe

            # Generate write mask .coe file if needed
            if self._should_generate_writemask(context_with_header):
                writemask_coe = self._generate_writemask_coe(
                    context_with_header
                )
                overlays["pcileech_cfgspace_writemask.coe"] = writemask_coe

            log_info_safe(
                self.logger,
                safe_format(
                    "Generated {count} overlay files",
                    count=len(overlays),
                ),
                prefix=self.prefix,
            )

            return overlays

        except Exception as e:
            log_error_safe(
                self.logger,
                safe_format(
                    "Overlay generation failed: {error}",
                    error=str(e),
                ),
                prefix=self.prefix,
            )
            raise PCILeechGenerationError(
                f"Overlay generation failed: {str(e)}"
            ) from e

    def _validate_context(self, context: Dict[str, Any]) -> None:
        """Validate that required context fields are present."""
        # Require donor-bound device identifiers
        device_cfg = context.get("device_config") or {}
        device_obj = context.get("device") or {}

        vid = device_obj.get("vendor_id") or device_cfg.get("vendor_id")
        did = device_obj.get("device_id") or device_cfg.get("device_id")

        if not vid or not did:
            error_msg = safe_format(
                "Missing required device identifiers: "
                "vendor_id={vid}, device_id={did}",
                vid=str(vid) if vid else "MISSING",
                did=str(did) if did else "MISSING",
            )
            log_error_safe(
                self.logger,
                error_msg,
                prefix=self.prefix,
            )
            raise TemplateRenderError(error_msg)

        # Validate config_space data
        config_space = context.get("config_space")
        if not config_space:
            log_error_safe(
                self.logger,
                "Missing required config_space in context",
                prefix=self.prefix,
            )
            raise TemplateRenderError(
                "Missing required config_space in context"
            )

    def _prepare_context(self, context: Dict[str, Any]) -> Dict[str, Any]:
        """Prepare context with necessary defaults for template rendering."""
        # Make a copy to avoid modifying original
        prepared = dict(context)

        # Add header if not present
        if "header" not in prepared:
            prepared["header"] = generate_tcl_header_comment(
                "PCILeech Configuration Space Overlay",
                generator="SVOverlayGenerator",
                description="Device-specific configuration space data",
            )

        return prepared

    def _generate_config_space_coe(
        self, context: Dict[str, Any]
    ) -> str:
        """Generate the configuration space .coe file."""
        log_debug_safe(
            self.logger,
            "Rendering pcileech_cfgspace.coe template",
            prefix=self.prefix,
        )

        try:
            template_path = "sv/pcileech_cfgspace.coe.j2"
            content = self.renderer.render_template(template_path, context)

            log_debug_safe(
                self.logger,
                safe_format(
                    "Generated config space overlay ({size} bytes)",
                    size=len(content),
                ),
                prefix=self.prefix,
            )

            return content

        except TemplateRenderError as e:
            error_msg = safe_format(
                "Failed to render config space template: {error}",
                error=str(e),
            )
            log_error_safe(
                self.logger,
                error_msg,
                prefix=self.prefix,
            )
            raise TemplateRenderError(error_msg) from e

    def _should_generate_writemask(self, context: Dict[str, Any]) -> bool:
        """Determine if write mask overlay should be generated."""
        # Generate write mask if we have writemask data in context
        writemask_data = context.get("writemask_data")
        return writemask_data is not None and len(writemask_data) > 0

    def _generate_writemask_coe(self, context: Dict[str, Any]) -> str:
        """Generate the write mask .coe file."""
        log_debug_safe(
            self.logger,
            "Generating write mask overlay",
            prefix=self.prefix,
        )

        # For now, generate a simple write mask template
        # This protects critical config space registers from writes
        writemask_data = context.get("writemask_data", {})

        # COE file format for write mask
        lines = [
            "; PCILeech Configuration Space Write Mask",
            "; Generated by PCILeechFWGenerator",
            "; 1 = writable, 0 = read-only",
            "",
            "memory_initialization_radix=16;",
            "memory_initialization_vector=",
            "",
        ]

        # Generate write mask for 4KB config space (1024 DWORDs)
        # Default: protect VID/DID, class code, and capabilities
        for i in range(1024):
            if i == 0:
                # Protect VID/DID (offset 0x00)
                lines.append("00000000,")
            elif i == 1:
                # Protect command/status (offset 0x04)
                lines.append("FFFF0210,")  # Command writable, status RO
            elif i == 2:
                # Protect class code and revision (offset 0x08)
                lines.append("00000000,")
            elif i >= 4 and i <= 9:
                # BARs are writable (offsets 0x10-0x27)
                lines.append("FFFFFFFF,")
            elif i == 11:
                # Subsystem IDs are read-only (offset 0x2C)
                lines.append("00000000,")
            else:
                # Default: writable
                lines.append("FFFFFFFF," if i < 1023 else "FFFFFFFF;")

        return "\n".join(lines)

    # Backward compatibility aliases for existing code
    def generate_pcileech_modules(
        self, context: Dict[str, Any], behavior_profile: Optional[Any] = None
    ) -> Dict[str, str]:
        """Legacy method name - redirects to overlay generation."""
        log_debug_safe(
            self.logger,
            "generate_pcileech_modules called - redirecting to overlay",
            prefix=self.prefix,
        )
        return self.generate_config_space_overlay(context)


# Backward compatibility alias
SVModuleGenerator = SVOverlayGenerator


__all__ = [
    "SVOverlayGenerator",
    "SVModuleGenerator",  # Backward compatibility
]
