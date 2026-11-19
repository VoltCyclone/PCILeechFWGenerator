"""Hardware-free replay of compiled evidence in the rendered BAR storage RTL."""

import re
import shutil
import subprocess
from unittest.mock import Mock

import pytest
from pcileechfwgenerator.device_clone.bar_model_loader import serialize_bar_model
from pcileechfwgenerator.device_clone.register_evidence import (
    FieldClaim,
    RegisterClassification,
    RegisterRef,
    width_mask,
)
from pcileechfwgenerator.device_clone.register_policy import compile_bar_model
from pcileechfwgenerator.exceptions import PCILeechGenerationError, TemplateRenderError
from pcileechfwgenerator.templating.sv_overlay_generator import SVOverlayGenerator
from pcileechfwgenerator.templating.template_renderer import TemplateRenderer

TEMPLATE = "sv/pcileech_bar_impl_device.sv.j2"


def model_for(kind):
    # Asymmetric values and partial masks expose lane reversal and RO corruption.
    rows = (
        [(0x14, 1, 0x96, 0x0F), (0x15, 1, 0x35, 0), (0x16, 2, 0xC781, 0xF03C)]
        if kind == "subdword"
        else [(0x14, 4, 0xC7813596, 0 if kind == "passive" else 0xF03C000F)]
    )
    classifications = []
    for offset, width, initial, mask in rows:
        classifications.append(
            RegisterClassification(
                ref=RegisterRef(
                    space="mem_bar",
                    bar_index=0,
                    offset=offset,
                    width=width,
                    field_mask=width_mask(width),
                ),
                observed_initial_value=initial,
                claims=(
                    (
                        FieldClaim(
                            mask=mask,
                            semantic="rw",
                            strength="measured",
                            experiment_ids=("fixture-storage",),
                        ),
                    )
                    if mask
                    else ()
                ),
            )
        )
    return compile_bar_model(classifications, 0, 4096).model


def render(model, strategy="msi"):
    return TemplateRenderer().render_template(
        TEMPLATE,
        {
            "header": "// offline test fixture",
            "device_signature": "fixture",
            "bar_model": model,
            "interrupt_config": {"strategy": strategy} if strategy else None,
        },
    )


@pytest.mark.parametrize("kind", ["passive", "rw", "subdword"])
def test_rendered_policy_has_no_guessed_side_effects(kind):
    model = model_for(kind)
    # Old hints must never change semantics, including at the old guessed offsets.
    for reg in model.registers.values():
        reg.hints["maybe_rw1c"] = True
    rtl = render(model)
    for forbidden in (
        "int_status_shadow",
        "int_mask_shadow",
        "int_trigger",
        "doorbell",
        "bram_bar_zero4k",
        "INT_TIMING_SEED",
    ):
        assert forbidden not in rtl
    assert "source=observed_initial_value" in rtl
    assert "unknown_mask=0x" in rtl
    assert "provenance=legacy_unverified" in rtl
    assert "(reset=" not in rtl
    if kind == "passive":
        assert "if (wr_be[" not in rtl
    # Check each case independently (read and write may share an address).
    for case in re.findall(r"case \(.*?endcase", rtl, flags=re.S):
        labels = re.findall(r"32'h([0-9A-F]+):", case)
        assert len(labels) == len(set(labels))
        assert "default:" in case
    if kind == "subdword":
        assert "rd_data_d1[7:0] <= reg_0x0014" in rtl
        assert "rd_data_d1[15:8] <= reg_0x0015" in rtl
        assert "rd_data_d1[31:16] <= reg_0x0016" in rtl
        assert "if (wr_be[2]) reg_0x0016[7:0]" in rtl
        assert "if (wr_be[3]) reg_0x0016[15:8]" in rtl


