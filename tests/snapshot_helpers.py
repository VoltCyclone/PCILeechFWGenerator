"""Independent schema fixtures for tests, not usable production donor profiles."""

import hashlib
import json
from uuid import uuid4

# Stands in for this host's fingerprint; tests/conftest.py pins it.
TEST_HOST_FINGERPRINT = "f" * 64


def host_binding_record(fingerprint=TEST_HOST_FINGERPRINT):
    return {
        "schema_version": 1,
        "method": "machine-id-sha256",
        "fingerprint": fingerprint,
    }


def config_bytes():
    data = bytearray(256)
    data[:4] = bytes.fromhex("12191400")
    data[8:12] = bytes.fromhex("0330030c")
    return bytes(data)


def make_config(
    *,
    vendor_id=0x8086,
    device_id=0x10D3,
    revision_id=0x00,
    class_code=0x020000,
    header_type=0x00,
    subsystem_vendor_id=0x8086,
    subsystem_device_id=0xA01F,
):
    """Build a 4096-byte config space; subsystem fields apply to type-0 only."""
    data = bytearray(4096)
    data[0:2] = vendor_id.to_bytes(2, "little")
    data[2:4] = device_id.to_bytes(2, "little")
    data[8] = revision_id
    data[9:12] = class_code.to_bytes(3, "little")
    data[0x0E] = header_type
    if header_type & 0x7F == 0:
        data[0x2C:0x2E] = subsystem_vendor_id.to_bytes(2, "little")
        data[0x2E:0x30] = subsystem_device_id.to_bytes(2, "little")
    return bytes(data)


def make_snapshot(
    directory, config=None, bdf="0000:03:00.0", msix_info=None, context=None
):
    """Write schema-v1 fixture files independently of the production writer."""
    config = config_bytes() if config is None else config
    ids = {
        "vendor_id": int.from_bytes(config[0:2], "little"),
        "device_id": int.from_bytes(config[2:4], "little"),
        "revision_id": config[8],
        "class_code": int.from_bytes(config[9:12], "little"),
    }
    if config[0x0E] & 0x7F == 0:  # type-1 bridges have no subsystem registers
        ids["subsystem_vendor_id"] = int.from_bytes(config[44:46], "little")
        ids["subsystem_device_id"] = int.from_bytes(config[46:48], "little")
    capture = {
        "schema_version": 1,
        "capture_id": str(uuid4()),
        "collected_at": "2026-10-24T00:00:00+00:00",
        "bdf": bdf,
        **ids,
        "config_space_size": len(config),
        "config_space_sha256": hashlib.sha256(config).hexdigest(),
    }
    common = {"capture": capture, "bdf": bdf, "config_space_hex": config.hex()}
    directory.mkdir(parents=True, exist_ok=True)
    path = directory / "device_context.json"
    path.write_text(json.dumps({**(context or {}), **ids, **common}))
    (directory / "msix_data.json").write_text(
        json.dumps({**common, "msix_info": msix_info or {}})
    )
    return path


def offline_fixture(directory, *, size=4096, msix=False):
    config = bytearray(config_bytes())
    config[0x10:0x14] = (0x80000000).to_bytes(4, "little")
    info = {}
    if msix:
        config[6] = 0x10
        config[0x34] = 0x40
        config[0x40] = 0x11
        config[0x42:0x44] = (3).to_bytes(2, "little")
        config[0x44:0x48] = (0x1000).to_bytes(4, "little")
        config[0x48:0x4C] = (0x2000).to_bytes(4, "little")
        info = dict(
            table_size=4,
            table_bir=0,
            table_offset=0x1000,
            pba_bir=0,
            pba_offset=0x2000,
            enabled=False,
            function_mask=False,
        )
    return make_snapshot(
        directory,
        bytes(config),
        msix_info=info,
        context={
            "offline_resources": {
                "schema_version": 1,
                "source": "linux_sysfs_resource",
                "bar_sizes": [size, 0, 0, 0, 0, 0],
            },
            "host_binding": host_binding_record(),
        },
    )
