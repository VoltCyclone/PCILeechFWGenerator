#!/usr/bin/env python3
"""
PCIe Payload Size Configuration Module

This module handles validation and configuration of PCIe Maximum Payload Size (MPS),
including automatic cfg_force_mps parameter calculation and tiny PCIe algorithm detection.
"""

import logging
from typing import Any, Dict, Optional, Tuple

from src.device_clone.constants import (
    MPS_VALUE_TO_ENCODING,
    PCIE_MPS_CONSTANTS,
    VALID_MPS_VALUES,
)

from src.exceptions import ContextError

from src.string_utils import (log_info_safe, log_warning_safe,
                              safe_format)

logger = logging.getLogger(__name__)


class PayloadSizeError(ContextError):
    """Raised when payload size configuration is invalid."""

    pass


class PayloadSizeConfig:
    """
    Manages PCIe Maximum Payload Size (MPS) configuration.

    This class validates MPS values, calculates cfg_force_mps parameters,
    and detects potential issues with tiny PCIe algorithm performance.
    """

    def __init__(
        self, max_payload_size: int, device_capabilities: Optional[Dict] = None
    ):
        """
        Initialize payload size configuration.

        Args:
            max_payload_size: Maximum payload size in bytes
            device_capabilities: Optional device capabilities for validation

        Raises:
            PayloadSizeError: If payload size is invalid
        """
        self.max_payload_size = max_payload_size
        self.device_capabilities = device_capabilities or {}
        self._validate_payload_size()

    def _validate_payload_size(self) -> None:
        """
        Validate that the payload size is a valid PCIe MPS value.

        Raises:
            PayloadSizeError: If payload size is invalid
        """
        if self.max_payload_size not in VALID_MPS_VALUES:
            raise PayloadSizeError(
                safe_format(
                    "Invalid maximum payload size: {max_payload_size} bytes. "
                    "Valid values are: {valid_values} bytes",
                    max_payload_size=self.max_payload_size,
                    valid_values=", ".join(map(str, VALID_MPS_VALUES)),
                )
            )
        log_info_safe(
            logger,
            safe_format(
                "Validated payload size: {size} bytes", size=self.max_payload_size
            ),
        )

    def get_mps_encoding(self) -> int:
        """
        Get the PCIe MPS encoding value for the configured payload size.

        Returns:
            MPS encoding value (0-5) for PCIe Device Control Register
        """
        return MPS_VALUE_TO_ENCODING[self.max_payload_size]

    def get_cfg_force_mps(self) -> int:
        """
        Calculate the cfg_force_mps parameter based on the payload size.

        The cfg_force_mps parameter forces a specific Maximum Payload Size
        in the PCIe configuration. This is the encoding value that will be
        written to the Device Control Register.

        Returns:
            cfg_force_mps value (0-5)
        """
        encoding = self.get_mps_encoding()
        log_info_safe(
            logger,
            safe_format(
                "Calculated cfg_force_mps: {encoding} for payload size {size} bytes",
                encoding=encoding,
                size=self.max_payload_size,
            ),
            prefix="PAYLOAD",
        )
        return encoding

    def check_tiny_pcie_algo_issues(self) -> Tuple[bool, Optional[str]]:
        """
        Check if the payload size might cause tiny PCIe algorithm issues.

        The "tiny PCIe algo" refers to performance degradation when payload
        sizes are too small for efficient PCIe operation. This typically
        occurs with payload sizes < 256 bytes.

        Returns:
            Tuple of (has_issues, warning_message)
        """
        threshold = PCIE_MPS_CONSTANTS["TINY_PCIE_THRESHOLD"]

        if self.max_payload_size < threshold:
            warning = safe_format(
                (
                    "Payload size {max_payload_size} bytes is below the recommended "
                    "threshold of {threshold} bytes. This may cause performance "
                    "issues with the 'tiny PCIe algorithm'. Consider using a larger "
                    "payload size if your device supports it."
                ),
                max_payload_size=self.max_payload_size,
                threshold=threshold,
            )
            log_warning_safe(logger, warning, prefix="PAYLOAD")
            return True, warning

        return False, None

    def validate_against_device_capabilities(self) -> None:
        """
        Validate payload size against device capabilities if available.

        Raises:
            PayloadSizeError: If payload size exceeds device capabilities
        """
        if not self.device_capabilities:
            return

        # Check if device has a maximum supported payload size
        device_max_payload = self.device_capabilities.get("max_payload_supported")
        if device_max_payload and self.max_payload_size > device_max_payload:
            raise PayloadSizeError(
                safe_format(
                    (
                        "Configured payload size {max_payload_size} bytes exceeds "
                        "device maximum supported payload size of "
                        "{device_max_payload} bytes"
                    ),
                    max_payload_size=self.max_payload_size,
                    device_max_payload=device_max_payload,
                )
            )

        # Check PCIe generation compatibility
        pcie_gen = self.device_capabilities.get("pcie_generation")
        if pcie_gen:
            recommended_mps = self._get_recommended_mps_for_gen(pcie_gen)
            if recommended_mps and self.max_payload_size < recommended_mps:
                log_warning_safe(
                    logger,
                    safe_format(
                        "Payload size {max_payload_size} bytes is below the "
                        "recommended {recommended_mps} bytes for PCIe Gen{pcie_gen}",
                        max_payload_size=self.max_payload_size,
                        recommended_mps=recommended_mps,
                        pcie_gen=pcie_gen,
                    ),
                    prefix="PAYLOAD",
                )

    def _get_recommended_mps_for_gen(self, pcie_gen: int) -> Optional[int]:
        """
        Get recommended MPS for a specific PCIe generation.

        Args:
            pcie_gen: PCIe generation (1-5)

        Returns:
            Recommended MPS in bytes, or None if no recommendation
        """
        recommendations = {
            1: 256,  # PCIe Gen1: 256 bytes recommended
            2: 256,  # PCIe Gen2: 256 bytes recommended
            3: 512,  # PCIe Gen3: 512 bytes recommended
            4: 1024,  # PCIe Gen4: 1024 bytes recommended
            5: 2048,  # PCIe Gen5: 2048 bytes recommended
        }
        return recommendations.get(pcie_gen)

    def get_configuration_summary(self) -> Dict[str, Any]:
        """
        Get a summary of the payload size configuration.

        Returns:
            Dictionary with configuration details
        """
        has_tiny_pcie_issues, warning = self.check_tiny_pcie_algo_issues()

        return {
            "max_payload_size": self.max_payload_size,
            "mps_encoding": self.get_mps_encoding(),
            "cfg_force_mps": self.get_cfg_force_mps(),
            "has_tiny_pcie_issues": has_tiny_pcie_issues,
            "warning": warning,
            "hex_encoding": f"0x{self.get_mps_encoding():X}",
        }


def validate_and_configure_payload_size(
    max_payload_size: int,
    device_capabilities: Optional[Dict] = None,
    fail_on_warning: bool = False,
) -> Dict[str, Any]:
    """
    Validate and configure payload size with automatic cfg_force_mps calculation.

    Args:
        max_payload_size: Maximum payload size in bytes
        device_capabilities: Optional device capabilities for validation
        fail_on_warning: If True, raise error on tiny PCIe algo warnings

    Returns:
        Configuration dictionary with cfg_force_mps and validation results

    Raises:
        PayloadSizeError: If validation fails
    """
    try:
        config = PayloadSizeConfig(max_payload_size, device_capabilities)
        config.validate_against_device_capabilities()

        summary = config.get_configuration_summary()

        # Check if we should fail on warnings
        if fail_on_warning and summary["has_tiny_pcie_issues"]:
            raise PayloadSizeError(summary["warning"])

        return summary

    except Exception as e:
        raise PayloadSizeError(
            safe_format("Failed to configure payload size: {error}", error=str(e))
        ) from e
