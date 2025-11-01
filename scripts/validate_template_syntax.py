#!/usr/bin/env python3
"""
Validate all Jinja2 template syntax for the repository.

This script validates Jinja2 templates using either the project's TemplateRenderer
or a fallback Jinja2 environment. It supports parallel processing, custom filters,
and detailed error reporting.

Exit codes:
    0: All templates valid
    1: Template syntax errors found
    2: Configuration or dependency errors
    3: File system errors
"""
import argparse
import concurrent.futures
import json
import logging
import sys
from dataclasses import dataclass, field
from enum import IntEnum
from pathlib import Path
from typing import Any, Dict, List, Optional, Set, Tuple, Union

# Try to import project-specific renderer (prefer importing as `src.templating`)
try:
    # Ensure repository root is on sys.path so `src` package can be imported
    repo_root = Path(__file__).resolve().parents[1]
    if str(repo_root) not in sys.path:
        sys.path.insert(0, str(repo_root))
    from src.templating.template_renderer import TemplateRenderer

    HAS_TEMPLATE_RENDERER = True
except Exception:
    HAS_TEMPLATE_RENDERER = False

# Try to import Jinja2
try:
    from jinja2 import (Environment, FileSystemLoader, TemplateError,
                        TemplateSyntaxError)

    HAS_JINJA2 = True
except ImportError:
    HAS_JINJA2 = False


class ExitCode(IntEnum):
    """Exit codes for the validation script."""

    SUCCESS = 0
    TEMPLATE_ERRORS = 1
    DEPENDENCY_ERROR = 2
    FILESYSTEM_ERROR = 3


@dataclass
class ValidationResult:
    """Result of validating a single template."""

    path: Path
    relative_path: Path
    success: bool
    error: Optional[str] = None
    line_number: Optional[int] = None
    column_number: Optional[int] = None
    error_type: Optional[str] = None


@dataclass
class ValidationConfig:
    """Configuration for template validation."""

    base_path: Path = field(default_factory=lambda: Path("src/templates"))
    patterns: List[str] = field(
        default_factory=lambda: ["**/*.j2", "**/*.jinja", "**/*.jinja2"]
    )
    exclude_patterns: List[str] = field(default_factory=list)
    parallel: bool = True
    max_workers: Optional[int] = None
    verbose: bool = False
    quiet: bool = False
    json_output: bool = False
    fail_fast: bool = False
    cache_templates: bool = True


