/*

Copyright (c) 2022 Alex Forencich

Permission is hereby granted, free of charge, to any person obtaining a copy
of this software and associated documentation files (the "Software"), to deal
in the Software without restriction, including without limitation the rights
to use, copy, modify, merge, publish, distribute, sublicense, and/or sell
copies of the Software, and to permit persons to whom the Software is
furnished to do so, subject to the following conditions:

The above copyright notice and this permission notice shall be included in
all copies or substantial portions of the Software.

THE SOFTWARE IS PROVIDED "AS IS", WITHOUT WARRANTY OF ANY KIND, EXPRESS OR
IMPLIED, INCLUDING BUT NOT LIMITED TO THE WARRANTIES OF MERCHANTABILITY
FITNESS FOR A PARTICULAR PURPOSE AND NONINFRINGEMENT. IN NO EVENT SHALL THE
AUTHORS OR COPYRIGHT HOLDERS BE LIABLE FOR ANY CLAIM, DAMAGES OR OTHER
LIABILITY, WHETHER IN AN ACTION OF CONTRACT, TORT OR OTHERWISE, ARISING FROM,
OUT OF OR IN CONNECTION WITH THE SOFTWARE OR THE USE OR OTHER DEALINGS IN
THE SOFTWARE.

*/

// Vendored from https://github.com/alexforencich/verilog-pcie
// revision 25156a9a162c41c60f11f41590c7d006d015ae5a, rtl/pcie_msix.v.
// Local modifications (see README.md in this directory):
//   MX-02: default PBA write address uses >> 6 (64-bit PBA words), was >> 5.
//   MX-02b: scan re-reads the PBA word after any PBA write (stale-word fix);
//           masked branch advances the scan index (head-of-line fix);
//           Vector Control Mask bits reset to 1 (PCIe default).
//   MX-04: a single-clock host port and a duplicated table replace the
//          AXI-lite slave (one host read and one host write every clock).
//   MX-05: msix_clear sweep, Bus Master Enable gating, a commit/withdraw
//          contract for presented messages, requests dropped while disabled,
//          and scan fairness under continuous requests.

// Language: Verilog 2001

