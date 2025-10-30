"""
Shared constants for PCILeech firmware generation.

This module consolidates board mappings and other constants that were
previously duplicated across the build system.
"""

import random
from typing import Dict, Final, List, Optional, Union

# Import the VendorID enum for use in fallback functions
try:
    from src.pci_capability.dynamic_functions import VendorID as ExternalVendorID

    # Use the external VendorID enum values
    VENDOR_ID_INTEL = ExternalVendorID.INTEL
    VENDOR_ID_REALTEK = ExternalVendorID.REALTEK
    VENDOR_ID_NVIDIA = ExternalVendorID.NVIDIA
    VENDOR_ID_AMD = ExternalVendorID.AMD_ATI
except ImportError:
    # Define fallback values if the import fails
    VENDOR_ID_INTEL = 0x8086
    VENDOR_ID_REALTEK = 0x10EC
    VENDOR_ID_NVIDIA = 0x10DE
    VENDOR_ID_AMD = 0x1002

# Common device IDs
DEVICE_ID_GENERIC = 0x1234  # Generic device ID for fallback
DEVICE_ID_INTEL_ETH = 0x1533  # Intel I210 Gigabit Network Connection
DEVICE_ID_INTEL_NVME = 0x2522  # Intel NVMe SSD Controller
DEVICE_ID_NVIDIA_GPU = 0x2204  # NVIDIA RTX GPU
DEVICE_ID_FALLBACK = 0x0000  # Fallback when device ID is missing

# PCIe BAR type constants
BAR_TYPE_MEMORY_32BIT = 0
BAR_TYPE_MEMORY_64BIT = 1

# PCI configuration space constants
MAX_32BIT_VALUE = 0xFFFFFFFF  # Maximum 32-bit unsigned value
DEFAULT_EXT_CFG_CAP_PTR = 0x100  # Default extended capability pointer
DEFAULT_CLASS_CODE = "000000"  # Default class code for unknown devices
DEFAULT_REVISION_ID = "00"  # Default revision ID

# PCI device class codes
PCI_CLASS_NETWORK = "02"
PCI_CLASS_STORAGE = "01"
PCI_CLASS_DISPLAY = "03"
PCI_CLASS_AUDIO = "040"

# Power management states
POWER_STATE_D0 = "D0"  # Fully operational power state
POWER_STATE_D3 = "D3"  # Lowest power state

# Fallback vendor ID list for generating random vendor IDs when needed
FALLBACK_VENDOR_IDS: Final[List[int]] = [
    VENDOR_ID_INTEL,  # Intel
    VENDOR_ID_REALTEK,  # Realtek
    VENDOR_ID_NVIDIA,  # NVIDIA
    VENDOR_ID_AMD,  # AMD/ATI
]


def get_fallback_vendor_id(prefer_random: bool = False) -> int:
    """Get a fallback vendor ID, randomly selecting from list if requested.

    Args:
        prefer_random: If True, returns a random vendor ID from the fallback list.
                      If False, returns Intel's vendor ID (0x8086).

    Returns:
        A vendor ID integer to use as fallback.
    """
    if prefer_random:
        return random.choice(FALLBACK_VENDOR_IDS)
    return VENDOR_ID_INTEL  # Default to Intel


def get_fallback_device_id(vendor_id: Optional[int] = None) -> int:
    """Get a fallback device ID appropriate for the given vendor ID.

    Args:
        vendor_id: The vendor ID to get a matching device ID for.
                  If None, returns the generic device ID (0x1234).

    Returns:
        A device ID integer to use as fallback.
    """
    if vendor_id == VENDOR_ID_INTEL:
        return DEVICE_ID_INTEL_ETH
    elif vendor_id == VENDOR_ID_NVIDIA:
        return DEVICE_ID_NVIDIA_GPU
    return DEVICE_ID_GENERIC  # Default to generic device ID


# Board to FPGA part mapping
BOARD_PARTS = {
    # Original boards
    "35t": "xc7a35tcsg324-2",
    "75t": "xc7a75tfgg484-2",
    # 100T CaptainDMA is an Artix-7 100T (FGG484-1). Use the 7-series part.
    "100t": "xc7a100tfgg484-1",
    # CaptainDMA boards
    "pcileech_75t484_x1": "xc7a75tfgg484-2",
    "pcileech_35t484_x1": "xc7a35tfgg484-2",
    "pcileech_35t325_x4": "xc7a35tcsg324-2",
    "pcileech_35t325_x1": "xc7a35tcsg324-2",
    # CaptainDMA 100T x1 board: Artix-7 100T, FFG484 package, -1 speed grade.
    "pcileech_100t484_x1": "xc7a100tfgg484-1",
    # Other boards
    "pcileech_enigma_x1": "xc7a75tfgg484-2",
    "pcileech_squirrel": "xc7a35tfgg484-2",
    "pcileech_pciescreamer_xc7a35": "xc7a35tcsg324-2",
}

