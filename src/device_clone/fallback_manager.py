#!/usr/bin/env python3
"""
Fallback Manager for Template Variables

This module provides a centralized fallback management system for
template variables, ensuring that all templates have access to required variables
without needing to define defaults in the templates themselves.

"""

import copy
import logging
import re
import threading
from dataclasses import dataclass, field
from enum import Enum
from pathlib import Path
from typing import (
    Any,
    Callable,
    Dict,
    Final,
    List,
    Optional,
    Protocol,
    Set,
    Tuple,
    TypeVar,
    Union,
)

from src.string_utils import (
    log_debug_safe,
    log_error_safe,
    log_info_safe,
    log_warning_safe,
    safe_format,
)

from ..utils.validation_constants import DEVICE_IDENTIFICATION_FIELDS, SENSITIVE_TOKENS

# Type variable for return type of handler functions
T = TypeVar("T")

logger = logging.getLogger(__name__)


class FallbackMode(Enum):
    """Policy modes for fallback confirmation behavior."""

    NONE = "none"  # Never allow fallbacks
    AUTO = "auto"  # Always allow fallbacks
    PROMPT = "prompt"  # Permissive in non-interactive contexts


class VariableType(Enum):
    """Types of variables for categorization."""

    CRITICAL = "critical"  # Must come from hardware, no fallbacks
    SENSITIVE = "sensitive"  # Contains sensitive tokens
    STANDARD = "standard"  # Normal variables with fallbacks
    DEFAULT = "default"  # Built-in system defaults


@dataclass
class FallbackConfig:
    """Configuration for fallback behavior."""

    mode: FallbackMode = FallbackMode.PROMPT
    allowed_fallbacks: Set[str] = field(default_factory=set)
    config_path: Optional[Path] = None

    def __post_init__(self):
        """Validate and normalize configuration."""
        if isinstance(self.mode, str):
            self.mode = FallbackMode(self.mode)
        if self.config_path and isinstance(self.config_path, str):
            self.config_path = Path(self.config_path)


@dataclass
class VariableMetadata:
    """Metadata for a registered variable."""

    name: str
    value: Any
    var_type: VariableType = VariableType.STANDARD
    is_dynamic: bool = False
    handler: Optional[Callable[[], Any]] = None
    description: Optional[str] = None


class FallbackHandler(Protocol):
    """Protocol for fallback value handlers."""

    def __call__(self) -> Any: ...


