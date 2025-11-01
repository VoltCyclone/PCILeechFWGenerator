import types

import pytest

import src.cli.vfio as vfio


class FakeResolved:
    def __init__(self, name: str):
        self.name = name


class FakePath:
    """Minimal fake Path to simulate /sys layout for vfio tests.

    Behavior is driven by class-level maps so each test can configure paths.
    """
    # Maps to control behavior per test
    exists_map = {}
    resolve_name_map = {}
    writes = []
    write_raises = set()

    def __init__(self, path: str):
        self._path = str(path)

    # Support string conversion for debug/inspection

    def __str__(self):
        return self._path

    # Methods used by vfio.py

    def exists(self) -> bool:
        return bool(FakePath.exists_map.get(self._path, False))

    def resolve(self):
        name = FakePath.resolve_name_map.get(self._path, None)
        if name is None:
            # Fallback: last segment of path
            name = self._path.rstrip("/").split("/")[-1]
        return FakeResolved(name)

    def write_text(self, text: str):
        if self._path in FakePath.write_raises:
            raise OSError("simulated write failure")
        FakePath.writes.append((self._path, text))



@pytest.fixture(autouse=True)
def reset_fake_path(monkeypatch):
    # Reset maps before each test
    FakePath.exists_map = {}
    FakePath.resolve_name_map = {}
    FakePath.writes = []
    FakePath.write_raises = set()
    # Patch Path used inside module
    monkeypatch.setattr(vfio, "Path", FakePath)
    yield


def test_get_current_driver_when_link_exists(monkeypatch):
    bdf = "0000:01:00.0"
    driver_link = f"/sys/bus/pci/devices/{bdf}/driver"

    FakePath.exists_map[driver_link] = True
    FakePath.resolve_name_map[driver_link] = "vfio-pci"

    drv = vfio.get_current_driver(bdf)
    assert drv == "vfio-pci"


def test_get_current_driver_when_link_missing():
    bdf = "0000:02:00.0"
    # Not setting exists_map means exists() -> False
    assert vfio.get_current_driver(bdf) is None


def test_restore_driver_writes_bind_when_driver_diff(monkeypatch):
    bdf = "0000:03:00.0"
    original = "nvidia"
    # Simulate currently bound to different driver
    monkeypatch.setattr(vfio, "get_current_driver", lambda x: "vfio-pci")

    bind_path = f"/sys/bus/pci/drivers/{original}/bind"
    FakePath.exists_map[bind_path] = True

    vfio.restore_driver(bdf, original)

    assert FakePath.writes == [(bind_path, f"{bdf}\n")]


def test_restore_driver_noop_when_same_driver(monkeypatch):
    bdf = "0000:04:00.0"
    original = "amdgpu"
    monkeypatch.setattr(vfio, "get_current_driver", lambda x: original)

    vfio.restore_driver(bdf, original)

    assert FakePath.writes == []


def test_restore_driver_raises_on_write_failure(monkeypatch):
    bdf = "0000:05:00.0"
    original = "mlx5_core"
    monkeypatch.setattr(vfio, "get_current_driver", lambda x: "vfio-pci")

    bind_path = f"/sys/bus/pci/drivers/{original}/bind"
    FakePath.exists_map[bind_path] = True
    FakePath.write_raises.add(bind_path)

    with pytest.raises(vfio.VFIOBindError):
        vfio.restore_driver(bdf, original)
