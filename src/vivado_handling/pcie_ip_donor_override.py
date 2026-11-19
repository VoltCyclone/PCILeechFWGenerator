"""Override Vivado PCIe IP CONFIG with donor IDs and capabilities (issue #593).

The board ships ``pcie_7x_0.xci`` with Xilinx default values (Vendor_ID=10EE,
Device_ID=0666, Class_Code_Base=02, MSIx_Enabled=false, AER_Enabled=false,
LINK_CAP_MAX_LINK_WIDTH=1, etc.). Without an override the host sees those
defaults during PCIe enumeration -- even when the cfgspace shadow has the
donor's real bytes -- because the hard PCIe block emits IP-baked values
during the window between LTSSM up and the first cfg-read.

This module emits a TCL fragment that ``set_property -dict`` the donor's
collected values onto the staged IP, then re-runs ``generate_target`` so the
change reaches the synthesizable HDL. The unified Stage 3 script
(``build_all.tcl``) verifies its hash against the Stage 2 manifest and sources
it. Board project scripts are not staged.

Coverage:

- closed end-to-end: Class_Code (A4), LinkCap (A7), MPS (A8),
  AER (C1), DSN (C2), ARI (C3), Cpl_Timeout (D2), served BAR aperture (A5)
- MSI-X (A6) is advertised only with a validated layout and generated subsystem.
- A5/C scope: the primary register BAR and optionally a separate MSI-X BAR are
  mirrored at their real donor indices, with exact independent apertures.
  Other slots are disabled. Additional register BARs and split table/PBA BARs
  remain outside the supported served set.
- not yet implemented: UltraScale+ IP property names. The emitter targets the
  Xilinx 7-series IP property schema only.

Note on DSN: sv_context_builder.py also writes ``device_serial_number_int``
into an ``enhanced_context`` dict (separate from template_context) used for
SystemVerilog rendering. Our override pipeline uses the producer added by
donor_capability_extractor.py in Step 3, which writes directly into
template_context where this module's extractor reads. The two paths are
independent and write the same value computed from the same donor cap.
"""

from __future__ import annotations

from dataclasses import dataclass, replace
from pathlib import Path
from typing import Optional, cast

from pcileechfwgenerator.device_clone.msix_layout import MsixLayoutError
from pcileechfwgenerator.device_clone.msix_subsystem import (
    MsixSubsystemPlan,
    plan_msix_subsystem,
)
from pcileechfwgenerator.device_clone.requester_tracking import RequesterTrackingPolicy
from pcileechfwgenerator.device_clone.served_bar import (
    ServedBar,
    served_bars_from_config,
)
from pcileechfwgenerator.log_config import get_logger
from pcileechfwgenerator.string_utils import log_warning_safe, safe_format
from pcileechfwgenerator.utils.validators import get_bar_size_validator

from .fifo_donor_patcher import DonorIDs, _coerce_int
from .requester_qualification import startup_qualification_guard_tcl

logger = get_logger(__name__)


class PcieIpOverrideError(RuntimeError):
    """Raised when the donor IP override cannot be produced for the build."""


class CplTimeoutRangeUnrepresentableError(ValueError):
    """The donor's completion-timeout range mask has no 7-series IP enum.

    Distinct from a malformed token: the donor value is genuine (or a reserved
    encoding the donor actually reports), but the core offers no setting that
    advertises it, so emitting any enum would misstate the donor.
    """


@dataclass(frozen=True)
class DonorPCIeIPConfig:
    """Optional donor PCIe IP CONFIG values beyond the five identification IDs.

    Every field is optional. A ``None`` value means "the donor profile did not
    expose this; leave the Xilinx IP default in place." That keeps the override
    safe when the donor profile is partial (e.g. an older capture without DSN).

    Field labels (A4, A5, ...) match the coverage list in the module docstring.
    """

    # A4 — Class Code (24-bit, packed base/sub/interface)
    class_code: Optional[int] = None

    # A8 — Max Payload Size (DevCap.MPS in bytes: 128/256/512/1024/2048/4096)
    max_payload_size: Optional[int] = None

    # A7 — LinkCap negotiated ceiling
    link_speed: Optional[int] = None  # PCIe gen: 1, 2, 3, 4, 5 (mapped to spec)
    link_speed_code: Optional[int] = None  # Spec-encoded value 1/2/4/8/16 (direct)
    link_width: Optional[int] = None  # Lane count 1/2/4/8/16

    # A6 — MSI-X capability advertisement, independent of capture-time Enable
    msix_enabled: Optional[bool] = None
    msix_table_size: Optional[int] = None  # number of vectors (1..2048)
    msix_table_bir: Optional[int] = None  # 0..5
    msix_table_offset: Optional[int] = None
    msix_pba_bir: Optional[int] = None  # 0..5
    msix_pba_offset: Optional[int] = None

    # C1 / C3 — Capability enables
    aer_enabled: Optional[bool] = None
    ari_forwarding_supported: Optional[bool] = None

    # D2 — Completion-timeout policy (DevCap2 bits 3:0, as a range-letter token).
    # Representable: "A", "B", "AB", "BC", "ABC", "BCD", "ABCD" (see
    # _CPL_TIMEOUT_RANGE_ENUMS). "none" (0000b) and the reserved "C"/"D"
    # encodings have no Vivado enum and are rejected, never approximated.
    cpl_timeout_ranges: Optional[str] = None
    cpl_timeout_disable_supported: Optional[bool] = None
    extended_tag_supported: Optional[bool] = None

    # C2 — Device Serial Number (64-bit; emitted as two 32-bit halves into the IP)
    dsn_value: Optional[int] = None

    # A5 — served BAR aperture, presented as config-space BAR0 (the only window
    # the PCILeech controller services). Setting bar_aperture_size enables the
    # block; the rest default sensibly.
    bar_aperture_size: Optional[int] = None  # bytes, power of 2
    bar_is_memory: Optional[bool] = None  # True=Memory (default), False=IO
    bar_is_64bit: Optional[bool] = None  # memory only; consumes the next slot
    bar_prefetchable: Optional[bool] = None  # memory only
    bar_index: Optional[int] = None  # donor PCI BAR index N the device serves
    # Explicit register/MSI-X served set. Scalars above remain the register BAR
    # compatibility view; empty keeps the historical single-BAR path.
    served_bars: tuple[ServedBar, ...] = ()


