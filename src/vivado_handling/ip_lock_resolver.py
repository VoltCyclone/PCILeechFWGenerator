#!/usr/bin/env python3
"""Utilities for repairing Vivado IP artifacts in the datastore."""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from stat import S_IWGRP, S_IWOTH, S_IWUSR
from typing import Dict, List, Optional

from pcileechfwgenerator.log_config import get_logger
from pcileechfwgenerator.string_utils import (
    log_debug_safe,
    log_info_safe,
    log_warning_safe,
    safe_format,
)

LOCK_SUFFIXES = (".lck", ".lock")
IP_FILE_SUFFIXES = (".xci", ".xcix")


def _is_pcie_core_xci(name: str) -> bool:
    """True if an XCI filename is the PCIe core whose stale IDs cause #622."""
    return name.lower().startswith("pcie_7x")


@dataclass
class XciPatchSummary:
    """Outcome of :func:`patch_xci_donor_ids` across all discovered XCIs."""

    patched: List[str] = field(default_factory=list)
    unmatched: List[str] = field(default_factory=list)
    failed: List[str] = field(default_factory=list)
    total_files: int = 0

    @property
    def num_patched(self) -> int:
        return len(self.patched)

    def has_unmatched_core(self) -> bool:
        """True if any unmatched OR failed file is the PCIe core."""
        return any(
            _is_pcie_core_xci(name)
            for name in (*self.unmatched, *self.failed)
        )


def _apply_xci_subs(text: str, subs, fmt: str):
    """Return (new_text, total_substitutions) for the given format.

    ``fmt`` is "json" or "xml". JSON anchors on ``"key": [ { "value": ...``;
    XML anchors on ``referenceId="(PARAM_VALUE|MODELPARAM_VALUE).key">VALUE<``.
    Both anchor on the exact key so e.g. ``class_code`` cannot match inside
    ``Class_Code_Base``.
    """
    import re as _re

    new_text = text
    total = 0
    for key, val in subs:
        if fmt == "json":
            pattern = (
                rf'("{key}"\s*:\s*\[\s*\{{\s*"value"\s*:\s*)"[0-9A-Fa-f]+"'
            )
            repl = rf'\1"{val}"'
        else:  # xml
            pattern = (
                rf'(referenceId="(?:PARAM_VALUE|MODELPARAM_VALUE)\.{key}"\s*>)'
                rf"[0-9A-Fa-f]+(?=</)"
            )
            repl = rf"\g<1>{val}"
        new_text, n = _re.subn(pattern, repl, new_text)
        total += n
    return new_text, total


def _discover_ip_dirs(root: Path) -> List[Path]:
    """Return every `ip` directory rooted at *root*."""
    ip_dirs: List[Path] = []
    visited = set()

    direct = root / "ip"
    if direct.is_dir():
        ip_dirs.append(direct)
        visited.add(direct.resolve())

    for candidate in root.rglob("ip"):
        if not candidate.is_dir():
            continue
        resolved = candidate.resolve()
        if resolved in visited:
            continue
        ip_dirs.append(candidate)
        visited.add(resolved)

    return ip_dirs


def _remove_lock_files(ip_dir: Path, prefix: str, logger) -> int:
    removed = 0
    for suffix in LOCK_SUFFIXES:
        for lock_file in ip_dir.rglob(f"*{suffix}"):
            try:
                lock_file.unlink()
                removed += 1
                log_info_safe(
                    logger,
                    safe_format(
                        "Removed stale lock file: {path}",
                        path=str(lock_file),
                    ),
                    prefix=prefix,
                )
            except FileNotFoundError:
                continue
            except Exception as exc:  # pragma: no cover - unexpected fs error
                log_warning_safe(
                    logger,
                    safe_format(
                        "Unable to remove lock file {path}: {err}",
                        path=str(lock_file),
                        err=str(exc),
                    ),
                    prefix=prefix,
                )
    return removed


def _ensure_writable(ip_dir: Path, prefix: str, logger) -> int:
    repaired = 0
    for suffix in IP_FILE_SUFFIXES:
        for ip_file in ip_dir.rglob(f"*{suffix}"):
            try:
                current_mode = ip_file.stat().st_mode
                desired_mode = current_mode | S_IWUSR | S_IWGRP | S_IWOTH
                if current_mode != desired_mode:
                    ip_file.chmod(desired_mode)
                    repaired += 1
                    log_debug_safe(
                        logger,
                        safe_format(
                            "Ensured write access for IP file: {path}",
                            path=str(ip_file),
                        ),
                        prefix=prefix,
                    )
            except FileNotFoundError:
                continue
            except PermissionError as exc:
                log_warning_safe(
                    logger,
                    safe_format(
                        "Permission error updating {path}: {err}",
                        path=str(ip_file),
                        err=str(exc),
                    ),
                    prefix=prefix,
                )
            except Exception as exc:  # pragma: no cover - unexpected fs error
                log_warning_safe(
                    logger,
                    safe_format(
                        "Failed to adjust permissions for {path}: {err}",
                        path=str(ip_file),
                        err=str(exc),
                    ),
                    prefix=prefix,
                )
    return repaired


