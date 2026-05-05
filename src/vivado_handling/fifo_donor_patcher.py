"""Patch donor PCIe IDs into the upstream pcileech_fifo.sv (issue #593).

Upstream ``pcileech_fifo.sv`` ships with hardcoded Xilinx defaults
(``16'h10EE`` / ``16'h0666`` / ``8'h02``) in the RW reset block at offsets
``+010``..``+018`` and in the ``_pcie_core_config`` packed initializer.
Donor cloning otherwise injects vendor/device IDs only into the cfgspace BRAM,
so the FIFO's runtime control register keeps Xilinx defaults forever (the
upstream comments mark these slots ``(NOT IMPLEMENTED)`` in software).

This module rewrites those literals deterministically as a post-copy step. It
also commented-outs the five ``assign dpcie.pcie_cfg_*`` lines that exist only
in the CaptainDMA/75t484_x1 fifo but reference fields the matching
``IfPCIeFifoCore`` interface never declares — that mismatch is what makes
75t484_x1 fail synthesis with ``cannot resolve hierarchical name``.
"""
from __future__ import annotations

import re
from dataclasses import dataclass
from pathlib import Path
from typing import Optional


class FifoPatchError(RuntimeError):
    """Raised when the FIFO source does not match the expected upstream shape."""


@dataclass(frozen=True)
class DonorIDs:
    """Donor PCIe identifiers used to rewrite the FIFO reset block."""

    vendor_id: int
    device_id: int
    subsystem_vendor_id: int
    subsystem_id: int
    revision_id: int


# RW-reset-block anchors. Ordered (label, regex, hex-width, donor-attr).
# The regex captures up to the literal so the suffix (``;`` + comment) survives.
_RW_ANCHORS = (
    ("+010: CFG_SUBSYS_VEND_ID", r"(rw\[143:128\]\s*<=\s*)16'h[0-9A-Fa-f]{4}", 4, "subsystem_vendor_id"),
    ("+012: CFG_SUBSYS_ID",      r"(rw\[159:144\]\s*<=\s*)16'h[0-9A-Fa-f]{4}", 4, "subsystem_id"),
    ("+014: CFG_VEND_ID",        r"(rw\[175:160\]\s*<=\s*)16'h[0-9A-Fa-f]{4}", 4, "vendor_id"),
    ("+016: CFG_DEV_ID",         r"(rw\[191:176\]\s*<=\s*)16'h[0-9A-Fa-f]{4}", 4, "device_id"),
    ("+018: CFG_REV_ID",         r"(rw\[199:192\]\s*<=\s*)8'h[0-9A-Fa-f]{2}",  2, "revision_id"),
)

# Trailing 5 fields of the packed _pcie_core_config initializer:
# ``8'hXX, 16'hXXXX, 16'hXXXX, 16'hXXXX, 16'hXXXX``
# Order is rev, dev, vend, subsys_id, subsys_vend (LSB-last per concat semantics).
_PCIE_CORE_CONFIG_TAIL = re.compile(
    r"(_pcie_core_config\s*=\s*\{[^}]*?,\s*)"
    r"8'h[0-9A-Fa-f]{2}\s*,\s*"
    r"16'h[0-9A-Fa-f]{4}\s*,\s*"
    r"16'h[0-9A-Fa-f]{4}\s*,\s*"
    r"16'h[0-9A-Fa-f]{4}\s*,\s*"
    r"16'h[0-9A-Fa-f]{4}"
    r"(\s*\})"
)

_CFG_ID_ASSIGN_RE = re.compile(
    r"^(\s*)assign\s+dpcie\.pcie_cfg_"
    r"(subsys_vend_id|subsys_id|vend_id|dev_id|rev_id)\b"
)

# Active (non-commented) assign to any dpcie field. Used to detect mismatches
# between the staged fifo and header that the patcher can't auto-fix.
_DPCIE_ASSIGN_RE = re.compile(
    r"^\s*assign\s+dpcie\.([A-Za-z_][A-Za-z0-9_]*)\b"
)

# A wire/reg/logic declaration name inside an interface body. Conservative
# regex: matches "wire <field>" / "wire [N:0] <field>" forms. We're only
# checking presence-by-name, so this doesn't need to be a full SV parser.
_INTERFACE_WIRE_RE = re.compile(
    r"\b(?:wire|reg|logic)\b(?:\s*\[[^\]]+\])?\s+([A-Za-z_][A-Za-z0-9_]*)"
)

_HEADER_FIELD_TOKENS = (
    "pcie_cfg_subsys_vend_id",
    "pcie_cfg_subsys_id",
    "pcie_cfg_vend_id",
    "pcie_cfg_dev_id",
    "pcie_cfg_rev_id",
)


def _validate_width(name: str, value: int, width_bits: int) -> None:
    if value < 0 or value >> width_bits:
        raise FifoPatchError(
            f"donor {name}=0x{value:X} does not fit in {width_bits} bits"
        )


