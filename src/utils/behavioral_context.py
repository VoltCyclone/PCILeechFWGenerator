#!/usr/bin/env python3
"""Behavioral simulation context integration."""

import logging
from typing import Dict, Any, Optional

from src.string_utils import log_info_safe, log_warning_safe, log_debug_safe, safe_format
from src.behavioral.analyzer import BehavioralAnalyzerFactory
from src.behavioral.base import require


logger = logging.getLogger(__name__)


def build_behavioral_context(device_config: Any) -> Dict[str, Any]:
    """Build behavioral simulation context for device."""
    if not getattr(device_config, 'enable_behavioral_simulation', False):
        log_debug_safe(logger, "Behavioral simulation disabled")
        # Return minimal context for template compatibility
        return {
            "enable_behavioral_simulation": False,
            "behavioral_spec": {
                "device_category": "generic",
                "registers": {},
                "counters": {}
            }
        }
        
    log_info_safe(logger, "Building behavioral simulation context")
    
    # Generate behavioral specification
    spec = BehavioralAnalyzerFactory.generate_behavioral_spec(device_config)
    if not spec:
        log_warning_safe(
            logger,
            "No behavioral spec generated for device",
            prefix="BEHAVIORAL"
        )
        return {}
        
    # Validate specification
    require(spec.validate(), "Invalid behavioral specification")
    
    # Convert to template-compatible format
    context = {
        "enable_behavioral_simulation": True,
        "behavioral_spec": spec.to_dict(),
        "behavioral_bar_index": getattr(device_config, 'behavioral_bar_index', 0)
    }
    
    log_info_safe(logger, safe_format(
        "Generated behavioral context with {reg_count} registers, {cnt_count} counters",
        reg_count=len(spec.registers),
        cnt_count=len(spec.counters)
        ),
        prefix="BEHAVIORAL"
    )
    
    return context


def integrate_behavioral_context(base_context: Dict[str, Any], 
                                device_config: Any) -> Dict[str, Any]:
    """Integrate behavioral context into base context."""
    behavioral_ctx = build_behavioral_context(device_config)
    
    if behavioral_ctx:
        base_context.update(behavioral_ctx)
        log_info_safe(
            logger,
            "Integrated behavioral simulation context",
            prefix="BEHAVIORAL"
        )
        
    return base_context
