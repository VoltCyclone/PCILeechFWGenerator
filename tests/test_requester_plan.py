"""Execute requester plan Tcl with controlled live IP readbacks."""

import json
import shutil
import subprocess

import pytest
from pcileechfwgenerator.device_clone.requester_tracking import (
    RequesterTrackingPlan,
    RequesterTrackingPolicy,
)
from pcileechfwgenerator.vivado_handling.requester_plan import (
    render_requester_plan_tcl,
    stage_requester_plan,
)
from pydantic import ValidationError


def policy(**updates):
    values = dict(
        cpl_timeout_ranges="ABCD",
        cpl_timeout_disable_supported=True,
        extended_tag_supported=True,
        drp_ready_timeout_cycles=64,
    )
    return RequesterTrackingPolicy(**(values | updates))


def test_missing_or_invented_plan_inputs_are_refused():
    with pytest.raises(ValidationError):
        RequesterTrackingPolicy(cpl_timeout_ranges="ABCD")
    for values in (
        {"cpl_timeout_ranges": "none"},
        {"extended_tag_supported": "false"},
        {"drp_ready_timeout_cycles": 15},
        {"drp_ready_timeout_cycles": 65536},
        {"n_np_max": 256},
    ):
        with pytest.raises(ValidationError):
            policy(**values)


def run_plan(tmp_path, *, ignore_generics=False, plan=None, **changes):
    tclsh = shutil.which("tclsh")
    if not tclsh:
        pytest.skip("needs Tcl")
    values = {
        "cfg_fc_if": "true",
        "User_Clk_Freq": "62.5",
        "Cpl_Timeout_Range": "Ranges_A,_B,_C_&_D",
        "Cpl_Timeout_Disable_Sup": "true",
        "Extended_Tag_Field": "true",
        "RECRC_Check": "0",
        "RECRC_Check_Trim": "false",
    } | changes
    initial = "\n".join(
        f"dict set props CONFIG.{key} {{{value}}}"
        for key, value in values.items()
        if value is not None
    )
    script = tmp_path / "run.tcl"
    script.write_text(
        "set props {}\n" + initial + "\n"
        "set generics {PARAM_DEVICE_ID=9}\n"
        "proc generate_target {args} {}\n"
        "proc get_ips {args} {return pcie_7x_0}\n"
        "proc get_filesets {args} {return sources_1}\n"
        "proc get_property {key obj} {\n"
        " if {$key eq {generic}} {return $::generics}\n"
        " return [dict get $::props $key]\n}\n"
        "proc set_property {key value obj} {\n"
        " if {$key eq {CONFIG.cfg_fc_if}} {return}\n"
        " if {$key ne {generic} || $obj ne {sources_1}} {error {bad setter}}\n"
        + (" return\n}\n" if ignore_generics else " set ::generics $value\n}\n")
        + render_requester_plan_tcl(plan or policy())
        + "\nif {[catch {::pcileech_requester::apply} reason]} {\n"
        " puts stderr $reason\n exit 1\n}\nputs $generics\n"
    )
    return subprocess.run(
        [tclsh, str(script)], cwd=tmp_path, text=True, capture_output=True, timeout=10
    )


@pytest.mark.parametrize(
    "clock,hz", [("62.5", 62500000), ("125.0", 125000000), ("250", 250000000)]
)
def test_uses_live_clock_and_donor_bits_without_losing_other_generics(
    tmp_path, clock, hz
):
    result = run_plan(tmp_path, User_Clk_Freq=clock)
    assert result.returncode == 0, result.stderr
    assignments = dict(item.split("=", 1) for item in result.stdout.split())
    assert assignments == {
        "PARAM_DEVICE_ID": "9",
        "REQ_CLK_PCIE_HZ": str(hz),
        "DRP_PLAN_BOUND": "1",
        "DRP_PLAN_ADDR": "9'h019",
        "DRP_PLAN_MASK": "16'h0200",
        "DRP_PLAN_VALUE": "16'h0200",
        "DRP_PLAN_CHECK_MASK": "16'h3C00",
        "DRP_PLAN_BASE_EXPECT": "16'h3C00",
        "DRP_RDY_TIMEOUT_CYCLES": "64",
    }
    plan = RequesterTrackingPlan.model_validate_json(
        (tmp_path / "requester_plan_resolved.json").read_text()
    )
    assert plan.clk_pcie_hz == hz
    assert plan.trim_tlp_digest is False
    assert plan.ecrc_check_enabled is False


@pytest.mark.parametrize(
    "changes",
    [
        {"cfg_fc_if": "false"},
        {"cfg_fc_if": None},
        {"User_Clk_Freq": None},
        {"User_Clk_Freq": "100"},
        {"Cpl_Timeout_Range": "Range_B"},
        {"Cpl_Timeout_Disable_Sup": "false"},
        {"Extended_Tag_Field": "false"},
        {"RECRC_Check": "1"},
        {"RECRC_Check_Trim": None},
        {"RECRC_Check_Trim": "unknown"},
    ],
)
def test_unsafe_or_missing_live_readback_aborts_before_binding(tmp_path, changes):
    result = run_plan(tmp_path, **changes)
    assert result.returncode != 0
    assert not (tmp_path / "requester_plan_resolved.json").exists()