# Canonical fallback board list (used when dynamic discovery fails)
BOARD_FALLBACKS: Final[List[str]] = [
    "pcileech_35t325_x4",
    "pcileech_35t325_x1",
    "pcileech_35t484_x1",
    "pcileech_75t484_x1",
    "pcileech_100t484_x1",
    "pcileech_enigma_x1",
    "pcileech_squirrel",
    "pcileech_pciescreamer_xc7a35",
]

# Default FPGA part for unknown boards
DEFAULT_FPGA_PART = "xc7a35tcsg324-2"

# Vivado project configuration constants
VIVADO_PROJECT_NAME = "pcileech_firmware"
VIVADO_PROJECT_DIR = "./vivado_project"
VIVADO_OUTPUT_DIR = "."

# PCILeech TCL script file names (2-script approach - current)
PCILEECH_TCL_SCRIPT_FILES = [
    "vivado_generate_project.tcl",
    "vivado_build.tcl",
]

# PCILeech build scripts
PCILEECH_PROJECT_SCRIPT = "vivado_generate_project.tcl"
PCILEECH_BUILD_SCRIPT = "vivado_build.tcl"

# Synthesis and implementation strategies
SYNTHESIS_STRATEGY = "Vivado Synthesis Defaults"
IMPLEMENTATION_STRATEGY = "Performance_Explore"

# FPGA family detection patterns
FPGA_FAMILIES = {
    "ZYNQ_ULTRASCALE": "xczu",
    "ARTIX7_35T": "xc7a35t",
    "ARTIX7_75T": "xc7a75t",
    "KINTEX7": "xc7k",
}

# Legacy TCL files to clean up
LEGACY_TCL_FILES = [
    "build_unified.tcl",
    "unified_build.tcl",
    "build_firmware.tcl",
]

# Production mode defaults - enable all advanced features by default
PRODUCTION_DEFAULTS = {
    "ADVANCED_SV": True,
    "MANUFACTURING_VARIANCE": True,
    "BEHAVIOR_PROFILING": True,
    "POWER_MANAGEMENT": True,
    "ERROR_HANDLING": True,
    "PERFORMANCE_COUNTERS": True,
    "CONFIG_SPACE_SHADOW": True,
    "MSIX_CAPABILITY": True,
    "OPTION_ROM_SUPPORT": True,
    "DEFAULT_DEVICE_TYPE": "network",
}

# PCILeech Control Bit Definitions
PCILEECH_CONTROL_BITS = {
    # Core functionality
    "ENABLE": 0,  # rw[0] - Enable PCILeech functionality
    "DMA_ENABLE": 1,  # rw[1] - Enable DMA operations
    "SCATTER_GATHER": 2,  # rw[2] - Scatter-gather support
    "INTERRUPT": 3,  # rw[3] - Interrupt support
    # CFGTLP control bits
    "CFG_A7_BIT0": 20,  # rw[20] - Configuration address bit 7, bit 0
    "CFG_A7_BIT1": 21,  # rw[21] - Configuration address bit 7, bit 1
    "CFGTLP_WREN": 192,  # rw[192] - CFGTLP write enable
    "CFGTLP_ZERO_DATA": 203,  # rw[203] - CFGTLP zero data (0 = custom config space)
    "CFGTLP_EN": 204,  # rw[204] - CFGTLP enable
    "CFGTLP_FILTER": 205,  # rw[205] - CFGTLP filter
    "CFGTLP_PCIE_WRITE_EN": 206,  # rw[206] - CFGTLP PCIe write enable
    "ALLTLP_FILTER": 207,  # rw[207] - All TLP filter (moved from 206)
    "BAR_EN_START": 208,  # rw[223:208] - BAR enable bits
    "BAR_EN_END": 223,
}

# CFGTLP Configuration
CFGTLP_CONFIG = {
    "ZERO_DATA_ENABLED": 0,  # Value for rw[203] when custom config space enabled
    "ZERO_DATA_DISABLED": 1,  # Value for rw[203] when standard config space
    "PCIE_WRITE_ENABLED": 1,  # Value for rw[206] to enable PCIe writes
    "PCIE_WRITE_DISABLED": 0,  # Value for rw[206] to disable PCIe writes
}

