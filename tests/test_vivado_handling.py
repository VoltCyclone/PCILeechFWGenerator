#!/usr/bin/env python3
"""
Unit tests for the Vivado Stage 3 modules.

These tests give coverage to ``vivado_utils``, ``vivado_runner`` and
``vivado_error_reporter`` WITHOUT requiring a real Vivado installation.

Everything that would otherwise touch the filesystem, a subprocess or a
container is mocked.  Patch targets reference the *imported* names inside the
module under test (e.g. ``...vivado_utils.subprocess`` /
``...vivado_utils.shutil``) so the real implementations are never invoked.

Note: ``run_vivado_command`` and ``VivadoRunner.run`` use late/dynamic imports
of ``vivado_error_reporter`` / ``pcileech_build_integration``, so those module
attributes are patched directly rather than the importing module's namespace.
"""

import subprocess
from pathlib import Path

import pytest
from pcileechfwgenerator.vivado_handling import vivado_error_reporter as rep_mod
from pcileechfwgenerator.vivado_handling import vivado_runner as runner_mod
from pcileechfwgenerator.vivado_handling import vivado_utils
from pcileechfwgenerator.vivado_handling.vivado_error_reporter import (
    ColorFormatter,
    ErrorSeverity,
    VivadoErrorParser,
    VivadoErrorReporter,
    VivadoErrorType,
)
from pcileechfwgenerator.vivado_handling.vivado_runner import (
    VivadoIntegrationError,
    VivadoRunner,
    create_vivado_runner,
)
from pcileechfwgenerator.vivado_handling.vivado_utils import (
    _detect_version,
    _iter_candidate_dirs,
    _vivado_executable,
    find_vivado_installation,
    get_vivado_search_paths,
    get_vivado_version,
    run_vivado_command,
)

# ────────────────────────── Test helpers ──────────────────────────


def _completed(returncode=0, stdout=""):
    """Build a fake subprocess.CompletedProcess for subprocess.run mocks."""
    return subprocess.CompletedProcess(
        args=["vivado", "-version"],
        returncode=returncode,
        stdout=stdout,
        stderr="",
    )


class FakeStdout:
    """Minimal stand-in for process.stdout supporting readline()."""

    def __init__(self, lines):
        self._lines = list(lines)
        self._idx = 0

    def readline(self):
        if self._idx < len(self._lines):
            line = self._lines[self._idx]
            self._idx += 1
            return line
        return ""


class FakePopen:
    """Fake subprocess.Popen yielding lines then a terminal poll() value."""

    def __init__(self, lines, returncode=0):
        self.stdout = FakeStdout(lines)
        self._returncode = returncode
        self._poll_calls = 0

    def poll(self):
        # Return None while there is still output to read, then the final code.
        self._poll_calls += 1
        if self.stdout._idx < len(self.stdout._lines):
            return None
        return self._returncode

    def wait(self, timeout=None):  # pragma: no cover - convenience
        return self._returncode

    def kill(self):  # pragma: no cover - convenience
        pass


# ══════════════════════════════════════════════════════════════════
#  vivado_utils: find_vivado_installation
# ══════════════════════════════════════════════════════════════════


