#!/usr/bin/env python3
"""
Shared PCI capability constants for PCILeechFWGenerator.

This module centralizes constants used by multiple capability analyzers so
they're not duplicated inside analyzer classes.
"""

# PCI class codes for USB devices
CLASS_CODES = {
    "uhci": 0x0C0300,  # Serial bus controller, USB (UHCI)
    "ohci": 0x0C0310,  # Serial bus controller, USB (OHCI)
    "ehci": 0x0C0320,  # Serial bus controller, USB2 (EHCI)
    "xhci": 0x0C0330,  # Serial bus controller, USB3 (xHCI)
    "usb4": 0x0C0340,  # Serial bus controller, USB4
    "other_usb": 0x0C0380,  # Serial bus controller, USB (Other)
}

# PCI Configuration Space Register Offsets
PCI_VENDOR_ID_OFFSET = 0x00
PCI_DEVICE_ID_OFFSET = 0x02
PCI_STATUS_REGISTER = 0x06
PCI_CAPABILITIES_POINTER = 0x34

# PCI Extended Configuration Space
PCI_EXT_CAP_START = 0x100
PCI_EXT_CONFIG_SPACE_END = 0x1000

# PCI Status Register Bits
PCI_STATUS_CAP_LIST = 0x10  # Capabilities List bit (bit 4)

# PCI Capability Header Offsets
PCI_CAP_ID_OFFSET = 0x00
PCI_CAP_NEXT_PTR_OFFSET = 0x01

# PCI Extended Capability Header Fields
PCI_EXT_CAP_ID_MASK = 0xFFFF
PCI_EXT_CAP_VERSION_MASK = 0xF
PCI_EXT_CAP_VERSION_SHIFT = 16
PCI_EXT_CAP_NEXT_PTR_MASK = 0xFFF
PCI_EXT_CAP_NEXT_PTR_SHIFT = 20

# PCI Extended Capability Alignment
PCI_EXT_CAP_ALIGNMENT = 0x3  # DWORD alignment mask

# Configuration Space Size Limits
PCI_CONFIG_SPACE_MIN_SIZE = 256  # Minimum 256 bytes
PCI_CONFIG_SPACE_MIN_HEX_CHARS = 512  # 256 bytes * 2 hex chars per byte

# Capability-Specific Offsets and Values

# Power Management Capability
PM_CAP_CAPABILITIES_OFFSET = 2  # PMC register offset from capability header
PM_CAP_D3HOT_SUPPORT = 0x0008  # D3hot support bit

# PCI Express Capability
PCIE_CAP_LINK_CONTROL_OFFSET = 0x10  # Link Control register offset
PCIE_CAP_DEVICE_CONTROL2_OFFSET = 0x28  # Device Control 2 register offset
PCIE_LINK_CONTROL_ASPM_MASK = 0x0003  # ASPM Control bits (0-1)
PCIE_DEVICE_CONTROL2_OBFF_LTR_MASK = 0x6400  # OBFF Enable (13-14) and LTR Enable (10)

# Access Control Services (ACS) Extended Capability
ACS_CONTROL_REGISTER_OFFSET = 6  # ACS Control Register offset from capability header

# Downstream Port Containment (DPC) Extended Capability
DPC_CONTROL_REGISTER_OFFSET = 6  # DPC Control Register offset from capability header

# Resizable BAR Extended Capability
RBAR_CAPABILITY_REGISTER_OFFSET = 8  # First BAR capability register offset
# Clear bits [31:27] (sizes above 128MB). Keep only lower 27 bits.
RBAR_SIZE_MASK_ABOVE_128MB = 0x07FFFFFF

