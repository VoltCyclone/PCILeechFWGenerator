#!/usr/bin/env python3
from __future__ import annotations

import os
from pathlib import Path
from typing import Any, Dict, Optional

# Reuse existing MSI-X parser
from pcileechfwgenerator.device_clone.msix_capability import parse_msix_capability
from pcileechfwgenerator.host_collect.snapshot import (
    ensure_capture_destination,
    normalize_bdf,
    write_snapshot,
)
from pcileechfwgenerator.log_config import get_logger
from pcileechfwgenerator.string_utils import (
    log_debug_safe,
    log_error_safe,
    log_info_safe,
    log_warning_safe,
    safe_format,
)

CONFIG_PATH_TEMPLATE = "/sys/bus/pci/devices/{bdf}/config"


class HostCollector:
    """Collect PCIe device information on the host and write a datastore.

    Writes a versioned device_context.json and matching msix_data.json using
    the shared capture identity contract. Existing capture files are preserved.
    """

    def __init__(
        self,
        bdf: str,
        datastore: Path,
        logger=None,
        enable_mmio_learning: bool = True,
        force_recapture: bool = False,
        complete_capture: bool = False,
        strict_offline_resources: bool = True,
    ) -> None:
        self.bdf = normalize_bdf(bdf)
        self.datastore = datastore
        self.logger = logger or get_logger(self.__class__.__name__)
        self.enable_mmio_learning = enable_mmio_learning
        self.force_recapture = force_recapture
        self.complete_capture = complete_capture
        # Live builds only need the BAR extension for container rendering, so
        # a live collect may proceed without it; explicit offline collects may not.
        self.strict_offline_resources = strict_offline_resources

    def run(self) -> int:
        try:
            ensure_capture_destination(self.datastore)
            cfg = self._read_config_space()
            if cfg is None:
                return 1

            # Minimal visualization: dump first 64 bytes
            self._visualize(cfg[:64])

            cfg_hex = cfg.hex()
            msix_info = self._parse_msix(cfg_hex)

            context = None
            if self.complete_capture:
                from .offline import capture_bar_sizes
                from .snapshot import SnapshotError

                resource = Path("/sys/bus/pci/devices") / self.bdf / "resource"
                try:
                    context = {
                        "offline_resources": {
                            "schema_version": 1,
                            "source": "linux_sysfs_resource",
                            "bar_sizes": capture_bar_sizes(resource.read_text()),
                        }
                    }
                except (SnapshotError, OSError) as error:
                    if self.strict_offline_resources:
                        raise
                    log_warning_safe(
                        self.logger,
                        safe_format(
                            "BAR lengths not recorded ({err}); this capture "
                            "serves local live builds only",
                            err=str(error),
                        ),
                        prefix="COLLECT",
                    )
            write_snapshot(self.datastore, self.bdf, cfg, msix_info, context)
            log_info_safe(
                self.logger,
                "Wrote matching device context and MSI-X capture files",
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
        except OSError as e:
            # Only file I/O happens here; a non-OSError would be a programming
            # bug and should propagate rather than be swallowed.
            log_error_safe(
                self.logger,
                safe_format("Config read error: {err}", err=str(e)),
                prefix="COLLECT",
            )
            return None

    def _parse_msix(self, cfg_hex: str) -> Dict[str, Any]:
        # parse_msix_capability expects the config space as a hex string,
        # not raw bytes (see issue #612).
        try:
            info = parse_msix_capability(cfg_hex)
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
