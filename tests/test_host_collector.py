import types
import sys
from pathlib import Path

import json
import pytest


def test_host_collector_writes_datastore(tmp_path, monkeypatch):
    # Lazy import inside function; create a fake config space
    cfg_bytes = bytes(range(256))

    # Import the collector
    from src.host_collect.collector import HostCollector

    # Monkeypatch _read_config_space to avoid touching /sys
    monkeypatch.setattr(HostCollector, "_read_config_space", lambda self: cfg_bytes)

    # Run
    hc = HostCollector(bdf="0000:03:00.0", datastore=tmp_path, logger=None)
    rc = hc.run()
    assert rc == 0

    # Validate files
    ctx_path = tmp_path / "device_context.json"
    msix_path = tmp_path / "msix_data.json"
    assert ctx_path.exists()
    assert msix_path.exists()

    ctx = json.loads(ctx_path.read_text())
    msix = json.loads(msix_path.read_text())

    assert "config_space_hex" in ctx
    assert isinstance(ctx["config_space_hex"], str)
    # Should be 512 hex chars for 256 bytes
    assert len(ctx["config_space_hex"]) == 512

    # Verify device IDs are extracted and saved (Bug #511 fix)
    assert "vendor_id" in ctx, "device_context.json should include vendor_id"
    assert "device_id" in ctx, "device_context.json should include device_id"
    assert "class_code" in ctx, "device_context.json should include class_code"
    assert "revision_id" in ctx, "device_context.json should include revision_id"
    
    # Verify the extracted values match the config space
    # cfg_bytes = bytes(range(256)), so bytes 0-1 are 0x0100 (little endian)
    assert ctx["vendor_id"] == 0x0100, "vendor_id should be extracted from config space"
    assert ctx["device_id"] == 0x0302, "device_id should be extracted from config space"

    assert "config_space_hex" in msix
    assert "msix_info" in msix
    assert isinstance(msix["msix_info"], dict)