class TemplateValidator:
    """Base class for template validators."""

    def __init__(self, config: ValidationConfig):
        self.config = config
        self.logger = self._setup_logger()
        self._template_cache: Dict[str, Any] = {}

    def _setup_logger(self) -> logging.Logger:
        """Set up logging based on configuration."""
        logger = logging.getLogger(self.__class__.__name__)
        handler = logging.StreamHandler(sys.stdout)

        if self.config.quiet:
            level = logging.ERROR
        elif self.config.verbose:
            level = logging.DEBUG
        else:
            level = logging.INFO

        logger.setLevel(level)
        handler.setLevel(level)

        formatter = logging.Formatter("%(levelname)s: %(message)s")
        handler.setFormatter(formatter)
        logger.addHandler(handler)

        return logger

    def collect_templates(self) -> List[Path]:
        """Collect all template files based on patterns."""
        if not self.config.base_path.exists():
            raise FileNotFoundError(
                f"Template directory not found: {self.config.base_path}"
            )

        templates: Set[Path] = set()

        # Collect files matching patterns
        for pattern in self.config.patterns:
            templates.update(self.config.base_path.glob(pattern))

        # Exclude files matching exclude patterns
        if self.config.exclude_patterns:
            excluded: Set[Path] = set()
            for pattern in self.config.exclude_patterns:
                excluded.update(self.config.base_path.glob(pattern))
            templates -= excluded

        return sorted(templates)

    def validate_template(self, template_path: Path) -> ValidationResult:
        """Validate a single template. Must be implemented by subclasses."""
        raise NotImplementedError

    def validate_all(self) -> Tuple[List[ValidationResult], int]:
        """Validate all templates and return results with error count."""
        try:
            templates = self.collect_templates()
        except FileNotFoundError as e:
            self.logger.error(str(e))
            return [], ExitCode.FILESYSTEM_ERROR

        if not templates:
            self.logger.info("No templates found to validate")
            return [], ExitCode.SUCCESS

        self.logger.info(f"Found {len(templates)} templates to validate")

        results: List[ValidationResult] = []
        error_count = 0

        if self.config.parallel and len(templates) > 1:
            results, error_count = self._validate_parallel(templates)
        else:
            results, error_count = self._validate_sequential(templates)

        return results, error_count

    def _validate_sequential(
        self, templates: List[Path]
    ) -> Tuple[List[ValidationResult], int]:
        """Validate templates sequentially."""
        results = []
        error_count = 0

        for template in templates:
            result = self.validate_template(template)
            results.append(result)

            if not result.success:
                error_count += 1
                if self.config.fail_fast:
                    break

        return results, error_count

    def _validate_parallel(
        self, templates: List[Path]
    ) -> Tuple[List[ValidationResult], int]:
        """Validate templates in parallel."""
        results = []
        error_count = 0

        with concurrent.futures.ThreadPoolExecutor(
            max_workers=self.config.max_workers
        ) as executor:
            future_to_template = {
                executor.submit(self.validate_template, template): template
                for template in templates
            }

            for future in concurrent.futures.as_completed(future_to_template):
                result = future.result()
                results.append(result)

                if not result.success:
                    error_count += 1
                    if self.config.fail_fast:
                        # Cancel remaining futures
                        for f in future_to_template:
                            f.cancel()
                        break

        # Sort results by path for consistent output
        results.sort(key=lambda r: r.path)
        return results, error_count


class ProjectTemplateValidator(TemplateValidator):
    """Validator using the project's TemplateRenderer."""

    def __init__(self, config: ValidationConfig):
        super().__init__(config)
        try:
            self.renderer = TemplateRenderer(config.base_path)
            self.env = self.renderer.env
        except Exception as e:
            raise RuntimeError(f"Failed to initialize TemplateRenderer: {e}")

    def validate_template(self, template_path: Path) -> ValidationResult:
        """Validate a template using the project's renderer."""
        relative_path = template_path.relative_to(self.config.base_path)

        try:
            if (
                self.config.cache_templates
                and str(relative_path) in self._template_cache
            ):
                # Template already validated successfully
                self.logger.debug(f"Using cached validation for {relative_path}")
            else:
                template = self.env.get_template(str(relative_path))
                if self.config.cache_templates:
                    self._template_cache[str(relative_path)] = template

            self.logger.info(f"✓ {relative_path}")
            return ValidationResult(
                path=template_path, relative_path=relative_path, success=True
            )
        except TemplateSyntaxError as e:
            self.logger.error(f"✗ {relative_path}: {e.message}")
            return ValidationResult(
                path=template_path,
                relative_path=relative_path,
                success=False,
                error=e.message,
                line_number=e.lineno,
                error_type="syntax",
            )
        except Exception as e:
            self.logger.error(f"✗ {relative_path}: {str(e)}")
            return ValidationResult(
                path=template_path,
                relative_path=relative_path,
                success=False,
                error=str(e),
                error_type="load",
            )


