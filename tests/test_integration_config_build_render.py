import pytest

from pcileechfwgenerator.device_clone.device_config import (DeviceCapabilities, DeviceClass,
                                            DeviceConfiguration,
                                            DeviceIdentification, DeviceType,
                                            PCIeRegisters)
from pcileechfwgenerator.templating.template_renderer import TemplateRenderer
from pcileechfwgenerator.utils.unified_context import (UnifiedContextBuilder,
                                       ensure_template_compatibility)


def test_integration_config_build_render(tmp_path):
    # Step 1: Create a device configuration
    config = DeviceConfiguration(
        name="integration_test_device",
        device_type=DeviceType.NETWORK,
        device_class=DeviceClass.CONSUMER,
        identification=DeviceIdentification(
            vendor_id=0x8086,
            device_id=0x1000,
            class_code=0x020000,
        ),
        registers=PCIeRegisters(),
        capabilities=DeviceCapabilities(),
    )
    # Step 2: Build context using UnifiedContextBuilder
    builder = UnifiedContextBuilder()
    context = builder.create_complete_template_context(
        vendor_id="0x8086",
        device_id="0x1000",
        device_type="network",
        device_class="consumer",
        config=config.to_dict(),
    )
    context = ensure_template_compatibility(dict(context))
    # Step 3: Render a simple template
    renderer = TemplateRenderer(template_dir=tmp_path)
    template_file = tmp_path / "integration.j2"
    template_file.write_text(
        "Device: {{ config.name }} | Vendor: {{ vendor_id }} | Type: {{ device_type }}"
    )
    result = renderer.render_template("integration.j2", context)
    assert "integration_test_device" in result
    assert "0x8086" in result
    assert "network" in result


# Edge case: missing required context key should fail


def test_integration_missing_context_key(tmp_path):
    builder = UnifiedContextBuilder()
    context = builder.create_complete_template_context(
        vendor_id="0x8086",
        device_id="0x1000",
        device_type="network",
        device_class="consumer",
        # config missing
    )
    context = ensure_template_compatibility(dict(context))
    renderer = TemplateRenderer(template_dir=tmp_path)
    template_file = tmp_path / "fail.j2"
    template_file.write_text("Device: {{ config.name }}")
    with pytest.raises(Exception):
        renderer.render_template("fail.j2", context)
