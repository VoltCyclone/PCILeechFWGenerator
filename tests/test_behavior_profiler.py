#!/usr/bin/env python3
"""
Unit test suite for behavior_profiler.py

Tests the BehaviorProfiler class and related functionality.
"""

import json
import os
import platform
import queue
import tempfile
from pathlib import Path
from unittest.mock import MagicMock, mock_open, patch

import pytest

from src.device_clone.behavior_profiler import (BehaviorProfile,
                                                BehaviorProfiler,
                                                RegisterAccess, TimingPattern,
                                                check_linux_requirement,
                                                is_linux)


class TestPlatformDetection:
    """Test platform detection utilities."""

    @patch("platform.system")
    def test_is_linux_true(self, mock_system):
        """Test is_linux returns True on Linux."""
        mock_system.return_value = "Linux"
        assert is_linux() is True

    @patch("platform.system")
    def test_is_linux_false(self, mock_system):
        """Test is_linux returns False on non-Linux."""
        mock_system.return_value = "Darwin"
        assert is_linux() is False

    @patch("platform.system")
    def test_is_linux_case_insensitive(self, mock_system):
        """Test is_linux is case insensitive."""
        mock_system.return_value = "linux"
        assert is_linux() is True

    @patch("src.device_clone.behavior_profiler.is_linux")
    def test_check_linux_requirement_success(self, mock_is_linux):
        """Test check_linux_requirement succeeds on Linux."""
        mock_is_linux.return_value = True

        # Should not raise an exception
        check_linux_requirement("test operation")

    @patch("src.device_clone.behavior_profiler.is_linux")
    def test_check_linux_requirement_failure(self, mock_is_linux):
        """Test check_linux_requirement fails on non-Linux."""
        mock_is_linux.return_value = False

        with patch("platform.system", return_value="Darwin"):
            with pytest.raises(Exception) as exc_info:
                check_linux_requirement("test operation")

            assert "requires Linux" in str(exc_info.value)
            assert "Darwin" in str(exc_info.value)


class TestDataClasses:
    """Test dataclasses used in behavior profiling."""

    def test_register_access_creation(self):
        """Test RegisterAccess dataclass creation."""
        access = RegisterAccess(
            timestamp=1234567890.123,
            register="CONFIG_000",
            offset=0x00,
            operation="read",
            value=0x1234,
            duration_us=5.5,
        )

        assert access.timestamp == 1234567890.123
        assert access.register == "CONFIG_000"
        assert access.offset == 0x00
        assert access.operation == "read"
        assert access.value == 0x1234
        assert access.duration_us == 5.5

    def test_register_access_defaults(self):
        """Test RegisterAccess with default values."""
        access = RegisterAccess(
            timestamp=1234567890.0,
            register="TEST_REG",
            offset=0x10,
            operation="write",
        )

        assert access.value is None
        assert access.duration_us is None

    def test_timing_pattern_creation(self):
        """Test TimingPattern dataclass creation."""
        pattern = TimingPattern(
            pattern_type="periodic",
            registers=["REG1", "REG2"],
            avg_interval_us=1000.0,
            std_deviation_us=50.0,
            frequency_hz=1000.0,
            confidence=0.85,
        )

        assert pattern.pattern_type == "periodic"
        assert pattern.registers == ["REG1", "REG2"]
        assert pattern.avg_interval_us == 1000.0
        assert pattern.std_deviation_us == 50.0
        assert pattern.frequency_hz == 1000.0
        assert pattern.confidence == 0.85

    def test_behavior_profile_creation(self):
        """Test BehaviorProfile dataclass creation."""
        accesses = [
            RegisterAccess(
                timestamp=1234567890.0,
                register="REG1",
                offset=0x00,
                operation="read",
            )
        ]

        patterns = [
            TimingPattern(
                pattern_type="periodic",
                registers=["REG1"],
                avg_interval_us=1000.0,
                std_deviation_us=50.0,
                frequency_hz=1000.0,
                confidence=0.85,
            )
        ]

        profile = BehaviorProfile(
            device_bdf="0000:03:00.0",
            capture_duration=30.0,
            total_accesses=1,
            register_accesses=accesses,
            timing_patterns=patterns,
            state_transitions={"REG1": ["REG2"]},
            power_states=["D0"],
            interrupt_patterns={"test": "data"},
        )

        assert profile.device_bdf == "0000:03:00.0"
        assert profile.capture_duration == 30.0
        assert profile.total_accesses == 1
        assert len(profile.register_accesses) == 1
        assert len(profile.timing_patterns) == 1
        assert profile.state_transitions == {"REG1": ["REG2"]}
        assert profile.power_states == ["D0"]
        assert profile.interrupt_patterns == {"test": "data"}


