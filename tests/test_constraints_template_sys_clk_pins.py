#!/usr/bin/env python3
"""
Unit tests for sys_clk differential pin constraint template functionality.

Tests the UCIO-1 fix that adds conditional sys_clk_p/sys_clk_n pin assignments
to the constraints.j2 template to prevent unconstrained logical port DRC errors.
"""

import pytest

from src.string_utils import generate_tcl_header_comment
from src.templating.template_renderer import TemplateRenderer
from src.utils.unified_context import TemplateObject


class TestConstraintsTemplateSysClkPins:
    """Test sys_clk differential pin constraint generation."""

    @pytest.fixture
    def base_context(self):
        """Provide base context required by constraints template."""
        return {
            "device": TemplateObject({
                "vendor_id": "1912",
                "device_id": "0014", 
                "revision_id": "03",
                "class_code": "0c0330",
            }),
            "board": TemplateObject({
                "name": "test_board",
                "fpga_part": "xc7a200tfbg484-2"
            }),
            "header": generate_tcl_header_comment("TCL Constraints"),
            "pcie_refclk_freq": 0,
            "pcie_userclk1_freq": 2,
            "pcie_userclk2_freq": 2,
        }

    @pytest.fixture
    def single_ended_context(self):
        """Provide context for single-ended clock board."""
        return {
            "device": TemplateObject({
                "vendor_id": "1912",
                "device_id": "0014",
                "revision_id": "03",
                "class_code": "0c0330",
            }),
            "board": TemplateObject({
                "name": "pcileech_screamer_m2",
                "fpga_part": "xc7a35tcsg325-2",
                "clk_pin": "R2",
                "pcie_refclk_loc": "IBUFDS_GTE2_X0Y0",
            }),
            "header": generate_tcl_header_comment("TCL Constraints"),
            "pcie_refclk_freq": 0,
            "pcie_userclk1_freq": 2,
            "pcie_userclk2_freq": 2,
        }

    def test_template_renders_sys_clk_pins_when_provided(self, base_context):
        """Test that template generates sys_clk pin assignments when pins are provided."""
        renderer = TemplateRenderer()
        
        # Add differential clock pins to board object (where template expects them)
        context = base_context.copy()
        context["board"] = TemplateObject({
            "name": "test_board",
            "fpga_part": "xc7a200tfbg484-2",
            "sys_clk_p_pin": "R3",
            "sys_clk_n_pin": "P3",
        })
        
        output = renderer.render_template("tcl/constraints.j2", context)
        
        # Should contain PACKAGE_PIN assignments for differential clock pins
        assert "set_property PACKAGE_PIN R3 [get_ports sys_clk_p]" in output
        assert "set_property PACKAGE_PIN P3 [get_ports sys_clk_n]" in output

    def test_template_omits_sys_clk_pins_when_not_provided(self):
        """Test that template omits sys_clk pin constraints when no pins provided."""
        # Create board without sys_clk pins
        board = TemplateObject({
            "name": "test_board",
            "fpga_part": "xc7a35tfgg484-2"
        })
        
        context = {
            "board": board,
            "device": {"vendor_id": "0x10de", "device_id": "0x1234"},
            "header": "# Test Header"
        }
        
        renderer = TemplateRenderer()
        output = renderer.render_template("tcl/constraints.j2", context)
        
        # Should not contain sys_clk PACKAGE_PIN assignments (but timing constraints are always present)
        lines_with_package_pin = [line for line in output.split('\n') if 'set_property PACKAGE_PIN' in line]
        sys_clk_package_pin_lines = [line for line in lines_with_package_pin if 'sys_clk' in line]
        
        assert len(sys_clk_package_pin_lines) == 0, \
            f"Should not have sys_clk PACKAGE_PIN constraints, but found: {sys_clk_package_pin_lines}"
        
        # Should still contain timing constraints for sys_clk_p (required for PCIe core)
        assert "create_clock" in output
        assert "sys_clk_p" in output  # timing constraint always present

    def test_template_includes_user_guidance(self, base_context):
        """Test that template includes helpful user guidance about clock types."""
        renderer = TemplateRenderer()
        
        context = base_context.copy()
        # Test with no clock pins to get the warning message
        context["board"] = TemplateObject({
            "name": "test_board_no_clk",
            "fpga_part": "xc7a35t",
            # No clock pins provided
        })
        
        output = renderer.render_template("tcl/constraints.j2", context)
        
        # Should contain explanatory warning about clock configuration options
        assert "Differential clock: sys_clk_p_pin + sys_clk_n_pin" in output
        assert "Single-ended clock: clk_pin" in output
        assert "most PCILeech boards" in output

    def test_template_handles_partial_sys_clk_pin_specification(self, base_context):
        """Test template behavior when only one sys_clk pin is specified."""
        renderer = TemplateRenderer()
        
        # Test with only sys_clk_p_pin (missing sys_clk_n_pin)
        context = base_context.copy()
        context["board"] = TemplateObject({
            "name": "test_board",
            "fpga_part": "xc7a200tfbg484-2",
            "sys_clk_p_pin": "R3",
            # Missing sys_clk_n_pin
        })
        
        output = renderer.render_template("tcl/constraints.j2", context)
        
        # Should NOT generate pin assignments if both pins aren't provided
        assert "set_property PACKAGE_PIN R3 [get_ports sys_clk_p]" not in output

    def test_template_handles_empty_sys_clk_pins(self, base_context):
        """Test template behavior when sys_clk pins are empty strings."""
        renderer = TemplateRenderer()
        
        context = base_context.copy()
        context["board"] = TemplateObject({
            "name": "test_board",
            "fpga_part": "xc7a200tfbg484-2",
            "sys_clk_p_pin": "",
            "sys_clk_n_pin": "",
        })
        
        output = renderer.render_template("tcl/constraints.j2", context)
        
        # Should NOT generate pin assignments for empty pins
        assert "set_property PACKAGE_PIN  [get_ports sys_clk_p]" not in output

    def test_template_sys_clk_pins_different_boards(self, base_context):
        """Test sys_clk pin generation for different board types."""
        renderer = TemplateRenderer()
        
        # Test AC701 board pins
        ac701_context = base_context.copy()
        ac701_context["board"] = TemplateObject({
            "name": "ac701", 
            "fpga_part": "xc7a200tfbg484-2",
            "sys_clk_p_pin": "R3",
            "sys_clk_n_pin": "P3",
        })
        
        ac701_output = renderer.render_template("tcl/constraints.j2", ac701_context)
        assert "set_property PACKAGE_PIN R3 [get_ports sys_clk_p]" in ac701_output
        assert "set_property PACKAGE_PIN P3 [get_ports sys_clk_n]" in ac701_output
        
        # Test Acorn board pins
        acorn_context = base_context.copy()
        acorn_context["board"] = TemplateObject({
            "name": "acorn", 
            "fpga_part": "xc7a35tfgg484-2",
            "sys_clk_p_pin": "J19",
            "sys_clk_n_pin": "H19",
        })
        
        acorn_output = renderer.render_template("tcl/constraints.j2", acorn_context)
        assert "set_property PACKAGE_PIN J19 [get_ports sys_clk_p]" in acorn_output
        assert "set_property PACKAGE_PIN H19 [get_ports sys_clk_n]" in acorn_output
        
        # Test SP605 board pins
        sp605_context = base_context.copy()
        sp605_context["board"] = TemplateObject({
            "name": "sp605", 
            "fpga_part": "xc6slx45tfgg484-3",
            "sys_clk_p_pin": "M6",
            "sys_clk_n_pin": "L6",
        })
        
        sp605_output = renderer.render_template("tcl/constraints.j2", sp605_context)
        assert "set_property PACKAGE_PIN M6 [get_ports sys_clk_p]" in sp605_output
        assert "set_property PACKAGE_PIN L6 [get_ports sys_clk_n]" in sp605_output

    def test_single_ended_clock_boards(self, single_ended_context):
        """Test single-ended clock constraint generation for PCILeech boards."""
        renderer = TemplateRenderer()
        
        # Test ScreamerM2 board with single-ended clock
        output = renderer.render_template("tcl/constraints.j2", single_ended_context)
        
        # Should contain single-ended clock pin assignment
        assert "set_property PACKAGE_PIN R2 [get_ports clk]" in output
        assert "set_property IOSTANDARD LVCMOS33 [get_ports clk]" in output
        
        # Should NOT contain differential clock pin assignments
        assert "set_property PACKAGE_PIN" in output and "[get_ports sys_clk_p]" not in output
        assert "set_property PACKAGE_PIN" in output and "[get_ports sys_clk_n]" not in output
        
        # Should contain timing constraint for single-ended clock
        assert "create_clock" in output
        assert "net_clk" in output
        assert "[get_ports clk]" in output

    def test_different_single_ended_boards(self, base_context):
        """Test single-ended clock pins for various PCILeech boards."""
        renderer = TemplateRenderer()
        
        # Test EnigmaX1 (J19)
        enigma_context = base_context.copy()
        enigma_context["board"] = TemplateObject({
            "name": "pcileech_enigma_x1",
            "fpga_part": "xc7a75tfgg484-2",
            "clk_pin": "J19",
        })
        enigma_output = renderer.render_template("tcl/constraints.j2", enigma_context)
        assert "set_property PACKAGE_PIN J19 [get_ports clk]" in enigma_output
        
        # Test PCIeScreamer (R4)
        screamer_context = base_context.copy()
        screamer_context["board"] = TemplateObject({
            "name": "pcileech_pciescreamer",
            "fpga_part": "xc7a35tfgg484-2",
            "clk_pin": "R4",
        })
        screamer_output = renderer.render_template("tcl/constraints.j2", screamer_context)
        assert "set_property PACKAGE_PIN R4 [get_ports clk]" in screamer_output
        
        # Test CaptainDMA 35T (H4)
        captain_context = base_context.copy()
        captain_context["board"] = TemplateObject({
            "name": "pcileech_35t484_x1",
            "fpga_part": "xc7a35tfgg484-2",
            "clk_pin": "H4",
        })
        captain_output = renderer.render_template("tcl/constraints.j2", captain_context)
        assert "set_property PACKAGE_PIN H4 [get_ports clk]" in captain_output


