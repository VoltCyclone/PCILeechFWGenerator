"""Warnings and risk acceptance for builds from unverified donor captures."""

from __future__ import annotations

import sys
from pathlib import Path
from typing import TYPE_CHECKING, Final, TextIO

from pcileechfwgenerator.exceptions import ConfigurationError
from pcileechfwgenerator.log_config import get_logger
from pcileechfwgenerator.string_utils import log_warning_safe, safe_format

if TYPE_CHECKING:
    from .snapshot import DonorSnapshot

logger = get_logger(__name__)

ACCEPT_FLAG: Final[str] = "--accept-offline-risk"

OFFLINE_BUILD_WARNING: Final[str] = """\
################################################################################
##                                                                            ##
##              WARNING: UNVERIFIED OFFLINE FIRMWARE BUILD                    ##
##                                                                            ##
################################################################################

This build uses a SAVED donor capture. The donor device was NOT read or
verified during this run; the firmware's PCIe identity comes entirely from
the capture files.

  * The donor's identity cannot be confirmed. A stale, copied or edited
    capture yields firmware presenting as a device you may not possess.
  * BAR contents are derived, not sampled from hardware. MSI-X table
    entries and behaviour profiles are not captured.
  * Captures can only be used offline on the machine that collected them.

Only use this firmware with hardware you own, against systems you own or are
explicitly authorised to test. See SECURITY.md and the legal notice in the
README. You are responsible for how this firmware is used.
################################################################################
"""


def confirm_offline_build(
    accepted: bool,
    *,
    warn: bool = True,
    stdin: TextIO | None = None,
    stderr: TextIO | None = None,
) -> None:
    """Require explicit acceptance of an unverified offline build.

    Prints the warning (unless ``warn`` is False). Already accepted
    (``--accept-offline-risk``): continue with a one-line notice. At a
    terminal: ask y/N. Otherwise refuse.
    """
    stdin = sys.stdin if stdin is None else stdin
    stderr = sys.stderr if stderr is None else stderr
    if warn:
        print(OFFLINE_BUILD_WARNING, file=stderr)
    if accepted:
        print(
            f"Unverified offline build: risk accepted via {ACCEPT_FLAG}.",
            file=stderr,
        )
        return
    if not (hasattr(stdin, "isatty") and stdin.isatty()):
        raise ConfigurationError(
            "Unverified offline build needs confirmation: run it at a terminal "
            f"or pass {ACCEPT_FLAG}"
        )
    print("Continue with this unverified build? [y/N]: ", end="", file=stderr)
    stderr.flush()
    answer = stdin.readline().strip().lower()
    if answer not in ("y", "yes"):
        raise ConfigurationError(
            "Offline build cancelled: the unverified-build risk was not accepted"
        )


CAPTURE_WARNING_NAME: Final[str] = "CAPTURE_WARNING.txt"
BUILD_WARNING_NAME: Final[str] = "OFFLINE_BUILD_WARNING.txt"

CAPTURE_WARNING: Final[str] = """\
################################################################################
##                 WARNING: DONOR PCIe IDENTITY CAPTURE                       ##
################################################################################

device_context.json and msix_data.json in this directory hold the PCIe
identity (IDs, configuration space, BAR layout) of a real donor device.
Firmware built from them presents itself as that device.

  * Treat these files as sensitive. Do not publish or share them.
  * Offline builds from this capture only run on the machine that collected
    it; other machines refuse it.
  * Builds from a saved capture are UNVERIFIED and require explicitly
    accepting the risk (interactive prompt or --accept-offline-risk).

Only use firmware built from this capture with hardware you own, against
systems you own or are explicitly authorised to test. See SECURITY.md and the
legal notice in the README.
"""


def write_capture_warning(directory: Path) -> None:
    """Place CAPTURE_WARNING.txt beside a published capture (never replaced)."""
    try:
        with open(directory / CAPTURE_WARNING_NAME, "x", encoding="utf-8") as f:
            f.write(CAPTURE_WARNING)
    except FileExistsError:
        pass
    except OSError as error:
        log_warning_safe(
            logger,
            safe_format("Could not write capture warning: {err}", err=str(error)),
            prefix="CAPTURE",
        )


def write_build_warning(output_dir: Path, snapshot: "DonorSnapshot") -> Path:
    """Write OFFLINE_BUILD_WARNING.txt describing an unverified build."""
    from datetime import datetime, timezone

    from .host_binding import HOST_BINDING_KEY

    identity = snapshot.identity
    binding = snapshot.context.get(HOST_BINDING_KEY) or {}
    details = "\n".join(
        (
            "Build details",
            "-------------",
            "Trust level:        unverified (built from a saved capture)",
            "Risk accepted:      yes",
            f"Built at (UTC):     {datetime.now(timezone.utc).isoformat()}",
            f"Capture ID:         {identity.capture_id}",
            f"Capture collected:  {identity.collected_at.isoformat()}",
            f"Capture address:    {identity.bdf}",
            f"Config SHA-256:     {identity.config_space_sha256}",
            "Collecting host:    "
            + str(binding.get("fingerprint", "unknown"))[:16]
            + " (machine-id hash prefix)",
        )
    )
    path = Path(output_dir) / BUILD_WARNING_NAME
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(f"{OFFLINE_BUILD_WARNING}\n{details}\n", encoding="utf-8")
    return path
