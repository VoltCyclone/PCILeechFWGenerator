#!/usr/bin/env python3
"""
UI Helper Functions

Common UI utility functions for TUI components. This module provides
standardized helper functions for safely updating UI elements, formatting
status messages, and handling UI-related operations across the TUI codebase.
"""

from typing import Any, Dict

# We don't directly import Textual classes to avoid import errors during static analysis
# The objects will be checked using getattr and isinstance() at runtime


def safely_update_static(app: Any, selector: str, text: str) -> None:
    """
    Safely update a Static widget, handling potential errors.

    Args:
        app: The Textual app instance
        selector: CSS selector for the widget
        text: Text to update the widget with
    """
    try:
        # Use query_one without specifying the type to avoid import errors
        widget = app.query_one(selector)
        if hasattr(widget, "update") and callable(widget.update):
            widget.update(text)
        else:
            print(f"Widget {selector} doesn't have an update method")
    except Exception as e:
        print(f"Error updating widget {selector}: {e}")


def format_donor_module_status(status: Dict[str, Any]) -> str:
    """
    Format donor module status with appropriate emoji.

    Args:
        status: Donor module status dictionary

    Returns:
        Formatted status string
    """
    status_text = status.get("status", "unknown")

    if status_text == "installed":
        return "🧩 Donor Module: ✅ Installed"
    elif status_text == "built_not_loaded":
        return "🧩 Donor Module: ⚠️ Built but not loaded"
    elif status_text == "not_built":
        return "🧩 Donor Module: ❌ Not built"
    elif status_text == "missing_source":
        return "🧩 Donor Module: ❌ Source missing"
    elif status_text == "loaded_but_error":
        return "🧩 Donor Module: ⚠️ Loaded with errors"
    else:
        return "🧩 Donor Module: ❓ Unknown state"


def format_status_messages(status: Dict[str, Any]) -> Dict[str, str]:
    """
    Format system status into UI-ready messages with emojis.

    Args:
        status: System status dictionary from system_status.get_system_status()

    Returns:
        Dictionary mapping status keys to formatted UI messages
    """
    messages = {}

    # Podman status
    podman = status.get("podman", {})
    messages["podman"] = "🐳 Podman: " + (
        "Ready" if podman.get("status") == "ready" else "Not Available"
    )

    # Vivado status
    vivado = status.get("vivado", {})
    if vivado and vivado.get("status") == "detected":
        version = vivado.get("version", "Unknown")
        messages["vivado"] = f"⚡ Vivado: {version} Detected"
    else:
        messages["vivado"] = "⚡ Vivado: Not Detected"

    # USB devices
    usb = status.get("usb_devices", {})
    usb_count = usb.get("count", 0) if usb else 0
    messages["usb"] = f"🔌 USB Devices: {usb_count} Found"

    # Disk space
    disk = status.get("disk_space", {})
    if disk and "free_gb" in disk:
        free_gb = disk.get("free_gb")
        messages["disk"] = f"💾 Disk Space: {free_gb} GB Free"
    else:
        messages["disk"] = "💾 Disk Space: Unknown"

    # Root access
    root = status.get("root_access", {})
    messages["root"] = "🔒 Root Access: " + (
        "Available" if root and root.get("available") else "Required"
    )

    # Donor module status (if available)
    if "donor_module" in status:
        messages["donor_module"] = format_donor_module_status(status["donor_module"])

    return messages


def format_build_mode(config: Any) -> str:
    """
    Format build mode message based on configuration.

    Args:
        config: BuildConfiguration object

    Returns:
        Formatted build mode string
    """
    if config.local_build:
        return "Build Mode: Local Build (No Donor Dump)"
    else:
        return "Build Mode: Standard (With Donor Dump)"
