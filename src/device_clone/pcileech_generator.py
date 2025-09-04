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
from typing import Any, Dict, List, Optional, Tuple

# Import existing infrastructure components
from src.device_clone.behavior_profiler import BehaviorProfile, BehaviorProfiler
from src.device_clone.config_space_manager import ConfigSpaceManager
from src.device_clone.msix_capability import (
    parse_msix_capability,
    validate_msix_configuration,
)
from src.device_clone.pcileech_context import (
    PCILeechContextBuilder,
    VFIODeviceManager,
)
from src.device_clone.writemask_generator import WritemaskGenerator
from src.error_utils import extract_root_cause
from src.exceptions import PCILeechGenerationError, PlatformCompatibilityError

# Import from centralized locations
from src.string_utils import log_error_safe, log_info_safe, log_warning_safe
from src.templating import AdvancedSVGenerator, TemplateRenderer, TemplateRenderError
from src.utils.attribute_access import has_attr, safe_get_attr

logger = logging.getLogger(__name__)

# Data sizing constants for MSI-X handling
MSIX_ENTRY_SIZE = 16  # bytes per MSI-X table entry
DWORD_SIZE = 4  # bytes per 32-bit word
DWORDS_PER_MSIX_ENTRY = MSIX_ENTRY_SIZE // DWORD_SIZE


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
                f"Failed to initialize PCILeech generator: {e}"
            ) from e

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
        """
        Generate complete PCILeech firmware with dynamic data integration.

        Returns:
            Dictionary containing generated firmware components and metadata

        Raises:
            PCILeechGenerationError: If generation fails at any stage
        """
        log_info_safe(
            self.logger,
            "Starting PCILeech firmware generation for device {bdf}",
            bdf=self.config.device_bdf,
            prefix="PCIL",
        )

        try:
            # Step 0: Try to preload MSI-X data before any VFIO operations
            preloaded_msix = self._preload_msix_data_early()

            # Use a single VFIO binding session for both config space reading and BAR analysis
            # This prevents the cleanup from happening too early
            from src.cli.vfio_handler import VFIOBinder

            # Step 1: Capture device behavior profile (doesn't need VFIO)
            behavior_profile = self._capture_device_behavior()

            # Steps 2-4: Perform all VFIO-dependent operations within a single context
            with VFIOBinder(self.config.device_bdf) as vfio_device_path:
                log_info_safe(
                    self.logger,
                    "VFIO binding established for device {bdf} at {path}",
                    bdf=self.config.device_bdf,
                    path=vfio_device_path,
                    prefix="VFIO",
                )

                # Step 2: Analyze configuration space (with VFIO active)
                config_space_data = self._analyze_configuration_space_with_vfio()

                # Step 3: Process MSI-X capabilities (prefer preloaded data)
                msix_data = preloaded_msix or self._process_msix_capabilities(
                    config_space_data
                )

                # Step 3a: If MSI-X capability present, capture actual table bytes via VFIO
                if msix_data and msix_data.get("table_size", 0) > 0:
                    try:
                        captured = self._capture_msix_table_entries(msix_data)
                        if captured:
                            # Merge captured entries/init hex into msix_data
                            msix_data.update(captured)
                            log_info_safe(
                                self.logger,
                                "Captured MSI-X table: {vectors} vectors",
                                vectors=msix_data.get("table_size", 0),
                                prefix="MSIX",
                            )
                        else:
                            log_warning_safe(
                                self.logger,
                                "MSI-X table capture returned no data",
                                prefix="MSIX",
                            )
                    except Exception as e:
                        log_warning_safe(
                            self.logger,
                            "MSI-X table capture failed: {error}",
                            error=str(e),
                            prefix="MSIX",
                        )

                # Step 3a: Handle interrupt strategy fallback if MSI-X not available
                if msix_data is None or msix_data.get("table_size", 0) == 0:
                    log_info_safe(
                        self.logger,
                        "MSI-X not available, checking for MSI capability",
                        prefix="PCIL",
                    )
                    # Check for MSI capability (ID 0x05)
                    config_space_hex = config_space_data.get("config_space_hex", "")
                    if config_space_hex:
                        from src.device_clone.msix_capability import find_cap

                        msi_cap = find_cap(config_space_hex, 0x05)
                        if msi_cap is not None:
                            log_info_safe(
                                self.logger,
                                "MSI capability found, using MSI with 1 vector",
                                prefix="PCIL",
                            )
                            interrupt_strategy = "msi"
                            interrupt_vectors = 1
                        else:
                            log_info_safe(
                                self.logger,
                                "No MSI capability found, using INTx",
                                prefix="PCIL",
                            )
                            interrupt_strategy = "intx"
                            interrupt_vectors = 1
                    else:
                        log_info_safe(
                            self.logger,
                            "No config space data, defaulting to INTx",
                            prefix="PCIL",
                        )
                        interrupt_strategy = "intx"
                        interrupt_vectors = 1
                else:
                    interrupt_strategy = "msix"
                    interrupt_vectors = msix_data["table_size"]

                # Step 4: Build comprehensive template context (with VFIO still active for BAR analysis)
                template_context = self._build_template_context(
                    behavior_profile,
                    config_space_data,
                    msix_data,
                    interrupt_strategy,
                    interrupt_vectors,
                )

                # Ensure msix_data is embedded so SV generator can access it
                if isinstance(template_context, dict):
                    template_context.setdefault("msix_data", msix_data)

            # VFIO cleanup happens here automatically when exiting the 'with' block
            log_info_safe(
                self.logger,
                "VFIO binding cleanup completed for device {bdf}",
                bdf=self.config.device_bdf,
                prefix="VFIO",
            )

            # Step 5: Generate SystemVerilog modules (no VFIO needed)
            systemverilog_modules = self._generate_systemverilog_modules(
                template_context
            )

            # Step 6: Generate additional firmware components
            firmware_components = self._generate_firmware_components(template_context)

            # Step 7: Validate generated firmware
            self._validate_generated_firmware(
                systemverilog_modules, firmware_components
            )

            # Compile results
            generation_result = {
                "device_bdf": self.config.device_bdf,
                "generation_timestamp": self._get_timestamp(),
                "behavior_profile": behavior_profile,
                "config_space_data": config_space_data,
                "msix_data": msix_data,
                "template_context": template_context,
                "systemverilog_modules": systemverilog_modules,
                "firmware_components": firmware_components,
                "generation_metadata": self._build_generation_metadata(),
            }

            log_info_safe(
                self.logger,
                "PCILeech firmware generation completed successfully",
                prefix="PCIL",
            )

            return generation_result

        except PlatformCompatibilityError:
            # For platform compatibility issues, don't log additional error messages
            # The original detailed error was already logged
            raise
        except Exception as e:
            log_error_safe(
                self.logger,
                "PCILeech firmware generation failed: {error}",
                error=str(e),
                prefix="PCIL",
            )
            root_cause = extract_root_cause(e)
            raise PCILeechGenerationError(
                "Firmware generation failed", root_cause=root_cause
            )

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
            )
            return None

        log_info_safe(
            self.logger,
            "Capturing device behavior profile for {duration}s",
            duration=self.config.behavior_capture_duration,
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
                "Captured {accesses} register accesses with {patterns} timing patterns",
                accesses=behavior_profile.total_accesses,
                patterns=len(behavior_profile.timing_patterns),
                prefix="MSIX",
            )

            return behavior_profile

        except Exception as e:
            # Behavior profiling is optional - can use fallback manager
            details = "Without behavior profiling, the generated firmware may not accurately reflect device timing patterns and behavior."

            if self.fallback_manager.confirm_fallback(
                "behavior-profiling", str(e), details=details
            ):
                log_warning_safe(
                    self.logger,
                    "Device behavior profiling failed, continuing without profile: {error}",
                    error=str(e),
                    prefix="MSIX",
                )
                return None
            else:
                raise PCILeechGenerationError(
                    f"Device behavior profiling failed: {e}"
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
            "Analyzing configuration space for device {bdf}",
            bdf=self.config.device_bdf,
            prefix="MSIX",
        )

        try:
            # Read configuration space
            config_space_bytes = self.config_space_manager.read_vfio_config_space()
            return self._process_config_space_bytes(config_space_bytes)

        except Exception as e:
            # Configuration space is critical for device identity - MUST FAIL, no fallbacks allowed
            log_error_safe(
                self.logger,
                "CRITICAL: Configuration space analysis failed - cannot continue without device identity: {error}",
                error=str(e),
                prefix="MSIX",
            )
            raise PCILeechGenerationError(
                f"Configuration space analysis failed (critical for device identity): {e}"
            ) from e

    def _analyze_configuration_space_with_vfio(self) -> Dict[str, Any]:
        """
        Analyze device configuration space when VFIO is already bound.

        This method assumes VFIO is already active and doesn't create its own binding.

        Returns:
            Dictionary containing configuration space data and analysis

        Raises:
            PCILeechGenerationError: If configuration space analysis fails
        """
        log_info_safe(
            self.logger,
            "Analyzing configuration space for device {bdf} (VFIO already active)",
            bdf=self.config.device_bdf,
            prefix="MSIX",
        )

        try:
            # Read configuration space without creating new VFIO binding
            config_space_bytes = self.config_space_manager._read_sysfs_config_space()
            return self._process_config_space_bytes(config_space_bytes)

        except Exception as e:
            # Configuration space is critical for device identity - MUST FAIL, no fallbacks allowed
            log_error_safe(
                self.logger,
                "CRITICAL: Configuration space analysis failed - cannot continue without device identity: {error}",
                error=str(e),
                prefix="MSIX",
            )
            raise PCILeechGenerationError(
                f"Configuration space analysis failed (critical for device identity): {e}"
            ) from e

    def _process_config_space_bytes(self, config_space_bytes: bytes) -> Dict[str, Any]:
        """
        Process configuration space bytes into a comprehensive data structure.

        This consolidates the duplicate logic from both _analyze_configuration_space methods.
        The PCILeechContextBuilder will handle device info enhancement, so we don't need
        to duplicate that work here.

        Args:
            config_space_bytes: Raw configuration space bytes

        Returns:
            Dictionary containing configuration space data

        Raises:
            PCILeechGenerationError: If critical fields are missing
        """
        # Extract device information using ConfigSpaceManager
        device_info = self.config_space_manager.extract_device_info(config_space_bytes)

        # Validate critical fields (vendor_id and device_id)
        # ConfigSpaceManager should always extract these, but validate just in case
        if not device_info.get("vendor_id"):
            if len(config_space_bytes) >= 2:
                device_info["vendor_id"] = int.from_bytes(
                    config_space_bytes[0:2], "little"
                )
            else:
                raise PCILeechGenerationError(
                    "Cannot determine vendor ID - device identity unknown"
                )

        if not device_info.get("device_id"):
            if len(config_space_bytes) >= 4:
                device_info["device_id"] = int.from_bytes(
                    config_space_bytes[2:4], "little"
                )
            else:
                raise PCILeechGenerationError(
                    "Cannot determine device ID - device identity unknown"
                )

        # Build configuration space data structure
        config_space_data = {
            "raw_config_space": config_space_bytes,
            "config_space_hex": config_space_bytes.hex(),
            "device_info": device_info,
            "vendor_id": f"{device_info.get('vendor_id', 0):04x}",
            "device_id": f"{device_info.get('device_id', 0):04x}",
            "class_code": f"{device_info.get('class_code', 0):06x}",
            "revision_id": f"{device_info.get('revision_id', 0):02x}",
            "bars": device_info.get("bars", []),
            "config_space_size": len(config_space_bytes),
        }

        log_info_safe(
            self.logger,
            "Configuration space processed: VID={vendor_id}, DID={device_id}, Class={class_code}",
            vendor_id=device_info.get("vendor_id", 0),
            device_id=device_info.get("device_id", 0),
            class_code=device_info.get("class_code", 0),
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
            log_info_safe(self.logger, "MSI-X capability not found or table_size is 0")
            return None

        # Validate MSI-X configuration
        is_valid, validation_errors = validate_msix_configuration(msix_info)

        if not is_valid and self.config.strict_validation:
            log_warning_safe(
                self.logger,
                "MSI-X validation failed: {errors}",
                errors="; ".join(validation_errors),
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
            "MSI-X capabilities processed: {vectors} vectors, table BIR {bir}, offset 0x{offset:x}",
            vectors=msix_info["table_size"],
            bir=msix_info["table_bir"],
            offset=msix_info["table_offset"],
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

        This method now acts as a thin orchestration layer, delegating all the
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
        log_info_safe(self.logger, "Building comprehensive template context")

        try:
            # Initialize context builder
            self.context_builder = PCILeechContextBuilder(
                device_bdf=self.config.device_bdf, config=self.config
            )

            # Delegate all context building to PCILeechContextBuilder
            # It will handle:
            # - Device info enhancement via lookup_device_info
            # - Board configuration
            # - BAR analysis
            # - Behavior profiling integration
            # - All fallback mechanisms
            template_context = self.context_builder.build_context(
                behavior_profile=behavior_profile,
                config_space_data=config_space_data,
                msix_data=msix_data,
                interrupt_strategy=interrupt_strategy,
                interrupt_vectors=interrupt_vectors,
                donor_template=self.config.donor_template,
            )

            # Validate context completeness
            # Cast to Dict[str, Any] for type checker since TypedDict is a dict at runtime
            context_dict = dict(template_context)
            self._validate_template_context(context_dict)

            log_info_safe(
                self.logger,
                "Template context built successfully with {keys} top-level keys",
                keys=len(template_context),
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

            # Since we're generating PCILeech firmware, we need to validate
            # against PCILeech-specific template requirements
            # The validator will automatically detect PCILeech templates by pattern
            template_name = "pcileech_firmware.j2"  # Generic PCILeech template name

            # Use strict validation mode based on config
            strict_mode = self.config.strict_validation

            # Validate the context
            validated_context = validate_template_context(
                template_name, context, strict=strict_mode
            )

            # The validator returns the validated context, but we don't need to
            # update the original since we're just validating

            log_info_safe(
                self.logger,
                "Template context validation successful",
                prefix="PCIL",
            )

        except ValueError as e:
            # Convert ValueError from validator to PCILeechGenerationError
            if self.config.fail_on_missing_data:
                raise PCILeechGenerationError(
                    f"Template context validation failed: {e}"
                ) from e
            else:
                log_warning_safe(
                    self.logger,
                    "Template context validation warning: {error}",
                    error=str(e),
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
                    f"Template context missing required keys: {missing_keys}"
                )

            if missing_keys:
                log_warning_safe(
                    self.logger,
                    "Template context missing optional keys: {keys}",
                    keys=missing_keys,
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

            log_info_safe(
                self.logger,
                "Generated {count} SystemVerilog modules",
                count=len(modules),
            )

            return modules

        except TemplateRenderError as e:
            raise PCILeechGenerationError(
                f"SystemVerilog generation failed: {e}"
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
                    self.logger, "Generating advanced modules with behavior profile"
                )
                registers = self._extract_register_definitions(template_context)
                variance_model = template_context.get("device_config", {}).get(
                    "variance_model"
                )

                log_info_safe(
                    self.logger,
                    "Calling generate_advanced_systemverilog with {reg_count} registers",
                    reg_count=len(registers),
                )
                advanced_modules["advanced_controller"] = (
                    self.sv_generator.generate_advanced_systemverilog(
                        regs=registers, variance_model=variance_model
                    )
                )
                log_info_safe(
                    self.logger, "Successfully generated advanced_controller module"
                )

        except Exception as e:
            log_error_safe(
                self.logger,
                "Advanced module generation failed: {error}\nTraceback: {tb}",
                error=str(e),
                tb=traceback.format_exc(),
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
            details = "Using fallback build integration may result in inconsistent or unpredictable build behavior."

            if self.fallback_manager.confirm_fallback(
                "build-integration", str(e), details=details
            ):
                log_warning_safe(
                    self.logger,
                    "PCILeech build integration generation failed, attempting fallback: {error}",
                    error=str(e),
                )
                # Fallback to base integration
                try:
                    return self.sv_generator.generate_enhanced_build_integration()
                except Exception as fallback_e:
                    # Build integration is critical - cannot use minimal fallback
                    log_error_safe(
                        self.logger,
                        "CRITICAL: Build integration generation failed completely: {error}",
                        error=str(fallback_e),
                    )
                    raise PCILeechGenerationError(
                        f"Build integration generation failed (no safe fallback available): {fallback_e}"
                    ) from fallback_e
            else:
                raise PCILeechGenerationError(
                    f"Build integration generation failed: {e}"
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
                    "CRITICAL: Missing vendor_id or device_id in template context - cannot generate constraints with fallback IDs"
                )

            constraints_context = {
                # Device information - NO FALLBACKS for critical IDs
                "device": {
                    "vendor_id": template_context["vendor_id"],  # Required, no fallback
                    "device_id": template_context["device_id"],  # Required, no fallback
                    "revision_id": template_context.get(
                        "revision_id", "00"
                    ),  # Less critical, can have fallback
                    "class_code": template_context.get(
                        "class_code"
                    ),  # Should be present but not using fallback
                },
                # Board information
                "board": {
                    "name": template_context.get("board_name", "default"),
                    "fpga_part": template_context.get("fpga_part", ""),
                    "fpga_family": template_context.get("fpga_family", ""),
                    "constraints": template_context.get("board_constraints", {}),
                },
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
                # Header comment
                "header": template_context.get(
                    "header", "# TCL Constraints Generated by PCILeech FW Generator"
                ),
            }

            timing_constraints = (
                renderer.render_template("tcl/constraints.j2", constraints_context)
                if renderer.template_exists("tcl/constraints.j2")
                else self._generate_default_timing_constraints(template_context)
            )

            # Generate pin constraints based on board
            pin_constraints = self._generate_pin_constraints(template_context)

            return {
                "timing_constraints": timing_constraints,
                "pin_constraints": pin_constraints,
            }
        except ImportError:
            # Fallback to basic constraints if TCL builder not available
            return self._generate_default_constraints(template_context)

    def _generate_default_timing_constraints(self, context: Dict[str, Any]) -> str:
        """Generate default timing constraints."""
        clock_period = context.get("clock_period", "10.0")
        return f"""# Timing Constraints
# Generated by PCILeech FW Generator

# Primary clock constraint
create_clock -period {clock_period} -name sys_clk [get_ports sys_clk]

# PCIe reference clock
create_clock -period 10.000 -name pcie_refclk [get_ports pcie_refclk_p]

# False paths for asynchronous resets
set_false_path -from [get_ports sys_rst_n]
set_false_path -from [get_ports pcie_perst_n]

# Input/output delays
set_input_delay -clock sys_clk -max 2.0 [get_ports -filter {{NAME !~ *clk*}}]
set_output_delay -clock sys_clk -max 2.0 [get_ports -filter {{NAME !~ *clk*}}]
"""

    def _generate_pin_constraints(self, context: Dict[str, Any]) -> str:
        """Generate pin location constraints based on board."""
        board_name = context.get("board_name", "")

        # Basic pin constraints template
        constraints = """# Pin Location Constraints
# Generated by PCILeech FW Generator

"""

        # Add board-specific constraints
        if "35t" in board_name.lower():
            constraints += """# Artix-7 35T specific pins
set_property PACKAGE_PIN E3 [get_ports sys_clk]
set_property IOSTANDARD LVCMOS33 [get_ports sys_clk]
"""
        elif "75t" in board_name.lower():
            constraints += """# Artix-7 75T specific pins
set_property PACKAGE_PIN F4 [get_ports sys_clk]
set_property IOSTANDARD LVCMOS33 [get_ports sys_clk]
"""
        elif "100t" in board_name.lower():
            constraints += """# Artix-7 100T specific pins
set_property PACKAGE_PIN G4 [get_ports sys_clk]
set_property IOSTANDARD LVCMOS33 [get_ports sys_clk]
"""

        return constraints

    def _generate_default_constraints(self, context: Dict[str, Any]) -> Dict[str, str]:
        """Generate minimal default constraints as fallback."""
        return {
            "timing_constraints": self._generate_default_timing_constraints(context),
            "pin_constraints": self._generate_pin_constraints(context),
        }

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
            self.logger.warning(f"TCL builder not available, using fallback: {e}")
            # Fallback to basic TCL scripts if builder not available
            return self._generate_default_tcl_scripts(template_context)

    def _generate_default_tcl_scripts(self, context: Dict[str, Any]) -> Dict[str, str]:
        """Generate default TCL scripts as fallback."""
        project_name = context.get("project_name", "pcileech_fw")
        fpga_part = context.get("fpga_part", "xc7a35t-csg324-1")

        build_script = f"""# PCILeech Build Script
# Generated by PCILeech FW Generator

# Create project
create_project {project_name} ./{project_name} -part {fpga_part}

# Add sources
add_files -fileset sources_1 [glob ./src/*.sv]
add_files -fileset sources_1 [glob ./src/*.v]

# Add constraints
add_files -fileset constrs_1 [glob ./constraints/*.xdc]

# Run synthesis
launch_runs synth_1 -jobs 4
wait_on_run synth_1

# Run implementation
launch_runs impl_1 -jobs 4
wait_on_run impl_1

# Generate bitstream
launch_runs impl_1 -to_step write_bitstream -jobs 4
wait_on_run impl_1

puts "Build complete!"
"""

        synthesis_script = f"""# Synthesis Script
# Generated by PCILeech FW Generator

# Open project
open_project ./{project_name}/{project_name}.xpr

# Reset synthesis run
reset_run synth_1

# Configure synthesis settings
set_property strategy Flow_PerfOptimized_high [get_runs synth_1]
set_property STEPS.SYNTH_DESIGN.ARGS.DIRECTIVE AlternateRoutability [get_runs synth_1]
set_property STEPS.SYNTH_DESIGN.ARGS.RETIMING true [get_runs synth_1]

# Launch synthesis
launch_runs synth_1 -jobs 4
wait_on_run synth_1

# Check for errors
if {{[get_property PROGRESS [get_runs synth_1]] != "100%"}} {{
    error "Synthesis failed"
}}

puts "Synthesis complete!"
"""

        return {
            "build_script": build_script,
            "synthesis_script": synthesis_script,
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
            log_info_safe(self.logger, "Generating writemask COE file")

            # Initialize writemask generator
            writemask_gen = WritemaskGenerator()

            # Get configuration space COE path from src directory (where COE files are saved)
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

                # First check if it exists in the already generated systemverilog modules
                # This avoids regenerating what was already created
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
                        "Used cached config space COE content at {path}",
                        path=str(cfg_space_coe),
                        prefix="WRMASK",
                    )
                else:
                    # Check if COE file already exists in systemverilog directory
                    # This prevents regenerating files that were already saved
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
                            "Copied existing COE from systemverilog directory to {path}",
                            path=str(cfg_space_coe),
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
                                "Generated config space COE file at {path}",
                                path=str(cfg_space_coe),
                                prefix="WRMASK",
                            )
                        else:
                            log_warning_safe(
                                self.logger,
                                "Config space COE module not found in generated modules",
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
                    self.logger, "Writemask COE file was not generated", prefix="WRMASK"
                )
                return None

        except Exception as e:
            log_warning_safe(
                self.logger,
                "Failed to generate writemask COE: {error}",
                error=str(e),
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

            # Helper to coerce various representations into bytes
            def _coerce_to_bytes(value: Any) -> Optional[bytes]:
                if not value:
                    return None
                # Already bytes
                if isinstance(value, (bytes, bytearray)):
                    return bytes(value)
                # Hex string
                if isinstance(value, str):
                    # Remove common whitespace and separators
                    s = value.replace(" ", "").replace("\n", "").replace("\t", "")
                    # Accept strings with 0x prefixes or separators like ':' or '-' by
                    # stripping non-hex characters and any 0x/0X prefixes.
                    try:
                        import re

                        # Remove 0x prefixes (case-insensitive)
                        s = re.sub(r"0x", "", s, flags=re.IGNORECASE)
                        # Keep only hex digits
                        s = "".join(ch for ch in s if ch in "0123456789abcdefABCDEF")
                        # Ensure even length for fromhex
                        if len(s) % 2 != 0:
                            s = "0" + s
                        return bytes.fromhex(s)
                    except Exception:
                        return None
                # Lists of ints
                if isinstance(value, list) and all(isinstance(x, int) for x in value):
                    try:
                        return bytes(value)
                    except Exception:
                        return None
                return None

            raw_config_space: Optional[bytes] = None

            # 1) Top-level config_space_data (preferred)
            csd = template_context.get("config_space_data")
            if isinstance(csd, dict):
                raw_config_space = (
                    _coerce_to_bytes(csd.get("raw_config_space"))
                    or _coerce_to_bytes(csd.get("raw_data"))
                    or _coerce_to_bytes(csd.get("config_space_hex"))
                )

            # 2) Top-level raw keys
            if raw_config_space is None:
                raw_config_space = _coerce_to_bytes(
                    template_context.get("raw_config_space")
                ) or _coerce_to_bytes(template_context.get("config_space_hex"))

            # 3) config_space dict (common from context builder)
            if raw_config_space is None:
                cfg = template_context.get("config_space")
                if isinstance(cfg, dict):
                    raw_config_space = (
                        _coerce_to_bytes(cfg.get("raw_data"))
                        or _coerce_to_bytes(cfg.get("raw_config_space"))
                        or _coerce_to_bytes(cfg.get("config_space_hex"))
                    )

            # 4) device_config -> config_space_data (legacy)
            if raw_config_space is None:
                device_cfg = template_context.get("device_config")
                if isinstance(device_cfg, dict) and "config_space_data" in device_cfg:
                    nested = device_cfg.get("config_space_data")
                    if isinstance(nested, dict):
                        raw_config_space = (
                            _coerce_to_bytes(nested.get("raw_config_space"))
                            or _coerce_to_bytes(nested.get("raw_data"))
                            or _coerce_to_bytes(nested.get("config_space_hex"))
                        )

            # 5) Best-effort: probe any dict-like entry that looks like config space
            if raw_config_space is None:
                for key, value in template_context.items():
                    if not isinstance(value, dict):
                        continue
                    k = str(key).lower()
                    if "config" in k or "raw" in k:
                        raw_config_space = (
                            _coerce_to_bytes(value.get("raw_config_space"))
                            or _coerce_to_bytes(value.get("raw_data"))
                            or _coerce_to_bytes(value.get("config_space_hex"))
                        )
                        if raw_config_space:
                            log_info_safe(
                                self.logger,
                                "Found potential config space key '{key}' with subkeys: {subkeys}",
                                key=key,
                                subkeys=(
                                    list(value.keys())
                                    if isinstance(value, dict)
                                    else type(value)
                                ),
                                prefix="HEX",
                            )
                            break

            if not raw_config_space:
                # Log all available keys for debugging
                log_warning_safe(
                    self.logger,
                    "Configuration space data not found. Available template context keys: {keys}",
                    keys=list(template_context.keys()),
                    prefix="HEX",
                )
                raise ValueError(
                    "No configuration space data available in template context"
                )

            # Create hex formatter
            formatter = ConfigSpaceHexFormatter()

            # Generate hex content
            hex_content = formatter.format_config_space_to_hex(
                raw_config_space, include_comments=True
            )

            log_info_safe(
                self.logger,
                "Generated configuration space hex file with {size} bytes",
                size=len(raw_config_space),
                prefix="HEX",
            )

            return hex_content

        except Exception as e:
            log_error_safe(
                self.logger,
                "Configuration space hex generation failed: {error}",
                error=str(e),
                prefix="HEX",
            )
            raise PCILeechGenerationError(
                f"Config space hex generation failed: {e}"
            ) from e

    def _validate_generated_firmware(
        self, systemverilog_modules: Dict[str, str], firmware_components: Dict[str, Any]
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
                    f"Missing required SystemVerilog modules: {missing_modules}"
                )

            # Validate module content
            for module_name, module_code in systemverilog_modules.items():
                if not module_code or len(module_code.strip()) == 0:
                    raise PCILeechGenerationError(
                        f"SystemVerilog module '{module_name}' is empty"
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

    def _get_timestamp(self) -> str:
        """Get current timestamp for generation metadata."""
        from datetime import datetime

        return datetime.now().isoformat()

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
            # IMPORTANT: TCL scripts expect files in "src" directory, not "systemverilog"
            sv_dir = output_dir / "src"
            sv_dir.mkdir(exist_ok=True)

            log_info_safe(
                self.logger, "Saving SystemVerilog modules to {path}", path=str(sv_dir)
            )

            sv_modules = generation_result.get("systemverilog_modules", {})
            log_info_safe(
                self.logger,
                "Found {count} SystemVerilog modules to save: {modules}",
                count=len(sv_modules),
                modules=list(sv_modules.keys()),
            )

            for module_name, module_code in sv_modules.items():
                # COE files should also go in src directory for Vivado to find them
                if module_name.endswith(".sv") or module_name.endswith(".coe"):
                    module_file = sv_dir / module_name
                else:
                    module_file = sv_dir / f"{module_name}.sv"

                log_info_safe(
                    self.logger,
                    "Writing module {name} to {path} ({size} bytes)",
                    name=module_name,
                    path=str(module_file),
                    size=len(module_code),
                )

                try:
                    module_file.write_text(module_code)

                    # Verify the file was written
                    if not module_file.exists():
                        log_error_safe(
                            self.logger,
                            "Failed to write module {name} - file does not exist after write",
                            name=module_name,
                        )
                    elif module_file.stat().st_size == 0:
                        log_error_safe(
                            self.logger,
                            "Module {name} was written but is empty",
                            name=module_name,
                        )
                except Exception as e:
                    log_error_safe(
                        self.logger,
                        "Failed to write module {name}: {error}",
                        name=module_name,
                        error=str(e),
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
                    "Saved writemask COE to {path}",
                    path=str(writemask_file),
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
                    "Saved configuration space hex file to {path}",
                    path=str(hex_file),
                    prefix="HEX",
                )

            # Save metadata
            import json

            metadata_file = output_dir / "generation_metadata.json"
            with open(metadata_file, "w") as f:
                json.dump(generation_result["generation_metadata"], f, indent=2)

            log_info_safe(
                self.logger, "Generated firmware saved to {path}", path=str(output_dir)
            )

            return output_dir

        except Exception as e:
            raise PCILeechGenerationError(
                f"Failed to save generated firmware: {e}"
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
            config_space_path = f"/sys/bus/pci/devices/{self.config.device_bdf}/config"

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
                    "Preloaded MSI-X capability: {vectors} vectors, table BIR {bir}, offset 0x{offset:x}",
                    vectors=msix_info["table_size"],
                    bir=msix_info["table_bir"],
                    offset=msix_info["table_offset"],
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
            f"Invalidated caches for context hash: {context_hash[:8]}...",
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
                "Invalid MSI-X capability fields: {error}",
                error=str(e),
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
                "MSI-X table read incomplete: requested={req} got={got}",
                req=total_bytes,
                got=(len(raw) if raw else 0),
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
                hex_lines.append(f"{word:08X}")

        return {
            "table_entries": entries,
            "table_init_hex": "\n".join(hex_lines) + "\n",
        }
