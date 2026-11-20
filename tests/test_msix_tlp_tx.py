"""MX-06: MSI-X Memory Write packing and arbitration on the PCILeech TX stream.

Icarus simulations of the vendored pcie_msix core feeding
pcileech_msix_tlp_tx, whose merged output enters the real upstream
pcileech_tlps128_sink_mux1 next to competing sources, and then the real
upstream converter to the PCIe core (dst64 for x1 boards, dst128 for x4).
Upstream modules are read from the voltcyclone-fpga submodule and only
flattened to plain ports. Every packet leaving the converter is reassembled
and checked: exact framing and content, no interleaving, per-source order,
MSI-X header fields, address width and payload byte order.
"""

import random
import shutil
import subprocess
from pathlib import Path

import pytest
from pcileech_upstream_rtl import BOARDS, flatten, module_text

ROOT = Path(__file__).resolve().parents[1]
TX = ROOT / "src/templates/sv/msix/pcileech_msix_tlp_tx.sv"
CORE = ROOT / "src/templates/sv/vendor/verilog-pcie/pcie_msix.v"
X1 = BOARDS / "CaptainDMA/100t484-1/src"
X4 = BOARDS / "CaptainDMA/35t325_x4/src"

REQUESTER_ID = 0x0310  # bus 3, device 2, function 0
W = 6
SOURCES = {"cfg": 1, "cpl": 2, "fifo": 3}  # cpl enters through the merger
MEMWR_3DW, MEMWR_4DW = 0x40000001, 0x60000001


def bswap32(v):
    return int.from_bytes(v.to_bytes(4, "little"), "big")


