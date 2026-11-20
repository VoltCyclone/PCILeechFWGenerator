"""Preserve initialization file bindings across Vivado IP upgrades."""

CAPTURE_MEMORY_CONTRACT_TCL = r"""
set memory_contract [dict create]
set bound_memory_names {}
foreach ip [get_ips] {
    foreach key {CONFIG.Coe_File CONFIG.coefficient_file} {
        if {[lsearch -exact [list_property $ip] $key] < 0} {continue}
        set coe [get_property $key $ip]
        if {$coe eq "" || $coe eq "no_coe_file_loaded"} {continue}
        set name [file tail $coe]
        if {[lsearch -exact $staged_memory_names $name] < 0} {
            error "IP $ip references an unrecorded memory: $coe"
        }
        set expected [file normalize [file join ip $name]]
        if {![file isfile $expected]} {error "Missing staged IP memory: $expected"}
        dict set memory_contract [get_property NAME $ip] [list $key $expected]
        lappend bound_memory_names $name
    }
}
foreach name $generated_memory_names {
    if {[lsearch -exact $bound_memory_names $name] < 0} {
        error "Generated memory is not bound to an IP: $name"
    }
}
proc verify_memory_contract {} {
    dict for {name binding} $::memory_contract {
        lassign $binding key expected
        set ip [get_ips $name]
        set actual [get_property $key $ip]
        if {[file pathtype $actual] ne "absolute"} {
            set actual [file join [file dirname [get_property IP_FILE $ip]] $actual]
        }
        if {![file isfile $actual]} {error "IP $name lost initialization file: $actual"}
        foreach label {actual expected} {
            set channel [open [set $label] rb]
            set ${label}_data [read $channel]
            close $channel
        }
        if {$actual_data ne $expected_data} {error "IP $name initialization differs from Stage 2"}
        if {$key eq "CONFIG.Coe_File" && ![get_property CONFIG.Load_Init_File $ip]} {
            error "IP $name has disabled initialization"
        }
    }
}
"""

RESTORE_MEMORY_CONTRACT_TCL = r"""
dict for {name binding} $memory_contract {
    lassign $binding key expected
    set ip [get_ips $name]
    if {$key eq "CONFIG.Coe_File"} {
        set_property -dict [list CONFIG.Load_Init_File true $key $expected] $ip
    } else {
        set_property $key $expected $ip
    }
}
verify_memory_contract
"""
