#!/usr/bin/env python3
"""
Templating module for PCILeech firmware generation.

This module contains all templating-related functionality including:
- Jinja2-based template rendering
- TCL script generation
- SystemVerilog code generation
- Configuration classes for advanced features
"""

# Import with fallback for missing dependencies
try:
    from .template_renderer import (TemplateRenderer, TemplateRenderError,
                                    render_tcl_template)
except ImportError:
    TemplateRenderer = None
    TemplateRenderError = None
    render_tcl_template = None

try:
    from .tcl_builder import (BuildContext, TCLBuilder, TCLScriptBuilder,
                              TCLScriptType)
except ImportError:
    TCLBuilder = None
    TCLScriptBuilder = None
    TCLScriptType = None
    BuildContext = None

try:
    from .systemverilog_generator import (AdvancedSVGenerator,
                                          DeviceSpecificLogic)
    from .sv_overlay_generator import SVOverlayGenerator
except ImportError:
    SystemVerilogGenerator = None
    AdvancedSVGenerator = None
    DeviceSpecificLogic = None
    SVOverlayGenerator = None

# Import centralized config classes
try:
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
except ImportError:
    AdvancedFeatureConfig = None
    ErrorHandlingConfig = None
    ErrorType = None
    LinkState = None
    PerformanceConfig = None
    PerformanceMetric = None
    PowerManagementConfig = None
    PowerState = None
    TransitionCycles = None

__all__ = [
    # Template rendering
    "TemplateRenderer",
    "TemplateRenderError",
    "render_tcl_template",
    # TCL building
    "TCLBuilder",
    "TCLScriptBuilder",
    "TCLScriptType",
    "BuildContext",
    # SystemVerilog generation (legacy)
    "SystemVerilogGenerator",
    "AdvancedSVGenerator",
    "DeviceSpecificLogic",
    # Overlay generation (new)
    "SVOverlayGenerator",
    # Configuration classes
    "AdvancedFeatureConfig",
    "ErrorHandlingConfig",
    "ErrorType",
    "LinkState",
    "PerformanceConfig",
    "PerformanceMetric",
    "PowerManagementConfig",
    "PowerState",
    "TransitionCycles",
]
