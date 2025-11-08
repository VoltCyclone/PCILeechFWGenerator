#!/usr/bin/env python3
"""
Jinja2-based template rendering system for PCILeech firmware generation.

This module provides a centralized template rendering system to replace
the string formatting and concatenation currently used in build.py.
"""

import logging
import math
import sys
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple, Union, cast

from src.__version__ import __version__
from src.exceptions import TemplateRenderError
from src.string_utils import (
    generate_tcl_header_comment,
    log_debug_safe,
    log_error_safe,
    log_info_safe,
    log_warning_safe,
    safe_format,
)
from src.templates.template_mapping import update_template_path
from src.utils.unified_context import ensure_template_compatibility

from .sv_constants import SV_CONSTANTS

try:
    from jinja2 import (
        Environment,
        FileSystemLoader,
        StrictUndefined,
        TemplateError,
        TemplateNotFound,
        TemplateRuntimeError,
        Undefined,
        nodes,
    )
    from jinja2.bccache import FileSystemBytecodeCache
    from jinja2.ext import Extension
    from jinja2.sandbox import SandboxedEnvironment
except ImportError:
    raise ImportError(
        "Jinja2 is required for template rendering. Install with: pip install jinja2"
    )


class MappingFileSystemLoader(FileSystemLoader):
    """
    Custom Jinja2 loader that applies template path mapping for both direct
    template loading and includes/extends.
    """

    def get_source(self, environment, template):
        """Override get_source to apply template path mapping."""
        # Apply template mapping
        mapped_template = update_template_path(template)
        return super().get_source(environment, mapped_template)


logger = logging.getLogger(__name__)


class ErrorTagExtension(Extension):
    """Custom Jinja2 extension to handle {% error %} tags for template validation."""

    tags = {"error"}

    def parse(self, parser):
        lineno = next(parser.stream).lineno
        # Parse the string expression after the tag
        args = [parser.parse_expression()]
        return nodes.CallBlock(
            self.call_method("_raise_error", args), [], [], []
        ).set_lineno(lineno)

    def _raise_error(self, message):
        """Raise a template runtime error with the given message."""
        raise TemplateRuntimeError(message)


