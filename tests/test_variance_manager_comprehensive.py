#!/usr/bin/env python3
"""
Comprehensive unit tests for src/device_clone/variance_manager.py

Tests manufacturing variance simulation, behavior profiling, and fallback scenarios
to improve test coverage from 15% to acceptable levels.
"""

import json
import logging
import tempfile
from pathlib import Path
from typing import Any, Dict, Optional
from unittest.mock import MagicMock, Mock, mock_open, patch

import pytest

from src.device_clone.behavior_profiler import (BehaviorProfile,
                                                RegisterAccess, TimingPattern)
from src.device_clone.manufacturing_variance import DeviceClass, VarianceModel
from src.device_clone.variance_manager import VarianceManager


class TestVarianceManagerInitialization:
    """Test VarianceManager initialization."""

    def test_initialization_basic(self):
        """Test basic VarianceManager initialization."""
        with tempfile.TemporaryDirectory() as temp_dir:
            manager = VarianceManager("0000:03:00.0", Path(temp_dir))

            assert manager.bdf == "0000:03:00.0"
            assert manager.output_dir == Path(temp_dir)
            assert manager.variance_simulator is None
            assert manager.behavior_profiler is None

    def test_initialization_with_custom_fallback_manager(self):
        """Test initialization with custom fallback manager."""
        mock_fallback = Mock()
        with tempfile.TemporaryDirectory() as temp_dir:
            manager = VarianceManager(
                "0000:03:00.0", Path(temp_dir), fallback_manager=mock_fallback
            )

            assert manager.fallback_manager == mock_fallback

    @patch("src.device_clone.fallback_manager.get_global_fallback_manager")
    def test_initialization_with_global_fallback_manager(self, mock_get_global):
        """Test initialization with global fallback manager."""
        mock_fallback = Mock()
        mock_get_global.return_value = mock_fallback

        with tempfile.TemporaryDirectory() as temp_dir:
            manager = VarianceManager("0000:03:00.0", Path(temp_dir))

            assert manager.fallback_manager == mock_fallback
            mock_get_global.assert_called_once_with(mode="none")

    @patch("src.device_clone.fallback_manager.get_global_fallback_manager")
    def test_initialization_fallback_import_error(self, mock_get_global):
        """Test initialization when fallback manager import fails."""
        mock_get_global.side_effect = ImportError("Module not found")

        with tempfile.TemporaryDirectory() as temp_dir:
            manager = VarianceManager("0000:03:00.0", Path(temp_dir))

            assert manager.fallback_manager is None


