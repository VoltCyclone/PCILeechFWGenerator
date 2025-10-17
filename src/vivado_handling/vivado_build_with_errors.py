#!/usr/bin/env python3
"""
Vivado Build Script with Enhanced Error Reporting

This script demonstrates the enhanced Vivado error reporting system
for console builds.
"""

import argparse
import logging
import sys
from pathlib import Path

from ..string_utils import log_error_safe, log_info_safe, safe_format

try:
    from .vivado_error_reporter import (
        VivadoErrorReporter,
        run_vivado_with_error_reporting,
    )
    from .vivado_utils import find_vivado_installation
except ImportError as e:
    print(f"Error importing modules: {e}")
    print("Make sure you're running from the project root directory")
    sys.exit(1)


def setup_logging(verbose: bool = False):
    """Setup logging configuration."""
    level = logging.DEBUG if verbose else logging.INFO
    logging.basicConfig(
        level=level,
        format="%(asctime)s - %(levelname)s - %(message)s",
        handlers=[logging.StreamHandler()],
    )


def main():
    """Main entry point."""
    parser = argparse.ArgumentParser(
        description="Run Vivado build with enhanced error reporting"
    )
    parser.add_argument("tcl_script", type=Path, help="TCL script to execute")
    parser.add_argument(
        "--output-dir",
        "-o",
        type=Path,
        default=Path("."),
        help="Output directory (default: current directory)",
    )
    parser.add_argument(
        "--vivado-exe", help="Path to Vivado executable (auto-detected if not provided)"
    )
    parser.add_argument(
        "--no-colors", action="store_true", help="Disable colored output"
    )
    parser.add_argument(
        "--verbose", "-v", action="store_true", help="Enable verbose logging"
    )
    parser.add_argument(
        "--analyze-log",
        type=Path,
        help="Analyze existing Vivado log file instead of running build",
    )

    args = parser.parse_args()

    setup_logging(args.verbose)
    logger = logging.getLogger(__name__)

    # Create output directory if it doesn't exist
    args.output_dir.mkdir(parents=True, exist_ok=True)

    if args.analyze_log:
        # Analyze existing log file
        log_info_safe(
            logger,
            "Analyzing log file: {logfile}",
            logfile=str(args.analyze_log),
            prefix="ANALYZE",
        )

        if not args.analyze_log.exists():
            log_error_safe(
                logger,
                "Log file not found: {logfile}",
                logfile=str(args.analyze_log),
                prefix="ANALYZE",
            )
            return 1

        reporter = VivadoErrorReporter(use_colors=not args.no_colors)
        errors, warnings = reporter.parser.parse_log_file(args.analyze_log)

        # Generate report
        report = reporter.generate_error_report(
            errors,
            warnings,
            "Log Analysis",
            args.output_dir / "error_analysis_report.txt",
        )

        print(report)
        reporter.print_summary(errors, warnings)

        return 1 if errors else 0

    else:
        # Run Vivado build
        if not args.tcl_script.exists():
            log_error_safe(
                logger,
                "TCL script not found: {tcl_script}",
                tcl_script=str(args.tcl_script),
            )
            return 1

        log_info_safe(
            logger,
            safe_format(
                "Running Vivado build: {tcl_script}",
                tcl_script=str(args.tcl_script),
            ),
            prefix="BUILD",
        )
        log_info_safe(
            logger,
            safe_format(
                "Output directory: {output_dir}", output_dir=str(args.output_dir)
            ),
            prefix="BUILD",
        )

        # Check Vivado installation
        if not args.vivado_exe:
            vivado_info = find_vivado_installation()
            if not vivado_info:
                log_error_safe(logger, "Vivado installation not found", prefix="BUILD")
                log_error_safe(
                    logger,
                    "Please ensure Vivado is in PATH or use --vivado-exe",
                    prefix="BUILD",
                )
                return 1
            args.vivado_exe = vivado_info["executable"]
            log_info_safe(
                logger,
                safe_format(
                    "Found Vivado: {vivado_exe}", vivado_exe=str(args.vivado_exe)
                ),
                prefix="BUILD",
            )

        # Run build with error reporting
        try:
            return_code, report = run_vivado_with_error_reporting(
                args.tcl_script, args.output_dir, args.vivado_exe
            )

            log_info_safe(
                logger,
                safe_format(
                    "Build completed with return code: {return_code}",
                    return_code=return_code,
                ),
                prefix="BUILD",
            )

            return return_code

        except Exception as e:
            log_error_safe(
                logger,
                safe_format("Build failed with exception: {error}", error=e),
                prefix="BUILD",
            )
            return 1


if __name__ == "__main__":
    sys.exit(main())