@pytest.mark.parametrize(
    "offset",
    [
        0x14,
        0x18,
        0x1C,
        0x20,
        0x24,
        0x28,
        0x2C,
        0x100,
        0x104,
        0x108,
        0x1000,
        0xD0,
        0xD4,
        0xD8,
        0xE0,
        0xC0,
    ],
)
def test_prior_guessed_offsets_only_grant_storage(offset):
    classification = RegisterClassification(
        ref=RegisterRef(
            space="mem_bar", bar_index=0, offset=offset, width=4, field_mask=0xFFFFFFFF
        ),
        observed_initial_value=0x98765432,
        claims=(
            FieldClaim(
                mask=0xFFFFFFFF,
                semantic="rw",
                strength="measured",
                experiment_ids=("fixture-storage",),
            ),
        ),
    )
    model = compile_bar_model([classification], 0, 8192).model
    model.registers[offset].hints["maybe_rw1c"] = True
    rtl = render(model)
    assert "if (wr_be[" in rtl
    assert "int_status_shadow" not in rtl
    assert "int_mask_shadow" not in rtl
    assert "int_trigger" not in rtl
    assert "assign interrupt_assert = 1'b0;" in rtl


@pytest.mark.parametrize("serialized", [False, True])
def test_none_initial_value_fails_before_formatting(serialized):
    model = _null_initial_model(0x14, 4)
    if serialized:
        generator = SVOverlayGenerator(TemplateRenderer(), Mock())
        with pytest.raises(
            PCILeechGenerationError, match="has no initial value.*No successful read"
        ):
            generator._generate_bar_implementation(
                {"bar_config": {"bar_models": {0: serialize_bar_model(model)}}}
            )
    else:
        with pytest.raises(
            TemplateRenderError, match="has no initial value.*No successful read"
        ):
            render(model)


@pytest.mark.parametrize("direct", [False, True])
def test_explicit_empty_model_is_an_error(direct):
    model = compile_bar_model([], 0, 4096).model
    if direct:
        with pytest.raises(TemplateRenderError, match="nonempty"):
            render(model)
    else:
        # Through the generator an empty model means "no learned BAR": the
        # read-only fallback is generated, with no storage registers at all.
        generator = SVOverlayGenerator(TemplateRenderer(), Mock())
        out = generator._generate_bar_implementation(
            {"bar_config": {"bar_models": {0: serialize_bar_model(model)}}}
        )
        assert "READ-ONLY FALLBACK" in out and "reg_0x" not in out


def test_served_bar_model_cannot_be_replaced_with_another_bar():
    from pcileechfwgenerator.templating.template_renderer import TemplateRenderer as R

    context = {
        "header": "// test",
        "device_signature": "1234:5678",
        "bar_config": {
            "served_bar_index": 2,
            "aperture_size": 4096,
            "bars": [{"size": 4096}],
            "bar_models": {0: serialize_bar_model(model_for("rw"))},
        },
    }
    generator = SVOverlayGenerator(TemplateRenderer(), Mock())
    # Neither the generator nor the controller borrows BAR0's map for BAR2:
    # BAR2 gets the read-only fallback, not BAR0's registers.
    impl = generator._generate_bar_implementation(context)
    assert "READ-ONLY FALLBACK" in impl and "reg_0x0014" not in impl
    controller = generator._generate_bar_controller(context)
    assert re.search(
        r"pcileech_bar_impl_device\s*#.*?rd_req_bar\[2\]", controller, re.S
    )
    assert "pcileech_bar_impl_zerowrite4k" not in controller
    # The direct-render fallback (no generator decision) keeps the old wiring.
    direct = R().render_template("sv/pcileech_tlps128_bar_controller.sv.j2", context)
    assert re.search(r"pcileech_bar_impl_zerowrite4k\s+i_bar2\b", direct)


def test_template_errors_propagate_through_overlay_entrypoint():
    renderer = Mock()
    renderer.render_template.side_effect = TemplateRenderError("broken BAR template")
    generator = SVOverlayGenerator(renderer, Mock())
    # Isolate unrelated config-space prerequisites; execute the actual BAR path.
    generator._validate_context = Mock()
    generator._generate_config_space_coe = Mock(return_value="fixture coe")
    generator._should_generate_writemask = Mock(return_value=False)
    with pytest.raises(PCILeechGenerationError, match="broken BAR template"):
        generator.generate_config_space_overlay(
            {
                "bar_config": {
                    "bar_models": {0: serialize_bar_model(model_for("rw"))},
                }
            }
        )


