#!/usr/bin/env python3
"""
Test overlay architecture bug fixes for systemverilog_generator.py

Validates:
1. No import shadowing (imports twice produce same objects)
2. Legacy methods raise clear errors
3. VFIO error messages are actionable
4. MSI-X normalization works
5. active_device_config returns dict
"""

import pytest
from src.templating.systemverilog_generator import (
    SystemVerilogGenerator,
    SVConstants,
    SVTemplates,
    SVValidation,
    TemplateRenderError,
)


class TestImportConsistency:
    """Verify no import shadowing or conflicts."""

    def test_sv_constants_singleton(self):
        """Ensure SVConstants references are consistent."""
        from src.templating.systemverilog_generator import (
            SVConstants as SVConstants1,
        )
        from src.templating.systemverilog_generator import (
            SVConstants as SVConstants2,
        )

        # Same class object
        assert SVConstants1 is SVConstants2

        # Can access same constants
        assert hasattr(SVConstants1, "DEFAULT_MPS_BYTES")
        assert SVConstants1.DEFAULT_MPS_BYTES == SVConstants2.DEFAULT_MPS_BYTES

    def test_sv_templates_singleton(self):
        """Ensure SVTemplates references are consistent."""
        from src.templating.systemverilog_generator import (
            SVTemplates as SVTemplates1,
        )
        from src.templating.systemverilog_generator import (
            SVTemplates as SVTemplates2,
        )

        assert SVTemplates1 is SVTemplates2

    def test_sv_validation_accessible(self):
        """Ensure SVValidation is accessible and has expected attributes."""
        assert hasattr(SVValidation, "NO_DONOR_DEVICE_IDS_ERROR")
        assert isinstance(SVValidation.NO_DONOR_DEVICE_IDS_ERROR, str)


class TestLegacyMethodBlockers:
    """Verify legacy methods raise clear errors in overlay-only mode."""

    @pytest.fixture
    def generator(self):
        """Create generator instance."""
        return SystemVerilogGenerator(use_pcileech_primary=True)

    def test_generate_advanced_systemverilog_blocked(self, generator):
        """Legacy generate_advanced_systemverilog raises clear error."""
        with pytest.raises(TemplateRenderError) as exc_info:
            generator.generate_advanced_systemverilog([], None)

        error_msg = str(exc_info.value)
        assert "overlay-only mode" in error_msg.lower()
        assert ".coe" in error_msg.lower()
        assert "pcileech-fpga" in error_msg.lower()

    def test_extract_registers_blocked(self, generator):
        """Legacy _extract_pcileech_registers raises clear error."""
        with pytest.raises(TemplateRenderError) as exc_info:
            generator._extract_pcileech_registers(None)

        error_msg = str(exc_info.value)
        assert "overlay-only mode" in error_msg.lower()

    def test_generate_advanced_modules_blocked(self, generator):
        """Legacy _generate_pcileech_advanced_modules raises clear error."""
        with pytest.raises(TemplateRenderError) as exc_info:
            generator._generate_pcileech_advanced_modules({}, None)

        error_msg = str(exc_info.value)
        assert "overlay-only mode" in error_msg.lower()


class TestVFIOErrorMessages:
    """Verify VFIO errors include remediation guidance."""

    @pytest.fixture
    def generator(self):
        """Create generator instance."""
        return SystemVerilogGenerator(use_pcileech_primary=True)

    def test_vfio_error_includes_remediation(self, generator, monkeypatch):
        """VFIO error message includes environment variable hint."""
        # Mock environment to ensure VFIO check fails
        monkeypatch.setenv("PCILEECH_SKIP_VFIO_CHECK", "0")

        # Mock VFIO detection to return False
        def mock_detect_vfio():
            return False

        monkeypatch.setattr(generator, "_detect_vfio_environment", mock_detect_vfio)

        vfio_context = {
            "vfio_device": None,
            "vfio_binding_verified": False,
            "device_config": {},
        }

        with pytest.raises(TemplateRenderError) as exc_info:
            generator.generate_pcileech_integration_code(vfio_context)

        error_msg = str(exc_info.value)
        assert "PCILEECH_SKIP_VFIO_CHECK=1" in error_msg
        assert "/dev/vfio" in error_msg
        assert "local builds" in error_msg.lower()


