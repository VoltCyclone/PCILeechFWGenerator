import asyncio
from unittest.mock import AsyncMock, MagicMock, mock_open, patch

import pytest
from pcileechfwgenerator.tui.core.device_manager import (
    BAR_TYPE_IO,
    BAR_TYPE_MEMORY,
    DeviceManager,
)
from pcileechfwgenerator.tui.models.device import PCIDevice


@pytest.fixture
def device_manager():
    return DeviceManager()


@pytest.fixture
def sample_raw_device():
    return {
        "bdf": "0000:00:01.0",
        "ven": "8086",
        "dev": "1234",
        "class": "0200",
        "pretty": "0000:00:01.0 Ethernet controller [0200]: Intel Corporation 82574L Gigabit Network Connection [8086:1234]",
    }


@pytest.fixture
def sample_raw_devices():
    return [
        {
            "bdf": "0000:00:01.0",
            "ven": "8086",
            "dev": "1234",
            "class": "0200",
            "pretty": "0000:00:01.0 Ethernet controller [0200]: Intel Corporation 82574L Gigabit Network Connection [8086:1234]",
        },
        {
            "bdf": "0000:00:02.0",
            "ven": "10de",
            "dev": "5678",
            "class": "0300",
            "pretty": "0000:00:02.0 VGA compatible controller [0300]: NVIDIA Corporation Display Controller [10de:5678]",
        },
    ]


@pytest.mark.asyncio
async def test_scan_devices(device_manager, sample_raw_devices):
    with patch.object(
        device_manager, "_get_raw_devices", return_value=asyncio.Future()
    ) as mock_get_raw:
        mock_get_raw.return_value.set_result(sample_raw_devices)

        with patch.object(
            device_manager, "_enhance_device_info", new_callable=AsyncMock
        ) as mock_enhance:
            mock_enhance.side_effect = [
                PCIDevice(
                    bdf="0000:00:01.0",
                    vendor_id="8086",
                    device_id="1234",
                    vendor_name="Vendor 8086",
                    device_name="Intel Corporation 82574L Gigabit Network Connection",
                    device_class="0200",
                    subsystem_vendor="",
                    subsystem_device="",
                    driver="e1000e",
                    iommu_group="1",
                    power_state="D0",
                    link_speed="5 GT/s",
                    bars=[],
                    suitability_score=1.0,
                    compatibility_issues=[],
                    compatibility_factors=[],
                    is_valid=True,
                    has_driver=True,
                    is_detached=False,
                    vfio_compatible=True,
                    iommu_enabled=True,
                    detailed_status={},
                ),
                PCIDevice(
                    bdf="0000:00:02.0",
                    vendor_id="10de",
                    device_id="5678",
                    vendor_name="Vendor 10de",
                    device_name="NVIDIA Corporation Display Controller",
                    device_class="0300",
                    subsystem_vendor="",
                    subsystem_device="",
                    driver="nvidia",
                    iommu_group="2",
                    power_state="D0",
                    link_speed="8 GT/s",
                    bars=[],
                    suitability_score=0.5,
                    compatibility_issues=[
                        "Display controllers may have driver conflicts"
                    ],
                    compatibility_factors=[],
                    is_valid=True,
                    has_driver=True,
                    is_detached=False,
                    vfio_compatible=True,
                    iommu_enabled=True,
                    detailed_status={},
                ),
            ]

            devices = await device_manager.scan_devices()

            assert len(devices) == 2
            assert devices[0].bdf == "0000:00:01.0"
            assert devices[1].bdf == "0000:00:02.0"
            assert device_manager._device_cache == devices

            mock_get_raw.assert_called_once()
            assert mock_enhance.call_count == 2


@pytest.mark.asyncio
async def test_get_raw_devices(device_manager, sample_raw_devices):
    with patch(
        "pcileechfwgenerator.tui.core.device_manager.list_pci_devices", return_value=sample_raw_devices
    ):
        devices = await device_manager._get_raw_devices()

        assert devices == sample_raw_devices
        assert len(devices) == 2


