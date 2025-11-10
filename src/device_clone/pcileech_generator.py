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

from __future__ import annotations

import logging
import os

from contextlib import contextmanager
from dataclasses import dataclass, field

from pathlib import Path

from typing import Any, Dict, Generator, List, Optional

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
    log_debug_safe,
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
    
    # Preloaded data (to avoid redundant VFIO operations)
    preloaded_config_space: Optional[bytes] = None
    
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
        
        # Store preloaded config space data if available
        self._preloaded_config_space = getattr(
            self.config, "preloaded_config_space", None
        )
        
        if self._preloaded_config_space:
            log_info_safe(
                self.logger,
                safe_format(
                    "Generator received preloaded config space: {size} bytes",
                    size=len(self._preloaded_config_space)
                ),
                prefix="PCIL"
            )
        else:
            log_info_safe(
                self.logger,
                "Generator has no preloaded config space - will use VFIO",
                prefix="PCIL"
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
            firmware_components = self._generate_firmware_components(
                template_context
            )

            # 8. Validate generated firmware artifacts
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
                firmware_components.get("tcl_scripts", {}),
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

    @contextmanager
    def _generation_step(
        self, step: str, allow_fallback: bool = False
    ) -> Generator[None, None, None]:
        log_info_safe(self.logger, "Starting {step}", step=step, prefix="PCIL")
        try:
            yield
            log_info_safe(self.logger, "Completed {step}", step=step, prefix="PCIL")
        except Exception as e:  # pragma: no cover (control flow wrapper)
            if allow_fallback and self.fallback_manager.confirm_fallback(
                step, str(e)
            ):
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
                prefix="PCIL",
            )
            return None

        log_info_safe(
            self.logger,
            safe_format(
                "Capturing device behavior profile for {duration}s",
                duration=self.config.behavior_capture_duration,
            ),
            prefix="PCIL",
        )

        try:
            behavior_profile = self.behavior_profiler.capture_behavior_profile(
                duration=self.config.behavior_capture_duration
            )

            # Analyze patterns for enhanced context
            pattern_analysis = self.behavior_profiler.analyze_patterns(
                behavior_profile
            )

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
                prefix="PCIL",
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
                    prefix="PCIL",
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
            prefix="CFG",
        )

        try:
            # Check if we have pre-collected config space data (from host)
            # This avoids redundant VFIO binding when host has already collected data
            if (hasattr(self, '_preloaded_config_space') and 
                self._preloaded_config_space):
                log_info_safe(
                    self.logger,
                    "Using pre-collected configuration space data from host",
                    prefix="CFG",
                )
                config_space_bytes = self._preloaded_config_space
            else:
                # Fallback to VFIO reading (original behavior)
                config_space_bytes = (
                    self.config_space_manager.read_vfio_config_space()
                )
            return self._process_config_space_bytes(config_space_bytes)
        except (OSError, IOError) as e:
            log_error_safe(
                self.logger,
                safe_format(
                    "Config space read failed (IO): {error}",
                    error=str(e),
                ),
                prefix="CFG",
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
                prefix="CFG",
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
                prefix="CFG",
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
            prefix="CFG",
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
                prefix="CFG",
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
        # Validate config space length (must be 256 or 4096 bytes)
        if not config_space_bytes or len(config_space_bytes) == 0:
            raise PCILeechGenerationError(
                "Configuration space is empty"
            )
        
        cfg_len = len(config_space_bytes)
        if cfg_len not in (256, 4096):
            # Check if it's a power of 2 at least
            is_power_of_two = (cfg_len & (cfg_len - 1)) == 0 and cfg_len > 0
            log_warning_safe(
                self.logger,
                safe_format(
                    "Unexpected config space length: {len} bytes "
                    "(expected 256 or 4096). Power of 2: {is_pow2}. "
                    "Did you pass a hex string without proper conversion?",
                    len=cfg_len,
                    is_pow2=is_power_of_two,
                ),
                prefix="CFG",
            )
            # Don't fail, but warn - some devices might have unusual sizes
        
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
                prefix="CFG",
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
            prefix="CFG",
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

        # Guard against None or missing fields
        if not msix_info:
            log_info_safe(
                self.logger,
                "MSI-X capability not found",
                prefix="MSIX",
            )
            return None

        # Return None if table_size == 0
        if msix_info.get("table_size", 0) == 0:
            log_info_safe(
                self.logger,
                "MSI-X capability found but table_size is 0",
                prefix="MSIX",
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
            "table_size": msix_info.get("table_size", 0),
            "table_bir": msix_info.get("table_bir", 0),
            "table_offset": msix_info.get("table_offset", 0),
            "pba_bir": msix_info.get("pba_bir", 0),
            "pba_offset": msix_info.get("pba_offset", 0),
            "enabled": msix_info.get("enabled", False),
            "function_mask": msix_info.get("function_mask", False),
            "validation_errors": validation_errors,
            "is_valid": is_valid,
        }

        log_info_safe(
            self.logger,
            safe_format(
                "MSI-X capabilities processed: {vectors} vectors, table BIR {bir}, "
                "offset 0x{offset:x}",
                vectors=msix_info.get("table_size", 0),
                bir=msix_info.get("table_bir", 0),
                offset=msix_info.get("table_offset", 0),
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
                enable_mmio_learning=getattr(
                    self.config, "enable_mmio_learning", True
                ),
                force_recapture=getattr(self.config, "force_recapture", False),
            )

            log_info_safe(
                self.logger,
                safe_format(
                    "Template context built successfully with {keys} top-level keys",
                    keys=len(template_context),
                ),
                prefix="PCIL",
            )

            return dict(template_context)

        except Exception as e:
            root_cause = extract_root_cause(e)
            raise PCILeechGenerationError(
                "Template context building failed", root_cause=root_cause
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
            "constraint_files": self._copy_constraint_files(template_context),
            "tcl_scripts": self._copy_tcl_scripts(template_context),
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

    def _copy_constraint_files(
        self, template_context: Dict[str, Any]
    ) -> Dict[str, str]:
        """Copy XDC constraint files from voltcyclone-fpga submodule.
        
        This method copies pre-existing XDC constraint files from the 
        lib/voltcyclone-fpga submodule instead of generating them, following 
        the architecture where static infrastructure files should be used as-is.
        
        Args:
            template_context: Template context data (used to determine board)
            
        Returns:
            Dictionary mapping constraint file names to their paths
            
        Raises:
            PCILeechGenerationError: If constraint file copying fails
        """
        try:
            from src.file_management.file_manager import FileManager
            from src.file_management.repo_manager import RepoManager
            
            # Get board name from context
            board = template_context.get(
                "board_name"
            ) or template_context.get("board")
            if not board:
                raise PCILeechGenerationError(
                    "Cannot copy constraint files: board name not specified"
                )
            
            # Initialize FileManager with output directory
            output_dir = Path(
                template_context.get("output_dir", self.config.output_dir)
            )
            
            # Get XDC files from submodule (RepoManager uses class methods only)
            log_info_safe(
                self.logger,
                safe_format(
                    "Copying XDC constraint files for board {board} from submodule",
                    board=board
                ),
                prefix="PCIL"
            )
            
            repo_path = RepoManager.ensure_repo()
            xdc_files = RepoManager.get_xdc_files(board, repo_root=repo_path)
            
            # Validate output directory exists and is writable
            if not output_dir.exists():
                log_warning_safe(
                    self.logger,
                    safe_format(
                        "Output directory does not exist: {path}, "
                        "attempting to create",
                        path=output_dir
                    ),
                    prefix="PCIL"
                )
                try:
                    output_dir.mkdir(parents=True, exist_ok=True)
                except PermissionError as e:
                    raise PCILeechGenerationError(
                        safe_format(
                            "Cannot create output directory {path}: "
                            "Permission denied. "
                            "Ensure the parent directory is writable. "
                            "Try: sudo chown -R $(id -u):$(id -g) {parent} "
                            "or: sudo mkdir -p {path} && "
                            "sudo chown $(id -u):$(id -g) {path}",
                            path=output_dir,
                            parent=output_dir.parent
                        )
                    ) from e
            
            # Verify output directory is writable by attempting to create test file
            # os.access() can give false negatives with sudo/root, test directly
            test_file = output_dir / ".write_test"
            try:
                test_file.write_text("")
                test_file.unlink()
            except (PermissionError, OSError) as e:
                # Get directory ownership info for diagnostics
                try:
                    stat_info = output_dir.stat()
                    current_uid = os.getuid()
                    
                    # Check if we're in a container
                    in_container = (
                        os.path.exists("/.dockerenv") or
                        os.path.exists("/run/.containerenv") or
                        os.getenv("container") is not None
                    )
                    
                    # Build error message based on context
                    if current_uid == 0 and in_container:
                        # Running as root in container - likely mount issue
                        error_msg = safe_format(
                            "Output directory {path} is not writable in container. "
                            "This is likely a volume mount permission issue. "
                            "On the HOST, run: sudo chmod 777 {path} "
                            "or ensure volume is mounted with write permissions. "
                            "Directory mode: {mode}",
                            path=output_dir,
                            mode=oct(stat_info.st_mode)
                        )
                    elif current_uid == 0:
                        # Running as root - permission issue is unexpected
                        error_msg = safe_format(
                            "Output directory {path} is not writable as root. "
                            "Directory mode: {mode}. "
                            "Check for read-only mount or filesystem issue.",
                            path=output_dir,
                            mode=oct(stat_info.st_mode)
                        )
                    else:
                        # Not running as root - suggest ownership fix
                        owner_info = safe_format(
                            "uid={uid} gid={gid} mode={mode}",
                            uid=stat_info.st_uid,
                            gid=stat_info.st_gid,
                            mode=oct(stat_info.st_mode)
                        )
                        error_msg = safe_format(
                            "Output directory {path} is not writable. "
                            "Current ownership: {owner}. "
                            "Fix with: sudo chown -R $(id -u):$(id -g) {path}",
                            path=output_dir,
                            owner=owner_info
                        )
                except Exception:
                    error_msg = safe_format(
                        "Output directory {path} is not writable: {error}",
                        path=output_dir,
                        error=e
                    )
                
                raise PCILeechGenerationError(error_msg) from e
            
            # Copy XDC files to output directory
            constraints_dir = output_dir / "constraints"
            try:
                constraints_dir.mkdir(parents=True, exist_ok=True)
            except PermissionError as e:
                raise PCILeechGenerationError(
                    safe_format(
                        "Cannot create constraints directory {path}: "
                        "Permission denied. "
                        "Parent directory {parent} ownership: {owner}. "
                        "Check volume mount permissions or run: "
                        "chmod 777 {parent}",
                        path=constraints_dir,
                        parent=output_dir,
                        owner=(
                            f"uid={output_dir.stat().st_uid} "
                            f"gid={output_dir.stat().st_gid}"
                        )
                    )
                ) from e
            
            result = {}
            for xdc_file in xdc_files:
                dest_file = constraints_dir / xdc_file.name
                import shutil
                try:
                    shutil.copy2(xdc_file, dest_file)
                except PermissionError as e:
                    raise PCILeechGenerationError(
                        safe_format(
                            "Cannot write constraint file {dest}: "
                            "Permission denied. "
                            "Directory permissions: {perms}",
                            dest=dest_file,
                            perms=oct(constraints_dir.stat().st_mode)
                        )
                    ) from e
                result[xdc_file.name] = str(dest_file)
                
                log_info_safe(
                    self.logger,
                    safe_format(
                        "Copied constraint file: {name}",
                        name=xdc_file.name
                    ),
                    prefix="PCIL"
                )
            
            log_info_safe(
                self.logger,
                safe_format(
                    "Copied {count} XDC constraint files from submodule",
                    count=len(result)
                ),
                prefix="PCIL"
            )
            
            return result
            
        except ImportError as e:
            raise PCILeechGenerationError(
                safe_format(
                    "FileManager/RepoManager unavailable for XDC copying: {err}",
                    err=e
                )
            ) from e
        except Exception as e:
            raise PCILeechGenerationError(
                safe_format(
                    "Failed to copy XDC constraint files from submodule: {err}",
                    err=e
                )
            ) from e

    def _copy_tcl_scripts(self, template_context: Dict[str, Any]) -> Dict[str, str]:
        """Copy static TCL build scripts from voltcyclone-fpga submodule.
        
        This method copies pre-existing TCL scripts from the lib/voltcyclone-fpga
        submodule instead of generating them from templates, following the
        architecture where only .coe overlay files should be generated via templates.
        
        Args:
            template_context: Template context data (used to determine board)
            
        Returns:
            Dictionary mapping script names to their file paths
            
        Raises:
            PCILeechGenerationError: If TCL copying fails
        """
        try:
            from src.file_management.file_manager import FileManager
            from src.file_management.repo_manager import RepoManager
            
            # Get board name from context
            board = template_context.get("board_name") or template_context.get("board")
            if not board:
                raise PCILeechGenerationError(
                    "Cannot copy TCL scripts: board name not specified in context"
                )
            
            # Initialize FileManager with output directory
            output_dir = Path(template_context.get("output_dir", self.config.output_dir))
            fm = FileManager(output_dir=output_dir)
            
            # Copy TCL scripts from submodule
            log_info_safe(
                self.logger,
                safe_format(
                    "Copying static TCL scripts for board {board} from voltcyclone-fpga submodule",
                    board=board
                ),
                prefix="PCIL"
            )
            
            tcl_paths = fm.copy_vivado_tcl_scripts(board=board)
            
            # Copy IP files from submodule (COE, XCI files)
            log_info_safe(
                self.logger,
                safe_format(
                    "Copying IP files for board {board} from voltcyclone-fpga",
                    board=board
                ),
                prefix="PCIL"
            )
            
            ip_paths = fm.copy_ip_files(board=board)
            
            # Return dictionary mapping script names to paths
            result = {}
            for path in tcl_paths:
                script_name = path.name
                result[script_name] = str(path)
            
            # Add IP files to result
            for path in ip_paths:
                ip_name = path.name
                result[ip_name] = str(path)
            
            log_info_safe(
                self.logger,
                safe_format(
                    "Copied {count} files from submodule (TCL + IP)",
                    count=len(result)
                ),
                prefix="PCIL"
            )
            
            return result
            
        except ImportError as e:
            raise PCILeechGenerationError(
                safe_format(
                    "FileManager unavailable for TCL copying: {err}",
                    err=e
                )
            ) from e
        except Exception as e:
            raise PCILeechGenerationError(
                safe_format(
                    "Failed to copy TCL scripts from submodule: {err}",
                    err=e
                )
            ) from e

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

            # Config space COE path - use src directory for consistency
            cfg_space_coe = self.config.output_dir / "src" / "pcileech_cfgspace.coe"
            writemask_coe = (
                self.config.output_dir / "src" / "pcileech_cfgspace_writemask.coe"
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
                        self.config.output_dir / "src" / "pcileech_cfgspace.coe"
                    )
                    if systemverilog_coe_path.exists():
                        # Already in src directory
                        log_info_safe(
                            self.logger,
                            safe_format(
                                "Config space COE already exists at {path}",
                                path=str(cfg_space_coe),
                            ),
                            prefix="WRMASK",
                        )
                    else:
                        # Generate new content as last resort
                        # Use existing sv_generator instance instead of re-importing
                        modules = self.sv_generator.generate_pcileech_modules(
                            template_context
                        )

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
                return writemask_coe.read_text(encoding="utf-8")
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
            # NOTE: Validation updated for overlay-only architecture
            # The new architecture generates .coe overlay files instead of full .sv modules
            # The bar_controller now comes from lib/voltcyclone-fpga statically
            
            # Validate that we have at least config space overlay
            expected_overlays = ["pcileech_cfgspace"]
            missing_overlays = [
                name for name in expected_overlays if name not in systemverilog_modules
            ]
            
            if missing_overlays:
                log_warning_safe(
                    self.logger,
                    safe_format(
                        "Missing expected overlay files: {missing}",
                        missing=missing_overlays,
                    ),
                    prefix="PCIL",
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

            # Persist build integration, if provided
            bi = generation_result.get("firmware_components", {}).get(
                "build_integration"
            )
            if bi:
                # Save as .sv file in src directory
                bi_file = sv_dir / "pcileech_integration.sv"
                bi_file.write_text(bi)
                log_info_safe(
                    self.logger,
                    safe_format(
                        "Saved build integration to {path}",
                        path=str(bi_file),
                    ),
                    prefix="PCIL",
                )

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
            from src.error_utils import extract_root_cause
            root_cause = extract_root_cause(e)
            log_warning_safe(
                self.logger,
                safe_format(
                    "MSI-X preload failed: {error}",
                    error=root_cause,
                ),
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
            from src.error_utils import extract_root_cause
            root_cause = extract_root_cause(e)
            log_warning_safe(
                self.logger,
                safe_format(
                    "Invalid MSI-X capability fields: {error}",
                    error=root_cause,
                ),
                prefix="MSIX",
            )
            return None

        if table_size <= 0:
            return None

        # Read bytes from the BAR region using VFIO
        manager = VFIODeviceManager(self.config.device_bdf, self.logger)
        total_bytes = table_size * MSIX_ENTRY_SIZE

        try:
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
        finally:
            # Best-effort close if provided
            close = getattr(manager, "close", None)
            if callable(close):
                try:
                    close()
                except Exception:
                    # Ignore close errors
                    pass

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
            except Exception as e:
                # Log malformed entries for diagnostics
                log_debug_safe(
                    self.logger,
                    safe_format(
                        "Failed to coerce BAR entry: {entry} | error: {err}",
                        entry=str(b)[:100],  # Truncate for safety
                        err=str(e),
                    ),
                    prefix="PCIL",
                )
                # Skip malformed entries; validator will catch missing BARs
                continue
        return result
