#!/usr/bin/env python3
"""Template Variable Validation CI Script

Enhanced validator used by both CI and the `scripts/check_templates.sh` wrapper.

Features:
1. Extract all variables referenced in Jinja2 templates.
2. Discover where variables are defined (context builders, fallbacks, etc.).
3. Detect variables that appear to have no definition/fallback.
4. Flag potentially unsafe default filter usages.
5. (Optional) Capture runtime context keys from safe builders.
6. (Optional) Emit suggested fallback registration stubs (generate-fixes).
7. Support inline ignore directives for suppressing warnings.

Ignore Directives:
    Templates can suppress unsafe-default warnings using inline comments:

    {# validator: ignore-unsafe-default variable_name #}
    {# validator: ignore-unsafe-default var1, var2, var3 #}

    Use this for module parameters with sensible engineering defaults that
    don't violate the donor-uniqueness principle (e.g., FIFO depth, data width).

CLI Flags (superset of original):
    --format {text,json}        Output style (default: text)
    --strict                    Non‑zero exit on any unsafe variables
    --warnings-as-errors        Treat warnings (unsafe defaults) as failures
    --generate-fixes / --fix    Emit suggested fallback stubs to stdout
    --capture-runtime           Attempt safe runtime context capture
    --verbose / -v              Verbose logging

Backward compatibility: original flags still work; new flags are ignored by
older wrapper versions. The shell wrapper previously passed unsupported
arguments (--format/--strict) causing failures; this module now accepts them.
"""

import argparse
import ast
import importlib
import inspect
import json
import logging
import os
import re
import sys
from collections import defaultdict
from pathlib import Path
from typing import Dict, List, Set, Tuple

# Add parent directory to path for imports
sys.path.insert(0, str(Path(__file__).parent.parent))

try:
    from jinja2 import Environment, FileSystemLoader, meta
    from jinja2.exceptions import TemplateSyntaxError
except ImportError:
    print("Jinja2 is required. Install with: pip install jinja2")
    sys.exit(1)

try:
    from src.device_clone.fallback_manager import get_global_fallback_manager
except ImportError:
    print("PCILeech modules not found. Run from project root.")
    sys.exit(1)

logger = logging.getLogger("template_validator")

# Configuration
TEMPLATES_DIR = Path("src/templates")
CODE_DIRS = [
    Path("src/templating"),
    Path("src/device_clone"),
    Path("src/utils"),  # include unified_context and related builders
    Path("src/build.py"),
]
EXCLUDED_VARS = {
    # Common builtin functions
    "range",
    "len",
    "min",
    "max",
    "sorted",
    "zip",
    "sum",
    "int",
    "hex",
    "hasattr",
    "getattr",
    "isinstance",
    # Jinja2 special variables
    "loop",
    "self",
    "super",
    "namespace",
    # Custom globals
    "generate_tcl_header_comment",
    "throw_error",
    "__version__",
    # Internal template variables with proper defensive handling
    "_dsn_hex",
    "_dsn_valid",
}


class VariableDefinition:
    """Tracks where a variable is defined and used."""

    def __init__(self, name: str):
        self.name = name
        self.templates_used_in: Set[str] = set()
        self.defined_in_files: Set[str] = set()
        self.fallbacks_defined: bool = False
        self.has_default_in_template: bool = False
        self.unsafe_defaults: List[str] = []

    def is_safely_handled(self) -> bool:
        """Check if the variable is safely handled."""
        return bool(self.defined_in_files) or self.fallbacks_defined

    def add_template_usage(self, template_path: str):
        """Add a template where this variable is used."""
        self.templates_used_in.add(template_path)

    def add_definition(self, file_path: str):
        """Add a file where this variable is defined."""
        self.defined_in_files.add(file_path)

    def set_fallback_defined(self):
        """Mark that a fallback is defined for this variable."""
        self.fallbacks_defined = True

    def set_has_default_in_template(self):
        """Mark that the template has a default for this variable."""
        self.has_default_in_template = True

    def add_unsafe_default(self, default_value: str):
        """Add an unsafe default value found in templates."""
        self.unsafe_defaults.append(default_value)

    def __str__(self) -> str:
        """String representation for debugging."""
        status = "✅" if self.is_safely_handled() else "❌"
        used_cnt = len(self.templates_used_in)
        def_cnt = len(self.defined_in_files)
        return (
            f"{status} {self.name} (Used in: {used_cnt} templates, "
            f"Defined in: {def_cnt} files)"
        )


