"""MX-04: lossless MSI-X request/response integration in the served BAR.

Icarus simulations of pcileech_msix_bar_router + the vendored pcie_msix core +
a two-clock inner register model (the latency of pcileech_bar_impl_device and
pcileech_bar_impl_zerowrite4k). A reference model checks every response for
order, context and data under sustained traffic: one read and one write per
clock with no backpressure, including same-clock read/write of one DWORD.
"""

import shutil
import subprocess
from pathlib import Path

import pytest
from pcileechfwgenerator.device_clone.config_space_manager import BarInfo
from pcileechfwgenerator.device_clone.msix_layout import derive_msix_layout

ROOT = Path(__file__).resolve().parents[1]
ROUTER = ROOT / "src/templates/sv/msix/pcileech_msix_bar_router.sv"
CORE = ROOT / "src/templates/sv/vendor/verilog-pcie/pcie_msix.v"


def layout_params(vectors, table, pba, bar_size):
    cfg = bytearray(256)
    cfg[0x06], cfg[0x34] = 0x10, 0x40
    cfg[0x40], cfg[0x41] = 0x11, 0x00
    cfg[0x42:0x44] = (vectors - 1).to_bytes(2, "little")
    cfg[0x44:0x48] = table.to_bytes(4, "little")
    cfg[0x48:0x4C] = pba.to_bytes(4, "little")
    layout = derive_msix_layout(
        bytes(cfg), [BarInfo(0, "memory", 0, bar_size, False, False)], 0
    )
    return layout.decode_parameters()


