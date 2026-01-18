#!/usr/bin/env python3
"""
Template Security Validation Linter

This script analyzes template usage throughout the codebase to ensure
security-first template validation practices are followed. It verifies:

1. All required template variables are explicitly initialized
2. No None values in critical template variables
3. Proper use of strict validation mode
4. No fallbacks or defaults for critical security variables

This linter can be integrated into the CI pipeline to catch
template security issues early in the development lifecycle.
"""

import argparse
import ast
import logging
import re
import sys
from pathlib import Path
from typing import Dict, List, Optional, Set, Tuple

# Add project root to path
sys.path.insert(0, str(Path(__file__).parent.parent))

try:
    from src.templating.template_context_validator import (
        TemplateContextValidator,
        analyze_template_variables,
    )
except ImportError:
    print("ERROR: Unable to import template validation modules")
    sys.exit(1)


# Configure logging
logging.basicConfig(
    level=logging.INFO,
    format="%(levelname)s: %(message)s",
)
logger = logging.getLogger("template-security")


class TemplateSecurityLinter:
    """Linter for template security validation."""

    def __init__(self, project_root: Path, templates_dir: Optional[Path] = None):
        """Initialize the linter with project paths."""
        self.project_root = project_root
        self.templates_dir = templates_dir or (project_root / "src" / "templates")
        self.validator = TemplateContextValidator()
        self.issues = []

        # Template patterns that should be analyzed
        self.template_patterns = ["*.j2", "**/*.j2"]

        # Template-related file patterns (Python files that use templates)
        self.python_patterns = ["*.py", "**/*.py"]

        # Security-critical variables that must be explicitly initialized
        self.critical_variables = {
            "device_config",
            "board_config",
            "device_signature",
            "NUM_MSIX",
        }

        # Keywords that indicate template rendering
        self.template_rendering_keywords = [
            "render_template",
            "render_to_file",
            "render_string",
            "template.render",
        ]

    def find_templates(self) -> List[Path]:
        """Find all template files in the project."""
        templates = []
        for pattern in self.template_patterns:
            templates.extend(self.templates_dir.glob(pattern))
        return templates

    def find_python_files(self) -> List[Path]:
        """Find all Python files that might use templates."""
        python_files = []
        for pattern in self.python_patterns:
            python_files.extend(self.project_root.glob(pattern))
        # Filter out files in certain directories
        excluded_dirs = [".git", "venv", ".venv", "__pycache__", ".mypy_cache"]
        return [
            f
            for f in python_files
            if not any(ex_dir in str(f) for ex_dir in excluded_dirs)
        ]

    def analyze_template_file(self, template_path: Path) -> Set[str]:
        """Analyze a template file to extract required variables."""
        # Use the template context validator to analyze variables
        try:
            variables = analyze_template_variables(template_path)
            return variables
        except Exception as e:
            self.issues.append(f"Error analyzing template {template_path}: {e}")
            return set()

    def extract_template_rendering_calls(
        self, python_file: Path
    ) -> List[Tuple[str, Dict]]:
        """Extract template rendering calls from a Python file."""
        with open(python_file, "r", encoding="utf-8") as f:
            content = f.read()

        # This is a simplified approach - a full implementation would use AST parsing
        template_calls = []

        try:
            tree = ast.parse(content)
            for node in ast.walk(tree):
                if isinstance(node, ast.Call):
                    func_name = None

                    # Handle different types of function calls
                    if hasattr(node, "func"):
                        # Direct name: function(...)
                        if isinstance(node.func, ast.Name):
                            func_name = node.func.id
                        # Attribute access: obj.method(...)
                        elif isinstance(node.func, ast.Attribute):
                            func_name = node.func.attr

                    if func_name and any(
                        keyword in func_name
                        for keyword in self.template_rendering_keywords
                    ):
                        # Basic extraction of template name and context
                        template_name = None
                        context_var = None

                        # Check for template name in first arg
                        if node.args and len(node.args) > 0:
                            if isinstance(node.args[0], ast.Str):
                                template_name = node.args[0].s
                            elif isinstance(node.args[0], ast.Constant) and isinstance(
                                node.args[0].value, str
                            ):
                                template_name = node.args[0].value

                        # Check for context in second arg or in keyword args
                        if node.args and len(node.args) > 1:
                            context_var = "arg[1]"  # We can't extract the actual value, just note position

                        for kw in node.keywords:
                            if kw.arg == "template_name":
                                if isinstance(kw.value, ast.Str):
                                    template_name = kw.value.s
                                elif isinstance(kw.value, ast.Constant) and isinstance(
                                    kw.value.value, str
                                ):
                                    template_name = kw.value.value
                            if kw.arg == "context":
                                context_var = "keyword context"

                        if template_name:
                            template_calls.append(
                                (template_name, {"context": context_var})
                            )
        except Exception as e:
            self.issues.append(f"Error parsing Python file {python_file}: {e}")

        return template_calls

    def check_context_initialization(self, python_file: Path) -> List[str]:
        """Check if template contexts are properly initialized."""
        issues = []

        with open(python_file, "r", encoding="utf-8") as f:
            content = f.read()

        # Check for template contexts without explicit initialization
        missing_init_pattern = re.compile(
            r"context\s*=\s*\{\s*\}|template_context\s*=\s*\{\s*\}"
        )
        if missing_init_pattern.search(content):
            issues.append(
                f"{python_file}: Empty context initialization found. "
                "All template variables must be explicitly initialized."
            )

        # Check for None assignments to critical variables
        for var in self.critical_variables:
            none_pattern = re.compile(rf'["\']?{var}["\']?\s*:\s*None')
            if none_pattern.search(content):
                issues.append(
                    f"{python_file}: None value assigned to critical variable '{var}'. "
                    "All critical variables must be explicitly initialized with non-None values."
                )

        # Check for non-strict validation mode
        non_strict_pattern = re.compile(r"strict\s*=\s*False")
        if non_strict_pattern.search(content):
            issues.append(
                f"{python_file}: Non-strict validation mode used. "
                "Consider using strict mode for better security."
            )

        return issues

    def check_template_variable_usage(self, template_file: Path) -> List[str]:
        """Check template for potential security issues with variable usage."""
        issues = []

        with open(template_file, "r", encoding="utf-8") as f:
            content = f.read()

        # Check for default filters that might hide missing values
        default_filter_pattern = re.compile(r"\|\s*default\(")
        default_matches = default_filter_pattern.findall(content)
        if default_matches:
            issues.append(
                f"{template_file}: Default filter used {len(default_matches)} times. "
                "Consider explicit initialization instead of defaults for better security."
            )

        # Check for conditionals that might hide missing values
        undefined_check_pattern = re.compile(r"is\s+not\s+defined|is\s+undefined")
        undefined_matches = undefined_check_pattern.findall(content)
        if undefined_matches:
            issues.append(
                f"{template_file}: Undefined variable checks found {len(undefined_matches)} times. "
                "Consider explicit initialization to ensure all variables are defined."
            )

        return issues

    def run_security_checks(self, verbose: bool = False) -> List[str]:
        """Run all security checks and return a list of issues."""
        self.issues = []

        # 1. Find and analyze templates
        templates = self.find_templates()
        if verbose:
            logger.info(f"Found {len(templates)} template files")

        for template in templates:
            variables = self.analyze_template_file(template)
            if verbose:
                logger.info(f"Template {template.name} uses {len(variables)} variables")

            # Check template for security issues
            template_issues = self.check_template_variable_usage(template)
            self.issues.extend(template_issues)

        # 2. Find and analyze Python files that use templates
        python_files = self.find_python_files()
        if verbose:
            logger.info(f"Found {len(python_files)} Python files to analyze")

        for py_file in python_files:
            # Check for template context initialization issues
            context_issues = self.check_context_initialization(py_file)
            self.issues.extend(context_issues)

            # Extract and analyze template rendering calls
            template_calls = self.extract_template_rendering_calls(py_file)
            if verbose and template_calls:
                logger.info(
                    f"Found {len(template_calls)} template rendering calls in {py_file.name}"
                )

        return self.issues

    def generate_report(self, output_file: Optional[Path] = None) -> str:
        """Generate a security report based on the findings."""
        if not self.issues:
            report = "✅ No template security issues found!"
        else:
            report = f"❌ Found {len(self.issues)} template security issues:\n\n"
            for i, issue in enumerate(self.issues, 1):
                report += f"{i}. {issue}\n"

        report += "\n\nSecurity Recommendations:\n"
        report += "1. Always explicitly initialize all template variables\n"
        report += "2. Never use None for critical template variables\n"
        report += "3. Use strict validation mode by default\n"
        report += "4. Avoid default filters and undefined checks in templates\n"

        if output_file:
            with open(output_file, "w", encoding="utf-8") as f:
                f.write(report)

        return report


def main():
    """Main entry point for the script."""
    parser = argparse.ArgumentParser(description="Template Security Validation Linter")
    parser.add_argument(
        "--project-root",
        type=str,
        default=".",
        help="Path to the project root directory",
    )
    parser.add_argument(
        "--templates-dir",
        type=str,
        help="Path to the templates directory (defaults to src/templates)",
    )
    parser.add_argument(
        "--output",
        type=str,
        help="Path to write the report to (if not provided, prints to stdout)",
    )
    parser.add_argument(
        "--verbose", "-v", action="store_true", help="Enable verbose output"
    )

    args = parser.parse_args()

    project_root = Path(args.project_root).resolve()
    templates_dir = Path(args.templates_dir).resolve() if args.templates_dir else None
    output_file = Path(args.output) if args.output else None

    linter = TemplateSecurityLinter(project_root, templates_dir)
    linter.run_security_checks(args.verbose)
    report = linter.generate_report(output_file)

    if not output_file:
        print(report)

    # Return non-zero exit code if issues were found
    return 1 if linter.issues else 0


if __name__ == "__main__":
    sys.exit(main())
