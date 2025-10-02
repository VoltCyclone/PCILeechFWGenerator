#!/usr/bin/env python3
from __future__ import annotations

"""
Enhanced error recovery mechanisms for PCILeech operations.

This module provides intelligent error recovery and retry logic for common
failure scenarios in firmware generation.
"""

import functools
import logging
import time
from dataclasses import dataclass
from enum import Enum
from typing import Callable, Dict, Optional

from src.string_utils import (log_error_safe, log_info_safe, log_warning_safe,
                              safe_format)

logger = logging.getLogger(__name__)


class ErrorCategory(Enum):
    """Categories of errors for different recovery strategies."""

    TRANSIENT = "transient"  # Temporary failures, retry immediately
    RECOVERABLE = "recoverable"  # Failures that can be recovered with action
    HARDWARE = "hardware"  # Hardware-related issues
    PERMISSION = "permission"  # Permission/privilege issues
    CONFIGURATION = "configuration"  # Configuration errors
    FATAL = "fatal"  # Unrecoverable errors


@dataclass
class RetryConfig:
    """Configuration for retry behavior."""

    max_attempts: int = 3
    base_delay: float = 1.0
    max_delay: float = 30.0
    exponential_backoff: bool = True
    jitter: bool = True


class PCILeechErrorRecovery:
    """Intelligent error recovery for PCILeech operations."""

    def __init__(self) -> None:
        self.error_patterns = self._build_error_patterns()
        self.recovery_actions = self._build_recovery_actions()

    def _build_error_patterns(self) -> Dict[str, ErrorCategory]:
        """Build patterns to categorize errors."""
        return {
            # VFIO errors
            "Device or resource busy": ErrorCategory.TRANSIENT,
            "No such device": ErrorCategory.HARDWARE,
            "Permission denied": ErrorCategory.PERMISSION,
            "Operation not permitted": ErrorCategory.PERMISSION,
            "Resource temporarily unavailable": ErrorCategory.TRANSIENT,
            # PCI errors
            "No such file or directory": ErrorCategory.HARDWARE,
            "Input/output error": ErrorCategory.HARDWARE,
            "Device not found": ErrorCategory.HARDWARE,
            # Memory errors
            "Cannot allocate memory": ErrorCategory.TRANSIENT,
            "Out of memory": ErrorCategory.TRANSIENT,
            # Configuration errors
            "Invalid BDF format": ErrorCategory.CONFIGURATION,
            "Board not supported": ErrorCategory.CONFIGURATION,
            "Missing required field": ErrorCategory.CONFIGURATION,
            # Network/timeout errors
            "Connection timed out": ErrorCategory.TRANSIENT,
            "Network is unreachable": ErrorCategory.TRANSIENT,
        }

    def _build_recovery_actions(self) -> Dict[ErrorCategory, list]:
        """Build recovery actions for each error category."""
        return {
            ErrorCategory.TRANSIENT: [
                "Wait and retry with exponential backoff",
                "Check system resources and retry",
                "Clear temporary state and retry",
            ],
            ErrorCategory.RECOVERABLE: [
                "Attempt automatic remediation",
                "Suggest manual intervention steps",
                "Retry with alternative approach",
            ],
            ErrorCategory.HARDWARE: [
                "Verify device is present and accessible",
                "Check hardware connections",
                "Validate device drivers",
            ],
            ErrorCategory.PERMISSION: [
                "Check for root privileges",
                "Verify VFIO permissions",
                "Suggest permission fixes",
            ],
            ErrorCategory.CONFIGURATION: [
                "Validate configuration parameters",
                "Suggest configuration corrections",
                "Provide example configurations",
            ],
            ErrorCategory.FATAL: [
                "Log detailed error information",
                "Suggest contacting support",
                "Provide troubleshooting steps",
            ],
        }

    def categorize_error(self, error: Exception) -> ErrorCategory:
        """Categorize an error for appropriate recovery strategy."""
        error_str = str(error).lower()
        for pattern, category in self.error_patterns.items():
            if pattern.lower() in error_str:
                return category
        return ErrorCategory.RECOVERABLE

    def should_retry(self, error: Exception, attempt: int, max_attempts: int) -> bool:
        """Determine if an operation should be retried."""
        if attempt >= max_attempts:
            return False
        category = self.categorize_error(error)
        return category in [ErrorCategory.TRANSIENT, ErrorCategory.RECOVERABLE]

    def get_recovery_suggestions(self, error: Exception) -> list:
        """Get recovery suggestions for an error."""
        category = self.categorize_error(error)
        return self.recovery_actions.get(category, [])


def retry_with_recovery(
    retry_config: Optional[RetryConfig] = None,
    error_recovery: Optional[PCILeechErrorRecovery] = None,
):
    """Decorator for retrying operations with intelligent error recovery."""
    if retry_config is None:
        retry_config = RetryConfig()
    if error_recovery is None:
        error_recovery = PCILeechErrorRecovery()

    def decorator(func: Callable):

        @functools.wraps(func)
        def wrapper(*args, **kwargs):
            last_exception: Optional[Exception] = None
            for attempt in range(1, retry_config.max_attempts + 1):
                try:
                    return func(*args, **kwargs)
                except Exception as e:  # noqa: BLE001 - broad by design here
                    last_exception = e
                    if not error_recovery.should_retry(
                        e, attempt, retry_config.max_attempts
                    ):
                        log_error_safe(
                            logger,
                            safe_format(
                                "Operation failed (non-retryable): {err}", err=e
                            ),
                            prefix="RECOV",
                        )
                        raise

                    if attempt < retry_config.max_attempts:
                        delay = _calculate_delay(
                            attempt,
                            retry_config.base_delay,
                            retry_config.max_delay,
                            retry_config.exponential_backoff,
                            retry_config.jitter,
                        )
                        log_warning_safe(
                            logger,
                            safe_format(
                                "Operation failed (attempt {attempt}/{max_attempts}): {err}. Retrying in {delay:.1f}s...",
                                attempt=attempt,
                                max_attempts=retry_config.max_attempts,
                                err=e,
                                delay=delay,
                            ),
                            prefix="RECOV",
                        )
                        time.sleep(delay)
                    else:
                        log_error_safe(
                            logger,
                            safe_format(
                                "Operation failed after {max_attempts} attempts: {err}",
                                max_attempts=retry_config.max_attempts,
                                err=e,
                            ),
                            prefix="RECOV",
                        )
                        suggestions = error_recovery.get_recovery_suggestions(e)
                        if suggestions:
                            log_info_safe(
                                logger, "Recovery suggestions:", prefix="RECOV"
                            )
                            for suggestion in suggestions:
                                log_info_safe(
                                    logger,
                                    safe_format("  - {s}", s=suggestion),
                                    prefix="RECOV",
                                )

            if last_exception is not None:
                raise last_exception
            raise RuntimeError("Operation failed after all retry attempts")

        return wrapper

    return decorator


def _calculate_delay(
    attempt: int,
    base_delay: float,
    max_delay: float,
    exponential_backoff: bool,
    jitter: bool,
) -> float:
    """Calculate delay for retry attempt."""
    delay = base_delay * (2 ** (attempt - 1)) if exponential_backoff else base_delay
    delay = min(delay, max_delay)
    if jitter:
        import random

        delay *= 0.5 + random.random() * 0.5  # 50-100% of calculated delay
    return delay