# BAR Size Constants according to PCIe specification
BAR_SIZE_CONSTANTS = {
    # Address masks
    "MEMORY_ADDRESS_MASK": 0xFFFFFFF0,  # Memory BAR address mask (bits [31:4])
    "IO_ADDRESS_MASK": 0xFFFFFFFC,  # I/O BAR address mask (bits [31:2])
    # Type bits
    "TYPE_IO": 0x1,  # Bit 0: 1 = I/O, 0 = Memory
    "TYPE_64BIT": 0x4,  # Bits [2:1]: 10 = 64-bit memory BAR
    "TYPE_PREFETCHABLE": 0x8,  # Bit 3: 1 = Prefetchable memory
    # Minimum sizes per PCIe spec
    "MIN_MEMORY_SIZE": 128,  # Minimum memory BAR size (128 bytes)
    "MIN_IO_SIZE": 16,  # Minimum I/O BAR size (16 bytes)
    "MAX_IO_SIZE": 256,  # Maximum I/O BAR size (256 bytes)
    # Common BAR sizes
    "SIZE_4KB": 4 * 1024,
    "SIZE_8KB": 8 * 1024,
    "SIZE_16KB": 16 * 1024,
    "SIZE_32KB": 32 * 1024,
    "SIZE_64KB": 64 * 1024,
    "SIZE_128KB": 128 * 1024,
    "SIZE_256KB": 256 * 1024,
    "SIZE_512KB": 512 * 1024,
    "SIZE_1MB": 1024 * 1024,
    "SIZE_2MB": 2 * 1024 * 1024,
    "SIZE_4MB": 4 * 1024 * 1024,
    "SIZE_8MB": 8 * 1024 * 1024,
    "SIZE_16MB": 16 * 1024 * 1024,
    "SIZE_32MB": 32 * 1024 * 1024,
    "SIZE_64MB": 64 * 1024 * 1024,
    "SIZE_128MB": 128 * 1024 * 1024,
    "SIZE_256MB": 256 * 1024 * 1024,
}

# PCIe Maximum Payload Size (MPS) Constants
PCIE_MPS_CONSTANTS = {
    # Valid MPS values in bytes
    "MPS_128": 128,
    "MPS_256": 256,
    "MPS_512": 512,
    "MPS_1024": 1024,
    "MPS_2048": 2048,
    "MPS_4096": 4096,
    # MPS encoding values (for PCIe Device Control Register bits 7:5)
    "MPS_128_ENCODING": 0b000,  # 128 bytes
    "MPS_256_ENCODING": 0b001,  # 256 bytes
    "MPS_512_ENCODING": 0b010,  # 512 bytes
    "MPS_1024_ENCODING": 0b011,  # 1024 bytes
    "MPS_2048_ENCODING": 0b100,  # 2048 bytes
    "MPS_4096_ENCODING": 0b101,  # 4096 bytes
    # Tiny PCIe algorithm threshold
    # Payloads smaller than this may cause performance issues
    "TINY_PCIE_THRESHOLD": 256,
    # Default MPS if not specified
    "DEFAULT_MPS": 256,
}

# Map from MPS value to encoding
MPS_VALUE_TO_ENCODING = {
    128: 0b000,
    256: 0b001,
    512: 0b010,
    1024: 0b011,
    2048: 0b100,
    4096: 0b101,
}

# Map from encoding to MPS value
MPS_ENCODING_TO_VALUE = {v: k for k, v in MPS_VALUE_TO_ENCODING.items()}

# Valid MPS values
VALID_MPS_VALUES = list(MPS_VALUE_TO_ENCODING.keys())

# Import writemask constants to avoid duplication
from .writemask_constants import (
    FIXED_SECTION,
    WRITE_PROTECTED_BITS_AER,
    WRITE_PROTECTED_BITS_DSN,
    WRITE_PROTECTED_BITS_L1PM,
    WRITE_PROTECTED_BITS_LTR,
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
    WRITE_PROTECTED_BITS_PCIE,
    WRITE_PROTECTED_BITS_PM,
    WRITE_PROTECTED_BITS_PTM,
    WRITE_PROTECTED_BITS_TPH,
    WRITE_PROTECTED_BITS_VC,
    WRITE_PROTECTED_BITS_VPD,
    WRITE_PROTECTED_BITS_VSC,
    WRITE_PROTECTED_BITS_VSEC,
    WRITEMASK_DICT,
)

# TCL Script Files
TCL_SCRIPT_FILES = {
    "BUILD_TCL": "vivado_generate_project.tcl",
    "PCIe_BASE_TCL": "pcie_7x_0.tcl",
    "PCIe_GEN3_TCL": "pcie3_7x_0.tcl",
    "BOARD_CONSTRAINTS": "board_constraints.xdc",
}
