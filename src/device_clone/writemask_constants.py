"""
Writemask and PCIe capability constants used by the writemask generator.

This module centralizes all static tables and bitmask definitions so logic
modules remain focused and DRY.
"""

# Write-protected bits for standard PCI configuration space
WRITE_PROTECTED_BITS_PCIE = ( #pragma: no cover
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
WRITE_PROTECTED_BITS_PM = ( #pragma: no cover
    "00000000",  # PM Cap ID, Next Ptr, PM Capabilities
    "031F0000",  # PMCSR, PMCSR_BSE
)

# Write-protected bits for MSI capability variations
WRITE_PROTECTED_BITS_MSI_ENABLED_0 = ("00007104",)  # MSI Control (enable bit writable)

WRITE_PROTECTED_BITS_MSI_64_BIT_1 = ( #pragma: no cover
    "00007104",  # MSI Control
    "03000000",  # Message Address Low
    "00000000",  # Message Address High
    "ffff0000",  # Message Data
)

WRITE_PROTECTED_BITS_MSI_MULTIPLE_MESSAGE_ENABLED_1 = ( #pragma: no cover
    "00007104",  # MSI Control
    "03000000",  # Message Address Low
    "00000000",  # Message Data
)

WRITE_PROTECTED_BITS_MSI_MULTIPLE_MESSAGE_CAPABLE_1 = ( #pragma: no cover
    "00007104",  # MSI Control
    "03000000",  # Message Address Low
    "00000000",  # Message Data
    "ffff0000",  # Reserved
    "00000000",  # Reserved
    "01000000",  # Reserved
)

# Write-protected bits for MSI-X capability variations
WRITE_PROTECTED_BITS_MSIX_3 = ( #pragma: no cover
    "000000c0",  # MSI-X Control
    "00000000",  # Table Offset/BIR
    "00000000",  # PBA Offset/BIR
)

WRITE_PROTECTED_BITS_MSIX_4 = ( #pragma: no cover
    "000000c0",  # MSI-X Control
    "00000000",  # Table Offset/BIR
    "00000000",  # PBA Offset/BIR
    "00000000",  # Reserved
)

WRITE_PROTECTED_BITS_MSIX_5 = ( #pragma: no cover
    "000000c0",  # MSI-X Control
    "00000000",  # Table Offset/BIR
    "00000000",  # PBA Offset/BIR
    "00000000",  # Reserved
    "00000000",  # Reserved
)

WRITE_PROTECTED_BITS_MSIX_6 = ( #pragma: no cover
    "000000c0",  # MSI-X Control
    "00000000",  # Table Offset/BIR
    "00000000",  # PBA Offset/BIR
    "00000000",  # Reserved
    "00000000",  # Reserved
    "00000000",  # Reserved
)

WRITE_PROTECTED_BITS_MSIX_7 = ( #pragma: no cover
    "000000c0",  # MSI-X Control
    "00000000",  # Table Offset/BIR
    "00000000",  # PBA Offset/BIR
    "00000000",  # Reserved
    "00000000",  # Reserved
    "00000000",  # Reserved
    "00000000",  # Reserved
)

WRITE_PROTECTED_BITS_MSIX_8 = ( #pragma: no cover
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
WRITE_PROTECTED_BITS_VPD = ( #pragma: no cover
    "0000ffff",  # VPD Address
    "ffffffff",  # VPD Data
)

WRITE_PROTECTED_BITS_VSC = ( #pragma: no cover
    "000000ff",  # Vendor Specific Cap ID
    "ffffffff",  # Vendor Specific Data
)

WRITE_PROTECTED_BITS_TPH = ( #pragma: no cover
    "00000000",  # TPH Requester Cap
    "00000000",  # TPH Requester Control
    "070c0000",  # ST Table
)

WRITE_PROTECTED_BITS_VSEC = ( #pragma: no cover
    "00000000",  # VSEC Cap
    "00000000",  # VSEC Header
    "ffffffff",  # Vendor Specific
    "ffffffff",  # Vendor Specific
)

WRITE_PROTECTED_BITS_AER = ( #pragma: no cover
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

WRITE_PROTECTED_BITS_DSN = ( #pragma: no cover
    "00000000",  # DSN Cap
    "00000000",  # Serial Number Low
    "00000000",  # Serial Number High
)

WRITE_PROTECTED_BITS_LTR = ( #pragma: no cover
    "00000000",  # LTR Cap
    "00000000",  # Max Snoop/No-Snoop Latency
)

WRITE_PROTECTED_BITS_L1PM = ( #pragma: no cover
    "00000000",  # L1 PM Substates Cap
    "00000000",  # L1 PM Substates Control 1
    "3f00ffe3",  # L1 PM Substates Control 2
    "fb000000",  # Reserved
)

WRITE_PROTECTED_BITS_PTM = ( #pragma: no cover
    "00000000",  # PTM Cap
    "00000000",  # PTM Control
    "00000000",  # PTM Effective Granularity
    "03ff0000",  # Reserved
)

WRITE_PROTECTED_BITS_VC = ( #pragma: no cover
    "00000000",  # VC Cap
    "00000000",  # Port VC Cap 1
    "00000000",  # Port VC Cap 2
    "0F000000",  # Port VC Control
    "00000000",  # Port VC Status
    "FF000F87",  # VC Resource Cap
    "00000000",  # VC Resource Control
)

# Capability ID mappings
CAPABILITY_NAMES = { #pragma: no cover
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

EXTENDED_CAPABILITY_NAMES = { #pragma: no cover
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

# Fixed configuration space protection section
FIXED_SECTION = ( #pragma: no cover
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
WRITEMASK_DICT = { #pragma: no cover
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
