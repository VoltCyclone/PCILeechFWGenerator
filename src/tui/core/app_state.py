"""
Application State Manager

Centralized state management for the PCILeech TUI application.
"""

from typing import Any, Callable, Dict, List, Optional

from ..models.config import BuildConfiguration
from ..models.device import PCIDevice
from ..models.progress import BuildProgress


class AppState:
    """
    Centralized state management for the PCILeech TUI application.

    This class implements a centralized store pattern to manage application state
    and notify subscribers of state changes. Components can subscribe to state
    changes and react accordingly, creating a unidirectional data flow architecture.

    State changes are propagated to subscribers, allowing for reactive UI updates
    and decoupled component communication.
    """

    def __init__(self):
        """Initialize the application state with default values."""
        self._state = {
            "devices": [],  # List of all discovered PCIe devices
            "selected_device": None,  # Currently selected device
            "config": BuildConfiguration(),  # Current build configuration
            "build_progress": None,  # Current build progress
            "filters": {},  # Device filter criteria
            "ui_state": {},  # UI-specific state (expanded sections, etc.)
        }
        self._subscribers = []

    def subscribe(self, callback: Callable[[Dict[str, Any], Dict[str, Any]], None]):
        """
        Subscribe to state changes.

        Args:
            callback: Function to call when state changes. The callback receives
                     the old state and new state as arguments.
        """
        self._subscribers.append(callback)
        return lambda: self._subscribers.remove(callback)  # Return unsubscribe function

    def update_state(self, updates: Dict[str, Any]):
        """
        Update the state with the provided values.

        Subscribers receive shallow *copies* of the old and new state so that
        a re-entrant update (or an accidental in-place mutation in a
        subscriber) cannot corrupt the live store mid-iteration. Subscribers
        MUST treat both arguments as read-only.

        Args:
            updates: Dictionary of state updates to apply
        """
        old_state = self._state.copy()
        self._state.update(updates)
        new_snapshot = self._state.copy()

        # Iterate a copy of subscribers so a callback that unsubscribes
        # itself doesn't skip the next one.
        for callback in list(self._subscribers):
            callback(old_state, new_snapshot)

    def get_state(self, key: Optional[str] = None) -> Any:
        """
        Get the current state or a specific state value.

        Args:
            key: Optional key to retrieve specific state value

        Returns:
            The requested state value or the entire state dictionary
        """
        if key:
            return self._state.get(key)
        return self._state.copy()

    # Convenience methods for common state operations

    def set_devices(self, devices: List[PCIDevice]):
        """Update the devices list in the state."""
        self.update_state({"devices": devices})

    def set_selected_device(self, device: Optional[PCIDevice]):
        """Update the selected device in the state."""
        self.update_state({"selected_device": device})

    def set_config(self, config: BuildConfiguration):
        """Update the build configuration in the state."""
        self.update_state({"config": config})

    def set_build_progress(self, progress: Optional[BuildProgress]):
        """Update the build progress in the state."""
        self.update_state({"build_progress": progress})

    def set_filters(self, filters: Dict[str, Any]):
        """Update the device filters in the state."""
        self.update_state({"filters": filters})

    def update_ui_state(self, ui_updates: Dict[str, Any]):
        """
        Update UI-specific state values.

        Args:
            ui_updates: Dictionary of UI state updates to apply
        """
        ui_state = self._state.get("ui_state", {}).copy()
        ui_state.update(ui_updates)
        self.update_state({"ui_state": ui_state})
