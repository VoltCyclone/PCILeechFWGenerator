"""Provenance and Icarus regressions for the vendored verilog-pcie MSI-X core.

See src/templates/sv/vendor/verilog-pcie/README.md (MX-02).
"""

import hashlib
import shutil
import subprocess
from pathlib import Path

import pytest
from pcie_msix_local_patches import HOST_PORT_HUNKS, LIVE_CONTROL_HUNKS

VENDOR = Path(__file__).resolve().parents[1] / "src/templates/sv/vendor/verilog-pcie"
CORE = VENDOR / "pcie_msix.v"
UPSTREAM_SHA256 = "85da9abfe32cc3ca92934ac9ae2c1228da606496e8762a9015766791410d9570"

# Every local modification as (fix id, vendored text, upstream text). Reverting
# all of them must reproduce the pinned upstream file byte for byte; reverting
# one fix lets a test prove its regression detects that defect.
PATCHES = [
    (
        "provenance",
        "// Vendored from https://github.com/alexforencich/verilog-pcie\n"
        "// revision 25156a9a162c41c60f11f41590c7d006d015ae5a, rtl/pcie_msix.v.\n"
        "// Local modifications (see README.md in this directory):\n"
        "//   MX-02: default PBA write address uses >> 6 (64-bit PBA words), was >> 5.\n"
        "//   MX-02b: scan re-reads the PBA word after any PBA write (stale-word fix);\n"
        "//           masked branch advances the scan index (head-of-line fix);\n"
        "//           Vector Control Mask bits reset to 1 (PCIe default).\n"
        "//   MX-04: a single-clock host port and a duplicated table replace the\n"
        "//          AXI-lite slave (one host read and one host write every clock).\n"
        "//   MX-05: msix_clear sweep, Bus Master Enable gating, a commit/withdraw\n"
        "//          contract for presented messages, requests dropped while disabled,\n"
        "//          and scan fairness under continuous requests.\n"
        "\n",
        "",
    ),
    (
        "pba_addr",
        "    pba_mem_addr = irq_index_reg >> 6; "
        "// MX-02: PBA words are 64 bits; upstream used >> 5\n",
        "    pba_mem_addr = irq_index_reg >> 5;\n",
    ),
    (
        "stale_word",
        "reg [63:0] pba_mem_rd_data_reg = 0;\n"
        "// MX-02b: pba_mem_rd_data_reg holds the PBA word for irq_index_reg's group\n"
        "reg pba_word_valid_reg = 1'b0, pba_word_valid_next;\n",
        "reg [63:0] pba_mem_rd_data_reg = 0;\n",
    ),
    (
        "stale_word",
        "                if (!pba_word_valid_reg) begin\n"
        "                    // MX-02b: cached word was invalidated by a PBA write; refresh it\n"
        "                    pba_mem_rd_en = 1'b1;\n"
        "                    pba_mem_addr = irq_index_reg >> 6;\n"
        "\n"
        "                    state_next = STATE_IDLE;\n"
        "                end else if (pba_mem_rd_data_reg[irq_index_reg & 6'h3f]) begin\n",
        "                if (pba_mem_rd_data_reg[irq_index_reg & 6'h3f]) begin\n",
    ),
    (
        "stale_word",
        "    endcase\n"
        "\n"
        "    // MX-02b: a PBA write invalidates the cached word; a read refreshes it\n"
        "    pba_word_valid_next = pba_mem_wr_en ? 1'b0 : "
        "(pba_mem_rd_en ? 1'b1 : pba_word_valid_reg);\n",
        "    endcase\n",
    ),
    (
        "stale_word",
        "    irq_index_reg <= irq_index_next;\n"
        "    pba_word_valid_reg <= pba_word_valid_next;\n",
        "    irq_index_reg <= irq_index_next;\n",
    ),
    (
        "stale_word",
        "        state_reg <= STATE_IDLE;\n" "        pba_word_valid_reg <= 1'b0;\n",
        "        state_reg <= STATE_IDLE;\n",
    ),
    (
        "masked_advance",
        "                // MX-02b: move past a still-masked vector so the scan cannot livelock on it\n"
        "                irq_index_next = irq_index_reg + 1;\n",
        "",
    ),
    (
        "mask_default",
        "        // MX-02b: odd words hold Vector Control; its Mask bit defaults to 1\n"
        "        tbl_mem[i] = (i & 1) ? 64'h0000000100000000 : 64'd0;\n",
        "        tbl_mem[i] = 0;\n",
    ),
]
# Later layers are recorded against the text before them, so they are listed
# (and reverted) newest first, after the provenance comment.
PATCHES = (
    PATCHES[:1]
    + [("live_control", local, upstream) for local, upstream in LIVE_CONTROL_HUNKS]
    + [("host_port", local, upstream) for local, upstream in HOST_PORT_HUNKS]
    + PATCHES[1:]
)
FIX_IDS = {"pba_addr", "stale_word", "masked_advance", "mask_default"}