class Jinja2TemplateValidator(TemplateValidator):
    """Validator using standard Jinja2."""

    def __init__(self, config: ValidationConfig):
        super().__init__(config)
        if not HAS_JINJA2:
            raise RuntimeError("Jinja2 is not installed")

        # Import ErrorTagExtension if available
        try:
            from jinja2.ext import Extension
            from jinja2 import nodes, TemplateRuntimeError
            
            class ErrorTagExtension(Extension):
                """Custom Jinja2 extension to handle {% error %} tags."""
                tags = {"error"}
                
                def parse(self, parser):
                    lineno = next(parser.stream).lineno
                    args = [parser.parse_expression()]
                    return nodes.CallBlock(
                        self.call_method("_raise_error", args), [], [], []
                    ).set_lineno(lineno)
                
                def _raise_error(self, message, caller):
                    raise TemplateRuntimeError(message)
            
            extensions = [ErrorTagExtension, "jinja2.ext.do"]
        except Exception:
            extensions = ["jinja2.ext.do"]

        self.env = Environment(
            loader=FileSystemLoader(str(config.base_path)),
            cache_size=1000 if config.cache_templates else 0,
            extensions=extensions,
        )
        
        # Register custom filters used in templates
        self._register_custom_filters()

    def _register_custom_filters(self):
        """Register custom filters needed by templates."""
        import math
        
        def safe_int(value, default=0):
            """Convert value to int with fallback."""
            if value is None:
                return default
            if isinstance(value, int):
                return value
            if isinstance(value, (float, complex)):
                return int(value)
            # Handle hex strings
            try:
                if isinstance(value, str):
                    value = value.strip()
                    if value.startswith('0x') or value.startswith('0X'):
                        return int(value, 16)
                    return int(value, 0)  # Auto-detect base
                return int(value)
            except (ValueError, TypeError):
                return default
        
        def python_list(value):
            """Format value as Python list literal."""
            if isinstance(value, list):
                formatted_items = []
                for item in value:
                    if isinstance(item, (int, float)):
                        formatted_items.append(str(item))
                    else:
                        formatted_items.append(repr(str(item)))
                return "[" + ", ".join(formatted_items) + "]"
            elif isinstance(value, (str, int, float)):
                return repr([value])
            else:
                return "[]"
        
        def hex_format(value, width=4):
            """Format integer as hex string."""
            try:
                if isinstance(value, str):
                    value = int(value, 0)
                return f"{int(value):0{width}x}"
            except (ValueError, TypeError):
                return "0" * width
        
        def clog2(v):
            """Calculate ceiling of log2."""
            if v <= 0:
                return 0
            return int(math.ceil(math.log2(v)))
        
        # Register all custom filters
        self.env.filters["safe_int"] = safe_int
        self.env.filters["python_list"] = python_list
        self.env.filters["hex"] = hex_format
        self.env.filters["clog2"] = clog2
        self.env.filters["log2"] = clog2

    def validate_template(self, template_path: Path) -> ValidationResult:
        """Validate a template using standard Jinja2."""
        relative_path = template_path.relative_to(self.config.base_path)

        try:
            template = self.env.get_template(str(relative_path))
            self.logger.info(f"✓ {relative_path}")
            return ValidationResult(
                path=template_path, relative_path=relative_path, success=True
            )
        except TemplateSyntaxError as e:
            self.logger.error(
                f"✗ {relative_path}: Syntax error at line {e.lineno}: {e.message}"
            )
            return ValidationResult(
                path=template_path,
                relative_path=relative_path,
                success=False,
                error=e.message,
                line_number=e.lineno,
                error_type="syntax",
            )
        except TemplateError as e:
            self.logger.error(f"✗ {relative_path}: Template error: {str(e)}")
            return ValidationResult(
                path=template_path,
                relative_path=relative_path,
                success=False,
                error=str(e),
                error_type="template",
            )
        except Exception as e:
            self.logger.error(f"✗ {relative_path}: Unexpected error: {str(e)}")
            return ValidationResult(
                path=template_path,
                relative_path=relative_path,
                success=False,
                error=str(e),
                error_type="unknown",
            )


def format_results_json(results: List[ValidationResult]) -> str:
    """Format validation results as JSON."""
    data = {
        "total": len(results),
        "passed": sum(1 for r in results if r.success),
        "failed": sum(1 for r in results if not r.success),
        "results": [
            {
                "path": str(r.relative_path),
                "success": r.success,
                "error": r.error,
                "line": r.line_number,
                "column": r.column_number,
                "error_type": r.error_type,
            }
            for r in results
        ],
    }
    return json.dumps(data, indent=2)


def format_results_summary(results: List[ValidationResult], error_count: int) -> str:
    """Format a summary of validation results."""
    total = len(results)
    passed = total - error_count

    if error_count == 0:
        return f"\n✅ All {total} templates validated successfully!"
    else:
        failed_templates = [r for r in results if not r.success]
        summary = f"\n❌ Template validation failed: {error_count} errors in {total} templates\n"
        summary += "\nFailed templates:\n"
        for result in failed_templates:
            summary += f"  - {result.relative_path}"
            if result.line_number:
                summary += f" (line {result.line_number})"
            summary += f": {result.error}\n"
        return summary


