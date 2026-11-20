"""Bind requester generics after donor override, using live IP properties.

The file is load-only: the board script can source it before IP setup. Call
``::pcileech_requester::apply`` only after all IP writes/readback checks.
Only a source-bound qualified startup policy can distinguish initial Disable
Supported=false from its donor final target. Other readbacks remain strict.
"""

from pathlib import Path
from typing import Any, Iterable, Mapping, Optional, cast

from ..device_clone.requester_tracking import RequesterTrackingPolicy
from .pcie_ip_donor_override import cpl_timeout_range_enum
from .requester_qualification import (
    qualify_staged_startup,
    startup_qualification_guard_tcl,
)

PLAN_TCL = "requester_plan.tcl"
POLICY_JSON = "requester_policy.json"
PLAN_FILES = (PLAN_TCL, POLICY_JSON)
REQUESTER_SOURCE = "pcileech_req_gate.sv"
# Present only on boards whose top has the FT601_PHY_MMCM generic.
FT601_MMCM_SOURCE = "pcileech_ft601_phy.sv"
# Engineering watchdog, NOT a vendor-specified latency maximum. 64 DRP clocks
# matches the standalone controller bench; exact-core qualification is pending.
# PG054 "Writing and Reading the DRP Interface" specifies a ready handshake
# without a maximum response time. A timeout leaves the endpoint held in reset.
DRP_WATCHDOG_CYCLES = 64


def render_requester_plan_tcl(
    policy: RequesterTrackingPolicy, *, ft601_phy_mmcm: Optional[bool] = None
) -> str:
    """Render a load-only Tcl plan; bind only after validating every readback.

    ``ft601_phy_mmcm`` (boards with the MMCM FT601 PHY only) binds
    FT601_PHY_MMCM. Stage 2 chooses the matching constraints.
    """
    # Revalidate even a model constructed with Pydantic's unchecked APIs.
    policy = RequesterTrackingPolicy.model_validate(policy.model_dump())
    ranges = sum(1 << "ABCD".index(letter) for letter in policy.cpl_timeout_ranges)
    expected = ranges << 10
    value = 0x200 if policy.cpl_timeout_disable_supported else 0
    header = (
        "namespace eval ::pcileech_requester {}\n"
        "set ::pcileech_requester::plan_root [file normalize [file dirname [info script]]]\n"
        "proc ::pcileech_requester::apply {} {\n"
        "    if {[file exists requester_plan_resolved.json]} {\n"
        '        file rename requester_plan_resolved.json "previous-requester-plan-[clock clicks].json"\n'
        "    }\n"
        "    set ip [get_ips -quiet pcie_7x_0]\n"
        '    if {[llength $ip] != 1} {error "Requester plan needs exactly one PCIe IP"}\n'
        "    set_property CONFIG.cfg_fc_if true $ip\n"
        "    generate_target -force all $ip\n"
        "    set fc [string tolower [get_property CONFIG.cfg_fc_if $ip]]\n"
        '    if {$fc ni {true 1}} {error "Requester flow-control interface is disabled"}\n'
        f"    set expected_range {cpl_timeout_range_enum(policy.cpl_timeout_ranges)}\n"
        f"    set expected_disable {int(policy.cpl_timeout_disable_supported)}\n"
        f"    set expected_tags {int(policy.extended_tag_supported)}\n"
    )
    guard = ""
    if policy.startup_qualification is not None:
        guard = startup_qualification_guard_tcl(
            policy.startup_qualification,
            ip_variable="$ip",
            root_expression="$::pcileech_requester::plan_root",
        )
    qualification_json = (
        "null"
        if policy.startup_qualification is None
        else policy.startup_qualification.model_dump_json()
    )
    disable_check = "$disable != $expected_disable"
    if policy.startup_qualification is not None:
        # Only the proved false-initial/true-final transition is permitted.
        disable_check = "!($disable == $expected_disable || ($disable == 0 && $expected_disable == 1))"
    return (
        header
        + guard
        + r"""
    set clock [string trim [get_property CONFIG.User_Clk_Freq $ip]]
    if {![string is double -strict $clock] || !($clock == 62.5 || $clock == 125 || $clock == 250)} {
        error "Unsupported requester User_Clk_Freq: $clock"
    }
    set hz [expr {wide(double($clock) * 1000000)}]
    set range [get_property CONFIG.Cpl_Timeout_Range $ip]
    if {![string equal -nocase $range $expected_range]} {
        error "Requester Completion Timeout Range disagrees with donor"
    }
    foreach {key var} {
        Cpl_Timeout_Disable_Sup disable
        Extended_Tag_Field tags
        RECRC_Check check
        RECRC_Check_Trim trim
    } {
        set raw [string tolower [string trim [get_property CONFIG.$key $ip]]]
        if {$raw in {true 1}} {set $var 1} elseif {$raw in {false 0}} {set $var 0} else {
            error "Invalid requester IP boolean CONFIG.$key: $raw"
        }
    }
"""
        + f"    if {{{disable_check} || $tags != $expected_tags}} {{\n"
        + r"""
        error "Requester capability readback disagrees with donor"
    }
    if {$check} {error "Requester RX does not support receive ECRC checking"}
    set fs [get_filesets sources_1]
    if {[llength $fs] != 1} {error "Requester plan needs sources_1"}
    set generics [dict create]
    foreach item [get_property generic $fs] {
        if {![regexp {^([^=]+)=(.+)$} $item -> key val]} {
            error "Cannot preserve existing top-level generic: $item"
        }
        dict set generics $key $val
    }
"""
        + (
            "    dict set generics REQ_CLK_PCIE_HZ $hz\n"
            "    dict set generics DRP_PLAN_BOUND 1\n"
            "    dict set generics DRP_PLAN_ADDR 9'h019\n"
            "    dict set generics DRP_PLAN_MASK 16'h0200\n"
            f"    dict set generics DRP_PLAN_VALUE 16'h{value:04X}\n"
            "    dict set generics DRP_PLAN_CHECK_MASK 16'h3C00\n"
            f"    dict set generics DRP_PLAN_BASE_EXPECT 16'h{expected:04X}\n"
            f"    dict set generics DRP_RDY_TIMEOUT_CYCLES {policy.drp_ready_timeout_cycles}\n"
        )
        + (
            ""
            if ft601_phy_mmcm is None
            else f"    dict set generics FT601_PHY_MMCM {int(bool(ft601_phy_mmcm))}\n"
        )
        + r"""
    set bindings {}
    dict for {key val} $generics {lappend bindings $key=$val}
    set_property generic $bindings $fs
    set actual [get_property generic $fs]
    foreach binding $bindings {
        if {[lsearch -exact $actual $binding] < 0} {
            error "Requester generic binding did not read back: $binding"
        }
    }

    # Only numeric/validated boolean tokens are substituted into this JSON.
    set bools {false true}
"""
        + (
            "    set resolved [format {\n"
            '      "cpl_timeout_ranges": "' + policy.cpl_timeout_ranges + '",\n'
            '      "cpl_timeout_disable_supported": %s,\n'
            '      "ip_initial_cpl_timeout_disable_supported": %s,\n'
            '      "extended_tag_supported": %s,\n'
            f'      "drp_ready_timeout_cycles": {policy.drp_ready_timeout_cycles},\n'
            '      "clk_pcie_hz": %d,\n'
            '      "trim_tlp_digest": %s,\n'
            '      "ecrc_check_enabled": false,\n'
            '      "flow_control_interface_enabled": true,\n'
            '      "startup_verified": false,\n'
            f'      "startup_qualification": {qualification_json}\n'
            "    } [lindex $bools $expected_disable] [lindex $bools $disable] [lindex $bools $tags] $hz [lindex $bools $trim]]\n"
            # Tcl braces delimit the format argument, so add JSON object braces.
            "    set target [open requester_plan_resolved.json w]\n"
            '    puts $target "\\{$resolved\\}"\n'
            "    close $target\n"
            "}\n"
        )
    )


