#!/usr/bin/env python3
"""
Media Function Capabilities

This module provides dynamic media function capabilities for PCIe device
generation. It analyzes build-time provided vendor/device IDs to generate
realistic audio, video, and multimedia function capabilities without hardcoding.

The module integrates with the existing templating and logging infrastructure
to provide production-ready dynamic capability generation.
"""

import logging

from typing import Any, Dict, List, Optional, Set

from ..string_utils import (log_debug_safe, log_error_safe, log_info_safe,
                            log_warning_safe, safe_format)

from .base_function_analyzer import (BaseFunctionAnalyzer,
                                     create_function_capabilities)

from .constants import (  # Common PCI Capability IDs; Media class codes; Common device ID masks; Media-specific device ranges; Media device thresholds; Media BAR sizes; Media power management; Media queue counts; Audio specifications; Video specifications; PCIe defaults; Additional vendor IDs
    AMD_AUDIO_RANGES, BAR_SIZE_AUDIO_REGISTERS, BAR_SIZE_HDAUDIO_REGISTERS,
    BAR_SIZE_MSIX_TABLE, BAR_SIZE_VIDEO_FRAMEBUFFER, BAR_SIZE_VIDEO_REGISTERS,
    BIT_DEPTHS_BASIC_AUDIO, BIT_DEPTHS_HDAUDIO, CAP_ID_MSI, CAP_ID_MSIX,
    CAP_ID_PCIE, CAP_ID_PM, CAP_ID_VENDOR_SPECIFIC, CHANNELS_MULTICHANNEL,
    CHANNELS_STEREO, DEFAULT_PCIE_MAX_PAYLOAD_SIZE, DEVICE_ID_ENTROPY_MASK,
    DEVICE_ID_LOWER_MASK, DEVICE_UPPER_HDAUDIO_THRESHOLD,
    DEVICE_UPPER_VIDEO_THRESHOLD, FRAME_RATES_BASIC, FRAME_RATES_HIGH,
    HDAUDIO_AUX_CURRENT_MA, HDAUDIO_MULTICHANNEL_THRESHOLD,
    HIGH_END_DEVICE_THRESHOLD, INTEL_HDAUDIO_RANGES, INTEL_VIDEO_RANGES,
    MEDIA_CLASS_CODES, NVIDIA_HDMI_AUDIO_RANGES, NVIDIA_VIDEO_RANGES,
    QUEUE_COUNT_AUDIO, QUEUE_COUNT_HDAUDIO_BASIC, QUEUE_COUNT_HDAUDIO_HIGH,
    QUEUE_COUNT_MIN, QUEUE_COUNT_VIDEO, SAMPLE_RATES_BASIC_AUDIO,
    SAMPLE_RATES_HDAUDIO, VENDOR_CAP_DEVICE_THRESHOLD, VENDOR_ID_CMEDIA,
    VENDOR_ID_CREATIVE, VIDEO_HARDWARE_ENCODING_THRESHOLD,
    VIDEO_HIGH_FRAMERATE_THRESHOLD)

logger = logging.getLogger(__name__)


