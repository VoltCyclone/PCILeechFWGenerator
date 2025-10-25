#!/usr/bin/env python3
"""Behavioral analyzer factory and dispatcher."""

import logging
from typing import Any, Optional

from src.string_utils import log_info_safe, log_warning_safe, safe_format
from .base import BehavioralSpec
from .network_behavioral import NetworkBehavioralAnalyzer
from .storage_behavioral import StorageBehavioralAnalyzer
from .media_behavioral import MediaBehavioralAnalyzer


logger = logging.getLogger(__name__)


class BehavioralAnalyzerFactory:
    """Factory for creating device-specific behavioral analyzers."""
    
    @staticmethod
    def create_analyzer(device_config: Any) -> Optional[Any]:
        """Create appropriate behavioral analyzer based on device class."""
        class_code = getattr(device_config, 'class_code', 0)
        device_class = (class_code >> 16) & 0xFF
        
        log_info_safe(logger, safe_format("Creating behavioral analyzer for class=0x{cls:02X}",
                                 cls=device_class))
        
        if device_class == 0x02:  # Network controller
            return NetworkBehavioralAnalyzer(device_config)
        elif device_class == 0x01:  # Storage controller
            return StorageBehavioralAnalyzer(device_config)
        elif device_class == 0x04:  # Multimedia controller
            return MediaBehavioralAnalyzer(device_config)
        else:
            log_warning_safe(logger, safe_format("No behavioral analyzer for class=0x{cls:02X}",
                                        cls=device_class))
            return None
            
    @staticmethod
    def generate_behavioral_spec(device_config: Any) -> Optional[BehavioralSpec]:
        """Generate behavioral specification for device."""
        if not getattr(device_config, 'enable_behavioral_simulation', False):
            log_info_safe(logger, "Behavioral simulation disabled")
            return None
            
        analyzer = BehavioralAnalyzerFactory.create_analyzer(device_config)
        if not analyzer:
            return None
            
        return analyzer.generate_spec()


def generate_behavioral_spec(device_config: Any) -> Optional[BehavioralSpec]:
    """Convenience function to generate behavioral spec."""
    return BehavioralAnalyzerFactory.generate_behavioral_spec(device_config)
