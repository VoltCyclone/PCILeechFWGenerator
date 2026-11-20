"""MX-07: one decision drives the generated MSI-X RTL and the hard IP.

The plan (device_clone.msix_subsystem) is made once at generation, stored in
the template context, and read by the BAR controller template and the IP
override. These tests pin the decision, the board check, the generated wiring
and that RTL and IP never disagree.
"""

import logging
import re
from pathlib import Path

import pytest
from pcileechfwgenerator.device_clone.msix_subsystem import (
    MsixSubsystemPlan,
    board_msix_support_error,
    plan_msix_subsystem,
)
from pcileechfwgenerator.templating.sv_overlay_generator import SVOverlayGenerator
from pcileechfwgenerator.templating.template_renderer import TemplateRenderer
from pcileechfwgenerator.vivado_handling import pcie_ip_donor_override as override

BOARDS = Path(__file__).resolve().parents[1] / "lib" / "voltcyclone-fpga"


def _context(served=2, size=0x4000, jitter=False):
    return {
        "header": "// test",
        "device_signature": "fixture",
        "bar_config": {
            "primary_bar": 0,
            "served_bar_index": served,
            "aperture_size": size,
            "bars": [{"index": served, "size": size, "is_memory": True}],
        },
        "timing_config": {
            "enable_latency_jitter": jitter,
            "min_read_latency": 2,
            "max_read_latency": 9,
            "avg_read_latency": 4,
        },
    }


def _msix(served=2, vectors=65, table=0x2000, pba=0x3000, **changes):
    fields = dict(
        is_valid=True,
        table_size=vectors,
        table_bir=served,
        table_offset=table,
        pba_bir=served,
        pba_offset=pba,
    )
    fields.update(changes)
    return fields


def _result(context, msix, *, rtl_emitted=True):
    # A real generated build's result carries the emitted MSI-X RTL; the IP
    # override keys on it so a stored plan never advertises MSI-X unless this
    # build produced the controller (rtl_emitted=False models the host-context
    # path, which reuses the static board controller with no generated modules).
    modules = (
        {
            "pcileech_tlps128_bar_controller.sv": "// controller",
            "pcileech_msix_bar_router.sv": "// router",
            "pcie_msix.v": "// core",
        }
        if rtl_emitted
        else {}
    )
    return {
        "template_context": context,
        "msix_data": msix,
        "systemverilog_modules": modules,
    }


def _decided(context, msix, board_error=None):
    plan = plan_msix_subsystem(_result(context, msix), board_error=board_error)
    context["msix_subsystem"] = plan.to_context()
    return plan


def _generator():
    return SVOverlayGenerator(TemplateRenderer(), logging.getLogger("t"))


# --------------------------------------------------------------------------
# The decision
# --------------------------------------------------------------------------


def test_no_msix_capability_is_not_present():
    plan = plan_msix_subsystem(_result(_context(), None), board_error=None)
    assert plan == MsixSubsystemPlan(present=False)
    assert plan.support() == {
        "table_and_pba": False,
        "interrupt_transport": False,
        "event_producer": False,
    }


def test_valid_layout_on_a_capable_board_is_generated():
    plan = plan_msix_subsystem(_result(_context(), _msix()), board_error=None)
    assert plan.generated and plan.reason is None
    assert plan.rtl_parameters() == {
        "bar_index": 2,
        "vectors": 65,
        "irq_index_width": 7,
        "table_first": 0x2000,
        "table_last": 0x2000 + 65 * 16 - 1,
        "pba_first": 0x3000,
        "pba_last": 0x3000 + 16 - 1,
    }
    # Transport is reported separately from an event producer, which does
    # not exist: table emulation is never presented as a working device.
    assert plan.support() == {
        "table_and_pba": True,
        "interrupt_transport": True,
        "event_producer": False,
    }


def test_board_error_refuses_with_its_reason():
    plan = plan_msix_subsystem(
        _result(_context(), _msix()), board_error="the board cannot"
    )
    assert plan.present and not plan.generated
    assert plan.reason == "the board cannot"


@pytest.mark.parametrize(
    "changes,reason",
    [
        ({"table_bir": 0}, "names no implemented BAR"),
        ({"pba_offset": 0x3FF8 + 8}, "exceeds"),
        ({"pba_offset": None}, "malformed MSI-X layout"),
        ({"is_valid": False}, "failed validation"),
    ],
)
def test_invalid_layout_refuses_before_the_board_check(changes, reason):
    plan = plan_msix_subsystem(
        _result(_context(), _msix(**changes)), board_error="the board cannot"
    )
    assert plan.present and not plan.generated
    assert reason in plan.reason