class TestManufacturingVarianceSimulation:
    """Test manufacturing variance simulation functionality."""

    @pytest.fixture
    def manager(self):
        """Create a VarianceManager for testing."""
        with tempfile.TemporaryDirectory() as temp_dir:
            yield VarianceManager("0000:03:00.0", Path(temp_dir))

    @patch("src.device_clone.variance_manager.VarianceModel")
    @patch("src.device_clone.variance_manager.DeviceClass")
    def test_apply_manufacturing_variance_ethernet_device(
        self, mock_device_class, mock_variance_model, manager
    ):
        """Test variance application for ethernet device."""
        # Mock device info for ethernet device
        device_info = {
            "device_id": "0x1234",
            "class_code": "0x0200",  # Ethernet
        }

        # Mock DeviceClass enum
        mock_device_class.ENTERPRISE = Mock()
        mock_device_class.ENTERPRISE.value = "enterprise"
        mock_device_class.CONSUMER = Mock()
        mock_device_class.CONSUMER.value = "consumer"

        # Mock VarianceModel constructor
        mock_model_instance = Mock()
        mock_model_instance.device_id = "0x1234"
        mock_model_instance.device_class = mock_device_class.ENTERPRISE
        mock_model_instance.base_frequency_mhz = 100.0
        mock_model_instance.clock_jitter_percent = 2.5
        mock_model_instance.register_timing_jitter_ns = 25.0
        mock_model_instance.power_noise_percent = 2.0
        mock_model_instance.temperature_drift_ppm_per_c = 50.0
        mock_model_instance.process_variation_percent = 10.0
        mock_model_instance.propagation_delay_ps = 100.0
        mock_variance_model.return_value = mock_model_instance

        result = manager.apply_manufacturing_variance(device_info)

        assert len(result) == 1
        variance_file = Path(result[0])
        assert variance_file.exists()
        assert variance_file.name == "manufacturing_variance.json"

        # Verify file content
        with open(variance_file) as f:
            variance_data = json.load(f)

        assert "device_class" in variance_data
        assert "variance_model" in variance_data
        assert variance_data["device_class"] == "enterprise"

    @patch("src.device_clone.variance_manager.json.dump")
    @patch("src.device_clone.variance_manager.VarianceModel")
    @patch("src.device_clone.variance_manager.DeviceClass")
    def test_apply_manufacturing_variance_audio_device(
        self, mock_device_class, mock_variance_model, mock_json_dump, manager
    ):
        """Test variance application for audio device."""
        device_info = {
            "device_id": "0x5678",
            "class_code": "0x0403",  # Audio
        }

        # Mock DeviceClass enum
        mock_consumer = Mock()
        mock_consumer.value = "consumer"
        mock_device_class.CONSUMER = mock_consumer

        # Mock VarianceModel to return a proper mock instance
        mock_model_instance = Mock()
        mock_model_instance.device_id = "0x5678"
        # Create a mock device_class that has a .value attribute
        mock_device_class_attr = Mock()
        mock_device_class_attr.value = "consumer"
        mock_model_instance.device_class = mock_device_class_attr
        mock_model_instance.base_frequency_mhz = 100.0
        mock_model_instance.clock_jitter_percent = 2.5
        mock_model_instance.register_timing_jitter_ns = 25.0
        mock_model_instance.power_noise_percent = 2.0
        mock_model_instance.temperature_drift_ppm_per_c = 50.0
        mock_model_instance.process_variation_percent = 10.0
        mock_model_instance.propagation_delay_ps = 100.0
        mock_variance_model.return_value = mock_model_instance

        result = manager.apply_manufacturing_variance(device_info)

        assert len(result) == 1

    @patch("src.device_clone.variance_manager.json.dump")
    @patch("src.device_clone.variance_manager.VarianceModel")
    @patch("src.device_clone.variance_manager.DeviceClass")
    def test_apply_manufacturing_variance_other_device(
        self, mock_device_class, mock_variance_model, mock_json_dump, manager
    ):
        """Test variance application for other device types."""
        device_info = {
            "device_id": "0x9abc",
            "class_code": "0x0100",  # Storage controller
        }

        # Mock DeviceClass enum
        mock_consumer = Mock()
        mock_consumer.value = "consumer"
        mock_device_class.CONSUMER = mock_consumer

        # Mock VarianceModel to return a proper mock instance
        mock_model_instance = Mock()
        mock_model_instance.device_id = "0x9abc"
        # Create a mock device_class that has a .value attribute
        mock_device_class_attr = Mock()
        mock_device_class_attr.value = "consumer"
        mock_model_instance.device_class = mock_device_class_attr
        mock_model_instance.base_frequency_mhz = 100.0
        mock_model_instance.clock_jitter_percent = 2.5
        mock_model_instance.register_timing_jitter_ns = 25.0
        mock_model_instance.power_noise_percent = 2.0
        mock_model_instance.temperature_drift_ppm_per_c = 50.0
        mock_model_instance.process_variation_percent = 10.0
        mock_model_instance.propagation_delay_ps = 100.0
        mock_variance_model.return_value = mock_model_instance

        result = manager.apply_manufacturing_variance(device_info)

        assert len(result) == 1

    def test_apply_manufacturing_variance_modules_unavailable(self, manager):
        """Test variance application when modules are unavailable."""
        device_info = {"device_id": "0x1234", "class_code": "0x0200"}

        # Mock unavailable modules
        with patch("src.device_clone.variance_manager.DeviceClass", None):
            with patch("src.device_clone.variance_manager.VarianceModel", None):
                result = manager.apply_manufacturing_variance(device_info)

        assert result == []

    def test_apply_manufacturing_variance_with_fallback_manager(self, manager):
        """Test variance application with fallback manager."""
        device_info = {"device_id": "0x1234", "class_code": "0x0200"}

        # Set up mock fallback manager
        mock_fallback = Mock()
        mock_fallback.confirm_fallback.return_value = True
        manager.fallback_manager = mock_fallback

        with patch("src.device_clone.variance_manager.DeviceClass", None):
            result = manager.apply_manufacturing_variance(device_info)

        assert result == []
        mock_fallback.confirm_fallback.assert_called()

    def test_apply_manufacturing_variance_exception_handling(self, manager):
        """Test variance application exception handling."""
        device_info = {"device_id": "0x1234", "class_code": "0x0200"}

        # Mock to raise exception
        with patch(
            "src.device_clone.variance_manager.DeviceClass"
        ) as mock_device_class:
            mock_device_class.side_effect = Exception("Test exception")

            result = manager.apply_manufacturing_variance(device_info)

        assert result == []


