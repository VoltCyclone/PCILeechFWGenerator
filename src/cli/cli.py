#!/usr/bin/env python3
"""cli - one front‑door for the whole tool‑chain.

Usage examples
~~~~~~~~~~~~~~
    # guided build flow (device & board pickers)
    ./cli build

    # scripted build for CI (non‑interactive)
    ./cli build --bdf 0000:01:00.0 --board pcileech_75t484_x1 --advanced-sv

    # flash an already‑generated bitstream
    ./cli flash output/firmware.bin --board pcileech_75t484_x1
"""
from __future__ import annotations

import argparse
import logging
import os
import re
import subprocess
import sys
from pathlib import Path
from types import SimpleNamespace
from typing import Dict, List, Optional

try:
    # Try direct import first (when run as a module)
    from log_config import get_logger, setup_logging
    from shell import Shell
except ImportError:
    # Fallback to absolute import (when run as a script)
    from src.log_config import get_logger, setup_logging
    from src.shell import Shell

from ..string_utils import log_error_safe, log_info_safe, log_warning_safe, safe_format
from .build_constants import (
    DEFAULT_ACTIVE_INTERRUPT_MODE,
    DEFAULT_ACTIVE_INTERRUPT_VECTOR,
    DEFAULT_ACTIVE_PRIORITY,
    DEFAULT_ACTIVE_TIMER_PERIOD,
)
from .version_checker import add_version_args, check_and_notify

logger = get_logger(__name__)

# ──────────────────────────────────────────────────────────────────────────────
# Helpers - PCIe enumeration & interactive pickers
# ──────────────────────────────────────────────────────────────────────────────
PCI_RE = re.compile(
    r"(?P<bdf>[0-9a-fA-F:.]+) .*?\["
    r"(?P<class>[0-9a-fA-F]{4})\]: .*?\["
    r"(?P<ven>[0-9a-fA-F]{4}):(?P<dev>[0-9a-fA-F]{4})\]"
)


def list_pci_devices() -> List[Dict[str, str]]:
    out = Shell().run("lspci -Dnn")
    devs: list[dict[str, str]] = []
    for line in out.splitlines():
        m = PCI_RE.match(line)
        if m:
            d = m.groupdict()
            d["pretty"] = line
            devs.append(d)
    return devs


def pick(lst: list[str], prompt: str) -> str:
    for i, item in enumerate(lst):
        print(f" [{i}] {item}")
    while True:
        sel = input(prompt).strip()
        if not sel and lst:
            return lst[0]
        try:
            return lst[int(sel)]
        except Exception:
            print("  Invalid selection - try again.")


def choose_device() -> Dict[str, str]:
    devs = list_pci_devices()
    if not devs:
        raise RuntimeError("No PCIe devices found - are you root?")
    for i, dev in enumerate(devs):
        print(f" [{i}] {dev['pretty']}")
    return devs[int(input("Select donor device #: "))]


# Use dynamic board discovery for supported boards
from src.device_clone.board_config import list_supported_boards


def get_supported_boards():
    return list_supported_boards()


def resolve_board_name(board_input: str) -> str:
    """Resolve board shorthand to full board name.
    
    Args:
        board_input: User-provided board name or shorthand (e.g., "35t", "75t484")
    
    Returns:
        Full board name (e.g., "pcileech_35t325_x1")
    
    Raises:
        ValueError: If board cannot be resolved
    """
    supported = get_supported_boards()
    
    # Exact match
    if board_input in supported:
        return board_input
    
    # Try with pcileech_ prefix
    with_prefix = f"pcileech_{board_input}"
    if with_prefix in supported:
        return with_prefix
    
    # Try fuzzy match - find boards containing the input
    matches = [b for b in supported if board_input.lower() in b.lower()]
    
    if len(matches) == 1:
        log_info_safe(
            logger,
            safe_format(
                "Board shorthand '{short}' resolved to '{full}'",
                short=board_input,
                full=matches[0],
            ),
            prefix="BOARD",
        )
        return matches[0]
    elif len(matches) > 1:
        raise ValueError(
            safe_format(
                "Ambiguous board shorthand '{short}'. Multiple matches: {matches}. "
                "Please specify one of: {boards}",
                short=board_input,
                matches=", ".join(matches),
                boards=", ".join(supported),
            )
        )
    else:
        raise ValueError(
            safe_format(
                "Unknown board '{board}'. Supported boards: {boards}",
                board=board_input,
                boards=", ".join(supported),
            )
        )


# ──────────────────────────────────────────────────────────────────────────────
# CLI setup
# ──────────────────────────────────────────────────────────────────────────────