TB = r"""
`timescale 1ns/1ps
module inner_model #(parameter BAR_SIZE = 4096, parameter LATENCY = 2) (
    input clk, input rst,
    input [31:0] wr_addr, input [3:0] wr_be, input [31:0] wr_data, input wr_valid,
    input [87:0] rd_req_ctx, input [31:0] rd_req_addr, input rd_req_valid,
    output [87:0] rd_rsp_ctx, output [31:0] rd_rsp_data, output rd_rsp_valid);
  localparam N = BAR_SIZE / 4;
  reg [31:0] mem [0:N-1];
  integer i;
  initial for (i = 0; i < N; i = i + 1) mem[i] = 32'h0;
  wire [31:0] wi = (wr_addr & (BAR_SIZE - 1)) >> 2;
  wire [31:0] ri = (rd_req_addr & (BAR_SIZE - 1)) >> 2;
  reg [31:0] merged;
  always @* begin
    merged = mem[wi];
    for (i = 0; i < 4; i = i + 1) if (wr_be[i]) merged[8*i +: 8] = wr_data[8*i +: 8];
  end
  reg [87:0] ctx [0:LATENCY-1];
  reg [31:0] dat [0:LATENCY-1];
  reg        vld [0:LATENCY-1];
  always @(posedge clk) begin
    if (wr_valid) mem[wi] <= merged;
    ctx[0] <= rd_req_ctx; vld[0] <= rd_req_valid && !rst;
    dat[0] <= mem[ri];  // read-old, as pcileech_bar_impl_device (nonblocking storage)
    for (i = 1; i < LATENCY; i = i + 1) begin ctx[i] <= ctx[i-1]; dat[i] <= dat[i-1]; vld[i] <= vld[i-1] && !rst; end
  end
  assign rd_rsp_ctx = ctx[LATENCY-1];
  assign rd_rsp_data = dat[LATENCY-1];
  assign rd_rsp_valid = vld[LATENCY-1];
endmodule

module tb;
localparam BAR_SIZE = __BAR_SIZE__;
localparam W = __W__;
localparam [31:0] TF = __TF__, TL = __TL__, PF = __PF__, PL = __PL__;
localparam N = BAR_SIZE / 4;

reg clk = 0, rst = 1;
always #2 clk = ~clk;

reg [31:0] wr_addr = 0, wr_data = 0, rd_addr = 0;
reg [3:0] wr_be = 0;
reg wr_valid = 0, rd_valid = 0;
reg [87:0] rd_ctx = 0;
wire [87:0] rsp_ctx; wire [31:0] rsp_data; wire rsp_valid;
wire iwv, irv; wire [87:0] ictx; wire [31:0] idat; wire ivld;
wire hwv, hwp, hrv, hrp; wire [W+1:0] hwdw, hrdw; wire [31:0] hwd, hrd; wire [3:0] hwbe;
reg [W-1:0] irq_index = 0; reg irq_valid = 0; wire irq_ready;
reg msix_enable = 0, msix_mask = 0;
wire [31:0] tlp_data; wire [127:0] tlp_hdr; wire tlp_strb, tlp_valid, tlp_sop, tlp_eop;

pcileech_msix_bar_router #(.BAR_SIZE(BAR_SIZE), .IRQ_INDEX_WIDTH(W),
    .TABLE_FIRST(TF), .TABLE_LAST(TL), .PBA_FIRST(PF), .PBA_LAST(PL)) router (
  .rst(rst), .clk(clk),
  .wr_addr(wr_addr), .wr_be(wr_be), .wr_data(wr_data), .wr_valid(wr_valid),
  .rd_req_ctx(rd_ctx), .rd_req_addr(rd_addr), .rd_req_valid(rd_valid),
  .rd_rsp_ctx(rsp_ctx), .rd_rsp_data(rsp_data), .rd_rsp_valid(rsp_valid),
  .inner_wr_valid(iwv), .inner_rd_req_valid(irv),
  .inner_rd_rsp_ctx(ictx), .inner_rd_rsp_data(idat), .inner_rd_rsp_valid(ivld),
  .host_wr_valid(hwv), .host_wr_pba(hwp), .host_wr_dw(hwdw), .host_wr_data(hwd), .host_wr_be(hwbe),
  .host_rd_valid(hrv), .host_rd_pba(hrp), .host_rd_dw(hrdw), .host_rd_data(hrd));

inner_model #(.BAR_SIZE(BAR_SIZE), .LATENCY(__INNER_LATENCY__)) inner (
  .clk(clk), .rst(rst),
  .wr_addr(wr_addr), .wr_be(wr_be), .wr_data(wr_data), .wr_valid(iwv),
  .rd_req_ctx(rd_ctx), .rd_req_addr(rd_addr), .rd_req_valid(irv),
  .rd_rsp_ctx(ictx), .rd_rsp_data(idat), .rd_rsp_valid(ivld));

pcie_msix #(.IRQ_INDEX_WIDTH(W), .TLP_HDR_WIDTH(128), .TLP_FORCE_64_BIT_ADDR(0)) core (
  .clk(clk), .rst(rst),
  .host_wr_valid(hwv), .host_wr_pba(hwp), .host_wr_dw(hwdw), .host_wr_data(hwd), .host_wr_be(hwbe),
  .host_rd_valid(hrv), .host_rd_pba(hrp), .host_rd_dw(hrdw), .host_rd_data(hrd),
  .irq_index(irq_index), .irq_valid(irq_valid), .irq_ready(irq_ready),
  .tx_wr_req_tlp_data(tlp_data), .tx_wr_req_tlp_strb(tlp_strb), .tx_wr_req_tlp_hdr(tlp_hdr),
  .tx_wr_req_tlp_valid(tlp_valid), .tx_wr_req_tlp_sop(tlp_sop), .tx_wr_req_tlp_eop(tlp_eop),
  .tx_wr_req_tlp_ready(1'b1),
  .requester_id(16'h0100), .msix_enable(msix_enable), .msix_mask(msix_mask),
  .bus_master_enable(1'b1), .msix_clear(1'b0), .msix_clearing());

// Reference: BAR contents as the host must observe them.
reg [31:0] ref_mem [0:N-1];
integer i;
function in_tbl(input [31:0] off); in_tbl = off >= TF && off <= TL; endfunction
function in_pba(input [31:0] off); in_pba = off >= PF && off <= PL; endfunction
initial begin
  for (i = 0; i < N; i = i + 1) ref_mem[i] = 32'h0;
  // Vector Control (DWORD 3 of each entry) resets with Mask set.
  for (i = TF; i <= TL; i = i + 16) ref_mem[(i + 12) >> 2] = 32'h1;
end

// Expected responses, in request order.
reg [31:0] exp_data [0:65535];
reg [31:0] exp_seq [0:65535];
integer q_wr = 0, q_rd = 0, seq = 0, writes = 0, reads = 0, msix_reads = 0, both = 0;

task drive(input do_w, input [31:0] w_off, input [3:0] be, input [31:0] d,
           input do_r, input [31:0] r_off, input [31:0] hi);
  reg [31:0] v, old_val;
  integer b;
  begin
    @(negedge clk);
    // Inner storage answers a same-clock read with the old value; the MSI-X
    // core returns the new bytes. Capture the old value before the write.
    old_val = ref_mem[r_off >> 2];
    wr_valid = do_w; wr_addr = hi | w_off; wr_be = be; wr_data = d;
    rd_valid = do_r; rd_addr = hi | r_off;
    if (do_w) begin
      writes = writes + 1;
      if (!in_pba(w_off)) begin
        v = ref_mem[w_off >> 2];
        for (b = 0; b < 4; b = b + 1) if (be[b]) v[8*b +: 8] = d[8*b +: 8];
        ref_mem[w_off >> 2] = v;  // a same-clock read sees this write
      end
    end
    if (do_r) begin
      rd_ctx = {56'd0, seq};
      exp_seq[q_wr] = seq;
      exp_data[q_wr] = (in_tbl(r_off) || in_pba(r_off)) ? ref_mem[r_off >> 2] : old_val;
      q_wr = q_wr + 1; seq = seq + 1; reads = reads + 1;
      if (in_tbl(r_off) || in_pba(r_off)) msix_reads = msix_reads + 1;
      if (do_w) both = both + 1;
    end
  end
endtask

task idle; begin @(negedge clk); wr_valid = 0; rd_valid = 0; end endtask

// Single ownership: each access reaches exactly one owner.
always @(posedge clk) if (!rst) begin
  if (iwv && (in_tbl(wr_addr & (BAR_SIZE - 4)) || in_pba(wr_addr & (BAR_SIZE - 4))))
    $fatal(1, "inner implementation received an MSI-X window write at %h", wr_addr);
  if (irv && (in_tbl(rd_addr & (BAR_SIZE - 4)) || in_pba(rd_addr & (BAR_SIZE - 4))))
    $fatal(1, "inner implementation received an MSI-X window read at %h", rd_addr);
  if (wr_valid && (iwv == hwv)) $fatal(1, "write at %h has %0d owners", wr_addr, iwv + hwv);
  if (rd_valid && (irv == hrv)) $fatal(1, "read at %h has %0d owners", rd_addr, irv + hrv);
end

always @(posedge clk) if (!rst && rsp_valid) begin
  if (q_rd >= q_wr) $fatal(1, "response with no outstanding request (duplicate) ctx %h", rsp_ctx);
  if (rsp_ctx[31:0] !== exp_seq[q_rd])
    $fatal(1, "out of order or lost: got ctx %0d want %0d", rsp_ctx[31:0], exp_seq[q_rd]);
  if (rsp_data !== exp_data[q_rd])
    $fatal(1, "read %0d data %h want %h", exp_seq[q_rd], rsp_data, exp_data[q_rd]);
  q_rd = q_rd + 1;
end

// Offsets biased to window edges and to padded entries beyond the table.
function [31:0] pick(input integer r);
  integer k;
  begin
    k = r % 8;
    case (k)
      0: pick = TF + (($unsigned(r) >> 3) % (TL - TF + 1));
      1: pick = PF + (($unsigned(r) >> 3) % (PL - PF + 1));
      2: pick = (k == 2 && (r & 8)) ? TL - 3 : TF;
      3: pick = (r & 8) ? TL + 1 : TF - 4;           // just outside the table
      4: pick = (r & 8) ? PL + 1 : PF - 4;           // just outside the PBA
      default: pick = ($unsigned(r) >> 3) % BAR_SIZE;
    endcase
    pick = pick & (BAR_SIZE - 4);
  end
endfunction

integer seed = __SEED__, n, r1, r2, r3, r4;
reg [31:0] r32;
initial begin
  repeat (4) @(negedge clk); rst = 0; repeat (4) @(negedge clk);
__BODY__
  idle; repeat (8) @(negedge clk);
  if (q_rd != q_wr) $fatal(1, "%0d responses missing", q_wr - q_rd);
  $display("PASS reads=%0d writes=%0d msix_reads=%0d same_clock=%0d", reads, writes, msix_reads, both);
  $finish;
end
initial begin #20000000; $fatal(1, "timeout"); end
endmodule
"""

