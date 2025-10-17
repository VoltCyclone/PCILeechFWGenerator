#!/usr/bin/env python3
"""
Advanced SystemVerilog Error Handling Module

This module provides comprehensive error detection, handling, and recovery logic
for PCIe devices, including correctable/uncorrectable errors, retry mechanisms,
and error logging.

Advanced Error Handling feature for the PCILeechFWGenerator project.
"""

from dataclasses import dataclass, field
from enum import Enum
from typing import List, Optional

from .template_renderer import TemplateRenderer


class ErrorType(Enum):
    """PCIe error types."""

    CORRECTABLE = "correctable"
    UNCORRECTABLE_NON_FATAL = "uncorrectable_non_fatal"
    UNCORRECTABLE_FATAL = "uncorrectable_fatal"


@dataclass
class ErrorHandlingConfig:
    """Configuration for error detection and handling."""

    # Supported error types
    supported_error_types: List[ErrorType] = field(
        default_factory=lambda: [
            ErrorType.CORRECTABLE,
            ErrorType.UNCORRECTABLE_NON_FATAL,
        ]
    )

    # Error detection features
    enable_ecc: bool = True
    enable_parity_check: bool = True
    enable_crc_check: bool = True
    enable_timeout_detection: bool = True

    # Error recovery features
    enable_auto_retry: bool = True
    max_retry_count: int = 3
    enable_error_logging: bool = True

    # Error thresholds
    correctable_error_threshold: int = 100
    uncorrectable_error_threshold: int = 10

    # Recovery timing (in clock cycles)
    error_recovery_cycles: int = 1000
    retry_delay_cycles: int = 100
    timeout_cycles: int = 1048576  # ~10ms at 100MHz


class ErrorHandlingGenerator:
    """Generator for advanced error handling SystemVerilog logic."""

    def __init__(self, config: Optional[ErrorHandlingConfig] = None):
        """Initialize the error handling generator."""
        self.config = config or ErrorHandlingConfig()
        self.renderer = TemplateRenderer()

    def generate_error_declarations(self) -> str:
        """Generate error handling signal declarations."""
        context = {"config": self.config}
        return self.renderer.render_template(
            "sv/error_handling/error_declarations.sv.j2", context
        )

    def generate_error_detection(self) -> str:
        """Generate error detection logic."""
        context = {"config": self.config}
        return self.renderer.render_template(
            "sv/error_handling/error_detection.sv.j2", context
        )

    def generate_error_state_machine(self) -> str:
        """Generate error handling state machine."""
        context = {"config": self.config}
        return self.renderer.render_template(
            "sv/error_handling/error_state_machine.sv.j2", context
        )

    def generate_error_logging(self) -> str:
        """Generate error logging logic."""
        context = {"config": self.config}
        return self.renderer.render_template(
            "sv/error_handling/error_logging.sv.j2", context
        )

    def generate_error_counters(self) -> str:
        """Generate error counting logic."""
        context = {"config": self.config}
        return self.renderer.render_template(
            "sv/error_handling/error_counters.sv.j2", context
        )

    def generate_error_outputs(self) -> str:
        """Generate error output assignments."""
        context = {"config": self.config}
        return self.renderer.render_template(
            "sv/error_handling/error_outputs.sv.j2", context
        )

    def generate_complete_error_handling(self) -> str:
        """Generate complete error handling logic."""
        context = {"config": self.config}
        return self.renderer.render_template(
            "sv/error_handling_complete.sv.j2", context
        )
