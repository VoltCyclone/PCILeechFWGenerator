"""Override Vivado PCIe IP CONFIG with donor IDs and capabilities (issue #593).

The upstream ``vivado_generate_project_*.tcl`` imports ``pcie_7x_0.xci``
verbatim. The .xci ships with Xilinx default values (Vendor_ID=10EE,
Device_ID=0666, Class_Code_Base=02, MSIx_Enabled=false, AER_Enabled=false,
LINK_CAP_MAX_LINK_WIDTH=1, etc.). Without an override the host sees those
defaults during PCIe enumeration -- even when the cfgspace shadow has the
donor's real bytes -- because the hard PCIe block emits IP-baked values
during the window between LTSSM up and the first cfg-read.

This module emits a TCL fragment that ``set_property -dict`` the donor's
collected values onto the staged IP, then re-runs ``generate_target`` so the
change reaches the synthesizable HDL. It is wired in by appending a guarded
``source`` line to the staged ``vivado_generate_project_*.tcl``.

Coverage as of Step 3 (see docs/superpowers/plans/2026-05-*-pcie-ip-donor-override-*.md):

- closed end-to-end: Class_Code (A4), MSI-X (A6), LinkCap (A7), MPS (A8),
  AER (C1), DSN (C2), ARI (C3), Cpl_Timeout (D2), served BAR aperture (A5)
- A5 scope: only the single served BAR is mirrored, presented as config-space
  BAR0 (the one window the PCILeech controller answers). Multi-BAR layout
  fidelity and donors whose served window isn't BAR0 are out of scope.
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

from dataclasses import dataclass
from pathlib import Path
from typing import List, Optional

from pcileechfwgenerator.utils.validators import get_bar_size_validator

from .fifo_donor_patcher import DonorIDs, _coerce_int


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
    link_speed: Optional[int] = None  # PCIe gen: 1, 2, 3, 4, 5 (mapped to spec)
    link_speed_code: Optional[int] = None  # Spec-encoded value 1/2/4/8/16 (direct)
    link_width: Optional[int] = None  # Lane count 1/2/4/8/16

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

    # A5 — served BAR aperture, presented as config-space BAR0 (the only window
    # the PCILeech controller services). Setting bar_aperture_size enables the
    # block; the rest default sensibly. See docs/plans/firmware-fidelity-gaps.md.
    bar_aperture_size: Optional[int] = None  # bytes, power of 2
    bar_is_memory: Optional[bool] = None  # True=Memory (default), False=IO
    bar_is_64bit: Optional[bool] = None  # memory only; consumes the next slot
    bar_prefetchable: Optional[bool] = None  # memory only
    bar_index: Optional[int] = None  # donor PCI BAR index N the device serves


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
        f"    generate_target -force all [get_ips {ip_name}]\n"
        "}\n"
    )


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

    out.extend(_format_bar_aperture_lines(extra))

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
    """Emit the ``CONFIG.Bar{N}_*`` block for the served donor BAR (gaps A5/C).

    The device register window is served at the donor's REAL PCI BAR index N
    (``bar_index``) — the controller gates its impl on rd_req_bar[N]/wr_bar[N]
    to match. IO BARs cannot be 64-bit or prefetchable, so those flags are
    coerced off. Every other standard BAR index is force-disabled (including the
    .xci default BAR0 when N != 0) so the host enumerates exactly the one served
    window — no phantom BARs, and no BAR the controller answers with ``none``
    (which would hang reads). A 64-bit BAR consumes index N+1, which falls into
    the disabled set automatically.
    """
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
        f"CONFIG.Bar{k}_Enabled false"
        for k in range(_NUM_STANDARD_BARS)
        if k != n
    )
    return lines


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

    Defensive: every field is independently extracted and set to None when the
    source data is missing or unparseable. Never raises. Mirrors the pattern of
    ``donor_ids_from_template_context`` in ``fifo_donor_patcher.py``.
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
    cpl_disable = _coerce_bool(device_config.get("cpl_timeout_disable_sup"))

    dsn_value = _coerce_int(template_context.get("device_serial_number_int"))

    # MSI-X — only honor the block if the parser flagged it valid AND every
    # layout field coerced. Partial state would crash the emitter downstream;
    # all-or-nothing means the rest of the override still ships.
    msix = (result or {}).get("msix_data") or {}
    msix_enabled = msix_table_size = msix_table_bir = None
    msix_table_offset = msix_pba_bir = msix_pba_offset = None
    if msix.get("is_valid"):
        candidate_enabled = _coerce_bool(msix.get("enabled"))
        candidate_size = _coerce_int(msix.get("table_size"))
        candidate_table_bir = _coerce_int(msix.get("table_bir"))
        candidate_table_off = _coerce_int(msix.get("table_offset"))
        candidate_pba_bir = _coerce_int(msix.get("pba_bir"))
        candidate_pba_off = _coerce_int(msix.get("pba_offset"))
        if None not in (
            candidate_enabled,
            candidate_size,
            candidate_table_bir,
            candidate_table_off,
            candidate_pba_bir,
            candidate_pba_off,
        ):
            msix_enabled = candidate_enabled
            msix_table_size = candidate_size
            msix_table_bir = candidate_table_bir
            msix_table_offset = candidate_table_off
            msix_pba_bir = candidate_pba_bir
            msix_pba_offset = candidate_pba_off

    (
        bar_aperture_size,
        bar_is_memory,
        bar_is_64bit,
        bar_prefetchable,
        bar_index,
    ) = _served_bar_from_context(template_context)

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
        dsn_value=dsn_value,
        bar_aperture_size=bar_aperture_size,
        bar_is_memory=bar_is_memory,
        bar_is_64bit=bar_is_64bit,
        bar_prefetchable=bar_prefetchable,
        bar_index=bar_index,
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