def requester_policy_from_result(
    staging_dir: Path,
    result: Mapping[str, Any],
    *,
    sources: Iterable[str],
    board: Optional[str] = None,
) -> Optional[RequesterTrackingPolicy]:
    """Require donor inputs and qualify only an exact staged board snapshot."""
    sources = set(sources)
    if REQUESTER_SOURCE not in sources:
        return None
    context = result.get("template_context") or {}
    donor = context.get("device_config") or {}
    if type(context.get("pcie_extended_tag_supported")) is not bool:
        raise ValueError(
            "Requester tracking needs the donor's DevCap Extended Tag Field "
            "Supported bit (pcie_extended_tag_supported); the capture did not "
            "provide it"
        )
    policy = RequesterTrackingPolicy(
        # Pydantic validates these; a missing donor value is rejected there.
        cpl_timeout_ranges=cast(Any, donor.get("cpl_timeout_ranges")),
        cpl_timeout_disable_supported=cast(Any, donor.get("cpl_timeout_disable_sup")),
        extended_tag_supported=cast(Any, context.get("pcie_extended_tag_supported")),
        drp_ready_timeout_cycles=DRP_WATCHDOG_CYCLES,
    )
    if (
        policy.cpl_timeout_ranges == "ABCD"
        and policy.cpl_timeout_disable_supported
        and not policy.extended_tag_supported
    ):
        qualification = qualify_staged_startup(
            staging_dir, sources=sources, board=board
        )
        if qualification is not None:
            policy = RequesterTrackingPolicy.model_validate(
                policy.model_dump()
                | {"startup_qualification": qualification.model_dump()}
            )
    return policy


def stage_requester_plan(
    staging_dir: Path,
    result: Mapping[str, Any],
    *,
    sources: Iterable[str],
    board: Optional[str] = None,
    ft601_phy_mmcm: Optional[bool] = None,
) -> Optional[Path]:
    """Stage required requester inputs only for a board with the requester RTL."""
    staging_dir = Path(staging_dir)
    policy = requester_policy_from_result(
        staging_dir, result, sources=sources, board=board
    )
    if policy is None:
        if ft601_phy_mmcm is False:
            raise ValueError("The legacy FT601 PHY binding needs the requester plan")
        return None
    target = staging_dir / PLAN_TCL
    target.write_text(render_requester_plan_tcl(policy, ft601_phy_mmcm=ft601_phy_mmcm))
    (staging_dir / POLICY_JSON).write_text(policy.model_dump_json(indent=2) + "\n")
    return target
