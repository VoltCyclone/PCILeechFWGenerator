"""Unit tests for kernel_utils.run_command.

Exercises the post-hardening contract: argv-only inputs, PathLike support,
shell-free execution, and informative error wrapping on failure. The real
subprocess is mocked throughout so these tests run on any platform.
"""

import os
import subprocess
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

from pcileechfwgenerator.scripts import kernel_utils


class TestRunCommandContract:
    def test_string_input_rejected(self) -> None:
        """Passing a shell string must raise TypeError, not silently run."""
        with pytest.raises(TypeError, match="argv sequence"):
            kernel_utils.run_command("ls -la /tmp")

    def test_returns_stdout_on_success(self) -> None:
        with patch.object(kernel_utils.subprocess, "run") as mock_run:
            mock_run.return_value = MagicMock(stdout="hello\n", returncode=0)
            out = kernel_utils.run_command(["echo", "hello"])
        assert out == "hello\n"

    def test_does_not_invoke_shell(self) -> None:
        """run_command must never pass shell=True to subprocess.run."""
        with patch.object(kernel_utils.subprocess, "run") as mock_run:
            mock_run.return_value = MagicMock(stdout="", returncode=0)
            kernel_utils.run_command(["echo", "hi"])
        _, kwargs = mock_run.call_args
        assert kwargs.get("shell", False) is False
        # First positional arg must be the argv list, not a string
        args = mock_run.call_args.args
        assert isinstance(args[0], list)

    def test_pathlike_elements_accepted(self, tmp_path: Path) -> None:
        """Path objects in argv are os.fspath-normalized for both the call
        and the log/error formatting (the bug Copilot flagged)."""
        binary = tmp_path / "fakebin"
        with patch.object(kernel_utils.subprocess, "run") as mock_run:
            mock_run.return_value = MagicMock(stdout="", returncode=0)
            kernel_utils.run_command([binary, "--flag"])
        args, _ = mock_run.call_args
        # All argv elements should now be strings (or fspath of them)
        assert args[0] == [os.fspath(binary), "--flag"]

    def test_called_process_error_wraps_to_runtime_error(self) -> None:
        cpe = subprocess.CalledProcessError(
            returncode=2, cmd=["false"], stderr="boom"
        )
        with patch.object(kernel_utils.subprocess, "run", side_effect=cpe):
            with pytest.raises(RuntimeError) as excinfo:
                kernel_utils.run_command(["false"])
        # The chained cause should be the original CalledProcessError
        assert excinfo.value.__cause__ is cpe
        # And the wrapped message should carry diagnostic detail
        msg = str(excinfo.value)
        assert "Exit code: 2" in msg
        assert "boom" in msg

    def test_error_message_uses_shlex_quoting(self) -> None:
        """Args with spaces must be quoted in the error message so the log
        is unambiguous (the second half of Copilot's concern)."""
        cpe = subprocess.CalledProcessError(
            returncode=1, cmd=["modprobe"], stderr=""
        )
        with patch.object(kernel_utils.subprocess, "run", side_effect=cpe):
            with pytest.raises(RuntimeError) as excinfo:
                kernel_utils.run_command(["modprobe", "an arg with spaces"])
        # shlex.join wraps that arg in single quotes
        assert "'an arg with spaces'" in str(excinfo.value)
