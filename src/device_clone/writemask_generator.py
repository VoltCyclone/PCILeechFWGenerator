#!/usr/bin/env python3
"""
PCILeech Writemask Generator

This module generates writemask COE files for PCILeech firmware to control
which configuration space bits are writable vs read-only. This is critical
for proper device emulation as it prevents detection through write tests.

Based on PCIe specifications and capability structures.

Thanks @Simonrak
"""

import logging
import re
from pathlib import Path
from typing import Dict, List, Optional, Tuple

from src.string_utils import (
    log_debug_safe,
    log_error_safe,
    log_info_safe,
    log_warning_safe,
    safe_format,
)

logger = logging.getLogger(__name__)

# Write-protected bits for standard PCI configuration space
WRITE_PROTECTED_BITS_PCIE = (
    "00000000",  # 0x00-0x03: Vendor ID, Device ID (read-only)
    "00000000",  # 0x04-0x07: Command, Status
    "ffff0000",  # 0x08-0x0B: Revision ID, Class Code (read-only upper)
    "00000000",  # 0x0C-0x0F: Cache Line, Latency, Header, BIST
    "ffff0000",  # 0x10-0x13: BAR0 (size bits read-only)
    "00000000",  # 0x14-0x17: BAR1
    "00000000",  # 0x18-0x1B: BAR2
    "00000000",  # 0x1C-0x1F: BAR3
    "00000000",  # 0x20-0x23: BAR4
    "00000000",  # 0x24-0x27: BAR5
    "ffff0000",  # 0x28-0x2B: Cardbus CIS (read-only upper)
    "00000000",  # 0x2C-0x2F: Subsystem ID
    "00000000",  # 0x30-0x33: Expansion ROM
)

# Write-protected bits for Power Management capability
WRITE_PROTECTED_BITS_PM = (
    "00000000",  # PM Cap ID, Next Ptr, PM Capabilities
    "031F0000",  # PMCSR, PMCSR_BSE
)

# Write-protected bits for MSI capability variations
WRITE_PROTECTED_BITS_MSI_ENABLED_0 = ("00007104",)  # MSI Control (enable bit writable)

WRITE_PROTECTED_BITS_MSI_64_BIT_1 = (
    "00007104",  # MSI Control
    "03000000",  # Message Address Low
    "00000000",  # Message Address High
    "ffff0000",  # Message Data
)

WRITE_PROTECTED_BITS_MSI_MULTIPLE_MESSAGE_ENABLED_1 = (
    "00007104",  # MSI Control
    "03000000",  # Message Address Low
    "00000000",  # Message Data
)

WRITE_PROTECTED_BITS_MSI_MULTIPLE_MESSAGE_CAPABLE_1 = (
    "00007104",  # MSI Control
    "03000000",  # Message Address Low
    "00000000",  # Message Data
    "ffff0000",  # Reserved
    "00000000",  # Reserved
    "01000000",  # Reserved
)

# Write-protected bits for MSI-X capability variations
WRITE_PROTECTED_BITS_MSIX_3 = (
    "000000c0",  # MSI-X Control
    "00000000",  # Table Offset/BIR
    "00000000",  # PBA Offset/BIR
)

WRITE_PROTECTED_BITS_MSIX_4 = (
    "000000c0",  # MSI-X Control
    "00000000",  # Table Offset/BIR
    "00000000",  # PBA Offset/BIR
    "00000000",  # Reserved
)

WRITE_PROTECTED_BITS_MSIX_5 = (
    "000000c0",  # MSI-X Control
    "00000000",  # Table Offset/BIR
    "00000000",  # PBA Offset/BIR
    "00000000",  # Reserved
    "00000000",  # Reserved
)

WRITE_PROTECTED_BITS_MSIX_6 = (
    "000000c0",  # MSI-X Control
    "00000000",  # Table Offset/BIR
    "00000000",  # PBA Offset/BIR
    "00000000",  # Reserved
    "00000000",  # Reserved
    "00000000",  # Reserved
)

WRITE_PROTECTED_BITS_MSIX_7 = (
    "000000c0",  # MSI-X Control
    "00000000",  # Table Offset/BIR
    "00000000",  # PBA Offset/BIR
    "00000000",  # Reserved
    "00000000",  # Reserved
    "00000000",  # Reserved
    "00000000",  # Reserved
)

