import json
from pathlib import Path
from typing import Any

from textual.app import ComposeResult
from textual.containers import Container, Horizontal, VerticalScroll
from textual.screen import ModalScreen
from textual.widgets import Button, Static


class DeviceDetailsDialog(ModalScreen[bool]):
    """Modal dialog for displaying detailed device information.

    This implementation uses plain Containers/VerticalScroll instead of
    TabbedContent/TabPane to avoid import-time errors across Textual versions.
    """

    def __init__(self, device: Any) -> None:
        """Initialize the device details dialog.

        Args:
            device: The device object containing PCI device information
        """
        super().__init__()
        self.device = device

    def compose(self) -> ComposeResult:
        """Compose the device details dialog UI."""
        with Container(id="device-details-dialog"):
            yield Static(
                f"📡 Device Details: {self._get_device_attr('bdf', 'unknown')}",
                id="dialog-title",
            )

            # Basic Info section
            with Container(id="basic-info"):
                yield Static("Basic Info", classes="section-title")
                with VerticalScroll():
                    yield Static(f"BDF: {self._get_device_attr('bdf')}")
                    yield Static(
                        f"Vendor: {self._get_device_attr('vendor_name')} ({self._get_device_attr('vendor_id')})"
                    )
                    yield Static(
                        f"Device: {self._get_device_attr('device_name')} ({self._get_device_attr('device_id')})"
                    )
                    yield Static(f"Class: {self._get_device_attr('device_class')}")
                    yield Static(f"Driver: {self._get_device_attr('driver', 'None')}")
                    yield Static(f"IOMMU Group: {self._get_device_attr('iommu_group')}")
                    yield Static(f"Power State: {self._get_device_attr('power_state')}")
                    yield Static(f"Link Speed: {self._get_device_attr('link_speed')}")

            # Compatibility section
            with Container(id="compatibility"):
                yield Static("Compatibility", classes="section-title")
                with VerticalScroll():
                    yield Static(
                        f"Suitability Score: {self._get_device_attr('suitability_score', 0):.2f}"
                    )
                    yield Static(
                        "✅ Suitable"
                        if self._get_device_attr("is_suitable", False)
                        else "❌ Not Suitable"
                    )

                    compatibility_issues = self._get_device_attr(
                        "compatibility_issues", None
                    )
                    if compatibility_issues:
                        yield Static("Issues:", classes="text-bold")
                        for issue in compatibility_issues:
                            yield Static(f"• {issue}", classes="status-error")

                    compatibility_factors = self._get_device_attr(
                        "compatibility_factors", None
                    )
                    if compatibility_factors:
                        yield Static("Compatibility Factors:", classes="text-bold")
                        for factor in compatibility_factors:
                            sign = "+" if factor.get("adjustment", 0) >= 0 else ""
                            yield Static(
                                f"• {factor.get('name')}: {sign}{factor.get('adjustment', 0):.1f} - {factor.get('description', '')}"
                            )

            # Hardware section
            with Container(id="hardware"):
                yield Static("Hardware", classes="section-title")
                with VerticalScroll():
                    yield Static("Base Address Registers (BARs):", classes="text-bold")
                    bars = self._get_device_attr("bars", None)
                    if bars:
                        for i, bar in enumerate(bars):
                            yield Static(f"BAR{i}: {bar}")
                    else:
                        yield Static("No BAR information available")

                    yield Static("Additional Hardware Info:", classes="text-bold")
                    for key, value in self._get_device_attr(
                        "detailed_status", {}
                    ).items():
                        yield Static(f"{key}: {value}")

            with Horizontal(id="dialog-buttons"):
                yield Button("Export Details", id="export-details", variant="primary")
                yield Button("Close", id="close-details", variant="default")

    async def on_button_pressed(self, event: Button.Pressed) -> None:
        if event.button.id == "close-details":
            self.dismiss(False)
        elif event.button.id == "export-details":
            await self._export_device_details()

    def _get_device_attr(self, attr_name: str, default: Any = "") -> Any:
        """Safely get an attribute from the device object.

        Args:
            attr_name: The name of the attribute to retrieve
            default: The default value to return if attribute doesn't exist

        Returns:
            The attribute value or the default value
        """
        return getattr(self.device, attr_name, default)

    async def _export_device_details(self) -> None:
        """Export device details to a JSON file in the current directory."""
        # Create export directory if it doesn't exist
        export_dir = Path("exports")
        try:
            export_dir.mkdir(exist_ok=True)

            # Create safe filename from BDF
            device_bdf = self._get_device_attr("bdf", "unknown")
            safe_filename = f"device_details_{device_bdf.replace(':', '_')}.json"
            output_path = export_dir / safe_filename

            # Prepare data for export
            data = self._get_device_attr("to_dict", lambda: None)()
            if data is None:
                data = {
                    "bdf": self._get_device_attr("bdf"),
                    "vendor": self._get_device_attr("vendor_name"),
                    "device": self._get_device_attr("device_name"),
                    "export_timestamp": self._get_device_attr("timestamp", ""),
                }

            # Write data to file
            try:
                with open(output_path, "w") as f:
                    json.dump(data, f, indent=2)
                self._notify(f"Device details exported to {output_path}", "success")
            except IOError as e:
                self._notify(f"File I/O error: {e}", "error")
            except TypeError as e:
                self._notify(f"JSON serialization error: {e}", "error")

        except PermissionError as e:
            self._notify(f"Permission error creating export directory: {e}", "error")
        except Exception as e:
            self._notify(f"Failed to export device details: {e}", "error")

    def _notify(self, message: str, severity: str = "information") -> None:
        """Safely send a notification to the user.

        Args:
            message: The notification message
            severity: The severity level (success, error, information)
        """
        try:
            self.app.notify(message, severity=severity)
        except Exception:
            # notify may not exist on minimal test harnesses
            pass
