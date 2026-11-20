"""Keep the board's PCIe interface stable when Vivado upgrades an IP."""

# Capture before upgrade: the donor override owns identity/BAR/MSI-X, while
# these settings belong to the board wiring and must not reset to IP defaults.
CAPTURE_PCIE_CONTRACT_TCL = r"""
set board_pcie_ip [get_ips -quiet pcie_7x_0]
if {[llength $board_pcie_ip] != 1} {error "Expected exactly one board PCIe IP"}
set board_pcie_contract [dict create]
foreach key {
    mode_selection en_ext_clk shared_logic_in_core cfg_fc_if
    Interface_Width User_Clk_Freq Ref_Clk_Freq Maximum_Link_Width Link_Speed
    PCI_CFG_Space EXT_PCI_CFG_Space PCI_CFG_Space_Addr EXT_PCI_CFG_Space_Addr
} {
    set prop CONFIG.$key
    if {[lsearch -exact [list_property $board_pcie_ip] $prop] < 0} {
        error "Board PCIe contract property unavailable: $prop"
    }
    dict set board_pcie_contract $prop [get_property $prop $board_pcie_ip]
}
"""

RESTORE_PCIE_CONTRACT_TCL = r"""
# Do not force IS_LOCKED false or silently accept regeneration failures.
foreach ip [get_ips] {
    if {[get_property IS_LOCKED $ip] || [get_property UPGRADE_VERSIONS $ip] ne ""} {
        upgrade_ip $ip
    }
}
set board_pcie_ip [get_ips pcie_7x_0]
if {[catch {set_property -dict $board_pcie_contract $board_pcie_ip} contract_error]} {
    error "Cannot preserve the board PCIe interface on this FPGA part/tool: $contract_error"
}
"""

VERIFY_PCIE_CONTRACT_TCL = r"""
dict for {prop expected} $board_pcie_contract {
    set actual [get_property $prop [get_ips pcie_7x_0]]
    if {![string equal -nocase $actual $expected]} {
        error "Board PCIe contract changed: $prop expected=$expected actual=$actual"
    }
}
report_ip_status -file ip_status_final.txt
"""
