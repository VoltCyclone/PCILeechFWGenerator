"""Behavioral contract for the mandatory donor PCIe IP override.

The emitted override is executed against Tcl stubs of ``get_ips``,
``set_property``, ``get_property`` and ``generate_target``. Identity,
``CONFIG.Bar*`` and ``CONFIG.MSIx_*`` are mandatory: a missing or ambiguous IP,
a mandatory setter failure, or a mandatory value that does not read back after
all writes must raise a Tcl error before ``generate_target``.
"""

import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

import pytest

project_root = Path(__file__).parent.parent.resolve()
if str(project_root) not in sys.path:
    sys.path.insert(0, str(project_root))

from pcileechfwgenerator.vivado_handling.fifo_donor_patcher import DonorIDs
from pcileechfwgenerator.vivado_handling.pcie_ip_donor_override import (
    DonorPCIeIPConfig,
    generate_pcie_ip_override_tcl,
)

try:
    import tkinter
except ImportError:  # pragma: no cover - depends on the Python build
    tkinter = None

_TCLSH = shutil.which("tclsh")

pytestmark = pytest.mark.skipif(
    tkinter is None and _TCLSH is None, reason="needs a Tcl interpreter"
)


def _capture_donor() -> DonorIDs:
    # Real NVMe donor capture identity (1E4B:1202, subsystem 1E4B:1202). The
    # capture's revision is not recorded in the repo; the contract below does
    # not depend on its value.
    return DonorIDs(
        vendor_id=0x1E4B,
        device_id=0x1202,
        subsystem_vendor_id=0x1E4B,
        subsystem_id=0x1202,
        revision_id=0x00,
    )


def _msix_extra(*, is_64bit: bool = True) -> DonorPCIeIPConfig:
    # Same capture's MSI-X layout: 9 vectors, table 0x3000 / PBA 0x2000 in BAR0.
    return DonorPCIeIPConfig(
        class_code=0x010802,
        msix_enabled=True,
        msix_table_size=9,
        msix_table_bir=0,
        msix_table_offset=0x3000,
        msix_pba_bir=0,
        msix_pba_offset=0x2000,
        aer_enabled=True,
        dsn_value=0,
        bar_aperture_size=0x4000,
        bar_is_memory=True,
        bar_is_64bit=is_64bit,
        bar_prefetchable=False,
        bar_index=0,
    )


_STUBS = r"""
array set ::props {}
set ::set_calls 0
set ::generated 0
proc get_ips {args} {
    set out {}
    foreach ip $::ips {
        if {[string match [lindex $args end] $ip]} { lappend out $ip }
    }
    return $out
}
proc set_property {key value obj} {
    incr ::set_calls
    if {$key in $::fail_keys} {
        error "ERROR: \[IP_Flow 19-3461\] Value '$value' is out of the range for $key"
    }
    if {$key in $::ignore_keys} { return }
    if {[dict exists $::normalize $key]} { set value [dict get $::normalize $key] }
    set ::props($key) $value
    if {[dict exists $::reverts $key]} {
        lassign [dict get $::reverts $key] target stock
        set ::props($target) $stock
    }
}
proc get_property {key obj} {
    if {![info exists ::props($key)]} { error "no property $key on $obj" }
    return $::props($key)
}
proc generate_target {args} { set ::generated 1 }
"""


def _tcl_list(items):
    return "{" + " ".join(items) + "}"


def _tcl_dict(mapping):
    return "{" + " ".join(f"{k} {{{v}}}" for k, v in mapping.items()) + "}"


def _run(
    fragment,
    *,
    ips=("pcie_7x_0",),
    fail_keys=(),
    ignore_keys=(),
    normalize=None,
    reverts=None,
):
    """Run *fragment* against the stubs; return (error or None, state dict)."""
    script = (
        f"set ::ips {_tcl_list(ips)}\n"
        f"set ::fail_keys {_tcl_list(fail_keys)}\n"
        f"set ::ignore_keys {_tcl_list(ignore_keys)}\n"
        f"set ::normalize {_tcl_dict(normalize or {})}\n"
        f"set ::reverts {_tcl_dict(reverts or {})}\n"
        f"{_STUBS}\n"
        "set ::rc [catch {eval $::fragment} ::err]\n"
        "set ::report [list $::rc $::err $::set_calls $::generated]\n"
    )
    if tkinter is not None:
        interp = tkinter.Tcl()
        interp.setvar("::fragment", fragment)
        interp.eval(script)
        rc, err, calls, generated = interp.splitlist(interp.eval("set ::report"))
    else:
        with tempfile.TemporaryDirectory() as tmp:
            frag_path = Path(tmp) / "fragment.tcl"
            frag_path.write_text(fragment)
            driver = (
                "set fh [open [lindex $argv 0]]\n"
                "set ::fragment [read $fh]\n"
                "close $fh\n" + script
                # stderr: the override's own WARNING puts goes to stdout.
                + "puts -nonewline stderr [join $::report \\x1f]\n"
            )
            proc = subprocess.run(  # noqa: S603 - fixed argv, no shell
                [_TCLSH, "/dev/stdin", str(frag_path)],
                input=driver,
                capture_output=True,
                text=True,
                check=True,
            )
        rc, err, calls, generated = proc.stderr.split("\x1f")
    return (err if int(rc) else None), {
        "set_calls": int(calls),
        "generated": int(generated) == 1,
    }


