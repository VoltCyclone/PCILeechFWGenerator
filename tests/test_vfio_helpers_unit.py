#!/usr/bin/env python3
"""Unit tests for src.cli.vfio_helpers critical logic paths.

Covers:
- check_vfio_prerequisites branches (missing device, permission denied, driver missing, success)
- check_iommu_group_binding (missing path, all bound, unbound and wrong driver)
- ensure_device_vfio_binding (no IOMMU group -> "unknown", and successful resolution)
- get_device_fd early returns and error paths (no IOMMU group, missing group file, container open fails, group not viable)

All tests use mocks; no host VFIO/kernel dependencies.
"""

import errno
from pathlib import Path
from types import SimpleNamespace

import pytest

import src.cli.vfio_helpers as vh


class TestCheckVfioPrerequisites:
    def test_missing_container_device(self, monkeypatch):
        # /dev/vfio/vfio not present -> soft return
        monkeypatch.setattr(
            vh.os.path,
            "exists",
            lambda p: False if str(p) == "/dev/vfio/vfio" else True,
        )
        # Should not raise
        vh.check_vfio_prerequisites()

    def test_permission_denied_on_open(self, monkeypatch):
        # Container exists but open raises PermissionError
        monkeypatch.setattr(vh.os.path, "exists", lambda p: True)

        def fake_open(path, flags):
            if str(path) == "/dev/vfio/vfio":
                raise PermissionError()
            return 3

        monkeypatch.setattr(vh.os, "open", fake_open)
        monkeypatch.setattr(vh.os, "close", lambda fd: None)
        # Soft return
        vh.check_vfio_prerequisites()

    def test_vfio_pci_driver_missing(self, monkeypatch):
        # Container exists and opens, but vfio-pci driver path missing
        seq = {"opened": False}

        def fake_exists(p):
            s = str(p)
            if s == "/dev/vfio/vfio":
                return True
            if s == "/sys/bus/pci/drivers/vfio-pci":
                return False
            return True

        def fake_open(path, flags):
            if str(path) == "/dev/vfio/vfio":
                seq["opened"] = True
                return 11
            return 3

        monkeypatch.setattr(vh.os.path, "exists", fake_exists)
        monkeypatch.setattr(vh.os, "open", fake_open)
        monkeypatch.setattr(vh.os, "close", lambda fd: None)
        vh.check_vfio_prerequisites()
        assert seq["opened"] is True

    def test_success_path(self, monkeypatch):
        # Container exists and opens; vfio-pci exists
        def fake_exists(p):
            s = str(p)
            return s in {"/dev/vfio/vfio", "/sys/bus/pci/drivers/vfio-pci"}

        opened = {"count": 0}

        def fake_open(path, flags):
            opened["count"] += 1
            return 42

        monkeypatch.setattr(vh.os.path, "exists", fake_exists)
        monkeypatch.setattr(vh.os, "open", fake_open)
        monkeypatch.setattr(vh.os, "close", lambda fd: None)
        vh.check_vfio_prerequisites()
        assert opened["count"] >= 1


class TestCheckIommuGroupBinding:
    def test_group_devices_path_missing(self, monkeypatch):
        monkeypatch.setattr(vh.os.path, "exists", lambda p: False)
        # Soft return
        vh.check_iommu_group_binding("42")

    def test_all_devices_bound(self, monkeypatch):
        # Path exists, one device with vfio-pci
        monkeypatch.setattr(vh.os.path, "exists", lambda p: True)
        monkeypatch.setattr(vh.os, "listdir", lambda p: ["0000:01:00.0"])
        monkeypatch.setattr(
            vh.os.path,
            "basename",
            lambda p: "vfio-pci" if isinstance(p, str) else "vfio-pci",
        )
        monkeypatch.setattr(
            vh.os,
            "readlink",
            lambda p: "/sys/bus/pci/drivers/vfio-pci",
        )
        vh.check_iommu_group_binding("42")

    def test_unbound_and_wrong_driver(self, monkeypatch):
        # Two devices: one unbound, one wrong driver
        def fake_exists(p):
            s = str(p)
            # devices path exists, but driver path exists only for second device
            if "/devices" in s:
                return True
            if s.endswith("/0000:01:00.1/driver"):
                return True
            return False

        monkeypatch.setattr(vh.os.path, "exists", fake_exists)
        monkeypatch.setattr(
            vh.os, "listdir", lambda p: ["0000:01:00.0", "0000:01:00.1"]
        )
        monkeypatch.setattr(vh.os, "readlink", lambda p: "/sys/bus/pci/drivers/nvidia")
        vh.check_iommu_group_binding("42")


