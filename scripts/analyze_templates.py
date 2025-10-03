#!/usr/bin/env python3
"""
Comprehensive template analysis script to check for:
1. Alignment issues between templates and context validator
2. Insecure defaults
3. Duplicate templates and code
4. Dead/unused templates
"""

import json
import os
import re
from collections import defaultdict
from pathlib import Path
from typing import Dict, List, Set, Tuple

# Project root
PROJECT_ROOT = Path(__file__).parent.parent


def load_template_mapping() -> Dict[str, str]:
    """Load the template mapping from template_mapping.py"""
    mapping_file = PROJECT_ROOT / "src/templates/template_mapping.py"
    mapping = {}

    with open(mapping_file, "r") as f:
        content = f.read()
        # Extract the TEMPLATE_PATH_MAPPING dictionary
        match = re.search(r"TEMPLATE_PATH_MAPPING\s*=\s*{([^}]+)}", content, re.DOTALL)
        if match:
            # Parse the dictionary content
            dict_content = match.group(1)
            for line in dict_content.split("\n"):
                if '":' in line and '.j2"' in line:
                    # Extract key and value
                    parts = line.split('":')
                    if len(parts) == 2:
                        key = parts[0].strip().strip('"').strip("'").strip(",").strip()
                        value = (
                            parts[1].strip().strip('"').strip("'").strip(",").strip()
                        )
                        if key.startswith('"'):
                            key = key[1:]
                        if value.endswith('"'):
                            value = value[:-1]
                        mapping[key] = value

    return mapping


def get_actual_templates() -> Set[str]:
    """Get all actual template files in the templates directory"""
    templates_dir = PROJECT_ROOT / "src/templates"
    templates = set()

    for template_file in templates_dir.rglob("*.j2"):
        relative_path = template_file.relative_to(templates_dir)
        templates.add(str(relative_path))

    return templates


def find_template_references() -> Dict[str, List[Tuple[str, int]]]:
    """Find all references to templates in Python code (src and tests)."""
    references = defaultdict(list)
    code_dirs = [PROJECT_ROOT / "src", PROJECT_ROOT / "tests"]

    # Patterns to find template references
    patterns = [
        r'render_template\s*\(\s*["\']([^"\']+\.j2)["\']',
        r'get_template\s*\(\s*["\']([^"\']+\.j2)["\']',
        r'template_exists\s*\(\s*["\']([^"\']+\.j2)["\']',
        r'["\']([^"\']+\.j2)["\']',  # General .j2 references
    ]

    for base_dir in code_dirs:
        if not base_dir.exists():
            continue
        for py_file in base_dir.rglob("*.py"):
            try:
                with open(py_file, "r") as f:
                    content = f.read()
                    for line_num, line in enumerate(content.split("\n"), 1):
                        for pattern in patterns:
                            matches = re.findall(pattern, line)
                            for match in matches:
                                references[match].append(
                                    (str(py_file.relative_to(PROJECT_ROOT)), line_num)
                                )
            except Exception as e:
                print(f"Error reading {py_file}: {e}")

    return dict(references)


def find_jinja_template_includes() -> Set[str]:
    """Scan all Jinja templates for include/import references to other templates."""
    templates_dir = PROJECT_ROOT / "src/templates"
    include_refs: Set[str] = set()

    # Regex patterns to detect Jinja include/import statements
    patterns = [
        r"\{%\s*include\s+[\"\']([^\"\']+\.j2)[\"\']\s*%\}",
        r"\{%\s*import\s+[\"\']([^\"\']+\.j2)[\"\']\s+as\s+\w+\s*%\}",
        r"\{%\s*from\s+[\"\']([^\"\']+\.j2)[\"\']\s+import\s+.+?%\}",
    ]

    for template_file in templates_dir.rglob("*.j2"):
        try:
            with open(template_file, "r") as f:
                content = f.read()
                for pattern in patterns:
                    for match in re.findall(pattern, content):
                        include_refs.add(match)
        except Exception as e:
            print(f"Error reading {template_file}: {e}")

    return include_refs