TB = r"""
`timescale 1ns/1ps
module src_model #(parameter NB = 1, parameter FILE = "") (
    input clk, input rst, input [31:0] cycle, input tready,
    output reg [127:0] tdata, output reg [3:0] tkeepdw, output reg tlast, output [8:0] tuser,
    output reg tvalid = 0, output has_data);
  // {release[31:0], 2'b0, first, last, keep[3:0], data[127:0]} per beat; packets sorted by release
  reg [167:0] mem [0:(NB > 0 ? NB - 1 : 0)];
  reg tfirst;
  integer rd = 0, rel = 0, rel_last = 0, out_last = 0;
  initial if (NB > 0) $readmemh(FILE, mem);
  always @* begin
    rel = 0; rel_last = 0;
    while (rel < NB && mem[rel][167:136] <= cycle) begin
      if (mem[rel][132]) rel_last = rel_last + 1;
      rel = rel + 1;
    end
  end
  // as the rdengine output FIFO: whole packets only, minus the one ending now
  assign has_data = (rel_last - out_last - ((tvalid && tlast) ? 1 : 0)) > 0;
  assign tuser = {7'd0, tlast, tfirst};
  always @(posedge clk) begin
    if (rst) tvalid <= 0;
    else begin
      if (tvalid && tlast) out_last <= out_last + 1;
      tvalid <= tready && rd < rel;
      if (tready && rd < rel) begin
        {tfirst, tlast, tkeepdw, tdata} <= mem[rd][133:0];
        rd <= rd + 1;
      end
    end
  end
endmodule

module tb;
localparam W = __W__;
reg clk = 0, rst = 1;
always #2 clk = ~clk;
reg [31:0] cycle = 0;
always @(posedge clk) if (!rst) cycle <= cycle + 1;

// ---------------- core ----------------
reg hwv = 0; reg [W+1:0] hwdw = 0; reg [31:0] hwd = 0;
reg [W-1:0] irq_index = 0; reg irq_valid = 0; wire irq_ready;
reg msix_enable = 0, bme = 1, clr = 0;
wire core_clr, clearing;
wire [31:0] tlp_data; wire tlp_strb, tlp_sop, tlp_eop; wire [127:0] tlp_hdr; wire tlp_valid, tlp_ready;
wire [31:0] hrd;
pcie_msix #(.IRQ_INDEX_WIDTH(W), .TLP_HDR_WIDTH(128), .TLP_FORCE_64_BIT_ADDR(0)) core (
  .clk(clk), .rst(rst),
  .host_wr_valid(hwv), .host_wr_pba(1'b0), .host_wr_dw(hwdw), .host_wr_data(hwd), .host_wr_be(4'hF),
  .host_rd_valid(1'b0), .host_rd_pba(1'b0), .host_rd_dw({(W+2){1'b0}}), .host_rd_data(hrd),
  .irq_index(irq_index), .irq_valid(irq_valid), .irq_ready(irq_ready),
  .tx_wr_req_tlp_data(tlp_data), .tx_wr_req_tlp_strb(tlp_strb), .tx_wr_req_tlp_hdr(tlp_hdr),
  .tx_wr_req_tlp_valid(tlp_valid), .tx_wr_req_tlp_sop(tlp_sop), .tx_wr_req_tlp_eop(tlp_eop),
  .tx_wr_req_tlp_ready(tlp_ready),
  .requester_id(16'h__RID__), .msix_enable(msix_enable), .msix_mask(1'b0),
  .bus_master_enable(bme), .msix_clear(core_clr), .msix_clearing(clearing));

// ---------------- sources ----------------
`define SRC(name, nb, file) \
  wire [127:0] name``_tdata; wire [3:0] name``_tkeepdw; wire name``_tlast, name``_tvalid, name``_has_data, name``_tready; wire [8:0] name``_tuser; \
  src_model #(.NB(nb), .FILE(file)) name``_src (.clk(clk), .rst(rst), .cycle(cycle), .tready(name``_tready), \
    .tdata(name``_tdata), .tkeepdw(name``_tkeepdw), .tlast(name``_tlast), .tuser(name``_tuser), .tvalid(name``_tvalid), .has_data(name``_has_data));
`SRC(cfg, __NB_cfg__, "__F_cfg__")
`SRC(cpl, __NB_cpl__, "__F_cpl__")
`SRC(fifo, __NB_fifo__, "__F_fifo__")

// ---------------- merger ----------------
wire [127:0] m_tdata; wire [3:0] m_tkeepdw; wire m_tlast, m_tvalid, m_has_data, m_tready; wire [8:0] m_tuser;
pcileech_msix_tlp_tx tx (.clk(clk), .rst(rst),
  .msg_hdr(tlp_hdr), .msg_data(tlp_data), .msg_valid(tlp_valid), .msg_allow(msix_enable && bme && !clr), .msg_ready(tlp_ready),
  .clear_req(clr), .clear_out(core_clr),
  .cpl_tdata(cpl_tdata), .cpl_tkeepdw(cpl_tkeepdw), .cpl_tlast(cpl_tlast), .cpl_tuser(cpl_tuser),
  .cpl_tvalid(cpl_tvalid), .cpl_has_data(cpl_has_data), .cpl_tready(cpl_tready),
  .out_tdata(m_tdata), .out_tkeepdw(m_tkeepdw), .out_tlast(m_tlast), .out_tuser(m_tuser),
  .out_tvalid(m_tvalid), .out_has_data(m_has_data), .out_tready(m_tready));

// ---------------- upstream mux (BAR responses are input 2) ----------------
wire [127:0] x_tdata; wire [3:0] x_tkeepdw; wire x_tlast, x_tvalid, x_has_data, x_tready; wire [8:0] x_tuser;
pcileech_tlps128_sink_mux1 mux (.clk_pcie(clk), .rst(rst), .stop(1'b0), .idle(),
  .tlps_out_tready(x_tready), .tlps_out_tdata(x_tdata), .tlps_out_tkeepdw(x_tkeepdw), .tlps_out_tvalid(x_tvalid),
  .tlps_out_tlast(x_tlast), .tlps_out_tuser(x_tuser), .tlps_out_has_data(x_has_data),
  .tlps_in1_tready(cfg_tready), .tlps_in1_tdata(cfg_tdata), .tlps_in1_tkeepdw(cfg_tkeepdw), .tlps_in1_tvalid(cfg_tvalid),
  .tlps_in1_tlast(cfg_tlast), .tlps_in1_tuser(cfg_tuser), .tlps_in1_has_data(cfg_has_data),
  .tlps_in2_tready(m_tready), .tlps_in2_tdata(m_tdata), .tlps_in2_tkeepdw(m_tkeepdw), .tlps_in2_tvalid(m_tvalid),
  .tlps_in2_tlast(m_tlast), .tlps_in2_tuser(m_tuser), .tlps_in2_has_data(m_has_data),
  .tlps_in3_tready(fifo_tready), .tlps_in3_tdata(fifo_tdata), .tlps_in3_tkeepdw(fifo_tkeepdw), .tlps_in3_tvalid(fifo_tvalid),
  .tlps_in3_tlast(fifo_tlast), .tlps_in3_tuser(fifo_tuser), .tlps_in3_has_data(fifo_has_data),
  .tlps_in4_tready(), .tlps_in4_tdata(128'd0), .tlps_in4_tkeepdw(4'd0), .tlps_in4_tvalid(1'b0),
  .tlps_in4_tlast(1'b0), .tlps_in4_tuser(9'd0), .tlps_in4_has_data(1'b0));

// ---------------- converter to the PCIe core, with stalls ----------------
reg link_ready = 0;
integer ready_hold_until = 0;
always @(negedge clk) link_ready <= (cycle >= ready_hold_until) && ($urandom % 100 >= __STALL__);
integer npkt = 0;
`ifdef DST128
wire [127:0] l_tdata; wire [3:0] l_tkeepdw; wire l_tlast, l_tvalid, l_has_data; wire [8:0] l_tuser;
pcileech_tlps128_dst128 conv (.rst(rst), .clk_pcie(clk),
  .tlps_out_tready(link_ready), .tlps_out_tdata(l_tdata), .tlps_out_tkeepdw(l_tkeepdw), .tlps_out_tvalid(l_tvalid),
  .tlps_out_tlast(l_tlast), .tlps_out_tuser(l_tuser), .tlps_out_has_data(l_has_data),
  .tlps_in_tready(x_tready), .tlps_in_tdata(x_tdata), .tlps_in_tkeepdw(x_tkeepdw), .tlps_in_tvalid(x_tvalid),
  .tlps_in_tlast(x_tlast), .tlps_in_tuser(x_tuser), .tlps_in_has_data(x_has_data));
integer k;
always @(posedge clk) if (!rst && l_tvalid && link_ready) begin
  for (k = 0; k < 4; k = k + 1) if (l_tkeepdw[k]) $write(" %08h", l_tdata[32*k +: 32]);
  if (l_tlast) begin $display(""); npkt = npkt + 1; end
end
`else
wire [63:0] l_data; wire [7:0] l_keep; wire l_last, l_valid; wire [21:0] l_user;
pcileech_tlps128_dst64 conv (.rst(rst), .clk_pcie(clk),
  .tlp_tx_data(l_data), .tlp_tx_keep(l_keep), .tlp_tx_last(l_last), .tlp_tx_user(l_user),
  .tlp_tx_valid(l_valid), .tlp_tx_ready(link_ready),
  .tlps_in_tready(x_tready), .tlps_in_tdata(x_tdata), .tlps_in_tkeepdw(x_tkeepdw), .tlps_in_tvalid(x_tvalid),
  .tlps_in_tlast(x_tlast), .tlps_in_tuser(x_tuser), .tlps_in_has_data(x_has_data));
// The PCIe core takes a word only on valid && ready, and a stalled word must
// stay unchanged until then (the dst64 stall fix, voltcyclone-fpga PR #5).
reg l_held = 0; reg [63:0] l_hdata; reg [7:0] l_hkeep; reg l_hlast;
always @(posedge clk) if (!rst) begin
  if (l_held && (!l_valid || l_data !== l_hdata || l_keep !== l_hkeep || l_last !== l_hlast))
    $fatal(1, "dst64 changed or dropped a stalled word");
  l_held <= l_valid && !link_ready; l_hdata <= l_data; l_hkeep <= l_keep; l_hlast <= l_last;
end
always @(posedge clk) if (!rst && l_valid && link_ready) begin
  $write(" %08h", l_data[31:0]);
  if (l_keep == 8'hFF) $write(" %08h", l_data[63:32]);
  if (l_last) begin $display(""); npkt = npkt + 1; end
end
`endif

// ---------------- merger-level bounds ----------------
// A committed message waits for at most the completion packet in progress or
// already selected when it was committed. A completion ending in the commit
// clock does not count.
integer since_commit = -1, max_wait = 0, present_t = -1;
always @(posedge clk) if (!rst) begin
  if (m_tvalid && m_tlast && tx.sel == 2'd2 && since_commit >= 0) since_commit = since_commit + 1;
  if (tlp_valid && present_t < 0) present_t = cycle;
  if (tlp_valid && tlp_ready) begin
    if (cycle - present_t > max_wait) max_wait = cycle - present_t;
    present_t = -1;
    since_commit = 0;
  end
  if (tx.sel == 2'd1 && m_tvalid && m_tuser[0]) begin
    if (since_commit > 1) $fatal(1, "message waited behind %0d completion packets", since_commit);
    since_commit = -1;
  end
end

task wr_tbl(input integer vec, input integer dw, input [31:0] data);
  begin @(negedge clk); hwv = 1; hwdw = vec * 4 + dw; hwd = data; @(negedge clk); hwv = 0; end
endtask
task prog(input integer vec, input [63:0] addr, input [31:0] data);
  begin wr_tbl(vec, 0, addr[31:0]); wr_tbl(vec, 1, addr[63:32]); wr_tbl(vec, 2, data); wr_tbl(vec, 3, 0); end
endtask
task raise(input integer vec);
  begin
    @(negedge clk); irq_index = vec; irq_valid = 1;
    @(posedge clk); while (!irq_ready) @(posedge clk);
    @(negedge clk); irq_valid = 0;
  end
endtask
task wait_cycle(input integer c); begin while (cycle < c) @(negedge clk); end endtask

initial begin
  repeat (5) @(negedge clk); rst = 0;
__BODY__
  wait_cycle(__END__);
  while (npkt < __NPKT__ && cycle < __END__ + 20000) @(negedge clk);
  $display("MAXWAIT %0d", max_wait);
  if (npkt != __NPKT__) $fatal(1, "saw %0d packets, expected %0d", npkt, __NPKT__);
  repeat (50) @(negedge clk);
  if (npkt != __NPKT__) $fatal(1, "extra packets: %0d", npkt);
  $display("PASS");
  $finish;
end
endmodule
"""


