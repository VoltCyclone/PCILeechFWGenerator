#!/usr/bin/env python3
from __future__ import annotations

"""
PCILeech Template Context Builder

Builds comprehensive template context from device profiling data.
Integrates BehaviorProfiler, ConfigSpaceManager, and MSIXCapability data.

"""

import ctypes

import fcntl

import logging

import os

from dataclasses import asdict, dataclass, field, fields

from enum import Enum

from pathlib import Path

from typing import Any, Dict, Optional, Tuple, TypedDict, Union, cast

from src.cli.vfio_constants import (
    VFIO_DEVICE_GET_REGION_INFO,
    VFIO_REGION_INFO_FLAG_MMAP,
    VFIO_REGION_INFO_FLAG_READ,
    VFIO_REGION_INFO_FLAG_WRITE,
    VfioRegionInfo,
)

from src.device_clone.bar_content_generator import BarContentGenerator

from src.device_clone.bar_size_converter import extract_bar_size

from src.device_clone.behavior_profiler import BehaviorProfile

from src.device_clone.board_config import get_pcileech_board_config

from src.device_clone.config_space_manager import BarInfo, ConfigSpaceConstants

from src.device_clone.constants import (
    BAR_SIZE_CONSTANTS,
    BAR_TYPE_MEMORY_64BIT,
    DEFAULT_CLASS_CODE,
    DEFAULT_EXT_CFG_CAP_PTR,
    DEFAULT_REVISION_ID,
    DEVICE_ID_FALLBACK,
    MAX_32BIT_VALUE,
    PCI_CLASS_AUDIO,
    PCI_CLASS_DISPLAY,
    PCI_CLASS_NETWORK,
    PCI_CLASS_STORAGE,
    POWER_STATE_D0,
)
from src.device_clone.device_config import get_device_config

from src.device_clone.fallback_manager import (
    FallbackManager,
    get_global_fallback_manager,
)

from src.device_clone.identifier_normalizer import IdentifierNormalizer

from src.device_clone.overlay_mapper import OverlayMapper

from src.device_clone.overlay_utils import (
    compute_sparse_hash_table_size,
    normalize_overlay_entry_count,
)

from src.error_utils import extract_root_cause

from src.exceptions import ContextError

from src.pci_capability.constants import PCI_CONFIG_SPACE_MIN_SIZE

from src.string_utils import (
    log_debug_safe,
    log_error_safe,
    log_info_safe,
    log_warning_safe,
    safe_format,
)

from ..utils.validation_constants import (
    CORE_DEVICE_ID_FIELDS,
    CORE_DEVICE_IDS,
    REQUIRED_CONTEXT_SECTIONS,
)

def require(condition: bool, message: str, **context) -> None:
    """Validate condition or exit with error."""
    if not condition:
        logger = logging.getLogger(__name__)
        log_error_safe(
            logger,
            safe_format("Build aborted: {msg} | ctx={ctx}", msg=message, ctx=context),
            prefix="PCIL",
        )
        raise SystemExit(2)


from src.utils.unified_context import (
    TemplateObject,
    UnifiedContextBuilder,
    ensure_template_compatibility,
)

from src.utils.validation_constants import SV_FILE_HEADER

_MSIX_BASE_RUNTIME_FLAGS: Dict[str, Any] = {
    # Clear table & PBA memories on reset
    "reset_clear": True,
    # Honor byte enables on writes to table structures
    "use_byte_enables": True,
    # Staging + atomic commit design indicators consumed by SV templates
    "supports_staging": True,
    "supports_atomic_commit": True,
    # Entry sizing metadata (16B entries = 4 dwords)
    "table_entry_dwords": 4,
    "entry_size_bytes": 16,
}


def _build_msix_disabled_runtime_flags() -> Dict[str, Any]:
    """Return runtime flag set for a disabled / unsupported MSI-X capability.

    Always returns a fresh dict (avoid accidental shared mutations).
    """
    return {
        **_MSIX_BASE_RUNTIME_FLAGS,
        # Capability-level state flags
        "function_mask": False,
        # PBA writes remain ignored while disabled
        "write_pba_allowed": False,
        # Zero PBA storage requirement
        "pba_size_dwords": 0,
    }


def _build_msix_enabled_runtime_flags(
    pba_size_dwords: int,
    *,
    function_mask: bool = False,
    write_pba_allowed: bool = False,
) -> Dict[str, Any]:
    """Return runtime flag set for an enabled MSI-X capability.

    Parameters
    ----------
    pba_size_dwords: int
        Computed dword length of PBA storage
    function_mask: bool
        Current function mask state from capability
    write_pba_allowed: bool
        Whether template logic should permit PBA writes (kept False for
        spec-compliant read-only behavior unless explicitly changed later)
    """
    return {
        **_MSIX_BASE_RUNTIME_FLAGS,
        "function_mask": function_mask,
        "write_pba_allowed": write_pba_allowed,
        "pba_size_dwords": pba_size_dwords,
    }


class TemplateContext(TypedDict, total=False):
    """Template context structure."""

    vendor_id: str
    device_id: str
    device_signature: str
    generation_metadata: Dict[str, Any]
    device_config: Dict[str, Any]
    config_space: Dict[str, Any]
    msix_config: Dict[str, Any]
    msix_data: Optional[Dict[str, Any]]
    interrupt_config: Dict[str, Any]
    active_device_config: Dict[str, Any]
    bar_config: Dict[str, Any]
    timing_config: Dict[str, Any]
    pcileech_config: Dict[str, Any]
    board_config: Dict[str, Any]
    # Donor-derived PCIe link capabilities (codes)
    pcie_max_link_speed: int
    pcie_max_link_width: int
    EXT_CFG_CAP_PTR: int
    EXT_CFG_XP_CAP_PTR: int
    # Optional VFIO availability/verification flags for integration rendering
    vfio_device: bool
    vfio_binding_verified: bool


@dataclass(slots=True)
class DeviceIdentifiers:
    """Device identification data (uses centralized normalization)."""

    vendor_id: str
    device_id: str
    class_code: str
    revision_id: str
    subsystem_vendor_id: Optional[str] = None
    subsystem_device_id: Optional[str] = None

    def __post_init__(self):
        try:
            norm = IdentifierNormalizer.validate_all_identifiers(
                {
                    "vendor_id": self.vendor_id,
                    "device_id": self.device_id,
                    "class_code": self.class_code,
                    "revision_id": self.revision_id,
                    "subsystem_vendor_id": self.subsystem_vendor_id,
                    "subsystem_device_id": self.subsystem_device_id,
                }
            )
        except ContextError:
            # Preserve original validation error details without rewriting
            raise
        self.vendor_id = norm["vendor_id"]
        self.device_id = norm["device_id"]
        self.class_code = norm["class_code"]
        self.revision_id = norm["revision_id"]
        self.subsystem_vendor_id = norm["subsystem_vendor_id"]
        self.subsystem_device_id = norm["subsystem_device_id"]

    @property
    def device_signature(self) -> str:
        return safe_format(
            "{vendor}:{device}",
            vendor=self.vendor_id,
            device=self.device_id,
        )

    @property
    def full_signature(self) -> str:
        subsys_vendor = self.subsystem_vendor_id or self.vendor_id
        subsys_device = self.subsystem_device_id or self.device_id
        return safe_format(
            "{vendor}:{device}:{subsys_vendor}:{subsys_device}",
            vendor=self.vendor_id,
            device=self.device_id,
            subsys_vendor=subsys_vendor,
            subsys_device=subsys_device,
        )

    def get_device_class_type(self) -> str:
        class_map = {
            PCI_CLASS_NETWORK: "Network Controller",
            PCI_CLASS_STORAGE: "Storage Controller",
            PCI_CLASS_DISPLAY: "Display Controller",
            PCI_CLASS_AUDIO: "Audio Controller",
        }
        return class_map.get(self.class_code[:2], "Unknown Device")


class ValidationLevel(Enum):
    """Validation strictness levels."""

    STRICT = "strict"
    MODERATE = "moderate"
    PERMISSIVE = "permissive"


@dataclass(slots=True)
class BarConfiguration:
    """BAR configuration data."""

    index: int
    base_address: int
    size: int
    bar_type: int
    prefetchable: bool
    is_memory: bool
    is_io: bool
    is_64bit: bool = field(default=False)
    _size_encoding: Optional[int] = field(default=None, init=False, repr=False)

    def __post_init__(self):
        """Validate BAR configuration with aggregated errors and clear guidance."""
        issues = []
        if not 0 <= self.index < ConfigSpaceConstants.MAX_BARS:
            issues.append(
                safe_format(
                    "Invalid BAR index: {index} (allowed 0..{max_idx})",
                    index=self.index,
                    max_idx=ConfigSpaceConstants.MAX_BARS - 1,
                )
            )
        # BAR size must be a positive 32-bit unsigned value
        if self.size <= 0:
            issues.append(
                safe_format(
                    "Invalid BAR size: {size} (must be > 0)", size=self.size
                )
            )
        if self.size > MAX_32BIT_VALUE:
            issues.append(
                safe_format(
                    "Invalid BAR size: {size} (exceeds 32-bit limit {limit})",
                    size=self.size,
                    limit=MAX_32BIT_VALUE,
                )
            )

        if issues:
            raise ContextError(
                safe_format(
                    "Invalid BAR configuration | index={index} size={size} | "
                    "issues={issues}",
                    index=self.index,
                    size=self.size,
                    issues=issues,
                )
            )

        if self.is_memory and self.bar_type == BAR_TYPE_MEMORY_64BIT:
            self.is_64bit = True

    def get_size_encoding(self) -> int:
        """Get size encoding for this BAR."""
        if self._size_encoding is None:
            from src.device_clone.bar_size_converter import BarSizeConverter

            bar_type_str = "io" if self.is_io else "memory"
            self._size_encoding = BarSizeConverter.size_to_encoding(
                self.size, bar_type_str, self.is_64bit, self.prefetchable
            )
        return self._size_encoding

    @property
    def size_mb(self) -> float:
        """Get BAR size in MB."""
        return self.size / (1024 * 1024)

    @property
    def type_description(self) -> str:
        """Get BAR type description."""
        if self.is_io:
            return "I/O"
        return "64-bit Memory" if self.is_64bit else "32-bit Memory"


@dataclass(slots=True)
class TimingParameters:
    """Device timing parameters."""

    read_latency: int
    write_latency: int
    burst_length: int
    inter_burst_gap: int
    timeout_cycles: int
    clock_frequency_mhz: float
    timing_regularity: float

    def __post_init__(self):
        """Validate timing parameters."""
        for field_obj in fields(self):
            field_value = getattr(self, field_obj.name)
            if field_value is None:
                raise ContextError(
                    safe_format("{field} cannot be None", field=field_obj.name)
                )
            if field_value <= 0:
                raise ContextError(
                    safe_format(
                        "{field} must be positive: {value}",
                        field=field_obj.name,
                        value=field_value,
                    )
                )

        if not 0 < self.timing_regularity <= 1.0:
            raise ContextError(
                safe_format(
                    "Invalid timing_regularity: {timing_regularity}",
                    timing_regularity=self.timing_regularity,
                )
            )

    @property
    def total_latency(self) -> int:
        """Calculate total latency."""
        return self.read_latency + self.write_latency

    @property
    def effective_bandwidth_mbps(self) -> float:
        """Estimate bandwidth in MB/s."""
        cycles_per_burst = self.burst_length + self.inter_burst_gap
        bursts_per_second = (self.clock_frequency_mhz * 1e6) / cycles_per_burst
        bytes_per_burst = self.burst_length * 4  # 32-bit transfers
        return (bursts_per_second * bytes_per_burst) / 1e6

    def to_dict(self) -> Dict[str, Any]:
        """Convert to dictionary."""
        context_dict = asdict(self)
        context_dict.update(
            {
                "total_latency": self.total_latency,
                "effective_bandwidth_mbps": self.effective_bandwidth_mbps,
            }
        )
        return context_dict


