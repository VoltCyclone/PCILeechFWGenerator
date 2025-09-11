#!/usr/bin/env python3
"""Tests enforcing that TCL generation has NO inline fallback.

If required TCL templates are missing, generation must raise a
`PCILeechGenerationError` (or a subclass) and MUST NOT silently
produce placeholder / legacy inline script content.
"""
import re
from pathlib import Path

import pytest

from src.device_clone.pcileech_generator import (PCILeechGenerationConfig,
                                                 PCILeechGenerationError,
                                                 PCILeechGenerator)


def _mk_cfg(tmp: Path, **over):
    return PCILeechGenerationConfig(
        device_bdf="0000:00:00.0",
        template_dir=tmp,  # intentionally empty for negative test
        output_dir=tmp / "out",
        strict_validation=True,
        fail_on_missing_data=False,
        enable_behavior_profiling=False,
        **over,
    )


def test_missing_tcl_templates_raises(tmp_path: Path):
    """All TCL script templates absent -> must raise and not fallback."""
    cfg = _mk_cfg(tmp_path)
    gen = PCILeechGenerator(cfg)

    # Minimal context to reach TCL stage. We call the private method directly
    # to isolate behavior (full flow requires many other templates/contexts).
    with pytest.raises(
        PCILeechGenerationError,
        match=r"build.*template|synthesis.*template|missing|not found",
    ):
        gen._generate_default_tcl_scripts({})  # type: ignore[arg-type]


@pytest.mark.parametrize("template_name", ["pcileech_build.j2", "build.j2"])
def test_present_build_and_synthesis_templates_succeed(
    tmp_path: Path, template_name: str
):
    """When build + synthesis templates exist generation returns both scripts."""
    tcl_dir = tmp_path / "tcl"
    tcl_dir.mkdir(parents=True)

    # Provide one of the accepted build template names and synthesis template.
    (tcl_dir / template_name).write_text(
        "# build template: {{ header|default('hdr') }}\nputs {BUILD}\n"
    )
    (tcl_dir / "synthesis.j2").write_text("# synth template\nputs {SYNTH}\n")

    cfg = _mk_cfg(tmp_path)
    gen = PCILeechGenerator(cfg)

    scripts = gen._generate_default_tcl_scripts(
        {
            "header": "H",
            "vendor_id": "0x1234",
            "device_id": "0x5678",
            "revision_id": "0x01",
            "class_code": "0x020000",
            "board_name": "test_board",
        }
    )  # type: ignore[arg-type]

    assert "build.tcl" in scripts
    assert "synthesis.tcl" in scripts
    assert "puts {BUILD}" in scripts["build.tcl"]
    assert "puts {SYNTH}" in scripts["synthesis.tcl"]
    # Basic security keys presence: device_signature rendered in header_comment
    assert "1234" in scripts["build.tcl"]  # vendor id
    assert "5678" in scripts["build.tcl"]  # device id
    # Ensure no legacy fallback markers accidentally appear
    # Heuristic legacy pattern should not appear (ensures no inline fallback)
    assert not re.search(r"create_project .*auto", scripts["build.tcl"])


def test_missing_synthesis_template_raises(tmp_path: Path):
    """Build template present but synthesis template missing -> raise."""
    tcl_dir = tmp_path / "tcl"
    tcl_dir.mkdir(parents=True)
    (tcl_dir / "pcileech_build.j2").write_text("# build only\n")

    cfg = _mk_cfg(tmp_path)
    gen = PCILeechGenerator(cfg)

    with pytest.raises(PCILeechGenerationError, match=r"synthesis.*template.*missing"):
        gen._generate_default_tcl_scripts(
            {"vendor_id": "0x1234", "device_id": "0x5678"}
        )  # type: ignore[arg-type]
