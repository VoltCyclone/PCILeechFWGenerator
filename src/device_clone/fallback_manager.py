#!/usr/bin/env python3
"""Centralized fallback management for template variables."""

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

from pcileechfwgenerator.string_utils import (
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
    NONE = "none"
    AUTO = "auto"
    PROMPT = "prompt"


class VariableType(Enum):
    CRITICAL = "critical"
    SENSITIVE = "sensitive"
    STANDARD = "standard"
    DEFAULT = "default"


@dataclass
class FallbackConfig:
    mode: FallbackMode = FallbackMode.PROMPT
    allowed_fallbacks: Set[str] = field(default_factory=set)
    config_path: Optional[Path] = None

    def __post_init__(self):
        if isinstance(self.mode, str):
            self.mode = FallbackMode(self.mode)
        if self.config_path and isinstance(self.config_path, str):
            self.config_path = Path(self.config_path)


@dataclass
class VariableMetadata:
    name: str
    value: Any
    var_type: VariableType = VariableType.STANDARD
    is_dynamic: bool = False
    handler: Optional[Callable[[], Any]] = None
    description: Optional[str] = None


class FallbackHandler(Protocol):
    def __call__(self) -> Any: ...


class FallbackManager:
    """Manages template variable fallbacks with hardware-critical variable protection."""

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
        if isinstance(config_path, FallbackConfig):
            self.config = config_path
        else:
            # Legacy API: individual parameters
            self.config = FallbackConfig(
                mode=FallbackMode(mode) if isinstance(mode, str) else mode,
                allowed_fallbacks=set(allowed_fallbacks or []),
                config_path=Path(config_path) if config_path else None,
            )

        self.mode = self.config.mode.value
        self.allowed_fallbacks = self.config.allowed_fallbacks

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
        if path not in self._path_cache:
            self._path_cache[path] = path.split(".")
        return self._path_cache[path]

    def _navigate_nested_dict(
        self,
        context: Dict[str, Any],
        path_parts: List[str],
        create_missing: bool = False,
    ) -> Tuple[Optional[Dict[str, Any]], Optional[str], bool]:
        if not path_parts:
            return context, "", True

        current = context
        for part in path_parts[:-1]:
            if part not in current:
                if create_missing:
                    try:
                        from pcileechfwgenerator.utils.unified_context import (
                            TemplateObject,
                        )

                        current[part] = TemplateObject({})
                    except Exception:
                        current[part] = {}
                else:
                    return None, None, False

            next_obj = current[part]

            if isinstance(next_obj, dict):
                current = next_obj
                continue

            if hasattr(next_obj, "get") and callable(getattr(next_obj, "get")):
                current = next_obj
                continue

            return None, None, False

        return current, path_parts[-1], True

    def register_fallback(
        self, var_name: str, value: Any, description: Optional[str] = None
    ) -> bool:
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
        for var_name in var_names:
            if not self._validate_variable_name(var_name):
                continue

            self._critical_vars.add(var_name)

            if var_name in self._variables:
                del self._variables[var_name]
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
        """Get the fallback value for a variable, or None if not found."""
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
        """Apply all registered fallbacks to a template context."""
        # Convert TemplateObject to plain dict to avoid deep-copy recursion,
        # then convert back afterward.
        original_was_template_object = False
        working_ctx: Any = template_context

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

        context = copy.deepcopy(working_ctx) if working_ctx else {}

        for var_name, metadata in self._variables.items():
            if var_name in self._critical_vars:
                continue

            self._apply_single_fallback(context, metadata)

        if original_was_template_object:
            try:
                from pcileechfwgenerator.utils.unified_context import (
                    ensure_template_compatibility,
                )

                return ensure_template_compatibility(context)
            except Exception:
                return context

        return context

    def _apply_single_fallback(
        self, context: Dict[str, Any], metadata: VariableMetadata
    ) -> bool:
        var_name = metadata.name

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
        """Return (is_valid, missing_variables) for all critical vars."""
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

        if value is None:
            return False, None
        if isinstance(value, (list, dict, str)) and len(value) == 0:
            return False, None

        return True, value

    def get_exposable_fallbacks(self) -> Dict[str, Any]:
        """Get fallbacks that are safe to expose to users."""
        exposable = {}

        for var_name, metadata in self._variables.items():
            if var_name in self._critical_vars:
                continue
            if self.is_sensitive_var(var_name):
                continue
            if metadata.is_dynamic:
                continue

            if var_name in self._default_registered_keys:
                exposable[var_name] = ""
            else:
                exposable[var_name] = metadata.value

        return exposable

    def is_sensitive_var(self, name: str) -> bool:
        if not isinstance(name, str):
            return False

        name_lower = name.lower()
        return any(token in name_lower for token in SENSITIVE_TOKENS)

    def _determine_variable_type(self, var_name: str) -> VariableType:
        if var_name in self._critical_vars:
            return VariableType.CRITICAL
        if self.is_sensitive_var(var_name):
            return VariableType.SENSITIVE
        if var_name in self._default_registered_keys:
            return VariableType.DEFAULT
        return VariableType.STANDARD

    def _validate_variable_name(self, var_name: str) -> bool:
        if not var_name or not isinstance(var_name, str):
            log_error_safe(
                logger,
                "Invalid variable name: must be non-empty string",
                prefix="FALLBACK",
            )
            return False

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
        """Scan Jinja2 templates to discover used variable names."""
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
        """Bool-only wrapper around validate_templates_with_details()."""
        is_valid, _ = self.validate_templates_with_details(template_dir, pattern)
        return is_valid

    def validate_templates_with_details(
        self, template_dir: str, pattern: str = "*.j2"
    ) -> Tuple[bool, Set[str]]:
        """Return (is_valid, critical_vars_found) for templates in a directory."""
        all_template_vars = self.scan_template_variables(template_dir, pattern)
        critical_vars_in_templates = set()

        for var in all_template_vars:
            if var in self._critical_vars:
                critical_vars_in_templates.add(var)
                continue

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
        stats = {
            "total_variables": len(self._variables),
            "critical_variables": len(self._critical_vars),
            "default_variables": len(self._default_registered_keys),
            "dynamic_handlers": sum(
                1 for v in self._variables.values() if v.is_dynamic
            ),
            "by_type": {},
        }

        for var_type in VariableType:
            count = sum(1 for v in self._variables.values() if v.var_type == var_type)
            stats["by_type"][var_type.value] = count

        return stats

    def export_config(self, output_path: str) -> bool:
        try:
            import yaml

            config = {
                "critical_variables": sorted(self._critical_vars),
                "fallbacks": {},
            }

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

        self._fallbacks.clear()
        self._default_handlers.clear()

        self._register_default_fallbacks()

        log_info_safe(
            logger, "Cleared all fallbacks and reset to defaults", prefix="FALLBACK"
        )


_GLOBAL_FALLBACK_MANAGER: Optional["FallbackManager"] = None
_FALLBACK_MANAGER_LOCK = threading.Lock()


def get_global_fallback_manager(
    config_path: Optional[Union[str, FallbackConfig]] = None,
    mode: str = "prompt",
    allowed_fallbacks: Optional[List[str]] = None,
) -> "FallbackManager":
    """Return the lazily-created global FallbackManager singleton (thread-safe)."""
    global _GLOBAL_FALLBACK_MANAGER

    if _GLOBAL_FALLBACK_MANAGER is not None:
        return _GLOBAL_FALLBACK_MANAGER

    with _FALLBACK_MANAGER_LOCK:
        if _GLOBAL_FALLBACK_MANAGER is None:
            _GLOBAL_FALLBACK_MANAGER = FallbackManager(
                config_path=config_path,
                mode=mode,
                allowed_fallbacks=allowed_fallbacks
            )

    return _GLOBAL_FALLBACK_MANAGER


def set_global_fallback_manager(manager: Optional["FallbackManager"]) -> None:
    """Set or clear the global fallback manager (useful for tests)."""
    global _GLOBAL_FALLBACK_MANAGER
    with _FALLBACK_MANAGER_LOCK:
        _GLOBAL_FALLBACK_MANAGER = manager
