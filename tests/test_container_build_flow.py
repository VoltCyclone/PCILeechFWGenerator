#!/usr/bin/env python3
import os
from pathlib import Path

import pytest

from src.cli.container import BuildConfig, _build_podman_command
from src.exceptions import ConfigurationError


def test_bdf_validation_success():
    cfg = BuildConfig(bdf="0000:03:00.0", board="pcileech_35t325_x4")
    assert cfg.bdf == "0000:03:00.0"


def test_bdf_validation_failure():
    with pytest.raises(ConfigurationError):
        BuildConfig(bdf="invalid", board="pcileech_35t325_x4")


def test_cmd_args_basic():
    cfg = BuildConfig(bdf="0000:03:00.0", board="pcileech_35t325_x4")
    args = cfg.cmd_args()
    assert "--bdf" in args and "0000:03:00.0" in args
    assert "--board" in args and "pcileech_35t325_x4" in args


def test_cmd_args_optional():
    cfg = BuildConfig(
        bdf="0000:03:00.0",
        board="pcileech_35t325_x4",
        advanced_sv=True,
        enable_variance=True,
        behavior_profile_duration=45,
        output_template="out.json",
        donor_template="donor.json",
    )
    args = cfg.cmd_args()
    # Ordering: ensure flags present
    assert "--advanced-sv" in args
    assert "--enable-variance" in args
    assert args.count("--profile") == 1
    assert "out.json" in args and "donor.json" in args


def test_build_podman_command_construction(tmp_path: Path):
    cfg = BuildConfig(bdf="0000:03:00.0", board="pcileech_35t325_x4")
    group_dev = "/dev/vfio/12"
    output_dir = tmp_path
    cmd = _build_podman_command(cfg, group_dev, output_dir)
    # Basic invariants
    assert cmd[0:2] == ["podman", "run"]
    # Volume flag pattern: ['-v', 'host:container']
    vol_indices = [i for i, v in enumerate(cmd) if v == "-v"]
    mounted = False
    for i in vol_indices:
        if i + 1 < len(cmd) and str(output_dir) in cmd[i + 1]:
            mounted = True
            break
    assert mounted, "Output directory not mounted in podman command"
    assert f"{cfg.container_image}:{cfg.container_tag}" in cmd
    # Ensure python module invocation appears
    assert "-m" in cmd and "src.build" in cmd