class FallbackManager:
    """
    Manages template variable fallbacks with security-first approach.

    Features:
        CRITICAL_VARS: Variables that must be hardware-derived
        SENSITIVE_TOKENS: Tokens that indicate sensitive variables
        DEFAULT_FALLBACKS: Safe fallback values for non-critical variables
    """

    DEFAULT_FALLBACKS: Final[Dict[str, Any]] = {
        "board.name": "",
        "board.fpga_part": "",
        "board.fpga_family": "7series",
        "board.pcie_ip_type": "pcie7x",
        "sys_clk_freq_mhz": 100,
        # PCIe clock parameters for Xilinx 7-series
        "pcie_refclk_freq": 0,      # 0=100MHz, 1=125MHz, 2=250MHz
        "pcie_userclk1_freq": 2,    # 1=31.25MHz, 2=62.5MHz, 3=125MHz, 4=250MHz, 5=500MHz
        "pcie_userclk2_freq": 2,    # Same encoding as userclk1
        "pcie_link_speed": 2,       # 1=Gen1, 2=Gen2, 3=Gen3
        "pcie_oobclk_mode": 1,      # OOB clock mode
        "generated_xdc_path": "",
        "board_xdc_content": "",
        "max_lanes": 1,
        "supports_msi": True,
        "supports_msix": False,
        # Control whether PM sideband interface signals are exposed. Safe
        # default is False (feature disabled) so templates gate logic cleanly.
        "expose_pm_sideband": False,
        "ROM_BAR_INDEX": 0,
        "ROM_HEX_FILE": "",
        "ROM_SIZE": 0,
        "CACHE_SIZE": 0,
        "CONFIG_SHDW_HI": 0,
        "CONFIG_SPACE_SIZE": 0,
        "CUSTOM_WIN_BASE": 0,
        "kwargs": {},
        "meta": {},
        "opt_directive": "",
        "phys_opt_directive": "",
        "place_directive": "",
        "route_directive": "",
        "pcie_clk_n_pin": "",
        "pcie_clk_p_pin": "",
        "pcie_rst_pin": "",
        "pcie_config": {},
        "process_var": "",
        "reg_value": 0,
        "temp_coeff": 0.0,
        "title": "",
        "transition_delays": [],
        "varied_value": 0,
        "voltage_var": 0.0,
        "_td": 0,
        "from_state_value": 0,
        "to_state_value": 0,
        "error_name": "",
        "error_value": 0,
        # Template include list for SystemVerilog header template
        "header_includes": [],
        # Architecture/feature toggles for SV templates (non-device-unique)
        "ENABLE_BIT_TYPES": 1,
        "ENABLE_SPARSE_MAP": 1,
        "HASH_TABLE_SIZE": 256,
        # Power management CFG glue emission toggle
        "pmcsr_cfg_glue": False,
    }

    # Regex patterns for template variable detection
    JINJA_VAR_PATTERN: Final[re.Pattern] = re.compile(
        r"{{\s*([a-zA-Z0-9_.]+)|{%\s*if\s+([a-zA-Z0-9_.]+)"
    )

    def __init__(
        self,
        config_path: Optional[Union[str, FallbackConfig]] = None,
        mode: str = "prompt",
        allowed_fallbacks: Optional[List[str]] = None,
    ):
        """
        Initialize the fallback manager.

        Args:
            config_path: Path to YAML config file OR FallbackConfig object for new API
            mode: Policy mode controlling fallback confirmation behavior
            allowed_fallbacks: Optional whitelist of fallback keys
        """
        # Support both old and new initialization styles
        if isinstance(config_path, FallbackConfig):
            self.config = config_path
        else:
            # Legacy API: individual parameters
            self.config = FallbackConfig(
                mode=FallbackMode(mode) if isinstance(mode, str) else mode,
                allowed_fallbacks=set(allowed_fallbacks or []),
                config_path=Path(config_path) if config_path else None,
            )

        # Expose legacy attributes for backward compatibility
        self.mode = self.config.mode.value
        self.allowed_fallbacks = self.config.allowed_fallbacks

        # Internal storage
        self._variables: Dict[str, VariableMetadata] = {}
        self._critical_vars: Set[str] = set()
        self._default_registered_keys: Set[str] = set()
        self._fallbacks: Dict[str, Any] = {}
        self._default_handlers: Dict[str, Callable[[], Any]] = {}
        self._path_cache: Dict[str, List[str]] = {}
        self._register_default_fallbacks()
        if self.config.config_path:
            self.load_from_config(str(self.config.config_path))

    def confirm_fallback(
        self, key: str, reason: str, details: Optional[str] = None
    ) -> bool:
        """
        Policy decision helper for fallback permission.

        Args:
            key: Identifier for the fallback being requested
            reason: Short explanation why the fallback would be used
            details: Optional longer description

        Returns:
            True if the fallback is permitted, False otherwise
        """
        # Check whitelist first
        if self.config.allowed_fallbacks and key not in self.config.allowed_fallbacks:
            log_warning_safe(
                logger, "Fallback {key} not in whitelist", prefix="FALLBACK", key=key
            )
            return False

        if self.config.mode == FallbackMode.NONE:
            log_warning_safe(
                logger,
                "Fallback denied by policy (mode=none) for {key}: {reason}",
                prefix="FALLBACK",
                key=key,
                reason=reason,
            )
            return False

        if self.config.mode == FallbackMode.AUTO:
            log_info_safe(
                logger,
                "Fallback auto-approved for {key}",
                prefix="FALLBACK",
                key=key,
            )
            return True

        log_info_safe(
            logger,
            "Fallback permitted (mode=prompt) for {key}: {reason}",
            prefix="FALLBACK",
            key=key,
            reason=reason,
        )
        return True

    def _register_default_fallbacks(self) -> None:
        """Register default fallbacks for common variables."""
        for key, value in self.DEFAULT_FALLBACKS.items():
            metadata = VariableMetadata(
                name=key,
                value=value,
                var_type=VariableType.DEFAULT,
                description=f"Default fallback for {key}",
            )
            self._variables[key] = metadata
            self._default_registered_keys.add(key)

            log_debug_safe(
                logger,
                safe_format(
                    "Registered default fallback for {var_name}",
                    var_name=key,
                ),
                prefix="FALLBACK",
            )

        self._register_default_critical_variables()

    def _register_default_critical_variables(self) -> None:
        """Register default critical variables that should never have fallbacks."""
        critical_vars: List[str] = []

        for field in DEVICE_IDENTIFICATION_FIELDS:
            critical_vars.append(f"device.{field}")
            critical_vars.append(field)

        for token in SENSITIVE_TOKENS:
            if token == "bars":
                critical_vars.extend(["bars", "device.bars"])
            else:
                if token not in DEVICE_IDENTIFICATION_FIELDS:
                    critical_vars.append(f"device.{token}")
                    critical_vars.append(token)

        seen: Set[str] = set()
        deduped: List[str] = []
        for v in critical_vars:
            if v not in seen:
                seen.add(v)
                deduped.append(v)

        self.mark_as_critical(deduped)

    def _split_path(self, path: str) -> List[str]:
        """
        Split a dot-notation path into parts with caching.

        Args:
            path: Dot-notation path like "device.config.id"

        Returns:
            List of path components
        """
        if path not in self._path_cache:
            self._path_cache[path] = path.split(".")
        return self._path_cache[path]

    def _navigate_nested_dict(
        self,
        context: Dict[str, Any],
        path_parts: List[str],
        create_missing: bool = False,
    ) -> Tuple[Optional[Dict[str, Any]], Optional[str], bool]:
        """
        Navigate to a nested dictionary path.

        Args:
            context: The dictionary to navigate
            path_parts: List of parts in the path
            create_missing: Whether to create missing intermediate dicts

        Returns:
            Tuple of (parent_dict, final_key, success)
        """
        if not path_parts:
            return context, "", True

        current = context

        # Navigate to parent of final key
        for part in path_parts[:-1]:
            # Missing intermediate part
            if part not in current:
                if create_missing:
                    try:
                        from src.utils.unified_context import TemplateObject

                        current[part] = TemplateObject({})
                    except Exception:
                        current[part] = {}
                else:
                    return None, None, False

            next_obj = current[part]

            # Accept plain dicts
            if isinstance(next_obj, dict):
                current = next_obj
                continue

            # Accept TemplateObject-like objects (duck-typed) that expose .get
            # and __setitem__/__getitem__ so we can set attributes on them.
            if hasattr(next_obj, "get") and callable(getattr(next_obj, "get")):
                current = next_obj
                continue

            # Anything else is not navigable
            return None, None, False

        return current, path_parts[-1], True

    def register_fallback(
        self, var_name: str, value: Any, description: Optional[str] = None
    ) -> bool:
        """
        Register a fallback value for a variable.

        Args:
            var_name: The variable name (can use dot notation)
            value: The fallback value
            description: Optional description of the variable

        Returns:
            True if registered successfully, False otherwise
        """
        if not self._validate_variable_name(var_name):
            return False

        if var_name in self._critical_vars:
            log_warning_safe(
                logger,
                "Cannot register fallback for critical variable: {var_name}",
                prefix="FALLBACK",
                var_name=var_name,
            )
            return False

        # Idempotency: if the same static value is already registered, do nothing
        existing = self._variables.get(var_name)
        if existing and not existing.is_dynamic and existing.value == value:
            # No change required
            return True

        metadata = VariableMetadata(
            name=var_name,
            value=value,
            var_type=self._determine_variable_type(var_name),
            description=description,
        )
        self._variables[var_name] = metadata

        log_info_safe(
            logger,
            "Registered fallback for {var_name} = {value}",
            prefix="FALLBACK",
            var_name=var_name,
            value=value,
        )
        return True

    def register_handler(
        self, var_name: str, handler: FallbackHandler, description: Optional[str] = None
    ) -> bool:
        """
        Register a dynamic handler for a variable.

        Args:
            var_name: The variable name (can use dot notation)
            handler: A callable that returns the fallback value
            description: Optional description

        Returns:
            True if registered successfully, False otherwise
        """
        if not self._validate_variable_name(var_name):
            return False

        if var_name in self._critical_vars:
            log_warning_safe(
                logger,
                "Cannot register handler for critical variable: {var_name}",
                prefix="FALLBACK",
                var_name=var_name,
            )
            return False

        if not callable(handler):
            log_error_safe(
                logger,
                "Handler for {var_name} is not callable",
                prefix="FALLBACK",
                var_name=var_name,
            )
            return False
        # Idempotency: if same dynamic handler already registered, do nothing
        existing = self._variables.get(var_name)
        if existing and existing.is_dynamic and existing.handler == handler:
            return True

        metadata = VariableMetadata(
            name=var_name,
            value=None,
            var_type=self._determine_variable_type(var_name),
            is_dynamic=True,
            handler=handler,
            description=description,
        )
        self._variables[var_name] = metadata

        log_info_safe(
            logger,
            "Registered dynamic handler for {var_name}",
            prefix="FALLBACK",
            var_name=var_name,
        )
        return True

    def mark_as_critical(self, var_names: List[str]) -> None:
        """
        Mark variables as critical, preventing fallbacks.

        Args:
            var_names: List of variable names to mark as critical
        """
        for var_name in var_names:
            if not self._validate_variable_name(var_name):
                continue

            self._critical_vars.add(var_name)

            # Remove any existing registrations
            if var_name in self._variables:
                del self._variables[var_name]
            # Legacy compatibility: also remove from old dicts
            if var_name in self._fallbacks:
                del self._fallbacks[var_name]
            if var_name in self._default_handlers:
                del self._default_handlers[var_name]
            # Legacy compatibility
            if var_name in self._fallbacks:
                del self._fallbacks[var_name]
            if var_name in self._default_handlers:
                del self._default_handlers[var_name]

        log_info_safe(
            logger,
            "Marked {count} variables as critical (no fallbacks)",
            prefix="FALLBACK",
            count=len(var_names),
        )

    def get_fallback(self, var_name: str) -> Any:
        """
        Get the fallback value for a variable.

        Args:
            var_name: The variable name

        Returns:
            The fallback value or None if not found

        Raises:
            ValueError: If the variable is critical
            RuntimeError: If a dynamic handler fails
        """
        if var_name in self._critical_vars:
            raise ValueError(f"Cannot get fallback for critical variable: {var_name}")

        if var_name not in self._variables:
            return None

        metadata = self._variables[var_name]

        if metadata.is_dynamic and metadata.handler:
            try:
                return metadata.handler()
            except Exception as e:
                log_error_safe(
                    logger,
                    "Handler for {var_name} raised an exception: {error}",
                    prefix="FALLBACK",
                    var_name=var_name,
                    error=str(e),
                )
                raise RuntimeError(f"Handler failed for {var_name}") from e

        return metadata.value

    def apply_fallbacks(self, template_context: Optional[Any] = None) -> Dict[str, Any]:
        """
        Apply all registered fallbacks to a template context.

        Args:
            template_context: The original template context

        Returns:
            Updated template context with fallbacks applied
        """
        # Prepare a working dict. If a TemplateObject-like context is provided
        # (it exposes `to_dict()`), convert it to a plain dict first to avoid
        # deep-copy recursion issues. Remember the original shape so we can
        # convert back to template-compatible objects afterward.
        original_was_template_object = False
        working_ctx: Any = template_context

        # Detect TemplateObject-like API and try to convert to plain dict
        try:
            if (
                template_context is not None
                and hasattr(template_context, "to_dict")
                and callable(getattr(template_context, "to_dict"))
            ):
                original_was_template_object = True
                try:
                    working_ctx = template_context.to_dict()
                except Exception:
                    # Fall back to using the original object if conversion fails
                    working_ctx = template_context
        except Exception:
            working_ctx = template_context

        # Deep copy to avoid modifying original
        context = copy.deepcopy(working_ctx) if working_ctx else {}

        # Apply all registered variables
        for var_name, metadata in self._variables.items():
            if var_name in self._critical_vars:
                continue

            self._apply_single_fallback(context, metadata)

        # If the original context was template-compatible, convert back so
        # consumers still receive TemplateObjects rather than plain dicts.
        if original_was_template_object:
            try:
                from src.utils.unified_context import ensure_template_compatibility

                return ensure_template_compatibility(context)
            except Exception:
                # If conversion fails, return the plain dict
                return context

        return context

    def _apply_single_fallback(
        self, context: Dict[str, Any], metadata: VariableMetadata
    ) -> bool:
        """
        Apply a single fallback value to the context.

        Args:
            context: The template context to update
            metadata: Variable metadata

        Returns:
            True if the fallback was applied, False otherwise
        """
        var_name = metadata.name

        # Get the value (from handler or static)
        if metadata.is_dynamic and metadata.handler:
            try:
                value = metadata.handler()
            except Exception as e:
                log_warning_safe(
                    logger,
                    safe_format(
                        "Handler for {var_name} failed: {error}",
                        var_name=var_name,
                        error=str(e),
                    ),
                    prefix="FALLBACK",
                )
                return False
        else:
            value = metadata.value

        # Apply to context
        if "." in var_name:
            parts = self._split_path(var_name)
            parent, key, success = self._navigate_nested_dict(
                context, parts, create_missing=True
            )

            if success and parent is not None and key:
                if key not in parent:
                    parent[key] = value
                    self._log_fallback_applied(var_name, metadata.is_dynamic)
                    return True
        else:
            if var_name not in context:
                context[var_name] = value
                self._log_fallback_applied(var_name, metadata.is_dynamic)
                return True

        return False

    def _log_fallback_applied(self, var_name: str, is_dynamic: bool) -> None:
        """Log that a fallback was applied."""
        if is_dynamic:
            log_debug_safe(
                logger,
                safe_format(
                    "Applied dynamic fallback for {var_name}",
                    var_name=var_name,
                ),
                prefix="FALLBACK",
            )
        else:
            log_debug_safe(
                logger,
                safe_format("Applied fallback for {var_name}", var_name=var_name),
                prefix="FALLBACK",
            )

    def validate_critical_variables(
        self, template_context: Dict[str, Any]
    ) -> Tuple[bool, List[str]]:
        """
        Validate that all critical variables are present.

        Args:
            template_context: The template context to validate

        Returns:
            Tuple of (is_valid, missing_variables)
        """
        missing = []

        for var_name in self._critical_vars:
            exists, _ = self._check_var_exists(template_context, var_name)
            if not exists:
                missing.append(var_name)

        if missing:
            log_error_safe(
                logger,
                safe_format(
                    "Missing critical variables: {missing}",
                    missing=", ".join(missing),
                ),
                prefix="FALLBACK",
            )
            return False, missing

        return True, []

    def _check_var_exists(
        self, template_context: Dict[str, Any], var_name: str
    ) -> Tuple[bool, Any]:
        """
        Check if a variable exists and has a non-empty value.

        Args:
            template_context: The template context to check
            var_name: The variable name (can use dot notation)

        Returns:
            Tuple of (exists, value)
        """
        if "." in var_name:
            parts = self._split_path(var_name)
            parent, key, success = self._navigate_nested_dict(
                template_context, parts, create_missing=False
            )

            if not success or parent is None or key not in parent:
                return False, None

            value = parent[key]
        else:
            if var_name not in template_context:
                return False, None
            value = template_context[var_name]

        # Check for empty containers
        if value is None:
            return False, None
        if isinstance(value, (list, dict, str)) and len(value) == 0:
            return False, None

        return True, value

    def get_exposable_fallbacks(self) -> Dict[str, Any]:
        """
        Get fallbacks that are safe to expose to users.

        Returns:
            Dictionary of variable names and values that are safe to expose
        """
        exposable = {}

        for var_name, metadata in self._variables.items():
            # Skip critical and sensitive variables
            if var_name in self._critical_vars:
                continue
            if self.is_sensitive_var(var_name):
                continue

            # Skip dynamic handlers (can't serialize)
            if metadata.is_dynamic:
                continue

            # Present defaults as blanks for user input
            if var_name in self._default_registered_keys:
                exposable[var_name] = ""
            else:
                exposable[var_name] = metadata.value

        return exposable

    def is_sensitive_var(self, name: str) -> bool:
        """
        Check if a variable name contains sensitive tokens.

        Args:
            name: The variable name to check

        Returns:
            True if the name contains a sensitive token
        """
        if not isinstance(name, str):
            return False

        name_lower = name.lower()
        return any(token in name_lower for token in SENSITIVE_TOKENS)

    def _determine_variable_type(self, var_name: str) -> VariableType:
        """
        Determine the type of a variable based on its name.

        Args:
            var_name: The variable name

        Returns:
            The determined variable type
        """
        if var_name in self._critical_vars:
            return VariableType.CRITICAL
        if self.is_sensitive_var(var_name):
            return VariableType.SENSITIVE
        if var_name in self._default_registered_keys:
            return VariableType.DEFAULT
        return VariableType.STANDARD

    def _validate_variable_name(self, var_name: str) -> bool:
        """
        Validate that a variable name is properly formatted.

        Args:
            var_name: The variable name to validate

        Returns:
            True if valid, False otherwise
        """
        if not var_name or not isinstance(var_name, str):
            log_error_safe(
                logger,
                "Invalid variable name: must be non-empty string",
                prefix="FALLBACK",
            )
            return False

        # Check for valid characters
        if not re.match(r"^[a-zA-Z_][a-zA-Z0-9_.]*$", var_name):
            log_error_safe(
                logger,
                safe_format(
                    "Invalid variable name format: {var_name}", var_name=var_name
                ),
                prefix="FALLBACK",
            )
            return False

        return True

    def load_from_config(self, config_path: str) -> bool:
        """
        Load fallback configurations from a YAML file.

        Args:
            config_path: Path to the YAML configuration file

        Returns:
            True if configuration was loaded successfully
        """
        try:
            import yaml

            config_file = Path(config_path)
            if not config_file.exists():
                log_error_safe(
                    logger,
                    safe_format(
                        "Configuration file not found: {path}",
                        path=config_path,
                    ),
                    prefix="FALLBACK",
                )
                return False

            with open(config_file, "r") as f:
                config = yaml.safe_load(f)

            if not config:
                log_warning_safe(
                    logger,
                    safe_format(
                        "Empty fallback configuration in {path}",
                        path=config_path,
                    ),
                    prefix="FALLBACK",
                )
                return False

            # Process critical variables first
            if "critical_variables" in config:
                self.mark_as_critical(config["critical_variables"])
                log_info_safe(
                    logger,
                    safe_format(
                        "Loaded {count} critical variables from {path}",
                        count=len(config["critical_variables"]),
                        path=config_path,
                    ),
                    prefix="FALLBACK",
                )

            # Then register fallbacks
            if "fallbacks" in config:
                for var_name, value in config["fallbacks"].items():
                    self.register_fallback(var_name, value)

                log_info_safe(
                    logger,
                    safe_format(
                        "Loaded {count} fallbacks from {path}",
                        count=len(config["fallbacks"]),
                        path=config_path,
                    ),
                    prefix="FALLBACK",
                )

            return True

        except yaml.YAMLError as e:
            log_error_safe(
                logger,
                "YAML parsing error in {path}: {error}",
                prefix="FALLBACK",
                path=config_path,
                error=str(e),
            )
            return False
        except Exception as e:
            log_error_safe(
                logger,
                "Error loading fallback configuration: {error}",
                prefix="FALLBACK",
                error=str(e),
            )
            return False

    def scan_template_variables(
        self, template_dir: str, pattern: str = "*.j2"
    ) -> Set[str]:
        """
        Scan templates to discover used variables.

        Args:
            template_dir: Directory containing template files
            pattern: Glob pattern for matching template files

        Returns:
            Set of discovered variable names
        """
        discovered_vars = set()
        template_path = Path(template_dir)

        if not template_path.exists():
            log_error_safe(
                logger,
                "Template directory not found: {dir}",
                prefix="FALLBACK",
                dir=template_dir,
            )
            return discovered_vars

        # Find all template files
        template_files = list(template_path.rglob(pattern))

        log_info_safe(
            logger,
            "Scanning {count} template files in {dir}",
            prefix="FALLBACK",
            count=len(template_files),
            dir=template_dir,
        )

        for file_path in template_files:
            try:
                content = file_path.read_text()

                # Extract variable names
                for match in self.JINJA_VAR_PATTERN.finditer(content):
                    var_name = match.group(1) or match.group(2)
                    if var_name:
                        discovered_vars.add(var_name)

            except Exception as e:
                log_warning_safe(
                    logger,
                    "Error scanning template {path}: {error}",
                    prefix="FALLBACK",
                    path=file_path,
                    error=str(e),
                )

        return discovered_vars

    def validate_templates_for_critical_vars(
        self, template_dir: str, pattern: str = "*.j2"
    ) -> bool:
        """
        Validate templates don't use critical variables directly.

        This method maintains backward compatibility by returning just a bool.
        Use validate_templates_with_details() for the full tuple return.

        Args:
            template_dir: Directory containing template files
            pattern: Glob pattern for matching template files

        Returns:
            True if no critical variables are found in templates, False otherwise
        """
        is_valid, _ = self.validate_templates_with_details(template_dir, pattern)
        return is_valid

    def validate_templates_with_details(
        self, template_dir: str, pattern: str = "*.j2"
    ) -> Tuple[bool, Set[str]]:
        """
        Validate templates don't use critical variables directly with details.

        Args:
            template_dir: Directory containing template files
            pattern: Glob pattern for matching template files

        Returns:
            Tuple of (is_valid, critical_vars_found)
        """
        all_template_vars = self.scan_template_variables(template_dir, pattern)
        critical_vars_in_templates = set()

        for var in all_template_vars:
            # Check direct critical variable usage
            if var in self._critical_vars:
                critical_vars_in_templates.add(var)
                continue

            # Check if any parent path is critical
            if "." in var:
                parts = self._split_path(var)
                for i in range(1, len(parts)):
                    parent_path = ".".join(parts[: i + 1])
                    if parent_path in self._critical_vars:
                        critical_vars_in_templates.add(var)
                        break

        if critical_vars_in_templates:
            log_error_safe(
                logger,
                "Critical variables found in templates: {vars}",
                prefix="FALLBACK",
                vars=", ".join(sorted(critical_vars_in_templates)),
            )
            return False, critical_vars_in_templates

        log_info_safe(
            logger,
            "No critical variables found in templates - validation passed",
            prefix="FALLBACK",
        )
        return True, set()

    def get_statistics(self) -> Dict[str, Any]:
        """
        Get statistics about registered fallbacks.

        Returns:
            Dictionary with statistics about the fallback manager
        """
        stats = {
            "total_variables": len(self._variables),
            "critical_variables": len(self._critical_vars),
            "default_variables": len(self._default_registered_keys),
            "dynamic_handlers": sum(
                1 for v in self._variables.values() if v.is_dynamic
            ),
            "by_type": {},
        }

        # Count by type
        for var_type in VariableType:
            count = sum(1 for v in self._variables.values() if v.var_type == var_type)
            stats["by_type"][var_type.value] = count

        return stats

    def export_config(self, output_path: str) -> bool:
        """
        Export current configuration to a YAML file.

        Args:
            output_path: Path to write the configuration

        Returns:
            True if exported successfully
        """
        try:
            import yaml

            config = {
                "critical_variables": sorted(self._critical_vars),
                "fallbacks": {},
            }

            # Export non-dynamic, non-critical variables
            for var_name, metadata in self._variables.items():
                if var_name in self._critical_vars:
                    continue
                if metadata.is_dynamic:
                    continue
                if self.is_sensitive_var(var_name):
                    continue

                config["fallbacks"][var_name] = metadata.value

            output_file = Path(output_path)
            output_file.parent.mkdir(parents=True, exist_ok=True)

            with open(output_file, "w") as f:
                yaml.dump(config, f, default_flow_style=False, sort_keys=True)

            log_info_safe(
                logger,
                "Exported configuration to {path}",
                prefix="FALLBACK",
                path=output_path,
            )
            return True

        except Exception as e:
            log_error_safe(
                logger,
                "Failed to export configuration: {error}",
                prefix="FALLBACK",
                error=str(e),
            )
            return False

    def clear(self) -> None:
        """Clear all registered fallbacks and reset to defaults."""
        self._variables.clear()
        self._critical_vars.clear()
        self._default_registered_keys.clear()
        self._path_cache.clear()

        # Legacy compatibility
        self._fallbacks.clear()
        self._default_handlers.clear()

        # Re-register defaults
        self._register_default_fallbacks()

        log_info_safe(
            logger, "Cleared all fallbacks and reset to defaults", prefix="FALLBACK"
        )


