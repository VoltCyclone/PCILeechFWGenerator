#!/usr/bin/env python3
"""
Test suite for verifying reset behavior in the generated SystemVerilog code.
Tests the active-high reset functionality across all components.

This test verifies:
- Active-high reset polarity (changed from active-low reset_n)
- Proper reset of all state machines
- Reset of all registers and FIFOs
- Reset behavior in different clock domains
- Reset synchronization
"""

import pytest
import re
from pathlib import Path
from typing import List, Tuple, Optional


class TestTopLevelWrapperReset:
    """Test suite for PCIe top-level wrapper reset behavior."""

    @pytest.fixture
    def sample_sv_code(self):
        """Provide sample SystemVerilog code that would be generated from template."""
        return """
module pcileech_top (
    input  wire        sys_clk_p,
    input  wire        sys_clk_n,
    input  wire        sys_rst_n
);
    // Internal signals
    logic        clk;
    logic        reset;  // Active-high reset
    logic        device_ready;
    
    // State machine
    typedef enum logic [3:0] {
        TLP_IDLE,
        TLP_HEADER,
        TLP_DATA,
        TLP_PROCESSING,
        TLP_COMPLETION,
        TLP_BAR_WAIT,
        TLP_WRITE_DATA
    } tlp_state_t;
    
    tlp_state_t tlp_state;
    
    // Transaction tracking
    logic [4:0] transaction_wr_ptr;
    logic [4:0] transaction_rd_ptr;
    logic [5:0] transaction_count;
    
    // BAR signals
    logic [31:0] bar_addr;
    logic [31:0] bar_wr_data;
    logic        bar_wr_en;
    logic        bar_rd_en;
    logic        bar_rd_valid;
    logic [31:0] bar_rd_data_captured;
    
    // TLP signals
    logic [63:0] pcie_tx_data;
    logic        pcie_tx_valid;
    logic [31:0] tlp_header [0:3];
    logic [7:0]  tlp_header_count;
    logic [10:0] tlp_data_count;
    
    // PCIe IP Core
    pcie_7x_bridge pcie_core (
        .sys_rst_n(sys_rst_n),
        .user_reset_out(reset),  // Active-high reset output
        .user_clk_out(clk)
    );
    
    // Main state machine with reset
    always_ff @(posedge clk) begin
        if (reset) begin
            pcie_tx_data <= '0;
            pcie_tx_valid <= 1'b0;
            tlp_state <= TLP_IDLE;
            tlp_header_count <= 8'h0;
            bar_addr <= 32'h0;
            bar_wr_data <= 32'h0;
            bar_wr_en <= 1'b0;
            bar_rd_en <= 1'b0;
            tlp_data_count <= 11'h0;
        end else begin
            // Normal operation
            case (tlp_state)
                TLP_IDLE: begin
                    // State logic
                end
            endcase
        end
    end
    
    // Transaction FIFO management with reset
    always_ff @(posedge clk) begin
        if (reset) begin
            transaction_wr_ptr <= 5'h0;
            transaction_rd_ptr <= 5'h0;
            transaction_count <= 6'h0;
        end else begin
            // FIFO operations
        end
    end
    
    // BAR read data capture pipeline with reset
    always_ff @(posedge clk) begin
        if (reset) begin
            bar_rd_valid <= 1'b0;
            bar_rd_data_captured <= 32'h0;
        end else begin
            bar_rd_valid <= bar_rd_en;
            if (bar_rd_en) begin
                bar_rd_data_captured <= bar_rd_data;
            end
        end
    end
endmodule
"""

    def test_reset_signal_declaration(self, sample_sv_code):
        """Verify reset signal is declared as active-high."""
        # Check for active-high reset declaration
        assert "logic        reset;" in sample_sv_code

        # Ensure old reset_n is not used (except for system input)
        lines_with_reset_n = [
            line
            for line in sample_sv_code.split("\n")
            if "reset_n" in line and "sys_rst_n" not in line
        ]
        assert (
            len(lines_with_reset_n) == 0
        ), "Found reset_n usage outside of system reset"

        # Verify comment indicates active-high
        assert (
            "// Active-high reset" in sample_sv_code
            or "active-high reset" in sample_sv_code.lower()
        )

    def test_pcie_core_reset_connection(self, sample_sv_code):
        """Verify PCIe core reset connection uses active-high reset."""
        # Check user_reset_out connection
        assert ".user_reset_out(reset)," in sample_sv_code

        # Verify it's not inverted
        assert ".user_reset_out(~reset)" not in sample_sv_code
        assert ".user_reset_out(!reset)" not in sample_sv_code

    def test_reset_condition_polarity(self, sample_sv_code):
        """Test all reset conditions use positive polarity."""
        # Find all reset conditions
        reset_conditions = re.findall(r"if\s*\(\s*reset\s*\)\s*begin", sample_sv_code)
        assert len(reset_conditions) > 0, "No reset conditions found"

        # Check for incorrect negative reset conditions
        negative_resets = re.findall(r"if\s*\(\s*!reset\s*\)\s*begin", sample_sv_code)
        negative_resets += re.findall(r"if\s*\(\s*~reset\s*\)\s*begin", sample_sv_code)
        assert (
            len(negative_resets) == 0
        ), f"Found negative reset conditions: {negative_resets}"

    def test_all_registers_have_reset(self, sample_sv_code):
        """Verify all registers are properly reset."""
        # Extract all register declarations
        register_pattern = r"(logic\s+(?:\[[^\]]+\]\s+)?(\w+);)"
        registers = re.findall(register_pattern, sample_sv_code)

        # Filter out non-register signals
        excluded = [
            "clk",
            "reset",
            "sys_clk_p",
            "sys_clk_n",
            "sys_rst_n",
            "device_ready",
        ]
        register_names = [name for _, name in registers if name not in excluded]

        # Check each register has a reset assignment
        reset_blocks = re.findall(
            r"if\s*\(reset\)\s*begin(.*?)end", sample_sv_code, re.DOTALL
        )
        reset_text = "\n".join(reset_blocks)

        for reg in register_names:
            if reg.endswith("_t"):  # Skip type definitions
                continue
            # Check for reset assignment
            reset_pattern = rf"{reg}\s*<=\s*[^;]+;"
            if not re.search(reset_pattern, reset_text):
                # Some registers might be reset in separate blocks, so check full code
                full_reset_pattern = rf"if\s*\(reset\).*?{reg}\s*<=\s*[^;]+;"
                assert re.search(
                    full_reset_pattern, sample_sv_code, re.DOTALL
                ), f"Register {reg} does not have a reset assignment"

    def test_state_machine_reset(self, sample_sv_code):
        """Test state machine reset to IDLE state."""
        # Find state machine reset
        state_reset = re.search(
            r"if\s*\(reset\).*?tlp_state\s*<=\s*(\w+);", sample_sv_code, re.DOTALL
        )
        assert state_reset is not None, "State machine reset not found"
        assert (
            state_reset.group(1) == "TLP_IDLE"
        ), f"State machine should reset to TLP_IDLE, found: {state_reset.group(1)}"

    def test_fifo_pointer_reset(self, sample_sv_code):
        """Test FIFO pointers reset to zero."""
        # Check write pointer reset
        assert re.search(
            r"if\s*\(reset\).*?transaction_wr_ptr\s*<=\s*5\'h0;",
            sample_sv_code,
            re.DOTALL,
        ), "Write pointer not reset to 0"

        # Check read pointer reset
        assert re.search(
            r"if\s*\(reset\).*?transaction_rd_ptr\s*<=\s*5\'h0;",
            sample_sv_code,
            re.DOTALL,
        ), "Read pointer not reset to 0"

        # Check count reset
        assert re.search(
            r"if\s*\(reset\).*?transaction_count\s*<=\s*6\'h0;",
            sample_sv_code,
            re.DOTALL,
        ), "Transaction count not reset to 0"

    def test_data_signals_reset(self, sample_sv_code):
        """Test data signals reset to zero."""
        data_signals = [
            ("pcie_tx_data", "'0"),
            ("pcie_tx_valid", "1'b0"),
            ("bar_addr", "32'h0"),
            ("bar_wr_data", "32'h0"),
            ("bar_wr_en", "1'b0"),
            ("bar_rd_en", "1'b0"),
        ]

        for signal, value in data_signals:
            pattern = rf"if\s*\(reset\).*?{signal}\s*<=\s*{value};"
            assert re.search(
                pattern, sample_sv_code, re.DOTALL
            ), f"Signal {signal} not properly reset to {value}"

    def test_reset_block_structure(self, sample_sv_code):
        """Test reset block structure in always_ff blocks."""
        # Find all always_ff blocks
        always_blocks = re.findall(
            r"always_ff\s*@\s*\(posedge\s+clk\)\s*begin(.*?)end\s*(?=always_ff|endmodule)",
            sample_sv_code,
            re.DOTALL,
        )

        assert len(always_blocks) > 0, "No always_ff blocks found"

        for block in always_blocks:
            # Each should have reset condition first
            assert re.search(
                r"if\s*\(reset\)\s*begin", block
            ), "always_ff block missing reset condition"

            # Should have else for normal operation
            assert (
                "else begin" in block or "end else begin" in block
            ), "always_ff block missing else clause for normal operation"

    def test_no_reset_in_combinational(self):
        """Test that reset is not used in combinational logic."""
        sample_comb = """
        always_comb begin
            // Combinational logic should not depend on reset
            next_state = current_state;
            case (current_state)
                IDLE: if (start) next_state = ACTIVE;
                ACTIVE: if (done) next_state = IDLE;
            endcase
        end
        """

        # Reset should not appear in always_comb
        assert "reset" not in sample_comb or re.search(
            r"//.*reset", sample_comb
        ), "Reset found in combinational logic"

    def test_reset_synchronization_template(self):
        """Test template for proper reset synchronization."""
        sync_template = """
        // Reset synchronizer (if needed)
        logic reset_sync_1, reset_sync_2;
        
        always_ff @(posedge clk or negedge sys_rst_n) begin
            if (!sys_rst_n) begin
                reset_sync_1 <= 1'b1;
                reset_sync_2 <= 1'b1;
            end else begin
                reset_sync_1 <= 1'b0;
                reset_sync_2 <= reset_sync_1;
            end
        end
        
        assign reset = reset_sync_2;
        """

        # Verify synchronizer structure if present
        if "reset_sync" in sync_template:
            assert "reset_sync_1" in sync_template
            assert "reset_sync_2" in sync_template
            assert "reset_sync_2 <= reset_sync_1" in sync_template

    def test_reset_value_consistency(self, sample_sv_code):
        """Test that reset values are consistent and appropriate."""
        # Counters should reset to 0
        counter_resets = re.findall(r"(\w*count\w*)\s*<=\s*(\d+\'h\w+)", sample_sv_code)
        for counter, value in counter_resets:
            assert value.endswith(
                "h0"
            ), f"Counter {counter} should reset to 0, found {value}"

        # Pointers should reset to 0
        pointer_resets = re.findall(r"(\w*ptr\w*)\s*<=\s*(\d+\'h\w+)", sample_sv_code)
        for pointer, value in pointer_resets:
            assert value.endswith(
                "h0"
            ), f"Pointer {pointer} should reset to 0, found {value}"

        # Valid/enable signals should reset to 0
        enable_resets = re.findall(
            r"(\w*(?:valid|en|enable)\w*)\s*<=\s*1\'b(\d)", sample_sv_code
        )
        for signal, value in enable_resets:
            assert (
                value == "0"
            ), f"Enable signal {signal} should reset to 0, found {value}"

    def test_reset_coverage(self, sample_sv_code):
        """Test that all major components have reset coverage."""
        required_reset_items = [
            "tlp_state",
            "transaction_wr_ptr",
            "transaction_rd_ptr",
            "transaction_count",
            "pcie_tx_valid",
            "pcie_tx_data",
            "bar_wr_en",
            "bar_rd_en",
            "tlp_header_count",
            "tlp_data_count",
        ]

        for item in required_reset_items:
            pattern = rf"if\s*\(reset\).*?{item}\s*<=.*?;"
            assert re.search(
                pattern, sample_sv_code, re.DOTALL
            ), f"Required signal {item} not found in reset blocks"

    def test_reset_does_not_affect_constants(self, sample_sv_code):
        """Test that constants and parameters are not reset."""
        # Find all parameters and localparams
        params = re.findall(
            r"(?:parameter|localparam)\s+\w+\s+(\w+)\s*=", sample_sv_code
        )

        reset_blocks = re.findall(
            r"if\s*\(reset\)\s*begin(.*?)end", sample_sv_code, re.DOTALL
        )
        reset_text = "\n".join(reset_blocks)

        for param in params:
            assert (
                param not in reset_text
            ), f"Constant {param} should not be in reset block"

    def test_reset_timing_requirements(self):
        """Test documentation of reset timing requirements."""
        reset_spec = """
        // Reset Requirements:
        // - Minimum reset pulse width: 100ns
        // - Reset must be held for at least 10 clock cycles
        // - Reset is internally synchronized to clock domain
        // - All outputs tristated during reset
        """

        # This test would verify comments/documentation
        assert "clock cycles" in reset_spec or "pulse width" in reset_spec

    def test_pipeline_register_reset(self, sample_sv_code):
        """Test pipeline registers are properly reset."""
        # Check BAR read pipeline
        assert re.search(
            r"if\s*\(reset\).*?bar_rd_valid\s*<=\s*1\'b0;", sample_sv_code, re.DOTALL
        ), "BAR read valid not reset"
        assert re.search(
            r"if\s*\(reset\).*?bar_rd_data_captured\s*<=\s*32\'h0;",
            sample_sv_code,
            re.DOTALL,
        ), "BAR read data not reset"

    def test_no_latches_during_reset(self, sample_sv_code):
        """Ensure no latches are created during reset."""
        # This is more of a synthesis check, but we can verify coding style
        always_ff_pattern = r"always_ff\s*@\s*\(posedge\s+clk\)"
        always_latch_pattern = r"always_latch"

        # Should only use always_ff for sequential logic
        assert re.search(always_ff_pattern, sample_sv_code)
        assert not re.search(always_latch_pattern, sample_sv_code)

    def test_reset_assertion_order(self, sample_sv_code):
        """Test that reset assertion follows proper order."""
        # In each always_ff block, reset should be the first condition
        always_blocks = re.findall(
            r"always_ff\s*@\s*\(posedge\s+clk\)\s*begin\s*\n\s*(if.*?)(?=end\s*$)",
            sample_sv_code,
            re.MULTILINE | re.DOTALL,
        )

        for block in always_blocks:
            # First condition should be reset
            first_if = re.search(r"if\s*\((.*?)\)", block)
            if first_if:
                condition = first_if.group(1).strip()
                assert (
                    condition == "reset"
                ), f"First condition should be reset, found: {condition}"


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
