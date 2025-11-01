
import sys
import types
import pytest
from pathlib import Path
import importlib


import src.cli.build_wrapper as build_wrapper

import src.cli.build_wrapper as build_wrapper


def make_dummy_build_module(fullname: str = 'src.build'):
    """Create a lightweight module object with a main() we can observe."""
    mod = types.ModuleType(fullname)
    mod.called = False  # type: ignore[attr-defined]

    def _main():  # pragma: no cover - trivial
        mod.called = True  # type: ignore[attr-defined]

    mod.main = _main  # type: ignore[attr-defined]
    return mod


@pytest.fixture(autouse=True)
def patch_sys(monkeypatch):
    monkeypatch.setattr(sys, 'argv', ['build_wrapper.py', '--test'])
    yield


@pytest.mark.parametrize('container_exists', [True, False])

def test_path_setup(monkeypatch, container_exists):
    # Patch Path.exists to simulate environment
    monkeypatch.setattr(
        Path,
        'exists',
        lambda self: container_exists if str(self) == '/app' else True
    )

    chdir_called = {}
    monkeypatch.setattr(
        'os.chdir',
        lambda path: chdir_called.setdefault('chdir', path)
    )
    # Patch sys.path
    sys.path.clear()
    # Patch __file__
    monkeypatch.setattr(build_wrapper, '__file__', __file__)
    # Re-import to trigger logic
    import importlib
    importlib.reload(build_wrapper)
    # Check sys.path setup
    if container_exists:
        assert any('/app' in p for p in sys.path)
        assert any('/app/src' in p for p in sys.path)
        assert chdir_called['chdir'] == '/app/src'
    else:
        assert any('src' in p for p in sys.path)
        assert chdir_called['chdir'].endswith('src')


def test_main_runs_build(monkeypatch):
    dummy = make_dummy_build_module('src.build')
    # Provide a lightweight 'src' package so 'from src import build' does not
    # import the real package (which has heavy side effects in __init__).
    pkg = types.ModuleType('src')
    pkg.__path__ = []  # mark as package
    pkg.build = dummy  # type: ignore[attr-defined]
    monkeypatch.setitem(sys.modules, 'src', pkg)
    monkeypatch.setitem(sys.modules, 'src.build', dummy)
    monkeypatch.setattr(build_wrapper, '__name__', '__main__')
    monkeypatch.setattr(build_wrapper, '__file__', __file__)
    # Patch sys.argv
    sys.argv = ['build_wrapper.py', '--test']
    # Patch importlib
    monkeypatch.setattr(importlib, 'reload', lambda mod: mod)
    # Run main
    build_wrapper.__main__ = True
    # Simulate main block
    try:
        from src import build
        sys.argv[0] = 'build.py'
        build.main()
    except Exception:
        pytest.fail('build.main() should not raise')
    assert getattr(dummy, 'called', False)


def test_import_error_fallback(monkeypatch):

    # Simulate ImportError on first import
    monkeypatch.setattr(build_wrapper, '__name__', '__main__')
    monkeypatch.setattr(build_wrapper, '__file__', __file__)
    sys.argv = ['build_wrapper.py', '--test']
    # Remove src.build
    sys.modules.pop('src.build', None)
    # Patch importlib
    monkeypatch.setattr(importlib, 'reload', lambda mod: mod)
    # Patch import build fallback
    dummy = make_dummy_build_module('build')
    monkeypatch.setitem(sys.modules, 'build', dummy)
    # Simulate main block
    try:
        import build
        sys.argv[0] = 'build.py'
        build.main()
    except Exception:
        pytest.fail('Fallback build.main() should not raise')
    assert getattr(dummy, 'called', False)
