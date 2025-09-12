"""
Base Plugin Interface

Defines the base interface for plugins in the PCILeech TUI application.
"""

import abc
from typing import Any, Dict, List, Optional, Protocol


class DeviceAnalyzer(Protocol):
    """Protocol defining a device analyzer component."""

    def analyze_device(self, device_id: str, config_space: bytes) -> Dict[str, Any]:
        """
        Analyze a device and return a dictionary of analysis results.

        Args:
            device_id: Device identifier
            config_space: Device configuration space data

        Returns:
            Dictionary containing analysis results
        """
        ...

    def get_name(self) -> str:
        """Get the name of the analyzer."""
        ...

    def get_description(self) -> str:
        """Get a description of the analyzer."""
        ...


class BuildHook(Protocol):
    """Protocol defining a build hook component."""

    def pre_build(self, config: Dict[str, Any]) -> Dict[str, Any]:
        """
        Execute before the build process starts.

        Args:
            config: Build configuration

        Returns:
            Modified build configuration
        """
        ...

    def post_build(self, build_result: Dict[str, Any]) -> Dict[str, Any]:
        """
        Execute after the build process completes.

        Args:
            build_result: Build results

        Returns:
            Modified build results
        """
        ...


class ConfigValidator(Protocol):
    """Protocol defining a configuration validator component."""

    def validate_config(self, config: Dict[str, Any]) -> List[str]:
        """
        Validate a configuration and return a list of validation issues.

        Args:
            config: Configuration to validate

        Returns:
            List of validation issues (empty if valid)
        """
        ...


class PCILeechPlugin(abc.ABC):
    """Base class for PCILeech TUI plugins.

    Required implementations:
      - get_name()
      - get_version()
      - get_description()

    Optional overrides may return None. Lifecycle defaults are explicit
    no-ops (not bare 'pass').
    """

    @abc.abstractmethod
    def get_name(self) -> str:  # pragma: no cover - interface contract
        """Return plugin name (must be unique)."""
        raise NotImplementedError("Plugin must implement get_name().")

    @abc.abstractmethod
    def get_version(self) -> str:  # pragma: no cover - interface contract
        """Return semantic version string."""
        raise NotImplementedError("Plugin must implement get_version().")

    @abc.abstractmethod
    def get_description(self) -> str:  # pragma: no cover - interface contract
        """Return short human-readable description."""
        raise NotImplementedError("Plugin must implement get_description().")

    def get_device_analyzer(self) -> Optional[DeviceAnalyzer]:
        """Optional device analyzer component (override if provided)."""
        return None

    def get_build_hook(self) -> Optional[BuildHook]:
        """Optional build hook component (override if provided)."""
        return None

    def get_config_validator(self) -> Optional[ConfigValidator]:
        """Optional config validator component (override if provided)."""
        return None

    def initialize(self, app_context: Dict[str, Any]) -> bool:  # pragma: no cover
        """Initialize plugin with application context (override for setup)."""
        return True

    def shutdown(self) -> None:  # pragma: no cover
        """Hook for cleanup; override if plugin allocates resources."""
        return None


class SimplePlugin(PCILeechPlugin):
    """Helper base class for minimal plugins (supply components via ctor)."""

    def __init__(
        self,
        name: str,
        version: str,
        description: str,
        device_analyzer: Optional[DeviceAnalyzer] = None,
        build_hook: Optional[BuildHook] = None,
        config_validator: Optional[ConfigValidator] = None,
    ) -> None:
        self._name = name
        self._version = version
        self._description = description
        self._device_analyzer = device_analyzer
        self._build_hook = build_hook
        self._config_validator = config_validator

    def get_name(self) -> str:
        return self._name

    def get_version(self) -> str:
        return self._version

    def get_description(self) -> str:
        return self._description

    def get_device_analyzer(self) -> Optional[DeviceAnalyzer]:
        return self._device_analyzer

    def get_build_hook(self) -> Optional[BuildHook]:
        return self._build_hook

    def get_config_validator(self) -> Optional[ConfigValidator]:
        return self._config_validator
