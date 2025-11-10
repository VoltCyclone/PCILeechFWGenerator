import logging
from types import SimpleNamespace

import pytest

from src.device_clone.pcileech_context import (
    PCILeechContextBuilder,
    VFIODeviceManager,
    BarConfiguration,
)


@pytest.fixture()
def test_logger():
    return logging.getLogger(__name__)


def test_vfio_manager_normalizes_negative_fds(monkeypatch, test_logger):
    # Patch VFIO open to return invalid FDs
    import src.cli.vfio_helpers as vfio_helpers

    monkeypatch.setattr(vfio_helpers, "get_device_fd", lambda _: (-1, -1))
    # ensure_device_vfio_binding may get called but should be non-fatal; no-op
    monkeypatch.setattr(
        vfio_helpers,
        "ensure_device_vfio_binding",
        lambda _: "unknown",
        raising=True,
    )

    mgr = VFIODeviceManager("0000:00:00.0", test_logger)
    dev_fd, cont_fd = mgr.open()

    # With normalization, both FDs should be None (unavailable)
    assert dev_fd is None and cont_fd is None

    # get_region_info should early-return None when no device FD is available
    assert mgr.get_region_info(0) is None

    # Close should not raise when FDs are None
    mgr.close()


def test_vfio_manager_invalid_container_fd(monkeypatch, test_logger):
    import src.cli.vfio_helpers as vfio_helpers

    monkeypatch.setattr(vfio_helpers, "get_device_fd", lambda _: (10, -1))
    monkeypatch.setattr(
        vfio_helpers,
        "ensure_device_vfio_binding",
        lambda _: "unknown",
        raising=True,
    )

    mgr = VFIODeviceManager("0000:00:00.0", test_logger)
    dev_fd, cont_fd = mgr.open()

    # Any negative FD triggers normalization to None for both
    assert dev_fd is None and cont_fd is None
    assert mgr.get_region_info(0) is None
    mgr.close()


def test_bar_info_sysfs_fallback_returns_configuration(monkeypatch, test_logger):
    # Build a context builder but stub out VFIO region info
    builder = PCILeechContextBuilder(
        device_bdf="0000:00:00.0",
        config=SimpleNamespace(),
    )

    # Force VFIO path to be unavailable
    monkeypatch.setattr(builder._vfio_manager, "get_region_info", lambda _: None)

    bar_data = {
        "type": "memory",
        "address": 0xA4000000,
        "size": 0x2000,
        "prefetchable": False,
        "is_64bit": True,
    }

    bar = builder._get_vfio_bar_info(0, bar_data)
    assert isinstance(bar, BarConfiguration)
    assert bar.index == 0
    assert bar.base_address == 0xA4000000
    assert bar.size == 0x2000
    assert bar.is_memory is True and bar.is_io is False
    assert bar.is_64bit is True


def test_bar_info_sysfs_fallback_ignores_zero_size(monkeypatch, test_logger):
    builder = PCILeechContextBuilder(
        device_bdf="0000:00:00.0",
        config=SimpleNamespace(),
    )
    monkeypatch.setattr(builder._vfio_manager, "get_region_info", lambda _: None)

    bar_data = {
        "type": "memory",
        "address": 0xDEADBEEF,
        "size": 0,
        "prefetchable": False,
        "is_64bit": False,
    }

    assert builder._get_vfio_bar_info(0, bar_data) is None