# Standard Capability Names Mapping
STANDARD_CAPABILITY_NAMES = {
    0x01: "Power Management",
    0x02: "AGP",
    0x03: "VPD",
    0x04: "Slot ID",
    0x05: "MSI",
    0x06: "CompactPCI Hot Swap",
    0x07: "PCI-X",
    0x08: "HyperTransport",
    0x09: "Vendor-Specific",
    0x0A: "Debug Port",
    0x0B: "CompactPCI CRC",
    0x0C: "PCI Hot Plug",
    0x0D: "PCI Bridge Subsystem VID",
    0x0E: "AGP 8x",
    0x0F: "Secure Device",
    0x10: "PCI Express",
    0x11: "MSI-X",
    0x12: "SATA Data Index Conf",
    0x13: "Advanced Features",
}

# Extended Capability Names Mapping
EXTENDED_CAPABILITY_NAMES = {
    0x0001: "Advanced Error Reporting",
    0x0002: "Virtual Channel",
    0x0003: "Device Serial Number",
    0x0004: "Power Budgeting",
    0x0005: "Root Complex Link Declaration",
    0x0006: "Root Complex Internal Link Control",
    0x0007: "Root Complex Event Collector Endpoint Association",
    0x0008: "Multi-Function Virtual Channel",
    0x0009: "Virtual Channel (MFVC)",
    0x000A: "Root Complex Register Block",
    0x000B: "Vendor-Specific Extended",
    0x000C: "Config Access Correlation",
    0x000D: "Access Control Services",
    0x000E: "Alternative Routing-ID Interpretation",
    0x000F: "Address Translation Services",
    0x0010: "Single Root I/O Virtualization",
    0x0011: "Multi-Root I/O Virtualization",
    0x0012: "Multicast",
    0x0013: "Page Request",
    0x0014: "Reserved for AMD",
    0x0015: "Resizable BAR",
    0x0016: "Dynamic Power Allocation",
    0x0017: "TPH Requester",
    0x0018: "Latency Tolerance Reporting",
    0x0019: "Secondary PCI Express",
    0x001A: "Protocol Multiplexing",
    0x001B: "Process Address Space ID",
    0x001C: "LN Requester",
    0x001D: "Downstream Port Containment",
    0x001E: "L1 PM Substates",
    0x001F: "Precision Time Measurement",
    0x0020: "PCI Express over M-PHY",
    0x0021: "FRS Queueing",
    0x0022: "Readiness Time Reporting",
    0x0023: "Designated Vendor-Specific",
    0x0024: "VF Resizable BAR",
    0x0025: "Data Link Feature",
    0x0026: "Physical Layer 16.0 GT/s",
    0x0027: "Lane Margining at Receiver",
    0x0028: "Hierarchy ID",
    0x0029: "Native PCIe Enclosure Management",
}

# Capabilities with 2-byte headers (instead of standard 1-byte)
TWO_BYTE_HEADER_CAPABILITIES = {0x07, 0x04}  # PCI-X and Slot ID

# Capability Size Constants
# Standard capability size estimates in bytes
STD_CAP_SIZE_POWER_MANAGEMENT = 8
STD_CAP_SIZE_MSI = 24
STD_CAP_SIZE_MSI_X = 12
STD_CAP_SIZE_PCI_EXPRESS = 60
STD_CAP_SIZE_DEFAULT = 16

# Extended capability size estimates in bytes
EXT_CAP_SIZE_ADVANCED_ERROR_REPORTING = 48
EXT_CAP_SIZE_ACCESS_CONTROL_SERVICES = 8
EXT_CAP_SIZE_DOWNSTREAM_PORT_CONTAINMENT = 16
EXT_CAP_SIZE_RESIZABLE_BAR = 16
EXT_CAP_SIZE_DEFAULT = 32

## MSI-X Constants
MSIX_CAPABILITY_SIZE = 12  # MSI-X capability structure is 12 bytes
MSIX_MESSAGE_CONTROL_OFFSET = 2
MSIX_TABLE_OFFSET_BIR_OFFSET = 4
MSIX_PBA_OFFSET_BIR_OFFSET = 8

# MSI-X Message Control register bit definitions
MSIX_TABLE_SIZE_MASK = 0x07FF  # Bits 0-10
MSIX_FUNCTION_MASK_BIT = 0x4000  # Bit 14
MSIX_ENABLE_BIT = 0x8000  # Bit 15

