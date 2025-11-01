#!/usr/bin/env python3
"""
Unit tests for the donor info template generator.
"""

import json
import tempfile
from pathlib import Path
from unittest.mock import patch

import pytest

from src.device_clone.donor_info_template import DonorInfoTemplateGenerator
from src.exceptions import DeviceConfigError, ValidationError


class TestDonorInfoTemplateGenerator:
    """Test cases for DonorInfoTemplateGenerator."""

    def test_generate_blank_template_structure(self):
        """Test that the generated template has the correct structure."""
        template = DonorInfoTemplateGenerator.generate_blank_template()

        # Check top-level keys
        expected_keys = {
            "metadata",
            "device_info",
            "behavioral_profile",
            "advanced_features",
            "emulation_hints",
            "extended_behavioral_data",
        }
        assert set(template.keys()) == expected_keys

    def test_metadata_section(self):
        """Test the metadata section of the template."""
        template = DonorInfoTemplateGenerator.generate_blank_template()
        metadata = template["metadata"]

        # Check metadata fields
        assert "generated_at" in metadata
        assert metadata["device_bdf"] == ""
        assert metadata["kernel_version"] == ""
        assert metadata["behavioral_data_included"] is True
        assert metadata["profile_capture_duration"] is None
        assert metadata["comments"] == ""

    def test_device_info_structure(self):
        """Test the device_info section structure."""
        template = DonorInfoTemplateGenerator.generate_blank_template()
        device_info = template["device_info"]

        # Check main subsections
        assert "identification" in device_info
        assert "capabilities" in device_info
        assert "bars" in device_info
        assert "power_management" in device_info
        assert "error_handling" in device_info

        # Check identification fields are blank/None
        ident = device_info["identification"]
        assert ident["vendor_id"] is None
        assert ident["device_id"] is None
        assert ident["device_name"] == ""
        assert ident["manufacturer"] == ""

    def test_behavioral_profile_structure(self):
        """Test the behavioral_profile section structure."""
        template = DonorInfoTemplateGenerator.generate_blank_template()
        profile = template["behavioral_profile"]

        # Check main subsections
        expected_sections = {
            "initialization",
            "runtime_behavior",
            "dma_behavior",
            "error_injection_response",
            "performance_profile",
        }
        assert set(profile.keys()) == expected_sections

    def test_bars_configuration(self):
        """Test that BARs are properly configured."""
        template = DonorInfoTemplateGenerator.generate_blank_template()
        bars = template["device_info"]["bars"]

        # Check that bars is a list
        assert isinstance(bars, list)
        assert len(bars) >= 1

        # Check first BAR structure
        bar0 = bars[0]
        assert bar0["bar_number"] == 0
        assert bar0["type"] == ""
        assert bar0["size"] is None
        assert bar0["prefetchable"] is None
        assert bar0["64bit"] is None
        assert bar0["purpose"] == ""

    def test_advanced_features_section(self):
        """Test the advanced_features section."""
        template = DonorInfoTemplateGenerator.generate_blank_template()
        advanced = template["advanced_features"]

        # Check subsections
        assert "custom_protocols" in advanced
        assert "security_features" in advanced
        assert "virtualization_support" in advanced
        assert "debug_features" in advanced
        assert "platform_specific" in advanced

        # Check virtualization fields
        virt = advanced["virtualization_support"]
        assert "vf_bar_layout" in virt
        assert "live_migration_support" in virt

    def test_save_template_pretty(self):
        """Test saving template with pretty formatting."""
        with tempfile.TemporaryDirectory() as tmpdir:
            output_path = Path(tmpdir) / "test_template.json"

            DonorInfoTemplateGenerator.save_template(output_path, pretty=True)

            # Check file exists
            assert output_path.exists()

            # Load and verify JSON is valid
            with open(output_path, "r") as f:
                loaded = json.load(f)

            # Check it has the expected structure
            assert "metadata" in loaded
            assert "device_info" in loaded

            # Check pretty formatting (should have newlines and indentation)
            with open(output_path, "r") as f:
                content = f.read()
            assert "\n" in content
            assert "  " in content  # Indentation

    def test_save_template_compact(self):
        """Test saving template without pretty formatting."""
        with tempfile.TemporaryDirectory() as tmpdir:
            output_path = Path(tmpdir) / "test_template_compact.json"

            DonorInfoTemplateGenerator.save_template(output_path, pretty=False)

            # Check file exists
            assert output_path.exists()

            # Load and verify JSON is valid
            with open(output_path, "r") as f:
                loaded = json.load(f)

            # Check it has the expected structure
            assert "metadata" in loaded
            assert "device_info" in loaded

    def test_runtime_behavior_section(self):
        """Test the runtime behavior section."""
        template = DonorInfoTemplateGenerator.generate_blank_template()
        runtime = template["behavioral_profile"]["runtime_behavior"]

        # Check subsections
        assert "interrupt_patterns" in runtime
        assert "memory_access_patterns" in runtime
        assert "timing_characteristics" in runtime
        assert "state_machine" in runtime

        # Check interrupt patterns
        interrupts = runtime["interrupt_patterns"]
        assert interrupts["type"] == ""
        assert interrupts["typical_rate_hz"] is None
        assert interrupts["coalescing_supported"] is None

    def test_dma_behavior_section(self):
        """Test DMA behavior configuration."""
        template = DonorInfoTemplateGenerator.generate_blank_template()
        dma = template["behavioral_profile"]["dma_behavior"]

        # Check DMA fields
        assert dma["supports_dma"] is None
        assert dma["dma_engine_count"] is None
        assert dma["scatter_gather_support"] is None

        # Check DMA direction patterns
        assert "dma_direction_patterns" in dma
        directions = dma["dma_direction_patterns"]
        assert directions["host_to_device"] is None
        assert directions["device_to_host"] is None
        assert directions["bidirectional"] is None

    def test_emulation_hints_section(self):
        """Test the emulation hints section."""
        template = DonorInfoTemplateGenerator.generate_blank_template()
        hints = template["emulation_hints"]

        # Check fields
        assert "critical_features" in hints
        assert "optional_features" in hints
        assert "performance_critical_paths" in hints
        assert "compatibility_quirks" in hints
        assert "recommended_optimizations" in hints
        assert "testing_recommendations" in hints

    def test_extended_behavioral_data_section(self):
        """Test the extended behavioral data section."""
        template = DonorInfoTemplateGenerator.generate_blank_template()
        extended = template["extended_behavioral_data"]

        # Check subsections
        assert "workload_profiles" in extended
        assert "state_transitions" in extended
        assert "error_recovery_sequences" in extended
        assert "performance_scaling" in extended
        assert "compatibility_matrix" in extended

    def test_template_completeness(self):
        """Test that the template is comprehensive and complete."""
        template = DonorInfoTemplateGenerator.generate_blank_template()

        # Convert to JSON string to check size (should be substantial)
        json_str = json.dumps(template)
        assert len(json_str) > 5000  # Template should be quite large

    def test_generate_template_with_comments(self):
        """Test generating template with inline comments."""

    template_str = DonorInfoTemplateGenerator.generate_template_with_comments()

    # Should be valid JSON and contain $comment guidance fields
    loaded = json.loads(template_str)
    assert "$comment" in loaded
    assert "$comment" in loaded.get("metadata", {})
    assert "$comment" in loaded.get("device_info", {})

    def test_validate_template_valid(self):
        """Test validating a valid template."""
        # Create a valid template
        template = DonorInfoTemplateGenerator.generate_blank_template()
        # Fill in required fields
        template["device_info"]["identification"]["vendor_id"] = "0x8086"
        template["device_info"]["identification"]["device_id"] = "0x10D3"

        # Validate it
        generator = DonorInfoTemplateGenerator()
        is_valid, errors = generator.validate_template(template)

        assert is_valid is True
        assert len(errors) == 0

    def test_validate_template_invalid(self):
        """Test validating an invalid template."""
        # Create an invalid template (missing required sections)
        template = {"metadata": {}}

        # Validate it
        generator = DonorInfoTemplateGenerator()
        is_valid, errors = generator.validate_template(template)

        assert is_valid is False
        assert len(errors) > 0
        assert any("device_info" in error for error in errors)

    def test_validate_template_file_valid(self):
        """Test validating a valid template file."""
        with tempfile.TemporaryDirectory() as tmpdir:
            # Create a valid template
            template = DonorInfoTemplateGenerator.generate_blank_template()
            # Fill in required fields
            template["device_info"]["identification"]["vendor_id"] = "0x8086"
            template["device_info"]["identification"]["device_id"] = "0x10D3"

            # Save it
            filepath = Path(tmpdir) / "valid_template.json"
            with open(filepath, "w") as f:
                json.dump(template, f)

            # Validate it
            generator = DonorInfoTemplateGenerator()
            is_valid, errors = generator.validate_template_file(str(filepath))

            assert is_valid is True
            assert len(errors) == 0

    def test_validate_template_file_invalid(self):
        """Test validating an invalid template file."""
        with tempfile.TemporaryDirectory() as tmpdir:
            # Create an invalid template (missing device IDs)
            template = DonorInfoTemplateGenerator.generate_blank_template()
            # Don't fill required fields

            # Save it
            filepath = Path(tmpdir) / "invalid_template.json"
            with open(filepath, "w") as f:
                json.dump(template, f)

            # Validate it
            generator = DonorInfoTemplateGenerator()
            is_valid, errors = generator.validate_template_file(str(filepath))

            assert is_valid is False
            assert len(errors) > 0
            # Should complain about missing vendor/device IDs
            assert any("vendor_id" in error for error in errors)

    def test_validate_template_file_malformed_json(self):
        """Test validating a malformed JSON file."""
        with tempfile.TemporaryDirectory() as tmpdir:
            filepath = Path(tmpdir) / "malformed.json"
            with open(filepath, "w") as f:
                f.write("{ invalid json }")

            generator = DonorInfoTemplateGenerator()

            # Should raise ValidationError
            with pytest.raises(ValidationError) as exc_info:
                generator.validate_template_file(str(filepath))

            assert "Invalid JSON" in str(exc_info.value)

    def test_validate_template_file_nonexistent(self):
        """Test validating a non-existent file."""
        generator = DonorInfoTemplateGenerator()

        # Should raise ValidationError
        with pytest.raises(ValidationError) as exc_info:
            generator.validate_template_file("/nonexistent/file.json")

        assert "not found" in str(exc_info.value)

    def test_generate_template_from_device_no_lspci(self):
        """Test generating template from device when lspci is not available."""
        generator = DonorInfoTemplateGenerator()

        # Mock subprocess.run to raise FileNotFoundError (lspci not found)
        with patch('subprocess.run', side_effect=FileNotFoundError("lspci not found")):
            # This should raise DeviceConfigError when lspci is not found
            with pytest.raises(DeviceConfigError) as exc_info:
                generator.generate_template_from_device("0000:00:00.0")

            assert "lspci" in str(exc_info.value)

    def test_save_template_dict(self):
        """Test saving a template dictionary."""
        with tempfile.TemporaryDirectory() as tmpdir:
            output_path = Path(tmpdir) / "test_dict.json"

            # Create a custom template
            template = {"test": "data", "nested": {"value": 123}}

            DonorInfoTemplateGenerator.save_template_dict(template, output_path)

            # Check file exists and contains correct data
            assert output_path.exists()
            with open(output_path, "r") as f:
                loaded = json.load(f)

            assert loaded == template

    def test_generate_minimal_template(self):
        """Test generating a minimal donor info template."""
        template = DonorInfoTemplateGenerator.generate_minimal_template()

        # Check basic structure
        assert "metadata" in template
        assert "device_info" in template

        # Check metadata
        metadata = template["metadata"]
        assert "generated_at" in metadata
        assert "device_bdf" in metadata
        assert "generator_version" in metadata
        assert metadata["template_type"] == "minimal"

        # Check device info - should have only essential fields
        device_info = template["device_info"]
        assert "identification" in device_info
        assert "capabilities" in device_info
        assert "bars" in device_info

        # Should NOT have advanced fields
        assert "behavioral_profile" not in template
        assert "advanced_features" not in template
        assert "emulation_hints" not in template
        assert "extended_behavioral_data" not in template

        # Check identification fields
        ident = device_info["identification"]
        assert "vendor_id" in ident
        assert "device_id" in ident
        assert "subsystem_vendor_id" in ident
        assert "subsystem_device_id" in ident
        assert "class_code" in ident
        assert "revision_id" in ident

        # Check capabilities - minimal set
        caps = device_info["capabilities"]
        assert "pcie_version" in caps
        assert "link_width" in caps
        assert "link_speed" in caps

        # Check BAR structure
        bars = device_info["bars"]
        assert isinstance(bars, list)
        assert len(bars) >= 1
        bar0 = bars[0]
        assert "bar_number" in bar0
        assert "type" in bar0
        assert "size" in bar0
        assert "prefetchable" in bar0
        assert "64bit" in bar0

    def test_load_template(self):
        """Test loading a donor template from file."""
        # Create a test template
        test_template = {
            "metadata": {
                "device_bdf": "0000:03:00.0",
                "generator_version": "test-v1.0",
            },
            "device_info": {
                "identification": {"vendor_id": 0x8086, "device_id": 0x10D3}
            },
        }

        with tempfile.TemporaryDirectory() as tmpdir:
            template_path = Path(tmpdir) / "test_template.json"

            # Save the template
            with open(template_path, "w") as f:
                json.dump(test_template, f)

            # Load it back
            loaded = DonorInfoTemplateGenerator.load_template(str(template_path))

            assert loaded == test_template

    def test_load_template_file_not_found(self):
        """Test loading a non-existent template file."""
        with pytest.raises(DeviceConfigError, match="Template file not found"):
            DonorInfoTemplateGenerator.load_template("/non/existent/file.json")

    def test_load_template_invalid_json(self):
        """Test loading an invalid JSON file."""
        with tempfile.TemporaryDirectory() as tmpdir:
            template_path = Path(tmpdir) / "invalid.json"

            # Write invalid JSON
            with open(template_path, "w") as f:
                f.write("{ invalid json }")

            with pytest.raises(
                DeviceConfigError, match="Invalid JSON in template file"
            ):
                DonorInfoTemplateGenerator.load_template(str(template_path))

    def test_merge_template_with_discovered(self):
        """Test merging template values with discovered values."""
        # Template data (with some null values to ignore)
        template = {
            "device_config": {
                "vendor_id": "0x8086",  # Override
                "device_id": None,  # Ignore (null)
                "custom_field": "custom_value",  # New field
            },
            "bar_config": {"bars": [{"index": 0, "size": 4096}]},  # Override
        }

        # Discovered data
        discovered = {
            "device_config": {
                "vendor_id": "0x10de",  # Will be overridden
                "device_id": "0x1234",  # Will be kept
                "class_code": "0x030000",  # Will be kept
            },
            "bar_config": {
                "bars": [
                    {"index": 0, "size": 2048},  # Will be overridden
                    {"index": 1, "size": 1024},  # Will be kept
                ]
            },
            "other_config": {"field": "value"},  # Will be kept
        }

        # Merge
        merged = DonorInfoTemplateGenerator.merge_template_with_discovered(
            template=template, discovered=discovered
        )

        # Verify results
        assert merged["device_config"]["vendor_id"] == "0x8086"  # From template
        assert (
            merged["device_config"]["device_id"] == "0x1234"
        )  # From discovered (null ignored)
        assert merged["device_config"]["class_code"] == "0x030000"  # From discovered
        assert (
            merged["device_config"]["custom_field"] == "custom_value"
        )  # From template

        assert merged["bar_config"]["bars"][0]["size"] == 4096  # From template
        assert len(merged["bar_config"]["bars"]) == 2  # Both bars preserved
        assert merged["bar_config"]["bars"][1]["size"] == 1024  # From discovered

        assert "other_config" in merged  # Discovered fields preserved
        assert merged["other_config"]["field"] == "value"

    def test_merge_template_with_discovered_null_handling(self):
        """Test that null values in template are properly ignored."""
        template = {
            "config": {
                "field1": "value1",
                "field2": None,
                "field3": "",
                "field4": 0,
                "field5": False,
                "nested": {"inner1": "inner_value", "inner2": None},
            }
        }

        discovered = {
            "config": {
                "field1": "original1",
                "field2": "original2",
                "field3": "original3",
                "field4": 100,
                "field5": True,
                "nested": {"inner1": "original_inner1", "inner2": "original_inner2"},
            }
        }

        merged = DonorInfoTemplateGenerator.merge_template_with_discovered(
            template=template, discovered=discovered
        )

        # Check that non-null values override
        assert merged["config"]["field1"] == "value1"
        assert merged["config"]["field3"] == ""  # Empty string is not null
        assert merged["config"]["field4"] == 0  # Zero is not null
        assert merged["config"]["field5"] is False  # False is not null

        # Check that null values are ignored
        assert merged["config"]["field2"] == "original2"
        assert merged["config"]["nested"]["inner1"] == "inner_value"
        assert merged["config"]["nested"]["inner2"] == "original_inner2"


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
