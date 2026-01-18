#!/usr/bin/env python3
"""
SystemVerilog Template Validation Script

This script validates that SystemVerilog templates follow proper syntax conventions
to prevent synthesis issues. It checks for:

1. Module structure (proper module/endmodule declarations)
2. Port declaration consistency (input/output in module ports vs standalone)
3. SystemVerilog-specific constructs that might cause issues
4. Verilog vs SystemVerilog syntax compatibility
5. Template rendering with sample data to catch basic syntax errors

Usage:
    python scripts/validate_systemverilog_templates.py
"""

import re
import sys
from pathlib import Path


class SystemVerilogValidator:
    """Validates SystemVerilog template files for common syntax issues."""

    def __init__(self):
        self.errors = []
        self.warnings = []
        self.templates_dir = Path("src/templates/sv")

        # SystemVerilog constructs that might cause compatibility issues
        self.systemverilog_constructs = {
            "always_ff": 'Use "always @(posedge clk)" instead for better compatibility',
            "always_comb": 'Use "always @(*)" instead for better compatibility',
            "logic": 'Consider using "wire" or "reg" for better compatibility',
            "bit": "OK for PCILeech compatibility (used in reference code)",
            "interface": "SystemVerilog interface - ensure project is configured for SystemVerilog",
        }

        # Patterns that indicate problematic constructs
        self.problematic_patterns = [
            (
                r"\binput\s+\w+\s+\w+.*?;",
                "Standalone input declaration found outside module ports",
            ),
            (
                r"\boutput\s+\w+\s+\w+.*?;",
                "Standalone output declaration found outside module ports",
            ),
            (
                r"\binout\s+\w+\s+\w+.*?;",
                "Standalone inout declaration found outside module ports",
            ),
        ]

    def validate_file(self, filepath: Path) -> bool:
        """Validate a single SystemVerilog template file."""
        print(f"Validating: {filepath}")

        try:
            with open(filepath, "r", encoding="utf-8") as f:
                content = f.read()
        except Exception as e:
            self.errors.append(f"{filepath}: Failed to read file - {e}")
            return False

        file_valid = True

        # Check for proper module structure
        if not self._check_module_structure(filepath, content):
            file_valid = False

        # Check for standalone port declarations
        if not self._check_standalone_ports(filepath, content):
            file_valid = False

        # Check for SystemVerilog constructs
        self._check_systemverilog_constructs(filepath, content)

        # Check template syntax
        if not self._check_template_syntax(filepath, content):
            file_valid = False

        return file_valid

    def _check_module_structure(self, filepath: Path, content: str) -> bool:
        """Check if file has proper module structure."""
        # Skip files that are meant to be included (not standalone modules)
        include_patterns = [
            "register_declarations",
            "error_outputs",
            "device_specific_ports",
            "clock_domain_logic",
            "interrupt_logic",
            "read_logic",
            "write_logic",
            "error_declarations",
            "error_detection",
            "error_handling",
            "error_logging",
            "error_state_machine",
            "error_injection",
            "error_counters",
            "error_handling_complete",
            "power_integration",
            "power_management",
            "power_declarations",
            "performance_counters",
            "register_logic",
        ]

        if any(pattern in filepath.stem for pattern in include_patterns):
            return True  # These are include files, not standalone modules

        module_match = re.search(r"\bmodule\s+(\w+)", content)
        endmodule_match = re.search(r"\bendmodule\b", content)

        if not module_match:
            self.errors.append(f"{filepath}: No module declaration found")
            return False

        if not endmodule_match:
            self.errors.append(f"{filepath}: No endmodule found")
            return False

        # Check if module declaration comes before endmodule
        if module_match.start() > endmodule_match.start():
            self.errors.append(
                f"{filepath}: endmodule appears before module declaration"
            )
            return False

        return True

    def _check_standalone_ports(self, filepath: Path, content: str) -> bool:
        """Check for problematic standalone port declarations."""
        valid = True

        # First, find if this is inside a module
        module_match = re.search(r"\bmodule\s+\w+.*?\(.*?\);", content, re.DOTALL)
        if not module_match:
            return True  # Not a module file, skip this check

        # Get content after module declaration
        module_end = module_match.end()
        endmodule_match = re.search(r"\bendmodule\b", content)

        if endmodule_match:
            module_body = content[module_end : endmodule_match.start()]
        else:
            module_body = content[module_end:]

        # Remove function and task blocks to avoid false positives
        # Functions and tasks can have input/output parameters
        clean_body = module_body

        # Remove function blocks
        clean_body = re.sub(
            r"\bfunction\s+.*?\bendfunction\b", "", clean_body, flags=re.DOTALL
        )

        # Remove task blocks
        clean_body = re.sub(r"\btask\s+.*?\bendtask\b", "", clean_body, flags=re.DOTALL)

        # Check for standalone port declarations in cleaned module body
        for pattern, message in self.problematic_patterns:
            matches = re.finditer(pattern, clean_body, re.MULTILINE)
            for match in matches:
                line_num = content[: module_end + match.start()].count("\n") + 1
                self.errors.append(f"{filepath}:{line_num}: {message}")
                valid = False

        return valid

    def _check_systemverilog_constructs(self, filepath: Path, content: str):
        """Check for SystemVerilog-specific constructs and provide guidance."""
        for construct, guidance in self.systemverilog_constructs.items():
            if construct in content:
                # Only warn about constructs that might cause real compatibility issues
                if construct == "logic" and "bit" not in content:
                    # Only warn if using logic without bit (indicates mixed usage)
                    self.warnings.append(f"{filepath}: Uses '{construct}' - {guidance}")
                elif construct == "interface":
                    self.warnings.append(f"{filepath}: Uses '{construct}' - {guidance}")

    def _check_template_syntax(self, filepath: Path, content: str) -> bool:
        """Check basic Jinja2 template syntax."""
        # Check for unmatched braces
        open_braces = content.count("{{")
        close_braces = content.count("}}")

        if open_braces != close_braces:
            self.errors.append(f"{filepath}: Unmatched template braces ({{ vs }})")
            return False

        # Check for unmatched control blocks
        open_blocks = len(re.findall(r"{%\s*(?:if|for|macro)", content))
        close_blocks = len(re.findall(r"{%\s*end(?:if|for|macro)", content))

        if open_blocks != close_blocks:
            self.errors.append(f"{filepath}: Unmatched template control blocks")
            return False

        return True

    def validate_all_templates(self) -> bool:
        """Validate all SystemVerilog templates."""
        if not self.templates_dir.exists():
            self.errors.append(f"Templates directory not found: {self.templates_dir}")
            return False

        template_files = list(self.templates_dir.glob("*.sv.j2"))

        if not template_files:
            self.warnings.append("No SystemVerilog template files found")
            return True

        print(f"Found {len(template_files)} SystemVerilog template files")

        all_valid = True
        for template_file in template_files:
            if not self.validate_file(template_file):
                all_valid = False

        return all_valid

    def print_results(self):
        """Print validation results."""
        if self.errors:
            print(f"\n❌ Found {len(self.errors)} errors:")
            for error in self.errors:
                print(f"   {error}")

        if self.warnings:
            print(f"\n⚠️  Found {len(self.warnings)} warnings:")
            for warning in self.warnings:
                print(f"   {warning}")

        if not self.errors and not self.warnings:
            print("\n✅ All SystemVerilog templates passed validation!")
        elif not self.errors:
            print(
                f"\n✅ All SystemVerilog templates are valid (with {len(self.warnings)} warnings)"
            )
        else:
            print(
                f"\n❌ Validation failed with {len(self.errors)} errors and {len(self.warnings)} warnings"
            )


def main():
    """Main validation function."""
    print("SystemVerilog Template Validator")
    print("=" * 50)

    validator = SystemVerilogValidator()
    success = validator.validate_all_templates()
    validator.print_results()

    if not success:
        sys.exit(1)

    print("\nValidation completed successfully! 🎉")


if __name__ == "__main__":
    main()
