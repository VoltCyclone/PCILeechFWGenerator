"""
Test config-only architecture for PCILeechFWGenerator.

This validates that:
1. Only configuration modules are generated (no HDL logic)
2. PCILeech sources are properly copied from repository
3. TCL scripts set PCILeech's top module (not our wrapper)
4. Generated device_config.sv contains only localparams
"""

import pytest
from pathlib import Path
from unittest.mock import Mock, patch, MagicMock

from src.templating.systemverilog_generator import SystemVerilogGenerator
from src.templating.sv_module_generator import SVModuleGenerator
from src.templating.sv_validator import SVValidator
from src.device_clone.pcileech_generator import PCILeechGenerator, PCILeechGenerationConfig


class TestConfigOnlyArchitecture:
    """Test that we follow config-only architecture principles."""

    def test_only_config_modules_generated(self):
        """Verify only configuration modules are generated, no HDL logic."""
        generator = SystemVerilogGenerator()
        
        # Create minimal valid context
        context = {
            "device_config": {
                "vendor_id": "10DE",
                "device_id": "1234",
                "class_code": "030000",
                "revision_id": "A1"
            },
            "device": {
                "vendor_id": "10DE", 
                "device_id": "1234"
            },
            "config_space": {},
            "bars": [],
            "header": "// Test header",
            "device_signature": "10DE:1234:A1"
        }
        
        # Generate modules
        modules = generator.generate_modules(context)
        
        # Should only generate device_config and COE file
        assert "device_config" in modules
        assert "pcileech_cfgspace.coe" in modules
        
        # Should NOT generate any HDL logic modules
        hdl_modules = [
            "top_level_wrapper",
            "bar_controller", 
            "tlp_processor",
            "state_machine",
            "pcie_core_wrapper"
        ]
        for hdl_module in hdl_modules:
            assert hdl_module not in modules, f"Found HDL module {hdl_module} - violates config-only architecture"
            
    def test_device_config_contains_only_localparams(self):
        """Verify device_config.sv contains only localparams, no logic."""
        generator = SystemVerilogGenerator()
        
        context = {
            "device_config": {
                "vendor_id": "10DE",
                "device_id": "1234", 
                "class_code": "030000",
                "revision_id": "A1"
            },
            "device": {
                "vendor_id": "10DE",
                "device_id": "1234"
            },
            "config_space": {"revision_id": "A1"},
            "bars": [{"size": 0x1000000}, {"size": 0x2000000}],
            "header": "// Test header",
            "device_signature": "10DE:1234:A1"
        }
        
        modules = generator.generate_modules(context)
        device_config = modules.get("device_config", "")
        
        # Check it contains localparams
        assert "localparam" in device_config
        assert "VENDOR_ID = 16'h10DE" in device_config
        assert "DEVICE_ID = 16'h1234" in device_config
        
        # Check it does NOT contain logic constructs
        logic_keywords = [
            "always", "always_ff", "always_comb",
            "assign cfg_", "output logic", "input logic",
            "posedge", "negedge", "if (", "case ("
        ]
        for keyword in logic_keywords:
            assert keyword not in device_config, f"Found logic keyword '{keyword}' - violates config-only architecture"
            
    @patch('src.file_management.file_manager.FileManager.copy_pcileech_sources')
    def test_pcileech_sources_are_copied(self, mock_copy):
        """Verify PCILeech HDL sources are copied from repository."""
        # Set up mock to return expected PCILeech files
        mock_copy.return_value = {
            "systemverilog": [
                "pcileech_pcie_a7.sv",
                "pcileech_tlps128.sv", 
                "pcileech_bar_controller.sv"
            ],
            "verilog": [],
            "constraints": ["pcileech_75t.xdc"]
        }
        
        config = PCILeechGenerationConfig(
            device_bdf="0000:03:00.0",
            output_dir=Path("/tmp/test_output"),
            fpga_board="75T"
        )
        
        generator = PCILeechGenerator(config)
        
        # Generate and save firmware
        with patch.object(generator, '_analyze_configuration_space') as mock_analyze:
            mock_analyze.return_value = {
                "vendor_id": "10DE",
                "device_id": "1234",
                "bars": []
            }
            
            result = generator.generate_pcileech_firmware()
            
            # Save to disk (this should trigger PCILeech source copy)
            with patch('pathlib.Path.mkdir'), \
                 patch('pathlib.Path.write_text'):
                generator.save_generated_firmware(result)
                
        # Verify PCILeech sources were copied
        mock_copy.assert_called_once()
        
    def test_tcl_sets_pcileech_top_module(self):
        """Verify TCL scripts set PCILeech's top module, not our wrapper."""
        from src.templating.template_renderer import TemplateRenderer
        
        renderer = TemplateRenderer()
        
        # Test context for 75T board
        context = {
            "board": {"name": "75T"},
            "pcileech": {"src_dir": "src"},
            "header_comment": "// Test"
        }
        
        # Render the sources TCL template
        tcl_content = renderer.render_template("tcl/pcileech_sources.j2", context)
        
        # Check it sets PCILeech's top module
        assert "set_property top pcileech_75t_top" in tcl_content
        
        # Should NOT set our wrapper as top
        assert "set_property top top_level_wrapper" not in tcl_content
        assert "set_property top our_wrapper" not in tcl_content
        
    def test_sv_validator_checks_pcileech_sources(self):
        """Verify validator can check for PCILeech source presence."""
        validator = SVValidator(Mock())
        
        with patch('pathlib.Path.exists') as mock_exists:
            # Simulate PCILeech sources present
            def exists_side_effect(self):
                path_str = str(self)
                if path_str.endswith('/src'):
                    return True
                if any(path_str.endswith(f) for f in [
                    'pcileech_pcie_a7.sv',
                    'pcileech_tlps128.sv', 
                    'pcileech_bar_controller.sv'
                ]):
                    return True
                return False
                
            mock_exists.side_effect = exists_side_effect
            
            # Should validate successfully
            assert validator.validate_pcileech_sources("/tmp/output") is True
            
        with patch('pathlib.Path.exists', return_value=False):
            # Should fail when sources missing
            assert validator.validate_pcileech_sources("/tmp/output") is False
            
    def test_no_wrapper_generation(self):
        """Verify no top-level wrapper is generated."""
        generator = SystemVerilogGenerator()
        
        # Check that wrapper generation methods have been removed
        # or that they don't generate HDL
        assert not hasattr(generator, 'generate_top_level_wrapper')
        
        # If the method still exists for compatibility, it should not generate HDL
        if hasattr(generator.module_generator, '_generate_wrapper'):
            with pytest.raises(NotImplementedError):
                generator.module_generator._generate_wrapper({})


class TestMigrationFromOldArchitecture:
    """Test that old HDL generation patterns are no longer present."""
    
    def test_no_hdl_templates_remain(self):
        """Verify problematic HDL templates have been removed."""
        from pathlib import Path
        
        template_dir = Path("src/templates/sv")
        if not template_dir.exists():
            pytest.skip("Template directory not found")
            
        # List of templates that should NOT exist
        removed_templates = [
            "top_level_wrapper.sv.j2",
            "bar_controller.sv.j2",
            "advanced_controller.sv.j2",
            "tlp_processor.sv.j2",
            "state_machine.sv.j2",
            "clock_crossing.sv.j2",
            "msix_implementation.sv.j2"
        ]
        
        for template in removed_templates:
            template_path = template_dir / template
            assert not template_path.exists(), f"Old HDL template {template} still exists"
            
        # Verify only config templates remain
        remaining = list(template_dir.glob("*.sv.j2"))
        allowed = ["device_config.sv.j2", "pcileech_header.svh.j2"]
        
        for template_path in remaining:
            assert template_path.name in allowed or template_path.name.endswith(".coe.j2"), \
                f"Unexpected template found: {template_path.name}"