_OVERRIDE_FILENAME = "pcileech_donor_ip_overrides.tcl"


def generate_pcie_ip_override_tcl(
    donor: DonorIDs,
    *,
    ip_name: str = "pcie_7x_0",
    extra: "DonorPCIeIPConfig | None" = None,
    requester_policy: Optional[RequesterTrackingPolicy] = None,
) -> str:
    """Render the donor-ID CONFIG override as a Vivado TCL string.

    Exactly one IP must match ``ip_name``; a missing or ambiguous IP raises a
    Tcl error instead of building with stock Xilinx values. Optional ``extra``
    contributes additional ``CONFIG.<key> <value>`` lines for fields the
    donor profile exposed (class code, MPS, MSI-X, LinkCap, AER, ARI,
    cpl-timeout, DSN — see ``DonorPCIeIPConfig``).

    Identity, ``CONFIG.Bar*``, ``CONFIG.MSIx_*`` and ``CONFIG.Cpl_Timeout_*``
    are mandatory: a setter failure raises, and after every write their
    effective values are read back with ``get_property`` so an ignored or
    later-reverted value also raises. Other fields only warn when the core
    does not expose them.

    A source-bound qualified requester policy may establish the donor's final
    Disable Supported=true through reset-held DRP when IP-initial readback is
    false. Only that mismatch is delegated; setters and all other readbacks stay
    mandatory. The exact live target and selected source hashes are checked.
    """
    # The 7-series PCIe core stores these ID fields as bare hexadecimal strings
    # (the stock .xci holds "10EE"/"0666", and Vivado 2026.1 rejects a 0x
    # prefix: "Entered invalid Hexadecimal value 0x1E4B"). Emit bare uppercase
    # hex, matching the GUI and the other hex fields (Class_Code_*, DSN_*).
    #
    # Identity-critical fields: if any of these fail to apply, the IP would
    # regenerate with the stock Xilinx identity (10EE:0666). That is the
    # placeholder-donor-ID anti-pattern the project forbids, so a failure here
    # must abort the build rather than warn — the rest of the fields may be
    # skipped (see below), but identity never silently falls back.
    id_lines: list[str] = [
        f"CONFIG.Vendor_ID {donor.vendor_id:04X}",
        f"CONFIG.Device_ID {donor.device_id:04X}",
        f"CONFIG.Subsystem_Vendor_ID {donor.subsystem_vendor_id:04X}",
        f"CONFIG.Subsystem_ID {donor.subsystem_id:04X}",
        f"CONFIG.Revision_ID {donor.revision_id:02X}",
    ]
    extra_lines = _format_extra_config_lines(extra) if extra is not None else []

    # Apply each property individually under catch rather than one atomic
    # set_property -dict: the 7-series core renames/removes some parameters
    # between Vivado versions (e.g. 2026.1 drops DSN_HEX1/2 and
    # LINK_CAP_MAX_LINK_SPEED/WIDTH), and an atomic dict aborts the whole
    # override — including the critical donor IDs — on the first unknown key.
    # The optional block only warns about parameters this core version does not
    # expose; the identity block above aborts.
    id_block = "\n".join(f"        {line}" for line in id_lines)
    extra_block = "\n".join(f"        {line}" for line in extra_lines)
    check_block = "\n".join(
        f"dict set _pcileech_required {key} {kind}"
        for key, kind in _mandatory_checks(id_lines + extra_lines, extra)
    )
    mismatch = (
        "        lappend _pcileech_mismatch "
        '"$_pcileech_k (expected $_pcileech_v, got $_pcileech_actual)"\n'
    )
    if requester_policy is not None:
        requester_policy = RequesterTrackingPolicy.model_validate(
            requester_policy.model_dump()
        )
        qualification = requester_policy.startup_qualification
        if qualification is not None:
            if extra is None or (
                extra.cpl_timeout_ranges != requester_policy.cpl_timeout_ranges
                or extra.cpl_timeout_disable_supported
                != requester_policy.cpl_timeout_disable_supported
                or extra.extended_tag_supported
                != requester_policy.extended_tag_supported
            ):
                raise ValueError(
                    "Qualified requester policy disagrees with donor override"
                )
            guard = startup_qualification_guard_tcl(
                qualification, ip_variable="$_pcileech_ip"
            )
            mismatch = (
                "        if {$_pcileech_k eq {CONFIG.Cpl_Timeout_Disable_Sup} && "
                "[_pcileech_value_matches bool true $_pcileech_v] && "
                "[_pcileech_value_matches bool false $_pcileech_actual]} {\n"
                + guard
                + "        } else {\n"
                + mismatch
                + "        }\n"
            )

    return (
        "# === PCILeechFWGenerator donor-ID override (issue #593) ===\n"
        f"{_VALUE_MATCH_PROC}"
        f"set _pcileech_ips [get_ips -quiet {ip_name}]\n"
        "if {[llength $_pcileech_ips] != 1} {\n"
        f'    error "PCILeechFWGenerator: expected exactly one PCIe IP named '
        f"{ip_name}, found [llength $_pcileech_ips] — refusing to build "
        'firmware without the donor IP override"\n'
        "}\n"
        "set _pcileech_ip [lindex $_pcileech_ips 0]\n"
        "# Mandatory keys and how their read-back value is compared.\n"
        "set _pcileech_required [dict create]\n"
        f"{check_block}\n"
        "set _pcileech_expected [dict create]\n"
        "set _pcileech_failed {}\n"
        "set _pcileech_id_failed {}\n"
        "# Identity first, then the donor fields in emission order. Mandatory\n"
        "# setter failures abort; optional / version-variant fields only warn.\n"
        "foreach {_pcileech_k _pcileech_v} {\n"
        f"{id_block}\n"
        f"{extra_block}\n"
        "} {\n"
        "    dict set _pcileech_expected $_pcileech_k $_pcileech_v\n"
        "    if {[catch {set_property $_pcileech_k $_pcileech_v "
        "$_pcileech_ip} _pcileech_err]} {\n"
        "        if {[dict exists $_pcileech_required $_pcileech_k]} {\n"
        '            lappend _pcileech_id_failed "$_pcileech_k ($_pcileech_err)"\n'
        "        } else {\n"
        '            lappend _pcileech_failed "$_pcileech_k ($_pcileech_err)"\n'
        "        }\n"
        "    }\n"
        "}\n"
        "if {[llength $_pcileech_failed] > 0} {\n"
        '    puts "WARNING: PCILeechFWGenerator donor override skipped '
        "[llength $_pcileech_failed] unsupported IP parameter(s): "
        '[join $_pcileech_failed {; }]"\n'
        "}\n"
        "if {[llength $_pcileech_id_failed] > 0} {\n"
        '    error "PCILeechFWGenerator: mandatory donor IP override failed on '
        "[join $_pcileech_id_failed {; }] — refusing to build firmware with "
        'stock Xilinx IDs or layout"\n'
        "}\n"
        "# Read back after ALL writes: catches a value the core accepted but\n"
        "# ignored, or one a later optional write reverted.\n"
        "set _pcileech_mismatch {}\n"
        "dict for {_pcileech_k _pcileech_kind} $_pcileech_required {\n"
        "    set _pcileech_v [dict get $_pcileech_expected $_pcileech_k]\n"
        "    if {[catch {get_property $_pcileech_k $_pcileech_ip} "
        "_pcileech_actual]} {\n"
        "        lappend _pcileech_mismatch "
        '"$_pcileech_k (read-back failed: $_pcileech_actual)"\n'
        "    } elseif {![_pcileech_value_matches $_pcileech_kind $_pcileech_v "
        "$_pcileech_actual]} {\n"
        f"{mismatch}"
        "    }\n"
        "}\n"
        "if {[llength $_pcileech_mismatch] > 0} {\n"
        '    error "PCILeechFWGenerator: mandatory donor IP read-back mismatch '
        "on [join $_pcileech_mismatch {; }] — refusing to build firmware with "
        'stock Xilinx IDs or layout"\n'
        "}\n"
        f"generate_target -force all [get_ips {ip_name}]\n"
    )


