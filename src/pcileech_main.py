#!/usr/bin/env python3
"""
CLI entry point module for packaging.

This module provides the main entry point for the installed pcileech package.
It routes commands directly to pcileechfwgenerator submodules instead of
delegating to the standalone pcileech.py script.
"""
from __future__ import annotations

import argparse
import logging
import os
import platform
import subprocess
import sys
from pathlib import Path


def _get_version_string() -> str:
    """Get version string for --version flag."""
    try:
        from pcileechfwgenerator.utils.version_resolver import get_version_info

        info = get_version_info()
        title = info.get("title", "PCILeech Firmware Generator")
        version = info.get("version", "unknown")
        return f"{title} v{version}"
    except (ImportError, AttributeError, KeyError):
        try:
            from pcileechfwgenerator.__version__ import __version__

            return f"PCILeech Firmware Generator v{__version__}"
        except ImportError:
            return "PCILeech Firmware Generator (version unknown)"


def _get_known_device_types():
    """Get known device types, with fallback."""
    try:
        from pcileechfwgenerator.utils.validation_constants import KNOWN_DEVICE_TYPES

        return KNOWN_DEVICE_TYPES
    except ImportError:
        return ["generic", "network", "storage", "media", "usb"]


def _create_parser() -> argparse.ArgumentParser:
    """Create the main argument parser with subcommands."""
    parser = argparse.ArgumentParser(
        prog="pcileech",
        description="PCILeech Firmware Generator - Unified Entry Point",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
Commands:
  build          Build firmware (CLI mode)
  tui            Launch interactive TUI
  flash          Flash firmware to device
  check          Check VFIO configuration and ACS bypass requirements
  donor-template Generate donor info template
  version        Show version information

Examples:
  # Interactive TUI mode
  pcileech tui

  # CLI build mode
  sudo pcileech build --bdf 0000:03:00.0 --board pcileech_35t325_x1

  # Check VFIO configuration
  sudo pcileech check --device 0000:03:00.0

  # Flash firmware
  sudo pcileech flash firmware.bin

  # Generate donor template
  sudo pcileech donor-template --save-to my_device.json
        """,
    )

    parser.add_argument("--version", action="version", version=_get_version_string())
    parser.add_argument(
        "--verbose", "-v", action="store_true", help="Enable verbose logging"
    )
    parser.add_argument(
        "--quiet", "-q", action="store_true", help="Suppress non-error messages"
    )
    parser.add_argument(
        "--skip-requirements-check",
        action="store_true",
        help="Skip automatic requirements checking",
    )

    subparsers = parser.add_subparsers(dest="command", help="Available commands")

    # Build command
    build_parser = subparsers.add_parser("build", help="Build firmware (CLI mode)")
    build_parser.add_argument(
        "--bdf", required=True, help="PCI Bus:Device.Function (e.g., 0000:03:00.0)"
    )
    build_parser.add_argument(
        "--board", required=True, help="Target board configuration"
    )
    build_parser.add_argument(
        "--advanced-sv",
        action="store_true",
        help="Enable advanced SystemVerilog features",
    )
    build_parser.add_argument(
        "--enable-variance",
        action="store_true",
        help="Enable manufacturing variance",
    )
    build_parser.add_argument(
        "--enable-error-injection",
        action="store_true",
        help="Enable hardware error injection test hooks (AER)",
    )
    build_parser.add_argument(
        "--build-dir", default="build", help="Directory for generated firmware files"
    )
    build_parser.add_argument(
        "--generate-donor-template",
        help="Generate donor info JSON template alongside build artifacts",
    )
    build_parser.add_argument(
        "--donor-template",
        help="Use donor info JSON template to override discovered values",
    )
    build_parser.add_argument(
        "--device-type",
        choices=_get_known_device_types(),
        default="generic",
        help="Override device type detection (default: auto-detect from class code)",
    )
    build_parser.add_argument(
        "--vivado-path",
        help="Manual path to Vivado installation directory",
    )
    build_parser.add_argument(
        "--vivado-jobs",
        type=int,
        default=4,
        help="Number of parallel jobs for Vivado builds (default: 4)",
    )
    build_parser.add_argument(
        "--vivado-timeout",
        type=int,
        default=3600,
        help="Timeout for Vivado operations in seconds (default: 3600)",
    )
    build_parser.add_argument(
        "--host-collect-only",
        action="store_true",
        help="Stage 1: collect PCIe data on host and exit (no build)",
    )
    build_parser.add_argument(
        "--local",
        action="store_true",
        help="Run full pipeline locally instead of container",
    )
    build_parser.add_argument(
        "--datastore",
        default="pcileech_datastore",
        help="Host dir for device_context.json and outputs",
    )
    build_parser.add_argument(
        "--no-mmio-learning",
        action="store_true",
        help="Disable MMIO trace capture for BAR register learning",
    )
    build_parser.add_argument(
        "--force-recapture",
        action="store_true",
        help="Force recapture of MMIO traces even if cached models exist",
    )
    build_parser.add_argument(
        "--container-mode",
        choices=["auto", "container", "local"],
        default="auto",
        help="Templating execution mode (default: auto)",
    )

    # TUI command
    tui_parser = subparsers.add_parser("tui", help="Launch interactive TUI")
    tui_parser.add_argument(
        "--profile", help="Load configuration profile on startup"
    )

    # Flash command
    flash_parser = subparsers.add_parser("flash", help="Flash firmware to device")
    flash_parser.add_argument("firmware", help="Path to firmware file")
    flash_parser.add_argument("--board", help="Board type for flashing")
    flash_parser.add_argument("--device", help="USB device for flashing")

    # Check command
    check_parser = subparsers.add_parser(
        "check", help="Check VFIO configuration and ACS bypass requirements"
    )
    check_parser.add_argument(
        "--device", help="Specific device to check (BDF format)"
    )
    check_parser.add_argument(
        "--interactive", action="store_true", help="Interactive remediation mode"
    )
    check_parser.add_argument(
        "--fix", action="store_true", help="Attempt to fix issues automatically"
    )

    # Version command
    subparsers.add_parser("version", help="Show version information")

    # Donor template command
    donor_parser = subparsers.add_parser(
        "donor-template", help="Generate donor info template"
    )
    donor_parser.add_argument(
        "--save-to",
        default="donor_info_template.json",
        help="File path to save template (default: donor_info_template.json)",
    )
    donor_parser.add_argument(
        "--compact",
        action="store_true",
        help="Generate compact JSON without indentation",
    )
    donor_parser.add_argument(
        "--blank",
        action="store_true",
        help="Generate minimal template with only essential fields",
    )
    donor_parser.add_argument(
        "--bdf", help="Pre-fill template with device info from specified BDF"
    )
    donor_parser.add_argument(
        "--validate", help="Validate an existing donor info file"
    )

    # Auto-detect command from console script name
    script_name = Path(sys.argv[0]).name
    auto_command = None
    if script_name == "pcileech-build":
        auto_command = "build"
    elif script_name == "pcileech-tui":
        auto_command = "tui"
    elif script_name == "pcileech-generate":
        auto_command = "build"

    if auto_command and not any(arg in sys.argv for arg in subparsers.choices):
        parser.set_defaults(command=auto_command)

    return parser


# ---------------------------------------------------------------------------
# Command handlers - each lazily imports only what it needs
# ---------------------------------------------------------------------------


def _handle_tui(args) -> int:
    """Launch the interactive TUI."""
    from pcileechfwgenerator.log_config import get_logger
    from pcileechfwgenerator.string_utils import log_error_safe, log_info_safe

    logger = get_logger(__name__)
    try:
        try:
            import textual  # noqa: F401
        except ImportError:
            print(
                "Error: Textual framework not installed.\n"
                "Install TUI support with: pip install pcileechfwgenerator[tui]",
                file=sys.stderr,
            )
            return 1

        from pcileechfwgenerator.tui.main import PCILeechTUI

        if platform.system().lower() != "linux":
            log_error_safe(
                logger,
                f"PCILeech requires Linux (current: {platform.system()})",
                prefix="TUI",
            )
            return 1

        log_info_safe(logger, "Launching interactive TUI", prefix="TUI")
        app = PCILeechTUI()
        app.run()
        return 0

    except KeyboardInterrupt:
        log_info_safe(logger, "TUI application interrupted by user", prefix="TUI")
        return 1
    except (ImportError, ModuleNotFoundError) as e:
        print(
            f"Error: TUI module not available: {e}\n"
            "Install TUI support with: pip install pcileechfwgenerator[tui]",
            file=sys.stderr,
        )
        return 1
    except Exception as e:
        try:
            from pcileechfwgenerator.error_utils import log_error_with_root_cause

            log_error_with_root_cause(logger, "TUI failed", e)
        except ImportError:
            print(f"Error: TUI failed: {e}", file=sys.stderr)
        return 1


def _handle_build(args) -> int:
    """Handle CLI build mode."""
    from pcileechfwgenerator.log_config import get_logger
    from pcileechfwgenerator.string_utils import (
        log_error_safe,
        log_info_safe,
        safe_format,
    )

    logger = get_logger(__name__)
    try:
        from pcileechfwgenerator.build import FirmwareBuilder

        builder = FirmwareBuilder(args)
        return builder.run()
    except (ImportError, ModuleNotFoundError) as e:
        log_error_safe(
            logger,
            safe_format("Required module not available: {error}", error=str(e)),
            prefix="BUILD",
        )
        return 1
    except KeyboardInterrupt:
        log_error_safe(logger, "Build interrupted by user", prefix="BUILD")
        return 1
    except Exception as e:
        try:
            from pcileechfwgenerator.error_utils import log_error_with_root_cause

            log_error_with_root_cause(logger, "Build failed", e)
        except ImportError:
            log_error_safe(
                logger,
                safe_format("Build failed: {error}", error=str(e)),
                prefix="BUILD",
            )
        return 1


def _handle_flash(args) -> int:
    """Handle firmware flashing."""
    from pcileechfwgenerator.log_config import get_logger
    from pcileechfwgenerator.string_utils import (
        log_error_safe,
        log_info_safe,
        safe_format,
    )

    logger = get_logger(__name__)
    try:
        firmware_path = Path(args.firmware)
        if not firmware_path.exists():
            log_error_safe(
                logger,
                safe_format("Firmware file not found: {path}", path=firmware_path),
                prefix="FLASH",
            )
            return 1

        log_info_safe(
            logger,
            safe_format("Flashing firmware: {path}", path=firmware_path),
            prefix="FLASH",
        )

        try:
            from pcileechfwgenerator.cli.flash import flash_firmware

            flash_firmware(firmware_path)
        except ImportError:
            try:
                result = subprocess.run(
                    ["usbloader", "-f", str(firmware_path)],
                    capture_output=True,
                    text=True,
                )
                if result.returncode != 0:
                    log_error_safe(
                        logger,
                        safe_format("Flash failed: {error}", error=result.stderr),
                        prefix="FLASH",
                    )
                    return 1
                log_info_safe(
                    logger,
                    safe_format("Successfully flashed {path}", path=firmware_path),
                    prefix="FLASH",
                )
            except FileNotFoundError:
                log_error_safe(
                    logger,
                    "usbloader not found in PATH. "
                    "Install or use the built-in flasher.",
                    prefix="FLASH",
                )
                return 1

        return 0

    except KeyboardInterrupt:
        log_info_safe(logger, "Flash operation interrupted by user", prefix="FLASH")
        return 1
    except Exception as e:
        try:
            from pcileechfwgenerator.error_utils import log_error_with_root_cause

            log_error_with_root_cause(logger, "Flash failed", e)
        except ImportError:
            print(f"Error: Flash failed: {e}", file=sys.stderr)
        return 1


def _handle_check(args) -> int:
    """Handle VFIO checking."""
    from pcileechfwgenerator.log_config import get_logger
    from pcileechfwgenerator.string_utils import (
        log_error_safe,
        log_info_safe,
        safe_format,
    )

    logger = get_logger(__name__)
    try:
        from pcileechfwgenerator.cli.vfio_diagnostics import (
            Diagnostics,
            Status,
            remediation_script,
            render,
        )

        log_info_safe(
            logger,
            safe_format(
                "Running VFIO diagnostics{device}",
                device=f" for device {args.device}" if args.device else "",
            ),
            prefix="CHECK",
        )

        diag = Diagnostics(args.device)
        report = diag.run()
        render(report)

        if args.fix:
            if report.overall == Status.OK:
                log_info_safe(logger, "System is VFIO-ready", prefix="CHECK")
                return 0

            import tempfile

            script_text = remediation_script(report)
            fd, temp_path = tempfile.mkstemp(
                suffix="_vfio_fix.sh", prefix="pcileech_"
            )
            temp = Path(temp_path)
            try:
                os.write(fd, script_text.encode())
            finally:
                os.close(fd)
            temp.chmod(0o700)

            log_info_safe(
                logger,
                safe_format("Remediation script written to {path}", path=temp),
                prefix="CHECK",
            )

            if not args.interactive:
                log_info_safe(
                    logger,
                    safe_format(
                        "Remediation script written to {path}. "
                        "Use --interactive to run it, or execute manually.",
                        path=temp,
                    ),
                    prefix="CHECK",
                )
                return 1

            confirm = input("Run remediation script now? [y/N]: ").strip().lower()
            if confirm not in ("y", "yes"):
                log_info_safe(logger, "Aborted", prefix="CHECK")
                return 1

            log_info_safe(
                logger,
                "Executing remediation script (requires root)",
                prefix="CHECK",
            )
            try:
                subprocess.run(["sudo", str(temp)], check=True)
                log_info_safe(
                    logger,
                    "Re-running diagnostics after remediation",
                    prefix="CHECK",
                )
                new_report = Diagnostics(args.device).run()
                render(new_report)
                return 0 if new_report.can_proceed else 1
            except subprocess.CalledProcessError as e:
                log_error_safe(
                    logger,
                    safe_format(
                        "Remediation script failed: {error}", error=str(e)
                    ),
                    prefix="CHECK",
                )
                return 1
            except (OSError, PermissionError) as e:
                log_error_safe(
                    logger,
                    safe_format(
                        "Cannot execute remediation script: {error}",
                        error=str(e),
                    ),
                    prefix="CHECK",
                )
                return 1

        return 0 if report.can_proceed else 1

    except (ImportError, ModuleNotFoundError) as e:
        log_error_safe(
            logger,
            safe_format(
                "VFIO diagnostics module not available: {error}",
                error=str(e),
            ),
            prefix="CHECK",
        )
        return 1
    except KeyboardInterrupt:
        log_info_safe(logger, "VFIO check interrupted by user", prefix="CHECK")
        return 1
    except Exception as e:
        try:
            from pcileechfwgenerator.error_utils import log_error_with_root_cause

            log_error_with_root_cause(logger, "VFIO check failed", e)
        except ImportError:
            log_error_safe(
                logger,
                safe_format("VFIO check failed: {error}", error=str(e)),
                prefix="CHECK",
            )
        return 1


def _handle_version(args) -> int:
    """Handle version information."""
    from pcileechfwgenerator.log_config import get_logger
    from pcileechfwgenerator.string_utils import (
        log_debug_safe,
        log_info_safe,
        safe_format,
    )

    logger = get_logger(__name__)

    try:
        from pcileechfwgenerator.utils.version_resolver import get_version_info

        version_info = get_version_info()
        version = version_info.get("version", "unknown")
        title = version_info.get("title", "PCILeech Firmware Generator")
        build_date = version_info.get("build_date", "unknown")
        commit_hash = version_info.get("commit_hash", "unknown")

        log_info_safe(
            logger,
            safe_format("{title} v{version}", title=title, version=version),
            prefix="VERSION",
        )
        log_info_safe(
            logger,
            safe_format("Build date: {build_date}", build_date=build_date),
            prefix="VERSION",
        )
        log_info_safe(
            logger,
            safe_format("Commit hash: {commit_hash}", commit_hash=commit_hash),
            prefix="VERSION",
        )

    except (ImportError, ModuleNotFoundError, AttributeError):
        log_info_safe(logger, _get_version_string(), prefix="VERSION")

    log_info_safe(logger, "Copyright (c) 2024 PCILeech Project", prefix="VERSION")
    log_info_safe(logger, "Licensed under MIT License", prefix="VERSION")

    try:
        from importlib.metadata import version as get_pkg_version

        pkg_ver = get_pkg_version("pcileechfwgenerator")
        log_info_safe(
            logger,
            safe_format("Package version: {version}", version=pkg_ver),
            prefix="VERSION",
        )
    except Exception as e:
        log_debug_safe(
            logger,
            safe_format(
                "Package version info unavailable: {error}", error=str(e)
            ),
        )

    return 0


def _handle_donor_template(args) -> int:
    """Handle donor template generation."""
    from pcileechfwgenerator.log_config import get_logger
    from pcileechfwgenerator.string_utils import (
        log_error_safe,
        log_info_safe,
        safe_format,
    )

    logger = get_logger(__name__)
    try:
        from pcileechfwgenerator.device_clone.donor_info_template import (
            DonorInfoTemplateGenerator,
        )

        if args.validate:
            try:
                validator = DonorInfoTemplateGenerator()
                is_valid, errors = validator.validate_template_file(args.validate)
                if is_valid:
                    log_info_safe(
                        logger,
                        safe_format(
                            "Template file '{file}' is valid", file=args.validate
                        ),
                        prefix="DONOR",
                    )
                    return 0
                else:
                    log_error_safe(
                        logger,
                        safe_format(
                            "Template file '{file}' has validation errors:",
                            file=args.validate,
                        ),
                        prefix="DONOR",
                    )
                    for error in errors:
                        log_error_safe(logger, f"  - {error}", prefix="DONOR")
                    return 1
            except (OSError, IOError) as e:
                log_error_safe(
                    logger,
                    safe_format(
                        "Cannot read template file: {error}", error=str(e)
                    ),
                    prefix="DONOR",
                )
                return 1

        generator = DonorInfoTemplateGenerator()

        if args.bdf:
            log_info_safe(
                logger,
                safe_format(
                    "Generating template with device info from {bdf}", bdf=args.bdf
                ),
                prefix="DONOR",
            )
            try:
                template = generator.generate_template_from_device(args.bdf)
                if template["device_info"]["identification"]["vendor_id"] is None:
                    log_error_safe(
                        logger,
                        safe_format(
                            "Failed to read device information from {bdf}",
                            bdf=args.bdf,
                        ),
                        prefix="DONOR",
                    )
                    return 1
            except (subprocess.SubprocessError, OSError, PermissionError) as e:
                log_error_safe(
                    logger,
                    safe_format(
                        "Cannot access device information: {error}", error=str(e)
                    ),
                    prefix="DONOR",
                )
                return 1
        elif args.blank:
            template = generator.generate_minimal_template()
            log_info_safe(
                logger, "Generating minimal donor info template", prefix="DONOR"
            )
        else:
            template = generator.generate_blank_template()

        generator.save_template_dict(
            template, Path(args.save_to), pretty=not args.compact
        )
        log_info_safe(
            logger,
            safe_format("Donor info template saved to: {file}", file=args.save_to),
            prefix="DONOR",
        )
        return 0

    except KeyboardInterrupt:
        log_info_safe(
            logger,
            "Donor template generation interrupted by user",
            prefix="DONOR",
        )
        return 1
    except Exception as e:
        try:
            from pcileechfwgenerator.error_utils import log_error_with_root_cause

            log_error_with_root_cause(
                logger, "Failed to generate donor template", e
            )
        except ImportError:
            print(f"Error: Failed to generate donor template: {e}", file=sys.stderr)
        return 1


# ---------------------------------------------------------------------------
# Main entry point
# ---------------------------------------------------------------------------


def main() -> int:
    """Main entry point for installed package.

    Routes commands to the appropriate handler using only pcileechfwgenerator
    package internals.
    """
    try:
        from pcileechfwgenerator.log_config import get_logger, setup_logging
    except ImportError as e:
        print(
            f"Error: Could not import pcileechfwgenerator: {e}",
            file=sys.stderr,
        )
        print(
            "Please ensure the package is properly installed.",
            file=sys.stderr,
        )
        return 1

    parser = _create_parser()
    args = parser.parse_args()

    # Setup logging
    if args.verbose:
        setup_logging(level=logging.DEBUG)
    elif args.quiet:
        setup_logging(level=logging.ERROR)
    else:
        setup_logging(level=logging.INFO)

    # Handle console script auto-command detection
    if not args.command:
        script_name = Path(sys.argv[0]).name
        if script_name == "pcileech-build":
            args.command = "build"
        elif script_name == "pcileech-tui":
            args.command = "tui"
        elif script_name == "pcileech-generate":
            args.command = "build"
        else:
            parser.print_help()
            return 1

    # Route to handler
    handlers = {
        "build": _handle_build,
        "tui": _handle_tui,
        "flash": _handle_flash,
        "check": _handle_check,
        "version": _handle_version,
        "donor-template": _handle_donor_template,
    }

    handler = handlers.get(args.command)
    if handler:
        return handler(args)

    parser.print_help()
    return 1


if __name__ == "__main__":  # pragma: no cover
    sys.exit(main())