class VFIODeviceManager:
    """Manages VFIO device operations."""

    def __init__(self, device_bdf: str, logger: logging.Logger):
        self.device_bdf = device_bdf
        self.logger = logger
        self._device_fd: Optional[int] = None
        self._container_fd: Optional[int] = None

    def __enter__(self):
        return self

    def __exit__(self, _exc_type, _exc_val, _exc_tb):
        self.close()

    def open(self) -> Tuple[int, int]:
        """Open VFIO device and container FDs."""
        if self._device_fd is not None and self._container_fd is not None:
            return self._device_fd, self._container_fd

        try:
            # Open device FDs first. Tests commonly patch `get_device_fd` so
            # calling it before a strict VFIO precheck allows unit tests to
            # control the returned fds without requiring a real VFIO device.
            # Late import so unit tests that patch src.cli.vfio_helpers.get_device_fd
            # are effective.
            import src.cli.vfio_helpers as vfio_helpers

            self._device_fd, self._container_fd = vfio_helpers.get_device_fd(
                self.device_bdf
            )

            # Treat negative FDs as unavailable; normalize to None so callers
            # can fall back to sysfs-based logic without crashing.
            if (self._device_fd is not None and self._device_fd < 0) or (
                self._container_fd is not None and self._container_fd < 0
            ):
                log_debug_safe(
                    self.logger,
                    "VFIO FDs unavailable; using sysfs fallback",
                    prefix="VFIO",
                )
                self._device_fd = None
                self._container_fd = None

            # Attempt to ensure VFIO binding and prerequisites. Do not make
            # this fatal here; log a warning if the check fails so callers can
            # decide whether to treat it as an error. This preserves the
            # original safety check while keeping unit tests hermetic.
            try:
                # Late import so patches on src.cli.vfio_helpers.ensure_device_vfio_binding
                # are honored by tests.
                group = vfio_helpers.ensure_device_vfio_binding(self.device_bdf)
                log_info_safe(
                    self.logger,
                    safe_format(
                        "Device {device} bound to VFIO group {group}",
                        device=self.device_bdf,
                        group=group,
                    ),
                    prefix="VFIO",
                )
            except Exception as e:
                # Log non-fatally; higher-level callers may re-run the check
                # and decide to abort the operation if required.
                log_debug_safe(
                    self.logger,
                    safe_format(
                        "VFIO binding check: {error}; using sysfs fallback", error=e
                    ),
                    prefix="VFIO",
                )

            return self._device_fd, self._container_fd
        except Exception as e:
            log_error_safe(
                self.logger,
                safe_format("Failed to open VFIO device: {error}", error=e),
                prefix="VFIO",
            )
            raise

    def close(self):
        """Close VFIO file descriptors."""
        for fd in [self._device_fd, self._container_fd]:
            if fd is not None and fd >= 0:
                try:
                    os.close(fd)
                except OSError as e:
                    log_warning_safe(
                        self.logger,
                        safe_format("Failed to close VFIO FD: {error}", error=e),
                        prefix="VFIO",
                    )
                    pass
        self._device_fd = None
        self._container_fd = None

    def get_region_info(self, index: int) -> Optional[Dict[str, Any]]:
        """Get VFIO region information (with transient retry)."""
        opened_here = self._device_fd is None
        if opened_here:
            try:
                self.open()
            except (OSError, PermissionError, FileNotFoundError) as e:
                log_error_safe(
                    self.logger,
                    safe_format("VFIO device open failed: {error}", error=e),
                    prefix="VFIO",
                )
                return None
            except Exception as e:
                # Catch any other open() failures and return None for fallback
                log_warning_safe(
                    self.logger,
                    safe_format("VFIO device open failed (fallback): {error}", error=e),
                    prefix="VFIO",
                )
                return None
            # After attempting open, ensure the FD is valid
            if (self._device_fd is None) or (
                isinstance(self._device_fd, int) and self._device_fd < 0
            ):
                return None

        info = VfioRegionInfo()
        info.argsz = ctypes.sizeof(VfioRegionInfo)
        info.index = index

        try:
            if self._device_fd is None:
                # Return None to allow fallback path instead of raising
                log_debug_safe(
                    self.logger,
                    "Device FD not available, returning None for fallback",
                    prefix="VFIO",
                )
                return None

            from src.utils.vfio_retry import retry_vfio_ioctl

            def _do_ioctl():
                # self._device_fd is validated above; inline assert for type checkers
                assert self._device_fd is not None
                return fcntl.ioctl(
                    self._device_fd, VFIO_DEVICE_GET_REGION_INFO, info, True
                )

            retry_vfio_ioctl(_do_ioctl, label="vfio-region-info", logger=self.logger)

            result = {
                "index": info.index,
                "flags": info.flags,
                "size": info.size,
                "readable": bool(info.flags & VFIO_REGION_INFO_FLAG_READ),
                "writable": bool(info.flags & VFIO_REGION_INFO_FLAG_WRITE),
                "mappable": bool(info.flags & VFIO_REGION_INFO_FLAG_MMAP),
            }

            # Clean up if we opened the FDs here
            if opened_here:
                self.close()

            return result
        except OSError as e:
            log_error_safe(
                self.logger,
                safe_format("VFIO region info failed: {error}", error=e),
                prefix="VFIO",
            )
            # Clean up if we opened the FDs here
            if opened_here:
                self.close()
            return None

    def read_region_slice(self, index: int, offset: int, size: int) -> Optional[bytes]:
        """Read a slice of a VFIO region safely using mmap (with transient retry).

        This handles page alignment requirements by mapping a page-aligned range
        and slicing out the requested bytes.

        Args:
            index: VFIO region index (BAR index for BARs)
            offset: Offset within the region to start reading
            size: Number of bytes to read

        Returns:
            Bytes read or None on error
        """
        import mmap

        import os

        if size <= 0:
            return b""

        opened_here = self._device_fd is None
        if opened_here:
            try:
                self.open()
            except Exception as e:
                log_error_safe(
                    self.logger,
                    safe_format(
                        "VFIO device open failed: {error}",
                        error=e,
                    ),
                    prefix="VFIO",
                )
                return None

        try:
            # Query full region info to get the kernel-provided mmap offset
            info = VfioRegionInfo()
            info.argsz = ctypes.sizeof(VfioRegionInfo)
            info.index = index
            if self._device_fd is None:
                # Return None to allow fallback path instead of raising
                log_debug_safe(
                    self.logger,
                    "Device FD not available for region slice, returning None",
                    prefix="VFIO",
                )
                if opened_here:
                    self.close()
                return None
            from src.utils.vfio_retry import retry_vfio_ioctl

            def _do_ioctl():
                assert self._device_fd is not None
                return fcntl.ioctl(
                    self._device_fd, VFIO_DEVICE_GET_REGION_INFO, info, True
                )

            retry_vfio_ioctl(_do_ioctl, label="vfio-region-info", logger=self.logger)

            region_size = int(info.size)
            region_off = int(info.offset)
            region_flags = int(info.flags)

            if offset < 0 or offset >= region_size:
                log_error_safe(
                    self.logger,
                    "Region {index} offset out of range: {offset} (size {size})",
                    index=index,
                    offset=offset,
                    size=region_size,
                    prefix="VFIO",
                )
                return None

            # Clamp size to region bounds
            read_len = min(size, region_size - offset)

            # Verify the region is mappable before attempting mmap
            if not (region_flags & VFIO_REGION_INFO_FLAG_MMAP):
                log_warning_safe(
                    self.logger,
                    safe_format(
                        "Region {index} is not mappable (flags={flags}); cannot mmap",
                        index=index,
                        flags=region_flags,
                    ),
                    prefix="VFIO",
                )
                return None

            # Compute page-aligned mapping window using portable page size
            # Prefer mmap.PAGESIZE, then resource.getpagesize(), then os.sysconf
            try:
                page_sz = mmap.PAGESIZE  # type: ignore[attr-defined]
            except Exception:
                try:
                    import resource  # noqa: WPS433 (local import by design)

                    page_sz = resource.getpagesize()
                except Exception:
                    page_sz = (
                        os.sysconf("SC_PAGESIZE") if hasattr(os, "sysconf") else 4096
                    )
            abs_off = region_off + offset
            map_off = (abs_off // page_sz) * page_sz
            delta = abs_off - map_off
            map_len = ((delta + read_len + page_sz - 1) // page_sz) * page_sz

            with mmap.mmap(
                self._device_fd, map_len, offset=map_off, access=mmap.ACCESS_READ
            ) as mm:
                return bytes(mm[delta : delta + read_len])
        except OSError as e:
            log_error_safe(
                self.logger,
                safe_format(
                    "VFIO read_region_slice failed: {error}",
                    error=e,
                ),
                prefix="VFIO",
            )
            return None
        finally:
            if opened_here:
                self.close()


class PCILeechContextBuilder:
    """Builds template context from device profiling data - Optimized."""

    # Required MSI-X fields for validation
    # Device fields use DEVICE_IDENTIFICATION_FIELDS from validation_constants
    REQUIRED_MSIX_FIELDS = ["table_size", "table_bir", "table_offset"]

    def __init__(
        self,
        device_bdf: str,
        config: Any,
        validation_level: ValidationLevel = ValidationLevel.STRICT,
        fallback_manager: Optional[FallbackManager] = None,
    ):
        """Initialize context builder."""
        if not device_bdf or not device_bdf.strip():
            raise ContextError("Device BDF cannot be empty")

        self.device_bdf = device_bdf.strip()
        self.config = config
        self.validation_level = validation_level
        self.logger = logging.getLogger(__name__)
        self._context_cache: Dict[str, Any] = {}
        self._vfio_manager = VFIODeviceManager(self.device_bdf, self.logger)
        if fallback_manager:
            self.fallback_manager = fallback_manager
        else:
            self.fallback_manager = get_global_fallback_manager(
                config_path=None, mode="prompt", allowed_fallbacks=["bar-analysis"]
            )

    def build_context(
        self,
        behavior_profile: Optional[BehaviorProfile],
        config_space_data: Dict[str, Any],
        msix_data: Optional[Dict[str, Any]],
        interrupt_strategy: str = "intx",
        interrupt_vectors: int = 1,
        donor_template: Optional[Dict[str, Any]] = None,
        enable_mmio_learning: bool = True,
        force_recapture: bool = False,
    ) -> TemplateContext:
        """Build comprehensive template context.
        
        Args:
            behavior_profile: Optional behavior profile
            config_space_data: Device configuration space data
            msix_data: Optional MSI-X capability data
            interrupt_strategy: Interrupt strategy to use
            interrupt_vectors: Number of interrupt vectors
            donor_template: Optional donor template data
            enable_mmio_learning: Enable automatic MMIO trace capture (default: True)
            force_recapture: Force recapture of MMIO traces even if cached (default: False)
        """
        log_info_safe(
            self.logger,
            safe_format(
                "Building context for {device_bdf} with {strategy}",
                device_bdf=self.device_bdf,
                strategy=interrupt_strategy,
            ),
        )

        def handle_error(msg, exc=None):
            root_cause = extract_root_cause(exc) if exc else None
            error_message = (
                safe_format(
                    "{message}: {root_cause}", message=msg, root_cause=root_cause
                )
                if root_cause
                else msg
            )
            log_error_safe(
                self.logger,
                error_message,
                prefix="PCIL",
            )
            raise ContextError(msg, root_cause=root_cause)

        try:
            self._validate_input_data(config_space_data, msix_data, behavior_profile)
        except Exception as e:
            handle_error("Input validation failed", e)

        try:
            device_identifiers = self._extract_device_identifiers(config_space_data)
        except Exception as e:
            handle_error("Device identifier extraction failed", e)

        try:
            context = self._build_context_sections(
                device_identifiers,
                behavior_profile,
                config_space_data,
                msix_data,
                interrupt_strategy,
                interrupt_vectors,
                donor_template,
                enable_mmio_learning,
                force_recapture,
            )
        except Exception as e:
            handle_error("Context section build failed", e)

        try:
            self._finalize_context(context)
        except Exception as e:
            handle_error("Context finalization failed", e)

        log_info_safe(
            self.logger,
            safe_format(
                "Context built successfully: {sig}",
                sig=context.get("device_signature", "unknown"),
            ),
            prefix="PCIL",
        )
        return context

    def _build_context_sections(
        self,
        device_identifiers,
        behavior_profile,
        config_space_data,
        msix_data,
        interrupt_strategy,
        interrupt_vectors,
        donor_template,
        enable_mmio_learning=True,
        force_recapture=False,
    ):
        """Build all context sections with minimal nesting."""
        context: TemplateContext = {
            "device_config": self._build_device_config(
                device_identifiers, behavior_profile, config_space_data
            ),
            "config_space": self._build_config_space_context(config_space_data),
            "msix_config": self._build_msix_context(msix_data),
            "msix_data": msix_data,  # Raw MSI-X data for SystemVerilog generator
            "bar_config": self._build_bar_config(
                config_space_data,
                behavior_profile,
                enable_mmio_learning,
                force_recapture,
            ),
            "timing_config": self._build_timing_config(
                behavior_profile, device_identifiers
            ).to_dict(),
            "pcileech_config": self._build_pcileech_config(device_identifiers),
            "interrupt_config": {
                "strategy": interrupt_strategy,
                "vectors": interrupt_vectors,
                "msix_available": msix_data is not None,
            },
            "active_device_config": self._build_active_device_config(
                device_identifiers, interrupt_strategy, interrupt_vectors
            ),
            "board_config": self._build_board_config(),
            "device_signature": self._generate_device_signature(
                device_identifiers, behavior_profile, config_space_data
            ),
            "generation_metadata": self._build_generation_metadata(device_identifiers),
            "vendor_id": device_identifiers.vendor_id,
            "device_id": device_identifiers.device_id,
        }

        # Expose critical board attributes at the top level to keep downstream
        # generators firmly non-interactive and ensure constraint builders can
        # resolve donor-specific XDC files.
        board_config = context["board_config"]

        def _board_attr(attr: str, default: Any = None) -> Any:
            """Safely extract attributes from the board configuration."""
            try:
                value = getattr(board_config, attr)
            except AttributeError:
                value = board_config.get(attr) if isinstance(board_config, dict) else None

            return default if value is None else value

        context["board_name"] = _board_attr("name", "")
        context["fpga_part"] = _board_attr("fpga_part")
        context["fpga_family"] = _board_attr("fpga_family")
        context["pcie_ip_type"] = _board_attr("pcie_ip_type")
        context["max_lanes"] = _board_attr("max_lanes", 1)
        context["supports_msi"] = _board_attr("supports_msi", False)
        context["supports_msix"] = _board_attr("supports_msix", False)
        context["board_constraints"] = _board_attr("constraints", TemplateObject({}))
        context["board_xdc_content"] = _board_attr("board_xdc_content", "")
        context.setdefault("sys_clk_freq_mhz", _board_attr("sys_clk_freq_mhz"))

        # Attempt to extract PCIe max link speed/width from config space for donor-uniqueness.
        # These are required by the TCL builder to derive target_link_speed/width enums.
        try:
            cfg_hex = config_space_data.get("config_space_hex")
            if not cfg_hex and isinstance(context.get("config_space"), dict):
                cfg_hex = context["config_space"].get("raw_data")

            if cfg_hex:
                try:
                    # Prefer centralized capability processor to avoid duplicating parsing logic
                    from src.pci_capability import (
                        core as _pcicore,
                        processor as _pciproc,
                    )

                    _cs = _pcicore.ConfigSpace(cfg_hex)
                    _cp = _pciproc.CapabilityProcessor(_cs)
                    # Use the processor's device context view to read derived fields
                    dev_ctx = (
                        _cp._get_device_context()
                    )  # Internal, but stable within project
                    max_speed = dev_ctx.get("pcie_max_link_speed")
                    max_width = dev_ctx.get("pcie_max_link_width")

                    if isinstance(max_speed, int) and max_speed > 0:
                        context["pcie_max_link_speed"] = max_speed
                    if isinstance(max_width, int) and max_width > 0:
                        context["pcie_max_link_width"] = max_width
                except Exception as e:
                    # Non-fatal: log and continue; TCL builder will enforce strictness downstream
                    log_debug_safe(
                        self.logger,
                        safe_format(
                            "PCIe link capability extraction via processor failed: {rc}",
                            rc=extract_root_cause(e),
                        ),
                        prefix="PCIL",
                    )
        except Exception as e:
            # Defensive catch-all to avoid blocking context construction
            log_debug_safe(
                self.logger,
                safe_format(
                    "PCIe link capability extraction skipped: {rc}",
                    rc=extract_root_cause(e),
                ),
                prefix="PCIL",
            )

        # Fallback: if PCIe link speed/width still missing, try sysfs current_link_* files
        try:
            need_speed = "pcie_max_link_speed" not in context
            need_width = "pcie_max_link_width" not in context
            if need_speed or need_width:
                bdf = self.device_bdf
                base = f"/sys/bus/pci/devices/{bdf}"

                def _read_sysfs(path: str) -> Optional[str]:
                    try:
                        with open(path, "r") as f:
                            return f.read().strip()
                    except Exception:
                        return None

                # Map speed string like "5 GT/s" or "5.0 GT/s" => code 2
                def _map_speed_str_to_code(s: str) -> Optional[int]:
                    try:
                        # Extract the leading float number
                        parts = s.replace("GT/s", "").replace("G T/s", "").split()
                        if not parts:
                            return None
                        val = (
                            parts[0]
                            .replace("GT/s", "")
                            .replace("G", "")
                            .replace("T/s", "")
                        )
                        val = (
                            val.replace("/s", "")
                            .replace("T", "")
                            .replace("(", "")
                            .replace(")", "")
                        )
                        num = float(val)
                        if 2.0 <= num < 3.0:
                            return 1
                        if 4.0 < num <= 6.0:
                            return 2
                        if 7.0 < num <= 9.0:
                            return 3
                        if 15.0 < num <= 17.0:
                            return 4
                        if 31.0 < num <= 33.0:
                            return 5
                        return None
                    except Exception:
                        return None

                # Map width string like "x4" => 4
                def _map_width_str_to_lanes(s: str) -> Optional[int]:
                    try:
                        s = s.lower().lstrip("x")
                        lanes = int("".join(ch for ch in s if ch.isdigit()))
                        return lanes if 1 <= lanes <= 16 else None
                    except Exception:
                        return None

                if need_speed:
                    spath = f"{base}/current_link_speed"
                    sval = _read_sysfs(spath)
                    code = _map_speed_str_to_code(sval) if sval else None
                    if code:
                        context["pcie_max_link_speed"] = code
                        log_info_safe(
                            self.logger,
                            safe_format(
                                "Using sysfs fallback for PCIe link speed: {val} -> code {code}",
                                val=sval,
                                code=code,
                            ),
                            prefix="PCIL",
                        )

                if need_width:
                    wpath = f"{base}/current_link_width"
                    wval = _read_sysfs(wpath)
                    lanes = _map_width_str_to_lanes(wval) if wval else None
                    if lanes:
                        context["pcie_max_link_width"] = lanes
                        log_info_safe(
                            self.logger,
                            safe_format(
                                "Using sysfs fallback for PCIe link width: {val} -> lanes {lanes}",
                                val=wval,
                                lanes=lanes,
                            ),
                            prefix="PCIL",
                        )
        except Exception as e:
            log_debug_safe(
                self.logger,
                safe_format(
                    "Sysfs fallback for PCIe link speed/width failed: {rc}",
                    rc=extract_root_cause(e),
                ),
                prefix="PCIL",
            )

        # Add overlay config
        overlay_config = self._build_overlay_config(config_space_data)
        for key, value in overlay_config.items():
            context[key] = value  # type: ignore

        # Add missing template variables using UnifiedContextBuilder
        device_type = self._get_device_type_from_class_code(
            device_identifiers.class_code
        )
        context["device_type"] = device_type  # type: ignore
        context["power_management"] = getattr(self.config, "power_management", False)  # type: ignore
        context["error_handling"] = getattr(self.config, "error_handling", False)  # type: ignore
        context["performance_counters"] = self._build_performance_config(device_type)  # type: ignore
        context["power_config"] = self._build_power_management_config()  # type: ignore
        context["error_config"] = self._build_error_handling_config()  # type: ignore
        context["variance_model"] = self._build_variance_model()  # type: ignore

        # Add device-specific signals
        device_signals = self._build_device_specific_signals(device_type)
        for key, value in device_signals.items():
            context[key] = value  # type: ignore

        # Add header for SystemVerilog generation from central constants

        context["header"] = SV_FILE_HEADER  # type: ignore
        context["registers"] = []  # type: ignore
        # EXT_CFG_CAP_PTR and EXT_CFG_XP_CAP_PTR must be present at top-level context for test contract
        ext_cfg_cap_ptr = None
        ext_cfg_xp_cap_ptr = None
        if isinstance(context.get("device_config"), dict):
            dc = context["device_config"]
            ext_cfg_cap_ptr = dc.get("ext_cfg_cap_ptr", DEFAULT_EXT_CFG_CAP_PTR)
            ext_cfg_xp_cap_ptr = dc.get("ext_cfg_xp_cap_ptr", DEFAULT_EXT_CFG_CAP_PTR)
            dc["EXT_CFG_CAP_PTR"] = ext_cfg_cap_ptr
            dc["EXT_CFG_XP_CAP_PTR"] = ext_cfg_xp_cap_ptr
        context["EXT_CFG_CAP_PTR"] = (
            ext_cfg_cap_ptr if ext_cfg_cap_ptr is not None else DEFAULT_EXT_CFG_CAP_PTR
        )
        context["EXT_CFG_XP_CAP_PTR"] = (
            ext_cfg_xp_cap_ptr
            if ext_cfg_xp_cap_ptr is not None
            else DEFAULT_EXT_CFG_CAP_PTR
        )

        if donor_template:
            context = self._merge_donor_template(dict(context), donor_template)

        # Ensure numeric ID aliases exist on top-level and device_config
        self._add_numeric_id_aliases(context)

        # Add VFIO status indicators for template integration logic.
        # These are dynamic, environment-derived values; never hardcode.
        try:
            import os as _os

            context["vfio_device"] = _os.path.exists("/dev/vfio/vfio")
        except Exception:
            context["vfio_device"] = False

        # Attempt a non-fatal verification of VFIO binding for this BDF.
        # If the check raises, treat as not verified but leave vfio_device intact.
        try:
            # Late import to allow unit tests to patch helpers
            from src.cli.vfio_helpers import ensure_device_vfio_binding as _ensure

            _ensure(self.device_bdf)
            context["vfio_binding_verified"] = True
        except Exception:
            context.setdefault("vfio_binding_verified", False)

        return context

    def _add_numeric_id_aliases(self, context):
        """Ensure numeric ID aliases exist on top-level and device_config."""
        from src.device_clone.constants import get_fallback_vendor_id

        # Simple int parsing - identifiers are already validated/normalized
        def _parse_hex_id(val):
            try:
                if isinstance(val, str) and val.startswith("0x"):
                    return int(val, 16)
                return int(val) if val else None
            except (ValueError, TypeError):
                return None

        # Get fallback vendor ID from central function
        fallback_vendor_id = get_fallback_vendor_id(
            prefer_random=getattr(self.config, "test_mode", False)
        )

        # Set numeric aliases (identifiers already validated in DeviceIdentifiers)
        parsed_vid = _parse_hex_id(context.get("vendor_id"))
        parsed_did = _parse_hex_id(context.get("device_id"))
        context.setdefault("vendor_id_int", parsed_vid or fallback_vendor_id)
        context.setdefault("device_id_int", parsed_did or DEVICE_ID_FALLBACK)

        # Also set aliases inside device_config dict if present
        if isinstance(context.get("device_config"), dict):
            dc = context["device_config"]
            vid_int = context.get("vendor_id_int", fallback_vendor_id)
            did_int = context.get("device_id_int", DEVICE_ID_FALLBACK)
            dc.setdefault("vendor_id_int", vid_int)
            dc.setdefault("device_id_int", did_int)
        elif hasattr(context.get("device_config"), "_data"):
            try:
                vid_int = context.get("vendor_id_int", fallback_vendor_id)
                did_int = context.get("device_id_int", DEVICE_ID_FALLBACK)
                context["device_config"]._data.setdefault("vendor_id_int", vid_int)
                context["device_config"]._data.setdefault("device_id_int", did_int)
            except Exception as e:
                log_warning_safe(
                    self.logger,
                    safe_format("Failed to set numeric ID aliases: {error}", error=e),
                    prefix="PCILEECH",
                )
                pass

    def _finalize_context(self, context):
        """Final validation and template compatibility."""
        self._validate_context_completeness(context)

        compatible_context = ensure_template_compatibility(dict(context))
        context.clear()
        context.update(cast(TemplateContext, compatible_context))
        # Ensure project and board are TemplateObjects with .name and .fpga_part
        if not isinstance(context.get("board_config"), TemplateObject):
            context["board_config"] = TemplateObject(
                context.get("board_config", {"name": "generic", "fpga_part": "xc7a35t"})
            )
        if not isinstance(context.get("board"), TemplateObject):
            context.setdefault(
                "board",
                context.get(
                    "board_config",
                    TemplateObject({"name": "generic", "fpga_part": "xc7a35t"}),
                ),
            )
        if not isinstance(context.get("project"), TemplateObject):
            context.setdefault("project", TemplateObject({"name": "pcileech_project"}))

    def _validate_input_data(
        self,
        config_space_data: Dict[str, Any],
        msix_data: Optional[Dict[str, Any]],
        behavior_profile: Optional[BehaviorProfile],
    ):
        """Validate input data with minimal nesting."""
        missing = []
        self._check_config_space(config_space_data, missing)
        self._check_msix_data(msix_data, missing)
        self._check_behavior_profile(behavior_profile, missing)
        if missing and self.validation_level in (
            ValidationLevel.STRICT,
            ValidationLevel.MODERATE,
        ):
            raise ContextError(
                safe_format("Missing required data: {missing}", missing=missing)
            )

    def _check_config_space(self, config_space_data, missing):
        if not config_space_data:
            missing.append("config_space_data")
            return
        # Basic config space validation - identifier validation happens in DeviceIdentifiers
        size = config_space_data.get("config_space_size", 0)
        if size < PCI_CONFIG_SPACE_MIN_SIZE:
            log_warning_safe(
                self.logger,
                safe_format(
                    "Config space size {size} < {min_size}",
                    size=size,
                    min_size=PCI_CONFIG_SPACE_MIN_SIZE,
                ),
                prefix="PCIL",
            )
            if self.validation_level == ValidationLevel.STRICT and size == 0:
                missing.append(safe_format("config_space_size ({size})", size=size))
        # Ensure required identifier keys trigger missing when absent so callers
        # that expect strict validation receive missing entries.
        for key in CORE_DEVICE_ID_FIELDS:
            if key not in config_space_data:
                missing.append(key)

    def _check_msix_data(self, msix_data, missing):
        if msix_data and msix_data.get("capability_info"):
            cap_info = msix_data["capability_info"]
            for field in self.REQUIRED_MSIX_FIELDS:
                if field not in cap_info:
                    missing.append(safe_format("msix.{field}", field=field))

    def _check_behavior_profile(self, behavior_profile, missing):
        if behavior_profile:
            if not getattr(behavior_profile, "total_accesses", 0) > 0:
                missing.append("behavior_profile.total_accesses")
            if not getattr(behavior_profile, "capture_duration", 0) > 0:
                missing.append("behavior_profile.capture_duration")

    def _extract_device_identifiers(
        self, config_space_data: Dict[str, Any]
    ) -> DeviceIdentifiers:
        """Extract device identifiers using ConfigSpaceManager if needed."""
        # If config_space_data doesn't have the required fields, use ConfigSpaceManager
        required_fields = CORE_DEVICE_ID_FIELDS
        missing_required = [k for k in required_fields if k not in config_space_data]

        if missing_required:
            # In strict validation mode, check if we have any essential identifiers
            if self.validation_level == ValidationLevel.STRICT:
                essential_missing = [
                    k for k in CORE_DEVICE_IDS if k not in config_space_data
                ]
                if essential_missing:
                    raise ContextError(
                        safe_format(
                            "Missing required data: {missing}",
                            missing=essential_missing,
                        )
                    )

            # Try to use ConfigSpaceManager for missing fields
            try:
                from src.device_clone.config_space_manager import ConfigSpaceManager

                manager = ConfigSpaceManager(self.device_bdf)
                config_space = manager.read_vfio_config_space()
                extracted_data = manager.extract_device_info(config_space)
                # Merge extracted data with provided data (prefer provided)
                config_space_data = {**extracted_data, **config_space_data}
            except Exception:
                if self.validation_level == ValidationLevel.STRICT:
                    raise ContextError(
                        safe_format(
                            "Missing required data: {missing}",
                            missing=missing_required,
                        )
                    )
                # In permissive mode, continue with what we have

        # Extract identifiers with basic validation
        vendor_id = config_space_data.get("vendor_id")
        device_id = config_space_data.get("device_id")

        # Only check for presence - DeviceIdentifiers will handle normalization
        require(
            vendor_id is not None and vendor_id != "",
            "Missing vendor_id from config space data",
        )
        require(
            device_id is not None and device_id != "",
            "Missing device_id from config space data",
        )

        # Require class_code - critical for device identity
        class_code = config_space_data.get("class_code")
        if class_code is None or class_code == "":
            # Try to extract from config space if not present
            if "class_code" not in config_space_data:
                log_error_safe(
                    self.logger,
                    "class_code missing from config space data - device identity incomplete",
                    prefix="CONTEXT",
                )
                # Use unknown device class as last resort, but warn heavily
                class_code = DEFAULT_CLASS_CODE
                log_warning_safe(
                    self.logger,
                    safe_format(
                        "Using fallback class_code {code} - device may enumerate incorrectly!",
                        code=class_code,
                    ),
                    prefix="CONTEXT",
                )

        # DeviceIdentifiers.__post_init__ handles validation and normalization
        return DeviceIdentifiers(
            vendor_id=str(vendor_id),
            device_id=str(device_id),
            class_code=str(class_code),
            revision_id=config_space_data.get("revision_id", DEFAULT_REVISION_ID),
            subsystem_vendor_id=config_space_data.get("subsystem_vendor_id"),
            subsystem_device_id=config_space_data.get("subsystem_device_id"),
        )

    def _build_device_config(
        self,
        identifiers: DeviceIdentifiers,
        behavior_profile: Optional[BehaviorProfile],
        config_space_data: Dict[str, Any],
    ) -> Dict[str, Any]:
        """Build device configuration."""
        device_config_dict = {
            "device_bdf": self.device_bdf,
            "vendor_id": identifiers.vendor_id,
            "device_id": identifiers.device_id,
            "class_code": identifiers.class_code,
            "revision_id": identifiers.revision_id,
            "subsystem_vendor_id": identifiers.subsystem_vendor_id,
            "subsystem_device_id": identifiers.subsystem_device_id,
            "enable_perf_counters": getattr(
                self.config, "enable_advanced_features", False
            ),
            "enable_advanced_features": getattr(
                self.config, "enable_advanced_features", False
            ),
            # Ensure templates can check for error injection hooks without crashing
            # This mirrors CLI/config flag --enable-error-injection handled upstream
            "enable_error_injection": getattr(
                self.config, "enable_error_injection", False
            ),
            "enable_dma_operations": getattr(
                self.config, "enable_dma_operations", False
            ),
            "enable_interrupt_coalescing": getattr(
                self.config, "enable_interrupt_coalescing", False
            ),
        }

        # Add hex representations
        if identifiers.subsystem_vendor_id:
            device_config_dict["subsystem_vendor_id_hex"] = safe_format(
                "0x{value:04X}",
                value=int(identifiers.subsystem_vendor_id, 16),
            )
        if identifiers.subsystem_device_id:
            device_config_dict["subsystem_device_id_hex"] = safe_format(
                "0x{value:04X}",
                value=int(identifiers.subsystem_device_id, 16),
            )

        # Add extended config pointers
        if hasattr(self.config, "device_config"):
            caps = getattr(self.config.device_config, "capabilities", None)
            if caps:
                device_config_dict["ext_cfg_cap_ptr"] = getattr(
                    caps, "ext_cfg_cap_ptr", DEFAULT_EXT_CFG_CAP_PTR
                )
                device_config_dict["ext_cfg_xp_cap_ptr"] = getattr(
                    caps, "ext_cfg_xp_cap_ptr", DEFAULT_EXT_CFG_CAP_PTR
                )

        # Add behavior profile
        if behavior_profile:
            device_config_dict.update(
                {
                    "behavior_profile": self._serialize_behavior_profile(
                        behavior_profile
                    ),
                    "total_register_accesses": behavior_profile.total_accesses,
                    "capture_duration": behavior_profile.capture_duration,
                    "timing_patterns_count": len(
                        getattr(behavior_profile, "timing_patterns", [])
                    ),
                    "state_transitions_count": len(
                        getattr(behavior_profile, "state_transitions", [])
                    ),
                    "has_manufacturing_variance": bool(
                        getattr(behavior_profile, "variance_metadata", None)
                    ),
                }
            )
            if hasattr(behavior_profile, "pattern_analysis"):
                device_config_dict["pattern_analysis"] = (
                    behavior_profile.pattern_analysis
                )

        return device_config_dict

    def _serialize_behavior_profile(self, profile: BehaviorProfile) -> Dict[str, Any]:
        """Serialize behavior profile."""
        try:
            profile_dict = asdict(profile)
            # Convert non-serializable objects
            for key, value in profile_dict.items():
                if hasattr(value, "__dict__"):
                    profile_dict[key] = safe_format(
                        "{type_name}_{value_hash}",
                        type_name=type(value).__name__,
                        value_hash=hash(str(value)),
                    )
            return profile_dict
        except Exception as e:
            raise ContextError(
                safe_format("Failed to serialize profile: {error}", error=e)
            )

    def _build_config_space_context(
        self, config_data: Dict[str, Any]
    ) -> Dict[str, Any]:
        """Build config space context, using ConfigSpaceManager if needed."""
        # If data is incomplete, use ConfigSpaceManager to get it
        if not all(
            k in config_data for k in ["config_space_hex", "config_space_size", "bars"]
        ):
            from src.device_clone.config_space_manager import ConfigSpaceManager

            manager = ConfigSpaceManager(self.device_bdf)
            config_space = manager.read_vfio_config_space()
            device_info = manager.extract_device_info(config_space)

            # Merge with provided data
            config_data = {**device_info, **config_data}

        return {
            "raw_data": config_data.get("config_space_hex", ""),
            "size": config_data.get("config_space_size", 256),
            "device_info": config_data.get("device_info", {}),
            "vendor_id": config_data["vendor_id"],  # Required - no fallback
            "device_id": config_data["device_id"],  # Required - no fallback
            "class_code": config_data.get("class_code", DEFAULT_CLASS_CODE),
            "revision_id": config_data.get("revision_id", DEFAULT_REVISION_ID),
            "bars": config_data.get("bars", []),
            "has_extended_config": config_data.get("config_space_size", 256) > 256,
        }

    def _build_msix_context(
        self, msix_data: Optional[Dict[str, Any]]
    ) -> Dict[str, Any]:
        """Build MSI-X context."""
        if not msix_data or not msix_data.get("capability_info"):
            # Return disabled MSI-X config
            disabled_ctx = {
                "num_vectors": 0,
                "table_bir": 0,
                "table_offset": 0,
                "pba_bir": 0,
                "pba_offset": 0,
                "enabled": False,
                "is_supported": False,
                "is_valid": False,
                "table_size": 0,
                "table_size_minus_one": 0,
                "NUM_MSIX": 0,
            }
            disabled_ctx.update(_build_msix_disabled_runtime_flags())
            return disabled_ctx

        cap = msix_data["capability_info"]
        table_size = cap["table_size"]
        table_offset = cap["table_offset"]
        pba_offset = cap.get("pba_offset", table_offset + (table_size * 16))
        pba_size_dwords = (table_size + 31) // 32

        # Check alignment
        alignment_warning = ""
        if table_offset % 8 != 0:
            alignment_warning = safe_format(
                "MSI-X table offset 0x{offset:x} is not 8-byte aligned",
                offset=table_offset,
            )

        context = {
            "num_vectors": table_size,
            "table_bir": cap["table_bir"],
            "table_offset": table_offset,
            "pba_bir": cap.get("pba_bir", cap["table_bir"]),
            "pba_offset": pba_offset,
            "enabled": cap.get("enabled", False),
            "is_supported": table_size > 0,
            "validation_errors": msix_data.get("validation_errors", []),
            "is_valid": msix_data.get("is_valid", True),
            "table_size_bytes": table_size * 16,
            "pba_size_bytes": pba_size_dwords * 4,
            "table_size": table_size,
            "table_size_minus_one": table_size - 1,
            "NUM_MSIX": table_size,
            "MSIX_TABLE_BIR": cap["table_bir"],
            "MSIX_TABLE_OFFSET": safe_format(
                "32'h{offset:08X}",
                offset=table_offset,
            ),
            "MSIX_PBA_BIR": cap.get("pba_bir", cap["table_bir"]),
            "MSIX_PBA_OFFSET": safe_format(
                "32'h{offset:08X}",
                offset=pba_offset,
            ),
        }
        # Merge standardized runtime flags
        context.update(
            _build_msix_enabled_runtime_flags(
                pba_size_dwords=pba_size_dwords,
                function_mask=cap.get("function_mask", False),
                # Preserve existing behavior: keep PBA writes disabled by default
                write_pba_allowed=False,
            )
        )

        # Add alignment warning if present
        if alignment_warning:
            context["alignment_warning"] = alignment_warning

        return context

    def _sample_bar_data_direct(
        self, device_bdf: str, bar_configs: list
    ) -> Dict[int, bytes]:
        """Sample BAR data directly via sysfs (read-only, safe).

        This complements MMIO tracing by capturing actual reset values
        from the donor device's BARs. Runs before MMIO learning to provide
        baseline data.

        Args:
            device_bdf: Device BDF (e.g., "0000:03:00.0")
            bar_configs: Analyzed BAR configurations

        Returns:
            Dict mapping BAR index to sampled bytes (first 8KB or full BAR)
        """
        try:
            from src.device_clone.sysfs_bar_reader import SysfsBarReader
        except ImportError:
            log_warning_safe(
                self.logger,
                "sysfs_bar_reader not available; skipping direct BAR sampling",
                prefix="SYSFS_BAR",
            )
            return {}

        sampled_bars: Dict[int, bytes] = {}

        try:
            reader = SysfsBarReader(device_bdf)

            for bar_info in bar_configs:
                if not bar_info.is_memory or bar_info.size == 0:
                    continue  # Skip I/O BARs and empty BARs

                bar_idx = bar_info.index

                try:
                    # Sample first 8KB or entire BAR if smaller
                    sample_size = min(8192, bar_info.size)
                    data = reader.read_bar_bytes(
                        bar_idx, offset=0, length=sample_size
                    )

                    if data:
                        sampled_bars[bar_idx] = data
                        log_info_safe(
                            self.logger,
                            safe_format(
                                "Sampled {size} bytes from BAR{idx} "
                                "(total size: 0x{total:X})",
                                size=len(data),
                                idx=bar_idx,
                                total=bar_info.size,
                            ),
                            prefix="SYSFS_BAR",
                        )
                    else:
                        log_warning_safe(
                            self.logger,
                            safe_format(
                                "Failed to sample BAR{idx}", idx=bar_idx
                            ),
                            prefix="SYSFS_BAR",
                        )

                except PermissionError:
                    log_warning_safe(
                        self.logger,
                        "BAR sampling requires root privileges; "
                        "skipping direct sampling",
                        prefix="SYSFS_BAR",
                    )
                    return {}  # Abort all sampling if permission denied
                except Exception as e:
                    log_warning_safe(
                        self.logger,
                        safe_format(
                            "Failed to sample BAR{idx}: {err}",
                            idx=bar_idx,
                            err=extract_root_cause(e),
                        ),
                        prefix="SYSFS_BAR",
                    )
                    continue

            if sampled_bars:
                log_info_safe(
                    self.logger,
                    safe_format(
                        "Successfully sampled {count} BAR(s) from donor device",
                        count=len(sampled_bars),
                    ),
                    prefix="SYSFS_BAR",
                )

        except FileNotFoundError:
            log_debug_safe(
                self.logger,
                safe_format(
                    "Device {bdf} not found in sysfs; skipping direct BAR sampling",
                    bdf=device_bdf,
                ),
                prefix="SYSFS_BAR",
            )
        except Exception as e:
            log_warning_safe(
                self.logger,
                safe_format(
                    "Direct BAR sampling failed: {err}",
                    err=extract_root_cause(e),
                ),
                prefix="SYSFS_BAR",
            )

        return sampled_bars

    def _capture_or_load_bar_models(
        self,
        device_bdf: str,
        bar_configs: list,
        enable_mmio_learning: bool = True,
        force_recapture: bool = False,
    ) -> Dict[int, Any]:
        """Capture MMIO traces or load cached models.

        Args:
            device_bdf: Device to profile
            bar_configs: Analyzed BAR info
            enable_mmio_learning: If False, skip MMIO learning
            force_recapture: Ignore cache, always capture

        Returns:
            Dict mapping BAR index to learned models
        """
        if not enable_mmio_learning:
            return {}

        # Check for prefilled bar_models from container flow
        import os
        device_context_path = os.getenv("DEVICE_CONTEXT_PATH")
        if device_context_path and os.path.exists(device_context_path):
            try:
                import json
                from src.device_clone.bar_model_loader import deserialize_bar_model
                
                with open(device_context_path, "r") as f:
                    device_context = json.load(f)
                
                bar_models_data = device_context.get("bar_models")
                if bar_models_data:
                    models = {}
                    for bar_idx_str, model_data in bar_models_data.items():
                        bar_idx = int(bar_idx_str)
                        models[bar_idx] = deserialize_bar_model(model_data)
                    
                    log_info_safe(
                        self.logger,
                        safe_format(
                            "Loaded {count} prefilled BAR models from {path}",
                            count=len(models),
                            path=device_context_path,
                        ),
                        prefix="MMIO",
                    )
                    return models
            except Exception as e:
                log_warning_safe(
                    self.logger,
                    safe_format(
                        "Failed to load prefilled BAR models: {err}",
                        err=str(e),
                    ),
                    prefix="MMIO",
                )

        from src.device_clone.bar_model_loader import BarModel, load_bar_model, save_bar_model
        from src.device_clone.bar_model_synthesizer import synthesize_model
        from src.device_clone.mmio_tracer import MmioTracer

        models = {}
        cache_dir = Path(".pcileech_cache") / device_bdf.replace(":", "_")
        cache_dir.mkdir(parents=True, exist_ok=True)

        for bar_info in bar_configs:
            if not bar_info.is_memory or bar_info.size == 0:
                continue  # Skip I/O BARs and empty BARs

            bar_idx = bar_info.index
            cache_file = cache_dir / f"bar{bar_idx}_model.json"

            # Try cache first
            if cache_file.exists() and not force_recapture:
                try:
                    models[bar_idx] = load_bar_model(cache_file)
                    log_info_safe(
                        self.logger,
                        safe_format("Loaded cached model for BAR{idx}", idx=bar_idx),
                        prefix="MMIO",
                    )
                    continue
                except Exception as e:
                    log_warning_safe(
                        self.logger,
                        safe_format(
                            "Cache invalid for BAR{idx}: {err}",
                            idx=bar_idx,
                            err=str(e),
                        ),
                        prefix="MMIO",
                    )

            # Capture fresh trace
            log_info_safe(
                self.logger,
                safe_format("Capturing MMIO trace for BAR{idx}...", idx=bar_idx),
                prefix="MMIO",
            )

            try:
                tracer = MmioTracer(device_bdf)
                trace = tracer.capture_probe_trace(
                    bar_index=bar_idx, duration_sec=5.0, trigger_rebind=False
                )

                if len(trace.accesses) == 0:
                    log_warning_safe(
                        self.logger,
                        safe_format(
                            "No MMIO traffic captured for BAR{idx}", idx=bar_idx
                        ),
                        prefix="MMIO",
                    )
                    continue

                # Synthesize model
                model = synthesize_model(trace)
                models[bar_idx] = model

                # Cache for future runs
                save_bar_model(model, cache_file)
                log_info_safe(
                    self.logger,
                    safe_format(
                        "Learned BAR{idx} model: {nregs} registers, cached to {path}",
                        idx=bar_idx,
                        nregs=len(model.registers),
                        path=cache_file,
                    ),
                    prefix="MMIO",
                )

            except PermissionError:
                log_warning_safe(
                    self.logger,
                    "Skipping MMIO trace (requires root); using synthetic content",
                    prefix="MMIO",
                )
                break  # Don't try other BARs if no perms
            except Exception as e:
                log_error_safe(
                    self.logger,
                    safe_format(
                        "Failed to capture BAR{idx}: {err}",
                        idx=bar_idx,
                        err=extract_root_cause(e),
                    ),
                    prefix="MMIO",
                )
                # Continue to next BAR

        return models

    def _build_bar_config(
        self,
        config_space_data: Dict[str, Any],
        behavior_profile: Optional[BehaviorProfile],
        enable_mmio_learning: bool = True,
        force_recapture: bool = False,
    ) -> Dict[str, Any]:
        """Build BAR configuration with optional MMIO learning."""
        self._check_and_fix_power_state()
        bars = config_space_data["bars"]
        bar_configs = self._analyze_bars(bars)
        primary_bar = self._select_primary_bar(bar_configs)
        log_info_safe(
            self.logger,
            safe_format(
                "Primary BAR: index={index}, size={size:.2f}MB",
                index=primary_bar.index,
                size=primary_bar.size_mb,
            ),
            prefix="BAR",
        )
        config = self._build_bar_config_dict(primary_bar, bar_configs)

        # --- BAR content generation with sampling + MMIO learning ---
        # Use device signature if available, else fallback to vendor:device
        device_signature = config_space_data.get("device_signature")
        if not device_signature:
            device_signature = safe_format(
                "{vendor}:{device}",
                vendor=config_space_data.get("vendor_id", ""),
                device=config_space_data.get("device_id", ""),
            )

        # Step 1: Sample BARs directly via sysfs (safe, read-only)
        device_bdf = config_space_data.get("device_bdf", self.device_bdf)
        bar_samples = self._sample_bar_data_direct(device_bdf, bar_configs)

        # Step 2: Attempt to learn models from live device (optional MMIO trace)
        bar_models = {}
        if enable_mmio_learning:
            try:
                bar_models = self._capture_or_load_bar_models(
                    device_bdf=config_space_data.get("device_bdf", self.device_bdf),
                    bar_configs=bar_configs,
                    enable_mmio_learning=enable_mmio_learning,
                    force_recapture=force_recapture,
                )
            except Exception as e:
                log_warning_safe(
                    self.logger,
                    safe_format("MMIO learning failed: {err}", err=str(e)),
                    prefix="MMIO",
                )

        # Generate BAR content (with learned models or sampled data if available)
        from src.device_clone.bar_content_generator import BarContentType

        bar_sizes = {b.index: b.size for b in bar_configs if b.size > 0}
        bar_content_gen = BarContentGenerator(device_signature=device_signature)

        bar_contents = {}
        for bar_idx, size in bar_sizes.items():
            model = bar_models.get(bar_idx)
            sampled_data = bar_samples.get(bar_idx)

            if model:
                # Use learned MMIO model (most accurate)
                content = bar_content_gen.generate_bar_content(
                    size, bar_idx, BarContentType.LEARNED, model=model
                )
                log_info_safe(
                    self.logger,
                    safe_format(
                        "BAR{idx}: Used LEARNED model with {nregs} registers",
                        idx=bar_idx,
                        nregs=len(model.registers),
                    ),
                    prefix="BAR",
                )
            elif sampled_data:
                # Use directly sampled data (donor reset values)
                # Pad or truncate to match BAR size
                if len(sampled_data) < size:
                    # Pad with high-entropy data
                    padding = bar_content_gen._get_seeded_bytes(
                        size - len(sampled_data),
                        f"pad_bar{bar_idx}"
                    )
                    content = sampled_data + padding
                else:
                    content = sampled_data[:size]

                log_info_safe(
                    self.logger,
                    safe_format(
                        "BAR{idx}: Used SAMPLED data ({sampled}B of {total}B)",
                        idx=bar_idx,
                        sampled=len(sampled_data),
                        total=size,
                    ),
                    prefix="BAR",
                )
            else:
                # Fallback to existing heuristics (synthetic data)
                if size <= 4096:
                    content_type = BarContentType.REGISTERS
                else:
                    content_type = BarContentType.MIXED
                content = bar_content_gen.generate_bar_content(
                    size, bar_idx, content_type
                )

            bar_contents[bar_idx] = content

        config["bar_contents"] = bar_contents
        # Store sampled data for reference (optional)
        if bar_samples:
            config["bar_samples"] = bar_samples

        # Store learned BAR models if available for SystemVerilog generation
        if bar_models:
            # Serialize models for template compatibility
            from src.device_clone.bar_model_loader import serialize_bar_model
            
            serialized_models = {}
            for bar_idx, model in bar_models.items():
                try:
                    serialized_models[bar_idx] = serialize_bar_model(model)
                except Exception as e:
                    log_warning_safe(
                        self.logger,
                        safe_format(
                            "Failed to serialize BAR{idx} model: {err}",
                            idx=bar_idx,
                            err=str(e),
                        ),
                        prefix="BAR",
                    )
            
            if serialized_models:
                config["bar_models"] = serialized_models
                log_info_safe(
                    self.logger,
                    safe_format(
                        "Stored {count} learned BAR models in context",
                        count=len(serialized_models),
                    ),
                    prefix="BAR",
                )

        if behavior_profile:
            config.update(
                self._adjust_bar_config_for_behavior(config, behavior_profile)
            )
        return config

    def _analyze_bars(self, bars):
        """Analyze BARs with detailed progress output."""
        bar_configs = []
        
        # Technical header
        log_info_safe(
            self.logger,
            safe_format(
                "╔═════════════════════════════════════════════════════════════╗"
            ),
            prefix="BAR",
        )
        log_info_safe(
            self.logger,
            safe_format(
                "║  BASE ADDRESS REGISTER DISCOVERY & ANALYSIS                ║"
            ),
            prefix="BAR",
        )
        log_info_safe(
            self.logger,
            safe_format(
                "╠═════════════════════════════════════════════════════════════╣"
            ),
            prefix="BAR",
        )
        
        total_bars = len(bars)
        discovered_count = 0
        total_memory_mapped = 0
        
        for i, bar_data in enumerate(bars):
            try:
                # Use the true BAR index from config space when querying VFIO.
                # enumerate() index may not match BAR index if some BARs are
                # disabled or filtered out, which can mis-read I/O BAR sizes.
                vfio_region_index = i
                if isinstance(bar_data, dict):
                    vfio_region_index = bar_data.get("index", bar_data.get("bar", i))
                elif isinstance(bar_data, BarInfo):
                    vfio_region_index = bar_data.index
                    vfio_region_index = getattr(bar_data, "index")

                bar_info = self._get_vfio_bar_info(vfio_region_index, bar_data)
                if bar_info:
                    bar_configs.append(bar_info)
                    discovered_count += 1
                    
                    # Technical per-BAR output
                    size_mb = bar_info.size / (1024 * 1024)
                    size_kb = bar_info.size / 1024
                    
                    if bar_info.is_memory:
                        total_memory_mapped += bar_info.size
                        if size_mb >= 1:
                            size_display = safe_format(
                                "{size:.2f} MB", size=size_mb
                            )
                        else:
                            size_display = safe_format(
                                "{size:.2f} KB", size=size_kb
                            )
                        bar_flags = "PREFETCH" if bar_info.prefetchable else "MEM"
                    else:
                        size_display = safe_format(
                            "{size} bytes", size=bar_info.size
                        )
                        bar_flags = "IO"
                    
                    width_indicator = (
                        "64-bit" if bar_info.bar_type == 1 else "32-bit"
                    )
                    
                    bar_line = safe_format(
                        "║ BAR{idx} @ 0x{addr:08X} │ {size:>12} │ "
                        "{width:>7} │ {flags:>8} ║",
                        idx=vfio_region_index,
                        addr=bar_info.base_address,
                        size=size_display,
                        width=width_indicator,
                        flags=bar_flags
                    )
                    log_info_safe(self.logger, bar_line, prefix="BAR")
            except Exception as e:
                log_warning_safe(
                    self.logger,
                    safe_format(
                        "║ BAR{index}: DISCOVERY FAILED - {error}",
                        index=i,
                        error=str(e)
                    ),
                    prefix="BAR",
                )
        
        # Summary footer
        separator = (
            "╠═════════════════════════════════════════════════════════════╣"
        )
        log_info_safe(self.logger, safe_format(separator), prefix="BAR")
        
        total_memory_mb = total_memory_mapped / (1024 * 1024)
        summary_line = safe_format(
            "║ DISCOVERED: {discovered}/{total} BARs │ "
            "MEMORY MAPPED: {mem:.2f} MB              ║",
            discovered=discovered_count,
            total=total_bars,
            mem=total_memory_mb
        )
        log_info_safe(self.logger, summary_line, prefix="BAR")
        
        footer = (
            "╚═════════════════════════════════════════════════════════════╝"
        )
        log_info_safe(self.logger, safe_format(footer), prefix="BAR")
        
        return bar_configs

    def _select_primary_bar(self, bar_configs):
        memory_bars = [b for b in bar_configs if b.is_memory and b.size > 0]
        if not memory_bars:
            raise ContextError("No valid MMIO BARs found")
        return max(memory_bars, key=lambda b: b.size)

    def _build_bar_config_dict(self, primary_bar, bar_configs):
        return {
            "bar_index": primary_bar.index,
            "aperture_size": primary_bar.size,
            "bar_type": primary_bar.bar_type,
            "prefetchable": primary_bar.prefetchable,
            "memory_type": "memory" if primary_bar.is_memory else "io",
            "bars": bar_configs,
        }

    def _get_vfio_bar_info(self, index: int, bar_data) -> Optional[BarConfiguration]:
        """Get BAR info via VFIO with strict size validation."""
        region_info = self._vfio_manager.get_region_info(index)
        if not region_info:
            # VFIO not available; construct from provided bar_data as a fallback.
            try:
                if isinstance(bar_data, dict):
                    is_memory = bar_data.get("type", "memory") == "memory"
                    is_io = not is_memory
                    base_address = bar_data.get("address", 0)
                    prefetchable = bar_data.get("prefetchable", False)
                    bar_type = 1 if bar_data.get("is_64bit", False) else 0
                    size = int(bar_data.get("size", 0) or 0)
                elif isinstance(bar_data, BarInfo):
                    is_memory = bar_data.bar_type == "memory"
                    is_io = not is_memory
                    base_address = bar_data.address
                    prefetchable = bar_data.prefetchable
                    bar_type = 1 if bar_data.is_64bit else 0
                    size = int(getattr(bar_data, "size", 0) or 0)
                elif hasattr(bar_data, "address"):
                    is_memory = getattr(bar_data, "type", "memory") == "memory"
                    is_io = not is_memory
                    base_address = getattr(bar_data, "address", 0)
                    prefetchable = getattr(bar_data, "prefetchable", False)
                    bar_type = 1 if getattr(bar_data, "is_64bit", False) else 0
                    size = int(getattr(bar_data, "size", 0) or 0)
                else:
                    return None

                if is_memory and size > 0:
                    return BarConfiguration(
                        index=index,
                        base_address=base_address,
                        size=size,
                        bar_type=bar_type,
                        prefetchable=prefetchable,
                        is_memory=is_memory,
                        is_io=is_io,
                    )
                return None
            except Exception as e:
                log_warning_safe(
                    self.logger,
                    safe_format(
                        "BAR{index}: VFIO unavailable; sysfs fallback failed: "
                        "{error}",
                        index=index,
                        error=str(e),
                    ),
                    prefix="BAR",
                )
                return None

        try:
            size = extract_bar_size(region_info)
        except Exception as e:
            log_error_safe(
                self.logger,
                safe_format(
                    "Invalid BAR size for BAR {index}: {error}",
                    index=index,
                    error=str(e),
                ),
                prefix="VFIO",
            )
            raise

        # Extract BAR properties
        if isinstance(bar_data, dict):
            is_memory = bar_data.get("type", "memory") == "memory"
            is_io = not is_memory
            base_address = bar_data.get("address", 0)
            prefetchable = bar_data.get("prefetchable", False)
            bar_type = 1 if bar_data.get("is_64bit", False) else 0
        elif isinstance(bar_data, BarInfo):
            is_memory = bar_data.bar_type == "memory"
            is_io = not is_memory
            base_address = bar_data.address
            prefetchable = bar_data.prefetchable
            bar_type = 1 if bar_data.is_64bit else 0
        elif hasattr(bar_data, "address"):
            is_memory = getattr(bar_data, "type", "memory") == "memory"
            is_io = not is_memory
            base_address = bar_data.address
            prefetchable = getattr(bar_data, "prefetchable", False)
            bar_type = 1 if getattr(bar_data, "is_64bit", False) else 0
        else:
            return None

        # Enforce minimum size for memory BARs; fallback to sysfs-reported size
        # in bar_data when VFIO reports an unexpectedly small size (e.g., due to
        # wrong region mapping or kernel quirks).
        if is_memory:
            min_mem = BAR_SIZE_CONSTANTS.get("MIN_MEMORY_SIZE", 128)
            if size < min_mem:
                fallback_size = None
                try:
                    if isinstance(bar_data, dict):
                        fallback_size = bar_data.get("size")
                    elif isinstance(bar_data, BarInfo):
                        fallback_size = getattr(bar_data, "size", 0)
                    elif hasattr(bar_data, "size"):
                        fallback_size = getattr(bar_data, "size")
                except Exception:
                    fallback_size = None

                if isinstance(fallback_size, int) and fallback_size >= min_mem:
                    # Use the more reliable sysfs resource size
                    log_warning_safe(
                        self.logger,
                        safe_format(
                            "BAR {index}: VFIO size {size}B < {min_mem}B; using sysfs size {fallback_size}B",
                            index=index,
                            size=size,
                            min_mem=min_mem,
                            fallback_size=fallback_size,
                        ),
                        prefix="BAR",
                    )
                    size = fallback_size
                else:
                    # Skip invalid/small memory BARs
                    log_warning_safe(
                        self.logger,
                        safe_format(
                            "Skipping BAR {index}: memory BAR size {size}B below minimum {min_mem}B",
                            index=index,
                            size=size,
                            min_mem=min_mem,
                        ),
                        prefix="BAR",
                    )
                    return None

        # Only return valid memory BARs; never select I/O BARs for MMIO aperture
        if is_memory and size > 0:
            return BarConfiguration(
                index=index,
                base_address=base_address,
                size=size,
                bar_type=bar_type,
                prefetchable=prefetchable,
                is_memory=is_memory,
                is_io=is_io,
            )

        return None

    def _check_and_fix_power_state(self):
        """Check and fix device power state."""
        try:
            power_state_path = safe_format(
                "/sys/bus/pci/devices/{device_bdf}/power_state",
                device_bdf=self.device_bdf,
            )
            if Path(power_state_path).exists():
                with open(power_state_path, "r") as f:
                    state = f.read().strip()
                    if state != POWER_STATE_D0:  # Power state D0 (fully on)
                        log_info_safe(
                            self.logger,
                            safe_format("Waking device from {state}", state=state),
                            prefix="PWR",
                        )
                        # Wake device by accessing config space
                        config_path = safe_format(
                            "/sys/bus/pci/devices/{device_bdf}/config",
                            device_bdf=self.device_bdf,
                        )
                        with open(config_path, "rb") as f:
                            f.read(4)  # Read vendor ID to wake device
        except Exception as e:
            log_warning_safe(
                self.logger,
                safe_format("Power state check failed: {error}", error=e),
                prefix="PWR",
            )

    def _adjust_bar_config_for_behavior(
        self, config: Dict[str, Any], profile: BehaviorProfile
    ) -> Dict[str, Any]:
        """Adjust BAR config based on behavior."""
        adjustments = {}

        if hasattr(profile, "access_patterns"):
            patterns = getattr(profile, "access_patterns", {})
            if patterns.get("burst_mode"):
                adjustments["burst_mode_enabled"] = True
                adjustments["burst_size"] = patterns.get("burst_size", 64)

        if hasattr(profile, "timing_patterns"):
            if len(profile.timing_patterns) > 0:
                adjustments["has_timing_patterns"] = True

        return adjustments

    def _build_timing_config(
        self,
        behavior_profile: Optional[BehaviorProfile],
        identifiers: DeviceIdentifiers,
    ) -> TimingParameters:
        """Build timing configuration using existing device config."""

        # Try to get timing from behavior profile first
        if behavior_profile and hasattr(behavior_profile, "timing_patterns"):
            patterns = behavior_profile.timing_patterns
            if patterns and len(patterns) > 0:
                # Use the behavior profile's actual timing data
                # This is dynamic based on actual device behavior
                total_read = 0
                total_write = 0
                count = 0

                for p in patterns:
                    # Handle both object and dict patterns
                    if hasattr(p, "avg_interval_us"):
                        # Convert interval to latency estimate
                        total_read += max(1, int(p.avg_interval_us / 100))
                        total_write += max(1, int(p.avg_interval_us / 100))
                        count += 1
                    elif isinstance(p, dict) and "avg_interval_us" in p:
                        total_read += max(1, int(p["avg_interval_us"] / 100))
                        total_write += max(1, int(p["avg_interval_us"] / 100))
                        count += 1

                if count > 0:
                    avg_read = total_read / count
                    avg_write = total_write / count
                    burst_length = 32  # Default
                    return TimingParameters(
                        read_latency=max(1, int(avg_read)),
                        write_latency=max(1, int(avg_write)),
                        burst_length=burst_length,
                        inter_burst_gap=max(1, burst_length // 4),
                        timeout_cycles=max(100, int(avg_read * 100)),
                        clock_frequency_mhz=100.0,  # This should come from config
                        timing_regularity=0.95,
                    )

        # Try to get device-specific config
        device_config = None
        try:
            # Derive profile name from device identifiers for configuration lookup
            profile_name = safe_format(
                "{vendor_id}_{device_id}",
                vendor_id=identifiers.vendor_id,
                device_id=identifiers.device_id,
            )
            device_config = get_device_config(profile_name)
        except Exception as e:
            # Device config not found or invalid - use defaults
            log_debug_safe(
                self.logger,
                safe_format(
                    "Device config lookup failed, using timing defaults: {error}",
                    prefix="TIMING",
                    error=str(e),
                ),
            )

        if device_config and hasattr(device_config, "capabilities"):
            # Use the device-specific timing configuration from capabilities
            caps = device_config.capabilities
            return TimingParameters(
                read_latency=getattr(caps, "read_latency", 10),
                write_latency=getattr(caps, "write_latency", 10),
                burst_length=getattr(caps, "burst_length", 32),
                inter_burst_gap=getattr(caps, "inter_burst_gap", 8),
                timeout_cycles=getattr(caps, "timeout_cycles", 1000),
                clock_frequency_mhz=getattr(caps, "clock_frequency_mhz", 100.0),
                timing_regularity=getattr(caps, "timing_regularity", 0.95),
            )
        class_prefix = identifiers.class_code[:2]

        # Default timing parameters based on device class
        if class_prefix == "02":  # Network controller
            base_freq = 125.0
            read_latency = 4
            write_latency = 2
        elif class_prefix == "01":  # Storage controller
            base_freq = 100.0
            read_latency = 8
            write_latency = 4
        elif class_prefix == "03":  # Display controller
            base_freq = 100.0
            read_latency = 6
            write_latency = 3
        else:  # Default for other devices
            base_freq = 100.0
            read_latency = 10
            write_latency = 10

        return TimingParameters(
            read_latency=read_latency,
            write_latency=write_latency,
            burst_length=32,
            inter_burst_gap=8,
            timeout_cycles=1000,
            clock_frequency_mhz=base_freq,
            timing_regularity=0.95,
        )

    def _build_pcileech_config(self, identifiers: DeviceIdentifiers) -> Dict[str, Any]:
        """Build PCILeech-specific configuration using dynamic values."""
        # Gather defaults and device/capability-provided values but avoid
        # overwriting any explicit configuration present in self.config.pcileech_config
        defaults = {
            "device_signature": identifiers.device_signature,
            "full_signature": identifiers.full_signature,
            "enable_shadow_config": True,
            "enable_bar_emulation": True,
            # sensible defaults; these may be overridden from device capabilities
            "max_payload_size": getattr(self.config, "max_payload_size", 256),
            "max_read_request": getattr(self.config, "max_read_request", 512),
            "completion_timeout": 50000,
            "replay_timer": 1000,
            "ack_nak_latency": 100,
            # buffer_size is expressed in bytes
            "buffer_size": None,
            # DMA/scatter settings
            "enable_dma": getattr(self.config, "enable_dma_operations", True),
            # Use explicit scatter_gather setting if present, otherwise fall back to DMA operations
            "enable_scatter_gather": getattr(
                self.config,
                "enable_scatter_gather",
                getattr(self.config, "enable_dma_operations", True),
            ),
            # backwards/alternate names some templates or older code may expect
            "max_read_req_size": None,
            "max_payload": None,
        }

        # Merge in values from any device-specific capabilities if available
        caps = None
        if hasattr(self.config, "device_config") and self.config.device_config:
            caps = getattr(self.config.device_config, "capabilities", None)

        if caps:
            # Prefer explicit capability attributes when present
            if hasattr(caps, "max_payload_size"):
                defaults["max_payload_size"] = caps.max_payload_size
            if hasattr(caps, "max_read_request"):
                defaults["max_read_request"] = caps.max_read_request
            if hasattr(caps, "completion_timeout"):
                defaults["completion_timeout"] = caps.completion_timeout
            if hasattr(caps, "replay_timer"):
                defaults["replay_timer"] = caps.replay_timer

        # Finalize derived/alias fields
        if defaults.get("buffer_size") is None:
            # buffer_size default: 4x max_payload_size (bytes)
            defaults["buffer_size"] = int(defaults.get("max_payload_size", 256)) * 4

        # Provide aliases to avoid template mismatch
        defaults["max_read_req_size"] = defaults.get("max_read_request")
        defaults["max_payload"] = defaults.get("max_payload_size")

        project_overrides = {}
        if hasattr(self.config, "pcileech_config") and isinstance(
            getattr(self.config, "pcileech_config"), dict
        ):
            project_overrides = getattr(self.config, "pcileech_config")

        # Build final config by starting with defaults, then applying capability
        # values (already in defaults). Prefer dynamic/capability values: only
        # apply project overrides when the dynamic/default value is empty (None or '').
        final = dict(defaults)
        if project_overrides:
            for k, v in project_overrides.items():
                current = final.get(k, None)
                if current is None or (isinstance(current, str) and current == ""):
                    final[k] = v

        required_keys = [
            "command_timeout",
            "buffer_size",
            "enable_dma",
            "enable_scatter_gather",
        ]
        # command_timeout is an alias for completion_timeout if not provided
        if "command_timeout" not in final or final.get("command_timeout") is None:
            final["command_timeout"] = final.get("completion_timeout")

        for k in required_keys:
            if k not in final:
                # fallback sensible default
                if k == "command_timeout":
                    final[k] = final.get("completion_timeout", 50000)
                elif k == "buffer_size":
                    final[k] = final.get(
                        "buffer_size", int(final.get("max_payload_size", 256)) * 4
                    )
                elif k == "enable_dma":
                    final[k] = bool(final.get("enable_dma", False))
                elif k == "enable_scatter_gather":
                    # Always use explicit scatter_gather setting if provided,
                    # otherwise use the explicit DMA setting if provided,
                    # with a final fallback to True (safe default)
                    final[k] = bool(
                        final.get(
                            "enable_scatter_gather", final.get("enable_dma", True)
                        )
                    )

        return final

    def _build_active_device_config(
        self,
        identifiers: DeviceIdentifiers,
        interrupt_strategy: str,
        interrupt_vectors: int,
    ) -> Any:
        """Build active device configuration using unified context builder."""
        from src.utils.unified_context import UnifiedContextBuilder

        builder = UnifiedContextBuilder(self.logger)

        return builder.create_active_device_config(
            vendor_id=identifiers.vendor_id,
            device_id=identifiers.device_id,
            subsystem_vendor_id=identifiers.subsystem_vendor_id,
            subsystem_device_id=identifiers.subsystem_device_id,
            class_code=identifiers.class_code,
            revision_id=identifiers.revision_id,
            interrupt_strategy=interrupt_strategy,
            interrupt_vectors=interrupt_vectors,
        )

    def _generate_device_signature(
        self,
        identifiers: DeviceIdentifiers,
        behavior_profile: Optional[BehaviorProfile],
        config_space_data: Dict[str, Any],
    ) -> str:
        """Generate canonical device signature as 'VID:DID:RID'."""
        rid = identifiers.revision_id or DEFAULT_REVISION_ID
        return safe_format(
            "{vendor}:{device}:{revision}",
            vendor=identifiers.vendor_id,
            device=identifiers.device_id,
            revision=rid,
        )

    def _build_generation_metadata(self, identifiers: DeviceIdentifiers) -> Any:
        """Build generation metadata using centralized metadata builder."""
        from src.utils.metadata import build_generation_metadata

        # Use device_signature as 'vendor_id:device_id' for test contract
        return build_generation_metadata(
            device_bdf=self.device_bdf,
            device_signature=safe_format(
                "{vendor}:{device}:{revision}",
                vendor=identifiers.vendor_id,
                device=identifiers.device_id,
                revision=identifiers.revision_id,
            ),
            device_class=identifiers.get_device_class_type(),
            validation_level=self.validation_level.value,
            vendor_name=self._get_vendor_name(identifiers.vendor_id),
            device_name=self._get_device_name(
                identifiers.vendor_id, identifiers.device_id
            ),
        )

    def _get_vendor_name(self, vendor_id: str) -> str:
        """Get vendor name from vendor ID using lspci directly.

        Removed indirection through now-deleted lookup_device_info to avoid
        redundant config space re-reads. Keep minimal logic only.
        """
        import subprocess

        try:
            result = subprocess.run(
                [
                    "lspci",
                    "-mm",
                    "-d",
                    safe_format("{vendor_id}:", vendor_id=vendor_id),
                ],
                capture_output=True,
                text=True,
                timeout=2,
            )
            if result.returncode == 0 and result.stdout:
                parts = result.stdout.strip().split('"')
                if len(parts) > 3:
                    return parts[3]
        except Exception as e:
            log_warning_safe(
                self.logger,
                safe_format("Failed to get vendor name: {error}", error=e),
                prefix="PCILEECH",
            )
            pass
        return safe_format("Vendor {vendor_id}", vendor_id=vendor_id)

    def _get_device_name(self, vendor_id: str, device_id: str) -> str:
        """Get device name from vendor/device IDs via lspci.

        Direct resolution keeps behavior identical to previous code path when
        lookup_device_info provided no enrichment (most cases).
        """
        import subprocess

        try:
            result = subprocess.run(
                [
                    "lspci",
                    "-mm",
                    "-d",
                    safe_format(
                        "{vendor_id}:{device_id}",
                        vendor_id=vendor_id,
                        device_id=device_id,
                    ),
                ],
                capture_output=True,
                text=True,
                timeout=2,
            )
            if result.returncode == 0 and result.stdout:
                parts = result.stdout.strip().split('"')
                if len(parts) > 5:
                    return parts[5]
        except Exception as e:
            log_warning_safe(
                self.logger,
                safe_format("Failed to get device name: {error}", error=e),
                prefix="PCILEECH",
            )
            pass
        return safe_format("Device {device_id}", device_id=device_id)

    def _build_overlay_config(
        self, config_space_data: Dict[str, Any]
    ) -> Dict[str, Any]:
        """Build overlay configuration for shadow config space."""
        try:
            mapper = OverlayMapper()
            # Convert config_space_data to proper format for overlay mapper
            dword_map = config_space_data.get("dword_map", {})
            if not dword_map and "config_space_hex" in config_space_data:
                # Create dword_map from hex data if not present
                hex_data = config_space_data["config_space_hex"]
                if isinstance(hex_data, str):
                    hex_data = hex_data.replace(" ", "").replace("\n", "")
                    dword_map = {}
                    for i in range(
                        0, min(len(hex_data), 1024), 8
                    ):  # Process up to 256 dwords
                        if i + 8 <= len(hex_data):
                            dword = hex_data[i : i + 8]
                            dword_map[i // 8] = int(dword, 16)

            capabilities = config_space_data.get("capabilities", {})
            overlay_result = mapper.generate_overlay_map(dword_map, capabilities)
            overlay_map = overlay_result.get("OVERLAY_MAP", [])
            raw_entry_count = overlay_result.get("OVERLAY_ENTRIES", overlay_map)
            overlay_entries = normalize_overlay_entry_count(raw_entry_count)

            def _coerce_toggle(value: Union[None, str, bool, int], default: int) -> int:
                if value is None:
                    return default
                if isinstance(value, str):
                    normalized = value.strip().lower()
                    if normalized in {"0", "false", "off", "no"}:
                        return 0
                    if normalized in {"1", "true", "on", "yes"}:
                        return 1
                return int(bool(value))

            sparse_toggle = getattr(self.config, "enable_sparse_map", None)
            enable_sparse_map = _coerce_toggle(sparse_toggle, int(overlay_entries > 0))

            bit_type_toggle = getattr(self.config, "enable_bit_types", None)
            enable_bit_types = _coerce_toggle(bit_type_toggle, 1)

            hash_table_size = compute_sparse_hash_table_size(overlay_entries)

            return {
                "OVERLAY_MAP": overlay_map,
                "OVERLAY_ENTRIES": overlay_entries,
                "ENABLE_SPARSE_MAP": enable_sparse_map,
                "HASH_TABLE_SIZE": hash_table_size,
                "ENABLE_BIT_TYPES": enable_bit_types,
            }
        except Exception as e:
            log_warning_safe(
                self.logger,
                safe_format("Overlay generation failed: {error}", error=e),
                prefix="OVR",
            )
            return {
                "OVERLAY_MAP": [],
                "OVERLAY_ENTRIES": 0,
                "ENABLE_SPARSE_MAP": 0,
                "HASH_TABLE_SIZE": compute_sparse_hash_table_size(0),
                "ENABLE_BIT_TYPES": 1,
            }

    def _merge_donor_template(
        self, context: Dict[str, Any], donor: Dict[str, Any]
    ) -> TemplateContext:
        """Merge donor template with context."""
        # Deep merge, preferring context values
        merged = dict(donor)
        # Merge: ALWAYS prefer the dynamic/context values over donor values.
        for key, value in context.items():
            if key in merged and merged[key] is not None and merged[key] != {}:
                # Donor provided a value; we'll prefer the context value but log the overwrite
                if merged[key] != value:
                    log_warning_safe(
                        self.logger,
                        safe_format(
                            "Donor template provided '{key}', but dynamic context value will be used.",
                            key=key,
                        ),
                        prefix="TEMPLATE",
                    )

            # If both sides are dict-like, perform a shallow merge where context overrides donor
            if (
                key in merged
                and isinstance(merged[key], dict)
                and isinstance(value, dict)
            ):
                merged[key] = {**merged[key], **value}
            else:
                merged[key] = value
        # Ensure active_device_config retains TemplateObject shape
        try:
            if "active_device_config" in merged:
                raw = merged["active_device_config"]

                if isinstance(raw, dict):
                    # Coerce dict into TemplateObject to preserve template attribute access
                    merged["active_device_config"] = TemplateObject(raw)
                    log_warning_safe(
                        self.logger,
                        "Donor template provided 'active_device_config' as dict; coerced to TemplateObject and dynamic values will be preserved.",
                        prefix="TEMPLATE",
                    )
                # If it's already a TemplateObject or an object with 'enabled', leave as-is
        except Exception as e:
            # If coercion fails, log and continue; caller will perform final validation
            log_warning_safe(
                self.logger,
                safe_format("Failed to coerce active_device_config: {error}", error=e),
                prefix="TEMPLATE",
            )

        return merged  # type: ignore

    def _build_board_config(self) -> Any:
        """Build board configuration using unified context builder."""

        builder = UnifiedContextBuilder(self.logger)

        try:
            # Get board name from config
            board_name = getattr(self.config, "board", None)
            if not board_name:
                # Try to get board from fallback or environment
                log_warning_safe(
                    self.logger,
                    "No board specified in config, using fallback detection",
                    prefix="PCIL",
                )
                # Use a default board or get from constants
                from src.device_clone.constants import BOARD_PARTS

                board_name = list(BOARD_PARTS.keys())[0]  # Use first available board

            log_info_safe(
                self.logger,
                safe_format(
                    "Building board configuration for {board_name}",
                    board_name=board_name,
                ),
                prefix="PCIL",
            )

            board_config = get_pcileech_board_config(board_name)

            log_info_safe(
                self.logger,
                safe_format(
                    "Board configuration loaded: {fpga_part}",
                    fpga_part=board_config.get("fpga_part", "unknown"),
                ),
                prefix="PCIL",
            )

            # Load board-specific XDC content from repository
            # This provides PCIe reference clock pin constraints and other board-specific pin assignments
            try:
                from src.file_management.repo_manager import RepoManager
                
                board_xdc_content = RepoManager.read_combined_xdc(board_name)
                board_config["board_xdc_content"] = board_xdc_content
                
                log_info_safe(
                    self.logger,
                    safe_format(
                        "Loaded board XDC content for {board_name} ({size} bytes)",
                        board_name=board_name,
                        size=len(board_xdc_content),
                    ),
                    prefix="PCIL",
                )
            except Exception as e:
                log_warning_safe(
                    self.logger,
                    safe_format(
                        "Failed to load board XDC content for {board_name}: {error}",
                        board_name=board_name,
                        error=extract_root_cause(e),
                    ),
                    prefix="PCIL",
                )
                # Set empty XDC content to avoid template errors
                board_config["board_xdc_content"] = ""

            # Pass only the fields present in board_config; builder should handle defaults internally
            return builder.create_board_config(**board_config)

        except Exception as e:
            log_error_safe(
                self.logger,
                safe_format("Failed to build board configuration: {error}", error=e),
                prefix="PCIL",
            )
            # Return a minimal board config to prevent template validation failure
            return builder.create_board_config(
                board_name="generic",
                fpga_part="xc7a35tcsg324-2",
                fpga_family="7series",
                pcie_ip_type="7x",
                max_lanes=4,
                supports_msi=True,
                supports_msix=False,
                config_voltage="3.3",
                bitstream_unusedpin="pullup",
                bitstream_spi_buswidth="4",
                bitstream_configrate="33",
            )

    def _validate_context_completeness(self, context: TemplateContext):
        """Validate context has all required fields."""
        for section in REQUIRED_CONTEXT_SECTIONS:
            if section not in context:  # type: ignore
                raise ContextError(
                    safe_format("Missing required section: {section}", section=section)
                )

        # Validate device signature (identifiers already validated in DeviceIdentifiers)
        if "device_signature" not in context or not context["device_signature"]:
            raise ContextError("Missing device signature")

        # Basic presence check - detailed validation done earlier
        vendor_id = context.get("vendor_id") or (
            context.get("device_config", {}).get("vendor_id")
            if context.get("device_config")
            else None
        )
        device_id = context.get("device_id") or (
            context.get("device_config", {}).get("device_id")
            if context.get("device_config")
            else None
        )

        if not vendor_id or not device_id:
            raise ContextError("Missing device identifiers")

    def _build_performance_config(self, device_type: str = "generic") -> Any:
        builder = UnifiedContextBuilder(self.logger)

        return builder.create_performance_config(
            enable_transaction_counters=getattr(
                self.config, "enable_transaction_counters", True
            ),
            enable_bandwidth_monitoring=getattr(
                self.config, "enable_bandwidth_monitoring", True
            ),
            enable_latency_tracking=getattr(
                self.config, "enable_latency_tracking", True
            ),
            enable_latency_measurement=getattr(
                self.config, "enable_latency_measurement", True
            ),
            enable_error_counting=getattr(self.config, "enable_error_counting", True),
            enable_error_rate_tracking=getattr(
                self.config, "enable_error_rate_tracking", True
            ),
            enable_performance_grading=getattr(
                self.config, "enable_performance_grading", True
            ),
            enable_perf_outputs=getattr(self.config, "enable_perf_outputs", True),
            # Set signal availability based on device type
            error_signals_available=True,
            network_signals_available=(device_type == "network"),
            storage_signals_available=(device_type == "storage"),
            graphics_signals_available=(device_type == "graphics"),
            generic_signals_available=True,
        )

    def _build_power_management_config(self) -> Any:
        builder = UnifiedContextBuilder(self.logger)
        return builder.create_power_management_config(
            enable_power_management=getattr(self.config, "power_management", True),
            has_interface_signals=getattr(
                self.config, "has_power_interface_signals", False
            ),
        )

    def _build_error_handling_config(self) -> Any:
        builder = UnifiedContextBuilder(self.logger)
        return builder.create_error_handling_config(
            enable_error_detection=getattr(self.config, "error_handling", True),
        )

    def _build_device_specific_signals(self, device_type: str) -> Dict[str, Any]:

        builder = UnifiedContextBuilder(self.logger)
        device_signals = builder.create_device_specific_signals(
            device_type=device_type,
        )

        return device_signals.to_dict()

    def _build_variance_model(self) -> Any:
        # Enable variance modeling when configured for manufacturing process simulation
        enable_variance = getattr(self.config, "enable_variance", False)

        variance_data = {
            "enabled": enable_variance,
            "variance_type": "normal",
            "process_variation": 0.1,  # Required by template
            "temperature_coefficient": 0.05,  # Required by template
            "voltage_variation": 0.03,  # Required by template
            "parameters": {
                "mean": 0.0,
                "std_dev": 0.1,
                "min_value": -1.0,
                "max_value": 1.0,
            },
        }

        return TemplateObject(variance_data)

    def _get_device_type_from_class_code(self, class_code: str) -> str:
        """Get device type string from PCI class code."""
        if class_code.startswith("01"):
            return "storage"
        elif class_code.startswith("02"):
            return "network"
        elif class_code.startswith("03"):
            return "graphics"
        elif class_code.startswith("04"):
            return "generic"
        elif class_code.startswith("0c"):
            return "serial_bus"
        else:
            return "generic"