_IDENTITY_KEYS = frozenset(
    {
        "CONFIG.Vendor_ID",
        "CONFIG.Device_ID",
        "CONFIG.Subsystem_Vendor_ID",
        "CONFIG.Subsystem_ID",
        "CONFIG.Revision_ID",
        "CONFIG.Class_Code_Base",
        "CONFIG.Class_Code_Sub",
        "CONFIG.Class_Code_Interface",
    }
)
_MSIX_BIR_KEYS = frozenset({"CONFIG.MSIx_Table_BIR", "CONFIG.MSIx_PBA_BIR"})


def validate_donor_ip_build_config(extra: "DonorPCIeIPConfig | None") -> None:
    """Reject incomplete production configuration before emitting an override.

    Low-level rendering supports partial fragments for tooling, but a firmware
    build must never interpret absent class/BAR values as stock defaults.
    """
    if extra is None or extra.class_code is None:
        raise PcieIpOverrideError("Donor class code is missing or invalid")
    if not 0 <= extra.class_code <= 0xFFFFFF:
        raise PcieIpOverrideError("Donor class code is outside its 24-bit range")
    if extra.bar_aperture_size is None:
        raise PcieIpOverrideError(
            "Served BAR is missing or has an unsupported aperture"
        )
    reason = _bar_aperture_error(
        extra.bar_aperture_size, extra.bar_is_memory is not False
    )
    if reason:
        raise PcieIpOverrideError(reason)


def _is_mandatory_key(key: str) -> bool:
    return (
        key in _IDENTITY_KEYS
        or key.startswith("CONFIG.Bar")
        or key.startswith("CONFIG.MSIx_")
        # Emitted only when the donor reports a representable policy, so a
        # rejected or ignored write is a fidelity failure, not a version skew.
        or key.startswith("CONFIG.Cpl_Timeout_")
        or key == "CONFIG.Extended_Tag_Field"
    )


