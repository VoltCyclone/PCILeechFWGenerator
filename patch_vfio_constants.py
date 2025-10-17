#!/usr/bin/env python3
"""
VFIO Constants Patcher - Updates vfio_constants.py with kernel-correct values

This script:
1. Compiles and runs the vfio_helper C program to extract kernel constants
2. Parses the output to get the correct ioctl numbers
3. Updates src/cli/vfio_constants.py with the correct hard-coded values
4. Preserves all other content in the file unchanged

The approach switches from dynamic computation to hard-coded constants because:
- Dynamic computation can fail if ctypes struct sizes don't match kernel exactly
- Hard-coded values from kernel headers are guaranteed correct
- Build-time extraction ensures kernel version compatibility
"""

import logging
import os
import re
import subprocess
import sys
from pathlib import Path

# Add project root to path for imports
project_root = Path(__file__).parent.absolute()
sys.path.insert(0, str(project_root))

from src.error_utils import log_error_with_root_cause
from src.log_config import get_logger, setup_logging

# Import required modules - use actual implementations
from src.string_utils import (
    log_error_safe,
    log_info_safe,
    log_warning_safe,
    safe_format,
)


def require(condition: bool, message: str, **context) -> None:
    """Validate condition or exit with error."""
    logger = get_logger(__name__)
    if not condition:
        log_error_safe(
            logger,
            safe_format(
                "Build aborted: {msg} | ctx={ctx}",
                msg=message,
                ctx=context,
            ),
            prefix="PATCH",
        )
        raise SystemExit(2)


def compile_and_run_helper():
    """Compile vfio_helper.c and run it to extract constants."""
    logger = get_logger(__name__)

    # Compile the helper
    compile_cmd = [
        "gcc",
        "-Wall",
        "-Werror",
        "-O2",
        "-o",
        "vfio_helper",
        "vfio_helper.c",
    ]

    log_info_safe(logger, "Compiling vfio_helper...", prefix="PATCH")
    try:
        result = subprocess.run(compile_cmd, check=True, capture_output=True, text=True)
    except subprocess.CalledProcessError as e:
        require(False, "VFIO helper compilation failed", error=str(e), stderr=e.stderr)

    # Run the helper to get constants
    log_info_safe(logger, "Extracting VFIO constants...", prefix="PATCH")
    try:
        result = subprocess.run(
            ["./vfio_helper"], check=True, capture_output=True, text=True
        )
        return result.stdout.strip()
    except subprocess.CalledProcessError as e:
        require(False, "VFIO helper execution failed", error=str(e), stderr=e.stderr)


def parse_constants(output):
    """Parse the helper output into a dictionary of constants."""
    constants = {}
    logger = get_logger(__name__)

    require(output and output.strip(), "Empty output from VFIO helper")

    for line in output.split("\n"):
        line = line.strip()
        if not line or not "=" in line:
            continue

        try:
            name, value = line.split("=", 1)
            name = name.strip()
            value_str = value.strip()

            # Validate constant name
            require(name and name.isidentifier(), "Invalid constant name", name=name)

            # Parse value as integer
            constants[name] = int(value_str)

        except ValueError as e:
            log_warning_safe(
                logger,
                safe_format(
                    "Skipping invalid line: {line} - {error}",
                    line=line,
                    error=str(e),
                ),
                prefix="PATCH",
            )
            continue

    return constants


def update_vfio_constants_file(constants):
    """Update src/cli/vfio_constants.py with the extracted constants."""
    logger = get_logger(__name__)

    vfio_constants_path = Path("src/cli/vfio_constants.py")
    require(
        vfio_constants_path.exists(),
        "vfio_constants.py not found",
        path=str(vfio_constants_path),
    )

    # Read the current file
    with open(vfio_constants_path, "r") as f:
        content = f.read()

    # Create the new constants section
    new_constants = []
    new_constants.append(
        "# ───── Ioctl numbers - extracted from kernel headers at build time ──────"
    )

    # Add each constant with its extracted value
    for const_name, const_value in constants.items():
        new_constants.append("{} = {}".format(const_name, const_value))

    # Add any missing constants that weren't in the original file
    missing_constants = {
        "VFIO_SET_IOMMU": 15206,  # VFIO_BASE + 2
        "VFIO_GROUP_SET_CONTAINER": 15208,  # VFIO_BASE + 4
        "VFIO_GROUP_UNSET_CONTAINER": 15209,  # VFIO_BASE + 5
    }

    for missing, fallback_value in missing_constants.items():
        if missing not in constants:
            # Add fallback values for missing constants
            log_warning_safe(
                logger,
                safe_format(
                    "{constant} not found in kernel headers output, using fallback value {value}",
                    constant=missing,
                    value=fallback_value,
                ),
                prefix="PATCH",
            )
            constants[missing] = fallback_value

    new_constants_text = "\n".join(new_constants)

    # Replace the section from "# ───── Ioctl numbers" to the end of constants
    # This preserves everything before the constants section
    pattern = r"# ───── Ioctl numbers.*?(?=\n\n# Export all constants|\n\n__all__|$)"

    if re.search(pattern, content, re.DOTALL):
        # Replace existing constants section
        new_content = re.sub(pattern, new_constants_text, content, flags=re.DOTALL)
        log_info_safe(logger, "Replaced existing constants section", prefix="PATCH")
    else:
        # If pattern not found, try to find __all__ section
        all_pattern = r"(# Export all constants\n__all__)"
        if re.search(all_pattern, content):
            new_content = re.sub(
                all_pattern, "{}\n\n\n\\1".format(new_constants_text), content
            )
            log_info_safe(
                logger, "Inserted constants before __all__ section", prefix="PATCH"
            )
        else:
            # Fallback: append at end
            new_content = content + "\n\n" + new_constants_text
            log_warning_safe(
                logger,
                "Could not find insertion point, appending to end",
                prefix="PATCH",
            )

    # Write the updated file
    with open(vfio_constants_path, "w") as f:
        f.write(new_content)

    log_info_safe(
        logger,
        safe_format(
            "Updated {path} with {count} constants",
            path=vfio_constants_path,
            count=len(constants),
        ),
        prefix="PATCH",
    )

    # Show what was updated
    for name, value in constants.items():
        log_info_safe(
            logger,
            safe_format("  {name} = {value}", name=name, value=value),
            prefix="PATCH",
        )


def main():
    """Main function to orchestrate the patching process."""
    # Setup logging
    setup_logging(level=logging.INFO)
    logger = get_logger(__name__)

    log_info_safe(logger, "VFIO Constants Patcher", prefix="PATCH")
    log_info_safe(logger, "=" * 50, prefix="PATCH")

    # Check if we're in the right directory
    require(
        Path("src/cli/vfio_constants.py").exists(),
        "Must run from project root directory - expected to find src/cli/vfio_constants.py",
    )

    # Check if helper source exists
    require(
        Path("vfio_helper.c").exists(), "vfio_helper.c not found in current directory"
    )

    # Extract constants from kernel
    output = compile_and_run_helper()
    constants = parse_constants(output)

    require(bool(constants), "No constants extracted from helper output")

    # Update the Python file
    update_vfio_constants_file(constants)

    # Cleanup
    if Path("vfio_helper").exists():
        os.unlink("vfio_helper")

    log_info_safe(logger, "\nPatching complete!", prefix="PATCH")
    log_info_safe(
        logger,
        "The vfio_constants.py file now contains kernel-correct ioctl numbers.",
        prefix="PATCH",
    )


if __name__ == "__main__":
    main()