class TestBehaviorProfiling:
    """Test behavior profiling functionality."""

    @pytest.fixture
    def manager(self):
        """Create a VarianceManager for testing."""
        with tempfile.TemporaryDirectory() as temp_dir:
            yield VarianceManager("0000:03:00.0", Path(temp_dir))

    @patch("src.device_clone.variance_manager.BehaviorProfiler")
    def test_run_behavior_profiling_success(self, mock_profiler_class, manager):
        """Test successful behavior profiling."""
        # Mock device info
        device_info = {"device_id": "0x1234", "vendor_id": "0x10de"}

        # Mock BehaviorProfiler and profile data
        mock_profiler = Mock()
        mock_profiler_class.return_value = mock_profiler

        # Create mock profile data
        mock_profile = Mock()
        mock_profile.device_bdf = "0000:03:00.0"
        mock_profile.capture_duration = 30
        mock_profile.total_accesses = 100
        mock_profile.register_accesses = []
        mock_profile.timing_patterns = []
        mock_profile.state_transitions = {}
        mock_profile.power_states = []
        mock_profile.interrupt_patterns = {}

        mock_profiler.capture_behavior_profile.return_value = mock_profile

        result = manager.run_behavior_profiling(device_info, duration=30)

        assert result is not None
        profile_file = Path(result)
        assert profile_file.exists()
        assert profile_file.name == "behavior_profile.json"

        # Verify file content
        with open(profile_file) as f:
            profile_data = json.load(f)

        assert profile_data["device_bdf"] == "0000:03:00.0"
        assert profile_data["capture_duration"] == 30
        assert profile_data["total_accesses"] == 100

    @patch("src.device_clone.variance_manager.BehaviorProfiler")
    def test_run_behavior_profiling_with_detailed_data(
        self, mock_profiler_class, manager
    ):
        """Test behavior profiling with detailed register access and timing data."""
        device_info = {"device_id": "0x1234"}

        mock_profiler = Mock()
        mock_profiler_class.return_value = mock_profiler

        # Create mock register access
        mock_access = Mock()
        mock_access.timestamp = 1234567890.0
        mock_access.register = "BAR0"
        mock_access.offset = 0x10
        mock_access.operation = "READ"
        mock_access.value = 0x12345678
        mock_access.duration_us = 10.5

        # Create mock timing pattern
        mock_pattern = Mock()
        mock_pattern.pattern_type = "periodic"
        mock_pattern.registers = ["BAR0", "BAR1"]
        mock_pattern.avg_interval_us = 1000.0
        mock_pattern.std_deviation_us = 50.0
        mock_pattern.frequency_hz = 1000.0
        mock_pattern.confidence = 0.95

        mock_profile = Mock()
        mock_profile.device_bdf = "0000:03:00.0"
        mock_profile.capture_duration = 30
        mock_profile.total_accesses = 1
        mock_profile.register_accesses = [mock_access]
        mock_profile.timing_patterns = [mock_pattern]
        mock_profile.state_transitions = {"idle_to_active": 5}
        mock_profile.power_states = ["D0", "D3"]
        mock_profile.interrupt_patterns = {"msi": 10}

        mock_profiler.capture_behavior_profile.return_value = mock_profile

        result = manager.run_behavior_profiling(device_info)

        assert result is not None

        # Verify detailed data structure
        with open(result) as f:
            profile_data = json.load(f)

        assert len(profile_data["register_accesses"]) == 1
        assert profile_data["register_accesses"][0]["register"] == "BAR0"
        assert profile_data["register_accesses"][0]["operation"] == "READ"

        assert len(profile_data["timing_patterns"]) == 1
        assert profile_data["timing_patterns"][0]["pattern_type"] == "periodic"
        assert profile_data["timing_patterns"][0]["confidence"] == 0.95

    def test_run_behavior_profiling_profiler_unavailable(self, manager):
        """Test behavior profiling when BehaviorProfiler is unavailable."""
        device_info = {"device_id": "0x1234"}

        with patch("src.device_clone.variance_manager.BehaviorProfiler", None):
            result = manager.run_behavior_profiling(device_info)

        assert result is None

    def test_run_behavior_profiling_with_fallback_manager(self, manager):
        """Test behavior profiling with fallback manager."""
        device_info = {"device_id": "0x1234"}

        # Set up mock fallback manager
        mock_fallback = Mock()
        mock_fallback.confirm_fallback.return_value = True
        manager.fallback_manager = mock_fallback

        with patch("src.device_clone.variance_manager.BehaviorProfiler", None):
            result = manager.run_behavior_profiling(device_info)

        assert result is None
        mock_fallback.confirm_fallback.assert_called()

    @patch("src.device_clone.variance_manager.BehaviorProfiler")
    def test_run_behavior_profiling_exception_handling(
        self, mock_profiler_class, manager
    ):
        """Test behavior profiling exception handling."""
        device_info = {"device_id": "0x1234"}

        mock_profiler = Mock()
        mock_profiler.capture_behavior_profile.side_effect = Exception(
            "Profiling failed"
        )
        mock_profiler_class.return_value = mock_profiler

        result = manager.run_behavior_profiling(device_info)

        assert result is None

    @patch("src.device_clone.variance_manager.BehaviorProfiler")
    def test_run_behavior_profiling_with_fallback_on_exception(
        self, mock_profiler_class, manager
    ):
        """Test behavior profiling fallback on exception."""
        device_info = {"device_id": "0x1234"}

        # Set up mock fallback manager
        mock_fallback = Mock()
        mock_fallback.confirm_fallback.return_value = False  # Deny fallback
        manager.fallback_manager = mock_fallback

        mock_profiler = Mock()
        mock_profiler.capture_behavior_profile.side_effect = Exception(
            "Profiling failed"
        )
        mock_profiler_class.return_value = mock_profiler

        result = manager.run_behavior_profiling(device_info)

        assert result is None
        mock_fallback.confirm_fallback.assert_called()


