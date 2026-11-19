# Vendored: verilog-pcie `pcie_msix.v`

MSI-X table/PBA storage and interrupt message generation, used by the MSI-X
subsystem summarized in `docs/development/MSIX_STATUS.md`.

| Item | Value |
| --- | --- |
| Upstream | <https://github.com/alexforencich/verilog-pcie> |
| Revision | `25156a9a162c41c60f11f41590c7d006d015ae5a` |
| File | `rtl/pcie_msix.v` |
| Upstream SHA-256 | `85da9abfe32cc3ca92934ac9ae2c1228da606496e8762a9015766791410d9570` |
| License | MIT, see `LICENSE` (upstream `COPYING`); file header retained |

`tests/test_vendored_pcie_msix.py` checks that the vendored file equals the
upstream file plus exactly the modifications below. The MX-04 and MX-05 hunks
live in `tests/pcie_msix_local_patches.py`, generated layer by layer. Update
them together with the core.

## Local modifications

1. **Provenance comment** after the upstream license header.
2. **MX-02 PBA write address.** Upstream sets the default
   `pba_mem_addr = irq_index_reg >> 5`. The pending-bit set path
   (`STATE_READ_TBL_2`) and clear path (`STATE_SEND_TLP`) both rely on that
   default, but every PBA read uses `>> 6` on 64-bit PBA words. As a result,
   vectors whose index mod 64 is 32 or higher lose their pending bit or corrupt
   another vector's pending word. The fix changes the default to `>> 6`.
   Regression coverage is in `tests/test_vendored_pcie_msix.py`.

```diff
-    pba_mem_addr = irq_index_reg >> 5;
+    pba_mem_addr = irq_index_reg >> 6; // MX-02: PBA words are 64 bits; upstream used >> 5
```

3. **MX-02b stale PBA word.** The pending-bit scan trusts `pba_mem_rd_data_reg`,
   but every PBA write blocks that cycle's read. After `STATE_SEND_TLP` clears
   vector 63 and advances to vector 64, the scan reads vector 0's pending bit
   as vector 64's. The same happens at the wrap from the top index back to 0.
   - Observed: when vector 64 had never been programmed, the core wrote to
     host address 0.
   - Fix: a new `pba_word_valid_reg`, cleared by any PBA write and set by any
     PBA read. The scan refreshes the word before testing a bit.
4. **MX-02b head-of-line livelock.** A pending vector that is still masked
   re-entered `READ_TBL_1`/`READ_TBL_2` without advancing the index, so the
   scan never reached other pending vectors once they were unmasked.
   - Fix: the masked branch of `STATE_READ_TBL_2` now advances `irq_index_next`.
5. **MX-02b Mask default.** `tbl_mem` started at all zeros, so every Vector
   Control Mask bit was 0. Raising a vector the host had never programmed sent
   a write of 0 to address 0. The PCIe Base Specification gives the MSI-X
   Vector Control Mask bit a default of 1b.
   - Fix: odd table words (Vector Control and Message Data) now initialize to
     `64'h0000000100000000`.

6. **MX-04 host port.** The upstream AXI-lite slave accepts at most one access
   every two clocks, and its table RAM serves a host read or a host write per
   clock, not both. The PCILeech BAR engines issue up to one read and one write
   per clock and cannot be stalled.
   - Replaced by `host_wr_*`/`host_rd_*`: DWORD-indexed, no handshake, read
     data exactly two clocks after the request (the latency of the other
     served-BAR implementations).
   - The table is duplicated. Each host write updates `tbl_mem` (read by the
     scan) and `tbl_host_mem` (read by the host) in the same clock.
   - A host read of the word written in the same clock returns the new bytes.
   - PBA host writes are ignored; host reads use a second PBA read port.
   - `AXIL_*` parameters and ports are gone. The MX-02 Icarus regressions run
     on this port; the pristine-upstream test still uses AXI-lite.
   - Synthesis on `xc7a100tfgg484-1` (Vivado 2026.1, out of context, with the
     router, routed at 125 MHz): 2048 vectors use 16 BRAM tiles, 421 LUTs,
     WNS +0.59 ns; 6 vectors use 2 BRAM tiles, 211 LUTs, WNS +2.60 ns.

7. **MX-05 live controls and reset.** New ports `bus_master_enable`,
   `msix_clear` and `msix_clearing`; state widened to 3 bits with
   `STATE_WAIT_TLP` and `STATE_CLEAR`. The contracts are below.

### MX-05 contracts

The integration (MX-07) drives `msix_enable`/`msix_mask` from the hard IP's
`cfg_interrupt_msixenable`/`cfg_interrupt_msixfm`, `bus_master_enable` from
`cfg_command[2]`, and `msix_clear` from the PCIe-defined resets: the IP's user
reset (PERST and link down), hot reset and FLR.

- **Reset.** `msix_clear` aborts any operation, withdraws a presented message,
  and starts a sweep (`msix_clearing` high) that writes every table entry to
  its reset value (address and data 0, Mask 1) and every PBA word to 0. The
  sweep takes `2**(IRQ_INDEX_WIDTH+1)` clocks, at most 4096 (66 us at
  62.5 MHz), well inside the 100 ms the host must wait after a reset or FLR.
  Requests wait (`irq_ready` low) and host table writes are dropped while it
  runs. A clear requested during `rst` completes after `rst`.
