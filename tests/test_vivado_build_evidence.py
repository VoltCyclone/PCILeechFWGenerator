"""Build provenance detects stale or modified staged inputs after relocation."""

import json
import shutil
import subprocess

import pytest
from pcileechfwgenerator.vivado_handling.build_evidence import (
    BUILD_QUALITY_TCL,
    BUS_SKEW_CHECK_TCL,
    verify_build_manifest,
    write_build_manifest,
)


def test_changed_memory_is_rejected_even_at_same_size(tmp_path):
    script = tmp_path / "build_all.tcl"
    script.write_text("# generated project\n")
    memory = tmp_path / "cfg.coe"
    memory.write_text("00000000\n")
    write_build_manifest(tmp_path, board="board", part="part", inputs=[script, memory])
    memory.write_text("00000001\n")
    with pytest.raises(ValueError, match="digest mismatch"):
        verify_build_manifest(script)


def test_relocation_keeps_manifest_valid_but_missing_input_does_not(tmp_path):
    root = tmp_path / "before"
    root.mkdir()
    script = root / "build_all.tcl"
    script.write_text("# generated project\n")
    memory = root / "cfg.coe"
    memory.write_text("00000000\n")
    write_build_manifest(root, board="board", part="part", inputs=[script, memory])
    relocated = tmp_path / "after"
    root.rename(relocated)
    verify_build_manifest(relocated / script.name)
    (relocated / memory.name).unlink()
    with pytest.raises(ValueError, match="missing or changed"):
        verify_build_manifest(relocated / script.name)


def test_manifest_cannot_reference_external_file(tmp_path):
    script = tmp_path / "build_all.tcl"
    script.write_text("# script\n")
    path = write_build_manifest(tmp_path, board="board", part="part", inputs=[script])
    data = json.loads(path.read_text())
    data["artifacts"][0]["path"] = "../outside"
    path.write_text(json.dumps(data))
    with pytest.raises(ValueError, match="escapes"):
        verify_build_manifest(script)


@pytest.mark.parametrize(
    "coverage_check", ["no_clock", "partial_input_delay", "partial_output_delay"]
)
@pytest.mark.parametrize(
    "timing_summary",
    [
        "All user specified timing constraints are met.",
        "Timing constraints are not met.",
        "",
    ],
)
@pytest.mark.parametrize(
    "slack,count,paths,drc,expected",
    [
        ("0.2", 0, "path", "", True),
        ("-0.1", 0, "path", "", False),
        ("0.2", 1, "path", "", False),
        ("0.2", 0, "", "", False),
        ("0.2", 0, "path", "violation", False),
    ],
)
def test_routed_quality_gate_rejects_unqualified_results(
    tmp_path, slack, count, paths, drc, expected, timing_summary, coverage_check
):
    tcl = shutil.which("tclsh")
    if not tcl:
        pytest.skip("Tcl interpreter required")
    harness = """
proc version {args} {return test-tool}
proc open_run {args} {}
proc report_timing_summary {args} {
    set f [open reports/timing_summary.rpt w]
    puts $f $::timing_summary
    close $f
}
proc report_clocks {args} {}
proc report_cdc {args} {set f [open reports/cdc.rpt w]; puts $f "Info safe"; close $f}
proc report_drc {args} {}
proc report_bus_skew {args} {return "Slack (MET) : 0.5ns"}
proc check_timing {args} {
    set result ""
    foreach check {no_clock unconstrained_internal_endpoints no_input_delay no_output_delay partial_input_delay partial_output_delay} {
        set n [expr {$check eq $::coverage_check ? $::count : 0}]
        append result "checking $check ($n)\\n"
    }
    return $result
}
proc get_timing_paths {args} {return $::paths}
proc get_drc_violations {args} {return $::drc}
proc get_property {name object} {
    if {$name eq "SLACK"} {return $::timing_slack}
    return "Critical Warning"
}
"""
    script = tmp_path / "gate.tcl"
    script.write_text(
        f"set timing_slack {{{slack}}}\nset count {count}\n"
        f"set paths {{{paths}}}\nset drc {{{drc}}}\n"
        f"set timing_summary {{{timing_summary}}}\n"
        f"set coverage_check {{{coverage_check}}}\n"
        + harness
        + "\nif {[catch {\n"
        + BUILD_QUALITY_TCL
        + "\n} why]} {puts stderr $why; exit 1}\n"
    )
    result = subprocess.run([tcl, str(script)], cwd=tmp_path, capture_output=True)
    expected = (
        expected and timing_summary == "All user specified timing constraints are met."
    )
    assert (result.returncode == 0) is expected, result.stderr.decode()


