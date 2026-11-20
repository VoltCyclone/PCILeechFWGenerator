"""Only the qualified, source-bound startup controller may own Disable support."""

import hashlib
import json
import shutil
import subprocess

import pytest
from pcileechfwgenerator.device_clone import requester_tracking as models
from pcileechfwgenerator.device_clone.requester_tracking import (
    RequesterTrackingPlan,
)
from pcileechfwgenerator.vivado_handling.pcie_ip_donor_override import (
    DonorPCIeIPConfig,
    generate_pcie_ip_override_tcl,
)
from pcileechfwgenerator.vivado_handling.requester_plan import (
    render_requester_plan_tcl,
    requester_policy_from_result,
)

from tests.test_msix_ip_contract import _capture_donor

BOARD = "pcileech_100t484_x1"


@pytest.fixture
def qualified(tmp_path, monkeypatch):
    """Use tiny source fixtures instead of requiring private board checkouts."""
    names = (
        "pcileech_pcie_drp_startup.sv",
        "pcileech_pcie_a7.sv",
        "pcileech_100t484_x1_top.sv",
    )
    (tmp_path / "src").mkdir()
    hashes = {}
    for i, name in enumerate(names):
        data = f"module fixture_{i}; endmodule\n".encode()
        (tmp_path / "src" / name).write_bytes(data)
        hashes[name] = hashlib.sha256(data).hexdigest()
    monkeypatch.setattr(models, "QUALIFIED_STARTUP_SOURCE_SHA256", hashes)
    sources = (*names, "pcileech_req_gate.sv")
    result = {
        "template_context": {
            "device_config": {
                "cpl_timeout_ranges": "ABCD",
                "cpl_timeout_disable_sup": True,
            },
            "pcie_extended_tag_supported": False,
        }
    }
    policy = requester_policy_from_result(
        tmp_path, result, sources=sources, board=BOARD
    )
    assert policy.startup_qualification is not None
    return tmp_path, sources, result, policy


def run_qualified(
    qualified,
    *,
    policy_override="qualified",
    changes=None,
    fail_setter=False,
    run_plan=True,
    relative_caller=False,
    unselected_source=None,
):
    root, sources, result, qualified_policy = qualified
    policy = qualified_policy if policy_override == "qualified" else policy_override
    props = {
        "PART": "xc7a100tfgg484-1",
        "IPDEF": "xilinx.com:ip:pcie_7x:3.3",
        "top": "pcileech_100t484_x1_top",
        "CONFIG.Link_Speed": "2.5_GT/s",
        "CONFIG.Maximum_Link_Width": "X1",
        "CONFIG.User_Clk_Freq": "62.5",
        "CONFIG.Interface_Width": "64_bit",
        "CONFIG.Device_Port_Type": "PCI_Express_Endpoint_device",
        "CONFIG.Cpl_Timeout_Range": "Ranges_A,_B,_C_&_D",
        "CONFIG.Cpl_Timeout_Disable_Sup": "false",
        "CONFIG.Extended_Tag_Field": "false",
        "CONFIG.AER_Enabled": "false",
        "CONFIG.RECRC_Check": "0",
        "CONFIG.RECRC_Check_Trim": "false",
        "CONFIG.cfg_fc_if": "true",
        "generic": "PARAM_DEVICE_ID=9",
    }
    props.update(changes or {})
    extra = DonorPCIeIPConfig(
        cpl_timeout_ranges="ABCD",
        cpl_timeout_disable_supported=True,
        extended_tag_supported=False,
    )
    (root / "override.tcl").write_text(
        generate_pcie_ip_override_tcl(
            _capture_donor(), extra=extra, requester_policy=policy
        )
    )
    if run_plan:
        (root / "requester_plan.tcl").write_text(
            render_requester_plan_tcl(qualified_policy)
        )
    initial = "set props [dict create]\n" + "\n".join(
        f"dict set props {{{key}}} {{{value}}}"
        for key, value in props.items()
        if value is not None
    )
    registered = " ".join(
        "{" + str(root / "src" / n) + "}"
        for n in sources
        if n.endswith(".sv") and n != unselected_source
    )
    driver = root / "run.tcl"
    driver.write_text(
        "cd [file dirname [info script]]\n"
        + initial
        + f"\nset registered [list {registered}]\n"
        + r"""
set generated 0
proc version {args} {return $::tool_version}
proc current_project {} {return project}
proc get_ips {args} {return pcie_7x_0}
proc get_filesets {args} {return sources_1}
proc get_files {args} {
 set path [lindex $args end]
 if {$path in $::registered} {return [list $path]}
 return {}
}
proc get_property {key obj} {
 if {$key eq "NAME"} {return $obj}
 return [dict get $::props $key]
}
proc set_property {key value obj} {
 if {$key eq "CONFIG.Cpl_Timeout_Disable_Sup"} {
  if {$::fail_setter} {error {setter refused}}
  return
 }
 # The negative cases model unsupported/ignored properties after a setter.
 if {$key in $::ignored} {return}
 dict set ::props $key $value
}
proc generate_target {args} {set ::generated 1}
"""
        + f"\nset tool_version {{{props.get('tool_version', '2026.1')}}}\n"
        + f"set ignored {{{' '.join(changes or {})}}}\nset fail_setter {int(fail_setter)}\n"
        + "if {[catch {source override.tcl} why]} {puts stderr $why; exit 1}\n"
        + (
            "source requester_plan.tcl\n"
            "if {[catch {::pcileech_requester::apply} why]} {puts stderr $why; exit 1}\n"
            if run_plan
            else ""
        )
        + "puts OVERRIDE_PASS\nputs [dict get $::props generic]\n"
    )
    tcl = shutil.which("tclsh")
    if tcl is None:
        pytest.skip("needs Tcl")
    return subprocess.run(
        [tcl, str(driver.relative_to(root.parent)) if relative_caller else str(driver)],
        cwd=root.parent if relative_caller else root,
        text=True,
        capture_output=True,
        timeout=20,
    )


