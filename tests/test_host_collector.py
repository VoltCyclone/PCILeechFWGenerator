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

    assert "config_space_hex" in msix
    assert "msix_info" in msix
    assert isinstance(msix["msix_info"], dict)
