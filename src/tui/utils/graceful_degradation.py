"""
Graceful degradation utility for the PCILeech TUI application.

This module provides utilities for implementing graceful degradation,
allowing the application to continue functioning when specific features fail.
"""

import asyncio
import functools
import logging
import traceback
from typing import Any, Awaitable, Callable, Dict, Optional, Set, TypeVar, Union

# Set up logging
logger = logging.getLogger(__name__)

# Type variables for the callable and its return type
T = TypeVar("T")
AsyncCallable = Callable[..., Awaitable[T]]
SyncCallable = Callable[..., T]
AnyCallable = Union[AsyncCallable[T], SyncCallable[T]]


class GracefulDegradation:
    """
    Utility class for implementing graceful degradation of features.

    This class provides methods to attempt to execute operations with graceful
    fallback mechanisms when they fail, allowing the application to continue
    functioning with reduced capabilities instead of crashing.
    """

    def __init__(self, app):
        """
        Initialize the GracefulDegradation utility.

        Args:
            app: The application instance that implements a notify method.
        """
        self.app = app
        self.failed_features = set()
        self.degradation_history = {}  # Track reasons for degradation

    async def try_feature(
        self, feature_name: str, operation: AsyncCallable[T], *args, **kwargs
    ) -> Optional[T]:
        """
        Try a feature and gracefully degrade if it fails.

        Args:
            feature_name: The name of the feature being attempted.
            operation: The async operation to execute.
            *args: Positional arguments to pass to the operation.
            **kwargs: Keyword arguments to pass to the operation.

        Returns:
            The result of the operation if successful, None otherwise.
        """
        if feature_name in self.failed_features:
            logger.warning(
                f"Feature '{feature_name}' already marked as failed, skipping"
            )
            return None

        try:
            return await operation(*args, **kwargs)
        except Exception as e:
            self._handle_feature_failure(feature_name, e)
            return None

    def try_feature_sync(
        self, feature_name: str, operation: SyncCallable[T], *args, **kwargs
    ) -> Optional[T]:
        """
        Try a synchronous feature and gracefully degrade if it fails.

        Args:
            feature_name: The name of the feature being attempted.
            operation: The synchronous operation to execute.
            *args: Positional arguments to pass to the operation.
            **kwargs: Keyword arguments to pass to the operation.

        Returns:
            The result of the operation if successful, None otherwise.
        """
        if feature_name in self.failed_features:
            logger.warning(
                f"Feature '{feature_name}' already marked as failed, skipping"
            )
            return None

        try:
            return operation(*args, **kwargs)
        except Exception as e:
            self._handle_feature_failure(feature_name, e)
            return None

    def _handle_feature_failure(self, feature_name: str, exception: Exception) -> None:
        """
        Handle a feature failure by marking it as failed and notifying the user.

        Args:
            feature_name: The name of the failed feature.
            exception: The exception that caused the failure.
        """
        self.failed_features.add(feature_name)
        error_message = str(exception)
        error_trace = traceback.format_exc()

        logger.error(f"Feature '{feature_name}' failed with error: {error_message}")
        logger.debug(f"Traceback for feature '{feature_name}' failure:\n{error_trace}")

        self.degradation_history[feature_name] = {
            "error": error_message,
            "traceback": error_trace,
            "timestamp": asyncio.get_event_loop().time(),
        }

        self.app.notify(
            f"Feature '{feature_name}' disabled due to error: {error_message}",
            severity="warning",
        )

    def reset_feature(self, feature_name: str) -> bool:
        """
        Reset a previously failed feature, allowing it to be tried again.

        Args:
            feature_name: The name of the feature to reset.

        Returns:
            True if the feature was reset, False if it wasn't marked as failed.
        """
        if feature_name in self.failed_features:
            self.failed_features.remove(feature_name)
            if feature_name in self.degradation_history:
                del self.degradation_history[feature_name]

            self.app.notify(
                f"Feature '{feature_name}' has been reset and will be available on next use",
                severity="info",
            )
            return True
        return False

    def get_failed_features(self) -> Set[str]:
        """
        Get the set of features that have been marked as failed.

        Returns:
            A set of feature names that have been marked as failed.
        """
        return self.failed_features.copy()

    def get_degradation_info(
        self, feature_name: Optional[str] = None
    ) -> Dict[str, Any]:
        """
        Get information about degraded features.

        Args:
            feature_name: If provided, get information about a specific feature.
                          If None, get information about all degraded features.

        Returns:
            A dictionary containing degradation information.
        """
        if feature_name is not None:
            return self.degradation_history.get(feature_name, {})
        return self.degradation_history.copy()

    def feature_decorator(self, feature_name: str):
        """
        Decorator to apply graceful degradation to a method.

        Args:
            feature_name: The name of the feature being decorated.

        Returns:
            A decorator function that applies graceful degradation.
        """

        def decorator(func):
            @functools.wraps(func)
            async def async_wrapper(*args, **kwargs):
                return await self.try_feature(feature_name, func, *args, **kwargs)

            @functools.wraps(func)
            def sync_wrapper(*args, **kwargs):
                return self.try_feature_sync(feature_name, func, *args, **kwargs)

            if asyncio.iscoroutinefunction(func):
                return async_wrapper
            return sync_wrapper

        return decorator
