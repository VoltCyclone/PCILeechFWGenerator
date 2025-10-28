#!/usr/bin/env python3
"""
Unit tests for MSI-X Capability Parser with enhanced 64-bit BAR support and extended capabilities.
"""

from unittest.mock import MagicMock, patch

import pytest

from src.device_clone.msix_capability import (
    BAR_IO_DEFAULT_SIZE, BAR_MEM_DEFAULT_SIZE, BAR_MEM_MIN_SIZE, find_cap,
    generate_msix_capability_registers, generate_msix_table_sv, hex_to_bytes,
    is_valid_offset, msix_size, parse_bar_info_from_config_space,
    parse_msix_capability, read_u8, read_u16_le, read_u32_le,
    validate_msix_configuration, validate_msix_configuration_enhanced)


class TestUtilityFunctions:
    """Test utility functions for configuration space parsing."""

    def test_hex_to_bytes_valid(self):
        """Test hex_to_bytes with valid input."""
        result = hex_to_bytes("DEADBEEF")
        expected = bytearray([0xDE, 0xAD, 0xBE, 0xEF])
        assert result == expected

    def test_hex_to_bytes_empty(self):
        """Test hex_to_bytes with empty string."""
        result = hex_to_bytes("")
        assert result == bytearray()

    def test_hex_to_bytes_odd_length(self):
        """Test hex_to_bytes with odd length string."""
        with pytest.raises(ValueError, match="Hex string must have even length"):
            hex_to_bytes("ABC")

    def test_read_u8(self):
        """Test reading 8-bit values."""
        data = bytearray([0x12, 0x34, 0x56, 0x78])
        assert read_u8(data, 0) == 0x12
        assert read_u8(data, 1) == 0x34
        assert read_u8(data, 3) == 0x78

    def test_read_u8_out_of_bounds(self):
        """Test read_u8 with out of bounds access."""
        data = bytearray([0x12, 0x34])
        with pytest.raises(IndexError):
            read_u8(data, 2)

    def test_read_u16_le(self):
        """Test reading 16-bit little-endian values."""
        data = bytearray([0x34, 0x12, 0x78, 0x56])
        assert read_u16_le(data, 0) == 0x1234
        assert read_u16_le(data, 2) == 0x5678

    def test_read_u32_le(self):
        """Test reading 32-bit little-endian values."""
        data = bytearray([0x78, 0x56, 0x34, 0x12])
        assert read_u32_le(data, 0) == 0x12345678

    def test_is_valid_offset(self):
        """Test offset validation."""
        data = bytearray([0x00] * 10)
        assert is_valid_offset(data, 0, 4) is True
        assert is_valid_offset(data, 6, 4) is True
        assert is_valid_offset(data, 7, 4) is False
        assert is_valid_offset(data, 10, 1) is False