SUSTAINED = """
  // Phase 1: a read and a write every clock.
  for (n = 0; n < __CYCLES__; n = n + 1) begin
    r1 = $random(seed); r2 = $random(seed); r3 = $random(seed); r4 = $random(seed);
    drive(1, pick(r1), r3[3:0], r4, 1, pick(r2), {r3[31:20], 20'd0} & ~(BAR_SIZE - 1));
  end
  // Phase 2: the same DWORD read and written in one clock.
  for (n = 0; n < 2000; n = n + 1) begin
    r1 = $random(seed); r3 = $random(seed); r4 = $random(seed);
    drive(1, pick(r1), r3[3:0] | 4'b0001, r4, 1, pick(r1), 32'd0);
  end
  // Phase 3: random occupancy.
  for (n = 0; n < __CYCLES__; n = n + 1) begin
    r1 = $random(seed); r2 = $random(seed); r3 = $random(seed); r4 = $random(seed);
    drive(r3[0], pick(r1), r3[7:4], r4, r3[1], pick(r2), 32'd0);
  end
"""


def simulate(tmp_path, params, body, *, inner_latency=2, seed=1, bar_size=0x4000):
    compiler, runner = shutil.which("iverilog"), shutil.which("vvp")
    if not compiler or not runner:
        pytest.skip("iverilog/vvp is not installed")
    tb = (
        TB.replace("__BAR_SIZE__", str(bar_size))
        .replace("__W__", str(params["irq_index_width"]))
        .replace("__TF__", f"32'h{params['table_first']:x}")
        .replace("__TL__", f"32'h{params['table_last']:x}")
        .replace("__PF__", f"32'h{params['pba_first']:x}")
        .replace("__PL__", f"32'h{params['pba_last']:x}")
        .replace("__INNER_LATENCY__", str(inner_latency))
        .replace("__SEED__", str(seed))
        .replace("__BODY__", body)
    )
    (tmp_path / "tb.sv").write_text(tb)
    out = tmp_path / "sim.vvp"
    compiled = subprocess.run(
        [
            compiler,
            "-g2012",
            "-s",
            "tb",
            "-o",
            str(out),
            str(ROUTER),
            str(CORE),
            str(tmp_path / "tb.sv"),
        ],
        capture_output=True,
        text=True,
        timeout=60,
    )
    assert compiled.returncode == 0, compiled.stdout + compiled.stderr
    return subprocess.run(
        [runner, str(out)], capture_output=True, text=True, timeout=300
    )


