import builtins
import importlib.util
import types

import pytest


def test_main_success_uses_src_build(monkeypatch):
    # Provide a fake src.build with a main that returns 0
    fake_build = types.ModuleType("src.build")
    fake_called = {"called": False}

    def fake_main():
        fake_called["called"] = True
        return 0

    fake_build.main = fake_main  # type: ignore[attr-defined]
    monkeypatch.setitem(__import__("sys").modules, "src.build", fake_build)

    from src.build_cli import main as cli_main

    rc = cli_main()
    assert rc == 0
    assert fake_called["called"] is True


def test_main_import_failure_falls_back_and_returns_1(monkeypatch):
    # Force ImportError for `from src.build import main as build_main`
    real_import = builtins.__import__

    def raising_import(name, *args, **kwargs):
        if name == "src.build":
            raise ImportError("forced for test")
        return real_import(name, *args, **kwargs)

    monkeypatch.setattr(builtins, "__import__", raising_import)

    # Also break the file-based import path by returning a spec without loader
    def fake_spec_from_file_location(*args, **kwargs):
        return types.SimpleNamespace(loader=None)

    monkeypatch.setattr(
        importlib.util,
        "spec_from_file_location",
        fake_spec_from_file_location,
    )

    from src.build_cli import main as cli_main

    rc = cli_main()
    # Expect non-zero (1) when imports fail and CLI logs guidance
    assert rc == 1