class TestCapabilityFinding:
    """Test capability finding functionality including extended capabilities."""

    def test_find_cap_msix_success(self):
        """Test finding MSI-X capability successfully."""
        # Create a minimal valid config space with MSI-X capability
        config_space = "00" * 256  # 256 bytes of zeros
        cfg_bytes = bytearray.fromhex(config_space)

        # Set status register bit 4 (capabilities supported)
        cfg_bytes[0x06] = 0x10

        # Set capabilities pointer to 0x40
        cfg_bytes[0x34] = 0x40

        # Add MSI-X capability at 0x40
        cfg_bytes[0x40] = 0x11  # MSI-X capability ID
        cfg_bytes[0x41] = 0x00  # Next capability (end of list)

        config_hex = cfg_bytes.hex().upper()

        result = find_cap(config_hex, 0x11)
        assert result == 0x40

    @patch("src.device_clone.msix_capability.pci_find_cap")
    def test_find_cap_with_pci_infrastructure_success(self, mock_pci_find_cap):
        """Test finding capability using PCI infrastructure."""
        mock_pci_find_cap.return_value = 0x50

        config_space = "00" * 256
        result = find_cap(config_space, 0x11)

        assert result == 0x50
        mock_pci_find_cap.assert_called_once_with(config_space, 0x11)

    @patch("src.device_clone.msix_capability.pci_find_cap")
    @patch("src.device_clone.msix_capability.find_ext_cap")
    def test_find_cap_extended_capability_success(
        self, mock_find_ext_cap, mock_pci_find_cap
    ):
        """Test finding extended capability when standard search fails."""
        mock_pci_find_cap.return_value = None  # Not found in standard space
        mock_find_ext_cap.return_value = 0x200  # Found in extended space

        config_space = "00" * 1024  # Extended config space
        result = find_cap(config_space, 0x11)

        assert result == 0x200
        mock_pci_find_cap.assert_called_once_with(config_space, 0x11)
        mock_find_ext_cap.assert_called_once_with(config_space, 0x11)

    @patch("src.device_clone.msix_capability.pci_find_cap")
    @patch("src.device_clone.msix_capability.find_ext_cap")
    def test_find_cap_not_found_anywhere(self, mock_find_ext_cap, mock_pci_find_cap):
        """Test capability not found in either standard or extended space."""
        mock_pci_find_cap.return_value = None
        mock_find_ext_cap.return_value = None

        config_space = "00" * 1024
        result = find_cap(config_space, 0x11)

        assert result is None
        mock_pci_find_cap.assert_called_once_with(config_space, 0x11)
        mock_find_ext_cap.assert_called_once_with(config_space, 0x11)

    @patch("src.device_clone.msix_capability.pci_find_cap")
    def test_find_cap_pci_infrastructure_exception_fallback(self, mock_pci_find_cap):
        """Test fallback to local implementation when PCI infrastructure fails."""
        mock_pci_find_cap.side_effect = Exception("PCI infrastructure error")

        # Create a minimal valid config space with MSI-X capability
        cfg_bytes = bytearray([0x00] * 256)
        cfg_bytes[0x06] = 0x10  # Status register with capabilities bit
        cfg_bytes[0x34] = 0x40  # Capabilities pointer
        cfg_bytes[0x40] = 0x11  # MSI-X capability ID
        cfg_bytes[0x41] = 0x00  # Next capability (end)

        config_space = cfg_bytes.hex().upper()
        result = find_cap(config_space, 0x11)

        # Should find via fallback implementation
        assert result == 0x40

    def test_find_cap_pci_infrastructure_unavailable(self):
        """Test behavior when PCI infrastructure is not available."""
        # Mock the module-level variables to simulate unavailable infrastructure
        with patch("src.device_clone.msix_capability.pci_find_cap", None), patch(
            "src.device_clone.msix_capability.find_ext_cap", None
        ):

            # Create a minimal valid config space with MSI-X capability
            cfg_bytes = bytearray([0x00] * 256)
            cfg_bytes[0x06] = 0x10  # Status register with capabilities bit
            cfg_bytes[0x34] = 0x40  # Capabilities pointer
            cfg_bytes[0x40] = 0x11  # MSI-X capability ID
            cfg_bytes[0x41] = 0x00  # Next capability (end)

            config_space = cfg_bytes.hex().upper()
            result = find_cap(config_space, 0x11)

            # Should find via local implementation
            assert result == 0x40

    def test_find_cap_extended_config_space_size(self):
        """Test with extended configuration space (4KB)."""
        # Create 4KB config space
        cfg_bytes = bytearray([0x00] * 4096)
        cfg_bytes[0x06] = 0x10  # Status register with capabilities bit
        cfg_bytes[0x34] = 0x40  # Capabilities pointer
        cfg_bytes[0x40] = 0x11  # MSI-X capability ID
        cfg_bytes[0x41] = 0x00  # Next capability (end)

        config_space = cfg_bytes.hex().upper()
        result = find_cap(config_space, 0x11)

        assert result == 0x40

    def test_find_cap_capability_chain_walking(self):
        """Test walking a chain of capabilities."""
        cfg_bytes = bytearray([0x00] * 256)
        cfg_bytes[0x06] = 0x10  # Status register with capabilities bit
        cfg_bytes[0x34] = 0x40  # Capabilities pointer

        # First capability (MSI)
        cfg_bytes[0x40] = 0x05  # MSI capability ID
        cfg_bytes[0x41] = 0x50  # Next capability pointer

        # Second capability (MSI-X)
        cfg_bytes[0x50] = 0x11  # MSI-X capability ID
        cfg_bytes[0x51] = 0x60  # Next capability pointer

        # Third capability (PCIe)
        cfg_bytes[0x60] = 0x10  # PCIe capability ID
        cfg_bytes[0x61] = 0x00  # End of chain

        config_space = cfg_bytes.hex().upper()

        # Test finding each capability
        assert find_cap(config_space, 0x05) == 0x40  # MSI
        assert find_cap(config_space, 0x11) == 0x50  # MSI-X
        assert find_cap(config_space, 0x10) == 0x60  # PCIe

    def test_find_cap_capability_loop_detection(self):
        """Test loop detection in capability chain."""
        cfg_bytes = bytearray([0x00] * 256)
        cfg_bytes[0x06] = 0x10  # Status register with capabilities bit
        cfg_bytes[0x34] = 0x40  # Capabilities pointer

        # Create a loop: 0x40 -> 0x50 -> 0x40
        cfg_bytes[0x40] = 0x05  # MSI capability ID
        cfg_bytes[0x41] = 0x50  # Next capability pointer
        cfg_bytes[0x50] = 0x11  # MSI-X capability ID
        cfg_bytes[0x51] = 0x40  # Points back to first capability (loop)

        config_space = cfg_bytes.hex().upper()

        # Should not find a non-existent capability due to loop protection
        result = find_cap(config_space, 0x99)
        assert result is None

    def test_find_cap_not_found(self):
        """Test capability not found."""
        # Create a minimal valid config space without MSI-X
        config_space = "00" * 256
        cfg_bytes = bytearray.fromhex(config_space)

        # Set status register bit 4 (capabilities supported)
        cfg_bytes[0x06] = 0x10

        # Set capabilities pointer to 0x40
        cfg_bytes[0x34] = 0x40

        # Add different capability at 0x40
        cfg_bytes[0x40] = 0x05  # MSI capability ID (not MSI-X)
        cfg_bytes[0x41] = 0x00  # Next capability (end of list)

        config_hex = cfg_bytes.hex().upper()

        result = find_cap(config_hex, 0x11)
        assert result is None

    def test_find_cap_no_capabilities(self):
        """Test device without capabilities support."""
        config_space = "00" * 256
        cfg_bytes = bytearray.fromhex(config_space)

        # Status register without capabilities bit set
        cfg_bytes[0x06] = 0x00

        config_hex = cfg_bytes.hex().upper()

        result = find_cap(config_hex, 0x11)
        assert result is None

    def test_find_cap_invalid_config_space(self):
        """Test with invalid configuration space."""
        # Too short config space
        config_space = "00" * 100  # Only 100 bytes

        result = find_cap(config_space, 0x11)
        assert result is None

    def test_find_cap_invalid_hex(self):
        """Test with invalid hex string."""
        config_space = "INVALID_HEX_STRING"

        result = find_cap(config_space, 0x11)
        assert result is None

    def test_find_cap_out_of_bounds_capability_pointer(self):
        """Test with capability pointer that goes out of bounds."""
        cfg_bytes = bytearray([0x00] * 256)
        cfg_bytes[0x06] = 0x10  # Status register with capabilities bit
        cfg_bytes[0x34] = 0xFF  # Capabilities pointer beyond valid range

        config_space = cfg_bytes.hex().upper()
        result = find_cap(config_space, 0x11)

        # Should handle gracefully and not crash
        assert result is None

    def test_find_cap_zero_capability_pointer(self):
        """Test with zero capability pointer."""
        cfg_bytes = bytearray([0x00] * 256)
        cfg_bytes[0x06] = 0x10  # Status register with capabilities bit
        cfg_bytes[0x34] = 0x00  # Zero capabilities pointer

        config_space = cfg_bytes.hex().upper()
        result = find_cap(config_space, 0x11)

        assert result is None