def revert(fixes):
    """Undo the named modifications. MX-02 fixes can be reverted alone because
    no later layer touches their text; later layers revert newest first."""
    text = CORE.read_text()
    for fix, local, upstream in PATCHES:
        if fix in fixes:
            assert text.count(local) == 1, f"{fix} patch not found exactly once"
            text = text.replace(local, upstream)
    return text


def upstream_text():
    return revert({fix for fix, _, _ in PATCHES})


def test_vendored_core_is_upstream_plus_recorded_patches():
    digest = hashlib.sha256(upstream_text().encode()).hexdigest()
    assert digest == UPSTREAM_SHA256


def test_vendored_core_retains_mit_notice():
    header = CORE.read_text()[:1200]
    assert "Copyright (c) 2022 Alex Forencich" in header
    assert "Permission is hereby granted, free of charge" in header
    assert "Permission is hereby granted" in (VENDOR / "LICENSE").read_text()


# ---------------------------------------------------------------- simulation

MSG_ADDR = 0xFEE00000  # x86 LAPIC message window; 32-bit, so 3DW MWr
MSG_ADDR64 = 0x00000001FEE00000  # above 4 GiB, so 4DW MWr
REQUESTER_ID = 0x0100


def msg_data(idx):
    return 0x4100 + idx