class TestFindVivadoInstallation:
    def test_manual_path_valid_returns_dict(self, tmp_path, monkeypatch):
        vivado_root = tmp_path / "Vivado"
        bin_dir = vivado_root / "bin"
        bin_dir.mkdir(parents=True)
        exe = bin_dir / "vivado"
        exe.write_text("#!/bin/sh\n")

        monkeypatch.setattr(vivado_utils, "get_vivado_version", lambda _exe: "2025.1")

        info = find_vivado_installation(manual_path=str(vivado_root))
        assert info is not None
        assert info["path"] == str(vivado_root)
        assert info["bin_path"] == str(vivado_root / "bin")
        assert info["executable"] == str(exe)
        assert info["version"] == "2025.1"

    def test_manual_path_missing_directory_falls_through_to_none(self, monkeypatch):
        # Manual path does not exist; auto-detection finds nothing.
        monkeypatch.setattr(vivado_utils, "_iter_candidate_dirs", lambda: iter(()))
        info = find_vivado_installation(manual_path="/definitely/not/here")
        assert info is None

    def test_manual_path_dir_without_executable_falls_through(
        self, tmp_path, monkeypatch
    ):
        # Directory exists but has no bin/vivado -> warning + fallthrough.
        root = tmp_path / "Vivado"
        root.mkdir()
        monkeypatch.setattr(vivado_utils, "_iter_candidate_dirs", lambda: iter(()))
        info = find_vivado_installation(manual_path=str(root))
        assert info is None

    def test_discovery_via_candidate_dirs(self, tmp_path, monkeypatch):
        vivado_root = tmp_path / "2024.2" / "Vivado"
        bin_dir = vivado_root / "bin"
        bin_dir.mkdir(parents=True)
        (bin_dir / "vivado").write_text("#!/bin/sh\n")

        monkeypatch.setattr(
            vivado_utils, "_iter_candidate_dirs", lambda: iter([vivado_root])
        )
        monkeypatch.setattr(vivado_utils, "get_vivado_version", lambda _exe: "2024.2")

        info = find_vivado_installation()
        assert info is not None
        assert info["path"] == str(vivado_root)
        assert info["version"] == "2024.2"

    def test_discovery_skips_dirs_without_executable(self, tmp_path, monkeypatch):
        empty_root = tmp_path / "empty" / "Vivado"
        empty_root.mkdir(parents=True)
        good_root = tmp_path / "2023.1" / "Vivado"
        (good_root / "bin").mkdir(parents=True)
        (good_root / "bin" / "vivado").write_text("#!/bin/sh\n")

        monkeypatch.setattr(
            vivado_utils,
            "_iter_candidate_dirs",
            lambda: iter([empty_root, good_root]),
        )
        monkeypatch.setattr(vivado_utils, "get_vivado_version", lambda _exe: "2023.1")

        info = find_vivado_installation()
        assert info is not None
        assert info["path"] == str(good_root)

    def test_none_found_returns_none(self, monkeypatch):
        monkeypatch.setattr(vivado_utils, "_iter_candidate_dirs", lambda: iter(()))
        assert find_vivado_installation() is None

    def test_version_unknown_is_returned_verbatim(self, tmp_path, monkeypatch):
        # Documents ACTUAL behavior: get_vivado_version returns the truthy
        # string "unknown", so the `or _detect_version(...)` fallback never
        # fires and the final version is "unknown" even when the dirname
        # encodes a version.  (See report: latent source quirk.)
        vivado_root = tmp_path / "2025.1" / "Vivado"
        (vivado_root / "bin").mkdir(parents=True)
        (vivado_root / "bin" / "vivado").write_text("#!/bin/sh\n")

        monkeypatch.setattr(
            vivado_utils, "_iter_candidate_dirs", lambda: iter([vivado_root])
        )
        monkeypatch.setattr(vivado_utils, "get_vivado_version", lambda _exe: "unknown")

        info = find_vivado_installation()
        assert info is not None
        assert info["version"] == "unknown"

    def test_version_falls_back_to_detect_when_query_returns_falsy(
        self, tmp_path, monkeypatch
    ):
        # When get_vivado_version returns a falsy value, the `or` fallback
        # to _detect_version(root) kicks in and pulls the version from the
        # directory name.
        vivado_root = tmp_path / "2022.2" / "Vivado"
        (vivado_root / "bin").mkdir(parents=True)
        (vivado_root / "bin" / "vivado").write_text("#!/bin/sh\n")

        monkeypatch.setattr(
            vivado_utils, "_iter_candidate_dirs", lambda: iter([vivado_root])
        )
        monkeypatch.setattr(vivado_utils, "get_vivado_version", lambda _exe: "")

        info = find_vivado_installation()
        assert info is not None
        assert info["version"] == "2022.2"


# ══════════════════════════════════════════════════════════════════
#  vivado_utils: get_vivado_version
# ══════════════════════════════════════════════════════════════════


