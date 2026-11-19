#!/usr/bin/env python3
"""Host Device Collector - Orchestrates device info collection before launch.

This module coordinates existing collection components to gather all device
information in a single VFIO binding session on the host, eliminating the need
for VFIO operations inside the container.
"""

import logging
import time
from contextlib import contextmanager
from dataclasses import asdict, is_dataclass
from pathlib import Path
from typing import Any, Dict, Iterator, Optional, cast

from pcileechfwgenerator.cli.vfio_handler import VFIOBinder
from pcileechfwgenerator.device_clone.config_space_manager import ConfigSpaceManager
from pcileechfwgenerator.device_clone.device_info_lookup import DeviceInfoLookup
from pcileechfwgenerator.device_clone.msix import MSIXData, MSIXManager
from pcileechfwgenerator.device_clone.msix_capability import parse_msix_capability
from pcileechfwgenerator.device_clone.register_evidence import LearningManifest
from pcileechfwgenerator.exceptions import BuildError
from pcileechfwgenerator.host_collect.mmiotrace import native_probe_observer
from pcileechfwgenerator.host_collect.register_learning import (
    LearningDiscovery,
    LearningResource,
    default_journal_directory,
    run_register_learning,
)
from pcileechfwgenerator.host_collect.snapshot import (
    ensure_capture_destination,
    normalize_bdf,
    write_snapshot,
)
from pcileechfwgenerator.log_config import get_logger
from pcileechfwgenerator.string_utils import (
    log_error_safe,
    log_info_safe,
    log_warning_safe,
    safe_format,
)

logger = get_logger(__name__)