# 2026.1-style read-back spellings for a 64-bit BAR0/1 pair.
_NORMALIZED = {
    "CONFIG.Vendor_ID": "0x1E4B",
    "CONFIG.Revision_ID": "0",
    "CONFIG.MSIx_Enabled": "TRUE",
    "CONFIG.Bar0_64bit": "1",
    "CONFIG.Bar1_Enabled": "False",
    "CONFIG.Bar0_Scale": "kilobytes",
    "CONFIG.MSIx_Table_Offset": "0x3000",
    "CONFIG.MSIx_Table_BIR": "BAR_1:0",
    "CONFIG.MSIx_PBA_BIR": "BAR_1:0",
}


class TestMandatoryOverrideContract:
    def test_applies_and_regenerates_with_normalized_readback(self):
        tcl = generate_pcie_ip_override_tcl(_capture_donor(), extra=_msix_extra())
        err, state = _run(tcl, normalize=_NORMALIZED)
        assert err is None
        assert state["generated"]

    def test_missing_ip_raises_before_any_write(self):
        tcl = generate_pcie_ip_override_tcl(_capture_donor(), extra=_msix_extra())
        err, state = _run(tcl, ips=())
        assert err is not None
        assert "exactly one PCIe IP named pcie_7x_0, found 0" in err
        assert state["set_calls"] == 0
        assert not state["generated"]

    def test_multiple_ips_raise_before_any_write(self):
        tcl = generate_pcie_ip_override_tcl(_capture_donor(), extra=_msix_extra())
        err, state = _run(tcl, ips=("pcie_7x_0", "pcie_7x_0"))
        assert err is not None
        assert "found 2" in err
        assert state["set_calls"] == 0
        assert not state["generated"]

    @pytest.mark.parametrize(
        "key",
        ["CONFIG.Device_ID", "CONFIG.Bar0_Size", "CONFIG.MSIx_Table_Size"],
    )
    def test_mandatory_setter_failure_raises(self, key):
        tcl = generate_pcie_ip_override_tcl(_capture_donor(), extra=_msix_extra())
        err, state = _run(tcl, fail_keys=(key,))
        assert err is not None
        assert "mandatory donor IP override failed" in err
        assert key in err
        assert not state["generated"]

    def test_optional_setter_failure_only_warns(self):
        tcl = generate_pcie_ip_override_tcl(_capture_donor(), extra=_msix_extra())
        err, state = _run(tcl, fail_keys=("CONFIG.DSN_HEX1", "CONFIG.AER_Enabled"))
        assert err is None
        assert state["generated"]

    @pytest.mark.parametrize(
        "key",
        [
            "CONFIG.Subsystem_ID",
            "CONFIG.Bar0_Size",
            "CONFIG.MSIx_PBA_Offset",
            "CONFIG.Class_Code_Base",
            "CONFIG.Class_Code_Sub",
            "CONFIG.Class_Code_Interface",
        ],
    )
    def test_accepted_but_ignored_mandatory_value_raises(self, key):
        tcl = generate_pcie_ip_override_tcl(_capture_donor(), extra=_msix_extra())
        err, state = _run(tcl, ignore_keys=(key,))
        assert err is not None
        assert "read-back mismatch" in err
        assert key in err
        assert not state["generated"]

    def test_optional_write_reverting_mandatory_value_raises(self):
        # AER_Enabled is written after the MSI-X block; a core that resets
        # MSIx_Enabled as a side effect must be caught by the final read-back.
        tcl = generate_pcie_ip_override_tcl(_capture_donor(), extra=_msix_extra())
        err, state = _run(
            tcl,
            reverts={"CONFIG.AER_Enabled": "CONFIG.MSIx_Enabled false"},
        )
        assert err is not None
        assert "CONFIG.MSIx_Enabled (expected true, got false)" in err
        assert not state["generated"]

    def test_pair_form_checks_the_lower_bar(self):
        tcl = generate_pcie_ip_override_tcl(_capture_donor(), extra=_msix_extra())
        err, _ = _run(tcl, normalize={"CONFIG.MSIx_Table_BIR": "BAR_2:1"})
        assert err is not None
        assert "CONFIG.MSIx_Table_BIR" in err

    def test_pair_form_rejected_for_a_32bit_bar(self):
        tcl = generate_pcie_ip_override_tcl(
            _capture_donor(), extra=_msix_extra(is_64bit=False)
        )
        err, _ = _run(tcl, normalize={"CONFIG.MSIx_Table_BIR": "BAR_1:0"})
        assert err is not None
        assert "CONFIG.MSIx_Table_BIR" in err

    def test_numeric_mismatch_is_not_normalized_away(self):
        tcl = generate_pcie_ip_override_tcl(_capture_donor(), extra=_msix_extra())
        err, _ = _run(tcl, normalize={"CONFIG.MSIx_Table_Offset": "0x2000"})
        assert err is not None
        assert "CONFIG.MSIx_Table_Offset" in err


