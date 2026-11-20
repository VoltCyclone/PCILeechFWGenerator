//
// PCILeech FPGA - MSI-X message transmit (MX-06)
//
// Packs one MSI-X Memory Write from the vendored pcie_msix core into the
// PCILeech 128-bit TLP stream and merges it with the BAR completion stream
// (pcileech_tlps128_bar_rdengine output), so both leave on the BAR
// controller's single tlps_out input to pcileech_tlps128_sink_mux1.
//
// Stream protocol (from the consumers, pcileech_tlps128_sink_mux1 and
// pcileech_tlps128_dst64): a source behaves like a standard FIFO with a valid
// flag. tready in clock T requests one beat; the beat is presented with tvalid
// in clock T+1 and is consumed unconditionally. has_data means at least one
// whole packet is available. The mux locks onto a source once it sees has_data
// and releases it only on a beat with tlast, so once has_data is raised the
// packet must be delivered: a committed message cannot be withdrawn.
//
// Lane order (from pcileech_tlps128_dst64 and the BAR engines): DW n of a TLP
// is tdata[32*n +: 32] in PCIe bit order (fmt at [31:29]). Payload DWs carry
// byte 0 in [31:24], so the little-endian Message Data is byte swapped, as the
// read engine swaps completion data.
//
// Arbitration: at each packet boundary a committed message goes before a BAR
// completion. A BAR completion produced after a message was committed (a PBA
// read, for example) therefore never passes it (PCIe ordering: completions
// must not pass posted requests). Configuration completions use another
// upstream mux input with higher priority; they never show interrupt state.
// A new message is accepted only when the buffer is empty, which is never true
// at the boundary that ends a message, so a waiting completion always goes
// next: neither stream can starve the other.
//
// msg_ready is high whenever the one-message buffer is empty and msg_allow is
// high, so the core's presented message is committed in its first WAIT_TLP
// clock unless the previous message is still waiting for the stream.
//
// msg_allow must be the live (unregistered) send condition: MSI-X Enable,
// function unmasked, Bus Master Enable, and no msix_clear. The core sees those
// one clock late, so without this gate a message could be committed in the
// clock a clear arrives or just after bus mastering is turned off. Gating
// ready (not valid) keeps both sides in step: no handshake, no send, and the
// core then withdraws and re-pends or drops the message itself.
//
// A message already committed cannot be withdrawn (see has_data above), so it
// is sent even if the controls drop while it waits for the stream. For a
// clear (FLR and the other PCIe resets) the merger instead drains it first:
// clear_out, which drives the core's msix_clear, stays high until the buffer
// is empty. The core holds its sweep at the start while msix_clear is high and
// drops host table writes until the sweep ends, so a message from before the
// clear always leaves before any new table entry can be programmed. rst
// (which includes link down and hot reset) discards the buffer.
//