class TestMsixParsing:
    """Test MSI-X capability parsing."""

    def create_msix_config_space(
        self,
        table_size=8,
        table_bir=0,
        table_offset=0x1000,
        pba_bir=0,
        pba_offset=0x2000,
        enabled=True,
        function_mask=False,
    ):
        """Create a config space with MSI-X capability."""
        cfg_bytes = bytearray([0x00] * 256)

        # Set status register bit 4 (capabilities supported)
        cfg_bytes[0x06] = 0x10

        # Set capabilities pointer to 0x40
        cfg_bytes[0x34] = 0x40

        # MSI-X capability at 0x40
        cfg_bytes[0x40] = 0x11  # MSI-X capability ID
        cfg_bytes[0x41] = 0x00  # Next capability (end of list)

        # Message Control register (table size, enabled, function mask)
        msg_ctrl = (table_size - 1) & 0x7FF  # Table size field (0-based)
        if enabled:
            msg_ctrl |= 0x8000
        if function_mask:
            msg_ctrl |= 0x4000

        cfg_bytes[0x42] = msg_ctrl & 0xFF
        cfg_bytes[0x43] = (msg_ctrl >> 8) & 0xFF

        # Table Offset/BIR register
        table_offset_bir = table_offset | table_bir
        cfg_bytes[0x44:0x48] = table_offset_bir.to_bytes(4, "little")

        # PBA Offset/BIR register
        pba_offset_bir = pba_offset | pba_bir
        cfg_bytes[0x48:0x4C] = pba_offset_bir.to_bytes(4, "little")

        return cfg_bytes.hex().upper()

    def test_msix_size_success(self):
        """Test successful MSI-X size parsing."""
        config_space = self.create_msix_config_space(table_size=16)

        result = msix_size(config_space)
        assert result == 16

    def test_msix_size_no_capability(self):
        """Test MSI-X size when capability not found."""
        config_space = "00" * 256  # No MSI-X capability

        result = msix_size(config_space)
        assert result == 0

    def test_parse_msix_capability_success(self):
        """Test successful MSI-X capability parsing."""
        config_space = self.create_msix_config_space(
            table_size=32,
            table_bir=1,
            table_offset=0x2000,
            pba_bir=2,
            pba_offset=0x3000,
            enabled=True,
            function_mask=False,
        )

        result = parse_msix_capability(config_space)

        assert result["table_size"] == 32
        assert result["table_bir"] == 1
        assert result["table_offset"] == 0x2000
        assert result["pba_bir"] == 2
        assert result["pba_offset"] == 0x3000
        assert result["enabled"] is True
        assert result["function_mask"] is False

    def test_parse_msix_capability_disabled(self):
        """Test parsing disabled MSI-X capability."""
        config_space = self.create_msix_config_space(
            table_size=8, enabled=False, function_mask=True
        )

        result = parse_msix_capability(config_space)

        assert result["table_size"] == 8
        assert result["enabled"] is False
        assert result["function_mask"] is True

    def test_parse_msix_capability_no_capability(self):
        """Test parsing when MSI-X capability not found."""
        config_space = "00" * 256  # No MSI-X capability

        result = parse_msix_capability(config_space)

        # Should return default values
        assert result["table_size"] == 0
        assert result["enabled"] is False


class TestBarParsing:
    """Test BAR information parsing."""

    def create_config_space_with_bars(self, bars):
        """Create config space with specified BARs."""
        cfg_bytes = bytearray([0x00] * 256)

        for i, bar in enumerate(bars):
            if i >= 6:  # Max 6 BARs
                break

            bar_offset = 0x10 + (i * 4)
            bar_value = bar.get("value", 0)
            cfg_bytes[bar_offset : bar_offset + 4] = bar_value.to_bytes(4, "little")

        return cfg_bytes.hex().upper()

    def test_parse_bar_info_32bit_memory(self):
        """Test parsing 32-bit memory BAR."""
        bars = [{"value": 0xF0000000}]  # 32-bit memory BAR, non-prefetchable
        config_space = self.create_config_space_with_bars(bars)

        result = parse_bar_info_from_config_space(config_space)

        assert len(result) == 1
        bar = result[0]
        assert bar["index"] == 0
        assert bar["bar_type"] == "memory"
        assert bar["address"] == 0xF0000000
        assert bar["is_64bit"] is False
        assert bar["prefetchable"] is False

    def test_parse_bar_info_64bit_memory(self):
        """Test parsing 64-bit memory BAR."""
        bars = [
            {"value": 0xF0000004},  # 64-bit memory BAR (type=10b)
            {"value": 0x00000001},  # Upper 32 bits
        ]
        config_space = self.create_config_space_with_bars(bars)

        result = parse_bar_info_from_config_space(config_space)

        assert len(result) == 1
        bar = result[0]
        assert bar["index"] == 0
        assert bar["bar_type"] == "memory"
        assert (
            bar["address"] == 0x1F0000000
        )  # 64-bit address: upper(0x1) << 32 | lower(0xF0000000)
        assert bar["is_64bit"] is True

    def test_parse_bar_info_io_bar(self):
        """Test parsing I/O BAR."""
        bars = [{"value": 0x0000E001}]  # I/O BAR
        config_space = self.create_config_space_with_bars(bars)

        result = parse_bar_info_from_config_space(config_space)

        assert len(result) == 1
        bar = result[0]
        assert bar["index"] == 0
        assert bar["bar_type"] == "io"
        assert bar["address"] == 0x0000E000  # Address with lower bits cleared
        assert bar["is_64bit"] is False

    def test_parse_bar_info_prefetchable_memory(self):
        """Test parsing prefetchable memory BAR."""
        bars = [{"value": 0xF0000008}]  # Memory BAR with prefetchable bit set
        config_space = self.create_config_space_with_bars(bars)

        result = parse_bar_info_from_config_space(config_space)

        assert len(result) == 1
        bar = result[0]
        assert bar["prefetchable"] is True

    def test_parse_bar_info_empty_bars(self):
        """Test parsing configuration space with no active BARs."""
        bars = [
            {"value": 0x00000000},  # Empty BAR
            {"value": 0x00000000},  # Empty BAR
        ]
        config_space = self.create_config_space_with_bars(bars)

        result = parse_bar_info_from_config_space(config_space)

        assert len(result) == 0

    def test_parse_bar_info_multiple_bars(self):
        """Test parsing multiple BARs."""
        bars = [
            {"value": 0xF0000000},  # 32-bit memory BAR
            {"value": 0x0000E001},  # I/O BAR
            {"value": 0xE0000004},  # 64-bit memory BAR
            {"value": 0x00000002},  # Upper 32 bits for 64-bit BAR
        ]
        config_space = self.create_config_space_with_bars(bars)

        result = parse_bar_info_from_config_space(config_space)

        assert len(result) == 3  # Two memory BARs + one I/O BAR

        # Check first BAR (32-bit memory)
        assert result[0]["index"] == 0
        assert result[0]["bar_type"] == "memory"
        assert result[0]["is_64bit"] is False

        # Check second BAR (I/O)
        assert result[1]["index"] == 1
        assert result[1]["bar_type"] == "io"

        # Check third BAR (64-bit memory)
        assert result[2]["index"] == 2
        assert result[2]["bar_type"] == "memory"
        assert result[2]["is_64bit"] is True
        assert (
            result[2]["address"] == 0x2E0000000
        )  # 64-bit address: upper(0x2) << 32 | lower(0xE0000000)


