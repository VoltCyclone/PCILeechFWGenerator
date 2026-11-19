"""Hash the exact inputs to a generated Vivado project before execution."""

import hashlib
import json
from pathlib import Path
from typing import Iterable

from pydantic import BaseModel, ConfigDict

from ..device_clone.requester_tracking import (
    QUALIFIED_STARTUP_MODEL_SHA256,
    QUALIFIED_STARTUP_SOURCE_SHA256,
    RequesterStartupQualification,
)

_STARTUP_QUALIFICATION = RequesterStartupQualification(
    qualification_id="captaindma100t-gen1-abcd-drp64-v1",
    scope="controller_model_only",
    board="pcileech_100t484_x1",
    source_sha256=dict(QUALIFIED_STARTUP_SOURCE_SHA256),
    model_evidence_sha256=dict(QUALIFIED_STARTUP_MODEL_SHA256),
).model_dump(mode="json")

# Run before preflight, including on repeated executions of an exported script.
# Keep earlier results as history; never leave them looking like this run's output.
PORTABLE_BEGIN_RUN = """
import pathlib, sys, uuid
root = pathlib.Path(sys.argv[1]).resolve().parent
published = root / "output"
if published.exists() or published.is_symlink():
    published.rename(root / ("previous-output-" + uuid.uuid4().hex))
resolved_plan = root / "requester_plan_resolved.json"
if resolved_plan.exists() or resolved_plan.is_symlink():
    resolved_plan.rename(root / ("previous-requester-plan-" + uuid.uuid4().hex + ".json"))
"""

# The exported host script may run without the package installed. Keep its
# preflight limited to the Python standard library, with the same hash checks.
PORTABLE_INPUT_CHECK = """
import hashlib, json, pathlib, sys
manifest = pathlib.Path(sys.argv[1]).resolve()
root = manifest.parent
records = json.loads(manifest.read_text())["artifacts"]
if not records:
    raise SystemExit("Empty build input manifest")
for item in records:
    path = (root / item["path"]).resolve()
    if not path.is_relative_to(root):
        raise SystemExit("Build input escapes project directory")
    data = path.read_bytes()
    if len(data) != item["size"] or hashlib.sha256(data).hexdigest() != item["sha256"]:
        raise SystemExit("Build input digest mismatch: " + item["path"])
"""

PORTABLE_PUBLISH_RESULT = (
    PORTABLE_INPUT_CHECK
    + f"\nknown_startup_qualification = {_STARTUP_QUALIFICATION!r}\n"
    + """
images = list((root / "output").glob("*.bit"))
if len(images) != 1 or images[0].stat().st_size == 0:
    raise SystemExit("Expected one nonempty qualified bitstream")
record = {
    "bitstream": images[0].name,
    "sha256": hashlib.sha256(images[0].read_bytes()).hexdigest(),
    "inputs_sha256": hashlib.sha256(manifest.read_bytes()).hexdigest(),
    "tool_version": (root / "reports" / "tool_version.txt").read_text().strip(),
    "qualification": "routed timing, clock coverage, DRC and CDC gates passed",
}
if any(item["path"] == "requester_policy.json" for item in records):
    policy = json.loads((root / "requester_policy.json").read_text())
    resolved_file = root / "requester_plan_resolved.json"
    resolved = json.loads(resolved_file.read_text())
    if any(resolved.get(key) != value or type(resolved.get(key)) is not type(value)
           for key, value in policy.items()):
        raise SystemExit("Resolved requester plan disagrees with build policy")
    if set(resolved) != set(policy) | {
            "clk_pcie_hz", "trim_tlp_digest", "ecrc_check_enabled",
            "flow_control_interface_enabled",
            "ip_initial_cpl_timeout_disable_supported", "startup_verified"}:
        raise SystemExit("Incomplete resolved requester plan")
    if (type(resolved["clk_pcie_hz"]) is not int
            or resolved["clk_pcie_hz"] not in (62500000, 125000000, 250000000)
            or type(resolved["trim_tlp_digest"]) is not bool
            or resolved["ecrc_check_enabled"] is not False
            or resolved["flow_control_interface_enabled"] is not True
            or type(resolved["ip_initial_cpl_timeout_disable_supported"]) is not bool
            or resolved["startup_verified"] is not False):
        raise SystemExit("Unsupported resolved requester IP properties")
    qualification = policy.get("startup_qualification")
    initial = resolved["ip_initial_cpl_timeout_disable_supported"]
    target = resolved["cpl_timeout_disable_supported"]
    if type(target) is not bool:
        raise SystemExit("Invalid requester final capability state")
    if qualification is not None:
        if qualification != known_startup_qualification:
            raise SystemExit("Unrecognized requester startup qualification")
        contract = json.loads(manifest.read_text())
        if (policy.get("cpl_timeout_ranges") != "ABCD" or target is not True
                or policy.get("extended_tag_supported") is not False
                or type(policy.get("drp_ready_timeout_cycles")) is not int
                or policy["drp_ready_timeout_cycles"] != 64
                or resolved["clk_pcie_hz"] != 62500000
                or resolved["trim_tlp_digest"] is not False
                or contract.get("board") != "pcileech_100t484_x1"
                or contract.get("part") != "xc7a100tfgg484-1"
                or record["tool_version"] != "2026.1"):
            raise SystemExit("Requester plan is outside startup qualification scope")
    if initial != target and not (
            initial is False and target is True
            and qualification is not None):
        raise SystemExit("Unqualified requester capability state transition")
    record["requester"] = {
        "plan": resolved,
        "resolved_sha256": hashlib.sha256(resolved_file.read_bytes()).hexdigest(),
        "drp_startup_verified": False,
        # Model provenance qualifies the controller mechanism, not this image's
        # runtime startup or complete board integration. Keep those distinct.
        "model_evidence": qualification or {},
    }
temporary = root / "output" / "build_result.json.tmp"
temporary.write_text(json.dumps(record, indent=2) + "\\n")
temporary.replace(root / "output" / "build_result.json")
"""
)

