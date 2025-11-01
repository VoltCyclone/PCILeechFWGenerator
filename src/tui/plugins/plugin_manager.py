"""
Plugin Manager

Manages plugin discovery, registration, and lifecycle.
"""

import logging
from typing import Any, Dict, Optional

from .plugin_base import (PCILeechPlugin)

# Set up logging
logger = logging.getLogger(__name__)


class PluginManager:
    """
    Manages the discovery, registration, and lifecycle of plugins.

    This class provides methods to load plugins from directories,
    register plugins programmatically, and access different types
    of plugin components.
    """

    def __init__(self):
        """Initialize the plugin manager."""
        self.plugins: Dict[str, PCILeechPlugin] = {}
        self.app_context: Dict[str, Any] = {}

    def register_plugin(self, name: str, plugin: PCILeechPlugin) -> bool:
        """
        Register a plugin with the manager.

        Args:
            name: Unique name for the plugin
            plugin: Plugin instance

        Returns:
            Boolean indicating success
        """
        if name in self.plugins:
            logger.warning(f"Plugin '{name}' is already registered, overwriting")

        self.plugins[name] = plugin

        # Initialize the plugin if we have application context
        if self.app_context and not plugin.initialize(self.app_context):
            logger.error(f"Failed to initialize plugin '{name}'")
            del self.plugins[name]
            return False

        logger.info(f"Registered plugin: {name} ({plugin.get_version()})")
        return True


# Singleton instance for global access
_plugin_manager: Optional[PluginManager] = None


def get_plugin_manager() -> PluginManager:
    """
    Get the global plugin manager instance.

    Returns:
        Global PluginManager instance
    """
    global _plugin_manager
    if _plugin_manager is None:
        _plugin_manager = PluginManager()
    return _plugin_manager
