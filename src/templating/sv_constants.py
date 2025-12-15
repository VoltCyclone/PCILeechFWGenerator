"""Constants for SystemVerilog generation."""

from typing import Any, Dict, List, Set

# Reuse shared validation constants instead of redefining overlapping values
from src.utils import context_error_messages as CEM

from src.utils import validation_constants as VC


class SVConstants:
    """SystemVerilog generation constants."""

    # Default values
    DEFAULT_FIFO_DEPTH: int = 512
    DEFAULT_DATA_WIDTH: int = 128
    DEFAULT_FPGA_FAMILY: str = "artix7"
    # CRITICAL: Use unknown device class, NOT Ethernet!
    # Using Ethernet (020000) as default causes devices to enumerate incorrectly
    DEFAULT_CLASS_CODE: str = "000000"  # Unknown device - should be read from hardware
    DEFAULT_REVISION_ID: str = "00"  # Unknown revision - should be read from hardware
    # Integer variants for modules that operate on numeric config-space values
    DEFAULT_CLASS_CODE_INT: int = 0x000000
    DEFAULT_REVISION_ID_INT: int = 0x00
    DEFAULT_SUBSYSTEM_ID: str = "0000"

    # Common sizes and alignments
    PAGE_SIZE_BYTES: int = 0x1000
    CONFIG_SPACE_DEFAULT_SIZE: int = 256

    # Extended capability pointer defaults
    EXTENDED_CAP_PTR_DEFAULT: int = 0x100

    # MSI-X defaults and alignment
    DEFAULT_NUM_MSIX: int = 0
    DEFAULT_MSIX_TABLE_BIR: int = 0
    DEFAULT_MSIX_PBA_BIR: int = 0
    DEFAULT_MSIX_TABLE_OFFSET: int = 0x1000
    DEFAULT_MSIX_PBA_OFFSET: int = 0x2000
    MSIX_ALIGNMENT_BYTES: int = 8
    # Deterministic MSI-X test-only values (used when running under pytest)
    MSIX_TEST_ADDR_BASE: int = 0xFEE00000
    MSIX_TEST_ADDR_HIGH: int = 0x00000000
    MSIX_TEST_VECTOR_CTRL_DEFAULT: int = 0x00000000

    # Shared defaults re-used from validation constants
    DEFAULT_COUNTER_WIDTH: int = VC.DEFAULT_COUNTER_WIDTH

    # File headers reused across generators
    SV_FILE_HEADER: str = VC.SV_FILE_HEADER

    # Validation ranges
    MIN_PAYLOAD_SIZE: int = 128
    MAX_PAYLOAD_SIZE: int = 4096
    MIN_READ_REQUEST_SIZE: int = 128
    MAX_READ_REQUEST_SIZE: int = 4096
    MIN_QUEUE_DEPTH: int = 1
    MAX_QUEUE_DEPTH: int = 65536
    MIN_FREQUENCY_MHZ: float = 1.0
    MAX_BASE_FREQUENCY_MHZ: float = 1000.0
    MAX_MEMORY_FREQUENCY_MHZ: float = 2000.0

    # Misc defaults used across templates
    DEFAULT_MPS_BYTES: int = 256
    DEFAULT_DUAL_PORT: bool = False
    DEFAULT_USE_BYTE_ENABLES: bool = True
    DEFAULT_BAR_APERTURE_SIZE: int = PAGE_SIZE_BYTES
    DEFAULT_NUM_SOURCES: int = 1
    DEFAULT_MSI_VECTORS: int = 0
    CONFIG_SHADOW_PAGE_FROM_END: int = 1
    CUSTOM_WINDOW_PAGE_FROM_END: int = 2
    DEFAULT_ENABLE_CLOCK_CROSSING: bool = True
    DEFAULT_ENABLE_PERF_COUNTERS: bool = True
    DEFAULT_ENABLE_ERROR_DETECTION: bool = True
    DEFAULT_ENABLE_CUSTOM_CONFIG: bool = True
    DEFAULT_OUT_OF_RANGE_SENTINEL: str = "DEADBEEF"

    # PCI config-space default words commonly referenced by generators
    DEFAULT_PCI_STATUS: int = 0x0010
    DEFAULT_PCI_COMMAND: int = 0x0000

    # Power management defaults and PMCSR layout (centralized)
    # Note: PMCSR position within the capability is fixed at offset + 0x04.
    # The absolute capability offset in config space is device-specific and must
    # come from donor/profile; do not hardcode absolute offsets.
    DEFAULT_CLOCK_HZ: int = 100_000_000  # 100 MHz
    DEFAULT_POWER_TRANSITION_TIMEOUT_NS: int = 10_000_000  # 10 ms
    PMCSR_REL_OFFSET: int = 0x04  # PMCSR is at PMC base + 0x04
    # The following bit positions reflect current pmcsr_stub usage.
    PMCSR_POWER_STATE_LSB: int = 0
    PMCSR_POWER_STATE_MSB: int = 1
    PMCSR_PME_ENABLE_BIT: int = 15
    PMCSR_PME_STATUS_BIT: int = 14

    # Reuse device taxonomy from validation constants
    KNOWN_DEVICE_TYPES: List[str] = VC.KNOWN_DEVICE_TYPES
    DEVICE_CLASS_MAPPINGS: Dict[str, str] = VC.DEVICE_CLASS_MAPPINGS

    # SystemVerilog reserved keywords for identifier sanitization
    SV_RESERVED_KEYWORDS: Set[str] = {
        "assign",
        "module",
        "endmodule",
        "begin",
        "end",
        "logic",
        "wire",
        "reg",
        "input",
        "output",
        "inout",
        "parameter",
        "localparam",
        "always",
        "always_ff",
        "always_comb",
        "always_latch",
        "if",
        "else",
        "case",
        "endcase",
        "for",
        "while",
        "do",
        "function",
        "endfunction",
        "task",
        "endtask",
        "class",
        "endclass",
        "package",
        "endpackage",
        "interface",
        "endinterface",
        "typedef",
        "enum",
        "struct",
        "union",
        "initial",
        "final",
        "generate",
        "endgenerate",
    }

    # PCI/PCIe config-space register maps
    REGISTER_OFFSET_TO_NAME: Dict[int, str] = {
        0x00: "VENDOR_ID",
        0x02: "DEVICE_ID",
        0x04: "COMMAND",
        0x06: "STATUS",
        0x08: "REVISION_ID",
        0x0C: "CLASS_CODE",
        0x10: "BAR0",
        0x14: "BAR1",
        0x18: "BAR2",
        0x1C: "BAR3",
        0x20: "BAR4",
        0x24: "BAR5",
        0x50: "MSI_CTRL",
        0x60: "MSIX_CTRL",
    }

    # Reverse map for quick lookup
    REGISTER_NAME_TO_OFFSET: Dict[str, int] = {
        name: offset for offset, name in REGISTER_OFFSET_TO_NAME.items()
    }

    # Default PCILeech register block used when behavior profile is absent
    DEFAULT_PCILEECH_REGISTERS: List[Dict[str, Any]] = [
        {
            "name": "PCILEECH_CTRL",
            "offset": 0x00,
            "access_type": "rw",
            "size": 32,
        },
        {
            "name": "PCILEECH_STATUS",
            "offset": 0x04,
            "access_type": "ro",
            "size": 32,
        },
        {
            "name": "PCILEECH_ADDR_LO",
            "offset": 0x08,
            "access_type": "rw",
            "size": 32,
        },
        {
            "name": "PCILEECH_ADDR_HI",
            "offset": 0x0C,
            "access_type": "rw",
            "size": 32,
        },
        {
            "name": "PCILEECH_DATA",
            "offset": 0x10,
            "access_type": "rw",
            "size": 32,
        },
        {
            "name": "PCILEECH_SIZE",
            "offset": 0x14,
            "access_type": "rw",
            "size": 32,
        },
    ]


