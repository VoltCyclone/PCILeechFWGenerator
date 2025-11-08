"""
Main TUI Application

The main entry point for the PCILeech Firmware Generator TUI.
"""

import asyncio
import json
import subprocess
import warnings
import webbrowser
from pathlib import Path
from typing import Any, Dict, List, Optional

from textual.app import App, ComposeResult
from textual.binding import Binding
from textual.containers import Container, Horizontal, Vertical, VerticalScroll
from textual.reactive import reactive
from textual.screen import ModalScreen
from textual.widgets import (
    Button,
    DataTable,
    Footer,
    Header,
    Input,
    Label,
    ProgressBar,
    RichLog,
    Select,
    Static,
)

from src.device_clone.board_config import list_supported_boards

from .core.app_state import AppState
from .core.background_monitor import BackgroundMonitor
from .core.build_orchestrator import BuildOrchestrator
from .core.config_manager import ConfigManager
from .core.device_manager import DeviceManager
from .core.error_handler import ErrorHandler
from .core.status_monitor import StatusMonitor
from .core.ui_coordinator import UICoordinator
from .dialogs.build_log import BuildLogDialog
from .dialogs.configuration import ConfigurationDialog
from .dialogs.confirmation import ConfirmationDialog
from .dialogs.device_details import DeviceDetailsDialog
from .dialogs.help_dialog import HelpDialog
from .dialogs.profile_manager import ProfileManagerDialog
from .dialogs.search_filter import SearchFilterDialog
from .models.config import BuildConfiguration
from .models.device import PCIDevice
from .models.progress import BuildProgress
from .utils.debounced_search import DebouncedSearch
from .widgets.virtual_device_table import VirtualDeviceTable


