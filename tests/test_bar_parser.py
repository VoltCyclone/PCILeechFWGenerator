#!/usr/bin/env python3
"""Unit tests for the unified BAR parser."""

import pytest

from src.device_clone.bar_parser import (
    UnifiedBarParser,
    parse_bar_info_from_config_space,
    parse_bar_info_as_dicts
)
from src.device_clone.config_space_manager import BarInfo


class TestUnifiedBarParser:
    """Test cases for UnifiedBarParser."""
    
    def create_config_space_with_bars(self, bars):
        """Helper to create config space with specified BARs."""
        cfg_bytes = bytearray([0x00] * 256)
        
        for i, bar in enumerate(bars):
            if i >= 6:
                break
            bar_offset = 0x10 + (i * 4)
            bar_value = bar.get("value", 0)
            cfg_bytes[bar_offset:bar_offset + 4] = bar_value.to_bytes(4, "little")
        
        return cfg_bytes
    
    def test_parse_empty_bars(self):
        """Test parsing configuration space with no BARs."""
        cfg_bytes = self.create_config_space_with_bars([])
        result = UnifiedBarParser.parse_bars(cfg_bytes)
        assert len(result) == 0
    
    def test_parse_32bit_memory_bar(self):
        """Test parsing 32-bit memory BAR."""
        bars = [{"value": 0xF0000000}]  # 32-bit memory BAR, non-prefetchable
        cfg_bytes = self.create_config_space_with_bars(bars)
        
        result = UnifiedBarParser.parse_bars(cfg_bytes)
        
        assert len(result) == 1
        bar = result[0]
        assert isinstance(bar, BarInfo)
        assert bar.index == 0
        assert bar.bar_type == "memory"
        assert bar.address == 0xF0000000
        assert bar.is_64bit is False
        assert bar.prefetchable is False
        # Size should be 0 since we can't decode it from just the base address
        assert bar.size == 0
    
    def test_parse_64bit_memory_bar(self):
        """Test parsing 64-bit memory BAR."""
        bars = [
            {"value": 0xF0000004},  # 64-bit memory BAR (type=10b)
            {"value": 0x00000001},  # Upper 32 bits
        ]
        cfg_bytes = self.create_config_space_with_bars(bars)
        
        result = UnifiedBarParser.parse_bars(cfg_bytes)
        
        assert len(result) == 1
        bar = result[0]
        assert bar.index == 0
        assert bar.bar_type == "memory"
        assert bar.address == 0x1F0000000  # 64-bit: upper(0x1) << 32 | lower(0xF0000000)
        assert bar.is_64bit is True
        assert bar.prefetchable is False
    
    def test_parse_io_bar(self):
        """Test parsing I/O BAR."""
        bars = [{"value": 0x0000E001}]  # I/O BAR
        cfg_bytes = self.create_config_space_with_bars(bars)
        
        result = UnifiedBarParser.parse_bars(cfg_bytes)
        
        assert len(result) == 1
        bar = result[0]
        assert bar.index == 0
        assert bar.bar_type == "io"
        assert bar.address == 0x0000E000  # Address with lower bits cleared
        assert bar.is_64bit is False
    
    def test_parse_prefetchable_memory_bar(self):
        """Test parsing prefetchable memory BAR."""
        bars = [{"value": 0xF0000008}]  # Memory BAR with prefetchable bit
        cfg_bytes = self.create_config_space_with_bars(bars)
        
        result = UnifiedBarParser.parse_bars(cfg_bytes)
        
        assert len(result) == 1
        bar = result[0]
        assert bar.prefetchable is True
        assert bar.bar_type == "memory"
    
    def test_parse_multiple_bars(self):
        """Test parsing multiple BARs."""
        bars = [
            {"value": 0xF0000000},  # 32-bit memory BAR
            {"value": 0x0000E001},  # I/O BAR
            {"value": 0xE0000004},  # 64-bit memory BAR
            {"value": 0x00000002},  # Upper 32 bits for 64-bit BAR
        ]
        cfg_bytes = self.create_config_space_with_bars(bars)
        
        result = UnifiedBarParser.parse_bars(cfg_bytes)
        
        assert len(result) == 3  # Two memory BARs + one I/O BAR
        
        # Check first BAR (32-bit memory)
        assert result[0].index == 0
        assert result[0].bar_type == "memory"
        assert result[0].is_64bit is False
        
        # Check second BAR (I/O)
        assert result[1].index == 1
        assert result[1].bar_type == "io"
        
        # Check third BAR (64-bit memory)
        assert result[2].index == 2
        assert result[2].bar_type == "memory"
        assert result[2].is_64bit is True
    
    def test_parse_hex_string_input(self):
        """Test parsing with hex string input."""
        bars = [{"value": 0xF0000000}]
        cfg_bytes = self.create_config_space_with_bars(bars)
        hex_string = cfg_bytes.hex()
        
        result = UnifiedBarParser.parse_bars(hex_string)
        
        assert len(result) == 1
        assert result[0].bar_type == "memory"
    
    def test_parse_invalid_hex_string(self):
        """Test parsing with invalid hex string."""
        result = UnifiedBarParser.parse_bars("INVALID_HEX")
        assert len(result) == 0
    
    def test_parse_too_short_config_space(self):
        """Test parsing with config space that's too short."""
        cfg_bytes = bytearray([0x00] * 20)  # Too short for BARs
        result = UnifiedBarParser.parse_bars(cfg_bytes)
        assert len(result) == 0
    
    def test_skip_empty_bars(self):
        """Test that empty BARs (value=0) are skipped."""
        bars = [
            {"value": 0xF0000000},  # Valid BAR
            {"value": 0x00000000},  # Empty BAR - should be skipped
            {"value": 0x0000E001},  # Valid BAR
        ]
        cfg_bytes = self.create_config_space_with_bars(bars)
        
        result = UnifiedBarParser.parse_bars(cfg_bytes)
        
        # Should only get 2 BARs (the empty one is skipped)
        assert len(result) == 2
        assert result[0].index == 0
        assert result[1].index == 2  # Skipped BAR 1
    
    def test_64bit_bar_consumes_two_slots(self):
        """Test that 64-bit BARs properly consume two BAR slots."""
        bars = [
            {"value": 0xF0000004},  # 64-bit memory BAR at slot 0
            {"value": 0x00000001},  # Upper 32 bits at slot 1
            {"value": 0x0000E001},  # I/O BAR at slot 2
        ]
        cfg_bytes = self.create_config_space_with_bars(bars)
        
        result = UnifiedBarParser.parse_bars(cfg_bytes)
        
        # Should get 2 BARs total (64-bit at index 0, I/O at index 2)
        assert len(result) == 2
        assert result[0].index == 0
        assert result[0].is_64bit is True
        assert result[1].index == 2
        assert result[1].bar_type == "io"


