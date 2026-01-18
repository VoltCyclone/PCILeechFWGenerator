"""
Loading Manager Utility for PCILeech TUI application.

This module provides a loading manager for showing visual feedback
during asynchronous operations.
"""

import asyncio
import logging
import time
from typing import Any, Callable, Coroutine, Optional, TypeVar

# Set up logging
logger = logging.getLogger(__name__)

# Type variable for the return type of the operation
T = TypeVar("T")


class LoadingManager:
    """
    Manages loading indicators and visual feedback during async operations.

    This class provides a way to show loading indicators during asynchronous
    operations, track the state of multiple concurrent operations, and update
    the UI accordingly.
    """

    def __init__(self, app):
        """
        Initialize the LoadingManager.

        Args:
            app: The application instance that will use this loading manager.
        """
        self.app = app
        self.loading_states = {}
        self.start_times = {}
        self.timeout = 30.0  # Default timeout in seconds

    async def with_loading(
        self, operation_id: str, operation: Callable[[], Coroutine[Any, Any, T]]
    ) -> T:
        """
        Show loading indicator during an async operation.

        Args:
            operation_id: A unique identifier for the operation.
            operation: The async operation to execute.

        Returns:
            The result of the operation.

        Raises:
            TimeoutError: If the operation exceeds the timeout.
            Exception: Any exception raised by the operation.
        """
        self.set_loading(operation_id, True)
        try:
            # Create a task for the operation
            task = asyncio.create_task(operation())

            # Wait for the operation to complete with timeout handling
            done, pending = await asyncio.wait([task], timeout=self.timeout)

            if task in pending:
                task.cancel()
                self.set_loading(operation_id, False)
                raise TimeoutError(
                    f"Operation {operation_id} timed out after {self.timeout} seconds"
                )

            # Return the result or re-raise any exception
            return await task
        except asyncio.CancelledError:
            logger.warning(f"Operation {operation_id} was cancelled")
            raise
        except Exception as e:
            logger.error(f"Error in operation {operation_id}: {str(e)}")
            raise
        finally:
            self.set_loading(operation_id, False)

    def set_loading(self, operation_id: str, loading: bool) -> None:
        """
        Set the loading state for an operation and update the UI.

        Args:
            operation_id: The identifier of the operation.
            loading: Whether the operation is loading or not.
        """
        previous_state = self.loading_states.get(operation_id, False)
        self.loading_states[operation_id] = loading

        if loading and not previous_state:
            # Operation just started loading
            self.start_times[operation_id] = time.time()
            logger.debug(f"Operation {operation_id} started loading")
        elif not loading and previous_state:
            # Operation just finished loading
            if operation_id in self.start_times:
                duration = time.time() - self.start_times[operation_id]
                logger.debug(
                    f"Operation {operation_id} finished after {duration:.2f} seconds"
                )
                del self.start_times[operation_id]

        # Update the UI
        self._update_loading_ui()

    def _update_loading_ui(self) -> None:
        """
        Update the UI to reflect the current loading states.
        """
        if hasattr(self.app, "call_after_refresh"):
            self.app.call_after_refresh(self._do_update_loading_ui)
        else:
            self._do_update_loading_ui()

    def _do_update_loading_ui(self) -> None:
        """
        Actual implementation of UI update for loading states.
        """
        # Count active operations
        active_operations = [
            op_id for op_id, loading in self.loading_states.items() if loading
        ]

        if not active_operations:
            # No active operations, clear loading indicators
            if hasattr(self.app, "hide_loading_indicator"):
                self.app.hide_loading_indicator()
            return

        # Show loading indicator with the first active operation
        primary_operation = active_operations[0]

        if hasattr(self.app, "show_loading_indicator"):
            elapsed = time.time() - self.start_times.get(primary_operation, time.time())
            self.app.show_loading_indicator(
                operation_id=primary_operation,
                elapsed_time=elapsed,
                total_operations=len(active_operations),
            )

    def is_loading(self, operation_id: Optional[str] = None) -> bool:
        """
        Check if an operation is currently loading.

        Args:
            operation_id: The identifier of the operation to check.
                         If None, checks if any operation is loading.

        Returns:
            True if the specified operation (or any operation) is loading,
            False otherwise.
        """
        if operation_id is not None:
            return self.loading_states.get(operation_id, False)
        return any(self.loading_states.values())

    def set_timeout(self, timeout: float) -> None:
        """
        Set the default timeout for operations.

        Args:
            timeout: The timeout value in seconds.
        """
        self.timeout = max(1.0, timeout)  # Ensure minimum 1 second timeout
        logger.debug(f"Operation timeout set to {self.timeout} seconds")