class TemplateRenderer:
    """
    Jinja2-based template renderer for TCL scripts and other text files.

    This class provides a clean interface for rendering templates with
    proper error handling and context management.
    """

    def __init__(
        self,
        template_dir: Optional[Union[str, Path]] = None,
        *,
        strict: bool = True,
        sandboxed: bool = False,
        bytecode_cache_dir: Optional[Union[str, Path]] = None,
        auto_reload: bool = True,
        prefix: str = "TEMPL",
    ):
        """
        Initialize the template renderer.

        Args:
            template_dir: Directory containing template files. If None,
                         defaults to src/templates/
            strict: Use StrictUndefined to fail on missing variables
            sandboxed: Use sandboxed environment for untrusted templates
            bytecode_cache_dir: Directory for bytecode cache
                                 (speeds up repeated renders)
            auto_reload: Auto-reload templates when changed
        """
        template_dir = Path(template_dir or Path(__file__).parent.parent / "templates")
        template_dir.mkdir(parents=True, exist_ok=True)
        self.template_dir = template_dir
        self.prefix = prefix

        # Choose undefined class based on strict mode
        undefined_cls = StrictUndefined if strict else Undefined

        # Choose environment class based on sandboxed mode
        env_cls = SandboxedEnvironment if sandboxed else Environment

        # Setup bytecode cache if directory provided
        bcc = (
            FileSystemBytecodeCache(str(bytecode_cache_dir))
            if bytecode_cache_dir
            else None
        )

        self.env = env_cls(
            loader=MappingFileSystemLoader(str(self.template_dir)),
            undefined=undefined_cls,
            trim_blocks=False,
            lstrip_blocks=False,
            keep_trailing_newline=True,
            auto_reload=auto_reload,
            extensions=[ErrorTagExtension, "jinja2.ext.do"],
            bytecode_cache=bcc,
            autoescape=False,  # Explicit: we're not doing HTML
        )

        # Add custom filters if needed
        self._setup_custom_filters()

        # Add global functions
        self._setup_global_functions()

        log_debug_safe(
            logger,
            safe_format(
                "Template renderer initialized with directory: {template_dir}",
                template_dir=self.template_dir,
            ),
            prefix=prefix,
        )

    def _setup_custom_filters(self):
        """Setup custom Jinja2 filters for TCL and SystemVerilog generation."""

        def hex_format(value: int, width: int = 4) -> str:
            """Format integer as hex string with specified width."""
            return f"{value:0{width}x}"

        def tcl_string_escape(value: str) -> str:
            """Escape string for safe use in TCL."""
            # Enhanced TCL string escaping including brackets and braces
            return (
                value.replace("\\", "\\\\")
                .replace('"', '\\"')
                .replace("$", "\\$")
                .replace("[", "\\[")
                .replace("]", "\\]")
                .replace("{", "\\{")
                .replace("}", "\\}")
            )

        def tcl_list_format(items: list) -> str:
            """Format Python list as TCL list."""
            escaped_items = [tcl_string_escape(str(item)) for item in items]
            return " ".join(f"{{{item}}}" for item in escaped_items)

        # SystemVerilog-specific filters

        def _parse_int(value) -> int:
            """Parse integer from various formats.

            Supported inputs:
            - Python int
            - Decimal strings (e.g., "1234")
            - Hex strings with 0x prefix (e.g., "0xCAFEBABE")
            - Bare hex strings (e.g., "DEADBEEF")
            - SystemVerilog literals (e.g., "32'hDEAD_BEEF", "16'd255", "8'b1010_1100")
            """
            if isinstance(value, int):
                return value

            s = str(value).strip()

            # Try SystemVerilog literal first: <width>'<base><digits>
            # base: h/H (hex), d/D (decimal), b/B (binary), o/O (octal)
            # allow underscores in digits
            import re

            sv_match = re.match(
                r"^(?P<width>\d+)?'(?P<base>[hHbBdDoO])(?P<digits>[0-9a-fA-F_xXzZ]+)$",
                s,
            )
            if sv_match:
                base_char = sv_match.group("base").lower()
                digits = sv_match.group("digits").replace("_", "")
                # Treat x/z as 0 for integer conversion purposes
                digits = re.sub(r"[xXzZ]", "0", digits)
                if base_char == "h":
                    return int(digits, 16)
                if base_char == "d":
                    return int(digits, 10)
                if base_char == "b":
                    return int(digits, 2)
                if base_char == "o":
                    return int(digits, 8)

            try:
                # 0x-prefixed hex
                if s.lower().startswith("0x"):
                    return int(s, 16)

                # VID:DID[:RID] format (colon-separated hex tokens)
                # Example: "1912:0014" -> 0x19120014
                #          "8086:1234:15" -> 0x8086123415 (will be width-masked by sv_hex)
                if ":" in s and "." not in s:
                    parts = s.split(":")
                    # Ensure all parts are non-empty hex strings
                    hexset = set("0123456789abcdefABCDEF")
                    if all(p and all(c in hexset for c in p) for p in parts):
                        # Concatenate parts in order (VID, DID, [RID, ...])
                        joined = "".join(parts)
                        return int(joined, 16)

                # If (after removing underscores) the string contains only hex digits
                # and at least one hex letter (A-F), treat as hex. This avoids
                # misclassifying decimal-like values that use underscores as separators.
                s_no_underscore = s.replace("_", "")
                hex_digits_no_us = set("0123456789abcdefABCDEF")
                if (
                    s_no_underscore
                    and all(c in hex_digits_no_us for c in s_no_underscore)
                    and any(c.isalpha() for c in s_no_underscore)
                ):
                    return int(s_no_underscore, 16)

                # Fallback: decimal (underscores allowed in Python 3.10+)
                return int(s.replace("_", ""), 10)
            except Exception as e:
                raise TemplateRenderError(
                    safe_format(
                        "sv_hex: cannot parse int from {value!r}: {e}", value=value, e=e
                    )
                )

        def sv_hex(value, width: int = 32) -> str:
            """Return SystemVerilog literal.

            width<=0 returns just hex without width.
            """
            iv = _parse_int(value)
            # If a width is specified, mask the value to that width to avoid
            # emitting too many hex digits for the declared literal width.
            if width and width > 0:
                mask = (1 << width) - 1
                iv &= mask
            if width and width > 0:
                hex_digits = (width + 3) // 4
                return f"{width}'h{iv:0{hex_digits}X}"
            return f"{iv:#X}"

        def sv_width(msb: int, lsb: int = 0) -> str:
            """Generate SystemVerilog bit width specification."""
            if msb == lsb:
                return ""
            if msb < lsb:
                msb, lsb = lsb, msb
            return f"[{msb}:{lsb}]"

        def sv_param(name: str, value, width: Optional[int] = None) -> str:
            """Format SystemVerilog parameter declaration."""
            if width:
                return f"parameter {name} = {sv_hex(value, width)}"
            return f"parameter {name} = {value}"

        def sv_signal(name: str, width: Optional[int] = None, initial=None) -> str:
            """Format SystemVerilog signal declaration."""
            width_str = f"[{width-1}:0] " if width and width >= 1 else ""
            if initial is not None:
                if width:
                    init_str = f" = {sv_hex(initial, width)}"
                else:
                    init_str = f" = {initial}"
            else:
                init_str = ""
            return f"logic {width_str}{name}{init_str};"

        def sv_identifier(name: str) -> str:
            """Convert to valid SystemVerilog identifier."""
            import re

            s = re.sub(r"[^a-zA-Z0-9_]", "_", name)
            if not re.match(r"^[a-zA-Z_]", s):
                s = "_" + s
            if s in SV_CONSTANTS.SV_RESERVED_KEYWORDS:
                s = s + "_id"
            return s

        def sv_comment(text: str, style: str = "//") -> str:
            """Format SystemVerilog comment."""
            if style == "//":
                return f"// {text}"
            elif style == "/*":
                return f"/* {text} */"
            else:
                return f"// {text}"

        def sv_bool(value) -> str:
            """Convert Python boolean to SystemVerilog boolean."""
            if isinstance(value, bool):
                return "1" if value else "0"
            return str(value)

        def clog2(v) -> int:
            """Calculate ceiling of log2 for SystemVerilog bit width calculations."""
            n = int(v)
            return 0 if n <= 1 else int(math.ceil(math.log2(n)))

        def flog2(v) -> int:
            """Calculate floor of log2."""
            n = max(1, int(v))
            return int(math.log2(n))

        def python_list(value) -> str:
            """Format value as Python list literal."""
            if isinstance(value, list):
                # Format as Python list with integers/numbers as-is
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

        def python_repr(value) -> str:
            """Format value as Python representation."""
            return repr(value)

        def dataclass_to_dict(value):
            """Convert dataclass objects to dictionaries for template access."""
            if hasattr(value, "__dataclass_fields__"):
                from dataclasses import asdict

                return asdict(value)
            return value

        # Register filters
        self.env.filters["hex"] = hex_format
        self.env.filters["tcl_string_escape"] = tcl_string_escape
        self.env.filters["tcl_list_format"] = tcl_list_format

        # Python code generation filters
        self.env.filters["python_list"] = python_list
        self.env.filters["python_repr"] = python_repr

        # Math filters
        self.env.filters["clog2"] = clog2
        self.env.filters["log2"] = clog2  # Default to ceiling for compatibility
        self.env.filters["flog2"] = flog2

        # Utility filters
        self.env.filters["dataclass_to_dict"] = dataclass_to_dict

        # SystemVerilog filters
        self.env.filters["sv_hex"] = sv_hex
        self.env.filters["sv_width"] = sv_width
        self.env.filters["sv_param"] = sv_param
        self.env.filters["sv_signal"] = sv_signal
        self.env.filters["sv_identifier"] = sv_identifier
        self.env.filters["sv_comment"] = sv_comment
        self.env.filters["sv_bool"] = sv_bool

        def safe_int_filter(value, default=0):
            try:
                return _parse_int(value)
            except Exception:
                try:
                    return int(value)
                except Exception:
                    return default

        self.env.filters["safe_int"] = safe_int_filter

    def _setup_global_functions(self):
        """Setup global functions available in templates."""

        def throw_error(message):
            """Throw a template runtime error."""
            raise TemplateRuntimeError(message)

        self.env.globals.update(
            {
                "generate_tcl_header_comment": generate_tcl_header_comment,
                "throw_error": throw_error,
                # Python builtins
                "len": len,
                "range": range,
                "min": min,
                "max": max,
                "sorted": sorted,
                "zip": zip,
                "sum": sum,
                "int": int,
                "hex": hex,
                "hasattr": hasattr,
                "getattr": getattr,
                "isinstance": isinstance,
                # Version info
                "__version__": __version__,
            }
        )

    def render_template(self, template_name: str, context: Dict[str, Any]) -> str:
        """
        Render a template with the given context.

        Args:
            template_name: Name of the template file (with path mapping support)
            context: Dictionary of variables to pass to the template

        Returns:
            Rendered template as string

        Raises:
            TemplateRenderError: If template rendering fails
        """
        template_name = update_template_path(template_name)
        try:
            # Prepare the context for template compatibility
            try:
                compatible = ensure_template_compatibility(context)

                # Add template constants to the context
                try:
                    from src.templates.constants import get_template_constants

                    template_constants = get_template_constants()
                    for key, value in template_constants.items():
                        # Don't override existing context values
                        if key not in compatible:
                            compatible[key] = value
                except ImportError:
                    log_debug_safe(
                        logger,
                        "Template constants not available",
                        prefix=self.prefix,
                    )

            except Exception as e:
                # Fallback to the original context if conversion fails
                log_debug_safe(
                    logger,
                    safe_format(
                        "ensure_template_compatibility failed, using original context: {error}",
                        error=e,
                    ),
                    prefix=self.prefix,
                )
                compatible = context

            # Apply centralized context validation/injection in non-strict mode
            # to ensure optional defaults (e.g., constraint_files) are present
            # across all templates, without relaxing required keys.
            try:
                from src.templating.template_context_validator import (
                    validate_template_context,
                )

                compatible = validate_template_context(
                    template_name, compatible, strict=False
                )
            except Exception as e:
                # Validator is best-effort; proceed with current context on any issue
                log_debug_safe(
                    logger,
                    safe_format(
                        "TemplateContextValidator unavailable or failed: {error}",
                        error=e,
                    ),
                    prefix=self.prefix,
                )

            # Render the template with the validated/compatible context
            template = self._load_template(template_name)
            return template.render(**compatible)

        except TemplateError as e:
            error_msg = safe_format(
                "Failed to render template '{template_name}': {error}",
                template_name=template_name,
                error=e,
            )
            raise TemplateRenderError(error_msg) from e
        except Exception as e:
            error_msg = safe_format(
                "Unexpected error rendering template '{template_name}': {error}",
                template_name=template_name,
                error=e,
            )
            log_error_safe(logger, error_msg, prefix=self.prefix)
            raise TemplateRenderError(error_msg) from e

    def render_string(self, template_string: str, context: Dict[str, Any]) -> str:
        """
        Render a template from a string with the given context.

        Args:
            template_string: Template content as string
            context: Dictionary of variables to pass to the template

        Returns:
            Rendered template content as string

        Raises:
            TemplateRenderError: If template rendering fails
        """
        try:
            # Reuse the same validation path for consistency
            validated = self._validate_template_context(context, "<inline>")
            template = self.env.from_string(template_string)
            return template.render(**validated)

        except TemplateError as e:
            raise TemplateRenderError(
                safe_format("Failed to render string template: {error}", error=e)
            ) from e
        except Exception as e:
            raise TemplateRenderError(
                safe_format(
                    "Unexpected error rendering string template: {error}", error=e
                )
            ) from e

    def template_exists(self, template_name: str) -> bool:
        """
        Check if a template file exists.

        Args:
            template_name: Name of the template file

        Returns:
            True if template exists, False otherwise
        """
        try:
            self.env.get_template(update_template_path(template_name))
            return True
        except TemplateNotFound:
            return False

    def list_templates(self, pattern: str = "*.j2") -> list[str]:
        """
        List available template files.

        Args:
            pattern: Glob pattern to match template files

        Returns:
            List of template file names
        """
        templates = []
        for template_path in self.template_dir.rglob(pattern):
            # Get relative path from template directory
            rel_path = template_path.relative_to(self.template_dir)
            templates.append(str(rel_path))

        return sorted(templates)

    def get_template_path(self, template_name: str) -> Path:
        """
        Get the full path to a template file.

        Args:
            template_name: Name of the template file

        Returns:
            Full path to the template file
        """
        name = update_template_path(template_name)
        src, filename, _ = self.env.loader.get_source(self.env, name)
        return Path(filename)

    def _load_template(self, template_name: str):
        """Internal helper to load a template object.

        Exists primarily to make the loader patchable in unit tests.
        """
        try:
            return self.env.get_template(template_name)
        except Exception:
            # Re-raise to let callers convert to TemplateRenderError
            raise

    def render_to_file(
        self, template_name: str, context: Dict[str, Any], out_path: Union[str, Path]
    ) -> Path:
        """
        Render template to file atomically.

        Args:
            template_name: Name of the template file
            context: Template context variables
            out_path: Output file path

        Returns:
            Path to the written file
        """
        content = self.render_template(template_name, context)
        out_path = Path(out_path)
        tmp = out_path.with_suffix(out_path.suffix + ".tmp")
        tmp.write_text(content, encoding="utf-8")
        tmp.replace(out_path)
        return out_path

    def render_many(self, pairs: List[Tuple[str, Dict[str, Any]]]) -> Dict[str, str]:
        """
        Render multiple templates efficiently.

        Args:
            pairs: List of (template_name, context) tuples

        Returns:
            Dictionary mapping template names to rendered content
        """
        results = {}
        for template_name, context in pairs:
            results[template_name] = self.render_template(template_name, context)
        return results

    def _validate_template_context(
        self,
        context: Dict[str, Any],
        template_name: Optional[str] = None,
    _required_fields: Optional[list] = None,
    _optional_fields: Optional[list] = None,
    ) -> Dict[str, Any]:
        """
        Validate and prepare template context with permissive validation.

        This method applies basic validation and preparation while allowing
        missing variables to be handled by Jinja2's StrictUndefined during rendering.

        Args:
            context: Original template context
            template_name: Name of the template being rendered
            _required_fields: List of required fields (deprecated, unused)
            _optional_fields: List of optional fields (deprecated, unused)

        Returns:
            Validated and prepared context

        Raises:
            TemplateRenderError: Only for critical validation failures
        """
        if not context:
            log_warning_safe(
                logger,
                safe_format(
                    "Empty template context provided for template '{name}', using empty dict",
                    name=(template_name or "unknown"),
                ),
                prefix="TEMPLATE",
            )
            return {}

        try:
            # Try to use the centralized validator if available, but don't fail if it's not
            try:
                from src.templating.template_context_validator import (
                    validate_template_context,
                )

                # Apply centralized validation with non-strict mode
                validated_context = validate_template_context(
                    template_name or "unknown", context, strict=False
                )
            except ImportError:
                log_debug_safe(
                    logger,
                    "TemplateContextValidator not available, using basic preparation",
                    prefix="TEMPLATE",
                )
                validated_context = context.copy()
            except Exception as e:
                log_debug_safe(
                    logger,
                    safe_format(
                        "TemplateContextValidator failed, falling back to basic preparation: {error}",
                        error=e,
                    ),
                    prefix=self.prefix,
                )
                validated_context = context.copy()

            # Handle dataclass conversions for backward compatibility
            if "timing_config" in validated_context:
                timing_config = validated_context.get("timing_config")
                if timing_config and hasattr(timing_config, "__dataclass_fields__"):
                    try:
                        from dataclasses import asdict

                        validated_context["timing_config"] = asdict(timing_config)
                    except Exception as e:
                        log_debug_safe(
                            logger,
                            safe_format(
                                "Failed to convert timing_config dataclass: {error}",
                                error=e,
                            ),
                            prefix=self.prefix,
                        )

            return validated_context

        except Exception as e:
            # Only raise for truly critical errors, otherwise log and continue
            log_warning_safe(
                logger,
                safe_format(
                    "Template context validation warning for '{name}': {error}",
                    name=(template_name or "unknown"),
                    error=e,
                ),
                prefix=self.prefix,
            )
            return context.copy()

    def clear_cache(self) -> None:
        """Clear template cache and bytecode cache."""
        # Clear Jinja2 bytecode cache if available
        if hasattr(self.env, "cache") and self.env.cache:
            self.env.cache.clear()

        # Clear template context validator cache
        try:
            from src.templating.template_context_validator import (
                clear_global_template_cache,
            )

            clear_global_template_cache()
        except ImportError:
            pass

        log_debug_safe(
            logger,
            "Cleared template renderer caches",
            prefix=self.prefix,
        )


def render_tcl_template(
    template_name: str,
    context: Dict[str, Any],
    template_dir: Optional[Union[str, Path]] = None,
) -> str:
    """
    Convenience function to render a TCL template.

    Args:
        template_name: Name of the template file
        context: Template context variables
        template_dir: Template directory (optional)

    Returns:
        Rendered template content
    """
    renderer = TemplateRenderer(template_dir)
    return renderer.render_template(template_name, context)
