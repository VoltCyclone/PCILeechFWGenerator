#!/usr/bin/env python3
"""
Unit tests for BaseFunctionAnalyzer and device function analyzers.

Tests the refactored base class approach for eliminating code duplication
across network, storage, media, and USB function analyzers.
"""

from typing import Any, Dict, Set
from unittest.mock import MagicMock, patch

import pytest

# Import the modules under test
from pcileechfwgenerator.pci_capability.base_function_analyzer import (
    BaseFunctionAnalyzer, create_function_capabilities)
from pcileechfwgenerator.pci_capability.media_functions import (
    MediaFunctionAnalyzer, create_media_function_capabilities)
from pcileechfwgenerator.pci_capability.network_functions import (
    NetworkFunctionAnalyzer, create_network_function_capabilities)
from pcileechfwgenerator.pci_capability.storage_functions import (
    StorageFunctionAnalyzer, create_storage_function_capabilities)
from pcileechfwgenerator.pci_capability.usb_functions import (USBFunctionAnalyzer,
                                              create_usb_function_capabilities)


class MockFunctionAnalyzer(BaseFunctionAnalyzer):
    """Mock implementation of BaseFunctionAnalyzer for testing."""

    def __init__(self, vendor_id: int, device_id: int):
        super().__init__(vendor_id, device_id, "mock")

    def _analyze_device_category(self) -> str:
        return "test_category"

    def _analyze_capabilities(self) -> Set[int]:
        return {0x01, 0x05, 0x10, 0x11}  # PM, MSI, PCIe, MSI-X

    def get_device_class_code(self) -> int:
        return 0xFF0000  # Test class code

    def generate_device_features(self) -> Dict[str, Any]:
        return {"test_feature": True}

    def generate_bar_configuration(self) -> list[Dict[str, Any]]:
        return [
            {
                "bar": 0,
                "type": "memory",
                "size": 0x1000,
                "prefetchable": False,
                "description": "Test registers",
            }
        ]


class TestBaseFunctionAnalyzer:
    """Test cases for BaseFunctionAnalyzer base class."""

    def test_initialization(self):
        """Test base class initialization."""
        analyzer = MockFunctionAnalyzer(0x8086, 0x1234)

        assert analyzer.vendor_id == 0x8086
        assert analyzer.device_id == 0x1234
        assert analyzer.analyzer_type == "mock"
        assert analyzer._device_category == "test_category"
        assert analyzer._capabilities == {0x01, 0x05, 0x10, 0x11}

    def test_create_pm_capability_default(self):
        """Test Power Management capability creation with defaults."""
        analyzer = MockFunctionAnalyzer(0x8086, 0x1234)

        pm_cap = analyzer._create_pm_capability()

        assert pm_cap["cap_id"] == 0x01
        assert pm_cap["version"] == 3
        assert pm_cap["d3_support"] is True
        assert pm_cap["aux_current"] == 0

    def test_create_pm_capability_with_aux_current(self):
        """Test Power Management capability creation with aux current."""
        analyzer = MockFunctionAnalyzer(0x8086, 0x1234)

        pm_cap = analyzer._create_pm_capability(aux_current=100)

        assert pm_cap["aux_current"] == 100

    def test_create_msi_capability_default(self):
        """Test MSI capability creation with defaults."""
        analyzer = MockFunctionAnalyzer(0x8086, 0x1234)

        msi_cap = analyzer._create_msi_capability()

        assert msi_cap["cap_id"] == 0x05
        assert msi_cap["supports_64bit"] is True
        assert "multi_message_capable" in msi_cap
        assert "supports_per_vector_masking" in msi_cap

    def test_create_msi_capability_custom(self):
        """Test MSI capability creation with custom parameters."""
        analyzer = MockFunctionAnalyzer(0x8086, 0x1234)

        msi_cap = analyzer._create_msi_capability(
            multi_message_capable=3, supports_per_vector_masking=True
        )

        assert msi_cap["multi_message_capable"] == 3
        assert msi_cap["supports_per_vector_masking"] is True

    def test_create_pcie_capability_default(self):
        """Test PCIe capability creation with defaults."""
        analyzer = MockFunctionAnalyzer(0x8086, 0x1234)

        pcie_cap = analyzer._create_pcie_capability()

        assert pcie_cap["cap_id"] == 0x10
        assert pcie_cap["version"] == 2
        assert pcie_cap["device_type"] == 0
        assert pcie_cap["supports_flr"] is True
        assert "max_payload_size" in pcie_cap

    def test_create_msix_capability_default(self):
        """Test MSI-X capability creation with defaults."""
        analyzer = MockFunctionAnalyzer(0x8086, 0x1234)

        msix_cap = analyzer._create_msix_capability()

        assert msix_cap["cap_id"] == 0x11
        assert msix_cap["function_mask"] is True
        assert "table_size" in msix_cap
        assert "table_bar" in msix_cap
        assert "pba_bar" in msix_cap
        assert "table_offset" in msix_cap
        assert "pba_offset" in msix_cap

    def test_calculate_default_queue_count(self):
        """Test default queue count calculation."""
        # Test different device ID ranges
        test_cases = [
            (0x8086, 0x0500, 1),  # Low device ID
            (0x8086, 0x1500, 8),  # Mid device ID
            (0x8086, 0x2500, 16),  # High device ID
        ]

        for vendor_id, device_id, min_expected in test_cases:
            analyzer = MockFunctionAnalyzer(vendor_id, device_id)
            queue_count = analyzer._calculate_default_queue_count()

            assert queue_count >= min_expected
            # Verify it's a power of 2
            assert queue_count & (queue_count - 1) == 0

    def test_generate_capability_list(self):
        """Test capability list generation."""
        analyzer = MockFunctionAnalyzer(0x8086, 0x1234)

        capabilities = analyzer.generate_capability_list()

        assert len(capabilities) == 4  # PM, MSI, PCIe, MSI-X
        cap_ids = [cap["cap_id"] for cap in capabilities]
        assert 0x01 in cap_ids  # PM
        assert 0x05 in cap_ids  # MSI
        assert 0x10 in cap_ids  # PCIe
        assert 0x11 in cap_ids  # MSI-X

    def test_device_id_entropy_variation(self):
        """Test that device ID provides entropy for security."""
        # Test multiple device IDs with same vendor to ensure variation
        analyzers = [
            MockFunctionAnalyzer(0x8086, device_id)
            for device_id in [0x1000, 0x1001, 0x1002, 0x1003]
        ]

        queue_counts = [a._calculate_default_queue_count() for a in analyzers]
        bar_allocations = [a._get_default_msix_bar_allocation() for a in analyzers]

        # Should have some variation (not all identical)
        assert len(set(queue_counts)) > 1 or len(set(bar_allocations)) > 1


