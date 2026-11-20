import json
import logging
from pathlib import Path
from types import SimpleNamespace
from typing import Any, Dict

import pytest
from pcileechfwgenerator.cli.host_device_collector import HostDeviceCollector
from pcileechfwgenerator.device_clone.msix import MSIXData


class _DummyBinder:
    def __init__(self, *args, **kwargs):
        self.original_driver = "native"
        self._path_manager = SimpleNamespace(
            driver_path=SimpleNamespace(
                is_symlink=lambda: True,
                resolve=lambda: SimpleNamespace(name="native"),
            ),
            override_path=SimpleNamespace(read_text=lambda: "\n"),
        )

    def __enter__(self):
        return self

    def unbind(self):
        pass

    def __exit__(self, exc_type, exc, tb):
        return False


class _FakeConfigSpaceManager:
    def __init__(self, bdf: str, strict_vfio: bool = True):
        self.bdf = bdf
        self.strict_vfio = strict_vfio

    def read_vfio_config_space(self) -> bytes:
        # In-memory header consistent with extracted_info below.
        data = bytearray(256)
        data[:4] = bytes.fromhex("de10d71a")
        data[8:12] = bytes.fromhex("a1000203")
        return bytes(data)

    def extract_device_info(self, config_space_bytes: bytes) -> Dict[str, Any]:
        return {
            "vendor_id": 0x10DE,
            "device_id": 0x1AD7,
            "class_code": 0x030200,
            "revision_id": 0xA1,
            "bars": [{"index": 0, "size": 0x1000}],
        }


class _FakeDeviceInfoLookup:
    def __init__(self, bdf: str):
        self.bdf = bdf

    def get_complete_device_info(
        self,
        extracted_info: Dict[str, Any],
        from_config_manager: bool = False,
    ) -> Dict[str, Any]:
        # Echo-through for our tests, ensure required keys exist
        return dict(extracted_info)


@pytest.fixture
def logger():
    return logging.getLogger("HostDeviceCollectorTests")


def test_collect_device_context_success(monkeypatch, tmp_path: Path, logger, caplog):
    # Patch heavy dependencies with fakes
    import pcileechfwgenerator.cli.host_device_collector as hdc

    monkeypatch.setattr(hdc, "VFIOBinder", _DummyBinder)
    monkeypatch.setattr(hdc, "ConfigSpaceManager", _FakeConfigSpaceManager)
    monkeypatch.setattr(hdc, "DeviceInfoLookup", _FakeDeviceInfoLookup)

    # Avoid exercising MSI-X path complexity here
    monkeypatch.setattr(
        HostDeviceCollector,
        "_collect_msix_data_vfio",
        lambda self, mgr, cfg: MSIXData(preloaded=False),
    )

    collector = HostDeviceCollector("0000:03:00.0", logger=logger)

    with caplog.at_level(logging.INFO):
        collected_data = collector.collect_device_context(tmp_path)

    # Validate returned collected_data shape
    assert isinstance(collected_data, dict)
    assert collected_data.get("bdf") == "0000:03:00.0"
    assert "device_info" in collected_data
    assert "collection_metadata" in collected_data

    # Validate files written
    context_file = tmp_path / "device_context.json"
    assert context_file.exists()
    payload = json.loads(context_file.read_text())
    assert payload.get("bdf") == "0000:03:00.0"
    # msix_data should be None since preloaded False
    assert payload.get("msix_data") is None
    assert payload.get("device_info", {}).get("vendor_id") == 0x10DE


