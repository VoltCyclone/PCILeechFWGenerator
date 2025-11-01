"""
Named constants for commonly used hex values to replace magic numbers.
This reduces duplication and makes the code more maintainable.
"""

# PCI Capability IDs (as hex values) - only the ones actually used
CAP_ID_MSI = 0x05
CAP_ID_VENDOR_SPECIFIC = 0x09
CAP_ID_PCIE = 0x10
CAP_ID_MSIX = 0x11

# Extended Capability IDs - only the ones actually used
EXT_CAP_ID_AER = 0x0001
EXT_CAP_ID_ACS = 0x000D
EXT_CAP_ID_ARI = 0x000E
EXT_CAP_ID_SRIOV = 0x0010
EXT_CAP_ID_LTR = 0x0018
EXT_CAP_ID_PTM = 0x001F

# Full class codes (6 hex chars) - only the ones actually used
CLASS_CODE_UNKNOWN = "000000"

# Common default values - only the ones actually used
DEFAULT_REVISION_ID = "00"

# BAR type bits
BAR_TYPE_IO = 0x01

# Common sizes - only the ones actually used
SIZE_64KB = 0x10000
SIZE_1MB = 0x100000
