#!/usr/bin/env python3
"""Unit tests for VFIO decision logic."""

import os

import pytest
from pathlib import Path
from unittest.mock import patch, MagicMock

from src.utils.vfio_decision import (
    VFIODecision,
    VFIODecisionMaker,
    make_vfio_decision
)


class TestVFIODecision:
    """Test VFIODecision dataclass."""

    def test_vfio_decision_enabled(self):
        """Test VFIODecision when enabled."""
        decision = VFIODecision(
            enabled=True,
            reasons=["VFIO available and enabled"],
            context="enabled"
        )
        assert decision.enabled is True
        assert "VFIO available" in decision.reasons[0]
        assert decision.context == "enabled"

    def test_vfio_decision_disabled(self):
        """Test VFIODecision when disabled."""
        decision = VFIODecision(
            enabled=False,
            reasons=["VFIO disabled by environment"],
            context="explicitly_disabled"
        )
        assert decision.enabled is False
        assert "disabled" in decision.reasons[0]
        assert decision.context == "explicitly_disabled"


class TestVFIODecisionMaker:
    """Test VFIODecisionMaker class."""

    def test_env_disable_vfio_set(self, monkeypatch):
        """Test PCILEECH_DISABLE_VFIO environment variable."""
        env = {"PCILEECH_DISABLE_VFIO": "1"}
        maker = VFIODecisionMaker()
        decision = maker.decide(env=env)

        assert decision.enabled is False
        assert decision.context == "explicitly_disabled"

    def test_env_host_context_only_set(self, monkeypatch):
        """Test PCILEECH_HOST_CONTEXT_ONLY environment variable."""
        env = {"PCILEECH_HOST_CONTEXT_ONLY": "1"}

        maker = VFIODecisionMaker()
        decision = maker.decide(env=env)

        assert decision.enabled is False
        assert decision.context == "host_context"

    def test_device_context_path_set(self):
        """Test DEVICE_CONTEXT_PATH environment variable."""
        env = {"DEVICE_CONTEXT_PATH": "/path/to/context.json"}

        maker = VFIODecisionMaker()
        decision = maker.decide(env=env)

        assert decision.enabled is False
        assert decision.context == "device_context"

    def test_container_detected(self):
        """Test container detection disables VFIO."""
        env = {}

        maker = VFIODecisionMaker()
        
        # Mock the checks to simulate container environment
        with patch.object(maker, '_is_explicitly_disabled', return_value=False), \
                patch.object(maker, '_is_host_context_only', return_value=False), \
                patch.object(maker, '_has_device_context', return_value=False), \
                patch.object(maker, '_is_container_without_vfio', return_value=True):
            
            decision = maker.decide(env=env)

            assert decision.enabled is False
            assert decision.context == "container"

    def test_vfio_enabled(self):
        """Test VFIO successfully enabled."""
        env = {}

        maker = VFIODecisionMaker()

        # Mock all the checks to pass
        with patch.object(maker, '_is_explicitly_disabled', return_value=False), \
                patch.object(maker, '_is_host_context_only', return_value=False), \
                patch.object(maker, '_has_device_context', return_value=False), \
                patch.object(maker, '_is_container_without_vfio', return_value=False):

            decision = maker.decide(env=env)

            assert decision.enabled is True
            assert decision.context == "enabled"


class TestMakeVFIODecision:
    """Test convenience function."""

    def test_make_vfio_decision(self):
        """Test make_vfio_decision convenience function."""
        env = {"PCILEECH_DISABLE_VFIO": "1"}

        result = make_vfio_decision(env=env)

        assert isinstance(result, VFIODecision)
        assert result.enabled is False