def make_packets(rng, source, count, release_span):
    """Packets whose first two DWs identify source and sequence; random sizes."""
    packets = []
    releases = sorted(rng.randrange(release_span) for _ in range(count))
    for seq, release in enumerate(releases):
        n = rng.randint(1, 12)
        dws = [0x4A000000 | n, (SOURCES[source] << 24) | seq] + [
            rng.getrandbits(32) for _ in range(n)
        ]
        packets.append((release, dws))
    return packets


def beats_hex(packets):
    lines = []
    for release, dws in packets:
        chunks = [dws[i : i + 4] for i in range(0, len(dws), 4)]
        for i, chunk in enumerate(chunks):
            data = sum(dw << (32 * k) for k, dw in enumerate(chunk))
            keep = (1 << len(chunk)) - 1
            first, last = int(i == 0), int(i == len(chunks) - 1)
            word = (
                (release << 136) | (first << 133) | (last << 132) | (keep << 128) | data
            )
            lines.append(f"{word:042x}")
    return lines


def simulate(tmp_path, *, packets, body, end, n_msix, dst128=False, stall=30):
    compiler, runner = shutil.which("iverilog"), shutil.which("vvp")
    if not compiler or not runner:
        pytest.skip("iverilog/vvp is not installed")
    upstream = flatten(
        module_text(X1 / "pcileech_pcie_tlp_a7.sv", "pcileech_tlps128_sink_mux1")
    )
    upstream += "\n" + flatten(
        module_text(X1 / "pcileech_pcie_a7.sv", "pcileech_tlps128_dst64")
    )
    upstream += "\n" + flatten(
        module_text(X4 / "pcileech_pcie_a7x4.sv", "pcileech_tlps128_dst128")
    )
    (tmp_path / "upstream.sv").write_text("`timescale 1ns/1ps\n" + upstream + "\n")
    tb = TB
    for name in SOURCES:
        lines = beats_hex(packets.get(name, []))
        f = tmp_path / f"{name}.hex"
        f.write_text("\n".join(lines) + "\n")
        tb = tb.replace(f"__NB_{name}__", str(len(lines))).replace(
            f"__F_{name}__", str(f)
        )
    npkt = sum(len(p) for p in packets.values()) + n_msix
    tb = (
        tb.replace("__W__", str(W))
        .replace("__RID__", f"{REQUESTER_ID:04x}")
        .replace("__STALL__", str(stall))
        .replace("__BODY__", body)
        .replace("__END__", str(end))
        .replace("__NPKT__", str(npkt))
    )
    (tmp_path / "tb.sv").write_text(tb)
    out = tmp_path / "sim.vvp"
    args = [compiler, "-g2012", "-s", "tb", "-o", str(out)]
    if dst128:
        args.append("-DDST128")
    args += [str(TX), str(CORE), str(tmp_path / "upstream.sv"), str(tmp_path / "tb.sv")]
    compiled = subprocess.run(args, capture_output=True, text=True, timeout=60)
    assert compiled.returncode == 0, compiled.stdout + compiled.stderr
    result = subprocess.run(
        [runner, str(out)], capture_output=True, text=True, timeout=300
    )
    output = result.stdout + result.stderr
    assert (
        result.returncode == 0 and "PASS" in result.stdout and "FATAL" not in output
    ), output
    return [
        [int(x, 16) for x in line.split()]
        for line in result.stdout.splitlines()
        if line.startswith(" ")
    ]


