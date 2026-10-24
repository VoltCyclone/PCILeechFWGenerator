"""Independent schema fixtures for tests, not usable production donor profiles."""

import hashlib
import json
from uuid import uuid4


def config_bytes():
    data = bytearray(256)
    data[:4] = bytes.fromhex("12191400")
    data[8:12] = bytes.fromhex("0330030c")
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
        "subsystem_vendor_id": int.from_bytes(config[44:46], "little"),
        "subsystem_device_id": int.from_bytes(config[46:48], "little"),
    }
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
        },
    )
