#!/usr/bin/env python3
"""
SystemVerilog Configuration Classes

Centralized configuration dataclasses for SystemVerilog generation features.
This module provides the single source of truth for all configuration classes
to avoid duplication across the codebase.
"""

from dataclasses import dataclass, field

from enum import Enum

from typing import Dict, Set

from src.utils import validation_constants as VC


# ============================================================================
# Enumerations
# ============================================================================


class PowerState(Enum):
    """PCIe power states."""

    D0 = "D0"  # Fully operational
    D1 = "D1"  # Light sleep
    D2 = "D2"  # Deep sleep
    D3_HOT = "D3_HOT"  # Deep sleep with aux power
    D3_COLD = "D3_COLD"  # No power


class LinkState(Enum):
    """PCIe link power states."""

    L0 = "L0"  # Active state
    L0S = "L0s"  # Standby state
    L1 = "L1"  # Low power standby
    L2 = "L2"  # Auxiliary power
    L3 = "L3"  # Off state


class ErrorType(Enum):
    """Types of errors that can be detected and handled."""

    NONE = "none"
    PARITY = "parity"
    CRC = "crc"
    TIMEOUT = "timeout"
    OVERFLOW = "overflow"
    UNDERFLOW = "underflow"
    PROTOCOL = "protocol"
    ALIGNMENT = "alignment"
    INVALID_TLP = "invalid_tlp"
    UNSUPPORTED = "unsupported"
    # PCIe-specific error types
    CORRECTABLE = "correctable"
    UNCORRECTABLE_NON_FATAL = "uncorrectable_non_fatal"
    UNCORRECTABLE_FATAL = "uncorrectable_fatal"


class PerformanceMetric(Enum):
    """Performance metrics that can be monitored."""

    TLP_COUNT = "tlp_count"
    COMPLETION_LATENCY = "completion_latency"
    BANDWIDTH_UTILIZATION = "bandwidth_utilization"
    ERROR_RATE = "error_rate"
    POWER_TRANSITIONS = "power_transitions"
    INTERRUPT_LATENCY = "interrupt_latency"


# ============================================================================
# Configuration Classes
# ============================================================================


_DEFAULT_TRANSITION_CYCLES = VC.get_power_transition_cycles()


@dataclass
class TransitionCycles:
    """Power state transition cycle counts."""

    # Default to centralized validation constants; allow overrides
    d0_to_d1: int = field(
        default_factory=lambda: _DEFAULT_TRANSITION_CYCLES["d0_to_d1"]
    )
    d1_to_d0: int = field(
        default_factory=lambda: _DEFAULT_TRANSITION_CYCLES["d1_to_d0"]
    )
    d0_to_d3: int = field(
        default_factory=lambda: _DEFAULT_TRANSITION_CYCLES["d0_to_d3"]
    )
    d3_to_d0: int = field(
        default_factory=lambda: _DEFAULT_TRANSITION_CYCLES["d3_to_d0"]
    )


@dataclass
class ErrorHandlingConfig:
    """Configuration for error detection and handling."""

    # Error detection features
    enable_error_detection: bool = True
    enable_ecc: bool = True
    enable_parity_check: bool = True
    enable_crc_check: bool = True
    enable_timeout_detection: bool = True

    # Error recovery features
    enable_error_logging: bool = True
    enable_auto_retry: bool = True
    max_retry_count: int = 3
    error_recovery_cycles: int = 1000

    # Error storage and thresholds
    error_log_depth: int = 256
    correctable_error_threshold: int = 100
    uncorrectable_error_threshold: int = 10

    # Recovery timing (in clock cycles)
    retry_delay_cycles: int = 100
    timeout_cycles: int = 1048576  # ~10ms at 100MHz

    # Error classification
    supported_error_types: list = field(
        default_factory=lambda: [
            ErrorType.CORRECTABLE,
            ErrorType.UNCORRECTABLE_NON_FATAL,
        ]
    )
    recoverable_errors: Set[ErrorType] = field(
        default_factory=lambda: {ErrorType.PARITY, ErrorType.CRC, ErrorType.TIMEOUT}
    )
    fatal_errors: Set[ErrorType] = field(
        default_factory=lambda: {ErrorType.PROTOCOL, ErrorType.INVALID_TLP}
    )
    error_thresholds: Dict[ErrorType, int] = field(
        default_factory=lambda: {
            ErrorType.PARITY: 10,
            ErrorType.CRC: 5,
            ErrorType.TIMEOUT: 3,
        }
    )


