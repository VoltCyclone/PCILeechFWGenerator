import pytest

from src.device_clone.device_config import DeviceClass, DeviceType
from src.templating.sv_config import ErrorHandlingConfig, PerformanceConfig
from src.templating.advanced_sv_power import PowerManagementConfig
from src.templating.systemverilog_generator import (
    SystemVerilogGenerator,
    DeviceSpecificLogic,
)


def test_config_only_generation():
    """Smoke test: ensure config-only generation works end-to-end."""
    # Create device logic with required identifiers for donor-uniqueness
    device_logic = DeviceSpecificLogic(
        device_type=DeviceType.GENERIC, device_class=DeviceClass.CONSUMER
    )

    g = SystemVerilogGenerator(
        device_config=device_logic,
        power_config=PowerManagementConfig(),
        perf_config=PerformanceConfig(),
        error_config=ErrorHandlingConfig(),
    )

    # Create minimal valid context for config-only generation
    context = {
        "device_config": {
            "vendor_id": "10DE",
            "device_id": "1234",
            "class_code": "030000",
            "revision_id": "A1"
        },
        "device": {
            "vendor_id": "10DE",
            "device_id": "1234"
        },
        "config_space": {},
        "bars": [],
        "header": "// Test header",
        "device_signature": "10DE:1234:A1"
    }

    result = g.generate_modules(context)

    assert result and isinstance(result, dict)
    # Should only generate device_config and COE
    assert "device_config" in result
    assert "pcileech_cfgspace.coe" in result
    # Should NOT generate HDL modules
    assert "advanced_controller" not in result
    assert "top_level_wrapper" not in result


# Legacy test removed - advanced controller no longer generated
@pytest.mark.skip(reason="Advanced controller removed in config-only architecture")
def test_advanced_controller_renders():
    """Legacy test - skipped as we no longer generate advanced controllers."""
    pass
