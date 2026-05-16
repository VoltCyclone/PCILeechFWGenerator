"""Override Vivado PCIe IP CONFIG with donor IDs (issue #593, T2).

The upstream ``vivado_generate_project_*.tcl`` imports ``pcie_7x_0.xci``
verbatim. The .xci ships with Xilinx default IDs (Vendor_ID=10EE,
Device_ID=0666, Subsystem_Vendor_ID=10EE, Subsystem_ID=0007, Revision_ID=02),
and the upstream never overrides them. Without a CONFIG override the host
sees those defaults during PCIe enumeration regardless of what the cfgspace
shadow contains.

This module emits a small TCL fragment that runs ``set_property -dict ...``
against the staged IP and re-runs ``generate_target`` so the change reaches
the synthesizable HDL. The fragment is wired in by appending a guarded
``source`` line to the staged ``vivado_generate_project_*.tcl``.
"""
from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import List, Optional

from .fifo_donor_patcher import DonorIDs


class PcieIpOverrideError(RuntimeError):
    """Raised when the donor IP override cannot be wired into the staged build."""


@dataclass(frozen=True)
class DonorPCIeIPConfig:
    """Optional donor PCIe IP CONFIG values beyond the five identification IDs.

    Every field is optional. A ``None`` value means "the donor profile did not
    expose this; leave the Xilinx IP default in place." That keeps the override
    safe when the donor profile is partial (e.g. an older capture without DSN).

    See ``docs/plans/firmware-fidelity-gaps.md`` for the rationale behind each
    field.
    """

    # A4 — Class Code (24-bit, packed base/sub/interface)
    class_code: Optional[int] = None

    # A8 — Max Payload Size (DevCap.MPS in bytes: 128/256/512/1024/2048/4096)
    max_payload_size: Optional[int] = None

    # A7 — LinkCap negotiated ceiling
    link_speed: Optional[int] = None  # PCIe gen: 1, 2, 3, 4, 5
    link_width: Optional[int] = None  # x1/x2/x4/x8/x16 as integer 1/2/4/8/16

    # A6 — MSI-X capability layout
    msix_enabled: Optional[bool] = None
    msix_table_size: Optional[int] = None  # number of vectors (1..2048)
    msix_table_bir: Optional[int] = None  # 0..5
    msix_table_offset: Optional[int] = None
    msix_pba_bir: Optional[int] = None  # 0..5
    msix_pba_offset: Optional[int] = None

    # C1 / C3 — Capability enables
    aer_enabled: Optional[bool] = None
    ari_forwarding_supported: Optional[bool] = None

    # D2 — Completion-timeout policy
    # one of: "none", "A", "B", "C", "D", "AB", "BC", "BCD", "ABCD"
    cpl_timeout_ranges: Optional[str] = None
    cpl_timeout_disable_supported: Optional[bool] = None

    # C2 — Device Serial Number (64-bit; emitted as two 32-bit halves into the IP)
    dsn_value: Optional[int] = None


_OVERRIDE_FILENAME = "pcileech_donor_ip_overrides.tcl"
# Use ``file join`` so paths containing spaces don't tokenize incorrectly
# in Tcl. Equivalent for sane paths but more idiomatic and robust.
_SOURCE_MARKER = (
    f"source [file join [file dirname [info script]] {_OVERRIDE_FILENAME}]"
)