def _cpl_extra(ranges="ABCD", disable_sup=True) -> DonorPCIeIPConfig:
    # The fresh NVMe capture's DevCap2 reports ranges ABCD + disable support.
    from dataclasses import replace

    return replace(
        _msix_extra(),
        cpl_timeout_ranges=ranges,
        cpl_timeout_disable_supported=disable_sup,
    )


class TestCompletionTimeoutContract:
    def test_supported_enum_applies_and_reads_back(self):
        tcl = generate_pcie_ip_override_tcl(_capture_donor(), extra=_cpl_extra())
        err, state = _run(tcl, normalize=_NORMALIZED)
        assert err is None
        assert state["generated"]

    def test_range_setter_rejection_raises(self):
        # Vivado 2026.1 raised IP_Flow 19-3461 for the old "Range_ABCD" and the
        # override previously only warned and regenerated the stock range.
        tcl = generate_pcie_ip_override_tcl(_capture_donor(), extra=_cpl_extra())
        err, state = _run(tcl, fail_keys=("CONFIG.Cpl_Timeout_Range",))
        assert err is not None
        assert "mandatory donor IP override failed" in err
        assert "CONFIG.Cpl_Timeout_Range" in err
        assert not state["generated"]

    @pytest.mark.parametrize(
        "key", ["CONFIG.Cpl_Timeout_Range", "CONFIG.Cpl_Timeout_Disable_Sup"]
    )
    def test_ignored_write_fails_readback(self, key):
        # Mirrors IP_Flow 19-3374: a disabled parameter's write is "ignored"
        # with only a warning, so set_property succeeds but nothing changes.
        tcl = generate_pcie_ip_override_tcl(_capture_donor(), extra=_cpl_extra())
        err, state = _run(tcl, ignore_keys=(key,))
        assert err is not None
        assert "read-back" in err
        assert key in err
        assert not state["generated"]

    def test_readback_value_mismatch_raises(self):
        tcl = generate_pcie_ip_override_tcl(_capture_donor(), extra=_cpl_extra())
        err, state = _run(
            tcl, normalize={"CONFIG.Cpl_Timeout_Range": "Ranges_B,_C_&_D"}
        )
        assert err is not None
        assert (
            "CONFIG.Cpl_Timeout_Range (expected Ranges_A,_B,_C_&_D, "
            "got Ranges_B,_C_&_D)" in err
        )
        assert not state["generated"]

    def test_later_write_reverting_range_raises(self):
        # DSN is written after the completion-timeout block; a core that
        # restores its previous range as a side effect must be caught.
        tcl = generate_pcie_ip_override_tcl(_capture_donor(), extra=_cpl_extra())
        err, state = _run(
            tcl,
            reverts={"CONFIG.DSN_HEX1": "CONFIG.Cpl_Timeout_Range Range_B"},
        )
        assert err is not None
        assert "CONFIG.Cpl_Timeout_Range (expected" in err
        assert not state["generated"]

    def test_disable_sup_false_also_verified(self):
        tcl = generate_pcie_ip_override_tcl(
            _capture_donor(), extra=_cpl_extra(ranges="BC", disable_sup=False)
        )
        assert "CONFIG.Cpl_Timeout_Range Ranges_B_&_C" in tcl
        err, state = _run(tcl, normalize={"CONFIG.Cpl_Timeout_Disable_Sup": "1"})
        assert err is not None
        assert "CONFIG.Cpl_Timeout_Disable_Sup (expected false, got 1)" in err
        assert not state["generated"]


@pytest.mark.parametrize(
    "field,value",
    [
        ("class_code", None),
        ("class_code", 1 << 24),
        ("bar_aperture_size", None),
        ("bar_aperture_size", 1024),
    ],
)
def test_production_config_cannot_omit_critical_fields(field, value):
    from dataclasses import replace

    from pcileechfwgenerator.vivado_handling.pcie_ip_donor_override import (
        PcieIpOverrideError,
        validate_donor_ip_build_config,
    )

    extra = replace(_msix_extra(), **{field: value})
    with pytest.raises(PcieIpOverrideError):
        validate_donor_ip_build_config(extra)
