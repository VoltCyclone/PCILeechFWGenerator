#!/usr/bin/env python3
"""Behavioral simulation for storage controllers.

Generates device-specific behavioral specifications to avoid fingerprinting.
All values are derived deterministically from device identifiers.
"""

import hashlib
import logging
from typing import Any, Dict, Optional

from pcileechfwgenerator.string_utils import log_info_safe, safe_format

from .base import BehavioralCounter, BehavioralRegister, BehavioralSpec, BehaviorType

logger = logging.getLogger(__name__)


class StorageBehavioralAnalyzer:
    """Generate behavioral specs for storage controllers.
    
    All register values are derived deterministically from device identifiers
    to avoid static fingerprints while maintaining reproducible builds.
    """
    
    def __init__(self, device_config: Any):
        self._device_config = device_config
        
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
        seed_str = f"StorageBehavior_{self._vendor_id:04X}_{self._device_id:04X}"
        return hashlib.sha256(seed_str.encode()).digest()
    
    def _derive_controller_status(self) -> int:
        """Generate device-specific controller status (CSTS).
        
        NVMe CSTS register format:
        - Bit 0: RDY (Ready) - must be 1 for operational controller
        - Bit 1: CFS (Controller Fatal Status)
        - Bit 2: SHST[0] (Shutdown Status)
        - Bit 3: SHST[1]
        - Bit 4: NSSRO (NVM Subsystem Reset Occurred)
        - Bit 5: PP (Processing Paused)
        """
        # Base: Ready (bit 0)
        status = 0x00000001
        
        # Add device-specific capability bits in upper portion
        # These don't affect functionality but add uniqueness
        status |= (self._device_seed[0] & 0x0F) << 16
        
        return status
    
    def _derive_admin_queue_attrs(self) -> int:
        """Generate device-specific admin queue attributes (AQA).
        
        AQA format:
        - Bits [11:0]: Admin Submission Queue Size (ASQS) - 0's based
        - Bits [27:16]: Admin Completion Queue Size (ACQS) - 0's based
        
        Real NVMe devices typically support queues of 2-4096 entries.
        """
        # Derive queue sizes from device seed (range 31-255 for realism)
        asqs = 31 + (self._device_seed[1] & 0x7F)  # 31-158 entries
        acqs = 31 + (self._device_seed[2] & 0x7F)  # 31-158 entries
        
        # Higher-end devices (based on device ID) get larger queues
        if self._device_id > 0x5000:
            asqs = min(asqs * 2, 0xFFF)
            acqs = min(acqs * 2, 0xFFF)
        
        return (acqs << 16) | asqs
    
    def _derive_controller_capabilities(self) -> int:
        """Generate device-specific controller capabilities (CAP).
        
        CAP format (simplified):
        - Bits [15:0]: MQES (Maximum Queue Entries Supported) - 0's based
        - Bit 16: CQR (Contiguous Queues Required)
        - Bits [19:17]: AMS (Arbitration Mechanism Supported)
        - Bits [23:20]: Reserved
        - Bits [27:24]: TO (Timeout)
        - Bits [31:28]: Reserved (for CSS in NVMe 1.3+)
        """
        # MQES: Maximum queue entries (device-specific)
        mqes = 0x00FF + ((self._device_seed[3] & 0x0F) << 8)  # 255-4351 entries
        
        # CQR: Most devices require contiguous queues
        cqr = 1
        
        # AMS: Support weighted round robin based on device
        ams = (self._device_seed[4] & 0x01)  # 0 or 1
        
        # TO: Timeout in 500ms units (device-specific response time)
        timeout = 4 + (self._device_seed[5] & 0x0F)  # 2-9.5 seconds
        
        return mqes | (cqr << 16) | (ams << 17) | (timeout << 24)
    
    def _derive_version(self) -> int:
        """Generate device-specific NVMe version.
        
        Version format: Major.Minor.Tertiary
        Real devices report their actual NVMe spec version.
        """
        # Common versions: 1.0, 1.1, 1.2, 1.3, 1.4, 2.0
        # Derive based on device ID (newer devices = newer spec)
        if self._device_id > 0x8000:
            return 0x00020000  # NVMe 2.0
        elif self._device_id > 0x5000:
            return 0x00010400  # NVMe 1.4
        elif self._device_id > 0x3000:
            return 0x00010300  # NVMe 1.3
        else:
            return 0x00010200  # NVMe 1.2
    
    def _get_nvme_registers(self) -> Dict[str, Dict[str, Any]]:
        """Generate device-specific NVMe register definitions."""
        csts = self._derive_controller_status()
        aqa = self._derive_admin_queue_attrs()
        cap = self._derive_controller_capabilities()
        version = self._derive_version()
        
        return {
            "controller_capabilities": {
                "offset": 0x0000,
                "behavior": BehaviorType.CONSTANT,
                "value": cap,
                "description": "Controller Capabilities (CAP)"
            },
            "version": {
                "offset": 0x0008,
                "behavior": BehaviorType.CONSTANT,
                "value": version,
                "description": "Version (VS)"
            },
            "controller_status": {
                "offset": 0x001C,
                "behavior": BehaviorType.CONSTANT,
                "value": csts,
                "description": "Controller Status (CSTS)"
            },
            "admin_queue_attrs": {
                "offset": 0x0024,
                "behavior": BehaviorType.CONSTANT,
                "value": aqa,
                "description": "Admin Queue Attributes (AQA)"
            },
            "controller_config": {
                "offset": 0x0014,
                "behavior": BehaviorType.WRITE_CAPTURE,
                "default": 0x00000000,
                "description": "Controller Configuration (CC)"
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
        
    def generate_spec(self) -> Optional[BehavioralSpec]:
        """Generate behavioral specification for storage device."""
        log_info_safe(logger, safe_format("Generating storage behavioral spec for device={dev}",
                                 dev=getattr(self._device_config, 'device_id', 'unknown')))
        
        spec = BehavioralSpec("nvme")
        
        # Get device-specific register definitions
        registers = self._get_nvme_registers()
        
        # Add NVMe registers
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
            name="cq_head_counter",
            width=16,
            increment_rate=1,
            description="Completion queue head counter"
        ))
        
        # Validate and return
        if not spec.validate():
            from pcileechfwgenerator.string_utils import log_error_safe
            log_error_safe(logger, "Failed to validate storage behavioral spec")
            return None
            
        return spec