def test_collect_device_context_failure_raises_build_error(
    monkeypatch, tmp_path: Path, logger, caplog
):
    import pcileechfwgenerator.cli.host_device_collector as hdc

    class _BoomConfigManager(_FakeConfigSpaceManager):
        def read_vfio_config_space(self) -> bytes:
            raise RuntimeError("vfio read failed")

    monkeypatch.setattr(hdc, "VFIOBinder", _DummyBinder)
    monkeypatch.setattr(hdc, "ConfigSpaceManager", _BoomConfigManager)

    collector = HostDeviceCollector("0000:03:00.0", logger=logger)

    with caplog.at_level(logging.ERROR), pytest.raises(Exception) as exc:
        collector.collect_device_context(tmp_path)

    # Ensure BuildError surface
    from pcileechfwgenerator.exceptions import BuildError

    assert isinstance(exc.value, BuildError)
    assert any("Failed to collect device context" in r.message for r in caplog.records)


def test_collect_msix_found(monkeypatch, logger):
    # Patch parse_msix_capability to report a valid capability

    def _fake_parse(cfg_hex: str):
        return {
            "table_size": 8,
            "table_bir": 0,
            "table_offset": 0x1000,
            "pba_bir": 0,
            "pba_offset": 0x2000,
            "enabled": False,
            "function_mask": 0,
        }

    monkeypatch.setattr(
        "pcileechfwgenerator.cli.host_device_collector.parse_msix_capability",
        _fake_parse,
    )

    collector = HostDeviceCollector("0000:03:00.0", logger=logger)
    data = collector._collect_msix_data_vfio(
        msix_manager=None, config_space_bytes=b"\x01\x02\x03\x04"
    )

    assert isinstance(data, MSIXData)
    assert data.preloaded is True
    assert data.msix_info["table_size"] == 8
    assert data.config_space_hex == b"\x01\x02\x03\x04".hex()


def test_collect_msix_not_found(monkeypatch, logger):
    # Return None -> treated as absent capability
    monkeypatch.setattr(
        "pcileechfwgenerator.cli.host_device_collector.parse_msix_capability",
        lambda cfg_hex: None,
    )
    collector = HostDeviceCollector("0000:03:00.0", logger=logger)
    data = collector._collect_msix_data_vfio(
        msix_manager=None, config_space_bytes=b"\x00" * 16
    )

    assert isinstance(data, MSIXData)
    assert data.preloaded is False


def test_collect_msix_exception_logs_and_recovers(monkeypatch, logger, caplog):

    def _boom(_cfg_hex: str):
        raise ValueError("parse error")

    monkeypatch.setattr(
        "pcileechfwgenerator.cli.host_device_collector.parse_msix_capability", _boom
    )
    collector = HostDeviceCollector("0000:03:00.0", logger=logger)
    with caplog.at_level(logging.WARNING):
        data = collector._collect_msix_data_vfio(
            msix_manager=None, config_space_bytes=b"\xff" * 32
        )

    assert data.preloaded is False
    assert any("MSI-X collection failed" in r.message for r in caplog.records)


def test_save_collected_data_writes_both_files(tmp_path: Path, logger):
    collector = HostDeviceCollector("0000:03:00.0", logger=logger)

    data = {
        "bdf": "0000:03:00.0",
        "config_space_hex": _FakeConfigSpaceManager("0000:03:00.0")
        .read_vfio_config_space()
        .hex(),
        "device_info": {"vendor_id": 0x10DE, "device_id": 0x1AD7},
        "msix_data": {
            "msix_info": {
                "table_size": 4,
                "table_bir": 0,
                "table_offset": 0x1000,
                "pba_bir": 0,
                "pba_offset": 0x2000,
                "enabled": False,
                "function_mask": 0,
            }
        },
        "collection_metadata": {
            "collected_at": 123.0,
            "config_space_size": 256,
            "has_msix": True,
            "collector_version": "1.0",
        },
    }

    collector._save_collected_data(tmp_path, data)

    context_path = tmp_path / "device_context.json"
    msix_path = tmp_path / "msix_data.json"

    assert context_path.exists()
    assert msix_path.exists()

    msix_payload = json.loads(msix_path.read_text())
    assert msix_payload["bdf"] == "0000:03:00.0"
    assert msix_payload["msix_info"]["table_size"] == 4