- **`rst` preserves state.** `rst` includes PCILeech's own soft reset, which is
  not a PCIe reset, so it resets control state and withdraws a presented
  message but keeps the table and PBA. A withdrawn scan-path message stays
  pending; a withdrawn direct request is dropped.
- **Sending.** A message is sent only while MSI-X is enabled, the function is
  unmasked, Bus Master Enable is set, and the vector is unmasked, read from the
  table in the two clocks before it is presented.
- **Presented messages.** Once presented (`tx_wr_req_tlp_valid`), a message is
  owned by the core until accepted or withdrawn; the FSM handles nothing else
  meanwhile. If the controls dropped between the table read and presentation,
  it is never presented. It is withdrawn before acceptance if the function is
  masked or bus mastering is turned off (it becomes pending again), or if MSI-X
  is disabled: a direct request is then dropped, and a vector found by the scan
  keeps the pending bit it already had. A per-vector mask set after
  presentation does not withdraw it, because the core does not re-read the
  table. MX-06 bounds that window (see below).
- **Requests while disabled.** A request while MSI-X Enable is clear is
  dropped, not recorded, so a driver enabling MSI-X later does not receive
  events from before. Pending bits already set are kept.
- **Fairness.** A request does not move the scan. After each accepted request,
  the scan gets the next two idle steps (the first may only refresh the PBA
  word), so a producer holding `irq_valid` high cannot starve pending vectors.
  The request rate drops by at most two clocks per request.

Each MX-02 fix has a regression, plus a test that reverts that fix alone and
checks the regression then fails (`test_each_fix_is_detected_when_reverted`).
Host-port behavior is covered by `tests/test_msix_bar_router.py`.

## Integration notes

No producer drives `irq_valid`, so the generated controller ties it low and
never sends a message.

The integration (MX-07) is generated by
`src/templates/sv/msix/_msix_subsystem.j2`, called from the BAR controller
template when the build's MSI-X decision (`device_clone/msix_subsystem.py`)
generates the subsystem. The live controls and the IP user reset reach the
controller through voltcyclone-fpga's read-only `IfPCIeSignals.mpro` port
(`pcie_ctx`, `rst_pcie_user`, upstream PR #6). The points below say how each
contract is wired.

- **Transmit (MX-06).** `src/templates/sv/msix/pcileech_msix_tlp_tx.sv`
  commits the presented message into a one-message buffer whenever the buffer
  is empty, so the core's wait in `STATE_WAIT_TLP` lasts at most until the
  previous message leaves. The window in which a per-vector mask, function
  mask or Bus Master Enable change cannot stop a message is therefore the
  presentation clock plus the time a committed message waits for the stream:
  at most the completion packet already in progress or selected, then the
  upstream mux and link. A committed message cannot be withdrawn, because the
  upstream mux locks onto an input once it reports `has_data`.
- **Clear drain.** The controller feeds the PCIe resets (`rst_pcie_user`,
  `pl_received_hot_rst`, `cfg_received_func_lvl_rst`) into the merger's
  `clear_req` and drives the core's `msix_clear` from the merger's
  `clear_out`. That holds the clear until a message committed before it has
  left, so it always goes out before the driver can program a new entry.
- **`msg_allow`.** The controller drives the merger's `msg_allow` from the live
  `cfg_interrupt_msixenable && !cfg_interrupt_msixfm && cfg_command[2] &&
  !clear_req`. The core registers those one clock late, and the gate on
  `msg_ready` stops a commit in that clock. Gating on `clear_req` rather than
  the core's `msix_clear` (`clear_out`) is equivalent: the two differ only
  while a committed message is buffered, and `msg_ready` is low then anyway.
- **`requester_id`.** PCILeech's `pcie_id` is `{function[2:0], device[4:0],
  bus[7:0]}`, not PCIe order. The controller feeds
  `{pcie_id[7:0], pcie_id[12:8], pcie_id[15:13]}`. Keep
  `TLP_FORCE_64_BIT_ADDR=0`: a 4DW header for an address below 4 GiB is
  malformed.

- **Reserved bits.** Vector Control bits [31:1] are stored and read back as
  written. Pinned by
  `test_known_behavior_vector_control_reserved_bits_are_stored`. Left as is
  on purpose: with TPH, Vector Control bits [31:16] can hold steering tags, so
  forcing them to 0 would be wrong for such donors.
- **Unconfirmed upstream behaviors.** These came out of review and are
  not reproduced:
  - A host table write landing between `READ_TBL_1` and `READ_TBL_2` can tear
    an entry. The spec forbids changing an unmasked entry.
  - While `rst` is held, the FSM is forced idle each clock, so at most one PBA
    write from the state before `rst` lands. (Analysis, not a reproduction.)
- **Layout.** Donor table/PBA offsets are translated by
  `src/templates/sv/msix/pcileech_msix_bar_router.sv`. `requester_id` must come
  from the live bus/device/function (`pcie_id`).
- **Same-clock scan read.** A host write landing in the clock the scan reads
  the same table word returns the old word to the scan. The spec forbids
  changing an unmasked entry, so this matters only together with the torn-entry
  hypothesis above.
