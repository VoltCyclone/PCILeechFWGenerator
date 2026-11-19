//
// PCILeech MSI-X BAR router (MX-04)
//
// Sits in the served BAR's slot of pcileech_tlps128_bar_controller. Each DWORD
// read or write has exactly one owner: the MSI-X table window, the PBA window,
// or the BAR's register implementation ("inner"). Windows come from the
// validated donor layout (src/device_clone/msix_layout.py, decode_parameters):
// inclusive first/last byte offsets bounded by the real vector count, so
// padded entries of the power-of-two core are never reachable.
//
// Capacity: the BAR engines issue at most one read and one write per clock and
// cannot be stalled. Both owners accept one of each per clock and answer reads
// exactly two clocks later (pcileech_bar_impl_device and
// pcileech_bar_impl_zerowrite4k do too), so responses leave in request order
// and never collide. On reset the router drops in-flight MSI-X reads, as
// pcileech_bar_impl_device does; zerowrite4k still answers reads issued during
// reset. A read and a write of one DWORD in the same clock have no defined
// order from the engines: the MSI-X core returns the new bytes,
// pcileech_bar_impl_device returns the old ones. A latency mismatch is a design error and stops simulation.
//

`timescale 1ns / 1ps

module pcileech_msix_bar_router #(
    parameter [32:0] BAR_SIZE = 4096,    // power of two up to 4 GiB, as the inner implementation's BAR_SIZE
    parameter IRQ_INDEX_WIDTH = 1,
    parameter [31:0] TABLE_FIRST = 32'h0,
    parameter [31:0] TABLE_LAST = 32'hF,
    parameter [31:0] PBA_FIRST = 32'h800,
    parameter [31:0] PBA_LAST = 32'h807
)(
    input               rst,
    input               clk,
    // from the BAR controller
    input [31:0]        wr_addr,
    input [3:0]         wr_be,
    input [31:0]        wr_data,
    input               wr_valid,
    input [87:0]        rd_req_ctx,
    input [31:0]        rd_req_addr,
    input               rd_req_valid,
    output [87:0]       rd_rsp_ctx,
    output [31:0]       rd_rsp_data,
    output              rd_rsp_valid,
    // to the inner register implementation (address/data buses are shared)
    output              inner_wr_valid,
    output              inner_rd_req_valid,
    input [87:0]        inner_rd_rsp_ctx,
    input [31:0]        inner_rd_rsp_data,
    input               inner_rd_rsp_valid,
    // to the MSI-X core host port
    output                       host_wr_valid,
    output                       host_wr_pba,
    output [IRQ_INDEX_WIDTH+1:0] host_wr_dw,
    output [31:0]                host_wr_data,
    output [3:0]                 host_wr_be,
    output                       host_rd_valid,
    output                       host_rd_pba,
    output [IRQ_INDEX_WIDTH+1:0] host_rd_dw,
    input [31:0]                 host_rd_data
);
    localparam [32:0] BAR_LAST = BAR_SIZE - 33'd1;
    localparam [31:0] OFFSET_MASK = BAR_LAST[31:0] & 32'hFFFFFFFC;

    wire [31:0] wr_off = wr_addr & OFFSET_MASK;
    wire [31:0] rd_off = rd_req_addr & OFFSET_MASK;

    wire wr_tbl = (wr_off >= TABLE_FIRST) && (wr_off <= TABLE_LAST);
    wire wr_pba = (wr_off >= PBA_FIRST) && (wr_off <= PBA_LAST);
    wire rd_tbl = (rd_off >= TABLE_FIRST) && (rd_off <= TABLE_LAST);
    wire rd_pba = (rd_off >= PBA_FIRST) && (rd_off <= PBA_LAST);

    wire [31:0] wr_rel = wr_off - (wr_pba ? PBA_FIRST : TABLE_FIRST);
    wire [31:0] rd_rel = rd_off - (rd_pba ? PBA_FIRST : TABLE_FIRST);

    assign inner_wr_valid     = wr_valid && !wr_tbl && !wr_pba;
    assign inner_rd_req_valid = rd_req_valid && !rd_tbl && !rd_pba;

    // PBA writes reach the core, which ignores them (no other owner may answer).
    assign host_wr_valid = wr_valid && (wr_tbl || wr_pba);
    assign host_wr_pba   = wr_pba;
    assign host_wr_dw    = wr_rel[IRQ_INDEX_WIDTH+3:2];
    assign host_wr_data  = wr_data;
    assign host_wr_be    = wr_be;
    assign host_rd_valid = rd_req_valid && (rd_tbl || rd_pba);
    assign host_rd_pba   = rd_pba;
    assign host_rd_dw    = rd_rel[IRQ_INDEX_WIDTH+3:2];

    // Carry the context of MSI-X reads alongside the core's two-clock latency.
    reg [87:0] msix_ctx_d1, msix_ctx_d2;
    reg        msix_rd_d1, msix_rd_d2;
    always @(posedge clk) begin
        if (rst) begin
            msix_rd_d1 <= 1'b0;
            msix_rd_d2 <= 1'b0;
        end
        else begin
            msix_rd_d1 <= host_rd_valid;
            msix_rd_d2 <= msix_rd_d1;
        end
        msix_ctx_d1 <= rd_req_ctx;
        msix_ctx_d2 <= msix_ctx_d1;
    end

    assign rd_rsp_valid = msix_rd_d2 || inner_rd_rsp_valid;
    assign rd_rsp_ctx   = msix_rd_d2 ? msix_ctx_d2 : inner_rd_rsp_ctx;
    assign rd_rsp_data  = msix_rd_d2 ? host_rd_data : inner_rd_rsp_data;

`ifndef SYNTHESIS
    always @(posedge clk)
        if (!rst && msix_rd_d2 && inner_rd_rsp_valid)
            $fatal(1, "pcileech_msix_bar_router: inner and MSI-X responses collided (latency mismatch)");
`endif
endmodule
