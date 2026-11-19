"""The generated BAR controller must reach Vivado intact.

copy_pcileech_sources runs after the generator writes src/. Every board ships a
pcileech_tlps128_bar_controller.sv; copying it over the generated one silently
discarded learned BAR models, the served BAR index and the read-only fallback.
"""

import logging
import re
import shutil
import subprocess
from pathlib import Path

import pytest
from pcileechfwgenerator.device_clone.bar_model_loader import serialize_bar_model
from pcileechfwgenerator.file_management.file_manager import FileManager
from pcileechfwgenerator.templating.sv_overlay_generator import SVOverlayGenerator
from pcileechfwgenerator.templating.template_renderer import TemplateRenderer

from tests.test_bar_storage_rtl import model_for

BOARD = Path(__file__).parent.parent / "lib" / "voltcyclone-fpga" / "PCIeSquirrel"
CONTROLLER = "pcileech_tlps128_bar_controller.sv"
pytestmark = pytest.mark.skipif(
    not (BOARD / "src" / CONTROLLER).exists(), reason="board submodule not present"
)


def _msix_plan(context, vectors=16, table=0x2000, pba=0x3000):
    from pcileechfwgenerator.device_clone.msix_subsystem import plan_msix_subsystem

    msix = dict(
        is_valid=True,
        table_size=vectors,
        table_bir=context["bar_config"]["served_bar_index"],
        table_offset=table,
        pba_bir=context["bar_config"]["served_bar_index"],
        pba_offset=pba,
    )
    plan = plan_msix_subsystem(
        {"template_context": context, "msix_data": msix}, board_error=None
    )
    assert plan.generated, plan.reason
    return plan.to_context()


def _generated(models=None, fallback=None, served=0, msix=False):
    bar_config = {
        "served_bar_index": served,
        "aperture_size": 0x4000 if msix else 4096,
        "bars": [
            {"index": served, "size": 0x4000 if msix else 4096, "is_memory": True}
        ],
        "bar_models": models or {},
    }
    if fallback:
        bar_config["bar_fallback"] = fallback
    context = {
        "header": "// gen",
        "device_signature": "fixture",
        "bar_config": bar_config,
    }
    if msix:
        context["msix_subsystem"] = _msix_plan(context)
    generator = SVOverlayGenerator(TemplateRenderer(), logging.getLogger("t"))
    files = {CONTROLLER: generator._generate_bar_controller(context)}
    if msix:
        files.update(generator._msix_sources())
    device = generator._generate_bar_implementation(context)
    if device is not None:
        files["pcileech_bar_impl_device.sv"] = device
    return files


def _copy(tmp_path, monkeypatch, files):
    from pcileechfwgenerator.file_management import repo_manager

    src = tmp_path / "out" / "src"
    src.mkdir(parents=True)
    for name, text in files.items():
        (src / name).write_text(text)
    monkeypatch.setattr(repo_manager.RepoManager, "ensure_repo", lambda: BOARD.parent)
    monkeypatch.setattr(
        repo_manager.RepoManager, "get_board_path", lambda *a, **k: BOARD / "src"
    )
    monkeypatch.setattr(repo_manager.RepoManager, "get_xdc_files", lambda *a, **k: [])
    FileManager(tmp_path / "out").copy_pcileech_sources(
        "pcileech_squirrel", generated=files
    )
    return src


def _ip_stubs(tmp_path, sources):
    """Stand-ins for Vivado-generated IP cores (fifo_*, bram_*), which exist only
    inside a Vivado project. Ports are taken from the instantiations; only
    the controller/engine wiring around them is under test."""
    text = "\n".join(Path(s).read_text() for s in sources)
    stubs = []
    for name, body in re.findall(
        r"^\s*((?:fifo|bram)_\w+)\s+\w+\s*\((.*?)\);", text, re.M | re.S
    ):
        ports = sorted(set(re.findall(r"\.(\w+)\s*\(", body)))
        stubs.append(
            f"module {name}("
            + ", ".join(f"input wire [255:0] {port}" for port in ports)
            + ");\nendmodule\n"
        )
    path = tmp_path / "ip_stubs.sv"
    path.write_text("\n".join(dict.fromkeys(stubs)))
    return path


