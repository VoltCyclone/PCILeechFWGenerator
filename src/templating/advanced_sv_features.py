#!/usr/bin/env python3
"""
Advanced SystemVerilog Features Module

Consolidates advanced SystemVerilog generation features such as error handling,
performance monitoring, and power management into one cohesive generator to
reduce import complexity.
"""

import logging
from typing import Dict, List, Optional

# Centralized version import (avoid hardcoding versions)
try:
    from ..__version__ import __version__ as PCILEECH_FWGEN_VERSION  # type: ignore
except Exception:  # pragma: no cover - fallback if package structure differs
    try:
        from src.__version__ import (
            __version__ as PCILEECH_FWGEN_VERSION,
        )  # type: ignore
    except Exception:
        PCILEECH_FWGEN_VERSION = "unknown"

# Import standard utilities
try:
    from ..string_utils import (
        generate_sv_header_comment,
        log_debug_safe,
        log_error_safe,
        log_info_safe,
        log_warning_safe,
        safe_format,
    )
    from .sv_config import (
        AdvancedFeatureConfig,
        ErrorHandlingConfig,
        ErrorType,
        LinkState,
        PerformanceConfig,
        PerformanceMetric,
        PowerManagementConfig,
        PowerState,
        TransitionCycles,
    )
    from .template_renderer import TemplateRenderer, TemplateRenderError
except ImportError:
    # Fallback for standalone usage
    from src.string_utils import (
        generate_sv_header_comment,
        log_debug_safe,
        log_error_safe,
        log_info_safe,
        log_warning_safe,
        safe_format,
    )
    from src.templating.sv_config import (
        AdvancedFeatureConfig,
        ErrorHandlingConfig,
        ErrorType,
        LinkState,
        PerformanceConfig,
        PerformanceMetric,
        PowerManagementConfig,
        PowerState,
        TransitionCycles,
    )
    from src.templating.template_renderer import (
        TemplateRenderer,
        TemplateRenderError,
    )

# Setup logger
logger = logging.getLogger(__name__)


