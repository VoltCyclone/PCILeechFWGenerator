#!/usr/bin/env python3
"""
Advanced SystemVerilog Error Handling Module

This module provides comprehensive error detection, handling, and recovery logic
for PCIe devices, including correctable/uncorrectable errors, retry mechanisms,
and error logging.

Advanced Error Handling feature for the PCILeechFWGenerator project.
"""

from typing import Optional

from .sv_config import ErrorHandlingConfig, ErrorType
from .template_renderer import TemplateRenderer


class ErrorHandlingGenerator:
    """Generator for advanced error handling SystemVerilog logic."""

    def __init__(
        self,
        config: Optional[ErrorHandlingConfig] = None,
        renderer: Optional[TemplateRenderer] = None,
    ):
        """Initialize the error handling generator.
        
        Args:
            config: Error handling configuration (uses defaults if None)
            renderer: Shared TemplateRenderer instance (creates new if None)
        """
        self.config = config or ErrorHandlingConfig()
        self.renderer = renderer if renderer is not None else TemplateRenderer()

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