def expected_msix(vec, addr, data):
    if addr >> 32:
        header = [
            MEMWR_4DW,
            (REQUESTER_ID << 16) | 0x000F,
            addr >> 32,
            addr & 0xFFFFFFFF,
        ]
    else:
        header = [MEMWR_3DW, (REQUESTER_ID << 16) | 0x000F, addr & 0xFFFFFFFF]
    return header + [bswap32(data)]


def split(observed):
    msix = [p for p in observed if p[0] in (MEMWR_3DW, MEMWR_4DW)]
    others = [p for p in observed if p[0] not in (MEMWR_3DW, MEMWR_4DW)]
    return msix, others


def check_sources(observed_others, packets):
    by_source = {}
    for p in observed_others:
        by_source.setdefault(p[1] >> 24, []).append(p)
    for name, sent in packets.items():
        assert by_source.get(SOURCES[name], []) == [
            dws for _, dws in sent
        ], f"{name} stream differs"


def vectors(rng, count):
    table = {}
    for vec in range(count):
        high = rng.choice([0, 0, rng.getrandbits(32) | 1])
        table[vec] = ((high << 32) | (rng.getrandbits(30) << 2), rng.getrandbits(32))
    return table


def program(table):
    return (
        "".join(
            f"  prog({v}, 64'h{a:016x}, 32'h{d:08x});\n" for v, (a, d) in table.items()
        )
        + "  msix_enable = 1;\n"
    )