@pytest.mark.parametrize(
    "report,passes",
    [
        ("Slack (MET) : 0.5ns\nSlack (MET) : 0.0ns", True),
        ("Bus Skew Report\nNo bus skew constraints\n", True),
        ("Slack (MET) : 0.5ns\nSlack (VIOLATED) : -0.1ns", False),
        ("Slack (MET) : -0.1ns", False),
        ("", False),
        ("Unrecognized report format", False),
    ],
)
def test_bus_skew_gate(tmp_path, report, passes):
    tcl = shutil.which("tclsh")
    if not tcl:
        pytest.skip("Tcl interpreter required")
    (tmp_path / "reports").mkdir()
    (tmp_path / "skew.txt").write_text(report)
    script = tmp_path / "skew.tcl"
    script.write_text(
        "proc report_bus_skew {args} {set f [open skew.txt r]; "
        "set value [read $f]; close $f; return $value}\n" + BUS_SKEW_CHECK_TCL
    )
    result = subprocess.run([tcl, str(script)], cwd=tmp_path, capture_output=True)
    assert (result.returncode == 0) is passes, result.stderr.decode()


_FP_IN = (
    "There are {u} input ports with no input delay specified.\n"
    "There are {f} input ports with no input delay but user has a false path constraint. (MEDIUM)\n"
)
_FP_OUT = (
    "There are {u} ports with no output delay specified.\n"
    "There are {f} ports with no output delay but user has a false path constraint (MEDIUM)\n"
    "There are {c} ports with no output delay but with a timing clock defined on it or propagating through it\n"
)


@pytest.mark.parametrize(
    "inputs,outputs,passes",
    [
        # CaptainDMA 100T: PERST#, present, button / LEDs, FT601 reset,
        # all with board set_false_path; nothing unconstrained.
        ((3, 0, 3), (3, 0, 3, 0), True),
        ((0, 0, 0), (0, 0, 0, 0), True),
        ((3, 1, 2), (0, 0, 0, 0), False),  # one input truly unconstrained
        ((0, 0, 0), (3, 0, 2, 1), False),  # one output carries a clock
        ((3, 0, 2), (0, 0, 0, 0), False),  # counts disagree
        ((2, None, None), (0, 0, 0, 0), False),  # detail lines missing
    ],
)
def test_coverage_gate_accepts_only_false_path_io(tmp_path, inputs, outputs, passes):
    tcl = shutil.which("tclsh")
    if not tcl:
        pytest.skip("Tcl interpreter required")
    total_in, u_in, f_in = inputs
    total_out, u_out, f_out, c_out = outputs
    text = ""
    for check in (
        "no_clock",
        "unconstrained_internal_endpoints",
        "no_input_delay",
        "no_output_delay",
        "partial_input_delay",
        "partial_output_delay",
    ):
        n = {"no_input_delay": total_in, "no_output_delay": total_out}.get(check, 0)
        text += f"checking {check} ({n})\n"
    if u_in is not None:
        text += _FP_IN.format(u=u_in, f=f_in)
    text += _FP_OUT.format(u=u_out, f=f_out, c=c_out)
    start = BUILD_QUALITY_TCL.index("set coverage")
    end = BUILD_QUALITY_TCL.index("foreach violation")
    script = tmp_path / "coverage.tcl"
    script.write_text(
        "proc check_timing {args} {return $::report}\n"
        + "set report {"
        + text
        + "}\n"
        + "if {[catch {\n"
        + BUILD_QUALITY_TCL[start:end]
        + "\n} why]} {puts stderr $why; exit 1}\n"
    )
    result = subprocess.run([tcl, str(script)], capture_output=True)
    assert (result.returncode == 0) is passes, result.stderr.decode()


@pytest.mark.parametrize(
    "line,blocked",
    [
        ("Critical  CDC-1  3  1-bit unknown CDC circuitry", True),
        ("CDC-1  Critical  3  1-bit unknown CDC circuitry", True),
        ("  CDC-11 Critical  13  Fan-out from launch flop", True),
        ("Warning   CDC-13  2  1-bit CDC path on a non-FD primitive", False),
        ("CDC-15  Info  206  Clock enable controlled CDC structure", False),
    ],
)
def test_blanket_cdc_gate_matches_either_report_layout(tmp_path, line, blocked):
    """the rule ID can come before the severity."""
    tcl = shutil.which("tclsh")
    if not tcl:
        pytest.skip("Tcl interpreter required")
    from pcileechfwgenerator.vivado_handling.build_evidence import build_quality_tcl

    text = build_quality_tcl()
    start = text.index("set cdc_file [open reports/cdc.rpt r]")
    end = text.index("}", text.index("Unqualified critical CDC findings")) + 1
    (tmp_path / "reports").mkdir()
    (tmp_path / "reports" / "cdc.rpt").write_text(line + "\n")
    script = tmp_path / "cdc.tcl"
    script.write_text(
        "cd {"
        + str(tmp_path)
        + "}\n"
        + "if {[catch {\n"
        + text[start:end]
        + "\n} why]} {puts $why; exit 1}\n"
    )
    result = subprocess.run([tcl, str(script)], capture_output=True)
    assert (result.returncode != 0) is blocked, result.stdout.decode()