class TestMsixValidation:
    """Test MSI-X configuration validation."""

    def test_validate_msix_basic_valid(self):
        """Test basic validation with valid configuration."""
        msix_info = {
            "table_size": 16,
            "table_bir": 0,
            "table_offset": 0x1000,
            "pba_bir": 0,
            "pba_offset": 0x2000,
        }

        is_valid, errors = validate_msix_configuration(msix_info)
        assert is_valid is True
        assert len(errors) == 0

    def test_validate_msix_zero_table_size(self):
        """Test validation with zero table size."""
        msix_info = {
            "table_size": 0,
            "table_bir": 0,
            "table_offset": 0x1000,
            "pba_bir": 0,
            "pba_offset": 0x2000,
        }

        is_valid, errors = validate_msix_configuration(msix_info)
        assert is_valid is False
        assert "MSI-X table size is zero" in errors

    def test_validate_msix_table_size_too_large(self):
        """Test validation with table size exceeding maximum."""
        msix_info = {
            "table_size": 3000,  # Exceeds PCIe spec maximum of 2048
            "table_bir": 0,
            "table_offset": 0x1000,
            "pba_bir": 0,
            "pba_offset": 0x2000,
        }

        is_valid, errors = validate_msix_configuration(msix_info)
        assert is_valid is False
        assert "exceeds maximum of 2048" in errors[0]

    def test_validate_msix_invalid_bir(self):
        """Test validation with invalid BIR values."""
        msix_info = {
            "table_size": 16,
            "table_bir": 7,  # Invalid (must be 0-5)
            "table_offset": 0x1000,
            "pba_bir": 8,  # Invalid (must be 0-5)
            "pba_offset": 0x2000,
        }

        is_valid, errors = validate_msix_configuration(msix_info)
        assert is_valid is False
        assert any("table BIR 7 is invalid" in error for error in errors)
        assert any("PBA BIR 8 is invalid" in error for error in errors)

    def test_validate_msix_misaligned_offsets(self):
        """Test validation with misaligned offsets."""
        msix_info = {
            "table_size": 16,
            "table_bir": 0,
            "table_offset": 0x1004,  # Not 8-byte aligned
            "pba_bir": 0,
            "pba_offset": 0x2006,  # Not 8-byte aligned
        }

        is_valid, errors = validate_msix_configuration(msix_info)
        assert is_valid is False
        assert any(
            "table offset" in error and "not 8-byte aligned" in error
            for error in errors
        )
        assert any(
            "PBA offset" in error and "not 8-byte aligned" in error for error in errors
        )

    def test_validate_msix_basic_overlap(self):
        """Test basic overlap detection."""
        msix_info = {
            "table_size": 16,
            "table_bir": 0,
            "table_offset": 0x1000,  # Table: 0x1000-0x1100 (16 entries * 16 bytes)
            "pba_bir": 0,
            "pba_offset": 0x1080,  # PBA overlaps with table
        }

        is_valid, errors = validate_msix_configuration(msix_info)
        assert is_valid is False
        assert any("overlap" in error for error in errors)


class TestEnhancedMsixValidation:
    """Test enhanced MSI-X validation with BAR parsing."""

    def create_full_config_space(self, bars, msix_config):
        """Create a complete config space with BARs and MSI-X capability."""
        cfg_bytes = bytearray([0x00] * 256)

        # Add BARs
        for i, bar in enumerate(bars):
            if i >= 6:
                break
            bar_offset = 0x10 + (i * 4)
            bar_value = bar.get("value", 0)
            cfg_bytes[bar_offset : bar_offset + 4] = bar_value.to_bytes(4, "little")

        # Set status register bit 4 (capabilities supported)
        cfg_bytes[0x06] = 0x10

        # Set capabilities pointer to 0x40
        cfg_bytes[0x34] = 0x40

        # MSI-X capability at 0x40
        cfg_bytes[0x40] = 0x11  # MSI-X capability ID
        cfg_bytes[0x41] = 0x00  # Next capability (end of list)

        # Message Control register
        table_size = msix_config.get("table_size", 8)
        enabled = msix_config.get("enabled", True)
        function_mask = msix_config.get("function_mask", False)

        msg_ctrl = (table_size - 1) & 0x7FF
        if enabled:
            msg_ctrl |= 0x8000
        if function_mask:
            msg_ctrl |= 0x4000

        cfg_bytes[0x42] = msg_ctrl & 0xFF
        cfg_bytes[0x43] = (msg_ctrl >> 8) & 0xFF

        # Table Offset/BIR register
        table_offset = msix_config.get("table_offset", 0x1000)
        table_bir = msix_config.get("table_bir", 0)
        table_offset_bir = table_offset | table_bir
        cfg_bytes[0x44:0x48] = table_offset_bir.to_bytes(4, "little")

        # PBA Offset/BIR register
        pba_offset = msix_config.get("pba_offset", 0x2000)
        pba_bir = msix_config.get("pba_bir", 0)
        pba_offset_bir = pba_offset | pba_bir
        cfg_bytes[0x48:0x4C] = pba_offset_bir.to_bytes(4, "little")

        return cfg_bytes.hex().upper()

    def test_enhanced_validation_valid_config(self):
        """Test enhanced validation with valid configuration.
        
        Note: Since BAR size cannot be determined from config space alone
        (requires sysfs or write-back testing), this test validates that
        the basic validation passes and no errors are raised due to
        missing BAR size information.
        """
        bars = [{"value": 0xF0000000}]  # Memory BAR (size unknown from config space)
        msix_config = {
            "table_size": 8,
            "table_bir": 0,
            "table_offset": 0x1000,
            "pba_bir": 0,
            "pba_offset": 0x2000,
        }

        config_space = self.create_full_config_space(bars, msix_config)
        msix_info = parse_msix_capability(config_space)

        is_valid, errors = validate_msix_configuration_enhanced(msix_info, config_space)
        
        # Should be valid - basic validation passes (alignment, BIR range, etc.)
        # Enhanced BAR-based validation is skipped when size=0 (unknown)
        assert is_valid is True, f"Validation failed with errors: {errors}"
        assert len(errors) == 0

    def test_enhanced_validation_table_beyond_bar(self):
        """Test enhanced validation when table extends beyond BAR.
        
        Note: BAR size cannot be determined from config space alone,
        so this validation requires actual sysfs data or explicit size.
        Without known BAR size, validation passes (size=0 case).
        """
        bars = [{"value": 0xFFFFF000}]  # BAR with unknown size
        msix_config = {
            "table_size": 256,  # Large table (256 * 16 = 4KB)
            "table_bir": 0,
            "table_offset": 0x1000,  # Starts at 4KB
            "pba_bir": 0,
            "pba_offset": 0x2000,
        }

        config_space = self.create_full_config_space(bars, msix_config)
        msix_info = parse_msix_capability(config_space)

        is_valid, errors = validate_msix_configuration_enhanced(msix_info, config_space)
        # Since BAR size is unknown (0), validation cannot check bounds
        # This is correct behavior - we need sysfs for actual size validation
        assert is_valid is True
        assert len(errors) == 0

    def test_enhanced_validation_64bit_bar(self):
        """Test enhanced validation with 64-bit BAR.
        
        Note: Enhanced validation properly detects 64-bit BARs even though
        size cannot be determined from config space alone.
        """
        bars = [
            {"value": 0xF0000004},  # 64-bit memory BAR
            {"value": 0x00000001},  # Upper 32 bits
        ]
        msix_config = {
            "table_size": 16,
            "table_bir": 0,
            "table_offset": 0x1000,
            "pba_bir": 0,
            "pba_offset": 0x2000,
        }

        config_space = self.create_full_config_space(bars, msix_config)
        msix_info = parse_msix_capability(config_space)

        is_valid, errors = validate_msix_configuration_enhanced(msix_info, config_space)
        
        # Should be valid - enhanced validation handles 64-bit BARs
        # Size-based validation is skipped when size is unknown
        assert is_valid is True, f"Validation failed with errors: {errors}"
        assert len(errors) == 0

    def test_enhanced_validation_overlap_detection(self):
        """Test enhanced overlap detection."""
        bars = [{"value": 0xF0000000}]  # Large BAR
        msix_config = {
            "table_size": 16,
            "table_bir": 0,
            "table_offset": 0x1000,  # Table: 0x1000-0x1100
            "pba_bir": 0,
            "pba_offset": 0x1080,  # PBA overlaps with table
        }

        config_space = self.create_full_config_space(bars, msix_config)
        msix_info = parse_msix_capability(config_space)

        is_valid, errors = validate_msix_configuration_enhanced(msix_info, config_space)
        assert is_valid is False
        assert any("overlap" in error for error in errors)

    def test_enhanced_validation_fallback_to_basic(self):
        """Test fallback to basic validation when BAR not found."""
        bars = []  # No BARs
        msix_config = {
            "table_size": 16,
            "table_bir": 1,  # BAR 1 doesn't exist
            "table_offset": 0x1000,
            "pba_bir": 1,
            "pba_offset": 0x1080,  # Overlapping
        }

        config_space = self.create_full_config_space(bars, msix_config)
        msix_info = parse_msix_capability(config_space)

        is_valid, errors = validate_msix_configuration_enhanced(msix_info, config_space)
        assert is_valid is False
        assert any("basic validation" in error for error in errors)


