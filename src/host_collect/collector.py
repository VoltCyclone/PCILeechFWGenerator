#!/usr/bin/env python3
from __future__ import annotations

import os
import json

from pathlib import Path
from typing import Optional, Dict, Any

from src.string_utils import (
    log_info_safe,
    log_error_safe,
    log_warning_safe,
    log_debug_safe,
    safe_format,
)
from src.log_config import get_logger

# Reuse existing MSI-X parser
from src.device_clone.msix_capability import parse_msix_capability

CONFIG_PATH_TEMPLATE = "/sys/bus/pci/devices/{bdf}/config"


class HostCollector:
    """Collect PCIe device information on the host and write a datastore.

    Writes:
      - device_context.json: { "config_space_hex": "..." }
      - msix_data.json: { "config_space_hex": "...", "msix_info": {..} }
    """

    def __init__(
        self,
        bdf: str,
        datastore: Path,
        logger=None,
        enable_mmio_learning: bool = True,
        force_recapture: bool = False,
    ) -> None:
        self.bdf = bdf
        self.datastore = datastore
        self.logger = logger or get_logger(self.__class__.__name__)
        self.enable_mmio_learning = enable_mmio_learning
        self.force_recapture = force_recapture

    def run(self) -> int:
        try:
            cfg = self._read_config_space()
            if cfg is None:
                return 1

            # Minimal visualization: dump first 64 bytes
            self._visualize(cfg[:64])

            cfg_hex = cfg.hex()
            msix_info = self._parse_msix(cfg)
            
            # Extract device identifiers from config space
            device_ids = self._extract_device_ids(cfg)

            # Write device_context.json with extracted device IDs
            ctx_path = self.datastore / "device_context.json"
            with open(ctx_path, "w") as f:
                json.dump({
                    "config_space_hex": cfg_hex,
                    "vendor_id": device_ids["vendor_id"],
                    "device_id": device_ids["device_id"],
                    "class_code": device_ids["class_code"],
                    "revision_id": device_ids["revision_id"],
                    "subsystem_vendor_id": device_ids.get("subsystem_vendor_id"),
                    "subsystem_device_id": device_ids.get("subsystem_device_id"),
                }, f, indent=2)
            log_info_safe(
                self.logger,
                safe_format(
                    "Wrote {path} with device IDs: VID={vid:04x} DID={did:04x}",
                    path=str(ctx_path),
                    vid=device_ids["vendor_id"],
                    did=device_ids["device_id"]
                ),
                prefix="COLLECT",
            )

            # Write msix_data.json
            msix_path = self.datastore / "msix_data.json"
            with open(msix_path, "w") as f:
                json.dump(
                    {
                        "config_space_hex": cfg_hex,
                        "msix_info": msix_info,
                    },
                    f,
                    indent=2,
                )
            log_info_safe(
                self.logger,
                safe_format("Wrote {path}", path=str(msix_path)),
                prefix="COLLECT",
            )

            return 0
        except Exception as e:
            log_error_safe(
                self.logger,
                safe_format("Host collection failed: {err}", err=str(e)),
                prefix="COLLECT",
            )
            return 1

    def _read_config_space(self) -> Optional[bytes]:
        path = CONFIG_PATH_TEMPLATE.format(bdf=self.bdf)
        if not os.path.exists(path):
            log_error_safe(
                self.logger,
                safe_format("Config space not found: {path}", path=path),
                prefix="COLLECT",
            )
            return None
        try:
            with open(path, "rb") as f:
                data = f.read()
            if not data:
                log_error_safe(self.logger, "Empty config space", prefix="COLLECT")
                return None
            log_info_safe(
                self.logger,
                safe_format("Read {n} bytes of config space", n=len(data)),
                prefix="COLLECT",
            )
            return data
        except Exception as e:
            log_error_safe(
                self.logger,
                safe_format("Config read error: {err}", err=str(e)),
                prefix="COLLECT",
            )
            return None

    def _parse_msix(self, cfg: bytes) -> Dict[str, Any]:
        try:
            info = parse_msix_capability(cfg)
            if not info:
                return {}
            # Normalize keys expected by MSI-X manager
            return dict(info)
        except Exception as e:
            log_debug_safe(
                self.logger,
                safe_format("MSI-X parse skipped: {err}", err=str(e)),
                prefix="COLLECT",
            )
            return {}

    def _extract_device_ids(self, cfg: bytes) -> Dict[str, Any]:
        """Extract device identification fields from config space."""
        if len(cfg) < 64:
            log_error_safe(
                self.logger,
                "Config space too short for device ID extraction",
                prefix="COLLECT"
            )
            return {}
        
        try:
            # Extract basic device IDs (offsets 0x00-0x0B)
            vendor_id = int.from_bytes(cfg[0:2], "little")
            device_id = int.from_bytes(cfg[2:4], "little")
            revision_id = cfg[8]
            class_code = int.from_bytes(cfg[9:12], "little")
            
            # Extract subsystem IDs (offsets 0x2C-0x2F)
            subsystem_vendor_id = None
            subsystem_device_id = None
            if len(cfg) >= 48:
                subsystem_vendor_id = int.from_bytes(cfg[44:46], "little")
                subsystem_device_id = int.from_bytes(cfg[46:48], "little")
            
            log_info_safe(
                self.logger,
                safe_format(
                    "Extracted device IDs: VID=0x{vid:04x} DID=0x{did:04x} "
                    "Class=0x{cls:06x} Rev=0x{rev:02x}",
                    vid=vendor_id,
                    did=device_id,
                    cls=class_code,
                    rev=revision_id
                ),
                prefix="COLLECT"
            )
            
            return {
                "vendor_id": vendor_id,
                "device_id": device_id,
                "revision_id": revision_id,
                "class_code": class_code,
                "subsystem_vendor_id": subsystem_vendor_id,
                "subsystem_device_id": subsystem_device_id,
            }
        except Exception as e:
            log_error_safe(
                self.logger,
                safe_format("Failed to extract device IDs: {err}", err=str(e)),
                prefix="COLLECT"
            )
            return {}
    
    def _visualize(self, buf: bytes) -> None:
        # Simple hex dump with offsets
        lines = []
        for off in range(0, len(buf), 16):
            chunk = buf[off : off + 16]
            hexs = " ".join(f"{b:02x}" for b in chunk)
            lines.append(f"{off:04x}: {hexs}")
        for line in lines:
            log_info_safe(self.logger, line, prefix="CFG64")
