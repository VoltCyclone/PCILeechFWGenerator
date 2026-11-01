"""Bind donor captures to the host that collected them.

Offline (``--from-capture``) use of a capture is only permitted on the machine
that collected it. The binding is a salted SHA-256 of the systemd machine ID,
so captures never carry the raw identifier. This deters casual sharing of
captures; it is not tamper-proof (the project is open source and the JSON is
editable).
"""

from __future__ import annotations

import hashlib
from pathlib import Path
from typing import Any, Final

from .snapshot import DonorSnapshot, SnapshotError

HOST_BINDING_KEY: Final[str] = "host_binding"
MACHINE_ID_PATHS: Final[tuple[Path, ...]] = (
    Path("/etc/machine-id"),
    Path("/var/lib/dbus/machine-id"),
)
_SALT: Final[bytes] = b"pcileechfwgenerator-host-binding-v1\0"


def host_fingerprint() -> str:
    """Return this host's binding fingerprint, or raise if it has no machine ID."""
    for path in MACHINE_ID_PATHS:
        try:
            machine_id = path.read_text(encoding="ascii").strip()
        except (OSError, UnicodeDecodeError):
            continue
        if machine_id:
            return hashlib.sha256(_SALT + machine_id.encode("ascii")).hexdigest()
    raise SnapshotError(
        "cannot identify this host for capture binding (no /etc/machine-id)"
    )


def binding_record() -> dict[str, Any] | None:
    """Binding stored in a new capture, or None when the host is unidentifiable.

    A capture without a binding still serves live builds on this host; it is
    refused for offline use.
    """
    try:
        fingerprint = host_fingerprint()
    except SnapshotError:
        return None
    return {
        "schema_version": 1,
        "method": "machine-id-sha256",
        "fingerprint": fingerprint,
    }


def verify_host_binding(snapshot: DonorSnapshot) -> None:
    """Refuse offline use of a capture on any host but the collecting one."""
    binding = snapshot.context.get(HOST_BINDING_KEY)
    if (
        not isinstance(binding, dict)
        or binding.get("schema_version") != 1
        or binding.get("method") != "machine-id-sha256"
        or not isinstance(binding.get("fingerprint"), str)
    ):
        raise SnapshotError(
            "capture is not bound to a collecting host; offline builds need a "
            "capture collected by this version on this machine"
        )
    if binding["fingerprint"] != host_fingerprint():
        raise SnapshotError(
            "capture was collected on a different machine; offline builds are "
            "only permitted on the collecting host"
        )