# Global singleton accessor for sharing a single FallbackManager across the app
_GLOBAL_FALLBACK_MANAGER: Optional["FallbackManager"] = None
_FALLBACK_MANAGER_LOCK = threading.Lock()


def get_global_fallback_manager(
    config_path: Optional[Union[str, FallbackConfig]] = None,
    mode: str = "prompt",
    allowed_fallbacks: Optional[List[str]] = None,
) -> "FallbackManager":
    """Return a lazily-created global FallbackManager instance.

    If a global manager already exists, the existing instance is returned.
    The first call may pass initialization parameters which will be used to
    construct the singleton.

    This function is thread-safe using double-checked locking pattern.
    """
    global _GLOBAL_FALLBACK_MANAGER

    # First check without lock (fast path for already initialized case)
    if _GLOBAL_FALLBACK_MANAGER is not None:
        return _GLOBAL_FALLBACK_MANAGER

    # Acquire lock for initialization
    with _FALLBACK_MANAGER_LOCK:
        # Double-check after acquiring lock in case another thread initialized it
        if _GLOBAL_FALLBACK_MANAGER is None:
            _GLOBAL_FALLBACK_MANAGER = FallbackManager(
                config_path=config_path,
                mode=mode,
                allowed_fallbacks=allowed_fallbacks
            )

    return _GLOBAL_FALLBACK_MANAGER


def set_global_fallback_manager(manager: Optional["FallbackManager"]) -> None:
    """Set or clear the global fallback manager (useful for tests).

    This function is thread-safe.
    """
    global _GLOBAL_FALLBACK_MANAGER
    with _FALLBACK_MANAGER_LOCK:
        _GLOBAL_FALLBACK_MANAGER = manager
