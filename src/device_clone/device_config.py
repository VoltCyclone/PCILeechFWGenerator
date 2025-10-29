#!/usr/bin/env python3
"""
Device Configuration Management System

Centralized configuration for PCIe device parameters, replacing hardcoded values
throughout the codebase with a flexible, validated configuration system.
"""

import json

import logging

import os

import re

from dataclasses import dataclass, field

from enum import Enum

from pathlib import Path

from typing import Any, Dict, List, Optional, Union

from ..utils.validation_constants import (
    DEVICE_CAPABILITY_ERROR_MESSAGES,
    KNOWN_DEVICE_TYPES,
)

try:
    import yaml

    YAML_AVAILABLE = True
except ImportError:
    yaml = None
    YAML_AVAILABLE = False

from src.string_utils import (
    log_debug_safe,
    log_error_safe,
    log_info_safe,
    log_warning_safe,
    safe_format,
)

logger = logging.getLogger(__name__)


class DeviceType(Enum):
    """PCIe device types with their default configurations."""

    AUDIO = "audio"
    GRAPHICS = "graphics"
    MEDIA = "media"
    NETWORK = "network"
    PROCESSOR = "processor"
    STORAGE = "storage"
    USB = "usb"
    GENERIC = "generic"  # Keep generic last as it's the fallback

    @classmethod
    def validate_against_known_types(cls) -> None:
        """Validate that enum values match centralized constants."""
        enum_values = {member.value for member in cls}
        constant_values = set(KNOWN_DEVICE_TYPES)

        if enum_values != constant_values:
            missing_in_enum = constant_values - enum_values
            missing_in_constant = enum_values - constant_values
            error_msg = []
            if missing_in_enum:
                error_msg.append(f"Missing in DeviceType enum: {missing_in_enum}")
            if missing_in_constant:
                error_msg.append(
                    f"Missing in KNOWN_DEVICE_TYPES: {missing_in_constant}"
                )
            raise ValueError(
                f"DeviceType enum and KNOWN_DEVICE_TYPES "
                f"mismatch: {'; '.join(error_msg)}"
            )


# Validate at module load time to catch mismatches early
DeviceType.validate_against_known_types()


class DeviceClass(Enum):
    """PCIe device classes."""

    CONSUMER = "consumer"
    ENTERPRISE = "enterprise"
    EMBEDDED = "embedded"


@dataclass(slots=True)
class PCIeRegisters:
    """PCIe configuration space register values."""

    command: int = 0x0006  # Memory Space + Bus Master
    status: int = 0x0210  # Cap List + Fast B2B
    revision_id: int = 0x01
    cache_line_size: int = 0x10
    latency_timer: int = 0x00
    header_type: int = 0x00
    bist: int = 0x00

    def validate(self) -> None:
        """Validate register values against PCIe specification."""
        if not (0x0000 <= self.command <= 0xFFFF):
            raise ValueError(f"Invalid command register value: 0x{self.command:04X}")
        if not (0x0000 <= self.status <= 0xFFFF):
            raise ValueError(f"Invalid status register value: 0x{self.status:04X}")
        if not (0x00 <= self.revision_id <= 0xFF):
            raise ValueError(f"Invalid revision ID: 0x{self.revision_id:02X}")