WRITE_PROTECTED_BITS_MSIX_8 = (
    "000000c0",  # MSI-X Control
    "00000000",  # Table Offset/BIR
    "00000000",  # PBA Offset/BIR
    "00000000",  # Reserved
    "00000000",  # Reserved
    "00000000",  # Reserved
    "00000000",  # Reserved
    "00000000",  # Reserved
)

# Write-protected bits for other capabilities
WRITE_PROTECTED_BITS_VPD = (
    "0000ffff",  # VPD Address
    "ffffffff",  # VPD Data
)

WRITE_PROTECTED_BITS_VSC = (
    "000000ff",  # Vendor Specific Cap ID
    "ffffffff",  # Vendor Specific Data
)

WRITE_PROTECTED_BITS_TPH = (
    "00000000",  # TPH Requester Cap
    "00000000",  # TPH Requester Control
    "070c0000",  # ST Table
)

WRITE_PROTECTED_BITS_VSEC = (
    "00000000",  # VSEC Cap
    "00000000",  # VSEC Header
    "ffffffff",  # Vendor Specific
    "ffffffff",  # Vendor Specific
)

WRITE_PROTECTED_BITS_AER = (
    "00000000",  # AER Cap
    "00000000",  # Uncorrectable Error Status
    "30F0FF07",  # Uncorrectable Error Mask
    "30F0FF07",  # Uncorrectable Error Severity
    "00000000",  # Correctable Error Status
    "C1F10000",  # Correctable Error Mask
    "40050000",  # AER Capabilities and Control
    "00000000",  # Header Log 1
    "00000000",  # Header Log 2
    "00000000",  # Header Log 3
    "00000000",  # Header Log 4
)

WRITE_PROTECTED_BITS_DSN = (
    "00000000",  # DSN Cap
    "00000000",  # Serial Number Low
    "00000000",  # Serial Number High
)

WRITE_PROTECTED_BITS_LTR = (
    "00000000",  # LTR Cap
    "00000000",  # Max Snoop/No-Snoop Latency
)

WRITE_PROTECTED_BITS_L1PM = (
    "00000000",  # L1 PM Substates Cap
    "00000000",  # L1 PM Substates Control 1
    "3f00ffe3",  # L1 PM Substates Control 2
    "fb000000",  # Reserved
)

WRITE_PROTECTED_BITS_PTM = (
    "00000000",  # PTM Cap
    "00000000",  # PTM Control
    "00000000",  # PTM Effective Granularity
    "03ff0000",  # Reserved
)

WRITE_PROTECTED_BITS_VC = (
    "00000000",  # VC Cap
    "00000000",  # Port VC Cap 1
    "00000000",  # Port VC Cap 2
    "0F000000",  # Port VC Control
    "00000000",  # Port VC Status
    "FF000F87",  # VC Resource Cap
    "00000000",  # VC Resource Control
)

# Capability ID mappings
CAPABILITY_NAMES = {
    0x01: "power management",
    0x02: "AGP",
    0x03: "VPD",
    0x04: "slot identification",
    0x05: "MSI",
    0x06: "compact PCI hot swap",
    0x07: "PCI-X",
    0x08: "hyper transport",
    0x09: "vendor specific",
    0x0A: "debug port",
    0x0B: "compact PCI central resource control",
    0x0C: "PCI hot plug",
    0x0D: "PCI bridge subsystem vendor ID",
    0x0E: "AGP 8x",
    0x0F: "secure device",
    0x10: "PCI express",
    0x11: "MSI-X",
    0x12: "SATA data/index configuration",
    0x13: "advanced features",
    0x14: "enhanced allocation",
    0x15: "flattening portal bridge",
}

