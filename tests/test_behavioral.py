#!/usr/bin/env python3
"""Tests for behavioral device simulation."""

import pytest
from typing import Dict, Any
from unittest.mock import Mock, MagicMock

from src.behavioral.base import (
    BehavioralSpec,
    BehavioralRegister,
    BehavioralCounter,
    BehaviorType
)
from src.behavioral.network_behavioral import NetworkBehavioralAnalyzer
from src.behavioral.storage_behavioral import StorageBehavioralAnalyzer
from src.behavioral.media_behavioral import MediaBehavioralAnalyzer
from src.behavioral.analyzer import BehavioralAnalyzerFactory
from src.utils.behavioral_context import build_behavioral_context


class TestBehavioralBase:
    """Test behavioral base infrastructure."""
    
    def test_behavioral_register_creation(self):
        """Test creating behavioral register."""
        reg = BehavioralRegister(
            name="test_reg",
            offset=0x1000,
            behavior=BehaviorType.CONSTANT,
            default_value=0xDEADBEEF,
            description="Test register"
        )
        
        assert reg.name == "test_reg"
        assert reg.offset == 0x1000
        assert reg.behavior == BehaviorType.CONSTANT
        assert reg.default_value == 0xDEADBEEF
        
    def test_behavioral_register_to_dict(self):
        """Test converting behavioral register to dict."""
        reg = BehavioralRegister(
            name="test_reg",
            offset=0x1000,
            behavior=BehaviorType.AUTO_INCREMENT,
            default_value=0x12345678,
            pattern="counter[15:0]",
            counter_bits=16,
            description="Test auto-increment register"
        )
        
        reg_dict = reg.to_dict()
        assert reg_dict["offset"] == 0x1000
        assert reg_dict["behavior"] == "auto_increment"
        assert reg_dict["default"] == 0x12345678
        assert reg_dict["pattern"] == "counter[15:0]"
        assert reg_dict["counter_bits"] == 16
        
    def test_behavioral_counter_creation(self):
        """Test creating behavioral counter."""
        counter = BehavioralCounter(
            name="test_counter",
            width=32,
            increment_rate=1,
            reset_value=0,
            description="Test counter"
        )
        
        assert counter.name == "test_counter"
        assert counter.width == 32
        assert counter.increment_rate == 1
        assert counter.reset_value == 0
        
    def test_behavioral_spec_creation(self):
        """Test creating behavioral spec."""
        spec = BehavioralSpec("test_device")
        assert spec.device_category == "test_device"
        assert len(spec.registers) == 0
        assert len(spec.counters) == 0
        
    def test_behavioral_spec_add_register(self):
        """Test adding register to spec."""
        spec = BehavioralSpec("test_device")
        
        reg = BehavioralRegister(
            name="test_reg",
            offset=0x0000,
            behavior=BehaviorType.CONSTANT,
            default_value=0x00000001
        )
        
        spec.add_register(reg)
        assert "test_reg" in spec.registers
        assert spec.registers["test_reg"].offset == 0x0000
        
    def test_behavioral_spec_add_counter(self):
        """Test adding counter to spec."""
        spec = BehavioralSpec("test_device")
        
        counter = BehavioralCounter(
            name="test_counter",
            width=32,
            increment_rate=1
        )
        
        spec.add_counter(counter)
        assert "test_counter" in spec.counters
        assert spec.counters["test_counter"].width == 32
        
    def test_behavioral_spec_validation_success(self):
        """Test behavioral spec validation succeeds with no conflicts."""
        spec = BehavioralSpec("test_device")
        
        # Add non-conflicting registers
        spec.add_register(BehavioralRegister(
            name="reg1",
            offset=0x0000,
            behavior=BehaviorType.CONSTANT,
            default_value=0x12345678
        ))
        
        spec.add_register(BehavioralRegister(
            name="reg2",
            offset=0x0004,
            behavior=BehaviorType.WRITE_CAPTURE
        ))
        
        assert spec.validate()
        
    def test_behavioral_spec_validation_offset_conflict(self):
        """Test detection of offset conflicts."""
        spec = BehavioralSpec("test_device")
        
        # Add conflicting registers
        spec.add_register(BehavioralRegister(
            name="reg1",
            offset=0x1000,
            behavior=BehaviorType.CONSTANT
        ))
        
        spec.add_register(BehavioralRegister(
            name="reg2",
            offset=0x1000,  # Same offset - conflict!
            behavior=BehaviorType.WRITE_CAPTURE
        ))
        
        assert not spec.validate()
        
    def test_behavioral_spec_to_dict(self):
        """Test converting behavioral spec to dict."""
        spec = BehavioralSpec("ethernet")
        
        spec.add_register(BehavioralRegister(
            name="link_status",
            offset=0x0000,
            behavior=BehaviorType.CONSTANT,
            default_value=0x00000001
        ))
        
        spec.add_counter(BehavioralCounter(
            name="rx_counter",
            width=32,
            increment_rate=1
        ))
        
        spec_dict = spec.to_dict()
        assert spec_dict["device_category"] == "ethernet"
        assert "link_status" in spec_dict["registers"]
        assert "rx_counter" in spec_dict["counters"]


