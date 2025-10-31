import json
import logging
from pathlib import Path
from typing import Any, Dict, Optional

import pytest

from src.build import MSIXData
from src.cli.host_device_collector import HostDeviceCollector


class _DummyBinder:
    def __init__(self, *args, **kwargs):
        pass

    def __enter__(self):
        return self

    def __exit__(self, exc_type, exc, tb):
        return False


class _FakeConfigSpaceManager:
    def __init__(self, bdf: str, strict_vfio: bool = True):
        self.bdf = bdf
        self.strict_vfio = strict_vfio

    def read_vfio_config_space(self) -> bytes:
        # 256 bytes of deterministic data
        return bytes([i % 256 for i in range(256)])

    def extract_device_info(self, config_space_bytes: bytes) -> Dict[str, Any]:
        return {
            'vendor_id': 0x10DE,
            'device_id': 0x1AD7,
            'class_code': 0x030200,
            'revision_id': 0xA1,
            'bars': [{'index': 0, 'size': 0x1000}],
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


class _FakeContextBuilder:
    def __init__(
        self,
        device_bdf: str,
        validation_level: Any,
        logger: Optional[logging.Logger] = None,
    ):
        self.bdf = device_bdf
        self.validation_level = validation_level
        self.logger = logger or logging.getLogger(__name__)

    def build_context(self, **kwargs) -> Dict[str, Any]:
        # Return a minimal, deterministic context
        cfg_hex = (
            kwargs.get('config_space_data', {}).get('config_space_hex', '')
        )
        return {
            'device_config': {
                'vendor_id': '10de',
                'device_id': '1ad7',
                'class_code': '030200',
                'revision_id': 'a1',
            },
            'msix_config': {'is_supported': False, 'num_vectors': 0},
            # Include hex to mimic what build paths expect sometimes
            'config_space_hex': cfg_hex,
        }


@pytest.fixture
def logger():
    return logging.getLogger('HostDeviceCollectorTests')


def test_collect_device_context_success(monkeypatch, tmp_path: Path, logger, caplog):
    # Patch heavy dependencies with fakes
    import src.cli.host_device_collector as hdc

    monkeypatch.setattr(hdc, 'VFIOBinder', _DummyBinder)
    monkeypatch.setattr(hdc, 'ConfigSpaceManager', _FakeConfigSpaceManager)
    monkeypatch.setattr(hdc, 'DeviceInfoLookup', _FakeDeviceInfoLookup)
    monkeypatch.setattr(hdc, 'PCILeechContextBuilder', _FakeContextBuilder)

    # Avoid exercising MSI-X path complexity here
    monkeypatch.setattr(
        HostDeviceCollector,
        '_collect_msix_data_vfio',
        lambda self, mgr, cfg: MSIXData(preloaded=False),
    )

    collector = HostDeviceCollector('0000:03:00.0', logger=logger)

    with caplog.at_level(logging.INFO):
        ctx = collector.collect_device_context(tmp_path)

    # Validate returned context shape
    assert isinstance(ctx, dict)
    assert ctx.get('device_config', {}).get('vendor_id') == '10de'

    # Validate files written
    context_file = tmp_path / 'device_context.json'
    assert context_file.exists()
    payload = json.loads(context_file.read_text())
    assert payload.get('bdf') == '0000:03:00.0'
    assert 'template_context' in payload
    # msix_data should be None since preloaded False
    assert payload.get('msix_data') is None


def test_collect_device_context_failure_raises_build_error(
    monkeypatch, tmp_path: Path, logger, caplog
):
    import src.cli.host_device_collector as hdc

    class _BoomConfigManager(_FakeConfigSpaceManager):
        def read_vfio_config_space(self) -> bytes:
            raise RuntimeError('vfio read failed')

    monkeypatch.setattr(hdc, 'VFIOBinder', _DummyBinder)
    monkeypatch.setattr(hdc, 'ConfigSpaceManager', _BoomConfigManager)

    collector = HostDeviceCollector('0000:03:00.0', logger=logger)

    with caplog.at_level(logging.ERROR), pytest.raises(Exception) as exc:
        collector.collect_device_context(tmp_path)

    # Ensure BuildError surface
    from src.exceptions import BuildError

    assert isinstance(exc.value, BuildError)
    assert any(
        'Failed to collect device context' in r.message for r in caplog.records
    )


def test_collect_msix_found(monkeypatch, logger):
    # Patch parse_msix_capability to report a valid capability
    
    def _fake_parse(cfg_hex: str):
        return {
            'table_size': 8,
            'table_bir': 0,
            'table_offset': 0x1000,
            'pba_bir': 0,
            'pba_offset': 0x2000,
            'enabled': False,
            'function_mask': 0,
        }

    monkeypatch.setattr(
        'src.device_clone.msix_capability.parse_msix_capability', _fake_parse
    )

    collector = HostDeviceCollector('0000:03:00.0', logger=logger)
    data = collector._collect_msix_data_vfio(
        msix_manager=None, config_space_bytes=b'\x01\x02\x03\x04'
    )

    assert isinstance(data, MSIXData)
    assert data.preloaded is True
    assert data.msix_info['table_size'] == 8
    assert data.config_space_hex == b'\x01\x02\x03\x04'.hex()


def test_collect_msix_not_found(monkeypatch, logger):
    # Return None -> treated as absent capability
    monkeypatch.setattr(
        'src.device_clone.msix_capability.parse_msix_capability',
        lambda cfg_hex: None,
    )
    collector = HostDeviceCollector('0000:03:00.0', logger=logger)
    data = collector._collect_msix_data_vfio(
        msix_manager=None, config_space_bytes=b'\x00' * 16
    )

    assert isinstance(data, MSIXData)
    assert data.preloaded is False


def test_collect_msix_exception_logs_and_recovers(monkeypatch, logger, caplog):
    
    def _boom(_cfg_hex: str):
        raise ValueError('parse error')

    monkeypatch.setattr(
        'src.device_clone.msix_capability.parse_msix_capability', _boom
    )
    collector = HostDeviceCollector('0000:03:00.0', logger=logger)
    with caplog.at_level(logging.WARNING):
        data = collector._collect_msix_data_vfio(
            msix_manager=None, config_space_bytes=b'\xff' * 32
        )

    assert data.preloaded is False
    assert any('MSI-X collection failed' in r.message for r in caplog.records)


def test_save_collected_data_writes_both_files(tmp_path: Path, logger):
    collector = HostDeviceCollector('0000:03:00.0', logger=logger)

    data = {
        'bdf': '0000:03:00.0',
        'config_space_hex': '01020304',
        'device_info': {'vendor_id': 0x10DE, 'device_id': 0x1AD7},
        'msix_data': {
            'msix_info': {
                'table_size': 4,
                'table_bir': 0,
                'table_offset': 0x1000,
                'pba_bir': 0,
                'pba_offset': 0x2000,
                'enabled': False,
                'function_mask': 0,
            }
        },
        'template_context': {'device_config': {'vendor_id': '10de'}},
        'collection_metadata': {
            'collected_at': 123.0,
            'config_space_size': 256,
            'has_msix': True,
            'collector_version': '1.0',
        },
    }

    collector._save_collected_data(tmp_path, data)

    context_path = tmp_path / 'device_context.json'
    msix_path = tmp_path / 'msix_data.json'

    assert context_path.exists()
    assert msix_path.exists()

    msix_payload = json.loads(msix_path.read_text())
    assert msix_payload['bdf'] == '0000:03:00.0'
    assert msix_payload['msix_info']['table_size'] == 4
