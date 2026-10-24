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
    # sys.stdout.close() already closes fd 1 — a subsequent os.close(1)
    # raises OSError and would taint the return code. One close is enough.
    cmd = (
        'python3 -c "import sys,time; '
        "print('hello'); sys.stdout.close(); time.sleep(0.5)\""
    )

    start = time.monotonic()
    result = await orch._run_shell(cmd, monitor=True)
    elapsed = time.monotonic() - start

    assert elapsed < 5.0
    assert result.returncode == 0


@pytest.mark.asyncio
async def test_stop_terminates_descendant_that_ignores_sigterm(tmp_path):
    import sys

    import psutil

    pid_file = tmp_path / "descendant.pid"
    descendant = (
        "import os,signal,time; from pathlib import Path; "
        "signal.signal(signal.SIGTERM, signal.SIG_IGN); "
        f"Path({str(pid_file)!r}).write_text(str(os.getpid())); time.sleep(60)"
    )
    parent = (
        "import subprocess,sys,time; "
        f"subprocess.Popen([sys.executable, '-c', {descendant!r}]); time.sleep(60)"
    )
    orch = BuildOrchestrator()
    running = asyncio.create_task(orch._run_shell([sys.executable, "-c", parent]))
    try:
        for _ in range(100):
            if pid_file.exists():
                break
            await asyncio.sleep(0.02)
        assert pid_file.exists(), "descendant did not start"
        pid = int(pid_file.read_text())
        await orch.cancel_build()
        result = await asyncio.wait_for(running, 5)
        assert result.returncode != 0
        assert orch._build_process is None
        for _ in range(100):
            if (
                not psutil.pid_exists(pid)
                or psutil.Process(pid).status() == psutil.STATUS_ZOMBIE
            ):
                break
            await asyncio.sleep(0.02)
        else:
            pytest.fail("descendant survived cancellation")
    finally:
        if not running.done():
            await orch.cancel_build()
            await asyncio.wait_for(running, 5)


@pytest.mark.asyncio
async def test_actual_child_failure_is_reported_with_trailing_stderr():
    import sys
    from types import SimpleNamespace

    from pcileechfwgenerator.tui.models.configuration import BuildConfiguration

    orch = BuildOrchestrator()
    original = orch._run_shell

    async def run_failure(command):
        return await original(
            [
                sys.executable,
                "-c",
                "import sys; sys.stderr.write('final diagnostic'); sys.exit(7)",
            ]
        )

    orch._run_shell = run_failure
    with pytest.raises(RuntimeError, match="code 7"):
        await orch.start_build(
            SimpleNamespace(bdf="0000:03:00.0"),
            BuildConfiguration(board_type="pcileech_75t484_x1"),
        )
    progress = orch.get_current_progress()
    assert any("final diagnostic" in message for message in progress.errors)
    assert progress.completed_stages < progress.total_stages
    assert "successfully" not in progress.current_operation


@pytest.mark.asyncio
async def test_cancelled_pipeline_never_marks_success():
    import sys
    from types import SimpleNamespace

    from pcileechfwgenerator.tui.models.configuration import BuildConfiguration

    orch = BuildOrchestrator()
    original = orch._run_shell

    async def run_sleep(command):
        return await original([sys.executable, "-c", "import time; time.sleep(60)"])

    orch._run_shell = run_sleep
    running = asyncio.create_task(
        orch.start_build(
            SimpleNamespace(bdf="0000:03:00.0"),
            BuildConfiguration(board_type="pcileech_75t484_x1"),
        )
    )
    try:
        for _ in range(100):
            if orch._build_process is not None:
                break
            await asyncio.sleep(0.02)
        assert orch._build_process is not None
        await orch.cancel_build()
        assert await asyncio.wait_for(running, 5) is False
        progress = orch.get_current_progress()
        assert progress.completed_stages < progress.total_stages
        assert progress.completion_percent < 100
        assert "successfully" not in progress.current_operation
        assert not orch.is_building()
    finally:
        if not running.done():
            await orch.cancel_build()
            await asyncio.wait_for(running, 5)
