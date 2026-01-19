# Standard library imports
import json
from pathlib import Path
from typing import Any

# Optional dependencies with graceful fallback
try:
    import platform

    import psutil

    HAS_SYSTEM_INFO_DEPS = True
except ImportError:
    HAS_SYSTEM_INFO_DEPS = False

# Third-party imports
from textual.app import ComposeResult
from textual.containers import Container, Horizontal, VerticalScroll
from textual.screen import ModalScreen
from textual.widgets import Button, DataTable, RichLog, Static


class BuildLogDialog(ModalScreen[bool]):
    """Modal dialog showing current build log, history and system info.

    This implementation uses simple Container elements instead of TabbedContent/TabPane
    for better compatibility with different Textual versions.

    Attributes:
        build_orchestrator: The orchestrator object that manages build operations
                           and provides access to logs and history.
        LOG_EXPORT_PATH: Path where current build logs are exported.
        HISTORY_EXPORT_PATH: Path where build history is exported.
    """

    # Default export paths - can be overridden by subclasses
    LOG_EXPORT_PATH = Path("current_build.log")
    HISTORY_EXPORT_PATH = Path("build_history.json")

    def __init__(self, build_orchestrator: Any) -> None:
        """Initialize the build log dialog.

        Args:
            build_orchestrator: The object that manages build operations and provides
                               access to logs and history.
        """
        super().__init__()

        # Validate the build orchestrator has required methods
        required_methods = ["get_current_build_log", "get_build_history"]
        for method in required_methods:
            if not hasattr(build_orchestrator, method):
                raise AttributeError(
                    f"Build orchestrator must implement '{method}' method"
                )

        self.build_orchestrator = build_orchestrator

    def compose(self) -> ComposeResult:
        """Compose the dialog layout.

        Returns:
            ComposeResult: The composed UI elements.
        """
        with Container(id="build-log-dialog"):
            yield Static("Build Logs & History", id="dialog-title")

            # Current build log section
            with Container(id="current-build"):
                yield Static("Current Build", classes="section-title")
                yield RichLog(id="current-build-log", auto_scroll=True)
                with Horizontal(classes="button-row"):
                    yield Button(
                        "Refresh",
                        id="refresh-current",
                        variant="primary",
                    )
                    yield Button(
                        "Export Log",
                        id="export-current",
                        variant="default",
                    )

            # Build history section
            with Container(id="build-history"):
                yield Static("Build History", classes="section-title")
                yield DataTable(id="build-history-table")
                with Horizontal(classes="button-row"):
                    yield Button(
                        "View Details",
                        id="view-build-details",
                        variant="primary",
                    )
                    yield Button(
                        "Export History",
                        id="export-history",
                        variant="default",
                    )

            # System info section
            with Container(id="system-info"):
                yield Static(
                    "System Information",
                    classes="section-title",
                )
                with VerticalScroll():
                    yield Static(id="system-info-content")

            with Horizontal(id="dialog-buttons"):
                yield Button("Close", id="close-logs", variant="default")

    def on_mount(self) -> None:
        """Initialize the dialog content when it's mounted."""
        self._refresh_current_log()
        self._refresh_build_history()
        self._refresh_system_info()

    def _notify(self, message: str, severity: str = "information") -> None:
        """Display a notification to the user.

        Args:
            message: The notification message.
            severity: The severity level (error, warning, information, success).
        """
        try:
            self.app.notify(message, severity=severity)
        except Exception:
            # If notification fails, we can't do much - silent failure
            pass

    def _get_orchestrator_data(
        self, method_name: str, default_value: Any = None
    ) -> Any:
        """Safely get data from the build orchestrator.

        Args:
            method_name: The name of the method to call on build_orchestrator.
            default_value: The default value to return if the method call fails.

        Returns:
            The data from the orchestrator or the default value if the call fails.
        """
        try:
            method = getattr(self.build_orchestrator, method_name)
            result = method() or default_value
            return result
        except AttributeError:
            self._notify(f"Build orchestrator has no method {method_name}", "error")
            return default_value
        except Exception as e:
            self._notify(f"Failed to get data: {str(e)}", "error")
            return default_value

    def _refresh_current_log(self) -> None:
        """Refresh the current build log display."""
        try:
            log_widget = self.query_one("#current-build-log", RichLog)
            log_widget.clear()

            log_lines = self._get_orchestrator_data("get_current_build_log", [])

            for line in log_lines:
                log_widget.write(line)
        except Exception as e:
            self._notify(f"Failed to load current log: {str(e)}", "error")

    def _refresh_build_history(self) -> None:
        """Refresh the build history table."""
        try:
            table = self.query_one("#build-history-table", DataTable)
            table.clear()

            if not table.columns:
                table.add_columns(
                    "Date",
                    "Device",
                    "Board",
                    "Status",
                    "Duration",
                )

            history = self._get_orchestrator_data("get_build_history", [])

            for entry in history:
                try:
                    date = entry.get("date") or entry.get("timestamp") or "Unknown"
                    device = entry.get("device") or "Unknown"
                    board = entry.get("board") or entry.get("board_type") or "Unknown"
                    status = entry.get("status") or "Unknown"
                    duration = entry.get("duration") or entry.get("time") or "Unknown"

                    table.add_row(date, device, board, status, duration)
                except Exception:
                    table.add_row("Unknown", "Unknown", "Unknown", "Unknown", "Unknown")
        except Exception as e:
            self._notify(f"Failed to load build history: {str(e)}", "error")

    def _refresh_system_info(self) -> None:
        """Refresh the system information display."""
        info_content = self.query_one("#system-info-content", Static)

        if not HAS_SYSTEM_INFO_DEPS:
            info_content.update(
                "System information requires psutil and platform packages"
            )
            return

        try:
            # Get system information
            memory = psutil.virtual_memory()
            disk = psutil.disk_usage("/")

            # Format memory and disk values in GB with 2 decimal precision
            mem_total_gb = round(memory.total / (1024**3), 2)
            mem_used_gb = round(memory.used / (1024**3), 2)
            mem_percent = memory.percent

            disk_total_gb = round(disk.total / (1024**3), 2)
            disk_used_gb = round(disk.used / (1024**3), 2)
            disk_percent = disk.percent

            # CPU information
            cpu_cores = psutil.cpu_count(logical=False)
            cpu_threads = psutil.cpu_count(logical=True)
            cpu_percent = psutil.cpu_percent(interval=0.1)

            # Enhanced system information display
            info_lines = [
                f"OS: {platform.system()} {platform.release()} ({platform.version()})",
                f"Architecture: {platform.machine()}",
                f"Python: {platform.python_version()}",
                "---",
                f"CPU: {cpu_cores} cores, {cpu_threads} threads ({cpu_percent}% usage)",
                f"Memory: {mem_used_gb} GB / {mem_total_gb} GB ({mem_percent}% used)",
                f"Disk: {disk_used_gb} GB / {disk_total_gb} GB ({disk_percent}% used)",
                "---",
                f"Hostname: {platform.node()}",
                f"User: {psutil.Process().username()}",
            ]

            info_text = "\n".join(info_lines)
            info_content.update(info_text)
        except Exception as e:
            info_content.update(f"Error retrieving system information: {str(e)}")

    async def on_button_pressed(self, event: Button.Pressed) -> None:
        """Handle button press events.

        Args:
            event: The button pressed event.
        """
        button_id = event.button.id

        if button_id == "close-logs":
            self.dismiss(False)
        elif button_id == "refresh-current":
            self._refresh_current_log()
        elif button_id == "export-current":
            await self._export_current_log()
        elif button_id == "export-history":
            await self._export_build_history()
        elif button_id == "view-build-details":
            self._notify("Build details view not implemented yet", "warning")

    async def _export_current_log(self) -> None:
        """Export the current build log to a file."""
        try:
            log_path = self.LOG_EXPORT_PATH
            log_lines = self._get_orchestrator_data("get_current_build_log", [])

            with open(log_path, "w") as f:
                f.write("PCILeech Build Log\n")
                f.write("================\n\n")

                for line in log_lines:
                    f.write(f"{line}\n")

            self._notify(f"Log exported to {log_path}", "success")
        except IOError as e:
            self._notify(f"Failed to write log file: {str(e)}", "error")
        except Exception as e:
            self._notify(f"Failed to export log: {str(e)}", "error")

    async def _export_build_history(self) -> None:
        """Export the build history to a JSON file."""
        try:
            history_path = self.HISTORY_EXPORT_PATH
            history_data = self._get_orchestrator_data("get_build_history", [])

            with open(history_path, "w") as f:
                json.dump({"builds": history_data}, f, indent=2)

            self._notify(f"History exported to {history_path}", "success")
        except IOError as e:
            self._notify(f"Failed to write history file: {str(e)}", "error")
        except Exception as e:
            self._notify(f"Failed to export history: {str(e)}", "error")
