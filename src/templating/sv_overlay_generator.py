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
    log_warning_safe,
    safe_format,
)

from .sv_constants import SV_VALIDATION

from .template_renderer import TemplateRenderer, TemplateRenderError

from .validation_helpers import validate_template_context


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

            # Generate device-specific BAR implementation if BAR models available
            bar_impl_sv = self._generate_bar_implementation(context_with_header)
            if bar_impl_sv:
                overlays["pcileech_bar_impl_device.sv"] = bar_impl_sv

            # Generate device-aware BAR controller that uses device-specific impl
            bar_controller_sv = self._generate_bar_controller(context_with_header)
            if bar_controller_sv:
                overlays["pcileech_tlps128_bar_controller.sv"] = bar_controller_sv

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
                safe_format("Overlay generation failed: {error}", error=str(e))
            ) from e

    def _validate_context(self, context: Dict[str, Any]) -> None:
        """Validate that required context fields are present."""
        # Use centralized validation helper
        validate_template_context(
            context,
            self.logger,
            prefix=self.prefix,
            require_config_space=True
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
        # Always generate writemask for config space protection
        # Requires config_space to be present
        return context.get("config_space") is not None

    def _generate_writemask_coe(self, context: Dict[str, Any]) -> str:
        """Generate the write mask .coe file using device-specific data.
        
        This method generates writemask based on actual donor device
        capabilities and configuration, not hardcoded templates.
        
        Args:
            context: Template context with config_space and device config
            
        Returns:
            Generated writemask COE content
            
        Raises:
            TemplateRenderError: If required context data is missing
        """
        from pathlib import Path
        from tempfile import NamedTemporaryFile

        from src.device_clone.writemask_generator import WritemaskGenerator
        
        log_info_safe(
            self.logger,
            "Generating device-specific write mask overlay",
            prefix=self.prefix,
        )

        # Validate required context
        config_space = context.get("config_space")
        if not config_space:
            error_msg = "Missing required config_space for writemask generation"
            log_error_safe(
                self.logger,
                error_msg,
                prefix=self.prefix,
            )
            raise TemplateRenderError(error_msg)

        # Extract device configuration for MSI/MSI-X
        device_config = {
            "msi_config": context.get("msi_config", {}),
            "msix_config": context.get("msix_config", {}),
        }
        
        log_debug_safe(
            self.logger,
            safe_format(
                "Using device config: MSI={msi}, MSIX={msix}",
                msi=bool(device_config["msi_config"]),
                msix=bool(device_config["msix_config"]),
            ),
            prefix=self.prefix,
        )

        try:
            # Create temporary config space COE file
            with NamedTemporaryFile(
                mode='w', 
                suffix='.coe', 
                delete=False
            ) as cfg_temp:
                # Write config space in COE format
                cfg_temp.write("; PCILeech Configuration Space\n")
                cfg_temp.write("; Temporary file for writemask generation\n")
                cfg_temp.write("memory_initialization_radix=16;\n")
                cfg_temp.write("memory_initialization_vector=\n")
                
                # Convert config_space bytes to hex dwords
                if isinstance(config_space, (bytes, bytearray)):
                    cfg_bytes = bytes(config_space)
                elif isinstance(config_space, dict):
                    # Handle dict format from context
                    cfg_bytes = bytes(config_space.get("data", b""))
                else:
                    cfg_bytes = b""
                
                # Ensure we have 4KB of config space
                cfg_bytes = cfg_bytes[:4096].ljust(4096, b'\x00')
                
                # Write as dwords
                dwords = []
                for i in range(0, len(cfg_bytes), 4):
                    dword = int.from_bytes(cfg_bytes[i:i+4], byteorder='little')
                    dwords.append(f"{dword:08x}")
                
                # Write in groups of 4 per line
                for i in range(0, len(dwords), 4):
                    line_data = dwords[i:i+4]
                    cfg_temp.write(",".join(line_data))
                    if i + 4 < len(dwords):
                        cfg_temp.write(",\n")
                    else:
                        cfg_temp.write(";\n")
                
                cfg_temp_path = Path(cfg_temp.name)
            
            # Create temporary output file
            with NamedTemporaryFile(
                mode='w',
                suffix='.coe',
                delete=False
            ) as wm_temp:
                wm_temp_path = Path(wm_temp.name)
            
            try:
                # Generate writemask using WritemaskGenerator
                writemask_gen = WritemaskGenerator()
                writemask_gen.generate_writemask(
                    cfg_space_path=cfg_temp_path,
                    output_path=wm_temp_path,
                    device_config=device_config,
                )
                
                # Read generated writemask
                writemask_content = wm_temp_path.read_text(encoding="utf-8")
                
                log_info_safe(
                    self.logger,
                    safe_format(
                        "Generated writemask overlay ({size} bytes)",
                        size=len(writemask_content),
                    ),
                    prefix=self.prefix,
                )
                
                return writemask_content
                
            finally:
                # Clean up temporary files
                if cfg_temp_path.exists():
                    cfg_temp_path.unlink()
                if wm_temp_path.exists():
                    wm_temp_path.unlink()
                    
        except Exception as e:
            error_msg = safe_format(
                "Writemask generation failed: {error}",
                error=str(e),
            )
            log_error_safe(
                self.logger,
                error_msg,
                prefix=self.prefix,
            )
            raise TemplateRenderError(error_msg) from e

    # Backward compatibility aliases for existing code

    def generate_pcileech_modules(
        self, context: Dict[str, Any], behavior_profile: Optional[Any] = None
    ) -> Dict[str, str]:
        log_debug_safe(
            self.logger,
            "generate_pcileech_modules called - redirecting to overlay",
            prefix=self.prefix,
        )
        return self.generate_config_space_overlay(context)

    def _generate_bar_implementation(self, context: Dict[str, Any]) -> Optional[str]:
        """Generate device-specific BAR implementation module from learned models.
        
        This method generates a SystemVerilog module implementing the donor device's
        BAR register map based on learned behavior from MMIO tracing.
        
        Args:
            context: Template context with bar_config containing bar_models
            
        Returns:
            Generated BAR implementation SystemVerilog code, or None if no models
        """
        log_debug_safe(
            self.logger,
            "Checking for BAR models to generate device-specific implementation",
            prefix=self.prefix,
        )
        
        # Check if we have learned BAR models from the context
        bar_config = context.get("bar_config", {})
        
        # Try to get BAR models from various locations in context
        # Priority: bar_config.bar_models > bar_models > bars (w/ models)
        bar_models_data = None
        
        # Check bar_config for models
        if isinstance(bar_config, dict):
            bar_models_data = bar_config.get("bar_models")
        
        # Fallback to top-level bar_models
        if not bar_models_data:
            bar_models_data = context.get("bar_models")
        
        # If no models found, skip BAR implementation generation
        if not bar_models_data:
            log_debug_safe(
                self.logger,
                "No BAR models found - skipping device-specific BAR impl",
                prefix=self.prefix,
            )
            return None
        
        model_count = (
            len(bar_models_data) if isinstance(bar_models_data, dict) else 0
        )
        log_info_safe(
            self.logger,
            safe_format(
                "Generating device-specific BAR implementation from learned "
                "models (bar_count={count})",
                count=model_count,
            ),
            prefix=self.prefix,
        )
        
        try:
            # Find the primary BAR model (usually BAR0, but can be configured)
            primary_bar_idx = bar_config.get("primary_bar", 0)
            bar_model = None
            
            if isinstance(bar_models_data, dict):
                # Get the model for the primary BAR
                bar_model = (
                    bar_models_data.get(str(primary_bar_idx)) or 
                    bar_models_data.get(primary_bar_idx)
                )
                
                # If primary not found, try first available
                if not bar_model and bar_models_data:
                    first_key = next(iter(bar_models_data))
                    bar_model = bar_models_data[first_key]
                    log_debug_safe(
                        self.logger,
                        safe_format(
                            "Using BAR{idx} model "
                            "(primary BAR{primary} not found)",
                            idx=first_key,
                            primary=primary_bar_idx,
                        ),
                        prefix=self.prefix,
                    )
            
            if not bar_model:
                log_warning_safe(
                    self.logger,
                    "BAR models present but no usable model found",
                    prefix=self.prefix,
                )
                return None
            
            # Prepare template context with BAR model
            bar_impl_context = dict(context)
            
            # Transform serialized model format to template format
            # (serialization uses 'regs', template expects 'registers')
            if isinstance(bar_model, dict) and "regs" in bar_model:
                transformed_model = {"size": bar_model["size"]}
                # Convert hex string keys to integer offsets
                transformed_model["registers"] = {}
                for hex_key, reg_data in bar_model["regs"].items():
                    offset = int(hex_key, 16)
                    transformed_model["registers"][offset] = reg_data
                bar_impl_context["bar_model"] = transformed_model
            else:
                bar_impl_context["bar_model"] = bar_model
            
            # Ensure we have device_signature for the header
            if "device_signature" not in bar_impl_context:
                vendor_id = context.get("vendor_id", "0000")
                device_id = context.get("device_id", "0000")
                bar_impl_context["device_signature"] = safe_format(
                    "{vendor}:{device}",
                    vendor=vendor_id,
                    device=device_id,
                )
            
            # Render the BAR implementation template
            template_path = "sv/pcileech_bar_impl_device.sv.j2"
            content = self.renderer.render_template(template_path, bar_impl_context)
            
            # Calculate register count for logging
            if isinstance(bar_model, dict):
                nregs = len(bar_model.get("registers", {}))
            elif hasattr(bar_model, "registers"):
                nregs = len(getattr(bar_model, "registers", {}))
            else:
                nregs = 0
            
            log_info_safe(
                self.logger,
                safe_format(
                    "Generated device-specific BAR implementation "
                    "({size} bytes, {nregs} registers)",
                    size=len(content),
                    nregs=nregs,
                ),
                prefix=self.prefix,
            )
            
            return content
            
        except Exception as e:
            log_error_safe(
                self.logger,
                safe_format(
                    "Failed to generate BAR implementation: {error}",
                    error=str(e),
                ),
                prefix=self.prefix,
            )
            # Non-fatal - just skip BAR implementation generation
            return None

    def _generate_bar_controller(self, context: Dict[str, Any]) -> Optional[str]:
        """Generate device-aware BAR controller that uses device-specific impl.
        
        This method generates a templated version of pcileech_tlps128_bar_controller
        that automatically uses the device-specific BAR implementation when
        available.
        
        Args:
            context: Template context with bar_config
            
        Returns:
            Generated BAR controller SystemVerilog code, or None if not needed
        """
        log_debug_safe(
            self.logger,
            "Generating device-aware BAR controller",
            prefix=self.prefix,
        )
        
        try:
            # Always generate the BAR controller - it adapts based on context
            template_path = "sv/pcileech_tlps128_bar_controller.sv.j2"
            content = self.renderer.render_template(template_path, context)
            
            has_models = bool(
                context.get("bar_config", {}).get("bar_models") or
                context.get("bar_models")
            )
            
            impl_type = "device-specific" if has_models else "generic"
            
            log_info_safe(
                self.logger,
                safe_format(
                    "Generated BAR controller ({size} bytes, {impl_type} impl)",
                    size=len(content),
                    impl_type=impl_type,
                ),
                prefix=self.prefix,
            )
            
            return content
            
        except Exception as e:
            log_error_safe(
                self.logger,
                safe_format(
                    "Failed to generate BAR controller: {error}",
                    error=str(e),
                ),
                prefix=self.prefix,
            )
            # Non-fatal - use upstream pcileech-fpga version
            return None


# Backward compatibility alias
SVModuleGenerator = SVOverlayGenerator


__all__ = [
    "SVOverlayGenerator",
    "SVModuleGenerator",  # Backward compatibility
]