def test_trim_on_is_supported(tmp_path):
    result = run_plan(tmp_path, RECRC_Check_Trim="TRUE")
    assert result.returncode == 0, result.stderr
    assert (
        json.loads((tmp_path / "requester_plan_resolved.json").read_text())[
            "trim_tlp_digest"
        ]
        is True
    )


@pytest.mark.parametrize(
    "ranges,enum,base",
    [
        ("A", "Range_A", "16'h0400"),
        ("B", "Range_B", "16'h0800"),
        ("AB", "Ranges_A_&_B", "16'h0C00"),
        ("BC", "Ranges_B_&_C", "16'h1800"),
        ("ABC", "Ranges_A,_B_&_C", "16'h1C00"),
        ("BCD", "Ranges_B,_C_&_D", "16'h3800"),
        ("ABCD", "Ranges_A,_B,_C_&_D", "16'h3C00"),
    ],
)
def test_donor_ranges_and_false_support_bits_propagate(tmp_path, ranges, enum, base):
    result = run_plan(
        tmp_path,
        plan=policy(
            cpl_timeout_ranges=ranges,
            cpl_timeout_disable_supported=False,
            extended_tag_supported=False,
        ),
        Cpl_Timeout_Range=enum,
        Cpl_Timeout_Disable_Sup="false",
        Extended_Tag_Field="false",
    )
    assert result.returncode == 0, result.stderr
    assignments = dict(item.split("=", 1) for item in result.stdout.split())
    assert assignments["DRP_PLAN_BASE_EXPECT"] == base
    assert assignments["DRP_PLAN_VALUE"] == "16'h0000"


def test_plan_staging_requires_donor_inputs_only_for_requester_board(tmp_path):
    assert stage_requester_plan(tmp_path, {}, sources=[]) is None
    (tmp_path / "src").mkdir()
    (tmp_path / "src" / "pcileech_req_gate.sv").write_text("module gate; endmodule\n")
    with pytest.raises(ValueError):
        stage_requester_plan(tmp_path, {}, sources=["pcileech_req_gate.sv"])
    assert not (tmp_path / "requester_plan.tcl").exists()
    context = {
        "template_context": {
            "device_config": {
                "cpl_timeout_ranges": "ABCD",
                "cpl_timeout_disable_sup": True,
            },
            "pcie_extended_tag_supported": True,
        }
    }
    stage_requester_plan(tmp_path, context, sources=["pcileech_req_gate.sv"])
    first = (tmp_path / "requester_plan.tcl").read_text()
    stage_requester_plan(tmp_path, context, sources=["pcileech_req_gate.sv"])
    assert (tmp_path / "requester_plan.tcl").read_text() == first
    assert (tmp_path / "requester_policy.json").is_file()


def test_extended_tag_advertisement_is_donor_owned():
    from pcileechfwgenerator.vivado_handling.pcie_ip_donor_override import (
        donor_pcie_ip_config_from_result,
        generate_pcie_ip_override_tcl,
    )

    from tests.test_msix_ip_contract import _capture_donor, _run

    extra = donor_pcie_ip_config_from_result(
        {
            "template_context": {
                "pcie_extended_tag_supported": False,
            }
        }
    )
    fragment = generate_pcie_ip_override_tcl(_capture_donor(), extra=extra)
    err, _ = _run(fragment, ignore_keys=("CONFIG.Extended_Tag_Field",))
    assert err is not None
    err, state = _run(fragment)
    assert err is None
    assert state["generated"]