class AdvancedSVFeatureGenerator:
    """Generator for advanced SystemVerilog features."""

    def __init__(
        self,
        config: AdvancedFeatureConfig,
        renderer: Optional[TemplateRenderer] = None,
    ):
        """Initialize the advanced SV feature generator.
        
        Args:
            config: Advanced feature configuration
            renderer: Shared TemplateRenderer instance (creates new if None)
        """
        self.config = config
        self.renderer = renderer if renderer is not None else TemplateRenderer()
        self.prefix = config.prefix
        log_info_safe(
            logger,
            "Initialized AdvancedSVFeatureGenerator with config",
            prefix=self.config.prefix,
        )

    def generate_error_handling_module(self) -> str:
        """Generate complete error handling module."""
        if not self.config.error_handling.enable_error_detection:
            log_debug_safe(
                logger,
                "Error handling disabled, returning empty module",
                prefix=self.prefix,
            )
            return ""

        log_info_safe(logger, "Generating error handling module", prefix=self.prefix)

        try:
            # Import here to avoid circular imports
            from .advanced_sv_error import (
                ErrorHandlingConfig,
                ErrorHandlingGenerator,
            )

            # Create error handling configuration from our config
            error_config = ErrorHandlingConfig(
                enable_ecc=self.config.error_handling.enable_error_detection,
                enable_parity_check=(
                    self.config.error_handling.enable_error_detection
                ),
                enable_crc_check=self.config.error_handling.enable_error_detection,
                enable_timeout_detection=(
                    self.config.error_handling.enable_error_detection
                ),
                enable_auto_retry=True,
                max_retry_count=3,
                enable_error_logging=self.config.error_handling.enable_error_logging,
            )

            # Create error handling generator
            error_generator = ErrorHandlingGenerator(error_config)

            # Generate error handling components using templates
            context = {"config": self.config.error_handling}

            error_detection = error_generator.generate_error_detection()
            error_state_machine = error_generator.generate_error_state_machine()
            error_logging = error_generator.generate_error_logging()
            error_counters = error_generator.generate_error_counters()

            # Generate the complete module using template
            return self._generate_module_template(
                "error_handler",
                context,
                error_detection,
                error_state_machine,
                error_logging,
                error_counters,
            )

        except ImportError as e:
            log_error_safe(
                logger,
                safe_format(
                    "Failed to import error handling generator: {error}",
                    error=str(e),
                ),
                prefix=self.prefix,
            )
            return self._generate_fallback_error_module()
        except Exception as e:
            log_error_safe(
                logger,
                safe_format(
                    "Error generating error handling module: {error}",
                    error=str(e),
                ),
                prefix=self.prefix,
            )
            return self._generate_fallback_error_module()

    def generate_performance_monitor_module(self) -> str:
        """Generate performance monitoring module."""
        if not self.config.performance.enable_performance_counters:
            log_debug_safe(
                logger,
                "Performance monitoring disabled, returning empty module",
                prefix=self.prefix,
            )
            return ""

        log_info_safe(
            logger, "Generating performance monitoring module", prefix=self.prefix
        )

        try:
            context = {
                "config": self.config.performance,
                "counter_width": self.config.performance.counter_width,
                "sampling_period": self.config.performance.sampling_period,
                "metrics": list(self.config.performance.metrics_to_monitor),
            }

            return self._generate_module_template(
                "performance_monitor",
                context,
                self._generate_counter_logic(),
                self._generate_sampling_logic(),
                self._generate_reporting_logic(),
            )
        except Exception as e:
            log_error_safe(
                logger,
                safe_format(
                    "Error generating performance monitor module: {error}",
                    error=str(e),
                ),
                prefix=self.prefix,
            )
            return self._generate_fallback_performance_module()

    def generate_power_management_module(self) -> str:
        """Generate power management module."""
        if not self.config.power_management.enable_power_management:
            log_debug_safe(
                logger,
                "Power management disabled, returning empty module",
                prefix=self.prefix,
            )
            return ""

        log_info_safe(
            logger,
            "Generating power management module",
            prefix=self.prefix
        )

        try:
            pm_config = self.config.power_management
            context = {
                "config": pm_config,
                "supported_states": list(pm_config.supported_states),
                "enable_clock_gating": pm_config.enable_clock_gating,
                "enable_power_gating": pm_config.enable_power_gating,
            }

            return self._generate_module_template(
                "power_manager",
                context,
                self._generate_state_machine(),
                self._generate_clock_gating_logic(),
                self._generate_transition_logic(),
            )
        except Exception as e:
            log_error_safe(
                logger,
                safe_format(
                    "Error generating power management module: {error}",
                    error=str(e),
                ),
                prefix=self.prefix,
            )
            return self._generate_fallback_power_module()

    def _generate_fallback_error_module(self) -> str:
        """Generate a fallback error handling module when templates fail."""
        log_warning_safe(
            logger, "Using fallback error handling module", prefix=self.prefix
        )

        context = {"config": self.config.error_handling}
        return self._generate_fallback_module(
            "error_handler",
            context,
            self._generate_error_recovery_logic(),
            self._generate_error_logging_logic(),
        )

    def _generate_fallback_performance_module(self) -> str:
        """Generate a fallback performance monitor when templates fail."""
        log_warning_safe(
            logger,
            "Using fallback performance monitoring module",
            prefix=self.prefix,
        )

        context = {"config": self.config.performance}
        return self._generate_fallback_module(
            "performance_monitor",
            context,
            self._generate_counter_logic(),
            self._generate_sampling_logic(),
            self._generate_reporting_logic(),
        )

    def _generate_fallback_power_module(self) -> str:
        """Generate a fallback power manager when templates fail."""
        log_warning_safe(
            logger, "Using fallback power management module", prefix=self.prefix
        )

        context = {"config": self.config.power_management}
        return self._generate_fallback_module(
            "power_manager",
            context,
            self._generate_state_machine(),
            self._generate_clock_gating_logic(),
            self._generate_transition_logic(),
        )

    def _generate_module_template(
        self, module_name: str, context: Dict, *components: str
    ) -> str:
        """Generate a module template using Jinja2 or fallback."""
        try:
            log_debug_safe(
                logger,
                safe_format(
                    "Generating module template for {module}",
                    module=module_name,
                ),
                prefix=self.prefix,
            )

            # Try to use Jinja2 template first
            template_name = safe_format(
                "sv/advanced/{module_name}.sv.j2", module_name=module_name
            )

            try:
                return self.renderer.render_template(template_name, context)
            except TemplateRenderError:
                log_warning_safe(
                    logger,
                    safe_format(
                        "Template {template_name} not found, using fallback",
                        template_name=template_name,
                    ),
                    prefix=self.prefix,
                )
                return self._generate_fallback_module(
                    module_name, context, *components
                )

        except Exception as e:
            log_error_safe(
                logger,
                safe_format(
                    "Error in template generation: {error}",
                    error=str(e),
                ),
                prefix=self.prefix,
            )
            return self._generate_fallback_module(module_name, context, *components)

    def _generate_fallback_module(
        self, module_name: str, context: Dict, *components: str
    ) -> str:
        """Generate a fallback module when templates are not available."""
        log_info_safe(
            logger,
            safe_format(
                "Using fallback module generation for {module}",
                module=module_name,
            ),
            prefix=self.prefix,
        )

        header = generate_sv_header_comment(
            safe_format(
                "{module_name} Module",
                module_name=module_name.replace("_", " ").title(),
            ),
            generator="AdvancedSVFeatureGenerator",
            version=PCILEECH_FWGEN_VERSION,
        )

        module_body = "\n\n".join(filter(None, components))

        # Generate appropriate ports based on module type
        port_definitions = self._generate_module_ports(module_name)

        return safe_format(
            """{header}

module {module_name} #(
    parameter FEATURE_ENABLED = 1
) (
    // Clock and Reset
    input  logic        clk,
    input  logic        rst_n,
{port_definitions}
);

{module_body}

endmodule
""",
            header=header,
            module_name=module_name,
            port_definitions=port_definitions,
            module_body=module_body,
        )

    def _generate_module_ports(self, module_name: str) -> str:
        """Generate appropriate ports based on module type."""
        log_debug_safe(
            logger,
            safe_format(
                "Generating ports for {module_name}",
                module_name=module_name,
            ),
            prefix=self.prefix,
        )

        if module_name == "error_handler":
            return """
    // Error signals
    input  logic        error_detected,
    input  logic [7:0]  error_type,
    output logic        recovery_active"""
        elif module_name == "performance_monitor":
            return """
    // Performance monitoring signals
    input  logic        transaction_valid,
    input  logic [31:0] performance_data,
    input  logic        sample_trigger,
    input  logic [31:0] threshold,
    output logic        report_ready,
    output logic [31:0] report_data"""
        elif module_name == "power_manager":
            return """
    // Power management signals
    input  logic        power_down_req,
    input  logic        power_up_req,
    input  logic        power_off_req,
    input  logic        power_save_mode,
    output logic        gated_clk,
    output logic        transition_complete"""
        else:
            log_warning_safe(
                logger,
                safe_format(
                    "Unknown module type {module_name}, using default ports",
                    module_name=module_name,
                ),
                prefix=self.prefix,
            )
            return ""

    def _generate_error_recovery_logic(self) -> str:
        """Generate error recovery logic."""
        log_debug_safe(logger, "Generating error recovery logic", prefix=self.prefix)

        context = {
            "config": self.config.error_handling,
            "recoverable_errors": list(
                self.config.error_handling.recoverable_errors
            ),
            "fatal_errors": list(self.config.error_handling.fatal_errors),
            "error_thresholds": self.config.error_handling.error_thresholds,
        }
        return self.renderer.render_template(
            "sv/error_handling/error_recovery.sv.j2", context
        )

    def _generate_error_logging_logic(self) -> str:
        """Generate error logging logic."""
        log_debug_safe(logger, "Generating error logging logic", prefix=self.prefix)

        context = {"config": self.config.error_handling}
        return self.renderer.render_template(
            "sv/error_handling/error_logging.sv.j2", context
        )

    def _generate_counter_logic(self) -> str:
        """Generate performance counter logic."""
        log_debug_safe(
            logger, "Generating performance counter logic", prefix=self.prefix
        )

        context = {"config": self.config.performance}
        return self.renderer.render_template(
            "sv/performance_counters.sv.j2", context
        )

    def _generate_sampling_logic(self) -> str:
        """Generate sampling logic."""
        log_debug_safe(logger, "Generating sampling logic", prefix=self.prefix)

        context = {"config": self.config.performance}
        return self.renderer.render_template("sv/sampling_logic.sv.j2", context)

    def _generate_reporting_logic(self) -> str:
        """Generate reporting logic."""
        log_debug_safe(logger, "Generating reporting logic", prefix=self.prefix)

        context = {"config": self.config.performance}
        return self.renderer.render_template("sv/reporting_logic.sv.j2", context)

    def _generate_state_machine(self) -> str:
        """Generate power state machine."""
        log_debug_safe(logger, "Generating power state machine", prefix=self.prefix)

        context = {"config": self.config.power_management}
        return self.renderer.render_template("sv/power_management.sv.j2", context)

    def _generate_clock_gating_logic(self) -> str:
        """Generate clock gating logic."""
        log_debug_safe(logger, "Generating clock gating logic", prefix=self.prefix)

        if not self.config.power_management.enable_clock_gating:
            log_debug_safe(
                logger, "Clock gating disabled, skipping", prefix=self.prefix
            )
            return ""

        context = {"config": self.config.power_management}
        return self.renderer.render_template("sv/clock_gating.sv.j2", context)

    def _generate_transition_logic(self) -> str:
        """Generate power transition logic."""
        log_debug_safe(
            logger, "Generating power transition logic", prefix=self.prefix
        )

        context = {"config": self.config.power_management}
        return self.renderer.render_template("sv/power_transitions.sv.j2", context)


# Export the main components
__all__ = [
    "PowerState",
    "LinkState",
    "ErrorType",
    "PerformanceMetric",
    "ErrorHandlingConfig",
    "PerformanceConfig",
    "PowerManagementConfig",
    "AdvancedFeatureConfig",
    "AdvancedSVFeatureGenerator",
]