class TestNetworkFunctionAnalyzer:
    """Test cases for NetworkFunctionAnalyzer."""

    def test_initialization(self):
        """Test network analyzer initialization."""
        analyzer = NetworkFunctionAnalyzer(0x8086, 0x1234)

        assert analyzer.vendor_id == 0x8086
        assert analyzer.device_id == 0x1234
        assert analyzer.analyzer_type == "network"
        assert analyzer._device_category in [
            "ethernet",
            "wifi",
            "bluetooth",
            "cellular",
        ]

    def test_device_category_analysis(self):
        """Test device category analysis for network devices."""
        test_cases = [
            (0x8086, 0x1500, "ethernet"),  # Intel Ethernet pattern
            (0x8086, 0x2400, "wifi"),  # Intel WiFi pattern
            (0x10EC, 0x8100, "ethernet"),  # Realtek Ethernet pattern
        ]

        for vendor_id, device_id, expected_category in test_cases:
            analyzer = NetworkFunctionAnalyzer(vendor_id, device_id)
            assert analyzer._device_category == expected_category

    def test_class_code_generation(self):
        """Test class code generation for network devices."""
        analyzer = NetworkFunctionAnalyzer(0x8086, 0x1500)  # Ethernet
        class_code = analyzer.get_device_class_code()

        assert class_code in analyzer.CLASS_CODES.values()

    def test_sriov_support_detection(self):
        """Test SR-IOV support detection."""
        # High-end Intel Ethernet should support SR-IOV
        analyzer = NetworkFunctionAnalyzer(0x8086, 0x1600)
        assert analyzer._supports_sriov()

        # Low-end device should not
        analyzer = NetworkFunctionAnalyzer(0x8086, 0x1000)
        assert not analyzer._supports_sriov()

    def test_capability_generation(self):
        """Test capability generation for network devices."""
        analyzer = NetworkFunctionAnalyzer(0x8086, 0x1600)  # High-end device
        capabilities = analyzer.generate_capability_list()

        # Should have basic capabilities
        cap_ids = [cap["cap_id"] for cap in capabilities]
        assert 0x01 in cap_ids  # PM
        assert 0x05 in cap_ids  # MSI
        assert 0x10 in cap_ids  # PCIe
        assert 0x11 in cap_ids  # MSI-X

    def test_bar_configuration(self):
        """Test BAR configuration for network devices."""
        analyzer = NetworkFunctionAnalyzer(0x8086, 0x1600)
        bars = analyzer.generate_bar_configuration()

        assert len(bars) >= 1
        assert bars[0]["bar"] == 0
        assert bars[0]["type"] == "memory"
        assert bars[0]["size"] > 0

    def test_device_features(self):
        """Test device feature generation for network devices."""
        analyzer = NetworkFunctionAnalyzer(0x8086, 0x1600)
        features = analyzer.generate_device_features()

        assert "category" in features
        assert "queue_count" in features
        assert "supports_rss" in features
        assert "supports_tso" in features