# MSI-X Table/PBA offset register bit definitions
MSIX_BIR_MASK = 0x7  # Bits 0-2
MSIX_OFFSET_MASK = 0xFFFFFFF8  # Bits 3-31

# MSI-X constraints
MSIX_MIN_TABLE_SIZE = 1
MSIX_MAX_TABLE_SIZE = 2048
MSIX_MAX_BIR = 5
MSIX_OFFSET_ALIGNMENT = 8
MSIX_LARGE_TABLE_THRESHOLD = 64

# USB Function Analyzer Constants

# Additional Vendor IDs (beyond those in device_clone.constants)
VENDOR_ID_NEC = 0x1033
VENDOR_ID_VIA = 0x1106

# Device ID Pattern Masks and Thresholds
USB_DEVICE_LOWER_MASK = 0xFF00
USB_DEVICE_UPPER_SHIFT = 8
USB_DEVICE_UPPER_MASK = 0xFF

# USB Device Category Thresholds (based on device_upper)
USB_CATEGORY_USB4_THRESHOLD = 0xA0
USB_CATEGORY_XHCI_THRESHOLD_HIGH = 0x90
USB_CATEGORY_XHCI_THRESHOLD_LOW = 0x80
USB_CATEGORY_EHCI_THRESHOLD = 0x60
USB_CATEGORY_UHCI_THRESHOLD = 0x30

# Intel USB Device ID Patterns
INTEL_XHCI_PATTERNS = [0x1E00, 0x1F00, 0x8C00, 0x9C00]
INTEL_EHCI_PATTERNS = [0x2600, 0x2700]
INTEL_UHCI_PATTERNS = [0x2400, 0x2500]

# AMD USB Device ID Patterns
AMD_XHCI_PATTERNS = [0x7800, 0x7900]
AMD_EHCI_PATTERNS = [0x7600, 0x7700]

# NEC USB Device ID Patterns
NEC_XHCI_PATTERNS = [0x0100, 0x0200]

# VIA USB Device ID Patterns
VIA_UHCI_PATTERNS = [0x3000, 0x3100]

# PCI Capability IDs used in USB analysis
USB_CAP_ID_PM = 0x01  # Power Management
USB_CAP_ID_MSI = 0x05  # MSI
USB_CAP_ID_PCIE = 0x10  # PCI Express
USB_CAP_ID_MSIX = 0x11  # MSI-X

# USB Device Feature Thresholds
USB_MSIX_SUPPORT_THRESHOLD = 0x1000
USB_VERSION_31_THRESHOLD = 0x8000
USB_PORT_COUNT_HIGH_THRESHOLD_XHCI = 0x1500
USB_PORT_COUNT_HIGH_THRESHOLD_EHCI = 0x2600

# USB Power Management Values
USB_AUX_CURRENT_XHCI_USB4 = 200
USB_AUX_CURRENT_OTHER = 100

# USB MSI Configuration Values
USB_MSI_MESSAGES_XHCI_USB4 = 4
USB_MSI_MESSAGES_OTHER = 2

# USB PCIe Configuration Values
USB_PCIE_MAX_PAYLOAD_SIZE = 256

# USB Queue Count Base Values
USB_QUEUE_BASE_USB4 = 16
USB_QUEUE_BASE_XHCI = 8
USB_QUEUE_BASE_EHCI = 4
USB_QUEUE_BASE_OTHER = 2

# USB Queue Count Calculation Constants
USB_ENTROPY_MASK = 0x7
USB_ENTROPY_DIVISOR = 16.0
USB_ENTROPY_VARIATION_FACTOR = 0.5

# USB BAR Sizes
USB_BAR_SIZE_XHCI_BASE = 0x10000
USB_BAR_SIZE_MSIX_TABLE = 0x1000
USB_BAR_SIZE_EHCI_BASE = 0x1000
USB_BAR_SIZE_IO_PORTS = 0x20