class TestBarParserConvenienceFunctions:
    """Test convenience wrapper functions."""
    
    def test_parse_bar_info_from_config_space(self):
        """Test the main convenience function."""
        cfg_bytes = bytearray([0x00] * 256)
        cfg_bytes[0x10:0x14] = (0xF0000000).to_bytes(4, "little")
        
        result = parse_bar_info_from_config_space(cfg_bytes)
        
        assert len(result) == 1
        assert isinstance(result[0], BarInfo)
        assert result[0].address == 0xF0000000
    
    def test_parse_bar_info_as_dicts(self):
        """Test dict conversion function."""
        cfg_bytes = bytearray([0x00] * 256)
        cfg_bytes[0x10:0x14] = (0xF0000008).to_bytes(4, "little")  # Prefetchable
        
        result = parse_bar_info_as_dicts(cfg_bytes)
        
        assert len(result) == 1
        assert isinstance(result[0], dict)
        assert "index" in result[0]
        assert "bar_type" in result[0]
        assert "address" in result[0]
        assert "size" in result[0]
        assert "is_64bit" in result[0]
        assert "prefetchable" in result[0]
        assert result[0]["prefetchable"] is True


class TestBarSizeHandling:
    """Test BAR size estimation and handling."""
    
    def test_size_returns_zero_when_cannot_decode(self):
        """Test that size is 0 when we cannot decode it from config space alone."""
        cfg_bytes = bytearray([0x00] * 256)
        cfg_bytes[0x10:0x14] = (0xF0000000).to_bytes(4, "little")
        
        result = UnifiedBarParser.parse_bars(cfg_bytes)
        
        assert len(result) == 1
        # Without sysfs or write-back test, size should be 0
        # This is correct behavior - we need actual discovery mechanisms
        assert result[0].size == 0
    
    def test_io_bar_size_handling(self):
        """Test I/O BAR size handling."""
        cfg_bytes = bytearray([0x00] * 256)
        cfg_bytes[0x10:0x14] = (0x0000E001).to_bytes(4, "little")
        
        result = UnifiedBarParser.parse_bars(cfg_bytes)
        
        assert len(result) == 1
        assert result[0].bar_type == "io"
        # Size should be 0 since we can't reliably determine it
        assert result[0].size == 0


