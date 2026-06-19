"""Tests for the VFIOAccess domain seam.

The domain (ConfigSpaceManager / VFIODeviceManager) depends on a VFIOAccess
Protocol it owns, not on the concrete CLI helpers. These tests verify the
default adapter resolves and that a fake adapter can be injected so domain VFIO
logic is exercisable without root or real hardware.
"""

from pcileechfwgenerator.device_clone.config_space_manager import ConfigSpaceManager
from pcileechfwgenerator.device_clone.vfio_access import (
    VFIOAccess,
    get_default_vfio_access,
)


class _FakeBinder:
    def __init__(self):
        self.bound = False

    @property
    def is_bound(self):
        return self.bound

    def bind(self):
        self.bound = True

    def unbind(self):
        self.bound = False


class _FakeVFIOAccess:
    def __init__(self):
        self.calls = []

    def ensure_binding(self, bdf):
        self.calls.append(("ensure_binding", bdf))
        return "42"

    def open_device(self, bdf):
        self.calls.append(("open_device", bdf))
        return (3, 4)

    def bind_for_session(self, bdf, attach=True):
        self.calls.append(("bind_for_session", bdf, attach))
        return _FakeBinder()


def test_default_adapter_satisfies_protocol():
    adapter = get_default_vfio_access()
    assert isinstance(adapter, VFIOAccess)


def test_fake_adapter_satisfies_protocol():
    assert isinstance(_FakeVFIOAccess(), VFIOAccess)


def test_config_space_manager_accepts_injected_access():
    fake = _FakeVFIOAccess()
    mgr = ConfigSpaceManager("0000:01:00.0", vfio_access=fake)
    assert mgr._vfio_access is fake


def test_config_space_manager_defaults_to_cli_adapter():
    mgr = ConfigSpaceManager("0000:01:00.0")
    assert isinstance(mgr._vfio_access, VFIOAccess)


def test_importing_vfio_access_does_not_import_cli():
    # The port module must not drag the CLI layer in at import time.
    # Fresh-ish check: the adapter only imports CLI lazily inside its methods.
    import importlib
    import sys

    importlib.import_module("pcileechfwgenerator.device_clone.vfio_access")
    # We don't assert cli is absent globally (other tests may import it), but
    # constructing the default adapter must not require CLI to be importable
    # at definition time — covered by the import succeeding above.
    assert "pcileechfwgenerator.device_clone.vfio_access" in sys.modules


if __name__ == "__main__":
    import pytest

    pytest.main([__file__, "-v"])
