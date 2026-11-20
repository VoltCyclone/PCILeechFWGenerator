"""Memory initialization survives an IP upgrade, or the build aborts."""

import shutil
import subprocess

import pytest
from pcileechfwgenerator.vivado_handling.memory_ip_contract import (
    CAPTURE_MEMORY_CONTRACT_TCL,
    RESTORE_MEMORY_CONTRACT_TCL,
)


@pytest.mark.parametrize("ignore_load", [False, True])
def test_memory_initialization_cannot_be_silently_disabled(tmp_path, ignore_load):
    tcl = shutil.which("tclsh")
    if not tcl:
        pytest.skip("Tcl interpreter required")
    (tmp_path / "ip").mkdir()
    (tmp_path / "ip" / "generated.coe").write_text("memory_initialization_radix=16;\n")
    harness = r"""
set staged_memory_names {generated.coe}
set generated_memory_names {generated.coe}
set values [dict create CONFIG.Coe_File generated.coe CONFIG.Load_Init_File true NAME memory IP_FILE ip/memory.xci]
proc get_ips {args} {return memory}
proc list_property {ip} {return [dict keys $::values]}
proc get_property {key ip} {return [dict get $::values $key]}
proc set_property {flag settings ip} {
 dict for {k v} $settings {
  if {$::ignore_load && $k eq "CONFIG.Load_Init_File"} {continue}
  dict set ::values $k $v
 }
}
"""
    script = tmp_path / "check.tcl"
    script.write_text(
        f"set ignore_load {int(ignore_load)}\n"
        + harness
        + CAPTURE_MEMORY_CONTRACT_TCL
        + "\ndict set values CONFIG.Load_Init_File false\n"
        + "dict set values CONFIG.Coe_File no_coe_file_loaded\n"
        + "if {[catch {\n"
        + RESTORE_MEMORY_CONTRACT_TCL
        + "\n} why]} {puts stderr $why; exit 1}\n"
    )
    result = subprocess.run(
        [tcl, str(script)], cwd=tmp_path, capture_output=True, text=True
    )
    assert (result.returncode == 0) is (not ignore_load), result.stderr
    if ignore_load:
        assert "disabled initialization" in result.stderr


@pytest.mark.parametrize("binding", ["", "no_coe_file_loaded", "other.coe"])
def test_generated_memory_must_have_a_live_ip_binding(tmp_path, binding):
    tcl = shutil.which("tclsh")
    if not tcl:
        pytest.skip("Tcl interpreter required")
    (tmp_path / "ip").mkdir()
    (tmp_path / "ip" / "other.coe").write_text("memory_initialization_radix=16;\n")
    script = tmp_path / "check.tcl"
    script.write_text(f"set binding {{{binding}}}\n" + r"""
set staged_memory_names {generated.coe other.coe}
set generated_memory_names {generated.coe}
proc get_ips {args} {return memory}
proc list_property {ip} {return {CONFIG.Coe_File NAME}}
proc get_property {key ip} {
    if {$key eq "NAME"} {return memory}
    return $::binding
}
""" + CAPTURE_MEMORY_CONTRACT_TCL)
    result = subprocess.run(
        [tcl, str(script)], cwd=tmp_path, capture_output=True, text=True
    )
    assert result.returncode != 0
    assert "Generated memory is not bound to an IP: generated.coe" in result.stderr