def parse_arguments() -> argparse.Namespace:
    """Parse command-line arguments."""
    parser = argparse.ArgumentParser(
        description="Validate Jinja2 template syntax",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
Exit codes:
  0 - All templates valid
  1 - Template syntax errors found
  2 - Configuration or dependency errors
  3 - File system errors
        """,
    )

    parser.add_argument(
        "--base-path",
        "-b",
        type=Path,
        default=Path("src/templates"),
        help="Base directory containing templates (default: src/templates)",
    )

    parser.add_argument(
        "--pattern",
        "-p",
        action="append",
        dest="patterns",
        help="Glob pattern for template files (can be specified multiple times)",
    )

    parser.add_argument(
        "--exclude",
        "-e",
        action="append",
        dest="exclude_patterns",
        help="Glob pattern for files to exclude (can be specified multiple times)",
    )

    parser.add_argument(
        "--no-parallel", action="store_true", help="Disable parallel validation"
    )

    parser.add_argument(
        "--workers", "-w", type=int, help="Maximum number of parallel workers"
    )

    parser.add_argument(
        "--verbose", "-v", action="store_true", help="Enable verbose output"
    )

    parser.add_argument(
        "--quiet", "-q", action="store_true", help="Suppress non-error output"
    )

    parser.add_argument("--json", action="store_true", help="Output results as JSON")

    parser.add_argument(
        "--fail-fast", action="store_true", help="Stop validation on first error"
    )

    parser.add_argument(
        "--no-cache", action="store_true", help="Disable template caching"
    )

    parser.add_argument(
        "--force-jinja2",
        action="store_true",
        help="Force use of standard Jinja2 instead of project renderer",
    )

    return parser.parse_args()


def main() -> int:
    """Main entry point."""
    args = parse_arguments()

    # Build configuration from arguments
    config = ValidationConfig(
        base_path=args.base_path,
        patterns=args.patterns or ["**/*.j2", "**/*.jinja", "**/*.jinja2"],
        exclude_patterns=args.exclude_patterns or [],
        parallel=not args.no_parallel,
        max_workers=args.workers,
        verbose=args.verbose,
        quiet=args.quiet,
        json_output=args.json,
        fail_fast=args.fail_fast,
        cache_templates=not args.no_cache,
    )

    # Choose validator
    validator: Optional[TemplateValidator] = None

    if not args.force_jinja2 and HAS_TEMPLATE_RENDERER:
        try:
            validator = ProjectTemplateValidator(config)
            if not config.quiet:
                print("Using project TemplateRenderer")
        except Exception as e:
            if config.verbose:
                print(f"Failed to use TemplateRenderer: {e}")
            if HAS_JINJA2:
                if not config.quiet:
                    print("Falling back to standard Jinja2")
                validator = Jinja2TemplateValidator(config)
    elif HAS_JINJA2:
        validator = Jinja2TemplateValidator(config)
        if not config.quiet:
            print("Using standard Jinja2")

    if validator is None:
        print("ERROR: No template validation engine available", file=sys.stderr)
        print("Install jinja2 or ensure TemplateRenderer is available", file=sys.stderr)
        return ExitCode.DEPENDENCY_ERROR

    # Validate templates
    try:
        results, error_count = validator.validate_all()
    except Exception as e:
        print(f"ERROR: Validation failed: {e}", file=sys.stderr)
        return ExitCode.DEPENDENCY_ERROR

    # Output results
    if config.json_output:
        print(format_results_json(results))
    elif not config.quiet:
        print(format_results_summary(results, error_count))

    # Return appropriate exit code
    if isinstance(error_count, ExitCode):
        return error_count
    elif error_count > 0:
        return ExitCode.TEMPLATE_ERRORS
    else:
        return ExitCode.SUCCESS


if __name__ == "__main__":
    sys.exit(main())
