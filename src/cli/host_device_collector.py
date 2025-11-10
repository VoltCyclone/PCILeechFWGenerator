#!/usr/bin/env python3
"""Host Device Collector - Orchestrates device info collection before launch.

This module coordinates existing collection components to gather all device
information in a single VFIO binding session on the host, eliminating the need
for VFIO operations inside the container.
"""

import json
import logging
import time
from dataclasses import asdict

from pathlib import Path
from typing import Dict, Any, Optional

from src.string_utils import (
    safe_format,
    log_info_safe,
    log_warning_safe,
    log_error_safe,
)

from src.device_clone.config_space_manager import ConfigSpaceManager

from src.device_clone.device_info_lookup import DeviceInfoLookup
from src.device_clone.msix_capability import parse_msix_capability

from src.device_clone.msix import MSIXManager, MSIXData

from src.cli.vfio_handler import VFIOBinder

from src.exceptions import BuildError


class HostDeviceCollector:
    """Collects all device information on the host before container launch."""

    def _serialize_msix_data(self, msix_data: MSIXData) -> Optional[Dict[str, Any]]:
        """Serialize msix_data if preloaded, else return None."""
        if msix_data.preloaded:
            return asdict(msix_data)
        return None

    def __init__(
        self,
        bdf: str,
        logger: Optional[logging.Logger] = None,
        enable_mmio_learning: bool = True,
        force_recapture: bool = False,
    ):
        """Initialize the collector.

        Args:
            bdf: PCI Bus/Device/Function identifier
            logger: Optional logger instance
            enable_mmio_learning: Enable MMIO trace capture for BAR models
            force_recapture: Force recapture even if cached models exist
        """
        self.bdf = bdf
        self.logger = logger or logging.getLogger(__name__)
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

        # Use single VFIO binding session to collect all data
        with VFIOBinder(self.bdf, attach=True) as _:
            try:
                # 1. Use existing ConfigSpaceManager for VFIO config space reading
                config_manager = ConfigSpaceManager(self.bdf, strict_vfio=True)
                config_space_bytes = config_manager.read_vfio_config_space()

                log_info_safe(
                    self.logger,
                    safe_format(
                        "Read {size} bytes of config space via VFIO",
                        size=len(config_space_bytes),
                    ),
                    prefix="HOST",
                )

                # 2. Extract device info using existing DeviceInfoLookup
                device_lookup = DeviceInfoLookup(self.bdf)
                extracted_info = config_manager.extract_device_info(config_space_bytes)
                # Complete device information using the unified lookup API
                # Pass from_config_manager=True to avoid redundant extraction loops
                device_info = device_lookup.get_complete_device_info(
                    extracted_info, from_config_manager=True
                )

                # 3. Use existing MSIXManager for MSI-X data collection
                msix_manager = MSIXManager(self.bdf, self.logger)
                msix_data = self._collect_msix_data_vfio(
                    msix_manager, config_space_bytes
                )

                # 4. Collect BAR models via MMIO learning (optional)
                bar_models = None
                if self.enable_mmio_learning:
                    bar_models = self._collect_bar_models(device_info, output_dir)

                # 5. Save collected data for container consumption
                # Note: Template context building is deferred to the container
                # to avoid duplicating PCILeechContextBuilder instantiation
                config_space_hex = config_space_bytes.hex()

                # Validate that we have the full config space
                if len(config_space_hex) != len(config_space_bytes) * 2:
                    log_error_safe(
                        self.logger,
                        safe_format(
                            "Config space hex length mismatch: {hex_len} chars "
                            "vs {byte_len} bytes * 2",
                            hex_len=len(config_space_hex),
                            byte_len=len(config_space_bytes),
                        ),
                        prefix="HOST",
                    )

                collected_data = {
                    "bdf": self.bdf,
                    "config_space_hex": config_space_hex,
                    "device_info": device_info,
                    "msix_data": self._serialize_msix_data(msix_data),
                    "bar_models": bar_models,
                    "collection_metadata": {
                        "collected_at": time.time(),
                        "config_space_size": len(config_space_bytes),
                        "config_space_hex_length": len(config_space_hex),
                        "has_msix": msix_data.preloaded,
                        "has_bar_models": bar_models is not None,
                        "collector_version": "1.0",
                    },
                }
                self._save_collected_data(output_dir, collected_data)

                log_info_safe(
                    self.logger,
                    safe_format(
                        "Device context collected and saved to {output}",
                        output=output_dir,
                    ),
                    prefix="HOST",
                )

                # Return the raw collected data for caller to process
                return collected_data

            except Exception as e:
                log_error_safe(
                    self.logger,
                    safe_format(
                        "Failed to collect device context: {error}", error=str(e)
                    ),
                    prefix="HOST",
                )
                raise BuildError(f"Host device collection failed: {e}") from e

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

    def _collect_bar_models(
        self, device_info: Dict[str, Any], output_dir: Path
    ) -> Optional[Dict[int, Dict[str, Any]]]:
        """Collect BAR register models via MMIO trace capture.

        Args:
            device_info: Device information dict with bar_config
            output_dir: Output directory for cache

        Returns:
            Dict mapping BAR index to serialized BarModel, or None on failure
        """
        try:
            from src.device_clone.bar_model_synthesizer import (
                synthesize_bar_models,
            )

            bar_config = device_info.get("bar_config", {})
            bars = bar_config.get("bars", [])

            if not bars:
                log_info_safe(
                    self.logger, "No BARs found, skipping MMIO learning", prefix="MMIO"
                )
                return None

            cache_dir = output_dir / ".pcileech_cache"
            cache_dir.mkdir(parents=True, exist_ok=True)

            log_info_safe(
                self.logger,
                safe_format(
                    "Capturing MMIO traces for {bdf} (cache: {cache})",
                    bdf=self.bdf,
                    cache=cache_dir,
                ),
                prefix="MMIO",
            )

            bar_models = synthesize_bar_models(
                self.bdf,
                bars,
                cache_dir=cache_dir,
                force_recapture=self.force_recapture,
                logger=self.logger,
            )

            if bar_models:
                # Serialize models for JSON storage
                from src.device_clone.bar_model_loader import (
                    serialize_bar_model,
                )

                serialized = {}
                for bar_idx, model in bar_models.items():
                    serialized[bar_idx] = serialize_bar_model(model)

                log_info_safe(
                    self.logger,
                    safe_format(
                        "Collected BAR models for {count} BARs",
                        count=len(serialized),
                    ),
                    prefix="MMIO",
                )
                return serialized
            else:
                log_warning_safe(
                    self.logger,
                    "No BAR models captured (insufficient trace data)",
                    prefix="MMIO",
                )
                return None

        except PermissionError:
            log_warning_safe(
                self.logger,
                "MMIO learning requires root (bpftrace) - skipping",
                prefix="MMIO",
            )
            return None
        except Exception as e:
            log_warning_safe(
                self.logger,
                safe_format(
                    "MMIO learning failed (non-fatal): {error}",
                    error=str(e),
                ),
                prefix="MMIO",
            )
            return None

    def _save_collected_data(self, output_dir: Path, data: Dict[str, Any]) -> None:
        """Save collected device data to files for container consumption.

        Args:
            output_dir: Output directory
            data: Collected device data
        """
        output_dir.mkdir(parents=True, exist_ok=True)

        # Save complete device context
        context_file = output_dir / "device_context.json"
        with open(context_file, "w") as f:
            json.dump(data, f, indent=2)

        # Validate that the file was written correctly
        try:
            with open(context_file, "r") as f:
                test_data = json.load(f)
            test_hex = test_data.get("config_space_hex", "")
            if len(test_hex) != len(data["config_space_hex"]):
                log_error_safe(
                    self.logger,
                    safe_format(
                        "Config space hex corrupted: {orig} -> {saved} chars",
                        orig=len(data["config_space_hex"]),
                        saved=len(test_hex),
                    ),
                    prefix="HOST",
                )
        except Exception as e:
            log_warning_safe(
                self.logger,
                safe_format("Failed to validate saved context file: {err}", err=str(e)),
                prefix="HOST",
            )

        # Save MSI-X data separately for backward compatibility
        if data.get("msix_data"):
            msix_file = output_dir / "msix_data.json"
            msix_payload = {
                "bdf": data["bdf"],
                "msix_info": data["msix_data"]["msix_info"],
                "config_space_hex": data["config_space_hex"],
            }
            with open(msix_file, "w") as f:
                json.dump(msix_payload, f, indent=2)

            # Validate MSIX file as well
            try:
                with open(msix_file, "r") as f:
                    test_msix = json.load(f)
                test_msix_hex = test_msix.get("config_space_hex", "")
                if len(test_msix_hex) != len(data["config_space_hex"]):
                    log_error_safe(
                        self.logger,
                        safe_format(
                            "MSIX config space hex corrupted during save: "
                            "{orig} -> {saved} chars",
                            orig=len(data["config_space_hex"]),
                            saved=len(test_msix_hex),
                        ),
                        prefix="HOST",
                    )
            except Exception as e:
                log_warning_safe(
                    self.logger,
                    safe_format(
                        "Failed to validate saved MSIX file: {err}", err=str(e)
                    ),
                    prefix="HOST",
                )

        log_info_safe(
            self.logger,
            safe_format(
                "Host device data saved → {context_file}", context_file=context_file
            ),
            prefix="HOST",
        )
