#!/usr/bin/env python3

"""
Enhanced templating diagnostics for SystemVerilog generation.

This module provides improved diagnostic capabilities for template rendering,
including detailed error reporting, performance monitoring, and debugging aids.

Part of Phase 3 of the SystemVerilog refactor plan - improves developer
experience with better error messages and diagnostic information.
"""

import logging

import time

from contextlib import contextmanager

from typing import Any, Dict, List, Optional

from src.string_utils import log_debug_safe, log_error_safe, log_info_safe

# Create logger for diagnostics
logger = logging.getLogger(__name__)


class TemplateDiagnostics:
    """Enhanced diagnostic capabilities for template operations."""

    def __init__(
        self, verbose_errors: bool = False, performance_tracking: bool = False
    ):
        """
        Initialize template diagnostics.

        Args:
            verbose_errors: If True, show detailed error traces
            performance_tracking: If True, track rendering performance
        """
        self.verbose_errors = verbose_errors
        self.performance_tracking = performance_tracking
        self._render_stats: List[Dict[str, Any]] = []

    @contextmanager
    def track_rendering(self, template_name: str, context_size: int):
        """
        Context manager to track template rendering performance.

        Args:
            template_name: Name of template being rendered
            context_size: Number of context variables
        """
        if not self.performance_tracking:
            yield
            return

        start_time = time.time()
        start_memory = self._get_memory_usage()

        try:
            yield
            success = True
            error_msg = None
        except Exception as e:
            success = False
            error_msg = str(e)
            raise
        finally:
            end_time = time.time()
            end_memory = self._get_memory_usage()

            stats = {
                "template_name": template_name,
                "context_size": context_size,
                "duration_ms": (end_time - start_time) * 1000,
                "memory_delta_mb": (end_memory - start_memory) / 1024 / 1024,
                "success": success,
                "error": error_msg,
                "timestamp": time.time(),
            }

            self._render_stats.append(stats)

            if self.performance_tracking:
                log_debug_safe(
                    logger,
                    "Template {name} rendered in {duration:.2f}ms "
                    "(context: {size} vars, memory: {memory:+.2f}MB)",
                    name=template_name,
                    duration=stats["duration_ms"],
                    size=context_size,
                    memory=stats["memory_delta_mb"],
                )

    def format_enhanced_error(
        self,
        template_name: str,
        original_error: Exception,
        context: Optional[Dict[str, Any]] = None,
        line_number: Optional[int] = None,
    ) -> str:
        """
        Format an enhanced error message with diagnostic information.

        Args:
            template_name: Name of the template that failed
            original_error: The original exception
            context: Template context (optional)
            line_number: Line number where error occurred (optional)

        Returns:
            Enhanced error message with diagnostic details
        """
        if not self.verbose_errors:
            return f"Template '{template_name}' failed: {original_error}"

        error_parts = [
            f"=== Template Rendering Error ===",
            f"Template: {template_name}",
            f"Error Type: {type(original_error).__name__}",
            f"Message: {original_error}",
        ]

        if line_number:
            error_parts.append(f"Line: {line_number}")

        if context:
            error_parts.extend(
                [
                    "",
                    "Context Analysis:",
                    f"  - Total variables: {len(context)}",
                    f"  - Available keys: {sorted(context.keys())[:10]}",
                ]
            )

            if len(context) > 10:
                error_parts.append(f"  - ... and {len(context) - 10} more")

            # Check for common missing variables
            missing_vars = self._detect_missing_variables(str(original_error))
            if missing_vars:
                error_parts.extend(
                    [
                        "",
                        "Likely Missing Variables:",
                    ]
                )
                for var in missing_vars:
                    error_parts.append(f"  - {var}")

        error_parts.extend(
            [
                "",
                "Troubleshooting:",
                "  1. Check template syntax and variable names",
                "  2. Verify all required context variables are provided",
                "  3. Review template inheritance and includes",
                "  4. Check for typos in variable references",
                "",
            ]
        )

        return "\n".join(error_parts)

    def get_performance_summary(self) -> Dict[str, Any]:
        """
        Get summary of template rendering performance.

        Returns:
            Dictionary with performance statistics
        """
        if not self._render_stats:
            return {"message": "No rendering statistics available"}

        total_renders = len(self._render_stats)
        successful_renders = sum(1 for s in self._render_stats if s["success"])
        failed_renders = total_renders - successful_renders

        durations = [s["duration_ms"] for s in self._render_stats if s["success"]]

        if durations:
            avg_duration = sum(durations) / len(durations)
            max_duration = max(durations)
            min_duration = min(durations)
        else:
            avg_duration = max_duration = min_duration = 0

        # Group by template name
        by_template: Dict[str, List[float]] = {}
        for stat in self._render_stats:
            if stat["success"]:
                template = stat["template_name"]
                if template not in by_template:
                    by_template[template] = []
                by_template[template].append(stat["duration_ms"])

        slowest_templates = sorted(
            [(name, max(times)) for name, times in by_template.items()],
            key=lambda x: x[1],
            reverse=True,
        )[:5]

        return {
            "total_renders": total_renders,
            "successful_renders": successful_renders,
            "failed_renders": failed_renders,
            "success_rate": (
                successful_renders / total_renders if total_renders > 0 else 0
            ),
            "avg_duration_ms": avg_duration,
            "max_duration_ms": max_duration,
            "min_duration_ms": min_duration,
            "slowest_templates": slowest_templates,
            "unique_templates": len(by_template),
        }

    def print_performance_report(self) -> None:
        """Print a formatted performance report."""
        summary = self.get_performance_summary()

        if "message" in summary:
            print(summary["message"])
            return

        print("\n=== Template Performance Report ===")
        print(f"Total Renders: {summary['total_renders']}")
        print(f"Success Rate: {summary['success_rate']:.1%}")
        print(f"Average Duration: {summary['avg_duration_ms']:.2f}ms")
        print(
            f"Range: {summary['min_duration_ms']:.2f}ms - "
            f"{summary['max_duration_ms']:.2f}ms"
        )
        print(f"Unique Templates: {summary['unique_templates']}")

        if summary["slowest_templates"]:
            print("\nSlowest Templates:")
            for template, duration in summary["slowest_templates"]:
                print(f"  {template}: {duration:.2f}ms")
        print()

    def clear_stats(self) -> None:
        """Clear collected performance statistics."""
        self._render_stats.clear()

    def _detect_missing_variables(self, error_message: str) -> List[str]:
        """
        Detect likely missing variables from error message.

        Args:
            error_message: The error message to analyze

        Returns:
            List of likely missing variable names
        """
        import re

        # Common patterns for missing variables in Jinja2 errors
        patterns = [
            r"'(\w+)' is undefined",
            r"(\w+) is not defined",
            r"has no attribute '(\w+)'",
        ]

        missing_vars = []
        for pattern in patterns:
            matches = re.findall(pattern, error_message)
            missing_vars.extend(matches)

        return list(set(missing_vars))  # Remove duplicates

    def _get_memory_usage(self) -> float:
        """
        Get current memory usage in bytes.

        Returns:
            Memory usage in bytes, or 0 if unavailable
        """
        try:
            # Try to import psutil for memory tracking (optional)
            import psutil  # type: ignore

            process = psutil.Process()
            return process.memory_info().rss
        except ImportError:
            # psutil not available, return 0
            return 0.0