class TestGetVivadoVersion:
    def test_parses_standard_version(self, monkeypatch):
        out = "vivado v2025.1 (64-bit)\nSW Build 1234"
        monkeypatch.setattr(
            vivado_utils.subprocess,
            "run",
            lambda *a, **k: _completed(0, out),
        )
        assert get_vivado_version("/x/vivado") == "2025.1"

    def test_parses_version_with_suffix(self, monkeypatch):
        out = "Vivado v2023.2.1 (64-bit)"
        monkeypatch.setattr(
            vivado_utils.subprocess,
            "run",
            lambda *a, **k: _completed(0, out),
        )
        assert get_vivado_version("/x/vivado") == "2023.2.1"

    def test_malformed_output_returns_unknown(self, monkeypatch):
        out = "this output has no version token at all"
        monkeypatch.setattr(
            vivado_utils.subprocess,
            "run",
            lambda *a, **k: _completed(0, out),
        )
        assert get_vivado_version("/x/vivado") == "unknown"

    def test_nonzero_returncode_returns_unknown(self, monkeypatch):
        monkeypatch.setattr(
            vivado_utils.subprocess,
            "run",
            lambda *a, **k: _completed(1, "vivado v2025.1 (64-bit)"),
        )
        assert get_vivado_version("/x/vivado") == "unknown"

    def test_filenotfound_returns_unknown(self, monkeypatch):
        def _raise(*a, **k):
            raise FileNotFoundError("no such file")

        monkeypatch.setattr(vivado_utils.subprocess, "run", _raise)
        assert get_vivado_version("/x/vivado") == "unknown"

    def test_timeout_returns_unknown(self, monkeypatch):
        def _raise(*a, **k):
            raise subprocess.TimeoutExpired(cmd="vivado", timeout=5)

        monkeypatch.setattr(vivado_utils.subprocess, "run", _raise)
        assert get_vivado_version("/x/vivado") == "unknown"

    def test_permission_error_returns_unknown(self, monkeypatch):
        def _raise(*a, **k):
            raise PermissionError("denied")

        monkeypatch.setattr(vivado_utils.subprocess, "run", _raise)
        assert get_vivado_version("/x/vivado") == "unknown"


# ══════════════════════════════════════════════════════════════════
#  vivado_utils: get_vivado_search_paths
# ══════════════════════════════════════════════════════════════════


class TestGetVivadoSearchPaths:
    def test_includes_system_path_and_env_entry(self, monkeypatch):
        monkeypatch.setenv("XILINX_VIVADO", "/custom/vivado")
        paths = get_vivado_search_paths()
        assert "System PATH" in paths
        assert any("XILINX_VIVADO=/custom/vivado" in p for p in paths)

    def test_env_not_set_shows_placeholder(self, monkeypatch):
        monkeypatch.delenv("XILINX_VIVADO", raising=False)
        paths = get_vivado_search_paths()
        assert any("XILINX_VIVADO=<not set>" in p for p in paths)

    def test_includes_default_bases(self):
        paths = get_vivado_search_paths()
        # DEFAULT_BASES entries are stringified into the list.
        for base in vivado_utils.DEFAULT_BASES:
            assert str(base) in paths


# ══════════════════════════════════════════════════════════════════
#  vivado_utils: internal helpers
# ══════════════════════════════════════════════════════════════════


class TestUtilsInternals:
    def test_detect_version_from_dirname(self):
        assert _detect_version(Path("/tools/Xilinx/2025.1/Vivado")) == "2025.1"

    def test_detect_version_unknown(self):
        assert _detect_version(Path("/opt/nope/Vivado")) == "unknown"

    def test_vivado_executable_present(self, tmp_path):
        root = tmp_path / "Vivado"
        (root / "bin").mkdir(parents=True)
        exe = root / "bin" / "vivado"
        exe.write_text("#!/bin/sh\n")
        assert _vivado_executable(root) == exe

    def test_vivado_executable_absent(self, tmp_path):
        root = tmp_path / "Vivado"
        root.mkdir()
        assert _vivado_executable(root) is None

    def test_iter_candidate_dirs_includes_path_hit(self, monkeypatch):
        monkeypatch.setattr(
            vivado_utils.shutil, "which", lambda name: "/opt/V/bin/vivado"
        )
        monkeypatch.delenv("XILINX_VIVADO", raising=False)
        # Avoid scanning real /tools/Xilinx by forcing Path.exists -> False.
        monkeypatch.setattr(Path, "exists", lambda self: False)
        dirs = list(_iter_candidate_dirs())
        # bin/ -> Vivado/ : parent.parent of the executable
        assert Path("/opt/V") in dirs

    def test_iter_candidate_dirs_includes_env(self, monkeypatch):
        monkeypatch.setattr(vivado_utils.shutil, "which", lambda name: None)
        monkeypatch.setenv("XILINX_VIVADO", "/env/vivado")
        monkeypatch.setattr(Path, "exists", lambda self: False)
        dirs = list(_iter_candidate_dirs())
        assert Path("/env/vivado") in dirs


# ══════════════════════════════════════════════════════════════════
#  vivado_utils: run_vivado_command
# ══════════════════════════════════════════════════════════════════