def test_full_collector_publishes_serializable_msix_capture(
    monkeypatch, tmp_path, logger
):
    import pcileechfwgenerator.cli.host_device_collector as hdc
    from pcileechfwgenerator.host_collect.snapshot import load_snapshot

    monkeypatch.setattr(hdc, "VFIOBinder", _DummyBinder)
    from tests.snapshot_helpers import msix_config

    class _MsixConfigSpaceManager(_FakeConfigSpaceManager):
        def read_vfio_config_space(self) -> bytes:
            # The real parser runs on these bytes; the sidecar must match them.
            return msix_config(
                dict(
                    table_size=4,
                    table_bir=0,
                    table_offset=0x1000,
                    pba_bir=0,
                    pba_offset=0x2000,
                    enabled=False,
                    function_mask=False,
                ),
                super().read_vfio_config_space(),
            )

    monkeypatch.setattr(hdc, "ConfigSpaceManager", _MsixConfigSpaceManager)
    monkeypatch.setattr(hdc, "DeviceInfoLookup", _FakeDeviceInfoLookup)
    collector = HostDeviceCollector("03:00.0", logger, enable_mmio_learning=False)
    result = collector.collect_device_context(tmp_path)
    snapshot = load_snapshot(
        tmp_path / "device_context.json", tmp_path / "msix_data.json", "03:00.0"
    )
    assert result == snapshot.context
    assert snapshot.msix["msix_info"]["table_size"] == 4
    assert "config_space_bytes" not in result["msix_data"]


def test_full_collector_rejects_occupied_destination_before_binding(
    monkeypatch, tmp_path
):
    from unittest.mock import Mock

    from pcileechfwgenerator.host_collect.snapshot import SnapshotError

    existing = tmp_path / "device_context.json"
    existing.write_bytes(b"previous capture")
    binder = Mock(side_effect=AssertionError("unexpected bind"))
    monkeypatch.setattr(
        "pcileechfwgenerator.cli.host_device_collector.VFIOBinder", binder
    )
    with pytest.raises(SnapshotError, match="already contains"):
        HostDeviceCollector("03:00.0").collect_device_context(tmp_path)
    binder.assert_not_called()
    assert existing.read_bytes() == b"previous capture"


def test_shared_service_observes_before_single_bind_and_publishes_after_release(
    monkeypatch,
    tmp_path,
):
    import pcileechfwgenerator.cli.host_device_collector as module
    from pcileechfwgenerator.host_collect.register_observer import ObserverResult
    from pcileechfwgenerator.host_collect.snapshot import load_snapshot

    events = []
    manifests = []

    class Binder(_DummyBinder):
        def __enter__(self):
            assert "bind" not in events
            events.append("bind")
            return self

        def unbind(self):
            events.append("unbind")

        def __exit__(self, *args):
            events.append("release")

    class Manager(_FakeConfigSpaceManager):
        def __init__(self, bdf, strict_vfio=True):
            assert strict_vfio is False
            super().__init__(bdf, strict_vfio)

        def read_vfio_config_space(self):
            events.append("baseline" if "bind" in events else "discover")
            return super().read_vfio_config_space()

    service = module.run_register_learning

    def invoke(*args, **kwargs):
        kwargs["passive_observer"] = lambda *_: (
            events.append("observe") or ObserverResult()
        )
        manifest = service(*args, **kwargs)
        manifests.append(manifest)
        return manifest

    writer = module.write_snapshot

    def publish(*args, **kwargs):
        assert events[-1] == "release"
        events.append("publish")
        return writer(*args, **kwargs)

    monkeypatch.setattr(module, "VFIOBinder", Binder)
    monkeypatch.setattr(module, "ConfigSpaceManager", Manager)
    monkeypatch.setattr(module, "DeviceInfoLookup", _FakeDeviceInfoLookup)
    monkeypatch.setattr(module, "run_register_learning", invoke)
    monkeypatch.setattr(module, "write_snapshot", publish)
    HostDeviceCollector("03:00.0", enable_mmio_learning=False).collect_device_context(
        tmp_path
    )
    assert events == [
        "discover",
        "observe",
        "bind",
        "baseline",
        "unbind",
        "release",
        "publish",
    ]
    snapshot = load_snapshot(
        tmp_path / "device_context.json", tmp_path / "msix_data.json", "03:00.0"
    )
    assert snapshot.learning == manifests[0]
    assert snapshot.learning_status == "partial"
    assert str(manifests[0].context.capture_id) == str(snapshot.identity.capture_id)