@dataclass(slots=True)
class DeviceIdentification:
    """PCIe device identification parameters."""

    vendor_id: int
    device_id: int
    class_code: int  # Must be explicitly specified - no default for security
    subsystem_vendor_id: int = 0x0000
    subsystem_device_id: int = 0x0000

    def __post_init__(self):
        """Convert string values to integers if needed."""
        # Convert vendor_id
        if isinstance(self.vendor_id, str):
            self.vendor_id = self._convert_to_int(self.vendor_id)

        # Convert device_id
        if isinstance(self.device_id, str):
            self.device_id = self._convert_to_int(self.device_id)

        # Convert class_code
        if isinstance(self.class_code, str):
            self.class_code = self._convert_to_int(self.class_code)

        # Convert subsystem IDs
        if isinstance(self.subsystem_vendor_id, str):
            self.subsystem_vendor_id = self._convert_to_int(self.subsystem_vendor_id)

        if isinstance(self.subsystem_device_id, str):
            self.subsystem_device_id = self._convert_to_int(self.subsystem_device_id)

    @staticmethod
    def _convert_to_int(value) -> int:
        """Convert hex string or other value to int."""
        if isinstance(value, int):
            return value
        elif isinstance(value, str):
            if value.startswith(("0x", "0X")):
                return int(value, 16)
            else:
                return int(value, 0)  # Auto-detect base
        else:
            return int(value)

    def validate(self) -> None:
        """Validate device identification values."""
        if not (0x0001 <= self.vendor_id <= 0xFFFE):
            raise ValueError(f"Invalid vendor ID: 0x{self.vendor_id:04X}")
        if not (0x0001 <= self.device_id <= 0xFFFF):
            raise ValueError(f"Invalid device ID: 0x{self.device_id:04X}")
        if not (0x000000 <= self.class_code <= 0xFFFFFF):
            raise ValueError(f"Invalid class code: 0x{self.class_code:06X}")

    @property
    def vendor_id_hex(self) -> str:
        """Get vendor ID as hex string."""
        return f"0x{self.vendor_id:04X}"

    @property
    def device_id_hex(self) -> str:
        """Get device ID as hex string."""
        return f"0x{self.device_id:04X}"

    @property
    def class_code_hex(self) -> str:
        """Get class code as hex string."""
        return f"0x{self.class_code:06X}"


@dataclass(slots=True)
class ActiveDeviceConfig:
    """Active device interrupt configuration."""

    enabled: bool = True  # Enable active device by default
    timer_period: int = 100000  # Clock cycles between periodic interrupts
    timer_enable: bool = True  # Enable periodic timer
    interrupt_mode: str = "msi"  # "msi", "msix", or "intx"
    interrupt_vector: int = 0  # Which vector to use for active device interrupts
    priority: int = 15  # Interrupt priority (0-15, 15 = highest)

    # MSI-specific settings
    msi_vector_width: int = 5  # Number of MSI vectors (2^WIDTH)
    msi_64bit_addr: bool = False  # Use 64-bit MSI addresses

    # Advanced settings
    num_interrupt_sources: int = 8  # Number of interrupt sources to support
    default_source_priority: int = 8  # Default priority for interrupt sources

    def validate(self) -> None:
        """Validate active device configuration."""
        if self.timer_period <= 0:
            raise ValueError(f"Invalid timer period: {self.timer_period}")

        if self.interrupt_mode not in ["msi", "msix", "intx"]:
            raise ValueError(f"Invalid interrupt mode: {self.interrupt_mode}")

        if not (0 <= self.priority <= 15):
            raise ValueError(f"Invalid interrupt priority: {self.priority}")

        if not (0 <= self.msi_vector_width <= 5):
            raise ValueError(f"Invalid MSI vector width: {self.msi_vector_width}")

        if self.num_interrupt_sources <= 0:
            raise ValueError(
                f"Invalid number of interrupt sources: {self.num_interrupt_sources}"
            )