class TestRunVivadoCommand:
    def test_raises_when_no_executable(self, monkeypatch):
        monkeypatch.setattr(
            vivado_utils, "find_vivado_installation", lambda *a, **k: None
        )
        monkeypatch.setattr(vivado_utils.shutil, "which", lambda name: None)
        with pytest.raises(FileNotFoundError):
            run_vivado_command("-version", use_discovered=True)

    def test_builds_argv_for_string_args(self, monkeypatch):
        captured = {}

        def fake_run(cmd, **kwargs):
            captured["cmd"] = cmd
            return _completed(0, "")

        monkeypatch.setattr(
            vivado_utils, "find_vivado_installation", lambda *a, **k: None
        )
        monkeypatch.setattr(
            vivado_utils.shutil, "which", lambda name: "/opt/V/bin/vivado"
        )
        monkeypatch.setattr(vivado_utils.subprocess, "run", fake_run)

        run_vivado_command(
            "-mode batch", tcl_file="build.tcl", enable_error_reporting=False
        )
        assert captured["cmd"] == [
            "/opt/V/bin/vivado",
            "-mode",
            "batch",
            "-source",
            "build.tcl",
        ]

    def test_builds_argv_for_list_args(self, monkeypatch):
        captured = {}

        def fake_run(cmd, **kwargs):
            captured["cmd"] = cmd
            return _completed(0, "")

        monkeypatch.setattr(
            vivado_utils, "find_vivado_installation", lambda *a, **k: None
        )
        monkeypatch.setattr(
            vivado_utils.shutil, "which", lambda name: "/opt/V/bin/vivado"
        )
        monkeypatch.setattr(vivado_utils.subprocess, "run", fake_run)

        run_vivado_command(["-mode", "batch"], enable_error_reporting=False)
        assert captured["cmd"] == ["/opt/V/bin/vivado", "-mode", "batch"]

    def test_uses_discovered_executable(self, monkeypatch):
        captured = {}

        def fake_run(cmd, **kwargs):
            captured["cmd"] = cmd
            return _completed(0, "")

        monkeypatch.setattr(
            vivado_utils,
            "find_vivado_installation",
            lambda *a, **k: {"executable": "/disc/bin/vivado"},
        )
        monkeypatch.setattr(vivado_utils.subprocess, "run", fake_run)

        run_vivado_command(["-version"], enable_error_reporting=False)
        assert captured["cmd"][0] == "/disc/bin/vivado"

    def test_error_reporting_path_returns_completed_process(self, monkeypatch):
        monkeypatch.setattr(
            vivado_utils, "find_vivado_installation", lambda *a, **k: None
        )
        monkeypatch.setattr(
            vivado_utils.shutil, "which", lambda name: "/opt/V/bin/vivado"
        )
        monkeypatch.setattr(
            vivado_utils.subprocess,
            "Popen",
            lambda *a, **k: FakePopen([], returncode=0),
        )

        # Patch the reporter class on the *vivado_error_reporter module*
        # since run_vivado_command imports it dynamically.
        class FakeReporter:
            def __init__(self, *a, **k):
                pass

            def monitor_vivado_process(self, process):
                return 0, [], []

            def generate_error_report(self, *a, **k):
                return ""

            def print_summary(self, *a, **k):
                pass

        monkeypatch.setattr(rep_mod, "VivadoErrorReporter", FakeReporter)

        result = run_vivado_command(["-version"], enable_error_reporting=True)
        assert isinstance(result, subprocess.CompletedProcess)
        assert result.returncode == 0

    def test_error_reporting_nonzero_raises_calledprocesserror(self, monkeypatch):
        monkeypatch.setattr(
            vivado_utils, "find_vivado_installation", lambda *a, **k: None
        )
        monkeypatch.setattr(
            vivado_utils.shutil, "which", lambda name: "/opt/V/bin/vivado"
        )
        monkeypatch.setattr(
            vivado_utils.subprocess,
            "Popen",
            lambda *a, **k: FakePopen([], returncode=2),
        )

        fake_err = rep_mod.VivadoError(
            error_type=VivadoErrorType.SYNTAX_ERROR,
            severity=ErrorSeverity.ERROR,
            message="boom",
        )

        class FakeReporter:
            def __init__(self, *a, **k):
                pass

            def monitor_vivado_process(self, process):
                return 2, [fake_err], []

            def generate_error_report(self, *a, **k):
                return ""

            def print_summary(self, *a, **k):
                pass

        monkeypatch.setattr(rep_mod, "VivadoErrorReporter", FakeReporter)

        with pytest.raises(subprocess.CalledProcessError):
            run_vivado_command(["-version"], enable_error_reporting=True, cwd="/tmp")


