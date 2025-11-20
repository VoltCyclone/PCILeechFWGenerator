#!/usr/bin/env python3
"""
Centralized VFIO Decision System

This module provides a single, authoritative decision point for VFIO operations
to eliminate the scattered logic and confusing logging throughout the codebase.
"""

import os
import platform

from dataclasses import dataclass
from typing import List, Optional

from src.string_utils import log_info_safe, log_debug_safe, safe_format


@dataclass(frozen=True)
class VFIODecision:
    """Immutable VFIO decision result."""
    enabled: bool
    reasons: List[str]
    context: str

    def __bool__(self) -> bool:
        """Allow boolean evaluation."""
        return self.enabled


class VFIODecisionMaker:
    """Centralized VFIO decision maker."""

    def __init__(self, logger=None):
        """Initialize with optional logger."""
        self.logger = logger
        self._cached_decision: Optional[VFIODecision] = None

    def decide(self, args=None, env=None) -> VFIODecision:
        """
        Make VFIO decision once and cache it.

        Args:
            args: Command line arguments (optional)
            env: Environment variables dict (optional, defaults to os.environ)

        Returns:
            VFIODecision with clear reasoning
        """
        if self._cached_decision is not None:
            return self._cached_decision

        env = env or os.environ
        reasons = []

        # Check explicit disable flags first
        if self._is_explicitly_disabled(env):
            reasons.extend(self._get_disable_reasons(env))
            context = "explicitly_disabled"
            enabled = False
        # Check host context mode
        elif self._is_host_context_only(args, env):
            reasons.append("host-context-only mode")
            context = "host_context"
            enabled = False
        # Check device context availability
        elif self._has_device_context(env):
            reasons.append("preloaded device context available")
            context = "device_context"
            enabled = False
        # Check container environment
        elif self._is_container_without_vfio():
            reasons.append("container environment without VFIO support")
            context = "container"
            enabled = False
        else:
            reasons.append("VFIO available and enabled")
            context = "enabled"
            enabled = True

        decision = VFIODecision(
            enabled=enabled,
            reasons=reasons,
            context=context
        )

        self._cached_decision = decision
        self._log_decision(decision)
        return decision

    def _is_explicitly_disabled(self, env: dict) -> bool:
        """Check if VFIO is explicitly disabled via environment."""
        disable_vfio = env.get("PCILEECH_DISABLE_VFIO", "").lower()
        return disable_vfio in ("1", "true", "yes", "on")

    def _is_host_context_only(self, args, env: dict) -> bool:
        """Check if running in host-context-only mode."""
        env_flag = env.get("PCILEECH_HOST_CONTEXT_ONLY", "").lower() in (
            "1", "true", "yes", "on"
        )
        arg_flag = getattr(args, "host_context_only", False) if args else False
        return env_flag or arg_flag

    def _has_device_context(self, env: dict) -> bool:
        """Check if device context is preloaded."""
        return bool(env.get("DEVICE_CONTEXT_PATH"))

    def _is_container_without_vfio(self) -> bool:
        """Check if running in container without VFIO support."""
        if not self._is_container():
            return False
        return not (self._is_linux() and self._has_vfio_devices())

    def _is_container(self) -> bool:
        """Detect container environment."""
        try:
            return (
                os.path.exists("/.dockerenv") or
                os.path.exists("/run/.containerenv") or
                self._has_container_cgroup()
            )
        except Exception:
            return False

    def _has_container_cgroup(self) -> bool:
        """Check cgroup for container indicators."""
        try:
            with open("/proc/1/cgroup", "r") as f:
                data = f.read()
            return any(tag in data for tag in ("docker", "kubepods", "containerd"))
        except Exception:
            return False

    def _is_linux(self) -> bool:
        """Check if running on Linux."""
        try:
            return platform.system().lower() == "linux"
        except Exception:
            return False

    def _has_vfio_devices(self) -> bool:
        """Check for VFIO device availability."""
        try:
            import glob
            control = "/dev/vfio/vfio"
            if not os.path.exists(control):
                return False
            groups = glob.glob("/dev/vfio/[0-9]*")
            return any(os.access(g, os.R_OK | os.W_OK) for g in groups)
        except Exception:
            return False

    def _get_disable_reasons(self, env: dict) -> List[str]:
        """Get specific reasons for explicit disable."""
        reasons = []
        disable_env = env.get("PCILEECH_DISABLE_VFIO", "").lower()
        if disable_env in ("1", "true", "yes", "on"):
            reasons.append("PCILEECH_DISABLE_VFIO=1")
        return reasons

    def _log_decision(self, decision: VFIODecision) -> None:
        """Log the decision with consistent formatting."""
        if not self.logger:
            return

        if decision.enabled:
            log_info_safe(
                self.logger,
                "VFIO operations enabled",
                prefix="VFIO_DECISION"
            )
        else:
            reason_text = (", ".join(decision.reasons) if decision.reasons
                           else "policy")
            
            # Provide clearer messaging for common scenarios
            if "PCILEECH_DISABLE_VFIO=1" in decision.reasons:
                log_info_safe(
                    self.logger,
                    "VFIO operations skipped (using preloaded device data)",
                    prefix="VFIO_DECISION"
                )
            elif "host-context-only mode" in decision.reasons:
                log_info_safe(
                    self.logger,
                    "VFIO operations skipped (host-context-only mode)",
                    prefix="VFIO_DECISION"
                )
            elif "preloaded device context available" in decision.reasons:
                log_info_safe(
                    self.logger,
                    "VFIO operations skipped (preloaded device data available)",
                    prefix="VFIO_DECISION"
                )
            else:
                log_info_safe(
                    self.logger,
                    safe_format(
                        "VFIO operations disabled ({reasons})",
                        reasons=reason_text
                    ),
                    prefix="VFIO_DECISION"
                )

        # Debug details
        log_debug_safe(
            self.logger,
            safe_format(
                "VFIO decision context: {ctx}, reasons: {reasons}",
                ctx=decision.context,
                reasons=decision.reasons
            ),
            prefix="VFIO_DECISION"
        )


def make_vfio_decision(args=None, env=None, logger=None) -> VFIODecision:
    """
    Convenience function to make VFIO decision.

    Args:
        args: Command line arguments
        env: Environment variables
        logger: Optional logger

    Returns:
        VFIODecision instance
    """
    maker = VFIODecisionMaker(logger)
    return maker.decide(args, env)
