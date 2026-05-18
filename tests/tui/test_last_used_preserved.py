"""T13: `last_used` must not be clobbered on every read.

The set_timestamps validator used to unconditionally rewrite last_used on
every BuildConfiguration(...) call. validate_config + load_profile each
instantiate the model purely as a schema check, so reading a profile
mutated the on-disk timestamp.
"""

import json
import time

from pcileechfwgenerator.tui.models.configuration import BuildConfiguration


def test_existing_last_used_is_preserved():
    cfg = BuildConfiguration(last_used="2025-01-01T00:00:00", name="x")
    assert cfg.last_used == "2025-01-01T00:00:00"


def test_missing_last_used_is_populated_once():
    cfg = BuildConfiguration(name="x")
    ts = cfg.last_used
    assert ts is not None and ts != ""
    # Round-trip preserves the original timestamp.
    cfg2 = BuildConfiguration(**cfg.dict())
    assert cfg2.last_used == ts


def test_mark_used_updates_timestamp():
    cfg = BuildConfiguration(last_used="2025-01-01T00:00:00", name="x")
    cfg.mark_used()
    assert cfg.last_used != "2025-01-01T00:00:00"


def test_export_profile_does_not_touch_source_mtime(tmp_path, monkeypatch):
    """export_profile must be read-only on the source file."""
    from pcileechfwgenerator.tui.core import config_manager as cm_mod
    from pcileechfwgenerator.tui.core.config_manager import ConfigManager

    monkeypatch.setattr(cm_mod, "CACHE_DIR", tmp_path)
    mgr = ConfigManager()
    mgr.config_dir = tmp_path / "profiles"
    mgr.config_dir.mkdir(parents=True, exist_ok=True)

    cfg = BuildConfiguration(name="probe", last_used="2025-01-01T00:00:00")
    source = mgr.config_dir / "probe.json"
    source.write_text(json.dumps(cfg.dict()))
    source_mtime_before = source.stat().st_mtime
    source_text_before = source.read_text()

    target = tmp_path / "exported.json"
    time.sleep(0.05)
    ok = mgr.export_profile("probe", target)

    assert ok is True
    assert target.exists()
    assert source.stat().st_mtime == source_mtime_before
    assert source.read_text() == source_text_before


def test_validate_config_does_not_touch_file_mtime(tmp_path, monkeypatch):
    """validate_config must be read-only — no save-back side effect."""
    from pcileechfwgenerator.tui.core import config_manager as cm_mod
    from pcileechfwgenerator.tui.core.config_manager import ConfigManager

    monkeypatch.setattr(cm_mod, "CACHE_DIR", tmp_path)
    mgr = ConfigManager()
    mgr.config_dir = tmp_path / "profiles"
    mgr.config_dir.mkdir(parents=True, exist_ok=True)

    cfg = BuildConfiguration(name="probe", last_used="2025-01-01T00:00:00")
    profile_path = mgr.config_dir / "probe.json"
    profile_path.write_text(json.dumps(cfg.dict()))
    mtime_before = profile_path.stat().st_mtime

    time.sleep(0.05)
    issues = mgr.validate_config(cfg)
    mtime_after = profile_path.stat().st_mtime

    assert issues == [] or all(isinstance(s, str) for s in issues)
    assert mtime_after == mtime_before