# ══════════════════════════════════════════════════════════════════
#  vivado_error_reporter: VivadoErrorParser
# ══════════════════════════════════════════════════════════════════


class TestVivadoErrorParser:
    def test_syntax_error_classification(self):
        p = VivadoErrorParser()
        line = "ERROR: [Synth 8-2715] syntax error near foo [/tmp/mod.sv:42]"
        errors, warnings = p.parse_output(line)
        assert len(errors) == 1
        assert not warnings
        err = errors[0]
        assert err.error_type == VivadoErrorType.SYNTAX_ERROR
        assert err.severity == ErrorSeverity.ERROR

    def test_syntax_error_extracts_location(self):
        p = VivadoErrorParser()
        line = "ERROR: [Synth 8-2715] bad token [/tmp/mod.sv:42]"
        errors, _ = p.parse_output(line)
        err = errors[0]
        assert err.file_path == "/tmp/mod.sv"
        assert err.line_number == 42

    def test_timing_error_classification(self):
        p = VivadoErrorParser()
        line = "ERROR: [Timing 38-282] The design did not meet timing"
        errors, warnings = p.parse_output(line)
        assert len(errors) == 1
        assert errors[0].error_type == VivadoErrorType.TIMING_ERROR
        assert errors[0].severity == ErrorSeverity.ERROR

    def test_timing_critical_warning_is_critical_and_in_errors(self):
        p = VivadoErrorParser()
        line = "CRITICAL WARNING: [Timing 38-282] timing not met"
        errors, warnings = p.parse_output(line)
        # CRITICAL routes to the errors list, not warnings.
        assert len(errors) == 1
        assert not warnings
        assert errors[0].severity == ErrorSeverity.CRITICAL
        assert errors[0].error_type == VivadoErrorType.TIMING_ERROR

    def test_resource_error_classification(self):
        p = VivadoErrorParser()
        line = "ERROR: [Place 30-640] Placement failed for design"
        errors, _ = p.parse_output(line)
        assert errors[0].error_type == VivadoErrorType.RESOURCE_ERROR
        assert errors[0].severity == ErrorSeverity.ERROR

    def test_licensing_error_classification(self):
        p = VivadoErrorParser()
        line = "ERROR: [Common 17-349] No license found for PCIe license"
        errors, _ = p.parse_output(line)
        assert errors[0].error_type == VivadoErrorType.LICENSING_ERROR

    def test_generic_warning_is_unknown_type_and_in_warnings(self):
        p = VivadoErrorParser()
        line = "WARNING: [Synth 8-1234] something to note here"
        errors, warnings = p.parse_output(line)
        assert not errors
        assert len(warnings) == 1
        assert warnings[0].error_type == VivadoErrorType.UNKNOWN_ERROR
        assert warnings[0].severity == ErrorSeverity.WARNING

    def test_info_line_is_info_severity_in_warnings(self):
        p = VivadoErrorParser()
        line = "INFO: [Synth 8-5544] processing module top"
        errors, warnings = p.parse_output(line)
        assert not errors
        assert len(warnings) == 1
        assert warnings[0].severity == ErrorSeverity.INFO

    def test_generic_critical_warning_routes_to_errors(self):
        p = VivadoErrorParser()
        line = "CRITICAL WARNING: [Vivado 12-1234] something serious happened"
        errors, warnings = p.parse_output(line)
        assert len(errors) == 1
        assert not warnings
        assert errors[0].severity == ErrorSeverity.CRITICAL

    def test_unmatched_line_produces_nothing(self):
        p = VivadoErrorParser()
        errors, warnings = p.parse_output("just some random log noise")
        assert not errors
        assert not warnings

    def test_parse_log_file_missing_returns_empty(self, tmp_path):
        p = VivadoErrorParser()
        errors, warnings = p.parse_log_file(tmp_path / "does_not_exist.log")
        assert errors == []
        assert warnings == []

    def test_parse_log_file_reads_real_file(self, tmp_path):
        log = tmp_path / "vivado.log"
        log.write_text(
            "INFO: [Synth 8-1] starting\n"
            "ERROR: [Synth 8-2715] syntax error [/tmp/x.sv:7]\n"
            "WARNING: [Synth 8-99] minor warning\n"
        )
        p = VivadoErrorParser()
        errors, warnings = p.parse_log_file(log)
        assert len(errors) == 1
        assert errors[0].error_type == VivadoErrorType.SYNTAX_ERROR
        # INFO + WARNING both land in warnings list.
        assert len(warnings) == 2

    def test_parse_clears_state_between_calls(self):
        p = VivadoErrorParser()
        p.parse_output("ERROR: [Synth 8-2715] x [/a.sv:1]")
        errors, _ = p.parse_output("nothing here")
        assert errors == []


