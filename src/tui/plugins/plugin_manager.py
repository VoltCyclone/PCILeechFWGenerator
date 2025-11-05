"""
Plugin Manager

Manages plugin discovery, registration, and lifecycle.
"""

import importlib
import importlib.util
import inspect
import logging
import os
import sys
from pathlib import Path
from typing import Any, Dict, List, Optional, Union, cast

from .plugin_base import (BuildHook, ConfigValidator, DeviceAnalyzer,
                          PCILeechPlugin)

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

    def unregister_plugin(self, name: str) -> bool:
        """
        Unregister and shutdown a plugin.

        Args:
            name: Name of the plugin to unregister

        Returns:
            Boolean indicating success
        """
        if name not in self.plugins:
            logger.warning(f"Plugin '{name}' is not registered")
            return False

        # Shutdown the plugin gracefully
        try:
            self.plugins[name].shutdown()
        except Exception as e:
            logger.error(f"Error shutting down plugin '{name}': {e}")

        # Remove the plugin
        del self.plugins[name]
        logger.info(f"Unregistered plugin: {name}")
        return True

    def get_plugin(self, name: str) -> Optional[PCILeechPlugin]:
        """
        Get a plugin by name.

        Args:
            name: Name of the plugin to retrieve

        Returns:
            Plugin instance or None if not found
        """
        return self.plugins.get(name)

    def set_app_context(self, context: Dict[str, Any]) -> None:
        """
        Set the application context shared with plugins.

        Args:
            context: Application context dictionary
        """
        self.app_context = context

        # Initialize any plugins that haven't been initialized yet
        for name, plugin in self.plugins.items():
            if not plugin.initialize(context):
                logger.error(f"Failed to initialize plugin '{name}' with new context")

    def discover_plugins(self, plugin_dirs: List[str]) -> int:
        """
        Discover and load plugins from the specified directories.

        Args:
            plugin_dirs: List of directory paths to search for plugins

        Returns:
            Number of plugins loaded
        """
        count = 0

        for plugin_dir in plugin_dirs:
            path = Path(plugin_dir)
            if not path.exists() or not path.is_dir():
                logger.warning(f"Plugin directory does not exist: {plugin_dir}")
                continue

            # Add the plugin directory to the path temporarily
            sys.path.insert(0, str(path))

            try:
                # Find all Python files in the directory
                for file_path in path.glob("*.py"):
                    if file_path.name.startswith("_"):
                        continue  # Skip __init__.py and other special files

                    module_name = file_path.stem

                    try:
                        # Import the module
                        spec = importlib.util.spec_from_file_location(
                            module_name, file_path
                        )
                        if spec is None or spec.loader is None:
                            logger.warning(f"Could not load spec for {file_path}")
                            continue

                        module = importlib.util.module_from_spec(spec)
                        spec.loader.exec_module(module)

                        # Find all PCILeechPlugin subclasses in the module
                        for name, obj in inspect.getmembers(module):
                            if (
                                inspect.isclass(obj)
                                and issubclass(obj, PCILeechPlugin)
                                and obj != PCILeechPlugin
                            ):

                                # Create an instance of the plugin
                                try:
                                    plugin = obj()
                                    plugin_name = plugin.get_name()

                                    # Register the plugin
                                    if self.register_plugin(plugin_name, plugin):
                                        count += 1

                                except Exception as e:
                                    logger.error(
                                        f"Error instantiating plugin class {name}: {e}"
                                    )

                    except Exception as e:
                        logger.error(f"Error loading plugin module {module_name}: {e}")

            finally:
                # Remove the plugin directory from the path
                if str(path) in sys.path:
                    sys.path.remove(str(path))

        return count

    def get_device_analyzers(self) -> List[DeviceAnalyzer]:
        """
        Get all device analyzer components from registered plugins.

        Returns:
            List of device analyzer components
        """
        analyzers = []
        for plugin in self.plugins.values():
            analyzer = plugin.get_device_analyzer()
            if analyzer:
                analyzers.append(analyzer)
        return analyzers

    def get_build_hooks(self) -> List[BuildHook]:
        """
        Get all build hook components from registered plugins.

        Returns:
            List of build hook components
        """
        hooks = []
        for plugin in self.plugins.values():
            hook = plugin.get_build_hook()
            if hook:
                hooks.append(hook)
        return hooks

    def get_config_validators(self) -> List[ConfigValidator]:
        """
        Get all configuration validator components from registered plugins.

        Returns:
            List of configuration validator components
        """
        validators = []
        for plugin in self.plugins.values():
            validator = plugin.get_config_validator()
            if validator:
                validators.append(validator)
        return validators

    def shutdown_all(self) -> None:
        """Shutdown all plugins."""
        for name, plugin in list(self.plugins.items()):
            try:
                plugin.shutdown()
                logger.info(f"Shutdown plugin: {name}")
            except Exception as e:
                logger.error(f"Error shutting down plugin '{name}': {e}")

        self.plugins.clear()


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