def _hex(value: int, width_hex: int) -> str:
    return f"{value:0{width_hex}X}"


def _patch_rw_reset_block(text: str, donor: DonorIDs) -> str:
    out = text
    for label, pattern, width_hex, attr in _RW_ANCHORS:
        value = getattr(donor, attr)
        _validate_width(attr, value, width_hex * 4)
        replacement_literal = (
            f"{width_hex * 4}'h{_hex(value, width_hex)}"
        )
        new_text, count = re.subn(
            pattern,
            lambda m, lit=replacement_literal: m.group(1) + lit,
            out,
            count=1,
        )
        if count != 1:
            raise FifoPatchError(
                f"FIFO reset-block anchor not found: {label}"
            )
        out = new_text
    return out


def _patch_pcie_core_config_initializer(text: str, donor: DonorIDs) -> str:
    for attr, width in (
        ("vendor_id", 16),
        ("device_id", 16),
        ("subsystem_vendor_id", 16),
        ("subsystem_id", 16),
        ("revision_id", 8),
    ):
        _validate_width(attr, getattr(donor, attr), width)

    replacement_tail = (
        f"8'h{_hex(donor.revision_id, 2)}, "
        f"16'h{_hex(donor.device_id, 4)}, "
        f"16'h{_hex(donor.vendor_id, 4)}, "
        f"16'h{_hex(donor.subsystem_id, 4)}, "
        f"16'h{_hex(donor.subsystem_vendor_id, 4)}"
    )

    new_text, count = _PCIE_CORE_CONFIG_TAIL.subn(
        lambda m: m.group(1) + replacement_tail + m.group(2),
        text,
        count=1,
    )
    if count != 1:
        raise FifoPatchError(
            "_pcie_core_config packed initializer not found in FIFO"
        )
    return new_text


def _header_declares_cfg_fields(header_text: str) -> bool:
    """Return True iff the header declares the cfg-id fields the fifo writes to.

    Conservative: requires *all five* tokens to be present anywhere in the
    header text. The upstream interface block is the only sensible host, but
    we don't parse SystemVerilog — substring presence is enough to decide
    whether the assigns will resolve at synthesis time.
    """
    return all(token in header_text for token in _HEADER_FIELD_TOKENS)


def _comment_out_cfg_id_assigns(text: str) -> tuple[str, bool]:
    """Comment out the five ``assign dpcie.pcie_cfg_*`` lines.

    Returns (new_text, did_anything). Lines already commented are left alone.
    """
    changed = False
    out_lines = []
    for line in text.splitlines(keepends=True):
        match = _CFG_ID_ASSIGN_RE.match(line)
        if not match:
            out_lines.append(line)
            continue
        stripped = line.lstrip()
        if stripped.startswith("//"):
            out_lines.append(line)
            continue
        indent = match.group(1)
        out_lines.append(
            f"{indent}// {line[len(indent):].rstrip()}"
            f" // [issue-593] interface field not declared\n"
        )
        changed = True
    return "".join(out_lines), changed


def patch_pcileech_fifo(
    fifo_text: str,
    donor: DonorIDs,
    *,
    header_text: Optional[str] = None,
) -> str:
    """Return ``fifo_text`` rewritten with donor IDs.

    Always patches the RW reset block and ``_pcie_core_config`` initializer.
    If ``header_text`` is provided **and** the header does not declare the
    cfg-id interface fields, the matching ``assign dpcie.pcie_cfg_*`` lines
    are commented out so the upstream submodule's self-inconsistency does not
    fail synthesis.
    """
    out = _patch_rw_reset_block(fifo_text, donor)
    out = _patch_pcie_core_config_initializer(out, donor)

    if header_text is not None and not _header_declares_cfg_fields(header_text):
        out, _ = _comment_out_cfg_id_assigns(out)

    return out


def _coerce_int(value) -> Optional[int]:
    """Best-effort int coercion: accept ints and ``0x``-prefixed strings."""
    if value is None:
        return None
    if isinstance(value, bool):
        return None
    if isinstance(value, int):
        return value
    if isinstance(value, str):
        s = value.strip()
        if not s:
            return None
        try:
            return int(s, 16) if s.lower().startswith("0x") else int(s, 0)
        except ValueError:
            try:
                return int(s, 16)
            except ValueError:
                return None
    return None


