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

from src.exceptions import FileOperationError

from src.string_utils import (
    log_debug_safe,
    log_error_safe,
    log_info_safe,
    log_warning_safe,
    safe_format,
)
from src.pci_capability.constants import (
    STANDARD_CAPABILITY_NAMES as CAPABILITY_NAMES,
    EXTENDED_CAPABILITY_NAMES,
)
from src.device_clone.constants import (
    FIXED_SECTION,
    WRITEMASK_DICT,
    WRITE_PROTECTED_BITS_MSI_64_BIT_1,
    WRITE_PROTECTED_BITS_MSI_ENABLED_0,
    WRITE_PROTECTED_BITS_MSI_MULTIPLE_MESSAGE_CAPABLE_1,
    WRITE_PROTECTED_BITS_MSI_MULTIPLE_MESSAGE_ENABLED_1,
    WRITE_PROTECTED_BITS_MSIX_3,
    WRITE_PROTECTED_BITS_MSIX_4,
    WRITE_PROTECTED_BITS_MSIX_5,
    WRITE_PROTECTED_BITS_MSIX_6,
    WRITE_PROTECTED_BITS_MSIX_7,
    WRITE_PROTECTED_BITS_MSIX_8,
)

logger = logging.getLogger(__name__)


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
            raise FileOperationError(
                f"Failed to read configuration space from {file_path}: {str(e)}"
            ) from e

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