class TestNetworkBehavioral:
    """Test network device behavioral simulation."""
    
    @pytest.fixture
    def network_config(self):
        """Mock network device configuration."""
        config = Mock()
        config.class_code = 0x020000  # Network controller
        config.device_id = "1234"
        config.subclass_code = 0x00  # Ethernet
        return config
        
    def test_ethernet_behavioral_generation(self, network_config):
        """Test Ethernet controller behavioral generation."""
        analyzer = NetworkBehavioralAnalyzer(network_config)
        spec = analyzer.generate_spec()
        
        assert spec is not None
        assert spec.device_category == "ethernet"
        assert "link_status" in spec.registers
        assert "rx_data" in spec.registers
        assert "tx_data" in spec.registers
        assert "mac_addr_low" in spec.registers
        assert "mac_addr_high" in spec.registers
        
    def test_ethernet_link_status_constant(self, network_config):
        """Test link status is constant and up."""
        analyzer = NetworkBehavioralAnalyzer(network_config)
        spec = analyzer.generate_spec()
        
        link_status = spec.registers["link_status"]
        assert link_status.behavior == BehaviorType.CONSTANT
        assert link_status.default_value == 0x00000001
        
    def test_ethernet_rx_data_auto_increment(self, network_config):
        """Test RX data auto-increments."""
        analyzer = NetworkBehavioralAnalyzer(network_config)
        spec = analyzer.generate_spec()
        
        rx_data = spec.registers["rx_data"]
        assert rx_data.behavior == BehaviorType.AUTO_INCREMENT
        assert rx_data.pattern is not None
        assert "rx_counter" in rx_data.pattern
        
    def test_ethernet_tx_data_write_capture(self, network_config):
        """Test TX data captures writes."""
        analyzer = NetworkBehavioralAnalyzer(network_config)
        spec = analyzer.generate_spec()
        
        tx_data = spec.registers["tx_data"]
        assert tx_data.behavior == BehaviorType.WRITE_CAPTURE
        assert tx_data.default_value == 0x00000000
        
    def test_ethernet_has_counters(self, network_config):
        """Test Ethernet spec includes required counters."""
        analyzer = NetworkBehavioralAnalyzer(network_config)
        spec = analyzer.generate_spec()
        
        assert "rx_counter" in spec.counters
        assert "rx_packet_counter" in spec.counters
        assert "tx_packet_counter" in spec.counters
        
        # Verify counter properties
        rx_counter = spec.counters["rx_counter"]
        assert rx_counter.width == 32
        assert rx_counter.increment_rate == 1


class TestStorageBehavioral:
    """Test storage device behavioral simulation."""
    
    @pytest.fixture
    def storage_config(self):
        """Mock storage device configuration."""
        config = Mock()
        config.class_code = 0x010802  # NVMe controller
        config.device_id = "5678"
        return config
        
    def test_nvme_behavioral_generation(self, storage_config):
        """Test NVMe controller behavioral generation."""
        analyzer = StorageBehavioralAnalyzer(storage_config)
        spec = analyzer.generate_spec()
        
        assert spec is not None
        assert spec.device_category == "nvme"
        assert "controller_status" in spec.registers
        assert "admin_queue_attrs" in spec.registers
        assert "completion_queue_head" in spec.registers
        assert "submission_queue_tail" in spec.registers
        
    def test_nvme_controller_status_ready(self, storage_config):
        """Test controller status shows ready."""
        analyzer = StorageBehavioralAnalyzer(storage_config)
        spec = analyzer.generate_spec()
        
        status = spec.registers["controller_status"]
        assert status.behavior == BehaviorType.CONSTANT
        assert status.default_value == 0x00000001
        
    def test_nvme_completion_queue_auto_increment(self, storage_config):
        """Test completion queue head auto-increments."""
        analyzer = StorageBehavioralAnalyzer(storage_config)
        spec = analyzer.generate_spec()
        
        cq_head = spec.registers["completion_queue_head"]
        assert cq_head.behavior == BehaviorType.AUTO_INCREMENT
        assert cq_head.pattern is not None
        
    def test_nvme_submission_queue_write_capture(self, storage_config):
        """Test submission queue tail captures writes."""
        analyzer = StorageBehavioralAnalyzer(storage_config)
        spec = analyzer.generate_spec()
        
        sq_tail = spec.registers["submission_queue_tail"]
        assert sq_tail.behavior == BehaviorType.WRITE_CAPTURE