# ══════════════════════════════════════════════════════════════════
#  vivado_error_reporter: ColorFormatter
# ══════════════════════════════════════════════════════════════════


class TestColorFormatter:
    def test_colors_disabled_passthrough(self):
        f = ColorFormatter(use_colors=False)
        assert f.error("boom") == "boom"
        assert f.warning("w") == "w"
        assert f.info("i") == "i"
        assert f.success("s") == "s"

    def test_colors_enabled_wraps_ansi(self):
        f = ColorFormatter(use_colors=True)
        out = f.error("boom")
        assert out != "boom"
        assert out.startswith("\033[91m")
        assert out.endswith("\033[0m")
        assert "boom" in out

    def test_colorize_bold_and_underline(self):
        f = ColorFormatter(use_colors=True)
        assert f.bold("x").startswith("\033[1m")
        assert f.underline("x").startswith("\033[4m")

    def test_strip_ansi_codes_removes_codes(self):
        reporter = VivadoErrorReporter(use_colors=True)
        colored = "\033[91mred\033[0m and \033[92mgreen\033[0m"
        assert reporter._strip_ansi_codes(colored) == "red and green"


# ══════════════════════════════════════════════════════════════════
#  vivado_error_reporter: VivadoErrorReporter
# ══════════════════════════════════════════════════════════════════


class TestVivadoErrorReporter:
    def test_monitor_collects_errors_and_warnings(self):
        lines = [
            "INFO: [Synth 8-1] starting\n",
            "ERROR: [Synth 8-2715] bad [/tmp/x.sv:1]\n",
            "WARNING: [Synth 8-2] heads up\n",
        ]
        reporter = VivadoErrorReporter(use_colors=False)
        rc, errors, warnings = reporter.monitor_vivado_process(
            FakePopen(lines, returncode=0)
        )
        assert rc == 0
        assert len(errors) == 1
        # INFO + WARNING -> 2 warnings.
        assert len(warnings) == 2

    def test_monitor_returns_nonzero_code(self):
        reporter = VivadoErrorReporter(use_colors=False)
        rc, errors, warnings = reporter.monitor_vivado_process(
            FakePopen(["ERROR: [Synth 8-2715] x [/a.sv:1]\n"], returncode=3)
        )
        assert rc == 3
        assert len(errors) == 1

    def test_generate_error_report_no_issues(self):
        reporter = VivadoErrorReporter(use_colors=False)
        report = reporter.generate_error_report([], [], "Build")
        assert "No errors or warnings found" in report
        assert "ERROR REPORT" in report

    def test_generate_error_report_with_errors_summarizes(self):
        reporter = VivadoErrorReporter(use_colors=False)
        err = rep_mod.VivadoError(
            error_type=VivadoErrorType.SYNTAX_ERROR,
            severity=ErrorSeverity.ERROR,
            message="bad syntax",
            file_path="/tmp/x.sv",
            line_number=3,
        )
        report = reporter.generate_error_report([err], [], "Build")
        assert "SUMMARY" in report
        assert "ERRORS:" in report
        assert "bad syntax" in report

    def test_generate_error_report_writes_ansi_stripped_file(self, tmp_path):
        reporter = VivadoErrorReporter(use_colors=True)
        err = rep_mod.VivadoError(
            error_type=VivadoErrorType.SYNTAX_ERROR,
            severity=ErrorSeverity.ERROR,
            message="bad syntax",
        )
        out = tmp_path / "report.txt"
        reporter.generate_error_report([err], [], "Build", output_file=out)
        assert out.exists()
        content = out.read_text()
        # File must have ANSI codes stripped even though use_colors=True.
        assert "\033[" not in content
        assert "bad syntax" in content

    def test_print_summary_failure_path(self, capsys):
        reporter = VivadoErrorReporter(use_colors=False)
        err = rep_mod.VivadoError(
            error_type=VivadoErrorType.SYNTAX_ERROR,
            severity=ErrorSeverity.ERROR,
            message="bad",
        )
        reporter.print_summary([err], [])
        captured = capsys.readouterr()
        assert "1 error(s) found" in captured.out
        assert "Build FAILED" in captured.out

    def test_print_summary_success_path(self, capsys):
        reporter = VivadoErrorReporter(use_colors=False)
        reporter.print_summary([], [])
        captured = capsys.readouterr()
        assert "Build completed successfully" in captured.out


