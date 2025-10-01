#!/usr/bin/env python3
"""
PCI Capability Rule Engine

This module provides a configurable rule system for categorizing PCI capabilities
based on emulation feasibility. It replaces the hardcoded categorization logic
with a flexible, data-driven approach that supports device-type specific rules
and capability versions.
"""

import json
import logging
from pathlib import Path
from typing import Any, Dict, List, Optional, Union

from ..string_utils import (log_debug_safe, log_error_safe, log_info_safe,
                            log_warning_safe, safe_format)
from .constants import PCI_DEVICE_ID_OFFSET, PCI_VENDOR_ID_OFFSET
from .core import ConfigSpace
from .types import (CapabilityInfo, CapabilityType, EmulationCategory,
                    PCICapabilityID, PCIExtCapabilityID)

logger = logging.getLogger(__name__)


class CapabilityRule:
    """
    Individual capability rule for determining emulation category.

    A rule defines conditions under which a capability should be assigned
    a specific emulation category. Rules can be based on capability ID,
    version, device type, or other criteria.
    """

    def __init__(
        self,
        cap_id: int,
        cap_type: CapabilityType,
        category: EmulationCategory,
        conditions: Optional[Dict[str, Any]] = None,
        description: Optional[str] = None,
    ) -> None:
        """
        Initialize a capability rule.

        Args:
            cap_id: Capability ID this rule applies to
            cap_type: Type of capability (standard or extended)
            category: Emulation category to assign if rule matches
            conditions: Optional conditions for rule matching
            description: Human-readable description of the rule
        """
        self.cap_id = cap_id
        self.cap_type = cap_type
        self.category = category
        self.conditions = conditions or {}
        self.description = description or safe_format(
            "Rule for {cap_type} capability 0x{cap_id:02x}",
            cap_type=cap_type.value,
            cap_id=cap_id,
        )

    def matches(
        self,
        cap_info: CapabilityInfo,
        config_space: Optional[ConfigSpace] = None,
        device_context: Optional[Dict[str, Any]] = None,
    ) -> bool:
        """
        Check if this rule matches the given capability.

        Args:
            cap_info: Capability information to check
            config_space: Configuration space for additional checks
            device_context: Device context information (VID/DID, etc.)

        Returns:
            True if the rule matches, False otherwise
        """
        # Basic capability ID and type check
        if cap_info.cap_id != self.cap_id or cap_info.cap_type != self.cap_type:
            return False

        # Check additional conditions
        for condition, expected_value in self.conditions.items():
            if not self._check_condition(
                condition, expected_value, cap_info, config_space, device_context
            ):
                return False

        return True

    def _check_condition(
        self,
        condition: str,
        expected_value: Any,
        cap_info: CapabilityInfo,
        config_space: Optional[ConfigSpace],
        device_context: Optional[Dict[str, Any]],
    ) -> bool:
        """Check a specific condition."""
        if condition == "version":
            return cap_info.version == expected_value

        if condition == "min_version":
            return cap_info.version >= expected_value

        if condition == "max_version":
            return cap_info.version <= expected_value

        elif condition == "vendor_id" and device_context:
            return device_context.get("vendor_id") == expected_value

        elif condition == "device_id" and device_context:
            return device_context.get("device_id") == expected_value

        elif condition == "vendor_ids" and device_context:
            return device_context.get("vendor_id") in expected_value

        elif condition == "device_ids" and device_context:
            return device_context.get("device_id") in expected_value

        else:
            log_warning_safe(
                logger,
                safe_format(
                    "Unknown condition '{condition}' in rule",
                    condition=condition,
                ),
                prefix="PCI_CAP",
            )
            return True  # Unknown conditions are ignored

    def __repr__(self) -> str:
        return safe_format(
            "CapabilityRule(cap_id=0x{cap_id:02x}, type={cap_type}, category={category})",
            cap_id=self.cap_id,
            cap_type=self.cap_type,
            category=self.category,
        )


