"""Explicit writable masks for logical little-endian PCI DWORD values.

Polarity: 1 = writable, 0 = read-only. Word order matches cfgspace COE.
Reserved and unsupported bits are never granted writes by default.
"""

# Type-0 header; BAR and ROM grants are derived from the final image separately.
HEADER_RW_MASK = (
    0x00000000,  # Vendor / Device ID
    0x00000546,  # Command; IO Space is added only for a served IO BAR
    0x00000000,  # Revision / Class
    0x000000FF,  # Cache Line Size only
    0x00000000,  # BAR0
    0x00000000,  # BAR1
    0x00000000,  # BAR2
    0x00000000,  # BAR3
    0x00000000,  # BAR4
    0x00000000,  # BAR5
    0x00000000,  # Cardbus CIS
    0x00000000,  # Subsystem IDs
    0x00000000,  # Expansion ROM
    0x00000000,  # Capability pointer
    0x00000000,  # Reserved
    0x000000FF,  # Interrupt Line only
)

PM_RW_MASK = (0x00000000, 0x00000103)  # PowerState and PME_En; PME_Status RO
MSI_CONTROL_RW_MASK = 0x00710000  # Enable and Multiple Message Enable
MSI_ADDRESS_LOW_RW_MASK = 0xFFFFFFFC
MSI_ADDRESS_HIGH_RW_MASK = 0xFFFFFFFF
MSI_DATA_RW_MASK = 0x0000FFFF
MSIX_RW_MASK = (0xC0000000, 0x00000000, 0x00000000)  # Enable / Function Mask

PCIE_RW_MASK = (
    0x00000000,  # Capability header
    0x00000000,  # Device Capabilities
    0x00007FFF,  # Device Control; FLR and Device Status RO
    0x00000000,  # Link Capabilities
    0x000000CB,  # ASPM, RCB, Common Clock, Extended Synch; Link Status RO
    0x00000000,  # Slot Capabilities
    0x00000000,  # Slot Control / Status
    0x00000000,  # Root Control / Capabilities
    0x00000000,  # Root Status
    0x00000000,  # Device Capabilities 2
    0x00000000,  # Device Control 2: image-derived grants below
    0x00000000,  # Link Capabilities 2
    0x00000000,  # Link Control / Status 2
    0x00000000,  # Slot Capabilities 2
    0x00000000,  # Slot Control / Status 2
)
PCIE_DEVCTL2_TIMEOUT_RW_MASK = 0x0000000F
PCIE_DEVCTL2_TIMEOUT_DISABLE_RW_MASK = 0x00000010
PCIE_DEVCTL2_LTR_RW_MASK = 0x00000400

# Capability ID mappings
CAPABILITY_NAMES = {  # pragma: no cover
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

EXTENDED_CAPABILITY_NAMES = {  # pragma: no cover
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