def test_aperture_that_disagrees_with_the_layout_refuses_instead_of_failing():
    context = _context()
    context["bar_config"]["aperture_size"] = 0x8000
    plan = _decided(context, _msix())
    assert not plan.generated
    assert "disagrees with the IP aperture" in plan.reason
    assert "pcie_msix" not in _controller(context)


def test_plan_survives_template_object_wrapping():
    from pcileechfwgenerator.utils.unified_context import TemplateObject

    context = _context()
    plan = _decided(context, _msix())
    wrapped = {"msix_subsystem": TemplateObject(context["msix_subsystem"])}
    assert MsixSubsystemPlan.from_context(wrapped) == plan


def test_plan_round_trips_through_the_context():
    context = _context()
    plan = _decided(context, _msix())
    assert MsixSubsystemPlan.from_context(context) == plan


@pytest.mark.parametrize(
    "kwargs",
    [
        {"present": True, "generated": True},
        {"present": True},
        {
            "present": False,
            "generated": False,
            "reason": None,
            "layout": None,
            "extra": 1,
        },
    ],
)
def test_inconsistent_plans_are_rejected(kwargs):
    with pytest.raises(ValueError):
        MsixSubsystemPlan(**kwargs)


# --------------------------------------------------------------------------
# Board support
# --------------------------------------------------------------------------

_CAPABLE = [
    "CaptainDMA/100t484-1",
    "CaptainDMA/35t325_x1",
    "CaptainDMA/35t325_x4",
    "CaptainDMA/35t484_x1",
    "CaptainDMA/75t484_x1",
    "EnigmaX1",
    "GBOX",
    "PCIeSquirrel",
    "ScreamerM2",
    "ZDMA/100T",
    "ac701_ft601",
]


@pytest.mark.skipif(not (BOARDS / "EnigmaX1").exists(), reason="submodule absent")
@pytest.mark.parametrize("board", _CAPABLE)
def test_boards_with_a_controller_and_status_port_are_supported(board):
    assert board_msix_support_error(BOARDS / board) is None


@pytest.mark.skipif(not (BOARDS / "NeTV2").exists(), reason="submodule absent")
@pytest.mark.parametrize("board", ["pciescreamer", "NeTV2", "acorn_ft2232h"])
def test_boards_without_a_bar_controller_are_refused(board):
    assert "no BAR controller" in board_msix_support_error(BOARDS / board)


def test_board_sources_without_the_status_port_are_refused(tmp_path):
    (tmp_path / "src").mkdir()
    (tmp_path / "src" / "pcileech_tlps128_bar_controller.sv").write_text("")
    (tmp_path / "src" / "pcileech_header.svh").write_text("modport mpm ();")
    assert "too old" in board_msix_support_error(tmp_path)
    assert "not found" in board_msix_support_error(None)


# --------------------------------------------------------------------------
# Generated RTL
# --------------------------------------------------------------------------


def _controller(context):
    return _generator()._generate_bar_controller(context)


def test_without_a_plan_the_controller_has_no_msix_but_takes_the_new_ports():
    text = _controller(_context())
    assert "IfPCIeSignals.mpro      pcie_ctx" in text
    assert "rst_pcie_user" in text
    assert "pcie_msix" not in text and "msix_" not in text
    assert re.search(r"\.tlps_out\s*\(\s*tlps_out\s*\)", text)


def test_refused_plan_generates_no_msix():
    context = _context()
    _decided(context, _msix(), board_error="the board cannot")
    assert "pcie_msix" not in _controller(context)