BUS_SKEW_CHECK_TCL = r"""
set bus_skew_report [report_bus_skew -return_string]
set skew_file [open reports/bus_skew.rpt w]
puts $skew_file $bus_skew_report
close $skew_file
if {![regexp -line {^No bus skew constraints\s*$} $bus_skew_report]} {
    set skew_checks [regexp -all -inline -line {^Slack \(([^)]+)\)\s*:\s*([-+0-9.eE]+)ns} $bus_skew_report]
    if {[llength $skew_checks] == 0} {error "Cannot verify bus-skew results"}
    foreach {line status slack} $skew_checks {
        if {$status ne "MET" || ![string is double -strict $slack] || $slack < 0} {
            error "Unqualified bus skew: $line"
        }
    }
}
"""

# UG835: get_timing_paths and check_timing. Run only on the implemented design;
# standalone core timing does not establish board timing or clock coverage.
_BUILD_QUALITY_TEMPLATE = (
    r"""
open_run impl_1
file mkdir reports
set version_file [open reports/tool_version.txt w]
puts $version_file [version -short]
close $version_file
report_timing_summary -report_unconstrained -file reports/timing_summary.rpt
report_clocks -file reports/clocks.rpt
report_cdc -file reports/cdc.rpt
report_drc -file reports/drc.rpt
check_timing -verbose -file reports/check_timing.rpt
"""
    + BUS_SKEW_CHECK_TCL
    + r"""
set timing_file [open reports/timing_summary.rpt r]
set timing_report [read $timing_file]
close $timing_file
# The timing summary covers pulse-width/period/skew checks as well as setup/hold.
# Fail closed if a tool version changes the success wording.
if {![regexp -line {^All user specified timing constraints are met\.$} $timing_report]} {
    error "Routed timing summary did not pass all checks, including pulse width"
}
@@CDC_GATE@@
foreach delay {max min} {
    set paths [get_timing_paths -quiet -delay_type $delay -max_paths 1 -no_report_unconstrained]
    if {[llength $paths] == 0} {error "No $delay timing paths; cannot qualify this build"}
    set slack [get_property SLACK [lindex $paths 0]]
    if {![string is double -strict $slack] || $slack < 0} {
        error "Routed $delay timing failed: slack=$slack"
    }
}
set coverage [check_timing -verbose -override_defaults {no_clock unconstrained_internal_endpoints no_input_delay no_output_delay partial_input_delay partial_output_delay} -return_string]
# Asynchronous board I/O (PERST#, buttons, LEDs) is excluded from timing with an
# explicit set_false_path in the board XDC. check_timing still counts those
# ports under no_input_delay/no_output_delay. Accept a nonzero count only when
# every counted port is in that false-path category and none is unconstrained.
set false_path_only {
    no_input_delay {
        {There are ([0-9]+) input ports with no input delay specified} 0
        {There are ([0-9]+) input ports with no input delay but user has a false path constraint} fp
    }
    no_output_delay {
        {There are ([0-9]+) ports with no output delay specified} 0
        {There are ([0-9]+) ports with no output delay but with a timing clock defined on it or propagating through it} 0
        {There are ([0-9]+) ports with no output delay but user has a false path constraint} fp
    }
}
foreach check {no_clock unconstrained_internal_endpoints no_input_delay no_output_delay partial_input_delay partial_output_delay} {
    set pattern [format {checking %s \(([0-9]+)\)} $check]
    if {![regexp -nocase $pattern $coverage matched count]} {
        error "Cannot verify clock coverage for $check; inspect reports/check_timing.rpt"
    }
    if {$count == 0} {continue}
    if {![dict exists $false_path_only $check]} {
        error "Unqualified clock coverage: $check=$count"
    }
    set false_paths -1
    foreach {line want} [dict get $false_path_only $check] {
        if {![regexp $line $coverage matched n]} {
            error "Cannot verify clock coverage for $check; inspect reports/check_timing.rpt"
        }
        if {$want eq "fp"} {set false_paths $n} elseif {$n != $want} {
            error "Unqualified clock coverage: $check=$count"
        }
    }
    if {$false_paths != $count} {error "Unqualified clock coverage: $check=$count"}
}
foreach violation [get_drc_violations -quiet] {
    set severity [get_property SEVERITY $violation]
    if {$severity eq "Error" || $severity eq "Critical Warning"} {
        error "Unresolved DRC violation: $violation ($severity)"
    }
}
"""
)