EXTENDED_CAPABILITY_NAMES = {
    0x0001: "advanced error reporting",
    0x0002: "virtual channel",
    0x0003: "device serial number",
    0x0004: "power budgeting",
    0x0005: "root complex link declaration",
    0x0006: "root complex internal link control",
    0x0007: "root complex event collector endpoint association",
    0x0008: "multi-function virtual channel",
    0x0009: "virtual channel",
    0x000A: "root complex register block",
    0x000B: "vendor specific",
    0x000C: "configuration access correlation",
    0x000D: "access control services",
    0x000E: "alternative routing-ID interpretation",
    0x000F: "address translation services",
    0x0010: "single root IO virtualization",
    0x0011: "multi-root IO virtualization",
    0x0012: "multicast",
    0x0013: "page request interface",
    0x0014: "AMD reserved",
    0x0015: "resizable BAR",
    0x0016: "dynamic power allocation",
    0x0017: "TPH requester",
    0x0018: "latency tolerance reporting",
    0x0019: "secondary PCI express",
    0x001A: "protocol multiplexing",
    0x001B: "process address space ID",
    0x001C: "LN requester",
    0x001D: "downstream port containment",
    0x001E: "L1 PM substates",
    0x001F: "precision time measurement",
    0x0020: "M-PCIe",
    0x0021: "FRS queueing",
    0x0022: "Readyness time reporting",
    0x0023: "designated vendor specific",
    0x0024: "VF resizable BAR",
    0x0025: "data link feature",
    0x0026: "physical layer 16.0 GT/s",
    0x0027: "receiver lane margining",
    0x0028: "hierarchy ID",
    0x0029: "native PCIe enclosure management",
    0x002A: "physical layer 32.0 GT/s",
    0x002B: "alternate protocol",
    0x002C: "system firmware intermediary",
}

FIXED_SECTION = (
    "00000000",  # 0x00: Vendor/Device ID (read-only)
    "470500f9",  # 0x04: Command/Status (partially writable)
    "00000000",  # 0x08: Rev/Class (read-only)
    "ffff0040",  # 0x0C: Cache/Latency/Header/BIST
    "f0ffffff",  # 0x10: BAR0 (size bits protected)
    "ffffffff",  # 0x14: BAR1
    "f0ffffff",  # 0x18: BAR2
    "ffffffff",  # 0x1C: BAR3
    "f0ffffff",  # 0x20: BAR4
    "f0ffffff",  # 0x24: BAR5
    "00000000",  # 0x28: Cardbus CIS
    "00000000",  # 0x2C: Subsystem ID
    "01f8ffff",  # 0x30: Expansion ROM
    "00000000",  # 0x34: Cap Pointer
    "00000000",  # 0x38: Reserved
    "ff000000",  # 0x3C: Int Line/Pin/Min/Max
)

# Writemask dictionary mapping capability IDs to their write-protected bits
WRITEMASK_DICT = {
    "0x10": WRITE_PROTECTED_BITS_PCIE,
    "0x03": WRITE_PROTECTED_BITS_VPD,
    "0x01": WRITE_PROTECTED_BITS_PM,
    "0x09": WRITE_PROTECTED_BITS_VSC,
    "0x000A": WRITE_PROTECTED_BITS_VSEC,
    "0x0001": WRITE_PROTECTED_BITS_AER,
    "0x0002": WRITE_PROTECTED_BITS_VC,
    "0x0003": WRITE_PROTECTED_BITS_DSN,
    "0x0018": WRITE_PROTECTED_BITS_LTR,
    "0x001E": WRITE_PROTECTED_BITS_L1PM,
    "0x001F": WRITE_PROTECTED_BITS_PTM,
    "0x0017": WRITE_PROTECTED_BITS_TPH,
}