@dataclass(slots=True)
class DeviceCapabilities:
    """PCIe device capabilities configuration."""

    max_payload_size: int = 256
    msi_vectors: int = 1
    msix_vectors: int = 0
    supports_msi: bool = True
    supports_msix: bool = False
    supports_power_management: bool = True
    supports_advanced_error_reporting: bool = False
    link_width: int = 1  # x1, x4, x8, x16
    link_speed: str = "2.5GT/s"  # PCIe Gen1

    # Extended Configuration Space Pointer Control
    ext_cfg_cap_ptr: int = 0x100  # Extended capability pointer (256 by default)
    ext_cfg_xp_cap_ptr: int = 0x100  # Express capability pointer in extended space

    # Active Device Configuration
    active_device: ActiveDeviceConfig = field(default_factory=ActiveDeviceConfig)

    def validate(self) -> None:
        """Validate capability values."""
        # Import here to avoid circular dependency
        from src.device_clone.payload_size_config import (
            PayloadSizeConfig,
            PayloadSizeError,
        )

        # Validate payload size using the new payload size configuration
        try:
            payload_config = PayloadSizeConfig(self.max_payload_size)
        except PayloadSizeError as e:
            raise ValueError(str(e))

        if not (1 <= self.msi_vectors <= 32):
            raise ValueError(
                safe_format(
                    DEVICE_CAPABILITY_ERROR_MESSAGES["invalid_msi_vector_count"],
                    msi_vectors=self.msi_vectors,
                )
            )

        if not (0 <= self.msix_vectors <= 2048):
            raise ValueError(
                safe_format(
                    DEVICE_CAPABILITY_ERROR_MESSAGES["invalid_msix_vector_count"],
                    msix_vectors=self.msix_vectors,
                )
            )

        valid_link_widths = [1, 2, 4, 8, 16]
        if self.link_width not in valid_link_widths:
            raise ValueError(
                safe_format(
                    DEVICE_CAPABILITY_ERROR_MESSAGES["invalid_link_width"],
                    link_width=self.link_width,
                )
            )

        # Validate extended configuration space pointers
        if not (0x100 <= self.ext_cfg_cap_ptr <= 0xFFC):
            raise ValueError(
                safe_format(
                    DEVICE_CAPABILITY_ERROR_MESSAGES["invalid_ext_cfg_cap_ptr"],
                    ext_cfg_cap_ptr=self.ext_cfg_cap_ptr,
                )
            )

        if not (0x100 <= self.ext_cfg_xp_cap_ptr <= 0xFFC):
            raise ValueError(
                safe_format(
                    DEVICE_CAPABILITY_ERROR_MESSAGES["invalid_ext_cfg_xp_cap_ptr"],
                    ext_cfg_xp_cap_ptr=self.ext_cfg_xp_cap_ptr,
                )
            )

        # Ensure pointers are 4-byte aligned
        if self.ext_cfg_cap_ptr % 4 != 0:
            raise ValueError(
                safe_format(
                    DEVICE_CAPABILITY_ERROR_MESSAGES["ext_cfg_cap_ptr_alignment"],
                    ext_cfg_cap_ptr=self.ext_cfg_cap_ptr,
                )
            )
        if self.ext_cfg_xp_cap_ptr % 4 != 0:
            raise ValueError(
                safe_format(
                    DEVICE_CAPABILITY_ERROR_MESSAGES["ext_cfg_xp_cap_ptr_alignment"],
                    ext_cfg_xp_cap_ptr=self.ext_cfg_xp_cap_ptr,
                )
            )

        # Validate active device configuration
        self.active_device.validate()

    def get_cfg_force_mps(self) -> int:
        """
        Get the cfg_force_mps value for this device's maximum payload size.

        Returns:
            cfg_force_mps encoding value (0-5)
        """
        from src.device_clone.payload_size_config import PayloadSizeConfig

        payload_config = PayloadSizeConfig(self.max_payload_size)
        return payload_config.get_cfg_force_mps()

    def check_tiny_pcie_issues(self) -> tuple[bool, Optional[str]]:
        """
        Check if the payload size might cause tiny PCIe algorithm issues.

        Returns:
            Tuple of (has_issues, warning_message)
        """
        from src.device_clone.payload_size_config import PayloadSizeConfig

        payload_config = PayloadSizeConfig(self.max_payload_size)
        return payload_config.check_tiny_pcie_algo_issues()