class TemplateVariableValidator:
    """Validates template variables usage and definitions."""

    def __init__(self, verbose: bool = False, capture_runtime: bool = False):
        """Initialize the validator."""
        self.verbose = verbose
        self.variables: Dict[str, VariableDefinition] = {}
        self.captured_runtime = False
        self.capture_runtime = capture_runtime
        # Prefer using the project's TemplateRenderer so custom filters and
        # extensions (e.g. safe_int, sv_hex, python_list, {% error %}) are
        # available during parsing. Fall back to a plain Environment only
        # if the renderer cannot be imported.
        try:
            from src.templating.template_renderer import TemplateRenderer

            renderer = TemplateRenderer(template_dir=TEMPLATES_DIR, strict=True)
            self.env = renderer.env
        except Exception:
            logger.warning(
                "TemplateRenderer unavailable; falling back to basic Jinja2 Env"
            )
            self.env = Environment(loader=FileSystemLoader(TEMPLATES_DIR))

        # Use shared/global fallback manager for CI validation
        self.fallback_manager = get_global_fallback_manager()
        self.setup_logger()
        # Register runtime-provided names (Jinja globals, template constants,
        # and fallback manager keys) as definitions so templates that rely on
        # injected values aren't reported as unsafe.
        self._register_runtime_definitions()

    def _register_runtime_definitions(self):
        """Register names provided by the renderer, constants, and fallbacks."""
        try:
            # Jinja global functions/filters available as variables
            for name in getattr(self.env, "globals", {}).keys():
                if name in EXCLUDED_VARS:
                    continue
                if name not in self.variables:
                    self.variables[name] = VariableDefinition(name)
                self.variables[name].add_definition("<jinja_global>")
        except Exception:
            pass
        try:
            # Register template constants from src.templates.constants
            from src.templates.constants import get_template_constants

            consts = get_template_constants()
            for name in consts.keys():
                if name in EXCLUDED_VARS:
                    continue
                if name not in self.variables:
                    self.variables[name] = VariableDefinition(name)
                self.variables[name].add_definition("<template_constants>")
        except Exception:
            pass

        try:
            # Fallback manager registered keys should be treated as fallbacks
            fm = self.fallback_manager
            exposable = fm.get_exposable_fallbacks()
            for name in exposable.keys():
                if name in EXCLUDED_VARS:
                    continue
                if name not in self.variables:
                    self.variables[name] = VariableDefinition(name)
                self.variables[name].set_fallback_defined()
                self.variables[name].add_definition("<fallback_manager>")
        except Exception:
            pass

    def _capture_runtime_definitions(self):
        """Attempt to capture runtime template keys by calling safe context builders.

        This method is defensive: each import/call is wrapped in try/except to
        avoid executing unsafe code paths. We only call functions that take no
        required arguments (no-arg to_template_context functions) and the
        BuildContext.to_template_context with a safe, minimal BuildContext.
        """
        collected_keys: Set[str] = set()

        # 1) Explicit: BuildContext from src.templating.tcl_builder
        try:
            mod = importlib.import_module("src.templating.tcl_builder")
            if hasattr(mod, "BuildContext"):
                BuildContext = getattr(mod, "BuildContext")
                # Instantiate with safe dummy values (required fields only)
                try:
                    bc = BuildContext(
                        board_name="pcileech_35t325_x4",
                        fpga_part="xc7a35tcsg324-2",
                        fpga_family="Artix-7",
                        pcie_ip_type="7x",
                        max_lanes=1,
                        supports_msi=False,
                        supports_msix=False,
                    )
                    ctx = bc.to_template_context()
                    if isinstance(ctx, dict):
                        collected_keys.update(ctx.keys())
                except Exception:
                    # Ignore errors constructing/using BuildContext
                    pass
        except Exception:
            pass

        # 2) Heuristic: import modules under CODE_DIRS and call no-arg
        #    to_template_context
        for code_dir in CODE_DIRS:
            if not code_dir.exists():
                continue
            if code_dir.is_file():
                files = [code_dir]
            else:
                files = list(code_dir.glob("**/*.py"))

            for f in files:
                try:
                    rel = f.relative_to(Path.cwd())
                except Exception:
                    rel = f

                # Build module name from path: e.g. src/templating/foo.py
                # -> src.templating.foo
                try:
                    mod_name = ".".join(Path(rel).with_suffix("").parts)
                    # Only consider modules under 'src.'
                    if not mod_name.startswith("src."):
                        continue
                    module = importlib.import_module(mod_name)
                except Exception:
                    continue

                func = getattr(module, "to_template_context", None)
                if not func or not callable(func):
                    continue

                # Ensure function has no required parameters
                try:
                    sig = inspect.signature(func)
                    # If any parameter is required (no default and not VAR_*), skip
                    skip = False
                    for p in sig.parameters.values():
                        if (
                            p.kind in (p.VAR_POSITIONAL, p.VAR_KEYWORD)
                            or p.default is not inspect._empty
                        ):
                            continue
                        # Parameter has no default
                        skip = True
                        break
                    if skip:
                        continue
                except Exception:
                    # Can't introspect, skip to be safe
                    continue

                # Call the function and collect keys
                try:
                    result = func()
                    if isinstance(result, dict):
                        collected_keys.update(result.keys())
                except Exception:
                    # Some modules may still perform unsafe operations;
                    # ignore failures
                    continue

        # Register captured keys as definitions
        for key in collected_keys:
            if key in EXCLUDED_VARS:
                continue
            if key not in self.variables:
                self.variables[key] = VariableDefinition(key)
            self.variables[key].add_definition("<runtime_capture>")

        if collected_keys:
            logger.info(f"Captured {len(collected_keys)} runtime template keys")
            self.captured_runtime = True

    # End _capture_runtime_definitions

    def setup_logger(self):
        """Set up logging."""
        handler = logging.StreamHandler()
        formatter = logging.Formatter("%(levelname)s - %(message)s")
        handler.setFormatter(formatter)
        logger.addHandler(handler)
        logger.setLevel(logging.INFO if self.verbose else logging.WARNING)

    def extract_variables_from_template(self, template_path: str) -> Set[str]:
        """Extract all variables from a template file."""
        rel_path = Path(template_path).relative_to(TEMPLATES_DIR)
        template_source = Path(template_path).read_text(encoding="utf-8")

        try:
            # Parse template to get AST
            ast = self.env.parse(template_source)
            # Extract variables
            variables = meta.find_undeclared_variables(ast)
            # Filter out excluded variables
            variables = variables - EXCLUDED_VARS

            # Detect template-local definitions so they aren't reported as missing
            # Macro parameters: {% macro name(arg1, arg2) %}
            local_names = set()
            for m in re.finditer(
                r"{%\s*macro\s+[\w\.]+\s*\(([^)]*)\)", template_source
            ):
                params = m.group(1).strip()
                if params:
                    for p in params.split(","):
                        name = p.split("=")[0].strip()
                        if name:
                            local_names.add(name)

            # Set statements: {% set var = ... %}
            for m in re.finditer(
                r"{%\s*set\s+([A-Za-z_][A-Za-z0-9_]*)\s*=", template_source
            ):
                local_names.add(m.group(1))

            # For-loop targets: handle normal and trimmed tags
            # Patterns cover:
            #   {% for a, b in ... %}
            #   {%- for item in ... %}
            #   {% for (a, b) in ... %}
            for m in re.finditer(r"{%-?\s*for\s+([^\s]+)\s+in\s+", template_source):
                target = m.group(1)
                # split on comma for multiple targets
                for t in target.split(","):
                    name = t.strip()
                    if re.match(r"^[A-Za-z_][A-Za-z0-9_]*$", name):
                        local_names.add(name)

            # Register any local names we care about as definitions
            for name in local_names:
                if name in EXCLUDED_VARS:
                    continue
                if name not in self.variables:
                    # create a placeholder so it shows up in details if used
                    self.variables[name] = VariableDefinition(name)
                self.variables[name].add_definition("<template_local>")

            logger.info(f"Found {len(variables)} variables in {rel_path}")
            return variables

        except TemplateSyntaxError as e:
            logger.error(f"Syntax error in {rel_path}: {e}")
            return set()
        except Exception as e:
            logger.error(f"Error processing {rel_path}: {e}")
            return set()

    def find_ignore_directives(self, template_source: str) -> Set[str]:
        """
        Find validator ignore directives in template comments.

        Supports patterns like:
        {# validator: ignore-unsafe-default variable_name #}
        {# validator: ignore-unsafe-default variable_name, other_var #}
        """
        ignored_vars = set()
        # Pattern: {# validator: ignore-unsafe-default var1, var2, ... #}
        pattern = r"\{#\s*validator:\s*ignore-unsafe-default\s+([^#]+)\s*#\}"

        for match in re.finditer(pattern, template_source):
            var_list = match.group(1).strip()
            # Split by comma and clean up whitespace
            for var in var_list.split(","):
                var = var.strip()
                if var:
                    ignored_vars.add(var)
                    logger.debug(f"Found ignore directive for variable: {var}")

        return ignored_vars

    def find_default_filters(self, template_path: str) -> Dict[str, List[str]]:
        """Find uses of the default filter in templates."""
        template_source = Path(template_path).read_text(encoding="utf-8")
        rel_path = Path(template_path).relative_to(TEMPLATES_DIR)

        # Find ignore directives in this template
        ignored_vars = self.find_ignore_directives(template_source)

        # Pattern: {{ variable|default(...) }} or {{ variable | default(...) }}
        pattern = r"{{\s*([a-zA-Z0-9_\.]+)\s*\|\s*default\(([^)]+)\)\s*}}"

        default_usages = {}
        for match in re.finditer(pattern, template_source):
            var_name = match.group(1)
            default_value = match.group(2).strip()

            if var_name not in default_usages:
                default_usages[var_name] = []

            default_usages[var_name].append(default_value)
            logger.info(
                "Found default filter for %s = %s in %s",
                var_name,
                default_value,
                rel_path,
            )

            # Check for potentially unsafe defaults (unless explicitly ignored)
            if var_name in ignored_vars:
                logger.info(
                    "Ignoring unsafe default check for %s in %s (validator ignore directive)",
                    var_name,
                    rel_path,
                )
            elif default_value not in (
                "''",
                '""',
                "None",
                "[]",
                "{}",
                "0",
                "0.0",
                "False",
                "'unknown'",
                "'Unknown'",
                "'0'",
                "'0.0'",
            ):
                if var_name in self.variables:
                    self.variables[var_name].add_unsafe_default(default_value)

        return default_usages

    def scan_template_files(self):
        """Scan all template files and collect variables."""
        template_files = list(TEMPLATES_DIR.glob("**/*.j2"))
        logger.info(f"Found {len(template_files)} template files")

        for template_file in template_files:
            template_path = str(template_file)
            rel_path = template_file.relative_to(TEMPLATES_DIR)

            # Extract variables
            variables = self.extract_variables_from_template(template_path)

            # Find default filters
            default_filters = self.find_default_filters(template_path)

            # Register variables
            for var_name in variables:
                if var_name not in self.variables:
                    self.variables[var_name] = VariableDefinition(var_name)

                # Record template usage
                self.variables[var_name].add_template_usage(str(rel_path))

                # Check if it has a default in the template
                if var_name in default_filters:
                    self.variables[var_name].set_has_default_in_template()

    def find_variable_definitions(self):
        """Find where variables are defined in the codebase."""
        # Patterns to match variable definitions
        patterns = [
            # template_context[var_name] = value
            r'template_context\[[\'"]([\w\.]+)[\'"]\]\s*=',
            # template_context.setdefault(var_name, value)
            r'template_context\.setdefault\([\'"]([\w\.]+)[\'"]',
            # context.to_template_context() adds variables
            r'def to_template_context.*?return.*?[\'"](\w+)[\'"]',
        ]

        # Fallback manager patterns
        fallback_patterns = [
            r'fallback_manager\.register_fallback\([\'"]([\w\.]+)[\'"]',
            r'fallback_manager\.get_fallback\([\'"]([\w\.]+)[\'"]',
        ]

        for code_dir in CODE_DIRS:
            if code_dir.is_file():
                self._search_file_for_definitions(code_dir, patterns, fallback_patterns)
            else:
                for code_file in code_dir.glob("**/*.py"):
                    self._search_file_for_definitions(
                        code_file, patterns, fallback_patterns
                    )

        # Heuristic: scan code files for literal dict keys (e.g. 'vendor_id': ...)
        # Many template context builders construct dicts with literal keys; treat
        # those keys as definitions to avoid false positives.
        key_pattern = re.compile(r"[\'\"]([A-Za-z0-9_]+)[\'\"]\s*:\s*")
        for code_dir in CODE_DIRS:
            if code_dir.is_file():
                files = [code_dir]
            else:
                files = list(code_dir.glob("**/*.py"))

            for f in files:
                try:
                    content = f.read_text(encoding="utf-8")
                    for m in key_pattern.finditer(content):
                        key = m.group(1)
                        if key in self.variables:
                            try:
                                rel = os.path.relpath(str(f), start=str(Path.cwd()))
                            except Exception:
                                rel = str(f)
                            self.variables[key].add_definition(rel)
                except Exception:
                    continue

        # Additionally, try AST-based extraction to find literal dict keys and
        # explicit return dicts inside `to_template_context` functions. This is
        # more precise than regex scanning and captures nested keys.
        for code_dir in CODE_DIRS:
            if code_dir.is_file():
                afiles = [code_dir]
            else:
                afiles = list(code_dir.glob("**/*.py"))

            for f in afiles:
                try:
                    src = f.read_text(encoding="utf-8")
                    tree = ast.parse(src)
                except Exception:
                    continue

                # Pre-scan assignments to capture variables assigned to dict literals
                dict_assigns = {}
                for node in ast.walk(tree):
                    if isinstance(node, ast.Assign) and isinstance(
                        node.value, ast.Dict
                    ):
                        # Handle simple name targets: tmp = { 'k': v }
                        for t in node.targets:
                            if isinstance(t, ast.Name):
                                keys = set()
                                for key_node in node.value.keys:
                                    if isinstance(
                                        key_node, ast.Constant
                                    ) and isinstance(key_node.value, str):
                                        keys.add(key_node.value)
                                if keys:
                                    dict_assigns[t.id] = keys

                # Walk AST to find dict literal nodes and return statements
                for node in ast.walk(tree):
                    # Detect calls like template_context.update({...}) or
                    # context.update({...}) and extract literal dict keys from
                    # the first positional arg when it's a dict literal. Also
                    # support update(var) where var is previously assigned a
                    # dict literal (captured in dict_assigns).
                    if isinstance(node, ast.Call) and isinstance(
                        node.func, ast.Attribute
                    ):
                        if node.func.attr == "update":
                            # check positional args for dict literals or names
                            for arg in node.args:
                                if isinstance(arg, ast.Dict):
                                    for key_node in arg.keys:
                                        if isinstance(
                                            key_node, ast.Constant
                                        ) and isinstance(key_node.value, str):
                                            key = key_node.value
                                            if key in self.variables:
                                                try:
                                                    rel = os.path.relpath(
                                                        str(f), start=str(Path.cwd())
                                                    )
                                                except Exception:
                                                    rel = str(f)
                                                self.variables[key].add_definition(rel)
                                elif isinstance(arg, ast.Name):
                                    # arg refers to a variable name assigned earlier
                                    varname = arg.id
                                    keys = dict_assigns.get(varname, set())
                                    for key in keys:
                                        if key in self.variables:
                                            try:
                                                rel = os.path.relpath(
                                                    str(f), start=str(Path.cwd())
                                                )
                                            except Exception:
                                                rel = str(f)
                                            self.variables[key].add_definition(rel)

                    # Dict literal keys like {'vendor_id': ...}
                    if isinstance(node, ast.Dict):
                        for key_node in node.keys:
                            if isinstance(key_node, ast.Constant) and isinstance(
                                key_node.value, str
                            ):
                                key = key_node.value
                                if key in self.variables:
                                    try:
                                        rel = os.path.relpath(
                                            str(f), start=str(Path.cwd())
                                        )
                                    except Exception:
                                        rel = str(f)
                                    self.variables[key].add_definition(rel)

                    # Look for functions named to_template_context and returned dicts
                    if (
                        isinstance(node, ast.FunctionDef)
                        and node.name == "to_template_context"
                    ):
                        # Search return statements in this function
                        for sub in ast.walk(node):
                            if isinstance(sub, ast.Return) and sub.value is not None:
                                # If the return value is a dict literal, extract keys
                                if isinstance(sub.value, ast.Dict):
                                    for key_node in sub.value.keys:
                                        if isinstance(
                                            key_node, ast.Constant
                                        ) and isinstance(key_node.value, str):
                                            key = key_node.value
                                            if key in self.variables:
                                                try:
                                                    rel = os.path.relpath(
                                                        str(f), start=str(Path.cwd())
                                                    )
                                                except Exception:
                                                    rel = str(f)
                                                self.variables[key].add_definition(rel)

                    # Detect subscription assignments like
                    # template_context['key'] = ...
                    if isinstance(node, ast.Assign):
                        for tgt in node.targets:
                            if isinstance(tgt, ast.Subscript):
                                # tgt.value may be Name (template_context) or
                                # Attribute
                                try:
                                    sub_value = tgt.value
                                    # slice may be Constant for py3.8+ or
                                    # Index(Constant)
                                    slice_node = tgt.slice
                                    if isinstance(
                                        slice_node, ast.Constant
                                    ) and isinstance(slice_node.value, str):
                                        key = slice_node.value
                                    # Older Python AST (Index) path skipped –
                                    # treat unfamiliar slice nodes as no key.
                                    else:
                                        key = None

                                    if key and isinstance(
                                        sub_value, (ast.Name, ast.Attribute)
                                    ):
                                        if key in self.variables:
                                            try:
                                                rel = os.path.relpath(
                                                    str(f), start=str(Path.cwd())
                                                )
                                            except Exception:
                                                rel = str(f)
                                            self.variables[key].add_definition(rel)
                                except Exception:
                                    pass

                    # Detect setdefault calls:
                    # template_context.setdefault('key', ...)
                    if isinstance(node, ast.Call) and isinstance(
                        node.func, ast.Attribute
                    ):
                        if node.func.attr == "setdefault":
                            # check first positional arg
                            if node.args:
                                first = node.args[0]
                                if isinstance(first, ast.Constant) and isinstance(
                                    first.value, str
                                ):
                                    key = first.value
                                    if key in self.variables:
                                        try:
                                            rel = os.path.relpath(
                                                str(f), start=str(Path.cwd())
                                            )
                                        except Exception:
                                            rel = str(f)
                                        self.variables[key].add_definition(rel)

    def _search_file_for_definitions(
        self, file_path: Path, patterns: List[str], fallback_patterns: List[str]
    ):
        """Search a file for variable definitions and fallbacks."""
        file_content = file_path.read_text(encoding="utf-8")
        # Use os.path.relpath which tolerates absolute vs relative mixes and
        # files outside the current working directory.
        try:
            rel_path = os.path.relpath(str(file_path), start=str(Path.cwd()))
        except Exception:
            rel_path = str(file_path)

        # Look for direct definitions
        for pattern in patterns:
            for match in re.finditer(pattern, file_content, re.DOTALL):
                var_name = match.group(1)
                if var_name in self.variables:
                    self.variables[var_name].add_definition(str(rel_path))
                    logger.info(f"Found definition for {var_name} in {rel_path}")

        # Look for fallback definitions
        for pattern in fallback_patterns:
            for match in re.finditer(pattern, file_content):
                var_name = match.group(1)
                if var_name in self.variables:
                    self.variables[var_name].set_fallback_defined()
                    logger.info(f"Found fallback for {var_name} in {rel_path}")

        # Heuristic: detect keys returned by `to_template_context()` functions.
        # Many builder classes return a literal dict with the template keys; scan
        # the function body for string keys in dict literals and register them.
        try:
            if "def to_template_context" in file_content:
                for func_match in re.finditer(
                    (
                        r"def\s+to_template_context\s*\([^\)]*\):"  # signature
                        r"([\s\S]*?)(?:\n\s*\n|\Z)"  # body until blank line/EOF
                    ),
                    file_content,
                ):
                    body = func_match.group(1)
                    for key_match in re.finditer(
                        r"[\"']([A-Za-z0-9_]+)[\"']\s*:\s", body
                    ):
                        var_name = key_match.group(1)
                        if var_name in self.variables:
                            self.variables[var_name].add_definition(str(rel_path))
                            logger.info(
                                (
                                    f"Found definition for {var_name} in "
                                    f"{rel_path} (to_template_context)"
                                )
                            )
        except Exception:
            # Non-critical heuristic; ignore failures
            pass

    def generate_report(self) -> Tuple[Dict, List[str]]:
        """Generate a report of variable status."""
        report = {
            "total_variables": len(self.variables),
            "safely_handled": 0,
            "unsafe_variables": [],
            "variables_with_unsafe_defaults": [],
            "variables_by_template": defaultdict(list),
            "details": {},
        }

        issues = []

        for var_name, var_info in sorted(self.variables.items()):
            is_safe = var_info.is_safely_handled()
            if is_safe:
                report["safely_handled"] += 1
            else:
                report["unsafe_variables"].append(var_name)
                issues.append(
                    f"❌ Variable '{var_name}' is used but not safely handled"
                )

            if var_info.unsafe_defaults:
                report["variables_with_unsafe_defaults"].append(var_name)
                msg = "⚠️ Variable '%s' unsafe defaults: %s" % (
                    var_name,
                    var_info.unsafe_defaults,
                )
                issues.append(msg)

            # Organize by template
            for template in var_info.templates_used_in:
                report["variables_by_template"][template].append(
                    {
                        "name": var_name,
                        "is_safe": is_safe,
                        "has_fallback": var_info.fallbacks_defined,
                        "defined_in": list(var_info.defined_in_files),
                        "has_default_in_template": var_info.has_default_in_template,
                        "unsafe_defaults": var_info.unsafe_defaults,
                    }
                )

            # Add detailed info
            report["details"][var_name] = {
                "name": var_name,
                "templates_used_in": list(var_info.templates_used_in),
                "defined_in_files": list(var_info.defined_in_files),
                "fallbacks_defined": var_info.fallbacks_defined,
                "has_default_in_template": var_info.has_default_in_template,
                "is_safely_handled": is_safe,
                "unsafe_defaults": var_info.unsafe_defaults,
            }

        return report, issues

    def save_report(
        self, report: Dict, output_file: str = "template_variables_report.json"
    ):
        """Save the report to a JSON file."""
        with open(output_file, "w") as f:
            json.dump(report, f, indent=2)
        logger.info(f"Report saved to {output_file}")

    def print_summary(self, report: Dict, issues: List[str]):
        """Print a summary of the report."""
        total = report.get("total_variables", 0)
        safe = report.get("safely_handled", 0)
        unsafe = len(report.get("unsafe_variables", []))
        unsafe_defaults = len(report.get("variables_with_unsafe_defaults", []))

        # Avoid division by zero when computing percentages
        pct_safe = (safe / total * 100) if total else 0.0
        pct_unsafe = (unsafe / total * 100) if total else 0.0

        print("=" * 80)
        print("TEMPLATE VARIABLE VALIDATION REPORT")
        print("=" * 80)
        print(f"Total variables found: {total}")
        print(f"Safely handled: {safe} ({pct_safe:.1f}%)")
        print(f"Unsafe variables: {unsafe} ({pct_unsafe:.1f}%)")
        print(f"Variables with unsafe defaults: {unsafe_defaults}")
        print("=" * 80)

        if issues:
            print("ISSUES FOUND:")
            for issue in issues:
                print(f" - {issue}")
            print("=" * 80)

        # Print top templates by variable count
        print("Top 5 templates by variable count:")
        template_counts = [
            (t, len(v)) for t, v in report.get("variables_by_template", {}).items()
        ]
        for template, count in sorted(
            template_counts, key=lambda x: x[1], reverse=True
        )[:5]:
            print(f" - {template}: {count} variables")
        print("=" * 80)

    def validate_and_report(self):
        """Run validation and generate a report."""
        # Scan templates
        self.scan_template_files()

        # Optionally capture runtime-provided definitions (safe mode)
        if getattr(self, "capture_runtime", False):
            try:
                self._capture_runtime_definitions()
            except Exception:
                logger.warning("Runtime capture failed; continuing")

        # Find definitions (static analysis)
        self.find_variable_definitions()

        # Generate report
        report, issues = self.generate_report()

        # Save report
        self.save_report(report)

        # Print summary
        self.print_summary(report, issues)

        # Return exit code based on validation results
        return len(report["unsafe_variables"]) == 0


