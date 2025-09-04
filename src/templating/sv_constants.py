"""Constants for SystemVerilog generation."""

from typing import Any, Dict, List, Set

# Reuse shared validation constants instead of redefining overlapping values
from src.utils import validation_constants as VC


class SVConstants:
    """SystemVerilog generation constants."""

    # Default values
    DEFAULT_FIFO_DEPTH: int = 512
    DEFAULT_DATA_WIDTH: int = 128
    DEFAULT_FPGA_FAMILY: str = "artix7"
    DEFAULT_CLASS_CODE: str = "020000"  # Network controller
    DEFAULT_REVISION_ID: str = "01"
    DEFAULT_SUBSYSTEM_ID: str = "0000"

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
    MAIN_ADVANCED_CONTROLLER: str = "systemverilog/advanced/advanced_controller.sv.j2"
    CLOCK_CROSSING: str = "systemverilog/advanced/clock_crossing.sv.j2"
    BUILD_INTEGRATION: str = "python/build_integration.py.j2"
    PCILEECH_INTEGRATION: str = "python/pcileech_build_integration.py.j2"
    PCILEECH_TLPS_BAR_CONTROLLER: str = (
        "systemverilog/pcileech_tlps128_bar_controller.sv.j2"
    )
    PCILEECH_FIFO: str = "systemverilog/pcileech_fifo.sv.j2"
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
        "undefined_var": (
            "{context}: Missing required template variables. Ensure {object} "
            "has all required attributes. Details: {error}"
        ),
        "template_not_found": (
            "{context}: Template file not found. Ensure the template exists at "
            "'{path}' or check template_dir. Details: {error}"
        ),
        "missing_device_config": (
            "Device configuration is required for safe firmware generation. "
            "Please provide a valid DeviceSpecificLogic object."
        ),
        "invalid_device_type": (
            "Invalid device_type: {value}. Must be a DeviceType enum. Please "
            "use values from DeviceType class."
        ),
        "invalid_device_class": (
            "Invalid device_class: {value}. Must be a DeviceClass enum. Please "
            "use values from DeviceClass class."
        ),
        "invalid_numeric_param": (
            "{param} = {value} is out of valid range [{min}, {max}]."
        ),
        "no_template_context": ("Template context is required for {operation}"),
        "context_not_dict": ("Template context must be a dictionary, got {type_name}"),
        "missing_critical_field": (
            "device_config is missing from template context. This is required "
            "for safe PCILeech firmware generation."
        ),
        "device_config_not_dict": (
            "device_config must be a dictionary, got {type_name}. Cannot "
            "proceed with firmware generation."
        ),
        "missing_device_signature": (
            "CRITICAL: device_signature is missing from template context. "
            "This field is required for firmware security and uniqueness."
        ),
        "empty_device_signature": (
            "CRITICAL: device_signature is None or empty. A valid device "
            "signature is required to prevent generic firmware generation."
        ),
        "validation_failed": (
            "Template context validation failed with {count} critical errors:\n"
            "{errors}\n\nCannot proceed with firmware generation."
        ),
        "missing_behavior_profile": (
            "Behavior profile is required for register extraction"
        ),
    }


# Create singleton instances
SV_CONSTANTS = SVConstants()
SV_TEMPLATES = SVTemplates()
SV_VALIDATION = SVValidation()
