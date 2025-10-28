#!/usr/bin/env python3
"""Behavioral simulation for storage controllers."""

import logging
from typing import Dict, Any, Optional

from src.string_utils import log_info_safe, safe_format
from .base import (
    BehavioralSpec,
    BehavioralRegister,
    BehavioralCounter,
    BehaviorType
)


logger = logging.getLogger(__name__)


class StorageBehavioralAnalyzer:
    """Generate behavioral specs for storage controllers."""
    
    NVME_REGISTERS = {
        "controller_status": {
            "offset": 0x001C,
            "behavior": BehaviorType.CONSTANT,
            "value": 0x00000001,  # Controller ready
            "description": "Controller status (CSTS)"
        },
        "admin_queue_attrs": {
            "offset": 0x0024,
            "behavior": BehaviorType.CONSTANT,
            "value": 0x00FF00FF,  # Queue sizes
            "description": "Admin queue attributes"
        },
        "completion_queue_head": {
            "offset": 0x1000,
            "behavior": BehaviorType.AUTO_INCREMENT,
            "pattern": "cq_head_counter[15:0]",
            "counter_bits": 16,
            "description": "Completion queue head pointer"
        },
        "submission_queue_tail": {
            "offset": 0x1004,
            "behavior": BehaviorType.WRITE_CAPTURE,
            "default": 0x00000000,
            "description": "Submission queue tail pointer"
        }
    }
    
    def __init__(self, device_config: Any):
        self._device_config = device_config
        
    def generate_spec(self) -> Optional[BehavioralSpec]:
        """Generate behavioral specification for storage device."""
        log_info_safe(logger, safe_format("Generating storage behavioral spec for device={dev}",
                                 dev=getattr(self._device_config, 'device_id', 'unknown')))
        
        spec = BehavioralSpec("nvme")
        
        # Add NVMe registers
        for name, reg_def in self.NVME_REGISTERS.items():
            register = BehavioralRegister(
                name=name,
                offset=reg_def["offset"],
                behavior=reg_def["behavior"],
                default_value=reg_def.get("value", reg_def.get("default", 0)),
                pattern=reg_def.get("pattern"),
                counter_bits=reg_def.get("counter_bits"),
                description=reg_def["description"]
            )
            spec.add_register(register)
            
        # Add counters
        spec.add_counter(BehavioralCounter(
            name="cq_head_counter",
            width=16,
            increment_rate=1,
            description="Completion queue head counter"
        ))
        
        # Validate and return
        if not spec.validate():
            from src.string_utils import log_error_safe
            log_error_safe(logger, "Failed to validate storage behavioral spec")
            return None
            
        return spec
