#!/usr/bin/env python3
"""Unit tests for file manifest tracking."""

import json
import pytest
from pathlib import Path
from datetime import datetime
from unittest.mock import MagicMock

from src.utils.file_manifest import (
    FileOperation,
    FileManifest,
    FileManifestTracker,
    create_manifest_tracker
)


class TestFileOperation:
    """Test FileOperation dataclass."""

    def test_file_operation_creation(self):
        """Test creating a FileOperation."""
        op = FileOperation(
            source_path="/path/to/source.sv",
            dest_path="/path/to/dest.sv",
            operation_type="copy",
            timestamp=datetime.now()
        )
        assert op.source_path == "/path/to/source.sv"
        assert op.dest_path == "/path/to/dest.sv"
        assert op.operation_type == "copy"
        assert isinstance(op.timestamp, datetime)

    def test_file_operation_to_dict(self):
        """Test FileOperation to_dict method."""
        timestamp = datetime(2025, 11, 10, 12, 0, 0)
        op = FileOperation(
            source_path="/src/file.sv",
            dest_path="/dst/file.sv",
            operation_type="copy",
            timestamp=timestamp
        )
        result = op.to_dict()

        assert result["source_path"] == "/src/file.sv"
        assert result["dest_path"] == "/dst/file.sv"
        assert result["operation_type"] == "copy"
        assert result["timestamp"] == timestamp.isoformat()


class TestFileManifest:
    """Test FileManifest dataclass."""

    def test_file_manifest_creation(self):
        """Test creating a FileManifest."""
        manifest = FileManifest()
        assert manifest.operations == []
        assert isinstance(manifest.created_at, datetime)

    def test_file_manifest_to_dict(self):
        """Test FileManifest to_dict method."""
        manifest = FileManifest()
        op = FileOperation(
            source_path="/a/b.sv",
            dest_path="/c/b.sv",
            operation_type="copy",
            timestamp=datetime.now()
        )
        manifest.operations.append(op)

        result = manifest.to_dict()

        assert "created_at" in result
        assert "operations" in result
        assert len(result["operations"]) == 1
        assert result["operations"][0]["source_path"] == "/a/b.sv"


class TestFileManifestTracker:
    """Test FileManifestTracker class."""

    def test_tracker_initialization(self):
        """Test tracker initialization."""
        logger = MagicMock()
        tracker = FileManifestTracker(logger=logger)

        assert tracker.logger == logger
        assert isinstance(tracker.manifest, FileManifest)
        assert len(tracker.manifest.operations) == 0

    def test_record_copy_new_file(self):
        """Test recording a new file copy."""
        logger = MagicMock()
        tracker = FileManifestTracker(logger=logger)

        result = tracker.record_copy(
            source_path="/src/module.sv",
            dest_path="/build/module.sv"
        )

        assert result is True
        assert len(tracker.manifest.operations) == 1
        assert tracker.manifest.operations[0].source_path == "/src/module.sv"
        assert tracker.manifest.operations[0].operation_type == "copy"

    def test_record_copy_duplicate_basename(self):
        """Test duplicate file detection by basename."""
        logger = MagicMock()
        tracker = FileManifestTracker(logger=logger)

        # First copy succeeds
        result1 = tracker.record_copy(
            source_path="/src/module.sv",
            dest_path="/build/module.sv"
        )
        assert result1 is True

        # Second copy with same basename fails
        result2 = tracker.record_copy(
            source_path="/other/module.sv",
            dest_path="/build/subfolder/module.sv"
        )
        assert result2 is False

        # Should have logged warning
        logger.warning.assert_called()
        warning_msg = logger.warning.call_args[0][0]
        assert "already tracked" in warning_msg.lower()

    def test_record_copy_different_basenames(self):
        """Test recording files with different basenames."""
        logger = MagicMock()
        tracker = FileManifestTracker(logger=logger)

        result1 = tracker.record_copy(
            source_path="/src/module1.sv",
            dest_path="/build/module1.sv"
        )
        result2 = tracker.record_copy(
            source_path="/src/module2.sv",
            dest_path="/build/module2.sv"
        )

        assert result1 is True
        assert result2 is True
        assert len(tracker.manifest.operations) == 2

    def test_was_file_copied_true(self):
        """Test was_file_copied returns True for tracked file."""
        logger = MagicMock()
        tracker = FileManifestTracker(logger=logger)

        tracker.record_copy(
            source_path="/src/test.sv",
            dest_path="/build/test.sv"
        )

        assert tracker.was_file_copied("/build/test.sv") is True
        assert tracker.was_file_copied("/other/test.sv") is True  # Same basename

    def test_was_file_copied_false(self):
        """Test was_file_copied returns False for untracked file."""
        logger = MagicMock()
        tracker = FileManifestTracker(logger=logger)

        assert tracker.was_file_copied("/build/notfound.sv") is False

    def test_get_stats(self):
        """Test get_stats method."""
        logger = MagicMock()
        tracker = FileManifestTracker(logger=logger)

        tracker.record_copy("/src/a.sv", "/dst/a.sv")
        tracker.record_copy("/src/b.xdc", "/dst/b.xdc")
        tracker.record_copy("/src/c.sv", "/dst/c.sv")

        stats = tracker.get_stats()

        assert stats["total_operations"] == 3
        assert stats["by_type"]["copy"] == 3
        assert ".sv" in stats["by_extension"]
        assert stats["by_extension"][".sv"] == 2
        assert stats["by_extension"][".xdc"] == 1

    def test_export_manifest(self, tmp_path):
        """Test exporting manifest to JSON."""
        logger = MagicMock()
        tracker = FileManifestTracker(logger=logger)

        tracker.record_copy("/src/file.sv", "/dst/file.sv")

        output_file = tmp_path / "manifest.json"
        tracker.export_manifest(str(output_file))

        assert output_file.exists()

        with open(output_file, 'r') as f:
            data = json.load(f)

        assert "created_at" in data
        assert "operations" in data
        assert len(data["operations"]) == 1

    def test_get_duplicate_files(self):
        """Test get_duplicate_files method."""
        logger = MagicMock()
        tracker = FileManifestTracker(logger=logger)

        # Record same basename twice
        tracker.record_copy("/src1/dup.sv", "/dst1/dup.sv")
        tracker.record_copy("/src2/dup.sv", "/dst2/dup.sv")

        duplicates = tracker.get_duplicate_files()

        assert "dup.sv" in duplicates
        assert len(duplicates["dup.sv"]) == 2


class TestCreateManifestTracker:
    """Test convenience function."""

    def test_create_manifest_tracker(self):
        """Test create_manifest_tracker function."""
        logger = MagicMock()
        tracker = create_manifest_tracker(logger)

        assert isinstance(tracker, FileManifestTracker)
        assert tracker.logger == logger
