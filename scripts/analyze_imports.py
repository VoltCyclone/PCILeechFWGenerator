#!/usr/bin/env python3
"""
Import Analysis Tool for PCILeech Project
Analyzes all imports in the project and categorizes them.
"""

import ast
import re
from collections import defaultdict
from pathlib import Path
from typing import Dict, List, NamedTuple, Optional, Set


class ImportInfo(NamedTuple):
    """Information about a single import statement."""

    module: str
    names: List[str]  # For 'from X import A, B'
    alias: Optional[str]  # For 'import X as Y'
    is_relative: bool
    level: int  # Number of dots in relative import
    line_number: int
    file_path: str


class CircularImport(NamedTuple):
    """Information about a circular import chain."""

    cycle: List[str]  # List of module names in the cycle
    files: List[str]  # Corresponding file paths


class ImportAnalyzer:
    def __init__(self, project_root: str):
        self.project_root = Path(project_root)
        self.imports = defaultdict(set)
        self.files_analyzed = 0
        self.all_imports_by_file = {}  # file_path -> List[ImportInfo]
        self.module_to_file = {}  # module_name -> file_path
        self.file_to_module = {}  # file_path -> module_name

    def analyze_file(self, file_path: Path) -> Dict[str, Set[str]]:
        """Analyze imports in a single Python file."""
        file_imports = {
            "stdlib": set(),
            "third_party": set(),
            "local": set(),
            "relative": set(),
        }

        imports_in_file = []

        try:
            with open(file_path, "r", encoding="utf-8") as f:
                content = f.read()

            tree = ast.parse(content)

            # Build module name from file path
            relative_path = file_path.relative_to(self.project_root)
            if relative_path.name == "__init__.py":
                module_name = str(relative_path.parent).replace("/", ".")
            else:
                module_name = str(relative_path.with_suffix("")).replace("/", ".")

            self.file_to_module[str(file_path)] = module_name
            self.module_to_file[module_name] = str(file_path)

            for node in ast.walk(tree):
                if isinstance(node, ast.Import):
                    for name in node.names:
                        import_info = ImportInfo(
                            module=name.name,
                            names=[],
                            alias=name.asname,
                            is_relative=False,
                            level=0,
                            line_number=node.lineno,
                            file_path=str(file_path),
                        )
                        imports_in_file.append(import_info)
                        self._categorize_import(name.name, file_imports)

                elif isinstance(node, ast.ImportFrom):
                    if node.module:
                        names = (
                            [alias.name for alias in node.names] if node.names else []
                        )
                        import_info = ImportInfo(
                            module=node.module,
                            names=names,
                            alias=None,
                            is_relative=node.level > 0,
                            level=node.level,
                            line_number=node.lineno,
                            file_path=str(file_path),
                        )
                        imports_in_file.append(import_info)

                        if node.level > 0:  # Relative import
                            file_imports["relative"].add(
                                f"from {'.' * node.level}{node.module or ''} import {', '.join(names) if names else '...'}"
                            )
                        else:
                            self._categorize_import(node.module, file_imports)

        except (SyntaxError, UnicodeDecodeError) as e:
            print(f"Warning: Could not parse {file_path}: {e}")

        self.all_imports_by_file[str(file_path)] = imports_in_file
        return file_imports

    def _categorize_import(self, module_name: str, file_imports: Dict[str, Set[str]]):
        """Categorize an import as stdlib, third-party, or local."""
        if not module_name:
            return

        # Check if it's a standard library module
        if self._is_stdlib_module(module_name):
            file_imports["stdlib"].add(module_name)
        # Check if it's a local module (starts with 'src' or project-specific patterns)
        elif (
            module_name.startswith("src.")
            or module_name.startswith("tests.")
            or module_name in ["config", "generate", "flash", "pcileech"]
        ):
            file_imports["local"].add(module_name)
        else:
            file_imports["third_party"].add(module_name)

    def _is_stdlib_module(self, module_name: str) -> bool:
        """Check if a module is part of the Python standard library."""
        stdlib_modules = {
            "argparse",
            "ast",
            "collections",
            "configparser",
            "copy",
            "ctypes",
            "datetime",
            "enum",
            "fcntl",
            "functools",
            "hashlib",
            "importlib",
            "itertools",
            "json",
            "logging",
            "math",
            "os",
            "pathlib",
            "pickle",
            "re",
            "shutil",
            "subprocess",
            "sys",
            "tempfile",
            "threading",
            "time",
            "typing",
            "uuid",
            "warnings",
            "weakref",
            "struct",
            "unittest",
            "dataclasses",
            "io",
            "traceback",
            "inspect",
            "sqlite3",
            "platform",
            "socket",
            "urllib",
            "http",
            "email",
            "xml",
            "html",
            "gzip",
            "zipfile",
            "tarfile",
            "base64",
            "binascii",
            "codecs",
            "locale",
            "gettext",
            "string",
            "textwrap",
            "unicodedata",
        }

        # Get the top-level module name
        top_level = module_name.split(".")[0]
        return top_level in stdlib_modules

    def analyze_project(self) -> Dict[str, Set[str]]:
        """Analyze all Python files in the project."""
        all_imports = {
            "stdlib": set(),
            "third_party": set(),
            "local": set(),
            "relative": set(),
        }

        python_files = list(self.project_root.rglob("*.py"))
        self.files_analyzed = len(python_files)

        for file_path in python_files:
            # Skip __pycache__ and .git directories
            if "__pycache__" in str(file_path) or ".git" in str(file_path):
                continue

            file_imports = self.analyze_file(file_path)

            # Merge into overall results
            for category, imports in file_imports.items():
                all_imports[category].update(imports)

        return all_imports

    def generate_report(self) -> str:
        """Generate a formatted report of the import analysis."""
        imports = self.analyze_project()

        report = f"""
=== PCILeech Import Analysis Report ===
Files analyzed: {self.files_analyzed}

=== STANDARD LIBRARY IMPORTS ({len(imports['stdlib'])}) ===
"""
        for imp in sorted(imports["stdlib"]):
            report += f"  {imp}\n"

        report += f"\n=== THIRD-PARTY IMPORTS ({len(imports['third_party'])}) ===\n"
        for imp in sorted(imports["third_party"]):
            report += f"  {imp}\n"

        report += f"\n=== LOCAL PROJECT IMPORTS ({len(imports['local'])}) ===\n"
        for imp in sorted(imports["local"]):
            report += f"  {imp}\n"

        report += f"\n=== RELATIVE IMPORTS ({len(imports['relative'])}) ===\n"
        for imp in sorted(imports["relative"]):
            report += f"  {imp}\n"

        return report

    def find_missing_requirements(self) -> List[str]:
        """Find imports that might be missing from requirements.txt."""
        imports = self.analyze_project()

        # Read requirements.txt if it exists
        req_file = self.project_root / "requirements.txt"
        required_packages = set()

        if req_file.exists():
            with open(req_file, "r") as f:
                for line in f:
                    line = line.strip()
                    if line and not line.startswith("#"):
                        # Extract package name
                        pkg_name = re.split(r"[>=<~!]", line)[0].strip()
                        required_packages.add(pkg_name.lower())

        # Map import names to package names
        import_to_package = {
            "yaml": "pyyaml",
            "PIL": "pillow",
            "cv2": "opencv-python",
            "sklearn": "scikit-learn",
            "bs4": "beautifulsoup4",
            "dateutil": "python-dateutil",
        }

        missing = []
        for imp in imports["third_party"]:
            package_name = import_to_package.get(imp, imp)
            if package_name.lower() not in required_packages:
                missing.append(f"{imp} (package: {package_name})")

        return missing

    def find_circular_imports(self) -> List[CircularImport]:
        """Find circular import dependencies."""
        # Build dependency graph
        graph = defaultdict(set)

        for file_path, imports in self.all_imports_by_file.items():
            source_module = self.file_to_module.get(file_path)
            if not source_module:
                continue

            for import_info in imports:
                if import_info.is_relative:
                    # Resolve relative import
                    target_module = self._resolve_relative_import(
                        source_module, import_info
                    )
                    if target_module and target_module in self.module_to_file:
                        graph[source_module].add(target_module)
                elif import_info.module.startswith("src."):
                    # Local absolute import
                    if import_info.module in self.module_to_file:
                        graph[source_module].add(import_info.module)

        # Find cycles using DFS
        cycles = []
        visited = set()
        rec_stack = set()

        def dfs(node, path):
            if node in rec_stack:
                # Found a cycle
                cycle_start = path.index(node)
                cycle = path[cycle_start:] + [node]
                cycles.append(
                    CircularImport(
                        cycle=cycle,
                        files=[self.module_to_file.get(mod, mod) for mod in cycle],
                    )
                )
                return

            if node in visited:
                return

            visited.add(node)
            rec_stack.add(node)
            path.append(node)

            for neighbor in graph[node]:
                dfs(neighbor, path[:])

            rec_stack.remove(node)

        for node in list(graph.keys()):
            if node not in visited:
                dfs(node, [])

        return cycles

    def _resolve_relative_import(
        self, source_module: str, import_info: ImportInfo
    ) -> Optional[str]:
        """Resolve a relative import to an absolute module name."""
        if not import_info.is_relative:
            return import_info.module

        parts = source_module.split(".")

        # Go up the specified number of levels
        if import_info.level > len(parts):
            return None

        base_parts = parts[: -import_info.level] if import_info.level > 0 else parts

        if import_info.module:
            return ".".join(base_parts + import_info.module.split("."))
        else:
            return ".".join(base_parts)

    def find_unused_imports(self) -> Dict[str, List[ImportInfo]]:
        """Find imports that are never used in their respective files."""
        unused_by_file = {}

        for file_path, imports in self.all_imports_by_file.items():
            try:
                with open(file_path, "r", encoding="utf-8") as f:
                    content = f.read()

                unused = []
                for import_info in imports:
                    if not self._is_import_used(content, import_info):
                        unused.append(import_info)

                if unused:
                    unused_by_file[file_path] = unused

            except Exception as e:
                print(f"Warning: Could not analyze usage in {file_path}: {e}")

        return unused_by_file

    def _is_import_used(self, content: str, import_info: ImportInfo) -> bool:
        """Check if an import is used in the file content."""
        lines = content.split("\n")
        import_line = (
            lines[import_info.line_number - 1]
            if import_info.line_number <= len(lines)
            else ""
        )

        # Remove the import line from content to avoid false positives
        content_without_import = "\n".join(
            line for i, line in enumerate(lines) if i != import_info.line_number - 1
        )

        if import_info.names:  # from X import Y
            for name in import_info.names:
                if name == "*":
                    return True  # Assume star imports are used
                # Look for usage of the imported name
                if re.search(rf"\b{re.escape(name)}\b", content_without_import):
                    return True
        else:  # import X or import X as Y
            name_to_check = import_info.alias or import_info.module.split(".")[-1]
            if re.search(rf"\b{re.escape(name_to_check)}\b", content_without_import):
                return True

        return False

    def check_import_style_consistency(self) -> Dict[str, List[str]]:
        """Check for inconsistent import styles."""
        issues_by_file = {}

        for file_path, imports in self.all_imports_by_file.items():
            issues = []

            # Group imports by type
            stdlib_imports = []
            third_party_imports = []
            local_imports = []

            for import_info in imports:
                if self._is_stdlib_module(import_info.module):
                    stdlib_imports.append(import_info)
                elif import_info.module.startswith("src."):
                    local_imports.append(import_info)
                else:
                    third_party_imports.append(import_info)

            # Check if imports are grouped and ordered correctly
            all_imports = stdlib_imports + third_party_imports + local_imports
            sorted_imports = sorted(imports, key=lambda x: x.line_number)

            # Check for mixed import styles
            import_styles = set()
            from_import_styles = set()

            for imp in imports:
                if imp.names:  # from X import Y
                    if len(imp.names) == 1:
                        from_import_styles.add("single")
                    else:
                        from_import_styles.add("multiple")
                else:  # import X
                    import_styles.add("direct")

            # Check for issues
            if len(import_styles) > 1:
                issues.append(
                    "Mixed import styles: both 'import X' and 'from X import Y' used"
                )

            if len(from_import_styles) > 1:
                issues.append(
                    "Inconsistent from-import styles: both single and multiple imports"
                )

            # Check if imports are not grouped by type
            current_type = None
            for imp in sorted_imports:
                if self._is_stdlib_module(imp.module):
                    imp_type = "stdlib"
                elif imp.module.startswith("src."):
                    imp_type = "local"
                else:
                    imp_type = "third_party"

                if current_type and imp_type != current_type:
                    # Check if this is a valid transition
                    valid_transitions = [
                        ("stdlib", "third_party"),
                        ("third_party", "local"),
                        ("stdlib", "local"),  # If no third-party
                    ]
                    if (current_type, imp_type) not in valid_transitions:
                        issues.append(
                            f"Import ordering issue: {imp_type} import after {current_type} import"
                        )
                        break

                current_type = imp_type

            if issues:
                issues_by_file[file_path] = issues

        return issues_by_file

    def generate_enhanced_report(self) -> str:
        """Generate a comprehensive import analysis report."""
        # First run the basic analysis
        imports = self.analyze_project()

        # Then run the advanced analyses
        circular_imports = self.find_circular_imports()
        unused_imports = self.find_unused_imports()
        missing_imports = self.find_missing_local_imports()
        style_issues = self.check_import_style_consistency()

        report = f"""
=== PCILeech Enhanced Import Analysis Report ===
Files analyzed: {self.files_analyzed}

=== STANDARD LIBRARY IMPORTS ({len(imports['stdlib'])}) ===
"""
        for imp in sorted(imports["stdlib"]):
            report += f"  {imp}\n"

        report += f"\n=== THIRD-PARTY IMPORTS ({len(imports['third_party'])}) ===\n"
        for imp in sorted(imports["third_party"]):
            report += f"  {imp}\n"

        report += f"\n=== LOCAL PROJECT IMPORTS ({len(imports['local'])}) ===\n"
        for imp in sorted(imports["local"]):
            report += f"  {imp}\n"

        # Circular imports analysis
        report += f"\n=== CIRCULAR IMPORTS ANALYSIS ===\n"
        if circular_imports:
            report += f"⚠️  Found {len(circular_imports)} circular import chain(s):\n"
            for i, cycle in enumerate(circular_imports, 1):
                report += f"  {i}. {' → '.join(cycle.cycle)}\n"
                report += (
                    f"     Files: {' → '.join([Path(f).name for f in cycle.files])}\n"
                )
        else:
            report += "✅ No circular imports detected\n"

        # Unused imports analysis
        report += f"\n=== UNUSED IMPORTS ANALYSIS ===\n"
        if unused_imports:
            report += f"⚠️  Found unused imports in {len(unused_imports)} file(s):\n"
            for file_path, unused_list in unused_imports.items():
                report += f"  📄 {Path(file_path).name}:\n"
                for unused in unused_list:
                    if unused.names:
                        report += f"    - from {unused.module} import {', '.join(unused.names)} (line {unused.line_number})\n"
                    else:
                        import_name = (
                            f"{unused.module} as {unused.alias}"
                            if unused.alias
                            else unused.module
                        )
                        report += (
                            f"    - import {import_name} (line {unused.line_number})\n"
                        )
        else:
            report += "✅ No unused imports detected\n"

        # Missing imports analysis
        report += f"\n=== MISSING IMPORTS ANALYSIS ===\n"
        if missing_imports:
            report += f"⚠️  Found potentially missing imports in {len(missing_imports)} file(s):\n"
            for file_path, missing_list in missing_imports.items():
                report += f"  📄 {Path(file_path).name}:\n"
                for missing in missing_list:
                    report += f"    - {missing}\n"
        else:
            report += "✅ No obviously missing imports detected\n"

        # Style consistency analysis
        report += f"\n=== IMPORT STYLE CONSISTENCY ===\n"
        if style_issues:
            report += f"⚠️  Found style issues in {len(style_issues)} file(s):\n"
            for file_path, issues in style_issues.items():
                report += f"  📄 {Path(file_path).name}:\n"
                for issue in issues:
                    report += f"    - {issue}\n"
        else:
            report += "✅ Import styles are consistent\n"

        # Summary and recommendations
        total_issues = (
            len(circular_imports)
            + len(unused_imports)
            + len(missing_imports)
            + len(style_issues)
        )
        report += f"\n=== SUMMARY ===\n"
        report += f"Total files analyzed: {self.files_analyzed}\n"
        report += f"Files with issues: {total_issues}\n"

        if total_issues > 0:
            report += f"\n=== RECOMMENDATIONS ===\n"
            if circular_imports:
                report += "• Fix circular imports by restructuring dependencies or using late imports\n"
            if unused_imports:
                report += "• Remove unused imports to clean up code\n"
            if missing_imports:
                report += "• Review potentially missing imports (some may be false positives)\n"
            if style_issues:
                report += "• Consider using a tool like isort to standardize import formatting\n"
        else:
            report += (
                "\n🎉 All import checks passed! Your imports are well-organized.\n"
            )

        return report

    def find_missing_local_imports(self) -> Dict[str, List[str]]:
        """Find missing local imports by analyzing undefined names that could be local modules."""
        missing_by_file = {}

        for file_path, imports in self.all_imports_by_file.items():
            try:
                with open(file_path, "r", encoding="utf-8") as f:
                    content = f.read()

                tree = ast.parse(content)

                # Get all names that are used but not defined
                used_names = set()
                defined_names = set()
                imported_names = set()

                # Collect imported names
                for import_info in imports:
                    if import_info.names:
                        imported_names.update(import_info.names)
                    else:
                        name = import_info.alias or import_info.module.split(".")[-1]
                        imported_names.add(name)

                # Walk the AST to find used and defined names
                for node in ast.walk(tree):
                    if isinstance(node, ast.Name):
                        if isinstance(node.ctx, ast.Load):
                            used_names.add(node.id)
                        elif isinstance(node.ctx, ast.Store):
                            defined_names.add(node.id)
                    elif isinstance(node, ast.FunctionDef):
                        defined_names.add(node.name)
                    elif isinstance(node, ast.ClassDef):
                        defined_names.add(node.name)

                # Find names that are used but not imported or defined
                undefined_names = used_names - defined_names - imported_names

                # Filter to likely module names (check if they exist in the project)
                potential_missing = []
                for name in undefined_names:
                    # Skip built-ins and common names
                    if name in {
                        "print",
                        "len",
                        "str",
                        "int",
                        "float",
                        "bool",
                        "list",
                        "dict",
                        "set",
                        "tuple",
                        "range",
                        "enumerate",
                        "zip",
                        "open",
                        "max",
                        "min",
                        "sum",
                        "abs",
                        "round",
                        "sorted",
                        "reversed",
                        "any",
                        "all",
                        "super",
                        "Exception",
                        "ValueError",
                        "TypeError",
                        "KeyError",
                        "AttributeError",
                    }:
                        continue

                    # Check if this could be a local module
                    for module_name in self.module_to_file.keys():
                        if (
                            module_name.endswith(f".{name}")
                            or module_name.split(".")[-1] == name
                        ):
                            potential_missing.append(
                                f"{name} (possibly from {module_name})"
                            )
                            break

                if potential_missing:
                    missing_by_file[file_path] = potential_missing

            except Exception as e:
                print(f"Warning: Could not analyze {file_path}: {e}")

        return missing_by_file


def main():
    """Main entry point for the import analyzer."""
    # Determine project root - go up one directory from the script location
    script_dir = Path(__file__).resolve().parent
    project_root = script_dir.parent

    analyzer = ImportAnalyzer(str(project_root))

    # Generate and print the enhanced report
    report = analyzer.generate_enhanced_report()
    print(report)


if __name__ == "__main__":
    main()
