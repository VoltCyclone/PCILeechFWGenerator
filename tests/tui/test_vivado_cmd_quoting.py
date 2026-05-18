"""T11: spaced paths must survive the build command pipeline.

Old code: build args were string-joined then `.split()` again, shredding
any path with embedded spaces. Verify the new list[str] assembly + shlex
quoting keeps the spaced path as a single shell argument.
"""

import shlex

import pytest


def test_shlex_join_preserves_spaced_path():
    args = ["python3", "src/build.py", "--donor-info-file", "/tmp/foo bar/profile.json"]
    joined = shlex.join(args)
    # Round-trip through shlex.split must give the original list back.
    assert shlex.split(joined) == args


@pytest.mark.asyncio
async def test_run_shell_quotes_list_args(monkeypatch):
    """_run_shell, given a list[str], must pass a properly quoted string
    to create_subprocess_shell — not a naive ' '.join."""
    from pcileechfwgenerator.tui.core import build_orchestrator as bo
    from pcileechfwgenerator.tui.core.build_orchestrator import BuildOrchestrator

    captured = {}

    class _FakeProc:
        returncode = 0

        async def communicate(self):
            return (b"", b"")

    async def fake_shell(cmd_str, **_kwargs):
        captured["cmd_str"] = cmd_str
        return _FakeProc()

    monkeypatch.setattr(bo.asyncio, "create_subprocess_shell", fake_shell)

    orch = BuildOrchestrator()
    cmd = ["python3", "src/build.py", "--donor-info-file", "/tmp/foo bar/donor.json"]
    await orch._run_shell(cmd, monitor=False)

    assert shlex.split(captured["cmd_str"]) == cmd