# USB Port Counts
USB_PORT_COUNT_USB4 = 2
USB_PORT_COUNT_XHCI_HIGH = 8
USB_PORT_COUNT_XHCI_LOW = 4
USB_PORT_COUNT_EHCI_HIGH = 8
USB_PORT_COUNT_EHCI_LOW = 4
USB_PORT_COUNT_UHCI = 2
USB_PORT_COUNT_OHCI = 4

# USB Version Strings
USB_VERSION_40 = "4.0"
USB_VERSION_31 = "3.1"
USB_VERSION_30 = "3.0"
USB_VERSION_20 = "2.0"
USB_VERSION_11 = "1.1"

# USB Speed Strings
USB_SPEED_40GBPS = "40Gbps"
USB_SPEED_10GBPS = "10Gbps"
USB_SPEED_5GBPS = "5Gbps"
USB_SPEED_480MBPS = "480Mbps"
USB_SPEED_12MBPS = "12Mbps"

# =============================================================================
# COMMON PCI CAPABILITY IDs (Shared across all function analyzers)
# =============================================================================

# Standard PCI Capability IDs
CAP_ID_PM = 0x01  # Power Management
CAP_ID_MSI = 0x05  # Message Signaled Interrupts
CAP_ID_PCIE = 0x10  # PCI Express
CAP_ID_MSIX = 0x11  # MSI-X
CAP_ID_VENDOR_SPECIFIC = 0x09  # Vendor-specific capability

# Extended PCI Capability IDs
EXT_CAP_ID_AER = 0x0001  # Advanced Error Reporting
EXT_CAP_ID_SRIOV = 0x0010  # SR-IOV
EXT_CAP_ID_ACS = 0x000D  # Access Control Services
EXT_CAP_ID_LTR = 0x0018  # Latency Tolerance Reporting
EXT_CAP_ID_PTM = 0x001F  # Precision Time Measurement
EXT_CAP_ID_ARI = 0x000E  # Alternative Routing-ID Interpretation

# =============================================================================
# VENDOR IDs (Additional beyond device_clone.constants)
# =============================================================================

VENDOR_ID_CMEDIA = 0x13F6  # C-Media
VENDOR_ID_CREATIVE = 0x1274  # Creative Labs
VENDOR_ID_BROADCOM = 0x14E4  # Broadcom
VENDOR_ID_SAMSUNG = 0x144D  # Samsung
VENDOR_ID_MARVELL = 0x1B4B  # Marvell
VENDOR_ID_LSI_BROADCOM = 0x1000  # LSI/Broadcom

# =============================================================================
# DEVICE ID PATTERN MASKS AND CALCULATIONS
# =============================================================================

# Common Device ID Masks
DEVICE_ID_LOWER_MASK = 0xFF00
DEVICE_ID_UPPER_SHIFT = 8
DEVICE_ID_UPPER_MASK = 0xFF
DEVICE_ID_ENTROPY_MASK = 0x7
DEVICE_ID_PARITY_MASK = 0x1

# Entropy calculation constants
ENTROPY_MASK = 0xF
ENTROPY_DIVISOR = 32.0
ENTROPY_VARIATION_FACTOR = 0.5

# =============================================================================
# DEVICE CLASS CODES (Extended from existing CLASS_CODES)
# =============================================================================

# Add to existing CLASS_CODES dictionary
MEDIA_CLASS_CODES = {
    "audio": 0x040100,  # Multimedia controller, Audio device
    "video": 0x040000,  # Multimedia controller, Video
    "hdaudio": 0x040300,  # Multimedia controller, HD Audio
    "other_media": 0x040800,  # Multimedia controller, Other
}

NETWORK_CLASS_CODES = {
    "ethernet": 0x020000,
    "wifi": 0x028000,
    "bluetooth": 0x0D1100,
    "cellular": 0x028000,
}