class TestBehaviorProfilerInit:
    """Test BehaviorProfiler initialization."""

    def test_init_valid_bdf(self):
        """Test initialization with valid BDF."""
        profiler = BehaviorProfiler("0000:03:00.0")
        assert profiler.bdf == "0000:03:00.0"
        assert profiler.debug is False
        assert profiler.monitoring is False
        assert isinstance(profiler.access_queue, queue.Queue)

    def test_init_with_debug(self):
        """Test initialization with debug enabled."""
        profiler = BehaviorProfiler("0000:03:00.0", debug=True)
        assert profiler.debug is True

    def test_init_invalid_bdf_format(self):
        """Test initialization with invalid BDF format."""
        with pytest.raises(ValueError, match="Invalid BDF format"):
            BehaviorProfiler("invalid-bdf")

    def test_init_invalid_bdf_missing_parts(self):
        """Test initialization with incomplete BDF."""
        with pytest.raises(ValueError, match="Invalid BDF format"):
            BehaviorProfiler("0000:03")

    def test_init_invalid_bdf_function_out_of_range(self):
        """Test initialization with function number out of range."""
        with pytest.raises(ValueError, match="Invalid BDF format"):
            BehaviorProfiler("0000:03:00.8")

    @patch("src.device_clone.behavior_profiler.ManufacturingVarianceSimulator")
    def test_init_with_variance_enabled(self, mock_simulator_class):
        """Test initialization with variance simulation enabled."""
        mock_simulator = MagicMock()
        mock_simulator_class.return_value = mock_simulator

        profiler = BehaviorProfiler("0000:03:00.0", enable_variance=True)
        assert profiler.enable_variance is True
        assert profiler.variance_simulator == mock_simulator

    @patch("src.device_clone.behavior_profiler.ManufacturingVarianceSimulator")
    def test_init_with_variance_disabled(self, mock_simulator_class):
        """Test initialization with variance simulation disabled."""
        profiler = BehaviorProfiler("0000:03:00.0", enable_variance=False)
        assert profiler.enable_variance is False
        assert profiler.variance_simulator is None
        # Should not instantiate the simulator
        mock_simulator_class.assert_not_called()


class TestBehaviorProfilerSetup:
    """Test monitoring setup functionality."""

    def setup_method(self):
        """Set up test fixtures."""
        self.profiler = BehaviorProfiler("0000:03:00.0")

    @patch("src.device_clone.behavior_profiler.is_linux")
    @patch("subprocess.run")
    def test_setup_monitoring_success(self, mock_subprocess, mock_is_linux):
        """Test successful monitoring setup."""
        mock_is_linux.return_value = True
        mock_subprocess.return_value.returncode = 0
        mock_subprocess.return_value.stdout = "03:00.0 Ethernet controller"

        result = self.profiler._setup_monitoring()
        assert result is True

    @patch("src.device_clone.behavior_profiler.is_linux")
    @patch("subprocess.run")
    def test_setup_monitoring_device_not_found(self, mock_subprocess, mock_is_linux):
        """Test monitoring setup when device is not found."""
        mock_is_linux.return_value = True
        mock_subprocess.return_value.returncode = 1
        mock_subprocess.return_value.stdout = ""

        result = self.profiler._setup_monitoring()
        assert result is False

    @patch("src.device_clone.behavior_profiler.is_linux")
    def test_setup_monitoring_non_linux(self, mock_is_linux):
        """Test monitoring setup fails on non-Linux platforms."""
        mock_is_linux.return_value = False

        with patch("platform.system", return_value="Darwin"):
            result = self.profiler._setup_monitoring()
            assert result is False


