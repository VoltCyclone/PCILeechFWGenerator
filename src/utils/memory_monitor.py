#!/usr/bin/env python3
"""
Memory monitoring utilities for the PCILeech firmware generator.

This module provides memory profiling and monitoring capabilities to help
optimize memory usage during large firmware generation operations.
"""

import functools
import logging
import tracemalloc
from contextlib import contextmanager
from dataclasses import dataclass
from typing import Any, Dict

import psutil

logger = logging.getLogger(__name__)


@dataclass
class MemoryStats:
    """Memory usage statistics."""

    rss_mb: float  # Resident Set Size in MB
    vms_mb: float  # Virtual Memory Size in MB
    peak_mb: float  # Peak memory usage in MB
    available_mb: float  # Available system memory in MB
    percent_used: float  # Percentage of system memory used


class MemoryMonitor:
    """Monitor memory usage during operations."""

    def __init__(self, enable_tracemalloc: bool = False):
        """Initialize memory monitor.

        Args:
            enable_tracemalloc: Enable detailed Python memory tracing
        """
        self.enable_tracemalloc = enable_tracemalloc
        self.peak_memory = 0.0

    @contextmanager
    def monitor_operation(self, operation_name: str):
        """Context manager to monitor memory usage during an operation.

        Args:
            operation_name: Name of the operation being monitored
        """
        # Start monitoring
        if self.enable_tracemalloc:
            tracemalloc.start()

        initial_stats = self._get_memory_stats()
        logger.debug(
            f"[{operation_name}] Initial memory: {initial_stats.rss_mb:.1f} MB"
        )

        try:
            yield self
        finally:
            final_stats = self._get_memory_stats()
            self.peak_memory = max(self.peak_memory, final_stats.rss_mb)

            logger.info(
                f"[{operation_name}] Memory usage: "
                f"{initial_stats.rss_mb:.1f} → {final_stats.rss_mb:.1f} MB "
                f"(Δ {final_stats.rss_mb - initial_stats.rss_mb:+.1f} MB)"
            )

            if self.enable_tracemalloc:
                current, peak = tracemalloc.get_traced_memory()
                logger.debug(
                    f"[{operation_name}] Python memory: "
                    f"current={current / 1024 / 1024:.1f} MB, "
                    f"peak={peak / 1024 / 1024:.1f} MB"
                )
                tracemalloc.stop()

    def _get_memory_stats(self) -> MemoryStats:
        """Get current memory statistics."""
        process = psutil.Process()
        memory_info = process.memory_info()
        system_memory = psutil.virtual_memory()

        return MemoryStats(
            rss_mb=memory_info.rss / 1024 / 1024,
            vms_mb=memory_info.vms / 1024 / 1024,
            peak_mb=self.peak_memory,
            available_mb=system_memory.available / 1024 / 1024,
            percent_used=system_memory.percent,
        )

    def check_memory_pressure(self, threshold_percent: float = 85.0) -> bool:
        """Check if system is under memory pressure.

        Args:
            threshold_percent: Memory usage percentage threshold

        Returns:
            True if memory usage is above threshold
        """
        stats = self._get_memory_stats()
        if stats.percent_used > threshold_percent:
            logger.warning(
                f"High memory usage detected: {stats.percent_used:.1f}% "
                f"(threshold: {threshold_percent}%)"
            )
            return True
        return False

    def suggest_optimizations(self) -> Dict[str, Any]:
        """Suggest memory optimizations based on usage patterns."""
        stats = self._get_memory_stats()
        suggestions = {
            "current_usage_mb": stats.rss_mb,
            "peak_usage_mb": stats.peak_mb,
            "system_memory_percent": stats.percent_used,
            "recommendations": [],
        }

        if stats.percent_used > 80:
            suggestions["recommendations"].append(
                "Consider processing devices in smaller batches"
            )

        if stats.rss_mb > 1000:  # > 1GB
            suggestions["recommendations"].append(
                "Enable lazy loading for large device configurations"
            )

        if self.peak_memory > stats.rss_mb * 2:
            suggestions["recommendations"].append(
                "Optimize memory usage during peak operations"
            )

        return suggestions


# Global memory monitor instance
memory_monitor = MemoryMonitor()


def monitor_memory(operation_name: str):
    """Decorator to monitor memory usage of a function.

    Args:
        operation_name: Name to identify the operation
    """

    def decorator(func):
        @functools.wraps(func)
        def wrapper(*args, **kwargs):
            with memory_monitor.monitor_operation(operation_name):
                return func(*args, **kwargs)

        return wrapper

    return decorator
