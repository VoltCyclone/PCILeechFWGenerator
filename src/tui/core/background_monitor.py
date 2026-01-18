"""
Background Monitor

This module provides a background monitoring system for optimized status updates
with different intervals for different types of checks.
"""

import asyncio
import logging
from typing import Any, Dict

logger = logging.getLogger(__name__)


class BackgroundMonitor:
    """
    A background monitoring system for efficiently checking various system statuses
    at different intervals.

    This class improves performance by:
    1. Using different intervals for different types of monitoring tasks
    2. Only triggering UI updates when status actually changes
    3. Handling exceptions gracefully to prevent monitoring tasks from failing
    """

    def __init__(self, app):
        """
        Initialize the background monitor.

        Args:
            app: The main application instance
        """
        self.app = app
        self._tasks = {}
        self._running = True
        self._last_status = {}

    def start_monitoring(self):
        """
        Start all monitoring tasks with different intervals for each type of check.

        This creates separate asyncio tasks for different monitoring activities,
        each with an appropriate interval:
        - System status: Checked every 5 seconds
        - Device changes: Checked every 10 seconds
        - Build progress: Checked every 1 second (when active)
        """
        # Use different intervals for different checks
        self._tasks["system"] = asyncio.create_task(
            self._monitor_system_status(interval=5)
        )
        self._tasks["devices"] = asyncio.create_task(
            self._monitor_device_changes(interval=10)
        )
        self._tasks["build"] = asyncio.create_task(
            self._monitor_build_progress(interval=1)
        )

        logger.info("Background monitoring started")

    def stop_monitoring(self):
        """Stop all monitoring tasks."""
        self._running = False

        # Cancel all running tasks
        for name, task in self._tasks.items():
            if not task.done():
                task.cancel()

        self._tasks.clear()
        logger.info("Background monitoring stopped")

    async def _monitor_system_status(self, interval: int):
        """
        Monitor system status at specified interval.

        Args:
            interval: Time in seconds between checks
        """
        while self._running:
            try:
                # Only update changed status
                new_status = await self.app.status_monitor.get_system_status()

                # Compare with previous status
                if new_status != self._last_status.get("system"):
                    self._last_status["system"] = new_status

                    # Safely set system status
                    try:
                        self.app._system_status = new_status
                    except Exception as e:
                        logger.error(f"Failed to set system status: {e}")

                    # Schedule UI update on the main thread, safely
                    try:
                        if hasattr(self.app, "_update_status_display") and callable(
                            self.app._update_status_display
                        ):
                            self.app.call_after_refresh(self.app._update_status_display)
                            logger.debug("System status update scheduled")
                        else:
                            logger.warning(
                                "App does not have _update_status_display method"
                            )
                    except Exception as e:
                        logger.error(f"Failed to schedule status update: {e}")

                    logger.debug("System status updated")
            except Exception as e:
                # Handle error without stopping the monitoring task
                logger.error(f"Error monitoring system status: {e}")

            await asyncio.sleep(interval)

    async def _monitor_device_changes(self, interval: int):
        """
        Monitor device changes at specified interval.

        Args:
            interval: Time in seconds between checks
        """
        while self._running:
            try:
                # Check for device changes
                has_changes = await self.app.device_manager.check_for_changes()

                if has_changes:
                    # Only update if devices have changed
                    devices = await self.app.device_manager.get_devices()

                    # Store current filtered devices
                    self.app._devices = devices

                    # Apply filters and update UI
                    self.app.ui_coordinator.apply_device_filters()
                    self.app.ui_coordinator.update_device_table()
                    logger.debug("Device list updated due to changes")
            except Exception as e:
                # Handle error without stopping the monitoring task
                logger.error(f"Error monitoring device changes: {e}")

            await asyncio.sleep(interval)

    async def _monitor_build_progress(self, interval: int):
        """
        Monitor build progress at specified interval.

        Args:
            interval: Time in seconds between checks
        """
        while self._running:
            try:
                # Only check if a build is in progress
                if hasattr(self.app, "build_progress") and self.app.build_progress:
                    # Get current build progress
                    progress = await self.app.build_orchestrator.get_build_progress()

                    # Only update if progress has changed
                    if progress != self.app.build_progress:
                        self.app.build_progress = progress

                        # Update UI with new progress
                        self.app._on_build_progress(progress)
                        logger.debug(f"Build progress updated: {progress.percentage}%")
            except Exception as e:
                # Handle error without stopping the monitoring task
                logger.error(f"Error monitoring build progress: {e}")

            await asyncio.sleep(interval)

    async def get_system_status(self) -> Dict[str, Any]:
        """
        Get the current system status.

        Returns:
            Dict containing system status information
        """
        try:
            return await self.app.status_monitor.get_system_status()
        except Exception as e:
            logger.error(f"Error getting system status: {e}")
            return {}