class TestMSIXNormalization:
    """Verify MSI-X configuration key normalization."""

    @pytest.fixture
    def generator(self):
        """Create generator instance."""
        return SystemVerilogGenerator(use_pcileech_primary=True)

    def test_normalize_enabled_to_is_supported(self, generator):
        """Normalize 'enabled' to 'is_supported'."""
        context = {
            "device_signature": "10DE:1234:00",
            "msix_config": {"enabled": True, "vectors": 4},
            "device_config": {
                "vendor_id": "0x10de",
                "device_id": "0x1234",
            },
        }

        generator._normalize_msix_config(context)

        assert context["msix_config"]["is_supported"] is True
        assert context["msix_config"]["enabled"] is True

    def test_normalize_vectors_to_num_vectors(self, generator):
        """Normalize 'vectors' to 'num_vectors'."""
        context = {
            "device_signature": "10DE:1234:00",
            "msix_config": {"is_supported": True, "vectors": 8},
            "device_config": {
                "vendor_id": "0x10de",
                "device_id": "0x1234",
            },
        }

        generator._normalize_msix_config(context)

        assert context["msix_config"]["num_vectors"] == 8
        assert context["msix_config"]["vectors"] == 8

    def test_normalize_bidirectional(self, generator):
        """Both naming conventions work after normalization."""
        # Start with only 'is_supported' and 'num_vectors'
        context1 = {
            "msix_config": {"is_supported": False, "num_vectors": 0},
        }
        generator._normalize_msix_config(context1)
        assert context1["msix_config"]["enabled"] is False
        assert context1["msix_config"]["vectors"] == 0

        # Start with only 'enabled' and 'vectors'
        context2 = {
            "msix_config": {"enabled": True, "vectors": 16},
        }
        generator._normalize_msix_config(context2)
        assert context2["msix_config"]["is_supported"] is True
        assert context2["msix_config"]["num_vectors"] == 16


class TestActiveDeviceConfigNormalization:
    """Verify active_device_config returns dict, not TemplateObject."""

    @pytest.fixture
    def generator(self):
        """Create generator instance."""
        return SystemVerilogGenerator(use_pcileech_primary=True)

    def test_active_device_config_is_dict(self, generator):
        """_create_default_active_device_config returns dict."""
        context = {
            "device_signature": "10DE:1234:00",
            "device_config": {
                "vendor_id": "0x10de",
                "device_id": "0x1234",
            },
            "config_space": {},
        }

        result = generator._create_default_active_device_config(context)

        # Must be a dict, not TemplateObject
        assert isinstance(result, dict)
        assert "vendor_id" in result
        assert "device_id" in result

    def test_active_device_config_fails_without_ids(self, generator):
        """Fail fast when vendor_id/device_id are missing."""
        context = {
            "device_signature": "0000:0000:00",
            "device_config": {},
            "config_space": {},
        }

        with pytest.raises(TemplateRenderError) as exc_info:
            generator._create_default_active_device_config(context)

        error_msg = str(exc_info.value)
        assert "vendor_id" in error_msg.lower()
        assert "device_id" in error_msg.lower()


class TestGenerateModulesFailFast:
    """Verify generate_modules fails fast on invalid config."""

    def test_generate_modules_requires_pcileech_primary(self):
        """Fail fast when use_pcileech_primary=False."""
        generator = SystemVerilogGenerator(use_pcileech_primary=False)

        # Use properly formatted device identifiers to pass initial validation
        context = {
            "device_signature": "10DE:1234:00",
            "device_config": {
                "vendor_id": "10DE",  # 4-char hex string (no 0x prefix)
                "device_id": "1234",  # 4-char hex string
                "subsystem_vendor_id": "10DE",
                "subsystem_device_id": "ABCD",
                "class_code": "020000",
                "revision_id": "00",
            },
        }

        with pytest.raises(TemplateRenderError) as exc_info:
            generator.generate_modules(context)

        error_msg = str(exc_info.value)
        assert "use_pcileech_primary=True" in error_msg


class TestContextSchemaStability:
    """Verify context schema is stable and documented."""

    @pytest.fixture
    def generator(self):
        """Create generator instance."""
        return SystemVerilogGenerator(use_pcileech_primary=True)

    def test_context_has_required_keys(self, generator):
        """Context contains all required top-level keys."""
        # This would normally be tested in integration tests
        # Here we just verify the normalization methods populate expected keys
        context = {
            "device_signature": "10DE:1234:00",
            "device_config": {
                "vendor_id": "0x10de",
                "device_id": "0x1234",
            },
        }

        # Apply all normalization steps
        generator._normalize_device_config(context)
        generator._normalize_msix_config(context)

        # Verify device_config is dict after normalization
        assert isinstance(context["device_config"], dict)
        assert "enable_advanced_features" in context["device_config"]
        assert "enable_perf_counters" in context["device_config"]


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
