"""Shared fixtures for the Textual TUI test suite.

Forces the Textual *headless* driver for every test in ``tests/tui``.

Why this exists
---------------
Tests that instantiate a Textual ``App`` (e.g. via ``App.run_test()``) trigger
``App.__init__`` -> ``App.get_driver_class()``. On a normal POSIX host that
imports ``textual.drivers.linux_driver``, whose module body does ``import tty``.

In the trimmed CI Python / hosted toolcache environment the ``tty`` stdlib
module is not importable, so constructing *any* ``App`` raises
``ModuleNotFoundError: No module named 'tty'`` before a headless driver can be
selected.

``App.get_driver_class()`` short-circuits on ``textual.constants.DRIVER``
(populated from the ``TEXTUAL_DRIVER`` env var at import time): when it is set
to a ``module:Class`` path it imports *that* driver and never touches the linux
driver. Pointing it at ``HeadlessDriver`` makes these tests run genuinely
headless in CI -- preserving their assertions -- without importing ``tty``.

We set both the environment variable and ``constants.DRIVER`` directly because
the constant is read once at module import; patching the attribute is what
takes effect for the already-imported module, while the env var keeps any
freshly-spawned/re-imported code in agreement.
"""

import pytest
from textual import constants

_HEADLESS_DRIVER = "textual.drivers.headless_driver:HeadlessDriver"


@pytest.fixture(autouse=True)
def _force_headless_textual_driver(monkeypatch):
    """Force Textual to use the headless driver (no ``tty`` import)."""
    monkeypatch.setenv("TEXTUAL_DRIVER", _HEADLESS_DRIVER)
    # raising=True (the default) so the suite fails fast if Textual ever
    # renames/removes ``constants.DRIVER`` rather than silently running
    # against the real platform driver.
    monkeypatch.setattr(constants, "DRIVER", _HEADLESS_DRIVER)
