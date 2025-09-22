#!/usr/bin/env python3
import sys
import types
from typing import Any, cast

import pytest

from src import import_utils


def test_safe_import_absolute(monkeypatch):
    # Create a fake module in sys.modules
    mod = types.ModuleType("fake_mod")
    sys.modules["fake_mod"] = mod
    try:
        m = import_utils.safe_import("fake_mod")
        assert m is mod
    finally:
        sys.modules.pop("fake_mod", None)


def test_safe_import_class(monkeypatch):
    # Prepare a fake module with a class
    mod = types.ModuleType("fake_pkg.mod")

    class C:
        pass

    cast(Any, mod).C = C
    sys.modules["fake_pkg.mod"] = mod
    try:
        Cls = import_utils.safe_import_class("fake_pkg.mod", "C")
        assert Cls is C
    finally:
        sys.modules.pop("fake_pkg.mod", None)


def test_safe_import_class_missing_raises():
    mod = types.ModuleType("fake_mod2")
    sys.modules["fake_mod2"] = mod
    try:
        with pytest.raises(ImportError):
            import_utils.safe_import_class("fake_mod2", "Nope")
    finally:
        sys.modules.pop("fake_mod2", None)


def test_get_repo_manager_fallback(monkeypatch):
    # Make sure normal import fails so fallback is used

    def _raise_import_error(*args, **kwargs):
        raise ImportError("x")

    monkeypatch.setattr(import_utils, "safe_import_class", _raise_import_error)
    RM = import_utils.get_repo_manager()
    # Fallback has static methods that return predictable strings or raise
    s1 = RM.read_xdc_constraints("pcileech_35t325_x4")
    assert "Fallback XDC constraints" in s1
    s2 = RM.read_combined_xdc("pcileech_35t325_x4")
    assert "Fallback XDC constraints" in s2
    with pytest.raises(RuntimeError):
        RM.ensure_git_repo()