def assert_pass(result):
    output = result.stdout + result.stderr
    assert result.returncode == 0 and "PASS" in result.stdout, output
    return output


LAYOUTS = {
    # non-power-of-two vector count: padded core entries 6 and 7 must stay unreachable
    "6vec_shared": (6, 0x2000, 0x3000),
    # PBA below the table, adjacent windows
    "33vec_pba_first": (33, 0x1000, 0x0FF8),
    # two PBA QWORDs
    "65vec": (65, 0x0, 0x2000),
}


@pytest.mark.parametrize("name", sorted(LAYOUTS))
@pytest.mark.parametrize("seed", [1, 7])
def test_sustained_traffic_is_lossless_ordered_and_exact(tmp_path, name, seed):
    params = layout_params(*LAYOUTS[name], 0x4000)
    output = assert_pass(
        simulate(tmp_path, params, SUSTAINED.replace("__CYCLES__", "6000"), seed=seed)
    )
    # The run really exercised MSI-X reads and same-clock read/write pairs.
    stats = dict(kv.split("=") for kv in output.split("PASS ")[1].split()[:4])
    assert int(stats["msix_reads"]) > 2000 and int(stats["same_clock"]) > 6000, stats


def test_latency_mismatch_is_caught_not_merged(tmp_path):
    """An inner implementation with a different latency would interleave
    responses; the router refuses rather than corrupting completions."""
    params = layout_params(6, 0x2000, 0x3000, 0x4000)
    body = """
  drive(0, 0, 0, 0, 1, 32'h100, 0);
  drive(0, 0, 0, 0, 1, TF, 0);
  repeat (6) @(negedge clk);
"""
    result = simulate(tmp_path, params, body, inner_latency=3)
    output = result.stdout + result.stderr
    assert result.returncode != 0 and "PASS" not in result.stdout, output
    assert "collided (latency mismatch)" in output or "out of order" in output, output


