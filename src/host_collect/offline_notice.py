"""Warnings and risk acceptance for builds from unverified donor captures."""

from __future__ import annotations

import sys
from typing import Final, TextIO

from pcileechfwgenerator.exceptions import ConfigurationError

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