def test_generated_controller_wires_router_core_and_merger():
    context = _context()
    plan = _decided(context, _msix())
    text = _controller(context)
    params = plan.rtl_parameters()

    # Completions are intercepted and merged on their way to tlps_out.
    assert "IfAXIS128 tlps_bar_cpl();" in text
    assert re.search(r"\.tlps_out\s*\(\s*tlps_bar_cpl\.source\s*\)", text)
    assert re.search(r"\.out_tready\s*\(\s*tlps_out\.tready\s*\)", text)

    # Router windows come straight from decode_parameters().
    for name in ("table_first", "table_last", "pba_first", "pba_last"):
        assert f".{name.upper():<18} ( 32'h{params[name]:08X} )" in text
    assert f"localparam MSIX_IRQ_INDEX_WIDTH = {params['irq_index_width']};" in text
    assert ".BAR_SIZE           ( 16384 )" in text

    # The router sits in the served slot; the slot's implementation is inner.
    assert "rd_req_valid && rd_req_bar[2] )" in text
    router = text.index("i_msix_router")
    inner = text.index("i_bar2(")
    assert router < inner
    assert re.search(r"\.rd_req_valid\s*\(\s*msix_inner_rd_req_valid\s*\)", text)
    assert re.search(r"\.rd_rsp_valid\s*\(\s*msix_inner_rd_rsp_valid\s*\)", text)

    # Live controls, resets, requester ID and the send gate.
    assert "msix_live_enable = pcie_ctx.cfg_interrupt_msixenable" in text
    assert "msix_live_fm     = pcie_ctx.cfg_interrupt_msixfm" in text
    assert "msix_live_bme    = pcie_ctx.cfg_command[2]" in text
    assert re.search(
        r"msix_clear_req\s*=\s*rst_pcie_user \|\| pcie_ctx\.pl_received_hot_rst \|\|"
        r"\s*pcie_ctx\.cfg_received_func_lvl_rst;",
        text,
    )
    assert "{ pcie_id[7:0], pcie_id[12:8], pcie_id[15:13] }" in text
    assert (
        ".msg_allow      ( msix_live_enable && !msix_live_fm && msix_live_bme "
        "&& !msix_clear_req )" in text
    )
    assert re.search(r"\.clear_out\s*\(\s*msix_clear\s*\)", text)
    assert re.search(r"\.msix_clear\s*\(\s*msix_clear\s*\)", text)

    # No producer, and a 3DW header below 4 GiB.
    assert re.search(r"\.irq_valid\s*\(\s*1'b0\s*\)", text)
    assert ".TLP_FORCE_64_BIT_ADDR  ( 0 )" in text


def test_msix_sources_are_staged_only_when_generated(monkeypatch):
    generator = _generator()
    # Only the BAR files matter here; the config-space files need donor IDs.
    monkeypatch.setattr(generator, "_validate_context", lambda c: None)
    monkeypatch.setattr(generator, "_prepare_context", lambda c: c)
    monkeypatch.setattr(generator, "_generate_config_space_coe", lambda c: "")
    monkeypatch.setattr(generator, "_should_generate_writemask", lambda c: False)
    plain = generator.generate_config_space_overlay(_context())
    assert not {"pcie_msix.v", "pcileech_msix_bar_router.sv"} & set(plain)

    context = _context()
    _decided(context, _msix())
    overlays = generator.generate_config_space_overlay(context)
    for name, relative in (
        ("pcileech_msix_bar_router.sv", "sv/msix/pcileech_msix_bar_router.sv"),
        ("pcileech_msix_tlp_tx.sv", "sv/msix/pcileech_msix_tlp_tx.sv"),
        ("pcie_msix.v", "sv/vendor/verilog-pcie/pcie_msix.v"),
    ):
        source = Path(generator.renderer.template_dir) / relative
        assert overlays[name] == source.read_text()


def test_verilog_sources_keep_their_extension_when_written(tmp_path):
    from pcileechfwgenerator.build import FileOperationsManager

    manager = FileOperationsManager(tmp_path)
    path, category = manager._determine_file_path("pcie_msix.v", tmp_path)
    assert path.name == "pcie_msix.v" and category == "sv"


def test_latency_jitter_is_turned_off_when_msix_is_generated(caplog):
    context = _context(jitter=True)
    assert "tlp_latency_emulator" in _controller(context)
    _decided(context, _msix())
    text = _controller(context)
    assert "tlp_latency_emulator" not in text
    assert "latency jitter is disabled" in caplog.text


def test_layout_for_another_bar_is_a_render_error():
    context = _context(served=2)
    _decided(context, _msix())
    context["bar_config"]["served_bar_index"] = 0
    with pytest.raises(Exception, match="does not match the served BAR"):
        _controller(context)


# --------------------------------------------------------------------------
# RTL and IP agree
# --------------------------------------------------------------------------


@pytest.mark.parametrize("board_error", [None, "the board cannot"])
def test_ip_advertises_msix_exactly_when_the_rtl_has_it(board_error):
    """In pipeline order: rendering first wraps the stored record in
    TemplateObject in place, and the IP override must still read it."""
    context = _context()
    _decided(context, _msix(), board_error=board_error)
    # Match the actual core instantiation, not the name in comments or
    # the pcie_msix.v filename, so a broken instantiation fails the test.
    rtl = "pcie_msix #(" in _controller(context)
    cfg = override.donor_pcie_ip_config_from_result(_result(context, _msix()))
    assert cfg.msix_enabled is rtl
    assert rtl is (board_error is None)


