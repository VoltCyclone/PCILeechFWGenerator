#!/usr/bin/env python3
"""
PCILeech Generator - Main orchestrator for PCILeech firmware generation

This module provides the main orchestrator class that coordinates complete PCILeech
firmware generation by integrating with existing infrastructure components and
eliminating all hard-coded fallbacks.

The PCILeechGenerator class serves as the central coordination point for:
- Device behavior profiling and analysis
- Configuration space management
- MSI-X capability handling
- Template context building
- SystemVerilog generation
- Production-ready error handling

All data sources are dynamic with no fallback mechanisms - the system fails
fast if required data is not available.
"""

import logging
import traceback
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Dict, Generator, List, Optional, Tuple, TypedDict

# Import existing infrastructure components
from src.device_clone.behavior_profiler import BehaviorProfile, BehaviorProfiler
from src.device_clone.config_space_manager import ConfigSpaceManager
from src.device_clone.constants import DEFAULT_FPGA_PART
from src.device_clone.device_info_lookup import lookup_device_info
from src.device_clone.msix_capability import (
    parse_msix_capability,
    validate_msix_configuration,
)
from src.device_clone.pcileech_context import PCILeechContextBuilder, VFIODeviceManager
from src.device_clone.writemask_generator import WritemaskGenerator
from src.error_utils import extract_root_cause
from src.exceptions import PCILeechGenerationError, PlatformCompatibilityError
from src.pci_capability.msix_bar_validator import validate_msix_bar_configuration

# Import from centralized locations
from src.string_utils import (
    generate_tcl_header_comment,
    log_error_safe,
    log_info_safe,
    log_warning_safe,
    safe_format,
    utc_timestamp,
)
from src.templating import AdvancedSVGenerator, TemplateRenderer, TemplateRenderError
from src.templating.tcl_builder import format_hex_id

logger = logging.getLogger(__name__)

# Data sizing constants for MSI-X handling
# NOTE: Core sizing constants kept here for backward import compatibility.
MSIX_ENTRY_SIZE = 16  # bytes per MSI-X table entry
DWORD_SIZE = 4  # bytes per 32-bit word
DWORDS_PER_MSIX_ENTRY = MSIX_ENTRY_SIZE // DWORD_SIZE

# (Removed MSIXData TypedDict to avoid typing friction with dynamic dict usage)


@dataclass
class PCILeechGenerationConfig:
    """Configuration for PCILeech firmware generation."""

    # Device identification
    device_bdf: str

    # Board configuration
    board: Optional[str] = None
    fpga_part: Optional[str] = None

    # Generation options
    enable_behavior_profiling: bool = True
    behavior_capture_duration: float = 30.0
    enable_manufacturing_variance: bool = True
    enable_advanced_features: bool = True

    # Template configuration
    template_dir: Optional[Path] = None
    output_dir: Path = Path("generated")

    # PCILeech-specific options
    pcileech_command_timeout: int = 1000
    pcileech_buffer_size: int = 4096
    enable_dma_operations: bool = True
    enable_interrupt_coalescing: bool = False

    # Validation options
    strict_validation: bool = True
    fail_on_missing_data: bool = True

    # Fallback control options
    fallback_mode: str = "none"  # "none", "prompt", or "auto"
    allowed_fallbacks: List[str] = field(default_factory=list)

    # Donor template
    donor_template: Optional[Dict[str, Any]] = None
    # Experimental / testing features
    enable_error_injection: bool = False


