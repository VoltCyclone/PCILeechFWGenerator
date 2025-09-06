import logging
from pathlib import Path
from unittest.mock import Mock

import pytest

from src.build import BuildConfiguration, FirmwareBuilder


def make_builder(monkeypatch, tmp_path: Path) -> FirmwareBuilder:
    # Prevent heavy component initialization during test
    monkeypatch.setattr(FirmwareBuilder, "_init_components", lambda self: None)

    cfg = BuildConfiguration(
        bdf="0000:00:00.0",
        board="pcileech_35t325_x4",
        output_dir=tmp_path,
    )

    builder = FirmwareBuilder(cfg)
    # Ensure logger is quiet for tests
    builder.logger = logging.getLogger("test")
    builder.logger.setLevel(logging.WARNING)
    return builder


def test_generate_firmware_injects_msix_defaults(monkeypatch, tmp_path: Path):
    """
    If the generator returns a result lacking template context, _generate_firmware
    must still populate a conservative MSI-X configuration so templates do not crash.
    """
    builder = make_builder(monkeypatch, tmp_path)

    # Mock generator to return minimal result (no template_context)
    mock_gen = Mock()
    mock_gen.generate_pcileech_firmware.return_value = {
        "systemverilog_modules": {},
        "config_space_data": {},
    }
    builder.gen = mock_gen

    result = builder._generate_firmware()

    assert "template_context" in result
    tc = result["template_context"]
    # Conservative defaults: msix_config present and indicates unsupported
    assert "msix_config" in tc
    assert tc["msix_config"].get("is_supported") is False
    assert tc["msix_config"].get("num_vectors") == 0
    # Ensure msix_data exists (explicitly set to None by conservative logic)
    assert "msix_data" in tc and tc["msix_data"] is None


def test_recheck_vfio_bindings_calls_helper(monkeypatch, tmp_path: Path, caplog):
    """
    _recheck_vfio_bindings should call the canonical helper and log a passing message.
    """
    builder = make_builder(monkeypatch, tmp_path)

    # Replace the ensure_device_vfio_binding helper used in vfio_helpers
    import src.cli.vfio_helpers as vfio_helpers

    monkeypatch.setattr(vfio_helpers, "ensure_device_vfio_binding", lambda bdf: "99")

    caplog.set_level(logging.INFO)
    # Should not raise
    builder._recheck_vfio_bindings()

    # Confirm the log contains the bdf and group
    found = any(
        "VFIO binding recheck passed" in rec.getMessage()
        or builder.config.bdf in rec.getMessage()
        for rec in caplog.records
    )
    assert found, "Expected VFIO recheck success message to be logged"