class TestBehavioralFactory:
    """Test behavioral analyzer factory."""
    
    def test_factory_network_device(self):
        """Test factory creates network analyzer."""
        config = Mock()
        config.class_code = 0x020000
        
        analyzer = BehavioralAnalyzerFactory.create_analyzer(config)
        assert analyzer is not None
        assert isinstance(analyzer, NetworkBehavioralAnalyzer)
        
    def test_factory_storage_device(self):
        """Test factory creates storage analyzer."""
        config = Mock()
        config.class_code = 0x010802
        
        analyzer = BehavioralAnalyzerFactory.create_analyzer(config)
        assert analyzer is not None
        assert isinstance(analyzer, StorageBehavioralAnalyzer)
        
    def test_factory_unsupported_device(self):
        """Test factory returns None for unsupported device."""
        config = Mock()
        config.class_code = 0xFF0000  # Unknown class
        
        analyzer = BehavioralAnalyzerFactory.create_analyzer(config)
        assert analyzer is None
        
    def test_factory_generate_spec_enabled(self):
        """Test factory generates spec when enabled."""
        config = Mock()
        config.enable_behavioral_simulation = True
        config.class_code = 0x020000
        config.device_id = "1234"
        
        spec = BehavioralAnalyzerFactory.generate_behavioral_spec(config)
        assert spec is not None
        assert spec.device_category == "ethernet"
        
    def test_factory_generate_spec_disabled(self):
        """Test factory returns None when disabled."""
        config = Mock()
        config.enable_behavioral_simulation = False
        config.class_code = 0x020000
        
        spec = BehavioralAnalyzerFactory.generate_behavioral_spec(config)
        assert spec is None


class TestBehavioralContext:
    """Test behavioral context integration."""
    
    def test_behavioral_context_enabled(self):
        """Test context generation when enabled."""
        config = Mock()
        config.enable_behavioral_simulation = True
        config.class_code = 0x020000
        config.device_id = "1234"
        config.behavioral_bar_index = 2
        
        context = build_behavioral_context(config)
        
        assert context.get("enable_behavioral_simulation") is True
        assert "behavioral_spec" in context
        assert context["behavioral_bar_index"] == 2
        
        spec = context["behavioral_spec"]
        assert spec["device_category"] == "ethernet"
        assert "registers" in spec
        assert "counters" in spec
        
    def test_behavioral_context_disabled(self):
        """Test context contains minimal spec when disabled."""
        config = Mock()
        config.enable_behavioral_simulation = False
        config.class_code = 0x020000
        
        context = build_behavioral_context(config)
        assert context.get("enable_behavioral_simulation") is False
        assert "behavioral_spec" in context
        spec = context["behavioral_spec"]
        assert spec["device_category"] == "generic"
        assert spec["registers"] == {}
        assert spec["counters"] == {}
        
    def test_behavioral_context_unsupported_device(self):
        """Test context is empty for unsupported devices."""
        config = Mock()
        config.enable_behavioral_simulation = True
        config.class_code = 0xFF0000  # Unknown class
        
        context = build_behavioral_context(config)
        assert context == {}
        
    def test_behavioral_context_register_details(self):
        """Test context contains detailed register information."""
        config = Mock()
        config.enable_behavioral_simulation = True
        config.class_code = 0x020000
        config.device_id = "1234"
        config.behavioral_bar_index = 0
        
        context = build_behavioral_context(config)
        spec = context["behavioral_spec"]
        
        # Check link_status register details
        link_status = spec["registers"]["link_status"]
        assert link_status["offset"] == 0x0000
        assert link_status["behavior"] == "constant"
        assert link_status["default"] == 0x00000001
        
    def test_behavioral_context_counter_details(self):
        """Test context contains detailed counter information."""
        config = Mock()
        config.enable_behavioral_simulation = True
        config.class_code = 0x020000
        config.device_id = "1234"
        config.behavioral_bar_index = 0
        
        context = build_behavioral_context(config)
        spec = context["behavioral_spec"]
        
        # Check rx_counter details
        rx_counter = spec["counters"]["rx_counter"]
        assert rx_counter["width"] == 32
        assert rx_counter["increment_rate"] == 1
        assert rx_counter["reset_value"] == 0


