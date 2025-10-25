#!/usr/bin/env python3
"""
CLI entry point for pcileech-build console script.
This module provides the main() function that setuptools will use as an entry point.
"""

import logging
import sys
from pathlib import Path

# Add project root to path for imports
project_root = Path(__file__).parent.parent
if str(project_root) not in sys.path:
    sys.path.insert(0, str(project_root))

from src.string_utils import (
    log_error_safe,
    log_info_safe,
    log_warning_safe,
)
from src.log_config import get_logger, setup_logging


def main():
    """Main entry point for pcileech-build command"""
    # Initialize logging if not configured
    if not logging.getLogger().handlers:
        setup_logging(level=logging.INFO)

    logger = get_logger("pcileech_build_cli")

    try:
        from src.build import main as build_main

        return build_main()
    except ImportError as e:
        log_error_safe(
            logger,
            "Could not import build module: {err}",
            err=str(e),
            prefix="CLI",
        )
        log_warning_safe(
            logger,
            "This may be due to running with sudo without preserving PYTHONPATH",
            prefix="CLI",
        )
        log_info_safe(
            logger, "Try: sudo -E python3 -m src.build_cli", prefix="CLI"
        )
        return 1
    except KeyboardInterrupt:
        log_warning_safe(
            logger, "Build process interrupted by user", prefix="CLI"
        )
        return 1
    except Exception as e:
        log_error_safe(
            logger, "Build process failed: {err}", err=str(e), prefix="CLI"
        )
        return 1


if __name__ == "__main__":
    sys.exit(main())
