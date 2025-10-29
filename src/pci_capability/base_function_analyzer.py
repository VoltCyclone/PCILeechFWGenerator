#!/usr/bin/env python3
"""
Base Function Analyzer

This module provides a base class for device function analyzers to eliminate
code duplication across network, storage, media, and USB function analyzers.
"""

import logging

from abc import ABC, abstractmethod

from typing import Any, Dict, List, Optional, Set, Tuple

from ..string_utils import (log_debug_safe, log_error_safe, log_info_safe,
                            log_warning_safe, safe_format)
from ..error_utils import extract_root_cause, format_user_friendly_error

logger = logging.getLogger(__name__)


class MSIXConfigurationError(ValueError):
    """
    Exception raised for MSI-X configuration validation failures.

    This exception is raised when MSI-X table/PBA configuration conflicts
    with BAR layout, has invalid alignment, or violates PCIe specification
    requirements.
    """

    def __init__(
        self,
        message: str,
        *,
        vendor_id: Optional[int] = None,
        device_id: Optional[int] = None,
        validation_errors: Optional[List[str]] = None,
    ):
        """
        Initialize MSI-X configuration error.

        Args:
            message: Human-readable error message
            vendor_id: Optional PCI vendor ID
            device_id: Optional PCI device ID
            validation_errors: Optional list of specific validation errors
        """
        self.vendor_id = vendor_id
        self.device_id = device_id
        self.validation_errors = validation_errors or []

        # Build detailed message
        parts = [message]
        if vendor_id is not None and device_id is not None:
            parts.append(
                f"Device: {vendor_id:04x}:{device_id:04x}"
            )
        if self.validation_errors:
            parts.append(
                f"Validation errors: {'; '.join(self.validation_errors)}"
            )

        super().__init__(" | ".join(parts))