STORAGE_CLASS_CODES = {
    "scsi": 0x010000,  # Mass storage controller, SCSI
    "ide": 0x010100,  # Mass storage controller, IDE
    "floppy": 0x010200,  # Mass storage controller, Floppy
    "ipi": 0x010300,  # Mass storage controller, IPI bus
    "raid": 0x010400,  # Mass storage controller, RAID
    "ata": 0x010500,  # Mass storage controller, ATA
    "sata": 0x010601,  # Mass storage controller, Serial ATA (AHCI)
    "sas": 0x010700,  # Mass storage controller, Serial Attached SCSI
    "nvme": 0x010802,  # Mass storage controller, NVMe
    "other_storage": 0x018000,  # Mass storage controller, Other
}

# =============================================================================
# MEDIA FUNCTION CONSTANTS
# =============================================================================

# Media device ID ranges
INTEL_HDAUDIO_RANGES = [0x2600, 0x2700, 0x2800]
INTEL_VIDEO_RANGES = [0x5900, 0x5A00]
NVIDIA_HDMI_AUDIO_RANGES = [0x0E00, 0x0F00]
NVIDIA_VIDEO_RANGES = [0x1000, 0x1100]
AMD_AUDIO_RANGES = [0xAA00, 0xAB00]

# Media device categorization thresholds
DEVICE_UPPER_HDAUDIO_THRESHOLD = 0x80
DEVICE_UPPER_VIDEO_THRESHOLD = 0x50

# Media device feature thresholds
HIGH_END_DEVICE_THRESHOLD = 0x2000
VENDOR_CAP_DEVICE_THRESHOLD = 0x1000
HDAUDIO_MULTICHANNEL_THRESHOLD = 0x2500
VIDEO_HIGH_FRAMERATE_THRESHOLD = 0x1500
VIDEO_HARDWARE_ENCODING_THRESHOLD = 0x2500

# Media BAR sizes
BAR_SIZE_HDAUDIO_REGISTERS = 0x4000
BAR_SIZE_VIDEO_FRAMEBUFFER = 0x10000
BAR_SIZE_VIDEO_REGISTERS = 0x2000
BAR_SIZE_AUDIO_REGISTERS = 0x1000

# Media power management constants
HDAUDIO_AUX_CURRENT_MA = 50

# Media queue count defaults
QUEUE_COUNT_HDAUDIO_HIGH = 8
QUEUE_COUNT_HDAUDIO_BASIC = 4
QUEUE_COUNT_VIDEO = 4
QUEUE_COUNT_AUDIO = 2
QUEUE_COUNT_MIN = 1

# Audio specifications
SAMPLE_RATES_HDAUDIO = [44100, 48000, 96000, 192000]
SAMPLE_RATES_BASIC_AUDIO = [44100, 48000]
BIT_DEPTHS_HDAUDIO = [16, 20, 24, 32]
BIT_DEPTHS_BASIC_AUDIO = [16]
CHANNELS_MULTICHANNEL = 8
CHANNELS_STEREO = 2

# Video specifications
FRAME_RATES_HIGH = [30, 60]
FRAME_RATES_BASIC = [30]

# Default PCIe parameters
DEFAULT_PCIE_MAX_PAYLOAD_SIZE = 128

# =============================================================================
# NETWORK FUNCTION CONSTANTS
# =============================================================================

# Network device ID thresholds
DEVICE_ID_THRESHOLD_BASIC = 0x1000
DEVICE_ID_THRESHOLD_ENTERPRISE = 0x1200
DEVICE_ID_THRESHOLD_ADVANCED = 0x1500
DEVICE_ID_THRESHOLD_HIGH_END = 0x1700
DEVICE_ID_THRESHOLD_ULTRA_HIGH = 0x2000
DEVICE_ID_THRESHOLD_WIFI_ADVANCED = 0x2400
DEVICE_ID_THRESHOLD_WIFI_PREMIUM = 0x2500
DEVICE_ID_THRESHOLD_WIFI_ULTRA = 0x2700