def analyze_template_variables(template_path: Path) -> Set[str]:
    """Extract variables used in a template"""
    variables = set()

    try:
        with open(template_path, "r") as f:
            content = f.read()

            # Find variables in {{ }} expressions
            var_pattern = r"\{\{\s*(\w+)(?:\.\w+)*"
            variables.update(re.findall(var_pattern, content))

            # Find variables in {% if %} conditions
            if_pattern = r"\{%\s*if\s+(\w+)(?:\.\w+)*"
            variables.update(re.findall(if_pattern, content))

            # Find variables in {% for %} loops
            for_pattern = r"\{%\s*for\s+\w+\s+in\s+(\w+)(?:\.\w+)*"
            variables.update(re.findall(for_pattern, content))
    except Exception as e:
        print(f"Error analyzing {template_path}: {e}")

    return variables


def check_insecure_defaults() -> List[Dict]:
    """Check for insecure defaults in templates and context validator"""
    issues = []

    # Check context validator for insecure defaults
    validator_file = PROJECT_ROOT / "src/templating/template_context_validator.py"
    with open(validator_file, "r") as f:
        content = f.read()

        # Check for problematic defaults
        insecure_patterns = [
            (
                r"ALLOW_ROM_WRITES.*=.*True",
                "ALLOW_ROM_WRITES should default to False for security",
            ),
            (
                r"WRITE_PBA_ALLOWED.*=.*True",
                "WRITE_PBA_ALLOWED should default to False for security",
            ),
            (
                r"ENABLE_SIGNATURE_CHECK.*=.*False",
                "ENABLE_SIGNATURE_CHECK should default to True for security",
            ),
            (
                r'device_signature.*=.*["\'].*["\']',
                "device_signature should not have a default value",
            ),
            (
                r"SIGNATURE_CHECK.*=.*False",
                "SIGNATURE_CHECK should default to True for security",
            ),
        ]

        for pattern, message in insecure_patterns:
            if re.search(pattern, content):
                issues.append(
                    {
                        "file": "src/templating/template_context_validator.py",
                        "issue": message,
                        "severity": "HIGH",
                    }
                )

    return issues


def find_duplicates() -> Dict[str, List[str]]:
    """Find duplicate templates or similar code"""
    duplicates = defaultdict(list)
    templates_dir = PROJECT_ROOT / "src/templates"

    # Group templates by content hash
    content_hashes = {}
    for template_file in templates_dir.rglob("*.j2"):
        try:
            with open(template_file, "rb") as f:
                content = f.read()
                content_hash = hash(content)

                relative_path = str(template_file.relative_to(templates_dir))
                if content_hash in content_hashes:
                    duplicates[content_hashes[content_hash]].append(relative_path)
                else:
                    content_hashes[content_hash] = relative_path
        except Exception as e:
            print(f"Error reading {template_file}: {e}")

    # Only keep actual duplicates
    return {k: v for k, v in duplicates.items() if v}


def find_dead_templates() -> List[str]:
    """Find templates that are not referenced anywhere"""
    actual_templates = get_actual_templates()
    references = find_template_references()
    include_refs = find_jinja_template_includes()
    mapping = load_template_mapping()

    # Get all referenced templates (both old and new paths)
    referenced_templates = set()
    for ref in list(references.keys()) + list(include_refs):
        referenced_templates.add(ref)
        # Check if this is an old path that maps to a new one
        if ref in mapping:
            referenced_templates.add(mapping[ref])
        # Check if this is a new path that has an old mapping
        for old, new in mapping.items():
            if new == ref:
                referenced_templates.add(old)

    # Find templates that exist but are not referenced
    dead_templates = []
    for template in actual_templates:
        # Check various path formats
        template_refs = [
            template,
            f"systemverilog/{template}" if template.startswith("sv/") else template,
            template.replace("sv/", "systemverilog/"),
        ]

        if not any(ref in referenced_templates for ref in template_refs):
            dead_templates.append(template)

    return dead_templates


def check_alignment() -> List[Dict]:
    """Check alignment between templates and context validator"""
    issues = []
    templates_dir = PROJECT_ROOT / "src/templates"

    # Load context validator requirements
    validator_file = PROJECT_ROOT / "src/templating/template_context_validator.py"
    with open(validator_file, "r") as f:
        validator_content = f.read()

    # Check each template
    for template_file in templates_dir.rglob("*.j2"):
        relative_path = str(template_file.relative_to(templates_dir))
        template_vars = analyze_template_variables(template_file)

        # Check if template has requirements defined
        has_requirements = False
        for pattern in [
            f'"{relative_path}"',
            f"'{relative_path}'",
            f'"sv/*.sv.j2"',
            f'"tcl/*.j2"',
        ]:
            if pattern in validator_content:
                has_requirements = True
                break

        if not has_requirements and template_vars:
            issues.append(
                {
                    "template": relative_path,
                    "issue": f"Template uses variables {template_vars} but has no requirements defined",
                    "severity": "MEDIUM",
                }
            )

    return issues


