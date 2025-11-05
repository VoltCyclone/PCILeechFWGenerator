import importlib
import json
import sys

from pathlib import Path

import pytest


MODULE_NAME = 'scripts.vfio_container_manager'


def load_module(monkeypatch, tmp_path, extra_env=None):
    extra_env = extra_env or {}
    monkeypatch.delenv('PCILEECH_VFIO_STATE_DIR', raising=False)
    for key, value in extra_env.items():
        monkeypatch.setenv(key, value)
    repo_root = Path(__file__).resolve().parents[1]
    sys.path.insert(0, str(repo_root))
    state_dir_value = extra_env.get('PCILEECH_VFIO_STATE_DIR', tmp_path / 'state')
    state_dir = Path(state_dir_value)
    monkeypatch.setenv('PCILEECH_VFIO_STATE_DIR', str(state_dir))
    sys.modules.pop(MODULE_NAME, None)
    return importlib.import_module(MODULE_NAME)


def test_resolve_state_dir_uses_override(monkeypatch, tmp_path):
    override = tmp_path / 'custom' / 'vfio'
    module = load_module(
        monkeypatch,
        tmp_path,
        {'PCILEECH_VFIO_STATE_DIR': str(override)},
    )
    assert module.STATE_DIR == override
    assert override.exists()


def test_resolve_state_dir_permission_fallback(monkeypatch, tmp_path):
    sys.modules.pop(MODULE_NAME, None)

    temp_dir = tmp_path / 'temp'
    temp_dir.mkdir()
    monkeypatch.delenv('PCILEECH_VFIO_STATE_DIR', raising=False)
    monkeypatch.setenv('XDG_STATE_HOME', str(tmp_path / 'xdg'))
    monkeypatch.setattr(Path, 'home', classmethod(lambda cls: tmp_path / 'home'))

    import scripts.vfio_container_manager as vcm

    original_mkdir = Path.mkdir
    temp_state = temp_dir / 'pcileech-vfio-state'
    fallback_state = Path.cwd() / '.pcileech-vfio-state'

    def fake_mkdir(self, parents=False, exist_ok=False):
        path_str = str(self)
        if path_str.startswith(str(temp_state)):
            return original_mkdir(self, parents=parents, exist_ok=exist_ok)
        if path_str.startswith(str(fallback_state)):
            return original_mkdir(self, parents=parents, exist_ok=exist_ok)
        raise PermissionError('denied')

    monkeypatch.setattr(Path, 'mkdir', fake_mkdir, raising=False)
    monkeypatch.setattr(vcm.tempfile, 'gettempdir', lambda: str(temp_dir))

    module = importlib.reload(vcm)
    assert module.STATE_DIR == temp_state


def test_bind_device_writes_state(monkeypatch, tmp_path):
    module = load_module(monkeypatch, tmp_path)

    captured_instances = []

    

    class DummyBinder:
        def __init__(self, bdf, attach=True):
            self.bdf = bdf
            self.attach = attach
            self.original_driver = 'ixgbe'
            captured_instances.append(self)

        def get_device_info(self):
            return module.DeviceInfo(
                bdf=self.bdf,
                vendor_id='1234',
                device_id='5678',
                iommu_group='55',
                driver='',
                description='',
            )

        def bind(self):
            self.bound = True

    monkeypatch.setattr(module, 'VFIOBinder', DummyBinder)

    manager = module.VFIOContainerManager()
    manager.state = {'devices': {}, 'containers': {}}

    group = manager.bind_device('0000:01:00.0')

    assert group == '55'
    assert captured_instances[0].bound is True
    assert '0000:01:00.0' in manager.state['devices']

    saved = json.loads(module.CONTAINER_STATE_FILE.read_text())
    assert '0000:01:00.0' in saved['devices']


def test_unbind_device_removes_state(monkeypatch, tmp_path):
    module = load_module(monkeypatch, tmp_path)

    created = []

    

    class DummyBinder:
        def __init__(self, bdf, attach=True):
            self.bdf = bdf
            self.attach = attach
            created.append(self)

        def unbind(self):
            self.unbound = True

    monkeypatch.setattr(module, 'VFIOBinder', DummyBinder)

    manager = module.VFIOContainerManager()
    manager.state = {
        'devices': {
            '0000:02:00.0': {
                'original_driver': 'ixgbe',
                'iommu_group': '66',
            }
        },
        'containers': {},
    }
    manager._save_state()

    assert manager.unbind_device('0000:02:00.0') is True
    assert created[0].unbound is True
    assert '0000:02:00.0' not in manager.state['devices']

    saved = json.loads(module.CONTAINER_STATE_FILE.read_text())
    assert '0000:02:00.0' not in saved['devices']
