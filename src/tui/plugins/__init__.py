"""
PCILeech TUI Plugin System

This package provides plugin capabilities for the PCILeech TUI application.
"""

from .plugin_base import (
    BuildHook,
    ConfigValidator,
    DeviceAnalyzer,
    PCILeechPlugin,
    SimplePlugin,
)
from .plugin_manager import PluginManager, get_plugin_manager

__all__ = [
    "PCILeechPlugin",
    "SimplePlugin",
    "DeviceAnalyzer",
    "BuildHook",
    "ConfigValidator",
    "PluginManager",
    "get_plugin_manager",
]
