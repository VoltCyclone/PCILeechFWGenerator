#!/usr/bin/env python3
"""
Advanced SystemVerilog Generation Module

This module provides sophisticated SystemVerilog generation capabilities for PCIe device
firmware, including advanced timing models, power management, error handling, performance
counters, and device-specific logic generation using Jinja2 templates.

Advanced SystemVerilog Generation feature for the PCILeechFWGenerator project.
"""

from dataclasses import dataclass, field
from enum import Enum
from typing import Dict, List, Optional, Union

from ..device_clone import DeviceConfiguration as NewDeviceConfiguration
from ..device_clone import VarianceModel
from ..device_clone.manufacturing_variance import DeviceClass

# Import from centralized utils
from ..string_utils import generate_sv_header_comment
from ..utils.unified_context import TemplateObject
from ..utils.validation_constants import get_power_transition_cycles

# Import template renderer
from . import TemplateRenderer, TemplateRenderError

# Import centralized enums and constants
from .advanced_sv_features import LinkState, PowerState


_DEFAULT_TRANSITION_CYCLES = get_power_transition_cycles()
_TRANSITION_KEYS = tuple(_DEFAULT_TRANSITION_CYCLES.keys())


class ErrorType(Enum):
    """PCIe error types."""

    CORRECTABLE = "correctable"
    UNCORRECTABLE_NON_FATAL = "uncorrectable_non_fatal"
    UNCORRECTABLE_FATAL = "uncorrectable_fatal"


class DeviceType(Enum):
    """Device-specific types for specialized logic."""

    GENERIC = "generic"
    NETWORK_CONTROLLER = "network"
    STORAGE_CONTROLLER = "storage"
    GRAPHICS_CONTROLLER = "graphics"
    AUDIO_CONTROLLER = "audio"


@dataclass(slots=True)
class PowerManagementConfig:
    """Configuration for power management features."""

    # Power state support
    supported_power_states: List[PowerState] = field(
        default_factory=lambda: [PowerState.D0, PowerState.D1, PowerState.D3_HOT]
    )
    supported_link_states: List[LinkState] = field(
        default_factory=lambda: [LinkState.L0, LinkState.L0S, LinkState.L1]
    )

    # Power transition timing (in clock cycles)
    d0_to_d1_cycles: int = field(
        default_factory=lambda: _DEFAULT_TRANSITION_CYCLES["d0_to_d1"]
    )
    d1_to_d0_cycles: int = field(
        default_factory=lambda: _DEFAULT_TRANSITION_CYCLES["d1_to_d0"]
    )
    d0_to_d3_cycles: int = field(
        default_factory=lambda: _DEFAULT_TRANSITION_CYCLES["d0_to_d3"]
    )
    d3_to_d0_cycles: int = field(
        default_factory=lambda: _DEFAULT_TRANSITION_CYCLES["d3_to_d0"]
    )

    # Link state transition timing
    l0_to_l0s_cycles: int = 10
    l0s_to_l0_cycles: int = 20
    l0_to_l1_cycles: int = 100
    l1_to_l0_cycles: int = 200

    # Power management features
    enable_clock_gating: bool = True
    enable_power_gating: bool = False
    enable_dynamic_voltage_scaling: bool = False


@dataclass(slots=True)
class PerformanceConfig:
    """Configuration for performance monitoring features."""

    # Counter configuration
    counter_width: int = 32
    enable_bandwidth_monitoring: bool = True
    enable_latency_monitoring: bool = True
    enable_error_rate_monitoring: bool = True
    enable_transaction_counting: bool = True

    # Sampling configuration
    sample_period_cycles: int = 65536
    enable_overflow_interrupts: bool = True
    enable_threshold_alerts: bool = False


@dataclass(slots=True)
class ErrorHandlingConfig:
    """Configuration for error handling features."""

    # Error detection
    enable_correctable_error_detection: bool = True
    enable_uncorrectable_error_detection: bool = True
    enable_fatal_error_detection: bool = True

    # Error recovery
    enable_automatic_recovery: bool = True
    max_retry_count: int = 3
    error_recovery_cycles: int = 1000

    # Error reporting
    enable_error_logging: bool = True
    enable_error_interrupts: bool = True


@dataclass
class DeviceConfig:
    """Legacy configuration class - deprecated in favor of DeviceConfiguration."""

    device_type: DeviceType = DeviceType.GENERIC
    device_class: DeviceClass = DeviceClass.CONSUMER
    vendor_id: Optional[str] = None  # Must be explicitly provided - no default
    device_id: Optional[str] = None  # Must be explicitly provided - no default
    max_payload_size: int = 256
    msi_vectors: int = 1

    def __post_init__(self):
        """Validate that required fields are provided."""
        if not self.vendor_id:
            raise ValueError(
                "vendor_id must be explicitly provided - no fallback allowed"
            )
        if not self.device_id:
            raise ValueError(
                "device_id must be explicitly provided - no fallback allowed"
            )

    @classmethod
    def from_device_configuration(
        cls, config: NewDeviceConfiguration
    ) -> "DeviceConfig":
        """Create legacy DeviceConfig from new DeviceConfiguration."""
        return cls(
            device_type=DeviceType.GENERIC,  # Map from new enum
            device_class=DeviceClass.CONSUMER,  # Map from new enum
            vendor_id=config.identification.vendor_id_hex,
            device_id=config.identification.device_id_hex,
            max_payload_size=config.capabilities.max_payload_size,
            msi_vectors=config.capabilities.msi_vectors,
        )


