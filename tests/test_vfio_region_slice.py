import fcntl as _fcntl
import logging
import mmap as _mmap
from typing import Optional

import pytest


class _FakeMMap:
    """A minimal mmap-like object for testing.

    It exposes a sliceable buffer representing the mapped window.
    """

    def __init__(self, region_bytes: bytes, length: int, offset: int):
        self._buf = memoryview(region_bytes)[offset : offset + length]
        self.length = length
        self.offset = offset

    def __enter__(self):
        return self

    def __exit__(self, exc_type, exc, tb):
        return False

    def __getitem__(self, key):
        # Return bytes for any slice/index as mmap would yield bytes
        res = self._buf[key]
        return bytes(res) if isinstance(res, (bytes, bytearray, memoryview)) else res


def _install_fakes(
    monkeypatch,
    *,
    region_size: int,
    region_offset: int,
    flags: int,
    region_bytes: Optional[bytes] = None,
    record: Optional[dict] = None,
    page_size: Optional[int] = None,
):
    """Install fakes for fcntl.ioctl and mmap.mmap used by read_region_slice.

    - fcntl.ioctl fills a VfioRegionInfo struct with requested fields.
        - mmap.mmap returns a window into `region_bytes` with the requested
            offset and length.
    - Optionally override mmap.PAGESIZE.
    - Records last mmap args in `record` dict if provided.
    """
    # Region contents
    region_bytes = region_bytes or bytes(range(256)) * ((region_size + 255) // 256)

    def fake_ioctl(fd, req, info, mutate):
        # Late import to align with production code path
        from src.cli.vfio_constants import VfioRegionInfo

        assert isinstance(info, VfioRegionInfo)
        info.flags = flags
        info.size = region_size
        info.offset = region_offset
        return 0

    monkeypatch.setattr(_fcntl, "ioctl", fake_ioctl, raising=True)

    def fake_mmap(fd, length, offset=0, access=None):  # noqa: D401
        if record is not None:
            record["fd"] = fd
            record["length"] = length
            record["offset"] = offset
            record["access"] = access
        return _FakeMMap(region_bytes, length, offset)

    monkeypatch.setattr(_mmap, "mmap", fake_mmap, raising=True)

    if page_size is not None:
        monkeypatch.setattr(_mmap, "PAGESIZE", page_size, raising=False)


def _make_manager():
    from src.device_clone.pcileech_context import VFIODeviceManager

    mgr = VFIODeviceManager("0000:00:00.0", logging.getLogger(__name__))
    # Avoid calling open(); provide a dummy FD
    mgr._device_fd = 123
    return mgr


def test_read_region_slice_happy_path_mappable(monkeypatch):
    from src.cli.vfio_constants import VFIO_REGION_INFO_FLAG_MMAP

    # Arrange: region with predictable data and mappable flag
    region_size = 0x5000
    region_offset = 0
    flags = VFIO_REGION_INFO_FLAG_MMAP

    # Build region bytes 0..255 repeating for deterministic verification
    region_bytes = bytes(range(256)) * ((region_size + 255) // 256)

    _install_fakes(
        monkeypatch,
        region_size=region_size,
        region_offset=region_offset,
        flags=flags,
        region_bytes=region_bytes,
    )

    mgr = _make_manager()

    # Unaligned offset to exercise page alignment math
    offset = 123
    size = 1000

    # Act
    data = mgr.read_region_slice(index=0, offset=offset, size=size)

    # Assert
    assert data is not None
    assert data == region_bytes[offset : offset + size]


def test_read_region_slice_non_mappable_returns_none(monkeypatch):
    # Arrange: region not mappable
    region_size = 0x2000
    region_offset = 0
    flags = 0  # no MMAP flag

    _install_fakes(
        monkeypatch,
        region_size=region_size,
        region_offset=region_offset,
        flags=flags,
    )

    mgr = _make_manager()

    # Act
    result = mgr.read_region_slice(index=2, offset=64, size=128)

    # Assert
    assert result is None


def test_read_region_slice_clamps_to_region_end(monkeypatch):
    from src.cli.vfio_constants import VFIO_REGION_INFO_FLAG_MMAP

    # Arrange: request extends past end of region
    region_size = 0x1000
    region_offset = 0
    flags = VFIO_REGION_INFO_FLAG_MMAP

    region_bytes = bytes(range(256)) * ((region_size + 255) // 256)

    _install_fakes(
        monkeypatch,
        region_size=region_size,
        region_offset=region_offset,
        flags=flags,
        region_bytes=region_bytes,
    )

    mgr = _make_manager()
    offset = region_size - 100
    size = 500  # extends beyond

    # Act
    data = mgr.read_region_slice(index=1, offset=offset, size=size)

    # Assert: clamped length
    assert data is not None
    assert len(data) == region_size - offset
    assert data == region_bytes[offset:region_size]


def test_read_region_slice_zero_size_returns_empty_bytes(monkeypatch):
    # No fakes needed; size<=0 short-circuits
    mgr = _make_manager()
    assert mgr.read_region_slice(index=0, offset=0, size=0) == b""
    assert mgr.read_region_slice(index=0, offset=0, size=-5) == b""


def test_read_region_slice_respects_custom_page_size(monkeypatch):
    from src.cli.vfio_constants import VFIO_REGION_INFO_FLAG_MMAP

    # Arrange: force a non-standard page size and verify mmap offset alignment
    region_size = 0x50000
    region_offset = 0
    flags = VFIO_REGION_INFO_FLAG_MMAP

    region_bytes = bytes(range(256)) * ((region_size + 255) // 256)
    record = {}
    fake_page_size = 8192

    _install_fakes(
        monkeypatch,
        region_size=region_size,
        region_offset=region_offset,
        flags=flags,
        region_bytes=region_bytes,
        record=record,
        page_size=fake_page_size,
    )

    mgr = _make_manager()

    # Choose an offset that is not aligned to `fake_page_size`
    offset = fake_page_size + 123
    size = 4096

    data = mgr.read_region_slice(index=0, offset=offset, size=size)
    assert data is not None
    # Verify that mmap was called with an offset aligned to the fake page size
    assert record.get("offset") is not None
    assert record["offset"] % fake_page_size == 0
    # Data content must match ground truth
    assert data == region_bytes[offset : offset + size]