@pytest.mark.parametrize("kind", ["passive", "rw", "subdword"])
@pytest.mark.parametrize("strategy", [None, "msi", "msix"])
def test_rendered_sv_lints(tmp_path, kind, strategy):
    tool = shutil.which("verilator")
    if tool is None:
        pytest.skip("verilator is not installed")
    source = tmp_path / "device.sv"
    source.write_text(render(model_for(kind), strategy))
    result = subprocess.run(
        [tool, "--lint-only", "--top-module", "pcileech_bar_impl_device", str(source)],
        capture_output=True,
        text=True,
        timeout=30,
    )
    assert result.returncode == 0, result.stdout + result.stderr


def storage_step(value, data, byte_enable, mask):
    """Independent per-bit reference; untested bits always retain their value."""
    result = value
    for bit in range(32):
        if (byte_enable >> (bit // 8)) & 1 and (mask >> bit) & 1:
            if (data >> bit) & 1:
                result |= 1 << bit
            else:
                result &= ~(1 << bit)
    return result


@pytest.mark.parametrize("kind", ["passive", "rw", "subdword"])
def test_rtl_replays_byte_enables_resets_and_read_pipeline(tmp_path, kind):
    compiler = shutil.which("iverilog")
    runner = shutil.which("vvp")
    if compiler is None or runner is None:
        pytest.skip("iverilog/vvp is not installed")
    source = tmp_path / "device.sv"
    source.write_text(render(model_for(kind)))
    initial = 0xC7813596
    # For subdword this combines the word mask in lanes 2/3 and byte in lane 0.
    mask = {"passive": 0, "rw": 0xF03C000F, "subdword": 0xF03C000F}[kind]
    current = initial
    steps = [f"check_read(32'h14, 32'h{current:08X}, 88'h123456789ABCDEF0123456);"]
    # Exercise every byte enable, repeated transitions, and old-value read/write
    # collision behavior. Transactions are contiguous clock cycles.
    for byte_enable in range(16):
        data = 0x69A3DE5C if byte_enable % 2 else 0x965C21A3
        expected = storage_step(current, data, byte_enable, mask)
        steps.append(
            f"write_and_read(32'h14, 32'h{data:08X}, 4'h{byte_enable:X}, "
            f"32'h{current:08X}, 32'h{expected:08X});"
        )
        current = expected
    steps += [
        f"check_read(32'h14, 32'h{current:08X}, 88'hFEDCBA9876543210FEDCBA);",
        "check_read(32'h80, 32'h0, 88'h3);",
        "@(negedge clk); wr_valid=1; wr_addr=BASE | 32'h80; wr_data=32'hFFFFFFFF; wr_be=4'hF;",
        "@(negedge clk); wr_valid=0;",
        "check_read(32'h80, 32'h0, 88'h4);",
        "@(negedge clk); rst=1; wr_valid=1; wr_addr=BASE | 32'h14; wr_data=0; "
        "wr_be=4'hF; rd_req_valid=1;",
        "@(negedge clk); if (rd_rsp_valid !== 0) "
        '$fatal(1, "reset did not clear response"); '
        "rst=0; wr_valid=0; rd_req_valid=0;",
        f"check_read(32'h14, 32'h{initial:08X}, 88'h5);",
    ]
    tb = tmp_path / "tb.sv"
    tb.write_text(
        """`timescale 1ns/1ps
module tb;
reg clk=0; always #5 clk=~clk;
reg rst=1;
// Real hosts never map a BAR at 0; the controller passes host addresses.
localparam [31:0] BASE = 32'hF7E00000;
reg [31:0] wr_addr=0, wr_data=0, rd_req_addr=0;
reg [3:0] wr_be=0;
reg wr_valid=0, rd_req_valid=0;
reg [87:0] rd_req_ctx=0;
wire [87:0] rd_rsp_ctx;
wire [31:0] rd_rsp_data;
wire rd_rsp_valid, interrupt_assert;
wire [7:0] interrupt_data;
pcileech_bar_impl_device dut(.*);
always @(negedge clk) begin
  if (interrupt_assert !== 0 || interrupt_data !== 0)
    $fatal(1, "storage write produced an interrupt");
end
task check_read(input [31:0] address, value, input [87:0] context_value);
  @(negedge clk); rd_req_addr=BASE | address; rd_req_ctx=context_value; rd_req_valid=1;
  @(negedge clk); rd_req_valid=0;
  @(negedge clk);
  if (rd_rsp_valid !== 1 || rd_rsp_data !== value || rd_rsp_ctx !== context_value)
    $fatal(1, "read mismatch got %h expected %h", rd_rsp_data, value);
  @(negedge clk); if (rd_rsp_valid !== 0) $fatal(1, "stale read valid");
endtask
task write_and_read(input [31:0] address, data, input [3:0] lanes,
                    input [31:0] old_value, new_value);
  @(negedge clk); wr_addr=BASE | address; wr_data=data; wr_be=lanes; wr_valid=1;
  rd_req_addr=BASE | address; rd_req_ctx=88'h71; rd_req_valid=1;
  @(negedge clk); wr_valid=0; rd_req_ctx=88'h72;
  @(negedge clk); rd_req_valid=0;
  if (rd_rsp_valid !== 1 || rd_rsp_data !== old_value || rd_rsp_ctx !== 88'h71)
    $fatal(1, "read/write collision or context mismatch");
  @(negedge clk);
  if (rd_rsp_valid !== 1 || rd_rsp_data !== new_value || rd_rsp_ctx !== 88'h72)
    $fatal(1, "byte-enable update mismatch got %h expected %h", rd_rsp_data, new_value);
endtask
initial begin
  repeat (2) @(negedge clk); rst=0;
"""
        + "\n".join(steps)
        + """
  $display("PASS"); $finish;
end
initial begin #100000; $fatal(1, "timeout"); end
endmodule
"""
    )
    output = tmp_path / "sim.vvp"
    compiled = subprocess.run(
        [compiler, "-g2012", "-s", "tb", "-o", str(output), str(source), str(tb)],
        capture_output=True,
        text=True,
        timeout=30,
    )
    assert compiled.returncode == 0, compiled.stdout + compiled.stderr
    simulation = subprocess.run(
        [runner, str(output)], capture_output=True, text=True, timeout=30
    )
    assert simulation.returncode == 0, simulation.stdout + simulation.stderr
    assert "PASS" in simulation.stdout


@pytest.mark.parametrize("key", [20, "20", "0x14"])
def test_template_format_model_preserves_offset_interpretation(key):
    generator = SVOverlayGenerator(TemplateRenderer(), Mock())
    model = {
        "size": 4096,
        "registers": {
            key: {"width": 4, "reset": 0xC7813596, "rw_mask": 0, "hints": {}},
        },
    }
    rtl = generator._generate_bar_implementation(
        {"header": "// fixture", "bar_config": {"bar_models": {0: model}}}
    )
    assert "reg_0x0014" in rtl
    assert "reg_0x0020" not in rtl


def test_generator_import_does_not_leave_partial_templating_package():
    import sys

    result = subprocess.run(
        [
            sys.executable,
            "-c",
            "from pcileechfwgenerator.device_clone.pcileech_generator "
            "import AdvancedSVGenerator; assert callable(AdvancedSVGenerator)",
        ],
        capture_output=True,
        text=True,
        timeout=30,
    )
    assert result.returncode == 0, result.stdout + result.stderr


def test_controller_template_errors_are_fatal():
    renderer = Mock()
    renderer.render_template.side_effect = TemplateRenderError("invalid controller")
    generator = SVOverlayGenerator(renderer, Mock())
    with pytest.raises(PCILeechGenerationError, match="invalid controller"):
        generator._generate_bar_controller({})


def test_template_format_offset_aliases_fail_instead_of_replacing_permissions():
    generator = SVOverlayGenerator(TemplateRenderer(), Mock())
    model = {
        "size": 4096,
        "registers": {
            "20": {"width": 4, "reset": 0, "rw_mask": 0, "hints": {}},
            "0x14": {"width": 4, "reset": 0, "rw_mask": 0xFF, "hints": {}},
        },
    }
    with pytest.raises(PCILeechGenerationError, match="Duplicate register offset"):
        generator._generate_bar_implementation(
            {"bar_config": {"bar_models": {0: model}}}
        )


def _null_initial_model(offset, width):
    """compile_bar_model omits registers without an initial value; build one
    directly so the defensive loader/template guards stay covered."""
    from pcileechfwgenerator.device_clone.bar_model_loader import BarModel
    from pcileechfwgenerator.device_clone.register_policy import EvidenceRegisterSpec

    reason = "No successful read or explicit reset value"
    return BarModel(
        size=4096,
        registers={
            offset: EvidenceRegisterSpec(
                offset=offset,
                width=width,
                reset=None,
                rw_mask=0,
                hints={
                    "evidence": {
                        "classifications": [{"observed_initial_value": None}],
                        "state_epoch": None,
                        "initial_value_source": None,
                    },
                    "initial_value_absent_reason": reason,
                    "unknown_mask": (1 << (8 * width)) - 1,
                    "unsupported": [],
                },
            )
        },
    )


def _readonly_fallback(size):
    from pcileechfwgenerator.device_clone.bar_model_loader import BarModel

    return TemplateRenderer().render_template(
        TEMPLATE,
        {
            "header": "// fallback fixture",
            "device_signature": "fixture",
            "bar_model": BarModel(size=size, registers={}),
            "bar_model_readonly_fallback": True,
            "interrupt_config": None,
        },
    )


def test_readonly_fallback_lints(tmp_path):
    tool = shutil.which("verilator")
    if tool is None:
        pytest.skip("verilator is not installed")
    source = tmp_path / "device.sv"
    source.write_text(_readonly_fallback(65536))
    result = subprocess.run(
        [tool, "--lint-only", "--top-module", "pcileech_bar_impl_device", str(source)],
        capture_output=True,
        text=True,
        timeout=60,
    )
    assert result.returncode == 0, result.stdout + result.stderr


def test_readonly_fallback_ignores_writes_and_does_not_alias(tmp_path):
    """Default no-model BAR: every read returns zero after any write, with no
    4 KiB aliasing across a 64 KiB aperture at a non-zero host base."""
    compiler, runner = shutil.which("iverilog"), shutil.which("vvp")
    if compiler is None or runner is None:
        pytest.skip("iverilog/vvp is not installed")
    source = tmp_path / "device.sv"
    source.write_text(_readonly_fallback(65536))
    offsets = [0x0, 0x4, 0x1000, 0x1004, 0x8000, 0xFFFC]
    steps = []
    for i, offset in enumerate(offsets):
        steps.append(
            f"do_write(32'h{offset:08X}, 32'hA5A5A5A{i:X}); "
            f"check_read(32'h{offset:08X}, 88'h{i + 1:X});"
        )
    steps.append("check_read(32'h0, 88'h99);")
    tb = tmp_path / "tb.sv"
    tb.write_text(
        """`timescale 1ns/1ps
module tb;
reg clk=0; always #5 clk=~clk;
reg rst=1;
localparam [31:0] BASE = 32'hF7E00000;
reg [31:0] wr_addr=0, wr_data=0, rd_req_addr=0;
reg [3:0] wr_be=0;
reg wr_valid=0, rd_req_valid=0;
reg [87:0] rd_req_ctx=0;
wire [87:0] rd_rsp_ctx;
wire [31:0] rd_rsp_data;
wire rd_rsp_valid;
pcileech_bar_impl_device dut(.*);
task do_write(input [31:0] address, data);
  @(negedge clk); wr_addr=BASE | address; wr_data=data; wr_be=4'hF; wr_valid=1;
  @(negedge clk); wr_valid=0;
endtask
task check_read(input [31:0] address, input [87:0] context_value);
  @(negedge clk); rd_req_addr=BASE | address; rd_req_ctx=context_value; rd_req_valid=1;
  @(negedge clk); rd_req_valid=0;
  @(negedge clk);
  if (rd_rsp_valid !== 1 || rd_rsp_data !== 0 || rd_rsp_ctx !== context_value)
    $fatal(1, "fallback read at %h got %h", address, rd_rsp_data);
endtask
initial begin
  repeat (2) @(negedge clk); rst=0;
"""
        + "\n".join(steps)
        + """
  $display("PASS"); $finish;
end
initial begin #100000; $fatal(1, "timeout"); end
endmodule
"""
    )
    output = tmp_path / "sim.vvp"
    compiled = subprocess.run(
        [compiler, "-g2012", "-s", "tb", "-o", str(output), str(source), str(tb)],
        capture_output=True,
        text=True,
        timeout=30,
    )
    assert compiled.returncode == 0, compiled.stdout + compiled.stderr
    simulation = subprocess.run(
        [runner, str(output)], capture_output=True, text=True, timeout=30
    )
    assert simulation.returncode == 0, simulation.stdout + simulation.stderr
    assert "PASS" in simulation.stdout