class TestConstraintsTemplateIntegration:
    """Integration tests for sys_clk pin constraints with full template context."""

    @pytest.fixture
    def full_differential_context(self):
        """Provide full context for differential clock board."""
        return {
            "device": TemplateObject({
                "vendor_id": "1912",
                "device_id": "0014",
                "revision_id": "03", 
                "class_code": "0c0330",
            }),
            "board": TemplateObject({
                "name": "ac701",
                "fpga_part": "xc7a200tfbg484-2",
                "fpga_family": "7series",
                "sys_clk_p_pin": "R3",
                "sys_clk_n_pin": "P3",
                "pcie_refclk_loc": "IBUFDS_GTE2_X0Y1",
            }),
            "header": generate_tcl_header_comment("TCL Constraints"),
            "pcie_refclk_freq": 0,
            "pcie_userclk1_freq": 2,
            "pcie_userclk2_freq": 2,
        }

    @pytest.fixture
    def full_single_ended_context(self):
        """Provide full context for single-ended clock board."""
        return {
            "device": TemplateObject({
                "vendor_id": "1912",
                "device_id": "0014",
                "revision_id": "03",
                "class_code": "0c0330",
            }),
            "board": TemplateObject({
                "name": "pcileech_100t484_x1",
                "fpga_part": "xc7a100tfgg484-1",
                "fpga_family": "7series",
                "clk_pin": "R4",  # Single-ended clock
                "pcie_refclk_loc": "IBUFDS_GTE2_X0Y1",
            }),
            "header": generate_tcl_header_comment("TCL Constraints"),
            "pcie_refclk_freq": 0,
            "pcie_userclk1_freq": 2,
            "pcie_userclk2_freq": 2,
        }

    def test_full_differential_template_rendering(self, full_differential_context):
        """Test complete template rendering for differential clock board."""
        renderer = TemplateRenderer()
        
        output = renderer.render_template("tcl/constraints.j2", full_differential_context)
        
        # Should be valid TCL output
        assert isinstance(output, str)
        assert len(output) > 100  # Should be substantial content
        
        # Should contain differential clock constraints (PACKAGE_PIN only, no IOSTANDARD in template)
        assert "set_property PACKAGE_PIN R3 [get_ports sys_clk_p]" in output
        assert "set_property PACKAGE_PIN P3 [get_ports sys_clk_n]" in output
        
        # Should contain standard template sections
        assert "Adding constraint files" in output
        assert "Generated device constraints file" in output

    def test_full_single_ended_template_rendering(self, full_single_ended_context):
        """Test complete template rendering for single-ended clock board."""
        renderer = TemplateRenderer()
        
        output = renderer.render_template("tcl/constraints.j2", full_single_ended_context)
        
        # Should be valid TCL output
        assert isinstance(output, str)
        assert len(output) > 100  # Should be substantial content
        
        # Should NOT contain sys_clk PACKAGE_PIN constraints (but other constraints are ok)
        lines_with_package_pin = [line for line in output.split('\n') if 'set_property PACKAGE_PIN' in line]
        sys_clk_package_pin_lines = [line for line in lines_with_package_pin if 'sys_clk' in line]
        
        assert len(sys_clk_package_pin_lines) == 0, \
            f"Single-ended board should not have sys_clk PACKAGE_PIN constraints, found: {sys_clk_package_pin_lines}"
        
        # Should contain standard template sections
        assert "Adding constraint files" in output
        assert "Generated device constraints file" in output

    def test_template_backwards_compatibility(self, full_single_ended_context):
        """Test that template changes don't break existing single-ended clock boards."""
        renderer = TemplateRenderer()
        
        # Remove any sys_clk related keys to simulate old context
        old_style_context = full_single_ended_context.copy()
        old_style_context.pop("sys_clk_p_pin", None)
        old_style_context.pop("sys_clk_n_pin", None)
        
        # Should render without errors
        output = renderer.render_template("tcl/constraints.j2", old_style_context)
        
        assert isinstance(output, str)
        assert len(output) > 100
        
        # Should not contain sys_clk PACKAGE_PIN assignments
        lines_with_package_pin = [line for line in output.split('\n') if 'set_property PACKAGE_PIN' in line]
        sys_clk_package_pin_lines = [line for line in lines_with_package_pin if 'sys_clk' in line]
        
        assert len(sys_clk_package_pin_lines) == 0, \
            f"Backwards compatibility test should not have sys_clk PACKAGE_PIN constraints, found: {sys_clk_package_pin_lines}"


