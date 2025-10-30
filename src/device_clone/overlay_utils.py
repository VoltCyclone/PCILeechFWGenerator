"""Utility helpers for overlay metadata normalization and sizing."""

from __future__ import annotations

import logging

from typing import Any

from src.string_utils import log_debug_safe, safe_format

logger = logging.getLogger(__name__)


def normalize_overlay_entry_count(overlay_data: Any) -> int:
    """Return a normalized overlay entry count from assorted structures.

    Args:
        overlay_data: Overlay metadata emitted by generators or tests. May be
            an integer, iterable, mapping, or a structure containing
            ``OVERLAY_ENTRIES``/``OVERLAY_MAP`` keys.

    Returns:
        Non-negative integer representing the number of overlay entries.
    """
    if overlay_data is None:
        return 0

    # Direct integer-like representations
    if isinstance(overlay_data, int):
        return max(0, overlay_data)

    try:
        # Handle numeric strings
        if isinstance(overlay_data, str) and overlay_data.strip():
            return max(0, int(overlay_data, 0))
    except ValueError as e:
        log_debug_safe(
            logger,
            safe_format(
                "Failed to parse overlay_data string as integer: {data} | error={err}",
                prefix="OVERLAY",
                data=overlay_data,
                err=str(e),
            ),
        )
        # Continue to next fallback handler

    # Sequence types represent entry collections directly
    if isinstance(overlay_data, (list, tuple, set)):
        return len(overlay_data)

    if isinstance(overlay_data, dict):
        # Prefer explicit entry counts if present
        for key in ("OVERLAY_ENTRIES", "overlay_entries"):
            if key in overlay_data:
                return normalize_overlay_entry_count(overlay_data.get(key))
        for key in ("OVERLAY_MAP", "overlay_map"):
            if key in overlay_data:
                return normalize_overlay_entry_count(overlay_data.get(key))
        # Treat other mappings as direct address->mask tables
        return len(overlay_data)

    # Fallback: attempt to coerce via __len__ for custom containers
    if hasattr(overlay_data, "__len__"):
        try:
            return max(0, int(len(overlay_data)))  # type: ignore[arg-type]
        except Exception:
            return 0

    # Final fallback: no recognized representation
    return 0


def compute_sparse_hash_table_size(
    entry_count: int, *, min_size: int = 16, max_size: int = 1 << 20
) -> int:
    """Compute a power-of-two hash table size for sparse overlay lookups."""
    normalized = max(0, entry_count)
    base = max(min_size, normalized * 2)

    size = min_size
    while size < base:
        size <<= 1
        if size >= max_size:
            return max_size
    return max(size, min_size)
