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

NOTE: This script is designed to be standalone and run during container builds.
It should NOT import heavy project dependencies (string_utils, log_config, etc.)
to avoid circular import issues during the build stage.
"""

import os
import re
import subprocess
import sys
from pathlib import Path


def log_info(message: str, prefix: str = "PATCH") -> None:
    """Minimal logging for info messages."""
    print(f"[{prefix}] {message}", file=sys.stderr)


def log_warning(message: str, prefix: str = "PATCH") -> None:
    """Minimal logging for warning messages."""
    print(f"[{prefix}] WARNING: {message}", file=sys.stderr)


def log_error(message: str, prefix: str = "PATCH") -> None:
    """Minimal logging for error messages."""
    print(f"[{prefix}] ERROR: {message}", file=sys.stderr)


def require(condition: bool, message: str, **context) -> None:
    """Validate condition or exit with error."""
    if not condition:
        ctx_str = ", ".join(f"{k}={v}" for k, v in context.items())
        log_error(f"Build aborted: {message} | {ctx_str}")
        raise SystemExit(2)


def compile_and_run_helper():
    """Compile vfio_helper.c and run it to extract constants."""
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

    log_info("Compiling vfio_helper...")
    try:
        result = subprocess.run(
            compile_cmd, check=True, capture_output=True, text=True
        )
    except subprocess.CalledProcessError as e:
        require(
            False,
            "VFIO helper compilation failed",
            error=str(e),
            stderr=e.stderr,
        )

    # Run the helper to get constants
    log_info("Extracting VFIO constants...")
    try:
        result = subprocess.run(
            ["./vfio_helper"], check=True, capture_output=True, text=True
        )
        return result.stdout.strip()
    except subprocess.CalledProcessError as e:
        require(
            False,
            "VFIO helper execution failed",
            error=str(e),
            stderr=e.stderr,
        )


def parse_constants(output):
    """Parse the helper output into a dictionary of constants."""
    constants = {}

    require(output and output.strip(), "Empty output from VFIO helper")

    for line in output.split("\n"):
        line = line.strip()
        if not line or "=" not in line:
            continue

        try:
            name, value = line.split("=", 1)
            name = name.strip()
            value_str = value.strip()

            # Validate constant name
            require(
                name and name.isidentifier(),
                "Invalid constant name",
                name=name,
            )

            # Parse value as integer
            constants[name] = int(value_str)

        except ValueError as e:
            log_warning(f"Skipping invalid line: {line} - {e}")
            continue

    return constants


def update_vfio_constants_file(constants):
    """Update src/cli/vfio_constants.py with the extracted constants."""
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
        "# ───── Ioctl numbers - extracted from kernel headers "
        "at build time ──────"
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
            log_warning(
                f"{missing} not found in kernel headers output, "
                f"using fallback value {fallback_value}"
            )
            constants[missing] = fallback_value

    new_constants_text = "\n".join(new_constants)

    # Replace the section from "# ───── Ioctl numbers" to the end of constants
    # This preserves everything before the constants section
    pattern = r"# ───── Ioctl numbers.*?(?=\n\n# Export all constants|\n\n__all__|$)"

    if re.search(pattern, content, re.DOTALL):
        # Replace existing constants section
        new_content = re.sub(pattern, new_constants_text, content, flags=re.DOTALL)
        log_info("Replaced existing constants section")
    else:
        # If pattern not found, try to find __all__ section
        all_pattern = r"(# Export all constants\n__all__)"
        if re.search(all_pattern, content):
            new_content = re.sub(
                all_pattern, "{}\n\n\n\\1".format(new_constants_text), content
            )
            log_info("Inserted constants before __all__ section")
        else:
            # Fallback: append at end
            new_content = content + "\n\n" + new_constants_text
            log_warning("Could not find insertion point, appending to end")

    # Write the updated file
    with open(vfio_constants_path, "w") as f:
        f.write(new_content)

    log_info(
        f"Updated {vfio_constants_path} with {len(constants)} constants"
    )

    # Show what was updated
    for name, value in constants.items():
        log_info(f"  {name} = {value}")


def main():
    """Main function to orchestrate the patching process."""
    log_info("VFIO Constants Patcher")
    log_info("=" * 50)

    # Check if we're in the right directory
    require(
        Path("src/cli/vfio_constants.py").exists(),
        "Must run from project root directory - "
        "expected to find src/cli/vfio_constants.py",
    )

    # Check if helper source exists
    require(
        Path("vfio_helper.c").exists(),
        "vfio_helper.c not found in current directory",
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

    log_info("\nPatching complete!")
    log_info(
        "The vfio_constants.py file now contains kernel-correct "
        "ioctl numbers."
    )


if __name__ == "__main__":
    main()