class DiagnosticTemplateRenderer:
    """
    Template renderer wrapper with enhanced diagnostics.

    This wraps the existing template renderer to provide better error
    reporting and performance monitoring capabilities.
    """

    def __init__(self, base_renderer, diagnostics: TemplateDiagnostics):
        """
        Initialize diagnostic renderer.

        Args:
            base_renderer: The base template renderer to wrap
            diagnostics: Diagnostics configuration
        """
        self.base_renderer = base_renderer
        self.diagnostics = diagnostics

    def render_template(self, template_name: str, context: Dict[str, Any]) -> str:
        """
        Render template with enhanced diagnostics.

        Args:
            template_name: Name of template to render
            context: Template context variables

        Returns:
            Rendered template content

        Raises:
            Enhanced template rendering errors with diagnostic information
        """
        context_size = len(context) if context else 0

        with self.diagnostics.track_rendering(template_name, context_size):
            try:
                # Use the base renderer to do the actual work
                return self.base_renderer.render_template(template_name, context)
            except Exception as e:
                # Extract line number if available
                line_number = getattr(e, "lineno", None)

                # Create enhanced error message
                enhanced_msg = self.diagnostics.format_enhanced_error(
                    template_name, e, context, line_number
                )

                log_error_safe(
                    logger,
                    "Template rendering failed: {msg}",
                    msg=enhanced_msg if self.diagnostics.verbose_errors else str(e),
                )

                # Re-raise with enhanced message if in verbose mode
                if self.diagnostics.verbose_errors:
                    raise type(e)(enhanced_msg) from e
                else:
                    raise


# Global diagnostics instance - can be configured by applications
default_diagnostics = TemplateDiagnostics()


def enable_verbose_errors(enabled: bool = True) -> None:
    """Enable or disable verbose error reporting globally."""
    default_diagnostics.verbose_errors = enabled
    log_info_safe(
        logger,
        "Verbose template errors {status}",
        status="enabled" if enabled else "disabled",
        prefix="TEMPLATE_ERRORS",
    )


def enable_performance_tracking(enabled: bool = True) -> None:
    """Enable or disable performance tracking globally."""
    default_diagnostics.performance_tracking = enabled
    log_info_safe(
        logger,
        "Template performance tracking {status}",
        status="enabled" if enabled else "disabled",
        prefix="TEMPLATE_PERF",
    )


def get_diagnostics_instance() -> TemplateDiagnostics:
    """Get the global diagnostics instance."""
    return default_diagnostics


__all__ = [
    "TemplateDiagnostics",
    "DiagnosticTemplateRenderer",
    "enable_verbose_errors",
    "enable_performance_tracking",
    "get_diagnostics_instance",
]
