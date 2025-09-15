#!/usr/bin/env python3
"""
Comprehensive template syntax checker for PCILeech templates.

This script validates Jinja2 template syntax with full custom filter and function support.
"""

import sys
from pathlib import Path
from typing import List, Tuple

# Add src to path for imports
script_dir = Path(__file__).parent
project_root = script_dir.parent
sys.path.insert(0, str(project_root))
sys.path.insert(0, str(project_root / "src"))


def check_template_syntax() -> Tuple[int, int]:
    """
    Check syntax of all Jinja2 templates.

    Returns:
        Tuple of (total_templates, errors)
    """
    try:
        from jinja2 import TemplateSyntaxError

        from src.templating.template_renderer import TemplateRenderer
    except ImportError as e:
        print(f"❌ Error importing modules: {e}")
        print("Make sure you're running from the project root directory")
        return 0, 1

    template_dir = project_root / "src" / "templates"
    if not template_dir.exists():
        print(f"❌ Template directory not found: {template_dir}")
        return 0, 1

    # Initialize renderer with full custom filters and functions
    try:
        renderer = TemplateRenderer(template_dir)
    except Exception as e:
        print(f"❌ Failed to initialize template renderer: {e}")
        return 0, 1

    # Find all template files
    template_patterns = ["*.j2", "*.jinja", "*.jinja2"]
    templates = []
    for pattern in template_patterns:
        templates.extend(template_dir.rglob(pattern))

    if not templates:
        print(f"⚠️  No template files found in {template_dir}")
        return 0, 0

    print(f"🔍 Checking syntax of {len(templates)} templates...")

    errors = 0
    for template_path in sorted(templates):
        rel_path = template_path.relative_to(template_dir)
        try:
            # Use the template renderer's environment which has custom filters
            template = renderer.env.get_template(str(rel_path))
            print(f"✅ {rel_path}")
        except TemplateSyntaxError as e:
            print(f"❌ {rel_path}: Syntax error at line {e.lineno}: {e.message}")
            errors += 1
        except Exception as e:
            # Check if it's a known issue that we can provide helpful info for
            error_msg = str(e)
            if "No filter named" in error_msg:
                print(f"❌ {rel_path}: {error_msg}")
                print(f"   💡 This may indicate a missing custom filter registration")
            elif "Encountered unknown tag" in error_msg:
                print(f"❌ {rel_path}: {error_msg}")
                print(f"   💡 This may indicate use of unsupported Jinja2 syntax")
            else:
                print(f"❌ {rel_path}: {error_msg}")
            errors += 1

    return len(templates), errors


def main():
    """Main entry point."""
    print("PCILeech Template Syntax Checker")
    print("=================================")

    total_templates, errors = check_template_syntax()

    if errors == 0:
        print(f"\n✅ All {total_templates} templates have valid syntax!")
        return 0
    else:
        print(f"\n❌ Found {errors} template syntax errors")
        return 1


if __name__ == "__main__":
    sys.exit(main())
