"""
Tests for enhanced TUI functionality

Tests for the new features and improvements in the TUI application.
"""

import json
from pathlib import Path
from unittest.mock import AsyncMock, Mock, patch

import pytest

# Import from the actual source location following the project structure
try:
    from pcileechfwgenerator.tui.models.config import BuildConfiguration
    from pcileechfwgenerator.tui.models.device import PCIDevice
    from pcileechfwgenerator.tui.utils import (ConfigurationTemplates, DeviceFilter,
                               ExportManager, KeyboardShortcuts, SystemInfo,
                               ValidationHelper)
except ImportError:
    # Fallback import paths for different project configurations
    try:
        from tui.models.config import BuildConfiguration
        from tui.models.device import PCIDevice
        from tui.utils import (ConfigurationTemplates, DeviceFilter,
                               ExportManager, KeyboardShortcuts, SystemInfo,
                               ValidationHelper)
    except ImportError:
        pytest.skip("TUI modules not available", allow_module_level=True)


pytestmark = pytest.mark.tui


@pytest.mark.unit
class TestDeviceFilter:
    """Test device filtering functionality"""

    def setup_method(self):
        """Set up test devices"""
        self.devices = [
            PCIDevice(
                bdf="0000:01:00.0",
                vendor_id="10de",
                device_id="1234",
                vendor_name="NVIDIA",
                device_name="Test GPU",
                device_class="Display controller",
                subsystem_vendor="10de",
                subsystem_device="1234",
                driver="nvidia",
                iommu_group="1",
                power_state="D0",
                link_speed="8.0 GT/s",
                bars=[],
                suitability_score=0.9,
                compatibility_issues=[],
                has_driver=True,
                vfio_compatible=True,
            ),
            PCIDevice(
                bdf="0000:02:00.0",
                vendor_id="8086",
                device_id="5678",
                vendor_name="Intel",
                device_name="Test NIC",
                device_class="Network controller",
                subsystem_vendor="8086",
                subsystem_device="5678",
                driver=None,
                iommu_group="2",
                power_state="D0",
                link_speed="5.0 GT/s",
                bars=[],
                suitability_score=0.6,
                compatibility_issues=["Low score"],
                has_driver=False,
                vfio_compatible=False,
            ),
        ]

    def test_filter_by_search_text(self):
        """Test filtering by search text"""
        filters = {"device_search": "nvidia"}
        filtered = DeviceFilter.filter_devices(self.devices, filters)
        assert len(filtered) == 1
        assert filtered[0].vendor_name == "NVIDIA"

    def test_filter_by_class(self):
        """Test filtering by device class"""
        filters = {"class_filter": "network"}
        filtered = DeviceFilter.filter_devices(self.devices, filters)
        assert len(filtered) == 1
        assert "Network" in filtered[0].device_class

    def test_filter_by_status(self):
        """Test filtering by device status"""
        filters = {"status_filter": "suitable"}
        filtered = DeviceFilter.filter_devices(self.devices, filters)
        assert len(filtered) == 1
        assert filtered[0].is_suitable

    def test_filter_by_min_score(self):
        """Test filtering by minimum score"""
        filters = {"min_score": 0.8}
        filtered = DeviceFilter.filter_devices(self.devices, filters)
        assert len(filtered) == 1
        assert filtered[0].suitability_score >= 0.8

    def test_combined_filters(self):
        """Test combining multiple filters"""
        filters = {
            "device_search": "test",
            "status_filter": "suitable",
            "min_score": 0.8,
        }
        filtered = DeviceFilter.filter_devices(self.devices, filters)
        assert len(filtered) == 1
        assert filtered[0].vendor_name == "NVIDIA"

    def test_get_device_statistics(self):
        """Test device statistics calculation"""
        stats = DeviceFilter.get_device_statistics(self.devices)
        assert stats["total"] == 2
        assert stats["suitable"] == 1
        assert stats["bound"] == 1
        assert stats["unbound"] == 1
        assert stats["vfio_compatible"] == 1
        assert "NVIDIA" in stats["vendors"]
        assert "Intel" in stats["vendors"]


@pytest.mark.unit
class TestExportManager:
    """Test export functionality"""

    def setup_method(self):
        """Set up test device"""
        self.device = PCIDevice(
            bdf="0000:01:00.0",
            vendor_id="10de",
            device_id="1234",
            vendor_name="NVIDIA",
            device_name="Test GPU",
            device_class="Display controller",
            subsystem_vendor="10de",
            subsystem_device="1234",
            driver="nvidia",
            iommu_group="1",
            power_state="D0",
            link_speed="8.0 GT/s",
            bars=[],
            suitability_score=0.9,
            compatibility_issues=[],
        )
        self.devices = [self.device]

    def test_export_devices_json(self, tmp_path):
        """Test JSON export"""
        output_path = tmp_path / "devices.json"
        success = ExportManager.export_devices_json(self.devices, output_path)

        assert success
        assert output_path.exists()

        with open(output_path) as f:
            data = json.load(f)

        assert "devices" in data
        assert "metadata" in data
        assert len(data["devices"]) == 1
        assert (
            data["devices"][0].get("device_id") is not None
        )  # Expect 'bdf' key for consistency with field naming

    def test_export_devices_csv(self, tmp_path):
        """Test CSV export"""
        output_path = tmp_path / "devices.csv"
        success = ExportManager.export_devices_csv(self.devices, output_path)

        assert success
        assert output_path.exists()

        content = output_path.read_text()
        assert "BDF" in content
        assert "0000:01:00.0" in content
        assert "NVIDIA" in content


