"""
Base Plugin Interface

Defines the base interface for plugins in the PCILeech TUI application.
"""

import abc
from typing import Any, Dict


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
    def get_version(self) -> str:  # pragma: no cover - interface contract
        """Return semantic version string."""
        raise NotImplementedError("Plugin must implement get_version().")

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
        version: str,
    ) -> None:
        self._version = version
        
    def get_version(self) -> str:
        return self._version