class TestVarianceManagerAvailabilityChecks:
    """Test availability check methods."""

    @pytest.fixture
    def manager(self):
        """Create a VarianceManager for testing."""
        with tempfile.TemporaryDirectory() as temp_dir:
            yield VarianceManager("0000:03:00.0", Path(temp_dir))

    @patch("src.device_clone.variance_manager.ManufacturingVarianceSimulator")
    def test_is_variance_available_true(self, mock_simulator, manager):
        """Test is_variance_available returns True when simulator is available."""
        mock_simulator.return_value = Mock()

        result = manager.is_variance_available()

        assert result is True

    def test_is_variance_available_false(self, manager):
        """Test is_variance_available returns False when simulator is None."""
        with patch(
            "src.device_clone.variance_manager.ManufacturingVarianceSimulator", None
        ):
            result = manager.is_variance_available()

        assert result is False

    @patch("src.device_clone.variance_manager.BehaviorProfiler")
    def test_is_profiling_available_true(self, mock_profiler, manager):
        """Test is_profiling_available returns True when profiler is available."""
        mock_profiler.return_value = Mock()

        result = manager.is_profiling_available()

        assert result is True

    def test_is_profiling_available_false(self, manager):
        """Test is_profiling_available returns False when profiler is None."""
        with patch("src.device_clone.variance_manager.BehaviorProfiler", None):
            result = manager.is_profiling_available()

        assert result is False