# Network device category patterns
DEVICE_PATTERN_HIGH_FEATURE = 0x80
DEVICE_PATTERN_WIRELESS = 0x20
DEVICE_PATTERN_INTEL_LAN_BASE = 0x1500
DEVICE_PATTERN_INTEL_LAN_EXT = 0x1600
DEVICE_PATTERN_INTEL_WIRELESS_BASE = 0x2500
DEVICE_PATTERN_INTEL_WIRELESS_EXT = 0x2600
DEVICE_PATTERN_REALTEK_ETH_BASE = 0x8100
DEVICE_PATTERN_REALTEK_ETH_EXT = 0x8200
DEVICE_PATTERN_REALTEK_WIFI_BASE = 0x8700
DEVICE_PATTERN_REALTEK_WIFI_EXT = 0x8800

# Enterprise device patterns
INTEL_ENTERPRISE_MASK = 0x0F00
INTEL_ENTERPRISE_THRESHOLD = 0x0500
BROADCOM_ENTERPRISE_MASK = 0x00F0
BROADCOM_ENTERPRISE_THRESHOLD = 0x0080

# Network BAR configuration constants
BASE_REGISTER_SIZE_BASIC = 0x10000
BASE_REGISTER_SIZE_ADVANCED = 0x20000
REGISTER_SIZE_VARIATION_MASK = 0xF
REGISTER_SIZE_VARIATION_MULTIPLIER = 0x1000
MSIX_TABLE_MIN_SIZE = 0x1000
MSIX_TABLE_ENTRY_SIZE = 16
MSIX_TABLE_ALIGN_MASK = 0xFFF
ETHERNET_FLASH_SIZE = 0x4000
WIFI_REGISTER_SIZE = 0x100000

# Network queue count constants
BASE_QUEUE_COUNT = 4
QUEUE_COUNT_ADVANCED = 16
QUEUE_COUNT_HIGH = 32
QUEUE_COUNT_ULTRA = 64
WIFI_MAX_QUEUES = 16
SRIOV_MIN_QUEUES = 32

# Network VF constants
BASE_VF_COUNT = 8
VF_COUNT_HIGH = 32
VF_COUNT_ULTRA = 64
VF_COUNT_VARIATION_MASK = 0x7
VF_COUNT_VARIATION_OFFSET = 4

# Network latency constants
LTR_LATENCY_ETHERNET = 0x1001
LTR_LATENCY_WIFI = 0x1003
PTM_CLOCK_GRANULARITY = 0xFF  # 255ns

# SR-IOV constants
SRIOV_SUPPORTED_PAGE_SIZES = 0x553
SRIOV_SYSTEM_PAGE_SIZE = 0x1

# MSI-X BAR allocation patterns
MSIX_BAR_VARIATION_MASK = 0x0F
MSIX_BAR_VARIATION_THRESHOLD = 8

# Size padding constants
SIZE_PADDING_MASK = 0x7

# =============================================================================
# STORAGE FUNCTION CONSTANTS
# =============================================================================

# Storage device ID range patterns
STORAGE_DEVICE_ID_RANGES = {
    "intel_sata": [0x2800, 0x2900, 0x3A00],
    "intel_nvme": [0x0900, 0x0A00],
    "samsung_nvme": [0xA800, 0xA900],
    "marvell_sata": [0x9100, 0x9200],
    "lsi_sas": [0x0050, 0x0060],
    "lsi_raid": [0x0070],
}

# Storage device ID thresholds
STORAGE_DEVICE_ID_THRESHOLDS = {
    "high_end_nvme": 0xA000,
    "enterprise_nvme": 0xA500,
    "high_performance_nvme": 0xA800,
    "high_end_storage": 0x2000,
    "enterprise_storage": 0x1500,
    "device_upper_nvme": 0xA0,
    "device_upper_sata": 0x80,
    "device_upper_sas": 0x50,
}

# Storage MSI message capabilities
STORAGE_MSI_MESSAGES = {
    "nvme": 5,  # Up to 32 messages
    "sas": 4,  # Up to 16 messages
    "raid": 4,  # Up to 16 messages
    "default": 3,  # Up to 8 messages
}

# Storage maximum payload sizes (bytes)
STORAGE_MAX_PAYLOAD_SIZES = {
    "nvme": 512,
    "sas": 256,
    "raid": 256,
    "default": 128,
}

