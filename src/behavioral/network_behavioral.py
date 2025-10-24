#!/usr/bin/env python3
"""Behavioral simulation for network controllers."""

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


class NetworkBehavioralAnalyzer:
    """Generate behavioral specs for network controllers."""
    
    ETHERNET_REGISTERS = {
        # Standard Ethernet controller registers
        "link_status": {
            "offset": 0x0000,
            "behavior": BehaviorType.CONSTANT,
            "value": 0x00000001,  # Link always up
            "description": "Link status (bit 0: link up)"
        },
        "rx_data": {
            "offset": 0x0004,
            "behavior": BehaviorType.AUTO_INCREMENT,
            "pattern": "32'hAABB0000 | rx_counter[15:0]",
            "counter_bits": 16,
            "description": "Simulated RX data"
        },
        "tx_data": {
            "offset": 0x0008,
            "behavior": BehaviorType.WRITE_CAPTURE,
            "default": 0x00000000,
            "description": "TX data capture"
        },
        "mac_addr_low": {
            "offset": 0x0010,
            "behavior": BehaviorType.CONSTANT,
            "value": 0x12345678,
            "description": "MAC address low 32 bits"
        },
        "mac_addr_high": {
            "offset": 0x0014,
            "behavior": BehaviorType.CONSTANT,
            "value": 0x00009ABC,
            "description": "MAC address high 16 bits"
        },
        "rx_packet_count": {
            "offset": 0x0020,
            "behavior": BehaviorType.AUTO_INCREMENT,
            "pattern": "rx_packet_counter",
            "description": "RX packet counter"
        },
        "tx_packet_count": {
            "offset": 0x0024,
            "behavior": BehaviorType.AUTO_INCREMENT,
            "pattern": "tx_packet_counter",
            "description": "TX packet counter"
        }
    }
    
    def __init__(self, device_config: Any):
        self._device_config = device_config
        self._subclass = getattr(device_config, 'subclass_code', 0)
        
    def generate_spec(self) -> Optional[BehavioralSpec]:
        """Generate behavioral specification for network device."""
        log_info_safe(logger, safe_format("Generating network behavioral spec for device={dev}",
                                 dev=getattr(self._device_config, 'device_id', 'unknown')))
        
        spec = BehavioralSpec("ethernet")
        
        # Add standard Ethernet registers
        for name, reg_def in self.ETHERNET_REGISTERS.items():
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
            name="rx_counter",
            width=32,
            increment_rate=1,
            description="RX data counter"
        ))
        
        spec.add_counter(BehavioralCounter(
            name="rx_packet_counter",
            width=32,
            increment_rate=1,
            description="RX packet counter"
        ))
        
        spec.add_counter(BehavioralCounter(
            name="tx_packet_counter",
            width=32,
            increment_rate=1,
            description="TX packet counter"
        ))
        
        # Validate and return
        if not spec.validate():
            from src.string_utils import log_error_safe
            log_error_safe(logger, "Failed to validate network behavioral spec")
            return None
            
        return spec