class TestVarianceManagerIntegration:
    """Integration tests for VarianceManager."""

    def test_full_variance_workflow(self):
        """Test complete variance workflow with both simulation and profiling."""
        with tempfile.TemporaryDirectory() as temp_dir:
            manager = VarianceManager("0000:03:00.0", Path(temp_dir))

            device_info = {
                "device_id": "0x1234",
                "class_code": "0x0200",
            }

            with patch(
                "src.device_clone.variance_manager.DeviceClass"
            ) as mock_device_class:
                with patch(
                    "src.device_clone.variance_manager.VarianceModel"
                ) as mock_variance_model:
                    with patch(
                        "src.device_clone.variance_manager.BehaviorProfiler"
                    ) as mock_profiler_class:
                        # Mock device class
                        mock_enterprise = Mock()
                        mock_enterprise.value = "enterprise"
                        mock_device_class.ENTERPRISE = mock_enterprise
                        mock_consumer = Mock()
                        mock_consumer.value = "consumer"
                        mock_device_class.CONSUMER = mock_consumer

                        # Mock variance model
                        mock_model = Mock()
                        mock_model.device_id = "0x1234"
                        mock_model.device_class = mock_enterprise
                        mock_model.base_frequency_mhz = 100.0
                        mock_model.clock_jitter_percent = 2.5
                        mock_model.register_timing_jitter_ns = 25.0
                        mock_model.power_noise_percent = 2.0
                        mock_model.temperature_drift_ppm_per_c = 50.0
                        mock_model.process_variation_percent = 10.0
                        mock_model.propagation_delay_ps = 100.0
                        mock_variance_model.return_value = mock_model

                        # Mock behavior profiler
                        mock_profiler = Mock()
                        mock_profile = Mock()
                        mock_profile.device_bdf = "0000:03:00.0"
                        mock_profile.capture_duration = 30
                        mock_profile.total_accesses = 0
                        mock_profile.register_accesses = []
                        mock_profile.timing_patterns = []
                        mock_profile.state_transitions = {}
                        mock_profile.power_states = []
                        mock_profile.interrupt_patterns = {}
                        mock_profiler.capture_behavior_profile.return_value = (
                            mock_profile
                        )
                        mock_profiler_class.return_value = mock_profiler

                        # Run variance simulation
                        variance_files = manager.apply_manufacturing_variance(
                            device_info
                        )
                        assert len(variance_files) == 1

                        # Run behavior profiling
                        profile_file = manager.run_behavior_profiling(device_info)
                        assert profile_file is not None

                        # Verify both files exist
                        assert Path(variance_files[0]).exists()
                        assert Path(profile_file).exists()

    def test_graceful_degradation_without_modules(self):
        """Test that VarianceManager degrades gracefully without optional modules."""
        with tempfile.TemporaryDirectory() as temp_dir:
            manager = VarianceManager("0000:03:00.0", Path(temp_dir))

            device_info = {"device_id": "0x1234", "class_code": "0x0200"}

            # Test without variance modules
            with patch("src.device_clone.variance_manager.DeviceClass", None):
                with patch("src.device_clone.variance_manager.VarianceModel", None):
                    variance_files = manager.apply_manufacturing_variance(device_info)
                    assert variance_files == []

            # Test without behavior profiler
            with patch("src.device_clone.variance_manager.BehaviorProfiler", None):
                profile_file = manager.run_behavior_profiling(device_info)
                assert profile_file is None


if __name__ == "__main__":
    pytest.main([__file__])
