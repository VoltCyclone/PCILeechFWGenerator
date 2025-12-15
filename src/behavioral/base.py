#!/usr/bin/env python3
"""Base behavioral register infrastructure for device simulation."""

import logging
from typing import Dict, Any, List, Optional
from dataclasses import dataclass
from enum import Enum

from src.string_utils import log_error_safe, log_debug_safe, safe_format


logger = logging.getLogger(__name__)


class BehaviorType(Enum):
    """Register behavior types for simulation."""
    CONSTANT = "constant"              # Always returns fixed value
    AUTO_INCREMENT = "auto_increment"  # Auto-incrementing counter
    WRITE_CAPTURE = "write_capture"    # Captures written value
    RANDOM = "random"                  # Random data generation
    PATTERN = "pattern"                # Pattern-based generation
    TRIGGERED = "triggered"            # State change on trigger
    PERIODIC = "periodic"              # Periodic value changes


@dataclass
class BehavioralRegister:
    """Definition of a behavioral register."""
    name: str
    offset: int
    behavior: BehaviorType
    default_value: int = 0x00000000
    pattern: Optional[str] = None
    counter_bits: Optional[int] = None
    description: str = ""
    read_only: bool = False
    
    def to_dict(self) -> Dict[str, Any]:
        """Convert to template-compatible dictionary."""
        result = {
            "offset": self.offset,
            "behavior": self.behavior.value,
            "default": self.default_value,
            "description": self.description,
            "read_only": self.read_only
        }
        if self.pattern:
            result["pattern"] = self.pattern
        if self.counter_bits:
            result["counter_bits"] = self.counter_bits
        return result


@dataclass
class BehavioralCounter:
    """Definition of a behavioral counter."""
    name: str
    width: int
    increment_rate: int = 1
    reset_value: int = 0
    description: str = ""
    
    def to_dict(self) -> Dict[str, Any]:
        """Convert to template-compatible dictionary."""
        return {
            "width": self.width,
            "increment_rate": self.increment_rate,
            "reset_value": self.reset_value,
            "description": self.description
        }


class BehavioralSpec:
    """Complete behavioral specification for a device."""
    
    def __init__(self, device_category: str):
        self.device_category = device_category
        self.registers: Dict[str, BehavioralRegister] = {}
        self.counters: Dict[str, BehavioralCounter] = {}
        self.state_machines: Dict[str, Any] = {}
        
    def add_register(self, register: BehavioralRegister) -> None:
        """Add a behavioral register."""
        log_debug_safe(logger, safe_format("Adding behavioral register: {name} at 0x{offset:04X}",
                                  name=register.name, offset=register.offset))
        self.registers[register.name] = register
        
    def add_counter(self, counter: BehavioralCounter) -> None:
        """Add a behavioral counter."""
        log_debug_safe(logger, safe_format("Adding counter: {name} ({width} bits)",
                                  name=counter.name, width=counter.width))
        self.counters[counter.name] = counter
        
    def to_dict(self) -> Dict[str, Any]:
        """Convert to template-compatible dictionary."""
        return {
            "device_category": self.device_category,
            "registers": {k: v.to_dict() for k, v in self.registers.items()},
            "counters": {k: v.to_dict() for k, v in self.counters.items()},
            "state_machines": self.state_machines
        }
        
    def validate(self) -> bool:
        """Validate the behavioral specification."""
        # Check for offset conflicts
        offsets = {}
        for name, reg in self.registers.items():
            if reg.offset in offsets:
                log_error_safe(logger, safe_format("Offset conflict: {name1} and {name2} at 0x{offset:04X}",
                                          name1=offsets[reg.offset], name2=name, offset=reg.offset))
                return False
            offsets[reg.offset] = name
            
        # Validate counter references in patterns
        for name, reg in self.registers.items():
            if reg.pattern and reg.behavior == BehaviorType.AUTO_INCREMENT:
                # Check if pattern references valid counters
                for counter_name in self.counters.keys():
                    if counter_name in reg.pattern:
                        log_debug_safe(logger, safe_format("Register {reg} uses counter {cnt}",
                                                  reg=name, cnt=counter_name))
                        
        return True


def require(condition: bool, message: str, **context) -> None:
    """Validate condition or exit with error."""
    if not condition:
        log_error_safe(logger, safe_format("Build aborted: {msg} | ctx={ctx}", 
                                  msg=message, ctx=context))
        raise SystemExit(2)