class WritemaskGenerator:
    """Generator for PCILeech configuration space writemask."""

    def __init__(self):
        """Initialize the writemask generator."""
        self.logger = logging.getLogger(__name__)

    def get_msi_writemask(self, msi_config: Dict) -> Optional[Tuple[str, ...]]:
        """
        Get appropriate MSI writemask based on configuration.

        Args:
            msi_config: MSI configuration dictionary

        Returns:
            Tuple of writemask strings or None
        """
        if not msi_config.get("enabled", False):
            return WRITE_PROTECTED_BITS_MSI_ENABLED_0

        if msi_config.get("64bit_capable", False):
            return WRITE_PROTECTED_BITS_MSI_64_BIT_1

        if msi_config.get("multiple_message_capable", False):
            return WRITE_PROTECTED_BITS_MSI_MULTIPLE_MESSAGE_CAPABLE_1

        if msi_config.get("multiple_message_enabled", False):
            return WRITE_PROTECTED_BITS_MSI_MULTIPLE_MESSAGE_ENABLED_1

        return WRITE_PROTECTED_BITS_MSI_ENABLED_0

    def get_msix_writemask(self, msix_config: Dict) -> Optional[Tuple[str, ...]]:
        """
        Get appropriate MSI-X writemask based on configuration.

        Args:
            msix_config: MSI-X configuration dictionary

        Returns:
            Tuple of writemask strings or None
        """
        table_size = msix_config.get("table_size", 0)

        # Map table size to capability length
        if table_size <= 8:
            return WRITE_PROTECTED_BITS_MSIX_3
        elif table_size <= 16:
            return WRITE_PROTECTED_BITS_MSIX_4
        elif table_size <= 32:
            return WRITE_PROTECTED_BITS_MSIX_5
        elif table_size <= 64:
            return WRITE_PROTECTED_BITS_MSIX_6
        elif table_size <= 128:
            return WRITE_PROTECTED_BITS_MSIX_7
        else:
            return WRITE_PROTECTED_BITS_MSIX_8

    def read_cfg_space(self, file_path: Path) -> Dict[int, int]:
        """
        Read configuration space from COE file.

        Args:
            file_path: Path to COE file

        Returns:
            Dictionary mapping dword index to value
        """
        dword_map = {}
        index = 0

        try:
            with open(file_path, "r") as file:
                in_data_section = False
                for line in file:
                    line = line.strip()

                    # Skip comments and empty lines
                    if not line or line.startswith(";"):
                        continue

                    # Check for data section start
                    if "memory_initialization_vector=" in line:
                        in_data_section = True
                        continue

                    if in_data_section:
                        # Extract hex values from line
                        dwords = re.findall(r"[0-9a-fA-F]{8}", line)
                        for dword in dwords:
                            if dword and index < 1024:
                                dword_map[index] = int(dword, 16)
                                index += 1

        except Exception as e:
            log_error_safe(
                self.logger,
                safe_format("Failed to read configuration space: {error}", error=e),
                prefix="WRITEMASK",
            )
            raise

        return dword_map

    def locate_capabilities(self, dword_map: Dict[int, int]) -> Dict[str, int]:
        """
        Locate PCI capabilities in configuration space.

        Args:
            dword_map: Configuration space dword map

        Returns:
            Dictionary mapping capability ID to offset
        """
        capabilities = {}

        # Standard capabilities
        cap_ptr = (dword_map.get(0x34 // 4, 0) >> 0) & 0xFF

        while cap_ptr != 0 and cap_ptr < 0x100:
            cap_dword_idx = cap_ptr // 4
            cap_dword = dword_map.get(cap_dword_idx, 0)

            # Extract capability ID and next pointer
            cap_id = (cap_dword >> ((cap_ptr % 4) * 8)) & 0xFF
            next_cap = (cap_dword >> ((cap_ptr % 4) * 8 + 8)) & 0xFF

            cap_name = CAPABILITY_NAMES.get(cap_id, f"Unknown (0x{cap_id:02X})")
            log_debug_safe(
                self.logger,
                safe_format(
                    "Found capability at 0x{ptr:02X}: {name}",
                    ptr=cap_ptr,
                    name=cap_name,
                ),
                prefix="WRITEMASK",
            )

            capabilities[f"0x{cap_id:02X}"] = cap_ptr
            cap_ptr = next_cap

        # Extended capabilities
        ext_cap_offset = 0x100
        while ext_cap_offset != 0 and ext_cap_offset < 0x1000:
            ext_cap_dword = dword_map.get(ext_cap_offset // 4, 0)

            # Extended capability header format
            ext_cap_id = ext_cap_dword & 0xFFFF
            ext_cap_ver = (ext_cap_dword >> 16) & 0xF
            next_offset = (ext_cap_dword >> 20) & 0xFFF

            if ext_cap_id != 0 and ext_cap_id != 0xFFFF:
                cap_name = EXTENDED_CAPABILITY_NAMES.get(
                    ext_cap_id, f"Unknown (0x{ext_cap_id:04X})"
                )
                log_debug_safe(
                    self.logger,
                    safe_format(
                        "Found extended capability at 0x{offset:03X}: {name}",
                        offset=ext_cap_offset,
                        name=cap_name,
                    ),
                    prefix="WRITEMASK",
                )

                capabilities[f"0x{ext_cap_id:04X}"] = ext_cap_offset

            ext_cap_offset = next_offset

        return capabilities

    def create_writemask(self, dwords: Dict[int, int]) -> List[str]:
        """
        Create initial writemask (all bits writable).

        Args:
            dwords: Configuration space dword map

        Returns:
            List of writemask strings
        """
        # Default to all bits writable (0xFFFFFFFF)
        return ["ffffffff" for _ in range(len(dwords))]

    def update_writemask(
        self, wr_mask: List[str], protected_bits: Tuple[str, ...], start_index: int
    ) -> List[str]:
        """
        Update writemask with protected bits.

        Args:
            wr_mask: Current writemask
            protected_bits: Tuple of protected bit masks
            start_index: Starting dword index

        Returns:
            Updated writemask
        """
        end_index = min(start_index + len(protected_bits), len(wr_mask))

        for i, mask in enumerate(protected_bits):
            if start_index + i < len(wr_mask):
                # Convert to integers for bitwise operations
                current = int(wr_mask[start_index + i], 16)
                protected = int(mask, 16)

                # Clear protected bits (0 = read-only, 1 = writable)
                new_mask = current & ~protected

                wr_mask[start_index + i] = f"{new_mask:08x}"

        return wr_mask

    def generate_writemask(
        self,
        cfg_space_path: Path,
        output_path: Path,
        device_config: Optional[Dict] = None,
    ) -> None:
        """
        Generate writemask COE file from configuration space.

        Args:
            cfg_space_path: Path to configuration space COE file
            output_path: Path for output writemask COE file
            device_config: Optional device configuration for MSI/MSI-X
        """
        log_info_safe(
            self.logger,
            safe_format("Generating writemask from {path}", path=cfg_space_path),
            prefix="WRITEMASK",
        )

        # Read configuration space
        cfg_space = self.read_cfg_space(cfg_space_path)

        # Locate capabilities
        capabilities = self.locate_capabilities(cfg_space)

        # Create initial writemask (all writable)
        wr_mask = self.create_writemask(cfg_space)

        # Apply fixed section protection
        wr_mask = self.update_writemask(wr_mask, FIXED_SECTION, 0)

        # Apply capability-specific protections
        for cap_id, cap_offset in capabilities.items():
            cap_start_index = cap_offset // 4

            # Handle MSI capability
            if cap_id == "0x05":
                msi_config = (
                    device_config.get("msi_config", {}) if device_config else {}
                )
                protected_bits = self.get_msi_writemask(msi_config)
                if protected_bits:
                    wr_mask = self.update_writemask(
                        wr_mask, protected_bits, cap_start_index
                    )

            # Handle MSI-X capability
            elif cap_id == "0x11":
                msix_config = (
                    device_config.get("msix_config", {}) if device_config else {}
                )
                protected_bits = self.get_msix_writemask(msix_config)
                if protected_bits:
                    wr_mask = self.update_writemask(
                        wr_mask, protected_bits, cap_start_index
                    )

            # Handle other capabilities
            else:
                protected_bits = WRITEMASK_DICT.get(cap_id)
                if protected_bits:
                    wr_mask = self.update_writemask(
                        wr_mask, protected_bits, cap_start_index
                    )

        # Write output COE file
        self._write_writemask_coe(wr_mask, output_path)

        log_info_safe(
            self.logger,
            safe_format("Writemask generated successfully: {path}", path=output_path),
            prefix="WRITEMASK",
        )

    def _write_writemask_coe(self, wr_mask: List[str], output_path: Path) -> None:
        """
        Write writemask to COE file.

        Args:
            wr_mask: Writemask data
            output_path: Output file path
        """
        with open(output_path, "w") as f:
            # Write header
            f.write("; PCILeech Configuration Space Writemask\n")
            f.write("; Generated by PCILeech Firmware Generator\n")
            f.write(";\n")
            f.write(
                "; This file controls which configuration space bits are writable.\n"
            )
            f.write("; 0 = read-only, 1 = writable\n")
            f.write(";\n")
            f.write("memory_initialization_radix=16;\n")
            f.write("memory_initialization_vector=\n")

            # Write data in groups of 4 dwords per line
            for i in range(0, len(wr_mask), 4):
                line_data = wr_mask[i : i + 4]
                f.write(",".join(line_data))

                # Add comma except for last line
                if i + 4 < len(wr_mask):
                    f.write(",\n")
                else:
                    f.write(";\n")