_BLANKET_CDC_GATE = """set cdc_file [open reports/cdc.rpt r]
set cdc_report [read $cdc_file]
close $cdc_file
if {[regexp -nocase -line {^\\s*(CDC-[0-9]+\\s+)?Critical\\s} $cdc_report]} {
    error "Unqualified critical CDC findings; review reports/cdc.rpt before bitstream generation"
}
"""

# The board's endpoint-scoped CDC disposition (Stage 2 artifact
# cdc_check/check_cdc.tcl, hash-checked). It accepts exactly the board's
# dispositioned findings and fails on any new or missing one, so it replaces
# the blanket "no Critical finding" rule on boards that ship one.
_BOARD_CDC_GATE = r"""
write_checkpoint -force reports/routed_cdc.dcp
if {![info exists ::env(XILINX_VIVADO)]} {error "XILINX_VIVADO is not set; cannot run the board CDC checker"}
set cdc_vivado [file join $::env(XILINX_VIVADO) bin vivado]
if {[catch {exec $cdc_vivado -mode batch -nojournal -nolog \
        -source [file normalize cdc_check/check_cdc.tcl] \
        -tclargs [file normalize reports/routed_cdc.dcp] [file normalize reports/cdc_check] \
        > reports/cdc_check.log 2>@1} cdc_error]} {
    error "Board CDC disposition check failed; see reports/cdc_check.log"
}
set cdc_file [open reports/cdc_check.log r]
set cdc_log [read $cdc_file]
close $cdc_file
if {![regexp -line {^CDC_CHECK_PASS$} $cdc_log]} {
    error "Board CDC disposition check did not pass; see reports/cdc_check.log"
}
"""


def build_quality_tcl(*, board_cdc_check: bool = False) -> str:
    """Post-route quality gates; the board CDC checker replaces the blanket rule."""
    gate = _BOARD_CDC_GATE if board_cdc_check else _BLANKET_CDC_GATE
    return _BUILD_QUALITY_TEMPLATE.replace("@@CDC_GATE@@\n", gate)


BUILD_QUALITY_TCL = build_quality_tcl()


class ArtifactDigest(BaseModel):
    """One required project input, relative to its portable build directory."""

    model_config = ConfigDict(frozen=True, extra="forbid")
    path: str
    sha256: str
    size: int


class BuildInputManifest(BaseModel):
    """Recorded project inputs; unrelated directory contents are not inputs."""

    model_config = ConfigDict(frozen=True, extra="forbid")
    board: str
    part: str
    artifacts: tuple[ArtifactDigest, ...]

    def verify(self, root: Path) -> None:
        """Reject missing or changed files before launching the tool."""
        root = root.resolve()
        for item in self.artifacts:
            path = (root / item.path).resolve()
            if not path.is_relative_to(root):
                raise ValueError(f"Build input escapes project directory: {item.path}")
            if not path.is_file() or path.stat().st_size != item.size:
                raise ValueError(f"Build input missing or changed: {item.path}")
            if hashlib.sha256(path.read_bytes()).hexdigest() != item.sha256:
                raise ValueError(f"Build input digest mismatch: {item.path}")


def write_build_manifest(
    root: Path, *, board: str, part: str, inputs: Iterable[Path]
) -> Path:
    """Record a complete explicit input list, refusing empty or external files."""
    root = root.resolve()
    records = []
    for path in sorted({p.resolve() for p in inputs}):
        relative = path.relative_to(root)
        data = path.read_bytes()
        if not data:
            raise ValueError(f"Empty build input: {relative}")
        records.append(
            ArtifactDigest(
                path=relative.as_posix(),
                sha256=hashlib.sha256(data).hexdigest(),
                size=len(data),
            )
        )
    if not records:
        raise ValueError("No build inputs supplied")
    manifest = BuildInputManifest(board=board, part=part, artifacts=tuple(records))
    target = root / "build_inputs.json"
    target.write_text(json.dumps(manifest.model_dump(mode="json"), indent=2) + "\n")
    return target


def verify_build_manifest(script: Path) -> None:
    """Verify the manifest accompanying a generated project entry point."""
    path = script.parent / "build_inputs.json"
    manifest = BuildInputManifest.model_validate_json(path.read_text())
    manifest.verify(script.parent)


def record_build_success(script: Path) -> None:
    """Publish the same portable success record used by the host handoff."""
    import subprocess
    import sys

    subprocess.run(
        [
            sys.executable,
            "-c",
            PORTABLE_PUBLISH_RESULT,
            str(script.parent / "build_inputs.json"),
        ],
        check=True,
    )