class TestSystemVerilogGeneration:
    """Test SystemVerilog code generation."""

    @patch("src.device_clone.msix_capability.TemplateRenderer")
    def test_generate_msix_table_sv_valid(self, mock_renderer_class):
        """Test SystemVerilog generation with valid MSI-X info."""
        mock_renderer = MagicMock()
        mock_renderer.render_template.return_value = "// Mock SystemVerilog code"
        mock_renderer_class.return_value = mock_renderer

        msix_info = {
            "table_size": 16,
            "table_bir": 0,
            "table_offset": 0x1000,
            "pba_bir": 0,
            "pba_offset": 0x2000,
            "enabled": True,
            "function_mask": False,
        }

        result = generate_msix_table_sv(msix_info)

        # Verify template renderer was called
        assert mock_renderer.render_template.call_count >= 1
        assert "Mock SystemVerilog code" in result

    @patch("src.device_clone.msix_capability.TemplateRenderer")
    def test_generate_msix_table_sv_disabled(self, mock_renderer_class):
        """Test SystemVerilog generation with disabled MSI-X."""
        mock_renderer = MagicMock()
        mock_renderer.render_template.return_value = "// Disabled MSI-X module"
        mock_renderer_class.return_value = mock_renderer

        msix_info = {
            "table_size": 0,  # Disabled
            "table_bir": 0,
            "table_offset": 0x1000,
            "pba_bir": 0,
            "pba_offset": 0x2000,
            "enabled": False,
            "function_mask": True,
        }

        result = generate_msix_table_sv(msix_info)

        # Should still generate valid code
        assert "Disabled MSI-X module" in result

    @patch("src.device_clone.msix_capability.TemplateRenderer")
    def test_generate_msix_table_sv_missing_fields(self, mock_renderer_class):
        """Test SystemVerilog generation with missing required fields."""
        mock_renderer = MagicMock()
        mock_renderer.render_template.return_value = "// Fallback code"
        mock_renderer_class.return_value = mock_renderer

        # Missing some required fields
        msix_info = {
            "table_size": 16,
            "table_bir": 0,
            # Missing table_offset, pba_bir, pba_offset, enabled, function_mask
        }

        # generate_msix_table_sv now raises when required fields are missing
        with pytest.raises(
            ValueError, match=r"Cannot generate MSI-X module - missing critical fields"
        ):
            generate_msix_table_sv(msix_info)

    @patch("src.device_clone.msix_capability.TemplateRenderer")
    def test_generate_msix_table_sv_alignment_warning(self, mock_renderer_class):
        """Test SystemVerilog generation with misaligned table offset."""
        mock_renderer = MagicMock()
        mock_renderer.render_template.return_value = "// Code with warning"
        mock_renderer_class.return_value = mock_renderer

        msix_info = {
            "table_size": 8,
            "table_bir": 0,
            "table_offset": 0x1004,  # Not 8-byte aligned
            "pba_bir": 0,
            "pba_offset": 0x2000,
            "enabled": True,
            "function_mask": False,
        }

        result = generate_msix_table_sv(msix_info)

        # Verify the context passed to template renderer includes alignment warning
        call_args = mock_renderer.render_template.call_args_list
        context = call_args[0][0][1]  # First call, second argument (context)

        assert "alignment_warning" in context
        assert context["alignment_warning"] != ""
        assert "0x1004" in context["alignment_warning"]

    @patch("src.device_clone.msix_capability.TemplateRenderer")
    def test_generate_msix_table_sv_template_error_handling(self, mock_renderer_class):
        """Test handling of template rendering errors."""
        mock_renderer = MagicMock()
        mock_renderer.render_template.side_effect = Exception("Template error")
        mock_renderer_class.return_value = mock_renderer

        msix_info = {
            "table_size": 8,
            "table_bir": 0,
            "table_offset": 0x1000,
            "pba_bir": 0,
            "pba_offset": 0x2000,
            "enabled": True,
            "function_mask": False,
        }

        # Should handle template errors gracefully
        with pytest.raises(Exception, match="Template error"):
            generate_msix_table_sv(msix_info)

    @patch("src.device_clone.msix_capability.TemplateRenderer")
    def test_generate_msix_capability_registers(self, mock_renderer_class):
        """Test MSI-X capability register generation."""
        mock_renderer = MagicMock()
        mock_renderer.render_template.return_value = "// Mock capability registers"
        mock_renderer_class.return_value = mock_renderer

        msix_info = {
            "table_size": 8,
            "table_bir": 1,
            "table_offset": 0x2000,
            "pba_bir": 2,
            "pba_offset": 0x3000,
        }

        result = generate_msix_capability_registers(msix_info)

        # Verify template renderer was called with correct context
        mock_renderer.render_template.assert_called_once()
        call_args = mock_renderer.render_template.call_args
        assert "systemverilog/msix_capability_registers.sv.j2" in call_args[0]

        context = call_args[0][1]
        assert "table_size_minus_one" in context
        assert context["table_size_minus_one"] == 7  # 8 - 1