TB = r"""
`timescale 1ns/1ps
module tb;
localparam W = __WIDTH__;
localparam AW = W + 5;
localparam NVEC = 1 << W;
localparam NWORDS = (NVEC + 63) / 64;
localparam [AW-1:0] PBA = 1 << (AW - 1);

reg clk = 0, rst = 1;
always #2 clk = ~clk;

reg [AW-1:0] awaddr = 0, araddr = 0;
reg [31:0] wdata = 0;
reg [3:0] wstrb = 4'hf;
reg awvalid = 0, wvalid = 0, arvalid = 0;
wire awready, wready, bvalid, arready, rvalid;
wire [1:0] bresp, rresp;
wire [31:0] rdata;
reg [W-1:0] irq_index = 0;
reg irq_valid = 0;
wire irq_ready;
wire [31:0] tlp_data;
wire tlp_strb, tlp_valid, tlp_sop, tlp_eop;
wire [127:0] tlp_hdr;
reg msix_enable = 0, msix_mask = 0;
reg tx_ready = 1;

`ifdef HOST_PORT
reg hwv = 0, hwp = 0, hrv = 0, hrp = 0;
reg bme = 1, clr = 0;
wire clearing;
reg [W+1:0] hwdw = 0, hrdw = 0;
reg [31:0] hwd = 0;
wire [31:0] hrd;
pcie_msix #(.IRQ_INDEX_WIDTH(W), .TLP_HDR_WIDTH(128), .TLP_FORCE_64_BIT_ADDR(0)) dut (
  .clk(clk), .rst(rst),
  .host_wr_valid(hwv), .host_wr_pba(hwp), .host_wr_dw(hwdw), .host_wr_data(hwd), .host_wr_be(wstrb),
  .host_rd_valid(hrv), .host_rd_pba(hrp), .host_rd_dw(hrdw), .host_rd_data(hrd),
  .irq_index(irq_index), .irq_valid(irq_valid), .irq_ready(irq_ready),
  .tx_wr_req_tlp_data(tlp_data), .tx_wr_req_tlp_strb(tlp_strb), .tx_wr_req_tlp_hdr(tlp_hdr),
  .tx_wr_req_tlp_valid(tlp_valid), .tx_wr_req_tlp_sop(tlp_sop), .tx_wr_req_tlp_eop(tlp_eop),
  .tx_wr_req_tlp_ready(tx_ready),
  .requester_id(16'h__RID__), .msix_enable(msix_enable), .msix_mask(msix_mask),
  .bus_master_enable(bme), .msix_clear(clr), .msix_clearing(clearing));
`else
pcie_msix #(.IRQ_INDEX_WIDTH(W), .AXIL_DATA_WIDTH(32), .AXIL_ADDR_WIDTH(AW),
            .AXIL_STRB_WIDTH(4), .TLP_HDR_WIDTH(128), .TLP_FORCE_64_BIT_ADDR(0)) dut (
  .clk(clk), .rst(rst),
  .s_axil_awaddr(awaddr), .s_axil_awprot(3'b0), .s_axil_awvalid(awvalid), .s_axil_awready(awready),
  .s_axil_wdata(wdata), .s_axil_wstrb(wstrb), .s_axil_wvalid(wvalid), .s_axil_wready(wready),
  .s_axil_bresp(bresp), .s_axil_bvalid(bvalid), .s_axil_bready(1'b1),
  .s_axil_araddr(araddr), .s_axil_arprot(3'b0), .s_axil_arvalid(arvalid), .s_axil_arready(arready),
  .s_axil_rdata(rdata), .s_axil_rresp(rresp), .s_axil_rvalid(rvalid), .s_axil_rready(1'b1),
  .irq_index(irq_index), .irq_valid(irq_valid), .irq_ready(irq_ready),
  .tx_wr_req_tlp_data(tlp_data), .tx_wr_req_tlp_strb(tlp_strb), .tx_wr_req_tlp_hdr(tlp_hdr),
  .tx_wr_req_tlp_valid(tlp_valid), .tx_wr_req_tlp_sop(tlp_sop), .tx_wr_req_tlp_eop(tlp_eop),
  .tx_wr_req_tlp_ready(tx_ready),
  .requester_id(16'h__RID__), .msix_enable(msix_enable), .msix_mask(msix_mask));
`endif

// Captured message-write TLPs, accepted on valid && ready.
reg [127:0] cap_hdr [0:63];
reg [31:0] cap_data [0:63];
integer cap_n = 0, cap_rd = 0;
always @(posedge clk) if (tlp_valid && tx_ready) begin
  if (cap_n >= 64) $fatal(1, "message TLP storm: more than 64 captured");
  cap_hdr[cap_n] <= tlp_hdr; cap_data[cap_n] <= tlp_data; cap_n <= cap_n + 1;
end

// Testbench drives and samples on negedges so handshakes never race the DUT.
// Addresses use the upstream AXI-lite map (PBA at the top half) on both ports.
`ifdef HOST_PORT
task wr32(input [AW-1:0] a, input [31:0] d);
  begin
    @(negedge clk); hwp = a[AW-1]; hwdw = (a & ~PBA) >> 2; hwd = d; hwv = 1;
    @(negedge clk); hwv = 0;
  end
endtask

task rd32(input [AW-1:0] a, output [31:0] d);
  begin
    @(negedge clk); hrp = a[AW-1]; hrdw = (a & ~PBA) >> 2; hrv = 1;
    @(negedge clk); hrv = 0;
    @(negedge clk); d = hrd;  // fixed two-clock latency
  end
endtask
`else
task wr32(input [AW-1:0] a, input [31:0] d);
  begin
    @(negedge clk); awaddr = a; wdata = d; awvalid = 1; wvalid = 1;
    while (!awready) @(negedge clk);
    awvalid = 0; wvalid = 0;
  end
endtask

task rd32(input [AW-1:0] a, output [31:0] d);
  begin
    @(negedge clk); araddr = a; arvalid = 1;
    while (!arready) @(negedge clk);
    arvalid = 0;
    while (!rvalid) @(negedge clk);
    d = rdata;
  end
endtask
`endif

task rd64(input [AW-1:0] a, output [63:0] d);
  reg [31:0] lo, hi;
  begin rd32(a, lo); rd32(a + 4, hi); d = {hi, lo}; end
endtask

task prog_vec(input integer idx, input [63:0] addr, input masked);
  begin
    wr32(idx * 16 + 0, addr[31:0]);
    wr32(idx * 16 + 4, addr[63:32]);
    wr32(idx * 16 + 8, 32'h4100 + idx);
    wr32(idx * 16 + 12, masked ? 32'd1 : 32'd0);
  end
endtask

task raise(input integer idx);
  begin
    @(negedge clk); irq_index = idx; irq_valid = 1;
    while (!irq_ready) @(negedge clk);
    @(negedge clk); irq_valid = 0;
  end
endtask

task settle;
  integer k;
  begin for (k = 0; k < 40; k = k + 1) @(negedge clk); end
endtask

// Every PBA word must equal the expectation: only the listed vectors pending.
task expect_pba(input integer a, input integer b, input [255:0] what);
  integer w;
  reg [63:0] got, want;
  begin
    for (w = 0; w < NWORDS; w = w + 1) begin
      want = 64'd0;
      if (a >= 0 && a / 64 == w) want = want | (64'd1 << (a % 64));
      if (b >= 0 && b / 64 == w) want = want | (64'd1 << (b % 64));
      rd64(PBA + w * 8, got);
      if (got !== want)
        $fatal(1, "%0s: PBA word %0d got %h want %h", what, w, got, want);
    end
  end
endtask

task expect_msg(input integer idx, input [63:0] addr);
  reg [127:0] h;
  reg [63:0] a;
  integer k;
  begin
    for (k = 0; k < 200 && cap_rd >= cap_n; k = k + 1) @(negedge clk);
    if (cap_rd >= cap_n) $fatal(1, "vector %0d: no message TLP", idx);
    h = cap_hdr[cap_rd]; cap_rd = cap_rd + 1;
    a = (h[127:125] == 3'b011) ? {h[63:2], 2'b00} : {32'd0, h[63:34], 2'b00};
    if (h[124:120] !== 5'b00000 || h[127:126] !== 2'b01)
      $fatal(1, "vector %0d: not a memory write, fmt/type %b", idx, h[127:120]);
    if ((addr >> 32) != 0 && h[127:125] !== 3'b011)
      $fatal(1, "vector %0d: 64-bit address sent as 3DW", idx);
    if ((addr >> 32) == 0 && h[127:125] !== 3'b010)
      $fatal(1, "vector %0d: address below 4 GiB must use a 3DW header", idx);
    if (a !== addr) $fatal(1, "vector %0d: address %h want %h", idx, a, addr);
    if (h[105:96] !== 10'd1 || h[67:64] !== 4'hf || h[71:68] !== 4'h0)
      $fatal(1, "vector %0d: length/BE %h", idx, h[105:64]);
    if (h[95:80] !== 16'h__RID__) $fatal(1, "vector %0d: requester id %h", idx, h[95:80]);
    if (cap_data[cap_rd - 1] !== 32'h4100 + idx)
      $fatal(1, "vector %0d: data %h", idx, cap_data[cap_rd - 1]);
  end
endtask

task expect_no_msg(input [255:0] what);
  begin
    if (cap_rd != cap_n)
      $fatal(1, "%0s: unexpected message TLP hdr %h data %h", what, cap_hdr[cap_rd], cap_data[cap_rd]);
  end
endtask

reg [31:0] r32;
integer k5;
initial begin
  repeat (4) @(negedge clk); rst = 0; repeat (4) @(negedge clk);
__BODY__
  // No case may leave a late or spurious message behind.
  settle; expect_no_msg("end of test");
  $display("PASS"); $finish;
end
initial begin #2000000; $fatal(1, "timeout"); end
endmodule
"""


