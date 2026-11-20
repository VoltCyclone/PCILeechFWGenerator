"""The register BAR and a separate MSI-X BAR retain independent ownership."""

import logging
import shutil
import subprocess
from copy import deepcopy

import pytest
from pcileechfwgenerator.device_clone.msix_subsystem import plan_msix_subsystem
from pcileechfwgenerator.device_clone.served_bar import (
    ServedBar,
    select_served_bars,
    served_bars_from_config,
)
from pcileechfwgenerator.templating.sv_overlay_generator import SVOverlayGenerator
from pcileechfwgenerator.templating.template_renderer import TemplateRenderer
from pcileechfwgenerator.vivado_handling.pcie_ip_donor_override import (
    _format_bar_aperture_lines,
    donor_pcie_ip_config_from_result,
)

from tests.test_cfgspace_bar_index import _bar_dwords, _render_cfgspace_coe
from tests.test_coe_error_injection import _minimal_base_context
from tests.test_msix_subsystem_generation import _msix, _result


def _bars():
    return [
        dict(index=0, size=0x20000, is_memory=True, is_64bit=True, prefetchable=False),
        dict(index=3, size=0x4000, is_memory=True, is_64bit=False, prefetchable=False),
    ]


def _context():
    bars = _bars()
    context = deepcopy(_minimal_base_context())
    context.update(header="// test", device_signature="captured-layout")
    context["bar_config"] = dict(
        primary_bar=0,
        served_bar_index=0,
        served_is_64bit=True,
        aperture_size=bars[0]["size"],
        bars=bars,
        served_bars=[b.model_dump() for b in select_served_bars(bars[0], bars, 3, 3)],
    )
    return context


def _decide(context):
    result = _result(context, _msix(served=3, vectors=10, table=0, pba=0x2000))
    plan = plan_msix_subsystem(result, board_error=None)
    context["msix_subsystem"] = plan.to_context()
    return result, plan


def test_select_register_and_msix_bar_without_conflating_apertures():
    bars = _bars()
    served = select_served_bars(bars[0], bars, 3, 3)
    assert [(b.index, b.size, b.role) for b in served] == [
        (0, 0x20000, "registers"),
        (3, 0x4000, "msix"),
    ]
    same = select_served_bars(bars[0], bars, 0, 0)
    assert len(same) == 1 and same[0].role == "registers+msix"


@pytest.mark.parametrize(
    "change",
    [
        dict(index=1),
        dict(index=5, is_64bit=True),
        dict(size=6000),
    ],
)
def test_invalid_or_overlapping_served_set_is_rejected(change):
    context = _context()["bar_config"]
    context["served_bars"][1].update(change)
    with pytest.raises(ValueError):
        served_bars_from_config(context)


def test_split_table_pba_is_explicitly_refused():
    bars = _bars()
    with pytest.raises(ValueError, match="same BAR"):
        select_served_bars(bars[0], bars, 0, 3)


def test_context_builder_selects_separate_msix_bar_and_reports_its_unknown_fidelity(
    monkeypatch,
):
    from pcileechfwgenerator.device_clone.pcileech_context import PCILeechContextBuilder

    from tests.test_pcie_ip_donor_override_bar import _mem_bar

    builder = object.__new__(PCILeechContextBuilder)
    builder.logger = logging.getLogger("multibar")
    builder.device_bdf = "0000:01:00.1"
    bars = [_mem_bar(0, 0x20000, is_64bit=True), _mem_bar(3, 0x4000)]
    monkeypatch.setattr(builder, "_analyze_bars", lambda records: bars)
    monkeypatch.setattr(builder, "_capture_or_load_bar_models", lambda **kwargs: {})
    config = builder._build_bar_config(
        {"bars": bars},
        None,
        msix_data=_msix(served=3, vectors=10, table=0, pba=0x2000),
    )
    assert [b["index"] for b in config["served_bars"]] == [0, 3]
    assert config["served_bar_fidelities"] == {
        "0": "readonly_zero",
        "3": "readonly_zero",
    }


def test_two_64bit_bars_suppress_both_upper_partner_slots():
    from tests.test_bar_controller_index_serving import _impl_for_slot

    context = _context()
    config = context["bar_config"]
    config["bars"][1]["is_64bit"] = True
    config["served_bars"][1]["is_64bit"] = True
    _decide(context)
    generator = SVOverlayGenerator(TemplateRenderer(), logging.getLogger("multibar"))
    controller = generator._generate_bar_controller(context)
    for index in (1, 4):
        assert _impl_for_slot(controller, index) == "pcileech_bar_impl_none"
    words = _bar_dwords(_render_cfgspace_coe(context))
    assert words[1] == words[4] == "FFFFFFFF"
    assert int(words[3], 16) == 0xFFFFC004


