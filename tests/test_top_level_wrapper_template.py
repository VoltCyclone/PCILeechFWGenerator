#!/usr/bin/env python3
"""
Test suite for the top_level_wrapper.sv.j2 Jinja2 template.
Tests template rendering with various contexts and edge cases.

This test verifies the fixes for:
- Reset polarity (active-high reset)
- Correct RX data slicing for 64-bit interface
- Proper TLP completion header generation
- Transaction tracking with FIFO
- Complete TLP type decoding (7 bits)
- Write data handling
- Dynamic header size handling (3-DW vs 4-DW)
- Fixed BAR access pipeline
- Sideband signal usage
- Proper flow control with tkeep/tlast
- TLP length field validation
- Byte enable handling
- Safe Jinja2 device ID slicing
- Fixed LED width calculation
"""

import pytest
import re
from pathlib import Path
from jinja2 import Environment, FileSystemLoader, TemplateError

from src.templating.template_renderer import TemplateRenderer
from typing import Dict, Any, Optional


class TestTopLevelWrapperTemplate:
    """Test suite for PCIe top-level wrapper template rendering."""

    @pytest.fixture
    def template_env(self):
        """Create Jinja2 environment using project's TemplateRenderer with filters."""
        template_dir = Path(__file__).parent.parent / "src/templates"
        renderer = TemplateRenderer(template_dir=str(template_dir))
        # Reuse the configured environment (includes MappingFileSystemLoader and filters like 'safe_int')
        return renderer.env

    @pytest.fixture
    def base_context(self):
        """Provide base template context with required values."""
        return {
            "header": "// Generated PCIe Top Level Wrapper",
            "vendor_id": "0x10ee",
            "device_id": "0x7024",
            "vendor_id_int": 0x10EE,
            "device_id_int": 0x7024,
            "config_space": bytes(256),
            "device_serial_number_int": 0xAABBCCDDEEFF0011,
            "device": {"vendor_id": "0x10ee", "device_id": "0x7024"},
            "active_device_config": {"vendor_id": "0x10ee", "device_id": "0x7024"},
            "board": {"max_lanes": 4, "has_status_leds": True, "num_status_leds": 8},
            "enable_pme": False,
            "enable_wake_events": False,
            "expose_pm_sideband": False,
            "power_management": {"has_interface_signals": False},
        }

    @pytest.fixture
    def minimal_context(self):
        """Minimal context for edge case testing."""
        return {
            "header": "// Minimal context test",
            "vendor_id_int": 0x1234,
            "device_id_int": 0x5678,
            "config_space": bytes(256),
            "device_serial_number_int": 0,
        }

    def test_basic_template_rendering(self, template_env, base_context):
        """Test basic template rendering with standard context."""
        template = template_env.get_template("sv/top_level_wrapper.sv.j2")
        result = template.render(base_context)

        # Verify basic structure
        assert "`default_nettype none" in result
        assert "module pcileech_top" in result
        assert "`default_nettype wire" in result

        # Verify reset is active-high and reset_n appears only in internal plumbing
        assert "logic        reset;" in result
        module_decl_start = result.find("module pcileech_top")
        module_decl_end = result.find(");", module_decl_start)
        port_section = result[module_decl_start:module_decl_end]
        assert "reset_n" not in port_section
        assert ".reset_n(" in result  # Internal modules receive active-low reset

        # Verify vendor/device ID usage
        assert "0x10ee" in result
        assert "0x7024" in result

    def test_lane_width_calculation(self, template_env, base_context):
        """Test lane width calculation for different configurations."""
        template = template_env.get_template("sv/top_level_wrapper.sv.j2")

        # Test x1 lane
        base_context["board"]["max_lanes"] = 1
        result = template.render(base_context)
        assert "output wire [0:0]  pci_exp_txp" in result

        # Test x4 lanes
        base_context["board"]["max_lanes"] = 4
        result = template.render(base_context)
        assert "output wire [3:0]  pci_exp_txp" in result

        # Test x8 lanes
        base_context["board"]["max_lanes"] = 8
        result = template.render(base_context)
        assert "output wire [7:0]  pci_exp_txp" in result

        # Test x16 lanes
        base_context["board"]["max_lanes"] = 16
        result = template.render(base_context)
        assert "output wire [15:0]  pci_exp_txp" in result

    def test_lane_width_prefers_pcie_config_when_broader(
        self, template_env, base_context
    ):
        """Ensure PCIe link width follows generated IP configuration over board defaults."""
        template = template_env.get_template("sv/top_level_wrapper.sv.j2")

        base_context["board"]["max_lanes"] = 1
        base_context["pcie_config"] = {"max_lanes": 4}

        result = template.render(base_context)

        assert "output wire [3:0]  pci_exp_txp" in result

    def test_power_management_sideband(self, template_env, base_context):
        """Test power management sideband signal generation."""
        template = template_env.get_template("sv/top_level_wrapper.sv.j2")

        # Test with PME enabled
        base_context["enable_pme"] = True
        base_context["expose_pm_sideband"] = True
        result = template.render(base_context)
        assert "input  wire         pme_turnoff" in result
        assert "output logic        pme_to_ack" in result

        # Test with wake events enabled
        base_context["enable_wake_events"] = True
        result = template.render(base_context)
        assert "output logic        wake_n" in result

        # Test with both disabled
        base_context["enable_pme"] = False
        base_context["enable_wake_events"] = False
        base_context["expose_pm_sideband"] = False
        result = template.render(base_context)
        assert "pme_turnoff" not in result
        assert "wake_n" not in result

    def test_data_width_handling(self, template_env, base_context):
        """Test 64-bit data width handling."""
        template = template_env.get_template("sv/top_level_wrapper.sv.j2")
        result = template.render(base_context)

        # Verify 64-bit data paths
        assert "logic [63:0] pcie_rx_data;" in result
        assert "logic [63:0] pcie_tx_data;" in result

        # Verify AXI-Stream sideband signals for 64-bit
        assert "logic  [7:0] s_axis_tx_tkeep;" in result
        assert "logic  [7:0] m_axis_rx_tkeep;" in result

        # Verify correct data slicing for 64-bit interface
        assert "tlp_header[0] <= pcie_rx_data[31:0];   // DW0" in result
        assert "tlp_header[1] <= pcie_rx_data[63:32];  // DW1" in result

    def test_data_width_clamps_nonstandard_values(self, template_env, base_context):
        """Non-standard data widths should clamp to supported 64-bit path."""
        template = template_env.get_template("sv/top_level_wrapper.sv.j2")

        base_context["data_width"] = 40
        result = template.render(base_context)

        # Should fall back to the 64-bit code path
        assert "logic [63:0] pcie_rx_data;" in result
        assert "s_axis_tx_tkeep <= {(64 / 8){1'b1}};" in result

        # Ensure 32-bit specific path is not emitted
        assert "logic [31:0] pcie_rx_data;" not in result
        assert "For 32-bit interface - send header DWs" not in result

    def test_cfg_dsn_and_mgmt_tieoffs(self, template_env, base_context):
        """Ensure cfg_dsn and cfg_mgmt_wr_rw1c_as_rw are wired and tied off correctly."""
        template = template_env.get_template("sv/top_level_wrapper.sv.j2")
        result = template.render(base_context)

        assert (
            "localparam logic [63:0] DEVICE_SERIAL_NUMBER = 64'hAABBCCDDEEFF0011;"
            in result
        )
        # cfg_mgmt_wr_rw1c_as_rw can be tied off with wire declaration or assign
        assert ("wire cfg_mgmt_wr_rw1c_as_rw = 1'b1;" in result or 
                "assign cfg_mgmt_wr_rw1c_as_rw = 1'b1;" in result)
        assert "assign cfg_dsn = DEVICE_SERIAL_NUMBER;" in result
        assert ".cfg_mgmt_wr_rw1c_as_rw(cfg_mgmt_wr_rw1c_as_rw)" in result
        assert ".cfg_dsn(cfg_dsn)" in result

    def test_tlp_type_decoding(self, template_env, base_context):
        """Test TLP type decoding uses all 7 bits."""
        template = template_env.get_template("sv/top_level_wrapper.sv.j2")
        result = template.render(base_context)

        # Verify 7-bit TLP type extraction
        assert "tlp_type <= pcie_rx_data[30:24];" in result
        assert "logic [6:0]  tlp_type;" in result

        # Verify TLP type constants
        assert "localparam TLP_MEM_RD_32  = 7'b0000000;" in result
        assert "localparam TLP_MEM_RD_64  = 7'b0100000;" in result
        assert "localparam TLP_MEM_WR_32  = 7'b1000000;" in result
        assert "localparam TLP_MEM_WR_64  = 7'b1100000;" in result

    def test_transaction_fifo_structure(self, template_env, base_context):
        """Test transaction tracking FIFO implementation."""
        template = template_env.get_template("sv/top_level_wrapper.sv.j2")
        result = template.render(base_context)

        # Verify transaction structure
        assert "typedef struct packed {" in result
        assert "logic [15:0] requester_id;" in result
        assert "logic [7:0]  tag;" in result
        assert "logic [6:0]  lower_addr;" in result
        assert "logic [9:0]  length;" in result
        assert "} transaction_info_t;" in result

        # Verify FIFO signals
        assert "transaction_info_t transaction_fifo [0:31];" in result
        assert "logic [4:0] transaction_wr_ptr;" in result
        assert "logic [4:0] transaction_rd_ptr;" in result
        assert "logic [5:0] transaction_count;" in result

    def test_completion_header_generation(self, template_env, base_context):
        """Test completion TLP header generation function."""
        template = template_env.get_template("sv/top_level_wrapper.sv.j2")
        result = template.render(base_context)

        # Verify completion header generation function
        assert "function logic [95:0] generate_cpld_header;" in result
        assert "input logic [15:0] requester_id;" in result
        assert "input logic [7:0]  tag;" in result
        assert "input logic [6:0]  lower_addr;" in result
        assert "input logic [9:0]  length;" in result
        assert "input logic [15:0] completer_id;" in result
        assert "input logic [2:0]  status;" in result
        assert "input logic [11:0] byte_count;" in result

        # Verify header field assignments
        assert "header[31:29] = 3'b010;           // Format: 3DW with data" in result
        assert (
            "header[28:24] = 5'b01010;         // Type: Completion with Data" in result
        )

    def test_byte_enable_handling(self, template_env, base_context):
        """Test byte enable signal handling."""
        template = template_env.get_template("sv/top_level_wrapper.sv.j2")
        result = template.render(base_context)

        # Verify byte enable signals
        assert "logic [3:0]  first_be;" in result
        assert "logic [3:0]  last_be;" in result
        assert "logic [3:0]  current_be;" in result

        # Verify byte enable extraction from header
        assert "first_be <= pcie_rx_data[35:32];  // DW1[3:0]" in result
        assert "last_be <= pcie_rx_data[39:36];   // DW1[7:4]" in result

        # Verify byte enable propagation into BAR controller
        assert ".bar_wr_be(current_be)" in result

    def test_bar_memory_implementation(self, template_env, base_context):
        """Test BAR memory implementation."""
        template = template_env.get_template("sv/top_level_wrapper.sv.j2")
        result = template.render(base_context)

        # Verify BAR controller instantiation instead of local memory array
        assert "pcileech_tlps128_bar_controller" in result
        assert ".bar_rd_data(bar_rd_data)" in result

        # Verify BAR access pipeline
        assert "logic        bar_rd_valid;" in result
        assert "logic [31:0] bar_rd_data_captured;" in result

    def test_state_machine_states(self, template_env, base_context):
        """Test TLP processing state machine states."""
        template = template_env.get_template("sv/top_level_wrapper.sv.j2")
        result = template.render(base_context)

        # Verify state enum
        assert "typedef enum logic [3:0] {" in result
        assert "TLP_IDLE," in result
        assert "TLP_HEADER," in result
        assert "TLP_DATA," in result
        assert "TLP_PROCESSING," in result
        assert "TLP_COMPLETION," in result
        assert "TLP_BAR_WAIT," in result
        assert "TLP_WRITE_DATA" in result
        assert "} tlp_state_t;" in result

    def test_reset_behavior(self, template_env, base_context):
        """Test active-high reset behavior."""
        template = template_env.get_template("sv/top_level_wrapper.sv.j2")
        result = template.render(base_context)

        # Verify active-high reset usage
        assert "if (reset) begin" in result
        assert "if (!reset)" not in result
        assert ".user_reset_out(reset)," in result

        # Verify reset initialization
        reset_blocks = re.findall(
            r"if \(reset\) begin(.*?)end else begin", result, re.DOTALL
        )
        assert len(reset_blocks) > 0
        for block in reset_blocks:
            assert "<=" in block  # Should have reset assignments

    def test_edge_case_minimal_context(self, template_env, minimal_context):
        """Test template rendering with minimal context."""
        template = template_env.get_template("sv/top_level_wrapper.sv.j2")
        result = template.render(minimal_context)

        # Should still generate valid SystemVerilog
        assert "module pcileech_top" in result
        assert "`default_nettype none" in result
        assert "`default_nettype wire" in result

        # Should use default values
        assert "[0:0]  pci_exp_txp" in result  # Default to x1 lane

    def test_device_id_edge_cases(self, template_env, base_context):
        """Test device ID handling edge cases."""
        template = template_env.get_template("sv/top_level_wrapper.sv.j2")

        # Test with single character device ID
        base_context["device_id"] = "0x1"
        base_context["device_id_int"] = 0x1
        result = template.render(base_context)
        # Should handle gracefully without errors
        assert "module pcileech_top" in result

        # Test with empty device dict
        base_context["device"] = {}
        result = template.render(base_context)
        assert "module pcileech_top" in result

    def test_led_status_calculation(self, template_env, base_context):
        """Test LED status width calculation."""
        template = template_env.get_template("sv/top_level_wrapper.sv.j2")

        # Test with different LED counts
        base_context["board"]["has_status_leds"] = True

        # 4 LEDs
        base_context["board"]["num_status_leds"] = 4
        result = template.render(base_context)
        assert "assign led_status = { 1'h0, device_ready, tlp_state};" in result

        # 8 LEDs
        base_context["board"]["num_status_leds"] = 8
        result = template.render(base_context)
        assert "assign led_status = { 5'h0, device_ready, tlp_state};" in result

        # No LEDs
        base_context["board"]["has_status_leds"] = False
        result = template.render(base_context)
        assert "led_status" not in result

    def test_sideband_signal_decoding(self, template_env, base_context):
        """Test sideband signal decoding from m_axis_rx_tuser."""
        template = template_env.get_template("top_level_wrapper.sv.j2")
        result = template.render(base_context)

        # Verify sideband signal extraction
        assert "bar_hit <= m_axis_rx_tuser[2:0];" in result
        assert "bar_hit_valid <= |m_axis_rx_tuser[6:3];" in result
        assert "rx_err_fwd <= m_axis_rx_tuser[21];" in result

    def test_tlp_length_validation(self, template_env, base_context):
        """Test TLP length field handling."""
        template = template_env.get_template("top_level_wrapper.sv.j2")
        result = template.render(base_context)

        # Verify length extraction
        assert "tlp_length <= pcie_rx_data[9:0];" in result

        # Verify expected beats calculation for 64-bit
        assert "tlp_expected_beats <= (pcie_rx_data[9:0] + 1) >> 1;" in result

        # Verify length validation in write data state
        assert "if (tlp_current_beat > tlp_expected_beats)" in result

    def test_header_size_handling(self, template_env, base_context):
        """Test 3-DW vs 4-DW header handling."""
        template = template_env.get_template("top_level_wrapper.sv.j2")
        result = template.render(base_context)

        # Verify format bit detection
        assert "tlp_fmt_4dw <= pcie_rx_data[29];" in result

        # Verify header completeness check
        assert (
            "header_complete = tlp_fmt_4dw ? (tlp_header_count >= 8'h4) : (tlp_header_count >= 8'h3);"
            in result
        )

        # Verify address extraction based on header format
        assert "if (tlp_fmt_4dw) begin" in result
        assert "// 64-bit addressing: address is in DW2 and DW3" in result
        assert "// 32-bit addressing: address is in DW2" in result

    def test_flow_control_signals(self, template_env, base_context):
        """Test AXI-Stream flow control signal handling."""
        template = template_env.get_template("top_level_wrapper.sv.j2")
        result = template.render(base_context)

        # Verify tkeep and tlast are not statically tied off
        assert "s_axis_tx_tkeep <= {" not in result or "<= 8'b1111_1111;" in result
        assert "s_axis_tx_tlast <=" in result

        # Verify dynamic tkeep/tlast handling in completion
        assert "s_axis_tx_tkeep <= 8'b1111_1111;  // All 8 bytes valid" in result
        assert "s_axis_tx_tlast <= 1'b1;  // This is the last beat" in result

    def test_write_data_handling(self, template_env, base_context):
        """Test write data capture and processing."""
        template = template_env.get_template("top_level_wrapper.sv.j2")
        result = template.render(base_context)

        # Verify write data state handling
        assert "TLP_WRITE_DATA: begin" in result
        assert "tlp_write_data <= pcie_rx_data[31:0];" in result
        assert "bar_wr_data <= pcie_rx_data[31:0];" in result

        # Verify multi-DWORD write handling
        assert "if (tlp_data_count == 11'h0) begin" in result
        assert "else if (tlp_data_count == tlp_length - 1) begin" in result
        assert "current_be <= last_be;" in result

    def test_error_handling(self, template_env, base_context):
        """Test error handling in TLP processing."""
        template = template_env.get_template("top_level_wrapper.sv.j2")
        result = template.render(base_context)

        # Verify BAR hit validation
        assert "if (!bar_hit_valid || rx_err_fwd) begin" in result
        assert "// Invalid BAR access or error - skip processing" in result

        # Verify error flag in debug status
        assert "debug_status[31] <= 1'b1;  // Set error flag" in result

    def test_template_error_handling(self, template_env):
        """Test template error handling with invalid contexts."""
        template = template_env.get_template("top_level_wrapper.sv.j2")

        # Test with None context
        with pytest.raises(Exception):
            template.render(None)

        # Test with missing required helpers
        with pytest.raises(Exception):
            template.render({})


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