@dataclass(slots=True)
class DeviceConfiguration:
    """Complete device configuration."""

    name: str
    device_type: DeviceType
    device_class: DeviceClass
    identification: DeviceIdentification
    registers: PCIeRegisters = field(default_factory=PCIeRegisters)
    capabilities: DeviceCapabilities = field(default_factory=DeviceCapabilities)
    custom_properties: Dict[str, Any] = field(default_factory=dict)

    def validate(self) -> None:
        """Validate entire device configuration."""
        self.identification.validate()
        self.registers.validate()
        self.capabilities.validate()

    def to_dict(self) -> Dict[str, Any]:
        """Convert configuration to dictionary."""
        return {
            "name": self.name,
            "device_type": self.device_type.value,
            "device_class": self.device_class.value,
            "identification": {
                "vendor_id": self.identification.vendor_id,
                "device_id": self.identification.device_id,
                "subsystem_vendor_id": self.identification.subsystem_vendor_id,
                "subsystem_device_id": self.identification.subsystem_device_id,
                "class_code": self.identification.class_code,
            },
            "registers": {
                "command": self.registers.command,
                "status": self.registers.status,
                "revision_id": self.registers.revision_id,
                "cache_line_size": self.registers.cache_line_size,
                "latency_timer": self.registers.latency_timer,
                "header_type": self.registers.header_type,
                "bist": self.registers.bist,
            },
            "capabilities": {
                "max_payload_size": self.capabilities.max_payload_size,
                "msi_vectors": self.capabilities.msi_vectors,
                "msix_vectors": self.capabilities.msix_vectors,
                "supports_msi": self.capabilities.supports_msi,
                "supports_msix": self.capabilities.supports_msix,
                "supports_power_management": (
                    self.capabilities.supports_power_management
                ),
                "supports_advanced_error_reporting": (
                    self.capabilities.supports_advanced_error_reporting
                ),
                "link_width": self.capabilities.link_width,
                "link_speed": self.capabilities.link_speed,
                "ext_cfg_cap_ptr": self.capabilities.ext_cfg_cap_ptr,
                "ext_cfg_xp_cap_ptr": self.capabilities.ext_cfg_xp_cap_ptr,
                "active_device": {
                    "enabled": self.capabilities.active_device.enabled,
                    "timer_period": self.capabilities.active_device.timer_period,
                    "timer_enable": self.capabilities.active_device.timer_enable,
                    "interrupt_mode": self.capabilities.active_device.interrupt_mode,
                    # NOTE: Avoid trailing commas creating 1-element tuples.
                    "interrupt_vector": (
                        self.capabilities.active_device.interrupt_vector
                    ),
                    "priority": self.capabilities.active_device.priority,
                    "msi_vector_width": (
                        self.capabilities.active_device.msi_vector_width
                    ),
                    "msi_64bit_addr": self.capabilities.active_device.msi_64bit_addr,
                    "num_interrupt_sources": (
                        self.capabilities.active_device.num_interrupt_sources
                    ),
                    "default_source_priority": (
                        self.capabilities.active_device.default_source_priority
                    ),
                },
            },
            "custom_properties": self.custom_properties,
        }


def validate_hex_id(value: Any, bit_width: int = 16) -> int:
    """
    Validate and convert hex ID string to integer.

    Accepts strings like '0x10ec', '10ec', or decimal strings like '4332'.
    Auto-detects base: hex if starts with 0x or contains A-F, decimal otherwise.
    """
    s = str(value).strip()
    if s.startswith(("0x", "0X")):
        s = s[2:]
        base = 16
    elif re.match(r"^-?\d+$", s):
        # Decimal digits with optional leading minus, treat as decimal
        base = 10
    elif re.match(r"^-?[0-9A-Fa-f]+$", s):
        # Contains hex characters with optional leading minus, treat as hex
        base = 16
    else:
        raise ValueError(f"Invalid format: {value}")

    int_value = int(s, base)

    # Check for negative values
    if int_value < 0:
        raise ValueError(f"Value {int_value} out of range for {bit_width}-bit field")

    max_value = (1 << bit_width) - 1
    if not (0 <= int_value <= max_value):
        raise ValueError(
            f"Value 0x{int_value:X} out of range for {bit_width}-bit field"
        )

    return int_value


