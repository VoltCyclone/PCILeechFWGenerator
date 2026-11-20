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
from pathlib import Path
from typing import Any, Dict, Optional

from pcileechfwgenerator.device_clone.coe_word_order import logical_coe_to_bram
from pcileechfwgenerator.device_clone.msix_layout import MsixLayoutError
from pcileechfwgenerator.device_clone.msix_subsystem import MsixSubsystemPlan
from pcileechfwgenerator.device_clone.served_bar import (
    register_bar_config,
    served_bars_from_config,
)
from pcileechfwgenerator.exceptions import PCILeechGenerationError
from pcileechfwgenerator.log_config import get_logger
from pcileechfwgenerator.string_utils import (
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

logger = get_logger(__name__)


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

    def generate_config_space_overlay(self, context: Dict[str, Any]) -> Dict[str, str]:
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
            config_space_coe = self._generate_config_space_coe(context_with_header)
            overlays["pcileech_cfgspace.coe"] = config_space_coe

            # Generate write mask .coe file if needed
            if self._should_generate_writemask(context_with_header):
                writemask_coe = self._generate_writemask_coe(
                    context_with_header, config_space_coe=config_space_coe
                )
                overlays["pcileech_cfgspace_writemask.coe"] = writemask_coe

            # Generate device-specific BAR implementation if BAR models available
            overlays.update(self._generate_bar_implementations(context_with_header))

            # Generate device-aware BAR controller that uses device-specific impl
            bar_controller_sv = self._generate_bar_controller(context_with_header)
            if bar_controller_sv:
                overlays["pcileech_tlps128_bar_controller.sv"] = bar_controller_sv
                if self._msix_rtl_parameters(context_with_header):
                    overlays.update(self._msix_sources())

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
            context, self.logger, prefix=self.prefix, require_config_space=True
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

    def _generate_config_space_coe(self, context: Dict[str, Any]) -> str:
        """Generate the configuration space .coe file."""
        context = {**context, "bar_config": self._bar_config(context)}
        log_debug_safe(
            self.logger,
            "Rendering pcileech_cfgspace.coe template",
            prefix=self.prefix,
        )

        try:
            template_path = "sv/pcileech_cfgspace.coe.j2"
            logical = self.renderer.render_template(template_path, context)
            # The template is authored in logical PCI DWORD values; the shadow
            # BRAM stores config byte +0 in bits [31:24]. Convert once, here.
            content = logical_coe_to_bram(logical)

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

    def _config_space_to_bytes(self, config_space: Any) -> bytes:
        """Normalize a donor payload, padding only valid, aligned short images.

        Structured inputs may supply raw_data as hex or data as bytes/hex.
        If both fields exist, their decoded bytes must agree before padding.
        Invalid or missing payloads raise a logged TemplateRenderError.
        """
        try:
            if isinstance(config_space, (bytes, bytearray)):
                payload = bytes(config_space)
            else:
                missing = object()
                if isinstance(config_space, dict):
                    raw_data = config_space.get("raw_data", missing)
                    data = config_space.get("data", missing)
                else:
                    raw_data = getattr(config_space, "raw_data", missing)
                    data = getattr(config_space, "data", missing)

                decoded = []
                for name, value in (("raw_data", raw_data), ("data", data)):
                    if value is missing:
                        continue
                    if isinstance(value, str):
                        hex_data = value.strip()
                        if hex_data.lower().startswith("0x"):
                            hex_data = hex_data[2:]
                        try:
                            decoded.append(bytes.fromhex(hex_data))
                        except ValueError as e:
                            raise ValueError(
                                f"config_space.{name} must contain valid "
                                "even-length hex"
                            ) from e
                    elif name == "data" and isinstance(value, (bytes, bytearray)):
                        decoded.append(bytes(value))
                    else:
                        raise ValueError(
                            f"config_space.{name} has an unsupported payload type"
                        )

                if not decoded:
                    raise ValueError(
                        "config_space is missing a raw_data or data payload"
                    )
                if len(decoded) == 2 and decoded[0] != decoded[1]:
                    raise ValueError(
                        "config_space.raw_data and data conflict (ambiguous)"
                    )
                payload = decoded[0]

            if not payload:
                raise ValueError("config_space payload must not be empty")
            if len(payload) < 64:
                raise ValueError("config_space payload must contain at least 64 bytes")
            if len(payload) > 4096:
                raise ValueError("config_space payload exceeds 4096 bytes")
            if len(payload) % 4:
                raise ValueError("config_space payload length must be a multiple of 4")
            return payload.ljust(4096, b"\x00")
        except ValueError as e:
            error_msg = str(e)
            log_error_safe(self.logger, error_msg, prefix=self.prefix)
            raise TemplateRenderError(error_msg) from e

    def _generate_writemask_coe(
        self, context: Dict[str, Any], config_space_coe: Optional[str] = None
    ) -> str:
        """Generate the write mask .coe file using device-specific data.

        This method generates writemask based on actual donor device
        capabilities and configuration, not hardcoded templates.

        Args:
            context: Template context with config_space and device config
            config_space_coe: Final rendered configuration image, used verbatim
                when provided. Otherwise normalize context's config_space.

        Returns:
            Generated writemask COE content

        Raises:
            TemplateRenderError: If required context data is missing
        """
        from pathlib import Path
        from tempfile import TemporaryDirectory

        from pcileechfwgenerator.device_clone.writemask_generator import (
            WritemaskGenerator,
        )

        log_info_safe(
            self.logger,
            "Generating device-specific write mask overlay",
            prefix=self.prefix,
        )

        if config_space_coe is None:
            cfg_bytes = self._config_space_to_bytes(context.get("config_space"))
            # A raw donor image holds assigned BAR/ROM base addresses, not the
            # rendered size encodings the BAR policy derives from; zero them so
            # those slots stay read-only rather than exposing a wrong aperture.
            cfg_bytes = (
                cfg_bytes[:0x10]
                + bytes(0x18)
                + cfg_bytes[0x28:0x30]
                + bytes(4)
                + cfg_bytes[0x34:]
            )
            dwords = [
                f"{int.from_bytes(cfg_bytes[i:i+4], byteorder='little'):08x}"
                for i in range(0, len(cfg_bytes), 4)
            ]
            config_space_coe = (
                "; PCILeech Configuration Space\n"
                "; Temporary file for writemask generation\n"
                "memory_initialization_radix=16;\n"
                "memory_initialization_vector=\n"
                + ",\n".join(
                    ",".join(dwords[i : i + 4]) for i in range(0, len(dwords), 4)
                )
                + ";\n"
            )

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
            # Use a TemporaryDirectory so both temp files are guaranteed to be
            # cleaned up even if any step (including writes) raises.
            with TemporaryDirectory(prefix="pcileech_writemask_") as tmpdir:
                cfg_temp_path = Path(tmpdir) / "cfgspace.coe"
                wm_temp_path = Path(tmpdir) / "writemask.coe"

                cfg_temp_path.write_text(config_space_coe, encoding="utf-8")

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

    @staticmethod
    def _plain(value: Any) -> Any:
        """Recursively convert TemplateObject/mapping views to plain containers."""
        if isinstance(value, (str, bytes, int, float, bool)) or value is None:
            return value
        if hasattr(value, "items") and callable(value.items):
            return {k: SVOverlayGenerator._plain(v) for k, v in value.items()}
        if isinstance(value, (list, tuple)):
            return [SVOverlayGenerator._plain(v) for v in value]
        return value

    def _bar_config(self, context: Any) -> Dict[str, Any]:
        """Resolve compatibility keys once from a validated served descriptor."""
        return register_bar_config(self._plain(context.get("bar_config")))

    def _served_bar_index(self, context: Any) -> int:
        """Resolved served BAR index shared by the implementation and controller.

        Explicit served descriptors are authoritative. Scalar-only contexts
        retain ``served_bar_index`` and its historical BAR0 default;
        ``primary_bar`` is a list position, never a donor BAR number.
        """
        bar_config = self._bar_config(context)
        value = (
            bar_config.get("served_bar_index", 0) if isinstance(bar_config, dict) else 0
        )
        try:
            index = int(value)
        except (TypeError, ValueError):
            raise PCILeechGenerationError(
                "served_bar_index is not an integer"
            ) from None
        if not 0 <= index <= 5:
            raise PCILeechGenerationError("served_bar_index outside BAR0-BAR5")
        return index

    def _select_served_bar_model(self, context: Any) -> Optional[Any]:
        """The one decision both the implementation and controller use.

        Returns the served BAR's non-empty learned model, or None when there is
        none (absent collection, no entry for the served BAR, or no registers).
        Never borrows another BAR's map. Invalid models raise.

        Accepts plain dicts and TemplateObject-normalized contexts alike;
        selection never relies on object truthiness.
        """
        from pcileechfwgenerator.device_clone.bar_model_loader import (
            BarModel,
            deserialize_bar_model,
        )

        bar_config = self._bar_config(context)
        if not isinstance(bar_config, dict):
            bar_config = {}
        models = bar_config.get("bar_models") or self._plain(context.get("bar_models"))
        if not models:
            return None
        if not isinstance(models, dict):
            raise PCILeechGenerationError("BAR models must be indexed by BAR number")
        served = self._served_bar_index(context)
        data = models.get(str(served), models.get(served))
        if data is None:
            # TemplateObject stringifies integer keys; also accept int lookups.
            data = next((v for k, v in models.items() if str(k) == str(served)), None)
        if data is None:
            log_info_safe(
                self.logger,
                safe_format(
                    "No learned model for served BAR{idx}; using generic BAR",
                    idx=served,
                ),
                prefix=self.prefix,
            )
            return None
        try:
            if isinstance(data, dict):
                if "regs" not in data and "registers" in data:
                    # Template-format keys are decimal after context/JSON
                    # normalization; serialized `regs` keys are hexadecimal.
                    registers = {}
                    for key, value in data["registers"].items():
                        offset = (
                            int(key, 0)
                            if isinstance(key, str) and key.startswith("0x")
                            else int(key)
                        )
                        if offset in registers:
                            raise ValueError("Duplicate register offset")
                        registers[offset] = value
                    data = {"size": data["size"], "regs": registers}
                model = deserialize_bar_model(data)
            else:
                model = data
            if not isinstance(model, BarModel):
                raise ValueError("Invalid BAR model type")
        except (ValueError, TypeError, KeyError) as exc:
            raise PCILeechGenerationError(
                safe_format("Failed to generate BAR implementation: {error}", error=exc)
            ) from exc
        if not model.registers:
            log_info_safe(
                self.logger,
                safe_format(
                    "Learned model for served BAR{idx} has no registers; "
                    "using generic BAR",
                    idx=served,
                ),
                prefix=self.prefix,
            )
            return None
        return model

    def _served_bar_impl(self, context: Any) -> tuple[Optional[Any], str]:
        """What the served BAR slot instantiates, decided once for both sides.

        Returns ``(model, fidelity)``. A learned model is served as is. With no
        learned model the default is a read-only device implementation over
        the whole aperture (an empty register map: reads return zero, writes
        are ignored). Only an explicit ``bar_fallback == "generic_ram"``
        (``--writable-bar-fallback``) returns no model, so the controller
        instantiates the board's writable ``zerowrite4k``.
        """
        from pcileechfwgenerator.device_clone.bar_model_loader import BarModel
        from pcileechfwgenerator.device_clone.register_policy import (
            BAR_FALLBACK_WRITABLE,
            bar_fallback_mode,
        )

        model = self._select_served_bar_model(context)
        if model is not None:
            # The controller passes 32-bit addresses, so a BAR of up to 4 GiB
            # decodes exactly. Clamping below the aperture would drop offset
            # bits and alias the upper part of the BAR onto the registers.
            limit = 1 << 32
            if model.size > limit:
                # Registers above 4 GiB cannot be reached, so fail here, not
                # in the template.
                if any(o + r.width > limit for o, r in model.registers.items()):
                    raise PCILeechGenerationError(
                        "Learned BAR registers lie above the 4 GiB decode limit"
                    )
                # Offsets above 4 GiB are not visible to the controller and
                # alias every 4 GiB; that is a controller limit, said once.
                log_warning_safe(
                    self.logger,
                    "Served BAR is larger than 4 GiB; offsets above 4 GiB alias "
                    "onto the learned registers",
                    prefix="SV_GEN",
                )
                model = BarModel(size=limit, registers=dict(model.registers))
            return model, "learned"
        bar_config = self._bar_config(context)
        if not isinstance(bar_config, dict):
            bar_config = {}
        try:
            mode = bar_fallback_mode(bar_config)
        except ValueError as exc:
            raise PCILeechGenerationError(str(exc)) from exc
        if mode == BAR_FALLBACK_WRITABLE:
            return None, mode
        try:
            size = int(bar_config.get("aperture_size") or 4096)
        except (TypeError, ValueError):
            raise PCILeechGenerationError("aperture_size is not an integer") from None
        # Every offset reads zero, so the decode width changes nothing. Clamp
        # it to the 32-bit address the controller passes (wr_addr/rd_req_addr):
        # served BARs above 4 GiB (common on GPUs) must still build.
        return BarModel(size=min(size, 1 << 32), registers={}), mode

    def _generate_bar_implementation(self, context: Dict[str, Any]) -> Optional[str]:
        """Generate device-specific BAR implementation module from learned models.

        This method generates a SystemVerilog module implementing the donor device's
        BAR storage map from compiled register policy. Errors are fatal;
        None is returned only when the model collection is absent or empty.

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

        bar_model, fidelity = self._served_bar_impl(context)
        if bar_model is None:
            return None
        try:
            bar_impl_context = dict(context)
            bar_impl_context["bar_model"] = bar_model
            bar_impl_context["bar_model_readonly_fallback"] = fidelity != "learned"
            if "device_signature" not in bar_impl_context:
                bar_impl_context["device_signature"] = safe_format(
                    "{vendor}:{device}",
                    vendor=context.get("vendor_id", "unknown"),
                    device=context.get("device_id", "unknown"),
                )
            content = self.renderer.render_template(
                "sv/pcileech_bar_impl_device.sv.j2", bar_impl_context
            )
        except (ValueError, TypeError, KeyError, TemplateRenderError) as exc:
            raise PCILeechGenerationError(
                safe_format("Failed to generate BAR implementation: {error}", error=exc)
            ) from exc

        log_info_safe(
            self.logger,
            safe_format(
                "Generated BAR storage implementation "
                "({size} bytes, {nregs} registers)",
                size=len(content),
                nregs=len(bar_model.registers),
            ),
            prefix=self.prefix,
        )
        return content

    def _bar_context(self, context: Any, bar: Any) -> Dict[str, Any]:
        """Select one BAR's own model and aperture without borrowing another map."""
        selected = dict(context)
        config = self._bar_config(context)
        config.update(
            served_bar_index=bar.index,
            aperture_size=bar.size,
            served_is_64bit=bar.is_64bit,
        )
        config.pop("served_bars", None)
        selected["bar_config"] = config
        selected["device_bar_impl_module"] = f"pcileech_bar_impl_device_bar{bar.index}"
        return selected

    def _generate_bar_implementations(self, context: Any) -> Dict[str, str]:
        """Emit separate register models for two served BARs, keeping legacy names."""
        bars = served_bars_from_config(self._plain(context.get("bar_config")))
        if bars is None or len(bars) == 1:
            content = self._generate_bar_implementation(context)
            return {"pcileech_bar_impl_device.sv": content} if content else {}
        implementations = {}
        for bar in bars:
            selected = self._bar_context(context, bar)
            content = self._generate_bar_implementation(selected)
            if content:
                implementations[selected["device_bar_impl_module"] + ".sv"] = content
        return implementations

    # Static MSI-X sources the generated controller instantiates. They are
    # written next to it so they reach the same Vivado source set.
    MSIX_SOURCES = (
        "sv/msix/pcileech_msix_bar_router.sv",
        "sv/msix/pcileech_msix_tlp_tx.sv",
        "sv/vendor/verilog-pcie/pcie_msix.v",
    )

    def _msix_sources(self) -> Dict[str, str]:
        sources = {}
        for relative in self.MSIX_SOURCES:
            path = Path(self.renderer.template_dir) / relative
            sources[path.name] = path.read_text()
        return sources

    def _msix_rtl_parameters(self, context: Dict[str, Any]) -> Optional[Dict[str, int]]:
        """MSI-X router/core parameters when this build generates the subsystem.

        Read from the generation-time decision (``device_clone.msix_subsystem``)
        so the RTL and the hard IP's advertised capability always agree.
        """
        try:
            stored = context.get("msix_subsystem") if hasattr(context, "get") else None
            plan = MsixSubsystemPlan.from_context(
                {"msix_subsystem": self._plain(stored)}
            )
        except (MsixLayoutError, ValueError, TypeError):
            return None
        if not plan.generated or plan.layout is None:
            return None
        params = plan.rtl_parameters()
        params["bar_size"] = plan.layout.served_bar.size
        return params

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
            has_models = self._served_bar_impl(context)[0] is not None
            controller_context = dict(context)
            controller_context["bar_config"] = self._bar_config(context)
            # Must match _generate_bar_implementation exactly; the template
            # never re-derives this from (always-truthy) TemplateObjects.
            controller_context["device_bar_impl_selected"] = has_models
            controller_context["device_bar_served_index"] = self._served_bar_index(
                context
            )
            bars = served_bars_from_config(self._plain(context.get("bar_config")))
            if bars is not None and len(bars) > 1:
                slots = {}
                for bar in bars:
                    selected = self._bar_context(context, bar)
                    model, fidelity = self._served_bar_impl(selected)
                    slots[str(bar.index)] = {
                        **bar.model_dump(),
                        "module": (
                            selected["device_bar_impl_module"]
                            if model is not None
                            else "pcileech_bar_impl_zerowrite4k"
                        ),
                        "has_model": model is not None,
                        "fidelity": fidelity,
                    }
                controller_context["device_bar_slots"] = slots
            msix_rtl = self._msix_rtl_parameters(context)
            controller_context["msix_rtl"] = msix_rtl
            if msix_rtl and (controller_context.get("timing_config") or {}).get(
                "enable_latency_jitter"
            ):
                # The jitter emulator holds one read at a time, so back-to-back
                # reads (a table entry is four DWORDs) would lose completions.
                timing = dict(controller_context["timing_config"])
                timing["enable_latency_jitter"] = False
                controller_context["timing_config"] = timing
                log_warning_safe(
                    self.logger,
                    "BAR read latency jitter is disabled because the MSI-X "
                    "subsystem is generated",
                    prefix=self.prefix,
                )
            content = self.renderer.render_template(template_path, controller_context)

            impl_type = "device-specific" if has_models else "generic writable"

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
            raise PCILeechGenerationError(
                safe_format("Failed to generate BAR controller: {error}", error=e)
            ) from e


# Backward compatibility alias
SVModuleGenerator = SVOverlayGenerator


__all__ = [
    "SVOverlayGenerator",
    "SVModuleGenerator",  # Backward compatibility
]
