#!/usr/bin/env python3
"""
CLI entry point for pcileech-build console script.
This module provides the main() function that setuptools will use as an entry point.
"""

import logging
import os
import site
import sys
from pathlib import Path

# Add project root to path for imports
project_root = Path(__file__).parent.parent
sys.path.insert(0, str(project_root))

for site_dir in site.getsitepackages():
    if site_dir not in sys.path:
        sys.path.insert(0, site_dir)

# Add user site-packages
user_site = site.getusersitepackages()
if user_site not in sys.path:
    sys.path.insert(0, user_site)


def main():
    """Main entry point for pcileech-build command"""
    try:
        # Initialize logging if not configured
        if not logging.getLogger().handlers:
            try:
                from .log_config import get_logger, setup_logging
            except Exception:
                # Fallback absolute import if executed differently
                from src.log_config import get_logger, setup_logging
            setup_logging(level=logging.INFO)
        else:
            try:
                from .log_config import get_logger
            except Exception:
                from src.log_config import get_logger
        logger = get_logger("pcileech_build_cli")

        # Import logging helpers
        try:
            from src.string_utils import (log_error_safe, log_info_safe,
                                          log_warning_safe)
        except Exception:
            # If unavailable, rethrow later when used
            raise

        # different import strategies to handle various installation
        # scenarios
        try:
            # First try the standard import (works when installed as package)
            from src.build import main as build_main
        except ImportError:
            # If that fails, try a direct import from the sibling build.py
            try:
                import importlib.util

                build_path = Path(__file__).parent / "build.py"
                spec = importlib.util.spec_from_file_location(
                    "src.build", str(build_path)
                )
                if spec and spec.loader:
                    mod = importlib.util.module_from_spec(spec)
                    sys.modules[spec.name] = mod
                    spec.loader.exec_module(mod)
                    build_main = getattr(mod, "main")
                else:
                    raise ImportError("Could not load build module spec")
            except ImportError:
                log_error_safe(
                    logger, "Error: Could not import build module.", prefix="CLI"
                )
                log_warning_safe(
                    logger,
                    "This could be due to running with sudo without preserving the Python path.",
                    prefix="CLI",
                )
                log_info_safe(
                    logger,
                    "Try using the pcileech-build-sudo script instead.",
                    prefix="CLI",
                )
                return 1

        return build_main()

    except KeyboardInterrupt:
        # Use warning level to match build.py behavior
        try:
            from src.string_utils import log_warning_safe

            from .log_config import get_logger
        except Exception:
            from src.log_config import get_logger
            from src.string_utils import log_warning_safe
        logger = get_logger("pcileech_build_cli")
        log_warning_safe(logger, "Build process interrupted by user", prefix="CLI")
        return 1
    except Exception as e:
        try:
            from src.string_utils import log_error_safe

            from .log_config import get_logger
        except Exception:
            from src.log_config import get_logger
            from src.string_utils import log_error_safe
        logger = get_logger("pcileech_build_cli")
        log_error_safe(
            logger, "Error running build process: {err}", err=str(e), prefix="CLI"
        )
        return 1


if __name__ == "__main__":
    sys.exit(main())