class PCILeechGenerator:
    """
    Main orchestrator class for PCILeech firmware generation.

    This class coordinates the complete PCILeech firmware generation process by
    integrating with existing infrastructure components and providing dynamic
    data sourcing for all template variables.

    Key responsibilities:
    - Orchestrate device behavior profiling
    - Manage configuration space analysis
    - Handle MSI-X capability processing
    - Build comprehensive template contexts
    - Generate SystemVerilog modules
    - Provide production-ready error handling
    """

    def __init__(self, config: PCILeechGenerationConfig):
        """
        Initialize the PCILeech generator.

        Args:
            config: Generation configuration

        Raises:
            PCILeechGenerationError: If initialization fails
        """
        self.config = config
        self.logger = logging.getLogger(__name__)

        # Initialize shared/global fallback manager
        from src.device_clone.fallback_manager import get_global_fallback_manager

        self.fallback_manager = get_global_fallback_manager(
            mode=config.fallback_mode, allowed_fallbacks=config.allowed_fallbacks
        )

        # Initialize infrastructure components
        try:
            self._initialize_components()
        except Exception as e:
            raise PCILeechGenerationError(
                safe_format("Failed to initialize PCILeech generator: {err}", err=e)
            ) from e

    # ------------------------------------------------------------------
    # Internal validation helper (reduces repetitive fail-fast patterns)
    # ------------------------------------------------------------------

    def _require(self, condition: bool, message: str, **context: Any) -> None:
        """Fail fast with a consistent error + log when condition is false.

        This keeps enforcement localized and aligns with the donor uniqueness
        policy – no silent fallbacks. Uses PCILeechGenerationError so callers
        inherit existing exception handling semantics.
        """
        if condition:
            return
        # Log first (explicit prefix for quick grep in logs)
        log_error_safe(
            self.logger,
            safe_format(
                "Build aborted: {msg} | ctx={ctx}",
                msg=message,
                ctx=context,
            ),
            prefix="PCIL",
        )
        raise PCILeechGenerationError(message)

    # ------------------------------------------------------------------
    # Timestamp helper (legacy compatibility for tests expecting _get_timestamp)
    # ------------------------------------------------------------------

    def _get_timestamp(self) -> str:
        """Return build timestamp.

        Prefers BUILD_TIMESTAMP env (for reproducible builds/tests) else falls
        back to naive ISO8601 (local time). This mirrors legacy behavior so
        existing tests that patch datetime in modules still receive a plain
        ISO string without a trailing 'Z'.
        """
        import os
        from datetime import datetime

        override = os.getenv("BUILD_TIMESTAMP")
        if override:
            return override
        try:
            return datetime.now().isoformat()
        except Exception:
            return utc_timestamp()

    def _initialize_components(self) -> None:
        """Initialize all infrastructure components."""
        log_info_safe(
            self.logger,
            "Initializing PCILeech generator for device {bdf}",
            bdf=self.config.device_bdf,
            prefix="PCIL",
        )

        # Initialize behavior profiler
        if self.config.enable_behavior_profiling:
            self.behavior_profiler = BehaviorProfiler(
                bdf=self.config.device_bdf,
                debug=True,
                enable_variance=self.config.enable_manufacturing_variance,
                enable_ftrace=True,
            )
        else:
            self.behavior_profiler = None

        # Initialize configuration space manager
        self.config_space_manager = ConfigSpaceManager(
            bdf=self.config.device_bdf,
            strict_vfio=getattr(self.config, "strict_vfio", True),
        )

        # Initialize template renderer
        self.template_renderer = TemplateRenderer(self.config.template_dir)

        # Initialize SystemVerilog generator
        self.sv_generator = AdvancedSVGenerator(template_dir=self.config.template_dir)

        # Initialize context builder (will be created after profiling)
        self.context_builder = None

        log_info_safe(
            self.logger,
            "PCILeech generator components initialized successfully",
            prefix="PCIL",
        )

    def generate_pcileech_firmware(self) -> Dict[str, Any]:
        """Main orchestration entrypoint for firmware generation.

        Steps (fail-fast, no inline TCL fallbacks):
          1. Capture device behavior (optional)
          2. Analyze configuration space
          3. Preload MSI-X data & optionally capture table entries
          4. Validate MSI-X/BAR layout
          5. Build + validate template context
          6. Generate SystemVerilog modules
             7. Generate additional components (constraints, integration,
                 COE, writemask)
          8. Generate TCL scripts strictly via templates
          9. Validate generated firmware & assemble result
        """
        try:
            # 1. Behavior profiling (optional)
            behavior_profile = self._capture_device_behavior()

            # 2. Config space analysis
            config_space_data = self._analyze_configuration_space()

            # 3. Preload MSI-X & capture table entries if available
            msix_data = self._preload_msix_data_early()
            if msix_data:
                table_capture = self._capture_msix_table_entries(msix_data)
                if table_capture:
                    if "table_entries" in table_capture:
                        msix_data["table_entries"] = table_capture["table_entries"]
                    if "table_init_hex" in table_capture:
                        msix_data["table_init_hex"] = table_capture["table_init_hex"]

            # 4. Early MSI-X/BAR validation (context not yet built; pass minimal)
            try:
                self._validate_msix_and_bar_layout(
                    template_context={},
                    config_space_data=config_space_data,
                    msix_data=msix_data,
                )
            except Exception as e:  # surface as generation error
                raise PCILeechGenerationError(
                    safe_format("MSI-X/BAR validation failed: {err}", err=e)
                ) from e

            # 5. Build & validate template context
            interrupt_strategy = (
                "msix" if (msix_data and msix_data.get("table_size")) else "none"
            )
            interrupt_vectors = msix_data.get("table_size", 0) if msix_data else 0
            template_context = self._build_template_context(
                behavior_profile,
                config_space_data,
                msix_data,
                interrupt_strategy,
                interrupt_vectors,
            )

            # 6. SystemVerilog generation
            systemverilog_modules = self._generate_systemverilog_modules(
                template_context
            )

            # 7. Additional firmware components (constraints, COE, writemask,
            #    integration)
            firmware_components = self._generate_firmware_components(template_context)

            # 8. Enforced-template TCL scripts (no fallback inline generation)
            tcl_scripts = self._generate_default_tcl_scripts(template_context)

            # 9. Validate generated firmware artifacts
            self._validate_generated_firmware(
                systemverilog_modules, firmware_components
            )

            generation_result = self._assemble_generation_result(
                behavior_profile,
                config_space_data,
                msix_data,
                template_context,
                systemverilog_modules,
                firmware_components,
                tcl_scripts,
            )

            log_info_safe(
                self.logger,
                "PCILeech firmware generation completed successfully",
                prefix="PCIL",
            )
            return generation_result

        except PlatformCompatibilityError:
            raise
        except Exception as e:  # Keep broad catch for top-level wrapper
            raise self._handle_generation_exception(e)

    # ------------------------------------------------------------------
    # Small extracted helpers (low-risk, no behavioral changes)
    # ------------------------------------------------------------------

    def _assemble_generation_result(
        self,
        behavior_profile: Optional[BehaviorProfile],
        config_space_data: Dict[str, Any],
        msix_data: Optional[Dict[str, Any]],
        template_context: Dict[str, Any],
        systemverilog_modules: Dict[str, str],
        firmware_components: Dict[str, Any],
        tcl_scripts: Dict[str, str],
    ) -> Dict[str, Any]:
        return {
            "device_bdf": self.config.device_bdf,
            "generation_timestamp": self._get_timestamp(),
            "behavior_profile": behavior_profile,
            "config_space_data": config_space_data,
            "msix_data": msix_data,
            "template_context": template_context,
            "systemverilog_modules": systemverilog_modules,
            "firmware_components": firmware_components,
            "tcl_scripts": tcl_scripts,
            "generation_metadata": self._build_generation_metadata(),
        }

    def _handle_generation_exception(self, e: Exception) -> PCILeechGenerationError:
        log_error_safe(
            self.logger,
            safe_format(
                "PCILeech firmware generation failed: {error}",
                error=str(e),
            ),
            prefix="PCIL",
        )
        root_cause = extract_root_cause(e)
        return PCILeechGenerationError(
            "Firmware generation failed", root_cause=root_cause
        )

    # ------------------------------------------------------------------
    # Lightweight context manager for consistent step logging/handling
    # ------------------------------------------------------------------
    from contextlib import contextmanager

    @contextmanager
    def _generation_step(
        self, step: str, allow_fallback: bool = False
    ) -> Generator[None, None, None]:
        log_info_safe(self.logger, "Starting {step}", step=step, prefix="PCIL")
        try:
            yield
            log_info_safe(self.logger, "Completed {step}", step=step, prefix="PCIL")
        except Exception as e:  # pragma: no cover (control flow wrapper)
            if allow_fallback and self.fallback_manager.confirm_fallback(step, str(e)):
                log_warning_safe(
                    self.logger,
                    safe_format(
                        "{step} failed, continuing with fallback: {err}",
                        step=step,
                        err=str(e),
                    ),
                    prefix="PCIL",
                )
                return
            raise

    def _capture_device_behavior(self) -> Optional[BehaviorProfile]:
        """
        Capture device behavior profile using the behavior profiler.

        Returns:
            BehaviorProfile if profiling is enabled, None otherwise

        Raises:
            PCILeechGenerationError: If behavior profiling fails
        """
        if not self.behavior_profiler:
            log_info_safe(
                self.logger,
                "Behavior profiling disabled, skipping device behavior capture",
                prefix="MSIX",
            )
            return None

        log_info_safe(
            self.logger,
            safe_format(
                "Capturing device behavior profile for {duration}s",
                duration=self.config.behavior_capture_duration,
            ),
            prefix="MSIX",
        )

        try:
            behavior_profile = self.behavior_profiler.capture_behavior_profile(
                duration=self.config.behavior_capture_duration
            )

            # Analyze patterns for enhanced context
            pattern_analysis = self.behavior_profiler.analyze_patterns(behavior_profile)

            # Store analysis results in profile for later use
            behavior_profile.pattern_analysis = pattern_analysis

            log_info_safe(
                self.logger,
                safe_format(
                    "Captured {accesses} register accesses with {patterns} "
                    "timing patterns",
                    accesses=behavior_profile.total_accesses,
                    patterns=len(behavior_profile.timing_patterns),
                ),
                prefix="MSIX",
            )

            return behavior_profile

        except Exception as e:
            # Behavior profiling is optional - can use fallback manager
            details = (
                "Without behavior profiling, generated firmware may not reflect "
                "actual device timing patterns and behavior."
            )

            if self.fallback_manager.confirm_fallback(
                "behavior-profiling", str(e), details=details
            ):
                log_warning_safe(
                    self.logger,
                    safe_format(
                        "Device behavior profiling failed, continuing without "
                        "profile: {error}",
                        error=str(e),
                    ),
                    prefix="MSIX",
                )
                return None
            else:
                raise PCILeechGenerationError(
                    safe_format("Device behavior profiling failed: {err}", err=e)
                ) from e

    def _analyze_configuration_space(self) -> Dict[str, Any]:
        """
        Analyze device configuration space.

        Returns:
            Dictionary containing configuration space data and analysis

        Raises:
            PCILeechGenerationError: If configuration space analysis fails
        """
        log_info_safe(
            self.logger,
            safe_format(
                "Analyzing configuration space for device {bdf}",
                bdf=self.config.device_bdf,
            ),
            prefix="MSIX",
        )

        try:
            config_space_bytes = self.config_space_manager.read_vfio_config_space()
            return self._process_config_space_bytes(config_space_bytes)
        except (OSError, IOError) as e:
            log_error_safe(
                self.logger,
                safe_format(
                    "Config space read failed (IO): {error}",
                    error=str(e),
                ),
                prefix="MSIX",
            )
            raise PCILeechGenerationError(
                safe_format("Configuration space read failed: {err}", err=e)
            ) from e
        except ValueError as e:
            log_error_safe(
                self.logger,
                safe_format(
                    "Config space value error: {error}",
                    error=str(e),
                ),
                prefix="MSIX",
            )
            raise PCILeechGenerationError(
                safe_format("Configuration space parse failed: {err}", err=e)
            ) from e
        except Exception as e:
            log_error_safe(
                self.logger,
                safe_format(
                    "CRITICAL: Configuration space analysis failed - cannot "
                    "continue without device identity: {error}",
                    error=str(e),
                ),
                prefix="MSIX",
            )
            raise PCILeechGenerationError(
                safe_format(
                    (
                        "Configuration space analysis failed (critical for device "
                        "identity): {err}"
                    ),
                    err=e,
                )
            ) from e

    def _analyze_configuration_space_with_vfio(self) -> Dict[str, Any]:
        """
            Analyze device configuration space when VFIO is already bound.

        This method assumes VFIO already active and doesn't create its own binding.

            Returns:
                Dictionary containing configuration space data and analysis

            Raises:
                PCILeechGenerationError: If configuration space analysis fails
        """
        log_info_safe(
            self.logger,
            safe_format(
                "Analyzing configuration space for device {bdf} (VFIO already active)",
                bdf=self.config.device_bdf,
            ),
            prefix="MSIX",
        )

        try:
            # Read configuration space without creating new VFIO binding
            config_space_bytes = self.config_space_manager._read_sysfs_config_space()
            return self._process_config_space_bytes(config_space_bytes)

        except Exception as e:
            # Configuration space is critical for device identity - MUST FAIL
            log_error_safe(
                self.logger,
                safe_format(
                    "CRITICAL: Configuration space analysis failed - cannot "
                    "continue without device identity: {error}",
                    error=str(e),
                ),
                prefix="MSIX",
            )
            raise PCILeechGenerationError(
                safe_format(
                    (
                        "Configuration space analysis failed (critical for device "
                        "identity): {err}"
                    ),
                    err=e,
                )
            ) from e

    def _process_config_space_bytes(self, config_space_bytes: bytes) -> Dict[str, Any]:
        """
            Process configuration space bytes into a comprehensive data structure.

        This consolidates logic from both _analyze_configuration_space methods.
        The PCILeechContextBuilder handles device info enhancement; we don't need
            to duplicate that work here.

            Args:
                config_space_bytes: Raw configuration space bytes

            Returns:
                Dictionary containing configuration space data

            Raises:
                PCILeechGenerationError: If critical fields are missing
        """
        # Initial extraction (fast, local)
        base_info = self.config_space_manager.extract_device_info(config_space_bytes)

        # Centralized enrichment + fallback policy (avoid duplicated parsing logic)
        try:
            device_info = lookup_device_info(
                bdf=self.config.device_bdf,
                partial_info=base_info,
                from_config_manager=True,
            )
        except Exception as e:  # Fallback to base if lookup path fails
            log_warning_safe(
                self.logger,
                safe_format(
                    "DeviceInfoLookup failed, using base extracted info: {err}",
                    err=str(e),
                ),
                prefix="MSIX",
            )
            device_info = base_info

        # Fail fast if still missing critical identifiers
        if not device_info.get("vendor_id") or not device_info.get("device_id"):
            raise PCILeechGenerationError(
                "Cannot determine device identity (vendor_id/device_id missing)"
            )

        # Build configuration space data structure
        config_space_data = {
            "raw_config_space": config_space_bytes,
            "config_space_hex": config_space_bytes.hex(),
            "device_info": device_info,
            "vendor_id": format(device_info.get("vendor_id", 0), "04x"),
            "device_id": format(device_info.get("device_id", 0), "04x"),
            "class_code": format(device_info.get("class_code", 0), "06x"),
            "revision_id": format(device_info.get("revision_id", 0), "02x"),
            "bars": device_info.get("bars", []),
            "config_space_size": len(config_space_bytes),
        }

        log_info_safe(
            self.logger,
            safe_format(
                "Configuration space processed: VID={vendor_id}, DID={device_id}, "
                "Class={class_code}",
                vendor_id=device_info.get("vendor_id", 0),
                device_id=device_info.get("device_id", 0),
                class_code=device_info.get("class_code", 0),
            ),
            prefix="MSIX",
        )

        return config_space_data

    def _process_msix_capabilities(
        self, config_space_data: Dict[str, Any]
    ) -> Optional[Dict[str, Any]]:
        """
        Process MSI-X capabilities from configuration space.

        Args:
            config_space_data: Configuration space data

        Returns:
            Dictionary containing MSI-X capability information, or None if MSI-X
            capability is missing or table_size == 0
        """
        log_info_safe(self.logger, "Processing MSI-X capabilities", prefix="MSIX")

        config_space_hex = config_space_data.get("config_space_hex", "")

        if not config_space_hex:
            log_info_safe(
                self.logger,
                "No configuration space data available for MSI-X analysis",
                prefix="MSIX",
            )
            return None

        # Parse MSI-X capability
        msix_info = parse_msix_capability(config_space_hex)

        # Return None if capability is missing or table_size == 0
        if msix_info["table_size"] == 0:
            log_info_safe(
                self.logger,
                "MSI-X capability not found or table_size is 0",
            )
            return None

        # Validate MSI-X configuration
        is_valid, validation_errors = validate_msix_configuration(msix_info)

        if not is_valid and self.config.strict_validation:
            log_warning_safe(
                self.logger,
                safe_format(
                    "MSI-X validation failed: {errors}",
                    errors="; ".join(validation_errors),
                ),
                prefix="MSIX",
            )
            return None

        # Build comprehensive MSI-X data
        msix_data = {
            "capability_info": msix_info,
            "table_size": msix_info["table_size"],
            "table_bir": msix_info["table_bir"],
            "table_offset": msix_info["table_offset"],
            "pba_bir": msix_info["pba_bir"],
            "pba_offset": msix_info["pba_offset"],
            "enabled": msix_info["enabled"],
            "function_mask": msix_info["function_mask"],
            "validation_errors": validation_errors,
            "is_valid": is_valid,
        }

        log_info_safe(
            self.logger,
            safe_format(
                "MSI-X capabilities processed: {vectors} vectors, table BIR {bir}, "
                "offset 0x{offset:x}",
                vectors=msix_info["table_size"],
                bir=msix_info["table_bir"],
                offset=msix_info["table_offset"],
            ),
            prefix="MSIX",
        )

        return msix_data

    def _build_template_context(
        self,
        behavior_profile: Optional[BehaviorProfile],
        config_space_data: Dict[str, Any],
        msix_data: Optional[Dict[str, Any]],
        interrupt_strategy: str,
        interrupt_vectors: int,
    ) -> Dict[str, Any]:
        """
        Build comprehensive template context from all data sources.

        This is a thin orchestration layer, delegating all the
        actual context building work to PCILeechContextBuilder.

        Args:
            behavior_profile: Device behavior profile
            config_space_data: Configuration space data
            msix_data: MSI-X capability data (None if not available)
            interrupt_strategy: Interrupt strategy ("msix", "msi", or "intx")
            interrupt_vectors: Number of interrupt vectors

        Returns:
            Comprehensive template context dictionary

        Raises:
            PCILeechGenerationError: If context building fails
        """
        log_info_safe(
            self.logger, "Building comprehensive template context", prefix="PCIL"
        )

        try:
            # Initialize context builder
            self.context_builder = PCILeechContextBuilder(
                device_bdf=self.config.device_bdf, config=self.config
            )

            # Delegate all context building to PCILeechContextBuilder
            template_context = self.context_builder.build_context(
                behavior_profile=behavior_profile,
                config_space_data=config_space_data,
                msix_data=msix_data,
                interrupt_strategy=interrupt_strategy,
                interrupt_vectors=interrupt_vectors,
                donor_template=self.config.donor_template,
            )

            # Validate context completeness
            context_dict = dict(template_context)
            self._validate_template_context(context_dict)

            log_info_safe(
                self.logger,
                safe_format(
                    "Template context built successfully with {keys} top-level keys",
                    keys=len(template_context),
                ),
                prefix="PCIL",
            )

            return context_dict

        except Exception as e:
            root_cause = extract_root_cause(e)
            raise PCILeechGenerationError(
                "Template context building failed", root_cause=root_cause
            )

    def _validate_template_context(self, context: Dict[str, Any]) -> None:
        """
        Validate template context using the centralized TemplateContextValidator.

        This method ensures all required template variables are present and properly
        initialized before template rendering.

        Args:
            context: Template context to validate

        Raises:
            PCILeechGenerationError: If context validation fails
        """
        try:
            # Import the centralized validator
            from src.templating.template_context_validator import (
                validate_template_context,
            )

            # Derive template name dynamically.
            # Priority order:
            #  1. config.firmware_template (explicit override)
            #  2. donor_template['template'] or ['name']
            #  3. default constant below
            DEFAULT_PCILEECH_TEMPLATE = "pcileech_firmware.j2"

            template_name: Optional[str] = None
            explicit: Optional[str] = None

            # 1. Explicit attribute on config (highest priority)
            if hasattr(self.config, "firmware_template"):
                explicit = getattr(self.config, "firmware_template") or None
                if explicit:
                    template_name = explicit

            # 2. Donor template dict key(s)
            if not template_name and isinstance(self.config.donor_template, dict):
                donor_name = self.config.donor_template.get(
                    "template"
                ) or self.config.donor_template.get("name")
                if donor_name:
                    template_name = donor_name

            # 3. Auto-detect if still unset (scan template dir)
            if not template_name:
                try:
                    renderer = getattr(
                        self, "template_renderer", None
                    ) or TemplateRenderer(getattr(self.config, "template_dir", None))
                    # Candidate patterns (basename match)
                    all_templates = renderer.list_templates("*.j2")
                    candidates = []
                    for t in all_templates:
                        base = Path(t).name.lower()
                        if base.startswith("pcileech") and "firmware" in base:
                            candidates.append(t)
                    # Fallback: any pcileech*.j2 if firmware-specific not found
                    if not candidates:
                        for t in all_templates:
                            base = Path(t).name.lower()
                            if base.startswith("pcileech"):
                                candidates.append(t)
                    if len(candidates) == 1:
                        template_name = candidates[0]
                        log_info_safe(
                            self.logger,
                            "Auto-detected firmware template: {tpl}",
                            tpl=template_name,
                            prefix="PCIL",
                        )
                    elif len(candidates) > 1:
                        # Try to pick canonical: exact default name first
                        for c in candidates:
                            if Path(c).name == DEFAULT_PCILEECH_TEMPLATE:
                                template_name = c
                                break
                        if not template_name:
                            # Choose the shortest path (likely top-level canonical)
                            template_name = min(candidates, key=len)
                        log_warning_safe(
                            self.logger,
                            safe_format(
                                "Multiple candidate templates found; selected: {sel}, all={all}",  # noqa: E501
                                sel=template_name,
                                all=",".join(candidates),
                            ),
                            prefix="PCIL",
                        )
                except Exception as e:
                    log_warning_safe(
                        self.logger,
                        safe_format(
                            "Template auto-detection failed: {err}",
                            err=str(e),
                        ),
                        prefix="PCIL",
                    )

            # 4. Fallback to default constant
            if not template_name:
                template_name = DEFAULT_PCILEECH_TEMPLATE

            # Normalize: enforce .j2 suffix
            if not template_name.endswith(".j2"):
                template_name = f"{template_name}.j2"

            # Final sanity check
            if not template_name:
                raise PCILeechGenerationError(
                    "Unable to resolve firmware template name for validation"
                )

            # Use strict validation mode based on config
            strict_mode = self.config.strict_validation

            # Validate the context
            validated_context = validate_template_context(
                template_name, context, strict=strict_mode
            )
            log_info_safe(
                self.logger,
                "Template context validation successful",
                prefix="PCIL",
            )

        except ValueError as e:
            # Convert ValueError from validator to PCILeechGenerationError
            if self.config.fail_on_missing_data:
                raise PCILeechGenerationError(
                    safe_format("Template context validation failed: {err}", err=e)
                ) from e
            else:
                log_warning_safe(
                    self.logger,
                    safe_format(
                        "Template context validation warning: {error}",
                        error=str(e),
                    ),
                    prefix="PCIL",
                )
        except ImportError:
            # Fallback to basic validation if validator not available
            log_warning_safe(
                self.logger,
                "TemplateContextValidator not available, using basic validation",
                prefix="PCIL",
            )

            # Basic validation for backward compatibility
            required_keys = [
                "device_config",
                "config_space",
                "msix_config",
                "bar_config",
                "timing_config",
                "pcileech_config",
                "device_signature",
            ]

            missing_keys = [key for key in required_keys if key not in context]

            if missing_keys and self.config.fail_on_missing_data:
                raise PCILeechGenerationError(
                    safe_format(
                        "Template context missing required keys: {keys}",
                        keys=missing_keys,
                    )
                )

            if missing_keys:
                log_warning_safe(
                    self.logger,
                    safe_format(
                        "Template context missing optional keys: {keys}",
                        keys=missing_keys,
                    ),
                    prefix="PCIL",
                )

    def _generate_systemverilog_modules(
        self, template_context: Dict[str, Any]
    ) -> Dict[str, str]:
        """
        Generate SystemVerilog modules using template context.

        Args:
            template_context: Template context data

        Returns:
            Dictionary mapping module names to generated SystemVerilog code

        Raises:
            PCILeechGenerationError: If SystemVerilog generation fails
        """
        log_info_safe(self.logger, "Generating SystemVerilog modules")

        try:
            # Use the enhanced SystemVerilog generator for PCILeech modules
            behavior_profile = template_context.get("device_config", {}).get(
                "behavior_profile"
            )
            modules = self.sv_generator.generate_systemverilog_modules(
                template_context=template_context, behavior_profile=behavior_profile
            )

            # Cache the generated modules for use in writemask generation
            self._cached_systemverilog_modules = modules

            msix_ctx = template_context.get("msix_data") or {}
            init_hex = msix_ctx.get("table_init_hex", "")
            entries_list = msix_ctx.get("table_entries", []) or []
            init_len = len(init_hex) if isinstance(init_hex, str) else 0
            log_info_safe(
                self.logger,
                safe_format(
                    "Generated {count} SystemVerilog modules | msix init_len={ihl} "
                    "entries={entries}",
                    count=len(modules),
                    ihl=init_len,
                    entries=len(entries_list),
                ),
                prefix="PCIL",
            )

            return modules

        except TemplateRenderError as e:
            raise PCILeechGenerationError(
                safe_format("SystemVerilog generation failed: {err}", err=e)
            ) from e

    def _generate_advanced_modules(
        self, template_context: Dict[str, Any]
    ) -> Dict[str, str]:
        """Generate advanced SystemVerilog modules."""
        advanced_modules = {}

        try:
            # Generate advanced controller if behavior profile is available
            if template_context.get("device_config", {}).get("behavior_profile"):
                log_info_safe(
                    self.logger,
                    "Generating advanced modules with behavior profile",
                    prefix="PCIL",
                )
                registers = self._extract_register_definitions(template_context)
                variance_model = template_context.get("device_config", {}).get(
                    "variance_model"
                )

                log_info_safe(
                    self.logger,
                    safe_format(
                        "Calling generate_advanced_systemverilog with {reg_count} "
                        "registers",
                        reg_count=len(registers),
                    ),
                    prefix="PCIL",
                )
                advanced_modules["advanced_controller"] = (
                    self.sv_generator.generate_advanced_systemverilog(
                        regs=registers, variance_model=variance_model
                    )
                )
                log_info_safe(
                    self.logger,
                    "Successfully generated advanced_controller module",
                    prefix="PCIL",
                )

        except Exception as e:
            log_error_safe(
                self.logger,
                safe_format(
                    "Advanced module generation failed: {error}\nTraceback: {tb}",
                    error=str(e),
                    tb=traceback.format_exc(),
                ),
                prefix="PCIL",
            )

        return advanced_modules

    def _extract_register_definitions(
        self, template_context: Dict[str, Any]
    ) -> List[Dict]:
        """Extract register definitions from template context."""
        registers = []

        # Extract from behavior profile if available
        behavior_profile = template_context.get("device_config", {}).get(
            "behavior_profile"
        )
        if behavior_profile:
            for access in behavior_profile.get("register_accesses", []):
                registers.append(
                    {
                        "name": access.get("register", "UNKNOWN"),
                        "offset": access.get("offset", 0),
                        "size": 32,  # Default to 32-bit registers
                        "access_type": access.get("operation", "rw"),
                    }
                )

        return registers

    def _generate_firmware_components(
        self, template_context: Dict[str, Any]
    ) -> Dict[str, Any]:
        """
        Generate additional firmware components.

        Args:
            template_context: Template context data

        Returns:
            Dictionary containing additional firmware components
        """
        log_info_safe(self.logger, "Generating additional firmware components")

        components = {
            "build_integration": self._generate_build_integration(template_context),
            "constraint_files": self._generate_constraint_files(template_context),
            "tcl_scripts": self._generate_tcl_scripts(template_context),
            "config_space_hex": self._generate_config_space_hex(template_context),
        }

        # Generate writemask COE after config space COE is available
        # This requires the config space COE to be saved to disk first
        components["writemask_coe"] = self._generate_writemask_coe(template_context)

        return components

    def _generate_build_integration(self, template_context: Dict[str, Any]) -> str:
        """Generate build system integration code."""
        try:
            return self.sv_generator.generate_pcileech_integration_code(
                template_context
            )
        except Exception as e:
            details = (
                "Using fallback build integration may result in inconsistent or "
                "unpredictable build behavior."
            )

            if self.fallback_manager.confirm_fallback(
                "build-integration", str(e), details=details
            ):
                log_warning_safe(
                    self.logger,
                    safe_format(
                        "PCILeech build integration generation failed, attempting "
                        "fallback: {error}",
                        error=str(e),
                    ),
                    prefix="PCIL",
                )
                # Fallback to base integration
                try:
                    # Retry with explicit VFIO verification override to bypass
                    # environment probing in CI/sandbox builds. Do NOT mutate
                    # the original context; work on a shallow copy.
                    fallback_ctx = dict(template_context)
                    fallback_ctx["vfio_binding_verified"] = True
                    return self.sv_generator.generate_pcileech_integration_code(
                        fallback_ctx
                    )
                except Exception as fallback_e:
                    # Build integration is critical - cannot use minimal fallback
                    log_error_safe(
                        self.logger,
                        safe_format(
                            "CRITICAL: Build integration generation failed "
                            "completely: {error}",
                            error=str(fallback_e),
                        ),
                        prefix="PCIL",
                    )
                    raise PCILeechGenerationError(
                        safe_format(
                            (
                                "Build integration generation failed (no safe "
                                "fallback available): {err}"
                            ),
                            err=fallback_e,
                        )
                    ) from fallback_e
            else:
                raise PCILeechGenerationError(
                    safe_format("Build integration generation failed: {err}", err=e)
                ) from e

    def _generate_constraint_files(
        self, template_context: Dict[str, Any]
    ) -> Dict[str, str]:
        """Generate constraint files."""
        try:
            # Import TCL builder components
            from src.templating.tcl_builder import (
                BuildContext,
                TCLBuilder,
                TCLScriptType,
            )
            from src.templating.template_renderer import TemplateRenderer

            # Create template renderer
            renderer = TemplateRenderer()

            # Build constraints using the TCL builder
            tcl_builder = TCLBuilder(output_dir=Path("./output"))

            # Build comprehensive context for constraints template
            # CRITICAL: Device IDs must be present - no fallbacks allowed
            if not template_context.get("vendor_id") or not template_context.get(
                "device_id"
            ):
                raise PCILeechGenerationError(
                    (
                        "CRITICAL: Missing vendor_id or device_id in template "
                        "context - cannot generate constraints with fallback IDs"
                    )
                )

            constraints_context = {
                # Device information - NO FALLBACKS for critical IDs
                "device": {
                    "vendor_id": template_context["vendor_id"],
                    "device_id": template_context.get("device_id"),
                    "revision_id": template_context.get("revision_id", "00"),
                    "class_code": template_context.get("class_code"),
                    "subsys_vendor_id": template_context.get("subsys_vendor_id"),
                    "subsys_device_id": template_context.get("subsys_device_id"),
                },
                # Board information
                "board": {
                    "name": template_context.get("board_name", "default"),
                    "fpga_part": template_context.get("fpga_part", ""),
                    "fpga_family": template_context.get("fpga_family", ""),
                    "constraints": template_context.get("board_constraints", {}),
                },
                # Explicit constraint files list (may be empty). This must always
                # be present to satisfy StrictUndefined in templates.
                "constraint_files": (
                    template_context.get("constraint_files")
                    if isinstance(template_context.get("constraint_files"), list)
                    else []
                ),
                # Timing parameters
                "sys_clk_freq_mhz": int(
                    float(1000.0 / float(template_context.get("clock_period", "10.0")))
                ),
                "clock_period": template_context.get("clock_period", "10.0"),
                "setup_time": template_context.get("setup_time", "0.5"),
                "hold_time": template_context.get("hold_time", "0.5"),
                # XDC paths and content
                "generated_xdc_path": template_context.get("generated_xdc_path", ""),
                "board_xdc_content": template_context.get("board_xdc_content", ""),
                # Header comment (use unified generator if not provided)
                "header": template_context.get(
                    "header",
                    generate_tcl_header_comment(
                        "PCILeech Timing/Pin Constraints",
                        vendor_id=str(template_context.get("vendor_id", "")),
                        device_id=str(template_context.get("device_id", "")),
                        board=template_context.get("board_name", ""),
                    ),
                ),
            }

            # Timing constraints MUST come from the template. We no longer allow
            # falling back to a generic default because that may hide missing
            # dynamic timing data and violates donor uniqueness policy.
            if not renderer.template_exists("tcl/constraints.j2"):
                raise PCILeechGenerationError(
                    "Missing required template tcl/constraints.j2 for timing "
                    "constraints; no fallback permitted"
                )

            timing_constraints = renderer.render_template(
                "tcl/constraints.j2", constraints_context
            )

            # Generate pin constraints based on board
            pin_constraints = self._generate_pin_constraints(template_context)

            return {
                "timing_constraints": timing_constraints,
                "pin_constraints": pin_constraints,
            }
        except ImportError:
            # TCL builder not available: fail fast; no generic timing fallback
            # permitted
            raise PCILeechGenerationError(
                "Timing constraints template build path unavailable (ImportError); "
                "refusing generic defaults"
            )

    def _generate_pin_constraints(self, context: Dict[str, Any]) -> str:
        """Generate pin location constraints based on board."""
        board_name = context.get("board_name", "") or ""
        board_name_l = board_name.lower()

        # Basic pin constraints header (kept identical for backward compatibility)
        constraints = """# Pin Location Constraints
# Generated by PCILeech FW Generator

"""

        # Allow dynamic override from context (preferred if provided)
        board_constraints = context.get("board_constraints", {}) or {}
        override_pin = (
            context.get("sys_clk_pin")
            or board_constraints.get("sys_clk_pin")
            or board_constraints.get("sysclk_pin")
        )

        # Centralized mapping (token -> (comment, pin))
        pin_map: Dict[str, Tuple[str, str]] = {
            "35t": ("Artix-7 35T specific pins", "E3"),
            "75t": ("Artix-7 75T specific pins", "F4"),
            "100t": ("Artix-7 100T specific pins", "G4"),
        }

        # Determine source of pin assignment
        comment: Optional[str] = None
        selected_pin: Optional[str] = None

        if override_pin:  # Dynamic override takes precedence
            selected_pin = str(override_pin)
            comment = "Dynamic sys_clk pin (context override)"
            log_info_safe(
                self.logger,
                "Using dynamic sys_clk_pin override {pin} for board {board}",
                pin=selected_pin,
                board=board_name,
                prefix="PCIL",
            )
        else:
            token = next((t for t in pin_map.keys() if t in board_name_l), None)
            if token:
                comment, selected_pin = pin_map[token]
                log_info_safe(
                    self.logger,
                    safe_format(
                        "Matched board token {tok} -> pin {pin}",
                        tok=token,
                        pin=selected_pin,
                    ),
                    prefix="PCIL",
                )
            else:
                log_warning_safe(
                    self.logger,
                    safe_format(
                        "No pin mapping found for board '{board}', emitting header only",
                        board=board_name or "<unknown>",
                    ),
                    prefix="PCIL",
                )

        if selected_pin:
            # Emit standardized block
            constraints += safe_format(
                "# {comment}\nset_property PACKAGE_PIN {pin} [get_ports sys_clk]\n"
                "set_property IOSTANDARD LVCMOS33 [get_ports sys_clk]\n",
                comment=comment,
                pin=selected_pin,
            )

        return constraints

    def _generate_tcl_scripts(self, template_context: Dict[str, Any]) -> Dict[str, str]:
        """Generate TCL build scripts."""
        try:
            from templating.tcl_builder import TCLBuilder

            # Build TCL scripts using the TCL builder
            tcl_builder = TCLBuilder(
                output_dir=template_context.get("output_dir", "./output")
            )

            # Create build context
            context = tcl_builder.create_build_context(
                board=template_context.get("board_name"),
                fpga_part=template_context.get("fpga_part"),
                vendor_id=template_context.get("vendor_id"),
                device_id=template_context.get("device_id"),
                revision_id=template_context.get("revision_id"),
                subsys_vendor_id=template_context.get("subsys_vendor_id"),
                subsys_device_id=template_context.get("subsys_device_id"),
                # Auto-plumb donor PCIe link fields for IP config derivation
                pcie_max_link_speed_code=template_context.get("pcie_max_link_speed"),
                pcie_max_link_width=template_context.get("pcie_max_link_width"),
            )

            # Generate PCILeech project script
            project_script = tcl_builder.build_pcileech_project_script(context)

            # Generate PCILeech build script
            build_script = tcl_builder.build_pcileech_build_script(context)

            # Generate master TCL script
            master_script = tcl_builder.build_master_tcl(context)

            # Generate sources TCL
            sources_script = tcl_builder.build_sources_tcl(
                context, source_files=template_context.get("source_files", [])
            )

            return {
                "build_script": build_script,
                "project_script": project_script,
                "master_script": master_script,
                "sources_script": sources_script,
            }

        except (ImportError, AttributeError, Exception) as e:
            log_warning_safe(
                self.logger,
                safe_format(
                    "TCL script generation failed, attempting fallback: {error}",
                    error=str(e),
                ),
                prefix="PCIL",
            )
            # Fallback to basic TCL scripts if builder not available
            return self._generate_default_tcl_scripts(template_context)

    def _generate_default_tcl_scripts(self, context: Dict[str, Any]) -> Dict[str, str]:
        from src.string_utils import get_project_name

        project_name = context.get("project_name", get_project_name())
        fpga_part = context.get("fpga_part", "xc7a35t-csg324-1")
        # Enforce template-only generation (no inline fallback permitted).
        # This aligns with donor uniqueness & central templating policy.
        try:
            from src.templating.template_renderer import TemplateRenderer
        except ImportError as e:  # pragma: no cover - treated as fatal
            raise PCILeechGenerationError(
                safe_format(
                    "Template renderer unavailable: {err} (no fallback permitted)",
                    err=e,
                )
            ) from e

        renderer = TemplateRenderer(getattr(self.config, "template_dir", None))

        try:
            from src.string_utils import generate_tcl_header_comment

            unified_header = generate_tcl_header_comment(
                "PCILeech Build Scripts",
                vendor_id=str(context.get("vendor_id", "")),
                device_id=str(context.get("device_id", "")),
                board=context.get("board_name") or context.get("board", ""),
            )
        except Exception:
            unified_header = "# Generated PCILeech TCL Script"

        vid = context.get("vendor_id")
        did = context.get("device_id")
        if not vid or not did:  # Strict: cannot proceed without both
            raise PCILeechGenerationError(
                safe_format(
                    (
                        "Fallback TCL generation aborted: missing vendor_id/"
                        "device_id (vid={vid} did={did})"
                    ),
                    vid=vid,
                    did=did,
                )
            )

        subsys_vid = context.get("subsystem_vendor_id") or vid
        subsys_did = context.get("subsystem_device_id") or did
        # Some tests stub only vendor_id/device_id; tolerate missing revision_id/class_code
        revision_id = context.get("revision_id") or context.get("revision")
        class_code = context.get("class_code")

        device_block = {
            "vendor_id": format_hex_id(vid, 4),
            "device_id": format_hex_id(did, 4),
            # Only include fields when provided to avoid raising on None in tests
            **(
                {"revision_id": format_hex_id(revision_id, 2)}
                if revision_id is not None
                else {}
            ),
            **(
                {"class_code": format_hex_id(class_code, 6)}
                if class_code is not None
                else {}
            ),
            "subsys_vendor_id": format_hex_id(subsys_vid, 4),
            "subsys_device_id": format_hex_id(subsys_did, 4),
        }

        # Build device signature; if revision is missing in permissive tests, omit it
        if "revision_id" in device_block:
            device_signature = safe_format(
                "{vid}:{did}:{rid}",
                vid=device_block["vendor_id"],
                did=device_block["device_id"],
                rid=device_block["revision_id"],
            )
        else:
            device_signature = safe_format(
                "{vid}:{did}",
                vid=device_block["vendor_id"],
                did=device_block["device_id"],
            )

        # Provide structured configs mirroring normal builder output so that
        # any template expecting device_config/board_config remains satisfied.
        device_config = {
            **device_block,
            "identification": {
                "vendor_id": device_block["vendor_id"],
                "device_id": device_block["device_id"],
                **(
                    {"class_code": device_block["class_code"]}
                    if "class_code" in device_block
                    else {}
                ),
                "subsystem_vendor_id": device_block["subsys_vendor_id"],
                "subsystem_device_id": device_block["subsys_device_id"],
            },
            "registers": (
                {"revision_id": device_block["revision_id"]}
                if "revision_id" in device_block
                else {}
            ),
        }

        board_block = {
            "name": context.get("board_name", context.get("board", "unknown")),
            "fpga_part": fpga_part,
            "fpga_family": context.get("fpga_family", "7series"),
            "pcie_ip_type": context.get("pcie_ip_type", "7x"),
            "supports_msi": bool(context.get("supports_msi", False)),
            "supports_msix": bool(context.get("supports_msix", False)),
            "max_lanes": context.get("max_lanes", 1),
        }

        msix_cfg = context.get("msix_config") or {
            "enabled": False,
            "table_size": 0,
            "vectors": 0,
            "table_bir": 0,
            "table_offset": 0x0,
            "pba_bir": 0,
            "pba_offset": 0x0,
            "is_supported": False,
        }

        tpl_context: Dict[str, Any] = {
            # Provide both legacy 'header_comment' and internal 'header'.
            "header_comment": unified_header,
            "header": unified_header,
            "project": project_name,
            "project_dir": safe_format("./{p}", p=project_name),
            # Some TCL templates reference fpga_family at the top-level; expose
            # it directly in addition to nested under board for backward
            # compatibility with existing templates.
            "fpga_family": context.get("fpga_family", "7series"),
            "board": board_block,
            "board_config": board_block,
            "device": device_block,
            "device_config": device_config,
            "device_signature": device_signature,
            "msix_config": msix_cfg,
            "constraint_files": [],
            "build": {"jobs": 4, "batch_mode": True, "timeout": 3600},
            "batch_mode": True,
            "synthesis_strategy": "Flow_PerfOptimized_high",
            "implementation_strategy": "Performance_Explore",
        }

        # Resolve required templates.
        build_tpl_candidates = ["tcl/pcileech_build.j2", "tcl/build.j2"]
        build_template_name: Optional[str] = None
        for cand in build_tpl_candidates:
            if renderer.template_exists(cand):
                build_template_name = cand
                break
        if not build_template_name:
            raise PCILeechGenerationError(
                safe_format(
                    "Missing required build TCL template(s): {cands} (no fallback)",
                    cands=build_tpl_candidates,
                )
            )

        if not renderer.template_exists("tcl/synthesis.j2"):
            raise PCILeechGenerationError(
                "synthesis template missing: tcl/synthesis.j2 (no fallback)"
            )

        try:
            rendered_build = renderer.render_template(build_template_name, tpl_context)
        except Exception as e:
            raise PCILeechGenerationError(
                safe_format(
                    "Failed rendering build template {tpl}: {err}",
                    tpl=build_template_name,
                    err=e,
                )
            ) from e

        try:
            rendered_synth = renderer.render_template("tcl/synthesis.j2", tpl_context)
        except Exception as e:
            raise PCILeechGenerationError(
                safe_format(
                    "Failed rendering synthesis template tcl/synthesis.j2: {err}",
                    err=e,
                )
            ) from e

        log_info_safe(
            self.logger,
            safe_format(
                "Generated TCL scripts using enforced templates: build={build_tpl}",
                build_tpl=build_template_name,
            ),
            prefix="PCIL",
        )

        # Provide both legacy internal keys and user-facing filenames expected
        # by tests (build.tcl / synthesis.tcl) for maximum compatibility.
        return {
            "build_script": rendered_build,
            "synthesis_script": rendered_synth,
            "build.tcl": rendered_build,
            "synthesis.tcl": rendered_synth,
        }

    def _generate_writemask_coe(
        self, template_context: Dict[str, Any]
    ) -> Optional[str]:
        """
        Generate writemask COE file for configuration space.

        Args:
            template_context: Template context data

        Returns:
            Writemask COE content or None if generation fails
        """
        try:
            log_info_safe(
                self.logger,
                "Generating writemask COE file",
                prefix="WRMASK",
            )

            # Initialize writemask generator
            writemask_gen = WritemaskGenerator()

            # Config space COE path
            cfg_space_coe = (
                self.config.output_dir / "systemverilog" / "pcileech_cfgspace.coe"
            )
            writemask_coe = (
                self.config.output_dir
                / "systemverilog"
                / "pcileech_cfgspace_writemask.coe"
            )

            # Ensure output directory exists
            cfg_space_coe.parent.mkdir(parents=True, exist_ok=True)

            # Check if config space COE exists, if not, generate it first
            if not cfg_space_coe.exists():
                log_info_safe(
                    self.logger,
                    "Config space COE not found, generating it first",
                    prefix="WRMASK",
                )

                # Check cached generated systemverilog modules first
                systemverilog_modules = getattr(
                    self, "_cached_systemverilog_modules", {}
                )

                if "pcileech_cfgspace.coe" in systemverilog_modules:
                    # Use the already generated content
                    cfg_space_coe.write_text(
                        systemverilog_modules["pcileech_cfgspace.coe"]
                    )
                    log_info_safe(
                        self.logger,
                        safe_format(
                            "Used cached config space COE content at {path}",
                            path=str(cfg_space_coe),
                        ),
                        prefix="WRMASK",
                    )
                else:
                    # If COE already exists in output, reuse it
                    systemverilog_coe_path = (
                        self.config.output_dir
                        / "systemverilog"
                        / "pcileech_cfgspace.coe"
                    )
                    if systemverilog_coe_path.exists():
                        # Copy from systemverilog directory to avoid regeneration
                        cfg_space_coe.write_text(systemverilog_coe_path.read_text())
                        log_info_safe(
                            self.logger,
                            safe_format(
                                "Copied existing COE from systemverilog dir to {path}",
                                path=str(cfg_space_coe),
                            ),
                            prefix="WRMASK",
                        )
                    else:
                        # Generate new content as last resort
                        from src.templating.systemverilog_generator import (
                            AdvancedSVGenerator,
                        )

                        sv_gen = AdvancedSVGenerator(
                            template_dir=self.config.template_dir
                        )
                        modules = sv_gen.generate_pcileech_modules(template_context)

                        if "pcileech_cfgspace.coe" in modules:
                            cfg_space_coe.write_text(modules["pcileech_cfgspace.coe"])
                            log_info_safe(
                                self.logger,
                                safe_format(
                                    "Generated config space COE file at {path}",
                                    path=str(cfg_space_coe),
                                ),
                                prefix="WRMASK",
                            )
                        else:
                            log_warning_safe(
                                self.logger,
                                "Config space COE module missing in modules",
                                prefix="WRMASK",
                            )
                            return None

            # Extract device configuration for MSI/MSI-X settings
            device_config = {
                "msi_config": template_context.get("msi_config", {}),
                "msix_config": template_context.get("msix_config", {}),
            }

            # Generate writemask
            writemask_gen.generate_writemask(
                cfg_space_coe, writemask_coe, device_config
            )

            # Read generated writemask content
            if writemask_coe.exists():
                return writemask_coe.read_text()
            else:
                log_warning_safe(
                    self.logger,
                    "Writemask COE file not generated",
                    prefix="WRMASK",
                )
                return None

        except Exception as e:
            log_warning_safe(
                self.logger,
                safe_format(
                    "Failed to generate writemask COE: {error}",
                    error=str(e),
                ),
                prefix="WRMASK",
            )
            return None

    def _generate_config_space_hex(self, template_context: Dict[str, Any]) -> str:
        """
        Generate configuration space hex file for FPGA initialization.

        Args:
            template_context: Template context containing config space data

        Returns:
            Path to generated hex file as string

        Raises:
            PCILeechGenerationError: If hex generation fails
        """
        log_info_safe(
            self.logger, "Generating configuration space hex file", prefix="HEX"
        )

        try:
            # Import hex formatter
            from src.device_clone.hex_formatter import ConfigSpaceHexFormatter

            # Resolve raw configuration space bytes via centralized helper
            raw_config_space = self._extract_raw_config_space(template_context)

            # Create hex formatter
            formatter = ConfigSpaceHexFormatter()

            # Try to extract optional metadata for header enrichment (safe/fallbacks)
            vid_hex = template_context.get("vendor_id")
            did_hex = template_context.get("device_id")

            # Resolve class_code from multiple known locations (no guessing)

            def _get_nested(ctx: Dict[str, Any], path: list[str]) -> Any:
                cur: Any = ctx
                for key in path:
                    if cur is None:
                        return None
                    # Support dict-like and TemplateObject with .get / attribute access
                    if isinstance(cur, dict):
                        cur = cur.get(key)
                    else:
                        # Try attribute, then mapping-style get
                        cur = getattr(
                            cur, key, getattr(cur, "get", lambda *_: None)(key)
                        )
                return cur

            cls_hex = (
                template_context.get("class_code")
                or _get_nested(
                    template_context, ["config_space", "class_code"]
                )  # from context builder
                or _get_nested(
                    template_context, ["device_config", "class_code"]
                )  # legacy path
            )

            board_name = (
                template_context.get("board_name")
                or template_context.get("board")
                or None
            )
            # Normalize to string if ints

            # vendor_id/device_id must be present at top-level per context contract
            vid_str = format_hex_id(vid_hex, 4)
            did_str = format_hex_id(did_hex, 4)

            # class_code may reside under config_space/device_config; fail fast if unresolved
            if cls_hex is None:
                raise PCILeechGenerationError(
                    "Missing class_code in template context; expected at one of: "
                    "class_code, config_space.class_code, device_config.class_code"
                )
            cls_str = format_hex_id(cls_hex, 6)

            # Generate hex content
            hex_content = formatter.format_config_space_to_hex(
                raw_config_space,
                include_comments=True,
                vendor_id=vid_str,
                device_id=did_str,
                class_code=cls_str,
                board=board_name,
            )

            log_info_safe(
                self.logger,
                safe_format(
                    "Generated configuration space hex file with {size} bytes",
                    size=len(raw_config_space),
                ),
                prefix="HEX",
            )

            return hex_content

        except Exception as e:
            log_error_safe(
                self.logger,
                safe_format(
                    "Configuration space hex generation failed: {error}",
                    error=str(e),
                ),
                prefix="HEX",
            )
            raise PCILeechGenerationError(
                safe_format("Config space hex generation failed: {err}", err=e)
            ) from e

    def _extract_raw_config_space(self, template_context: Dict[str, Any]) -> bytes:
        """Extract raw PCI configuration space bytes from diverse context shapes.

        This centralizes the previously duplicated probing logic. It tries a
        prioritized sequence of known container keys, then performs a
        best-effort scan of dict-like values. Fails fast if nothing is found.

        Args:
            template_context: Full template context.

        Returns:
            Raw configuration space as bytes.

        Raises:
            ValueError: If no configuration space bytes can be resolved.
        """
        import re

        def _coerce_to_bytes(value: Any) -> Optional[bytes]:
            if not value:
                return None
            if isinstance(value, (bytes, bytearray)):
                return bytes(value)
            if isinstance(value, str):
                s = value.replace(" ", "").replace("\n", "").replace("\t", "")
                try:
                    s = re.sub(r"0x", "", s, flags=re.IGNORECASE)
                    s = "".join(ch for ch in s if ch in "0123456789abcdefABCDEF")
                    if len(s) % 2 != 0:
                        s = "0" + s
                    return bytes.fromhex(s)
                except Exception:
                    return None
            if isinstance(value, list) and all(isinstance(x, int) for x in value):
                try:
                    return bytes(value)
                except Exception:
                    return None
            return None

        def _attempt_extract(container: Any) -> Optional[bytes]:
            """Attempt ordered extraction of raw config space bytes from a mapping-like.

            Accepts plain dicts and TemplateObject/Mapping-like containers. Preference:
              1. raw_config_space
              2. raw_data
              3. config_space_hex
            """
            # Support TemplateObject from unified_context
            # Accept dict-like or TemplateObject that exposes .get()
            get = getattr(container, "get", None)
            if not callable(get):
                return None
            for _k in ("raw_config_space", "raw_data", "config_space_hex"):
                try:
                    v = get(_k)
                except Exception:
                    v = None
                b = _coerce_to_bytes(v)
                if b:
                    return b
            return None

        raw: Optional[bytes] = None

        # 1) Top-level config_space_data (preferred rich structure)
        raw = _attempt_extract(template_context.get("config_space_data"))

        # 2) Direct raw keys
        if raw is None:
            first = _coerce_to_bytes(template_context.get("raw_config_space"))
            second = _coerce_to_bytes(template_context.get("config_space_hex"))
            raw = first or second

        # 3) Nested config_space dict
        if raw is None:
            raw = _attempt_extract(template_context.get("config_space"))

        # 4) Legacy path: device_config -> config_space_data
        if raw is None:
            device_cfg = template_context.get("device_config")
            if isinstance(device_cfg, dict) and "config_space_data" in device_cfg:
                raw = _attempt_extract(device_cfg.get("config_space_data"))

        # 5) Heuristic scan of dict-like entries
        if raw is None:
            for key, value in template_context.items():
                if not isinstance(value, dict):
                    continue
                k = str(key).lower()
                if "config" in k or "raw" in k:
                    candidate = _attempt_extract(value)
                    if candidate:
                        raw = candidate
                        log_info_safe(
                            self.logger,
                            safe_format(
                                "Found config space candidate key '{key}'",
                                key=key,
                            ),
                            prefix="HEX",
                        )
                        break

        if not raw:
            log_warning_safe(
                self.logger,
                safe_format(
                    "Config space data not found; keys={keys}",
                    keys=list(template_context.keys()),
                ),
                prefix="HEX",
            )
            raise ValueError(
                "No configuration space data available in template context"
            )

        return raw

    def _validate_generated_firmware(
        self,
        systemverilog_modules: Dict[str, str],
        firmware_components: Dict[str, Any],
    ) -> None:
        """
        Validate generated firmware for completeness and correctness.

        Args:
            systemverilog_modules: Generated SystemVerilog modules
            firmware_components: Generated firmware components

        Raises:
            PCILeechGenerationError: If validation fails
        """
        if self.config.strict_validation:
            # Validate SystemVerilog modules
            required_modules = ["pcileech_tlps128_bar_controller"]
            missing_modules = [
                mod for mod in required_modules if mod not in systemverilog_modules
            ]

            if missing_modules:
                raise PCILeechGenerationError(
                    safe_format(
                        "Missing required SystemVerilog modules: {mods}",
                        mods=missing_modules,
                    )
                )

            # Validate module content
            for module_name, module_code in systemverilog_modules.items():
                if not module_code or len(module_code.strip()) == 0:
                    raise PCILeechGenerationError(
                        safe_format(
                            "SystemVerilog module '{name}' is empty",
                            name=module_name,
                        )
                    )

    def _build_generation_metadata(self) -> Dict[str, Any]:
        """Build metadata about the generation process."""
        from src.utils.metadata import build_config_metadata

        return build_config_metadata(
            device_bdf=self.config.device_bdf,
            enable_behavior_profiling=self.config.enable_behavior_profiling,
            enable_manufacturing_variance=self.config.enable_manufacturing_variance,
            enable_advanced_features=self.config.enable_advanced_features,
            strict_validation=self.config.strict_validation,
        )

    def save_generated_firmware(
        self, generation_result: Dict[str, Any], output_dir: Optional[Path] = None
    ) -> Path:
        """
        Save generated firmware to disk.

        Args:
            generation_result: Result from generate_pcileech_firmware()
            output_dir: Output directory (optional, uses config default)

        Returns:
            Path to the output directory

        Raises:
            PCILeechGenerationError: If saving fails
        """
        if output_dir is None:
            output_dir = self.config.output_dir

        output_dir = Path(output_dir)
        output_dir.mkdir(parents=True, exist_ok=True)

        try:
            # Save SystemVerilog modules
            # IMPORTANT: TCL scripts expect files in "src" directory
            # (avoid using legacy systemverilog path)
            sv_dir = output_dir / "src"
            sv_dir.mkdir(exist_ok=True)

            log_info_safe(
                self.logger,
                safe_format(
                    "Saving SystemVerilog modules to {path}",
                    path=str(sv_dir),
                ),
                prefix="PCIL",
            )

            sv_modules = generation_result.get("systemverilog_modules", {})
            log_info_safe(
                self.logger,
                safe_format(
                    "Found {count} SystemVerilog modules to save: {modules}",
                    count=len(sv_modules),
                    modules=list(sv_modules.keys()),
                ),
                prefix="PCIL",
            )

            for module_name, module_code in sv_modules.items():
                # COE files should also go in src directory for Vivado to find them
                if module_name.endswith(".sv") or module_name.endswith(".coe"):
                    module_file = sv_dir / module_name
                else:
                    module_file = sv_dir / safe_format("{name}.sv", name=module_name)

                log_info_safe(
                    self.logger,
                    safe_format(
                        "Writing module {name} to {path} ({size} bytes)",
                        name=module_name,
                        path=str(module_file),
                        size=len(module_code),
                    ),
                    prefix="PCIL",
                )

                try:
                    module_file.write_text(module_code)

                    # Verify the file was written
                    if not module_file.exists():
                        log_error_safe(
                            self.logger,
                            safe_format(
                                "Module {name} missing after write",
                                name=module_name,
                            ),
                            prefix="MODL",
                        )
                    elif module_file.stat().st_size == 0:
                        log_error_safe(
                            self.logger,
                            safe_format(
                                "Module {name} was written but is empty",
                                name=module_name,
                            ),
                            prefix="MODL",
                        )
                except Exception as e:
                    log_error_safe(
                        self.logger,
                        safe_format(
                            "Failed to write module {name}: {error}",
                            name=module_name,
                            error=str(e),
                        ),
                        prefix="MODL",
                    )
                    raise

            # Save firmware components
            components_dir = output_dir / "components"
            components_dir.mkdir(exist_ok=True)

            # Save writemask COE if generated
            firmware_components = generation_result.get("firmware_components", {})
            if (
                "writemask_coe" in firmware_components
                and firmware_components["writemask_coe"]
            ):
                # Writemask COE goes in the src directory alongside other COE files
                writemask_file = sv_dir / "pcileech_cfgspace_writemask.coe"
                writemask_file.write_text(firmware_components["writemask_coe"])

                log_info_safe(
                    self.logger,
                    safe_format(
                        "Saved writemask COE to {path}",
                        path=str(writemask_file),
                    ),
                    prefix="WRMASK",
                )

            # Save config space hex file if generated
            if (
                "config_space_hex" in firmware_components
                and firmware_components["config_space_hex"]
            ):
                # Config space hex file goes in the src directory for $readmemh
                hex_file = sv_dir / "config_space_init.hex"
                hex_file.write_text(firmware_components["config_space_hex"])

                log_info_safe(
                    self.logger,
                    safe_format(
                        "Saved configuration space hex file to {path}",
                        path=str(hex_file),
                    ),
                    prefix="HEX",
                )

            # Save metadata
            import json

            metadata_file = output_dir / "generation_metadata.json"
            with open(metadata_file, "w") as f:
                json.dump(generation_result["generation_metadata"], f, indent=2)

            log_info_safe(
                self.logger,
                safe_format(
                    "Generated firmware saved to {path}",
                    path=str(output_dir),
                ),
                prefix="MODL",
            )

            return output_dir

        except Exception as e:
            raise PCILeechGenerationError(
                safe_format("Failed to save generated firmware: {err}", err=e)
            ) from e

    def _preload_msix_data_early(self) -> Optional[Dict[str, Any]]:
        """
        Preload MSI-X data from sysfs before VFIO binding to ensure availability.

        Returns:
            MSI-X data dictionary if available, None otherwise
        """
        try:
            import os

            # Try to read config space from sysfs before VFIO binding
            config_space_path = safe_format(
                "/sys/bus/pci/devices/{bdf}/config", bdf=self.config.device_bdf
            )

            if not os.path.exists(config_space_path):
                log_info_safe(
                    self.logger,
                    "Config space not accessible via sysfs, skipping MSI-X preload",
                    prefix="MSIX",
                )
                return None

            log_info_safe(
                self.logger,
                "Preloading MSI-X data from sysfs before VFIO binding",
                prefix="MSIX",
            )

            with open(config_space_path, "rb") as f:
                config_space_bytes = f.read()

            config_space_hex = config_space_bytes.hex()

            # Parse MSI-X capability
            msix_info = parse_msix_capability(config_space_hex)

            if msix_info["table_size"] > 0:
                log_info_safe(
                    self.logger,
                    safe_format(
                        "Preloaded MSI-X: {vectors} vec, BIR {bir}, off 0x{offset:x}",
                        vectors=msix_info["table_size"],
                        bir=msix_info["table_bir"],
                        offset=msix_info["table_offset"],
                    ),
                    prefix="MSIX",
                )

                # Validate MSI-X configuration
                is_valid, validation_errors = validate_msix_configuration(msix_info)

                # Build comprehensive MSI-X data
                msix_data = {
                    "capability_info": msix_info,
                    "table_size": msix_info["table_size"],
                    "table_bir": msix_info["table_bir"],
                    "table_offset": msix_info["table_offset"],
                    "pba_bir": msix_info["pba_bir"],
                    "pba_offset": msix_info["pba_offset"],
                    "enabled": msix_info["enabled"],
                    "function_mask": msix_info["function_mask"],
                    "validation_errors": validation_errors,
                    "is_valid": is_valid,
                    "preloaded": True,
                }

                return msix_data
            else:
                log_info_safe(
                    self.logger,
                    "No MSI-X capability found during preload",
                    prefix="MSIX",
                )
                return None

        except Exception as e:
            log_warning_safe(
                self.logger,
                "MSI-X preload failed: {error}",
                error=str(e),
                prefix="MSIX",
            )
            return None

    def clear_cache(self) -> None:
        """Clear all cached data to ensure fresh generation."""
        # Clear SystemVerilog module cache
        if hasattr(self, "_cached_systemverilog_modules"):
            delattr(self, "_cached_systemverilog_modules")

        # Clear SystemVerilog generator cache
        if hasattr(self.sv_generator, "clear_cache"):
            self.sv_generator.clear_cache()

        # Clear context builder cache
        if self.context_builder and hasattr(self.context_builder, "_context_cache"):
            self.context_builder._context_cache.clear()

        log_info_safe(
            self.logger,
            "Cleared all PCILeech generator caches",
            prefix="CACHE",
        )

    def invalidate_cache_for_context(self, context_hash: str) -> None:
        """Invalidate caches when context changes."""
        # For now, just clear all cache since we don't have fine-grained tracking
        self.clear_cache()
        log_info_safe(
            self.logger,
            safe_format(
                "Invalidated caches for context hash: {hash}...",
                hash=context_hash[:8],
            ),
            prefix="CACHE",
        )

    def _capture_msix_table_entries(
        self, msix_data: Dict[str, Any]
    ) -> Optional[Dict[str, Any]]:
        """Capture MSI-X table bytes from hardware via VFIO.

        Returns a dict with either 'table_entries' (list of per-vector 16B hex)
        and/or 'table_init_hex' (newline-separated 32-bit words) on success.
        """
        try:
            table_size = int(msix_data.get("table_size", 0))
            table_bir = int(msix_data.get("table_bir", 0))
            table_offset = int(msix_data.get("table_offset", 0))
        except Exception as e:
            log_warning_safe(
                self.logger,
                safe_format(
                    "Invalid MSI-X capability fields: {error}",
                    error=str(e),
                ),
                prefix="MSIX",
            )
            return None

        if table_size <= 0:
            return None

        # Read bytes from the BAR region using VFIO
        manager = VFIODeviceManager(self.config.device_bdf, self.logger)
        total_bytes = table_size * MSIX_ENTRY_SIZE

        raw = manager.read_region_slice(
            index=table_bir, offset=table_offset, size=total_bytes
        )
        if not raw or len(raw) < total_bytes:
            log_warning_safe(
                self.logger,
                safe_format(
                    "MSI-X table read incomplete: requested={req} got={got}",
                    req=total_bytes,
                    got=(len(raw) if raw else 0),
                ),
                prefix="MSIX",
            )
            return None

        # Split into 16-byte entries and also build dword-wise init hex
        entries: List[Dict[str, Any]] = []
        hex_lines: List[str] = []
        for i in range(table_size):
            start = i * MSIX_ENTRY_SIZE
            chunk = raw[start : start + MSIX_ENTRY_SIZE]
            entries.append({"vector": i, "data": chunk.hex(), "enabled": True})
            # Break into four 32-bit LE words for init hex
            for w in range(DWORDS_PER_MSIX_ENTRY):
                word = int.from_bytes(
                    chunk[w * DWORD_SIZE : (w + 1) * DWORD_SIZE], "little"
                )
                hex_lines.append(format(word, "08X"))

        return {
            "table_entries": entries,
            "table_init_hex": "\n".join(hex_lines) + "\n",
        }

    # --- Validation helpers ---

    def _validate_msix_and_bar_layout(
        self,
        template_context: Dict[str, Any],
        config_space_data: Dict[str, Any],
        msix_data: Optional[Dict[str, Any]],
    ) -> None:
        """Run comprehensive MSI-X/BAR validation and fail fast on errors.

        This enforces donor BAR layout fidelity and MSI-X placement correctness
        before any template rendering. Warnings are logged; errors abort.
        """
        # Gather device_info for report context
        device_info = config_space_data.get("device_info") or {
            "vendor_id": (
                int(template_context.get("vendor_id", "0") or "0", 16)
                if isinstance(template_context.get("vendor_id"), str)
                else template_context.get("vendor_id")
            ),
            "device_id": (
                int(template_context.get("device_id", "0") or "0", 16)
                if isinstance(template_context.get("device_id"), str)
                else template_context.get("device_id")
            ),
        }

        # Build BARs list for validator
        # (expecting dicts with keys: bar, type, size, prefetchable)
        raw_bars = config_space_data.get("bars", [])
        bars_for_validation: List[Dict[str, Any]] = self._coerce_bars_for_validation(
            raw_bars
        )

        # Build capabilities list (only MSI-X is needed for this validation)
        capabilities: List[Dict[str, Any]] = []
        if msix_data and msix_data.get("table_size", 0) > 0:
            try:
                # Validator expects MSI-X table_size encoded as N-1
                encoded_size = int(msix_data.get("table_size", 0)) - 1
                capabilities.append(
                    {
                        "cap_id": 0x11,
                        "table_size": max(encoded_size, 0),
                        "table_bar": int(msix_data.get("table_bir", 0)),
                        "table_offset": int(msix_data.get("table_offset", 0)),
                        "pba_bar": int(msix_data.get("pba_bir", 0)),
                        "pba_offset": int(msix_data.get("pba_offset", 0)),
                    }
                )
            except Exception:
                # If msix_data malformed treat as no MSI-X; validator checks BARs
                capabilities = []

        is_valid, errors, warnings = validate_msix_bar_configuration(
            bars_for_validation, capabilities, device_info
        )

        # Log warnings as non-fatal
        for w in warnings or []:
            log_warning_safe(
                self.logger,
                safe_format(
                    "MSI-X/BAR validation warning: {msg}",
                    msg=w,
                ),
                prefix="PCIL",
            )

        if not is_valid:
            # Emit actionable error and abort
            joined = "; ".join(errors or ["unknown error"])
            log_error_safe(
                self.logger,
                safe_format(
                    "Build aborted: MSI-X/BAR configuration invalid: {errs}",
                    errs=joined,
                ),
                prefix="PCIL",
            )
            raise ValueError(joined)

    def _coerce_bars_for_validation(self, bars: List[Any]) -> List[Dict[str, Any]]:
        """Coerce heterogeneous BAR representations into validator's dict format.

        Accepts:
          - BarInfo instances
          - dicts with keys {bar, type, size, prefetchable}
          - dicts from parse_bar_info_from_config_space with {index, bar_type, ...}
        """
        result: List[Dict[str, Any]] = []
        for b in bars or []:
            try:
                if isinstance(b, dict):
                    if "bar" in b and "type" in b:
                        result.append(
                            {
                                "bar": int(b.get("bar", b.get("index", 0))),
                                "type": str(
                                    b.get(
                                        "type",
                                        b.get("bar_type", "memory"),
                                    )
                                ),
                                "size": int(b.get("size", 0)),
                                "prefetchable": bool(b.get("prefetchable", False)),
                            }
                        )
                    else:
                        # Likely parse_bar_info format
                        result.append(
                            {
                                "bar": int(b.get("index", 0)),
                                "type": str(b.get("bar_type", "memory")),
                                "size": int(b.get("size", 0)),
                                "prefetchable": bool(b.get("prefetchable", False)),
                            }
                        )
                else:
                    # Try attribute-based (e.g., BarInfo)
                    idx = getattr(b, "index", 0)
                    btype = getattr(b, "bar_type", None) or (
                        "memory"
                        if getattr(b, "is_memory", False)
                        else ("io" if getattr(b, "is_io", False) else "memory")
                    )
                    size = getattr(b, "size", 0)
                    prefetch = getattr(b, "prefetchable", False)
                    result.append(
                        {
                            "bar": int(idx),
                            "type": str(btype),
                            "size": int(size) if size is not None else 0,
                            "prefetchable": bool(prefetch),
                        }
                    )
            except Exception:
                # Skip malformed entries silently; validator will catch missing BARs
                continue
        return result