class TestStorageFunctionAnalyzer:
    """Test cases for StorageFunctionAnalyzer."""

    def test_initialization(self):
        """Test storage analyzer initialization."""
        analyzer = StorageFunctionAnalyzer(0x144D, 0xA800)  # Samsung NVMe

        assert analyzer.vendor_id == 0x144D
        assert analyzer.device_id == 0xA800
        assert analyzer.analyzer_type == "storage"

    def test_nvme_device_detection(self):
        """Test NVMe device detection."""
        analyzer = StorageFunctionAnalyzer(0x144D, 0xA800)
        assert analyzer._device_category == "nvme"

    def test_aer_support_detection(self):
        """Test AER support detection for storage devices."""
        # NVMe should support AER
        analyzer = StorageFunctionAnalyzer(0x144D, 0xA800)
        assert analyzer._supports_aer()

        # Basic SATA should not
        analyzer = StorageFunctionAnalyzer(0x8086, 0x1000)
        assert not analyzer._supports_aer()

    def test_queue_count_calculation(self):
        """Test queue count calculation for storage devices."""
        # NVMe should have more queues than SATA
        nvme_analyzer = StorageFunctionAnalyzer(0x144D, 0xA800)
        sata_analyzer = StorageFunctionAnalyzer(0x8086, 0x2800)

        nvme_queues = nvme_analyzer._calculate_default_queue_count()
        sata_queues = sata_analyzer._calculate_default_queue_count()

        assert nvme_queues >= sata_queues


class TestMediaFunctionAnalyzer:
    """Test cases for MediaFunctionAnalyzer."""

    def test_initialization(self):
        """Test media analyzer initialization."""
        analyzer = MediaFunctionAnalyzer(0x8086, 0x2700)  # Intel HD Audio

        assert analyzer.vendor_id == 0x8086
        assert analyzer.device_id == 0x2700
        assert analyzer.analyzer_type == "media"

    def test_hdaudio_detection(self):
        """Test HD Audio device detection."""
        analyzer = MediaFunctionAnalyzer(0x8086, 0x2700)
        assert analyzer._device_category == "hdaudio"

    def test_vendor_capability_support(self):
        """Test vendor capability support for media devices."""
        analyzer = MediaFunctionAnalyzer(0x8086, 0x2700)
        assert analyzer._supports_vendor_capability()


class TestUSBFunctionAnalyzer:
    """Test cases for USBFunctionAnalyzer."""

    def test_initialization(self):
        """Test USB analyzer initialization."""
        analyzer = USBFunctionAnalyzer(0x8086, 0x1E00)  # Intel xHCI

        assert analyzer.vendor_id == 0x8086
        assert analyzer.device_id == 0x1E00
        assert analyzer.analyzer_type == "usb"

    def test_xhci_detection(self):
        """Test xHCI controller detection."""
        analyzer = USBFunctionAnalyzer(0x8086, 0x1E00)
        assert analyzer._device_category == "xhci"

    def test_msix_support_detection(self):
        """Test MSI-X support detection for USB controllers."""
        # Modern xHCI should support MSI-X
        analyzer = USBFunctionAnalyzer(0x8086, 0x1E00)
        assert analyzer._supports_msix()

        # Old UHCI should not
        analyzer = USBFunctionAnalyzer(0x8086, 0x2400)
        assert not analyzer._supports_msix()


