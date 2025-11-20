#!/usr/bin/env python3
"""Simple smoke tests for new utility modules."""

import os
from pathlib import Path
from unittest.mock import MagicMock

import pytest

from src.utils.vfio_decision import (
    VFIODecision,
    VFIODecisionMaker,
    make_vfio_decision)
from src.utils.build_logger import BuildLogger, get_build_logger
from src.utils.file_manifest import create_manifest_tracker


class TestVFIODecisionSmoke:
    """Smoke tests for VFIO decision module."""

    def test_vfio_decision_dataclass(self):
        """Test VFIODecision can be created."""
        decision = VFIODecision(
            enabled=True,
            reasons=["test"],
            context="test"
        )
        assert decision.enabled is True

    def test_vfio_decision_maker_decides(self):
        """Test VFIODecisionMaker can make a decision."""
        maker = VFIODecisionMaker()
        env = {"PCILEECH_DISABLE_VFIO": "1"}
        decision = maker.decide(env=env)
        assert isinstance(decision, VFIODecision)
        assert decision.enabled is False

    def test_make_vfio_decision_function(self):
        """Test convenience function works."""
        env = {"PCILEECH_DISABLE_VFIO": "1"}
        decision = make_vfio_decision(env=env)
        assert isinstance(decision, VFIODecision)


class TestBuildLoggerSmoke:
    """Smoke tests for build logger module."""

    def test_build_logger_creation(self):
        """Test BuildLogger can be created."""
        mock_logger = MagicMock()
        build_logger = BuildLogger(mock_logger)
        assert build_logger.logger == mock_logger

    def test_build_logger_logs(self):
        """Test BuildLogger can log messages."""
        mock_logger = MagicMock()
        build_logger = BuildLogger(mock_logger)

        # Just verify it doesn't crash
        build_logger.info("test message", prefix="BUILD")
        assert mock_logger.info.called

    def test_get_build_logger_function(self):
        """Test convenience function works."""
        logger = get_build_logger()
        assert isinstance(logger, BuildLogger)


class TestFileManifestSmoke:
    """Smoke tests for file manifest module."""

    def test_create_manifest_tracker(self):
        """Test manifest tracker can be created."""
        mock_logger = MagicMock()
        tracker = create_manifest_tracker(mock_logger)
        assert tracker is not None

    def test_manifest_tracker_tracks(self):
        """Test tracker can track files."""
        mock_logger = MagicMock()
        tracker = create_manifest_tracker(mock_logger)

        # Just verify it doesn't crash
        result = tracker.add_copy_operation(
            Path("/src/file.sv"),
            Path("/dst/file.sv")
        )
        assert isinstance(result, bool)
