"""
Tests for device scanning functionality in the PCILeech TUI application.

This module demonstrates how to use mock implementations of protocols
to test the application without requiring real hardware.
"""

import asyncio
from typing import Any, List

import pytest

from src.tui.core.protocols import DeviceScanner
from src.tui.models.device import PCIDevice


# Mock TUI App for testing, no UI dependencies
class MockPCILeechTUI:
    """Mock implementation of PCILeechTUI for testing."""

    def __init__(self, device_scanner=None):
        """Initialize the mock app with optional dependencies."""
        self.device_scanner = device_scanner
        self.notify_calls = []

    async def _scan_devices(self) -> List[PCIDevice]:
        """Mock implementation of device scanning."""
        if self.device_scanner:
            return await self.device_scanner.scan_devices()
        return []

    def notify(self, message: str, severity: str = "info") -> None:
        """Mock notification method."""
        self.notify_calls.append({"message": message, "severity": severity})


class MockDeviceScanner:
    """Mock implementation of DeviceScanner for testing."""

    def __init__(self, mock_devices: List[PCIDevice]):
        """
        Initialize the MockDeviceScanner with a list of mock devices.

        Args:
            mock_devices: A list of PCIDevice objects to return when scanning.
        """
        self.mock_devices = mock_devices
        self.scan_count = 0

    async def scan_devices(self) -> List[PCIDevice]:
        """
        Mock implementation of scan_devices that returns predefined devices.

        Returns:
            The list of mock devices provided during initialization.
        """
        self.scan_count += 1
        return self.mock_devices


@pytest.fixture
def test_device() -> PCIDevice:
    """Fixture providing a test device for use in tests."""
    return PCIDevice(
        bdf="0000:00:00.0",
        vendor_id="8086",  # Intel
        device_id="1234",
        vendor_name="Intel Corporation",
        device_name="Test Device",
        device_class="0300",  # VGA compatible controller
        subsystem_vendor="1234",
        subsystem_device="5678",
        driver="i915",
        is_valid=True,
        has_driver=True,
        vfio_compatible=True,
        iommu_enabled=True,
    )


@pytest.fixture
def unsupported_device() -> PCIDevice:
    """Fixture providing an unsupported device for use in tests."""
    return PCIDevice(
        bdf="0000:00:01.0",
        vendor_id="10de",  # NVIDIA
        device_id="5678",
        vendor_name="NVIDIA Corporation",
        device_name="Unsupported Device",
        device_class="0300",  # VGA compatible controller
        compatibility_issues=[
            "Device not in supported list",
            "Missing driver capabilities",
        ],
        is_valid=True,
        has_driver=True,
        vfio_compatible=False,
        iommu_enabled=True,
    )


@pytest.mark.asyncio
async def test_device_scanning(test_device: PCIDevice):
    """Test that the app can scan for devices using the mock scanner."""
    # Create mock scanner with our test device
    mock_scanner = MockDeviceScanner([test_device])

    # Create app with mock scanner
    app = MockPCILeechTUI(device_scanner=mock_scanner)

    # Call the scan devices method
    devices = await app._scan_devices()

    # Verify we got our test device
    assert len(devices) == 1
    assert devices[0].bdf == test_device.bdf
    assert devices[0].vendor_name == test_device.vendor_name
    assert devices[0].device_name == test_device.device_name

    # Verify the scan method was called
    assert mock_scanner.scan_count == 1


@pytest.mark.asyncio
async def test_empty_device_list():
    """Test behavior when no devices are found."""
    # Create mock scanner with no devices
    mock_scanner = MockDeviceScanner([])

    # Create app with mock scanner
    app = MockPCILeechTUI(device_scanner=mock_scanner)

    # Call the scan devices method
    devices = await app._scan_devices()

    # Verify we got an empty list
    assert len(devices) == 0

    # Verify the scan method was called
    assert mock_scanner.scan_count == 1


@pytest.mark.asyncio
async def test_mixed_device_support(
    test_device: PCIDevice, unsupported_device: PCIDevice
):
    """Test behavior with both supported and unsupported devices."""
    # Create mock scanner with both devices
    mock_scanner = MockDeviceScanner([test_device, unsupported_device])

    # Create app with mock scanner
    app = MockPCILeechTUI(device_scanner=mock_scanner)

    # Call the scan devices method
    devices = await app._scan_devices()

    # Verify we got both devices
    assert len(devices) == 2

    # With simplified logic, both devices are considered supported; suitability will differ
    assert all(d.is_supported for d in devices)
    # Ensure the original devices are present
    assert any(d.bdf == test_device.bdf for d in devices)
    assert any(d.bdf == unsupported_device.bdf for d in devices)
