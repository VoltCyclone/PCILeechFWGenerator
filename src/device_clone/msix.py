"""
MSI-X capability management module.

This module contains MSI-X related data structures and management classes
that were previously in src.build module.
"""

from __future__ import annotations

import logging
import os
from dataclasses import dataclass
from typing import Any, Dict, Optional

from pcileechfwgenerator.device_clone.msix_capability import parse_msix_capability
from pcileechfwgenerator.host_collect.snapshot import (
    load_configured_snapshot,
    verify_current_device,
)
from pcileechfwgenerator.string_utils import (
    log_debug_safe,
    log_info_safe,
    log_warning_safe,
    safe_format,
)

# Constants
CONFIG_SPACE_PATH_TEMPLATE = "/sys/bus/pci/devices/{}/config"


@dataclass(slots=True)
class MSIXData:
    """Container for MSI-X capability data."""

    preloaded: bool
    msix_info: Optional[Dict[str, Any]] = None
    config_space_hex: Optional[str] = None
    config_space_bytes: Optional[bytes] = None


class MSIXManager:
    """Manages MSI-X capability data preloading and injection."""

    def __init__(self, bdf: str, logger: Optional[logging.Logger] = None):
        """
        Initialize the MSI-X manager.

        Args:
            bdf: PCI Bus/Device/Function address
            logger: Optional logger instance
        """
        self.bdf = bdf
        from pcileechfwgenerator.log_config import get_logger

        self.logger = logger or get_logger(self.__class__.__name__)

    def preload_data(self) -> MSIXData:
        """Load a validated capture or read live config; invalid captures fail closed."""
        disable_vfio = bool(os.environ.get("DEVICE_CONTEXT_PATH")) or any(
            os.environ.get(name, "").lower() in ("1", "true", "yes", "on")
            for name in ("PCILEECH_DISABLE_VFIO", "PCILEECH_HOST_CONTEXT_ONLY")
        )
        # Keep validation outside the optional live-probe exception handler.
        snapshot = load_configured_snapshot(self.bdf, include_msix_only=True)
        if snapshot is not None:
            if not disable_vfio:
                verify_current_device(snapshot)
            msix_info = snapshot.msix["msix_info"]
            if msix_info and int(msix_info.get("table_size", 0)) > 0:
                return MSIXData(
                    preloaded=True,
                    msix_info=msix_info,
                    config_space_hex=snapshot.config_space.hex(),
                    config_space_bytes=snapshot.config_space,
                )
            return MSIXData(preloaded=False)
        if disable_vfio:
            return MSIXData(preloaded=False)
        try:
            config_space_path = CONFIG_SPACE_PATH_TEMPLATE.format(self.bdf)
            if not os.path.exists(config_space_path):
                log_warning_safe(
                    self.logger,
                    "Config space not accessible via sysfs, skipping MSI-X preload",
                    prefix="MSIX",
                )
                return MSIXData(preloaded=False)

            config_space_bytes = self._read_config_space(config_space_path)
            config_space_hex = config_space_bytes.hex()
            msix_info = parse_msix_capability(config_space_hex)

            if (
                msix_info is not None
                and isinstance(msix_info, dict)
                and "table_size" in msix_info
                and msix_info["table_size"] > 0
            ):
                log_info_safe(
                    self.logger,
                    safe_format(
                        "Found MSI-X capability: {vectors} vectors",
                        vectors=msix_info["table_size"],
                    ),
                    prefix="MSIX",
                )
                return MSIXData(
                    preloaded=True,
                    msix_info=msix_info,
                    config_space_hex=config_space_hex,
                    config_space_bytes=config_space_bytes,
                )
            else:
                # No MSI-X capability found -> treat as not preloaded so callers
                # don't assume hardware MSI-X values are available.
                log_info_safe(
                    self.logger,
                    "No MSI-X capability found",
                    prefix="MSIX",
                )
                return MSIXData(preloaded=False, msix_info=None)

        except Exception as e:
            log_warning_safe(
                self.logger,
                safe_format("MSI-X preload failed: {err}", err=str(e)),
                prefix="MSIX",
            )
            if self.logger.isEnabledFor(logging.DEBUG):
                log_debug_safe(
                    self.logger,
                    safe_format(
                        "MSI-X preload exception details: {err}",
                        err=str(e),
                    ),
                    prefix="MSIX",
                )
            return MSIXData(preloaded=False)

    def inject_data(self, result: Dict[str, Any], msix_data: MSIXData) -> None:
        """
        Inject preloaded MSI-X data into the generation result.

        Args:
            result: The generation result dictionary to update
            msix_data: The preloaded MSI-X data
        """
        if not self._should_inject(msix_data):
            return

        log_info_safe(
            self.logger, safe_format("Using preloaded MSI-X data"), prefix="MSIX"
        )

        # msix_info is guaranteed to be non-None by _should_inject
        if msix_data.msix_info is not None:
            if "msix_data" not in result or not result["msix_data"]:
                result["msix_data"] = self._create_msix_result(msix_data.msix_info)

            # Update template context if present
            if (
                "template_context" in result
                and "msix_config" in result["template_context"]
            ):
                result["template_context"]["msix_config"].update(
                    {
                        "is_supported": True,
                        "num_vectors": msix_data.msix_info["table_size"],
                    }
                )

    def _read_config_space(self, path: str) -> bytes:
        """
        Read PCI config space from sysfs.

        Args:
            path: Path to config space file

        Returns:
            Config space bytes

        Raises:
            IOError: If reading fails
        """
        with open(path, "rb") as f:
            return f.read()

    def _should_inject(self, msix_data: MSIXData) -> bool:
        """
        Check if MSI-X data should be injected.

        Args:
            msix_data: The MSI-X data to check

        Returns:
            True if data should be injected
        """
        return (
            msix_data.preloaded
            and msix_data.msix_info is not None
            and msix_data.msix_info.get("table_size", 0) > 0
        )

    def _create_msix_result(self, msix_info: Dict[str, Any]) -> Dict[str, Any]:
        """
        Create MSI-X result dictionary from capability info.

        Args:
            msix_info: MSI-X capability information

        Returns:
            Formatted MSI-X result dictionary
        """
        return {
            "capability_info": msix_info,
            "table_size": msix_info["table_size"],
            "table_bir": msix_info["table_bir"],
            "table_offset": msix_info["table_offset"],
            "pba_bir": msix_info["pba_bir"],
            "pba_offset": msix_info["pba_offset"],
            "enabled": msix_info["enabled"],
            "function_mask": msix_info["function_mask"],
            "is_valid": True,
            "validation_errors": [],
        }