@pytest.mark.parametrize("dst128", [False, True], ids=["dst64", "dst128"])
@pytest.mark.parametrize("seed", [1, 2, 3])
def test_messages_and_competing_streams_are_exact_and_never_interleaved(
    tmp_path, dst128, seed
):
    rng = random.Random(seed)
    table = vectors(rng, 12)
    packets = {name: make_packets(rng, name, 40, 1500) for name in SOURCES}
    raises = sorted((rng.randrange(300, 1500), rng.randrange(12)) for _ in range(60))
    body = program(table) + "".join(
        f"  wait_cycle({c}); raise({v});\n" for c, v in raises
    )
    observed = simulate(
        tmp_path,
        packets=packets,
        body=body,
        end=1600,
        n_msix=len(raises),
        dst128=dst128,
    )
    msix, others = split(observed)
    assert msix == [expected_msix(v, *table[v]) for _, v in raises]
    assert any(p[0] == MEMWR_4DW for p in msix) and any(p[0] == MEMWR_3DW for p in msix)
    check_sources(others, packets)


def test_payload_byte_order_matches_the_bar_read_engine():
    """The byte swap is taken from the consumer convention, not assumed: the
    upstream read engine swaps BAR data the same way before it enters this
    stream, and the write engine swaps it back."""
    rd = module_text(
        X1 / "pcileech_tlps128_bar_controller.sv", "pcileech_tlps128_bar_rdengine"
    )
    assert (
        "rd_rsp_data_bs  = { rd_rsp_data[7:0], rd_rsp_data[15:8], rd_rsp_data[23:16], rd_rsp_data[31:24] }"
        in rd
    )
    wr = module_text(
        X1 / "pcileech_tlps128_bar_controller.sv", "pcileech_tlps128_bar_wrengine"
    )
    assert (
        "wr_data     <= { tdata[32+00+:8], tdata[32+08+:8], tdata[32+16+:8], tdata[32+24+:8] }"
        in wr
    )
    tx = TX.read_text()
    assert "{ msg_data[7:0], msg_data[15:8], msg_data[23:16], msg_data[31:24] }" in tx