class MediaFunctionAnalyzer(BaseFunctionAnalyzer):
    """
    Dynamic media function capability analyzer.

    Analyzes vendor/device IDs provided at build time to generate realistic
    media function capabilities without hardcoding device-specific behavior.
    """

    # Media-specific capability IDs
    VENDOR_CAP_ID = CAP_ID_VENDOR_SPECIFIC  # Vendor-specific capability

    # PCI class codes for media devices
    CLASS_CODES = MEDIA_CLASS_CODES

    def __init__(self, vendor_id: int, device_id: int):
        """
        Initialize analyzer with build-time provided vendor/device IDs.

        Args:
            vendor_id: PCI vendor ID from build process
            device_id: PCI device ID from build process
        """
        super().__init__(vendor_id, device_id, "media")

    def _analyze_device_category(self) -> str:
        """
        Analyze device category based on vendor/device ID patterns.

        Returns:
            Device category string (audio, video, hdaudio, other_media)
        """
        # Pattern-based analysis without hardcoding specific device IDs
        device_lower = self.device_id & DEVICE_ID_LOWER_MASK
        device_upper = (self.device_id >> 8) & 0xFF

        # Import vendor ID constants
        from src.device_clone.constants import (VENDOR_ID_AMD, VENDOR_ID_INTEL,
                                                VENDOR_ID_NVIDIA)

        # Vendor-specific patterns
        if self.vendor_id == VENDOR_ID_INTEL:  # Intel
            if device_lower in INTEL_HDAUDIO_RANGES:  # HD Audio ranges
                return "hdaudio"
            if device_lower in INTEL_VIDEO_RANGES:  # Video ranges
                return "video"
        if self.vendor_id == VENDOR_ID_NVIDIA:  # NVIDIA
            if device_lower in NVIDIA_HDMI_AUDIO_RANGES:  # Audio over HDMI
                return "hdaudio"
            if device_lower in NVIDIA_VIDEO_RANGES:  # Video capture
                return "video"
        if self.vendor_id == VENDOR_ID_AMD:  # AMD/ATI
            if device_lower in AMD_AUDIO_RANGES:  # Audio
                return "hdaudio"
        if self.vendor_id == VENDOR_ID_CMEDIA:  # C-Media
            return "audio"
        if self.vendor_id == VENDOR_ID_CREATIVE:  # Creative Labs
            return "audio"

        # Generic patterns based on device ID structure
        if device_upper >= DEVICE_UPPER_HDAUDIO_THRESHOLD:
            return "hdaudio"  # Higher device IDs often HD Audio
        if device_upper >= DEVICE_UPPER_VIDEO_THRESHOLD:
            return "video"  # Mid-range often video
        return "audio"  # Default to basic audio

    def _analyze_capabilities(self) -> Set[int]:
        """
        Analyze which capabilities this device should support.

        Returns:
            Set of capability IDs that should be present
        """
        caps = set()

        # Always include basic media capabilities
        caps.update([CAP_ID_PM, CAP_ID_MSI, CAP_ID_PCIE])  # PM, MSI, PCIe

        # MSI-X for high-end devices
        if self._is_high_end_device():
            caps.add(CAP_ID_MSIX)  # MSI-X

        # Vendor-specific capabilities for certain devices
        if self._supports_vendor_capability():
            caps.add(self.VENDOR_CAP_ID)

        return caps

    def _is_high_end_device(self) -> bool:
        """Check if this is a high-end media device."""
        return (
            self._device_category in ["hdaudio", "video"]
            and self.device_id > HIGH_END_DEVICE_THRESHOLD
        )

    def _supports_vendor_capability(self) -> bool:
        """Check if device supports vendor-specific capabilities."""
        # Vendor caps common in audio devices for DSP features
        return (
            self._device_category in ["audio", "hdaudio"]
            and self.device_id > VENDOR_CAP_DEVICE_THRESHOLD
        )

    def get_device_class_code(self) -> int:
        """Get appropriate PCI class code for this device."""
        return self.CLASS_CODES.get(self._device_category, self.CLASS_CODES["audio"])

    def _create_capability_by_id(self, cap_id: int) -> Optional[Dict[str, Any]]:
        """Create capability by ID, handling media-specific capabilities."""
        # Try base class capabilities first
        capability = super()._create_capability_by_id(cap_id)
        if capability:
            return capability

        # Handle media-specific capabilities
        if cap_id == self.VENDOR_CAP_ID:
            return self._create_vendor_capability()
        return None

    def _create_pm_capability(self, aux_current: int = 0) -> Dict[str, Any]:
        """Create Power Management capability for media devices."""
        # HD Audio may need aux power for always-on features
        aux_current = (
            HDAUDIO_AUX_CURRENT_MA if self._device_category == "hdaudio" else 0
        )
        return super()._create_pm_capability(aux_current)

    def _create_msi_capability(
        self,
        multi_message_capable: Optional[int] = None,
        supports_per_vector_masking: Optional[bool] = None,
    ) -> Dict[str, Any]:
        """Create MSI capability for media devices."""
        if multi_message_capable is None:
            # Most media devices need only single MSI
            multi_message_capable = 1 if not self._is_high_end_device() else 2

        return super()._create_msi_capability(
            multi_message_capable, supports_per_vector_masking
        )

    def _create_pcie_capability(
        self,
        max_payload_size: Optional[int] = None,
        supports_flr: bool = True,
    ) -> Dict[str, Any]:
        """Create PCIe Express capability for media devices."""
        if max_payload_size is None:
            # Conservative payload size for media devices
            max_payload_size = DEFAULT_PCIE_MAX_PAYLOAD_SIZE

        # HD Audio benefits from FLR
        supports_flr = self._device_category == "hdaudio"
        return super()._create_pcie_capability(max_payload_size, supports_flr)

    def _get_default_msix_bar_allocation(self) -> tuple[int, int]:
        """Get appropriate BAR allocation for MSI-X tables."""
        if self._device_category == "hdaudio":
            return (1, 1)  # HD Audio: Use BAR 1 for MSI-X
        return (0, 0)  # Other media: Use BAR 0

    def _calculate_default_queue_count(self) -> int:
        """Calculate appropriate queue count for media devices."""
        # Media devices typically need fewer queues
        if self._device_category == "hdaudio":
            base_queues = (
                QUEUE_COUNT_HDAUDIO_HIGH
                if self.device_id > HDAUDIO_MULTICHANNEL_THRESHOLD
                else QUEUE_COUNT_HDAUDIO_BASIC
            )
        elif self._device_category == "video":
            base_queues = QUEUE_COUNT_VIDEO
        else:
            base_queues = QUEUE_COUNT_AUDIO

        # Add entropy-based variation for security
        entropy_factor = (
            (self.vendor_id ^ self.device_id) & DEVICE_ID_ENTROPY_MASK
        ) / 16.0
        variation = int(base_queues * entropy_factor * 0.5)
        if (self.device_id & 0x1) == 0:
            variation = -variation

        final_queues = max(QUEUE_COUNT_MIN, base_queues + variation)
        return 1 << (final_queues - 1).bit_length()

    def _create_vendor_capability(self) -> Dict[str, Any]:
        """Create vendor-specific capability for media devices."""
        return {
            "cap_id": self.VENDOR_CAP_ID,
            "vendor_id": self.vendor_id,
            "length": 16,  # Basic vendor capability
            "vendor_data": f"MediaDSP_{self.device_id:04x}",
        }

    def generate_bar_configuration(self) -> List[Dict[str, Any]]:
        """Generate realistic BAR configuration for media device."""
        bars = []

        # Base register space - size based on device type
        if self._device_category == "hdaudio":
            # HD Audio needs larger register space
            base_size = BAR_SIZE_HDAUDIO_REGISTERS
            bars.append(
                {
                    "bar": 0,
                    "type": "memory",
                    "size": base_size,
                    "prefetchable": False,
                    "description": "HD Audio registers",
                }
            )

            # MSI-X table space if supported
            if CAP_ID_MSIX in self._capabilities:
                bars.append(
                    {
                        "bar": 1,
                        "type": "memory",
                        "size": BAR_SIZE_MSIX_TABLE,
                        "prefetchable": False,
                        "description": "MSI-X table",
                    }
                )
        elif self._device_category == "video":
            # Video devices need frame buffer space
            base_size = BAR_SIZE_VIDEO_FRAMEBUFFER
            bars.append(
                {
                    "bar": 0,
                    "type": "memory",
                    "size": base_size,
                    "prefetchable": True,
                    "description": "Video frame buffer",
                }
            )
            bars.append(
                {
                    "bar": 1,
                    "type": "memory",
                    "size": BAR_SIZE_VIDEO_REGISTERS,
                    "prefetchable": False,
                    "description": "Video registers",
                }
            )
        else:
            # Basic audio device
            base_size = BAR_SIZE_AUDIO_REGISTERS
            bars.append(
                {
                    "bar": 0,
                    "type": "memory",
                    "size": base_size,
                    "prefetchable": False,
                    "description": "Audio registers",
                }
            )

        return bars

    def generate_device_features(self) -> Dict[str, Any]:
        """Generate media-specific device features."""
        features = {
            "category": self._device_category,
            "queue_count": self._calculate_default_queue_count(),
        }

        # Category-specific features
        if self._device_category == "hdaudio":
            features.update(
                {
                    "codec_support": ["AC97", "HDA"],
                    "sample_rates": SAMPLE_RATES_HDAUDIO,
                    "bit_depths": BIT_DEPTHS_HDAUDIO,
                    "channels": (
                        CHANNELS_MULTICHANNEL
                        if self.device_id > HDAUDIO_MULTICHANNEL_THRESHOLD
                        else CHANNELS_STEREO
                    ),
                    "supports_dsp": self._supports_vendor_capability(),
                }
            )
        elif self._device_category == "video":
            features.update(
                {
                    "max_resolution": (
                        "4K" if self.device_id > HIGH_END_DEVICE_THRESHOLD else "1080p"
                    ),
                    "color_formats": ["RGB", "YUV420", "YUV422"],
                    "frame_rates": (
                        FRAME_RATES_HIGH
                        if self.device_id > VIDEO_HIGH_FRAMERATE_THRESHOLD
                        else FRAME_RATES_BASIC
                    ),
                    "supports_hardware_encoding": (
                        self.device_id > VIDEO_HARDWARE_ENCODING_THRESHOLD
                    ),
                }
            )
        elif self._device_category == "audio":
            features.update(
                {
                    "codec_support": ["AC97"],
                    "sample_rates": SAMPLE_RATES_BASIC_AUDIO,
                    "bit_depths": BIT_DEPTHS_BASIC_AUDIO,
                    "channels": CHANNELS_STEREO,
                    "supports_midi": True,
                }
            )

        # High-end features
        if self._is_high_end_device():
            features["supports_advanced_features"] = True

        return features


def create_media_function_capabilities(
    vendor_id: int, device_id: int
) -> Dict[str, Any]:
    """
    Factory function to create media function capabilities from build-time IDs.

    Args:
        vendor_id: PCI vendor ID from build process
        device_id: PCI device ID from build process

    Returns:
        Complete media device configuration dictionary
    """
    return create_function_capabilities(
        MediaFunctionAnalyzer, vendor_id, device_id, "MediaFunctionAnalyzer"
    )