def build_sub(parser: argparse._SubParsersAction):
    p = parser.add_parser("build", help="Build firmware (guided or scripted)")
    p.add_argument("--bdf", help="PCI BDF (skip for interactive picker)")
    p.add_argument(
        "--board",
        choices=get_supported_boards(),
        help="FPGA board name or shorthand (e.g., 'pcileech_35t325_x1' or '35t')",
    )
    p.add_argument(
        "--advanced-sv", action="store_true", help="Enable advanced SV features"
    )
    p.add_argument("--enable-variance", action="store_true", help="Enable variance")
    p.add_argument(
        "--enable-error-injection",
        action="store_true",
        help=(
            "Enable hardware error injection test hooks (AER). "
            "Disabled by default; use only in controlled test scenarios."
        ),
    )
    p.add_argument(
        "--auto-fix",
        action="store_true",
        help="Let VFIOBinder auto-remediate issues",
    )
    p.add_argument(
        "--output-template",
        help="Output donor info JSON template alongside build artifacts",
    )
    p.add_argument(
        "--donor-template",
        help="Use donor info JSON template to override discovered values",
    )
    p.add_argument(
        "--dynamic-image",
        action="store_true",
        help=(
            "Derive container image tag from project version and feature flags "
            "(improves cache reuse across builds)"
        ),
    )

    # Add active device configuration group
    active_group = p.add_argument_group("Active Device Configuration")
    active_group.add_argument(
        "--disable-active-device",
        action="store_true",
        help="Disable active device interrupts (enabled by default)",
    )
    active_group.add_argument(
        "--active-timer-period",
        type=int,
        default=DEFAULT_ACTIVE_TIMER_PERIOD,
        help=(
            "Timer period in clock cycles (default: " f"{DEFAULT_ACTIVE_TIMER_PERIOD})"
        ),
    )
    active_group.add_argument(
        "--active-interrupt-mode",
        choices=["msi", "msix", "intx"],
        default=DEFAULT_ACTIVE_INTERRUPT_MODE,
        help=(
            "Interrupt mode for active device "
            f"(default: {DEFAULT_ACTIVE_INTERRUPT_MODE})"
        ),
    )
    active_group.add_argument(
        "--active-interrupt-vector",
        type=int,
        default=DEFAULT_ACTIVE_INTERRUPT_VECTOR,
        help=(
            "Interrupt vector to use (default: " f"{DEFAULT_ACTIVE_INTERRUPT_VECTOR})"
        ),
    )
    active_group.add_argument(
        "--active-priority",
        type=int,
        default=DEFAULT_ACTIVE_PRIORITY,
        help=(
            "Interrupt priority 0-15 (default: " f"{DEFAULT_ACTIVE_PRIORITY}, highest)"
        ),
    )

    # Add fallback control group
    fallback_group = p.add_argument_group("Fallback Control")
    fallback_group.add_argument(
        "--fallback-mode",
        choices=["none", "prompt", "auto"],
        default="none",
        help="Control fallback behavior (none=fail-fast, prompt=ask, auto=allow)",
    )
    fallback_group.add_argument(
        "--allow-fallbacks",
        type=str,
        help="Comma-separated list of allowed fallbacks",
    )
    fallback_group.add_argument(
        "--deny-fallbacks", type=str, help="Comma-separated list of denied fallbacks"
    )
    fallback_group.add_argument(
        "--legacy-compatibility",
        action="store_true",
        help=(
            "Enable legacy compatibility mode "
            "(temporarily restores old fallback behavior)"
        ),
    )


def flash_sub(parser: argparse._SubParsersAction):
    p = parser.add_parser("flash", help="Flash a firmware binary via usbloader")
    p.add_argument("firmware", help="Path to .bin")
    p.add_argument(
        "--board",
        required=True,
        help="FPGA board name or shorthand (e.g., 'pcileech_35t325_x1' or '35t')",
    )


def donor_template_sub(parser: argparse._SubParsersAction):
    p = parser.add_parser("donor-template", help="Generate a donor info JSON template")
    p.add_argument(
        "-o",
        "--output",
        type=Path,
        default=Path("donor_info_template.json"),
        help="Output file path (default: donor_info_template.json)",
    )
    p.add_argument(
        "--compact",
        action="store_true",
        help="Generate compact JSON without indentation",
    )
    p.add_argument(
        "--with-comments",
        action="store_true",
        help="Generate template with explanatory $comment fields (valid JSON)",
    )


def get_parser() -> argparse.ArgumentParser:
    ap = argparse.ArgumentParser("cli", description=__doc__)

    # Add version checking arguments
    add_version_args(ap)

    sub = ap.add_subparsers(
        dest="cmd",
        required=False,  # Make optional for --check-version
        help="Command to run (build/flash)",
    )
    build_sub(sub)
    flash_sub(sub)
    donor_template_sub(sub)
    return ap


def flash_bin(path: Path):
    from .flash import flash_firmware

    flash_firmware(path)
    log_info_safe(logger, "Firmware flashed successfully ✓")


# ──────────────────────────────────────────────────────────────────────────────
# Entrypoint
# ──────────────────────────────────────────────────────────────────────────────