def main():
    """Run comprehensive template analysis"""
    print("=" * 80)
    print("TEMPLATE ANALYSIS REPORT")
    print("=" * 80)

    # 1. Check template mapping vs actual files
    print("\n1. TEMPLATE STRUCTURE ANALYSIS")
    print("-" * 40)
    mapping = load_template_mapping()
    actual = get_actual_templates()

    # Find templates in mapping but not in filesystem
    missing_templates = []
    for old_path, new_path in mapping.items():
        if new_path not in actual:
            missing_templates.append(new_path)

    if missing_templates:
        print(f"❌ Missing templates referenced in mapping: {len(missing_templates)}")
        for template in sorted(set(missing_templates)):
            print(f"   - {template}")
    else:
        print("✅ All mapped templates exist")

    # 2. Check for insecure defaults
    print("\n2. SECURITY ANALYSIS")
    print("-" * 40)
    security_issues = check_insecure_defaults()
    if security_issues:
        print(f"❌ Found {len(security_issues)} security issues:")
        for issue in security_issues:
            print(f"   [{issue['severity']}] {issue['file']}: {issue['issue']}")
    else:
        print("✅ No insecure defaults found")

    # 3. Check for duplicates
    print("\n3. DUPLICATE ANALYSIS")
    print("-" * 40)
    duplicates = find_duplicates()
    if duplicates:
        print(f"❌ Found {len(duplicates)} sets of duplicate templates:")
        for original, dups in duplicates.items():
            print(f"   - {original} duplicated in:")
            for dup in dups:
                print(f"     • {dup}")
    else:
        print("✅ No duplicate templates found")

    # 4. Check for dead/unused templates
    print("\n4. DEAD CODE ANALYSIS")
    print("-" * 40)
    dead_templates = find_dead_templates()
    if dead_templates:
        print(f"⚠️  Found {len(dead_templates)} potentially unused templates:")
        for template in sorted(dead_templates):
            print(f"   - {template}")
    else:
        print("✅ All templates appear to be in use")

    # 5. Check alignment
    print("\n5. ALIGNMENT ANALYSIS")
    print("-" * 40)
    alignment_issues = check_alignment()
    if alignment_issues:
        print(f"❌ Found {len(alignment_issues)} alignment issues:")
        for issue in alignment_issues[:10]:  # Show first 10
            print(f"   [{issue['severity']}] {issue['template']}: {issue['issue']}")
        if len(alignment_issues) > 10:
            print(f"   ... and {len(alignment_issues) - 10} more")
    else:
        print("✅ Templates and context validator are aligned")

    # 6. Check for missing header template
    print("\n6. MISSING CRITICAL FILES")
    print("-" * 40)
    critical_files = [
        "sv/pcileech_header.svh.j2",
    ]
    missing_critical = []
    for file in critical_files:
        file_path = PROJECT_ROOT / "src/templates" / file
        if not file_path.exists():
            missing_critical.append(file)

    if missing_critical:
        print(f"❌ Missing critical template files:")
        for file in missing_critical:
            print(f"   - {file}")
    else:
        print("✅ All critical files present")

    # Summary
    print("\n" + "=" * 80)
    print("SUMMARY")
    print("-" * 40)
    total_issues = (
        len(missing_templates)
        + len(security_issues)
        + len(duplicates)
        + len(dead_templates)
        + len(alignment_issues)
        + len(missing_critical)
    )

    if total_issues == 0:
        print("✅ No issues found! Templates are well-maintained.")
    else:
        print(f"⚠️  Total issues found: {total_issues}")
        print("\nRecommended actions:")
        if missing_templates:
            print("  1. Remove obsolete mappings or create missing templates")
        if security_issues:
            print("  2. Fix insecure defaults in template context validator")
        if duplicates:
            print("  3. Consolidate duplicate templates")
        if dead_templates:
            print("  4. Remove or document unused templates")
        if alignment_issues:
            print("  5. Add requirements for templates with undefined variables")
        if missing_critical:
            print("  6. Create missing critical template files")


if __name__ == "__main__":
    main()
