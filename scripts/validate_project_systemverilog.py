#!/usr/bin/env python3
"""
Project Template SystemVerilog Configuration Validator

This script validates that project generation templates are properly configured
to use SystemVerilog instead of Verilog to prevent synthesis compatibility issues.

Usage:
    python scripts/validate_project_systemverilog.py
"""

import re
import sys
from pathlib import Path


def validate_tcl_templates():
    """Validate that TCL templates configure projects for SystemVerilog."""
    tcl_templates_dir = Path("src/templates/tcl")
    errors = []
    warnings = []

    if not tcl_templates_dir.exists():
        errors.append(f"TCL templates directory not found: {tcl_templates_dir}")
        return errors, warnings

    tcl_files = list(tcl_templates_dir.glob("*.tcl.j2")) + list(
        tcl_templates_dir.glob("*.tcl")
    )

    if not tcl_files:
        warnings.append("No TCL template files found")
        return errors, warnings

    for tcl_file in tcl_files:
        print(f"Checking: {tcl_file}")

        try:
            with open(tcl_file, "r", encoding="utf-8") as f:
                content = f.read()
        except Exception as e:
            errors.append(f"{tcl_file}: Failed to read file - {e}")
            continue

        # Check for target_language configuration
        target_lang_matches = re.findall(
            r"set_property\s+target_language\s+(\w+)", content, re.IGNORECASE
        )

        if target_lang_matches:
            for match in target_lang_matches:
                if match.lower() == "verilog":
                    errors.append(
                        f"{tcl_file}: Uses 'target_language Verilog' - should be 'SystemVerilog'"
                    )
                elif match.lower() == "systemverilog":
                    print(f"  ✅ Correctly configured for SystemVerilog")
                else:
                    warnings.append(f"{tcl_file}: Unknown target_language '{match}'")

        # Check for any hardcoded Verilog references that might cause issues
        verilog_refs = re.findall(r"\bverilog\b(?!\s*header)", content, re.IGNORECASE)
        if verilog_refs:
            for ref in set(verilog_refs):  # Remove duplicates
                warnings.append(
                    f"{tcl_file}: Contains reference to '{ref}' - verify this is intentional"
                )

    return errors, warnings


def validate_python_generators():
    """Validate that Python code generators create SystemVerilog-compatible output."""
    src_dir = Path("src")
    errors = []
    warnings = []

    # Look for Python files that might configure language settings
    python_files = list(src_dir.rglob("*.py"))

    for py_file in python_files:
        if any(skip in str(py_file) for skip in ["__pycache__", ".pyc", "test_"]):
            continue

        try:
            with open(py_file, "r", encoding="utf-8") as f:
                content = f.read()
        except Exception:
            continue  # Skip files we can't read

        # Look for hardcoded language configurations
        if "target_language" in content and "Verilog" in content:
            # Check if it's setting target_language to Verilog
            if re.search(r'target_language.*["\']Verilog["\']', content):
                errors.append(
                    f"{py_file}: Sets target_language to 'Verilog' - should be 'SystemVerilog'"
                )

    return errors, warnings


def main():
    """Main validation function."""
    print("Project SystemVerilog Configuration Validator")
    print("=" * 50)

    all_errors = []
    all_warnings = []

    # Validate TCL templates
    print("\n🔍 Checking TCL templates...")
    tcl_errors, tcl_warnings = validate_tcl_templates()
    all_errors.extend(tcl_errors)
    all_warnings.extend(tcl_warnings)

    # Validate Python generators
    print("\n🔍 Checking Python generators...")
    py_errors, py_warnings = validate_python_generators()
    all_errors.extend(py_errors)
    all_warnings.extend(py_warnings)

    # Print results
    if all_errors:
        print(f"\n❌ Found {len(all_errors)} errors:")
        for error in all_errors:
            print(f"   {error}")

    if all_warnings:
        print(f"\n⚠️  Found {len(all_warnings)} warnings:")
        for warning in all_warnings:
            print(f"   {warning}")

    if not all_errors and not all_warnings:
        print("\n✅ All project templates are correctly configured for SystemVerilog!")
    elif not all_errors:
        print(f"\n✅ Project templates are valid (with {len(all_warnings)} warnings)")
    else:
        print(
            f"\n❌ Validation failed with {len(all_errors)} errors and {len(all_warnings)} warnings"
        )
        sys.exit(1)

    print("\nValidation completed! 🎉")


if __name__ == "__main__":
    main()
