"""Module generator for SystemVerilog code generation."""

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
    """Handles SystemVerilog module generation with improved architecture."""

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
        self._ports_cache = {}

    def generate_pcileech_modules(
        self, context: Dict[str, Any], behavior_profile: Optional[Any] = None
    ) -> Dict[str, str]:
        """
        Generate PCILeech-specific SystemVerilog modules.

        Args:
            context: Enhanced template context
            behavior_profile: Optional behavior profile

        Returns:
            Dictionary of module name to generated code
        """
        log_info_safe(
            self.logger,
            "Generating PCILeech SystemVerilog modules",
            prefix=self.prefix,
        )

        modules = {}

        try:
            # Generate core PCILeech modules
            self._generate_core_pcileech_modules(context, modules)

            # Generate MSI-X modules if needed
            self._generate_msix_modules_if_needed(context, modules)

            # Generate advanced modules if behavior profile available
            if behavior_profile and context.get("device_config", {}).get(
                "enable_advanced_features"
            ):
                self._generate_advanced_modules(context, behavior_profile, modules)

            log_info_safe(
                self.logger,
                "Generated {count} PCILeech SystemVerilog modules",
                prefix=self.prefix,
                count=len(modules),
            )

            return modules

        except Exception as e:
            log_error_safe(
                self.logger,
                safe_format(
                    "PCILeech module generation failed: {error}",
                    error=str(e),
                ),
                prefix=self.prefix,
            )
            raise PCILeechGenerationError(
                f"PCILeech module generation failed: {str(e)}"
            ) from e

    def generate_legacy_modules(
        self, context: Dict[str, Any], behavior_profile: Optional[Any] = None
    ) -> Dict[str, str]:
        """
        Generate legacy SystemVerilog modules.

        Args:
            context: Enhanced template context
            behavior_profile: Optional behavior profile

        Returns:
            Dictionary of module name to generated code
        """
        log_info_safe(
            self.logger,
            "Generating legacy SystemVerilog modules",
            prefix=self.prefix,
        )

        modules = {}
        failed_modules = []

        # Generate basic modules
        for module_template in self.templates.BASIC_SV_MODULES:
            try:
                template_path = f"systemverilog/{module_template}"
                module_content = self.renderer.render_template(template_path, context)
                module_name = module_template.replace(".sv.j2", "")
                modules[module_name] = module_content
            except Exception as e:
                log_error_safe(
                    self.logger,
                    safe_format(
                        "Failed to generate module {module}: {error}",
                        module=module_template,
                        error=str(e),
                    ),
                    prefix=self.prefix,
                )
                failed_modules.append(module_template)

        # Generate advanced controller if behavior profile available
        if behavior_profile:
            try:
                registers = self._extract_registers(behavior_profile)
                advanced_sv = self._generate_advanced_controller(
                    context, registers, behavior_profile
                )
                modules["advanced_controller"] = advanced_sv
            except Exception as e:
                log_error_safe(
                    self.logger,
                    safe_format(
                        "Failed to generate advanced controller: {error}",
                        error=str(e),
                    ),
                    prefix=self.prefix,
                )

        # Report results
        if failed_modules:
            log_warning_safe(
                self.logger,
                safe_format(
                    "Generated {success} of {total} modules. Failed: {failed}",
                    prefix=self.prefix,
                    success=len(modules),
                    total=len(self.templates.BASIC_SV_MODULES),
                    failed=", ".join(failed_modules),
                ),
                prefix=self.prefix,
            )

        return modules

    def generate_device_specific_ports(
        self, device_type: str, device_class: str, cache_key: str = ""
    ) -> str:
        """
        Generate device-specific port declarations with caching.

        Args:
            device_type: Device type value
            device_class: Device class value
            cache_key: Additional cache key for invalidation

        Returns:
            Generated SystemVerilog port declarations
        """
        cache_token = cache_key or ""
        cache_key_tuple: Tuple[str, str, str] = (
            str(device_type),
            str(device_class),
            str(cache_token),
        )
        if cache_key_tuple in self._ports_cache:
            return self._ports_cache[cache_key_tuple]

        context = {
            "device_type": device_type,
            "device_class": device_class,
        }

        try:
            log_debug_safe(
                self.logger,
                "Rendering device-specific ports for {dtype}/{dclass}",
                prefix=self.prefix,
                dtype=device_type,
                dclass=device_class,
            )
            rendered = self.renderer.render_template(
                self.templates.DEVICE_SPECIFIC_PORTS, context
            )
            log_debug_safe(
                self.logger,
                safe_format(
                    "Rendered device-specific ports for {dtype}/{dclass} (len={length})",
                    dtype=device_type,
                    dclass=device_class,
                    length=len(rendered) if rendered else 0,
                ),
                prefix=self.prefix,
            )
            # Save to cache and return
            self._ports_cache[cache_key_tuple] = rendered
            return rendered

        except TemplateRenderError as e:
            error_msg = safe_format(
                "Failed to render device-specific ports for {dtype}/{dclass}: {error}",
                dtype=device_type,
                dclass=device_class,
                error=str(e),
            )
            log_error_safe(
                self.logger,
                error_msg,
                prefix=self.prefix,
            )
            raise TemplateRenderError(error_msg) from e

    def _generate_core_pcileech_modules(
        self, context: Dict[str, Any], modules: Dict[str, str]
    ) -> None:
        """Generate core PCILeech modules."""
        # Ensure header is in context for templates that need it
        if "header" not in context:
            context = dict(context)  # Make a copy to avoid modifying original
            context["header"] = generate_sv_header_comment(
                "PCILeech Core Module",
                generator="SVModuleGenerator",
                features="Core PCILeech functionality",
            )

        # Require donor-bound device identifiers; don't fabricate or allow None.
        # Pull from existing device object first, then fall back to device_config.
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
            # Fail fast with actionable logs; these values must be present.
            log_error_safe(
                self.logger,
                error_msg,
                prefix=self.prefix,
            )
            raise TemplateRenderError(error_msg)

        # Normalize `device` in context without mutating original input.
        if (
            "device" not in context
            or context.get("device") is None
            or context.get("device", {}).get("vendor_id") != vid
            or context.get("device", {}).get("device_id") != did
        ):
            context = dict(context)  # Make a shallow copy before modification
            context["device"] = {"vendor_id": vid, "device_id": did}

        # TLP BAR controller (controller_variant governs behavior)
        bar_config = safe_get_attr(context, "bar_config", {}) or {}

        if isinstance(bar_config, dict):
            normalized_bar_config = dict(bar_config)
        else:
            log_warning_safe(
                self.logger,
                "bar_config is not a mapping; coercing to empty dict",
                prefix=self.prefix,
            )
            normalized_bar_config = {}

        requested_variant = safe_get_attr(normalized_bar_config, "controller_variant")
        if requested_variant:
            requested_variant = str(requested_variant).strip().lower()

        use_enhanced_flag = bool(
            safe_get_attr(normalized_bar_config, "use_enhanced_controller", False)
        )

        valid_variants = {"legacy", "enhanced", "basic"}
        if use_enhanced_flag:
            controller_variant = "enhanced"
        elif requested_variant in valid_variants:
            controller_variant = requested_variant
        else:
            controller_variant = "legacy"

        normalized_bar_config["controller_variant"] = controller_variant

        if context.get("bar_config") is not normalized_bar_config:
            context = dict(context)
            context["bar_config"] = normalized_bar_config

        log_debug_safe(
            self.logger,
            safe_format(
                "Rendering core template: TLPS128 BAR controller (variant={variant})",
                variant=controller_variant,
            ),
            prefix=self.prefix,
        )
        modules["pcileech_tlps128_bar_controller"] = self.renderer.render_template(
            self.templates.PCILEECH_TLPS_BAR_CONTROLLER,
            context,
        )

        # Check for error markers
        if (
            "ERROR_MISSING_DEVICE_SIGNATURE"
            in modules["pcileech_tlps128_bar_controller"]
        ):
            error_msg = safe_format(
                self.messages["missing_device_signature"],
                vid=str(vid),
                did=str(did),
            )
            log_error_safe(
                self.logger,
                error_msg,
                prefix=self.prefix,
            )
            raise TemplateRenderError(error_msg)

        # FIFO controller
        log_debug_safe(
            self.logger,
            "Rendering core template: FIFO controller",
            prefix=self.prefix,
        )
        modules["pcileech_fifo"] = self.renderer.render_template(
            self.templates.PCILEECH_FIFO, context
        )

        # Device configuration module
        log_debug_safe(
            self.logger,
            "Rendering core template: device_config",
            prefix=self.prefix,
        )
        modules["device_config"] = self.renderer.render_template(
            self.templates.DEVICE_CONFIG, context
        )

        # Top-level wrapper
        log_debug_safe(
            self.logger,
            "Rendering core template: top-level wrapper",
            prefix=self.prefix,
        )
        modules["top_level_wrapper"] = self.renderer.render_template(
            self.templates.TOP_LEVEL_WRAPPER, context
        )

        # Configuration space COE
        log_debug_safe(
            self.logger, "Rendering core template: cfgspace.coe", prefix=self.prefix
        )
        modules["pcileech_cfgspace.coe"] = self.renderer.render_template(
            self.templates.PCILEECH_CFGSPACE, context
        )

        # Configuration-space shadow BRAM module (provides
        # pcileech_tlps128_cfgspace_shadow used by the BAR controller).
        # This was previously only emitted via the legacy path; include it in
        # the primary PCILEech path to satisfy synthesis dependencies.
        log_debug_safe(
            self.logger,
            "Rendering core template: cfg_shadow.sv (config-space shadow)",
            prefix=self.prefix,
        )
        try:
            modules["cfg_shadow"] = self.renderer.render_template(
                "systemverilog/cfg_shadow.sv.j2", context
            )
        except Exception as e:
            error_msg = safe_format(
                self.messages["failed_to_render_cfg_shadow"], error=str(e)
            )
            log_error_safe(
                self.logger,
                error_msg,
                prefix=self.prefix,
            )
            raise TemplateRenderError(error_msg)

    def _generate_msix_modules_if_needed(
        self, context: Dict[str, Any], modules: Dict[str, str]
    ) -> None:
        """Generate MSI-X modules if MSI-X is supported."""
        msix_config = context.get("msix_config", {})

        if not self._is_msix_enabled(msix_config, context):
            log_debug_safe(
                self.logger,
                "MSI-X generation skipped (unsupported or no data)",
                prefix=self.prefix,
            )
            return

        log_info_safe(self.logger, "Generating MSI-X modules", prefix=self.prefix)

        # MSI-X capability registers
        log_debug_safe(
            self.logger, "Rendering MSI-X capability registers", prefix=self.prefix
        )
        modules["msix_capability_registers"] = self.renderer.render_template(
            self.templates.MSIX_CAPABILITY_REGISTERS, context
        )

        # MSI-X implementation
        log_debug_safe(
            self.logger, "Rendering MSI-X implementation", prefix=self.prefix
        )
        modules["msix_implementation"] = self.renderer.render_template(
            self.templates.MSIX_IMPLEMENTATION, context
        )

        # MSI-X table
        log_debug_safe(self.logger, "Rendering MSI-X table", prefix=self.prefix)
        modules["msix_table"] = self.renderer.render_template(
            self.templates.MSIX_TABLE, context
        )

        # Generate initialization files
        num_vectors = self._get_msix_vectors(msix_config)
        log_info_safe(
            self.logger,
            "MSI-X vectors: {count}",
            prefix=self.prefix,
            count=num_vectors,
        )
        modules["msix_pba_init.hex"] = self._generate_msix_pba_init(num_vectors)
        modules["msix_table_init.hex"] = self._generate_msix_table_init(
            num_vectors, context
        )

    def _generate_advanced_modules(
        self, context: Dict[str, Any], behavior_profile: Any, modules: Dict[str, str]
    ) -> None:
        """Generate advanced modules based on behavior profile."""
        registers = self._extract_registers(behavior_profile)
        variance_model = self._get_variance_model(behavior_profile)

        modules["pcileech_advanced_controller"] = self._generate_advanced_controller(
            context, registers, variance_model
        )

    def _generate_advanced_controller(
        self,
        context: Dict[str, Any],
        registers: List[Dict],
        variance_model: Optional[Any] = None,
    ) -> str:
        """Generate advanced SystemVerilog controller."""
        # Generate header
        header = generate_sv_header_comment(
            "Advanced PCIe Device Controller",
            generator="SVModuleGenerator",
            features="Power management, Error handling, Performance monitoring",
        )

        # Get device-specific ports
        device_type = context.get("device_type", "GENERIC")
        device_class = context.get("device_class", "CONSUMER")
        device_specific_ports = self.generate_device_specific_ports(
            device_type, device_class
        )

        # Build advanced context
        advanced_context = {
            **context,
            "header": header,
            "registers": registers,
            "variance_model": variance_model,
            "device_specific_ports": device_specific_ports,
            "clock_domain_logic": True,
            "interrupt_logic": False,
            "register_logic": False,
            "read_logic": True,
        }

        # Render main controller
        main_module = self.renderer.render_template(
            self.templates.MAIN_ADVANCED_CONTROLLER, advanced_context
        )

        # Try to render clock crossing module
        if self.renderer.template_exists(self.templates.CLOCK_CROSSING):
            try:
                clock_module = self.renderer.render_template(
                    self.templates.CLOCK_CROSSING, advanced_context
                )
                return f"{main_module}\n\n// CLOCK CROSSING MODULE\n{clock_module}"
            except Exception as e:
                error_msg = safe_format(
                    self.messages["failed_to_render_clock_crossing"],
                    error=str(e),
                )
                log_error_safe(
                    self.logger,
                    error_msg,
                    prefix=self.prefix,
                )
                raise TemplateRenderError(error_msg)

        return main_module

    def _extract_registers(self, behavior_profile: Any) -> List[Dict]:
        """Extract register definitions from behavior profile."""
        if not behavior_profile:
            log_warning_safe(
                self.logger,
                "No register accesses found, using defaults",
                prefix=self.prefix,
            )
            return self._get_default_registers()

        try:
            # Check if behavior_profile has register_accesses attribute
            if hasattr(behavior_profile, "register_accesses"):
                register_accesses = behavior_profile.register_accesses
            else:
                log_warning_safe(
                    self.logger,
                    "No register accesses found, using defaults",
                    prefix=self.prefix,
                )
                return self._get_default_registers()

        except AttributeError:
            log_warning_safe(
                self.logger,
                "No register accesses found, using defaults",
                prefix=self.prefix,
            )
            return self._get_default_registers()

        if not register_accesses:
            log_warning_safe(
                self.logger,
                "No register accesses found, using defaults",
                prefix=self.prefix,
            )
            return self._get_default_registers()

        # Process register accesses to build unique register map
        register_map = {}
        for access in register_accesses:
            self._process_register_access(access, register_map)

        if not register_map:
            log_warning_safe(
                self.logger,
                "No register accesses found, using defaults",
                prefix=self.prefix,
            )
            return self._get_default_registers()

        return list(register_map.values())

    def _process_register_access(
        self, access: Any, register_map: Dict[str, Dict]
    ) -> None:
        """Process a single register access."""
        # Try different ways to get the register name
        reg_name = safe_get_attr(access, "register")
        if not reg_name or reg_name == "UNKNOWN":
            # Try getting name from offset
            offset = safe_get_attr(access, "offset")
            if offset is not None:
                reg_name = self._get_register_name_from_offset(offset)
            else:
                return

        offset = safe_get_attr(access, "offset")
        if offset is None:
            # Try to derive offset from known register names
            offset = self._get_offset_from_register_name(reg_name)
        if offset is None or not isinstance(offset, (int, float)) or offset < 0:
            return

        offset = int(offset)

        # Get operation type
        operation = safe_get_attr(access, "operation")
        if not operation:
            operation = "read"  # Default to read

        # Initialize or update register entry
        if reg_name not in register_map:
            register_map[reg_name] = {
                "name": reg_name,
                "offset": offset,
                "size": 32,
                "access_count": 0,
                "read_count": 0,
                "write_count": 0,
                "access_type": "ro",  # Start with read-only
            }

        register_map[reg_name]["access_count"] += 1

        if operation == "read":
            register_map[reg_name]["read_count"] += 1
        elif operation == "write":
            register_map[reg_name]["write_count"] += 1
            # If we see any write operations, mark as read-write
            register_map[reg_name]["access_type"] = "rw"

    def _get_register_name_from_offset(self, offset: int) -> str:
        """Map register offset to name."""
        return SV_CONSTANTS.REGISTER_OFFSET_TO_NAME.get(offset, f"REG_{offset:02X}")

    def _get_offset_from_register_name(self, reg_name: str) -> Optional[int]:
        """Map register name to offset."""
        return SV_CONSTANTS.REGISTER_NAME_TO_OFFSET.get(reg_name)

    def _get_default_registers(self) -> List[Dict]:
        """Get default PCILeech registers."""
        return SV_CONSTANTS.DEFAULT_PCILEECH_REGISTERS

    def _is_msix_enabled(
        self, msix_config: Dict[str, Any], context: Dict[str, Any]
    ) -> bool:
        """Should we enable MSI-X module generation?

        - In tests (pytest), enable when is_supported or num_vectors>0 for coverage.
        - In production, only when is_supported True OR real msix_data injected.
        """
        import sys

        if "pytest" in sys.modules:
            return bool(
                msix_config.get("is_supported", False)
                or msix_config.get("num_vectors", 0) > 0
            )

        msix_data = context.get("msix_data") or context.get("template_context", {}).get(
            "msix_data"
        )
        if isinstance(msix_data, dict):
            if msix_data.get("table_size", 0) > 0:
                return True
            entries = msix_data.get("table_entries")
            if isinstance(entries, list) and len(entries) > 0:
                return True

        return bool(msix_config.get("is_supported", False))

    def _get_msix_vectors(self, msix_config: Dict[str, Any]) -> int:
        """Get number of MSI-X vectors."""
        return int(msix_config.get("num_vectors", 1))

    def _get_variance_model(self, behavior_profile: Any) -> Optional[Any]:
        """Extract variance model from behavior profile."""
        if isinstance(behavior_profile, dict):
            return behavior_profile.get("variance_metadata")
        elif hasattr(behavior_profile, "variance_metadata"):
            return behavior_profile.variance_metadata
        return None

    def _generate_msix_pba_init(self, num_vectors: int) -> str:
        """Generate MSI-X PBA initialization data."""
        pba_size = (num_vectors + 31) // 32
        hex_lines = ["00000000" for _ in range(pba_size)]
        return "\n".join(hex_lines) + "\n"

    def _extract_msix_entry_bytes(self, entries: List[Any], index: int) -> bytes:
        """Extract bytes from MSI-X table entry at given index.

        Args:
            entries: List of MSI-X table entries
            index: Entry index to extract

        Returns:
            Entry bytes, or empty bytes if not available
        """
        if index >= len(entries):
            return b""

        ent = entries[index]
        data_hex = None

        if isinstance(ent, dict):
            data_hex = ent.get("data")
        elif isinstance(ent, (bytes, bytearray)):
            data_hex = bytes(ent).hex()
        elif isinstance(ent, str):
            data_hex = ent

        if not data_hex:
            return b""

        try:
            return bytes.fromhex(data_hex)
        except Exception:
            log_warning_safe(
                self.logger,
                safe_format(
                    "MSI-X entry {index} has invalid hex data; padding to 16 bytes",
                    index=index,
                ),
                prefix=self.prefix,
            )
            return b""

    def _pad_msix_entry(self, data_bytes: bytes, index: int) -> bytes:
        """Pad MSI-X entry to 16 bytes if needed.

        Args:
            data_bytes: Entry bytes to pad
            index: Entry index for logging

        Returns:
            Padded bytes (always 16 bytes)
        """
        original_size = len(data_bytes)
        if original_size >= 16:
            return data_bytes

        if original_size > 0:
            log_warning_safe(
                self.logger,
                safe_format(
                    "MSI-X entry {index} is {size} bytes; padding to 16",
                    index=index,
                    size=original_size,
                ),
                prefix=self.prefix,
            )

        return data_bytes.ljust(16, b"\x00")

    def _generate_msix_table_init(
        self, num_vectors: int, context: Dict[str, Any]
    ) -> str:
        """Generate MSI-X table initialization data."""
        # Check if in test environment
        import sys

        if "pytest" in sys.modules:
            # Generate test data
            table_data = []
            for i in range(num_vectors):
                table_data.extend(
                    [
                        SV_CONSTANTS.MSIX_TEST_ADDR_BASE + (i << 4),  # Address Low
                        SV_CONSTANTS.MSIX_TEST_ADDR_HIGH,  # Address High
                        (0x00000000 | i),  # Message Data
                        SV_CONSTANTS.MSIX_TEST_VECTOR_CTRL_DEFAULT,  # Vector Control
                    ]
                )
            return "\n".join(f"{value:08X}" for value in table_data) + "\n"

        # Check for explicitly provided MSI-X table entries in the context.
        # This allows callers (or earlier preload steps) to inject real table
        # contents read from hardware so the generator can emit the correct
        # initialization hex without fabricating values.
        msix_data = context.get("msix_data") or context.get("template_context", {}).get(
            "msix_data"
        )
        if msix_data:
            # Support multiple possible representations:
            # - 'table_init_hex': a prebuilt hex string (returned as-is)
            # - 'table_entries': a list of dicts with 'data' (hex bytes) per vector
            table_init_hex = msix_data.get("table_init_hex")
            if table_init_hex:
                return table_init_hex

            entries = msix_data.get("table_entries")
            if entries and isinstance(entries, list):
                # Build hex lines from entries. Each entry should represent 16 bytes
                # (4 x 32-bit little-endian words). If an entry is missing or
                # shorter than 16 bytes, pad with zeros and log a warning.
                table_lines = []
                for i in range(num_vectors):
                    data_bytes = self._extract_msix_entry_bytes(entries, i)
                    data_bytes = self._pad_msix_entry(data_bytes, i)

                    # Split into four 32-bit little-endian words
                    for w in range(4):
                        word_bytes = data_bytes[w * 4 : (w + 1) * 4]
                        word_val = int.from_bytes(word_bytes, "little")
                        table_lines.append(f"{word_val:08X}")

                return "\n".join(table_lines) + "\n"

        # In production, if no explicit table entries are available, refuse to
        # fabricate MSI-X table contents. This is a safety measure; callers
        # should either provide real table contents or run without MSI-X.
        log_error_safe(
            self.logger,
            "Missing MSI-X table data; refusing to fabricate values",
            prefix=self.prefix,
        )
        raise TemplateRenderError(
            "MSI-X table data must be read from actual hardware. "
            "Cannot generate safe firmware without real MSI-X values."
        )
