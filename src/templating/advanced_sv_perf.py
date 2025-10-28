#!/usr/bin/env python3
"""
Advanced SystemVerilog Performance Counter Module

This module provides hardware performance monitoring capabilities for PCIe devices,
including transaction counters, bandwidth monitoring, latency measurement, and
device-specific performance metrics.

Performance Counter feature for the PCILeechFWGenerator project.
"""

import logging
from dataclasses import dataclass, field
from enum import Enum
from typing import Any, Dict, List, Optional

from ..string_utils import log_info_safe, safe_format

__all__ = [
    "DeviceType",
    "PerformanceCounterConfig",
    "PerformanceCounterGenerator",
]


class DeviceType(Enum):
    """Device-specific types for specialized performance counters."""

    GENERIC = "generic"
    NETWORK_CONTROLLER = "network"
    STORAGE_CONTROLLER = "storage"
    GRAPHICS_CONTROLLER = "graphics"
    AUDIO_CONTROLLER = "audio"


@dataclass
class PerformanceCounterConfig:
    """Configuration for performance monitoring counters."""

    # Counter types to implement
    enable_transaction_counters: bool = True
    enable_bandwidth_monitoring: bool = True
    enable_latency_measurement: bool = False
    enable_latency_tracking: bool = False  # Alias for backward compatibility
    enable_error_rate_tracking: bool = True
    enable_device_specific_counters: bool = True
    enable_performance_grading: bool = True
    enable_perf_outputs: bool = True

    # Counter specifications
    counter_width_bits: int = 32
    timestamp_width_bits: int = 64

    @property
    def counter_width(self) -> int:
        """Alias for counter_width_bits for template compatibility."""
        return self.counter_width_bits

    # Measurement windows
    bandwidth_window_cycles: int = 100000  # ~1ms at 100MHz
    latency_sample_rate: int = 1000  # Sample every 1000 transactions

    # Counter sets based on device type
    network_counters: List[str] = field(
        default_factory=lambda: [
            "rx_packets",
            "tx_packets",
            "rx_bytes",
            "tx_bytes",
            "rx_errors",
            "tx_errors",
        ]
    )
    storage_counters: List[str] = field(
        default_factory=lambda: [
            "read_ops",
            "write_ops",
            "read_bytes",
            "write_bytes",
            "io_errors",
            "queue_depth",
        ]
    )
    graphics_counters: List[str] = field(
        default_factory=lambda: [
            "frame_count",
            "pixel_count",
            "memory_bandwidth",
            "gpu_utilization",
        ]
    )

    # Performance thresholds
    high_bandwidth_threshold: int = 1000000  # bytes per window
    high_latency_threshold: int = 1000  # cycles
    error_rate_threshold: float = 0.01  # 1% error rate
    msi_threshold: int = 1000  # MSI interrupt threshold for perf_stub