def test_board_copy_keeps_generated_controller(tmp_path, monkeypatch):
    files = _generated({2: serialize_bar_model(model_for("rw"))}, served=2)
    src = _copy(tmp_path, monkeypatch, files)
    final = (src / CONTROLLER).read_text()
    assert final == files[CONTROLLER]
    assert re.search(r"pcileech_bar_impl_device\s*#.*?rd_req_bar\[2\]", final, re.S)
    companion = (src / "pcileech_tlps128_bar_controller_board_modules.sv").read_text()
    modules = re.findall(r"^module\s+(\w+)", companion, re.M)
    assert sorted(modules) == sorted(
        [
            "pcileech_tlps128_bar_wrengine",
            "pcileech_tlps128_bar_rdengine",
            "pcileech_bar_impl_none",
            "pcileech_bar_impl_loopaddr",
            "pcileech_bar_impl_zerowrite4k",
        ]
    )
    assert '`include "pcileech_header.svh"' in companion
    # Other board sources are still copied normally.
    assert (src / "pcileech_fifo.sv").exists()


def test_without_generated_names_board_copy_is_unchanged(tmp_path, monkeypatch):
    src = _copy(tmp_path, monkeypatch, {})
    assert (src / CONTROLLER).read_bytes() == (BOARD / "src" / CONTROLLER).read_bytes()
    assert not (src / "pcileech_tlps128_bar_controller_board_modules.sv").exists()


@pytest.mark.parametrize(
    "case",
    ["learned", "readonly_default", "writable_opt_out", "msix", "msix_writable"],
)
def test_generated_controller_elaborates_with_board_modules(
    tmp_path, monkeypatch, case
):
    """The generated controller, the board's engines and impls, and the device
    BAR module form one consistent design (ports, interfaces, module names)."""
    tool = shutil.which("verilator")
    if tool is None:
        pytest.skip("verilator is not installed")
    files = {
        "learned": lambda: _generated({0: serialize_bar_model(model_for("rw"))}),
        "readonly_default": lambda: _generated(),
        "writable_opt_out": lambda: _generated(fallback="generic_ram"),
        "msix": lambda: _generated(msix=True),
        "msix_writable": lambda: _generated(fallback="generic_ram", msix=True),
    }[case]()
    src = _copy(tmp_path, monkeypatch, files)
    sources = [
        src / CONTROLLER,
        src / "pcileech_tlps128_bar_controller_board_modules.sv",
    ]
    if (src / "pcileech_bar_impl_device.sv").exists():
        sources.append(src / "pcileech_bar_impl_device.sv")
    msix_sources = [
        src / name
        for name in (
            "pcileech_msix_bar_router.sv",
            "pcileech_msix_tlp_tx.sv",
            "pcie_msix.v",
        )
    ]
    assert all(p.exists() for p in msix_sources) == case.startswith("msix")
    sources.extend(p for p in msix_sources if p.exists())
    sources.append(_ip_stubs(tmp_path, sources))
    writable = case in ("writable_opt_out", "msix_writable")
    assert writable != (src / "pcileech_bar_impl_device.sv").exists()
    result = subprocess.run(
        [
            tool,
            "--lint-only",
            "-Wno-fatal",
            "-Wno-lint",
            "-Wno-style",
            "-I" + str(src),
            "--top-module",
            "pcileech_tlps128_bar_controller",
            *map(str, sources),
        ],
        capture_output=True,
        text=True,
        timeout=120,
    )
    assert result.returncode == 0, result.stdout + result.stderr


def test_build_passes_generated_names_to_board_copy(tmp_path, monkeypatch):
    """FirmwareBuilder: modules written first are named to the later copy, and a
    stale device BAR file from an earlier run is removed."""
    from types import SimpleNamespace

    from pcileechfwgenerator import build
    from pcileechfwgenerator.file_management import file_manager

    seen = {}

    def record(self, board, *, generated=()):
        seen["generated"] = set(generated)
        return {}

    monkeypatch.setattr(file_manager.FileManager, "copy_pcileech_sources", record)
    builder = build.FirmwareBuilder.__new__(build.FirmwareBuilder)
    builder.config = SimpleNamespace(output_dir=tmp_path, board="pcileech_squirrel")
    builder.logger = logging.getLogger("build")
    builder.file_manager = build.FileOperationsManager(tmp_path)
    stale = tmp_path / "src" / "pcileech_bar_impl_device.sv"
    stale.parent.mkdir(parents=True)
    stale.write_text("// stale")
    builder._write_modules(
        {"systemverilog_modules": {CONTROLLER: "// generated controller"}}
    )
    assert not stale.exists()
    from pcileechfwgenerator.exceptions import DeviceConfigError

    # The later FIFO patch step needs donor IDs; the copy already happened.
    with pytest.raises(DeviceConfigError):
        builder._generate_tcl_scripts({})
    assert CONTROLLER in seen["generated"]