class SystemVerilogGenerator:
    """Generates advanced SystemVerilog modules using Jinja2 templates."""

    def __init__(
        self,
        device_config: DeviceConfig,
        power_config: PowerManagementConfig,
        perf_config: PerformanceConfig,
        error_config: ErrorHandlingConfig,
    ):
        self.device_config = device_config
        self.power_config = power_config
        self.perf_config = perf_config
        self.error_config = error_config
        self.renderer = TemplateRenderer()

    def generate_advanced_module(
        self, regs: List[Dict], variance_model: Optional[VarianceModel] = None
    ) -> str:
        """Generate advanced SystemVerilog module using templates."""
        # Build template context with all required fields
        context = {
            "header": self._generate_header(),
            "device_config": self._build_device_config_context(),
            "power_management": self._build_power_context(),
            "performance_counters": self._build_perf_context(),
            "error_handling": self._build_error_context(),
            "registers": regs,
            "variance_model": variance_model,
        }

        # Add required template fields with safe defaults or disable if missing
        required_fields = ["timer_period", "default_priority", "active_device_config"]
        # timer_period: try to get from device_config, else disable feature
        timer_period = getattr(self.device_config, "timer_period", None)
        if timer_period is not None:
            context["timer_period"] = timer_period
        else:
            context["timer_period"] = 0  # Disable feature if missing

        # default_priority: try to get from device_config, else disable feature
        default_priority = getattr(self.device_config, "default_priority", None)
        if default_priority is not None:
            context["default_priority"] = default_priority
        else:
            context["default_priority"] = 0  # Disable feature if missing

        # active_device_config: always provide as TemplateObject
        context["active_device_config"] = TemplateObject(vars(self.device_config))

        # Wrap all context dicts in TemplateObject for safe template access
        for k, v in context.items():
            if isinstance(v, dict) and not isinstance(v, TemplateObject):
                context[k] = TemplateObject(v)

        # Fast-fail validation for missing required fields
        missing = [f for f in required_fields if f not in context or context[f] is None]
        if missing:
            raise TemplateRenderError(f"Missing required template fields: {missing}")

        # Render main module template
        return self.renderer.render_template("sv/main_module.sv.j2", context)

    def _build_device_config_context(self) -> TemplateObject:
        """Build device config context for templates, with all required fields."""
        # Use vars to get all fields, add safe defaults for missing ones
        dc = vars(self.device_config)
        # timer_period and default_priority are required by templates
        if "timer_period" not in dc:
            dc["timer_period"] = 0
        if "default_priority" not in dc:
            dc["default_priority"] = 0
        return TemplateObject(dc)

    def _generate_header(self) -> str:
        """Generate SystemVerilog header comment."""
        return generate_sv_header_comment(
            f"Advanced {self.device_config.device_type.value.title()} Controller",
            device_type=self.device_config.device_type.value,
            device_class=self.device_config.device_class.value,
            vendor_id=self.device_config.vendor_id,
            device_id=self.device_config.device_id,
        )

    def _build_power_context(self) -> TemplateObject:
        """Build power management context for templates."""
        transition_cycles = {
            key: getattr(
                self.power_config,
                f"{key}_cycles",
                _DEFAULT_TRANSITION_CYCLES[key],
            )
            for key in _TRANSITION_KEYS
        }

        return TemplateObject(
            {
                "supported_states": [
                    state.value for state in self.power_config.supported_power_states
                ],
                "transition_cycles": TemplateObject(transition_cycles),
                "enable_clock_gating": self.power_config.enable_clock_gating,
                "enable_power_gating": self.power_config.enable_power_gating,
            }
        )

    def _build_perf_context(self) -> TemplateObject:
        """Build performance monitoring context for templates."""
        return TemplateObject(
            {
                "counter_width": self.perf_config.counter_width,
                "enable_bandwidth": self.perf_config.enable_bandwidth_monitoring,
                "enable_latency": self.perf_config.enable_latency_monitoring,
                "enable_error_rate": self.perf_config.enable_error_rate_monitoring,
                "sample_period": self.perf_config.sample_period_cycles,
                "enable_overflow_interrupts": self.perf_config.enable_overflow_interrupts,
            }
        )

    def _build_error_context(self) -> TemplateObject:
        """Build error handling context for templates."""
        return TemplateObject(
            {
                "correctable_errors": self.error_config.enable_correctable_error_detection,
                "uncorrectable_errors": self.error_config.enable_uncorrectable_error_detection,
                "fatal_errors": self.error_config.enable_fatal_error_detection,
                "automatic_recovery": self.error_config.enable_automatic_recovery,
                "max_retry_count": self.error_config.max_retry_count,
                "error_recovery_cycles": self.error_config.error_recovery_cycles,
                "enable_logging": self.error_config.enable_error_logging,
                "enable_interrupts": self.error_config.enable_error_interrupts,
            }
        )

    def generate_clock_crossing_module(
        self, variance_model: Optional[VarianceModel] = None
    ) -> str:
        """Generate clock domain crossing module with variance compensation."""

        # Generate header comment using existing method
        header = generate_sv_header_comment(
            "Advanced Clock Domain Crossing Module",
            device_type=self.device_config.device_type.value,
            device_class=self.device_config.device_class.value,
        )

        # Build template context with required variables
        context = {
            "header": header,
            "module_name": "advanced_clock_crossing",
            "data_width": 32,
            "sync_stages": 2,
            "device_config": self.device_config,
            "variance_model": variance_model,
            "power_management": self._build_power_context(),
            "performance_counters": self._build_perf_context(),
            "error_handling": self._build_error_context(),
        }

        # Render template using the existing TemplateRenderer
        return self.renderer.render_template("sv/clock_crossing.sv.j2", context)


# Alias for backward compatibility
AdvancedSVGenerator = SystemVerilogGenerator