class SVTemplates:
    """Template paths for SystemVerilog generation."""

    DEVICE_SPECIFIC_PORTS: str = (
        "systemverilog/components/" "device_specific_ports.sv.j2"
    )
    MAIN_ADVANCED_CONTROLLER: str = "sv/advanced_controller.sv.j2"
    CLOCK_CROSSING: str = "sv/clock_crossing.sv.j2"
    BUILD_INTEGRATION: str = "python/build_integration.py.j2"
    PCILEECH_INTEGRATION: str = "python/pcileech_build_integration.py.j2"
    PCILEECH_TLPS_BAR_CONTROLLER: str = (
        "systemverilog/pcileech_tlps128_bar_controller.sv.j2"
    )
    PCILEECH_FIFO: str = "systemverilog/pcileech_fifo.sv.j2"
    DEVICE_CONFIG: str = "systemverilog/device_config.sv.j2"
    TOP_LEVEL_WRAPPER: str = "systemverilog/top_level_wrapper.sv.j2"
    PCILEECH_CFGSPACE: str = "systemverilog/pcileech_cfgspace.coe.j2"
    MSIX_CAPABILITY_REGISTERS: str = "systemverilog/msix_capability_registers.sv.j2"
    MSIX_IMPLEMENTATION: str = "systemverilog/msix_implementation.sv.j2"
    MSIX_TABLE: str = "systemverilog/msix_table.sv.j2"

    # Basic modules list
    BASIC_SV_MODULES: List[str] = [
        "bar_controller.sv.j2",
        "cfg_shadow.sv.j2",
        "device_config.sv.j2",
        "msix_capability_registers.sv.j2",
        "msix_implementation.sv.j2",
        "msix_table.sv.j2",
        "option_rom_bar_window.sv.j2",
        "option_rom_spi_flash.sv.j2",
        "top_level_wrapper.sv.j2",
    ]


