import asyncio
import json
from pathlib import Path

import pytest

from pcileechfwgenerator.tui.core.ui_coordinator import UICoordinator


class DummyDevice:
    def __init__(self, bdf="0000:00:00.0"):
        self.bdf = bdf

    def to_dict(self):
        return {"bdf": self.bdf}


class DummyDeviceManager:
    async def scan_devices(self):
        # Return a couple of dummy devices
        return [DummyDevice("0000:00:01.0"), DummyDevice("0000:00:02.0")]


class DummyApp:
    def __init__(self):
        self.device_manager = DummyDeviceManager()
        # simple state store
        self._state = {"devices": []}
        # Minimal attributes expected by UICoordinator
        self.config_manager = None
        self.build_orchestrator = None
        self.status_monitor = None
        # minimal device filters storage expected by apply_device_filters
        self.device_filters = {}
        # placeholder app_state set later in tests when needed
        self.app_state = None

        # Minimal UI query_one stubs used by coordinator
        class _Stub:
            def __init__(self):
                self.columns = []

            def clear(self):
                self.columns = []

            def add_columns(self, *args, **kwargs):
                self.columns = list(args)

            def add_row(self, *args, **kwargs):
                # no-op for tests
                pass

            def update(self, *_a, **_k):
                pass
            
            def set_data(self, data):
                # no-op for tests - used by VirtualDeviceTable
                pass

            @property
            def value(self):
                return ""

        self._stub = _Stub()

        def query_one(selector):
            # return the stub for any selector used in coordinator
            return self._stub

        self.query_one = query_one

    def notify(self, *_args, **_kwargs):
        # no-op for tests
        pass

    def _get_current_timestamp(self):
        return "2025-08-14T00:00:00"

    # Minimal app_state methods used by coordinator (tests set real app_state)
    def app_state_set_devices(self, devices):
        self._state["devices"] = devices

    # Provide filtered_devices property expected by coordinator
    @property
    def filtered_devices(self):
        return self._state.get("devices", [])


@pytest.mark.asyncio
async def test_scan_devices_updates_state(tmp_path, monkeypatch):
    app = DummyApp()

    # attach minimal app_state with required methods
    class SimpleState:
        def __init__(self, app):
            self._app = app

        def set_devices(self, devices):
            self._app._state["devices"] = devices
        
        def set_filters(self, filters):
            # No-op for test - filters are handled elsewhere
            pass

    app.app_state = SimpleState(app)

    coordinator = UICoordinator(app)

    devices = await coordinator.scan_devices()

    assert isinstance(devices, list)
    assert len(devices) == 2
    assert app._state["devices"][0].bdf == "0000:00:01.0"


@pytest.mark.asyncio
async def test_export_device_list_writes_file(tmp_path, monkeypatch):
    app = DummyApp()
    # populate filtered devices
    app._state["devices"] = [DummyDevice("0000:00:aa.0")]

    # make coordinator use tmp_path for file
    coordinator = UICoordinator(app)

    # monkeypatch timestamp method to deterministic
    app._get_current_timestamp = lambda: "2025-08-14T00:00:00"

    # Change working directory to tmp_path to capture export
    monkeypatch.chdir(tmp_path)

    await coordinator.export_device_list()

    export_path = tmp_path / "pcie_devices.json"

    assert export_path.exists(), f"Expected export at {export_path}"

    data = json.loads(export_path.read_text())
    assert data["device_count"] == 1
    assert data["devices"][0]["bdf"] == "0000:00:aa.0"