def _mandatory_checks(
    lines: "list[str]", extra: "DonorPCIeIPConfig | None"
) -> "list[tuple[str, str]]":
    """Return ``(key, kind)`` for each mandatory key, in first-write order.

    ``kind`` selects the read-back comparison: ``hex`` (bare-hex IDs), ``bool``,
    ``dec`` (integers), ``bar`` / ``bar64`` (MSI-X BIR tokens) or ``token``.
    """
    pair_lower = None
    if extra is not None and extra.bar_aperture_size is not None:
        is_memory = extra.bar_is_memory is None or bool(extra.bar_is_memory)
        if is_memory and extra.bar_is_64bit:
            pair_lower = 0 if extra.bar_index is None else extra.bar_index

    checks: "dict[str, str]" = {}
    for line in lines:
        key, value = line.split(None, 1)
        if not _is_mandatory_key(key) or key in checks:
            continue
        if key in _IDENTITY_KEYS:
            kind = "hex"
        elif key in _MSIX_BIR_KEYS:
            # A 64-bit pair can read back as BAR_<N+1>:<N>; only the lower BAR
            # of that pair is verified.
            kind = "bar64" if value == f"BAR_{pair_lower}" else "bar"
        elif value in ("true", "false"):
            kind = "bool"
        elif value.isdigit():
            kind = "dec"
        else:
            kind = "token"
        checks[key] = kind
    return list(checks.items())


# Tcl comparison for mandatory read-back. Accepts case/spelling-normalized
# booleans, numerically equal values (with or without 0x / leading zeros), and
# the 64-bit MSI-X BIR pair form BAR_<hi>:<lo> whose lower BAR is expected.
_VALUE_MATCH_PROC = r"""proc _pcileech_parse_int {text radix} {
    set text [string trim $text]
    if {[regexp -nocase {^0x([0-9a-f]+)$} $text -> digits]} {
        return [scan $digits %x]
    }
    if {$radix eq "hex" && [regexp -nocase {^[0-9a-f]+$} $text]} {
        return [scan $text %x]
    }
    if {[regexp {^[0-9]+$} $text]} {
        return [scan $text %d]
    }
    return {}
}
proc _pcileech_value_matches {kind expected actual} {
    set actual [string trim $actual]
    if {[string equal -nocase $expected $actual]} {
        return 1
    }
    switch -- $kind {
        hex - dec {
            set e [_pcileech_parse_int $expected $kind]
            set a [_pcileech_parse_int $actual $kind]
            return [expr {$e ne {} && $a ne {} && $e == $a}]
        }
        bool {
            if {![string is boolean -strict $actual]} {
                return 0
            }
            return [expr {[string is true $expected] == [string is true $actual]}]
        }
        bar64 {
            if {[regexp -nocase {^BAR_([0-9]+)$} $expected -> want] &&
                [regexp -nocase {^BAR_[0-9]+:([0-9]+)$} $actual -> lower]} {
                return [expr {[scan $lower %d] == [scan $want %d]}]
            }
            return 0
        }
        default {
            return 0
        }
    }
}
"""


_XILINX_BAR_SCALES = (
    ("Gigabytes", 1024 * 1024 * 1024),
    ("Megabytes", 1024 * 1024),
    ("Kilobytes", 1024),
    ("Bytes", 1),
)


