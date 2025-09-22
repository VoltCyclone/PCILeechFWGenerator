#!/usr/bin/env python3
import os
import stat
from pathlib import Path

import pytest

from src.shell import Shell


def test_run_success(monkeypatch):
    calls = {}

    def fake_check_output(cmd, shell, text, timeout, stderr, cwd):
        calls["args"] = {
            "cmd": cmd,
            "shell": shell,
            "text": text,
            "timeout": timeout,
            "stderr": bool(stderr),
            "cwd": cwd,
        }
        return "ok\n"

    import subprocess

    monkeypatch.setattr(subprocess, "check_output", fake_check_output)
    s = Shell()
    out = s.run("echo ok", timeout=3, cwd="/")
    assert out == "ok"
    assert calls["args"]["shell"] is True
    assert calls["args"]["text"] is True
    assert calls["args"]["timeout"] == 3
    assert calls["args"]["cwd"] == "/"


def test_run_timeout_raises_runtimeerror(monkeypatch):
    import subprocess

    def fake_check_output(cmd, shell, text, timeout, stderr, cwd):
        raise subprocess.TimeoutExpired(cmd=cmd, timeout=timeout)

    monkeypatch.setattr(subprocess, "check_output", fake_check_output)
    s = Shell()
    with pytest.raises(RuntimeError) as ei:
        s.run("sleep 10", timeout=1)
    assert "timed out" in str(ei.value).lower()


def test_run_calledprocesserror_raises_runtimeerror(monkeypatch):
    import subprocess

    def fake_check_output(cmd, shell, text, timeout, stderr, cwd):
        raise subprocess.CalledProcessError(returncode=7, cmd=cmd, output="boom")

    monkeypatch.setattr(subprocess, "check_output", fake_check_output)
    s = Shell()
    with pytest.raises(RuntimeError) as ei:
        s.run("false")
    msg = str(ei.value)
    assert "exit code 7" in msg
    assert "Command failed" in msg


def test_run_dry_run_skips_execution(monkeypatch):
    import subprocess

    def fake_check_output(*a, **kw):
        raise AssertionError("should not be called in dry-run")

    monkeypatch.setattr(subprocess, "check_output", fake_check_output)
    s = Shell(dry_run=True)
    out = s.run("echo hi")
    assert out == ""


def test_run_check_true_and_false(monkeypatch):
    s = Shell(dry_run=True)
    # Success path
    monkeypatch.setattr(s, "run", lambda *a, **k: "")
    assert s.run_check("true") is True

    # Failure path
    def boom(*a, **k):
        raise RuntimeError("fail")

    monkeypatch.setattr(s, "run", boom)
    assert s.run_check("false") is False


def test_safety_blocking_pattern(monkeypatch):
    # In safe_mode, any command containing the pattern "none" should raise
    s = Shell(dry_run=True, safe_mode=True)
    with pytest.raises(RuntimeError):
        s.run("echo none")


def test_safety_warning_sensitive_redirection(monkeypatch):
    # Should only warn, not raise, when redirecting into a sensitive path
    s = Shell(dry_run=True, safe_mode=True)
    s.run("cat /etc/hosts > /etc/hosts.bak")


def test_write_file_dry_run(tmp_path: Path):
    s = Shell(dry_run=True)
    target = tmp_path / "a" / "b.txt"
    s.write_file(str(target), "data", create_dirs=True)
    assert not target.exists()


def test_write_file_creates_and_sets_permissions(tmp_path: Path):
    s = Shell()
    target = tmp_path / "dir" / "file.txt"
    s.write_file(str(target), "hello", create_dirs=True, permissions=0o600)
    assert target.exists()
    assert target.read_text() == "hello"
    mode = stat.S_IMODE(os.stat(target).st_mode)
    assert mode == 0o600


def test_write_file_ioerror_raises_runtimeerror(monkeypatch, tmp_path: Path):
    s = Shell()
    target = tmp_path / "bad" / "file.txt"

    # Force open() to raise to simulate IO error
    import builtins

    def fake_open(*a, **k):
        raise OSError("disk full")

    monkeypatch.setattr(builtins, "open", fake_open)
    with pytest.raises(RuntimeError) as ei:
        s.write_file(str(target), "x", create_dirs=True)
    assert "Failed to write file" in str(ei.value)