class TestBehaviorProfilerMonitoring:
    """Test monitoring start/stop functionality."""

    def setup_method(self):
        """Set up test fixtures."""
        self.profiler = BehaviorProfiler("0000:03:00.0")

    @patch.object(BehaviorProfiler, "_setup_monitoring", return_value=True)
    @patch("threading.Thread")
    def test_start_monitoring_success(self, mock_thread_class, mock_setup):
        """Test successful monitoring start."""
        mock_thread = MagicMock()
        mock_thread_class.return_value = mock_thread

        result = self.profiler.start_monitoring()
        assert result is True
        assert self.profiler.monitoring is True
        mock_thread_class.assert_called_once()
        mock_thread.start.assert_called_once()

    @patch.object(BehaviorProfiler, "_setup_monitoring", return_value=False)
    def test_start_monitoring_setup_failure(self, mock_setup):
        """Test monitoring start fails when setup fails."""
        result = self.profiler.start_monitoring()
        assert result is False
        assert self.profiler.monitoring is False

    def test_start_monitoring_already_active(self):
        """Test starting monitoring when already active."""
        self.profiler.monitoring = True
        result = self.profiler.start_monitoring()
        assert result is True

    @patch("subprocess.run")
    def test_stop_monitoring(self, mock_subprocess):
        """Test monitoring stop."""
        self.profiler.monitoring = True
        self.profiler.monitor_thread = MagicMock()

        self.profiler.stop_monitoring()

        assert self.profiler.monitoring is False
        self.profiler.monitor_thread.join.assert_called_once_with(timeout=1.0)


class TestBehaviorProfilerCapture:
    """Test behavior profile capture functionality."""

    def setup_method(self):
        """Set up test fixtures."""
        self.profiler = BehaviorProfiler("0000:03:00.0")

    @patch.object(BehaviorProfiler, "start_monitoring", return_value=True)
    @patch.object(BehaviorProfiler, "stop_monitoring")
    def test_capture_behavior_profile_success(self, mock_stop, mock_start):
        """Test successful behavior profile capture."""
        # Mock the access queue to return some test data
        test_access = RegisterAccess(
            timestamp=1234567890.0,
            register="REG_TEST_READ",
            offset=0x500,
            operation="read",
        )

        # Mock the access queue to return test data and then raise Empty
        call_count = [0]  # Use list to modify in nested function

        def mock_get(timeout=0.1):
            call_count[0] += 1
            if call_count[0] == 1:
                return test_access
            else:
                raise queue.Empty()

        mock_queue = MagicMock()
        mock_queue.get.side_effect = mock_get

        self.profiler.access_queue = mock_queue

        profile = self.profiler.capture_behavior_profile(duration=1.0)

        assert profile.device_bdf == "0000:03:00.0"
        assert profile.capture_duration == 1.0
        assert profile.total_accesses >= 1
        assert len(profile.register_accesses) >= 1

    def test_capture_behavior_profile_invalid_duration(self):
        """Test capture with invalid duration."""
        with pytest.raises(ValueError, match="Duration must be positive"):
            self.profiler.capture_behavior_profile(duration=0)

    def test_capture_behavior_profile_negative_duration(self):
        """Test capture with negative duration."""
        with pytest.raises(ValueError, match="Duration must be positive"):
            self.profiler.capture_behavior_profile(duration=-1.0)

    @patch.object(BehaviorProfiler, "start_monitoring", return_value=False)
    def test_capture_behavior_profile_setup_failure(self, mock_start):
        """Test capture fails when monitoring setup fails."""
        with pytest.raises(RuntimeError, match="Failed to start monitoring"):
            self.profiler.capture_behavior_profile(duration=1.0)