class BaseFunctionAnalyzer(ABC):
    """
    Base class for device function analyzers.

    Provides common functionality for analyzing vendor/device IDs and generating
    capabilities, reducing code duplication across device-specific analyzers.
    """

    # Common capability IDs
    PM_CAP_ID = 0x01  # Power Management
    MSI_CAP_ID = 0x05  # MSI
    PCIE_CAP_ID = 0x10  # PCIe Express
    MSIX_CAP_ID = 0x11  # MSI-X

    def __init__(self, vendor_id: int, device_id: int, analyzer_type: str):
        """
        Initialize analyzer with build-time provided vendor/device IDs.

        Args:
            vendor_id: PCI vendor ID from build process
            device_id: PCI device ID from build process
            analyzer_type: Type of analyzer (e.g., "network", "storage", etc.)
        """
        self.vendor_id = vendor_id
        self.device_id = device_id
        self.analyzer_type = analyzer_type
        self._device_category = self._analyze_device_category()
        self._capabilities = self._analyze_capabilities()

        log_debug_safe(
            logger,
            safe_format(
                "Initialized {analyzer_type} analyzer for device {vendor_id:04x}:{device_id:04x}, category: {category}",
                analyzer_type=analyzer_type,
                vendor_id=vendor_id,
                device_id=device_id,
                category=self._device_category,
            ),
            prefix="PCI_CAP",
        )

    @abstractmethod
    def _analyze_device_category(self) -> str:
        """
        Analyze device category based on vendor/device ID patterns.

        Returns:
            Device category string specific to device type
        """

    @abstractmethod
    def _analyze_capabilities(self) -> Set[int]:
        """
        Analyze which capabilities this device should support.

        Returns:
            Set of capability IDs that should be present
        """

    @abstractmethod
    def get_device_class_code(self) -> int:
        """Get appropriate PCI class code for this device."""

    @abstractmethod
    def generate_device_features(self) -> Dict[str, Any]:
        """Generate device-specific features."""

    def _create_pm_capability(self, aux_current: int = 0) -> Dict[str, Any]:
        """
        Create Power Management capability.

        Args:
            aux_current: Auxiliary current requirement in mA

        Returns:
            Power Management capability dictionary
        """
        return {
            "cap_id": self.PM_CAP_ID,
            "version": 3,
            "d3_support": True,
            "aux_current": aux_current,
        }

    def _create_msi_capability(
        self,
        multi_message_capable: Optional[int] = None,
        supports_per_vector_masking: Optional[bool] = None,
    ) -> Dict[str, Any]:
        """
        Create MSI capability.

        Args:
            multi_message_capable: Number of messages supported (default: based on device)
            supports_per_vector_masking: Whether per-vector masking is supported

        Returns:
            MSI capability dictionary
        """
        if multi_message_capable is None:
            # Default based on device complexity
            multi_message_capable = min(5, (self.device_id >> 8).bit_length())

        if supports_per_vector_masking is None:
            supports_per_vector_masking = self.device_id > 0x1000

        return {
            "cap_id": self.MSI_CAP_ID,
            "multi_message_capable": multi_message_capable,
            "supports_64bit": True,
            "supports_per_vector_masking": supports_per_vector_masking,
        }

    def _create_pcie_capability(
        self,
        max_payload_size: Optional[int] = None,
        supports_flr: bool = True,
    ) -> Dict[str, Any]:
        """
        Create PCIe Express capability.

        Args:
            max_payload_size: Maximum payload size in bytes
            supports_flr: Whether Function Level Reset is supported

        Returns:
            PCIe capability dictionary
        """
        if max_payload_size is None:
            # Default based on device capability
            max_payload_size = 512 if self.device_id > 0x1500 else 256

        return {
            "cap_id": self.PCIE_CAP_ID,
            "version": 2,
            "device_type": 0,  # Endpoint
            "max_payload_size": max_payload_size,
            "supports_flr": supports_flr,
        }

    def _get_default_msix_bar_allocation(self) -> Tuple[int, int]:
        """
        Get default BAR allocation for MSI-X tables.

        Returns:
            Tuple of (table_bar, pba_bar)
        """
        # Add device-specific variation for security
        if (self.device_id & 0x0F) >= 8:
            return (0, 0)  # Some devices use BAR 0
        return (1, 1)  # Most use BAR 1

    def _create_msix_capability(
        self,
        table_size: Optional[int] = None,
        table_bar: Optional[int] = None,
        pba_bar: Optional[int] = None,
    ) -> Dict[str, Any]:
        """
        Create MSI-X capability.

        Args:
            table_size: Number of MSI-X table entries (default: based on device)
            table_bar: BAR for MSI-X table (default: device-specific)
            pba_bar: BAR for Pending Bit Array (default: same as table_bar)

        Returns:
            MSI-X capability dictionary
        """
        if table_size is None:
            table_size = self._calculate_default_queue_count()

        if table_bar is None or pba_bar is None:
            default_table_bar, default_pba_bar = self._get_default_msix_bar_allocation()
            table_bar = table_bar or default_table_bar
            pba_bar = pba_bar or default_pba_bar

        # Add entropy to offsets for security
        base_offset = (self.device_id & 0x1F) * 0x100
        table_offset = base_offset
        pba_offset = base_offset + 0x1000 + ((self.vendor_id & 0x7) * 0x200)

        return {
            "cap_id": self.MSIX_CAP_ID,
            "table_size": table_size - 1,  # MSI-X encodes as N-1
            "function_mask": True,
            "table_bar": table_bar,
            "table_offset": table_offset,
            "pba_bar": pba_bar,
            "pba_offset": pba_offset,
        }

    def _calculate_default_queue_count(self) -> int:
        """
        Calculate default queue count based on device characteristics.

        Returns:
            Number of queues/interrupt vectors
        """
        base_queues = 4

        # Scale based on device ID (higher = more capable)
        if self.device_id > 0x2000:
            base_queues = 32
        elif self.device_id > 0x1500:
            base_queues = 16
        elif self.device_id > 0x1000:
            base_queues = 8

        # Add entropy-based variation for security (±25% based on ID bits)
        entropy_factor = ((self.vendor_id ^ self.device_id) & 0xF) / 32.0
        variation = int(base_queues * entropy_factor * 0.5)
        if (self.device_id & 0x1) == 0:
            variation = -variation

        final_queues = max(1, base_queues + variation)
        # Ensure power of 2 for realistic hardware
        return 1 << (final_queues - 1).bit_length()

    def generate_capability_list(self) -> List[Dict[str, Any]]:
        """
        Generate list of capabilities for this device.

        Returns:
            List of capability dictionaries with appropriate parameters
        """
        capabilities = []

        for cap_id in sorted(self._capabilities):
            capability = self._create_capability_by_id(cap_id)
            if capability:
                capabilities.append(capability)

        log_info_safe(
            logger,
            safe_format(
                "Generated {count} capabilities for {analyzer_type} device {vendor_id:04x}:{device_id:04x}",
                count=len(capabilities),
                analyzer_type=self.analyzer_type,
                vendor_id=self.vendor_id,
                device_id=self.device_id,
            ),
            prefix="PCI_CAP",
        )

        return capabilities

    def _create_capability_by_id(self, cap_id: int) -> Optional[Dict[str, Any]]:
        """
        Create capability by ID. Can be overridden by subclasses for device-specific capabilities.

        Args:
            cap_id: PCI capability ID

        Returns:
            Capability dictionary or None if not supported
        """
        if cap_id == self.PM_CAP_ID:
            return self._create_pm_capability()
        if cap_id == self.MSI_CAP_ID:
            return self._create_msi_capability()
        if cap_id == self.PCIE_CAP_ID:
            return self._create_pcie_capability()
        if cap_id == self.MSIX_CAP_ID:
            return self._create_msix_capability()
        # Let subclasses handle device-specific capabilities
        return None

    def validate_msix_bar_configuration(
        self, bars: List[Dict[str, Any]], capabilities: List[Dict[str, Any]]
    ) -> Tuple[bool, List[str]]:
        """
        Validate MSI-X and BAR configuration to prevent collisions and driver errors.

        Args:
            bars: List of BAR configuration dictionaries
            capabilities: List of capability dictionaries

        Returns:
            Tuple of (is_valid, error_messages)
        """
        errors = []
        warnings = []

        # Find MSI-X capability
        msix_cap = None
        for cap in capabilities:
            if cap.get("cap_id") == self.MSIX_CAP_ID:
                msix_cap = cap
                break

        if not msix_cap:
            # No MSI-X capability, no validation needed
            return True, []

        # Extract MSI-X configuration
        table_bar = msix_cap.get("table_bar", 0)
        pba_bar = msix_cap.get("pba_bar", 0)
        table_offset = msix_cap.get("table_offset", 0)
        pba_offset = msix_cap.get("pba_offset", 0)
        table_size = msix_cap.get("table_size", 0) + 1  # MSI-X encodes as N-1

        # Validate BIR values (must be 0-5 for standard BARs)
        if table_bar > 5:
            errors.append(f"MSI-X table BIR {table_bar} is invalid (must be 0-5)")
        if pba_bar > 5:
            errors.append(f"MSI-X PBA BIR {pba_bar} is invalid (must be 0-5)")

        # Check alignment requirements (PCIe spec requires 8-byte alignment)
        if table_offset % 8 != 0:
            errors.append(
                f"MSI-X table offset 0x{table_offset:x} is not 8-byte aligned"
            )
        if pba_offset % 8 != 0:
            errors.append(f"MSI-X PBA offset 0x{pba_offset:x} is not 8-byte aligned")

        # Validate table size
        if not 1 <= table_size <= 2048:
            errors.append(f"MSI-X table size {table_size} is invalid (must be 1-2048)")

        # Calculate MSI-X structure sizes
        table_size_bytes = table_size * 16  # 16 bytes per MSI-X table entry
        pba_size_bytes = ((table_size + 31) // 32) * 4  # PBA size in bytes

        # Find relevant BARs
        table_bar_config = None
        pba_bar_config = None
        for bar_config in bars:
            if bar_config.get("bar") == table_bar:
                table_bar_config = bar_config
            if bar_config.get("bar") == pba_bar:
                pba_bar_config = bar_config

        # Validate that BARs exist
        if table_bar_config is None:
            errors.append(f"MSI-X table BAR {table_bar} is not configured")
        if pba_bar_config is None:
            errors.append(f"MSI-X PBA BAR {pba_bar} is not configured")

        if table_bar_config is None or pba_bar_config is None:
            return False, errors

        # Validate table fits in BAR
        table_bar_size = table_bar_config.get("size", 0)
        table_end = table_offset + table_size_bytes
        if table_end > table_bar_size:
            errors.append(
                f"MSI-X table (offset 0x{table_offset:x}, size {table_size_bytes}) "
                f"exceeds BAR {table_bar} size (0x{table_bar_size:x})"
            )

        # Validate PBA fits in BAR
        pba_bar_size = pba_bar_config.get("size", 0)
        pba_end = pba_offset + pba_size_bytes
        if pba_end > pba_bar_size:
            errors.append(
                f"MSI-X PBA (offset 0x{pba_offset:x}, size {pba_size_bytes}) "
                f"exceeds BAR {pba_bar} size (0x{pba_bar_size:x})"
            )

        # Check for overlap if table and PBA are in the same BAR
        if table_bar == pba_bar and table_bar_config and pba_bar_config:
            if table_offset < pba_end and table_end > pba_offset:
                errors.append(
                    f"MSI-X table (0x{table_offset:x}-0x{table_end:x}) "
                    f"and PBA (0x{pba_offset:x}-0x{pba_end:x}) overlap in BAR {table_bar}"
                )

        # Check for conflicts with other memory regions
        self._validate_msix_memory_conflicts(
            table_bar, table_offset, table_end, pba_bar, pba_offset, pba_end, errors
        )

        # Performance and driver compatibility warnings
        if table_size > 256:
            warnings.append(
                f"Large MSI-X table size ({table_size}) may impact performance"
            )

        if table_bar != pba_bar:
            warnings.append(
                "MSI-X table and PBA in different BARs may complicate driver implementation"
            )

        # Log warnings for informational purposes
        for warning in warnings:
            log_warning_safe(
                logger,
                safe_format(
                    "MSI-X validation warning for {vendor_id:04x}:{device_id:04x}: {warning}",
                    vendor_id=self.vendor_id,
                    device_id=self.device_id,
                    warning=warning,
                ),
                prefix="PCI_CAP",
            )

        is_valid = len(errors) == 0
        
        if not is_valid:
            # Log detailed validation failures
            log_debug_safe(
                logger,
                safe_format(
                    "MSI-X validation failed for {vendor_id:04x}:{device_id:04x}: {count} errors found",
                    vendor_id=self.vendor_id,
                    device_id=self.device_id,
                    count=len(errors),
                ),
                prefix="PCI_CAP",
            )
        
        return is_valid, errors

    def _validate_msix_memory_conflicts(
        self,
        table_bar: int,
        table_offset: int,
        table_end: int,
        pba_bar: int,
        pba_offset: int,
        pba_end: int,
        errors: List[str],
    ) -> None:
        """
        Validate MSI-X structures don't conflict with common memory regions.

        Args:
            table_bar: MSI-X table BAR index
            table_offset: MSI-X table offset
            table_end: MSI-X table end address
            pba_bar: MSI-X PBA BAR index
            pba_offset: MSI-X PBA offset
            pba_end: MSI-X PBA end address
            errors: List to append errors to
        """
        # Common PCIe memory regions that should not overlap with MSI-X
        reserved_regions = [
            # Configuration space shadow (typically high addresses)
            {"start": 0xF000, "end": 0x10000, "name": "Configuration Space Shadow"},
            # PCILeech memory regions (device-specific)
            {"start": 0x0000, "end": 0x1000, "name": "Device Control Registers"},
            # Custom PIO regions (typically at specific offsets)
            {"start": 0x4000, "end": 0x8000, "name": "Custom PIO Region"},
        ]

        # Check table conflicts
        for region in reserved_regions:
            if table_bar == 0:  # Assume BAR 0 for control regions
                if table_offset < region["end"] and table_end > region["start"]:
                    errors.append(
                        f"MSI-X table conflicts with {region['name']} "
                        f"(0x{region['start']:x}-0x{region['end']:x})"
                    )

            if pba_bar == 0:  # Assume BAR 0 for control regions
                if pba_offset < region["end"] and pba_end > region["start"]:
                    errors.append(
                        f"MSI-X PBA conflicts with {region['name']} "
                        f"(0x{region['start']:x}-0x{region['end']:x})"
                    )

        # Check for alignment with typical memory controller boundaries
        # Modern devices often use 4KB page alignment
        if table_offset % 0x1000 != 0:
            errors.append(
                f"MSI-X table offset 0x{table_offset:x} is not 4KB aligned "
                "(recommended for optimal DMA performance)"
            )
        if pba_offset % 0x1000 != 0:
            errors.append(
                f"MSI-X PBA offset 0x{pba_offset:x} is not 4KB aligned "
                "(recommended for optimal DMA performance)"
            )

    def _auto_fix_msix_conflicts(
        self, bars: List[Dict[str, Any]], capabilities: List[Dict[str, Any]]
    ) -> Tuple[List[Dict[str, Any]], List[Dict[str, Any]]]:
        """
        Attempt to automatically fix MSI-X and BAR configuration conflicts.

        Args:
            bars: List of BAR configuration dictionaries
            capabilities: List of capability dictionaries

        Returns:
            Tuple of (fixed_bars, fixed_capabilities)
        """
        fixed_bars = bars.copy()
        fixed_capabilities = capabilities.copy()

        # Find MSI-X capability
        msix_cap_index = None
        for i, cap in enumerate(fixed_capabilities):
            if cap.get("cap_id") == self.MSIX_CAP_ID:
                msix_cap_index = i
                break

        if msix_cap_index is None:
            return fixed_bars, fixed_capabilities

        msix_cap = fixed_capabilities[msix_cap_index].copy()
        table_bar = msix_cap.get("table_bar", 0)
        pba_bar = msix_cap.get("pba_bar", 0)
        table_size = msix_cap.get("table_size", 0) + 1  # MSI-X encodes as N-1

        # Calculate required sizes
        table_size_bytes = table_size * 16
        pba_size_bytes = ((table_size + 31) // 32) * 4

        # Fix 1: Ensure proper alignment
        if msix_cap.get("table_offset", 0) % 0x1000 != 0:
            # Align to 4KB boundary
            new_table_offset = (
                (msix_cap.get("table_offset", 0) + 0xFFF) // 0x1000
            ) * 0x1000
            msix_cap["table_offset"] = new_table_offset
            log_debug_safe(
                logger,
                safe_format(
                    "Auto-fixed MSI-X table offset alignment: 0x{offset:x}",
                    offset=new_table_offset,
                ),
                prefix="PCI_CAP",
            )

        if msix_cap.get("pba_offset", 0) % 0x1000 != 0:
            # Align to 4KB boundary
            new_pba_offset = (
                (msix_cap.get("pba_offset", 0) + 0xFFF) // 0x1000
            ) * 0x1000
            msix_cap["pba_offset"] = new_pba_offset
            log_debug_safe(
                logger,
                safe_format(
                    "Auto-fixed MSI-X PBA offset alignment: 0x{offset:x}",
                    offset=new_pba_offset,
                ),
                prefix="PCI_CAP",
            )

        # Fix 2: Ensure adequate BAR sizes
        for bar_config in fixed_bars:
            bar_index = bar_config.get("bar", -1)
            current_size = bar_config.get("size", 0)

            if bar_index == table_bar:
                required_size = msix_cap.get("table_offset", 0) + table_size_bytes
                # Add some padding for safety
                required_size = (
                    (required_size + 0xFFF) // 0x1000
                ) * 0x1000  # Round up to 4KB
                if current_size < required_size:
                    bar_config["size"] = required_size
                    log_debug_safe(
                        logger,
                        safe_format(
                            "Auto-fixed BAR {bar} size for MSI-X table: 0x{size:x}",
                            bar=bar_index,
                            size=required_size,
                        ),
                        prefix="PCI_CAP",
                    )

            if bar_index == pba_bar:
                required_size = msix_cap.get("pba_offset", 0) + pba_size_bytes
                # Add some padding for safety
                required_size = (
                    (required_size + 0xFFF) // 0x1000
                ) * 0x1000  # Round up to 4KB
                if current_size < required_size:
                    bar_config["size"] = max(bar_config["size"], required_size)
                    log_debug_safe(
                        logger,
                        safe_format(
                            "Auto-fixed BAR {bar} size for MSI-X PBA: 0x{size:x}",
                            bar=bar_index,
                            size=required_size,
                        ),
                        prefix="PCI_CAP",
                    )

        # Fix 3: Resolve table/PBA overlap in same BAR
        if table_bar == pba_bar:
            table_offset = msix_cap.get("table_offset", 0)
            pba_offset = msix_cap.get("pba_offset", 0)
            table_end = table_offset + table_size_bytes
            pba_end = pba_offset + pba_size_bytes

            if table_offset < pba_end and table_end > pba_offset:
                # Move PBA after table with some padding
                new_pba_offset = ((table_end + 0xFFF) // 0x1000) * 0x1000  # 4KB align
                msix_cap["pba_offset"] = new_pba_offset
                log_debug_safe(
                    logger,
                    safe_format(
                        "Auto-fixed MSI-X table/PBA overlap: moved PBA to 0x{offset:x}",
                        offset=new_pba_offset,
                    ),
                    prefix="PCI_CAP",
                )

                # Update BAR size if necessary
                for bar_config in fixed_bars:
                    if bar_config.get("bar") == pba_bar:
                        new_required_size = new_pba_offset + pba_size_bytes
                        new_required_size = (
                            (new_required_size + 0xFFF) // 0x1000
                        ) * 0x1000  # Round up
                        if bar_config.get("size", 0) < new_required_size:
                            bar_config["size"] = new_required_size

        # Fix 4: Avoid conflicts with reserved regions
        # Move MSI-X structures away from common reserved regions
        reserved_end = 0x8000  # Avoid first 32KB for device control regions
        table_offset = msix_cap.get("table_offset", 0)
        pba_offset = msix_cap.get("pba_offset", 0)

        if table_offset < reserved_end:
            new_table_offset = ((reserved_end + 0xFFF) // 0x1000) * 0x1000
            msix_cap["table_offset"] = new_table_offset
            log_debug_safe(
                logger,
                safe_format(
                    "Auto-fixed MSI-X table to avoid reserved region: 0x{offset:x}",
                    offset=new_table_offset,
                ),
                prefix="PCI_CAP",
            )

        if pba_offset < reserved_end:
            new_pba_offset = ((reserved_end + 0xFFF) // 0x1000) * 0x1000
            # Ensure PBA doesn't overlap with (potentially moved) table
            table_end = msix_cap.get("table_offset", 0) + table_size_bytes
            if new_pba_offset < table_end:
                new_pba_offset = ((table_end + 0xFFF) // 0x1000) * 0x1000
            msix_cap["pba_offset"] = new_pba_offset
            log_debug_safe(
                logger,
                safe_format(
                    "Auto-fixed MSI-X PBA to avoid reserved region: 0x{offset:x}",
                    offset=new_pba_offset,
                ),
                prefix="PCI_CAP",
            )

        # Update the capability in the list
        fixed_capabilities[msix_cap_index] = msix_cap

        return fixed_bars, fixed_capabilities

    @abstractmethod
    def generate_bar_configuration(self) -> List[Dict[str, Any]]:
        """Generate BAR configuration for this device type."""
        raise NotImplementedError(
            safe_format(
                "{analyzer_type} analyzer must implement generate_bar_configuration(). "
                "This is a required method for device {vendor_id:04x}:{device_id:04x}.",
                analyzer_type=self.analyzer_type,
                vendor_id=self.vendor_id,
                device_id=self.device_id,
            )
        )


def auto_fix_msix_conflicts(
    analyzer: Any, bars: List[Dict[str, Any]], capabilities: List[Dict[str, Any]]
) -> Tuple[List[Dict[str, Any]], List[Dict[str, Any]]]:
    """
    Attempt to automatically fix MSI-X and BAR configuration conflicts.
    This is a non-protected version of the method in BaseFunctionAnalyzer.

    Args:
        analyzer: The analyzer instance
        bars: List of BAR configuration dictionaries
        capabilities: List of capability dictionaries

    Returns:
        Tuple of (fixed_bars, fixed_capabilities)
    """
    # Since we're exposing a public interface to a protected method,
    # we implement this as a public interface to the actual implementation
    if hasattr(analyzer, "_auto_fix_msix_conflicts"):
        # Using getattr to avoid direct protected access in pylint
        auto_fix_method = getattr(analyzer, "_auto_fix_msix_conflicts")
        return auto_fix_method(bars, capabilities)

    # Fallback if method doesn't exist
    return bars, capabilities


def create_function_capabilities(
    analyzer_class: type,
    vendor_id: int,
    device_id: int,
    analyzer_name: str,
) -> Dict[str, Any]:
    """
    Generic factory function to create device function capabilities.

    Args:
        analyzer_class: The analyzer class to instantiate
        vendor_id: PCI vendor ID from build process
        device_id: PCI device ID from build process
        analyzer_name: Name of the analyzer for logging

    Returns:
        Complete device configuration dictionary
    """
    try:
        analyzer = analyzer_class(vendor_id, device_id)

        # Generate initial configuration
        capabilities = analyzer.generate_capability_list()
        bars = analyzer.generate_bar_configuration()

        # Validate MSI-X and BAR configuration
        is_valid, validation_errors = analyzer.validate_msix_bar_configuration(
            bars, capabilities
        )

        if not is_valid:
            # Log validation errors but continue with configuration
            log_warning_safe(
                logger,
                safe_format(
                    "MSI-X/BAR validation failed for {vendor_id:04x}:{device_id:04x}: {count} errors",
                    vendor_id=vendor_id,
                    device_id=device_id,
                    count=len(validation_errors),
                ),
                prefix="PCI_CAP",
            )

            # Attempt to auto-fix common issues
            bars, capabilities = auto_fix_msix_conflicts(analyzer, bars, capabilities)

            # Re-validate after fixes
            is_valid_after_fix, remaining_errors = (
                analyzer.validate_msix_bar_configuration(bars, capabilities)
            )
            if is_valid_after_fix:
                log_info_safe(
                    logger,
                    safe_format(
                        "Auto-fixed MSI-X/BAR configuration for {vendor_id:04x}:{device_id:04x}",
                        vendor_id=vendor_id,
                        device_id=device_id,
                    ),
                    prefix="PCI_CAP",
                )
            else:
                # Auto-fix failed - log errors but continue with validation_status tracking
                log_error_safe(
                    logger,
                    safe_format(
                        "Failed to auto-fix MSI-X/BAR configuration for {vendor_id:04x}:{device_id:04x}",
                        vendor_id=vendor_id,
                        device_id=device_id,
                    ),
                    prefix="PCI_CAP",
                )
                for error in remaining_errors:
                    log_error_safe(
                        logger,
                        safe_format("  - {error}", error=error),
                        prefix="PCI_CAP",
                    )
        else:
            is_valid_after_fix = True
            remaining_errors = []

        config = {
            "vendor_id": vendor_id,
            "device_id": device_id,
            "class_code": analyzer.get_device_class_code(),
            "capabilities": capabilities,
            "bars": bars,
            "features": analyzer.generate_device_features(),
            "generated_by": analyzer_name,
            "validation_status": {
                "is_valid": is_valid_after_fix if not is_valid else is_valid,
                "errors": remaining_errors if not is_valid else [],
                "auto_fixed": not is_valid and is_valid_after_fix,
            },
        }

        log_info_safe(
            logger,
            safe_format(
                "Generated {analyzer_type} function capabilities for {vendor_id:04x}:{device_id:04x}",
                analyzer_type=analyzer.analyzer_type,
                vendor_id=vendor_id,
                device_id=device_id,
            ),
            prefix="PCI_CAP",
        )

        return config

    except NotImplementedError as e:
        # Subclass failed to implement required abstract method
        log_error_safe(
            logger,
            safe_format(
                "Analyzer implementation error for {vendor_id:04x}:{device_id:04x}: {error}",
                vendor_id=vendor_id,
                device_id=device_id,
                error=str(e),
            ),
            prefix="PCI_CAP",
        )
        raise
    except MSIXConfigurationError as e:
        # MSI-X validation failed and couldn't be auto-fixed
        log_error_safe(
            logger,
            safe_format(
                "MSI-X configuration error for {vendor_id:04x}:{device_id:04x}: {error}",
                vendor_id=vendor_id,
                device_id=device_id,
                error=str(e),
            ),
            prefix="PCI_CAP",
        )
        if e.validation_errors:
            for validation_error in e.validation_errors:
                log_error_safe(
                    logger,
                    safe_format("  - {error}", error=validation_error),
                    prefix="PCI_CAP",
                )
        raise
    except (ValueError, TypeError, KeyError) as e:
        # Configuration or data validation error
        root_cause = extract_root_cause(e)
        log_error_safe(
            logger,
            safe_format(
                "Invalid configuration for {analyzer_type} device {vendor_id:04x}:{device_id:04x}: {error}",
                analyzer_type=analyzer_name,
                vendor_id=vendor_id,
                device_id=device_id,
                error=root_cause,
            ),
            prefix="PCI_CAP",
        )
        raise ValueError(
            safe_format(
                "Failed to generate {analyzer_type} capabilities for {vendor_id:04x}:{device_id:04x}: {error}",
                analyzer_type=analyzer_name,
                vendor_id=vendor_id,
                device_id=device_id,
                error=root_cause,
            )
        ) from e
    except Exception as e:
        # Unexpected error - log detailed info and provide user-friendly message
        root_cause = extract_root_cause(e)
        log_error_safe(
            logger,
            safe_format(
                "Unexpected error generating {analyzer_type} capabilities for {vendor_id:04x}:{device_id:04x}: {error}",
                analyzer_type=analyzer_name,
                vendor_id=vendor_id,
                device_id=device_id,
                error=root_cause,
            ),
            prefix="PCI_CAP",
        )
        log_debug_safe(
            logger,
            safe_format(
                "Full error details: {details}",
                details=format_user_friendly_error(
                    e, context=f"Generating {analyzer_name} capabilities"
                ),
            ),
            prefix="PCI_CAP",
        )
        raise RuntimeError(
            safe_format(
                "Failed to generate {analyzer_type} function capabilities for {vendor_id:04x}:{device_id:04x}. "
                "Root cause: {error}. Check logs for details.",
                analyzer_type=analyzer_name,
                vendor_id=vendor_id,
                device_id=device_id,
                error=root_cause,
            )
        ) from e
