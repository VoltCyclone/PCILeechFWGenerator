#!/usr/bin/env python3
"""Centralized build-related default constants.

Avoid scattering magic numbers across CLI modules. Import these where needed.
"""

# Default behavior profile duration (seconds) used when profiling device behavior
DEFAULT_BEHAVIOR_PROFILE_DURATION = 30

# Default active device timer period in clock cycles (~1ms @ 100MHz)
DEFAULT_ACTIVE_TIMER_PERIOD = 100000

# Default interrupt configuration
DEFAULT_ACTIVE_INTERRUPT_MODE = "msi"
DEFAULT_ACTIVE_INTERRUPT_VECTOR = 0
DEFAULT_ACTIVE_PRIORITY = 15  # Highest priority (0-15 scale)

__all__ = [
    "DEFAULT_BEHAVIOR_PROFILE_DURATION",
    "DEFAULT_ACTIVE_TIMER_PERIOD",
    "DEFAULT_ACTIVE_INTERRUPT_MODE",
    "DEFAULT_ACTIVE_INTERRUPT_VECTOR",
    "DEFAULT_ACTIVE_PRIORITY",
]
