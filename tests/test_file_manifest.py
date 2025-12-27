#!/usr/bin/env python3
"""Unit tests for file manifest tracking."""

import json
import pytest
import time
from pathlib import Path
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
        timestamp = time.time()
        op = FileOperation(
            source_path="/path/to/source.sv",
            dest_path="/path/to/dest.sv",
            operation_type="copy",
            timestamp=timestamp,
            size_bytes=1024
        )
        assert op.source_path == "/path/to/source.sv"
        assert op.dest_path == "/path/to/dest.sv"
        assert op.operation_type == "copy"
        assert op.timestamp == timestamp
        assert op.size_bytes == 1024

    def test_file_operation_to_dict(self):
        """Test FileOperation can be converted to dict using asdict."""
        from dataclasses import asdict
        timestamp = time.time()
        op = FileOperation(
            source_path="/src/file.sv",
            dest_path="/dst/file.sv",
            operation_type="copy",
            timestamp=timestamp,
            size_bytes=512
        )
        result = asdict(op)

        assert result["source_path"] == "/src/file.sv"
        assert result["dest_path"] == "/dst/file.sv"
        assert result["operation_type"] == "copy"
        assert result["timestamp"] == timestamp
        assert result["size_bytes"] == 512


class TestFileManifest:
    """Test FileManifest dataclass."""

    def test_file_manifest_creation(self):
        """Test creating a FileManifest."""
        timestamp = time.time()
        manifest = FileManifest(
            operations=[],
            created_at=timestamp,
            total_files=0,
            total_size_bytes=0,
            duplicate_operations=[]
        )
        assert manifest.operations == []
        assert manifest.created_at == timestamp
        assert manifest.total_files == 0
        assert manifest.total_size_bytes == 0

    def test_file_manifest_to_dict(self):
        """Test FileManifest can be converted to dict."""
        from dataclasses import asdict
        timestamp = time.time()
        op = FileOperation(
            source_path="/a/b.sv",
            dest_path="/c/b.sv",
            operation_type="copy",
            timestamp=timestamp,
            size_bytes=256
        )
        manifest = FileManifest(
            operations=[op],
            created_at=timestamp,
            total_files=1,
            total_size_bytes=256,
            duplicate_operations=[]
        )

        result = asdict(manifest)

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
        manifest = tracker.get_manifest()
        assert isinstance(manifest, FileManifest)
        assert len(manifest.operations) == 0

    def test_record_copy_new_file(self, tmp_path):
        """Test recording a new file copy."""
        logger = MagicMock()
        tracker = FileManifestTracker(logger=logger)
        
        # Create a temporary file to copy
        src_file = tmp_path / "source.sv"
        src_file.write_text("module test;")
        dest_file = tmp_path / "dest.sv"

        result = tracker.add_copy_operation(
            source_path=src_file,
            dest_path=dest_file
        )

        assert result is True
        manifest = tracker.get_manifest()
        assert len(manifest.operations) == 1
        assert str(src_file) in manifest.operations[0].source_path
        assert manifest.operations[0].operation_type == "copy"

    def test_record_copy_duplicate_basename(self, tmp_path):
        """Test duplicate file detection by destination."""
        logger = MagicMock()
        tracker = FileManifestTracker(logger=logger)
        
        # Create temporary files
        src1 = tmp_path / "source1.sv"
        src1.write_text("module test1;")
        src2 = tmp_path / "source2.sv"
        src2.write_text("module test2;")
        dest = tmp_path / "dest.sv"

        # First copy succeeds
        result1 = tracker.add_copy_operation(
            source_path=src1,
            dest_path=dest
        )
        assert result1 is True

        # Second copy with same destination fails
        result2 = tracker.add_copy_operation(
            source_path=src2,
            dest_path=dest
        )
        assert result2 is False

    def test_record_copy_different_basenames(self, tmp_path):
        """Test recording files with different destinations."""
        logger = MagicMock()
        tracker = FileManifestTracker(logger=logger)
        
        src1 = tmp_path / "source1.sv"
        src1.write_text("module test1;")
        src2 = tmp_path / "source2.sv"
        src2.write_text("module test2;")
        dest1 = tmp_path / "dest1.sv"
        dest2 = tmp_path / "dest2.sv"

        result1 = tracker.add_copy_operation(
            source_path=src1,
            dest_path=dest1
        )
        result2 = tracker.add_copy_operation(
            source_path=src2,
            dest_path=dest2
        )

        assert result1 is True
        assert result2 is True
        manifest = tracker.get_manifest()
        assert len(manifest.operations) == 2

    def test_was_file_copied_true(self, tmp_path):
        """Test has_destination returns True for tracked file."""
        logger = MagicMock()
        tracker = FileManifestTracker(logger=logger)
        
        src = tmp_path / "test.sv"
        src.write_text("module test;")
        dest = tmp_path / "dest.sv"
        
        tracker.add_copy_operation(source_path=src, dest_path=dest)
        
        assert tracker.has_destination(dest) is True

    def test_was_file_copied_false(self, tmp_path):
        """Test has_destination returns False for untracked file."""
        logger = MagicMock()
        tracker = FileManifestTracker(logger=logger)
        
        dest = tmp_path / "notfound.sv"
        assert tracker.has_destination(dest) is False

    def test_get_stats(self, tmp_path):
        """Test get_manifest returns statistics."""
        logger = MagicMock()
        tracker = FileManifestTracker(logger=logger)
        
        # Create test files
        files = []
        for i in range(3):
            f = tmp_path / f"file{i}.sv"
            f.write_text(f"module test{i};")
            files.append(f)
        
        for i, f in enumerate(files):
            tracker.add_copy_operation(
                source_path=f,
                dest_path=tmp_path / f"dest{i}.sv"
            )
        
        manifest = tracker.get_manifest()
        assert manifest.total_files == 3
        assert len(manifest.operations) == 3

    def test_export_manifest(self, tmp_path):
        """Test saving manifest to JSON."""
        logger = MagicMock()
        tracker = FileManifestTracker(logger=logger)
        
        src = tmp_path / "file.sv"
        src.write_text("module test;")
        dest = tmp_path / "dest.sv"
        
        tracker.add_copy_operation(source_path=src, dest_path=dest)
        
        output_file = tmp_path / "manifest.json"
        tracker.save_manifest(output_file)
        
        assert output_file.exists()
        
        with open(output_file, 'r') as f:
            data = json.load(f)
        
        assert "created_at" in data
        assert "operations" in data
        assert len(data["operations"]) == 1

    def test_get_duplicate_files(self, tmp_path):
        """Test duplicate detection."""
        logger = MagicMock()
        tracker = FileManifestTracker(logger=logger)
        
        src1 = tmp_path / "dup1.sv"
        src1.write_text("module test1;")
        src2 = tmp_path / "dup2.sv"
        src2.write_text("module test2;")
        dest = tmp_path / "dest.sv"
        
        # First succeeds
        tracker.add_copy_operation(source_path=src1, dest_path=dest)
        # Second is duplicate
        tracker.add_copy_operation(source_path=src2, dest_path=dest)
        
        # Check duplicate count
        assert tracker.get_duplicate_count() == 1


class TestCreateManifestTracker:
    """Test convenience function."""

    def test_create_manifest_tracker(self):
        """Test create_manifest_tracker function."""
        logger = MagicMock()
        tracker = create_manifest_tracker(logger)

        assert isinstance(tracker, FileManifestTracker)
        assert tracker.logger == logger