def simulate(tmp_path, width, body, core=CORE):
    compiler, runner = shutil.which("iverilog"), shutil.which("vvp")
    if not compiler or not runner:
        pytest.skip("iverilog/vvp is not installed")
    tb = tmp_path / "tb.sv"
    tb.write_text(
        TB.replace("__WIDTH__", str(width))
        .replace("__RID__", f"{REQUESTER_ID:04x}")
        .replace("__BODY__", body)
    )
    out = tmp_path / "sim.vvp"
    defines = ["-DHOST_PORT"] if "host_wr_valid" in Path(core).read_text() else []
    compiled = subprocess.run(
        [compiler, "-g2012", *defines, "-s", "tb", "-o", str(out), str(core), str(tb)],
        capture_output=True,
        text=True,
        timeout=60,
    )
    assert compiled.returncode == 0, compiled.stdout + compiled.stderr
    return subprocess.run(
        [runner, str(out)], capture_output=True, text=True, timeout=120
    )


def assert_pass(result):
    assert result.returncode == 0 and "PASS" in result.stdout, (
        result.stdout + result.stderr
    )


def masked_roundtrip(idx):
    return f"""
  msix_enable = 1;
  prog_vec({idx}, 64'h{MSG_ADDR + idx * 4:x}, 1);
  raise({idx}); settle;
  expect_no_msg("masked vector {idx}");
  expect_pba({idx}, -1, "after masked irq {idx}");
  wr32({idx} * 16 + 12, 0);
  expect_msg({idx}, 64'h{MSG_ADDR + idx * 4:x}); settle;
  expect_pba(-1, -1, "after delivering {idx}");
"""


def delivery_preserves(idx, victim):
    return f"""
  msix_enable = 1;
  prog_vec({victim}, 64'h{MSG_ADDR + victim * 4:x}, 1);
  prog_vec({idx}, 64'h{MSG_ADDR + idx * 4:x}, 0);
  raise({victim}); settle;
  expect_pba({victim}, -1, "victim {victim} pending");
  raise({idx});
  expect_msg({idx}, 64'h{MSG_ADDR + idx * 4:x}); settle;
  expect_pba({victim}, -1, "after delivering {idx}, victim {victim}");
"""