def patch_xci_speed_grade(
    ip_root: Path,
    target_fpga_part: str,
    logger=None,
    prefix: str = "VIVADO",
) -> int:
    """Patch XCI files whose SPEEDGRADE doesn't match the target FPGA part.

    Vivado locks IP cores when the XCI's SPEEDGRADE differs from the
    project's target part.  This pre-build step rewrites the value so
    Vivado can open/regenerate the cores without manual intervention.

    Returns the number of files patched.
    """
    import re as _re

    logger = logger or get_logger(__name__)

    # Extract speed grade from part string.
    # Handles both simple (e.g. "xc7a100tfgg484-1" -> "-1") and
    # suffixed parts (e.g. "xczu3eg-sbva484-1-e" -> "-1").
    match = _re.search(r"-(\d+)(?:-[a-zA-Z])?$", target_fpga_part)
    if not match:
        log_warning_safe(
            logger,
            safe_format(
                "Cannot extract speed grade from part '{part}'",
                part=target_fpga_part,
            ),
            prefix=prefix,
        )
        return 0
    target_grade = "-" + match.group(1)

    ip_dirs = _discover_ip_dirs(ip_root)
    patched = 0
    for ip_dir in ip_dirs:
        for xci_file in ip_dir.glob("*.xci"):
            try:
                text = xci_file.read_text(encoding="utf-8")
                # Match  "SPEEDGRADE": [ { "value": "-2" } ]
                new_text, n = _re.subn(
                    r'("SPEEDGRADE"\s*:\s*\[\s*\{\s*"value"\s*:\s*)"(-?\d+)"',
                    rf'\1"{target_grade}"',
                    text,
                )
                if n > 0 and new_text != text:
                    xci_file.write_text(new_text, encoding="utf-8")
                    patched += 1
                    log_info_safe(
                        logger,
                        safe_format(
                            "Patched SPEEDGRADE -> {grade} in {path}",
                            grade=target_grade,
                            path=xci_file.name,
                        ),
                        prefix=prefix,
                    )
            except Exception as exc:
                log_warning_safe(
                    logger,
                    safe_format(
                        "Failed to patch {path}: {err}",
                        path=str(xci_file),
                        err=str(exc),
                    ),
                    prefix=prefix,
                )

    if patched:
        log_info_safe(
            logger,
            safe_format(
                "Patched SPEEDGRADE in {count} XCI file(s) to {grade}",
                count=patched,
                grade=target_grade,
            ),
            prefix=prefix,
        )
    return patched


