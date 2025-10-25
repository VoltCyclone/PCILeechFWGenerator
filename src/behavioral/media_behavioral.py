#!/usr/bin/env python3
"""Behavioral simulation for media controllers (audio/video)."""

import logging
from typing import Dict, Any, Optional

from src.string_utils import log_info_safe, safe_format
from .base import (
    BehavioralSpec,
    BehavioralRegister,
    BehavioralCounter,
    BehaviorType
)


logger = logging.getLogger(__name__)


class MediaBehavioralAnalyzer:
    """Generate behavioral specs for media controllers."""
    
    # Audio/Video controller registers
    MEDIA_REGISTERS = {
        # Codec status and control
        "codec_status": {
            "offset": 0x0000,
            "behavior": BehaviorType.CONSTANT,
            "value": 0x00000003,  # Codec ready + initialized
            "description": "Codec status (bits: 0=ready, 1=initialized)"
        },
        "codec_version": {
            "offset": 0x0004,
            "behavior": BehaviorType.CONSTANT,
            "value": 0x00010203,  # Version 1.2.3
            "description": "Codec version register"
        },
        
        # Audio stream registers
        "audio_stream_status": {
            "offset": 0x0010,
            "behavior": BehaviorType.CONSTANT,
            "value": 0x00000001,  # Stream ready
            "description": "Audio stream status"
        },
        "audio_buffer_position": {
            "offset": 0x0014,
            "behavior": BehaviorType.AUTO_INCREMENT,
            "pattern": "audio_position_counter[15:0]",
            "counter_bits": 16,
            "description": "Audio buffer position pointer"
        },
        "audio_sample_rate": {
            "offset": 0x0018,
            "behavior": BehaviorType.CONSTANT,
            "value": 0x0000AC44,  # 44.1 kHz
            "description": "Audio sample rate (Hz)"
        },
        "audio_channels": {
            "offset": 0x001C,
            "behavior": BehaviorType.CONSTANT,
            "value": 0x00000002,  # Stereo
            "description": "Number of audio channels"
        },
        
        # Video stream registers
        "video_stream_status": {
            "offset": 0x0020,
            "behavior": BehaviorType.CONSTANT,
            "value": 0x00000001,  # Stream ready
            "description": "Video stream status"
        },
        "video_frame_counter": {
            "offset": 0x0024,
            "behavior": BehaviorType.AUTO_INCREMENT,
            "pattern": "video_frame_counter",
            "description": "Video frame counter"
        },
        "video_resolution": {
            "offset": 0x0028,
            "behavior": BehaviorType.CONSTANT,
            "value": 0x07800438,  # 1920x1080
            "description": "Video resolution (width<<16 | height)"
        },
        "video_fps": {
            "offset": 0x002C,
            "behavior": BehaviorType.CONSTANT,
            "value": 0x0000003C,  # 60 FPS
            "description": "Video frames per second"
        },
        
        # DMA and buffer management
        "dma_status": {
            "offset": 0x0030,
            "behavior": BehaviorType.CONSTANT,
            "value": 0x00000001,  # DMA ready
            "description": "DMA engine status"
        },
        "dma_buffer_count": {
            "offset": 0x0034,
            "behavior": BehaviorType.AUTO_INCREMENT,
            "pattern": "dma_buffer_counter[7:0]",
            "counter_bits": 8,
            "description": "DMA buffer completion counter"
        },
        "buffer_level": {
            "offset": 0x0038,
            "behavior": BehaviorType.AUTO_INCREMENT,
            "pattern": "32'h0000 | buffer_level_counter[15:0]",
            "counter_bits": 16,
            "description": "Current buffer fill level"
        },
        
        # Interrupt and event registers
        "interrupt_status": {
            "offset": 0x0040,
            "behavior": BehaviorType.WRITE_CAPTURE,
            "default": 0x00000000,
            "description": "Interrupt status (write 1 to clear)"
        },
        "interrupt_enable": {
            "offset": 0x0044,
            "behavior": BehaviorType.WRITE_CAPTURE,
            "default": 0x00000000,
            "description": "Interrupt enable mask"
        },
        
        # Performance monitoring
        "frames_processed": {
            "offset": 0x0050,
            "behavior": BehaviorType.AUTO_INCREMENT,
            "pattern": "frames_processed_counter",
            "description": "Total frames processed"
        },
        "bytes_transferred": {
            "offset": 0x0054,
            "behavior": BehaviorType.AUTO_INCREMENT,
            "pattern": "bytes_transferred_counter",
            "description": "Total bytes transferred"
        },
        
        # Codec configuration (writable)
        "codec_control": {
            "offset": 0x0060,
            "behavior": BehaviorType.WRITE_CAPTURE,
            "default": 0x00000001,  # Default: enabled
            "description": "Codec control register"
        },
        "volume_control": {
            "offset": 0x0064,
            "behavior": BehaviorType.WRITE_CAPTURE,
            "default": 0x00008080,  # 50% volume for L/R
            "description": "Volume control (left<<8 | right)"
        },
    }
    
    def __init__(self, device_config: Any):
        self._device_config = device_config
        self._subclass = getattr(device_config, 'subclass_code', 0)
        
    def _is_audio_device(self) -> bool:
        """Check if device is an audio controller."""
        # PCI class 0x04 subclass 0x01 (multimedia audio controller)
        return (self._subclass & 0xFF) == 0x01
        
    def _is_video_device(self) -> bool:
        """Check if device is a video controller."""
        # PCI class 0x04 subclass 0x00 (multimedia video controller)
        return (self._subclass & 0xFF) == 0x00
    
    def generate_spec(self) -> Optional[BehavioralSpec]:
        """Generate behavioral specification for media device."""
        log_info_safe(logger, safe_format("Generating media behavioral spec for device={dev}",
                                 dev=getattr(self._device_config, 'device_id', 'unknown')))
        
        # Determine device category
        if self._is_audio_device():
            category = "audio"
        elif self._is_video_device():
            category = "video"
        else:
            category = "media"  # Generic media
            
        spec = BehavioralSpec(category)
        
        # Add all media registers
        for name, reg_def in self.MEDIA_REGISTERS.items():
            # Skip video-specific registers for audio-only devices
            if self._is_audio_device() and not self._is_video_device():
                if name.startswith("video_"):
                    continue
                    
            # Skip audio-specific registers for video-only devices
            if self._is_video_device() and not self._is_audio_device():
                if name.startswith("audio_"):
                    continue
            
            register = BehavioralRegister(
                name=name,
                offset=reg_def["offset"],
                behavior=reg_def["behavior"],
                default_value=reg_def.get("value", reg_def.get("default", 0)),
                pattern=reg_def.get("pattern"),
                counter_bits=reg_def.get("counter_bits"),
                description=reg_def["description"]
            )
            spec.add_register(register)
            
        # Add counters
        spec.add_counter(BehavioralCounter(
            name="audio_position_counter",
            width=16,
            increment_rate=64,  # Increment by sample block size
            description="Audio buffer position counter"
        ))
        
        spec.add_counter(BehavioralCounter(
            name="video_frame_counter",
            width=32,
            increment_rate=1,
            description="Video frame counter"
        ))
        
        spec.add_counter(BehavioralCounter(
            name="dma_buffer_counter",
            width=8,
            increment_rate=1,
            description="DMA buffer completion counter"
        ))
        
        spec.add_counter(BehavioralCounter(
            name="buffer_level_counter",
            width=16,
            increment_rate=128,  # Buffer fill increment
            description="Buffer level counter"
        ))
        
        spec.add_counter(BehavioralCounter(
            name="frames_processed_counter",
            width=32,
            increment_rate=1,
            description="Total frames processed"
        ))
        
        spec.add_counter(BehavioralCounter(
            name="bytes_transferred_counter",
            width=32,
            increment_rate=1024,  # 1KB per cycle
            description="Bytes transferred counter"
        ))
        
        # Validate and return
        if not spec.validate():
            from src.string_utils import log_error_safe
            log_error_safe(logger, "Failed to validate media behavioral spec")
            return None
            
        return spec