MASKED_CASES = sorted(
    {
        (w, i)
        for w in (6, 7, 11)
        for i in (0, 31, 32, 63, 64, (1 << w) - 1)
        if i < (1 << w)
    }
)
DELIVERY_CASES = [(7, 32, 70), (11, 32, 70), (11, 100, 200), (7, 5, 33), (6, 40, 3)]


@pytest.mark.parametrize("width,idx", MASKED_CASES)
def test_masked_vector_sets_and_clears_its_own_pba_bit(tmp_path, width, idx):
    assert_pass(simulate(tmp_path, width, masked_roundtrip(idx)))


@pytest.mark.parametrize("width,idx,victim", DELIVERY_CASES)
def test_delivery_preserves_other_pending_vectors(tmp_path, width, idx, victim):
    assert_pass(simulate(tmp_path, width, delivery_preserves(idx, victim)))


def head_of_line():
    """A still-masked pending vector must not block delivery of another one.

    Vector 0 is raised first, so the scan finds it and parks there (requests
    do not move the scan since MX-05); vector 5 is then raised and unmasked.
    """
    return f"""
  msix_enable = 1;
  prog_vec(0, 64'h{MSG_ADDR:x}, 1); prog_vec(5, 64'h{MSG_ADDR + 20:x}, 1);
  raise(0); settle; raise(5); settle;
  expect_pba(0, 5, "both pending");
  wr32(5 * 16 + 12, 0);
  expect_msg(5, 64'h{MSG_ADDR + 20:x}); settle;
  expect_pba(0, -1, "vector 0 still masked and pending");
"""


def group_boundary(delivered, pending):
    """Delivering the last vector of a group must not reuse the old PBA word."""
    return f"""
  msix_enable = 1;
  prog_vec({pending}, 64'h{MSG_ADDR + pending * 4:x}, 1);
  prog_vec({delivered}, 64'h{MSG_ADDR + delivered * 4:x}, 0);
  raise({pending}); settle;
  raise({delivered});
  expect_msg({delivered}, 64'h{MSG_ADDR + delivered * 4:x}); settle;
  expect_no_msg("after delivering {delivered}");
  expect_pba({pending}, -1, "only {pending} pending");
"""


def group_boundary_scan(delivered, pending):
    """The scan, not a request, delivers the last vector of a group and must
    re-read the PBA word before testing the first vector of the next group."""
    return f"""
  msix_enable = 1;
  prog_vec({pending}, 64'h{MSG_ADDR + pending * 4:x}, 1);
  prog_vec({delivered}, 64'h{MSG_ADDR + delivered * 4:x}, 1);
  raise({pending}); raise({delivered}); settle;
  expect_pba({pending}, {delivered}, "both pending");
  wr32({delivered} * 16 + 12, 0);
  expect_msg({delivered}, 64'h{MSG_ADDR + delivered * 4:x}); settle;
  expect_no_msg("after the scan delivered {delivered}");
  expect_pba({pending}, -1, "only {pending} pending");
"""


def unprogrammed():
    """A vector the host never wrote must stay masked (PCIe default Mask=1)."""
    return """
  msix_enable = 1;
  raise(7); settle;
  expect_no_msg("unprogrammed vector 7");
  expect_pba(7, -1, "unprogrammed vector 7 pending");
"""


def test_scan_does_not_livelock_on_a_masked_pending_vector(tmp_path):
    assert_pass(simulate(tmp_path, 6, head_of_line()))


@pytest.mark.parametrize("delivered,pending", [(63, 0), (127, 64), (63, 64)])
def test_scan_rereads_pba_after_crossing_a_group(tmp_path, delivered, pending):
    assert_pass(simulate(tmp_path, 7, group_boundary(delivered, pending)))


@pytest.mark.parametrize("delivered,pending", [(63, 0), (127, 64)])
def test_scan_delivery_rereads_pba_after_crossing_a_group(tmp_path, delivered, pending):
    assert_pass(simulate(tmp_path, 7, group_boundary_scan(delivered, pending)))


def test_unprogrammed_vector_is_masked_by_default(tmp_path):
    assert_pass(simulate(tmp_path, 6, unprogrammed()))


# Each local fix, reverted alone, must make its regression fail with this symptom.
SENSITIVITY = [
    ("pba_addr", 7, masked_roundtrip(32), "after masked irq 32: PBA word 0 got"),
    ("stale_word", 7, group_boundary_scan(63, 0), "PBA word 1 got"),
    ("masked_advance", 6, head_of_line(), "vector 5: no message TLP"),
    (
        "mask_default",
        6,
        unprogrammed(),
        "unprogrammed vector 7: unexpected message TLP",
    ),
]


