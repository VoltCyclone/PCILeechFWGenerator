#!/usr/bin/env python3
"""
Simplified SystemVerilog Power Management Module

This module provides minimal power management logic generation for PCIe devices,
focusing on essential D-state transitions and PME support using a simplified approach
based on the pmcsr_stub.sv module design.

Simplified Power Management feature for the PCILeechFWGenerator project.
"""


from typing import Any, Dict, Optional

from ..string_utils import generate_sv_header_comment
from .sv_config import PowerManagementConfig, PowerState, TransitionCycles
from .sv_constants import SV_CONSTANTS
from .template_renderer import TemplateRenderer


class PowerManagementGenerator:
    """Generator for simplified power management SystemVerilog logic."""

    def __init__(
        self,
        config: Optional[PowerManagementConfig] = None,
        renderer: Optional[TemplateRenderer] = None,
    ):
        """Initialize the power management generator.
        
        Args:
            config: Power management configuration (uses defaults if None)
            renderer: Shared TemplateRenderer instance (creates new if None)
        """
        self.config = config or PowerManagementConfig()
        self.renderer = renderer if renderer is not None else TemplateRenderer()

    def _get_template_context(self) -> dict:
        """Get template context variables from configuration."""
        return {
            "clk_hz": self.config.clk_hz,
            "tr_ns": self.config.transition_timeout_ns,
            "timeout_ms": self.config.transition_timeout_ns // 1_000_000,
            "enable_pme": self.config.enable_pme,
            "enable_wake_events": self.config.enable_wake_events,
            # Provide transition cycles context for templates that need it
            "transition_cycles": {
                "d0_to_d1": self.config.transition_cycles.d0_to_d1,
                "d1_to_d0": self.config.transition_cycles.d1_to_d0,
                "d0_to_d3": self.config.transition_cycles.d0_to_d3,
                "d3_to_d0": self.config.transition_cycles.d3_to_d0,
            },
            "pmcsr_bits": {
                "power_state_msb": SV_CONSTANTS.PMCSR_POWER_STATE_MSB,
                "power_state_lsb": SV_CONSTANTS.PMCSR_POWER_STATE_LSB,
                "pme_enable_bit": SV_CONSTANTS.PMCSR_PME_ENABLE_BIT,
                "pme_status_bit": SV_CONSTANTS.PMCSR_PME_STATUS_BIT,
            },
        }

    def _render_template(
        self,
        template_name: str,
        *,
        header: Optional[str] = None,
        overrides: Optional[Dict[str, Any]] = None,
    ) -> str:
        """Render a template with shared context and optional overrides."""

        context = self._get_template_context()
        if overrides:
            context.update(overrides)
        if header is not None:
            context["header"] = header
        return self.renderer.render_template(template_name, context)

    def generate_pmcsr_stub_module(self) -> str:
        """Generate the complete pmcsr_stub module based on the provided design."""
        header = generate_sv_header_comment(
            "PMCSR Stub Module",
            description=(
                "Simplified representation of the PCIe power management CSR logic"
            ),
        )
        return self._render_template("sv/pmcsr_stub.sv.j2", header=header)

    def generate_complete_power_management(self) -> str:
        """Generate complete simplified power management logic."""

        header = generate_sv_header_comment(
            "Simplified Power Management Module",
            description=(
                "Based on minimal pmcsr_stub design for "
                "essential PCIe power management"
            ),
        )

        # Generate the individual components directly using templates
        declarations = self._render_template(
            "sv/components/power_declarations.sv.j2", header=""
        )
        integration = self._render_template(
            "sv/components/power_integration.sv.j2", header=""
        )
        monitoring = self._render_template(
            "sv/components/power_monitoring.sv.j2", header=""
        )

        components = (
            [
                header,
                "",
                declarations,
                "",
                integration,
                "",
            ]
            + [monitoring]
            + [""]
        )

        return "\n".join(components)

    def get_module_dependencies(self) -> list:
        """Return list of module dependencies."""
        return ["pmcsr_stub"]

    def get_config_space_requirements(self) -> dict:
        """Return config space requirements for power management."""
        return {
            # Absolute capability offset must be discovered from donor; we only expose
            # the fixed relative PMCSR offset within the capability structure.
            "pmcsr_rel_offset": f"0x{SV_CONSTANTS.PMCSR_REL_OFFSET:02x}",
            "pmcsr_size": "16 bits",
            "description": "Power Management Control/Status Register",
        }