def test_extract_device_name(device_manager):
    # Test standard format
    pretty = "0000:00:01.0 Ethernet controller [0200]: Intel Corporation 82574L Gigabit Network Connection [8086:1234]"
    assert (
        device_manager._extract_device_name(pretty)
        == "Intel Corporation 82574L Gigabit Network Connection"
    )

    # Test alternative format
    pretty = "0000:00:02.0 VGA compatible controller [0300]: NVIDIA Corporation"
    assert device_manager._extract_device_name(pretty) == "NVIDIA Corporation"

    # Test fallback
    pretty = "Invalid format string"
    assert device_manager._extract_device_name(pretty) == "Unknown Device"


@pytest.mark.asyncio
async def test_get_device_driver(device_manager):
    bdf = "0000:00:01.0"

    # Test successful retrieval
    with patch("pcileechfwgenerator.tui.core.device_manager.get_current_driver", return_value="e1000e"):
        driver = await device_manager._get_device_driver(bdf)
        assert driver == "e1000e"

    # Test error handling
    with patch(
        "pcileechfwgenerator.tui.core.device_manager.get_current_driver",
        side_effect=Exception("Driver error"),
    ):
        driver = await device_manager._get_device_driver(bdf)
        assert driver is None


@pytest.mark.asyncio
async def test_get_iommu_group(device_manager):
    bdf = "0000:00:01.0"

    # Test successful retrieval
    with patch("pathlib.Path.exists", return_value=True):
        with patch("pathlib.Path.resolve") as mock_resolve:
            mock_path = MagicMock()
            mock_path.name = "1"
            mock_resolve.return_value = mock_path

            iommu_group = await device_manager._get_iommu_group(bdf)
            assert iommu_group == "1"

    # Test path doesn't exist
    with patch("pathlib.Path.exists", return_value=False):
        iommu_group = await device_manager._get_iommu_group(bdf)
        assert iommu_group == "none"

    # Test error handling
    with patch("pathlib.Path.exists", side_effect=Exception("IOMMU error")):
        iommu_group = await device_manager._get_iommu_group(bdf)
        assert iommu_group == "unknown"


@pytest.mark.asyncio
async def test_get_power_state(device_manager):
    bdf = "0000:00:01.0"

    # Test successful retrieval
    with patch("os.path.exists", return_value=True):
        with patch("builtins.open", mock_open(read_data="D0")):
            power_state = await device_manager._get_power_state(bdf)
            assert power_state == "D0"

    # Test path doesn't exist
    with patch("os.path.exists", return_value=False):
        power_state = await device_manager._get_power_state(bdf)
        assert power_state == "unknown"

    # Test error handling
    with patch("os.path.exists", side_effect=Exception("Power error")):
        power_state = await device_manager._get_power_state(bdf)
        assert power_state == "unknown"


@pytest.mark.asyncio
async def test_get_link_speed(device_manager):
    bdf = "0000:00:01.0"

    # Test successful retrieval
    with patch("os.path.exists", return_value=True):
        with patch("builtins.open", mock_open(read_data="5 GT/s")):
            link_speed = await device_manager._get_link_speed(bdf)
            assert link_speed == "5 GT/s"

    # Test path doesn't exist
    with patch("os.path.exists", return_value=False):
        link_speed = await device_manager._get_link_speed(bdf)
        assert link_speed == "unknown"

    # Test error handling
    with patch("os.path.exists", side_effect=Exception("Link error")):
        link_speed = await device_manager._get_link_speed(bdf)
        assert link_speed == "unknown"