class TestConstraintsTemplateUCIO1Fix:
    """Test that the template fixes the specific UCIO-1 DRC error."""

    def test_ucio1_fix_generates_package_pin_constraints(self):
        """Test that the fix generates the specific PACKAGE_PIN constraints needed for UCIO-1."""
        renderer = TemplateRenderer()
        
        context = {
            "device": TemplateObject({
                "vendor_id": "1912",
                "device_id": "0014",
                "revision_id": "03",
                "class_code": "0c0330",
            }),
            "board": TemplateObject({
                "name": "ac701",
                "fpga_part": "xc7a200tfbg484-2",
                "sys_clk_p_pin": "R3",
                "sys_clk_n_pin": "P3",
            }),
            "header": generate_tcl_header_comment("TCL Constraints"),
            "pcie_refclk_freq": 0,
            "pcie_userclk1_freq": 2,
            "pcie_userclk2_freq": 2,
        }
        
        output = renderer.render_template("tcl/constraints.j2", context)
        
        # The UCIO-1 error specifically mentioned unconstrained sys_clk_n and sys_clk_p
        # Our fix should generate PACKAGE_PIN constraints for these exact ports
        
        # Check for the exact constraints that resolve UCIO-1
        expected_constraints = [
            "set_property PACKAGE_PIN R3 [get_ports sys_clk_p]",
            "set_property PACKAGE_PIN P3 [get_ports sys_clk_n]",
        ]
        
        for constraint in expected_constraints:
            assert constraint in output, f"Missing UCIO-1 fix constraint: {constraint}"

    def test_ucio1_fix_does_not_affect_single_ended_boards(self):
        """Test that the UCIO-1 fix doesn't impact boards that don't have sys_clk_p/sys_clk_n."""
        renderer = TemplateRenderer()
        
        # Single-ended clock board context (no sys_clk differential pins)
        context = {
            "device": TemplateObject({
                "vendor_id": "1912", 
                "device_id": "0014",
                "revision_id": "03",
                "class_code": "0c0330",
            }),
            "board": TemplateObject({
                "name": "pcileech_100t484_x1",
                "fpga_part": "xc7a100tfgg484-1",
                "clk_pin": "R4",  # Single-ended clock only
            }),
            "header": generate_tcl_header_comment("TCL Constraints"),
            "pcie_refclk_freq": 0,
            "pcie_userclk1_freq": 2,
            "pcie_userclk2_freq": 2,
        }
        
        output = renderer.render_template("tcl/constraints.j2", context)
        
        # Should NOT contain any sys_clk pin PACKAGE_PIN assignments
        # The template may still contain references to sys_clk_p in timing constraints (which is expected)
        # We specifically check that no PACKAGE_PIN assignments are made for sys_clk pins
        lines_with_package_pin = [line for line in output.split('\n') if 'set_property PACKAGE_PIN' in line]
        sys_clk_package_pin_lines = [line for line in lines_with_package_pin if 'sys_clk' in line]
        
        assert len(sys_clk_package_pin_lines) == 0, \
            f"Single-ended board should not have sys_clk PACKAGE_PIN constraints, but found: {sys_clk_package_pin_lines}"