def test_collect_bar_models_missing_import_regression(tmp_path, monkeypatch):
    from pcileechfwgenerator.host_collect.register_learning import LearningDiscovery

    from tests.test_register_learning import session

    cfg = _FakeConfigSpaceManager("03:00.0").read_vfio_config_space()
    service, _, _ = session(tmp_path / "journals", LearningDiscovery.from_config(cfg))
    manifest = service.run()
    collector = HostDeviceCollector("03:00.0")
    info = {"bar_config": {"bars": [{"index": 0, "size": 4096}]}}
    assert collector._collect_bar_models(info, tmp_path) is None
    models = collector._collect_bar_models(info, tmp_path, learning=manifest)
    assert models[0]["regs"]["0x0"]["rw_mask"] == 1
    assert not (tmp_path / ".pcileech_cache").exists()


def test_unbind_failure_is_published_separately(monkeypatch, tmp_path):
    import pcileechfwgenerator.cli.host_device_collector as module
    from pcileechfwgenerator.host_collect.snapshot import load_snapshot

    events = []

    class Binder(_DummyBinder):
        def unbind(self):
            raise OSError("unbind failed")

        def __exit__(self, *args):
            events.append("lock released")

    monkeypatch.setattr(module, "VFIOBinder", Binder)
    monkeypatch.setattr(module, "ConfigSpaceManager", _FakeConfigSpaceManager)
    monkeypatch.setattr(module, "DeviceInfoLookup", _FakeDeviceInfoLookup)
    HostDeviceCollector("03:00.0").collect_device_context(tmp_path)
    snapshot = load_snapshot(
        tmp_path / "device_context.json", tmp_path / "msix_data.json", "03:00.0"
    )
    assert snapshot.learning_status == "failed"
    assert any(g.reason == "restoration_failed" for g in snapshot.learning.gaps)
    assert events == ["lock released"]


@pytest.mark.parametrize("failure", ["driver", "override"])
def test_binder_restoration_failure_reaches_the_manifest(
    monkeypatch, tmp_path, failure
):
    """The binder verifies restoration itself (tests/test_vfio_binder_transactions.py)
    and raises; the collector must turn that into a failed capture."""
    import pcileechfwgenerator.cli.host_device_collector as module
    from pcileechfwgenerator.exceptions import VFIOBindError
    from pcileechfwgenerator.host_collect.snapshot import load_snapshot

    detail = {
        "driver": "driver is vfio-pci (expected native)",
        "override": "driver_override was not restored",
    }[failure]

    class Binder(_DummyBinder):
        def unbind(self):
            raise VFIOBindError(
                "Original binding of 0000:03:00.0 not restored: " + detail
            )

    monkeypatch.setattr(module, "VFIOBinder", Binder)
    monkeypatch.setattr(module, "ConfigSpaceManager", _FakeConfigSpaceManager)
    monkeypatch.setattr(module, "DeviceInfoLookup", _FakeDeviceInfoLookup)
    HostDeviceCollector("03:00.0").collect_device_context(tmp_path)
    snapshot = load_snapshot(
        tmp_path / "device_context.json", tmp_path / "msix_data.json", "03:00.0"
    )
    assert snapshot.learning_status == "failed"
    assert any(
        g.reason == "restoration_failed" and detail in (g.detail or "")
        for g in snapshot.learning.gaps
    )


