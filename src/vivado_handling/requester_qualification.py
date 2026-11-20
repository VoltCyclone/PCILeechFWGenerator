"""Fail-closed source and live-target binding of startup-controller evidence."""

import hashlib
import json
from pathlib import Path
from typing import Iterable, Optional

from ..device_clone import requester_tracking as models


def qualify_staged_startup(
    staging_dir: Path, *, sources: Iterable[str], board: Optional[str]
) -> Optional[models.RequesterStartupQualification]:
    """Select shipped qualification only for its authoritative source inventory."""
    if board != "pcileech_100t484_x1":
        return None
    inventory = set(sources)
    if (
        not {"pcileech_req_gate.sv", *models.QUALIFIED_STARTUP_SOURCE_SHA256}
        <= inventory
    ):
        return None
    for name, expected in models.QUALIFIED_STARTUP_SOURCE_SHA256.items():
        source = Path(staging_dir) / "src" / name
        if (
            not source.is_file()
            or hashlib.sha256(source.read_bytes()).hexdigest() != expected
        ):
            return None
    return models.RequesterStartupQualification(
        qualification_id="captaindma100t-gen1-abcd-drp64-v1",
        scope="controller_model_only",
        board="pcileech_100t484_x1",
        source_sha256=dict(models.QUALIFIED_STARTUP_SOURCE_SHA256),
        model_evidence_sha256=dict(models.QUALIFIED_STARTUP_MODEL_SHA256),
    )


def startup_qualification_guard_tcl(
    qualification: models.RequesterStartupQualification,
    *,
    ip_variable: str,
    root_expression: str = "[file normalize [file dirname [info script]]]",
) -> str:
    """Validate the actual selected HDL and exact live model-qualified target.

    Python is already required by portable build preflight. Only standard-library
    hashing is executed here; no model run or owner-side artifact is required.
    """
    qualification = models.RequesterStartupQualification.model_validate(
        qualification.model_dump()
    )
    expected = json.dumps(qualification.source_sha256, sort_keys=True)
    checker = (
        "import hashlib,json,pathlib,sys; "
        "root=pathlib.Path(sys.argv[1]); expected=json.loads(sys.argv[2]); "
        "bad=[n for n,h in expected.items() "
        "if hashlib.sha256((root/'src'/n).read_bytes()).hexdigest()!=h]; "
        "sys.exit('Unqualified requester startup source hash: '"
        "+','.join(bad) if bad else 0)"
    )
    return (
        f"set _startup_ip {ip_variable}\n"
        'if {[version -short] ne "2026.1"} '
        '{error "Unqualified requester Vivado version"}\n'
        'if {[get_property IPDEF $_startup_ip] ne "xilinx.com:ip:pcie_7x:3.3"} '
        '{error "Unqualified requester IP version"}\n'
        'if {[get_property PART [current_project]] ne "xc7a100tfgg484-1"} '
        '{error "Unqualified requester FPGA part"}\n'
        "if {[get_property top [get_filesets sources_1]] "
        'ne "pcileech_100t484_x1_top"} {error "Unqualified requester selected top"}\n'
        "foreach {key expected} {\n"
        "    Link_Speed 2.5_GT/s Maximum_Link_Width X1 Interface_Width 64_bit\n"
        "    Device_Port_Type PCI_Express_Endpoint_device\n"
        "    Cpl_Timeout_Range Ranges_A,_B,_C_&_D\n"
        "} {\n"
        "    if {![string equal -nocase [get_property CONFIG.$key $_startup_ip] "
        "$expected]} {\n"
        '        error "Unqualified requester target CONFIG.$key"\n'
        "    }\n"
        "}\n"
        "set _startup_clock [get_property CONFIG.User_Clk_Freq $_startup_ip]\n"
        "if {![string is double -strict $_startup_clock] "
        '|| $_startup_clock != 62.5} {error "Unqualified requester user clock"}\n'
        "foreach key {Extended_Tag_Field RECRC_Check RECRC_Check_Trim} {\n"
        "    set value [string tolower [string trim "
        "[get_property CONFIG.$key $_startup_ip]]]\n"
        '    if {$value ni {false 0}} {error "Unqualified requester CONFIG.$key"}\n'
        "}\n"
        # AER on and off are both proven (2026-11-12 and 2026-11-19 campaigns):
        # the DRP word at 0x019 and the controller result are identical.
        "set value [string tolower [string trim "
        "[get_property CONFIG.AER_Enabled $_startup_ip]]]\n"
        'if {$value ni {false 0 true 1}} {error "Unqualified requester CONFIG.AER_Enabled"}\n'
        f"set _startup_root {root_expression}\n"
        f"foreach name {{{' '.join(sorted(qualification.source_sha256))}}} {{\n"
        "    set path [file normalize [file join $_startup_root src $name]]\n"
        "    set selected [get_files -quiet $path]\n"
        "    if {[llength $selected] != 1 || "
        "[file normalize [get_property NAME $selected]] ne $path} {\n"
        '        error "Qualified requester source is not selected: $name"\n'
        "    }\n"
        "}\n"
        f"exec python3 -c {{{checker}}} $_startup_root {{{expected}}}\n"
    )