def generate_pcie_ip_override_tcl(
    donor: DonorIDs,
    *,
    ip_name: str = "pcie_7x_0",
    extra: "DonorPCIeIPConfig | None" = None,
) -> str:
    """Render the donor-ID CONFIG override as a Vivado TCL string.

    Guarded by ``[get_ips -quiet]`` so a board variant that ships a
    differently-named PCIe IP doesn't abort the build. Optional ``extra``
    contributes additional ``CONFIG.<key> <value>`` lines for fields the
    donor profile exposed (class code, MPS, MSI-X, LinkCap, AER, ARI,
    cpl-timeout, DSN — see ``DonorPCIeIPConfig``).
    """
    lines: list[str] = [
        f"CONFIG.Vendor_ID 0x{donor.vendor_id:04X}",
        f"CONFIG.Device_ID 0x{donor.device_id:04X}",
        f"CONFIG.Subsystem_Vendor_ID 0x{donor.subsystem_vendor_id:04X}",
        f"CONFIG.Subsystem_ID 0x{donor.subsystem_id:04X}",
        f"CONFIG.Revision_ID 0x{donor.revision_id:02X}",
    ]
    if extra is not None:
        lines.extend(_format_extra_config_lines(extra))

    set_property_block = "\n".join(f"        {line} \\" for line in lines)

    return (
        "# === PCILeechFWGenerator donor-ID override (issue #593) ===\n"
        f"if {{[llength [get_ips -quiet {ip_name}]] > 0}} {{\n"
        f"    set_property -dict [list \\\n"
        f"{set_property_block}\n"
        f"    ] [get_ips {ip_name}]\n"
        f"    generate_target all [get_ips {ip_name}]\n"
        "}\n"
    )


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
_VALID_CPL_TIMEOUT_RANGES = {"none", "A", "B", "C", "D", "AB", "BC", "BCD", "ABCD"}


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

    if extra.link_speed is not None:
        encoded = _LINK_SPEED_ENCODING.get(extra.link_speed)
        if encoded is None:
            raise ValueError(
                f"link_speed {extra.link_speed} is not a valid PCIe generation "
                f"(supported: {sorted(_LINK_SPEED_ENCODING)})"
            )
        out.append(f"CONFIG.LINK_CAP_MAX_LINK_SPEED {encoded}")

    if extra.link_width is not None:
        if extra.link_width not in _VALID_LINK_WIDTHS:
            raise ValueError(
                f"link_width {extra.link_width} is not one of {_VALID_LINK_WIDTHS}"
            )
        out.append(f"CONFIG.LINK_CAP_MAX_LINK_WIDTH {extra.link_width}")

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
            if extra.msix_table_size < 1 or extra.msix_table_size > 2048:
                raise ValueError(
                    f"msix_table_size {extra.msix_table_size} outside spec range 1..2048"
                )
            for label, bir in (
                ("msix_table_bir", extra.msix_table_bir),
                ("msix_pba_bir", extra.msix_pba_bir),
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
        if extra.cpl_timeout_ranges not in _VALID_CPL_TIMEOUT_RANGES:
            raise ValueError(
                f"cpl_timeout_ranges {extra.cpl_timeout_ranges!r} is not one of "
                f"{sorted(_VALID_CPL_TIMEOUT_RANGES)}"
            )
        token = extra.cpl_timeout_ranges
        encoded = "none" if token == "none" else f"Range_{token}"
        out.append(f"CONFIG.Cpl_Timeout_Range {encoded}")

    if extra.cpl_timeout_disable_supported is not None:
        out.append(
            f"CONFIG.Cpl_Timeout_Disable_Sup {_tcl_bool(extra.cpl_timeout_disable_supported)}"
        )

    if extra.dsn_value is not None:
        if not (0 <= extra.dsn_value <= 0xFFFFFFFFFFFFFFFF):
            raise ValueError(
                f"dsn_value 0x{extra.dsn_value:X} does not fit in 64 bits"
            )
        lower = extra.dsn_value & 0xFFFFFFFF
        upper = (extra.dsn_value >> 32) & 0xFFFFFFFF
        out.append(f"CONFIG.DSN_HEX1 {lower:08X}")
        out.append(f"CONFIG.DSN_HEX2 {upper:08X}")

    return out


def _find_generate_project_scripts(staging_dir: Path) -> List[Path]:
    return sorted(Path(staging_dir).glob("vivado_generate_project*.tcl"))


def _append_source_line(script_path: Path) -> None:
    text = script_path.read_text()
    if _OVERRIDE_FILENAME in text:
        return  # idempotent
    suffix = (
        "\n\n# [issue-593] PCILeechFWGenerator donor-ID override\n"
        f"{_SOURCE_MARKER}\n"
    )
    script_path.write_text(text + suffix)


def apply_pcie_ip_donor_override(
    staging_dir: Path,
    donor: DonorIDs,
    *,
    ip_name: str = "pcie_7x_0",
    extra: "DonorPCIeIPConfig | None" = None,
) -> dict:
    """Write the override TCL and wire it into the staged generate-project script.

    Returns a summary dict with:
      - ``override_path``: Path to the emitted override TCL.
      - ``wired_scripts``: list of generate-project scripts that received the
        ``source`` line.
    """
    staging_dir = Path(staging_dir)
    scripts = _find_generate_project_scripts(staging_dir)
    if not scripts:
        raise PcieIpOverrideError(
            "no vivado_generate_project*.tcl found in "
            f"{staging_dir} — cannot wire donor IP override"
        )

    override_path = staging_dir / _OVERRIDE_FILENAME
    override_path.write_text(
        generate_pcie_ip_override_tcl(donor, ip_name=ip_name, extra=extra)
    )

    for script in scripts:
        _append_source_line(script)

    return {
        "override_path": override_path,
        "wired_scripts": scripts,
    }