def test_collector_binder_gets_a_binding_journal(monkeypatch, tmp_path):
    import pcileechfwgenerator.cli.host_device_collector as module

    seen = {}

    class Binder(_DummyBinder):
        def __init__(self, *args, **kwargs):
            super().__init__(*args, **kwargs)
            seen.update(kwargs)

    monkeypatch.setattr(module, "VFIOBinder", Binder)
    monkeypatch.setattr(module, "ConfigSpaceManager", _FakeConfigSpaceManager)
    monkeypatch.setattr(module, "DeviceInfoLookup", _FakeDeviceInfoLookup)
    HostDeviceCollector("03:00.0").collect_device_context(tmp_path)
    assert seen["journal_directory"] == module.default_journal_directory() / "bindings"


def test_changed_identity_between_discovery_and_baseline_fails(monkeypatch, tmp_path):
    import pcileechfwgenerator.cli.host_device_collector as module
    from pcileechfwgenerator.exceptions import BuildError

    class Manager(_FakeConfigSpaceManager):
        reads = 0

        def read_vfio_config_space(self):
            Manager.reads += 1
            data = bytearray(super().read_vfio_config_space())
            if Manager.reads > 1:
                data[2] ^= 1
            return bytes(data)

    monkeypatch.setattr(module, "VFIOBinder", _DummyBinder)
    monkeypatch.setattr(module, "ConfigSpaceManager", Manager)
    with pytest.raises(BuildError):
        HostDeviceCollector("03:00.0").collect_device_context(tmp_path)
    assert not (tmp_path / "device_context.json").exists()


def test_real_bar_dataclass_shape_is_discovered_and_serialized(monkeypatch, tmp_path):
    import pcileechfwgenerator.cli.host_device_collector as module
    from pcileechfwgenerator.device_clone.config_space_manager import BarInfo
    from pcileechfwgenerator.host_collect.snapshot import load_snapshot

    class Manager(_FakeConfigSpaceManager):
        def extract_device_info(self, config):
            info = super().extract_device_info(config)
            info["bars"] = [
                BarInfo(index=0, bar_type="memory", address=0x80000000, size=4096)
            ]
            return info

    monkeypatch.setattr(module, "VFIOBinder", _DummyBinder)
    monkeypatch.setattr(module, "ConfigSpaceManager", Manager)
    monkeypatch.setattr(module, "DeviceInfoLookup", _FakeDeviceInfoLookup)
    HostDeviceCollector("03:00.0").collect_device_context(tmp_path)
    snapshot = load_snapshot(
        tmp_path / "device_context.json", tmp_path / "msix_data.json", "03:00.0"
    )
    assert snapshot.context["device_info"]["bars"][0]["size"] == 4096
    assert snapshot.learning_status == "partial"


def test_bind_failure_detail_reaches_build_error(tmp_path, monkeypatch):
    """A VFIO bind error must not be reduced to a generic baseline message."""
    from contextlib import contextmanager

    import pcileechfwgenerator.cli.host_device_collector as module
    from pcileechfwgenerator.exceptions import BuildError, VFIOBindError

    collector = module.HostDeviceCollector("0000:03:00.0")

    @contextmanager
    def failing_owner():
        raise VFIOBindError("IOMMU group 7 is busy")
        yield  # pragma: no cover

    monkeypatch.setattr(collector, "_learning_ownership", failing_owner)
    config = bytes.fromhex("86802115") + bytes(60)
    monkeypatch.setattr(
        module.ConfigSpaceManager,
        "read_vfio_config_space",
        lambda self: config,
        raising=False,
    )
    with pytest.raises(BuildError, match="IOMMU group 7 is busy"):
        collector.collect_device_context(tmp_path / "capture")