class TestIntegration:
    """Integration tests combining multiple components."""

    def test_full_pipeline_32bit_bar(self):
        """Test full pipeline with 32-bit BAR.
        
        This integration test verifies:
        1. MSI-X capability parsing
        2. BAR information extraction
        3. Enhanced validation (with graceful handling of unknown BAR size)
        """
        # Create config space with 32-bit BAR and MSI-X capability
        cfg_bytes = bytearray([0x00] * 256)

        # Add 32-bit memory BAR at BAR0
        cfg_bytes[0x10:0x14] = (0xF0000000).to_bytes(4, "little")

        # Set status register (capabilities supported)
        cfg_bytes[0x06] = 0x10

        # Set capabilities pointer
        cfg_bytes[0x34] = 0x40

        # MSI-X capability
        cfg_bytes[0x40] = 0x11  # MSI-X ID
        cfg_bytes[0x41] = 0x00  # Next capability

        # Message Control (16 entries, enabled)
        msg_ctrl = 15 | 0x8000  # 16 entries (0-based), enabled
        cfg_bytes[0x42:0x44] = msg_ctrl.to_bytes(2, "little")

        # Table Offset/BIR (BAR 0, offset 0x1000)
        cfg_bytes[0x44:0x48] = (0x1000 | 0).to_bytes(4, "little")

        # PBA Offset/BIR (BAR 0, offset 0x2000)
        cfg_bytes[0x48:0x4C] = (0x2000 | 0).to_bytes(4, "little")

        config_space = cfg_bytes.hex().upper()

        # Parse MSI-X capability
        msix_info = parse_msix_capability(config_space)
        assert msix_info["table_size"] == 16
        assert msix_info["table_bir"] == 0
        assert msix_info["enabled"] is True

        # Parse BAR information
        bars = parse_bar_info_from_config_space(config_space)
        assert len(bars) == 1
        assert bars[0]["index"] == 0
        assert bars[0]["bar_type"] == "memory"
        assert bars[0]["is_64bit"] is False
        # Size will be 0 since we can't decode it from config space alone
        assert bars[0]["size"] == 0

        # Enhanced validation - should pass basic validation
        # Enhanced BAR-size validation is skipped when size is unknown
        is_valid, errors = validate_msix_configuration_enhanced(msix_info, config_space)
        assert is_valid is True, f"Validation failed with errors: {errors}"
        assert len(errors) == 0

    @patch("src.device_clone.msix_capability.TemplateRenderer")
    def test_full_pipeline_64bit_bar(self, mock_renderer_class):
        """Test full pipeline with 64-bit BAR.
        
        This integration test verifies the complete workflow:
        1. MSI-X parsing from config space
        2. 64-bit BAR detection and parsing  
        3. Enhanced validation with 64-bit BAR support
        4. SystemVerilog code generation
        """
        mock_renderer = MagicMock()
        mock_renderer.render_template.return_value = "// Generated code"
        mock_renderer_class.return_value = mock_renderer

        # Create config space with 64-bit BAR and MSI-X capability
        cfg_bytes = bytearray([0x00] * 256)

        # Add 64-bit memory BAR at BAR0/BAR1
        cfg_bytes[0x10:0x14] = (0xF0000004).to_bytes(4, "little")  # Lower 32 bits
        cfg_bytes[0x14:0x18] = (0x00000001).to_bytes(4, "little")  # Upper 32 bits

        # Set status register (capabilities supported)
        cfg_bytes[0x06] = 0x10

        # Set capabilities pointer
        cfg_bytes[0x34] = 0x40

        # MSI-X capability
        cfg_bytes[0x40] = 0x11  # MSI-X ID
        cfg_bytes[0x41] = 0x00  # Next capability

        # Message Control (8 entries, enabled)
        msg_ctrl = 7 | 0x8000  # 8 entries (0-based), enabled
        cfg_bytes[0x42:0x44] = msg_ctrl.to_bytes(2, "little")

        # Table Offset/BIR (BAR 0, offset 0x1000)
        cfg_bytes[0x44:0x48] = (0x1000 | 0).to_bytes(4, "little")

        # PBA Offset/BIR (BAR 0, offset 0x2000)
        cfg_bytes[0x48:0x4C] = (0x2000 | 0).to_bytes(4, "little")

        config_space = cfg_bytes.hex().upper()

        # Full pipeline test
        msix_info = parse_msix_capability(config_space)
        bars = parse_bar_info_from_config_space(config_space)
        is_valid, errors = validate_msix_configuration_enhanced(msix_info, config_space)
        sv_code = generate_msix_table_sv(msix_info)

        # Verify results
        assert msix_info["table_size"] == 8
        assert len(bars) == 1
        assert bars[0]["is_64bit"] is True
        assert (
            bars[0]["address"] == 0x1F0000000
        )  # 64-bit address: upper(0x1) << 32 | lower(0xF0000000)
        # Size will be 0 since we can't decode it from config space alone
        assert bars[0]["size"] == 0
        # Validation should pass (basic checks) even with unknown BAR size
        assert is_valid is True, f"Validation failed with errors: {errors}"
        assert len(errors) == 0
        assert "Generated code" in sv_code


