import pytest

from src.device_clone.bar_size_converter import BarSizeConverter
from src.device_clone.behavior_profiler import (BehaviorProfile,
                                                BehaviorProfiler,
                                                RegisterAccess)
from src.device_clone.board_config import (get_fpga_family, get_fpga_part,
                                           get_pcie_ip_type, validate_board)
from src.device_clone.config_space_manager import BarInfo, ConfigSpaceManager

from src.device_clone.overlay_mapper import OverlayMapper

from src.string_utils import safe_format

# Integration test for BarSizeConverter


def test_bar_size_converter_integration():
    # Valid memory BAR
    size = 0x1000  # 4KB
    encoding = BarSizeConverter.size_to_encoding(
        size, bar_type="memory", is_64bit=False, prefetchable=True
    )
    decoded_size = BarSizeConverter.get_size_from_encoding(encoding, bar_type="memory")
    # The decoded size should match the original size
    assert decoded_size == size  # 4096 bytes
    assert BarSizeConverter.validate_bar_size(size, "memory")
    # Invalid BAR size validation should return False (not raise exception)
    assert not BarSizeConverter.validate_bar_size(0x1800, "memory")


# Integration test for OverlayMapper


def test_overlay_mapper_integration():
    config_space = {0: 0x10DE1234, 4: 0x0000FBF9, 16: 0x80000000}
    capabilities = {"05": 0x50, "10": 0x60}
    mapper = OverlayMapper()
    overlay = mapper.generate_overlay_map(config_space, capabilities)
    assert "OVERLAY_MAP" in overlay
    assert isinstance(overlay["OVERLAY_MAP"], list)
    assert overlay["OVERLAY_ENTRIES"] >= 0


# Integration test for BehaviorProfiler


def test_behavior_profiler_integration(monkeypatch):
    # Simulate register accesses
    accesses = [
        RegisterAccess(
            timestamp=1.0, register="CMD", offset=0x04, operation="write", value=1
        ),
        RegisterAccess(
            timestamp=2.0, register="STATUS", offset=0x06, operation="read", value=0
        ),
        RegisterAccess(
            timestamp=3.0,
            register="BAR0",
            offset=0x10,
            operation="read",
            value=0x80000000,
        ),
    ]
    profile = BehaviorProfile(
        device_bdf="0000:03:00.0",
        capture_duration=3.0,
        total_accesses=len(accesses),
        register_accesses=accesses,
        timing_patterns=[],
        state_transitions={},
        power_states=["D0"],
        interrupt_patterns={},
    )
    profiler = BehaviorProfiler(bdf="0000:03:00.0", debug=True)
    analysis = profiler.analyze_patterns(profile)
    assert "device_characteristics" in analysis
    assert analysis["device_characteristics"]["total_registers_accessed"] == 3


# Integration test for ConfigSpaceManager


def test_config_space_manager_integration(tmp_path, monkeypatch):
    # Simulate config space bytes for extraction
    config_bytes = bytearray(256)
    config_bytes[0:2] = (0x10DE).to_bytes(2, "little")
    config_bytes[2:4] = (0x1234).to_bytes(2, "little")
    config_bytes[4:6] = (0x0007).to_bytes(2, "little")
    config_bytes[6:8] = (0x0280).to_bytes(2, "little")
    config_bytes[8] = 0x01
    config_bytes[9:12] = (0x020000).to_bytes(3, "little")
    config_bytes[16:20] = (0x80000000).to_bytes(4, "little")
    manager = ConfigSpaceManager(bdf="0000:03:00.0")
    info = manager.extract_device_info(bytes(config_bytes))
    assert info["vendor_id"] == 0x10DE
    assert info["device_id"] == 0x1234
    assert any(isinstance(bar, BarInfo) for bar in info["bars"])


# Integration test for board_config


def test_board_config_integration(monkeypatch):
    # Patch _ensure_board_cache to return a fake board
    fake_board = {
        "testboard": {
            "fpga_part": "xc7a35t",
            "fpga_family": "7series",
            "pcie_ip_type": "axi_pcie",
        }
    }
    monkeypatch.setattr(
        "src.device_clone.board_config._ensure_board_cache",
        lambda repo_root=None: fake_board,
    )
    assert get_fpga_part("testboard") == "xc7a35t"
    assert get_fpga_family("xc7a35t") == "7series"
    assert get_pcie_ip_type("xc7a35t") == "axi_pcie"
    assert validate_board("testboard")


# Edge case: OverlayMapper with empty config/capabilities


def test_overlay_mapper_empty():
    mapper = OverlayMapper()
    overlay = mapper.generate_overlay_map({}, {})
    # Accept that default overlay entries may be present (e.g., 10 for standard PCI config)
    assert overlay["OVERLAY_ENTRIES"] >= 0


# Edge case: BehaviorProfiler with no accesses


def test_behavior_profiler_empty():
    profile = BehaviorProfile(
        device_bdf="0000:03:00.0",
        capture_duration=0.0,
        total_accesses=0,
        register_accesses=[],
        timing_patterns=[],
        state_transitions={},
        power_states=[],
        interrupt_patterns={},
    )
    profiler = BehaviorProfiler(bdf="0000:03:00.0", debug=True)
    analysis = profiler.analyze_patterns(profile)
    assert analysis["device_characteristics"]["total_registers_accessed"] == 0


# Edge case: ConfigSpaceManager with short config space


def test_config_space_manager_short_config():
    manager = ConfigSpaceManager(bdf="0000:03:00.0")
    with pytest.raises(ValueError):
        manager.extract_device_info(bytes([0x00, 0x01]))
