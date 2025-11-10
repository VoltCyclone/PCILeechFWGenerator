"""
MSI-X capability management module.

This module contains MSI-X related data structures and management classes
that were previously in src.build module.
"""

from __future__ import annotations

import json
import logging
import os

from dataclasses import dataclass
from typing import Any, Dict, Optional

from src.string_utils import (
    log_debug_safe,
    log_info_safe,
    log_warning_safe,
    safe_format,
)

from src.device_clone.msix_capability import parse_msix_capability

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
        from src.log_config import get_logger

        self.logger = logger or get_logger(self.__class__.__name__)

    def preload_data(self) -> MSIXData:
        """
        Preload MSI-X data before VFIO binding.

        Returns:
            MSIXData object containing preloaded information

        Note:
            Returns empty MSIXData on any failure (non-critical operation)
        """
        try:
            # In host-context-only mode, never touch sysfs/VFIO;
            # use pre-saved files only
            disable_vfio = str(os.environ.get("PCILEECH_DISABLE_VFIO", "")).lower() in (
                "1",
                "true",
                "yes",
                "on",
            ) or str(os.environ.get("PCILEECH_HOST_CONTEXT_ONLY", "")).lower() in (
                "1",
                "true",
                "yes",
                "on",
            )
            if disable_vfio:
                msix_path = os.environ.get(
                    "MSIX_DATA_PATH",
                    "/app/output/msix_data.json",
                )
                try:
                    if msix_path and os.path.exists(msix_path):
                        with open(msix_path, "r") as f:
                            payload = json.load(f)
                        # Support different shapes
                        msix_info = payload.get("capability_info") or payload.get(
                            "msix_info"
                        )
                        cfg_hex = payload.get("config_space_hex")
                        cfg_bytes = bytes.fromhex(cfg_hex) if cfg_hex else None
                        if not msix_info and cfg_hex:
                            msix_info = parse_msix_capability(cfg_hex)
                        if msix_info and int(msix_info.get("table_size", 0)) > 0:
                            log_info_safe(
                                self.logger,
                                safe_format(
                                    "Loaded MSI-X from {path} ({n} vectors)",
                                    path=msix_path,
                                    n=msix_info.get("table_size", 0),
                                ),
                                prefix="MSIX",
                            )
                            return MSIXData(
                                preloaded=True,
                                msix_info=msix_info,
                                config_space_hex=cfg_hex,
                                config_space_bytes=cfg_bytes,
                            )
                except Exception as e:
                    log_warning_safe(
                        self.logger,
                        safe_format(
                            "Failed to load MSI-X from host file: {err}", err=str(e)
                        ),
                        prefix="MSIX",
                    )
                # Do not attempt sysfs/VFIO in strict mode
                log_info_safe(
                    self.logger,
                    (
                        "MSI-X preload: host-context-only mode active; "
                        "skipping sysfs/VFIO"
                    ),
                    prefix="MSIX",
                )
                return MSIXData(preloaded=False)
            log_info_safe(self.logger, "Preloading MSI-X data before VFIO binding")

            # 1) Prefer host-provided JSON (mounted into container) if available
            #    This preserves MSI-X context when container lacks sysfs/VFIO access.
            try:
                msix_json_path = os.environ.get(
                    "MSIX_DATA_PATH", "/app/output/msix_data.json"
                )
                if msix_json_path and os.path.exists(msix_json_path):
                    with open(msix_json_path, "r") as f:
                        payload = json.load(f)

                    # Optional: ensure BDF matches if present
                    bdf_in = payload.get("bdf")
                    msix_info = payload.get("msix_info")
                    cfg_hex = payload.get("config_space_hex")
                    if msix_info and isinstance(msix_info, dict):
                        log_info_safe(
                            self.logger,
                            safe_format(
                                "Loaded MSI-X from {path} ({vectors} vectors)",
                                path=msix_json_path,
                                vectors=msix_info.get("table_size", 0),
                            ),
                            prefix="MSIX",
                        )
                        return MSIXData(
                            preloaded=True,
                            msix_info=msix_info,
                            config_space_hex=cfg_hex,
                            config_space_bytes=(
                                bytes.fromhex(cfg_hex) if cfg_hex else None
                            ),
                        )
            except Exception as e:
                # Non-fatal; fall back to sysfs path
                log_debug_safe(
                    self.logger,
                    safe_format(
                        "MSI-X JSON ingestion skipped: {err}",
                        err=str(e),
                    ),
                    prefix="MSIX",
                )

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