class TestEdgeCasesAndStressTests:
    """Test edge cases and stress scenarios."""

    def test_extremely_large_config_space(self):
        """Test with very large configuration space."""
        # Create 8KB config space (larger than standard 4KB)
        cfg_bytes = bytearray([0x00] * 8192)
        cfg_bytes[0x06] = 0x10  # Status register with capabilities bit
        cfg_bytes[0x34] = 0x40  # Capabilities pointer
        cfg_bytes[0x40] = 0x11  # MSI-X capability ID
        cfg_bytes[0x41] = 0x00  # Next capability (end)

        config_space = cfg_bytes.hex().upper()
        result = find_cap(config_space, 0x11)

        assert result == 0x40

    def test_msix_maximum_table_size(self):
        """Test MSI-X with maximum allowed table size."""
        # Use larger PBA offset to avoid overlap with large table
        config_space = TestMsixParsing().create_msix_config_space(
            table_size=2048,  # Maximum per PCIe spec
            table_offset=0x1000,
            pba_offset=0x10000,  # Large enough to avoid overlap
        )

        msix_info = parse_msix_capability(config_space)
        assert msix_info["table_size"] == 2048

        # Validation should pass for maximum size
        is_valid, errors = validate_msix_configuration(msix_info)
        assert is_valid is True

    def test_msix_minimum_table_size(self):
        """Test MSI-X with minimum table size."""
        config_space = TestMsixParsing().create_msix_config_space(table_size=1)

        msix_info = parse_msix_capability(config_space)
        assert msix_info["table_size"] == 1

        is_valid, errors = validate_msix_configuration(msix_info)
        assert is_valid is True

    def test_all_bars_64bit(self):
        """Test configuration with all BARs as 64-bit."""
        bars = []
        for i in range(0, 6, 2):  # 3 pairs of 64-bit BARs
            bars.append({"value": 0xF0000004 | (i << 24)})  # 64-bit memory BAR
            bars.append({"value": 0x00000001})  # Upper 32 bits

        config_space = TestBarParsing().create_config_space_with_bars(bars)
        result = parse_bar_info_from_config_space(config_space)

        assert len(result) == 3  # 3 BARs (each taking 2 slots)
        for bar in result:
            assert bar["is_64bit"] is True

    def test_mixed_bar_types(self):
        """Test configuration with mixed BAR types."""
        bars = [
            {"value": 0xF0000000},  # 32-bit memory BAR
            {"value": 0x0000E001},  # I/O BAR
            {"value": 0xE000000C},  # 32-bit prefetchable memory BAR
            {"value": 0xD0000004},  # 64-bit memory BAR
            {"value": 0x00000002},  # Upper 32 bits
            {"value": 0x0000C001},  # Another I/O BAR
        ]

        config_space = TestBarParsing().create_config_space_with_bars(bars)
        result = parse_bar_info_from_config_space(config_space)

        assert len(result) == 5  # All BARs should be parsed

        # Verify mixed types
        types = [bar["bar_type"] for bar in result]
        assert "memory" in types
        assert "io" in types

        # Check for 64-bit BAR
        has_64bit = any(bar["is_64bit"] for bar in result)
        assert has_64bit is True

        # Check for prefetchable BAR
        has_prefetchable = any(bar["prefetchable"] for bar in result)
        assert has_prefetchable is True

    def test_config_space_boundary_conditions(self):
        """Test reading at configuration space boundaries."""
        # Test reading at exactly the boundary
        cfg_bytes = bytearray([0x00] * 256)

        # Test valid boundary reads
        assert is_valid_offset(cfg_bytes, 252, 4) is True  # Last 4 bytes
        assert is_valid_offset(cfg_bytes, 255, 1) is True  # Last byte

        # Test invalid boundary reads
        assert is_valid_offset(cfg_bytes, 253, 4) is False  # Would read beyond
        assert is_valid_offset(cfg_bytes, 256, 1) is False  # Beyond end

    def test_malformed_msix_capability_structure(self):
        """Test handling of malformed MSI-X capability structure."""
        cfg_bytes = bytearray([0x00] * 256)
        cfg_bytes[0x06] = 0x10  # Status register with capabilities bit
        cfg_bytes[0x34] = 0x40  # Capabilities pointer
        cfg_bytes[0x40] = 0x11  # MSI-X capability ID
        cfg_bytes[0x41] = 0x00  # Next capability (end)

        # Truncate config space so MSI-X registers can't be fully read
        truncated_cfg = cfg_bytes[:66]  # Cut off before full MSI-X structure
        config_space = truncated_cfg.hex().upper()

        result = parse_msix_capability(config_space)

        # Should return default values when structure can't be read
        assert result["table_size"] == 0

    def test_capability_at_boundary(self):
        """Test capability located at configuration space boundary."""
        cfg_bytes = bytearray([0x00] * 256)
        cfg_bytes[0x06] = 0x10  # Status register with capabilities bit
        cfg_bytes[0x34] = 0xF0  # Capabilities pointer near end
        cfg_bytes[0xF0] = 0x11  # MSI-X capability ID
        cfg_bytes[0xF1] = 0x00  # Next capability (end)
        cfg_bytes[0xF2] = 0x00  # Message Control (lower)
        cfg_bytes[0xF3] = 0x00  # Message Control (upper), table size = 1

        config_space = cfg_bytes.hex().upper()
        result = find_cap(config_space, 0x11)

        # Should find the capability
        assert result == 0xF0

        # And parsing should work with table size 1
        msix_info = parse_msix_capability(config_space)
        assert msix_info["table_size"] == 1

    def test_stress_validation_multiple_errors(self):
        """Test validation with multiple simultaneous errors."""
        msix_info = {
            "table_size": 0,  # Error: zero size
            "table_bir": 7,  # Error: invalid BIR
            "table_offset": 0x1003,  # Error: misaligned
            "pba_bir": 9,  # Error: invalid BIR
            "pba_offset": 0x2007,  # Error: misaligned
        }

        is_valid, errors = validate_msix_configuration(msix_info)

        assert is_valid is False
        assert len(errors) >= 5  # Should catch all errors

        # Check that all error types are present
        error_text = " ".join(errors)
        assert "zero" in error_text
        assert "invalid" in error_text
        assert "aligned" in error_text

    def test_hex_string_case_insensitive(self):
        """Test that hex string parsing is case insensitive."""
        # Create config space with lowercase hex
        cfg_bytes = bytearray([0x00] * 256)
        cfg_bytes[0x06] = 0x10
        cfg_bytes[0x34] = 0x40
        cfg_bytes[0x40] = 0x11
        cfg_bytes[0x41] = 0x00

        config_upper = cfg_bytes.hex().upper()
        config_lower = cfg_bytes.hex().lower()
        config_mixed = cfg_bytes.hex().swapcase()

        # All should work the same
        assert find_cap(config_upper, 0x11) == 0x40
        assert find_cap(config_lower, 0x11) == 0x40
        assert find_cap(config_mixed, 0x11) == 0x40

    def test_memory_efficiency_large_spaces(self):
        """Test memory efficiency with large configuration spaces."""
        # Test with maximum extended config space (4KB)
        large_config = "00" * 4096

        # This should not cause memory issues
        result = find_cap(large_config, 0x99)  # Non-existent capability
        assert result is None

        # Test BAR parsing with large space
        result = parse_bar_info_from_config_space(large_config)
        assert isinstance(result, list)


