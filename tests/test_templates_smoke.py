import pytest

from src.device_clone.device_config import DeviceClass, DeviceType
from src.templating.sv_config import ErrorHandlingConfig, PerformanceConfig
from src.templating.advanced_sv_power import PowerManagementConfig
from src.templating.systemverilog_generator import (
    AdvancedSVGenerator,
    DeviceSpecificLogic,
)


def test_advanced_controller_renders():
    """Smoke test: render the advanced controller template end-to-end."""
    # Create device logic with required identifiers for donor-uniqueness
    device_logic = DeviceSpecificLogic(
        device_type=DeviceType.GENERIC, device_class=DeviceClass.CONSUMER
    )

    # Add required identifiers as attributes (will be read by generate_advanced_systemverilog)
    device_logic.vendor_id = "0x10de"  # type: ignore
    device_logic.device_id = "0x1234"  # type: ignore
    device_logic.device_signature = "10de:1234:01"  # type: ignore

    g = AdvancedSVGenerator(
        device_config=device_logic,
        power_config=PowerManagementConfig(),
        perf_config=PerformanceConfig(),
        error_config=ErrorHandlingConfig(),
    )

    result = g.generate_advanced_systemverilog(regs=[], variance_model=None)

    assert result and isinstance(result, str)
    # Basic sanity checks
    assert "module" in result or "//" in result
