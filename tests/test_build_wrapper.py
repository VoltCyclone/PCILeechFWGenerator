"""Tests for the thin build_wrapper delegation shim.

build_wrapper no longer manipulates sys.path / cwd; it simply delegates to
``pcileechfwgenerator.build.main``. These tests assert that delegation and the
exit-code contract.
"""

import sys


import pcileechfwgenerator.cli.build_wrapper as build_wrapper


def test_main_delegates_to_build_main(monkeypatch):
    """build_wrapper.main() should call build.main() and return its code."""
    called = {}

    def fake_main():
        called["yes"] = True
        return 0

    monkeypatch.setattr(build_wrapper.build, "main", fake_main)
    monkeypatch.setattr(sys, "argv", ["build_wrapper.py", "--test"])

    rc = build_wrapper.main()

    assert called.get("yes") is True
    assert rc == 0
    # argv[0] is normalized for downstream argparse.
    assert sys.argv[0] == "build.py"


def test_main_normalizes_none_return_to_zero(monkeypatch):
    """build.main() returning None must map to exit code 0."""
    monkeypatch.setattr(build_wrapper.build, "main", lambda: None)
    monkeypatch.setattr(sys, "argv", ["build_wrapper.py"])

    assert build_wrapper.main() == 0


def test_main_propagates_nonzero_exit_code(monkeypatch):
    """A non-zero return from build.main() is propagated unchanged."""
    monkeypatch.setattr(build_wrapper.build, "main", lambda: 2)
    monkeypatch.setattr(sys, "argv", ["build_wrapper.py"])

    assert build_wrapper.main() == 2


def test_module_does_not_mutate_sys_path():
    """Importing the wrapper must not insert anything into sys.path."""
    before = list(sys.path)
    import importlib

    importlib.reload(build_wrapper)
    assert sys.path == before