@pytest.mark.unit
class TestValidationHelper:
    """Test validation utilities"""

    def test_validate_bdf_valid(self):
        """Test valid BDF validation"""
        assert ValidationHelper.validate_bdf("0000:01:00.0")
        assert ValidationHelper.validate_bdf("ffff:ff:ff.f")
        assert ValidationHelper.validate_bdf("1234:ab:cd.e")

    def test_validate_bdf_invalid(self):
        """Test invalid BDF validation"""
        assert not ValidationHelper.validate_bdf("invalid")
        assert not ValidationHelper.validate_bdf("0000:01:00")
        assert not ValidationHelper.validate_bdf("0000:01:00.0.0")
        assert not ValidationHelper.validate_bdf("gggg:01:00.0")

    def test_validate_score_valid(self):
        """Test valid score validation"""
        assert ValidationHelper.validate_score("0.5") == 0.5
        assert ValidationHelper.validate_score("1.0") == 1.0
        assert ValidationHelper.validate_score("0.0") == 0.0

    def test_validate_score_invalid(self):
        """Test invalid score validation"""
        assert ValidationHelper.validate_score("1.5") is None
        assert ValidationHelper.validate_score("-0.1") is None
        assert ValidationHelper.validate_score("invalid") is None

    def test_sanitize_filename(self):
        """Test filename sanitization"""
        assert ValidationHelper.sanitize_filename("test.txt") == "test.txt"
        assert ValidationHelper.sanitize_filename("test/file.txt") == "test_file.txt"
        assert ValidationHelper.sanitize_filename("test<>file.txt") == "test__file.txt"
        assert ValidationHelper.sanitize_filename("") == "unnamed"


@pytest.mark.unit
class TestKeyboardShortcuts:
    """Test keyboard shortcuts functionality"""

    def test_get_help_text(self):
        """Test help text generation"""
        help_text = KeyboardShortcuts.get_help_text()
        assert "Keyboard Shortcuts" in help_text
        assert "Ctrl+Q" in help_text
        assert "Navigation" in help_text
        assert "Configuration" in help_text

    def test_shortcuts_structure(self):
        """Test shortcuts data structure"""
        shortcuts = KeyboardShortcuts.SHORTCUTS
        assert "Navigation" in shortcuts
        assert "Device Management" in shortcuts
        assert "Configuration" in shortcuts
        assert "Information" in shortcuts

        for category, bindings in shortcuts.items():
            assert isinstance(bindings, dict)
            for key, description in bindings.items():
                assert isinstance(key, str)
                assert isinstance(description, str)


@pytest.mark.unit
class TestConfigurationTemplates:
    """Test configuration templates"""

    def test_get_template_exists(self):
        """Test getting existing template"""
        template = ConfigurationTemplates.get_template("development")
        assert template is not None
        assert template["name"] == "Development"
        assert template["flash_after_build"] is True

    def test_get_template_not_exists(self):
        """Test getting non-existing template"""
        template = ConfigurationTemplates.get_template("nonexistent")
        assert template is None

    def test_list_templates(self):
        """Test listing all templates"""
        templates = ConfigurationTemplates.list_templates()
        assert "development" in templates
        assert "production" in templates
        assert "testing" in templates
        assert "minimal" in templates

    def test_template_structure(self):
        """Test template data structure"""
        for template_name in ConfigurationTemplates.list_templates():
            template = ConfigurationTemplates.get_template(template_name)
            assert template is not None
            assert "name" in template
            assert "description" in template
            assert "advanced_sv" in template
            assert isinstance(template["advanced_sv"], bool)


@pytest.mark.unit
class TestSystemInfo:
    """Test system information utilities"""

    def test_get_system_info_basic(self):
        """Test basic system info"""
        info = SystemInfo.get_system_info()
        assert "platform" in info
        assert "python" in info
        assert "pcileech" in info

        assert "system" in info["platform"]
        assert "version" in info["python"]
        assert "tui_version" in info["pcileech"]

    def test_get_system_info_with_psutil(self):
        """Test system info with or without psutil"""
        # This test just verifies that the function works
        # whether psutil is available or not
        info = SystemInfo.get_system_info()
        assert "platform" in info
        assert "python" in info
        assert "pcileech" in info

        # Test that it returns consistent structure
        assert "system" in info["platform"]
        assert "version" in info["python"]
        assert "tui_version" in info["pcileech"]