# ══════════════════════════════════════════════════════════════════
#  vivado_runner: VivadoRunner
# ══════════════════════════════════════════════════════════════════


class TestVivadoRunner:
    def _make(self, tmp_path, **kwargs):
        return VivadoRunner(
            board=kwargs.get("board", "pcileech_35t325_x1"),
            output_dir=kwargs.get("output_dir", tmp_path / "out"),
            vivado_path=kwargs.get("vivado_path", "/tools/Xilinx/2025.1/Vivado"),
        )

    def test_extract_version_matching(self, tmp_path):
        r = self._make(tmp_path)
        assert r._extract_version_from_path("/tools/Xilinx/2025.1/Vivado") == ("2025.1")

    def test_extract_version_non_matching(self, tmp_path):
        r = self._make(tmp_path)
        assert r._extract_version_from_path("/no/version/here") == "unknown"

    def test_derived_paths(self, tmp_path):
        r = self._make(tmp_path, vivado_path="/opt/Vivado")
        assert r.vivado_executable == "/opt/Vivado/bin/vivado"
        assert r.vivado_bin_dir == "/opt/Vivado/bin"

    def test_get_vivado_info_shape(self, tmp_path):
        r = self._make(tmp_path)
        info = r.get_vivado_info()
        assert set(info) == {
            "executable",
            "bin_dir",
            "version",
            "installation_path",
        }
        assert info["version"] == "2025.1"
        assert info["installation_path"] == "/tools/Xilinx/2025.1/Vivado"

    def test_is_running_in_container_dockerenv(self, tmp_path, monkeypatch):
        r = self._make(tmp_path)
        # /.dockerenv exists -> True (first indicator short-circuits).
        monkeypatch.setattr(Path, "exists", lambda self: str(self) == "/.dockerenv")
        assert r._is_running_in_container() is True

    def test_is_running_in_container_via_proc_environ(self, tmp_path, monkeypatch):
        r = self._make(tmp_path)
        # No indicator files exist.
        monkeypatch.setattr(Path, "exists", lambda self: False)

        import builtins

        real_open = builtins.open

        def fake_open(path, *a, **k):
            if str(path) == "/proc/1/environ":
                from io import BytesIO

                return BytesIO(b"container=podman\x00PATH=/usr/bin")
            return real_open(path, *a, **k)

        monkeypatch.setattr(builtins, "open", fake_open)
        assert r._is_running_in_container() is True

    def test_is_running_in_container_false(self, tmp_path, monkeypatch):
        r = self._make(tmp_path)
        monkeypatch.setattr(Path, "exists", lambda self: False)

        import builtins

        def fake_open(path, *a, **k):
            if str(path) == "/proc/1/environ":
                raise OSError("no proc")
            raise OSError("blocked")

        monkeypatch.setattr(builtins, "open", fake_open)
        assert r._is_running_in_container() is False

    def test_run_in_container_raises(self, tmp_path, monkeypatch):
        r = self._make(tmp_path)
        monkeypatch.setattr(r, "_prepare_ip_artifacts", lambda: None)
        monkeypatch.setattr(r, "_is_running_in_container", lambda: True)
        # Avoid actually writing a host script / chmod.
        monkeypatch.setattr(
            r,
            "_run_vivado_on_host",
            lambda: (_ for _ in ()).throw(VivadoIntegrationError("Container detected")),
        )
        with pytest.raises(VivadoIntegrationError):
            r.run()

    def test_run_success_path(self, tmp_path, monkeypatch):
        out_dir = tmp_path / "out"
        out_dir.mkdir()
        r = self._make(tmp_path, output_dir=out_dir)
        monkeypatch.setattr(r, "_prepare_ip_artifacts", lambda: None)
        monkeypatch.setattr(r, "_is_running_in_container", lambda: False)

        build_tcl = out_dir / "build.tcl"
        build_tcl.write_text("# tcl")

        # Patch the dynamically-imported integration + reporting functions on
        # their real modules.
        import pcileechfwgenerator.vivado_handling.pcileech_build_integration as pbi

        monkeypatch.setattr(pbi, "integrate_pcileech_build", lambda *a, **k: build_tcl)
        monkeypatch.setattr(
            rep_mod,
            "run_vivado_with_error_reporting",
            lambda *a, **k: (0, "ok"),
        )

        # Should not raise.
        assert r.run() is None

    def test_run_nonzero_raises(self, tmp_path, monkeypatch):
        out_dir = tmp_path / "out"
        out_dir.mkdir()
        r = self._make(tmp_path, output_dir=out_dir)
        monkeypatch.setattr(r, "_prepare_ip_artifacts", lambda: None)
        monkeypatch.setattr(r, "_is_running_in_container", lambda: False)

        build_tcl = out_dir / "build.tcl"
        build_tcl.write_text("# tcl")

        import pcileechfwgenerator.vivado_handling.pcileech_build_integration as pbi

        monkeypatch.setattr(pbi, "integrate_pcileech_build", lambda *a, **k: build_tcl)
        monkeypatch.setattr(
            rep_mod,
            "run_vivado_with_error_reporting",
            lambda *a, **k: (1, "/tmp/err.txt"),
        )

        with pytest.raises(VivadoIntegrationError):
            r.run()

    def test_run_falls_back_to_generated_script_when_integration_fails(
        self, tmp_path, monkeypatch
    ):
        out_dir = tmp_path / "out"
        out_dir.mkdir()
        r = self._make(tmp_path, output_dir=out_dir)
        monkeypatch.setattr(r, "_prepare_ip_artifacts", lambda: None)
        monkeypatch.setattr(r, "_is_running_in_container", lambda: False)

        # Fallback script must exist on disk.
        (out_dir / "vivado_build.tcl").write_text("# fallback")

        import pcileechfwgenerator.vivado_handling.pcileech_build_integration as pbi

        def _boom(*a, **k):
            raise RuntimeError("integration unavailable")

        monkeypatch.setattr(pbi, "integrate_pcileech_build", _boom)
        captured = {}

        def _runner(build_tcl, output_dir, exe, *a, **k):
            captured["tcl"] = build_tcl
            return (0, "ok")

        monkeypatch.setattr(rep_mod, "run_vivado_with_error_reporting", _runner)

        assert r.run() is None
        assert captured["tcl"] == out_dir / "vivado_build.tcl"

    def test_run_fallback_missing_script_raises(self, tmp_path, monkeypatch):
        out_dir = tmp_path / "out"
        out_dir.mkdir()
        r = self._make(tmp_path, output_dir=out_dir)
        monkeypatch.setattr(r, "_prepare_ip_artifacts", lambda: None)
        monkeypatch.setattr(r, "_is_running_in_container", lambda: False)

        import pcileechfwgenerator.vivado_handling.pcileech_build_integration as pbi

        def _boom(*a, **k):
            raise RuntimeError("integration unavailable")

        monkeypatch.setattr(pbi, "integrate_pcileech_build", _boom)
        # No vivado_build.tcl on disk -> should raise.
        with pytest.raises(VivadoIntegrationError):
            r.run()

    def test_prepare_ip_artifacts_swallows_errors(self, tmp_path, monkeypatch):
        r = self._make(tmp_path)

        def _boom(*a, **k):
            raise RuntimeError("repair failed")

        monkeypatch.setattr(runner_mod, "repair_ip_artifacts", _boom)
        # Best-effort: should not raise.
        r._prepare_ip_artifacts()


# ══════════════════════════════════════════════════════════════════
#  vivado_runner: create_vivado_runner factory
# ══════════════════════════════════════════════════════════════════


class TestCreateVivadoRunner:
    def test_factory_returns_runner(self, tmp_path):
        r = create_vivado_runner(
            board="pcileech_75t",
            output_dir=tmp_path / "out",
            vivado_path="/tools/Xilinx/2024.1/Vivado",
        )
        assert isinstance(r, VivadoRunner)
        assert r.board == "pcileech_75t"
        assert r.vivado_version == "2024.1"

    def test_factory_passes_device_config(self, tmp_path):
        cfg = {"vendor_id": "0x10ee"}
        r = create_vivado_runner(
            board="b",
            output_dir=tmp_path / "out",
            vivado_path="/opt/Vivado",
            device_config=cfg,
        )
        assert r.device_config == cfg
