import errno
import time
import types
from typing import List

import pytest

from src.utils.vfio_retry import retry_vfio_call


class SleepRecorder:
    def __init__(self):
        self.sleeps: List[float] = []

    def __call__(self, duration: float):  # mimic time.sleep signature
        self.sleeps.append(duration)


@pytest.mark.parametrize("transient_errno", [errno.EINTR, errno.EAGAIN, errno.EBUSY])
def test_retry_eventual_success(monkeypatch, transient_errno):
    """Should retry transient errors and eventually succeed."""
    attempts = {"count": 0}

    def op():
        if attempts["count"] < 2:
            attempts["count"] += 1
            raise OSError(transient_errno, "transient")
        return "ok"

    sleeper = SleepRecorder()
    monkeypatch.setattr(time, "sleep", sleeper)

    result = retry_vfio_call(op, retries=5, initial_delay=0.001, max_delay=0.01)
    assert result == "ok"
    # Two failures then success => 2 sleep calls
    assert len(sleeps := sleeper.sleeps) == 2
    # Backoff progression (approx; exact equality fine due to our fixed values)
    assert sleeps[0] == pytest.approx(0.001, rel=1e-3)
    assert 0.001 <= sleeps[1] <= 0.01


def test_retry_exhaustion_raises(monkeypatch):
    """Should raise after exhausting retries for persistent transient error."""

    def op():
        raise OSError(errno.EAGAIN, "always")

    sleeper = SleepRecorder()
    monkeypatch.setattr(time, "sleep", sleeper)

    with pytest.raises(OSError):
        retry_vfio_call(op, retries=2, initial_delay=0.0005, max_delay=0.001)

    # Should have slept exactly retries times
    assert len(sleeper.sleeps) == 2


def test_non_transient_error_no_retry(monkeypatch):
    """Non-transient errno should not be retried."""
    calls = {"count": 0}

    def op():
        calls["count"] += 1
        raise OSError(errno.EINVAL, "permanent")

    sleeper = SleepRecorder()
    monkeypatch.setattr(time, "sleep", sleeper)

    with pytest.raises(OSError):
        retry_vfio_call(op, retries=5)

    assert calls["count"] == 1
    assert sleeper.sleeps == []
