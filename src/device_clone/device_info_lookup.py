#!/usr/bin/env python3
"""
Device Information Lookup Module

Provides resilient device information lookup using multiple data sources
including lspci, sysfs, and configuration space scraping.
"""

import json
import logging
import re
import subprocess
from pathlib import Path
from typing import Any, Dict, Optional

from src.device_clone.device_config import DeviceIdentification

# Import DeviceConfiguration first to avoid cyclic import
from src.device_clone.fallback_manager import get_global_fallback_manager
from src.string_utils import (
    log_debug_safe,
    log_error_safe,
    log_info_safe,
    log_warning_safe,
    safe_format,
)
from src.utils.validation_constants import CORE_DEVICE_ID_FIELDS, CORE_DEVICE_IDS

# Import config_space_manager dynamically when needed to avoid circular dependencies
logger = logging.getLogger(__name__)

# Use centralized core device ID fields; treat revision_id as optional for
# "critical" fast-path checks to preserve prior behavior.
critical_field_keys = [k for k in CORE_DEVICE_ID_FIELDS if k != "revision_id"]


class DeviceInfoLookup:
    """
    DRY device information lookup using device clone subsystem.
    """

    def __init__(self, bdf: str):
        self.bdf = bdf
        self.sysfs_path = Path(f"/sys/bus/pci/devices/{bdf}")
        self._cached_info: Optional[Dict[str, Any]] = None

    def _extract_device_info_if_needed(
        self, device_info: Dict[str, Any], from_config_manager: bool
    ) -> Dict[str, Any]:
        """Extract device info from config space if needed."""
        missing_critical_fields = not all(
            key in device_info and device_info[key] is not None
            for key in critical_field_keys
        )

        invalid_fields = any(
            key in device_info
            and (
                device_info[key] is None
                or (key in CORE_DEVICE_IDS and device_info[key] in [0, 0xFFFF])
            )
            for key in critical_field_keys
        )

        needs_extraction = (
            not from_config_manager or missing_critical_fields or invalid_fields
        )

        if not needs_extraction:
            return device_info

        log_debug_safe(
            logger,
            safe_format(
                "Extracting device info from config space "
                "(from_manager={from_mgr}, missing={missing}, invalid={invalid})",
                from_mgr=from_config_manager,
                missing=missing_critical_fields,
                invalid=invalid_fields,
            ),
            prefix="LOOKUP",
        )

        # Dynamically import ConfigSpaceManager to avoid circular dependency
        from src.device_clone.config_space_manager import ConfigSpaceManager

        manager = ConfigSpaceManager(self.bdf)
        try:
            config_space = manager.read_vfio_config_space()
            # Use internal methods to avoid recursion
            extracted_info = manager._extract_basic_device_info(config_space)
            subsystem_vendor, subsystem_device = manager._extract_subsystem_info(
                config_space
            )
            extracted_info["subsystem_vendor_id"] = subsystem_vendor
            extracted_info["subsystem_device_id"] = subsystem_device
            extracted_info["bars"] = manager._extract_bar_info(config_space)

            # Merge extracted info with existing info, prioritizing valid values
            device_info.update(
                {
                    k: v
                    for k, v in extracted_info.items()
                    if k not in device_info
                    or device_info[k] is None
                    or (k in CORE_DEVICE_IDS and device_info[k] in [0, 0xFFFF])
                }
            )
        except Exception as e:
            log_warning_safe(
                logger,
                safe_format(
                    "Failed to extract device info for {bdf}: {error}",
                    bdf=self.bdf,
                    error=str(e),
                ),
                prefix="LOOKUP",
            )

        return device_info

    def _log_pre_fallback_diagnostics(self, device_info: Dict[str, Any]) -> None:
        """Log diagnostic information before applying fallbacks."""
        try:

            def _is_sensitive(var_name: str) -> bool:
                try:
                    mgr = get_global_fallback_manager()
                    return mgr.is_sensitive_var(var_name)
                except Exception:
                    return False

            def _sanitize(ctx: Any, path: list) -> Any:
                if not isinstance(ctx, dict):
                    return ctx
                out: Dict[str, Any] = {}
                for k, v in ctx.items():
                    var_name = ".".join(path + [k]) if path else k
                    if _is_sensitive(var_name):
                        continue
                    if isinstance(v, dict):
                        out[k] = _sanitize(v, path + [k])
                    else:
                        try:
                            s = repr(v)
                        except Exception:
                            s = f"<{type(v).__name__}>"
                        if len(s) > 200:
                            s = s[:200] + "...<truncated>"
                        out[k] = s
                return out

            def _shape(ctx: Any) -> Any:
                if not isinstance(ctx, dict):
                    return type(ctx).__name__
                return {k: _shape(v) for k, v in ctx.items()}

            sanitized = _sanitize(device_info or {}, [])
            shape = _shape(sanitized)

            log_info_safe(
                logger,
                "Pre-fallback device_info (shape): {shape}",
                prefix="LOOKUP",
                shape=shape,
            )

            s = json.dumps(sanitized, indent=2, sort_keys=True)
            if len(s) > 4000:
                s = s[:4000] + "...<truncated>"

            log_info_safe(
                logger,
                "Pre-fallback device_info (sanitized): {snapshot}",
                prefix="LOOKUP",
                snapshot=s,
            )
        except Exception:
            log_warning_safe(
                logger,
                "Failed to emit pre-fallback diagnostic",
                prefix="LOOKUP",
            )

    def _validate_device_identification(self, device_info: Dict[str, Any]) -> None:
        """Validate device identification using DeviceIdentification."""
        try:
            # Convert values to integers in case they're strings

            def to_int(value):
                """Convert a value to int, accepting hex strings with or without 0x.

                Accept formats like '0x10ec', '10ec', or decimal '1234'.
                """
                if isinstance(value, str):
                    s = value.strip()
                    if s.startswith(("0x", "0X")):
                        return int(s, 16)
                    # Plain decimal
                    if re.match(r"^\d+$", s):
                        return int(s, 10)
                    # Hex digits without 0x prefix (e.g. '10ec')
                    if re.match(r"^[0-9A-Fa-f]+$", s):
                        return int(s, 16)
                    # Fallback to python auto-detect; if that fails try hex
                    try:
                        return int(s, 0)
                    except ValueError:
                        return int(s, 16)

                return int(value) if value else 0

            ident = DeviceIdentification(
                vendor_id=to_int(device_info.get("vendor_id", 0)),
                device_id=to_int(device_info.get("device_id", 0)),
                class_code=to_int(device_info.get("class_code", 0)),
                subsystem_vendor_id=to_int(device_info.get("subsystem_vendor_id", 0)),
                subsystem_device_id=to_int(device_info.get("subsystem_device_id", 0)),
            )
            ident.validate()
        except Exception as e:
            log_error_safe(
                logger,
                safe_format(
                    "Device identification validation failed for {bdf}: {error}",
                    bdf=self.bdf,
                    error=str(e),
                ),
                prefix="VALIDATE",
            )

    def get_complete_device_info(
        self,
        partial_info: Optional[Dict[str, Any]] = None,
        from_config_manager: bool = False,
    ) -> Dict[str, Any]:
        """
        Get complete device information using ConfigSpaceManager and FallbackManager.
        Args:
            partial_info: Partial device information to complete
            from_config_manager: Indicating if this call is from ConfigSpaceManager

        Returns:
            Complete device information dictionary
        """
        # Start with provided partial info or empty dict
        device_info = partial_info.copy() if partial_info else {}

        # Extract device info from config space if needed
        device_info = self._extract_device_info_if_needed(
            device_info, from_config_manager
        )

        # Log pre-fallback diagnostics
        self._log_pre_fallback_diagnostics(device_info)

        # Apply fallbacks for missing fields using the shared/global FallbackManager
        fallback_mgr = get_global_fallback_manager()
        device_info = fallback_mgr.apply_fallbacks(device_info)

        # Validate device identification
        self._validate_device_identification(device_info)

        self._cached_info = device_info
        return device_info

    # Legacy compatibility methods for tests

    def _has_required_fields(self, info: Dict[str, Any]) -> bool:
        """Check if device info has all required fields."""
        # Use core device identification fields
        required_fields = CORE_DEVICE_ID_FIELDS
        return all(
            field in info and info[field] is not None for field in required_fields
        )

    def _get_info_from_sysfs(self) -> Dict[str, Any]:
        """Get device info from sysfs files."""
        info = {}
        sysfs_files = {
            "vendor_id": "vendor",
            "device_id": "device",
            "class_code": "class",
            "revision_id": "revision",
            "subsystem_vendor_id": "subsystem_vendor",
            "subsystem_device_id": "subsystem_device",
        }

        for key, filename in sysfs_files.items():
            file_path = self.sysfs_path / filename
            if file_path.exists():
                try:
                    content = file_path.read_text().strip()
                    # Convert hex string to int
                    if content.startswith("0x"):
                        info[key] = int(content, 16)
                    else:
                        info[key] = int(content, 16)
                except (ValueError, OSError) as e:
                    log_warning_safe(
                        logger,
                        safe_format(
                            "Failed to read {file}: {error}",
                            file=str(file_path),
                            error=str(e),
                        ),
                        prefix="LOOKUP",
                    )

        return info

    def _get_info_from_lspci(self) -> Dict[str, Any]:
        """Get device info from lspci command."""
        try:
            result = subprocess.run(
                ["lspci", "-D", "-s", self.bdf, "-v"],
                capture_output=True,
                text=True,
                timeout=5,
            )

            if result.returncode != 0:
                return {}

            info = {}
            lines = result.stdout.split("\n")

            # Get the short BDF format for matching (e.g., "03:00.0" from "0000:03:00.0")
            short_bdf = self.bdf.split(":")[-2] + ":" + self.bdf.split(":")[-1]

            for line in lines:
                if line.startswith(self.bdf) or line.startswith(short_bdf):
                    # Parse main line: "03:00.0 Network controller [0280]: Intel Corporation [8086] Device [10d3] (rev 00)"
                    # Extract class code first
                    class_match = re.search(r"\[([0-9a-fA-F]+)\]:", line)
                    if class_match:
                        info["class_code"] = (
                            int(class_match.group(1), 16) << 8
                        )  # Shift for full class code

                    # Extract vendor and device IDs - look for pattern like "[8086] Device [10d3]"
                    vendor_device_match = re.search(
                        r"\[([0-9a-fA-F]{4})\] Device \[([0-9a-fA-F]+)\]", line
                    )
                    if vendor_device_match:
                        info["vendor_id"] = int(vendor_device_match.group(1), 16)
                        info["device_id"] = int(vendor_device_match.group(2), 16)

                    # Extract revision
                    rev_match = re.search(r"\(rev ([0-9a-fA-F]+)\)", line)
                    if rev_match:
                        info["revision_id"] = int(rev_match.group(1), 16)

                elif line.strip().startswith("Subsystem:"):
                    # Parse subsystem line: "\tSubsystem: Intel Corporation [8086] Device [a01f]"
                    subsys_match = re.search(
                        r"\[([0-9a-fA-F]{4})\] Device \[([0-9a-fA-F]+)\]", line
                    )
                    if subsys_match:
                        info["subsystem_vendor_id"] = int(subsys_match.group(1), 16)
                        info["subsystem_device_id"] = int(subsys_match.group(2), 16)

            return info

        except (subprocess.TimeoutExpired, subprocess.CalledProcessError, OSError):
            return {}

    def _get_info_from_config_space(self) -> Dict[str, Any]:
        """Get device info from config space file."""
        config_path = self.sysfs_path / "config"
        if not config_path.exists():
            return {}

        try:
            with open(config_path, "rb") as f:
                config_data = f.read(256)  # Read PCI config space header

            if len(config_data) < 4:
                return {}

            # Extract vendor and device ID from first 4 bytes
            vendor_id = int.from_bytes(config_data[0:2], byteorder="little")
            device_id = int.from_bytes(config_data[2:4], byteorder="little")

            info = {
                "vendor_id": vendor_id,
                "device_id": device_id,
            }

            # Extract additional fields if available
            if len(config_data) >= 12:
                class_code = int.from_bytes(config_data[9:12], byteorder="little")
                info["class_code"] = class_code

            if len(config_data) >= 8:
                revision_id = config_data[8]
                info["revision_id"] = revision_id

            return info

        except (OSError, IOError):
            return {}

    def _merge_device_info(
        self, base: Dict[str, Any], new: Dict[str, Any]
    ) -> Dict[str, Any]:
        """Merge device information, preferring valid values."""
        merged = base.copy()

        for key, value in new.items():
            # Only update if base doesn't have the key, has None, or has invalid value
            if (
                key not in merged
                or merged[key] is None
                or (key in CORE_DEVICE_IDS and merged[key] in [0x0000, 0xFFFF])
            ):
                merged[key] = value

        return merged

    def _apply_intelligent_defaults(self, info: Dict[str, Any]) -> Dict[str, Any]:
        """Apply intelligent defaults for missing device information."""
        result = info.copy()

        # Default subsystem IDs to main IDs if missing
        if "subsystem_vendor_id" not in result or result["subsystem_vendor_id"] is None:
            result["subsystem_vendor_id"] = result.get("vendor_id", 0)
        if "subsystem_device_id" not in result or result["subsystem_device_id"] is None:
            result["subsystem_device_id"] = result.get("device_id", 0)

        # Default revision to 0x00 if missing
        if "revision_id" not in result or result["revision_id"] is None:
            result["revision_id"] = 0x00

        # Default class code to generic if missing
        if "class_code" not in result or result["class_code"] is None:
            result["class_code"] = 0x088000  # Generic system peripheral

        return result


def lookup_device_info(
    bdf: str,
    partial_info: Optional[Dict[str, Any]] = None,
    from_config_manager: bool = False,
) -> Dict[str, Any]:
    """
    Convenience function to lookup device information using DRY subsystem.
    Args:
        bdf: Bus:Device.Function identifier
        partial_info: Partial device information to complete
        from_config_manager: Flag indicating if this call originated from ConfigSpaceManager
                             to prevent recursion
    Returns:
        Complete device information dictionary
    """
    lookup = DeviceInfoLookup(bdf)
    return lookup.get_complete_device_info(partial_info, from_config_manager)