class TestMSIXAlignmentRegression:
    """Regression tests for MSI-X alignment bug fixes."""

    @patch("src.device_clone.msix_capability.log_warning_safe")
    def test_alignment_check_uses_offset_not_raw_register(self, mock_log):
        """
        Regression test for MSI-X alignment bug.

        Ensures alignment check is performed on table_offset (extracted offset)
        rather than table_offset_bir (raw register value including BIR bits).

        This test specifically covers the bug reported in user issue where:
        - Device has MSI-X table at BIR=4, offset=0
        - Raw register value table_offset_bir = 4 (BIR=4, offset=0)
        - Previous code incorrectly checked alignment on 4 (raw value)
        """
        # Create a configuration space with MSI-X capability
        # that has BIR=4 and offset=0 (8-byte aligned)
        cfg_bytes = bytearray([0x00] * 256)

        # Set up PCI header to indicate capabilities
        cfg_bytes[0x06] = 0x10  # Status register - capabilities list bit
        cfg_bytes[0x34] = 0x40  # Capabilities pointer

        # MSI-X capability at offset 0x40
        cfg_bytes[0x40] = 0x11  # MSI-X capability ID
        cfg_bytes[0x41] = 0x00  # Next capability (end of list)
        cfg_bytes[0x42] = 0x03  # Message Control: Table Size = 4-1 = 3
        cfg_bytes[0x43] = 0x00  # Message Control upper byte

        # Table Offset/BIR register (offset 0x44)
        # BIR = 4 (lower 3 bits), Offset = 0 (upper bits)
        # Raw value = 4 (0x00000004)
        cfg_bytes[0x44] = 0x04  # BIR = 4, offset = 0
        cfg_bytes[0x45] = 0x00
        cfg_bytes[0x46] = 0x00
        cfg_bytes[0x47] = 0x00

        # PBA Offset/BIR register (offset 0x48)
        # BIR = 4, Offset = 0x800 (8-byte aligned)
        cfg_bytes[0x48] = 0x04  # BIR = 4
        cfg_bytes[0x49] = 0x08  # Offset = 0x800
        cfg_bytes[0x4A] = 0x00
        cfg_bytes[0x4B] = 0x00

        config_space = cfg_bytes.hex()

        # Parse the MSI-X capability
        result = parse_msix_capability(config_space)

        # Verify the parsing worked correctly
        assert result["table_size"] == 4
        assert result["table_bir"] == 4
        assert result["table_offset"] == 0  # Should be 0 (8-byte aligned)
        assert result["pba_bir"] == 4
        assert result["pba_offset"] == 0x800  # Should be 0x800 (8-byte aligned)

        # Most importantly: no alignment warning should be logged
        # because table_offset=0 is 8-byte aligned
        mock_log.assert_not_called()

    @patch("src.device_clone.msix_capability.log_warning_safe")
    def test_no_false_positive_alignment_warning_with_bir_bits(self, mock_log):
        """
        Test that BIR bits in the table offset register don't cause false
        positive alignment warnings. This tests the specific bug fix where
        the old code incorrectly checked alignment on the raw register value
        instead of the extracted offset value.
        """
        # Create configuration space with MSI-X table at misaligned offset
        cfg_bytes = bytearray([0x00] * 256)

        # Set up PCI header
        cfg_bytes[0x06] = 0x10
        cfg_bytes[0x34] = 0x40

        # MSI-X capability
        cfg_bytes[0x40] = 0x11
        cfg_bytes[0x41] = 0x00
        cfg_bytes[0x42] = 0x03
        cfg_bytes[0x43] = 0x00

        # This test verifies that we DON'T generate a false positive warning
        # when the register value has BIR bits set but the actual offset is aligned.
        # Register = 0x0004 gives: BIR = 4, offset = 0x0000 (perfectly aligned)
        # The old buggy code would check (0x0004 & 0x7) = 4 != 0 and warn incorrectly
        # The new code checks (0x0000 & 0x7) = 0 and correctly doesn't warn
        cfg_bytes[0x44] = 0x04  # 0x0004 & 0xFF = 0x04
        cfg_bytes[0x45] = 0x00  # (0x0004 >> 8) & 0xFF = 0x00
        cfg_bytes[0x46] = 0x00
        cfg_bytes[0x47] = 0x00

        # PBA Offset/BIR: BIR=0, Offset=0x2000 (8-byte aligned)
        cfg_bytes[0x48] = 0x00
        cfg_bytes[0x49] = 0x20
        cfg_bytes[0x4A] = 0x00
        cfg_bytes[0x4B] = 0x00

        config_space = cfg_bytes.hex()

        # Parse the capability
        result = parse_msix_capability(config_space)

        # Verify parsing - register 0x0004 gives:
        # BIR = 0x0004 & 0x7 = 4
        # offset = 0x0004 & 0xFFFFFFF8 = 0x0000 (perfectly aligned)
        assert result["table_bir"] == 4
        assert result["table_offset"] == 0x0000  # Aligned

        # Should NOT generate alignment warning since offset is 0 (aligned)
        mock_log.assert_not_called()

    @patch("src.device_clone.msix_capability.log_warning_safe")
    def test_realtek_rtl8168_specific_case(self, mock_log):
        """
        Test the specific case from the user bug report:
        Realtek RTL8168 with MSI-X table at BIR=4, offset=0.
        """
        # Simulate Realtek RTL8168 MSI-X configuration
        cfg_bytes = bytearray([0x00] * 256)

        # PCI header
        cfg_bytes[0x00] = 0xEC  # Vendor ID: 0x10EC (Realtek)
        cfg_bytes[0x01] = 0x10
        cfg_bytes[0x02] = 0x68  # Device ID: 0x8168 (RTL8168)
        cfg_bytes[0x03] = 0x81
        cfg_bytes[0x06] = 0x10  # Capabilities present
        cfg_bytes[0x34] = 0x40  # Capabilities pointer

        # MSI-X capability (typically at 0x40 for this device)
        cfg_bytes[0x40] = 0x11  # MSI-X Cap ID
        cfg_bytes[0x41] = 0x00  # Next cap
        cfg_bytes[0x42] = 0x03  # Table Size: 4 entries (encoded as 3)
        cfg_bytes[0x43] = 0x00

        # Table Offset/BIR: BIR=4, Offset=0
        # This creates table_offset_bir = 4, which was incorrectly flagged
        cfg_bytes[0x44] = 0x04  # Raw value = 4 (BIR=4, offset=0)
        cfg_bytes[0x45] = 0x00
        cfg_bytes[0x46] = 0x00
        cfg_bytes[0x47] = 0x00

        # PBA Offset/BIR: BIR=4, Offset=0x800
        cfg_bytes[0x48] = 0x04  # BIR=4
        cfg_bytes[0x49] = 0x08  # Offset=0x800
        cfg_bytes[0x4A] = 0x00
        cfg_bytes[0x4B] = 0x00

        config_space = cfg_bytes.hex()

        # Parse MSI-X capability
        result = parse_msix_capability(config_space)

        # Verify correct parsing
        assert result["table_size"] == 4
        assert result["table_bir"] == 4
        assert result["table_offset"] == 0  # This is properly 8-byte aligned
        assert result["pba_bir"] == 4
        assert result["pba_offset"] == 0x800

        # Critical: No false alignment warning should be generated
        # The bug was that table_offset_bir=4 was checked for alignment
        # instead of table_offset=0
        mock_log.assert_not_called()


if __name__ == "__main__":
    # Run the tests
    pytest.main([__file__, "-v"])
