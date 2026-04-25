"""
Debounced Search Utility

This module provides a debounced search implementation to improve search
performance by delaying the actual search operation until the user stops typing.
"""

import asyncio
from typing import Any, Callable, Coroutine


class DebouncedSearch:
    """
    Implements a debounced search pattern to optimize search operations
    by delaying the execution until user input pauses.

    This reduces unnecessary search operations while the user is still typing,
    improving the overall performance and responsiveness of the application.
    """

    def __init__(self, delay=0.3):
        """
        Initialize a debounced search handler.

        Args:
            delay: Time in seconds to wait after the last input before executing the search
        """
        self.delay = delay
        self._search_task = None

    async def search(
        self, query: str, callback: Callable[[str], Coroutine[Any, Any, Any]]
    ):
        """
        Trigger a search with debouncing.

        Args:
            query: The search query to process
            callback: Async function to call after the debounce delay
        """
        # Cancel previous search
        if self._search_task:
            self._search_task.cancel()

        # Start new search after delay
        self._search_task = asyncio.create_task(self._delayed_search(query, callback))

    async def _delayed_search(
        self, query: str, callback: Callable[[str], Coroutine[Any, Any, Any]]
    ):
        """
        Private method to handle the delayed search execution.

        Args:
            query: The search query to process
            callback: Async function to call with the query
        """
        await asyncio.sleep(self.delay)
        await callback(query)

    def close(self) -> None:
        """Cancel any pending search task. Call this from the owner widget's
        ``on_unmount`` to avoid leaving an orphan task firing against a stale
        callback."""
        if self._search_task and not self._search_task.done():
            self._search_task.cancel()
        self._search_task = None
