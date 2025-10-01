#!/usr/bin/env python3
"""Unit tests for vfio_diagnostics critical logic paths.

Covers boot detection command generation, kernel param parsing, module checks,
linux/platform gate, device binding checks, ACS support probe, and helpers.
"""

import argparse
import os
from pathlib import Path
from types import SimpleNamespace
from typing import List
from unittest.mock import MagicMock, Mock, patch

import pytest

import src.cli.vfio_diagnostics as vd
from src.cli.vfio_diagnostics import Boot, Check, Diagnostics, Report, Status


# -----------------------------
# Helper: selective Path mocks
# -----------------------------
class PathExistsMap:
    def __init__(self, true_paths: List[str]):
        self._set = {str(Path(p)) for p in true_paths}

    def __call__(self, path: Path):
        return str(path) in self._set


# -----------------------------
# Tests for helpers and commands
# -----------------------------
class TestCmdHelpers:
    def test_dedup(self):
        existing = '"quiet splash"'
        new = ("intel_iommu=on", "iommu=pt")
        out = vd._dedup(existing, new)
        assert out.startswith('"') and out.endswith('"')
        assert "quiet" in out and "splash" in out
        assert "intel_iommu=on" in out and "iommu=pt" in out

    @patch("src.cli.vfio_diagnostics._detect_boot", return_value=Boot.GRUBBY)
    def test_cmds_for_args_grubby(self, mock_detect):
        cmds = vd._cmds_for_args(("intel_iommu=on", "iommu=pt"))
        assert any("grubby" in c for c in cmds)
        assert cmds[-1] == "sudo reboot"

    @patch("src.cli.vfio_diagnostics._detect_boot", return_value=Boot.GRUB2_MODERN)
    def test_cmds_for_args_grub2_modern(self, mock_detect):
        cmds = vd._cmds_for_args(("intel_iommu=on",))
        assert any("/etc/default/grub" in c for c in cmds)
        assert any("grub2-mkconfig" in c for c in cmds)
        assert cmds[-1] == "sudo reboot"

    @patch("src.cli.vfio_diagnostics._detect_boot", return_value=Boot.GRUB2_LEGACY)
    def test_cmds_for_args_grub2_legacy(self, mock_detect):
        cmds = vd._cmds_for_args(("amd_iommu=on",))
        assert any("/etc/default/grub" in c for c in cmds)
        assert any("grub-mkconfig" in c for c in cmds)
        assert cmds[-1] == "sudo reboot"

    @patch("src.cli.vfio_diagnostics._detect_boot", return_value=Boot.SYSTEMD_BOOT)
    def test_cmds_for_args_systemd_boot_append(self, mock_detect):
        with patch(
            "pathlib.Path.exists",
            new=lambda self: str(self) in {str(Path("/etc/kernel/cmdline"))},
        ):
            with patch("pathlib.Path.read_text", return_value="quiet splash"):
                cmds = vd._cmds_for_args(("iommu=pt",))
                assert any("/etc/kernel/cmdline" in c for c in cmds)
                assert any("kernelstub" in c or "bootctl update" in c for c in cmds)

    @patch("src.cli.vfio_diagnostics._detect_boot", return_value=Boot.UNKNOWN)
    def test_cmds_for_args_unknown(self, mock_detect):
        cmds = vd._cmds_for_args(("iommu=pt",))
        assert cmds[0].startswith("# Unknown boot loader")
        assert "iommu=pt" in "\n".join(cmds)

    def test_kernel_param_command_helpers(self):
        # smoke tests, just ensure non-empty sensible commands
        with patch("src.cli.vfio_diagnostics._detect_boot", return_value=Boot.GRUBBY):
            cmds = vd._kernel_param_commands()
            assert any("grubby" in c for c in cmds)
            cmds_acs = vd._kernel_param_commands_with_acs()
            assert any("pcie_acs_override" in c for c in cmds_acs)