@pytest.mark.parametrize(
    "fix,width,body,symptom", SENSITIVITY, ids=[s[0] for s in SENSITIVITY]
)
def test_each_fix_is_detected_when_reverted(tmp_path, fix, width, body, symptom):
    assert {s[0] for s in SENSITIVITY} == FIX_IDS
    core = tmp_path / f"pcie_msix_without_{fix}.v"
    core.write_text(revert({fix}))
    result = simulate(tmp_path, width, body, core=core)
    output = result.stdout + result.stderr
    assert result.returncode != 0 and "PASS" not in result.stdout, output
    assert symptom in output, output


def test_pristine_upstream_writes_to_host_address_zero(tmp_path):
    upstream = tmp_path / "pcie_msix_upstream.v"
    upstream.write_text(upstream_text())
    result = simulate(tmp_path, 7, masked_roundtrip(32), core=upstream)
    assert result.returncode != 0 and "PASS" not in result.stdout
    # The misplaced pending bit (word 1 bit 32, vector 96) is scanned and
    # "delivered" from an unprogrammed, unmasked-by-default table entry: a 3DW
    # MWr of 0 to host address 0 while the requested vector is still masked.
    spurious = (
        "unexpected message TLP hdr 400000010100000f0000000000000000 data 00000000"
    )
    assert spurious in result.stdout + result.stderr, result.stdout + result.stderr


def test_function_mask_defers_then_delivers(tmp_path):
    body = f"""
  msix_enable = 1; msix_mask = 1;
  prog_vec(9, 64'h{MSG_ADDR + 36:x}, 0);
  raise(9); settle;
  expect_no_msg("function masked");
  expect_pba(9, -1, "function masked");
  msix_mask = 0;
  expect_msg(9, 64'h{MSG_ADDR + 36:x}); settle;
  expect_pba(-1, -1, "function unmasked");
"""
    assert_pass(simulate(tmp_path, 6, body))


def test_64_bit_message_address_uses_4dw_header(tmp_path):
    body = f"""
  msix_enable = 1;
  prog_vec(2, 64'h{MSG_ADDR64:x}, 0);
  raise(2);
  expect_msg(2, 64'h{MSG_ADDR64:x}); settle;
  expect_pba(-1, -1, "after 4DW delivery");
"""
    assert_pass(simulate(tmp_path, 6, body))


def test_stalled_message_is_held_stable_and_sent_once(tmp_path):
    body = f"""
  msix_enable = 1;
  prog_vec(6, 64'h{MSG_ADDR + 24:x}, 0);
  @(negedge clk); tx_ready = 0;
  raise(6);
  while (!tlp_valid) @(negedge clk);
  r32 = tlp_data;
  repeat (25) begin
    @(negedge clk);
    if (!tlp_valid || tlp_data !== r32) $fatal(1, "stalled message dropped or changed");
  end
  expect_no_msg("while stalled");
  tx_ready = 1;
  expect_msg(6, 64'h{MSG_ADDR + 24:x}); settle;
  expect_pba(-1, -1, "after stalled delivery");
"""
    assert_pass(simulate(tmp_path, 6, body))


def test_request_while_msix_disabled_is_dropped(tmp_path):
    """MX-05 contract: requests while MSI-X Enable is clear are not recorded, so
    a driver enabling MSI-X later does not receive events from before."""
    body = f"""
  prog_vec(4, 64'h{MSG_ADDR + 16:x}, 0);
  raise(4); settle;
  expect_no_msg("msix disabled");
  expect_pba(-1, -1, "msix disabled");
  msix_enable = 1; settle;
  expect_no_msg("nothing delivered on enable");
"""
    assert_pass(simulate(tmp_path, 6, body))


def test_rst_preserves_table_and_pba(tmp_path):
    """MX-05 contract: rst includes PCILeech's own soft reset, which is not a
    PCIe reset, so it must not wipe state the host driver programmed."""
    body = f"""
  msix_enable = 1;
  prog_vec(3, 64'h{MSG_ADDR + 12:x}, 1);
  raise(3); settle;
  expect_pba(3, -1, "before reset");
  @(negedge clk); rst = 1; repeat (4) @(negedge clk); rst = 0; repeat (4) @(negedge clk);
  expect_pba(3, -1, "after rst (pending survives)");
  rd32(3 * 16 + 0, r32);
  if (r32 !== 32'h{MSG_ADDR + 12:x}) $fatal(1, "rst changed the table: %h", r32);
"""
    assert_pass(simulate(tmp_path, 6, body))


# ------------------------------------------------------------------ MX-05

