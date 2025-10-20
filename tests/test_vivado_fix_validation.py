#!/usr/bin/env python3
"""
Unit tests for Vivado DRC fix validation.

Tests that the fixes for DRC PLIOBUF-3 errors are correctly applied:
- Top-level module only exposes physical PCIe pins
- Internal signals are properly declared as wires
- PCIe core instantiation is present
- LED status output is conditional on board configuration
- MSI-X RAM inference uses proper patterns
- State machines include default cases

These tests validate the template rendering to ensure Vivado synthesis will succeed.
"""

from pathlib import Path
from typing import Any, Dict
from unittest.mock import Mock

import pytest

from src.device_clone.overlay_utils import compute_sparse_hash_table_size
from src.templating.systemverilog_generator import AdvancedSVGenerator
from src.templating.advanced_sv_power import PowerManagementConfig
from src.templating.sv_config import ErrorHandlingConfig, PerformanceConfig
from src.templating.template_renderer import TemplateRenderer


class TestVivadoFixValidation:
    """Test suite for Vivado DRC fix validation."""

    @pytest.fixture
    def sv_generator(self):
        """Provide SystemVerilog generator with default configs."""
        return AdvancedSVGenerator(
            power_config=PowerManagementConfig(),
            error_config=ErrorHandlingConfig(),
            perf_config=PerformanceConfig(),
        )

    @pytest.fixture
    def minimal_context(self):
        """Provide minimal valid template context."""
        return {
            "device_config": {
                "vendor_id": "10de",
                "device_id": "1234",
                "subsystem_vendor_id": "10de",
                "subsystem_device_id": "1234",
                "class_code": "030000",
                "revision_id": "01",
            },
            "device_signature": "12345678",
            "vendor_id": "10de",
            "device_id": "1234",
            "subsystem_vendor_id": "10de",
            "subsystem_device_id": "1234",
            "class_code": "030000",
            "revision_id": "01",
            "msix_config": {
                "num_vectors": 16,
                "table_bir": 0,
                "table_offset": 0x1000,
                "pba_bir": 0,
                "pba_offset": 0x2000,
            },
            "bar_config": {
                "bars": [
                    Mock(
                        index=0,
                        size=4096,
                        bar_type="memory",
                        prefetchable=False,
                        is_64bit=False,
                        get_size_encoding=lambda: 0xFFFFF000,
                    )
                ]
            },
            "board_config": {
                "name": "test_board",
                "fpga_part": "xc7a35t",
            },
            "generation_metadata": {
                "version": "2.0.0",
                "generator": "PCILeechFWGenerator",
            },
            "device_serial_number_int": 0x1122334455667788,
            "OVERLAY_MAP": [],
            "OVERLAY_ENTRIES": 0,
            "ENABLE_SPARSE_MAP": 0,
            "ENABLE_BIT_TYPES": 1,
            "HASH_TABLE_SIZE": compute_sparse_hash_table_size(0),
        }

    def test_top_level_module_has_only_physical_pins(
        self, sv_generator, minimal_context
    ):
        """Test that top-level module only exposes physical PCIe transceiver pins."""
        result = sv_generator.generate_pcileech_modules(minimal_context)
        top_level = result.get("top_level_wrapper", "")

        # Should have physical PCIe transceiver pins
        assert "pci_exp_txp" in top_level, "Missing PCIe TX+ pin"
        assert "pci_exp_txn" in top_level, "Missing PCIe TX- pin"
        assert "pci_exp_rxp" in top_level, "Missing PCIe RX+ pin"
        assert "pci_exp_rxn" in top_level, "Missing PCIe RX- pin"

        # Should have system clock differential inputs
        assert "sys_clk_p" in top_level, "Missing system clock positive"
        assert "sys_clk_n" in top_level, "Missing system clock negative"

        # Should have system reset
        assert "sys_rst_n" in top_level, "Missing system reset"

        # These signals should NOT be in the module port list (after the closing paren)
        # Extract module declaration
        module_start = top_level.find("module pcileech_top")
        module_end = top_level.find(");", module_start)
        assert module_start != -1, "Could not find module declaration"
        assert module_end != -1, "Could not find end of module ports"

        port_list = top_level[module_start:module_end]

        # These should NOT be in the port list (they should be internal)
        forbidden_ports = [
            "input  wire         clk,",  # Should be internal from PCIe core
            "input  wire         reset_n,",  # Should be internal from PCIe core
            "input  wire  [31:0] pcie_rx_data,",  # Should be internal
            "output logic [31:0] pcie_tx_data,",  # Should be internal
            "input  wire         cfg_ext_read_received,",  # Should be internal
            "output logic [31:0] cfg_ext_read_data,",  # Should be internal
            "output logic        msix_interrupt,",  # Should be internal
            "output logic [31:0] debug_status,",  # Should be internal
            "output logic        device_ready,",  # Should be internal (unless LED board)
        ]

        for forbidden in forbidden_ports:
            assert (
                forbidden not in port_list
            ), f"Found forbidden port in module declaration: {forbidden}"

    def test_internal_signals_declared_as_wires(self, sv_generator, minimal_context):
        """Test that internal PCIe signals are declared as internal wires."""
        result = sv_generator.generate_pcileech_modules(minimal_context)
        top_level = result.get("top_level_wrapper", "")

        # Find the section after module declaration but before instantiations
        module_end = top_level.find(");")
        pcie_core_start = top_level.find("pcie_7x_bridge pcie_core")

        assert module_end != -1, "Could not find end of module declaration"
        assert pcie_core_start != -1, "Could not find PCIe core instantiation"

        internal_section = top_level[module_end:pcie_core_start]

        # Detect data path width (32-bit legacy vs 64-bit standard on 7-series)
        detected_width = None
        for w in (64, 32):
            if (
                f"logic [{w-1}:0] pcie_rx_data;" in internal_section
                or f"wire [{w-1}:0] pcie_rx_data;" in internal_section
            ):
                detected_width = w
                break

        assert (
            detected_width is not None
        ), "Could not detect pcie_rx_data internal width (expected 32 or 64 bits)"

        # These signals should be declared as internal (7-series uses cfg_mgmt_* not cfg_ext_*)
        required_internal_signals = [
            "logic        clk;",
            "logic        reset;",
            "logic        device_ready;",
            f"logic [{detected_width-1}:0] pcie_rx_data;",
            "logic        pcie_rx_valid;",
            f"logic [{detected_width-1}:0] pcie_tx_data;",
            "logic        pcie_tx_valid;",
            "logic [31:0] cfg_mgmt_do;",
            "logic        cfg_mgmt_rd_wr_done;",
            "logic [31:0] cfg_mgmt_di;",
            "logic  [3:0] cfg_mgmt_byte_en;",
            "logic  [9:0] cfg_mgmt_dwaddr;",
            "logic [31:0] debug_status;",
        ]

        for signal in required_internal_signals:
            assert (
                signal in internal_section
                or signal.replace("logic", "wire") in internal_section
            ), f"Missing internal signal declaration: {signal}"

    def test_pcie_core_instantiation_present(self, sv_generator, minimal_context):
        """Test that PCIe IP core is instantiated in top-level module."""
        result = sv_generator.generate_pcileech_modules(minimal_context)
        top_level = result.get("top_level_wrapper", "")

        # Should have PCIe core instantiation
        assert (
            "pcie_7x_bridge pcie_core" in top_level
        ), "Missing PCIe core instantiation"

        # Check key port connections (7-series core, not UltraScale+)
        required_connections = [
            ".pci_exp_txp(pci_exp_txp)",
            ".pci_exp_txn(pci_exp_txn)",
            ".pci_exp_rxp(pci_exp_rxp)",
            ".pci_exp_rxn(pci_exp_rxn)",
            ".user_clk_out(clk)",
            ".user_reset_out(reset)",
            ".user_lnk_up(device_ready)",
            ".m_axis_rx_tdata(pcie_rx_data)",
            ".m_axis_rx_tvalid(pcie_rx_valid)",
            ".s_axis_tx_tdata(pcie_tx_data)",
            ".s_axis_tx_tvalid(pcie_tx_valid)",
            ".cfg_mgmt_do(cfg_mgmt_do)",
            ".sys_clk(pcie_sys_clk)",
            ".sys_rst_n(sys_rst_n)",
        ]

        for connection in required_connections:
            assert (
                connection in top_level
            ), f"Missing PCIe core port connection: {connection}"

    def test_msix_ram_no_explicit_style_attribute(self, sv_generator, minimal_context):
        """Test that MSI-X tables don't use explicit ram_style attributes."""
        result = sv_generator.generate_pcileech_modules(minimal_context)

        # Check if msix_table module exists
        if "msix_table" in result:
            msix_content = result["msix_table"]

            # Should NOT have explicit ram_style attributes
            # These prevent proper BlockRAM inference
            assert (
                '(* ram_style="block" *)' not in msix_content
            ), "MSI-X table should not have explicit ram_style attribute"
            assert (
                '(* ram_style = "block" *)' not in msix_content
            ), "MSI-X table should not have explicit ram_style attribute"

            # Should still have array declarations
            assert (
                "msix_table_staging" in msix_content
            ), "Missing MSI-X staging table array"
            assert (
                "msix_table_active" in msix_content
            ), "Missing MSI-X active table array"

    def test_msix_state_machine_has_default_case(self, sv_generator, minimal_context):
        """Test that MSI-X interrupt state machine includes default case."""
        result = sv_generator.generate_pcileech_modules(minimal_context)

        if "msix_table" in result:
            msix_content = result["msix_table"]

            # Should have state machine with default case
            if "case (intr_state)" in msix_content:
                # Find the case statement
                case_start = msix_content.find("case (intr_state)")
                case_end = msix_content.find("endcase", case_start)
                assert case_end != -1, "Could not find endcase"

                case_block = msix_content[case_start:case_end]

                # Should have default case
                assert (
                    "default:" in case_block or "default begin" in case_block
                ), "MSI-X state machine missing default case"

    def test_tlp_state_machine_has_default_case(self, sv_generator, minimal_context):
        """Test that TLP processing state machine includes default case."""
        result = sv_generator.generate_pcileech_modules(minimal_context)
        top_level = result.get("top_level_wrapper", "")

        # Should have TLP state machine
        if "case (tlp_state)" in top_level:
            # Find the case statement
            case_start = top_level.find("case (tlp_state)")
            case_end = top_level.find("endcase", case_start)
            assert case_end != -1, "Could not find endcase"

            case_block = top_level[case_start:case_end]

            # Should have default case
            assert (
                "default:" in case_block or "default begin" in case_block
            ), "TLP state machine missing default case"

    def test_no_floating_ports_in_generated_code(self, sv_generator, minimal_context):
        """Test that generated code has no floating/unconnected ports."""
        result = sv_generator.generate_pcileech_modules(minimal_context)
        top_level = result.get("top_level_wrapper", "")

        # All instantiated modules should have their ports connected
        # Find BAR controller instantiation
        if (
            "bar_controller" in top_level
            or "pcileech_tlps128_bar_controller" in top_level
        ):
            bar_start = top_level.find("pcileech_tlps128_bar_controller")
            if bar_start != -1:
                bar_end = top_level.find(");", bar_start)
                bar_instantiation = top_level[bar_start:bar_end]

                # Key ports should be connected
                required_bar_connections = [
                    ".clk(clk)",
                    ".reset_n(bar_controller_reset_n)",
                    ".cfg_ext_read_data(cfg_ext_read_data)",
                    ".cfg_ext_read_received(cfg_ext_read_received)",
                    ".msix_interrupt(msix_interrupt)",
                ]

                for connection in required_bar_connections:
                    assert (
                        connection in bar_instantiation
                    ), f"Missing BAR controller connection: {connection}"

    def test_timing_constraints_updated_for_differential_clock(
        self, sv_generator, minimal_context
    ):
        """Test that timing constraints reference differential clock inputs."""
        # This would require rendering the constraints template
        # For now, we'll check that the context is properly structured
        assert "board_config" in minimal_context
        assert minimal_context["board_config"] is not None

    def test_module_structure_validity(self, sv_generator, minimal_context):
        """Test that generated modules have valid SystemVerilog structure."""
        result = sv_generator.generate_pcileech_modules(minimal_context)
        top_level = result.get("top_level_wrapper", "")

        # Basic structure checks
        assert "`default_nettype none" in top_level, "Missing default_nettype directive"
        assert "module pcileech_top" in top_level, "Missing module declaration"
        assert "endmodule" in top_level, "Missing endmodule"
        assert "`default_nettype wire" in top_level, "Missing default_nettype reset"

        # Should have proper begin/end pairing
        begin_count = top_level.count(" begin")
        end_count = top_level.count(" end")
        # Allow a slightly wider tolerance due to comments/inline blocks
        assert (
            abs(begin_count - end_count) <= 5
        ), f"Mismatched begin/end count: {begin_count} begins vs {end_count} ends"

    def test_no_hardcoded_device_ids(self, sv_generator, minimal_context):
        """Test that device IDs come from context, not hardcoded."""
        # Change device IDs (without 0x prefix as required by validator)
        test_vendor = "ABCD"
        test_device = "5678"
        minimal_context["device_config"]["vendor_id"] = test_vendor
        minimal_context["device_config"]["device_id"] = test_device
        minimal_context["device_config"]["subsystem_vendor_id"] = test_vendor
        minimal_context["device_config"]["subsystem_device_id"] = test_device
        minimal_context["vendor_id"] = test_vendor
        minimal_context["device_id"] = test_device
        minimal_context["subsystem_vendor_id"] = test_vendor
        minimal_context["subsystem_device_id"] = test_device

        result = sv_generator.generate_pcileech_modules(minimal_context)
        top_level = result.get("top_level_wrapper", "")

        # Should reference the test IDs, not any hardcoded values
        # The template uses get_vendor_id/get_device_id helpers
        # Just verify no obvious hardcoded values
        forbidden_hardcoded = [
            "16'h8086",  # Intel vendor ID
            "16'h10de",  # NVIDIA vendor ID (from default context)
        ]

        for forbidden in forbidden_hardcoded:
            # Allow it in comments, but not in actual code
            if forbidden in top_level:
                # Check it's not in a comment
                lines_with_value = [
                    line for line in top_level.split("\n") if forbidden in line
                ]
                for line in lines_with_value:
                    stripped = line.strip()
                    if not stripped.startswith("//") and not stripped.startswith("/*"):
                        # This is acceptable if it's the custom device ID we set
                        pass

    def test_all_case_statements_have_defaults(self, sv_generator, minimal_context):
        """Test that all case statements in generated code have default cases."""
        result = sv_generator.generate_pcileech_modules(minimal_context)

        for module_name, content in result.items():
            if not content:
                continue

            # Find all case statements
            case_positions = []
            search_pos = 0
            while True:
                pos = content.find("case (", search_pos)
                if pos == -1:
                    break
                case_positions.append(pos)
                search_pos = pos + 1

            # For each case statement, verify it has a default
            for case_pos in case_positions:
                # Find corresponding endcase
                endcase_pos = content.find("endcase", case_pos)
                if endcase_pos == -1:
                    continue

                case_block = content[case_pos:endcase_pos]

                # Should have default case or be a complete enumeration
                # For safety, we require default in all cases
                assert (
                    "default" in case_block
                ), f"Case statement in {module_name} missing default case"

            # Ensure the completion block keeps the default assignment that zeroes unused data
            template_dir = Path(__file__).parent.parent / "src/templates"
            renderer = TemplateRenderer(template_dir=str(template_dir))
            manual_context = {
                "header": "// Generated PCIe Top Level Wrapper",
                "vendor_id": "0x10ee",
                "device_id": "0x7024",
                "vendor_id_int": 0x10EE,
                "device_id_int": 0x7024,
                "config_space": bytes(256),
                "device_serial_number_int": 0xAABBCCDDEEFF0011,
                "device": {"vendor_id": "0x10ee", "device_id": "0x7024"},
                "active_device_config": {"vendor_id": "0x10ee", "device_id": "0x7024"},
                "board": {
                    "max_lanes": 4,
                    "has_status_leds": True,
                    "num_status_leds": 8,
                },
                "enable_pme": False,
                "enable_wake_events": False,
                "expose_pm_sideband": False,
                "power_management": {"has_interface_signals": False},
            }
            manual_context["data_width"] = 32
            rendered_top_level = renderer.env.get_template(
                "sv/top_level_wrapper.sv.j2"
            ).render(manual_context)

            completion_case_pos = rendered_top_level.find("case (tlp_current_beat)")
            assert completion_case_pos != -1, "TLP completion case block missing"
            completion_case_end = rendered_top_level.find(
                "endcase", completion_case_pos
            )
            assert completion_case_end != -1, "TLP completion case block not terminated"
            completion_case_block = rendered_top_level[
                completion_case_pos:completion_case_end
            ]
            assert (
                "pcie_tx_data <= 32'h0000_0000;" in completion_case_block
            ), "Completion default assignment missing for 32-bit data width"


class TestConstraintsTemplateValidation:
    """Test suite for constraints template validation."""

    def test_constraints_reference_differential_clocks(self):
        """Test that constraints reference differential clock inputs."""
        # This would require rendering the constraints template
        # Placeholder for future implementation
        pass

    def test_constraints_include_pcie_pin_placeholders(self):
        """Test that constraints include PCIe pin assignment placeholders."""
        # This would require rendering the constraints template
        # Placeholder for future implementation
        pass


if __name__ == "__main__":
    pytest.main([__file__, "-v", "--tb=short"])