def test_a_committed_message_is_not_passed_by_a_later_completion(tmp_path):
    """Posted-request ordering: hold the link, commit a message, then make a
    completion available; the message must leave first."""
    table = {0: (0x00000000FEE00000, 0x00004021)}
    packets = {
        "cpl": [(400, [0x4A000002, (SOURCES["cpl"] << 24), 0x11111111, 0x22222222])]
    }
    body = (
        "  ready_hold_until = 500;\n"
        + program(table)
        + "  wait_cycle(300); raise(0);\n"
    )
    observed = simulate(
        tmp_path, packets=packets, body=body, end=600, n_msix=1, stall=0
    )
    assert [p[0] for p in observed] == [MEMWR_3DW, 0x4A000002]


def test_a_waiting_completion_is_not_passed_by_a_later_message(tmp_path):
    table = {0: (0x00000000FEE00000, 0x00004021)}
    packets = {
        "cpl": [(100, [0x4A000002, (SOURCES["cpl"] << 24), 0x11111111, 0x22222222])]
    }
    body = (
        "  ready_hold_until = 500;\n"
        + program(table)
        + "  wait_cycle(300); raise(0);\n"
    )
    observed = simulate(
        tmp_path, packets=packets, body=body, end=600, n_msix=1, stall=0
    )
    assert [p[0] for p in observed] == [0x4A000002, MEMWR_3DW]


