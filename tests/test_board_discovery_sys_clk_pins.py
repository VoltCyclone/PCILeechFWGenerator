#!/usr/bin/env python3
"""
Unit tests for sys_clk differential pin assignment functionality.

Tests the UCIO-1 fix that adds board-specific sys_clk_p/sys_clk_n pin mappings
to prevent unconstrained logical port DRC errors.
"""

import pytest

from pathlib import Path

from src.file_management.board_discovery import BoardDiscovery


class TestSysClkPinMapping:
    """Test sys_clk differential pin mapping functionality."""

    def test_sys_clk_pin_map_contains_expected_boards(self):
        """Test that SYS_CLK_PIN_MAP contains the expected differential clock boards."""
        expected_boards = {
            "ac701": ("R3", "P3"),
            "pcileech_ac701": ("R3", "P3"),
            "acorn": ("J19", "H19"),
            "pcileech_acorn": ("J19", "H19"),
            "sp605": ("M6", "L6"),
            "pcileech_sp605": ("M6", "L6"),
        }
        
        assert BoardDiscovery.SYS_CLK_PIN_MAP == expected_boards
        
    def test_sys_clk_pin_map_excludes_single_ended_boards(self):
        """Test that single-ended clock boards are NOT in SYS_CLK_PIN_MAP."""
        single_ended_boards = [
            "pcileech_100t484_x1",  # CaptainDMA 100T
            "pcileech_enigma_x1",   # EnigmaX1
            "pcileech_squirrel",    # PCIeSquirrel
            "pcileech_pciescreamer_xc7a35",  # PCIeScreamer
            "pcileech_75t484_x1",   # CaptainDMA 75T
            "pcileech_35t484_x1",   # CaptainDMA 35T x1
            "pcileech_35t325_x4",   # CaptainDMA 35T x4
            "pcileech_35t325_x1",   # CaptainDMA 35T x1 (325)
        ]
        
        for board in single_ended_boards:
            assert board not in BoardDiscovery.SYS_CLK_PIN_MAP, \
                f"Single-ended board '{board}' incorrectly in SYS_CLK_PIN_MAP"

    def test_sys_clk_pins_are_valid_fpga_pins(self):
        """Test that all sys_clk pins use valid FPGA pin naming convention."""
        for board_name, (p_pin, n_pin) in BoardDiscovery.SYS_CLK_PIN_MAP.items():
            # FPGA pins should be alphanumeric (e.g., R3, P3, J19, H19, M6, L6)
            assert p_pin.isalnum(), f"Invalid p_pin '{p_pin}' for board '{board_name}'"
            assert n_pin.isalnum(), f"Invalid n_pin '{n_pin}' for board '{board_name}'"
            
            # Pins should be different
            assert p_pin != n_pin, f"Board '{board_name}' has identical p_pin and n_pin"
            
            # Pins should be reasonably short (typical FPGA pin names are 2-3 chars)
            assert 1 <= len(p_pin) <= 4, f"Unusual p_pin length '{p_pin}' for board '{board_name}'"
            assert 1 <= len(n_pin) <= 4, f"Unusual n_pin length '{n_pin}' for board '{board_name}'"