@pytest.mark.asyncio
async def test_get_device_bars(device_manager):
    bdf = "0000:00:01.0"

    # Test successful retrieval
    resource_data = """0x00000000fed00000 0x00000000fed003ff 0x0000000000000200
0x0000000000000000 0x0000000000000000 0x0000000000000000
0x0000000000010000 0x000000000001ffff 0x0000000000000101
0x0000000000000000 0x0000000000000000 0x0000000000000000
0x0000000000000000 0x0000000000000000 0x0000000000000000
0x0000000000000000 0x0000000000000000 0x0000000000000000"""

    with patch("os.path.exists", return_value=True):
        with patch("builtins.open", mock_open(read_data=resource_data)):
            bars = await device_manager._get_device_bars(bdf)

            assert len(bars) == 2
            assert bars[0]["index"] == 0
            assert bars[0]["start"] == 0xFED00000
            assert bars[0]["end"] == 0xFED003FF
            assert bars[0]["type"] == BAR_TYPE_MEMORY

            assert bars[1]["index"] == 2
            assert bars[1]["start"] == 0x10000
            assert bars[1]["end"] == 0x1FFFF
            assert bars[1]["type"] == BAR_TYPE_IO

    # Test path doesn't exist
    with patch("os.path.exists", return_value=False):
        bars = await device_manager._get_device_bars(bdf)
        assert bars == []

    # Test error handling
    with patch("os.path.exists", side_effect=Exception("BAR error")):
        bars = await device_manager._get_device_bars(bdf)
        assert bars == []


@pytest.mark.asyncio
async def test_check_device_validity(device_manager):
    bdf = "0000:00:01.0"

    # Test valid device
    with patch("os.path.exists", return_value=True):
        with patch("builtins.open", mock_open(read_data="8086")):
            is_valid = await device_manager._check_device_validity(bdf)
            assert is_valid is True

    # Test device path doesn't exist
    with patch(
        "os.path.exists", side_effect=lambda path: False if path.endswith(bdf) else True
    ):
        is_valid = await device_manager._check_device_validity(bdf)
        assert is_valid is False

    # Test vendor/device paths don't exist
    with patch(
        "os.path.exists",
        side_effect=lambda path: not (
            path.endswith("/vendor") or path.endswith("/device")
        ),
    ):
        is_valid = await device_manager._check_device_validity(bdf)
        assert is_valid is False

    # Test error when reading files
    with patch("os.path.exists", return_value=True):
        with patch("builtins.open", side_effect=Exception("Read error")):
            is_valid = await device_manager._check_device_validity(bdf)
            assert is_valid is False


@pytest.mark.asyncio
async def test_check_driver_status(device_manager):
    bdf = "0000:00:01.0"

    # Test with regular driver
    with patch("os.path.islink", return_value=True):
        has_driver, is_detached = await device_manager._check_driver_status(
            bdf, "e1000e"
        )
        assert has_driver is True
        assert is_detached is False

    # Test with VFIO driver
    with patch("os.path.islink", return_value=True):
        has_driver, is_detached = await device_manager._check_driver_status(
            bdf, "vfio-pci"
        )
        assert has_driver is True
        assert is_detached is True

    # Test no driver
    has_driver, is_detached = await device_manager._check_driver_status(bdf, None)
    assert has_driver is False
    assert is_detached is False

    # Test driver name exists but not bound
    with patch("os.path.islink", return_value=False):
        has_driver, is_detached = await device_manager._check_driver_status(
            bdf, "e1000e"
        )
        assert has_driver is False
        assert is_detached is False


@pytest.mark.asyncio
async def test_check_vfio_compatibility(device_manager):
    bdf = "0000:00:01.0"

    # Test VFIO available and compatible
    with patch("os.path.exists", side_effect=lambda path: True):
        with patch("pcileechfwgenerator.tui.core.device_manager.check_vfio_prerequisites"):
            with patch("builtins.open", mock_open(read_data="0x020000")):
                is_compatible = await device_manager._check_vfio_compatibility(bdf)
                assert is_compatible is True

    # Test VFIO not available
    with patch("os.path.exists", return_value=False):
        is_compatible = await device_manager._check_vfio_compatibility(bdf)
        assert is_compatible is False

    # Test VFIO prerequisites check fails
    with patch(
        "os.path.exists", side_effect=lambda path: path.startswith("/sys/module")
    ):
        with patch(
            "pcileechfwgenerator.tui.core.device_manager.check_vfio_prerequisites",
            side_effect=Exception("VFIO error"),
        ):
            is_compatible = await device_manager._check_vfio_compatibility(bdf)
            assert is_compatible is False

    # Test device in excluded class
    with patch("os.path.exists", return_value=True):
        with patch("pcileechfwgenerator.tui.core.device_manager.check_vfio_prerequisites"):
            with patch("builtins.open", mock_open(read_data="0x060000")):  # Host bridge
                is_compatible = await device_manager._check_vfio_compatibility(bdf)
                assert is_compatible is False