class TestBehaviorTypes:
    """Test behavior type enum."""
    
    def test_behavior_type_values(self):
        """Test behavior type enum values."""
        assert BehaviorType.CONSTANT.value == "constant"
        assert BehaviorType.AUTO_INCREMENT.value == "auto_increment"
        assert BehaviorType.WRITE_CAPTURE.value == "write_capture"
        assert BehaviorType.RANDOM.value == "random"
        assert BehaviorType.PATTERN.value == "pattern"
        assert BehaviorType.TRIGGERED.value == "triggered"
        assert BehaviorType.PERIODIC.value == "periodic"


class TestEdgeCases:
    """Test edge cases and error conditions."""
    
    def test_spec_with_no_registers(self):
        """Test spec validation with no registers."""
        spec = BehavioralSpec("empty")
        assert spec.validate()  # Should still be valid
        
    def test_spec_with_only_counters(self):
        """Test spec with counters but no registers."""
        spec = BehavioralSpec("counter_only")
        spec.add_counter(BehavioralCounter("counter1", 32))
        assert spec.validate()
        
    def test_register_with_minimal_fields(self):
        """Test register with only required fields."""
        reg = BehavioralRegister(
            name="minimal",
            offset=0x0000,
            behavior=BehaviorType.CONSTANT
        )
        assert reg.default_value == 0x00000000
        assert reg.pattern is None
        assert reg.counter_bits is None
        assert reg.description == ""
        assert reg.read_only is False