def test_stored_generated_plan_without_emitted_rtl_does_not_advertise():
    """The host-context path reuses the static controller and emits no
    generated modules. A generated plan saved in that context must not drive
    the IP override to advertise a table the firmware does not implement."""
    context = _context()
    _decided(context, _msix())  # a fully generated decision is stored
    # This build emitted no controller RTL (systemverilog_modules == {}).
    cfg = override.donor_pcie_ip_config_from_result(
        _result(context, _msix(), rtl_emitted=False)
    )
    assert cfg.msix_enabled is False
    # Sanity: the same stored plan DOES advertise when the RTL was emitted.
    assert (
        override.donor_pcie_ip_config_from_result(
            _result(context, _msix(), rtl_emitted=True)
        ).msix_enabled
        is True
    )


def test_without_a_stored_plan_the_ip_never_advertises_msix(caplog):
    context = _context()
    cfg = override.donor_pcie_ip_config_from_result(_result(context, _msix()))
    assert cfg.msix_enabled is False
    assert "MSI-X subsystem is not generated" in caplog.text


def test_ip_refuses_a_plan_that_disagrees_with_the_served_aperture(caplog):
    context = _context()
    _decided(context, _msix())
    context["bar_config"]["aperture_size"] = 0x8000
    context["bar_config"]["bars"][0]["size"] = 0x8000
    cfg = override.donor_pcie_ip_config_from_result(_result(context, _msix()))
    assert cfg.msix_enabled is False
    assert "disagrees with the IP aperture" in caplog.text


# --------------------------------------------------------------------------
# Full SystemVerilog generation path
# --------------------------------------------------------------------------


def _pipeline_context():
    """The unified builder's complete context, as the full-render test uses."""
    from pcileechfwgenerator.utils.unified_context import UnifiedContextBuilder

    context = (
        UnifiedContextBuilder()
        .create_complete_template_context(vendor_id="10ee", device_id="7024")
        .to_dict()
    )
    context["bar_config"] = _context()["bar_config"]
    return context


@pytest.mark.parametrize("decided", [True, False])
def test_decision_reaches_the_controller_through_the_generator(decided):
    """SystemVerilogGenerator rebuilds the context from a fixed key list; the
    stored decision must survive it, or the IP would advertise MSI-X the RTL
    does not implement."""
    from pcileechfwgenerator.templating.systemverilog_generator import (
        SystemVerilogGenerator,
    )

    context = _pipeline_context()
    if decided:
        _decided(context, _msix())
    modules = SystemVerilogGenerator().generate_systemverilog_modules(
        template_context=context
    )
    controller = modules["pcileech_tlps128_bar_controller.sv"]
    assert ("pcie_msix #(" in controller) is decided
    assert ("pcie_msix.v" in modules) is decided
    cfg = override.donor_pcie_ip_config_from_result(_result(context, _msix()))
    assert cfg.msix_enabled is decided


@pytest.mark.skipif(not (BOARDS / "pciescreamer").exists(), reason="submodule absent")
@pytest.mark.parametrize(
    "board,generated,reason",
    [
        ("pcileech_100t484_x1", True, None),
        ("pcileech_35t325_x4", True, None),
        ("pcileech_pciescreamer_xc7a35", False, "no BAR controller"),
        ("pcileech_netv2_35t", False, "no BAR controller"),
        ("not_a_board", False, "sources were not found"),
        (None, False, "board is not known"),
    ],
)
def test_generator_records_one_decision_per_build(board, generated, reason, caplog):
    from types import SimpleNamespace

    from pcileechfwgenerator.device_clone.pcileech_generator import PCILeechGenerator

    generator = object.__new__(PCILeechGenerator)
    generator.config = SimpleNamespace(board=board)
    generator.logger = logging.getLogger("t")
    context = _context()
    context["interrupt_config"] = {"strategy": "msix", "vectors": 65}
    plan = generator._decide_msix_subsystem(context, {}, _msix())
    assert plan.generated is generated
    assert MsixSubsystemPlan.from_context(context) == plan
    assert context["interrupt_config"]["strategy"] == ("msix" if generated else "none")
    assert context["interrupt_config"]["msix_support"] == plan.support()
    if reason:
        assert reason in plan.reason
        assert reason in caplog.text