def test_requester_artifacts_survive_portable_staging_and_are_hash_checked(
    tmp_path, monkeypatch
):
    from pcileechfwgenerator.vivado_handling.build_evidence import verify_build_manifest
    from pcileechfwgenerator.vivado_handling.pcileech_build_integration import (
        BuildArtifactError,
        validate_stage2_artifacts,
        write_stage2_manifest,
    )

    from tests.test_msix_artifact_staging import (
        BOARD,
        _forbid_stock_lookup,
        _integration,
        _stage2,
    )

    _stage2(tmp_path)
    source = tmp_path / "src" / "pcileech_req_gate.sv"
    source.write_text("module requester; endmodule\n")
    (tmp_path / "requester_plan.tcl").write_text(render_requester_plan_tcl(policy()))
    (tmp_path / "requester_policy.json").write_text(policy().model_dump_json())
    previous = json.loads((tmp_path / "stage2_artifacts.json").read_text())
    write_stage2_manifest(
        tmp_path,
        board=BOARD,
        sources=[*previous["sources"], source.name],
        constraints=previous["constraints"],
        generated_memories=previous["generated_memories"],
        ip_definitions=previous["ip"]["xci"],
    )
    _forbid_stock_lookup(monkeypatch)
    script = _integration(tmp_path).create_unified_build_script(BOARD)
    # Both plan files must be actual portable build inputs, not orphan files.
    inventory = json.loads((script.parent / "build_inputs.json").read_text())
    assert {"requester_plan.tcl", "requester_policy.json"} <= {
        a["path"] for a in inventory["artifacts"]
    }
    assert script.read_text().index(
        'source "pcileech_donor_ip_overrides.tcl"'
    ) < script.read_text().index("::pcileech_requester::apply")
    verify_build_manifest(script)
    (script.parent / "requester_plan.tcl").write_text("# changed\n")
    with pytest.raises(ValueError):
        verify_build_manifest(script)
    (tmp_path / "requester_policy.json").write_text("{}")
    with pytest.raises(BuildArtifactError):
        validate_stage2_artifacts(tmp_path, board=BOARD)


def test_manifest_refuses_requester_rtl_without_plan(tmp_path):
    from pcileechfwgenerator.vivado_handling.pcileech_build_integration import (
        BuildArtifactError,
        write_stage2_manifest,
    )

    from tests.test_msix_artifact_staging import BOARD, _stage2

    _stage2(tmp_path)
    source = tmp_path / "src" / "pcileech_req_gate.sv"
    source.write_text("module requester; endmodule\n")
    previous = json.loads((tmp_path / "stage2_artifacts.json").read_text())
    with pytest.raises(BuildArtifactError, match="requester"):
        write_stage2_manifest(
            tmp_path,
            board=BOARD,
            sources=[*previous["sources"], source.name],
            constraints=previous["constraints"],
            generated_memories=previous["generated_memories"],
            ip_definitions=previous["ip"]["xci"],
        )


def test_ignored_generic_binding_is_fatal(tmp_path):
    result = run_plan(tmp_path, ignore_generics=True)
    assert result.returncode != 0
    assert not (tmp_path / "requester_plan_resolved.json").exists()


def test_failed_rebind_archives_resolved_plan(tmp_path):
    assert run_plan(tmp_path).returncode == 0
    assert run_plan(tmp_path, User_Clk_Freq="100").returncode != 0
    assert not (tmp_path / "requester_plan_resolved.json").exists()
    assert list(tmp_path.glob("previous-requester-plan-*.json"))


def test_stale_requester_source_is_not_current_board_evidence(tmp_path):
    (tmp_path / "src").mkdir()
    (tmp_path / "src" / "pcileech_req_gate.sv").write_text("// previous run\n")
    assert stage_requester_plan(tmp_path, {}, sources=[]) is None


@pytest.mark.parametrize("resolved", [False, True])
def test_published_build_requires_this_runs_resolved_requester_plan(tmp_path, resolved):
    from pcileechfwgenerator.vivado_handling.build_evidence import (
        record_build_success,
        write_build_manifest,
    )

    script = tmp_path / "build_all.tcl"
    script.write_text("# fixture\n")
    policy_path = tmp_path / "requester_policy.json"
    policy_path.write_text(policy().model_dump_json())
    write_build_manifest(
        tmp_path, board="test", part="test", inputs=[script, policy_path]
    )
    (tmp_path / "output").mkdir()
    (tmp_path / "output" / "image.bit").write_bytes(b"fixture-image")
    (tmp_path / "reports").mkdir()
    (tmp_path / "reports" / "tool_version.txt").write_text("fixture-tool")
    if resolved:
        assert run_plan(tmp_path).returncode == 0
        record_build_success(script)
        record = json.loads((tmp_path / "output" / "build_result.json").read_text())
        assert record["requester"]["plan"]["clk_pcie_hz"] == 62500000
        assert record["requester"]["drp_startup_verified"] is False
        assert len(record["requester"]["resolved_sha256"]) == 64
    else:
        with pytest.raises(subprocess.CalledProcessError):
            record_build_success(script)
        assert not (tmp_path / "output" / "build_result.json").exists()


def test_local_preflight_failure_archives_resolved_plan(tmp_path):
    from pcileechfwgenerator.vivado_handling.pcileech_build_integration import (
        BuildArtifactError,
    )

    from tests.test_msix_artifact_staging import BOARD, _integration, _stage2

    _stage2(tmp_path)
    board = tmp_path / BOARD
    board.mkdir()
    resolved = board / "requester_plan_resolved.json"
    resolved.write_text("previous-run")
    (tmp_path / "pcileech_donor_ip_overrides.tcl").write_text("changed")
    with pytest.raises(BuildArtifactError):
        _integration(tmp_path).create_unified_build_script(BOARD)
    assert not resolved.exists()
    assert list(board.glob("previous-requester-plan-*.json"))
