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

            # Write device_context.json
            ctx_path = self.datastore / "device_context.json"
            with open(ctx_path, "w") as f:
                json.dump({"config_space_hex": cfg_hex}, f, indent=2)
            log_info_safe(
                self.logger,
                safe_format("Wrote {path}", path=str(ctx_path)),
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

    def _visualize(self, buf: bytes) -> None:
        # Simple hex dump with offsets
        lines = []
        for off in range(0, len(buf), 16):
            chunk = buf[off : off + 16]
            hexs = " ".join(f"{b:02x}" for b in chunk)
            lines.append(f"{off:04x}: {hexs}")
        for line in lines:
            log_info_safe(self.logger, line, prefix="CFG64")