# Storage base queue counts
STORAGE_BASE_QUEUE_COUNTS = {
    "nvme_high_end": 64,
    "nvme_standard": 32,
    "sas_enterprise": 16,
    "sas_standard": 8,
    "default": 4,
    "minimum": 2,
}

# Storage BAR sizes
STORAGE_BAR_SIZES = {
    "nvme_registers": 0x4000,  # 16KB for NVMe controllers
    "sas_raid_registers": 0x8000,  # 32KB for SAS/RAID controllers
    "sata_registers": 0x2000,  # 8KB for SATA controllers
    "legacy_io": 0x100,  # 256 bytes for legacy IO
    "minimum_msix_table": 0x1000,  # 4KB minimum MSI-X table
}

# Storage power management constants
STORAGE_POWER_CONSTANTS = {
    "raid_aux_current": 100,  # mA for RAID controllers
    "default_aux_current": 0,
}

# Storage feature detection thresholds
STORAGE_FEATURE_THRESHOLDS = {
    "namespace_management": 0xA000,
    "max_namespaces_high": 0xA500,
    "pci_gen4": 0xA800,
    "port_multiplier": 0x1500,
}

# Storage device limits
STORAGE_DEVICE_LIMITS = {
    "max_namespaces_high": 256,
    "max_namespaces_standard": 64,
    "max_drives_enterprise": 64,
    "max_drives_standard": 16,
    "max_ports_high": 8,
    "max_ports_standard": 4,
}

# Storage bit manipulation constants
STORAGE_BIT_MANIPULATION = {
    "device_id_lower_mask": 0xFF00,
    "device_id_upper_shift": 8,
    "device_id_upper_mask": 0xFF,
    "entropy_mask": 0xF,
    "entropy_divisor": 32.0,
    "entropy_factor": 0.5,
    "device_id_parity_mask": 0x1,
    "alignment_mask": 0xFFF,
}

# Storage AER capability register values
AER_CAPABILITY_VALUES = {
    "uncorrectable_error_mask": 0x00000000,
    "uncorrectable_error_severity": 0x00462030,
    "correctable_error_mask": 0x00002000,
    "advanced_error_capabilities": 0x00000020,
}

# =============================================================================
# COMMON BAR SIZES (Shared across all function types)
# =============================================================================

# Common MSI-X table size
BAR_SIZE_MSIX_TABLE = 0x1000

# =============================================================================
# MSI-X VALIDATOR CONSTANTS (formerly inline in msix_bar_validator)
# =============================================================================

# BAR index bounds
BAR_INDEX_MIN = 0
BAR_INDEX_MAX = 5  # Standard 6 BARs (0-5)
NON_STANDARD_BAR_MAX = 2  # Typical expectation for MSI-X placement (BAR 0-2)

# Alignment and sizing
PAGE_SIZE_4K = 0x1000
CACHELINE_OPTIMAL = 64
MSIX_TABLE_ENTRY_SIZE_BYTES = 16  # Same as MSIX_TABLE_ENTRY_SIZE
PBA_VECTORS_PER_DWORD = 32  # 1 bit per vector
DWORD_SIZE_BYTES = 4

# Vector count thresholds
LARGE_VECTOR_WARNING_THRESHOLD = 256
INTEL_VECTOR_WARNING_THRESHOLD = 128
EXCESSIVE_VECTOR_THRESHOLD_LOWEND = 64
LOW_END_DEVICE_ID_THRESHOLD = 0x1500
OVERSIZED_BAR_FACTOR = 4  # BAR > needed * factor triggers warning

# Reserved region heuristic (first 32KB for control/PIO/etc.)
RESERVED_REGION_CONTROL_END = 0x8000
RESERVED_REGIONS = [
    {"start": 0x0000, "end": 0x1000, "name": "Device Control Registers"},
    {"start": 0x4000, "end": 0x8000, "name": "Custom PIO Region"},
    {"start": 0xF000, "end": 0x10000, "name": "Configuration Space Shadow"},
]