def test_third_served_bar_is_refused():
    config = _context()["bar_config"]
    config["served_bars"].append(
        ServedBar(index=5, size=4096, is_memory=True, role="registers").model_dump()
    )
    with pytest.raises(ValueError, match="only the register BAR"):
        served_bars_from_config(config)


def test_msix_plan_and_ip_enable_bar3_using_its_own_aperture():
    context = _context()
    result, plan = _decide(context)
    assert plan.generated, plan.reason
    assert plan.layout.served_bar.index == 3
    assert plan.layout.served_bar.size == 0x4000
    ip = donor_pcie_ip_config_from_result(result)
    assert ip.msix_enabled
    lines = _format_bar_aperture_lines(ip)
    assert "CONFIG.Bar0_Enabled true" in lines
    assert "CONFIG.Bar3_Enabled true" in lines
    assert "CONFIG.Bar0_Size 128" in lines
    assert "CONFIG.Bar3_Size 16" in lines
    for k in (1, 2, 4, 5):
        assert f"CONFIG.Bar{k}_Enabled false" in lines


def test_cfgspace_preserves_both_sizes_and_64bit_partner():
    words = _bar_dwords(_render_cfgspace_coe(_context()))
    assert [int(word, 16) for word in words] == [
        0xFFFE0004,
        0xFFFFFFFF,
        0,
        0xFFFFC000,
        0,
        0,
    ]


def test_overlay_gives_each_bar_its_own_module_and_msix_owner():
    context = _context()
    _decide(context)
    generator = SVOverlayGenerator(TemplateRenderer(), logging.getLogger("multibar"))
    controller = generator._generate_bar_controller(context)
    assert "pcileech_bar_impl_device_bar0" in controller
    assert "pcileech_bar_impl_device_bar3" in controller
    assert "wr_valid && wr_bar[3]" in controller
    assert "rd_req_valid && rd_req_bar[3]" in controller
    assert "131072" in controller and "16384" in controller
    files = generator._generate_bar_implementations(context)
    assert set(files) == {
        "pcileech_bar_impl_device_bar0.sv",
        "pcileech_bar_impl_device_bar3.sv",
    }
    for index in (0, 3):
        assert (
            f"module pcileech_bar_impl_device_bar{index}"
            in files[f"pcileech_bar_impl_device_bar{index}.sv"]
        )
        assert "READ-ONLY FALLBACK" in files[f"pcileech_bar_impl_device_bar{index}.sv"]


def test_secondary_bar_uses_its_own_learned_model_and_primary_stays_readonly():
    context = _context()
    context["bar_config"]["bar_models"] = {
        "3": dict(size=0x4000, regs={"0x100": dict(width=4, reset=0x8172, rw_mask=0)})
    }
    generator = SVOverlayGenerator(TemplateRenderer(), logging.getLogger("multibar"))
    files = generator._generate_bar_implementations(context)
    assert "READ-ONLY FALLBACK" in files["pcileech_bar_impl_device_bar0.sv"]
    assert "READ-ONLY FALLBACK" not in files["pcileech_bar_impl_device_bar3.sv"]
    assert "8172" in files["pcileech_bar_impl_device_bar3.sv"]
    assert "8172" not in files["pcileech_bar_impl_device_bar0.sv"]


def test_relocated_context_retains_bars_but_rederives_each_fidelity():
    from types import SimpleNamespace

    from pcileechfwgenerator.build import _rederive_bar_models

    context = _context()
    context["bar_config"]["bar_models"] = {"3": {"untrusted": True}}
    context["bar_config"]["served_bar_fidelities"] = {"3": "learned"}
    rebuilt = _rederive_bar_models(context, SimpleNamespace(learning=None))
    config = rebuilt["bar_config"]
    assert config["served_bars"] == context["bar_config"]["served_bars"]
    assert "bar_models" not in config
    assert config["served_bar_fidelities"] == {
        "0": "readonly_zero",
        "3": "readonly_zero",
    }


def test_descriptor_cannot_change_the_captured_bar_size():
    context = _context()
    context["bar_config"]["served_bars"][1]["size"] *= 2
    with pytest.raises(ValueError, match="captured aperture"):
        served_bars_from_config(context["bar_config"])