# -----------------------------
# Diagnostics checks
# -----------------------------
class TestDiagnosticsChecks:
    @patch("platform.system", return_value="Linux")
    def test_check_linux_ok(self, _mock_sys):
        d = Diagnostics()
        d._check_linux()
        assert any(c.name == "Platform" and c.status == Status.OK for c in d.checks)

    @patch("platform.system", return_value="Darwin")
    def test_check_linux_error(self, _mock_sys):
        d = Diagnostics()
        d._check_linux()
        ck = next(c for c in d.checks if c.name == "Platform")
        assert ck.status == Status.ERROR
        assert "Unsupported OS" in ck.message

    def test_check_modules_all_missing(self):
        d = Diagnostics()
        # None of the required modules present
        with patch("pathlib.Path.exists", return_value=False):
            d._check_modules()
        ck = next(c for c in d.checks if c.name == "Kernel modules")
        assert ck.status in (Status.ERROR, Status.WARNING)
        assert "Missing" in ck.message
        assert ck.commands and any("modprobe" in c for c in ck.commands)

    def test_check_modules_all_loaded(self):
        d = Diagnostics()
        true_paths = [
            "/sys/module/vfio",
            "/sys/module/vfio_pci",
            "/sys/module/vfio_iommu_type1",
        ]
        with patch(
            "pathlib.Path.exists",
            new=lambda self, _set={p for p in map(str, map(Path, true_paths))}: str(
                self
            )
            in _set,
        ):
            d._check_modules()
        ck = next(c for c in d.checks if c.name == "Kernel modules")
        assert ck.status == Status.OK
        assert "VFIO modules loaded" in ck.message or "All VFIO modules" in ck.message

    def test_check_kernel_params_ok_and_acs_supported(self):
        d = Diagnostics("0000:01:00.0")
        with patch(
            "pathlib.Path.exists",
            new=lambda self: str(self) in {str(Path("/proc/cmdline"))},
        ):
            with patch(
                "pathlib.Path.read_text",
                return_value="quiet amd_iommu=on iommu=pt pcie_acs_override=downstream,multifunction",
            ):
                with patch.object(
                    Diagnostics, "_test_acs_override_support", return_value=True
                ):
                    d._check_kernel_params()
        ck = next(c for c in d.checks if c.name == "Kernel cmdline")
        assert ck.status == Status.OK
        assert "IOMMU enabled" in ck.message
        assert "ACS override" in ck.message

    def test_check_kernel_params_missing_file(self):
        d = Diagnostics()
        with patch("pathlib.Path.exists", return_value=False):
            d._check_kernel_params()
        ck = next(c for c in d.checks if c.name == "Kernel cmdline")
        assert ck.status == Status.ERROR
        assert "/proc/cmdline not found" in ck.message

    def test_check_vfio_driver_path_present(self):
        d = Diagnostics()
        with patch(
            "pathlib.Path.exists",
            new=lambda self: str(self) in {str(Path("/sys/bus/pci/drivers/vfio-pci"))},
        ):
            d._check_vfio_driver_path()
        ck = next(c for c in d.checks if c.name == "vfio-pci driver")
        assert ck.status == Status.OK

    def test_check_vfio_driver_path_missing(self):
        d = Diagnostics()
        with patch("pathlib.Path.exists", return_value=False):
            d._check_vfio_driver_path()
        ck = next(c for c in d.checks if c.name == "vfio-pci driver")
        assert ck.status == Status.ERROR
        assert any("modprobe" in c for c in (ck.commands or []))

    def test_device_driver_binding_cases(self):
        bdf = "0000:01:00.0"
        d = Diagnostics(bdf)
        drv_link = f"/sys/bus/pci/devices/{bdf}/driver"

        # Bound to vfio-pci
        exists_map = PathExistsMap([drv_link])
        with patch("pathlib.Path.exists", new=lambda self, m=exists_map: m(self)):
            with patch("os.readlink", return_value="/sys/bus/pci/drivers/vfio-pci"):
                d._device_driver_binding()
        ck = d.checks[-1]
        assert ck.name == "Driver" and ck.status == Status.OK

        # Bound to other driver
        d.checks.clear()
        with patch("pathlib.Path.exists", new=lambda self, m=exists_map: m(self)):
            with patch("os.readlink", return_value="/sys/bus/pci/drivers/nvidia"):
                d._device_driver_binding()
        ck = d.checks[-1]
        assert ck.status == Status.WARNING
        assert ck.commands and any("vfio-pci" in c for c in ck.commands)

        # No driver link
        d.checks.clear()
        with patch("pathlib.Path.exists", return_value=False):
            d._device_driver_binding()
        ck = d.checks[-1]
        assert ck.status == Status.WARNING and "No driver bound" in ck.message

    def test_device_iommu_group_resolves_ok(self):
        bdf = "0000:01:00.0"
        d = Diagnostics(bdf)
        group_link = f"/sys/bus/pci/devices/{bdf}/iommu_group"
        group_dir = "/sys/kernel/iommu_groups/42"
        devices_dir = f"{group_dir}/devices"

        def path_exists(p: Path):
            s = str(p)
            return s in {group_link, group_dir, devices_dir}

        with patch("pathlib.Path.exists", new=path_exists):
            with patch("os.readlink", return_value=f"{group_dir}"):
                with patch("pathlib.Path.iterdir", return_value=[Path("0000:01:00.0")]):
                    d._device_iommu_group()
        ck = next(c for c in d.checks if c.name == "IOMMU group")
        assert ck.status == Status.OK and "Group" in ck.message