def test_only_qualified_policy_accepts_false_initial_and_preserves_final_true(
    qualified,
):
    result = run_qualified(qualified)
    assert result.returncode == 0, result.stderr
    plan = RequesterTrackingPlan.model_validate_json(
        (qualified[0] / "requester_plan_resolved.json").read_text()
    )
    assert plan.cpl_timeout_disable_supported is True
    assert plan.ip_initial_cpl_timeout_disable_supported is False
    assert plan.startup_verified is False
    assert plan.startup_qualification.scope == "controller_model_only"
    assert "DRP_PLAN_VALUE=16'h0200" in result.stdout


def test_no_policy_retains_strict_readback(qualified):
    result = run_qualified(qualified, policy_override=None, run_plan=False)
    assert result.returncode != 0


@pytest.mark.parametrize(
    "changes",
    [
        {"PART": "xc7a100tfgg484-2"},
        {"CONFIG.Link_Speed": "5.0_GT/s"},
        {"CONFIG.Maximum_Link_Width": "X2"},
        {"CONFIG.User_Clk_Freq": "125"},
        {"IPDEF": "xilinx.com:ip:pcie_7x:3.2"},
        {"tool_version": "2025.2"},
        {"CONFIG.Interface_Width": "128_bit"},
        {"CONFIG.Device_Port_Type": "Root_Port_of_PCI_Express_Root_Complex"},
        {"CONFIG.Extended_Tag_Field": "true"},
        {"CONFIG.AER_Enabled": "maybe"},
        {"CONFIG.RECRC_Check": "1"},
        {"CONFIG.RECRC_Check_Trim": "true"},
        {"CONFIG.Cpl_Timeout_Disable_Sup": "unsupported"},
        {"CONFIG.Cpl_Timeout_Disable_Sup": None},
        {"CONFIG.Cpl_Timeout_Range": "Range_B"},
        {"top": "other_top"},
        {"CONFIG.Vendor_ID": "0x10EE"},
    ],
)
def test_unqualified_live_target_or_unknown_readback_refuses(qualified, changes):
    result = run_qualified(qualified, changes=changes)
    assert result.returncode != 0, result.stdout


def test_qualified_policy_does_not_hide_setter_failure(qualified):
    assert run_qualified(qualified, fail_setter=True).returncode != 0


def test_changed_staged_source_refuses_at_runtime(qualified):
    root, sources, result, policy = qualified
    (root / "src" / "pcileech_pcie_a7.sv").write_text("module changed; endmodule\n")
    assert run_qualified(qualified).returncode != 0
    assert (
        requester_policy_from_result(
            root, result, sources=sources, board=BOARD
        ).startup_qualification
        is None
    )


def test_inventory_and_board_identity_are_required_for_qualification(qualified):
    root, sources, result, policy = qualified
    assert (
        requester_policy_from_result(
            root, result, sources=sources, board="other_board"
        ).startup_qualification
        is None
    )
    assert (
        requester_policy_from_result(
            root, result, sources=["pcileech_req_gate.sv"], board=BOARD
        ).startup_qualification
        is None
    )


def test_matching_true_initial_is_not_reported_as_false(qualified):
    result = run_qualified(
        qualified, changes={"CONFIG.Cpl_Timeout_Disable_Sup": "true"}
    )
    assert result.returncode == 0, result.stderr
    plan = json.loads((qualified[0] / "requester_plan_resolved.json").read_text())
    assert plan["ip_initial_cpl_timeout_disable_supported"] is True


def test_hash_binding_does_not_depend_on_python_assertions(qualified, monkeypatch):
    (qualified[0] / "src" / "pcileech_pcie_a7.sv").write_text(
        "module changed; endmodule\n"
    )
    monkeypatch.setenv("PYTHONOPTIMIZE", "1")
    assert run_qualified(qualified).returncode != 0


def test_plan_qualification_uses_loaded_plan_path_after_relative_caller_cd(qualified):
    result = run_qualified(qualified, relative_caller=True)
    assert result.returncode == 0, result.stderr
    assert (qualified[0] / "requester_plan_resolved.json").is_file()


def test_source_presence_without_project_selection_is_not_qualification(qualified):
    result = run_qualified(qualified, unselected_source="pcileech_pcie_a7.sv")
    assert result.returncode != 0


def test_explicit_unqualified_policy_retains_strict_readback(qualified):
    policy = qualified[3].model_copy(update={"startup_qualification": None})
    assert (
        run_qualified(qualified, policy_override=policy, run_plan=False).returncode != 0
    )


@pytest.mark.parametrize("aer", ["true", "false"])
def test_aer_on_and_off_are_both_qualified(qualified, aer):
    """Both proven by the controller campaign (2026-11-12 off, 2026-11-19 on)."""
    result = run_qualified(qualified, changes={"CONFIG.AER_Enabled": aer})
    assert result.returncode == 0, result.stdout + result.stderr