class HostDeviceCollector:
    """Collects all device information on the host before container launch."""

    def _serialize_msix_data(self, msix_data: MSIXData) -> Optional[Dict[str, Any]]:
        """Serialize msix_data if preloaded, else return None."""
        if msix_data.preloaded:
            payload = asdict(msix_data)
            payload.pop("config_space_bytes", None)
            return payload
        return None

    def __init__(
        self,
        bdf: str,
        logger: Optional[logging.Logger] = None,
        enable_mmio_learning: bool = True,
        force_recapture: bool = False,
    ) -> None:
        """Initialize the collector.

        Args:
            bdf: PCI Bus/Device/Function identifier
            logger: Optional logger instance
            enable_mmio_learning: Observe the native driver with mmiotrace
            force_recapture: Compatibility option; each collection is a new capture
        """
        self.bdf = normalize_bdf(bdf)
        self.logger = logger or get_logger(__name__)
        self.enable_mmio_learning = enable_mmio_learning
        self.force_recapture = force_recapture

    def collect_device_context(self, output_dir: Path) -> Dict[str, Any]:
        """Collect complete device context using existing infrastructure.

        This method orchestrates the existing collection components:
        - ConfigSpaceManager for VFIO config space reading
        - DeviceInfoLookup for device information extraction
        - MSIXManager for MSI-X capability data

        Template context building is deferred to the container to avoid
        duplicating PCILeechContextBuilder instantiation logic.

        Args:
            output_dir: Directory to save collected data

        Returns:
            Complete device context dictionary

        Raises:
            BuildError: If critical device information cannot be collected
        """
        log_info_safe(
            self.logger, "Collecting complete device context on host", prefix="HOST"
        )

        ensure_capture_destination(output_dir)

        collected_data: Dict[str, Any] = {}
        discovered: LearningDiscovery | None = None

        def discover() -> LearningDiscovery:
            # Read-only identity discovery with the native binding intact.
            # strict_vfio=False prevents ConfigSpaceManager acquiring a binder.
            nonlocal discovered
            manager = ConfigSpaceManager(self.bdf, strict_vfio=False)
            config = manager.read_vfio_config_space()
            info = self._serialize_device_info(manager.extract_device_info(config))
            resources = tuple(
                LearningResource(
                    index=bar["index"],
                    size=bar["size"],
                    space="io_bar" if config[16 + bar["index"] * 4] & 1 else "mem_bar",
                )
                for bar in info.get("bar_config", {}).get("bars", info.get("bars", []))
                if bar.get("size", 0) > 0
            )
            discovered = LearningDiscovery.from_config(config, resources=resources)
            return discovered

        def collect_baseline(epoch: int) -> None:
            # This read is under the ONE binder owned by the learning service.
            manager = ConfigSpaceManager(self.bdf, strict_vfio=False)
            config = manager.read_vfio_config_space()
            if discovered is None:
                raise RuntimeError("baseline cannot precede donor discovery")
            if LearningDiscovery.from_config(config).donor != discovered.donor:
                raise ValueError("Donor identity changed during binding")
            info = self._serialize_device_info(
                DeviceInfoLookup(self.bdf).get_complete_device_info(
                    manager.extract_device_info(config), from_config_manager=True
                )
            )
            msix = self._collect_msix_data_vfio(
                MSIXManager(self.bdf, self.logger), config
            )
            collected_data.update(
                {
                    "bdf": self.bdf,
                    "config_space_hex": config.hex(),
                    "device_info": info,
                    "msix_data": self._serialize_msix_data(msix),
                    "collection_metadata": {
                        "collected_at": time.time(),
                        "config_space_size": len(config),
                        "config_space_hex_length": len(config) * 2,
                        "has_msix": msix.preloaded,
                        "collector_version": "1.0",
                        "state_epoch": epoch,
                    },
                }
            )

        try:
            learning = run_register_learning(
                self.bdf,
                discover=discover,
                journal_directory=default_journal_directory(),
                passive_observer=(
                    native_probe_observer(self.bdf)
                    if self.enable_mmio_learning
                    else None
                ),
                ownership=self._learning_ownership,
                collect_baseline=collect_baseline,
            )
            if not collected_data:
                # Surface the underlying cause (e.g. a VFIO bind error such as
                # IOMMU disabled or group busy) rather than a generic message.
                details = [gap.detail for gap in learning.gaps if gap.detail]
                causes = (
                    "; ".join([d for d in details if "failed" in d] or details)
                    or "no detail recorded"
                )
                raise BuildError(
                    safe_format(
                        "Learning lifecycle could not collect the config "
                        "baseline: {causes}",
                        causes=causes,
                    )
                )
            for gap in learning.gaps:
                if gap.reason == "restoration_failed":
                    # The device may be left on vfio-pci or without a driver.
                    log_error_safe(
                        self.logger,
                        safe_format(
                            "Donor driver binding was not restored: {detail}. "
                            "Rebind the device manually before using it.",
                            detail=gap.detail or "no detail recorded",
                        ),
                        prefix="HOST",
                    )
            bar_models = self._collect_bar_models(
                collected_data["device_info"], output_dir, learning=learning
            )
            collected_data["bar_models"] = bar_models
            collected_data["collection_metadata"]["has_bar_models"] = bool(bar_models)
            collected_data["collection_metadata"]["learning_status"] = learning.status
            # Publish only after binding restoration has been attempted, so its
            # failure is part of the immutable manifest.
            return self._save_collected_data(
                output_dir, collected_data, learning=learning
            )
        except (OSError, RuntimeError, ValueError, BuildError) as error:
            log_error_safe(
                self.logger,
                safe_format(
                    "Failed to collect device context: {error}", error=str(error)
                ),
                prefix="HOST",
            )
            raise BuildError(f"Host device collection failed: {error}") from error

    @contextmanager
    def _learning_ownership(self) -> Iterator[object]:
        """Own the donor through the binder, with a binding journal.

        The binder rolls back a partial bind itself and verifies the original
        binding (driver and driver_override) on release, raising with the
        actual final binding. Calling unbind() here, rather than leaving it to
        __exit__ (which logs and suppresses), makes that failure part of the
        learning manifest. A device already on vfio-pci stays there.
        """
        with VFIOBinder(
            self.bdf,
            attach=True,
            journal_directory=default_journal_directory() / "bindings",
        ) as binder:
            try:
                yield binder
            finally:
                binder.unbind()

    def _collect_msix_data_vfio(
        self, msix_manager: MSIXManager, config_space_bytes: bytes
    ) -> MSIXData:
        """Collect MSI-X data using VFIO access.

        Args:
            msix_manager: MSIXManager instance
            config_space_bytes: Raw config space data

        Returns:
            MSIXData object with collected information
        """
        try:
            # Parse MSI-X capability from config space

            config_space_hex = config_space_bytes.hex()
            msix_info = parse_msix_capability(config_space_hex)

            if msix_info and msix_info.get("table_size", 0) > 0:
                log_info_safe(
                    self.logger,
                    safe_format(
                        "MSI-X capability found: {vectors} vectors, "
                        "table BIR {bir} offset 0x{offset:x}, "
                        "PBA BIR {pba_bir} offset 0x{pba_offset:x}",
                        vectors=msix_info["table_size"],
                        bir=msix_info.get("table_bir", 0),
                        offset=msix_info.get("table_offset", 0),
                        pba_bir=msix_info.get("pba_bir", 0),
                        pba_offset=msix_info.get("pba_offset", 0),
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
                log_info_safe(
                    self.logger, "No MSI-X capability found in device", prefix="MSIX"
                )
                return MSIXData(preloaded=False)

        except Exception as e:
            log_warning_safe(
                self.logger,
                safe_format(
                    "MSI-X collection failed (non-fatal): {error}", error=str(e)
                ),
                prefix="MSIX",
            )
            return MSIXData(preloaded=False)

    @staticmethod
    def _serialize_device_info(info: Dict[str, Any]) -> Dict[str, Any]:
        """Normalize the actual ConfigSpaceManager BAR dataclasses for JSON."""
        result = dict(info)
        if "bars" in result:
            result["bars"] = [
                asdict(cast(Any, bar)) if is_dataclass(bar) else bar
                for bar in result["bars"]
            ]
        if "bar_config" in result:
            result["bar_config"] = dict(result["bar_config"])
            result["bar_config"]["bars"] = [
                asdict(cast(Any, bar)) if is_dataclass(bar) else bar
                for bar in result["bar_config"].get("bars", [])
            ]
        return result

    def _collect_bar_models(
        self,
        device_info: Dict[str, Any],
        output_dir: Path,
        *,
        learning: LearningManifest | None = None,
    ) -> Optional[Dict[int, Dict[str, Any]]]:
        """Compile captured evidence only; never start tracing or use a BDF cache.

        ``output_dir`` remains accepted for callers of the former helper.
        Missing learning is a legacy capture, not a request to probe hardware.
        """
        if learning is None:
            return None
        from pcileechfwgenerator.device_clone.bar_model_loader import (
            serialize_bar_model,
        )
        from pcileechfwgenerator.device_clone.register_policy import (
            compile_manifest_bar_models,
        )

        device_info = self._serialize_device_info(device_info)
        bars = device_info.get("bar_config", {}).get(
            "bars", device_info.get("bars", [])
        )
        memory_bars = [
            (bar["index"], bar["size"])
            for bar in bars
            if bar.get("is_memory") is not False
            and bar.get("bar_type", "").lower() != "io"
            and bar.get("size", 0) > 0
        ]
        # Same trust boundary as Stage 2. The published models are a
        # diagnostic view; Stage 2 re-derives its own from the manifest.
        serialized = {
            index: serialize_bar_model(result.model)
            for index, result in compile_manifest_bar_models(
                learning, memory_bars, diagnostic=True
            ).items()
        }
        return serialized or None

    def _save_collected_data(
        self,
        output_dir: Path,
        data: Dict[str, Any],
        *,
        learning: LearningManifest | None = None,
    ) -> Dict[str, Any]:
        """Save a versioned, immutable context and matching MSI-X sidecar."""
        snapshot = write_snapshot(
            output_dir,
            self.bdf,
            bytes.fromhex(data["config_space_hex"]),
            (data.get("msix_data") or {}).get("msix_info") or {},
            context=data,
            learning=learning,
            capture_id=learning.context.capture_id if learning is not None else None,
        )
        log_info_safe(
            self.logger,
            "Host device context and matching MSI-X capture saved",
            prefix="HOST",
        )
        return snapshot.context