@dataclass
class PerformanceCounterGenerator:
    """Generator for advanced performance counter SystemVerilog logic."""

    def __init__(
        self,
        config: Optional[PerformanceCounterConfig] = None,
        device_type: DeviceType = DeviceType.GENERIC,
        renderer=None,
        logger: Optional[logging.Logger] = None,
        prefix: str = "PERF",
    ):
        """Initialize the performance counter generator."""
        self.config = config or PerformanceCounterConfig()
        # Ensure both properties are set for backward compatibility
        if hasattr(self.config, "enable_latency_measurement") and not hasattr(
            self.config, "enable_latency_tracking"
        ):
            self.config.enable_latency_tracking = (
                self.config.enable_latency_measurement
            )
        elif hasattr(self.config, "enable_latency_tracking") and not hasattr(
            self.config, "enable_latency_measurement"
        ):
            self.config.enable_latency_measurement = (
                self.config.enable_latency_tracking
            )
        self.device_type = device_type
        self.logger = logger or logging.getLogger(__name__)
        self.prefix = prefix
        # Initialize template renderer
        if renderer is None:
            from .template_renderer import TemplateRenderer

            self.renderer = TemplateRenderer()
        else:
            self.renderer = renderer

        log_info_safe(
            self.logger,
            safe_format(
                "Initialized PerformanceCounterGenerator for "
                "device type: {device_type}",
                device_type=device_type.value,
            ),
            prefix=prefix,
        )

    def generate_perf_declarations(self) -> str:
        """Generate performance counter signal declarations."""
        return safe_format(
            "    // Lightweight performance counter declarations\n"
            "    // See perf_stub module for implementation\n"
        )

    def _generate_device_specific_declarations(self) -> List[str]:
        """Generate device-specific counter declarations."""
        return [safe_format("    // Device-specific declarations in perf_stub\n")]

    def _build_context_from_template_context(
        self, template_context: Dict[str, Any]
    ) -> Dict[str, Any]:
        """Build performance counter context from template context using dynamic defaults."""
        # Get configuration objects from template context with safe defaults
        perf_config = template_context.get("perf_config", {})
        timing_config = template_context.get("timing_config", {})
        device_config = template_context.get("device_config", {})
        board_config = template_context.get("board_config", {})

        # Use template context to derive parameters dynamically
        context = {
            # Enable flags from template context or config
            "enable_transaction_counters": perf_config.get(
                "enable_transaction_counters",
                self.config.enable_transaction_counters
            ),
            "enable_bandwidth_monitoring": perf_config.get(
                "enable_bandwidth_monitoring",
                self.config.enable_bandwidth_monitoring
            ),
            "enable_latency_measurement": perf_config.get(
                "enable_latency_measurement", self.config.enable_latency_measurement
            ),
            "enable_latency_tracking": perf_config.get(
                "enable_latency_tracking", self.config.enable_latency_tracking
            ),
            "enable_error_rate_tracking": perf_config.get(
                "enable_error_rate_tracking", self.config.enable_error_rate_tracking
            ),
            "enable_device_specific_counters": perf_config.get(
                "enable_device_specific_counters",
                self.config.enable_device_specific_counters,
            ),
            "enable_performance_grading": perf_config.get(
                "enable_performance_grading", self.config.enable_performance_grading
            ),
            "enable_perf_outputs": perf_config.get(
                "enable_perf_outputs", self.config.enable_perf_outputs
            ),
            # Device type from template context or instance
            "device_type": device_config.get(
                "device_type", self.device_type.value.lower()
            ),
            # Timing parameters from template context
            "bandwidth_sample_period": timing_config.get(
                "bandwidth_sample_period", 100000
            ),
            "transfer_width": timing_config.get("transfer_width", 4),
            "bandwidth_shift": timing_config.get("bandwidth_shift", 10),
            "min_operations_for_error_rate": timing_config.get(
                "min_operations_for_error_rate", 100
            ),
            # Performance thresholds from template context with intelligent defaults
            "high_performance_threshold": perf_config.get(
                "high_performance_threshold", 1000
            ),
            "medium_performance_threshold": perf_config.get(
                "medium_performance_threshold", 100
            ),
            "high_bandwidth_threshold": perf_config.get(
                "high_bandwidth_threshold", 100
            ),
            "medium_bandwidth_threshold": perf_config.get(
                "medium_bandwidth_threshold", 50
            ),
            "low_latency_threshold": perf_config.get("low_latency_threshold", 10),
            "medium_latency_threshold": perf_config.get(
                "medium_latency_threshold", 50
            ),
            "low_error_threshold": perf_config.get("low_error_threshold", 1),
            "medium_error_threshold": perf_config.get("medium_error_threshold", 5),
            # Device-specific parameters with smart defaults based on device type
            "avg_packet_size": self._get_device_specific_param(
                template_context, "avg_packet_size", 1500
            ),
            "msi_threshold": self._get_device_specific_param(
                template_context, "msi_threshold", self.config.msi_threshold
            ),
        }

        log_info_safe(
            self.logger,
            safe_format(
                "Built performance context with {count} parameters "
                "for device type {device_type}",
                count=len(context),
                device_type=context["device_type"],
            ),
            prefix=self.prefix,
        )
        return context

    def _get_device_specific_param(
        self, template_context: Dict[str, Any], param_name: str, fallback: Any
    ) -> Any:
        """Get device-specific parameter from template context with intelligent fallback."""
        device_config = template_context.get("device_config", {})
        perf_config = template_context.get("perf_config", {})

        # Try device-specific config first, then perf config, then fallback
        return device_config.get(param_name, perf_config.get(param_name, fallback))

    def generate_transaction_counters(
        self, template_context: Optional[Dict[str, Any]] = None
    ) -> str:
        """Generate transaction counting logic."""
        context = self._build_context_from_template_context(template_context or {})
        return self.renderer.render_template(
            "sv/performance_counters.sv.j2", context
        )

    def generate_bandwidth_monitoring(
        self, template_context: Optional[Dict[str, Any]] = None
    ) -> str:
        """Generate bandwidth monitoring logic."""
        context = self._build_context_from_template_context(template_context or {})
        return self.renderer.render_template(
            "sv/performance_counters.sv.j2", context
        )

    def generate_latency_measurement(
        self, template_context: Optional[Dict[str, Any]] = None
    ) -> str:
        """Generate latency measurement logic."""
        context = self._build_context_from_template_context(template_context or {})
        return self.renderer.render_template(
            "sv/performance_counters.sv.j2",
            context
            )

    def generate_error_rate_tracking(
        self, template_context: Optional[Dict[str, Any]] = None
    ) -> str:
        """Generate error rate tracking logic."""
        context = self._build_context_from_template_context(template_context or {})
        return self.renderer.render_template(
            "sv/performance_counters.sv.j2",
            context
        )

    def generate_device_specific_counters(
        self, template_context: Optional[Dict[str, Any]] = None
    ) -> str:
        """Generate device-specific performance counters."""
        context = self._build_context_from_template_context(template_context or {})
        return self.renderer.render_template(
            "sv/performance_counters.sv.j2", context
        )

    def _generate_network_counters(
        self, template_context: Optional[Dict[str, Any]] = None
    ) -> str:
        """Generate network-specific performance counters."""
        context = self._build_context_from_template_context(template_context or {})
        context["device_type"] = "network"
        return self.renderer.render_template(
            "sv/performance_counters.sv.j2", context
        )

    def _generate_storage_counters(
        self, template_context: Optional[Dict[str, Any]] = None
    ) -> str:
        """Generate storage-specific performance counters."""
        context = self._build_context_from_template_context(template_context or {})
        context["device_type"] = "storage"
        return self.renderer.render_template(
            "sv/performance_counters.sv.j2", context
        )

    def _generate_graphics_counters(
        self, template_context: Optional[Dict[str, Any]] = None
    ) -> str:
        """Generate graphics-specific performance counters."""
        context = self._build_context_from_template_context(template_context or {})
        context["device_type"] = "graphics"
        return self.renderer.render_template(
            "sv/performance_counters.sv.j2", context
        )

    def generate_performance_grading(
        self, template_context: Optional[Dict[str, Any]] = None
    ) -> str:
        """Generate overall performance grading logic."""
        context = self._build_context_from_template_context(template_context or {})
        context["enable_performance_grading"] = True
        return self.renderer.render_template(
            "sv/performance_counters.sv.j2", context
        )   

    def generate_perf_outputs(
        self, template_context: Optional[Dict[str, Any]] = None
    ) -> str:
        """Generate performance counter output assignments."""
        context = self._build_context_from_template_context(template_context or {})
        context["enable_perf_outputs"] = True
        return self.renderer.render_template(
            "sv/performance_counters.sv.j2", context
        )

    def generate_complete_performance_counters(
        self, template_context: Optional[Dict[str, Any]] = None
    ) -> str:
        """Generate complete performance counter logic."""
        # Use dynamic context from template context
        context = self._build_context_from_template_context(template_context or {})

        log_info_safe(
            self.logger,
            safe_format(
                "Generating complete performance counters for "
                "device type: {device_type}",
                device_type=context["device_type"],
            ),
            prefix=self.prefix,
        )

        return self.renderer.render_template(
            "sv/performance_counters.sv.j2", context
        )

    def generate(self, template_context: Optional[Dict[str, Any]] = None) -> str:
        """Alias for generate_complete_performance_counters."""
        return self.generate_complete_performance_counters(template_context)