class TestFactoryFunctions:
    """Test cases for factory functions."""

    def test_create_function_capabilities_generic(self):
        """Test generic factory function."""
        config = create_function_capabilities(
            MockFunctionAnalyzer, 0x8086, 0x1234, "MockAnalyzer"
        )

        assert config["vendor_id"] == 0x8086
        assert config["device_id"] == 0x1234
        assert config["class_code"] == 0xFF0000
        assert "capabilities" in config
        assert "bars" in config
        assert "features" in config
        assert config["generated_by"] == "MockAnalyzer"

    def test_create_network_function_capabilities(self):
        """Test network function factory."""
        config = create_network_function_capabilities(0x8086, 0x1500)

        assert config["vendor_id"] == 0x8086
        assert config["device_id"] == 0x1500
        assert config["generated_by"] == "NetworkFunctionAnalyzer"
        assert "capabilities" in config
        assert len(config["capabilities"]) > 0

    def test_create_storage_function_capabilities(self):
        """Test storage function factory."""
        config = create_storage_function_capabilities(0x144D, 0xA800)

        assert config["vendor_id"] == 0x144D
        assert config["device_id"] == 0xA800
        assert config["generated_by"] == "StorageFunctionAnalyzer"

    def test_create_media_function_capabilities(self):
        """Test media function factory."""
        config = create_media_function_capabilities(0x8086, 0x2700)

        assert config["vendor_id"] == 0x8086
        assert config["device_id"] == 0x2700
        assert config["generated_by"] == "MediaFunctionAnalyzer"

    def test_create_usb_function_capabilities(self):
        """Test USB function factory."""
        config = create_usb_function_capabilities(0x8086, 0x1E00)

        assert config["vendor_id"] == 0x8086
        assert config["device_id"] == 0x1E00
        assert config["generated_by"] == "USBFunctionAnalyzer"

    @patch("pcileechfwgenerator.pci_capability.base_function_analyzer.log_error_safe")
    def test_factory_function_error_handling(self, mock_log_error):
        """Test factory function error handling."""

        # Mock analyzer that raises an exception
        class FailingAnalyzer(MockFunctionAnalyzer):
            def __init__(self, vendor_id: int, device_id: int):
                raise RuntimeError("Test error")

        with pytest.raises(RuntimeError):
            create_function_capabilities(
                FailingAnalyzer, 0x8086, 0x1234, "FailingAnalyzer"
            )

        # Verify error was logged
        mock_log_error.assert_called_once()


class TestCodeDeduplication:
    """Test cases to verify code deduplication goals."""

    def test_no_duplicate_pm_capability_code(self):
        """Test that PM capability code is not duplicated."""
        analyzers = [
            NetworkFunctionAnalyzer(0x8086, 0x1234),
            StorageFunctionAnalyzer(0x8086, 0x1234),
            MediaFunctionAnalyzer(0x8086, 0x1234),
            USBFunctionAnalyzer(0x8086, 0x1234),
        ]

        # All should use base class PM capability structure
        for analyzer in analyzers:
            pm_cap = analyzer._create_pm_capability()
            assert pm_cap["cap_id"] == 0x01
            assert pm_cap["version"] == 3
            assert pm_cap["d3_support"] is True

    def test_consistent_capability_structure(self):
        """Test that all analyzers produce consistent capability structures."""
        vendor_id, device_id = 0x8086, 0x1234

        configs = [
            create_network_function_capabilities(vendor_id, device_id),
            create_storage_function_capabilities(vendor_id, device_id),
            create_media_function_capabilities(vendor_id, device_id),
            create_usb_function_capabilities(vendor_id, device_id),
        ]

        # All configs should have the same top-level structure
        required_keys = [
            "vendor_id",
            "device_id",
            "class_code",
            "capabilities",
            "bars",
            "features",
            "generated_by",
        ]

        for config in configs:
            for key in required_keys:
                assert key in config

    def test_device_specific_customization_preserved(self):
        """Test that device-specific customizations are preserved."""
        # Network device should support SR-IOV capabilities
        network_config = create_network_function_capabilities(0x8086, 0x1600)
        network_caps = {cap["cap_id"] for cap in network_config["capabilities"]}

        # Storage device should support AER
        storage_config = create_storage_function_capabilities(0x144D, 0xA800)

        # USB device should have USB-specific features
        usb_config = create_usb_function_capabilities(0x8086, 0x1E00)
        usb_features = usb_config["features"]

        # Verify device-specific features are present
        assert "usb_version" in usb_features  # USB-specific
        assert "category" in storage_config["features"]  # All should have category


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