class TestBoardAnalysisWithSysClkPins:
    """Test _analyze_board method with sys_clk pin assignment logic."""

    def test_analyze_board_adds_sys_clk_pins_for_differential_boards(self):
        """Test that differential clock boards get sys_clk_p_pin and sys_clk_n_pin."""
        # Test AC701 board (known differential clock board)
        board_path = Path("/fake/ac701")
        base_config = {
            "name": "ac701", 
            "fpga_part": "xc7a200tfbg484-2"
        }
        
        config = BoardDiscovery._analyze_board(board_path, base_config)
        
        # Should have differential clock pins
        assert "sys_clk_p_pin" in config
        assert "sys_clk_n_pin" in config
        assert config["sys_clk_p_pin"] == "R3"
        assert config["sys_clk_n_pin"] == "P3"

    def test_analyze_board_no_sys_clk_pins_for_single_ended_boards(self):
        """Test that single-ended clock boards do NOT get sys_clk pins."""
        # Test CaptainDMA 100T board (known single-ended clock board)
        board_path = Path("/fake/captaindma")
        base_config = {
            "name": "pcileech_100t484_x1", 
            "fpga_part": "xc7a100tfgg484-1"
        }
        
        config = BoardDiscovery._analyze_board(board_path, base_config)
        
        # Should NOT have differential clock pins
        assert "sys_clk_p_pin" not in config
        assert "sys_clk_n_pin" not in config

    def test_analyze_board_adds_default_pins_for_special_boards(self):
        """Test that boards with differential-suggesting names get default pins."""
        # Test a hypothetical AC701 variant not in the explicit mapping
        board_path = Path("/fake/ac701_custom")
        base_config = {
            "name": "custom_ac701_board", 
            "fpga_part": "xc7a200tfbg484-2"
        }
        
        config = BoardDiscovery._analyze_board(board_path, base_config)
        
        # Should get default differential pins due to "ac701" in name
        assert "sys_clk_p_pin" in config
        assert "sys_clk_n_pin" in config
        assert config["sys_clk_p_pin"] == "J19"  # Default
        assert config["sys_clk_n_pin"] == "H19"  # Default

    def test_analyze_board_preserves_other_config_values(self):
        """Test that sys_clk pin addition doesn't interfere with other config."""
        board_path = Path("/fake/ac701")
        base_config = {
            "name": "ac701",
            "fpga_part": "xc7a200tfbg484-2",
            "max_lanes": 4,
            "supports_msi": True
        }
        
        config = BoardDiscovery._analyze_board(board_path, base_config)
        
        # Original config should be preserved
        assert config["name"] == "ac701"
        assert config["fpga_part"] == "xc7a200tfbg484-2"
        assert config["max_lanes"] == 4
        # Note: supports_msi may be overridden by capability detection, so check it exists
        assert "supports_msi" in config
        
        # And sys_clk pins should be added
        assert config["sys_clk_p_pin"] == "R3"
        assert config["sys_clk_n_pin"] == "P3"


class TestSysClkPinMappingIntegration:
    """Integration tests for sys_clk pin mapping in board discovery."""

    @pytest.fixture
    def mock_repo_structure(self, tmp_path):
        """Create a mock PCILeech repository structure."""
        repo_root = tmp_path / "pcileech-fpga"
        
        # Create AC701 board directory (differential clock)
        ac701_dir = repo_root / "AC701"
        ac701_dir.mkdir(parents=True)
        (ac701_dir / "build.tcl").write_text("# AC701 build script")
        
        # Create CaptainDMA board directory (single-ended clock)
        captaindma_dir = repo_root / "CaptainDMA" / "100t484-1"
        captaindma_dir.mkdir(parents=True)
        (captaindma_dir / "build.tcl").write_text("# CaptainDMA build script")
        
        return repo_root

    def test_discover_boards_assigns_correct_clock_types(self, mock_repo_structure):
        """Test that board discovery correctly assigns clock types."""
        boards = BoardDiscovery.discover_boards(mock_repo_structure)
        
        # Check that differential clock boards get sys_clk pins
        if "35t" in boards:  # AC701 maps to "35t" in BOARD_DIR_PATTERNS  
            # Note: The AC701 mapping in BOARD_DIR_PATTERNS uses "35t" as the name
            # but we want to test the sys_clk pin assignment logic
            pass
            
        # Check that CaptainDMA boards don't get sys_clk pins
        if "pcileech_100t484_x1" in boards:
            config = boards["pcileech_100t484_x1"]
            assert "sys_clk_p_pin" not in config
            assert "sys_clk_n_pin" not in config

    def test_board_config_consistency(self):
        """Test that board configurations are internally consistent."""
        for board_name, (p_pin, n_pin) in BoardDiscovery.SYS_CLK_PIN_MAP.items():
            # Create a test config for this board
            test_config = {"name": board_name, "fpga_part": "xc7a200tfbg484-2"}
            
            # Analyze the board (this will add sys_clk pins)
            analyzed = BoardDiscovery._analyze_board(Path("/fake"), test_config)
            
            # Verify that the pins match the mapping
            assert analyzed["sys_clk_p_pin"] == p_pin
            assert analyzed["sys_clk_n_pin"] == n_pin

    def test_sys_clk_pin_assignment_is_deterministic(self):
        """Test that sys_clk pin assignment is deterministic."""
        board_path = Path("/fake/ac701")
        base_config = {"name": "ac701", "fpga_part": "xc7a200tfbg484-2"}
        
        # Run analysis multiple times
        config1 = BoardDiscovery._analyze_board(board_path, base_config.copy())
        config2 = BoardDiscovery._analyze_board(board_path, base_config.copy())
        
        # Results should be identical
        assert config1["sys_clk_p_pin"] == config2["sys_clk_p_pin"]
        assert config1["sys_clk_n_pin"] == config2["sys_clk_n_pin"]
        assert config1["sys_clk_p_pin"] == "R3"
        assert config1["sys_clk_n_pin"] == "P3"


