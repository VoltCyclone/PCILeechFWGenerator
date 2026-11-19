# flake8: noqa: E501  (generated exact-text hunks)
"""Local modifications of the vendored pcie_msix.v after MX-02, as exact hunks.

Each entry is (text after the layer, text before it). Reverting the layers
newest first, then the MX-02 patches in test_vendored_pcie_msix.py, reproduces
the pinned upstream file. Generated with difflib; edit together with the core.
"""

HOST_PORT_HUNKS = [
    (
        "    // TLP interface configuration\n",
        "    // AXI-lite interface configuration\n    parameter AXIL_DATA_WIDTH = 32,\n    parameter AXIL_ADDR_WIDTH = IRQ_INDEX_WIDTH+5,\n    parameter AXIL_STRB_WIDTH = (AXIL_DATA_WIDTH/8),\n\n    // TLP interface configuration\n",
    ),
    (
        "     * MX-04: host access port for the MSI-X table and PBA (replaces the\n     * upstream AXI-lite slave). Accepts one read and one write every clock,\n     * with no backpressure. Addresses are DWORD indices within the table\n     * (pba=0) or the PBA (pba=1); PBA writes are ignored. host_rd_data is\n     * valid exactly two clocks after host_rd_valid.\n     */\n    input  wire                        host_wr_valid,\n    input  wire                        host_wr_pba,\n    input  wire [IRQ_INDEX_WIDTH+1:0]  host_wr_dw,\n    input  wire [31:0]                 host_wr_data,\n    input  wire [3:0]                  host_wr_be,\n    input  wire                        host_rd_valid,\n    input  wire                        host_rd_pba,\n    input  wire [IRQ_INDEX_WIDTH+1:0]  host_rd_dw,\n    output wire [31:0]                 host_rd_data,\n\n",
        "     * AXI lite interface for MSI-X tables\n     */\n    input  wire [AXIL_ADDR_WIDTH-1:0]  s_axil_awaddr,\n    input  wire [2:0]                  s_axil_awprot,\n    input  wire                        s_axil_awvalid,\n    output wire                        s_axil_awready,\n    input  wire [AXIL_DATA_WIDTH-1:0]  s_axil_wdata,\n    input  wire [AXIL_STRB_WIDTH-1:0]  s_axil_wstrb,\n    input  wire                        s_axil_wvalid,\n    output wire                        s_axil_wready,\n    output wire [1:0]                  s_axil_bresp,\n    output wire                        s_axil_bvalid,\n    input  wire                        s_axil_bready,\n    input  wire [AXIL_ADDR_WIDTH-1:0]  s_axil_araddr,\n    input  wire [2:0]                  s_axil_arprot,\n    input  wire                        s_axil_arvalid,\n    output wire                        s_axil_arready,\n    output wire [AXIL_DATA_WIDTH-1:0]  s_axil_rdata,\n    output wire [1:0]                  s_axil_rresp,\n    output wire                        s_axil_rvalid,\n    input  wire                        s_axil_rready,\n\n",
    ),
    (
        "// bus width assertions\ninitial begin\n    if (IRQ_INDEX_WIDTH > 11) begin\n",
        'parameter INDEX_SHIFT = $clog2(64/8);\nparameter WORD_SELECT_SHIFT = $clog2(AXIL_DATA_WIDTH/8);\nparameter WORD_SELECT_WIDTH = 64 > AXIL_DATA_WIDTH ? $clog2((64+7)/8) - $clog2(AXIL_DATA_WIDTH/8) : 0;\n\n// bus width assertions\ninitial begin\n    if (AXIL_STRB_WIDTH * 8 != AXIL_DATA_WIDTH) begin\n        $error("Error: AXI lite interface requires byte (8-bit) granularity (instance %m)");\n        $finish;\n    end\n\n    if (AXIL_DATA_WIDTH > 64) begin\n        $error("Error: AXI lite data width must be 64 or less (instance %m)");\n        $finish;\n    end\n\n    if (AXIL_ADDR_WIDTH < IRQ_INDEX_WIDTH+5) begin\n        $error("Error: AXI lite address width too narrow (instance %m)");\n        $finish;\n    end\n\n    if (IRQ_INDEX_WIDTH > 11) begin\n',
    ),
    (
        "reg [127:0] tlp_hdr;\n\n",
        "reg last_read_reg = 1'b0, last_read_next;\n\nreg [127:0] tlp_hdr;\n\nreg read_eligible;\nreg write_eligible;\n\nreg tbl_axil_mem_rd_en;\nreg tbl_axil_mem_wr_en;\nreg [7:0] tbl_axil_mem_wr_be;\nreg [63:0] tbl_axil_mem_wr_data;\nreg pba_axil_mem_rd_en;\n\n",
    ),
    (
        "\nreg irq_ready_reg = 1'b0, irq_ready_next;\n",
        "\nreg s_axil_awready_reg = 1'b0, s_axil_awready_next;\nreg s_axil_wready_reg = 1'b0, s_axil_wready_next;\nreg s_axil_bvalid_reg = 1'b0, s_axil_bvalid_next;\nreg s_axil_arready_reg = 1'b0, s_axil_arready_next;\nreg [AXIL_DATA_WIDTH-1:0] s_axil_rdata_reg = {AXIL_DATA_WIDTH{1'b0}}, s_axil_rdata_next;\nreg s_axil_rvalid_reg = 1'b0, s_axil_rvalid_next;\n\nreg irq_ready_reg = 1'b0, irq_ready_next;\n",
    ),
    (
        "// MX-04: host-read copy of the table. Every host write updates both copies in\n// the same clock; the scan reads tbl_mem and the host reads tbl_host_mem, so\n// neither memory needs more than one write and one read port.\nreg [63:0] tbl_host_mem[(2**TBL_ADDR_WIDTH)-1:0];\n\n",
        "\n",
    ),
    (
        "reg [63:0] tbl_mem_rd_data_reg = 0;\n",
        "reg tbl_rd_data_valid_reg = 1'b0, tbl_rd_data_valid_next;\nreg pba_rd_data_valid_reg = 1'b0, pba_rd_data_valid_next;\nreg [WORD_SELECT_WIDTH-1:0] rd_data_shift_reg = 0, rd_data_shift_next;\n\nreg [63:0] tbl_mem_rd_data_reg = 0;\n",
    ),
    (
        "\n// MX-04 host port: the table word is (DWORD index >> 1); bit 0 picks the half.\nwire [TBL_ADDR_WIDTH-1:0] host_wr_index = host_wr_dw >> 1;\nwire [TBL_ADDR_WIDTH-1:0] host_rd_index = host_rd_dw >> 1;\nwire host_tbl_wr = host_wr_valid && !host_wr_pba;\nwire [7:0] host_wr_be64 = host_wr_dw[0] ? {host_wr_be, 4'b0000} : {4'b0000, host_wr_be};\nwire [63:0] host_wr_data64 = {host_wr_data, host_wr_data};\n\nreg [63:0] host_tbl_rd_reg = 0;\nreg [63:0] host_pba_rd_reg = 0;\nreg host_rd_pba_reg = 1'b0;\nreg host_rd_hi_reg = 1'b0;\n// Bytes of a same-clock table write to the word being read: the read sees them.\nreg [7:0] host_bypass_be_reg = 0;\nreg [63:0] host_bypass_data_reg = 0;\nreg [63:0] host_rd_word;\nreg [31:0] host_rd_data_reg = 0;\n\nassign host_rd_data = host_rd_data_reg;\n\n",
        "reg [63:0] tbl_axil_mem_rd_data_reg = 0;\nreg [63:0] pba_axil_mem_rd_data_reg = 0;\n\nwire [TBL_ADDR_WIDTH-1:0] s_axil_awaddr_index = s_axil_awaddr >> INDEX_SHIFT;\nwire [WORD_SELECT_WIDTH-1:0] s_axil_awaddr_word = AXIL_DATA_WIDTH < 64 ? s_axil_awaddr >> WORD_SELECT_SHIFT : 0;\n\nwire [TBL_ADDR_WIDTH-1:0] s_axil_araddr_index = s_axil_araddr >> INDEX_SHIFT;\nwire [WORD_SELECT_WIDTH-1:0] s_axil_araddr_word = AXIL_DATA_WIDTH < 64 ? s_axil_araddr >> WORD_SELECT_SHIFT : 0;\n\nassign s_axil_awready = s_axil_awready_reg;\nassign s_axil_wready = s_axil_wready_reg;\nassign s_axil_bresp = 2'b00;\nassign s_axil_bvalid = s_axil_bvalid_reg;\nassign s_axil_arready = s_axil_arready_reg;\nassign s_axil_rdata = s_axil_rdata_reg;\nassign s_axil_rresp = 2'b00;\nassign s_axil_rvalid = s_axil_rvalid_reg;\n\n",
    ),
    (
        "        tbl_host_mem[i] = (i & 1) ? 64'h0000000100000000 : 64'd0;\n    end\n",
        "    end\n",
    ),
    (
        "// MX-04 host port: fixed two-clock read latency, one read and one write per clock\nalways @* begin\n    for (i = 0; i < 8; i = i + 1) begin\n        host_rd_word[8*i +: 8] = host_bypass_be_reg[i] ? host_bypass_data_reg[8*i +: 8] : host_tbl_rd_reg[8*i +: 8];\n    end\n    if (host_rd_pba_reg) begin\n        host_rd_word = host_pba_rd_reg;\n    end\n",
        "// AXI lite interface\nalways @* begin\n    tbl_axil_mem_rd_en = 1'b0;\n    tbl_axil_mem_wr_en = 1'b0;\n    tbl_axil_mem_wr_be = s_axil_wstrb << (s_axil_awaddr_word * AXIL_STRB_WIDTH);\n    tbl_axil_mem_wr_data = {2**WORD_SELECT_WIDTH{s_axil_wdata}};\n    pba_axil_mem_rd_en = 1'b0;\n\n    tbl_rd_data_valid_next = tbl_rd_data_valid_reg;\n    pba_rd_data_valid_next = pba_rd_data_valid_reg;\n    rd_data_shift_next = rd_data_shift_reg;\n\n    last_read_next = last_read_reg;\n\n    s_axil_awready_next = 1'b0;\n    s_axil_wready_next = 1'b0;\n    s_axil_bvalid_next = s_axil_bvalid_reg && !s_axil_bready;\n\n    s_axil_arready_next = 1'b0;\n    s_axil_rdata_next = s_axil_rdata_reg;\n    s_axil_rvalid_next = s_axil_rvalid_reg && !s_axil_rready;\n\n    write_eligible = s_axil_awvalid && s_axil_wvalid && (!s_axil_bvalid || s_axil_bready) && (!s_axil_awready && !s_axil_wready);\n    read_eligible = s_axil_arvalid && (!s_axil_rvalid || s_axil_rready || !(tbl_rd_data_valid_reg || pba_rd_data_valid_reg)) && (!s_axil_arready);\n\n    if ((tbl_rd_data_valid_reg || pba_rd_data_valid_reg) && (!s_axil_rvalid || s_axil_rready)) begin\n        s_axil_rvalid_next = 1'b1;\n        tbl_rd_data_valid_next = 1'b0;\n        pba_rd_data_valid_next = 1'b0;\n\n        if (tbl_rd_data_valid_reg) begin\n            if (AXIL_DATA_WIDTH < 64) begin\n                s_axil_rdata_next = tbl_axil_mem_rd_data_reg >> rd_data_shift_reg*AXIL_DATA_WIDTH;\n            end else begin\n                s_axil_rdata_next = tbl_axil_mem_rd_data_reg;\n            end\n        end else begin\n            if (AXIL_DATA_WIDTH < 64) begin\n                s_axil_rdata_next = pba_axil_mem_rd_data_reg >> rd_data_shift_reg*AXIL_DATA_WIDTH;\n            end else begin\n                s_axil_rdata_next = pba_axil_mem_rd_data_reg;\n            end\n        end\n    end\n\n    if (write_eligible && (!read_eligible || last_read_reg)) begin\n        last_read_next = 1'b0;\n\n        s_axil_awready_next = 1'b1;\n        s_axil_wready_next = 1'b1;\n        s_axil_bvalid_next = 1'b1;\n\n        if (s_axil_awaddr[AXIL_ADDR_WIDTH-1] == 0) begin\n            tbl_axil_mem_wr_en = 1'b1;\n        end\n    end else if (read_eligible) begin\n        last_read_next = 1'b1;\n\n        s_axil_arready_next = 1'b1;\n\n        rd_data_shift_next = s_axil_araddr_word;\n\n        if (s_axil_araddr[AXIL_ADDR_WIDTH-1] == 0) begin\n            tbl_axil_mem_rd_en = 1'b1;\n            tbl_rd_data_valid_next = 1'b1;\n        end else begin\n            pba_axil_mem_rd_en = 1'b1;\n            pba_rd_data_valid_next = 1'b1;\n        end\n    end\n",
    ),
    (
        "    for (i = 0; i < 8; i = i + 1) begin\n        if (host_tbl_wr && host_wr_be64[i]) begin\n            tbl_mem[host_wr_index][8*i +: 8] <= host_wr_data64[8*i +: 8];\n            tbl_host_mem[host_wr_index][8*i +: 8] <= host_wr_data64[8*i +: 8];\n        end\n    end\n\n    // Clock 1: read both stores and note any same-clock write to the word.\n    host_tbl_rd_reg <= tbl_host_mem[host_rd_index];\n    host_pba_rd_reg <= pba_mem[host_rd_dw >> 1];\n    host_rd_pba_reg <= host_rd_pba;\n    host_rd_hi_reg <= host_rd_dw[0];\n    host_bypass_be_reg <= (host_tbl_wr && host_rd_valid && !host_rd_pba && host_wr_index == host_rd_index) ? host_wr_be64 : 8'd0;\n    host_bypass_data_reg <= host_wr_data64;\n\n    // Clock 2: select the DWORD.\n    host_rd_data_reg <= host_rd_hi_reg ? host_rd_word[63:32] : host_rd_word[31:0];\nend\n",
        "    tbl_rd_data_valid_reg <= tbl_rd_data_valid_next;\n    pba_rd_data_valid_reg <= pba_rd_data_valid_next;\n    rd_data_shift_reg <= rd_data_shift_next;\n\n    last_read_reg <= last_read_next;\n\n    s_axil_awready_reg <= s_axil_awready_next;\n    s_axil_wready_reg <= s_axil_wready_next;\n    s_axil_bvalid_reg <= s_axil_bvalid_next;\n\n    s_axil_arready_reg <= s_axil_arready_next;\n    s_axil_rdata_reg <= s_axil_rdata_next;\n    s_axil_rvalid_reg <= s_axil_rvalid_next;\n\n    if (tbl_axil_mem_rd_en) begin\n        tbl_axil_mem_rd_data_reg <= tbl_mem[s_axil_araddr_index];\n    end else begin\n        for (i = 0; i < 8; i = i + 1) begin\n            if (tbl_axil_mem_wr_en && tbl_axil_mem_wr_be[i]) begin\n                tbl_mem[s_axil_awaddr_index][8*i +: 8] <= tbl_axil_mem_wr_data[8*i +: 8];\n            end\n        end\n    end\n\n    if (pba_axil_mem_rd_en) begin\n        pba_axil_mem_rd_data_reg <= pba_mem[s_axil_araddr_index];\n    end\n\n    if (rst) begin\n        tbl_rd_data_valid_reg <= 1'b0;\n        pba_rd_data_valid_reg <= 1'b0;\n        last_read_reg <= 1'b0;\n\n        s_axil_awready_reg <= 1'b0;\n        s_axil_wready_reg <= 1'b0;\n        s_axil_bvalid_reg <= 1'b0;\n\n        s_axil_arready_reg <= 1'b0;\n        s_axil_rvalid_reg <= 1'b0;\n    end\nend\n",
    ),
]