CLEAR_CHECK = """
  // Every entry is back at its reset value and nothing is pending.
  for (k5 = 0; k5 < NVEC; k5 = k5 + 1) begin
    rd32(k5 * 16 + 0, r32); if (r32 !== 0) $fatal(1, "entry %0d address %h after clear", k5, r32);
    rd32(k5 * 16 + 8, r32); if (r32 !== 0) $fatal(1, "entry %0d data %h after clear", k5, r32);
    rd32(k5 * 16 + 12, r32); if (r32 !== 1) $fatal(1, "entry %0d control %h after clear", k5, r32);
  end
  expect_pba(-1, -1, "after clear");
"""


@pytest.mark.parametrize("width", [6, 7, 11])
def test_msix_clear_restores_reset_values_and_drops_writes_meanwhile(tmp_path, width):
    top = (1 << width) - 1
    body = f"""
  msix_enable = 1;
  prog_vec(2, 64'h{MSG_ADDR + 8:x}, 0);
  prog_vec({top}, 64'h{MSG_ADDR + 4:x}, 1);
  raise({top}); settle;
  expect_pba({top}, -1, "before clear");
  @(negedge clk); clr = 1; @(negedge clk); clr = 0;
  if (!clearing) $fatal(1, "clear sweep did not start");
  if (irq_ready) $fatal(1, "requests accepted during the clear sweep");
  repeat (4) @(negedge clk);   // the sweep has passed entry 0 by now
  wr32(0 * 16 + 8, 32'hdead);  // dropped: the sweep owns the table
  while (clearing) @(negedge clk);
{CLEAR_CHECK}
  // The vector that was unmasked before the clear is masked again.
  raise(2); settle;
  expect_no_msg("vector 2 after clear");
"""
    assert_pass(simulate(tmp_path, width, body.replace("NVEC", "NVEC")))


def test_clear_requested_during_rst_completes_after_rst(tmp_path):
    body = f"""
  msix_enable = 1;
  prog_vec(5, 64'h{MSG_ADDR:x}, 1);
  raise(5); settle;
  @(negedge clk); rst = 1; clr = 1; repeat (3) @(negedge clk);
  clr = 0; repeat (2) @(negedge clk); rst = 0;
  @(negedge clk); while (clearing) @(negedge clk);
{CLEAR_CHECK}
"""
    assert_pass(simulate(tmp_path, 6, body))


def test_clear_withdraws_a_presented_message(tmp_path):
    body = f"""
  msix_enable = 1;
  prog_vec(6, 64'h{MSG_ADDR + 24:x}, 0);
  @(negedge clk); tx_ready = 0;
  raise(6);
  while (!tlp_valid) @(negedge clk);
  @(negedge clk); clr = 1; @(negedge clk); clr = 0;
  if (tlp_valid) $fatal(1, "presented message survived the clear");
  tx_ready = 1; while (clearing) @(negedge clk);
  settle; expect_no_msg("after clear");
  expect_pba(-1, -1, "after clear");
"""
    assert_pass(simulate(tmp_path, 6, body))


@pytest.mark.parametrize(
    "control,off,on",
    [
        ("function_mask", "msix_mask = 1", "msix_mask = 0"),
        ("bus_master", "bme = 0", "bme = 1"),
    ],
)
def test_presented_message_is_withdrawn_and_repended_then_sent_once(
    tmp_path, control, off, on
):
    body = f"""
  msix_enable = 1;
  prog_vec(6, 64'h{MSG_ADDR + 24:x}, 0);
  @(negedge clk); tx_ready = 0;
  raise(6);
  while (!tlp_valid) @(negedge clk);
  @(negedge clk); {off};
  repeat (3) @(negedge clk);
  if (tlp_valid) $fatal(1, "{control}: message still presented");
  expect_pba(6, -1, "{control}: withdrawn message is pending");
  tx_ready = 1; settle;
  expect_no_msg("{control}: while off");
  {on};
  expect_msg(6, 64'h{MSG_ADDR + 24:x}); settle;
  expect_pba(-1, -1, "{control}: delivered once");
"""
    assert_pass(simulate(tmp_path, 6, body))


@pytest.mark.parametrize("width", [3, 6, 7])
def test_back_to_back_requests_with_an_empty_pba(tmp_path, width):
    """Regression: with <= 64 vectors an empty-group scan step does not move
    the index, which once left the scan turn owed forever and blocked every
    request after the first."""
    body = f"""
  msix_enable = 1;
  prog_vec(1, 64'h{MSG_ADDR + 4:x}, 0);
  prog_vec(2, 64'h{MSG_ADDR + 8:x}, 0);
  raise(1); expect_msg(1, 64'h{MSG_ADDR + 4:x});
  raise(2); expect_msg(2, 64'h{MSG_ADDR + 8:x});
  raise(1); expect_msg(1, 64'h{MSG_ADDR + 4:x}); settle;
  expect_pba(-1, -1, "all delivered");
"""
    assert_pass(simulate(tmp_path, width, body))