def main(argv: Optional[List[str]] = None):
    # Setup proper logging with color support
    setup_logging(level=logging.INFO)

    args = get_parser().parse_args(argv)

    # Handle version check arguments
    if hasattr(args, "check_version") and args.check_version:
        from .version_checker import check_for_updates, prompt_for_update

        result = check_for_updates(force=True)
        if result:
            latest_version, update_available = result
            if update_available:
                prompt_for_update(latest_version)
            else:
                from ..__version__ import __version__
                
                log_info_safe(
                    logger,
                    safe_format(
                        "✓ You are running the latest version ({current_version})",
                        current_version=__version__,
                    ),
                    prefix="VERS",
                )
        else:
            log_warning_safe(
                logger, safe_format("Unable to check for updates"), prefix="VERS"
            )
        sys.exit(0)

    # Check for updates unless explicitly skipped
    if not (hasattr(args, "skip_version_check") and args.skip_version_check):
        check_and_notify()

    # If no command specified, we're done (e.g., --check-version only)
    if not args.cmd:
        return

    if args.cmd == "build":
        bdf = args.bdf or choose_device()["bdf"]
        board_input = args.board or pick(get_supported_boards(), "Board #: ")
        
        # Resolve board shorthand to full name
        try:
            board = resolve_board_name(board_input)
        except ValueError as e:
            log_error_safe(logger, str(e), prefix="BOARD")
            sys.exit(1)
        
        # Process fallback lists
        allowed_fallbacks = []
        if hasattr(args, "allow_fallbacks") and args.allow_fallbacks:
            allowed_fallbacks = [f.strip() for f in args.allow_fallbacks.split(",")]

        denied_fallbacks = []
        if hasattr(args, "deny_fallbacks") and args.deny_fallbacks:
            denied_fallbacks = [f.strip() for f in args.deny_fallbacks.split(",")]

        # Determine fallback mode based on legacy compatibility flag
        fallback_mode = getattr(args, "fallback_mode", "none")
        if (
            hasattr(args, "legacy_compatibility")
            and args.legacy_compatibility
            and fallback_mode == "none"
        ):
            log_warning_safe(
                logger,
                "Legacy compatibility mode enabled - using 'auto' fallback mode",
                prefix="BUILD",
            )
            fallback_mode = "auto"
            if not allowed_fallbacks:
                allowed_fallbacks = [
                    "config-space",
                    "msix",
                    "behavior-profiling",
                    "build-integration",
                ]

        # Validate board parameter before launching build to fail fast
        if not board or not board.strip():
            log_error_safe(
                logger,
                "Board name is required. Use --board to specify a valid board "
                "configuration (e.g., pcileech_100t484_x1)",
                prefix="BUILD",
            )
            sys.exit(2)

        # Use the unified flow through pcileech.py
        # Build arguments for pcileech.py main()
        build_args = SimpleNamespace(
            bdf=bdf,
            board=board,
            advanced_sv=args.advanced_sv,
            enable_variance=args.enable_variance,
            auto_fix=args.auto_fix,
            fallback_mode=fallback_mode,
            allowed_fallbacks=allowed_fallbacks,
            dynamic_image=getattr(args, "dynamic_image", False),
            denied_fallbacks=denied_fallbacks,
            disable_active_device=getattr(args, "disable_active_device", False),
            active_timer_period=getattr(args, "active_timer_period", 100000),
            active_interrupt_mode=getattr(args, "active_interrupt_mode", "msi"),
            active_interrupt_vector=getattr(args, "active_interrupt_vector", 0),
            active_priority=getattr(args, "active_priority", 15),
            output_template=getattr(args, "output_template", None),
            donor_template=getattr(args, "donor_template", None),
            enable_error_injection=getattr(args, "enable_error_injection", False),
        )
        
        # Import and call the unified entry point
        try:
            import pcileech
            exit_code = pcileech.main(build_args)
            sys.exit(exit_code)
        except Exception as e:
            log_error_safe(
                logger,
                safe_format("Build failed: {error}", error=str(e)),
                prefix="BUILD"
            )
            sys.exit(1)

    elif args.cmd == "flash":
        # Resolve board shorthand
        try:
            board = resolve_board_name(args.board)
        except ValueError as e:
            log_error_safe(logger, str(e), prefix="BOARD")
            sys.exit(1)
        # Update args with resolved board name for flash_bin
        args.board = board
        flash_bin(Path(args.firmware))

    elif args.cmd == "donor-template":
        from ..device_clone.donor_info_template import DonorInfoTemplateGenerator

        if args.with_comments:
            # Generate template with comments (for documentation)
            template_str = DonorInfoTemplateGenerator.generate_template_with_comments()
            with open(args.output, "w") as f:
                f.write(template_str)
            log_info_safe(
                logger,
                safe_format(
                    "✓ Donor info template with comments saved to: {output}",
                    output=args.output,
                ),
                prefix="BUILD",
            )
        else:
            # Generate valid JSON template
            DonorInfoTemplateGenerator.save_template(
                args.output, pretty=not args.compact
            )
            log_info_safe(
                logger,
                safe_format(
                    "✓ Donor info template saved to: {output}",
                    output=args.output,
                ),
                prefix="BUILD",
            )

        log_info_safe(logger, safe_format("\nNext steps:"))
        log_info_safe(
            logger, safe_format("1. Fill in the device-specific values in the template")
        )
        log_info_safe(
            logger, safe_format("2. Run behavioral profiling to capture timing data")
        )
        log_info_safe(
            logger,
            safe_format("3. Use the completed template for advanced device cloning"),
        )


if __name__ == "__main__":
    main()
