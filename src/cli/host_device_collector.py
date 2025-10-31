#!/usr/bin/env python3
"""Host Device Collector - Orchestrates device info collection before launch.

This module coordinates existing collection components to gather all device
information in a single VFIO binding session on the host, eliminating the need
for VFIO operations inside the container.
"""

import json
import logging
import os
import time

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

from src.device_clone.pcileech_context import (
    PCILeechContextBuilder,
    ValidationLevel,
)

from src.build import MSIXManager, MSIXData

from src.cli.vfio_handler import VFIOBinder

from src.exceptions import BuildError


class HostDeviceCollector:
    """Collects all device information on the host before container launch."""
    
    def __init__(self, bdf: str, logger: Optional[logging.Logger] = None):
        """Initialize the collector.
        
        Args:
            bdf: PCI Bus/Device/Function identifier
            logger: Optional logger instance
        """
        self.bdf = bdf
        self.logger = logger or logging.getLogger(__name__)
        
    def collect_device_context(self, output_dir: Path) -> Dict[str, Any]:
        """Collect complete device context using existing infrastructure.
        
        This method orchestrates the existing collection components:
        - ConfigSpaceManager for VFIO config space reading
        - DeviceInfoLookup for device information extraction  
        - PCILeechContextBuilder for comprehensive context building
        - MSIXManager for MSI-X capability data
        
        Args:
            output_dir: Directory to save collected data
            
        Returns:
            Complete device context dictionary
            
        Raises:
            BuildError: If critical device information cannot be collected
        """
        log_info_safe(
            self.logger,
            "Collecting complete device context on host",
            prefix="HOST"
        )
        
        # Use single VFIO binding session to collect all data
        with VFIOBinder(self.bdf, attach=True) as binder:
            try:
                # 1. Use existing ConfigSpaceManager for VFIO config space reading
                config_manager = ConfigSpaceManager(self.bdf, strict_vfio=True)
                config_space_bytes = config_manager.read_vfio_config_space()
                
                log_info_safe(
                    self.logger,
                    safe_format(
                        "Read {size} bytes of config space via VFIO",
                        size=len(config_space_bytes)
                    ),
                    prefix="HOST"
                )
                
                # 2. Extract device info using existing DeviceInfoLookup
                device_lookup = DeviceInfoLookup(self.bdf)
                extracted_info = config_manager.extract_device_info(
                    config_space_bytes
                )
                device_info = device_lookup.get_device_info(extracted_info)
                
                # 3. Use existing MSIXManager for MSI-X data collection
                msix_manager = MSIXManager(self.bdf, self.logger)
                msix_data = self._collect_msix_data_vfio(
                    msix_manager, config_space_bytes
                )
                
                # 4. Use existing PCILeechContextBuilder for comprehensive context
                context_builder = PCILeechContextBuilder(
                    device_bdf=self.bdf,
                    validation_level=ValidationLevel.PERMISSIVE,
                    logger=self.logger
                )
                
                # Build comprehensive context using existing infrastructure
                config_space_data = {
                    "raw_config_space": config_space_bytes,
                    "config_space_hex": config_space_bytes.hex(),
                    "device_info": device_info,
                    "vendor_id": format(device_info.get("vendor_id", 0), "04x"),
                    "device_id": format(device_info.get("device_id", 0), "04x"),
                    "class_code": format(device_info.get("class_code", 0), "06x"),
                    "revision_id": format(device_info.get("revision_id", 0), "02x"),
                    "bars": device_info.get("bars", []),
                    "config_space_size": len(config_space_bytes),
                }
                
                # Build full template context
                template_context = context_builder.build_context(
                    config_space_data=config_space_data,
                    behavior_profile=None,  # Collected in container if needed
                    msix_data=msix_data,
                    timing_params=None
                )
                
                # 5. Save collected data for container consumption
                collected_data = {
                    "bdf": self.bdf,
                    "config_space_hex": config_space_bytes.hex(),
                    "device_info": device_info,
                    "msix_data": (
                        msix_data._asdict() if msix_data.preloaded else None
                    ),
                    "template_context": template_context,
                    "collection_metadata": {
                        "collected_at": time.time(),
                        "config_space_size": len(config_space_bytes),
                        "has_msix": msix_data.preloaded,
                        "collector_version": "1.0"
                    }
                }
                self._save_collected_data(output_dir, collected_data)
                
                log_info_safe(
                    self.logger,
                    safe_format(
                        "Device context collected and saved to {output}",
                        output=output_dir
                    ),
                    prefix="HOST"
                )
                
                return template_context
                
            except Exception as e:
                log_error_safe(
                    self.logger,
                    safe_format(
                        "Failed to collect device context: {error}",
                        error=str(e)
                    ),
                    prefix="HOST"
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
            from src.device_clone.msix_capability import parse_msix_capability
            
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
                        pba_offset=msix_info.get("pba_offset", 0)
                    ),
                    prefix="MSIX"
                )
                
                return MSIXData(
                    preloaded=True,
                    msix_info=msix_info,
                    config_space_hex=config_space_hex,
                    config_space_bytes=config_space_bytes,
                )
            else:
                log_info_safe(
                    self.logger,
                    "No MSI-X capability found in device",
                    prefix="MSIX"
                )
                return MSIXData(preloaded=False)
                
        except Exception as e:
            log_warning_safe(
                self.logger,
                safe_format(
                    "MSI-X collection failed (non-fatal): {error}",
                    error=str(e)
                ),
                prefix="MSIX"
            )
            return MSIXData(preloaded=False)
    
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
            json.dump(data, f, indent=2, default=str)
        
        # Save MSI-X data separately for backward compatibility
        if data.get("msix_data"):
            msix_file = output_dir / "msix_data.json"
            msix_payload = {
                "bdf": data["bdf"],
                "msix_info": data["msix_data"]["msix_info"],
                "config_space_hex": data["config_space_hex"]
            }
            with open(msix_file, "w") as f:
                json.dump(msix_payload, f, indent=2)
        
        log_info_safe(
            self.logger,
            safe_format(
                "Host device data saved → {context_file}",
                context_file=context_file
            ),
            prefix="HOST"
        )