class TestConstraintsTemplateResetAndPCIeRefclk:
    """Test reset pin and PCIe reference clock constraints."""

    @pytest.fixture
    def board_with_reset_context(self):
        """Context for board with explicit reset pin."""
        return {
            "device": TemplateObject({
                "vendor_id": "1912",
                "device_id": "0014",
                "revision_id": "03",
                "class_code": "0c0330",
            }),
            "board": TemplateObject({
                "name": "pcileech_screamer_m2",
                "fpga_part": "xc7a35tcsg325-2",
                "clk_pin": "R2",
                "sys_rst_n_pin": "M1",
                "pcie_refclk_loc": "IBUFDS_GTE2_X0Y0",
            }),
            "header": generate_tcl_header_comment("TCL Constraints"),
        }

    def test_reset_pin_constraint_generation(self, board_with_reset_context):
        """Test that reset pin constraint is generated when sys_rst_n_pin is provided."""
        renderer = TemplateRenderer()
        
        output = renderer.render_template("tcl/constraints.j2", board_with_reset_context)
        
        # Should contain reset pin assignment
        assert "set_property PACKAGE_PIN M1 [get_ports sys_rst_n]" in output
        assert "set_property IOSTANDARD LVCMOS33 [get_ports sys_rst_n]" in output
        assert "set_property PULLUP TRUE [get_ports sys_rst_n]" in output

    def test_reset_timing_uses_false_path(self, board_with_reset_context):
        """Test that reset uses set_false_path instead of set_input_delay."""
        renderer = TemplateRenderer()
        
        output = renderer.render_template("tcl/constraints.j2", board_with_reset_context)
        
        # Should use false path for async reset
        assert "set_false_path -from [get_ports sys_rst_n]" in output
        
        # Should NOT use input_delay (old incorrect approach)
        assert "set_input_delay" not in output or "sys_rst_n" not in output

    def test_pcie_refclk_timing_constraint(self, board_with_reset_context):
        """Test that PCIe reference clock timing constraint is generated."""
        renderer = TemplateRenderer()
        
        output = renderer.render_template("tcl/constraints.j2", board_with_reset_context)
        
        # Should contain PCIe refclk timing constraint
        assert "create_clock -name pcie_refclk_p -period" in output
        assert "[get_nets pcie_clk_p]" in output

    def test_single_ended_clock_timing(self):
        """Test timing constraints for single-ended clock boards."""
        renderer = TemplateRenderer()
        
        context = {
            "device": TemplateObject({
                "vendor_id": "1912",
                "device_id": "0014",
            }),
            "board": TemplateObject({
                "name": "test_board",
                "fpga_part": "xc7a35t",
                "clk_pin": "R2",
            }),
            "header": generate_tcl_header_comment("Test"),
        }
        
        output = renderer.render_template("tcl/constraints.j2", context)
        
        # Should create clock on single-ended 'clk' port
        assert "create_clock" in output
        assert "net_clk" in output
        assert "[get_ports clk]" in output

    def test_differential_clock_timing(self):
        """Test timing constraints for differential clock boards."""
        renderer = TemplateRenderer()
        
        context = {
            "device": TemplateObject({
                "vendor_id": "1912",
                "device_id": "0014",
            }),
            "board": TemplateObject({
                "name": "ac701",
                "fpga_part": "xc7a200t",
                "sys_clk_p_pin": "R3",
                "sys_clk_n_pin": "P3",
            }),
            "header": generate_tcl_header_comment("Test"),
        }
        
        output = renderer.render_template("tcl/constraints.j2", context)
        
        # Should create clock on differential 'sys_clk_p' port
        assert "create_clock" in output
        assert "sys_clk_p" in output
        assert "[get_ports sys_clk_p]" in output

    def test_board_without_reset_pin(self):
        """Test that boards without sys_rst_n_pin don't generate reset constraint."""
        renderer = TemplateRenderer()
        
        context = {
            "device": TemplateObject({
                "vendor_id": "1912",
                "device_id": "0014",
            }),
            "board": TemplateObject({
                "name": "test_board",
                "fpga_part": "xc7a35t",
                "clk_pin": "R2",
                # No sys_rst_n_pin
            }),
            "header": generate_tcl_header_comment("Test"),
        }
        
        output = renderer.render_template("tcl/constraints.j2", context)
        
        # Should NOT generate reset pin PACKAGE_PIN assignment
        reset_package_lines = [line for line in output.split('\n') 
                              if 'PACKAGE_PIN' in line and 'sys_rst_n' in line]
        assert len(reset_package_lines) == 0, \
            f"Should not have sys_rst_n PACKAGE_PIN when not configured, found: {reset_package_lines}"
        
        # But should still have false_path for reset (defensive timing)
        assert "set_false_path -from [get_ports sys_rst_n]" in output
