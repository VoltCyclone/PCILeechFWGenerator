#!/usr/bin/env python3
"""
Production Configuration Validator

This script validates the production mode configuration and provides
recommendations for proper setup.

Usage:
    python3 scripts/validate_production_config.py

Environment Variables:
    PCILEECH_PRODUCTION_MODE - Set to 'true' for production mode
    PCILEECH_ALLOW_MOCK_DATA - Set to 'false' for strict production mode
"""

import os
import platform
import sys
from pathlib import Path

# Add src to path for imports
sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))


def check_environment_variables():
    """Check and display environment variable configuration."""
    print("=== Environment Variable Configuration ===")

    production_mode = os.getenv("PCILEECH_PRODUCTION_MODE", "false").lower() == "true"
    allow_mock_data = os.getenv("PCILEECH_ALLOW_MOCK_DATA", "true").lower() == "true"

    print(f"PCILEECH_PRODUCTION_MODE: {production_mode}")
    print(f"PCILEECH_ALLOW_MOCK_DATA: {allow_mock_data}")

    # Determine configuration type
    if production_mode and not allow_mock_data:
        config_type = "Strict Production Mode"
        status = "✓ RECOMMENDED for production builds"
    elif production_mode and allow_mock_data:
        config_type = "Production Mode with Fallback"
        status = "⚠ WARNING: Mock data allowed in production mode"
    elif not production_mode and allow_mock_data:
        config_type = "Development Mode"
        status = "✓ OK for development/testing"
    else:
        config_type = "Development Mode (No Mock)"
        status = "⚠ Unusual: Development mode without mock fallback"

    print(f"Configuration Type: {config_type}")
    print(f"Status: {status}")
    print()

    return production_mode, allow_mock_data


def check_system_requirements():
    """Check system requirements for production mode."""
    print("=== System Requirements ===")

    # Check operating system
    is_linux = platform.system().lower() == "linux"
    print(f"Operating System: {platform.system()} {'✓' if is_linux else '✗'}")
    if not is_linux:
        print("  WARNING: Driver scraping and behavior profiling require Linux")

    # Check kernel source availability
    kernel_sources = (
        list(Path("/usr/src").glob("linux-source-*"))
        if Path("/usr/src").exists()
        else []
    )
    print(
        f"Kernel Sources: {len(kernel_sources)} found {'✓' if kernel_sources else '✗'}"
    )
    if kernel_sources:
        for source in kernel_sources[:3]:  # Show first 3
            print(f"  - {source.name}")
    else:
        print("  WARNING: No kernel sources found in /usr/src/")
        print("  Install with: apt-get install linux-source (Ubuntu/Debian)")
        print("               yum install kernel-devel (RHEL/CentOS)")

    # Check for required tools
    tools = {
        "modprobe": "Module utilities (required)",
        "rg": "Ripgrep (optional, improves performance)",
        "lspci": "PCI utilities (helpful for debugging)",
    }

    print("Required Tools:")
    for tool, description in tools.items():
        try:
            import subprocess

            result = subprocess.run(["which", tool], capture_output=True, text=True)
            available = result.returncode == 0
            print(f"  {tool}: {'✓' if available else '✗'} {description}")
            if available:
                # Get version info for some tools
                if tool == "lspci":
                    try:
                        version_result = subprocess.run(
                            [tool, "--version"], capture_output=True, text=True
                        )
                        if version_result.returncode == 0:
                            version = version_result.stdout.strip().split("\n")[0]
                            print(f"    Version: {version}")
                    except Exception as e:
                        # Version check failed - tool may not support --version
                        print(f"    (version check failed: {e})")
        except Exception as e:
            print(f"  {tool}: ? Unable to check ({e})")

    print()
    return is_linux, len(kernel_sources) > 0


def validate_build_configuration():
    """Validate the build configuration."""
    print("=== Build Configuration Validation ===")

    try:
        from build import ALLOW_MOCK_DATA, PRODUCTION_MODE, validate_production_mode

        print(f"Loaded Configuration:")
        print(f"  PRODUCTION_MODE: {PRODUCTION_MODE}")
        print(f"  ALLOW_MOCK_DATA: {ALLOW_MOCK_DATA}")

        # Test validation
        validate_production_mode()
        print("✓ Configuration validation passed")

        return True

    except RuntimeError as e:
        print(f"✗ Configuration validation failed: {e}")
        return False
    except Exception as e:
        print(f"✗ Error loading build configuration: {e}")
        return False


def provide_recommendations(
    production_mode, allow_mock_data, is_linux, has_kernel_sources
):
    """Provide configuration recommendations."""
    print("=== Recommendations ===")

    if production_mode and allow_mock_data:
        print("⚠ CRITICAL: Production mode allows mock data")
        print("  Recommendation: Set PCILEECH_ALLOW_MOCK_DATA=false")
        print("  Command: export PCILEECH_ALLOW_MOCK_DATA=false")
        print()

    if production_mode and not is_linux:
        print("⚠ WARNING: Production mode on non-Linux system")
        print("  Real driver scraping and behavior profiling require Linux")
        print("  Consider using development mode for testing on this platform")
        print()

    if production_mode and not has_kernel_sources:
        print("⚠ WARNING: Production mode without kernel sources")
        print("  Driver scraping requires kernel source packages")
        print("  Install kernel sources before running production builds")
        print()

    if not production_mode:
        print("ℹ INFO: Development mode active")
        print("  For production builds, use:")
        print("    export PCILEECH_PRODUCTION_MODE=true")
        print("    export PCILEECH_ALLOW_MOCK_DATA=false")
        print()

    # Provide example commands
    print("Example Production Configuration:")
    print("  export PCILEECH_PRODUCTION_MODE=true")
    print("  export PCILEECH_ALLOW_MOCK_DATA=false")
    print("  python3 src/build.py --bdf 0000:03:00.0 --board pcileech_35t325_x4")
    print()

    print("Example Development Configuration:")
    print("  export PCILEECH_PRODUCTION_MODE=false")
    print("  export PCILEECH_ALLOW_MOCK_DATA=true")
    print("  python3 src/build.py --bdf 0000:03:00.0 --board pcileech_35t325_x4")


def main():
    """Main validation function."""
    print("PCILeech Production Configuration Validator")
    print("=" * 50)
    print()

    # Check environment variables
    production_mode, allow_mock_data = check_environment_variables()

    # Check system requirements
    is_linux, has_kernel_sources = check_system_requirements()

    # Validate build configuration
    config_valid = validate_build_configuration()

    print()

    # Provide recommendations
    provide_recommendations(
        production_mode, allow_mock_data, is_linux, has_kernel_sources
    )

    # Final status
    print("=" * 50)
    if (
        config_valid
        and production_mode
        and not allow_mock_data
        and is_linux
        and has_kernel_sources
    ):
        print("✓ READY FOR PRODUCTION BUILDS")
        exit_code = 0
    elif config_valid and not production_mode:
        print("✓ READY FOR DEVELOPMENT BUILDS")
        exit_code = 0
    else:
        print("⚠ CONFIGURATION ISSUES DETECTED")
        print("  Review recommendations above before proceeding")
        exit_code = 1

    return exit_code


if __name__ == "__main__":
    sys.exit(main())
