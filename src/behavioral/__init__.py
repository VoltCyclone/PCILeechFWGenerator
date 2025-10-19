#!/usr/bin/env python3
"""Behavioral device simulation for PCILeech firmware generation.

This module provides behavioral simulation capabilities that make generated
firmware behave like real devices (e.g., Ethernet showing as connected, 
NVMe showing as ready).
"""

from .base import (
    BehaviorType,
    BehavioralRegister,
    BehavioralCounter,
    BehavioralSpec
)
from .analyzer import (
    BehavioralAnalyzerFactory,
    generate_behavioral_spec
)

__all__ = [
    'BehaviorType',
    'BehavioralRegister',
    'BehavioralCounter',
    'BehavioralSpec',
    'BehavioralAnalyzerFactory',
    'generate_behavioral_spec',
]
