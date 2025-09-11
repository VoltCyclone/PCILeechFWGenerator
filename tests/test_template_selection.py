#!/usr/bin/env python3
"""Tests for dynamic firmware template selection in `PCILeechGenerator`.

These tests monkeypatch the validator to capture which template name the generator
resolves so we can assert the selection logic without needing real templates.
"""

import tempfile
from pathlib import Path
from typing import Any, Dict

import pytest

from src.device_clone.pcileech_generator import (PCILeechGenerationConfig,
                                                 PCILeechGenerator)

# Common dummy context (validator is patched to just echo it back)
MIN_CONTEXT: Dict[str, Any] = {}


def _make_generator(tmp_path: Path, **cfg_overrides) -> PCILeechGenerator:
    cfg = PCILeechGenerationConfig(
        device_bdf="0000:00:00.0",  # Dummy BDF
        template_dir=tmp_path,
        output_dir=tmp_path / "out",
        strict_validation=True,
        fail_on_missing_data=False,
        enable_behavior_profiling=False,
        **cfg_overrides,
    )
    return PCILeechGenerator(cfg)


@pytest.fixture()
def capture_validator(monkeypatch):
    captured: Dict[str, Any] = {}

    def fake_validate(
        name: str, context: Dict[str, Any], strict: bool
    ):  # pragma: no cover
        captured["template_name"] = name
        captured["strict"] = strict
        return context

    monkeypatch.setattr(
        "src.templating.template_context_validator.validate_template_context",
        fake_validate,
    )
    return captured


def test_template_selection_explicit(monkeypatch, tmp_path, capture_validator):
    gen = _make_generator(tmp_path)
    # Inject explicit attribute not defined in dataclass (dynamic override)
    setattr(gen.config, "firmware_template", "explicit_fw")  # omit suffix intentionally
    gen._validate_template_context(MIN_CONTEXT)
    assert capture_validator["template_name"].endswith("explicit_fw.j2")


def test_template_selection_donor(monkeypatch, tmp_path, capture_validator):
    gen = _make_generator(tmp_path, donor_template={"template": "donor_fw_core"})
    gen._validate_template_context(MIN_CONTEXT)
    assert capture_validator["template_name"].endswith("donor_fw_core.j2")


def test_template_selection_autodetect_single(monkeypatch, tmp_path, capture_validator):
    # Create single auto-detect candidate
    (tmp_path / "pcileech_auto_firmware.j2").write_text("// dummy")
    gen = _make_generator(tmp_path)
    gen._validate_template_context(MIN_CONTEXT)
    assert capture_validator["template_name"].endswith("pcileech_auto_firmware.j2")


def test_template_selection_autodetect_multiple_prefers_default(
    monkeypatch, tmp_path, capture_validator
):
    # Create multiple candidates including canonical default
    (tmp_path / "pcileech_firmware.j2").write_text("// default")
    (tmp_path / "pcileech_custom_firmware.j2").write_text("// custom")
    gen = _make_generator(tmp_path)
    gen._validate_template_context(MIN_CONTEXT)
    # Should choose canonical default
    assert capture_validator["template_name"].endswith("pcileech_firmware.j2")


def test_template_selection_fallback_default(monkeypatch, tmp_path, capture_validator):
    # No candidates and no overrides => fallback constant
    gen = _make_generator(tmp_path)
    gen._validate_template_context(MIN_CONTEXT)
    assert capture_validator["template_name"].endswith("pcileech_firmware.j2")