def test_primary_list_position_cannot_select_a_different_aperture():
    config = _context()["bar_config"]
    config["primary_bar"] = 1
    with pytest.raises(ValueError, match="primary_bar"):
        served_bars_from_config(config)


def test_build_removes_only_stale_generated_per_bar_modules(tmp_path):
    from types import SimpleNamespace

    from pcileechfwgenerator import build

    builder = build.FirmwareBuilder.__new__(build.FirmwareBuilder)
    builder.config = SimpleNamespace(output_dir=tmp_path)
    builder.logger = logging.getLogger("multibar")
    builder.file_manager = build.FileOperationsManager(tmp_path)
    src = tmp_path / "src"
    src.mkdir()
    stale = src / "pcileech_bar_impl_device_bar3.sv"
    stale.write_text("// old generated BAR3")
    unrelated = src / "pcileech_bar_impl_device_bar7.sv"
    unrelated.write_text("// outside generator-owned name set")
    builder._write_modules({"systemverilog_modules": {}})
    assert not stale.exists()
    assert unrelated.exists()


def test_full_generation_emits_both_models_and_consistent_msix_ip():
    from pcileechfwgenerator.templating.systemverilog_generator import (
        SystemVerilogGenerator,
    )

    from tests.test_msix_subsystem_generation import _pipeline_context

    context = _pipeline_context()
    context["bar_config"] = _context()["bar_config"]
    result, plan = _decide(context)
    modules = SystemVerilogGenerator().generate_systemverilog_modules(
        template_context=context
    )
    assert plan.generated
    assert "pcileech_bar_impl_device_bar0.sv" in modules
    assert "pcileech_bar_impl_device_bar3.sv" in modules
    result["systemverilog_modules"] = modules
    assert donor_pcie_ip_config_from_result(result).msix_table_bir == 3


def test_single_bar_descriptor_preserves_legacy_rendered_output():
    from tests.test_msix_subsystem_generation import _context as single_context
    from tests.test_msix_subsystem_generation import _decided

    original = single_context(served=2)
    _decided(original, _msix(served=2))
    explicit = deepcopy(original)
    explicit["bar_config"]["served_bars"] = [
        ServedBar(
            index=2, size=0x4000, is_memory=True, role="registers+msix"
        ).model_dump()
    ]
    generator = SVOverlayGenerator(TemplateRenderer(), logging.getLogger("multibar"))
    assert generator._generate_bar_controller(
        explicit
    ) == generator._generate_bar_controller(original)
    assert generator._generate_bar_implementations(
        explicit
    ) == generator._generate_bar_implementations(original)


@pytest.mark.parametrize("learned", [False, True])
def test_descriptor_only_single_bar3_is_consistent_across_generation(learned):
    """An explicit descriptor owns its BAR even without compatibility scalars."""
    from tests.test_bar_controller_index_serving import _impl_for_slot

    context = _context()
    config = context["bar_config"]
    config["served_bars"] = [{**config["served_bars"][1], "role": "registers"}]
    for key in ("primary_bar", "served_bar_index", "aperture_size", "served_is_64bit"):
        config.pop(key)
    if learned:
        config["bar_models"] = {
            "3": dict(
                size=0x4000, regs={"0x100": dict(width=4, reset=0x8172, rw_mask=0)}
            )
        }
    generator = SVOverlayGenerator(TemplateRenderer(), logging.getLogger("multibar"))
    ip = donor_pcie_ip_config_from_result({"template_context": context})
    assert ip.bar_index == 3 and ip.bar_aperture_size == 0x4000
    controller = generator._generate_bar_controller(context)
    assert _impl_for_slot(controller, 3) == "pcileech_bar_impl_device"
    assert _impl_for_slot(controller, 0) == "pcileech_bar_impl_none"
    assert ".BAR_SIZE       ( 16384 )" in controller
    assert [int(word, 16) for word in _bar_dwords(_render_cfgspace_coe(context))] == [
        0,
        0,
        0,
        0xFFFFC000,
        0,
        0,
    ]
    files = generator._generate_bar_implementations(context)
    assert set(files) == {"pcileech_bar_impl_device.sv"}
    implementation = files["pcileech_bar_impl_device.sv"]
    assert "parameter BAR_SIZE = 16384" in implementation
    assert ("8172" in implementation) is learned
    assert [
        int(word, 16)
        for word in _bar_dwords(generator._generate_config_space_coe(context))
    ] == [
        0,
        0,
        0,
        0x00C0FFFF,
        0,
        0,
    ]


