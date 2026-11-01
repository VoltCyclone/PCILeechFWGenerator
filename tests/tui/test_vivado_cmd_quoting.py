"""Spaced paths and shell metacharacters are preserved as literal argv."""

import pytest


@pytest.mark.asyncio
async def test_run_shell_preserves_list_args_without_shell(monkeypatch):
    from pcileechfwgenerator.tui.core import build_orchestrator as bo

    captured = {}

    class FakeProcess:
        returncode = 0

        async def communicate(self):
            return b"", b""

    async def fake_exec(*argv, **kwargs):
        captured["argv"] = list(argv)
        return FakeProcess()

    monkeypatch.setattr(bo.asyncio, "create_subprocess_exec", fake_exec)
    command = ["python3", "-m", "example", "/tmp/foo bar/$(literal).json"]
    await bo.BuildOrchestrator()._run_shell(command, monitor=False)
    assert captured["argv"] == command