class TestEdgeCases:
    """Test edge cases and error handling."""
    
    def test_all_bars_empty(self):
        """Test config space with all BARs empty."""
        cfg_bytes = bytearray([0x00] * 256)
        result = UnifiedBarParser.parse_bars(cfg_bytes)
        assert len(result) == 0
    
    def test_maximum_bars(self):
        """Test config space with all 6 BAR slots used."""
        bars = [
            {"value": 0xF0000000},
            {"value": 0xE0000000},
            {"value": 0xD0000000},
            {"value": 0xC0000000},
            {"value": 0xB0000000},
            {"value": 0xA0000000},
        ]
        cfg_bytes = bytearray([0x00] * 256)
        for i, bar in enumerate(bars):
            offset = 0x10 + (i * 4)
            cfg_bytes[offset:offset + 4] = bar["value"].to_bytes(4, "little")
        
        result = UnifiedBarParser.parse_bars(cfg_bytes)
        
        # Should get all 6 BARs
        assert len(result) == 6
    
    def test_64bit_bar_at_last_slot(self):
        """Test 64-bit BAR at the last available slot (should work)."""
        bars = [
            {"value": 0x00000000},  # Empty
            {"value": 0x00000000},  # Empty
            {"value": 0x00000000},  # Empty
            {"value": 0x00000000},  # Empty
            {"value": 0xF0000004},  # 64-bit BAR at slot 4
            {"value": 0x00000001},  # Upper 32 bits at slot 5
        ]
        cfg_bytes = bytearray([0x00] * 256)
        for i, bar in enumerate(bars):
            offset = 0x10 + (i * 4)
            cfg_bytes[offset:offset + 4] = bar["value"].to_bytes(4, "little")
        
        result = UnifiedBarParser.parse_bars(cfg_bytes)
        
        # Should get 1 BAR (the 64-bit one)
        assert len(result) == 1
        assert result[0].index == 4
        assert result[0].is_64bit is True
    
    def test_config_space_with_whitespace(self):
        """Test hex string input with whitespace."""
        cfg_bytes = bytearray([0x00] * 256)
        cfg_bytes[0x10:0x14] = (0xF0000000).to_bytes(4, "little")
        
        # Add whitespace to hex string (space every 2 hex chars = 1 byte)
        hex_with_spaces = " ".join([cfg_bytes.hex()[i:i+2] for i in range(0, len(cfg_bytes.hex()), 2)])
        
        result = UnifiedBarParser.parse_bars(hex_with_spaces)
        
        assert len(result) == 1


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