class TestBehaviorProfilerAnalysis:
    """Test pattern analysis functionality."""

    def setup_method(self):
        """Set up test fixtures."""
        self.profiler = BehaviorProfiler("0000:03:00.0")

    def test_analyze_patterns_with_data(self):
        """Test pattern analysis with sample data."""
        # Create test profile with sample data
        accesses = [
            RegisterAccess(
                timestamp=1234567890.0,
                register="REG1",
                offset=0x00,
                operation="read",
                duration_us=1.0,
            ),
            RegisterAccess(
                timestamp=1234567890.001,
                register="REG2",
                offset=0x04,
                operation="write",
                duration_us=2.0,
            ),
        ]

        patterns = [
            TimingPattern(
                pattern_type="periodic",
                registers=["REG1"],
                avg_interval_us=1000.0,
                std_deviation_us=50.0,
                frequency_hz=1000.0,
                confidence=0.85,
            )
        ]

        profile = BehaviorProfile(
            device_bdf="0000:03:00.0",
            capture_duration=2.0,
            total_accesses=2,
            register_accesses=accesses,
            timing_patterns=patterns,
            state_transitions={},
            power_states=["D0"],
            interrupt_patterns={},
        )

        analysis = self.profiler.analyze_patterns(profile)

        assert "device_characteristics" in analysis
        assert "performance_metrics" in analysis
        assert "behavioral_signatures" in analysis
        assert "recommendations" in analysis

        # Check device characteristics
        chars = analysis["device_characteristics"]
        assert chars["total_registers_accessed"] == 2
        assert chars["read_write_ratio"] == 1.0  # 1 read, 1 write
        assert chars["access_frequency_hz"] == 1.0  # 2 accesses in 2 seconds

    def test_analyze_patterns_empty_profile(self):
        """Test pattern analysis with empty profile."""
        profile = BehaviorProfile(
            device_bdf="0000:03:00.0",
            capture_duration=1.0,
            total_accesses=0,
            register_accesses=[],
            timing_patterns=[],
            state_transitions={},
            power_states=["D0"],
            interrupt_patterns={},
        )

        analysis = self.profiler.analyze_patterns(profile)

        assert analysis["device_characteristics"]["total_registers_accessed"] == 0
        assert analysis["device_characteristics"]["read_write_ratio"] == 0.0

    def test_calculate_rw_ratio(self):
        """Test read/write ratio calculation."""
        accesses = [
            RegisterAccess(timestamp=1.0, register="REG1", offset=0, operation="read"),
            RegisterAccess(timestamp=2.0, register="REG2", offset=4, operation="write"),
            RegisterAccess(timestamp=3.0, register="REG3", offset=8, operation="read"),
        ]

        ratio = self.profiler._calculate_rw_ratio(accesses)
        assert ratio == 2.0  # 2 reads, 1 write

    def test_calculate_rw_ratio_no_writes(self):
        """Test read/write ratio with no writes."""
        accesses = [
            RegisterAccess(timestamp=1.0, register="REG1", offset=0, operation="read"),
            RegisterAccess(timestamp=2.0, register="REG2", offset=4, operation="read"),
        ]

        ratio = self.profiler._calculate_rw_ratio(accesses)
        assert ratio == 1.0  # Safe default when no writes

    def test_calculate_rw_ratio_empty(self):
        """Test read/write ratio with empty access list."""
        ratio = self.profiler._calculate_rw_ratio([])
        assert ratio == 1.0  # Safe default


class TestBehaviorProfilerPersistence:
    """Test profile save/load functionality."""

    def setup_method(self):
        """Set up test fixtures."""
        self.profiler = BehaviorProfiler("0000:03:00.0")

    def test_save_profile(self):
        """Test saving profile to file."""
        profile = BehaviorProfile(
            device_bdf="0000:03:00.0",
            capture_duration=30.0,
            total_accesses=1,
            register_accesses=[
                RegisterAccess(
                    timestamp=1234567890.0,
                    register="TEST_REG",
                    offset=0x00,
                    operation="read",
                )
            ],
            timing_patterns=[],
            state_transitions={},
            power_states=["D0"],
            interrupt_patterns={},
        )

        with tempfile.NamedTemporaryFile(mode="w", delete=False) as tmp_file:
            tmp_path = tmp_file.name

        try:
            self.profiler.save_profile(profile, tmp_path)

            # Verify file was created and contains expected data
            assert Path(tmp_path).exists()

            with open(tmp_path, "r") as f:
                data = json.load(f)

            assert data["device_bdf"] == "0000:03:00.0"
            assert data["capture_duration"] == 30.0
            assert data["total_accesses"] == 1

        finally:
            Path(tmp_path).unlink(missing_ok=True)

    def test_load_profile(self):
        """Test loading profile from file."""
        original_profile = BehaviorProfile(
            device_bdf="0000:03:00.0",
            capture_duration=30.0,
            total_accesses=1,
            register_accesses=[
                RegisterAccess(
                    timestamp=1234567890.0,
                    register="TEST_REG",
                    offset=0x00,
                    operation="read",
                )
            ],
            timing_patterns=[],
            state_transitions={},
            power_states=["D0"],
            interrupt_patterns={},
        )

        with tempfile.NamedTemporaryFile(mode="w", delete=False) as tmp_file:
            tmp_path = tmp_file.name

        try:
            # Save profile first
            self.profiler.save_profile(original_profile, tmp_path)

            # Load profile back
            loaded_profile = self.profiler.load_profile(tmp_path)

            assert loaded_profile.device_bdf == original_profile.device_bdf
            assert loaded_profile.capture_duration == (
                original_profile.capture_duration
            )
            assert loaded_profile.total_accesses == original_profile.total_accesses
            assert len(loaded_profile.register_accesses) == len(
                original_profile.register_accesses
            )

        finally:
            Path(tmp_path).unlink(missing_ok=True)


