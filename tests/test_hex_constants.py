"""
Unit tests for hex constants to ensure they have correct values and types.
"""

import pytest
from src.device_clone.hex_constants import (
    HEX_ZERO_BYTE,
    HEX_ZERO_WORD,
    HEX_ZERO_DWORD,
    HEX_FULL_BYTE,
    HEX_FULL_WORD,
    HEX_FULL_DWORD,
    CAP_ID_POWER_MGMT,
    CAP_ID_MSI,
    CAP_ID_MSIX,
    CAP_ID_PCIE,
    EXT_CAP_ID_AER,
    EXT_CAP_ID_DSN,
    EXT_CAP_ID_LTR,
    EXT_CAP_ID_L1PM,
    CLASS_CODE_NETWORK,
    CLASS_CODE_ETHERNET,
    CLASS_CODE_UNKNOWN,
    DEFAULT_VENDOR_ID,
    DEFAULT_DEVICE_ID,
    DEFAULT_CLASS_CODE,
    PCI_VENDOR_ID_OFFSET,
    PCI_BAR0_OFFSET,
    PCI_CAP_PTR_OFFSET,
    CAP_OFFSET_PM,
    CAP_OFFSET_MSI,
    CAP_OFFSET_MSIX,
    CAP_OFFSET_PCIE,
    COMMAND_MEMORY_ENABLE,
    COMMAND_MASTER_ENABLE,
    STATUS_CAPABILITIES_LIST,
    BAR_TYPE_IO,
    BAR_TYPE_64BIT,
    BAR_TYPE_PREFETCHABLE,
    SIZE_4KB,
    SIZE_1MB,
    SIZE_256MB,
)


class TestHexStringConstants:
    """Test hex string constants."""

    def test_zero_constants(self):
        """Test zero hex string constants."""
        assert HEX_ZERO_BYTE == "00"
        assert HEX_ZERO_WORD == "0000"
        assert HEX_ZERO_DWORD == "00000000"

    def test_full_constants(self):
        """Test full hex string constants."""
        assert HEX_FULL_BYTE == "ff"
        assert HEX_FULL_WORD == "ffff"
        assert HEX_FULL_DWORD == "ffffffff"


class TestCapabilityIDs:
    """Test PCI capability ID constants."""

    def test_standard_cap_ids(self):
        """Test standard capability IDs."""
        assert CAP_ID_POWER_MGMT == 0x01
        assert CAP_ID_MSI == 0x05
        assert CAP_ID_MSIX == 0x11
        assert CAP_ID_PCIE == 0x10

    def test_extended_cap_ids(self):
        """Test extended capability IDs."""
        assert EXT_CAP_ID_AER == 0x0001
        assert EXT_CAP_ID_DSN == 0x0003
        assert EXT_CAP_ID_LTR == 0x0018
        assert EXT_CAP_ID_L1PM == 0x001E

    def test_cap_id_types(self):
        """Test that capability IDs are integers."""
        assert isinstance(CAP_ID_POWER_MGMT, int)
        assert isinstance(EXT_CAP_ID_AER, int)


class TestClassCodes:
    """Test PCI class code constants."""

    def test_major_class_codes(self):
        """Test major class code values."""
        assert CLASS_CODE_NETWORK == "02"
        assert len(CLASS_CODE_NETWORK) == 2

    def test_full_class_codes(self):
        """Test full 6-char class codes."""
        assert CLASS_CODE_UNKNOWN == "000000"
        assert CLASS_CODE_ETHERNET == "020000"
        assert len(CLASS_CODE_ETHERNET) == 6


class TestDefaultValues:
    """Test default value constants."""

    def test_default_ids(self):
        """Test default ID values."""
        assert DEFAULT_VENDOR_ID == "0000"
        assert DEFAULT_DEVICE_ID == "0000"
        assert DEFAULT_CLASS_CODE == CLASS_CODE_UNKNOWN


class TestPCIOffsets:
    """Test PCI configuration space offset constants."""

    def test_basic_offsets(self):
        """Test basic PCI config space offsets."""
        assert PCI_VENDOR_ID_OFFSET == 0x00
        assert PCI_BAR0_OFFSET == 0x10
        assert PCI_CAP_PTR_OFFSET == 0x34

    def test_capability_offsets(self):
        """Test capability offset constants."""
        assert CAP_OFFSET_PM == 0x40
        assert CAP_OFFSET_MSI == 0x48
        assert CAP_OFFSET_MSIX == 0x50
        assert CAP_OFFSET_PCIE == 0x60

    def test_offset_ordering(self):
        """Test that offsets are in expected order."""
        assert CAP_OFFSET_PM < CAP_OFFSET_MSI < CAP_OFFSET_MSIX < CAP_OFFSET_PCIE


class TestCommandStatusBits:
    """Test command and status register bit constants."""

    def test_command_bits(self):
        """Test command register bit values."""
        assert COMMAND_MEMORY_ENABLE == 0x0002
        assert COMMAND_MASTER_ENABLE == 0x0004
        
    def test_status_bits(self):
        """Test status register bit values."""
        assert STATUS_CAPABILITIES_LIST == 0x0010


class TestBARTypes:
    """Test BAR type bit constants."""

    def test_bar_type_bits(self):
        """Test BAR type bit values."""
        assert BAR_TYPE_IO == 0x01
        assert BAR_TYPE_64BIT == 0x04
        assert BAR_TYPE_PREFETCHABLE == 0x08

    def test_bar_type_combinations(self):
        """Test that BAR type bits can be combined."""
        # 64-bit prefetchable memory BAR
        combined = BAR_TYPE_64BIT | BAR_TYPE_PREFETCHABLE
        assert combined == 0x0C


class TestSizeConstants:
    """Test size constants."""

    def test_common_sizes(self):
        """Test common size values."""
        assert SIZE_4KB == 0x1000
        assert SIZE_1MB == 0x100000
        assert SIZE_256MB == 0x10000000

    def test_size_relationships(self):
        """Test relationships between sizes."""
        assert SIZE_1MB == 256 * SIZE_4KB
        assert SIZE_256MB == 256 * SIZE_1MB


class TestConstantConsistency:
    """Test consistency between related constants."""

    def test_hex_string_consistency(self):
        """Test that hex string constants have expected lengths."""
        assert len(HEX_ZERO_BYTE) == 2
        assert len(HEX_ZERO_WORD) == 4
        assert len(HEX_ZERO_DWORD) == 8

    def test_default_consistency(self):
        """Test that defaults are consistent."""
        assert len(DEFAULT_VENDOR_ID) == 4
        assert len(DEFAULT_DEVICE_ID) == 4
        assert DEFAULT_CLASS_CODE == CLASS_CODE_UNKNOWN