"""VFIO retry helper utilities.

Provides retry logic for transient VFIO ioctl/syscall errors (EINTR, EAGAIN, EBUSY)
without masking permanent failures. Keeps implementation minimal and side-effect
free beyond logging.
"""

from __future__ import annotations

import errno
import logging
import time
from typing import Callable, Iterable, Optional, Tuple, TypeVar

from src.string_utils import log_error_safe, log_warning_safe

T = TypeVar("T")

# Transient error codes that merit a retry
TRANSIENT_ERRNOS: Tuple[int, ...] = (
    errno.EINTR,
    errno.EAGAIN,
    errno.EBUSY,
)


def retry_vfio_call(
    func: Callable[[], T],
    *,
    retries: int = 5,
    initial_delay: float = 0.01,
    max_delay: float = 0.25,
    backoff: float = 2.0,
    transient_errnos: Iterable[int] = TRANSIENT_ERRNOS,
    label: str = "vfio-op",
    logger: Optional[logging.Logger] = None,
) -> T:
    """Execute func with retry on transient VFIO related OSError.

    Args:
        func: Zero-arg callable performing the VFIO action.
        retries: Maximum number of retries (total attempts = retries + 1).
        initial_delay: Initial backoff sleep in seconds.
        max_delay: Upper bound for backoff sleep.
        backoff: Exponential factor.
        transient_errnos: Iterable of errno codes to retry on.
        label: Short label for logging context.

    Raises:
        OSError: Re-raised if non-transient or retries exhausted.
    """
    if logger is None:
        logger = logging.getLogger(__name__)

    attempt = 0
    delay = initial_delay
    while True:
        try:
            return func()
        except OSError as e:  # Only catch OSError, never broad Exception
            eno = e.errno if e.errno is not None else -1
            transient = eno in transient_errnos
            if (not transient) or attempt >= retries:
                if not transient:
                    log_error_safe(
                        logger,
                        "{label} permanent failure errno={eno}: {msg}",
                        label=label,
                        eno=eno,
                        msg=str(e),
                        prefix="VFIO",
                    )
                raise

            # Transient; log and back off
            log_warning_safe(
                logger,
                "{label} transient errno={eno} attempt={attempt}/{max_attempts}; retrying in {sleep:.3f}s",
                label=label,
                eno=eno,
                attempt=attempt + 1,
                max_attempts=retries + 1,
                sleep=delay,
                prefix="VFIO",
            )
            time.sleep(delay)
            attempt += 1
            delay = min(delay * backoff, max_delay)


def retry_vfio_ioctl(
    func: Callable[[], T],
    **kwargs,
) -> T:
    """Alias wrapper for clarity when specifically retrying VFIO ioctls."""
    return retry_vfio_call(func, **kwargs)
