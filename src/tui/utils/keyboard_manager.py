"""
Keyboard Manager Utility for PCILeech TUI application.

This module provides a keyboard manager for handling context-sensitive keyboard
shortcuts across different application modes.
"""

import logging
from typing import Callable

# Set up logging
logger = logging.getLogger(__name__)


class KeyboardManager:
    """
    Manages keyboard input handling with context-sensitive modes.

    This class provides a centralized way to handle keyboard shortcuts
    across different application modes, such as normal navigation,
    search, or build operations.
    """

    def __init__(self, app):
        """
        Initialize the KeyboardManager.

        Args:
            app: The application instance that will use this keyboard manager.
        """
        self.app = app
        self.mode = "normal"  # normal, search, build
        self._handler_registry = {
            "normal": {},
            "search": {},
            "build": {},
        }

    def handle_key(self, key: str) -> bool:
        """
        Handle context-sensitive keyboard shortcuts.

        Args:
            key: The key or key combination that was pressed.

        Returns:
            True if the key was handled, False otherwise.
        """
        handlers = {
            "normal": self._handle_normal_mode,
            "search": self._handle_search_mode,
            "build": self._handle_build_mode,
        }
        return handlers[self.mode](key)

    def _handle_normal_mode(self, key: str) -> bool:
        """
        Handle keys in normal navigation mode.

        Args:
            key: The key or key combination that was pressed.

        Returns:
            True if the key was handled, False otherwise.
        """
        logger.debug(f"Handling key '{key}' in normal mode")
        handler = self._handler_registry["normal"].get(key)
        if handler:
            handler()
            return True
        return False

    def _handle_search_mode(self, key: str) -> bool:
        """
        Handle keys in search mode.

        Args:
            key: The key or key combination that was pressed.

        Returns:
            True if the key was handled, False otherwise.
        """
        logger.debug(f"Handling key '{key}' in search mode")
        handler = self._handler_registry["search"].get(key)
        if handler:
            handler()
            return True
        # Special case for ESC key to exit search mode
        if key == "escape":
            self.set_mode("normal")
            return True
        return False

    def _handle_build_mode(self, key: str) -> bool:
        """
        Handle keys in build configuration mode.

        Args:
            key: The key or key combination that was pressed.

        Returns:
            True if the key was handled, False otherwise.
        """
        logger.debug(f"Handling key '{key}' in build mode")
        handler = self._handler_registry["build"].get(key)
        if handler:
            handler()
            return True
        # Special case for ESC key to exit build mode
        if key == "escape":
            self.set_mode("normal")
            return True
        return False

    def register_handler(
        self, mode: str, key: str, handler: Callable[[], None]
    ) -> None:
        """
        Register a key handler for a specific mode.

        Args:
            mode: The mode to register the handler for ("normal", "search", "build").
            key: The key or key combination to handle.
            handler: The function to call when the key is pressed.
        """
        if mode not in self._handler_registry:
            logger.warning(f"Attempted to register handler for unknown mode: {mode}")
            return

        logger.debug(f"Registering handler for key '{key}' in {mode} mode")
        self._handler_registry[mode][key] = handler

    def set_mode(self, mode: str) -> None:
        """
        Set the current keyboard handling mode.

        Args:
            mode: The mode to switch to ("normal", "search", "build").
        """
        if mode not in self._handler_registry:
            logger.warning(f"Attempted to switch to unknown mode: {mode}")
            return

        logger.debug(f"Switching keyboard mode from {self.mode} to {mode}")
        self.mode = mode
        # Notify the app that the mode changed
        if hasattr(self.app, "on_keyboard_mode_changed"):
            self.app.on_keyboard_mode_changed(mode)