class RuleEngine:
    """
    Rule engine for processing capability categorization rules.

    The RuleEngine manages a collection of CapabilityRule objects and
    provides methods to determine the appropriate emulation category
    for capabilities based on the configured rules.
    """

    def __init__(self) -> None:
        """Initialize the rule engine with default rules."""
        self.rules: List[CapabilityRule] = []
        self._load_default_rules()

    def add_rule(self, rule: CapabilityRule) -> None:
        """
        Add a rule to the engine.

        Args:
            rule: CapabilityRule to add
        """
        self.rules.append(rule)
        log_debug_safe(
            logger,
            safe_format(
                "Added rule: {rule}",
                rule=rule,
            ),
            prefix="PCI_CAP",
        )

    def remove_rules(self, cap_id: int, cap_type: CapabilityType) -> int:
        """
        Remove all rules for a specific capability.

        Args:
            cap_id: Capability ID
            cap_type: Capability type

        Returns:
            Number of rules removed
        """
        initial_count = len(self.rules)
        self.rules = [
            rule
            for rule in self.rules
            if not (rule.cap_id == cap_id and rule.cap_type == cap_type)
        ]
        removed_count = initial_count - len(self.rules)

        if removed_count > 0:
            log_debug_safe(
                logger,
                safe_format(
                    "Removed {removed_count} rules for {cap_type.value} capability 0x{cap_id:02x}",
                    removed_count=removed_count,
                    cap_id=cap_id,
                ),
            )

        return removed_count

    def categorize_capability(
        self,
        cap_info: CapabilityInfo,
        config_space: Optional[ConfigSpace] = None,
        device_context: Optional[Dict[str, Any]] = None,
    ) -> EmulationCategory:
        """
        Determine the emulation category for a capability.

        Args:
            cap_info: Capability information
            config_space: Configuration space for additional checks
            device_context: Device context information

        Returns:
            EmulationCategory for the capability
        """
        # Extract device context from config space if not provided
        if device_context is None and config_space is not None:
            device_context = self._extract_device_context(config_space)

        # Find matching rules (first match wins)
        for offset, rule in enumerate(self.rules):
            if rule.matches(cap_info, config_space, device_context):
                log_debug_safe(
                    logger,
                    safe_format(
                        "Rule matched for {cap_type} capability 0x{cap_id:02x} at offset 0x{offset:02x}: {category}",
                        cap_type=cap_info.cap_type,
                        cap_id=cap_info.cap_id,
                        offset=getattr(cap_info, "offset", 0),
                        category=rule.category,
                    ),
                )
                return rule.category

        # Default to UNSUPPORTED if no rules match
        log_debug_safe(
            logger,
            safe_format(
                "No rules matched for {cap_type} capability 0x{cap_id:02x} at offset 0x{offset:02x}, defaulting to UNSUPPORTED",
                cap_type=cap_info.cap_type,
                cap_id=cap_info.cap_id,
                offset=getattr(cap_info, "offset", 0),
            ),
        )
        return EmulationCategory.UNSUPPORTED

    def categorize_capabilities(
        self,
        capabilities: Dict[int, CapabilityInfo],
        config_space: Optional[ConfigSpace] = None,
        device_context: Optional[Dict[str, Any]] = None,
    ) -> Dict[int, EmulationCategory]:
        """
        Categorize multiple capabilities.

        Args:
            capabilities: Dictionary mapping offsets to CapabilityInfo
            config_space: Configuration space for additional checks
            device_context: Device context information

        Returns:
            Dictionary mapping offsets to EmulationCategory
        """
        categories = {}

        for offset, cap_info in capabilities.items():
            categories[offset] = self.categorize_capability(
                cap_info, config_space, device_context
            )

        return categories

    def load_rules_from_file(self, file_path: Union[str, Path]) -> None:
        """
        Load rules from a JSON configuration file.

        Args:
            file_path: Path to the configuration file

        Raises:
            FileNotFoundError: If the file doesn't exist
            ValueError: If the file format is invalid
        """
        file_path = Path(file_path)

        if not file_path.exists():
            raise FileNotFoundError(
                safe_format(
                    "Rule configuration file not found: {file_path}",
                    file_path=file_path,
                )
            )

        if file_path.suffix.lower() != ".json":
            raise ValueError(safe_format("Unsupported file format: {file_path.suffix}"))

        try:
            with open(file_path, "r", encoding="utf-8") as f:
                config = json.load(f)

            self._load_rules_from_config(config)
            log_info_safe(
                logger,
                "Loaded rules from {file_path}",
                prefix="PCI_CAP",
                file_path=file_path,
            )

        except json.JSONDecodeError as e:
            raise ValueError(
                safe_format(
                    "Invalid configuration file format: {e}",
                    e=e,
                )
            ) from e

    def save_rules_to_file(self, file_path: Union[str, Path]) -> None:
        """
        Save current rules to a JSON configuration file.

        Args:
            file_path: Path to save the configuration file
        """
        file_path = Path(file_path)
        config = self._rules_to_config()

        with open(file_path, "w", encoding="utf-8") as f:
            json.dump(config, f, indent=2)

        log_info_safe(
            logger,
            "Saved {count} rules to {file_path}",
            prefix="PCI_CAP",
            count=len(self.rules),
            file_path=file_path,
        )

    def _load_default_rules(self) -> None:
        """Load the default rule configuration that matches current behavior."""
        # Standard capability rules
        standard_rules = [
            # Fully supported capabilities
            CapabilityRule(
                PCICapabilityID.MSI.value,
                CapabilityType.STANDARD,
                EmulationCategory.FULLY_SUPPORTED,
                description="MSI capability - fully supported",
            ),
            CapabilityRule(
                PCICapabilityID.MSI_X.value,
                CapabilityType.STANDARD,
                EmulationCategory.FULLY_SUPPORTED,
                description="MSI-X capability - fully supported",
            ),
            # Partially supported capabilities
            CapabilityRule(
                PCICapabilityID.POWER_MANAGEMENT.value,
                CapabilityType.STANDARD,
                EmulationCategory.PARTIALLY_SUPPORTED,
                description="Power Management capability - partially supported",
            ),
            CapabilityRule(
                PCICapabilityID.PCI_EXPRESS.value,
                CapabilityType.STANDARD,
                EmulationCategory.PARTIALLY_SUPPORTED,
                description="PCI Express capability - partially supported",
            ),
            # Unsupported capabilities
            CapabilityRule(
                PCICapabilityID.AGP.value,
                CapabilityType.STANDARD,
                EmulationCategory.UNSUPPORTED,
                description="AGP capability - unsupported",
            ),
            CapabilityRule(
                PCICapabilityID.VPD.value,
                CapabilityType.STANDARD,
                EmulationCategory.UNSUPPORTED,
                description="VPD capability - unsupported",
            ),
            CapabilityRule(
                PCICapabilityID.SLOT_ID.value,
                CapabilityType.STANDARD,
                EmulationCategory.UNSUPPORTED,
                description="Slot ID capability - unsupported",
            ),
            CapabilityRule(
                PCICapabilityID.PCI_X.value,
                CapabilityType.STANDARD,
                EmulationCategory.UNSUPPORTED,
                description="PCI-X capability - unsupported",
            ),
            CapabilityRule(
                PCICapabilityID.AF.value,
                CapabilityType.STANDARD,
                EmulationCategory.UNSUPPORTED,
                description="Advanced Features capability - unsupported",
            ),
            CapabilityRule(
                PCICapabilityID.VENDOR_SPECIFIC.value,
                CapabilityType.STANDARD,
                EmulationCategory.UNSUPPORTED,
                description="Vendor Specific capability - unsupported",
            ),
        ]

        # Extended capability rules
        extended_rules = [
            # Partially supported capabilities
            CapabilityRule(
                PCIExtCapabilityID.ADVANCED_ERROR_REPORTING.value,
                CapabilityType.EXTENDED,
                EmulationCategory.PARTIALLY_SUPPORTED,
                description="Advanced Error Reporting - partially supported",
            ),
            CapabilityRule(
                PCIExtCapabilityID.ACCESS_CONTROL_SERVICES.value,
                CapabilityType.EXTENDED,
                EmulationCategory.PARTIALLY_SUPPORTED,
                description="Access Control Services - partially supported",
            ),
            CapabilityRule(
                PCIExtCapabilityID.DOWNSTREAM_PORT_CONTAINMENT.value,
                CapabilityType.EXTENDED,
                EmulationCategory.PARTIALLY_SUPPORTED,
                description="Downstream Port Containment - partially supported",
            ),
            CapabilityRule(
                PCIExtCapabilityID.RESIZABLE_BAR.value,
                CapabilityType.EXTENDED,
                EmulationCategory.PARTIALLY_SUPPORTED,
                description="Resizable BAR - partially supported",
            ),
            # Additional commonly-present capabilities we handle
            CapabilityRule(
                PCIExtCapabilityID.LATENCY_TOLERANCE_REPORTING.value,
                CapabilityType.EXTENDED,
                EmulationCategory.PARTIALLY_SUPPORTED,
                description="Latency Tolerance Reporting - partially supported",
            ),
            CapabilityRule(
                PCIExtCapabilityID.L1_PM_SUBSTATES.value,
                CapabilityType.EXTENDED,
                EmulationCategory.PARTIALLY_SUPPORTED,
                description="L1 PM Substates - partially supported",
            ),
            CapabilityRule(
                PCIExtCapabilityID.PRECISION_TIME_MEASUREMENT.value,
                CapabilityType.EXTENDED,
                EmulationCategory.PARTIALLY_SUPPORTED,
                description="Precision Time Measurement - partially supported",
            ),
            CapabilityRule(
                PCIExtCapabilityID.SINGLE_ROOT_IO_VIRTUALIZATION.value,
                CapabilityType.EXTENDED,
                EmulationCategory.PARTIALLY_SUPPORTED,
                description="SR-IOV - partially supported",
            ),
            CapabilityRule(
                PCIExtCapabilityID.ALTERNATIVE_ROUTING_ID_INTERPRETATION.value,
                CapabilityType.EXTENDED,
                EmulationCategory.PARTIALLY_SUPPORTED,
                description="ARI - partially supported",
            ),
            # Mirror vendor-specific extended as-is by default
            CapabilityRule(
                PCIExtCapabilityID.VENDOR_SPECIFIC_EXTENDED.value,
                CapabilityType.EXTENDED,
                EmulationCategory.FULLY_SUPPORTED,
                description="Vendor-Specific Extended (VSEC) - preserve as-is",
            ),
            CapabilityRule(
                PCIExtCapabilityID.DESIGNATED_VENDOR_SPECIFIC.value,
                CapabilityType.EXTENDED,
                EmulationCategory.FULLY_SUPPORTED,
                description="Designated Vendor-Specific (DVSEC) - preserve as-is",
            ),
        ]

        # Add all rules
        for rule in standard_rules + extended_rules:
            self.add_rule(rule)

        log_info_safe(
            logger,
            "Loaded {count} default rules",
            prefix="PCI_CAP",
            count=len(self.rules),
        )

    def _extract_device_context(self, config_space: ConfigSpace) -> Dict[str, Any]:
        """
        Extract device context from configuration space.

        This method analyzes the configuration space to extract device information
        and features that can be used for capability categorization and processing.
        """
        context = {}

        try:
            # Extract basic device identification
            if config_space.has_data(PCI_VENDOR_ID_OFFSET, 2):
                context["vendor_id"] = config_space.read_word(PCI_VENDOR_ID_OFFSET)

            if config_space.has_data(PCI_DEVICE_ID_OFFSET, 2):
                context["device_id"] = config_space.read_word(PCI_DEVICE_ID_OFFSET)

            # Extract device class information
            if config_space.has_data(0x0B, 1):  # Class code (base)
                context["class_code"] = config_space.read_byte(0x0B)

            if config_space.has_data(0x0A, 1):  # Class code (sub)
                context["subclass_code"] = config_space.read_byte(0x0A)

            if config_space.has_data(0x09, 1):  # Class code (prog IF)
                context["prog_if"] = config_space.read_byte(0x09)

            # Extract revision ID
            if config_space.has_data(0x08, 1):
                context["revision_id"] = config_space.read_byte(0x08)

            # Extract header type (multi-function, etc.)
            if config_space.has_data(0x0E, 1):
                header_type = config_space.read_byte(0x0E)
                context["header_type"] = header_type & 0x7F
                context["multi_function"] = bool(header_type & 0x80)

            # Extract command register settings
            if config_space.has_data(0x04, 2):
                command = config_space.read_word(0x04)
                context["io_space_enabled"] = bool(command & 0x0001)
                context["memory_space_enabled"] = bool(command & 0x0002)
                context["bus_master_enabled"] = bool(command & 0x0004)
                context["parity_error_response"] = bool(command & 0x0040)
                context["serr_enabled"] = bool(command & 0x0100)
                context["interrupt_disable"] = bool(command & 0x0400)

            # Extract status register information
            if config_space.has_data(0x06, 2):
                status = config_space.read_word(0x06)
                context["capabilities_supported"] = bool(status & 0x0010)
                context["pci_x_capable"] = bool(status & 0x0080)
                context["master_data_parity_error"] = bool(status & 0x0100)
                context["signaled_target_abort"] = bool(status & 0x0800)
                context["received_target_abort"] = bool(status & 0x1000)
                context["received_master_abort"] = bool(status & 0x2000)
                context["signaled_system_error"] = bool(status & 0x4000)
                context["detected_parity_error"] = bool(status & 0x8000)

            # Default feature settings (will be updated by capability processor)
            context["enable_msi"] = True
            context["enable_msix"] = True
            context["enable_pcie"] = True
            context["enable_power_management"] = True
            context["enable_relaxed_ordering"] = True
            context["enable_no_snoop"] = True
            context["enable_extended_tag"] = True
            context["enable_d1_power_state"] = False
            context["enable_d2_power_state"] = False
            context["enable_d3hot_power_state"] = True
            context["enable_pme"] = True
            context["aspm_control"] = 0  # 0=disabled, 1=L0s, 2=L1, 3=both
            context["msi_address"] = 0xFEE00000  # Default MSI address
            context["msi_vector"] = 0x0020  # Default MSI vector

        except (IndexError, ValueError) as e:
            log_warning_safe(
                logger,
                "Failed to extract device context: {e}",
                prefix="PCI_CAP",
                e=e,
            )

        return context

    def _load_rules_from_config(self, config: Dict[str, Any]) -> None:
        """Load rules from configuration dictionary."""
        rules_config = config.get("rules", [])

        for rule_config in rules_config:
            try:
                rule = self._create_rule_from_config(rule_config)
                self.add_rule(rule)
            except (KeyError, ValueError) as e:
                log_error_safe(
                    logger,
                    "Failed to load rule from config: {e}",
                    prefix="PCI_CAP",
                    e=e,
                )

    def _create_rule_from_config(self, rule_config: Dict[str, Any]) -> CapabilityRule:
        """Create a CapabilityRule from configuration dictionary."""
        cap_id = rule_config["cap_id"]
        cap_type = CapabilityType(rule_config["cap_type"])
        category = EmulationCategory[rule_config["category"]]
        conditions = rule_config.get("conditions", {})
        description = rule_config.get("description")

        return CapabilityRule(cap_id, cap_type, category, conditions, description)

    def _rules_to_config(self) -> Dict[str, Any]:
        """Convert current rules to configuration dictionary."""
        rules_config = []

        for rule in self.rules:
            rule_config = {
                "cap_id": rule.cap_id,
                "cap_type": rule.cap_type.value,
                "category": rule.category.name,
            }

            if rule.conditions:
                rule_config["conditions"] = rule.conditions

            if rule.description:
                rule_config["description"] = rule.description

            rules_config.append(rule_config)

        return {"rules": rules_config}