LIVE_CONTROL_HUNKS = [
    (
        "    input  wire                        msix_mask,\n\n    /*\n     * MX-05: live controls and reset (contracts in README.md)\n     */\n    input  wire                        bus_master_enable,\n    input  wire                        msix_clear,\n    output wire                        msix_clearing\n);\n",
        "    input  wire                        msix_mask\n);\n",
    ),
    (
        "localparam [2:0]\n    STATE_IDLE = 3'd0,\n    STATE_READ_TBL_1 = 3'd1,\n    STATE_READ_TBL_2 = 3'd2,\n    STATE_SEND_TLP = 3'd3,\n    STATE_WAIT_TLP = 3'd4, // MX-05: message presented, awaiting acceptance or withdrawal\n    STATE_CLEAR = 3'd5;    // MX-05: table and PBA reset sweep\n\nreg [2:0] state_reg = STATE_IDLE, state_next;\n\n",
        "localparam [1:0]\n    STATE_IDLE = 2'd0,\n    STATE_READ_TBL_1 = 2'd1,\n    STATE_READ_TBL_2 = 2'd2,\n    STATE_SEND_TLP = 2'd3;\n\nreg [1:0] state_reg = STATE_IDLE, state_next;\n\n",
    ),
    (
        "reg bus_master_enable_reg = 1'b0;\n// MX-05: messages may be sent only with MSI-X enabled, unmasked and bus mastering\nwire scan_allowed = msix_enable_reg && !msix_mask_reg && bus_master_enable_reg;\n// MX-05: clear sweep index over table words; PBA words use its low bits\nreg [TBL_ADDR_WIDTH-1:0] clear_index_reg = 0, clear_index_next;\n// MX-05: after a request is accepted, the next idle step belongs to the PBA scan\nreg scan_owed_reg = 1'b0, scan_owed_next;\n// MX-05: a request does not move the scan; it resumes from scan_resume_reg\nreg [IRQ_INDEX_WIDTH-1:0] scan_resume_reg = 0, scan_resume_next;\nreg from_request_reg = 1'b0, from_request_next;\nreg scan_turn;\n// MX-05: the scan has taken one step of its turn (the first may only refresh\n// the PBA word, so at most two steps are owed)\nreg scan_stepped_reg = 1'b0, scan_stepped_next;\n\n",
        "\n",
    ),
    (
        "wire host_tbl_wr = host_wr_valid && !host_wr_pba && state_reg != STATE_CLEAR;\nwire [7:0] host_wr_be64 = host_wr_dw[0] ? {host_wr_be, 4'b0000} : {4'b0000, host_wr_be};\nwire [63:0] host_wr_data64 = {host_wr_data, host_wr_data};\n// MX-05: one byte-enabled write port shared by host writes and the reset sweep\n// (host table writes are dropped while the sweep runs), so RAM inference holds\nwire tbl_wr_clear = state_reg == STATE_CLEAR;\nwire [TBL_ADDR_WIDTH-1:0] tbl_wr_addr = tbl_wr_clear ? clear_index_reg : host_wr_index;\nwire [63:0] tbl_wr_data = tbl_wr_clear ? (clear_index_reg[0] ? 64'h0000000100000000 : 64'd0) : host_wr_data64;\nwire [7:0] tbl_wr_be = tbl_wr_clear ? 8'hFF : (host_tbl_wr ? host_wr_be64 : 8'h00);\n\n",
        "wire host_tbl_wr = host_wr_valid && !host_wr_pba;\nwire [7:0] host_wr_be64 = host_wr_dw[0] ? {host_wr_be, 4'b0000} : {4'b0000, host_wr_be};\nwire [63:0] host_wr_data64 = {host_wr_data, host_wr_data};\n\n",
    ),
    (
        "assign irq_ready = irq_ready_reg && !(scan_owed_reg && scan_allowed);\nassign msix_clearing = state_reg == STATE_CLEAR;\n\n",
        "assign irq_ready = irq_ready_reg;\n\n",
    ),
    (
        "    clear_index_next = clear_index_reg;\n    scan_owed_next = scan_owed_reg;\n    scan_resume_next = scan_resume_reg;\n    from_request_next = from_request_reg;\n    scan_turn = 1'b0;\n    scan_stepped_next = scan_stepped_reg;\n\n",
        "\n",
    ),
    (
        "                // MX-05: remember the scan position; the scan gets the next step\n                scan_owed_next = 1'b1;\n                scan_resume_next = irq_index_reg;\n                from_request_next = 1'b1;\n\n",
        "\n",
    ),
    (
        "            end else if ((!irq_valid || scan_owed_reg) && scan_allowed) begin\n                // MX-05: a waiting request yields one step to the scan, so\n                // continuous requests cannot starve pending vectors\n                scan_turn = 1'b1;\n                from_request_next = 1'b0;\n                // no new request waiting, scan PBA for masked requests\n",
        "            end else if (!irq_valid && msix_enable_reg && !msix_mask_reg) begin\n                // no new request waiting, scan PBA for masked requests\n",
    ),
    (
        "            if (scan_allowed && !vec_mask_next) begin\n                // send TLP\n                state_next = STATE_SEND_TLP;\n            end else if (!msix_enable_reg) begin\n                // MX-05: a request while MSI-X is disabled is dropped, not recorded\n                if (from_request_reg) begin\n                    // MX-05: resume the scan where the request interrupted it\n                    irq_index_next = scan_resume_reg;\n                    if (!pba_mem_wr_en) begin\n                        pba_mem_rd_en = 1'b1;\n                        pba_mem_addr = scan_resume_reg >> 6;\n                    end\n                end\n                irq_ready_next = 1'b1;\n                state_next = STATE_IDLE;\n            end else begin\n",
        "            if (msix_enable_reg && !msix_mask_reg && !vec_mask_next) begin\n                // send TLP\n                state_next = STATE_SEND_TLP;\n            end else begin\n",
    ),
    (
        "                if (from_request_reg) begin\n                    // MX-05: resume the scan where the request interrupted it\n                    irq_index_next = scan_resume_reg;\n                    if (!pba_mem_wr_en) begin\n                        pba_mem_rd_en = 1'b1;\n                        pba_mem_addr = scan_resume_reg >> 6;\n                    end\n                end\n                irq_ready_next = 1'b1;\n                state_next = STATE_IDLE;\n            end\n",
        "                irq_ready_next = 1'b1;\n                state_next = STATE_IDLE;\n            end\n",
    ),
    (
        "            // MX-05: present the message, then wait until it is accepted or\n            // withdrawn; the FSM owns it until then, so it cannot be lost.\n            // If the controls dropped since READ_TBL_2, WAIT_TLP withdraws it\n            // without presenting.\n            tx_wr_req_tlp_data_next = vec_data_reg;\n            tx_wr_req_tlp_hdr_next = tlp_hdr;\n            tx_wr_req_tlp_valid_next = scan_allowed;\n            state_next = STATE_WAIT_TLP;\n        end\n        STATE_WAIT_TLP: begin\n            if (tx_wr_req_tlp_valid && tx_wr_req_tlp_ready) begin\n                // accepted: delivered, clear the PBA bit\n                pba_mem_wr_en = 1'b1;\n",
        "            if (!tx_wr_req_tlp_valid || tx_wr_req_tlp_ready) begin\n                // send TLP\n                tx_wr_req_tlp_data_next = vec_data_reg;\n                tx_wr_req_tlp_hdr_next = tlp_hdr;\n                tx_wr_req_tlp_valid_next = 1'b1;\n\n                // clear PBA bit\n                pba_mem_wr_en = 1'b1;\n",
    ),
    (
        "                if (from_request_reg) begin\n                    // MX-05: resume the scan where the request interrupted it\n                    irq_index_next = scan_resume_reg;\n                    if (!pba_mem_wr_en) begin\n                        pba_mem_rd_en = 1'b1;\n                        pba_mem_addr = scan_resume_reg >> 6;\n                    end\n                end\n\n                irq_ready_next = 1'b1;\n                state_next = STATE_IDLE;\n            end else if (!scan_allowed || !tx_wr_req_tlp_valid) begin\n                // withdrawn before acceptance (or never presented): re-pend while MSI-X stays\n                // enabled (function mask or bus mastering off). If MSI-X is\n                // disabled, a direct request is dropped and a scanned vector\n                // keeps the pending bit it already had (PBA unchanged).\n                tx_wr_req_tlp_valid_next = 1'b0;\n                if (msix_enable_reg) begin\n                    pba_mem_wr_en = 1'b1;\n                    pba_mem_wr_data = pba_mem_rd_data_reg | (1 << (irq_index_reg & 6'h3F));\n                end else if (from_request_reg) begin\n                    pba_mem_wr_en = 1'b1;\n                    pba_mem_wr_data = pba_mem_rd_data_reg & ~(1 << (irq_index_reg & 6'h3F));\n                end\n                irq_index_next = irq_index_reg + 1;\n                if (from_request_reg) begin\n                    // MX-05: resume the scan where the request interrupted it\n                    irq_index_next = scan_resume_reg;\n                    if (!pba_mem_wr_en) begin\n                        pba_mem_rd_en = 1'b1;\n                        pba_mem_addr = scan_resume_reg >> 6;\n                    end\n                end\n                irq_ready_next = 1'b1;\n                state_next = STATE_IDLE;\n            end else begin\n                state_next = STATE_WAIT_TLP;\n            end\n        end\n        STATE_CLEAR: begin\n            // reset sweep: every PBA word to 0 here, every table word to its\n            // reset value (Mask=1, address/data 0) in the host write block\n            pba_mem_wr_en = 1'b1;\n            pba_mem_addr = clear_index_reg & ((2**PBA_ADDR_WIDTH)-1);\n            pba_mem_wr_data = 0;\n            clear_index_next = clear_index_reg + 1;\n            if (&clear_index_reg) begin\n                irq_index_next = 0;\n                irq_ready_next = 1'b1;\n                state_next = STATE_IDLE;\n            end else begin\n                state_next = STATE_CLEAR;\n            end\n        end\n        default: begin\n            state_next = STATE_IDLE;\n        end\n",
        "\n                irq_ready_next = 1'b1;\n                state_next = STATE_IDLE;\n            end else begin\n                state_next = STATE_SEND_TLP;\n            end\n        end\n",
    ),
    (
        "\n    // MX-05: a scan turn is two scan steps: the first may only refresh the\n    // PBA word, the second then tests a pending bit. Counting steps (rather\n    // than looking at the next index) keeps this off the PBA scan's timing path.\n    if (scan_turn) begin\n        scan_stepped_next = !scan_stepped_reg;\n        if (scan_stepped_reg) begin\n            scan_owed_next = 1'b0;\n        end\n    end\n\n    // MX-05: a clear request (link down/PERST, hot reset, FLR) aborts any\n    // operation, withdraws a presented message and restarts the sweep\n    if (msix_clear) begin\n        state_next = STATE_CLEAR;\n        clear_index_next = 0;\n        tx_wr_req_tlp_valid_next = 1'b0;\n        pba_mem_wr_en = 1'b0;\n        irq_ready_next = 1'b0;\n        scan_owed_next = 1'b0;\n        scan_stepped_next = 1'b0;\n        from_request_next = 1'b0;\n    end\nend\n",
        "end\n",
    ),
    (
        "    bus_master_enable_reg <= bus_master_enable;\n    clear_index_reg <= clear_index_next;\n    scan_owed_reg <= scan_owed_next;\n    scan_stepped_reg <= scan_stepped_next;\n    scan_resume_reg <= scan_resume_next;\n    from_request_reg <= from_request_next;\n\n",
        "\n",
    ),
    (
        "        scan_owed_reg <= 1'b0;\n        scan_stepped_reg <= 1'b0;\n        from_request_reg <= 1'b0;\n        // MX-05: rst (which includes PCILeech's own soft reset) does not clear\n        // the table or PBA, but must not abort a clear sweep in progress\n        if (state_next == STATE_CLEAR) begin\n            state_reg <= STATE_CLEAR;\n        end\n    end\n",
        "    end\n",
    ),
    (
        "        if (tbl_wr_be[i]) begin\n            tbl_mem[tbl_wr_addr][8*i +: 8] <= tbl_wr_data[8*i +: 8];\n            tbl_host_mem[tbl_wr_addr][8*i +: 8] <= tbl_wr_data[8*i +: 8];\n        end\n",
        "        if (host_tbl_wr && host_wr_be64[i]) begin\n            tbl_mem[host_wr_index][8*i +: 8] <= host_wr_data64[8*i +: 8];\n            tbl_host_mem[host_wr_index][8*i +: 8] <= host_wr_data64[8*i +: 8];\n        end\n",
    ),
]