class DeviceConfigManager:
    """Manages device configurations with file loading and validation."""

    DEFAULT_PROFILES = {}

    def __init__(self, config_dir: Optional[Path] = None):
        """Initialize configuration manager.

        If `config_dir` is provided it enables loading and saving profiles from
        the filesystem. If `config_dir` is None, no filesystem-backed profiles
        will be used.
        """
        # Do not assume any implicit on-disk directory. Keep as None unless
        # explicitly provided.
        self.config_dir = Path(config_dir) if config_dir is not None else None
        self.profiles: Dict[str, DeviceConfiguration] = {}
        self._load_default_profiles()

    def _load_default_profiles(self) -> None:
        """Load default device profiles."""
        self.profiles.update(self.DEFAULT_PROFILES)
        log_debug_safe(
            logger,
            "Loaded {count} default device profiles",
            count=len(self.DEFAULT_PROFILES),
        )

    # ------------------------------------------------------------------
    # Internal helpers
    # ------------------------------------------------------------------

    @staticmethod
    def _log_preconfigured_config_warning(config_file: Path, file_type: str) -> None:
        """Emit standardized warning for preconfigured device profiles.

        Avoids duplicated logging blocks for YAML/JSON cases while keeping
        strong messaging about hardcoded IDs.
        """
        # Ensure consistent uppercase file type label.
        ft_label = file_type.upper()
        log_warning_safe(logger, "=" * 80)
        log_warning_safe(
            logger,
            "⚠️  WARNING: USING PRECONFIGURED {ft_label} DEVICE CONFIGURATION",
            ft_label=ft_label,
        )
        log_warning_safe(
            logger,
            "   Loading device profile from: {config_file}",
            config_file=str(config_file),
        )
        log_warning_safe(
            logger,
            "   This uses hardcoded vendor/device IDs that may not be unique!",
        )
        log_warning_safe(
            logger,
            "   Consider using live device detection instead of {ft_label} configs.",
            ft_label=ft_label,
        )
        log_warning_safe(logger, "=" * 80)

    def load_config_file(self, file_path: Union[str, Path]) -> DeviceConfiguration:
        """Load device configuration from file."""
        file_path = Path(file_path)

        if not file_path.exists():
            raise FileNotFoundError(f"Configuration file not found: {file_path}")

        try:
            with open(file_path, "r") as f:
                if file_path.suffix.lower() in [".yaml", ".yml"]:
                    data = yaml.safe_load(f)  # type: ignore
                elif file_path.suffix.lower() == ".json":
                    data = json.load(f)
                else:
                    raise ValueError(f"Unsupported file format: {file_path.suffix}")

            config = self._dict_to_config(data)
            config.validate()

            log_info_safe(
                logger,
                "Loaded device configuration from {file_path}",
                file_path=str(file_path),
            )
            return config

        except Exception as e:
            log_error_safe(
                logger,
                "Failed to load configuration from {file_path}: {error}",
                file_path=str(file_path),
                error=e,
            )
            raise

    def _dict_to_config(self, data: Dict[str, Any]) -> DeviceConfiguration:
        """Convert raw dictionary into a validated DeviceConfiguration.

        Handles legacy artifacts (single-element lists for scalar fields)
        introduced by earlier serialization that wrapped scalars.
        """
        if "class_code" not in data["identification"]:
            raise ValueError(
                "class_code must be explicitly specified in device identification"
            )

        def convert_to_int(value: Any) -> int:
            if isinstance(value, int):
                return value
            if isinstance(value, str):
                s = value.strip()
                if s.startswith(("0x", "0X")):
                    return int(s, 16)
                if re.match(r"^\d+$", s):
                    return int(s, 10)
                if re.match(r"^[0-9A-Fa-f]+$", s):
                    return int(s, 16)
                try:
                    return int(s, 0)
                except ValueError:
                    return int(s, 16)
            return int(value)

        identification = DeviceIdentification(
            vendor_id=convert_to_int(data["identification"]["vendor_id"]),
            device_id=convert_to_int(data["identification"]["device_id"]),
            class_code=convert_to_int(data["identification"]["class_code"]),
            subsystem_vendor_id=convert_to_int(
                data["identification"].get("subsystem_vendor_id", 0x0000)
            ),
            subsystem_device_id=convert_to_int(
                data["identification"].get("subsystem_device_id", 0x0000)
            ),
        )

        registers = PCIeRegisters(
            command=convert_to_int(data["registers"].get("command", 0x0006)),
            status=convert_to_int(data["registers"].get("status", 0x0210)),
            revision_id=convert_to_int(data["registers"].get("revision_id", 0x01)),
            cache_line_size=convert_to_int(
                data["registers"].get("cache_line_size", 0x10)
            ),
            latency_timer=convert_to_int(data["registers"].get("latency_timer", 0x00)),
            header_type=convert_to_int(data["registers"].get("header_type", 0x00)),
            bist=convert_to_int(data["registers"].get("bist", 0x00)),
        )

        active_device_data = data["capabilities"].get("active_device", {})

        def _coerce_scalar(value: Any) -> Any:
            if (
                isinstance(value, (list, tuple))
                and len(value) == 1
                and isinstance(value[0], (int, str))
            ):
                return value[0]
            return value

        active_device = ActiveDeviceConfig(
            enabled=_coerce_scalar(active_device_data.get("enabled", False)),
            timer_period=convert_to_int(
                _coerce_scalar(active_device_data.get("timer_period", 100000))
            ),
            timer_enable=_coerce_scalar(active_device_data.get("timer_enable", True)),
            interrupt_mode=_coerce_scalar(
                active_device_data.get("interrupt_mode", "msi")
            ),
            interrupt_vector=convert_to_int(
                _coerce_scalar(active_device_data.get("interrupt_vector", 0))
            ),
            priority=convert_to_int(
                _coerce_scalar(active_device_data.get("priority", 15))
            ),
            msi_vector_width=convert_to_int(
                _coerce_scalar(active_device_data.get("msi_vector_width", 5))
            ),
            msi_64bit_addr=_coerce_scalar(
                active_device_data.get("msi_64bit_addr", False)
            ),
            num_interrupt_sources=convert_to_int(
                _coerce_scalar(active_device_data.get("num_interrupt_sources", 8))
            ),
            default_source_priority=convert_to_int(
                _coerce_scalar(active_device_data.get("default_source_priority", 8))
            ),
        )

        capabilities = DeviceCapabilities(
            max_payload_size=convert_to_int(
                data["capabilities"].get("max_payload_size", 256)
            ),
            msi_vectors=convert_to_int(data["capabilities"].get("msi_vectors", 1)),
            msix_vectors=convert_to_int(data["capabilities"].get("msix_vectors", 0)),
            supports_msi=data["capabilities"].get("supports_msi", True),
            supports_msix=data["capabilities"].get("supports_msix", False),
            supports_power_management=data["capabilities"].get(
                "supports_power_management", True
            ),
            supports_advanced_error_reporting=data["capabilities"].get(
                "supports_advanced_error_reporting", False
            ),
            link_width=convert_to_int(data["capabilities"].get("link_width", 1)),
            link_speed=data["capabilities"].get("link_speed", "2.5GT/s"),
            ext_cfg_cap_ptr=convert_to_int(
                data["capabilities"].get("ext_cfg_cap_ptr", 0x100)
            ),
            ext_cfg_xp_cap_ptr=convert_to_int(
                data["capabilities"].get("ext_cfg_xp_cap_ptr", 0x100)
            ),
            active_device=active_device,
        )

        return DeviceConfiguration(
            name=data["name"],
            device_type=DeviceType(data["device_type"]),
            device_class=DeviceClass(data["device_class"]),
            identification=identification,
            registers=registers,
            capabilities=capabilities,
            custom_properties=data.get("custom_properties", {}),
        )

    def get_profile(self, name: str) -> DeviceConfiguration:
        """Get device profile by name."""
        if name in self.profiles:
            return self.profiles[name]
        # If a config_dir was provided, attempt to load YAML or JSON files.
        if self.config_dir is not None:
            # Try YAML
            config_file = self.config_dir / f"{name}.yaml"
            if config_file.exists():
                self._log_preconfigured_config_warning(config_file, "yaml")
                config = self.load_config_file(config_file)
                self.profiles[name] = config
                return config

            # Try JSON
            config_file = self.config_dir / f"{name}.json"
            if config_file.exists():
                self._log_preconfigured_config_warning(config_file, "json")
                config = self.load_config_file(config_file)
                self.profiles[name] = config
                return config

        # No profile found either in-memory or on-disk
        raise ValueError(f"Device profile not found: {name}")

    def create_profile_from_env(self, name: str) -> DeviceConfiguration:
        """
        Create device profile from environment variables.

        SECURITY NOTE: All device identification values must be explicitly
        provided via environment variables. No default values are used to
        prevent insecure generic firmware.

        Required environment variables:
        - PCIE_{NAME}_VENDOR_ID: PCIe vendor ID (hex format)
        - PCIE_{NAME}_DEVICE_ID: PCIe device ID (hex format)
        - PCIE_{NAME}_CLASS_CODE: PCIe class code (hex format)
        """
        vendor_id_env = os.getenv(f"PCIE_{name.upper()}_VENDOR_ID")
        device_id_env = os.getenv(f"PCIE_{name.upper()}_DEVICE_ID")
        class_code_env = os.getenv(f"PCIE_{name.upper()}_CLASS_CODE")

        if not vendor_id_env:
            raise ValueError(
                f"PCIE_{name.upper()}_VENDOR_ID environment variable is required"
            )
        if not device_id_env:
            raise ValueError(
                f"PCIE_{name.upper()}_DEVICE_ID environment variable is required"
            )
        if not class_code_env:
            raise ValueError(
                f"PCIE_{name.upper()}_CLASS_CODE environment variable is required"
            )

        # Use robust validation/conversion for environment-provided hex IDs
        vendor_id = validate_hex_id(vendor_id_env, bit_width=16)
        device_id = validate_hex_id(device_id_env, bit_width=16)
        # class_code is 24-bit
        class_code = validate_hex_id(class_code_env, bit_width=24)

        identification = DeviceIdentification(
            vendor_id=vendor_id,
            device_id=device_id,
            class_code=class_code,
        )

        config = DeviceConfiguration(
            name=name,
            device_type=DeviceType.GENERIC,
            device_class=DeviceClass.CONSUMER,
            identification=identification,
        )

        config.validate()
        self.profiles[name] = config

        log_info_safe(
            logger,
            "Created device profile '{name}' from environment variables",
            name=name,
        )
        return config

    def list_profiles(self) -> List[str]:
        """List available device profiles."""
        profiles = list(self.profiles.keys())

        # Add profiles from config directory
        if self.config_dir and self.config_dir.exists():
            for file_path in self.config_dir.glob("*.yaml"):
                profile_name = file_path.stem
                if profile_name not in profiles:
                    profiles.append(profile_name)

            for file_path in self.config_dir.glob("*.json"):
                profile_name = file_path.stem
                if profile_name not in profiles:
                    profiles.append(profile_name)

        return sorted(profiles)

    def save_profile(
        self, config: DeviceConfiguration, file_path: Optional[Path] = None
    ) -> None:
        """Save device configuration to file."""
        if file_path is None:
            if self.config_dir is None:
                raise ValueError(
                    "No config_dir set: provide file_path or initialize "
                    "DeviceConfigManager with a config_dir"
                )
            self.config_dir.mkdir(parents=True, exist_ok=True)
            file_path = self.config_dir / f"{config.name}.yaml"

        try:
            if not YAML_AVAILABLE:
                raise ImportError(
                    "PyYAML is required for YAML file support. Install with: "
                    "pip install PyYAML"
                )

            def _sanitize(obj: Any):
                """Recursively sanitize object for safe YAML serialization.

                Converts tuples to lists and strips any non-primitive objects
                by using their string representation (last resort). This
                ensures yaml.safe_load can parse without python/tuple tags.
                """
                if isinstance(obj, dict):
                    return {k: _sanitize(v) for k, v in obj.items()}
                if isinstance(obj, (list, set, tuple)):
                    return [_sanitize(v) for v in list(obj)]
                if isinstance(obj, (str, int, float, bool)) or obj is None:
                    return obj
                # Fallback: represent unknown types as string
                try:
                    return str(obj)
                except Exception:
                    return f"<unserializable {type(obj).__name__}>"

            data = _sanitize(config.to_dict())

            # Use safe_dump so that safe_load works symmetrically
            assert yaml is not None  # for type checker
            with open(file_path, "w") as f:
                yaml.safe_dump(
                    data,
                    f,
                    default_flow_style=False,
                    indent=2,
                    sort_keys=True,
                )

            # Add the profile to the in-memory profiles dictionary
            self.profiles[config.name] = config

            log_info_safe(
                logger,
                "Saved device configuration to {file_path}",
                file_path=str(file_path),
            )

        except Exception as e:
            log_error_safe(
                logger,
                "Failed to save configuration to {file_path}: {error}",
                file_path=str(file_path),
                error=e,
            )
            raise