`timescale 1ns / 1ps

module pcileech_msix_tlp_tx (
    input               clk,
    input               rst,
    // from pcie_msix (TLP_HDR_WIDTH = 128, verilog-pcie header layout:
    // hdr[127:96] = DW0, hdr[95:64] = DW1, hdr[63:32] = DW2, hdr[31:0] = DW3)
    input  [127:0]      msg_hdr,
    input  [31:0]       msg_data,
    input               msg_valid,
    input               msg_allow,
    output              msg_ready,
    // PCIe-defined reset request in, held clear for the core out
    input               clear_req,
    output              clear_out,
    // completion stream source (pull, read latency 1)
    input  [127:0]      cpl_tdata,
    input  [3:0]        cpl_tkeepdw,
    input               cpl_tlast,
    input  [8:0]        cpl_tuser,
    input               cpl_tvalid,
    input               cpl_has_data,
    output              cpl_tready,
    // merged stream (same protocol)
    output [127:0]      out_tdata,
    output [3:0]        out_tkeepdw,
    output              out_tlast,
    output [8:0]        out_tuser,
    output              out_tvalid,
    output              out_has_data,
    input               out_tready
);

    localparam [1:0] SEL_NONE = 2'd0, SEL_MSG = 2'd1, SEL_CPL = 2'd2;

    // ------------------------------------------------------------------
    // One-message buffer: up to two beats (4DW header + 1 payload DW).
    // ------------------------------------------------------------------
    reg [127:0] beat0, beat1;
    reg         two_beats;
    reg [1:0]   msg_beats = 2'd0;   // beats not yet pulled
    reg         msg_next_is_1;      // the next beat to pull is beat1

    wire [31:0] data_bs = { msg_data[7:0], msg_data[15:8], msg_data[23:16], msg_data[31:24] };
    wire        hdr_4dw = msg_hdr[125];  // fmt[0]: 4DW header

    assign msg_ready = (msg_beats == 2'd0) && msg_allow && !rst;
    wire   msg_commit = msg_valid && msg_ready;
    wire   msg_has = msg_beats != 2'd0;

    reg    clear_pending = 1'b0;
    assign clear_out = clear_req || (clear_pending && msg_has);

    // ------------------------------------------------------------------
    // Packet-boundary arbitration (locked until the beat with tlast)
    // ------------------------------------------------------------------
    reg  [1:0]  sel = SEL_NONE;
    reg [127:0] o_tdata = 128'd0;
    reg [3:0]   o_tkeepdw = 4'd0;
    reg         o_tlast = 1'b0;
    reg         o_first = 1'b0;
    reg         o_valid = 1'b0;

    assign out_tvalid  = (sel == SEL_CPL) ? cpl_tvalid  : (sel == SEL_MSG) ? o_valid : 1'b0;
    assign out_tdata   = (sel == SEL_CPL) ? cpl_tdata   : (sel == SEL_MSG) ? o_tdata : 128'd0;
    assign out_tkeepdw = (sel == SEL_CPL) ? cpl_tkeepdw : (sel == SEL_MSG) ? o_tkeepdw : 4'd0;
    assign out_tlast   = (sel == SEL_CPL) ? cpl_tlast   : (sel == SEL_MSG) ? o_tlast : 1'b0;
    assign out_tuser   = (sel == SEL_CPL) ? cpl_tuser   :
                         (sel == SEL_MSG) ? { 7'd0, o_tlast, o_first } : 9'd0;

    wire [1:0] sel_new  = msg_has ? SEL_MSG : (cpl_has_data ? SEL_CPL : SEL_NONE);
    wire [1:0] sel_next = ((sel == SEL_NONE) || (out_tvalid && out_tlast)) ? sel_new : sel;

    wire msg_pull     = out_tready && (sel_next == SEL_MSG) && msg_has;
    assign cpl_tready = out_tready && (sel_next == SEL_CPL);
    assign out_has_data = msg_has || cpl_has_data;

    always @(posedge clk) begin
        if (rst) begin
            clear_pending <= clear_req;
            msg_beats <= 2'd0;
            sel       <= SEL_NONE;
            o_valid   <= 1'b0;
        end else begin
            sel     <= sel_next;
            o_valid <= msg_pull;
            clear_pending <= clear_req || (clear_pending && msg_has);
            if (msg_pull) begin
                o_tdata   <= msg_next_is_1 ? beat1 : beat0;
                o_tkeepdw <= msg_next_is_1 ? 4'b0001 : 4'b1111;
                o_tlast   <= msg_next_is_1 || !two_beats;
                o_first   <= !msg_next_is_1;
                msg_beats <= msg_beats - 2'd1;
                msg_next_is_1 <= 1'b1;
            end
            if (msg_commit) begin
                two_beats     <= hdr_4dw;
                msg_beats     <= hdr_4dw ? 2'd2 : 2'd1;
                msg_next_is_1 <= 1'b0;
                if (hdr_4dw) begin
                    beat0 <= { msg_hdr[31:0], msg_hdr[63:32], msg_hdr[95:64], msg_hdr[127:96] };
                    beat1 <= { 96'd0, data_bs };
                end else begin
                    beat0 <= { data_bs, msg_hdr[63:32], msg_hdr[95:64], msg_hdr[127:96] };
                    beat1 <= 128'd0;
                end
            end
        end
    end

`ifndef SYNTHESIS
    // A commit can only happen with an empty buffer, so it never overlaps a pull.
    always @(posedge clk) begin
        if (!rst && msg_commit && msg_pull) $fatal(1, "msix_tlp_tx: commit overlapped a pull");
        // A completion beat arrives only in response to cpl_tready, which is
        // given only to the selected source; anything else would be dropped.
        if (!rst && cpl_tvalid && sel != SEL_CPL) $fatal(1, "msix_tlp_tx: completion beat while not selected");
    end
`endif

endmodule