@dataclass
class PerformanceConfig:
    """Configuration for performance monitoring."""

    # Feature enables
    enable_performance_counters: bool = True
    enable_transaction_counters: bool = True
    enable_bandwidth_monitoring: bool = True
    enable_latency_tracking: bool = True
    enable_latency_measurement: bool = True
    enable_error_counting: bool = True
    enable_error_rate_tracking: bool = True
    enable_performance_grading: bool = True
    enable_perf_outputs: bool = True
    enable_histograms: bool = False

    # Counter configuration
    counter_width: int = VC.DEFAULT_COUNTER_WIDTH
    histogram_bins: int = 16

    # Timing configuration
    sampling_period: int = 1000  # Clock cycles
    bandwidth_sample_period: int = 100000  # Clock cycles for bandwidth sampling

    # Bandwidth calculation
    transfer_width: int = 4  # Transfer width in bytes
    bandwidth_shift: int = 10  # Shift for bandwidth calculation
    avg_packet_size: int = 1500  # For network devices

    # Thresholds
    min_operations_for_error_rate: int = 100
    high_performance_threshold: int = 1000
    medium_performance_threshold: int = 100
    high_bandwidth_threshold: int = 100
    medium_bandwidth_threshold: int = 50
    low_latency_threshold: int = 10
    medium_latency_threshold: int = 50
    low_error_threshold: int = 1
    medium_error_threshold: int = 5

    # Metrics to monitor
    metrics_to_monitor: Set[PerformanceMetric] = field(
        default_factory=lambda: {
            PerformanceMetric.TLP_COUNT,
            PerformanceMetric.COMPLETION_LATENCY,
            PerformanceMetric.BANDWIDTH_UTILIZATION,
        }
    )


@dataclass
class PowerManagementConfig:
    """Configuration for power management features."""

    # Feature enables
    enable_power_management: bool = True
    enable_pme: bool = True  # Power Management Event support
    enable_wake_events: bool = False
    enable_clock_gating: bool = True
    enable_power_gating: bool = False

    # Interface configuration
    has_interface_signals: bool = True

    # Clock frequency for timing calculations
    clk_hz: int = 100_000_000  # 100 MHz default

    # Transition timeout (nanoseconds) - PCIe spec allows up to 10ms
    transition_timeout_ns: int = 10_000_000  # 10 ms

    # Idle threshold (clock cycles before entering low power)
    idle_threshold: int = 10000

    # Supported power states
    supported_states: Set[PowerState] = field(
        default_factory=lambda: {PowerState.D0, PowerState.D3_HOT}
    )

    # Transition delays (cycles)
    transition_delays: Dict[tuple, int] = field(
        default_factory=lambda: {
            (PowerState.D0, PowerState.D3_HOT): 100,
            (PowerState.D3_HOT, PowerState.D0): 1000,
        }
    )

    # Transition cycle counts
    transition_cycles: TransitionCycles = field(default_factory=TransitionCycles)


@dataclass
class AdvancedFeatureConfig:
    """Combined configuration for all advanced features."""

    error_handling: ErrorHandlingConfig = field(default_factory=ErrorHandlingConfig)
    performance: PerformanceConfig = field(default_factory=PerformanceConfig)
    power_management: PowerManagementConfig = field(
        default_factory=PowerManagementConfig
    )

    # Global settings
    enable_debug_ports: bool = True
    enable_assertions: bool = True
    enable_coverage: bool = False
    clock_frequency_mhz: int = 250
    prefix: str = "ADV_SV_FEATURES"