def _bytes_to_xilinx_scale_size(size_bytes: int) -> "tuple[str, int]":
    """Map a byte size to the 7-series IP's ``(Scale, Size)`` token pair.

    Picks the largest scale that divides ``size_bytes`` evenly so the numeric
    multiplier stays below 1024 — the same Scale/Size split the Vivado IP GUI
    uses (e.g. 2 MiB is ``Megabytes 2``, never ``Kilobytes 2048``).
    """
    for scale, unit in _XILINX_BAR_SCALES:
        if size_bytes % unit == 0:
            return (scale, size_bytes // unit)
    # Unreachable: the ("Bytes", 1) tier divides every integer.
    raise ValueError(f"cannot encode BAR size {size_bytes} bytes")


_MPS_TOKENS = {
    128: "128_bytes",
    256: "256_bytes",
    512: "512_bytes",
    1024: "1024_bytes",
    2048: "2048_bytes",
    4096: "4096_bytes",
}

_LINK_SPEED_ENCODING = {1: 1, 2: 2, 3: 4, 4: 8, 5: 16}
_VALID_LINK_WIDTHS = (1, 2, 4, 8, 16)

# DevCap2 Completion Timeout Ranges Supported (bits 3:0; bit0=A .. bit3=D) ->
# the exact Cpl_Timeout_Range enum the 7-series core accepts. Spellings are
# taken verbatim from Vivado 2026.1's own rejection message (IP_Flow 19-3461):
#   Range_A, Range_B, Ranges_A_&_B, Ranges_B_&_C, Ranges_A,_B_&_C,
#   Ranges_B,_C_&_D, Ranges_A,_B,_C_&_D
# These are exactly the non-zero, non-reserved encodings the PCIe Base Spec
# defines for this field.
_CPL_TIMEOUT_RANGE_ENUMS = {
    "A": "Range_A",  # 0001b
    "B": "Range_B",  # 0010b
    "AB": "Ranges_A_&_B",  # 0011b
    "BC": "Ranges_B_&_C",  # 0110b
    "ABC": "Ranges_A,_B_&_C",  # 0111b
    "BCD": "Ranges_B,_C_&_D",  # 1110b
    "ABCD": "Ranges_A,_B,_C_&_D",  # 1111b
}
# Genuine donor encodings the core has no enum for. Emitting any enum here
# would advertise ranges the donor does not report.
_CPL_TIMEOUT_RANGE_UNREPRESENTABLE = {
    "none": "0000b (ranges not supported) has no Cpl_Timeout_Range enum",
    "C": "0100b is a reserved DevCap2 encoding with no Cpl_Timeout_Range enum",
    "D": "1000b is a reserved DevCap2 encoding with no Cpl_Timeout_Range enum",
    **{
        f"reserved:{bits:04b}": (
            f"{bits:04b} is a reserved DevCap2 encoding with no Cpl_Timeout_Range enum"
        )
        for bits in (5, 9, 10, 11, 12, 13)
    },
}


def cpl_timeout_range_enum(token: str) -> str:
    """Map a donor range token to its Vivado ``Cpl_Timeout_Range`` enum.

    Raises ``CplTimeoutRangeUnrepresentableError`` for a genuine donor encoding
    the core cannot advertise, and plain ``ValueError`` for a malformed token.
    """
    enum = _CPL_TIMEOUT_RANGE_ENUMS.get(token)
    if enum is not None:
        return enum
    reason = _CPL_TIMEOUT_RANGE_UNREPRESENTABLE.get(token)
    if reason is not None:
        raise CplTimeoutRangeUnrepresentableError(
            f"cpl_timeout_ranges {token!r} cannot be represented on the "
            f"7-series PCIe IP: {reason}"
        )
    raise ValueError(
        f"cpl_timeout_ranges {token!r} is not a PCIe completion-timeout range "
        f"token (supported: {sorted(_CPL_TIMEOUT_RANGE_ENUMS)}; "
        f"unrepresentable: {sorted(_CPL_TIMEOUT_RANGE_UNREPRESENTABLE)})"
    )


def _tcl_bool(value: bool) -> str:
    return "true" if value else "false"


def _format_extra_config_lines(extra: "DonorPCIeIPConfig") -> list[str]:
    """Return one ``CONFIG.<key> <value>`` string per non-None field in *extra*."""
    out: list[str] = []

    if extra.class_code is not None:
        if not (0 <= extra.class_code <= 0xFFFFFF):
            raise ValueError(
                f"class_code 0x{extra.class_code:X} does not fit in 24 bits"
            )
        base = (extra.class_code >> 16) & 0xFF
        sub = (extra.class_code >> 8) & 0xFF
        interface = extra.class_code & 0xFF
        out.append(f"CONFIG.Class_Code_Base {base:02X}")
        out.append(f"CONFIG.Class_Code_Sub {sub:02X}")
        out.append(f"CONFIG.Class_Code_Interface {interface:02X}")

    if extra.max_payload_size is not None:
        token = _MPS_TOKENS.get(extra.max_payload_size)
        if token is None:
            raise ValueError(
                f"max_payload_size {extra.max_payload_size} is not one of "
                f"{sorted(_MPS_TOKENS)}"
            )
        out.append(f"CONFIG.Max_Payload_Size {token}")

    if extra.link_speed is not None and extra.link_speed_code is not None:
        raise ValueError(
            "set exactly one of link_speed (generation) or "
            "link_speed_code (spec-encoded), not both"
        )
    if extra.link_speed is not None:
        encoded = _LINK_SPEED_ENCODING.get(extra.link_speed)
        if encoded is None:
            raise ValueError(
                f"link_speed {extra.link_speed} is not a valid PCIe generation "
                f"(supported: {sorted(_LINK_SPEED_ENCODING)})"
            )
        out.append(f"CONFIG.LINK_CAP_MAX_LINK_SPEED {encoded}")
    elif extra.link_speed_code is not None:
        if extra.link_speed_code not in _LINK_SPEED_ENCODING.values():
            raise ValueError(
                f"link_speed_code {extra.link_speed_code} is not a valid PCIe "
                f"spec encoding (supported: {sorted(_LINK_SPEED_ENCODING.values())})"
            )
        out.append(f"CONFIG.LINK_CAP_MAX_LINK_SPEED {extra.link_speed_code}")

    if extra.link_width is not None:
        if extra.link_width not in _VALID_LINK_WIDTHS:
            raise ValueError(
                f"link_width {extra.link_width} is not one of {_VALID_LINK_WIDTHS}"
            )
        out.append(f"CONFIG.LINK_CAP_MAX_LINK_WIDTH {extra.link_width}")

    # BAR apertures before the MSI-X layout: the core only accepts
    # MSIx_Table_BIR/MSIx_PBA_BIR values naming an enabled BAR, so a table in
    # BAR3 must follow Bar3_Enabled (IP_Flow 19-3461 otherwise).
    out.extend(_format_bar_aperture_lines(extra))

    if extra.msix_enabled is not None:
        if not extra.msix_enabled:
            out.append("CONFIG.MSIx_Enabled false")
        else:
            # When enabled, the full layout must be present — partial state
            # makes the IP synthesize with default offsets and the donor's
            # advertised offsets disagree → driver mmap mismatch.
            required = {
                "msix_table_size": extra.msix_table_size,
                "msix_table_bir": extra.msix_table_bir,
                "msix_table_offset": extra.msix_table_offset,
                "msix_pba_bir": extra.msix_pba_bir,
                "msix_pba_offset": extra.msix_pba_offset,
            }
            missing = [name for name, value in required.items() if value is None]
            if missing:
                raise ValueError(
                    "msix_enabled=True requires the full MSI-X layout; missing: "
                    + ", ".join(missing)
                )
            # `missing` above proves every required field is set.
            table_size = cast(int, extra.msix_table_size)
            if table_size < 1 or table_size > 2048:
                raise ValueError(
                    f"msix_table_size {table_size} outside spec range 1..2048"
                )
            for label, bir in (
                ("msix_table_bir", cast(int, extra.msix_table_bir)),
                ("msix_pba_bir", cast(int, extra.msix_pba_bir)),
            ):
                if not (0 <= bir <= 5):
                    raise ValueError(f"{label} {bir} is not a valid BAR index (0..5)")

            out.append("CONFIG.MSIx_Enabled true")
            out.append(f"CONFIG.MSIx_Table_Size {extra.msix_table_size}")
            out.append(f"CONFIG.MSIx_Table_BIR BAR_{extra.msix_table_bir}")
            out.append(f"CONFIG.MSIx_Table_Offset {extra.msix_table_offset}")
            out.append(f"CONFIG.MSIx_PBA_BIR BAR_{extra.msix_pba_bir}")
            out.append(f"CONFIG.MSIx_PBA_Offset {extra.msix_pba_offset}")

    if extra.aer_enabled is not None:
        out.append(f"CONFIG.AER_Enabled {_tcl_bool(extra.aer_enabled)}")

    if extra.ari_forwarding_supported is not None:
        out.append(
            f"CONFIG.ARI_Forwarding_Supported {_tcl_bool(extra.ari_forwarding_supported)}"
        )

    if extra.cpl_timeout_ranges is not None:
        enum = cpl_timeout_range_enum(extra.cpl_timeout_ranges)
        out.append(f"CONFIG.Cpl_Timeout_Range {enum}")

    if extra.cpl_timeout_disable_supported is not None:
        out.append(
            f"CONFIG.Cpl_Timeout_Disable_Sup {_tcl_bool(extra.cpl_timeout_disable_supported)}"
        )

    if extra.extended_tag_supported is not None:
        out.append(
            f"CONFIG.Extended_Tag_Field {_tcl_bool(extra.extended_tag_supported)}"
        )

    if extra.dsn_value is not None:
        if not (0 <= extra.dsn_value <= 0xFFFFFFFFFFFFFFFF):
            raise ValueError(f"dsn_value 0x{extra.dsn_value:X} does not fit in 64 bits")
        lower = extra.dsn_value & 0xFFFFFFFF
        upper = (extra.dsn_value >> 32) & 0xFFFFFFFF
        out.append(f"CONFIG.DSN_HEX1 {lower:08X}")
        out.append(f"CONFIG.DSN_HEX2 {upper:08X}")

    return out


def _bar_aperture_error(size: int, is_memory: bool) -> "str | None":
    """Return an error string if *size* can't be a valid BAR aperture, else None.

    Shared by the emitter (which raises on explicit misuse) and the extractor
    (which skips defensively, leaving the IP default rather than failing a build).
    """
    bar_type = "memory" if is_memory else "io"
    result = get_bar_size_validator(bar_type=bar_type).validate(size)
    if not result.valid:
        return (
            f"BAR aperture size {size} is invalid for a {bar_type} BAR: "
            + "; ".join(result.errors)
        )
    # Sub-page memory apertures wrap in the hard block; PCILeech's own guidance
    # is to never go below 4 KiB for the served memory BAR.
    if is_memory and size < 4096:
        return f"BAR aperture size {size} below the 4 KiB floor for a memory BAR"
    return None


_NUM_STANDARD_BARS = 6  # BAR0..BAR5


def _format_bar_aperture_lines(extra: "DonorPCIeIPConfig") -> list[str]:
    """Emit exact apertures for the register BAR and optional MSI-X BAR.

    The device register window is served at the donor's REAL PCI BAR index N
    (``bar_index``) — the controller gates its impl on rd_req_bar[N]/wr_bar[N]
    to match. IO BARs cannot be 64-bit or prefetchable, so those flags are
    coerced off. Every other standard BAR index is force-disabled (including the
    .xci default BAR0 when N != 0) so the host enumerates exactly the one served
    window — no phantom BARs, and no BAR the controller answers with ``none``
    (which would hang reads). A 64-bit BAR consumes index N+1, which falls into
    the disabled set automatically.
    """
    if extra.served_bars:
        served = served_bars_from_config({"served_bars": extra.served_bars}) or ()
        lines: list[str] = []
        for bar in served:
            single = replace(
                extra,
                served_bars=(),
                bar_index=bar.index,
                bar_aperture_size=bar.size,
                bar_is_memory=bar.is_memory,
                bar_is_64bit=bar.is_64bit,
                bar_prefetchable=bar.prefetchable,
            )
            lines.extend(
                line
                for line in _format_bar_aperture_lines(single)
                if not line.endswith("_Enabled false")
            )
        enabled = {bar.index for bar in served}
        lines.extend(
            f"CONFIG.Bar{k}_Enabled false"
            for k in range(_NUM_STANDARD_BARS)
            if k not in enabled
        )
        return lines
    if extra.bar_aperture_size is None:
        return []

    size = extra.bar_aperture_size
    is_memory = True if extra.bar_is_memory is None else bool(extra.bar_is_memory)

    error = _bar_aperture_error(size, is_memory)
    if error is not None:
        raise ValueError(error)

    n = 0 if extra.bar_index is None else extra.bar_index
    if not (0 <= n < _NUM_STANDARD_BARS):
        raise ValueError(f"bar_index {n} is not a standard BAR index (0..5)")

    is_64bit = bool(extra.bar_is_64bit) and is_memory
    if is_64bit and n == _NUM_STANDARD_BARS - 1:
        # The upper half would land on BAR6 (Expansion ROM), not a standard
        # BAR — electrically invalid PCI.
        raise ValueError(
            f"bar_index {n} cannot be 64-bit: the upper-half partner would be "
            "BAR6 (Expansion ROM), not a standard BAR"
        )
    prefetchable = bool(extra.bar_prefetchable) and is_memory
    scale, multiplier = _bytes_to_xilinx_scale_size(size)

    lines = [
        f"CONFIG.Bar{n}_Enabled true",
        f"CONFIG.Bar{n}_Type {'Memory' if is_memory else 'IO'}",
        f"CONFIG.Bar{n}_64bit {_tcl_bool(is_64bit)}",
        f"CONFIG.Bar{n}_Prefetchable {_tcl_bool(prefetchable)}",
        f"CONFIG.Bar{n}_Scale {scale}",
        f"CONFIG.Bar{n}_Size {multiplier}",
    ]
    # Disable every other standard BAR (incl. the 64-bit partner at N+1).
    lines.extend(
        f"CONFIG.Bar{k}_Enabled false" for k in range(_NUM_STANDARD_BARS) if k != n
    )
    return lines


def apply_pcie_ip_donor_override(
    staging_dir: Path,
    donor: DonorIDs,
    *,
    ip_name: str = "pcie_7x_0",
    extra: "DonorPCIeIPConfig | None" = None,
    requester_policy: Optional[RequesterTrackingPolicy] = None,
) -> dict:
    """Write the override TCL into the Stage 2 output.

    Returns a summary dict with ``override_path``, the emitted override TCL.
    """
    override_path = Path(staging_dir) / _OVERRIDE_FILENAME
    override_path.write_text(
        generate_pcie_ip_override_tcl(
            donor, ip_name=ip_name, extra=extra, requester_policy=requester_policy
        )
    )
    return {"override_path": override_path}


def _coerce_bool(value) -> Optional[bool]:
    """Best-effort bool coercion: accept True/False/1/0/"true"/"false"."""
    if value is None:
        return None
    if isinstance(value, bool):
        return value
    if isinstance(value, int):
        return bool(value)
    if isinstance(value, str):
        s = value.strip().lower()
        if s in ("true", "1", "yes"):
            return True
        if s in ("false", "0", "no"):
            return False
    return None


def donor_pcie_ip_config_from_result(result: dict) -> "DonorPCIeIPConfig":
    """Build a DonorPCIeIPConfig from the build's ``result`` dict.

    Missing optional fields leave IP defaults unchanged. MSI-X is different:
    a present capability is explicitly disabled unless the generation-time
    decision (``template_context["msix_subsystem"]``, see
    ``device_clone.msix_subsystem``) generated the subsystem. Capture-time
    Enable is runtime state.
    """
    template_context = (result or {}).get("template_context") or {}
    device_config = template_context.get("device_config") or {}

    class_code = _coerce_int(device_config.get("class_code"))
    # Canonical producer path: template_context["pcileech_config"]["max_payload_size"]
    # (see src/device_clone/pcileech_context.py:2323). Fall back to top-level for
    # legacy callers that pre-populate it directly.
    pcileech_config = template_context.get("pcileech_config") or {}
    max_payload_size = _coerce_int(
        pcileech_config.get("max_payload_size")
    ) or _coerce_int(template_context.get("max_payload_size"))
    # The producer writes the PCIe generation number (1..5) at top level — see
    # src/pci_capability/processor.py:452 (LinkCap bits[3:0]) and
    # src/device_clone/pcileech_context.py:912-918 (sysfs fallback maps
    # 2.5/5.0/8.0/16.0/32.0 GT/s to codes 1..5). Pass it as ``link_speed`` so
    # the emitter converts via _LINK_SPEED_ENCODING — passing it as
    # ``link_speed_code`` would reject Gen3 (3) and silently mis-emit Gen4 (4).
    link_speed = _coerce_int(template_context.get("pcie_max_link_speed"))
    link_width = _coerce_int(template_context.get("pcie_max_link_width"))
    aer_enabled = _coerce_bool(device_config.get("supports_aer"))
    ari_forwarding_supported = _coerce_bool(device_config.get("ari_capable"))

    cpl_ranges = device_config.get("cpl_timeout_ranges")
    if not isinstance(cpl_ranges, str) or not cpl_ranges:
        cpl_ranges = None
    # Preserve unsupported values so the emitter refuses the build. Dropping
    # them here would silently retain an unrelated IP default.
    cpl_disable = _coerce_bool(device_config.get("cpl_timeout_disable_sup"))

    dsn_value = _coerce_int(template_context.get("device_serial_number_int"))

    (
        bar_aperture_size,
        bar_is_memory,
        bar_is_64bit,
        bar_prefetchable,
        bar_index,
    ) = _served_bar_from_context(template_context)
    served_bars = served_bars_from_config(template_context.get("bar_config")) or ()

    msix_enabled = msix_table_size = msix_table_bir = None
    msix_table_offset = msix_pba_bir = msix_pba_offset = None
    reason = None
    plan = _msix_plan(result or {}, template_context)
    if plan.present:
        msix_enabled = False
        reason = plan.reason
        layout = plan.layout
        if layout is not None:
            aperture_matches = (
                any(
                    bar.index == layout.served_bar.index
                    and bar.size == layout.served_bar.size
                    for bar in served_bars
                )
                if served_bars
                else bar_index == layout.served_bar.index
                and bar_aperture_size == layout.served_bar.size
            )
            if not aperture_matches:
                # The generated RTL serves this layout; refuse rather than
                # advertise a capability at a different BAR or size.
                reason = "the MSI-X BAR disagrees with the IP aperture"
            else:
                msix_enabled = True
                msix_table_size = layout.vectors
                msix_table_bir = layout.capability.table_bir
                msix_table_offset = layout.table_start
                msix_pba_bir = layout.capability.pba_bir
                msix_pba_offset = layout.pba_start
    if reason:
        log_warning_safe(
            logger,
            safe_format(
                "MSI-X capability is not advertised because {reason}", reason=reason
            ),
            prefix="MSIX",
        )

    return DonorPCIeIPConfig(
        class_code=class_code,
        max_payload_size=max_payload_size,
        link_speed=link_speed,
        link_width=link_width,
        msix_enabled=msix_enabled,
        msix_table_size=msix_table_size,
        msix_table_bir=msix_table_bir,
        msix_table_offset=msix_table_offset,
        msix_pba_bir=msix_pba_bir,
        msix_pba_offset=msix_pba_offset,
        aer_enabled=aer_enabled,
        ari_forwarding_supported=ari_forwarding_supported,
        cpl_timeout_ranges=cpl_ranges,
        cpl_timeout_disable_supported=cpl_disable,
        extended_tag_supported=_coerce_bool(
            template_context.get("pcie_extended_tag_supported")
        ),
        dsn_value=dsn_value,
        bar_aperture_size=bar_aperture_size,
        bar_is_memory=bar_is_memory,
        bar_is_64bit=bar_is_64bit,
        bar_prefetchable=bar_prefetchable,
        bar_index=bar_index,
        served_bars=served_bars,
    )


# Emitted by sv_overlay_generator when the MSI-X subsystem is generated; its
# presence in a build's systemverilog_modules proves the controller RTL that
# implements the plan was produced in THIS build.
_MSIX_RTL_MARKER = "pcileech_msix_bar_router.sv"


def _msix_rtl_emitted(result: dict) -> bool:
    modules = (result or {}).get("systemverilog_modules") or {}
    return _MSIX_RTL_MARKER in modules


def _msix_plan(result: dict, template_context: dict) -> MsixSubsystemPlan:
    """The stored generation decision, or a refusal when there is none."""
    stored = template_context.get("msix_subsystem")
    if stored is not None:
        try:
            plan = MsixSubsystemPlan.from_context(template_context)
        except (MsixLayoutError, ValueError, TypeError):
            return plan_msix_subsystem(
                result, board_error="the stored MSI-X decision is malformed"
            )
        if plan.generated and not _msix_rtl_emitted(result):
            # A stored generated decision must only advertise MSI-X when this
            # build actually emitted the controller RTL. The host-context path
            # reuses the static board controller with systemverilog_modules == {},
            # so honoring a saved plan there would advertise a table that the
            # firmware does not implement.
            return MsixSubsystemPlan(
                present=True,
                reason="the MSI-X controller RTL was not generated for this build",
            )
        return plan
    return plan_msix_subsystem(
        result, board_error="the MSI-X subsystem is not generated"
    )


def _attr_or_key(obj, name, default=None):
    """Read ``name`` from an object attribute or a mapping key (defensive)."""
    if obj is None:
        return default
    if isinstance(obj, dict):
        return obj.get(name, default)
    return getattr(obj, name, default)


def _served_bar_from_context(template_context: dict):
    """Extract the served BAR's (size, is_memory, is_64bit, prefetchable).

    Anchors on the SAME entry the BAR controller serves —
    ``bars[primary_bar|default(0)]`` (see pcileech_tlps128_bar_controller.sv.j2)
    — so the emitted IP aperture is guaranteed to match the controller's
    ``BAR_SIZE``. Returns all-None when the served BAR can't be read, leaving
    the Xilinx IP default in place rather than emitting a bad aperture.
    """
    none5 = (None, None, None, None, None)
    bar_config = template_context.get("bar_config") or {}
    descriptors = served_bars_from_config(bar_config)
    if descriptors is not None:
        register = next(
            bar for bar in descriptors if bar.role in ("registers", "registers+msix")
        )
        return (
            register.size,
            register.is_memory,
            register.is_64bit,
            register.prefetchable,
            register.index,
        )
    bars = _attr_or_key(bar_config, "bars") or []
    # ``primary_bar`` is the list position of the served BAR within ``bars``;
    # the controller indexes the same way (bars[primary_bar]).
    index = _coerce_int(_attr_or_key(bar_config, "primary_bar", 0)) or 0
    if index >= len(bars):
        return none5

    served = bars[index]
    size = _coerce_int(_attr_or_key(served, "size"))
    if not size:
        return none5

    is_io = bool(_attr_or_key(served, "is_io", False))
    is_memory = _attr_or_key(served, "is_memory", None)
    if is_memory is None:
        is_memory = not is_io
    is_memory = bool(is_memory)

    # Defensive: don't propagate an aperture the emitter would reject — leave
    # the Xilinx IP default in place instead of failing the build.
    if _bar_aperture_error(size, is_memory) is not None:
        return none5

    # Served donor PCI index N: prefer the explicit bar_config key, else the
    # served BAR's own .index. This is the slot the controller gates on and the
    # IP enables (CONFIG.Bar{N}_*).
    served_index = _coerce_int(_attr_or_key(bar_config, "served_bar_index", None))
    if served_index is None:
        served_index = _coerce_int(_attr_or_key(served, "index", 0)) or 0

    return (
        size,
        is_memory,
        bool(_attr_or_key(served, "is_64bit", False)),
        bool(_attr_or_key(served, "prefetchable", False)),
        served_index,
    )