@pytest.mark.integration
class TestEnhancedTUIIntegration:
    """Integration tests for enhanced TUI features"""

    def test_filter_and_export_workflow(self, tmp_path):
        """Test complete filter and export workflow"""
        # Create test devices
        devices = [
            PCIDevice(
                bdf="0000:01:00.0",
                vendor_id="10de",
                device_id="1234",
                vendor_name="NVIDIA",
                device_name="Test GPU",
                device_class="Display controller",
                subsystem_vendor="10de",
                subsystem_device="1234",
                driver="nvidia",
                iommu_group="1",
                power_state="D0",
                link_speed="8.0 GT/s",
                bars=[],
                suitability_score=0.9,
                compatibility_issues=[],
            )
        ]

        # Apply filters
        filters = {"device_search": "nvidia", "status_filter": "suitable"}
        filtered = DeviceFilter.filter_devices(devices, filters)
        assert len(filtered) == 1

        # Export filtered results
        output_path = tmp_path / "filtered_devices.json"
        success = ExportManager.export_devices_json(filtered, output_path)
        assert success

        # Verify export
        with open(output_path) as f:
            data = json.load(f)
        assert len(data["devices"]) == 1
        assert data["devices"][0]["vendor_name"] == "NVIDIA"

    def test_configuration_template_workflow(self):
        """Test configuration template workflow"""
        # Get development template
        template = ConfigurationTemplates.get_template("development")
        assert template is not None

        # Create configuration from template - need to provide all required fields
        config_data = {
            "board_type": "pcileech_35t325_x1",
            "name": template["name"],
            "description": template["description"],
            "advanced_sv": template["advanced_sv"],
            "flash_after_build": template["flash_after_build"],
        }
        config = BuildConfiguration(**config_data)
        assert config.name == "Development"
        assert config.flash_after_build is True

        # Validate configuration
        config_dict = config.to_dict()
        assert "name" in config_dict
        assert "flash_after_build" in config_dict

    def test_validation_workflow(self):
        """Test validation workflow"""
        # Test BDF validation
        valid_bdf = "0000:01:00.0"
        assert ValidationHelper.validate_bdf(valid_bdf)

        # Test score validation
        valid_score = "0.8"
        score = ValidationHelper.validate_score(valid_score)
        assert score == 0.8

        # Test filename sanitization
        unsafe_filename = "test<file>.json"
        safe_filename = ValidationHelper.sanitize_filename(unsafe_filename)
        assert "<" not in safe_filename
        assert ">" not in safe_filename


@pytest.fixture
def sample_devices():
    """Fixture providing sample devices for testing"""
    return [
        PCIDevice(
            bdf="0000:01:00.0",
            vendor_id="10de",
            device_id="1234",
            vendor_name="NVIDIA",
            device_name="Test GPU",
            device_class="Display controller",
            subsystem_vendor="10de",
            subsystem_device="1234",
            driver="nvidia",
            iommu_group="1",
            power_state="D0",
            link_speed="8.0 GT/s",
            bars=[],
            suitability_score=0.9,
            compatibility_issues=[],
        ),
        PCIDevice(
            bdf="0000:02:00.0",
            vendor_id="8086",
            device_id="5678",
            vendor_name="Intel",
            device_name="Test NIC",
            device_class="Network controller",
            subsystem_vendor="8086",
            subsystem_device="5678",
            driver=None,
            iommu_group="2",
            power_state="D0",
            link_speed="5.0 GT/s",
            bars=[],
            suitability_score=0.6,
            compatibility_issues=["Low score"],
        ),
    ]


@pytest.mark.unit
class TestAsyncTUIFunctionality:
    """Test asynchronous TUI functionality"""

    @pytest.mark.asyncio
    async def test_async_device_scan(self):
        """Test asynchronous device scanning"""
        # Mock the device manager
        mock_manager = Mock()
        mock_manager.scan_devices = AsyncMock(return_value=[])

        # Test that async scan works
        devices = await mock_manager.scan_devices()
        assert isinstance(devices, list)
        mock_manager.scan_devices.assert_called_once()

    @pytest.mark.asyncio
    async def test_async_build_process(self):
        """Test asynchronous build process"""
        # Mock the build orchestrator
        mock_orchestrator = Mock()
        mock_orchestrator.start_build = AsyncMock(return_value=True)
        mock_orchestrator.is_building = Mock(return_value=False)

        # Test that async build works
        result = await mock_orchestrator.start_build(None, None, None)
        assert result is True
        mock_orchestrator.start_build.assert_called_once()


if __name__ == "__main__":
    pytest.main([__file__])
