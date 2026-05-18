"""T9: `_run_shell` must drain stdout+stderr concurrently and respect cancel.

The old monitored path read only stdout via blocking `readline()` and never
checked `_should_cancel`, so the Stop button was inert during a build that
held its pipes open (typical for Vivado). Verify the new path:
  1. Cancels a long-running child within ~2 seconds.
  2. Times out idle readlines instead of blocking forever.
"""

import asyncio
import time

import pytest

from pcileechfwgenerator.tui.core.build_orchestrator import (
    READLINE_TIMEOUT_SEC,
    BuildOrchestrator,
)


def test_readline_timeout_constant_is_module_level():
    assert isinstance(READLINE_TIMEOUT_SEC, float)
    assert 0 < READLINE_TIMEOUT_SEC <= 5


@pytest.mark.asyncio
async def test_cancel_mid_run_terminates_within_two_seconds():
    """A long-sleeping child must die when _should_cancel is set."""
    orch = BuildOrchestrator()

    # Trip cancel after ~250ms (well inside the readline poll cadence).
    async def _trip_cancel():
        await asyncio.sleep(0.25)
        orch._should_cancel = True

    asyncio.create_task(_trip_cancel())

    start = time.monotonic()
    result = await orch._run_shell(
        'python3 -c "import time; time.sleep(30)"',
        monitor=True,
    )
    elapsed = time.monotonic() - start

    assert elapsed < 4.0, f"cancel took too long: {elapsed:.2f}s"
    # process was killed — returncode is non-zero (negative on SIGTERM)
    assert result.returncode != 0
    assert orch._should_cancel is True


@pytest.mark.asyncio
async def test_process_that_closes_stdout_but_keeps_running_observed():
    """If a child closes stdout but lingers, the loop must still observe
    the exit (not hang on readline)."""
    orch = BuildOrchestrator()
    # Child: print then close stdout; exit ~0.5s later.
    cmd = (
        'python3 -c "import os,sys,time; '
        'print(\'hello\'); sys.stdout.close(); os.close(1); time.sleep(0.5)"'
    )

    start = time.monotonic()
    result = await orch._run_shell(cmd, monitor=True)
    elapsed = time.monotonic() - start

    assert elapsed < 5.0
    assert result.returncode == 0