def donor_ids_from_template_context(
    template_context: dict,
) -> Optional[DonorIDs]:
    """Extract donor IDs from the build's ``template_context``.

    Prefers ``*_int`` fields; falls back to the string forms (``vendor_id``
    etc.). Returns ``None`` when the donor's vendor or device ID is missing
    or zero — that signals the build never recovered real donor data, in
    which case patching with zeros would mask the bug.

    Subsystem IDs of zero are tolerated and fall back to the main vendor
    (matching the ``get_subsystem_vendor_id`` helper macro), since plenty
    of real devices report ``0:0`` for subsystem fields.
    """
    device_config = (template_context or {}).get("device_config") or {}
    if not device_config:
        return None

    def pick(int_key: str, str_key: str) -> Optional[int]:
        value = device_config.get(int_key)
        if value is None:
            value = device_config.get(str_key)
        return _coerce_int(value)

    vendor_id = pick("vendor_id_int", "vendor_id")
    device_id = pick("device_id_int", "device_id")
    revision_id = pick("revision_id_int", "revision_id")
    subsystem_vendor_id = pick("subsystem_vendor_id_int", "subsystem_vendor_id")
    subsystem_id = pick("subsystem_device_id_int", "subsystem_device_id")

    if not vendor_id or not device_id:
        return None

    if revision_id is None:
        revision_id = 0
    if not subsystem_vendor_id:
        # Mirror get_subsystem_vendor_id helper: fall back to main vendor.
        subsystem_vendor_id = vendor_id
    if subsystem_id is None:
        subsystem_id = 0

    return DonorIDs(
        vendor_id=vendor_id,
        device_id=device_id,
        subsystem_vendor_id=subsystem_vendor_id,
        subsystem_id=subsystem_id,
        revision_id=revision_id,
    )


def _extract_interface_member_names(header_text: str) -> set:
    """Return the union of wire/reg/logic names declared in the header.

    We don't scope the search to a single interface block — substring presence
    anywhere in the header is enough to decide whether a fifo's
    ``assign dpcie.<field>`` will resolve.
    """
    names: set = set()
    for match in _INTERFACE_WIRE_RE.finditer(header_text):
        names.add(match.group(1))
    return names


def _detect_unpatched_dpcie_mismatches(
    fifo_text: str, header_text: Optional[str]
) -> list:
    """Return dpcie fields the fifo writes to that the header doesn't declare.

    Lines that are commented out are skipped. The patcher's known cfg-id
    fields are also excluded (they're handled by the comment-out branch).
    """
    if header_text is None:
        return []

    declared = _extract_interface_member_names(header_text)
    known_cfg_fields = {f"pcie_cfg_{tag}" for tag in (
        "subsys_vend_id", "subsys_id", "vend_id", "dev_id", "rev_id"
    )}

    missing = []
    for line in fifo_text.splitlines():
        if line.lstrip().startswith("//"):
            continue
        match = _DPCIE_ASSIGN_RE.match(line)
        if not match:
            continue
        field = match.group(1)
        if field in declared or field in known_cfg_fields:
            continue
        missing.append(field)
    # Stable, de-duplicated.
    seen = set()
    out = []
    for field in missing:
        if field not in seen:
            seen.add(field)
            out.append(field)
    return out


def apply_fifo_donor_patch(
    src_dir: Path,
    donor: DonorIDs,
    *,
    fifo_filename: str = "pcileech_fifo.sv",
    header_filename: str = "pcileech_header.svh",
) -> dict:
    """Patch ``<src_dir>/pcileech_fifo.sv`` in place.

    Returns a summary dict with keys:
      - ``patched``: whether the fifo file was rewritten.
      - ``fifo_path``: Path of the fifo file (only when patched).
      - ``cfg_assigns_commented``: whether the cfg-id assigns were commented.
      - ``reason``: present only when ``patched`` is False.
    """
    fifo_path = Path(src_dir) / fifo_filename
    if not fifo_path.is_file():
        return {"patched": False, "reason": "fifo_not_found"}

    fifo_text = fifo_path.read_text()
    header_path = Path(src_dir) / header_filename
    header_text = header_path.read_text() if header_path.is_file() else None

    new_text = patch_pcileech_fifo(fifo_text, donor, header_text=header_text)

    def _active_cfg_id_assigns(text: str) -> int:
        return sum(
            1
            for line in text.splitlines()
            if not line.lstrip().startswith("//")
            and _CFG_ID_ASSIGN_RE.match(line) is not None
        )

    cfg_commented = (
        _active_cfg_id_assigns(fifo_text) > _active_cfg_id_assigns(new_text)
    )

    # After the patcher has commented-out the known cfg-id mismatches, any
    # remaining dpcie.<field> assigned in the fifo but absent from the header
    # is a mismatch we can't auto-fix. Surface it before Vivado runs.
    leftover = _detect_unpatched_dpcie_mismatches(new_text, header_text)
    if leftover:
        raise FifoPatchError(
            "FIFO writes to dpcie fields not declared in the staged header: "
            + ", ".join(leftover)
            + f" ({fifo_path.name})"
        )

    if new_text != fifo_text:
        fifo_path.write_text(new_text)

    return {
        "patched": True,
        "fifo_path": fifo_path,
        "cfg_assigns_commented": cfg_commented,
    }
