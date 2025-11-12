#!/usr/bin/env python3
"""
File Manifest System

Tracks file operations to prevent duplicates and provide audit trails.
"""

import json
import time

from dataclasses import dataclass, asdict
from pathlib import Path
from typing import Dict, List, Optional, Set

from src.string_utils import log_debug_safe, safe_format


@dataclass
class FileOperation:
    """Represents a single file operation."""
    source_path: str
    dest_path: str
    operation_type: str  # 'copy', 'generate', 'template'
    timestamp: float
    size_bytes: int
    checksum: Optional[str] = None
    metadata: Optional[Dict] = None

    def __post_init__(self):
        if self.metadata is None:
            self.metadata = {}


@dataclass
class FileManifest:
    """Complete manifest of file operations."""
    operations: List[FileOperation]
    created_at: float
    total_files: int
    total_size_bytes: int
    duplicate_operations: List[str]  # List of skipped duplicates


class FileManifestTracker:
    """Tracks file operations to prevent duplicates."""

    def __init__(self, logger=None):
        """Initialize tracker."""
        self.logger = logger
        self._operations: List[FileOperation] = []
        self._destinations: Set[str] = set()  # Normalized destination paths
        self._duplicates: List[str] = []

    def add_copy_operation(
        self,
        source_path: Path,
        dest_path: Path,
        metadata: Optional[Dict] = None
    ) -> bool:
        """
        Add a copy operation, checking for duplicates.

        Args:
            source_path: Source file path
            dest_path: Destination file path
            metadata: Optional metadata

        Returns:
            True if operation was added, False if skipped as duplicate
        """
        dest_str = str(dest_path.resolve())

        # Check for duplicate destination
        if dest_str in self._destinations:
            self._duplicates.append(dest_str)
            log_debug_safe(
                self.logger,
                safe_format(
                    "Skipping duplicate copy to {dest}",
                    dest=dest_path.name
                ),
                prefix="FILEMGR"
            )
            return False

        # Get file size
        try:
            size = source_path.stat().st_size if source_path.exists() else 0
        except Exception:
            size = 0

        operation = FileOperation(
            source_path=str(source_path),
            dest_path=str(dest_path),
            operation_type="copy",
            timestamp=time.time(),
            size_bytes=size,
            metadata=metadata or {}
        )

        self._operations.append(operation)
        self._destinations.add(dest_str)

        log_debug_safe(
            self.logger,
            safe_format(
                "Queued copy: {src} -> {dest} ({size} bytes)",
                src=source_path.name,
                dest=dest_path.name,
                size=size
            ),
            prefix="FILEMGR"
        )

        return True

    def add_generate_operation(
        self,
        dest_path: Path,
        content: str,
        operation_type: str = "generate",
        metadata: Optional[Dict] = None
    ) -> bool:
        """
        Add a file generation operation.

        Args:
            dest_path: Destination file path
            content: File content
            operation_type: Type of generation
            metadata: Optional metadata

        Returns:
            True if operation was added, False if skipped as duplicate
        """
        dest_str = str(dest_path.resolve())

        # Check for duplicate destination
        if dest_str in self._destinations:
            self._duplicates.append(dest_str)
            log_debug_safe(
                self.logger,
                safe_format(
                    "Skipping duplicate generation to {dest}",
                    dest=dest_path.name
                ),
                prefix="FILEMGR"
            )
            return False

        size = len(content.encode('utf-8'))

        operation = FileOperation(
            source_path="<generated>",
            dest_path=str(dest_path),
            operation_type=operation_type,
            timestamp=time.time(),
            size_bytes=size,
            metadata=metadata or {}
        )

        self._operations.append(operation)
        self._destinations.add(dest_str)

        log_debug_safe(
            self.logger,
            safe_format(
                "Queued generation: {dest} ({size} bytes)",
                dest=dest_path.name,
                size=size
            ),
            prefix="FILEMGR"
        )

        return True

    def get_manifest(self) -> FileManifest:
        """Get complete file manifest."""
        total_size = sum(op.size_bytes for op in self._operations)

        return FileManifest(
            operations=self._operations.copy(),
            created_at=time.time(),
            total_files=len(self._operations),
            total_size_bytes=total_size,
            duplicate_operations=self._duplicates.copy()
        )

    def save_manifest(self, output_path: Path) -> None:
        """Save manifest to JSON file."""
        manifest = self.get_manifest()

        # Convert to serializable format
        data = {
            "operations": [asdict(op) for op in manifest.operations],
            "created_at": manifest.created_at,
            "total_files": manifest.total_files,
            "total_size_bytes": manifest.total_size_bytes,
            "duplicate_operations": manifest.duplicate_operations,
            "summary": {
                "copy_operations": len([op for op in manifest.operations
                                       if op.operation_type == "copy"]),
                "generate_operations": len([op for op in manifest.operations
                                            if op.operation_type == "generate"]),
                "template_operations": len([op for op in manifest.operations
                                            if op.operation_type == "template"]),
                "duplicates_skipped": len(manifest.duplicate_operations)
            }
        }

        with open(output_path, 'w') as f:
            json.dump(data, f, indent=2)

        log_debug_safe(
            self.logger,
            safe_format(
                "Saved file manifest: {files} files, {size} bytes",
                files=manifest.total_files,
                size=manifest.total_size_bytes
            ),
            prefix="FILEMGR"
        )

    def get_operations_by_type(self, operation_type: str) -> List[FileOperation]:
        """Get operations filtered by type."""
        return [op for op in self._operations if op.operation_type == operation_type]

    def get_destinations(self) -> Set[str]:
        """Get all destination paths."""
        return self._destinations.copy()

    def has_destination(self, dest_path: Path) -> bool:
        """Check if destination already exists in manifest."""
        return str(dest_path.resolve()) in self._destinations

    def get_duplicate_count(self) -> int:
        """Get number of duplicate operations skipped."""
        return len(self._duplicates)

    def clear(self) -> None:
        """Clear all operations."""
        self._operations.clear()
        self._destinations.clear()
        self._duplicates.clear()


def create_manifest_tracker(logger=None) -> FileManifestTracker:
    """Create a new file manifest tracker."""
    return FileManifestTracker(logger)