def patch_xci_donor_ids(
    ip_root: Path,
    donor,
    class_code: Optional[int] = None,
    logger=None,
    prefix: str = "VIVADO",
) -> "XciPatchSummary":
    """Rewrite staged XCI files so their PCIe IDs match the donor.

    The upstream XCI ships with Xilinx defaults (Vendor_ID=10EE,
    Device_ID=0666, Class_Code_Base=02, ...). Vivado reads the XCI at
    project-open to decide whether IP regeneration is needed; if it still
    holds defaults Vivado may skip ``generate_target`` and bake the stale
    IDs into the bitstream. Rewriting both the user-config block and the
    generated-value block before Vivado opens the project makes the donor
    IDs the baseline, with the TCL CONFIG override acting as a second line
    of defense (issue #622, lineage of #593).

    ``donor`` must expose ``vendor_id``/``device_id``/``subsystem_vendor_id``/
    ``subsystem_id`` as ints (the ``DonorIDs`` dataclass). ``class_code`` is
    the 24-bit packed value (base<<16 | sub<<8 | interface); pass ``None`` to
    leave class-code fields unchanged. Returns an :class:`XciPatchSummary`.
    """
    logger = logger or get_logger(__name__)

    vid = f"{donor.vendor_id:04X}"
    did = f"{donor.device_id:04X}"
    svid = f"{donor.subsystem_vendor_id:04X}"
    sid = f"{donor.subsystem_id:04X}"

    # (json-key, replacement-value). User-config block uses long names and
    # splits the class code into three 2-hex fields; the generated block uses
    # short names and a single 6-hex class_code.
    subs = [
        ("Vendor_ID", vid),
        ("Device_ID", did),
        ("Subsystem_Vendor_ID", svid),
        ("Subsystem_ID", sid),
        ("ven_id", vid),
        ("dev_id", did),
        ("subsys_ven_id", svid),
        ("subsys_id", sid),
    ]
    if class_code is not None:
        if not (0 <= class_code <= 0xFFFFFF):
            raise ValueError(
                f"class_code 0x{class_code:X} does not fit in 24 bits"
            )
        base = (class_code >> 16) & 0xFF
        sub = (class_code >> 8) & 0xFF
        iface = class_code & 0xFF
        subs += [
            ("Class_Code_Base", f"{base:02X}"),
            ("Class_Code_Sub", f"{sub:02X}"),
            ("Class_Code_Interface", f"{iface:02X}"),
            ("class_code", f"{class_code:06X}"),
        ]

    ip_dirs = _discover_ip_dirs(ip_root)
    summary = XciPatchSummary()
    for ip_dir in ip_dirs:
        for xci_file in ip_dir.glob("*.xci"):
            summary.total_files += 1
            try:
                text = xci_file.read_text(encoding="utf-8")
                stripped = text.lstrip()
                if stripped.startswith("{"):
                    fmt = "json"
                elif stripped.startswith("<"):
                    fmt = "xml"
                else:
                    fmt = "unknown"

                if fmt == "xml":
                    # Validate well-formedness before trusting the regex path.
                    # Parse only — the edit stays a text substitution to avoid
                    # namespace/formatting churn on reserialize.
                    import xml.etree.ElementTree as _ET

                    try:
                        _ET.fromstring(text)
                    except Exception:
                        summary.failed.append(xci_file.name)
                        log_warning_safe(
                            logger,
                            safe_format(
                                "XCI {path} looks like XML but is not "
                                "well-formed; left unpatched (issue #622).",
                                path=xci_file.name,
                            ),
                            prefix=prefix,
                        )
                        continue

                if fmt == "unknown":
                    new_text, total = text, 0
                else:
                    new_text, total = _apply_xci_subs(text, subs, fmt)

                if total > 0:
                    # Fields matched the donor-ID anchors. If the text changed
                    # we rewrote it; if not, the baseline already holds the
                    # donor values (idempotent re-run). Both count as patched
                    # — only a zero-match file is genuinely unmatched.
                    if new_text != text:
                        xci_file.write_text(new_text, encoding="utf-8")
                        log_info_safe(
                            logger,
                            safe_format(
                                "Patched donor IDs ({n} field(s)) in {path}",
                                n=total,
                                path=xci_file.name,
                            ),
                            prefix=prefix,
                        )
                    else:
                        log_info_safe(
                            logger,
                            safe_format(
                                "Donor IDs already current ({n} field(s)) "
                                "in {path}",
                                n=total,
                                path=xci_file.name,
                            ),
                            prefix=prefix,
                        )
                    summary.patched.append(xci_file.name)
                else:
                    summary.unmatched.append(xci_file.name)
                    log_warning_safe(
                        logger,
                        safe_format(
                            "No donor-ID fields matched in {path} "
                            "(tried JSON and XML forms); left unpatched "
                            "(issue #622).",
                            path=xci_file.name,
                        ),
                        prefix=prefix,
                    )
            except Exception as exc:
                summary.failed.append(xci_file.name)
                log_warning_safe(
                    logger,
                    safe_format(
                        "Failed to patch donor IDs in {path}: {err}",
                        path=str(xci_file),
                        err=str(exc),
                    ),
                    prefix=prefix,
                )

    if summary.num_patched:
        log_info_safe(
            logger,
            safe_format(
                "Patched donor IDs in {count} XCI file(s)",
                count=summary.num_patched,
            ),
            prefix=prefix,
        )
    return summary


def repair_ip_artifacts(
    output_root: Path,
    logger=None,
    prefix: str = "VIVADO",
) -> Dict[str, int]:
    """Remove stale lock files and ensure Vivado IP files are writable."""
    logger = logger or get_logger(__name__)
    root = Path(output_root)
    if not root.exists():
        log_debug_safe(
            logger,
            safe_format(
                "Output root not found for IP repair: {path}",
                path=str(root),
            ),
            prefix=prefix,
        )
        return {"ip_dirs": 0, "locks_removed": 0, "files_repaired": 0}

    ip_dirs = _discover_ip_dirs(root)
    if not ip_dirs:
        log_debug_safe(
            logger,
            safe_format("No IP directories discovered under {path}", path=str(root)),
            prefix=prefix,
        )
        return {"ip_dirs": 0, "locks_removed": 0, "files_repaired": 0}

    total_locks = 0
    total_repaired = 0
    for ip_dir in ip_dirs:
        total_locks += _remove_lock_files(ip_dir, prefix, logger)
        total_repaired += _ensure_writable(ip_dir, prefix, logger)

    log_info_safe(
        logger,
        safe_format(
            "IP artifact repair complete → dirs={dirs} locks={locks} files={files}",
            dirs=len(ip_dirs),
            locks=total_locks,
            files=total_repaired,
        ),
        prefix=prefix,
    )
    return {
        "ip_dirs": len(ip_dirs),
        "locks_removed": total_locks,
        "files_repaired": total_repaired,
    }