def _emit_fix_suggestions(report: Dict):
    """Emit simple fallback registration suggestions for unsafe variables.

    We intentionally keep this light weight; real fixes require domain review.
    """
    unsafe = report.get("unsafe_variables", [])
    if not unsafe:
        print("No fixes required; all variables are safe.")
        return
    print("\nSuggested fallback registration stubs (review before use):")
    print("# ------------------------------")
    print(
        "from src.device_clone.fallback_manager import " "get_global_fallback_manager"
    )
    print("fm = get_global_fallback_manager()")
    for name in unsafe:
        print(
            "fm.register_fallback('%s', lambda ctx: (_ for _ in ()).throw("
            "RuntimeError('Missing required template variable: %s')))" % (name, name)
        )
    print("# ------------------------------\n")


def main():
    parser = argparse.ArgumentParser(description="Validate template variables")
    parser.add_argument(
        "--verbose", "-v", action="store_true", help="Enable verbose output"
    )
    parser.add_argument(
        "--capture-runtime",
        action="store_true",
        help="Attempt to import and call safe context builders to capture keys",
    )
    # Original --fix retained; alias --generate-fixes
    parser.add_argument(
        "--fix",
        action="store_true",
        help="Deprecated alias for --generate-fixes (kept for compatibility)",
    )
    parser.add_argument(
        "--generate-fixes",
        action="store_true",
        help="Emit suggested fallback registration stubs for unsafe variables",
    )
    parser.add_argument(
        "--output",
        "-o",
        default="template_variables_report.json",
        help="Output file for the JSON report file",
    )
    parser.add_argument(
        "--format",
        choices=["text", "json"],
        default="text",
        help="Console output format (text|json). JSON still writes the report file.",
    )
    parser.add_argument(
        "--strict",
        action="store_true",
        help="Exit non-zero if any unsafe variables are present",
    )
    parser.add_argument(
        "--warnings-as-errors",
        action="store_true",
        help="Treat warning-level issues (unsafe defaults) as failures",
    )
    args = parser.parse_args()

    validator = TemplateVariableValidator(
        verbose=args.verbose, capture_runtime=args.capture_runtime
    )
    success = validator.validate_and_report()

    # Load the report we just wrote so we can inspect details
    try:
        with open(args.output, "r", encoding="utf-8") as fh:
            report = json.load(fh)
    except Exception:
        report = {}

    unsafe_vars = report.get("unsafe_variables", [])
    unsafe_defaults = report.get("variables_with_unsafe_defaults", [])

    if args.generate_fixes or args.fix:
        _emit_fix_suggestions(report)

    # Determine exit conditions
    exit_code = 0
    fail_reasons: List[str] = []
    if args.strict and unsafe_vars:
        exit_code = 1
        fail_reasons.append(
            f"{len(unsafe_vars)} unsafe variable(s) lacking definition/fallback"
        )
    if args.warnings_as_errors and unsafe_defaults:
        exit_code = 1
        num = len(unsafe_defaults)
        fail_reasons.append(
            f"{num} variable(s) with potentially unsafe default filters"
        )

    # Console output mode
    if args.format == "json":
        # Emit JSON summary (the full report already saved to file)
        summary = {
            "unsafe_variables": unsafe_vars,
            "variables_with_unsafe_defaults": unsafe_defaults,
            "success": exit_code == 0,
            "strict": args.strict,
        }
        print(json.dumps(summary, indent=2))
    else:
        if exit_code == 0 and not unsafe_vars:
            print("✅ All template variables are safely handled!")
        elif exit_code == 0 and unsafe_vars:
            # Non-strict mode: surface issues but succeed
            print(
                "⚠️  Unsafe variables detected (non-strict mode). "
                "Use --strict to fail on these."
            )
        if fail_reasons:
            print("❌ Template variable validation failed:")
            for r in fail_reasons:
                print(f" - {r}")

    return exit_code


if __name__ == "__main__":
    sys.exit(main())