class TestEnsureDeviceVfioBinding:
    def test_no_iommu_group_returns_unknown(self, monkeypatch):
        bdf = "0000:01:00.0"

        # Driver path doesn't exist, and iommu_group path missing
        def fake_exists(p):
            s = str(p)
            if s.endswith(f"/devices/{bdf}/driver"):
                return False
            if s.endswith(f"/devices/{bdf}/iommu_group"):
                return False
            return True

        monkeypatch.setattr(vh.os.path, "exists", fake_exists)
        # check_vfio_prerequisites should be called but harmless; ensure it no-ops
        monkeypatch.setattr(vh, "check_vfio_prerequisites", lambda: None)
        assert vh.ensure_device_vfio_binding(bdf) == "unknown"

    def test_successful_group_resolution(self, monkeypatch):
        bdf = "0000:01:00.0"

        def fake_exists(p):
            s = str(p)
            # simulate driver and iommu_group present
            if s.endswith(f"/devices/{bdf}/driver"):
                return True
            if s.endswith(f"/devices/{bdf}/iommu_group"):
                return True
            return True

        monkeypatch.setattr(vh.os.path, "exists", fake_exists)
        # Driver symlink points to vfio-pci
        monkeypatch.setattr(
            vh.os, "readlink", lambda p: "/sys/bus/pci/drivers/vfio-pci"
        )
        monkeypatch.setattr(vh, "check_vfio_prerequisites", lambda: None)
        # Avoid deep checks inside
        monkeypatch.setattr(vh, "check_iommu_group_binding", lambda g: None)

        # Return simple basename from readlink for iommu group
        def fake_readlink_group(p):
            s = str(p)
            if s.endswith("/iommu_group"):
                return "42"
            return "/sys/bus/pci/drivers/vfio-pci"

        monkeypatch.setattr(vh.os, "readlink", fake_readlink_group)
        assert vh.ensure_device_vfio_binding(bdf) == "42"


class TestGetDeviceFd:
    def test_no_iommu_group_path(self, monkeypatch):
        bdf = "0000:01:00.0"

        # Pretend /sys/bus/pci/devices/<bdf>/iommu_group missing
        def fake_exists(p):
            return False

        monkeypatch.setattr(vh.os.path, "exists", fake_exists)
        monkeypatch.setattr(vh, "check_vfio_prerequisites", lambda: None)
        dev_fd, cont_fd = vh.get_device_fd(bdf)
        assert (dev_fd, cont_fd) == (-1, -1)

    def test_missing_group_device_file(self, monkeypatch):
        bdf = "0000:01:00.0"

        # iommu_group exists and resolves to 42, but /dev/vfio/42 missing
        def fake_exists(p):
            s = str(p)
            if s.endswith(f"/devices/{bdf}/iommu_group"):
                return True
            if s == "/dev/vfio/42":
                return False
            return True

        monkeypatch.setattr(vh.os.path, "exists", fake_exists)
        monkeypatch.setattr(vh, "check_vfio_prerequisites", lambda: None)
        monkeypatch.setattr(vh.os, "readlink", lambda p: "42")
        dev_fd, cont_fd = vh.get_device_fd(bdf)
        assert (dev_fd, cont_fd) == (-1, -1)

    def test_container_open_fails_enoent(self, monkeypatch):
        bdf = "0000:01:00.0"

        # Paths exist for iommu group and group file
        def fake_exists(p):
            s = str(p)
            if s.endswith(f"/devices/{bdf}/iommu_group"):
                return True
            if s == "/dev/vfio/42":
                return True
            return True

        # Track closes
        closed = []

        def fake_open(path, flags):
            if str(path) == "/dev/vfio/42":
                return 101  # group fd
            if str(path) == "/dev/vfio/vfio":
                raise OSError(errno.ENOENT, "no such file")
            return 3

        monkeypatch.setattr(vh.os.path, "exists", fake_exists)
        monkeypatch.setattr(vh, "check_vfio_prerequisites", lambda: None)
        monkeypatch.setattr(vh.os, "readlink", lambda p: "42")
        monkeypatch.setattr(vh.os, "open", fake_open)
        monkeypatch.setattr(vh.os, "close", lambda fd: closed.append(fd))

        with pytest.raises(OSError):
            vh.get_device_fd(bdf)

        # group fd should be closed in finally
        assert 101 in closed

    def test_group_not_viable_raises(self, monkeypatch):
        bdf = "0000:01:00.0"

        def fake_exists(p):
            s = str(p)
            if s.endswith(f"/devices/{bdf}/iommu_group"):
                return True
            if s == "/dev/vfio/42":
                return True
            # driver path exists but will fail later before checked
            if s.endswith(f"/devices/{bdf}/driver"):
                return True
            return True

        def fake_open(path, flags):
            if str(path) == "/dev/vfio/42":
                return 150
            if str(path) == "/dev/vfio/vfio":
                return 250
            return 3

        # noop until ioctl stage
        monkeypatch.setattr(vh.os.path, "exists", fake_exists)
        monkeypatch.setattr(vh.os, "open", fake_open)
        monkeypatch.setattr(vh.os, "close", lambda fd: None)
        monkeypatch.setattr(vh.os, "readlink", lambda p: "42")
        monkeypatch.setattr(vh, "check_vfio_prerequisites", lambda: None)

        # ioctl that does nothing (so group status flags remain 0 -> not viable)
        monkeypatch.setattr(vh.fcntl, "ioctl", lambda *args, **kwargs: 0)

        with pytest.raises(OSError):
            vh.get_device_fd(bdf)
