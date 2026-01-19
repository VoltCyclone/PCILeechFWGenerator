"""
Status Monitor

Monitors system status including container availability, USB devices, and system resources.
"""

import asyncio
from typing import Any, Dict

from pcileechfwgenerator.utils.system_status import (
    get_status_summary,
    get_system_status,
)


class StatusMonitor:
    """Monitors system status and resources"""

    def __init__(self):
        self._status_cache: Dict[str, Any] = {}
        self._monitoring = False

    async def get_system_status(self) -> Dict[str, Any]:
        """Get comprehensive system status"""
        status = await get_system_status()
        self._status_cache = status
        return status

    def get_cached_status(self) -> Dict[str, Any]:
        """Get cached status information"""
        return self._status_cache.copy()

    async def start_monitoring(self, interval: float = 5.0) -> None:
        """Start continuous monitoring"""
        self._monitoring = True
        while self._monitoring:
            await self.get_system_status()
            await asyncio.sleep(interval)

    def stop_monitoring(self) -> None:
        """Stop continuous monitoring"""
        self._monitoring = False

    def is_monitoring(self) -> bool:
        """Check if monitoring is active"""
        return self._monitoring

    def get_status_summary(self) -> Dict[str, str]:
        """Get a summary of system status"""
        return get_status_summary(self._status_cache)