# Global configuration manager instance
_config_manager = None


def get_config_manager() -> DeviceConfigManager:
    """Get global configuration manager instance."""
    global _config_manager
    if _config_manager is None:
        _config_manager = DeviceConfigManager()
    return _config_manager


def get_device_config(profile_name: str) -> Optional[DeviceConfiguration]:
    """
    Get device configuration by profile name.

    SECURITY NOTE: No default profiles are provided to prevent insecure
    generic firmware. You must specify a profile name or use live device
    detection instead of hardcoded configurations.

    Args:
        profile_name: Name of the device profile to load

    Returns:
        DeviceConfiguration if found, None if not found (for graceful degradation)
    """
    manager = get_config_manager()
    try:
        return manager.get_profile(profile_name)
    except ValueError:
        # Profile not found - return None for graceful degradation
        log_warning_safe(
            logger,
            "Device profile '{profile_name}' not found, using live device detection",
            profile_name=profile_name,
        )
        return None


def generate_device_state_machine(registers: List[Dict[str, Any]]) -> Dict[str, Any]:
    """Generate device-level state machine."""
    try:
        if not registers:
            return {"states": ["IDLE"], "registers": []}

        # Mock device state machine
        device_state_machine = {
            "device_states": ["INIT", "READY", "ACTIVE", "ERROR"],
            "register_count": len(registers),
            "state_transitions": [
                {
                    "from": "INIT",
                    "to": "READY",
                    "trigger": "initialization_complete",
                },
                # Short descriptions below remain compact; line wrapped above.
                {"from": "READY", "to": "ACTIVE", "trigger": "operation_start"},
                {"from": "ACTIVE", "to": "READY", "trigger": "operation_complete"},
                {"from": "*", "to": "ERROR", "trigger": "error_condition"},
            ],
            "registers": [
                reg.get("name", f"REG_{i}") for i, reg in enumerate(registers)
            ],
        }

        return device_state_machine

    except Exception as e:
        log_error_safe(
            logger, "Error in generate_device_state_machine: {error}", error=e
        )
        return {}