class TestMediaBehavioral:
    """Test media device behavioral simulation."""
    
    @pytest.fixture
    def audio_config(self):
        """Mock audio device configuration."""
        config = Mock()
        config.class_code = 0x040100  # Multimedia audio controller
        config.device_id = "ABCD"
        config.subclass_code = 0x01  # Audio
        return config
        
    @pytest.fixture
    def video_config(self):
        """Mock video device configuration."""
        config = Mock()
        config.class_code = 0x040000  # Multimedia video controller
        config.device_id = "EFGH"
        config.subclass_code = 0x00  # Video
        return config
        
    @pytest.fixture
    def media_config(self):
        """Mock generic media device configuration."""
        config = Mock()
        config.class_code = 0x040300  # Multimedia controller
        config.device_id = "1357"
        config.subclass_code = 0x03  # Generic
        return config
        
    def test_audio_behavioral_generation(self, audio_config):
        """Test audio controller behavioral generation."""
        analyzer = MediaBehavioralAnalyzer(audio_config)
        spec = analyzer.generate_spec()
        
        assert spec is not None
        assert spec.device_category == "audio"
        assert "codec_status" in spec.registers
        assert "audio_stream_status" in spec.registers
        assert "audio_buffer_position" in spec.registers
        # Should NOT have video registers
        assert "video_frame_counter" not in spec.registers
        
    def test_video_behavioral_generation(self, video_config):
        """Test video controller behavioral generation."""
        analyzer = MediaBehavioralAnalyzer(video_config)
        spec = analyzer.generate_spec()
        
        assert spec is not None
        assert spec.device_category == "video"
        assert "codec_status" in spec.registers
        assert "video_stream_status" in spec.registers
        assert "video_frame_counter" in spec.registers
        # Should NOT have audio registers
        assert "audio_buffer_position" not in spec.registers
        
    def test_generic_media_behavioral_generation(self, media_config):
        """Test generic media controller behavioral generation."""
        analyzer = MediaBehavioralAnalyzer(media_config)
        spec = analyzer.generate_spec()
        
        assert spec is not None
        assert spec.device_category == "media"
        assert "codec_status" in spec.registers
        
    def test_media_codec_status_ready(self, audio_config):
        """Test codec status shows ready."""
        analyzer = MediaBehavioralAnalyzer(audio_config)
        spec = analyzer.generate_spec()
        
        codec_status = spec.registers["codec_status"]
        assert codec_status.behavior == BehaviorType.CONSTANT
        assert codec_status.default_value == 0x00000003  # Ready + initialized
        
    def test_media_audio_buffer_auto_increment(self, audio_config):
        """Test audio buffer position auto-increments."""
        analyzer = MediaBehavioralAnalyzer(audio_config)
        spec = analyzer.generate_spec()
        
        audio_pos = spec.registers["audio_buffer_position"]
        assert audio_pos.behavior == BehaviorType.AUTO_INCREMENT
        assert audio_pos.pattern is not None
        assert "audio_position_counter" in audio_pos.pattern
        
    def test_media_video_frame_counter(self, video_config):
        """Test video frame counter auto-increments."""
        analyzer = MediaBehavioralAnalyzer(video_config)
        spec = analyzer.generate_spec()
        
        frame_counter = spec.registers["video_frame_counter"]
        assert frame_counter.behavior == BehaviorType.AUTO_INCREMENT
        assert frame_counter.pattern is not None
        
    def test_media_dma_status_ready(self, audio_config):
        """Test DMA status shows ready."""
        analyzer = MediaBehavioralAnalyzer(audio_config)
        spec = analyzer.generate_spec()
        
        dma_status = spec.registers["dma_status"]
        assert dma_status.behavior == BehaviorType.CONSTANT
        assert dma_status.default_value == 0x00000001
        
    def test_media_writable_registers(self, audio_config):
        """Test writable control registers."""
        analyzer = MediaBehavioralAnalyzer(audio_config)
        spec = analyzer.generate_spec()
        
        # Interrupt control should be writable
        int_status = spec.registers["interrupt_status"]
        assert int_status.behavior == BehaviorType.WRITE_CAPTURE
        
        # Volume control should be writable
        volume = spec.registers["volume_control"]
        assert volume.behavior == BehaviorType.WRITE_CAPTURE
        assert volume.default_value == 0x00008080  # 50% volume
        
    def test_media_has_counters(self, audio_config):
        """Test media spec includes required counters."""
        analyzer = MediaBehavioralAnalyzer(audio_config)
        spec = analyzer.generate_spec()
        
        assert "audio_position_counter" in spec.counters
        assert "dma_buffer_counter" in spec.counters
        assert "buffer_level_counter" in spec.counters
        assert "frames_processed_counter" in spec.counters
        assert "bytes_transferred_counter" in spec.counters
        
        # Verify counter properties
        audio_counter = spec.counters["audio_position_counter"]
        assert audio_counter.width == 16
        assert audio_counter.increment_rate == 64  # Sample block size
        
    def test_media_resolution_register(self, video_config):
        """Test video resolution register."""
        analyzer = MediaBehavioralAnalyzer(video_config)
        spec = analyzer.generate_spec()
        
        resolution = spec.registers["video_resolution"]
        assert resolution.behavior == BehaviorType.CONSTANT
        assert resolution.default_value == 0x07800438  # 1920x1080
        
    def test_media_sample_rate_register(self, audio_config):
        """Test audio sample rate register."""
        analyzer = MediaBehavioralAnalyzer(audio_config)
        spec = analyzer.generate_spec()
        
        sample_rate = spec.registers["audio_sample_rate"]
        assert sample_rate.behavior == BehaviorType.CONSTANT
        assert sample_rate.default_value == 0x0000AC44  # 44.1 kHz
        
    def test_factory_media_device(self):
        """Test factory creates media analyzer."""
        config = Mock()
        config.class_code = 0x040100  # Multimedia audio
        
        analyzer = BehavioralAnalyzerFactory.create_analyzer(config)
        assert analyzer is not None
        assert isinstance(analyzer, MediaBehavioralAnalyzer)
        
    def test_media_context_generation(self):
        """Test media behavioral context generation."""
        config = Mock()
        config.enable_behavioral_simulation = True
        config.class_code = 0x040100  # Multimedia audio
        config.device_id = "ABCD"
        config.subclass_code = 0x01
        config.behavioral_bar_index = 0
        
        context = build_behavioral_context(config)
        
        assert context.get("enable_behavioral_simulation") is True
        assert "behavioral_spec" in context
        
        spec = context["behavioral_spec"]
        assert spec["device_category"] == "audio"
        assert "codec_status" in spec["registers"]
        assert "audio_position_counter" in spec["counters"]