# -----------------------------
# ACS support probe
# -----------------------------


class TestACSSupportProbe:
    def test_acs_supported_via_sysfs(self):
        d = Diagnostics()
        with patch(
            "pathlib.Path.exists",
            new=lambda self: str(self)
            in {str(Path("/sys/module/pci/parameters/pcie_acs_override"))},
        ):
            assert d._test_acs_override_support() is True

    def test_acs_supported_via_config(self):
        d = Diagnostics()
        config_path = f"/boot/config-{os.uname().release}"
        with patch(
            "pathlib.Path.exists",
            new=lambda self, p=config_path: str(self) == p,
        ):
            with patch("pathlib.Path.read_text", return_value="CONFIG_PCI_QUIRKS=y\n"):
                assert d._test_acs_override_support() is True

    def test_acs_supported_via_modinfo(self):
        d = Diagnostics()
        completed = SimpleNamespace(returncode=0, stdout="pcie_acs_override: bool")
        with patch("pathlib.Path.exists", return_value=False):
            with patch("subprocess.run", return_value=completed):
                assert d._test_acs_override_support() is True

    def test_acs_not_supported(self):
        d = Diagnostics()
        with patch("pathlib.Path.exists", return_value=False):
            with patch("subprocess.run", side_effect=FileNotFoundError()):
                assert d._test_acs_override_support() is False


# -----------------------------
# Report and CLI
# -----------------------------


class TestReportAndCLI:
    def test_remediation_script_includes_commands_from_errors(self):
        report = Report(
            overall=Status.ERROR,
            checks=[
                Check(
                    name="Kernel modules",
                    status=Status.ERROR,
                    message="Missing modules",
                    commands=["sudo modprobe vfio", "sudo modprobe vfio-pci"],
                ),
                Check(
                    name="Platform",
                    status=Status.OK,
                    message="Linux detected",
                ),
                Check(
                    name="Driver",
                    status=Status.WARNING,
                    message="Bound to nvidia",
                    commands=[
                        "echo '0000:01:00.0' | sudo tee /sys/bus/pci/drivers/vfio-pci/bind"
                    ],
                ),
            ],
            device_bdf="0000:01:00.0",
            can_proceed=False,
        )
        script = vd.remediation_script(report)
        assert "modprobe vfio" in script and "vfio-pci/bind" in script
        assert script.startswith("#!/bin/bash")

    def test_parse_args_variants(self):
        ns = vd.parse_args(["diagnose", "-d", "0000:01:00.0"])  # type: ignore[arg-type]
        assert isinstance(ns, argparse.Namespace)
        assert ns.cmd == "diagnose" and ns.device_bdf == "0000:01:00.0"

        ns = vd.parse_args(["json"])  # type: ignore[arg-type]
        assert ns.cmd == "json"

        ns = vd.parse_args(["script", "--quiet"])  # type: ignore[arg-type]
        assert ns.cmd == "script" and ns.quiet is True

    @patch("src.cli.vfio_diagnostics.parse_args")
    @patch("src.cli.vfio_diagnostics.Diagnostics")
    def test_main_json_path(self, mock_diag_cls, mock_parse, capsys):
        fake_report = Report(
            overall=Status.OK, checks=[], device_bdf=None, can_proceed=True
        )
        mock_diag = Mock()
        mock_diag.run.return_value = fake_report
        mock_diag_cls.return_value = mock_diag

        mock_parse.return_value = SimpleNamespace(
            cmd="json", device_bdf=None, quiet=False
        )
        vd.main([])
        captured = capsys.readouterr().out
        assert "{\n" in captured and "overall" in captured
