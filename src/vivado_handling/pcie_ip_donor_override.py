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

from pathlib import Path
from typing import List

from .fifo_donor_patcher import DonorIDs


class PcieIpOverrideError(RuntimeError):
    """Raised when the donor IP override cannot be wired into the staged build."""


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
) -> str:
    """Render the donor-ID CONFIG override as a Vivado TCL string.

    Guarded by ``[get_ips -quiet]`` so a board variant that ships a
    differently-named PCIe IP doesn't abort the build.
    """
    return (
        "# === PCILeechFWGenerator donor-ID override (issue #593) ===\n"
        f"if {{[llength [get_ips -quiet {ip_name}]] > 0}} {{\n"
        f"    set_property -dict [list \\\n"
        f"        CONFIG.Vendor_ID 0x{donor.vendor_id:04X} \\\n"
        f"        CONFIG.Device_ID 0x{donor.device_id:04X} \\\n"
        f"        CONFIG.Subsystem_Vendor_ID 0x{donor.subsystem_vendor_id:04X} \\\n"
        f"        CONFIG.Subsystem_ID 0x{donor.subsystem_id:04X} \\\n"
        f"        CONFIG.Revision_ID 0x{donor.revision_id:02X} \\\n"
        f"    ] [get_ips {ip_name}]\n"
        f"    generate_target all [get_ips {ip_name}]\n"
        "}\n"
    )


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
        generate_pcie_ip_override_tcl(donor, ip_name=ip_name)
    )

    for script in scripts:
        _append_source_line(script)

    return {
        "override_path": override_path,
        "wired_scripts": scripts,
    }