class TestBehaviorProfilerAdvanced:
    """Test advanced functionality."""

    def setup_method(self):
        """Set up test fixtures."""
        self.profiler = BehaviorProfiler("0000:03:00.0")

    def test_get_most_active_registers(self):
        """Test getting most active registers."""
        accesses = [
            RegisterAccess(timestamp=1.0, register="REG1", offset=0, operation="read"),
            RegisterAccess(timestamp=2.0, register="REG1", offset=0, operation="read"),
            RegisterAccess(timestamp=3.0, register="REG2", offset=4, operation="write"),
        ]

        result = self.profiler._get_most_active_registers(accesses, top_n=2)

        expected = [("REG1", 2), ("REG2", 1)]
        assert result == expected

    def test_calculate_timing_regularity(self):
        """Test timing regularity calculation."""
        patterns = [
            TimingPattern(
                pattern_type="periodic",
                registers=["REG1"],
                avg_interval_us=1000.0,
                std_deviation_us=50.0,
                frequency_hz=1000.0,
                confidence=0.8,
            ),
            TimingPattern(
                pattern_type="periodic",
                registers=["REG2"],
                avg_interval_us=2000.0,
                std_deviation_us=100.0,
                frequency_hz=500.0,
                confidence=0.9,
            ),
        ]

        regularity = self.profiler._calculate_timing_regularity(patterns)
        assert abs(regularity - 0.85) < 1e-10  # Average of 0.8 and 0.9

    def test_calculate_timing_regularity_empty(self):
        """Test timing regularity with empty patterns."""
        regularity = self.profiler._calculate_timing_regularity([])
        assert regularity == 0.0

    def test_generate_recommendations_high_frequency(self):
        """Test recommendations for high-frequency devices."""
        analysis = {
            "device_characteristics": {"access_frequency_hz": 2000.0},
            "behavioral_signatures": {
                "timing_regularity": 0.5,
                "interrupt_activity": 0.1,
            },
        }

        recommendations = self.profiler._generate_recommendations(
            BehaviorProfile(
                device_bdf="0000:03:00.0",
                capture_duration=1.0,
                total_accesses=2000,
                register_accesses=[],
                timing_patterns=[],
                state_transitions={},
                power_states=["D0"],
                interrupt_patterns={},
            ),
            analysis,
        )

        assert any("High-frequency" in rec for rec in recommendations)

    def test_generate_recommendations_low_frequency(self):
        """Test recommendations for low-frequency devices."""
        analysis = {
            "device_characteristics": {"access_frequency_hz": 5.0},
            "behavioral_signatures": {
                "timing_regularity": 0.5,
                "interrupt_activity": 0.1,
            },
        }

        recommendations = self.profiler._generate_recommendations(
            BehaviorProfile(
                device_bdf="0000:03:00.0",
                capture_duration=1.0,
                total_accesses=5,
                register_accesses=[],
                timing_patterns=[],
                state_transitions={},
                power_states=["D0"],
                interrupt_patterns={},
            ),
            analysis,
        )

        assert any("Low-frequency" in rec for rec in recommendations)

    def test_generate_recommendations_high_regularity(self):
        """Test recommendations for highly regular timing."""
        analysis = {
            "device_characteristics": {"access_frequency_hz": 100.0},
            "behavioral_signatures": {
                "timing_regularity": 0.95,
                "interrupt_activity": 0.1,
            },
        }

        recommendations = self.profiler._generate_recommendations(
            BehaviorProfile(
                device_bdf="0000:03:00.0",
                capture_duration=1.0,
                total_accesses=100,
                register_accesses=[],
                timing_patterns=[],
                state_transitions={},
                power_states=["D0"],
                interrupt_patterns={},
            ),
            analysis,
        )

        assert any("Highly regular" in rec for rec in recommendations)

    def test_generate_recommendations_irregular_timing(self):
        """Test recommendations for irregular timing."""
        analysis = {
            "device_characteristics": {"access_frequency_hz": 100.0},
            "behavioral_signatures": {
                "timing_regularity": 0.2,
                "interrupt_activity": 0.1,
            },
        }

        recommendations = self.profiler._generate_recommendations(
            BehaviorProfile(
                device_bdf="0000:03:00.0",
                capture_duration=1.0,
                total_accesses=100,
                register_accesses=[],
                timing_patterns=[],
                state_transitions={},
                power_states=["D0"],
                interrupt_patterns={},
            ),
            analysis,
        )

        assert any("Irregular timing" in rec for rec in recommendations)
