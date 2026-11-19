"""Exercise upgrade preservation and ignored-property detection in Tcl."""

import shutil
import subprocess

import pytest
from pcileechfwgenerator.vivado_handling.ip_build_contract import (
    CAPTURE_PCIE_CONTRACT_TCL,
    RESTORE_PCIE_CONTRACT_TCL,
    VERIFY_PCIE_CONTRACT_TCL,
)


@pytest.mark.parametrize(
    "ignored", ["", "CONFIG.en_ext_clk", "CONFIG.PCI_CFG_Space", "CONFIG.cfg_fc_if"]
)
def test_upgrade_preserves_board_wiring_or_fails(tmp_path, ignored):
    tcl = shutil.which("tclsh")
    if not tcl:
        pytest.skip("Tcl interpreter required")
    harness = r"""
set values [dict create]
foreach key {mode_selection en_ext_clk shared_logic_in_core cfg_fc_if Interface_Width User_Clk_Freq Ref_Clk_Freq Maximum_Link_Width Link_Speed PCI_CFG_Space EXT_PCI_CFG_Space PCI_CFG_Space_Addr EXT_PCI_CFG_Space_Addr} {
 dict set values CONFIG.$key original
}
proc get_ips {args} {return pcie_7x_0}
proc list_property {ip} {return [dict keys $::values]}
proc get_property {key ip} {
 if {$key eq "IS_LOCKED"} {return 1}
 if {$key eq "UPGRADE_VERSIONS"} {return new_version}
 return [dict get $::values $key]
}
proc upgrade_ip {ip} {dict for {k v} $::values {dict set ::values $k changed}}
proc set_property {flag data ip} {
 dict for {k v} $data {if {$k ne $::ignored} {dict set ::values $k $v}}
}
proc report_ip_status {args} {}
"""
    script = tmp_path / "contract.tcl"
    script.write_text(
        f"set ignored {{{ignored}}}\n"
        + harness
        + "\nif {[catch {\n"
        + CAPTURE_PCIE_CONTRACT_TCL
        + RESTORE_PCIE_CONTRACT_TCL
        + VERIFY_PCIE_CONTRACT_TCL
        + "\n} why]} {puts stderr $why; exit 1}\n"
    )
    result = subprocess.run([tcl, str(script)], capture_output=True, text=True)
    assert (result.returncode == 0) is (not ignored), result.stderr
    if ignored:
        assert ignored in result.stderr
