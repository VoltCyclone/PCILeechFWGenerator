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