@pytest.mark.asyncio
async def test_check_iommu_status(device_manager):
    bdf = "0000:00:01.0"
    iommu_group = "1"

    # Test IOMMU properly configured
    with patch("os.path.exists", return_value=True):
        with patch("os.listdir", return_value=["0000:00:01.0"]):
            with patch("pcileechfwgenerator.tui.core.device_manager.check_iommu_group_binding"):
                is_enabled = await device_manager._check_iommu_status(bdf, iommu_group)
                assert is_enabled is True

    # Test IOMMU groups path doesn't exist
    with patch(
        "os.path.exists", side_effect=lambda path: not path.endswith("iommu_groups")
    ):
        is_enabled = await device_manager._check_iommu_status(bdf, iommu_group)
        assert is_enabled is False

    # Test invalid IOMMU group
    is_enabled = await device_manager._check_iommu_status(bdf, "none")
    assert is_enabled is False

    # Test device not in IOMMU group
    with patch("os.path.exists", return_value=True):
        with patch("os.listdir", return_value=["0000:00:02.0"]):  # Different device
            is_enabled = await device_manager._check_iommu_status(bdf, iommu_group)
            assert is_enabled is False


def test_assess_device_suitability(device_manager):
    # Test fully suitable device
    device_class = "0200"  # Network controller
    driver = "vfio-pci"
    bars = [
        {
            "index": 0,
            "start": 0x1000,
            "end": 0x1FFF,
            "size": 0x1000,
            "flags": 0,
            "type": BAR_TYPE_MEMORY,
        },
        {
            "index": 1,
            "start": 0x2000,
            "end": 0x2FFF,
            "size": 0x1000,
            "flags": 0,
            "type": BAR_TYPE_MEMORY,
        },
    ]

    score, issues, factors = device_manager._assess_device_suitability(
        device_class, driver, bars, True, True, True
    )

    assert score == 1.0
    assert len(issues) == 0
    assert len(factors) == 6  # All positive factors

    # Test unsuitable device
    device_class = "0300"  # Display controller
    driver = "nvidia"
    bars = []

    score, issues, factors = device_manager._assess_device_suitability(
        device_class, driver, bars, False, False, False
    )

    assert score == 0.5
    assert len(issues) >= 5  # Multiple issues
    assert len(factors) == 6  # Mix of positive and negative factors


def test_get_cached_devices(device_manager):
    # Create test devices
    device1 = PCIDevice(
        bdf="0000:00:01.0",
        vendor_id="8086",
        device_id="1234",
        vendor_name="Vendor 8086",
        device_name="Test Device 1",
        device_class="0200",
        subsystem_vendor="",
        subsystem_device="",
        driver="e1000e",
        iommu_group="1",
        power_state="D0",
        link_speed="5 GT/s",
        bars=[],
        suitability_score=1.0,
        compatibility_issues=[],
        compatibility_factors=[],
        is_valid=True,
        has_driver=True,
        is_detached=False,
        vfio_compatible=True,
        iommu_enabled=True,
        detailed_status={},
    )

    device2 = PCIDevice(
        bdf="0000:00:02.0",
        vendor_id="10de",
        device_id="5678",
        vendor_name="Vendor 10de",
        device_name="Test Device 2",
        device_class="0300",
        subsystem_vendor="",
        subsystem_device="",
        driver="nvidia",
        iommu_group="2",
        power_state="D0",
        link_speed="8 GT/s",
        bars=[],
        suitability_score=0.5,
        compatibility_issues=[],
        compatibility_factors=[],
        is_valid=True,
        has_driver=True,
        is_detached=False,
        vfio_compatible=False,
        iommu_enabled=True,
        detailed_status={},
    )

    # Set the cache
    device_manager._device_cache = [device1, device2]

    # Test get_cached_devices
    cached = device_manager.get_cached_devices()
    assert len(cached) == 2
    assert cached[0].bdf == "0000:00:01.0"
    assert cached[1].bdf == "0000:00:02.0"

    # Ensure it's a copy
    cached.pop()
    assert len(device_manager._device_cache) == 2


