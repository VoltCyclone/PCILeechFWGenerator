import json
import os
from pathlib import Path

import pytest

import pcileechfwgenerator.device_clone.device_config as dc


def test_get_device_config_returns_none_when_missing():
    # Ensure the global manager is reset to avoid cached config_dir
    dc._config_manager = None

    # get_device_config should return None (graceful fallback) when profile
    # does not exist and no config_dir was provided.
    assert dc.get_device_config("this_profile_does_not_exist") is None


def test_create_profile_from_env(monkeypatch):
    # Provide environment variables and verify a profile can be created
    monkeypatch.setenv("PCIE_TEST_VENDOR_ID", "0x1AF4")
    monkeypatch.setenv("PCIE_TEST_DEVICE_ID", "0x1000")
    monkeypatch.setenv("PCIE_TEST_CLASS_CODE", "0x020000")

    manager = dc.DeviceConfigManager(config_dir=None)
    profile = manager.create_profile_from_env("test")

    assert profile.name == "test"
    assert profile.identification.vendor_id == 0x1AF4
    assert profile.identification.device_id == 0x1000
    assert profile.identification.class_code == 0x020000


def test_load_json_profile_with_config_dir(tmp_path):
    # Create a minimal JSON device profile on disk and ensure it loads when
    # a config_dir is provided.
    profile = {
        "name": "diskdevice",
        "device_type": "generic",
        "device_class": "consumer",
        "identification": {
            "vendor_id": 0x1AF4,
            "device_id": 0x1001,
            "class_code": 0x020000,
        },
        "registers": {},
        "capabilities": {"max_payload_size": 128, "msi_vectors": 1},
    }

    config_dir = tmp_path / "configs"
    config_dir.mkdir()
    file_path = config_dir / "diskdevice.json"
    file_path.write_text(json.dumps(profile))

    manager = dc.DeviceConfigManager(config_dir=config_dir)
    loaded = manager.get_profile("diskdevice")

    assert loaded.name == "diskdevice"
    assert loaded.identification.vendor_id == 0x1AF4
    assert loaded.identification.device_id == 0x1001