@pytest.mark.parametrize("dst128", [False, True], ids=["dst64", "dst128"])
def test_neither_stream_starves_the_other(tmp_path, dst128):
    """Completions all available from the start and a producer raising as fast
    as the core accepts. No two messages leave back to back while completions
    wait, and the testbench fails if a committed message ever waits behind more
    than the one completion already in progress."""
    rng = random.Random(11)
    table = vectors(rng, 4)
    packets = {"cpl": make_packets(rng, "cpl", 60, 1)}
    raises = [(0, v % 4) for v in range(80)]
    body = program(table) + "".join(f"  raise({v});\n" for _, v in raises)
    observed = simulate(
        tmp_path,
        packets=packets,
        body=body,
        end=200,
        n_msix=len(raises),
        dst128=dst128,
        stall=20,
    )
    msix, others = split(observed)
    assert msix == [expected_msix(v, *table[v]) for _, v in raises]
    check_sources(others, packets)
    kinds = ["m" if p[0] in (MEMWR_3DW, MEMWR_4DW) else "c" for p in observed]
    last_cpl = max(i for i, k in enumerate(kinds) if k == "c")
    first_msg = kinds.index("m")
    window = "".join(kinds[first_msg : last_cpl + 1])
    # the two streams must actually have competed, or the check is vacuous
    assert first_msg < last_cpl and window.count("m") >= 20, "".join(kinds)
    assert "mm" not in window, window


@pytest.mark.parametrize("control", ["bme", "clr"])
@pytest.mark.parametrize("reenable", [False, True])
def test_controls_dropping_while_presented_prevent_the_commit(
    tmp_path, control, reenable
):
    """Regression: the core registers Bus Master Enable and
    reacts to msix_clear one clock late, so the live gate on msg_ready must
    stop a commit in that clock. Bus mastering off re-pends the vector; a clear
    discards it."""
    table = {0: (0x00000000FEE00000, 0x00004021)}
    packets = {}
    body = program(table) + f"""  ready_hold_until = 100000;
  @(negedge clk); irq_index = 0; irq_valid = 1;
  @(posedge clk); while (!irq_ready) @(posedge clk);
  @(negedge clk); irq_valid = 0;
  while (!tlp_valid) @(negedge clk);   // presented; the commit edge is next
  {control} = {1 if control == "clr" else 0};
  @(negedge clk); @(negedge clk);
  if (tx.msg_has) $fatal(1, "committed after the control dropped");
  {"clr = 0;" if control == "clr" else ""}
  repeat (300) @(negedge clk);
  {"bme = 1;" if reenable else ""}
  ready_hold_until = 0;
"""
    expect = 1 if (reenable and control == "bme") else 0
    observed = simulate(
        tmp_path, packets=packets, body=body, end=1500, n_msix=expect, stall=0
    )
    assert len(observed) == expect


def test_a_message_committed_before_a_clear_leaves_before_the_clear_ends(tmp_path):
    """Regression: a message committed while the stream is
    blocked, then an FLR-style clear. The clear must stay in progress until the
    stale message has left the merger, so a driver can never program a new
    entry first. After the clear the vector is reprogrammed and its new message
    is exact.

    A five-beat packet on the higher-priority mux input 1 with the link held
    keeps the upstream mux locked away from the merger whatever the converter
    buffers (the original dst64 has no queue; a queued converter would
    otherwise drain the merger while the link is held). Once the message has
    left the merger it is in the converter or the PCIe core's TX queue, which
    no device can recall."""
    old = (0x00000000FEE00000, 0x00004021)
    new = (0x00000000FEE01000, 0x00004033)
    blocker = [0x4A000012, (SOURCES["cfg"] << 24)] + [0x5A5A0000 + i for i in range(18)]
    packets = {"cfg": [(0, blocker)]}
    body = "  ready_hold_until = 100000;\n" + program({0: old}) + f"""  raise(0);
  while (!tx.msg_has) @(negedge clk);
  clr = 1; @(negedge clk); clr = 0;
  repeat (300) @(negedge clk);
  if (!clearing || !tx.msg_has) $fatal(1, "clear ended or message left the merger while the stream was blocked");
  ready_hold_until = 0;
  while (clearing) begin
    @(negedge clk);
    if (!clearing && tx.msg_has) $fatal(1, "clear ended before the stale message left the merger");
  end
  prog(0, 64'h{new[0]:016x}, 32'h{new[1]:08x});
  raise(0);
"""
    observed = simulate(
        tmp_path, packets=packets, body=body, end=2000, n_msix=2, stall=0
    )
    assert observed == [blocker, expected_msix(0, *old), expected_msix(0, *new)]