class SVValidation:
    """Validation messages for SystemVerilog generation."""

    ERROR_MESSAGES: Dict[str, str] = {
        "undefined_var": CEM.UNDEFINED_VAR,
        "template_not_found": CEM.TEMPLATE_NOT_FOUND,
        "missing_device_config": CEM.MISSING_DEVICE_CONFIG,
        "invalid_device_type": CEM.INVALID_DEVICE_TYPE,
        "invalid_device_class": CEM.INVALID_DEVICE_CLASS,
        "invalid_numeric_param": CEM.INVALID_NUMERIC_PARAM,
        "no_template_context": CEM.TEMPLATE_CONTEXT_REQUIRED,
        "context_not_dict": CEM.TEMPLATE_CONTEXT_NOT_DICT,
        "missing_critical_field": CEM.MISSING_CRITICAL_FIELD_DEVICE_CONFIG,
        "device_config_not_dict": CEM.DEVICE_CONFIG_NOT_DICT,
        "missing_device_signature": CEM.MISSING_DEVICE_SIGNATURE,
        "empty_device_signature": CEM.EMPTY_DEVICE_SIGNATURE,
        "validation_failed": CEM.TEMPLATE_VALIDATION_FAILED,
        "missing_behavior_profile": CEM.MISSING_BEHAVIOR_PROFILE,
    }
    NO_DONOR_DEVICE_IDS_ERROR: str = (
        "Missing required device identifiers (vendor_id/device_id). "
        "Cannot generate donor-unique firmware without these values."
    )
    NO_MSIX_HARDWARE_DATA_ERROR: str = (
        "MSI-X table data must be read from actual hardware. "
        "Cannot generate safe firmware without real MSI-X values."
    )


# Create singleton instances
SV_CONSTANTS = SVConstants()
SV_TEMPLATES = SVTemplates()
SV_VALIDATION = SVValidation()