@pytest.mark.asyncio
async def test_refresh_devices(device_manager):
    with patch.object(
        device_manager, "scan_devices", new_callable=AsyncMock
    ) as mock_scan:
        mock_scan.return_value = ["device1", "device2"]

        result = await device_manager.refresh_devices()

        assert result == ["device1", "device2"]
        mock_scan.assert_called_once()


def test_find_device_by_bdf(device_manager):
    # Create test devices
    device1 = PCIDevice(
        bdf="0000:00:01.0",
        vendor_id="8086",
        device_id="1234",
        vendor_name="Vendor 8086",
        device_name="Test Device 1",
        device_class="0200",
        subsystem_vendor="",
        subsystem_device="",
        driver="e1000e",
        iommu_group="1",
        power_state="D0",
        link_speed="5 GT/s",
        bars=[],
        suitability_score=1.0,
        compatibility_issues=[],
        compatibility_factors=[],
        is_valid=True,
        has_driver=True,
        is_detached=False,
        vfio_compatible=True,
        iommu_enabled=True,
        detailed_status={},
    )

    device2 = PCIDevice(
        bdf="0000:00:02.0",
        vendor_id="10de",
        device_id="5678",
        vendor_name="Vendor 10de",
        device_name="Test Device 2",
        device_class="0300",
        subsystem_vendor="",
        subsystem_device="",
        driver="nvidia",
        iommu_group="2",
        power_state="D0",
        link_speed="8 GT/s",
        bars=[],
        suitability_score=0.5,
        compatibility_issues=[],
        compatibility_factors=[],
        is_valid=True,
        has_driver=True,
        is_detached=False,
        vfio_compatible=False,
        iommu_enabled=True,
        detailed_status={},
    )

    # Set the cache
    device_manager._device_cache = [device1, device2]

    # Test find_device_by_bdf with existing BDF
    found = device_manager.find_device_by_bdf("0000:00:01.0")
    assert found is not None
    assert found.bdf == "0000:00:01.0"

    # Test find_device_by_bdf with non-existing BDF
    not_found = device_manager.find_device_by_bdf("0000:00:03.0")
    assert not_found is None


def test_get_suitable_devices(device_manager):
    # Create test devices with different suitability
    device1 = PCIDevice(
        bdf="0000:00:01.0",
        vendor_id="8086",
        device_id="1234",
        vendor_name="Vendor 8086",
        device_name="Test Device 1",
        device_class="0200",
        subsystem_vendor="",
        subsystem_device="",
        driver="e1000e",
        iommu_group="1",
        power_state="D0",
        link_speed="5 GT/s",
        bars=[],
        suitability_score=1.0,
        compatibility_issues=[],
        compatibility_factors=[],
        is_valid=True,
        has_driver=True,
        is_detached=False,
        vfio_compatible=True,
        iommu_enabled=True,
        detailed_status={},
    )
    # Make device1 suitable
    device1.is_suitable = True

    device2 = PCIDevice(
        bdf="0000:00:02.0",
        vendor_id="10de",
        device_id="5678",
        vendor_name="Vendor 10de",
        device_name="Test Device 2",
        device_class="0300",
        subsystem_vendor="",
        subsystem_device="",
        driver="nvidia",
        iommu_group="2",
        power_state="D0",
        link_speed="8 GT/s",
        bars=[],
        suitability_score=0.5,
        compatibility_issues=[],
        compatibility_factors=[],
        is_valid=True,
        has_driver=True,
        is_detached=False,
        vfio_compatible=False,
        iommu_enabled=True,
        detailed_status={},
    )
    # Make device2 unsuitable
    device2.is_suitable = False

    # Set the cache
    device_manager._device_cache = [device1, device2]

    # Test get_suitable_devices
    suitable = device_manager.get_suitable_devices()
    assert len(suitable) == 1
    assert suitable[0].bdf == "0000:00:01.0"
