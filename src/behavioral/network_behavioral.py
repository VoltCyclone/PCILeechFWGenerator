#!/usr/bin/env python3
"""Behavioral simulation for network controllers.

Generates device-specific behavioral specifications to avoid fingerprinting.
All values are derived deterministically from device identifiers.
"""

import hashlib
import logging
from typing import Any, Dict, Optional

from pcileechfwgenerator.string_utils import log_debug_safe, log_info_safe, safe_format

from .base import BehavioralCounter, BehavioralRegister, BehavioralSpec, BehaviorType

logger = logging.getLogger(__name__)


class NetworkBehavioralAnalyzer:
    """Generate behavioral specs for network controllers.
    
    All register values are derived deterministically from device identifiers
    to avoid static fingerprints while maintaining reproducible builds.
    """
    
    def __init__(self, device_config: Any):
        self._device_config = device_config
        self._subclass = getattr(device_config, 'subclass_code', 0)
        
        # Extract device identifiers for deterministic value generation
        # Fallback to 0 if not provided - seed generation handles zero case
        self._vendor_id = self._extract_id(device_config, 'vendor_id', 0)
        self._device_id = self._extract_id(device_config, 'device_id', 0)
        
        # Generate device-specific seed for all derived values
        self._device_seed = self._generate_device_seed()
    
    def _extract_id(self, config: Any, attr: str, default: int) -> int:
        """Extract device identifier, handling various input types."""
        value = getattr(config, attr, default)
        if value is None:
            return default
        if isinstance(value, int):
            return value
        if isinstance(value, str):
            try:
                return int(value, 16) if value.startswith('0x') or value.startswith('0X') else int(value)
            except (ValueError, TypeError):
                return default
        # Handle Mock or other objects by trying to convert to int
        try:
            return int(value)
        except (ValueError, TypeError):
            return default
        
    def _generate_device_seed(self) -> bytes:
        """Generate a deterministic seed from device identifiers."""
        seed_str = f"NetworkBehavior_{self._vendor_id:04X}_{self._device_id:04X}"
        return hashlib.sha256(seed_str.encode()).digest()
    
    def _derive_mac_address(self) -> tuple:
        """Generate a device-specific MAC address.
        
        Uses IEEE OUI format:
        - First 3 bytes: Derived from vendor ID (simulating vendor OUI)
        - Last 3 bytes: Derived from device ID + seed
        
        Returns:
            Tuple of (mac_low_32bits, mac_high_16bits)
        """
        # Use vendor ID to create a pseudo-OUI (with local bit set to indicate locally administered)
        oui_byte0 = (self._vendor_id & 0xFF) | 0x02  # Set locally administered bit
        oui_byte1 = (self._vendor_id >> 8) & 0xFF
        oui_byte2 = self._device_seed[0]
        
        # Device-specific bytes from seed
        nic_byte0 = self._device_seed[1]
        nic_byte1 = self._device_seed[2]
        nic_byte2 = self._device_seed[3]
        
        # Pack into 32-bit low and 16-bit high
        # MAC format: OUI[0]:OUI[1]:OUI[2]:NIC[0]:NIC[1]:NIC[2]
        mac_low = (nic_byte2 << 24) | (nic_byte1 << 16) | (nic_byte0 << 8) | oui_byte2
        mac_high = (oui_byte1 << 8) | oui_byte0
        
        log_debug_safe(logger, safe_format(
            "Generated MAC address: {b0:02X}:{b1:02X}:{b2:02X}:{b3:02X}:{b4:02X}:{b5:02X}",
            b0=oui_byte0, b1=oui_byte1, b2=oui_byte2,
            b3=nic_byte0, b4=nic_byte1, b5=nic_byte2
        ))
        
        return (mac_low, mac_high)
    
    def _derive_link_status(self) -> int:
        """Generate device-specific link status value.
        
        Real NICs report various status bits. We generate a realistic value
        that indicates link up with appropriate speed/duplex bits.
        """
        # Base: Link up (bit 0)
        status = 0x00000001
        
        # Add speed indication based on device ID (simulating different NIC capabilities)
        # Bits [3:1] often indicate speed: 001=10Mbps, 010=100Mbps, 011=1Gbps, 100=10Gbps
        speed_bits = ((self._device_id >> 4) & 0x3) + 1  # 1-4 range
        status |= (speed_bits << 1)
        
        # Full duplex (bit 4) - usually set for modern NICs
        status |= 0x10
        
        # Add some device-specific bits from seed
        status |= (self._device_seed[4] & 0xE0) << 8  # Upper bits for vendor-specific flags
        
        return status
    
    def _derive_rx_pattern(self) -> str:
        """Generate device-specific RX data pattern."""
        # Use device-specific prefix instead of static 0xAABB
        prefix = (self._device_seed[5] << 8) | self._device_seed[6]
        return f"32'h{prefix:04X}0000 | rx_counter[15:0]"
    
    def _get_ethernet_registers(self) -> Dict[str, Dict[str, Any]]:
        """Generate device-specific Ethernet register definitions."""
        mac_low, mac_high = self._derive_mac_address()
        link_status = self._derive_link_status()
        rx_pattern = self._derive_rx_pattern()
        
        return {
            # Standard Ethernet controller registers with device-specific values
            "link_status": {
                "offset": 0x0000,
                "behavior": BehaviorType.CONSTANT,
                "value": link_status,
                "description": "Link status (device-specific)"
            },
            "rx_data": {
                "offset": 0x0004,
                "behavior": BehaviorType.AUTO_INCREMENT,
                "pattern": rx_pattern,
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
                "value": mac_low,
                "description": "MAC address low 32 bits (device-specific)"
            },
            "mac_addr_high": {
                "offset": 0x0014,
                "behavior": BehaviorType.CONSTANT,
                "value": mac_high,
                "description": "MAC address high 16 bits (device-specific)"
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
            },
            # Additional registers for realism
            "interrupt_status": {
                "offset": 0x0028,
                "behavior": BehaviorType.MIXED,
                "value": 0x00000000,
                "description": "Interrupt status (RW1C)"
            },
            "interrupt_mask": {
                "offset": 0x002C,
                "behavior": BehaviorType.WRITE_CAPTURE,
                "default": 0x00000000,
                "description": "Interrupt mask"
            }
        }
        
    def generate_spec(self) -> Optional[BehavioralSpec]:
        """Generate behavioral specification for network device."""
        log_info_safe(logger, safe_format("Generating network behavioral spec for device={dev}",
                                 dev=getattr(self._device_config, 'device_id', 'unknown')))
        
        spec = BehavioralSpec("ethernet")
        
        # Get device-specific register definitions
        registers = self._get_ethernet_registers()
        
        # Add registers to spec
        for name, reg_def in registers.items():
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
            from pcileechfwgenerator.string_utils import log_error_safe
            log_error_safe(logger, "Failed to validate network behavioral spec")
            return None
            
        return spec