def test_scanned_vector_keeps_its_pending_bit_when_disabled_mid_presentation(tmp_path):
    body = f"""
  msix_enable = 1;
  prog_vec(6, 64'h{MSG_ADDR + 24:x}, 1);
  raise(6); settle;
  expect_pba(6, -1, "pending while masked");
  @(negedge clk); tx_ready = 0;
  wr32(6 * 16 + 12, 0);             // unmask: the scan presents it
  while (!tlp_valid) @(negedge clk);
  @(negedge clk); msix_enable = 0;
  repeat (3) @(negedge clk);
  if (tlp_valid) $fatal(1, "message still presented after disable");
  tx_ready = 1; settle;
  expect_no_msg("disabled");
  expect_pba(6, -1, "scanned vector still pending after disable");
  msix_enable = 1;
  expect_msg(6, 64'h{MSG_ADDR + 24:x}); settle;
  expect_pba(-1, -1, "delivered on re-enable");
"""
    assert_pass(simulate(tmp_path, 6, body))


def test_controls_dropping_before_presentation_never_present(tmp_path):
    """Function mask set in the clocks between the table read and presentation:
    the message must not be presented, and must stay pending."""
    body = f"""
  msix_enable = 1;
  prog_vec(6, 64'h{MSG_ADDR + 24:x}, 0);
  @(negedge clk); irq_index = 6; irq_valid = 1;
  while (!irq_ready) @(negedge clk);
  @(negedge clk); irq_valid = 0;
  while (dut.state_reg !== 3'd2) @(negedge clk);  // READ_TBL_2 decides to send
  msix_mask = 1;                      // registered in time for SEND_TLP
  repeat (6) @(negedge clk);
  expect_no_msg("masked before presentation");
  expect_pba(6, -1, "pending after masked presentation");
  msix_mask = 0;
  expect_msg(6, 64'h{MSG_ADDR + 24:x}); settle;
"""
    assert_pass(simulate(tmp_path, 6, body))


def test_presented_message_is_dropped_when_msix_is_disabled(tmp_path):
    body = f"""
  msix_enable = 1;
  prog_vec(6, 64'h{MSG_ADDR + 24:x}, 0);
  @(negedge clk); tx_ready = 0;
  raise(6);
  while (!tlp_valid) @(negedge clk);
  @(negedge clk); msix_enable = 0;
  repeat (3) @(negedge clk);
  if (tlp_valid) $fatal(1, "message still presented after disable");
  tx_ready = 1; settle;
  expect_pba(-1, -1, "dropped, not pending");
  msix_enable = 1; settle;
  expect_no_msg("dropped message is not delivered on re-enable");
"""
    assert_pass(simulate(tmp_path, 6, body))


def test_bus_master_disable_holds_requests_pending(tmp_path):
    body = f"""
  msix_enable = 1; bme = 0;
  prog_vec(9, 64'h{MSG_ADDR + 36:x}, 0);
  raise(9); settle;
  expect_no_msg("bus mastering off");
  expect_pba(9, -1, "bus mastering off");
  bme = 1;
  expect_msg(9, 64'h{MSG_ADDR + 36:x}); settle;
  expect_pba(-1, -1, "bus mastering on");
"""
    assert_pass(simulate(tmp_path, 6, body))


def test_continuous_requests_cannot_starve_a_pending_vector(tmp_path):
    """A producer holding irq_valid high (here on a masked vector) must not stop
    the scan from delivering another vector once it is unmasked."""
    body = f"""
  msix_enable = 1;
  prog_vec(0, 64'h{MSG_ADDR:x}, 1);
  prog_vec(1, 64'h{MSG_ADDR + 4:x}, 1);
  raise(0); settle;
  @(negedge clk); irq_index = 1; irq_valid = 1;
  repeat (20) @(negedge clk);
  wr32(0 * 16 + 12, 0);
  expect_msg(0, 64'h{MSG_ADDR:x});
  @(negedge clk); irq_valid = 0; settle;
  expect_pba(1, -1, "vector 1 still masked and pending");
"""
    assert_pass(simulate(tmp_path, 6, body))


def test_known_behavior_vector_control_reserved_bits_are_stored(tmp_path):
    """Pins upstream behavior for MX-05; reserved-bit readback is undecided."""
    body = """
  wr32(12, 32'hffffffff);
  rd32(12, r32);
  if (r32 !== 32'hffffffff) $fatal(1, "vector control readback %h", r32);
"""
    assert_pass(simulate(tmp_path, 6, body))