class PCILeechTUI(App):
    """Main TUI application for PCILeech firmware generation"""

    CSS_PATH = "styles/main.tcss"
    TITLE = "PCILeech Firmware Generator"
    SUB_TITLE = "Interactive firmware generation for PCIe devices"

    # Add keyboard bindings
    BINDINGS = [
        Binding("ctrl+q", "quit", "Quit"),
        Binding("ctrl+r", "refresh_devices", "Refresh"),
        Binding("ctrl+c", "configure", "Configure"),
        Binding("ctrl+s", "start_build", "Start Build"),
        Binding("ctrl+p", "manage_profiles", "Profiles"),
        Binding("ctrl+l", "view_logs", "Logs"),
        Binding("ctrl+f", "search_filter", "Search"),
        Binding("ctrl+d", "device_details", "Details"),
        Binding("ctrl+h", "show_help", "Help"),
        Binding("f1", "show_help", "Help"),
        Binding("f5", "refresh_devices", "Refresh"),
    ]

    # Reactive attributes
    selected_device: reactive[Optional[PCIDevice]] = reactive(None)
    current_config: reactive[BuildConfiguration] = reactive(BuildConfiguration())
    build_progress: reactive[Optional[BuildProgress]] = reactive(None)
    device_filters: reactive[Dict[str, Any]] = reactive({})

    # Type hints for dependency-injected services
    device_manager: DeviceManager
    config_manager: ConfigManager
    build_orchestrator: BuildOrchestrator
    status_monitor: StatusMonitor
    error_handler: ErrorHandler
    ui_coordinator: UICoordinator
    background_monitor: BackgroundMonitor
    app_state: AppState

    def __init__(self):
        # Initialize Textual app first to set up reactive system
        super().__init__()

        # Initialize app state
        self.app_state = AppState()

        # Core services
        self.device_manager = DeviceManager()
        self.config_manager = ConfigManager()
        self.build_orchestrator = BuildOrchestrator()
        self.status_monitor = StatusMonitor()
        self.background_monitor = BackgroundMonitor(self)

        # Performance optimizations
        self.debounced_search = DebouncedSearch(delay=0.3)

        # System state that isn't part of the app state
        self._system_status = {}
        self._build_history = []

        # Initialize app state with default config
        initial_config = self.config_manager.get_current_config()
        self.app_state.set_config(initial_config)
        self.current_config = initial_config

        # Create UI coordinator (needs to happen after other services are initialized)
        self.error_handler = ErrorHandler(self)
        self.ui_coordinator = UICoordinator(self)

        # Ensure logging is redirected to a file so log messages don't print
        # to stderr and corrupt the Textual UI. We'll tail that file into the
        # persistent notification panel instead.
        try:
            self._setup_file_logging()
        except Exception:
            pass

        # Install global exception handlers to avoid raw tracebacks printing
        # and corrupting the TUI rendering. These handlers will log the full
        # traceback via the ErrorHandler and show a compact notification.
        try:
            self._install_global_exception_handlers()
        except Exception:
            # Don't fail initialization if handlers can't be installed
            pass

        # Set up state change handler
        self.app_state.subscribe(self._on_state_change)

    # Keyboard action handlers
    def action_quit(self) -> None:
        """Quit the application"""
        self.exit()

    async def action_refresh_devices(self) -> None:
        """Refresh device list"""
        await self.ui_coordinator.scan_devices()

    async def action_configure(self) -> None:
        """Open configuration dialog"""
        await self._open_configuration_dialog()  # Still need a dialog opener

    async def action_start_build(self) -> None:
        """Start build process"""
        await self.ui_coordinator.handle_build_start()

    async def action_manage_profiles(self) -> None:
        """Open profile manager"""
        await self._open_profile_manager()

    async def action_view_logs(self) -> None:
        """Open build logs"""
        await self._open_build_logs()

    async def action_search_filter(self) -> None:
        """Open search/filter dialog"""
        await self._open_search_filter()

    async def action_device_details(self) -> None:
        """Show device details"""
        if self.selected_device:
            await self._show_device_details(self.selected_device)

    async def action_show_help(self) -> None:
        """Show help information"""
        await self._show_help()

    def compose(self) -> ComposeResult:
        """Create the main UI layout"""
        yield Header()

        with Container(id="main-container"):
            with Horizontal(id="top-section"):
                # Device Selection Panel
                with Vertical(id="device-panel", classes="panel"):
                    yield Static("📡 PCIe Device Selection", classes="panel-title")

                    # Add search bar
                    with Horizontal(classes="search-bar"):
                        yield Input(
                            placeholder="Search devices... (debounced)",
                            id="quick-search",
                        )
                        yield Button("🔍", id="advanced-search", variant="primary")

                    yield VirtualDeviceTable(id="device-table")
                    with Horizontal(classes="button-row"):
                        yield Button("Refresh", id="refresh-devices", variant="primary")
                        yield Button("Details", id="device-details", disabled=True)
                        yield Button(
                            "Export List", id="export-devices", variant="default"
                        )

                # Configuration Panel
                with Vertical(id="config-panel", classes="panel"):
                    yield Static("⚙️ Build Configuration", classes="panel-title")
                    # These are filled by the coordinator at runtime
                    yield Static("Board: (not loaded)", id="board-type")
                    yield Static("Advanced: (none)", id="advanced-features")
                    yield Static("Mode: (idle)", id="build-mode")
                    with Horizontal(classes="button-row"):
                        yield Button("Configure", id="configure", variant="primary")
                        yield Button("Profiles", id="manage-profiles")
                        yield Button("Load Profile", id="load-profile")
                        yield Button("Save Profile", id="save-profile")

                # Compatibility Panel
                with Vertical(id="compatibility-panel", classes="panel"):
                    yield Static("🔄 Compatibility Factors", classes="panel-title")
                    yield Static(
                        "Select a device to view compatibility factors",
                        id="compatibility-title",
                    )
                    yield Static("Score: N/A", id="compatibility-score")
                    yield DataTable(id="compatibility-table")

            with Horizontal(id="middle-section"):
                # Build Progress Panel
                with Vertical(id="build-panel", classes="panel"):
                    yield Static("🔨 Build Progress", classes="panel-title")
                    yield Static("Status: Ready to Build", id="build-status")
                    yield ProgressBar(total=100, id="build-progress")
                    yield Static("Progress: 0% (0/6 stages)", id="progress-text")
                    yield Static(
                        "Resources: CPU: 0% | Memory: 0GB | Disk: 0GB free",
                        id="resource-usage",
                    )
                    with Horizontal(classes="button-row"):
                        yield Button(
                            "▶ Start Build",
                            id="start-build",
                            variant="success",
                            disabled=True,
                        )
                        yield Button("⏸ Pause", id="pause-build", disabled=True)
                        yield Button("⏹ Stop", id="stop-build", disabled=True)
                        yield Button("📋 View Logs", id="view-logs")

            with Horizontal(id="bottom-section"):
                # System Status Panel
                with Vertical(id="status-panel", classes="panel"):
                    yield Static("📊 System Status", classes="panel-title")
                    yield Static("🐳 Podman: Checking...", id="podman-status")
                    yield Static("⚡ Vivado: Checking...", id="vivado-status")
                    yield Static("🔌 USB Devices: Checking...", id="usb-status")
                    yield Static("💾 Disk Space: Checking...", id="disk-status")
                    yield Static("🔒 Root Access: Checking...", id="root-status")
                    yield Static(
                        "🧩 Donor Module: Checking...", id="donor-module-status"
                    )

                # Quick Actions Panel
                with Vertical(id="actions-panel", classes="panel"):
                    yield Static("🚀 Quick Actions", classes="panel-title")
                    yield Button(
                        "🔍 Scan Devices", id="scan-devices", variant="primary"
                    )
                    yield Button("📁 Open Output Dir", id="open-output")
                    yield Button("📊 View Last Build Report", id="view-report")
                    yield Button("🧩 Check Donor Module", id="check-donor-module")
                    yield Button(
                        "🎯 Enable Donor Dump",
                        id="enable-donor-dump",
                        variant="success",
                    )
                    yield Button(
                        "📝 Generate Donor Template",
                        id="generate-donor-template",
                        variant="primary",
                    )
                    yield Button("⚙️ Advanced Settings", id="advanced-settings")
                    yield Button("📖 Documentation", id="documentation")
                    yield Button("💾 Backup Config", id="backup-config")

                # Notifications Panel (persistent)
                with Vertical(id="notifications-panel", classes="panel"):
                    yield Static("🔔 Notifications", classes="panel-title")
                    # RichLog provides a persistent scrollable log area
                    yield RichLog(id="notification-log")

        yield Footer()

    def on_mount(self) -> None:
        """Initialize the application"""
        try:
            # Set up the device table
            device_table = self.query_one("#device-table", DataTable)
            device_table.add_columns(
                "Status", "BDF", "Device", "Indicators", "Driver", "IOMMU"
            )

            # Set up quick search
            search_input = self.query_one("#quick-search", Input)
            search_input.placeholder = "Type to filter devices..."

            # Start background tasks
            self.call_after_refresh(self._initialize_app)

            # Start tailing the notifications log and forward new entries into the UI
            try:
                # Create an asyncio background task to tail the file
                asyncio.create_task(self._tail_notifications_log())
            except Exception:
                pass
        except Exception as e:
            # Handle initialization errors gracefully for tests
            print(f"Warning: Failed to initialize TUI: {e}")

    def _setup_file_logging(self) -> None:
        """Configure root logging to write to a notifications file and avoid stderr."""
        import logging
        import os

        log_dir = os.path.join(os.getcwd(), "logs")
        os.makedirs(log_dir, exist_ok=True)
        notif_path = os.path.join(log_dir, "notifications.log")

        # Configure basic logging to file and remove console handlers
        try:
            # Use basicConfig with force=True to replace handlers (Python 3.8+)
            logging.basicConfig(
                filename=notif_path,
                level=logging.INFO,
                format="%(asctime)s [%(levelname)s] %(message)s",
                force=True,
            )
        except TypeError:
            # Older basicConfig signature without force - manually replace handlers
            root = logging.getLogger()
            for h in list(root.handlers):
                root.removeHandler(h)
            fh = logging.FileHandler(notif_path)
            fh.setLevel(logging.INFO)
            fh.setFormatter(
                logging.Formatter("%(asctime)s [%(levelname)s] %(message)s")
            )
            root.addHandler(fh)

    async def _tail_notifications_log(self) -> None:
        """Tail the notifications.log file and forward appended lines into RichLog.

        Runs as a background asyncio task started on mount.
        """
        import asyncio
        import os

        log_path = os.path.join(os.getcwd(), "logs", "notifications.log")

        # Wait until file exists
        while not os.path.exists(log_path):
            await asyncio.sleep(0.2)

        try:
            with open(log_path, "r", encoding="utf-8", errors="replace") as f:
                # Seek to the end to only show new entries
                f.seek(0, os.SEEK_END)

                while True:
                    line = f.readline()
                    if not line:
                        await asyncio.sleep(0.25)
                        continue

                    # Write line to RichLog if available
                    try:
                        log_widget = self.query_one("#notification-log", RichLog)
                        # strip trailing newline for RichLog.write
                        log_widget.write(line.rstrip("\n"))
                    except Exception:
                        # If UI not ready, silently ignore
                        pass
        except Exception:
            # Never raise from the tailer
            return

    async def _initialize_app(self) -> None:
        """Initialize the application with data"""
        # Load default configuration profiles with error handling
        success = self.config_manager.create_default_profiles()
        if not success:
            self.notify(
                "Warning: Failed to create default profiles", severity="warning"
            )

            # No longer have error object with suggested actions
            self.notify(
                "Check configuration directory permissions", severity="information"
            )

        # Start system status monitoring using the optimized background monitor
        self.background_monitor.start_monitoring()

        # Initial device scan
        await self.ui_coordinator.scan_devices()

        # Update UI with current config
        self._update_config_display()

        # Show welcome message with keyboard shortcuts
        self.notify(
            "Welcome! Press F1 or Ctrl+H for help, Ctrl+Q to quit", severity="info"
        )

    # Input event handlers
    async def on_input_changed(self, event: Input.Changed) -> None:
        """Handle input changes for real-time search using DebouncedSearch"""
        if event.input.id == "quick-search":
            # Use the debounced search implementation for improved performance
            search_query = event.input.value
            await self.debounced_search.search(search_query, self._perform_search)

    async def _perform_search(self, query: str) -> None:
        """Callback for debounced search to perform the actual search operation"""
        self.ui_coordinator.apply_device_filters()
        self.ui_coordinator.update_device_table()

    # Enhanced button handlers
    async def on_button_pressed(self, event: Button.Pressed) -> None:
        """Handle button press events"""
        button_id = event.button.id

        if button_id == "refresh-devices" or button_id == "scan-devices":
            # Delegate device scanning to the centralized UI coordinator
            try:
                await self.ui_coordinator.scan_devices()
            except Exception as e:
                # Fallback to notify on failure
                self.notify(f"Failed to scan devices: {e}", severity="error")

        elif button_id == "start-build":
            await self.ui_coordinator.handle_build_start()

        elif button_id == "stop-build":
            await self.ui_coordinator.handle_build_stop()

        elif button_id == "configure":
            await self._open_configuration_dialog()

        elif button_id == "manage-profiles":
            await self._open_profile_manager()

        elif button_id == "advanced-search":
            await self._open_search_filter()

        elif button_id == "device-details":
            if self.selected_device:
                await self._show_device_details(self.selected_device)

        elif button_id == "export-devices":
            await self.ui_coordinator.export_device_list()

        elif button_id == "view-logs":
            await self._open_build_logs()

        elif button_id == "open-output":
            await self._open_output_directory()

        elif button_id == "view-report":
            await self._view_last_build_report()

        elif button_id == "backup-config":
            await self._backup_configuration()

        elif button_id == "check-donor-module":
            await self._check_donor_module_status(show_notification=True)

        elif button_id == "enable-donor-dump":
            await self._toggle_donor_dump()

        elif button_id == "generate-donor-template":
            await self._generate_donor_template()

        elif button_id == "documentation":
            await self._open_documentation()

        elif button_id == "advanced-settings":
            await self._open_advanced_settings()

    # New dialog methods
    async def _open_profile_manager(self) -> None:
        """Open the profile manager dialog"""
        try:
            # Open local profile manager dialog and delegate loading to coordinator
            result = await self.push_screen(ProfileManagerDialog(self.config_manager))
            if result:
                # Delegate profile loading and state update to coordinator
                await self.ui_coordinator.load_profile_by_name(result)
        except Exception as e:
            self.notify(f"Failed to open profile manager: {e}", severity="error")

    async def _open_search_filter(self) -> None:
        """Open the search/filter dialog"""
        try:
            result = await self.push_screen(SearchFilterDialog())
            if result:
                # Delegate applying filters to coordinator
                await self.ui_coordinator.apply_filters(result)
        except Exception as e:
            self.notify(f"Failed to open search dialog: {e}", severity="error")

    async def _show_device_details(self, device: PCIDevice) -> None:
        """Show detailed device information"""
        try:
            await self.push_screen(DeviceDetailsDialog(device))
        except Exception as e:
            self.notify(f"Failed to show device details: {e}", severity="error")

    async def _open_build_logs(self) -> None:
        """Open the build logs dialog"""
        try:
            # Use coordinator helpers to get logs when populating the dialog
            await self.push_screen(BuildLogDialog(self.build_orchestrator))
        except Exception as e:
            self.notify(f"Failed to open build logs: {e}", severity="error")

    async def _show_help(self) -> None:
        """Show help information"""
        # Show the shared HelpDialog (externalized to src/tui/dialogs/help_dialog.py)
        await self.push_screen(HelpDialog())

    async def _backup_configuration(self) -> None:
        """Backup current configuration"""
        try:
            timestamp = self._get_current_timestamp().replace(":", "-")
            backup_path = Path(f"config_backup_{timestamp}.json")

            config_data = {
                "backup_time": self._get_current_timestamp(),
                "current_config": self.app_state.get_state("config").to_dict(),
                "profiles": self.config_manager.list_profiles(),
            }

            with open(backup_path, "w") as f:
                json.dump(config_data, f, indent=2)

            self.notify(f"Configuration backed up to {backup_path}", severity="success")
        except Exception as e:
            self.notify(f"Failed to backup configuration: {e}", severity="error")

    async def _open_output_directory(self) -> None:
        """Open the output directory"""
        try:
            output_dir = Path("output")
            if output_dir.exists():
                if hasattr(subprocess, "run"):
                    # Try to open with system file manager
                    try:
                        subprocess.run(["xdg-open", str(output_dir)], check=False)
                    except (FileNotFoundError, subprocess.CalledProcessError):
                        # Fallback for different operating systems
                        try:
                            subprocess.run(
                                ["open", str(output_dir)], check=False
                            )  # macOS
                        except (FileNotFoundError, subprocess.CalledProcessError):
                            try:
                                subprocess.run(
                                    ["explorer", str(output_dir)], check=False
                                )  # Windows
                            except (FileNotFoundError, subprocess.CalledProcessError):
                                self.notify(
                                    f"Please manually open: {output_dir.absolute()}",
                                    severity="info",
                                )
                else:
                    self.notify(
                        f"Output directory: {output_dir.absolute()}", severity="info"
                    )
            else:
                self.notify("Output directory does not exist yet", severity="warning")
        except Exception as e:
            self.notify(f"Failed to open output directory: {e}", severity="error")

    async def _view_last_build_report(self) -> None:
        """View the last build report"""
        try:
            report_path = Path("output/last_build_report.json")
            if report_path.exists():
                with open(report_path, "r") as f:
                    report_data = json.load(f)

                # Show summary in notification
                build_time = report_data.get("build_time", "Unknown")
                status = report_data.get("status", "Unknown")
                device = report_data.get("device", "Unknown")

                self.notify(
                    f"Last build: {device} - {status} at {build_time}", severity="info"
                )
            else:
                self.notify("No build report found", severity="warning")
        except Exception as e:
            self.notify(f"Failed to read build report: {e}", severity="error")

    async def _open_documentation(self) -> None:
        """Open documentation"""
        try:
            # Try to open local documentation first
            docs_path = Path("docs/_build/html/index.html")
            if docs_path.exists():
                webbrowser.open(f"file://{docs_path.absolute()}")
                self.notify("Opening local documentation", severity="info")
            else:
                # Fallback to online documentation
                webbrowser.open("https://pcileechfwgenerator.voltcyclone.info")
                self.notify("Opening online documentation", severity="info")
        except Exception as e:
            self.notify(f"Failed to open documentation: {e}", severity="error")

    async def _open_advanced_settings(self) -> None:
        """Open advanced settings"""
        self.notify(
            "Advanced settings - use Configure button for full options", severity="info"
        )

    def _get_current_timestamp(self) -> str:
        """Get current timestamp as string"""
        from datetime import datetime

        return datetime.now().isoformat()

    def notify(self, message: str, severity: str = "info") -> None:
        """
        Display a persistent notification in the notification log and log it.

        This replaces ephemeral notifications that could be overwritten when
        the terminal redraws or when the mouse moves. Important messages
        (warnings/errors) will remain in the `#notification-log` area.
        """
        try:
            from datetime import datetime

            ts = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
            sev = severity.upper()
            line = f"[{ts}] [{sev}] {message}"

            # Append to RichLog if present
            try:
                log_widget = self.query_one("#notification-log", RichLog)
                log_widget.write(line)
            except Exception:
                # If UI not yet ready, or widget missing, fall back to logger
                import logging

                logging.getLogger(__name__).info(line)

        except Exception:
            # Never raise from notify - best effort only
            pass

    def _install_global_exception_handlers(self) -> None:
        """Install handlers for uncaught exceptions in main thread, threads, and asyncio.

        The goal is to prevent raw tracebacks from being printed to stdout/stderr
        (which breaks Textual's terminal rendering) and instead surface a
        concise notification while logging the full traceback to the app logger.
        """
        import asyncio
        import sys
        import threading
        import traceback

        def _handle_uncaught(exc_type, exc_value, exc_tb) -> None:
            # Format a concise message for the user and capture the full traceback
            try:
                tb = "".join(traceback.format_exception(exc_type, exc_value, exc_tb))
                # Persist via ErrorHandler if available, but avoid invoking the
                # logging subsystem here to prevent tracebacks from being
                # emitted to stderr (which breaks the TUI). Write directly to
                # the persistent error file and show a short notification.
                if hasattr(self, "error_handler") and self.error_handler is not None:
                    try:
                        # Write traceback to file directly (internal helper)
                        try:
                            self.error_handler._write_traceback_to_file(
                                "Uncaught exception", tb
                            )
                        except Exception:
                            # If that fails, silently ignore to avoid crashing
                            pass
                    except Exception:
                        # Swallow any errors here to avoid printing to stderr
                        pass

                # Try to show a compact notification in the TUI
                try:
                    # Only show a short one-line notification to avoid UI corruption
                    short = f"Unhandled {exc_type.__name__}: {str(exc_value)}"
                    if hasattr(self, "notify"):
                        self.notify(short, severity="error")
                except Exception:
                    # Ignore notification errors
                    pass
            except Exception:
                # Be extremely defensive: never raise from the excepthook
                try:
                    sys.stderr.write("Unhandled exception (failed to format)\n")
                except Exception:
                    pass

        # Main thread hook
        sys.excepthook = _handle_uncaught

        # Thread exceptions (Python 3.8+ supports threading.excepthook)
        try:

            def _thread_hook(args):
                _handle_uncaught(args.exc_type, args.exc_value, args.exc_traceback)

            threading.excepthook = _thread_hook
        except Exception:
            # Older Python versions don't have threading.excepthook
            pass

        # asyncio exceptions: set a loop exception handler that delegates to _handle_uncaught
        try:
            loop = asyncio.get_event_loop()

            def _asyncio_handler(loop, context):
                # context may contain 'exception' or only a message
                exc = context.get("exception")
                if exc is not None:
                    _handle_uncaught(
                        type(exc), exc, getattr(exc, "__traceback__", None)
                    )
                else:
                    # Create a synthetic exception to capture the message
                    msg = context.get("message", str(context))
                    _handle_uncaught(Exception, Exception(msg), None)

            loop.set_exception_handler(_asyncio_handler)
        except Exception:
            pass

    def _update_status_display(self) -> None:
        """Update system status display"""
        try:
            # Make sure _system_status exists
            status = getattr(self, "_system_status", {}) or {}

            # Only proceed if we have valid status and the UI is ready
            if not status:
                return

            # Import here to avoid circular import
            from src.tui.utils.ui_helpers import (format_status_messages,
                                                  safely_update_static)

            try:
                # Format all status messages at once
                messages = format_status_messages(status)

                # Update all status widgets with formatted messages
                safely_update_static(
                    self, "#podman-status", messages.get("podman", "� Podman: Unknown")
                )
                safely_update_static(
                    self, "#vivado-status", messages.get("vivado", "⚡ Vivado: Unknown")
                )
                safely_update_static(
                    self, "#usb-status", messages.get("usb", "� USB Devices: Unknown")
                )
                safely_update_static(
                    self, "#disk-status", messages.get("disk", "� Disk Space: Unknown")
                )
                safely_update_static(
                    self,
                    "#root-status",
                    messages.get("root", "🔒 Root Access: Unknown"),
                )

                # Update donor module status if available
                if "donor_module" in messages:
                    safely_update_static(
                        self, "#donor-module-status", messages["donor_module"]
                    )
            except Exception as e:
                print(f"Error updating status widgets: {e}")
        except Exception as e:
            print(f"Error in _update_status_display: {e}")

    def _safely_update_static(self, selector: str, text: str) -> None:
        """
        Safely update a Static widget, handling potential errors.

        This is a legacy method. Use src.tui.utils.ui_helpers.safely_update_static instead.

        Args:
            selector: CSS selector for the widget
            text: Text to update the widget with
        """
        # Legacy wrapper: keep behavior but warn so callers migrate to the
        # centralized helper in src.tui.utils.ui_helpers
        warnings.warn(
            "_safely_update_static is deprecated; use src.tui.utils.ui_helpers.safely_update_static",
            DeprecationWarning,
            stacklevel=2,
        )

        # Import here to avoid circular import and preserve runtime behavior
        from src.tui.utils.ui_helpers import safely_update_static

        safely_update_static(self, selector, text)

    def _update_config_display(self) -> None:
        """Delegate configuration display updates to the UI coordinator.

        Older code called this method on the App instance. The coordinator
        now owns UI updates, so forward the call to keep backward
        compatibility.
        """
        try:
            if hasattr(self, "ui_coordinator") and self.ui_coordinator is not None:
                # Use the public wrapper when possible
                try:
                    self.ui_coordinator.update_config_display()
                except Exception:
                    # Fallback to the internal implementation if present
                    try:
                        self.ui_coordinator._update_config_display()
                    except Exception:
                        pass
        except Exception:
            # Swallow errors during startup to avoid crashing the TUI
            pass

    def _update_donor_dump_button(self) -> None:
        """Delegate donor-dump button updates to the UI coordinator.

        This keeps compatibility with older callers that expected the App to
        expose this helper.
        """
        try:
            if hasattr(self, "ui_coordinator") and self.ui_coordinator is not None:
                # Call the coordinator's existing helper
                try:
                    self.ui_coordinator._update_donor_dump_button()
                except Exception:
                    pass
        except Exception:
            pass

    async def on_data_table_row_selected(self, event: DataTable.RowSelected) -> None:
        """Handle device table row selection"""
        row_key = event.row_key

        # Find selected device
        selected_device = None
        for device in self.filtered_devices:  # Use computed property
            if device.bdf == row_key:
                selected_device = device
                break

        if selected_device:
            # Update app state first
            self.app_state.set_selected_device(selected_device)
            # Then delegate to UI coordinator
            await self.ui_coordinator.handle_device_selection(selected_device)

    def _on_build_progress(self, progress: BuildProgress) -> None:
        """Handle build progress updates"""
        # Delegate to UI coordinator
        self.ui_coordinator.handle_build_progress(progress)

    async def _open_configuration_dialog(self) -> None:
        """Open the configuration dialog"""
        try:
            # Log current configuration before opening dialog
            try:
                print(
                    f"Current configuration device_type: {self.app_state.get_state('config').device_type}"
                )
            except Exception:
                pass

            result = await self.push_screen(
                ConfigurationDialog("Build Configuration", self.current_config)
            )
            if result is not None:
                # Update app state first
                self.app_state.set_config(result)
                # Then delegate configuration update to UI coordinator
                await self.ui_coordinator.handle_configuration_update(result)
        except Exception as e:
            if hasattr(self, "error_handler"):
                self.error_handler.handle_operation_error(
                    "opening configuration dialog", e
                )
            else:
                error_msg = f"Failed to open configuration dialog: {e}"
                print(f"ERROR: {error_msg}")
                self.notify(error_msg, severity="error")

    async def _confirm_with_warnings(self, title: str, message: str) -> bool:
        """Open a confirmation dialog with warnings and return user's choice"""
        try:
            result = await self.push_screen(ConfirmationDialog(title, message))
            return result is True
        except Exception as e:
            if hasattr(self, "error_handler"):
                self.error_handler.handle_operation_error(
                    "opening confirmation dialog", e
                )
            else:
                self.notify(
                    f"Failed to open confirmation dialog: {e}", severity="error"
                )
            return False

    async def _generate_donor_template(self) -> None:
        """Generate a donor info template file"""
        try:
            # Delegate generation to the coordinator which centralizes logic
            output_path = await self.ui_coordinator.generate_donor_template()
            if output_path:
                self.notify(
                    f"✓ Donor info template saved to: {output_path}", severity="success"
                )
                self.notify(
                    "Fill in the device-specific values and use it for advanced cloning",
                    severity="information",
                )

        except Exception as e:
            self.notify(f"Failed to generate donor template: {e}", severity="error")

    # App state handler
    def _on_state_change(
        self, old_state: Dict[str, Any], new_state: Dict[str, Any]
    ) -> None:
        """
        Handle app state changes.

        This method is called whenever the app state changes, and can be used to
        update UI elements or perform other actions in response to state changes.

        Args:
            old_state: The previous state
            new_state: The new state
        """
        # Update reactive attributes when app state changes
        if old_state.get("selected_device") != new_state.get("selected_device"):
            self.selected_device = new_state.get("selected_device")

        if old_state.get("config") != new_state.get("config"):
            self.current_config = new_state.get("config")

        if old_state.get("build_progress") != new_state.get("build_progress"):
            self.build_progress = new_state.get("build_progress")

        if old_state.get("filters") != new_state.get("filters"):
            self.device_filters = new_state.get("filters") or {}

    # Computed properties
    @property
    def devices(self) -> List[PCIDevice]:
        """Get all devices from app state."""
        return self.app_state.get_state("devices") or []

    @property
    def filtered_devices(self) -> List[PCIDevice]:
        """Get filtered devices based on search criteria and filters."""
        devices = self.devices
        filters = self.device_filters

        if not devices:
            return []

        # Apply filters if they exist
        if filters:
            # Filter by search text
            search_text = filters.get("search_text", "").lower()
            if search_text:
                devices = [
                    device
                    for device in devices
                    if search_text in device.display_name.lower()
                    or search_text in device.bdf.lower()
                    or search_text in device.vendor_name.lower()
                ]

            # Apply class filter
            if filters.get("class_filter") and filters["class_filter"] != "all":
                devices = [
                    device
                    for device in devices
                    if filters["class_filter"] in device.device_class.lower()
                ]

            # Apply status filter
            if filters.get("status_filter") and filters["status_filter"] != "all":
                status_filter = filters["status_filter"]
                if status_filter == "suitable":
                    devices = [d for d in devices if d.is_suitable]
                elif status_filter == "bound":
                    devices = [d for d in devices if d.has_driver]
                elif status_filter == "unbound":
                    devices = [d for d in devices if not d.has_driver]
                elif status_filter == "vfio":
                    devices = [d for d in devices if d.vfio_compatible]

            # Apply minimum score filter
            if filters.get("min_score", 0) > 0:
                min_score = filters["min_score"]
                devices = [
                    device
                    for device in devices
                    if device.suitability_score >= min_score
                ]

        return devices

    @property
    def can_start_build(self) -> bool:
        """Determine if a build can be started based on current state."""
        device = self.selected_device
        return device is not None and device.is_suitable and self.build_progress is None

    # Reactive watchers
    def watch_selected_device(self, device: Optional[PCIDevice]) -> None:
        """React to device selection changes"""
        if device:
            self.sub_title = f"Selected: {device.bdf} - {device.display_name}"
            # Use public coordinator wrapper
            self.ui_coordinator.update_compatibility_display(device)
        else:
            self.sub_title = "Interactive firmware generation for PCIe devices"
            self.ui_coordinator.clear_compatibility_display()

        # Update button states based on device selection
        try:
            start_button = self.query_one("#start-build", Button)
            start_button.disabled = not self.can_start_build
        except Exception:
            # Widget might not be available yet
            pass

    def watch_build_progress(self, progress: Optional[BuildProgress]) -> None:
        """React to build progress changes"""
        if progress:
            self.ui_coordinator.update_build_progress_display()

    async def _check_donor_module_status(
        self, show_notification: bool = True
    ) -> Dict[str, Any]:
        """
        Check donor_dump kernel module status and update UI

        Args:
            show_notification: Whether to show notification with status details

        Returns:
            Module status information dictionary
        """
        try:
            # Delegate the donor module check to the coordinator implementation
            module_status = await self.ui_coordinator.check_donor_module_status(
                show_notification=show_notification
            )
            return module_status

        except Exception as e:
            if show_notification:
                self.notify(
                    f"Failed to check donor module status: {e}", severity="error"
                )

            # Update status display with error
            if self._system_status is not None:
                self._system_status["donor_module"] = {
                    "status": "error",
                    "details": f"Error checking module: {str(e)}",
                    "issues": [f"Exception occurred: {str(e)}"],
                    "fixes": [
                        "Check if src/file_management/donor_dump_manager.py is accessible"
                    ],
                }
                self._update_status_display()

            return {
                "status": "error",
                "details": f"Error checking module: {str(e)}",
                "issues": [f"Exception occurred: {str(e)}"],
                "fixes": [
                    "Check if src/file_management/donor_dump_manager.py is accessible"
                ],
            }

    async def _toggle_donor_dump(self) -> None:
        """Toggle donor dump functionality"""
        current_config = self.current_config.copy()

        if current_config.donor_dump:
            # Disable donor dump
            current_config.donor_dump = False
            current_config.local_build = True
            self.current_config = current_config
            self.config_manager.set_current_config(current_config)
            self._update_config_display()
            self._update_donor_dump_button()
            self.notify("Donor dump disabled - using local build mode", severity="info")
        else:
            # Enable donor dump
            current_config.donor_dump = True
            current_config.local_build = False
            self.current_config = current_config
            self.config_manager.set_current_config(current_config)
            self._update_config_display()
            self._update_donor_dump_button()
            self.notify(
                "Donor dump enabled - device analysis will be performed",
                severity="success",
            )


if __name__ == "__main__":
    import asyncio

    from src.utils.system_status import check_root_access, check_vfio_support

    # Check OS compatibility and VFIO support
    vfio_status = asyncio.run(check_vfio_support())
    if not vfio_status.get("supported", False):
        print(f"❌ Error: {vfio_status.get('message', 'Unsupported operating system')}")
        print("PCILeech requires Linux for full functionality.")
        import sys

        sys.exit(1)

    # Check for warnings about VFIO modules
    vfio_checks = vfio_status.get("checks", {})
    if not vfio_checks.get("modules_loaded", True):
        print("⚠️  Warning: VFIO modules not loaded. Run:")
        print("⚠️  Warning:   sudo modprobe vfio vfio-pci")

    # Check sudo/root access
    root_status = asyncio.run(check_root_access())
    if not root_status.get("available", False):
        print("⚠️  Warning: Continuing without root privileges - limited functionality.")

    # Run the application
    app = PCILeechTUI()
    app.run()