def test_a_4_gib_bar_size_does_not_overflow_the_offset_mask(tmp_path):
    compiler, runner = shutil.which("iverilog"), shutil.which("vvp")
    if not compiler or not runner:
        pytest.skip("iverilog/vvp is not installed")
    (tmp_path / "t.sv").write_text("""
module t;
  pcileech_msix_bar_router #(.BAR_SIZE(33'h100000000), .IRQ_INDEX_WIDTH(3),
      .TABLE_FIRST(32'hFFFFF000), .TABLE_LAST(32'hFFFFF05F),
      .PBA_FIRST(32'h100), .PBA_LAST(32'h107)) r ();
  initial begin $display("MASK %h", r.OFFSET_MASK); $finish; end
endmodule
""")
    out = tmp_path / "t.vvp"
    built = subprocess.run(
        [
            compiler,
            "-g2012",
            "-s",
            "t",
            "-o",
            str(out),
            str(ROUTER),
            str(tmp_path / "t.sv"),
        ],
        capture_output=True,
        text=True,
        timeout=60,
    )
    assert built.returncode == 0, built.stderr
    ran = subprocess.run([runner, str(out)], capture_output=True, text=True, timeout=60)
    assert "MASK fffffffc" in ran.stdout, ran.stdout + ran.stderr


def test_pba_reads_through_the_router_show_pending_vectors(tmp_path):
    params = layout_params(6, 0x2000, 0x3000, 0x4000)
    body = """
  msix_enable = 1;
  // Program vector 4 masked through the BAR, then raise it.
  drive(1, TF + 4*16 + 0, 4'hf, 32'hFEE00000, 0, 0, 0);
  drive(1, TF + 4*16 + 8, 4'hf, 32'h4104, 0, 0, 0);
  idle;
  @(negedge clk); irq_index = 4; irq_valid = 1;
  while (!irq_ready) @(negedge clk);
  @(negedge clk); irq_valid = 0;
  repeat (20) @(negedge clk);
  // The reference model does not know about pending bits; read directly.
  @(negedge clk); rd_valid = 1; rd_addr = PF; rd_ctx = 88'hABC;
  @(negedge clk); rd_valid = 0;
  @(negedge clk); r32 = rsp_data;
  if (r32 !== 32'h10) $fatal(1, "PBA dword 0 = %h, want vector 4 pending", r32);
  // A host write to the PBA is ignored.
  drive(1, PF, 4'hf, 32'h0, 0, 0, 0); idle;
  @(negedge clk); rd_valid = 1; rd_addr = PF; rd_ctx = 88'hABD;
  @(negedge clk); rd_valid = 0;
  @(negedge clk); if (rsp_data !== 32'h10) $fatal(1, "PBA write was not ignored: %h", rsp_data);
"""
    # Direct reads bypass the reference queue, so disable its checker for them.
    body = body.replace(
        "rd_ctx = 88'hABC;",
        "rd_ctx = 88'hABC; q_wr = q_wr + 1; exp_seq[q_wr-1] = 32'hABC; exp_data[q_wr-1] = 32'h10;",
    )
    body = body.replace(
        "rd_ctx = 88'hABD;",
        "rd_ctx = 88'hABD; q_wr = q_wr + 1; exp_seq[q_wr-1] = 32'hABD; exp_data[q_wr-1] = 32'h10;",
    )
    assert_pass(simulate(tmp_path, params, body))