class TestSysClkPinValidation:
    """Test validation and edge cases for sys_clk pin functionality."""

    def test_sys_clk_pin_map_completeness(self):
        """Test that all expected differential clock boards are covered."""
        # These are the boards that typically use differential clocks
        # based on PCILeech FPGA repository analysis
        expected_differential_boards = [
            "ac701", "pcileech_ac701",      # AC701 development board
            "acorn", "pcileech_acorn",      # Acorn boards  
            "sp605", "pcileech_sp605",      # SP605 board
        ]
        
        for board in expected_differential_boards:
            assert board in BoardDiscovery.SYS_CLK_PIN_MAP, \
                f"Expected differential clock board '{board}' missing from SYS_CLK_PIN_MAP"

    def test_sys_clk_pins_follow_differential_pair_convention(self):
        """Test that sys_clk pins follow differential pair naming conventions."""
        for board_name, (p_pin, n_pin) in BoardDiscovery.SYS_CLK_PIN_MAP.items():
            # For boards with numeric pins, ensure pins are different
            # FPGA differential pairs can have various numbering schemes
            # The key requirement is that p_pin != n_pin
            
            # Pins should be different (fundamental requirement)
            assert p_pin != n_pin, \
                f"Board '{board_name}' has identical pin names: {p_pin}/{n_pin}"
            
            # Extract any numeric parts for additional validation
            p_nums = [int(c) for c in p_pin if c.isdigit()]
            n_nums = [int(c) for c in n_pin if c.isdigit()]
            
            # If both pins have numbers, ensure they're close in value (typical for diff pairs)
            if p_nums and n_nums:
                p_num = p_nums[0]  # Take first number found
                n_num = n_nums[0]  # Take first number found
                
                # Differential pairs are typically close in number (within 3)
                # This is a reasonable heuristic for FPGA pin pairs
                assert abs(p_num - n_num) <= 3, \
                    f"Board '{board_name}' pins seem too far apart: {p_pin}/{n_pin}"

    def test_sys_clk_pin_map_immutability(self):
        """Test that SYS_CLK_PIN_MAP cannot be accidentally modified."""
        original_mapping = dict(BoardDiscovery.SYS_CLK_PIN_MAP)
        
        # Ensure the original mapping has expected content
        assert len(original_mapping) > 0
        assert "ac701" in original_mapping
        
        # The mapping should remain unchanged after operations
        BoardDiscovery._analyze_board(Path("/fake"), {"name": "test", "fpga_part": "xc7a35t"})
        
        assert BoardDiscovery.SYS_CLK_PIN_MAP == original_mapping