`resetall
`timescale 1ns / 1ps
`default_nettype none

/*
 * PCIe MSI-X module
 */
module pcie_msix #
(
    // Interrupt configuration
    parameter IRQ_INDEX_WIDTH = 11,

    // TLP interface configuration
    parameter TLP_HDR_WIDTH = 128,
    parameter TLP_FORCE_64_BIT_ADDR = 0
)
(
    input  wire                        clk,
    input  wire                        rst,

    /*
     * MX-04: host access port for the MSI-X table and PBA (replaces the
     * upstream AXI-lite slave). Accepts one read and one write every clock,
     * with no backpressure. Addresses are DWORD indices within the table
     * (pba=0) or the PBA (pba=1); PBA writes are ignored. host_rd_data is
     * valid exactly two clocks after host_rd_valid.
     */
    input  wire                        host_wr_valid,
    input  wire                        host_wr_pba,
    input  wire [IRQ_INDEX_WIDTH+1:0]  host_wr_dw,
    input  wire [31:0]                 host_wr_data,
    input  wire [3:0]                  host_wr_be,
    input  wire                        host_rd_valid,
    input  wire                        host_rd_pba,
    input  wire [IRQ_INDEX_WIDTH+1:0]  host_rd_dw,
    output wire [31:0]                 host_rd_data,

    /*
     * Interrupt request input
     */
    input  wire [IRQ_INDEX_WIDTH-1:0]  irq_index,
    input  wire                        irq_valid,
    output wire                        irq_ready,

    /*
     * Memory write TLP output
     */
    output wire [31:0]                 tx_wr_req_tlp_data,
    output wire                        tx_wr_req_tlp_strb,
    output wire [TLP_HDR_WIDTH-1:0]    tx_wr_req_tlp_hdr,
    output wire                        tx_wr_req_tlp_valid,
    output wire                        tx_wr_req_tlp_sop,
    output wire                        tx_wr_req_tlp_eop,
    input  wire                        tx_wr_req_tlp_ready,

    /*
     * Configuration
     */
    input  wire [15:0]                 requester_id,
    input  wire                        msix_enable,
    input  wire                        msix_mask,

    /*
     * MX-05: live controls and reset (contracts in README.md)
     */
    input  wire                        bus_master_enable,
    input  wire                        msix_clear,
    output wire                        msix_clearing
);

parameter TBL_ADDR_WIDTH = IRQ_INDEX_WIDTH+1;
parameter PBA_ADDR_WIDTH = IRQ_INDEX_WIDTH > 6 ? IRQ_INDEX_WIDTH-6 : 0;
parameter PBA_ADDR_WIDTH_INT = PBA_ADDR_WIDTH > 0 ? PBA_ADDR_WIDTH : 1;

// bus width assertions
initial begin
    if (IRQ_INDEX_WIDTH > 11) begin
        $error("Error: IRQ index width must be 11 or less (instance %m)");
        $finish;
    end
end

localparam [2:0]
    TLP_FMT_3DW = 3'b000,
    TLP_FMT_4DW = 3'b001,
    TLP_FMT_3DW_DATA = 3'b010,
    TLP_FMT_4DW_DATA = 3'b011,
    TLP_FMT_PREFIX = 3'b100;

localparam [2:0]
    STATE_IDLE = 3'd0,
    STATE_READ_TBL_1 = 3'd1,
    STATE_READ_TBL_2 = 3'd2,
    STATE_SEND_TLP = 3'd3,
    STATE_WAIT_TLP = 3'd4, // MX-05: message presented, awaiting acceptance or withdrawal
    STATE_CLEAR = 3'd5;    // MX-05: table and PBA reset sweep

reg [2:0] state_reg = STATE_IDLE, state_next;

reg [IRQ_INDEX_WIDTH-1:0] irq_index_reg = 0, irq_index_next;

reg [63:0] vec_addr_reg = 0, vec_addr_next;
reg [31:0] vec_data_reg = 0, vec_data_next;
reg vec_mask_reg = 1'b0, vec_mask_next;

reg [127:0] tlp_hdr;

reg tbl_mem_rd_en;
reg [TBL_ADDR_WIDTH-1:0] tbl_mem_addr;
reg pba_mem_rd_en;
reg pba_mem_wr_en;
reg [PBA_ADDR_WIDTH-1:0] pba_mem_addr;
reg [63:0] pba_mem_wr_data;

reg irq_ready_reg = 1'b0, irq_ready_next;

reg [31:0] tx_wr_req_tlp_data_reg = 0, tx_wr_req_tlp_data_next;
reg [TLP_HDR_WIDTH-1:0] tx_wr_req_tlp_hdr_reg = 0, tx_wr_req_tlp_hdr_next;
reg tx_wr_req_tlp_valid_reg = 0, tx_wr_req_tlp_valid_next;

reg msix_enable_reg = 1'b0;
reg msix_mask_reg = 1'b0;
reg bus_master_enable_reg = 1'b0;
// MX-05: messages may be sent only with MSI-X enabled, unmasked and bus mastering
wire scan_allowed = msix_enable_reg && !msix_mask_reg && bus_master_enable_reg;
// MX-05: clear sweep index over table words; PBA words use its low bits
reg [TBL_ADDR_WIDTH-1:0] clear_index_reg = 0, clear_index_next;
// MX-05: after a request is accepted, the next idle step belongs to the PBA scan
reg scan_owed_reg = 1'b0, scan_owed_next;
// MX-05: a request does not move the scan; it resumes from scan_resume_reg
reg [IRQ_INDEX_WIDTH-1:0] scan_resume_reg = 0, scan_resume_next;
reg from_request_reg = 1'b0, from_request_next;
reg scan_turn;
// MX-05: the scan has taken one step of its turn (the first may only refresh
// the PBA word, so at most two steps are owed)
reg scan_stepped_reg = 1'b0, scan_stepped_next;

// MSI-X table
(* ramstyle = "no_rw_check, mlab" *)
reg [63:0] tbl_mem[(2**TBL_ADDR_WIDTH)-1:0];
// MX-04: host-read copy of the table. Every host write updates both copies in
// the same clock; the scan reads tbl_mem and the host reads tbl_host_mem, so
// neither memory needs more than one write and one read port.
reg [63:0] tbl_host_mem[(2**TBL_ADDR_WIDTH)-1:0];

// MSI-X PBA
(* ram_style = "distributed", ramstyle = "no_rw_check, mlab" *)
reg [63:0] pba_mem[(2**PBA_ADDR_WIDTH)-1:0];

reg [63:0] tbl_mem_rd_data_reg = 0;
reg [63:0] pba_mem_rd_data_reg = 0;
// MX-02b: pba_mem_rd_data_reg holds the PBA word for irq_index_reg's group
reg pba_word_valid_reg = 1'b0, pba_word_valid_next;

// MX-04 host port: the table word is (DWORD index >> 1); bit 0 picks the half.
wire [TBL_ADDR_WIDTH-1:0] host_wr_index = host_wr_dw >> 1;
wire [TBL_ADDR_WIDTH-1:0] host_rd_index = host_rd_dw >> 1;
wire host_tbl_wr = host_wr_valid && !host_wr_pba && state_reg != STATE_CLEAR;
wire [7:0] host_wr_be64 = host_wr_dw[0] ? {host_wr_be, 4'b0000} : {4'b0000, host_wr_be};
wire [63:0] host_wr_data64 = {host_wr_data, host_wr_data};
// MX-05: one byte-enabled write port shared by host writes and the reset sweep
// (host table writes are dropped while the sweep runs), so RAM inference holds
wire tbl_wr_clear = state_reg == STATE_CLEAR;
wire [TBL_ADDR_WIDTH-1:0] tbl_wr_addr = tbl_wr_clear ? clear_index_reg : host_wr_index;
wire [63:0] tbl_wr_data = tbl_wr_clear ? (clear_index_reg[0] ? 64'h0000000100000000 : 64'd0) : host_wr_data64;
wire [7:0] tbl_wr_be = tbl_wr_clear ? 8'hFF : (host_tbl_wr ? host_wr_be64 : 8'h00);

reg [63:0] host_tbl_rd_reg = 0;
reg [63:0] host_pba_rd_reg = 0;
reg host_rd_pba_reg = 1'b0;
reg host_rd_hi_reg = 1'b0;
// Bytes of a same-clock table write to the word being read: the read sees them.
reg [7:0] host_bypass_be_reg = 0;
reg [63:0] host_bypass_data_reg = 0;
reg [63:0] host_rd_word;
reg [31:0] host_rd_data_reg = 0;

assign host_rd_data = host_rd_data_reg;

assign irq_ready = irq_ready_reg && !(scan_owed_reg && scan_allowed);
assign msix_clearing = state_reg == STATE_CLEAR;

assign tx_wr_req_tlp_data = tx_wr_req_tlp_data_reg;
assign tx_wr_req_tlp_strb = 1;
assign tx_wr_req_tlp_hdr = tx_wr_req_tlp_hdr_reg;
assign tx_wr_req_tlp_valid = tx_wr_req_tlp_valid_reg;
assign tx_wr_req_tlp_sop = 1'b1;
assign tx_wr_req_tlp_eop = 1'b1;

integer i;

initial begin
    for (i = 0; i < 2**TBL_ADDR_WIDTH; i = i + 1) begin
        // MX-02b: odd words hold Vector Control; its Mask bit defaults to 1
        tbl_mem[i] = (i & 1) ? 64'h0000000100000000 : 64'd0;
        tbl_host_mem[i] = (i & 1) ? 64'h0000000100000000 : 64'd0;
    end
    for (i = 0; i < 2**PBA_ADDR_WIDTH; i = i + 1) begin
        pba_mem[i] = 0;
    end
end

always @* begin
    state_next = STATE_IDLE;

    tbl_mem_rd_en = 1'b0;
    tbl_mem_addr = {irq_index_reg, 1'b0};

    pba_mem_rd_en = 1'b0;
    pba_mem_wr_en = 1'b0;
    pba_mem_addr = irq_index_reg >> 6; // MX-02: PBA words are 64 bits; upstream used >> 5
    pba_mem_wr_data = 0;

    irq_index_next = irq_index_reg;
    clear_index_next = clear_index_reg;
    scan_owed_next = scan_owed_reg;
    scan_resume_next = scan_resume_reg;
    from_request_next = from_request_reg;
    scan_turn = 1'b0;
    scan_stepped_next = scan_stepped_reg;

    vec_addr_next = vec_addr_reg;
    vec_data_next = vec_data_reg;
    vec_mask_next = vec_mask_reg;

    irq_ready_next = 1'b0;

    tx_wr_req_tlp_data_next = tx_wr_req_tlp_data_reg;
    tx_wr_req_tlp_hdr_next = tx_wr_req_tlp_hdr_reg;
    tx_wr_req_tlp_valid_next = tx_wr_req_tlp_valid_reg && !tx_wr_req_tlp_ready;

    // TLP header
    // DW 0
    if (((vec_addr_reg[63:2] >> 30) != 0) || TLP_FORCE_64_BIT_ADDR) begin
        tlp_hdr[127:125] = TLP_FMT_4DW_DATA; // fmt - 4DW with data
    end else begin
        tlp_hdr[127:125] = TLP_FMT_3DW_DATA; // fmt - 3DW with data
    end
    tlp_hdr[124:120] = 5'b00000; // type - write
    tlp_hdr[119] = 1'b0; // T9
    tlp_hdr[118:116] = 3'b000; // TC
    tlp_hdr[115] = 1'b0; // T8
    tlp_hdr[114] = 1'b0; // attr
    tlp_hdr[113] = 1'b0; // LN
    tlp_hdr[112] = 1'b0; // TH
    tlp_hdr[111] = 1'b0; // TD
    tlp_hdr[110] = 1'b0; // EP
    tlp_hdr[109:108] = 2'b00; // attr
    tlp_hdr[107:106] = 3'b000; // AT
    tlp_hdr[105:96] = 10'd1; // length
    // DW 1
    tlp_hdr[95:80] = requester_id; // requester ID
    tlp_hdr[79:72] = 8'd0; // tag
    tlp_hdr[71:68] = 4'b0000; // last BE
    tlp_hdr[67:64] = 4'b1111; // first BE
    if (((vec_addr_reg[63:2] >> 30) != 0) || TLP_FORCE_64_BIT_ADDR) begin
        // DW 2+3
        tlp_hdr[63:2] = vec_addr_reg[63:2]; // address
        tlp_hdr[1:0] = 2'b00; // PH
    end else begin
        // DW 2
        tlp_hdr[63:34] = vec_addr_reg[63:2]; // address
        tlp_hdr[33:32] = 2'b00; // PH
        // DW 3
        tlp_hdr[31:0] = 32'd0;
    end

    case (state_reg)
        STATE_IDLE: begin
            irq_ready_next = 1'b1;

            if (irq_valid && irq_ready) begin
                // new request
                irq_ready_next = 1'b0;
                irq_index_next = irq_index;
                // MX-05: remember the scan position; the scan gets the next step
                scan_owed_next = 1'b1;
                scan_resume_next = irq_index_reg;
                from_request_next = 1'b1;

                tbl_mem_rd_en = 1'b1;
                tbl_mem_addr = {irq_index_next, 1'b0};

                pba_mem_rd_en = 1'b1;
                pba_mem_addr = irq_index_next >> 6;

                state_next = STATE_READ_TBL_1;
            end else if ((!irq_valid || scan_owed_reg) && scan_allowed) begin
                // MX-05: a waiting request yields one step to the scan, so
                // continuous requests cannot starve pending vectors
                scan_turn = 1'b1;
                from_request_next = 1'b0;
                // no new request waiting, scan PBA for masked requests

                if (!pba_word_valid_reg) begin
                    // MX-02b: cached word was invalidated by a PBA write; refresh it
                    pba_mem_rd_en = 1'b1;
                    pba_mem_addr = irq_index_reg >> 6;

                    state_next = STATE_IDLE;
                end else if (pba_mem_rd_data_reg[irq_index_reg & 6'h3f]) begin
                    // PBA bit for current index is set, try issuing it
                    irq_ready_next = 1'b0;

                    tbl_mem_rd_en = 1'b1;
                    tbl_mem_addr = {irq_index_next, 1'b0};

                    pba_mem_rd_en = 1'b1;
                    pba_mem_addr = irq_index_next >> 6;

                    state_next = STATE_READ_TBL_1;
                end else begin
                    // PBA bit for current index is not set
                    if (pba_mem_rd_data_reg) begin
                        // at least one bit set in current group, move to next index
                        irq_index_next = irq_index_reg + 1;
                    end else begin
                        // no bits set in current group, move to next group
                        irq_index_next = (irq_index_reg & ({IRQ_INDEX_WIDTH{1'b1}} << 6)) + 7'd64;
                    end

                    pba_mem_rd_en = 1'b1;
                    pba_mem_addr = irq_index_next >> 6;

                    state_next = STATE_IDLE;
                end
            end else begin
                state_next = STATE_IDLE;
            end
        end
        STATE_READ_TBL_1: begin
            // handle first table read
            tbl_mem_rd_en = 1'b1;
            tbl_mem_addr = {irq_index_reg, 1'b1};

            vec_addr_next = {tbl_mem_rd_data_reg[63:2], 2'b00};

            state_next = STATE_READ_TBL_2;
        end
        STATE_READ_TBL_2: begin
            // handle second table read
            vec_data_next = tbl_mem_rd_data_reg[31:0];
            vec_mask_next = tbl_mem_rd_data_reg[32];

            if (scan_allowed && !vec_mask_next) begin
                // send TLP
                state_next = STATE_SEND_TLP;
            end else if (!msix_enable_reg) begin
                // MX-05: a request while MSI-X is disabled is dropped, not recorded
                if (from_request_reg) begin
                    // MX-05: resume the scan where the request interrupted it
                    irq_index_next = scan_resume_reg;
                    if (!pba_mem_wr_en) begin
                        pba_mem_rd_en = 1'b1;
                        pba_mem_addr = scan_resume_reg >> 6;
                    end
                end
                irq_ready_next = 1'b1;
                state_next = STATE_IDLE;
            end else begin
                // set PBA bit
                pba_mem_wr_en = 1'b1;
                pba_mem_wr_data = pba_mem_rd_data_reg | (1 << (irq_index_reg & 6'h3F));
                // MX-02b: move past a still-masked vector so the scan cannot livelock on it
                irq_index_next = irq_index_reg + 1;
                if (from_request_reg) begin
                    // MX-05: resume the scan where the request interrupted it
                    irq_index_next = scan_resume_reg;
                    if (!pba_mem_wr_en) begin
                        pba_mem_rd_en = 1'b1;
                        pba_mem_addr = scan_resume_reg >> 6;
                    end
                end
                irq_ready_next = 1'b1;
                state_next = STATE_IDLE;
            end
        end
        STATE_SEND_TLP: begin
            // MX-05: present the message, then wait until it is accepted or
            // withdrawn; the FSM owns it until then, so it cannot be lost.
            // If the controls dropped since READ_TBL_2, WAIT_TLP withdraws it
            // without presenting.
            tx_wr_req_tlp_data_next = vec_data_reg;
            tx_wr_req_tlp_hdr_next = tlp_hdr;
            tx_wr_req_tlp_valid_next = scan_allowed;
            state_next = STATE_WAIT_TLP;
        end
        STATE_WAIT_TLP: begin
            if (tx_wr_req_tlp_valid && tx_wr_req_tlp_ready) begin
                // accepted: delivered, clear the PBA bit
                pba_mem_wr_en = 1'b1;
                pba_mem_wr_data = pba_mem_rd_data_reg & ~(1 << (irq_index_reg & 6'h3F));

                // increment index so we don't check the same PBA bit immediately
                irq_index_next = irq_index_reg + 1;
                if (from_request_reg) begin
                    // MX-05: resume the scan where the request interrupted it
                    irq_index_next = scan_resume_reg;
                    if (!pba_mem_wr_en) begin
                        pba_mem_rd_en = 1'b1;
                        pba_mem_addr = scan_resume_reg >> 6;
                    end
                end

                irq_ready_next = 1'b1;
                state_next = STATE_IDLE;
            end else if (!scan_allowed || !tx_wr_req_tlp_valid) begin
                // withdrawn before acceptance (or never presented): re-pend while MSI-X stays
                // enabled (function mask or bus mastering off). If MSI-X is
                // disabled, a direct request is dropped and a scanned vector
                // keeps the pending bit it already had (PBA unchanged).
                tx_wr_req_tlp_valid_next = 1'b0;
                if (msix_enable_reg) begin
                    pba_mem_wr_en = 1'b1;
                    pba_mem_wr_data = pba_mem_rd_data_reg | (1 << (irq_index_reg & 6'h3F));
                end else if (from_request_reg) begin
                    pba_mem_wr_en = 1'b1;
                    pba_mem_wr_data = pba_mem_rd_data_reg & ~(1 << (irq_index_reg & 6'h3F));
                end
                irq_index_next = irq_index_reg + 1;
                if (from_request_reg) begin
                    // MX-05: resume the scan where the request interrupted it
                    irq_index_next = scan_resume_reg;
                    if (!pba_mem_wr_en) begin
                        pba_mem_rd_en = 1'b1;
                        pba_mem_addr = scan_resume_reg >> 6;
                    end
                end
                irq_ready_next = 1'b1;
                state_next = STATE_IDLE;
            end else begin
                state_next = STATE_WAIT_TLP;
            end
        end
        STATE_CLEAR: begin
            // reset sweep: every PBA word to 0 here, every table word to its
            // reset value (Mask=1, address/data 0) in the host write block
            pba_mem_wr_en = 1'b1;
            pba_mem_addr = clear_index_reg & ((2**PBA_ADDR_WIDTH)-1);
            pba_mem_wr_data = 0;
            clear_index_next = clear_index_reg + 1;
            if (&clear_index_reg) begin
                irq_index_next = 0;
                irq_ready_next = 1'b1;
                state_next = STATE_IDLE;
            end else begin
                state_next = STATE_CLEAR;
            end
        end
        default: begin
            state_next = STATE_IDLE;
        end
    endcase

    // MX-02b: a PBA write invalidates the cached word; a read refreshes it
    pba_word_valid_next = pba_mem_wr_en ? 1'b0 : (pba_mem_rd_en ? 1'b1 : pba_word_valid_reg);

    // MX-05: a scan turn is two scan steps: the first may only refresh the
    // PBA word, the second then tests a pending bit. Counting steps (rather
    // than looking at the next index) keeps this off the PBA scan's timing path.
    if (scan_turn) begin
        scan_stepped_next = !scan_stepped_reg;
        if (scan_stepped_reg) begin
            scan_owed_next = 1'b0;
        end
    end

    // MX-05: a clear request (link down/PERST, hot reset, FLR) aborts any
    // operation, withdraws a presented message and restarts the sweep
    if (msix_clear) begin
        state_next = STATE_CLEAR;
        clear_index_next = 0;
        tx_wr_req_tlp_valid_next = 1'b0;
        pba_mem_wr_en = 1'b0;
        irq_ready_next = 1'b0;
        scan_owed_next = 1'b0;
        scan_stepped_next = 1'b0;
        from_request_next = 1'b0;
    end
end

always @(posedge clk) begin
    state_reg <= state_next;

    irq_index_reg <= irq_index_next;
    pba_word_valid_reg <= pba_word_valid_next;

    vec_addr_reg <= vec_addr_next;
    vec_data_reg <= vec_data_next;
    vec_mask_reg <= vec_mask_next;

    irq_ready_reg <= irq_ready_next;

    tx_wr_req_tlp_data_reg <= tx_wr_req_tlp_data_next;
    tx_wr_req_tlp_hdr_reg <= tx_wr_req_tlp_hdr_next;
    tx_wr_req_tlp_valid_reg <= tx_wr_req_tlp_valid_next;

    msix_enable_reg <= msix_enable;
    msix_mask_reg <= msix_mask;
    bus_master_enable_reg <= bus_master_enable;
    clear_index_reg <= clear_index_next;
    scan_owed_reg <= scan_owed_next;
    scan_stepped_reg <= scan_stepped_next;
    scan_resume_reg <= scan_resume_next;
    from_request_reg <= from_request_next;

    if (tbl_mem_rd_en) begin
        tbl_mem_rd_data_reg <= tbl_mem[tbl_mem_addr];
    end

    if (pba_mem_wr_en) begin
        pba_mem[pba_mem_addr] <= pba_mem_wr_data;
    end else if (pba_mem_rd_en) begin
        pba_mem_rd_data_reg <= pba_mem[pba_mem_addr];
    end

    if (rst) begin
        state_reg <= STATE_IDLE;
        pba_word_valid_reg <= 1'b0;

        irq_ready_reg <= 1'b0;

        tx_wr_req_tlp_valid_reg <= 1'b0;
        scan_owed_reg <= 1'b0;
        scan_stepped_reg <= 1'b0;
        from_request_reg <= 1'b0;
        // MX-05: rst (which includes PCILeech's own soft reset) does not clear
        // the table or PBA, but must not abort a clear sweep in progress
        if (state_next == STATE_CLEAR) begin
            state_reg <= STATE_CLEAR;
        end
    end
end

// MX-04 host port: fixed two-clock read latency, one read and one write per clock
always @* begin
    for (i = 0; i < 8; i = i + 1) begin
        host_rd_word[8*i +: 8] = host_bypass_be_reg[i] ? host_bypass_data_reg[8*i +: 8] : host_tbl_rd_reg[8*i +: 8];
    end
    if (host_rd_pba_reg) begin
        host_rd_word = host_pba_rd_reg;
    end
end

always @(posedge clk) begin
    for (i = 0; i < 8; i = i + 1) begin
        if (tbl_wr_be[i]) begin
            tbl_mem[tbl_wr_addr][8*i +: 8] <= tbl_wr_data[8*i +: 8];
            tbl_host_mem[tbl_wr_addr][8*i +: 8] <= tbl_wr_data[8*i +: 8];
        end
    end

    // Clock 1: read both stores and note any same-clock write to the word.
    host_tbl_rd_reg <= tbl_host_mem[host_rd_index];
    host_pba_rd_reg <= pba_mem[host_rd_dw >> 1];
    host_rd_pba_reg <= host_rd_pba;
    host_rd_hi_reg <= host_rd_dw[0];
    host_bypass_be_reg <= (host_tbl_wr && host_rd_valid && !host_rd_pba && host_wr_index == host_rd_index) ? host_wr_be64 : 8'd0;
    host_bypass_data_reg <= host_wr_data64;

    // Clock 2: select the DWORD.
    host_rd_data_reg <= host_rd_hi_reg ? host_rd_word[63:32] : host_rd_word[31:0];
end

endmodule

`resetall
