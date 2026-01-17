import pytest

from pcileechfwgenerator.device_clone.fallback_manager import (FallbackManager,
                                               get_global_fallback_manager,
                                               set_global_fallback_manager)


@pytest.fixture(autouse=True)
def clear_fallback():
    # Ensure global manager is cleared before and after each test
    set_global_fallback_manager(None)
    yield
    set_global_fallback_manager(None)


def test_dynamic_over_static_handler():
    gm = get_global_fallback_manager(mode="auto")
    # register static fallback
    gm.register_fallback("foo.bar", "static")

    # register dynamic handler which should take precedence
    gm.register_handler("foo.bar", lambda: "dynamic")

    assert gm.get_fallback("foo.bar") == "dynamic"


def test_apply_fallbacks_prefers_dynamic():
    gm = get_global_fallback_manager(mode="auto")
    gm.register_fallback("board.name", "")
    gm.register_handler("board.name", lambda: "DYNAMIC")

    ctx = gm.apply_fallbacks({})
    # dot-path should create nested dict
    assert ctx.get("board", {}).get("name") == "DYNAMIC"


def test_sensitive_defaults_not_exposable():
    gm = get_global_fallback_manager(mode="auto")
    # register sensitive-looking fallback
    gm.register_fallback("credentials.token", "not-secret")

    exposable = gm.get_exposable_fallbacks()
    assert "credentials.token" not in exposable
    # Also ensure is_sensitive_var flags it
    assert gm.is_sensitive_var("credentials.token")