def test_descriptor_only_relocated_fidelity_uses_the_same_register_bar():
    from types import SimpleNamespace

    from pcileechfwgenerator.build import _rederive_bar_models

    context = _context()
    config = context["bar_config"]
    config["served_bars"] = [{**config["served_bars"][1], "role": "registers"}]
    for key in ("primary_bar", "served_bar_index", "aperture_size", "served_is_64bit"):
        config.pop(key)
    rebuilt = _rederive_bar_models(context, SimpleNamespace(learning=None))[
        "bar_config"
    ]
    assert rebuilt["served_bar_index"] == 3
    assert rebuilt["primary_bar"] == 1
    assert rebuilt["aperture_size"] == 0x4000
    assert rebuilt["served_bar_fidelities"] == {"3": "readonly_zero"}


def test_explicit_single_bar_keeps_legacy_cfgspace_output():
    from tests.test_cfgspace_bar_index import _ctx_with_served

    context = _ctx_with_served(3, 0x4000)
    expected = _render_cfgspace_coe(deepcopy(context))
    context["bar_config"]["served_bars"] = [
        ServedBar(index=3, size=0x4000, is_memory=True, role="registers").model_dump()
    ]
    assert _render_cfgspace_coe(context) == expected


@pytest.mark.parametrize("descriptor_only", [False, True])
def test_generated_controller_routes_register_and_msix_reads_independently(
    tmp_path, monkeypatch, descriptor_only
):
    """Exercise generated BAR owners/mux; force decoded requests past the engines.

    Vendor FIFOs are elaboration stubs; the existing TLP engines are outside this
    test's scope. The generated storage, MSI-X router/core and response mux run.
    """
    from tests.test_generated_sources_survive_board_copy import (
        CONTROLLER,
        _copy,
        _ip_stubs,
    )

    tool = shutil.which("verilator")
    if tool is None:
        pytest.skip("verilator is not installed")
    context = _context()
    context["bar_config"]["bar_models"] = {
        "0": dict(
            size=0x20000,
            regs={"0x0": dict(width=4, reset=0x77112233, rw_mask=0xFFFFFFFF)},
        )
    }
    if descriptor_only:
        config = context["bar_config"]
        config["served_bars"] = [{**config["served_bars"][1], "role": "registers"}]
        for key in (
            "primary_bar",
            "served_bar_index",
            "aperture_size",
            "served_is_64bit",
        ):
            config.pop(key)
        config["bar_models"] = {
            "3": dict(
                size=0x4000,
                regs={"0x0": dict(width=4, reset=0x77112233, rw_mask=0xFFFFFFFF)},
            )
        }
    else:
        _decide(context)
    generator = SVOverlayGenerator(TemplateRenderer(), logging.getLogger("multibar"))
    files = {CONTROLLER: generator._generate_bar_controller(context)}
    files.update(generator._generate_bar_implementations(context))
    files.update(generator._msix_sources())
    src = _copy(tmp_path, monkeypatch, files)
    sources = [src / name for name in files]
    sources.append(src / "pcileech_tlps128_bar_controller_board_modules.sv")
    sources.append(_ip_stubs(tmp_path, sources))
    tb = tmp_path / "tb.sv"
    tb_text = r"""
`timescale 1ns/1ps
`include "pcileech_header.svh"
module tb;
  reg clk=0, rst=1;
  always #2 clk=~clk;
  IfAXIS128 incoming(); IfAXIS128 outgoing(); IfPCIeSignals pcie();
  assign incoming.tvalid=0;
  assign incoming.tdata=0;
  assign incoming.tlast=0;
  assign incoming.tuser=0;
  assign incoming.tkeepdw=0;
  assign incoming.has_data=0;
  assign outgoing.tready=1;
  assign pcie.cfg_command=16'h4;
  assign pcie.cfg_interrupt_msixenable=0;
  assign pcie.cfg_interrupt_msixfm=1;
  assign pcie.pl_received_hot_rst=0;
  assign pcie.cfg_received_func_lvl_rst=0;
  pcileech_tlps128_bar_controller dut(.rst(rst), .clk(clk), .bar_en(1'b1),
      .pcie_id(16'h0001), .pcie_ctx(pcie), .rst_pcie_user(1'b0),
      .tlps_in(incoming), .tlps_out(outgoing));
  reg [6:0] rb=0, wb=0;
  reg [31:0] ra=0, wa=0, wd=0;
  reg rv=0, wv=0;
  reg [87:0] rc=0;
  integer seq=0, n=0;
  task write_word(input integer bar, input [31:0] addr, input [31:0] data);
    @(negedge clk); wb=7'b1 << bar; wa=addr; wd=data; wv=1;
    @(negedge clk); wv=0;
    repeat(4) @(negedge clk);
  endtask
  task read_word(input integer bar, input [31:0] addr, input [31:0] expected);
    @(negedge clk); rb=7'b1 << bar; ra=addr; rc=seq; rv=1;
    @(negedge clk); rv=0;
    n=0;
    while (!dut.rd_rsp_valid && n<12) begin @(negedge clk); n=n+1; end
    if (!dut.rd_rsp_valid || dut.rd_rsp_ctx !== seq || dut.rd_rsp_data !== expected)
      $fatal(1,"BAR%0d addr=%h got ctx=%h data=%h expected=%h",bar,addr,dut.rd_rsp_ctx,dut.rd_rsp_data,expected);
    seq=seq+1;
    @(negedge clk);
    if(dut.rd_rsp_valid) $fatal(1,"duplicate response");
  endtask
  initial begin
    force dut.rd_req_bar=rb; force dut.rd_req_addr=ra;
    force dut.rd_req_ctx=rc; force dut.rd_req_valid=rv;
    force dut.wr_bar=wb; force dut.wr_addr=wa;
    force dut.wr_be=4'hf; force dut.wr_data=wd; force dut.wr_valid=wv;
    repeat(5) @(negedge clk); rst=0;
    repeat(64) @(negedge clk);
    read_word(0,0,32'h77112233);
    read_word(3,0,0);
    write_word(3,0,32'hFEE01000);
    read_word(3,0,32'hFEE01000);
    read_word(0,0,32'h77112233);
    write_word(0,0,32'h44332211);
    read_word(0,0,32'h44332211);
    read_word(3,0,32'hFEE01000);
    write_word(3,32'h100,32'hEEEEEEEE);
    read_word(3,32'h100,0);
    read_word(3,32'h2000,0);
    $display("MULTIBAR_ROUTING_PASS reads=%0d",seq); $finish;
  end
  initial begin #10000; $fatal(1,"timeout"); end
endmodule
"""
    if descriptor_only:
        start = tb_text.index("    read_word(0,0,32'h77112233);")
        end = tb_text.index('    $display("MULTIBAR_ROUTING_PASS', start)
        tb_text = tb_text[:start] + r"""
    read_word(3,0,32'h77112233);
    write_word(3,0,32'h44332211);
    read_word(3,0,32'h44332211);
    read_word(3,32'h100,0);
    read_word(3,4,0);
    read_word(3,0,32'h44332211);
    write_word(3,32'h100,32'hEEEEEEEE);
    read_word(3,32'h100,0);
    read_word(3,0,32'h44332211);
    read_word(3,32'h2000,0);
""" + tb_text[end:]
    tb.write_text(tb_text)
    obj = tmp_path / "obj"
    compile_result = subprocess.run(
        [
            tool,
            "--binary",
            "--timing",
            "-Wno-fatal",
            "-Wno-lint",
            "-Wno-style",
            "-I" + str(src),
            "--top-module",
            "tb",
            "--Mdir",
            str(obj),
            *map(str, sources),
            str(tb),
        ],
        capture_output=True,
        text=True,
        timeout=120,
    )
    assert compile_result.returncode == 0, compile_result.stdout + compile_result.stderr
    result = subprocess.run(
        [str(obj / "Vtb")], capture_output=True, text=True, timeout=30
    )
    assert (
        result.returncode == 0 and "MULTIBAR_ROUTING_PASS reads=8" in result.stdout
    ), (result.stdout + result.stderr)


def test_msix_bar_is_enabled_before_its_bir_is_set():
    """The core rejects an MSIx_*_BIR naming a BAR not yet enabled."""
    from pcileechfwgenerator.vivado_handling.pcie_ip_donor_override import (
        _format_extra_config_lines,
    )

    context = _context()
    result, _ = _decide(context)
    extra = donor_pcie_ip_config_from_result(result)
    lines = _format_extra_config_lines(extra)
    assert "CONFIG.MSIx_Table_BIR BAR_3" in lines
    for key in ("CONFIG.MSIx_Table_BIR BAR_3", "CONFIG.MSIx_PBA_BIR BAR_3"):
        assert lines.index("CONFIG.Bar3_Enabled true") < lines.index(key)
