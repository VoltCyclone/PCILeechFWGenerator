# Packet, abort and reset contract (CaptainDMA)

Scoped to the CaptainDMA 100T board RTL. It records how packets cross each
boundary between the host (FT601, COM), the FIFO router and the PCIe core, what
framing guarantees exist, and the rules the abort and reset logic follows. The
interface list describes the structure the reset and FIFO rules build on.
Overview: [MSIX_STATUS.md](MSIX_STATUS.md). No host software is covered here.

## Interfaces

File names are in `CaptainDMA/100t484-1/src/` of the board repo.

| Boundary | Contract | Source |
| --- | --- | --- |
| FT601 to COM | 32-bit data plus valid. COM RX has no backpressure, and the RX FIFO full flag is unconnected. | `pcileech_com.sv` |
| COM to FIFO router | 64-bit record plus valid. No ingress ready, framing reset, epoch or abort. | `pcileech_header.svh` |
| Router to host-TX packet queue | 32-bit DW, valid and last. No first, ready or abort. | `pcileech_header.svh`, `pcileech_fifo.sv` |
| PCIe core RX to 128-bit bridge | Core ready is always 1. First is synthesized from the preceding last or reset. | `pcileech_pcie_a7.sv` |
| RX queue to host mux | Pull request `rx_rd_en`, then up to four valid DWs with per-DW first and last. | `pcileech_header.svh`, `pcileech_pcie_tlp_a7.sv` |
| 128-bit TX to 64-bit converter | `tready` requests a beat for the next cycle. Arriving valid is consumed unconditionally. | `pcileech_pcie_a7.sv` |
| Converter to core TX | An accepted transfer is `valid && ready`. The final accepted word also has `last`. | `pcileech_pcie_a7.sv` |

The `IfAXIS128` name does **not** make this bridge a conventional same-cycle
AXI valid/ready handshake. The converter's pull and read latency stays explicit.
`tvalid && tlast` at the mux is a completed beat delivery into the converter,
not proof of a core transfer.

The host router accepts a record only if the magic byte is `0x77` and the type
is TLP. It forwards `[63:32]` as data and bit 10 as last. It does not interpret
a header length, count DWs or enforce a finite maximum (`pcileech_fifo.sv`).
COM's repeated `0x66665555` sequence resets 32-to-64 alignment only
(`pcileech_com.sv`). It sends no TLP-abort or boundary indication to the
router or packet queue, and no finite host-frame bound follows from these
files.

RX's synthesized first is not an independent start-of-packet detector. If the
core stream loses its last without a core or session reset, the converter has no
separate way to identify the next header. A `first` at the queue input can be
honored as a boundary, and the upstream generation of it is part of the
contract.

## What the framing guarantees

For a well-framed host stream, a packet consists of all valid TLP DWs after the
previous valid last, through the next valid last inclusive. Non-TLP records and
invalid cycles do not terminate the packet. Arbitrarily long idle periods are
allowed by the current interface, so an idle timeout cannot establish a boundary.

After a host stream starts mid-packet, two different histories can present the
same future sequence of `{data, valid, last}`: the old tail continued, or the
tail was abandoned and the next complete packet began. The queue cannot preserve
the first new packet in both histories. Keeping `host_mid` drops through a later
last, and clearing it can accept a headless old suffix. This is an observability
problem, not a counter-width problem.

The honest legacy recovery contract is therefore: after an uncertain boundary,
discard through an observed last, then accept subsequent complete packets. This
may discard the first otherwise healthy packet after an abandoned tail. If no
last arrives, stay in discard state without publishing anything. A guarantee
that the very next healthy host frame survives requires an explicit start/new
session boundary from upstream.

Resetting COM's RX FIFO makes a partial record/packet disappear, but does not
prove that the host's next bytes start a packet. An internal `tx_abort` signal
alone is insufficient: it must be paired with either an explicit new-boundary
event or the legacy discard-through-last rule. Adding semantics to the COM
resync sequence would be a wire-protocol contract change requiring host-side
investigation and tests. It is not an existing property.

The host output side carries first/last through the mux's context fields
(`pcileech_fifo.sv`, `pcileech_mux.sv`). Once part of a
frame has been emitted to USB, flushing FPGA queues cannot retract that prefix.
End-to-end reset safety therefore also needs either host-output packet drain
or an explicit host session invalidation/resynchronization contract. Queue-local
store-and-forward alone must not be described as whole-bridge reset safety.

**Host contract (100T).** The host contract is LeechCore 2.23+ (`TlpFrameStep`,
with FIRST required for FPGA version 4.13 and later):

- A FIRST drops an open prefix.
- A TLP DW without FIRST is ignored while no TLP is open.

PCIe-side cuts already meet that contract, because the destination FIFO admits
TLPs only at their first beat.

For COM session aborts, `pcileech_com_tx_resync` adds two steps after every TX
flush:

- It writes one all-idle word, which realigns the host's 32-byte parse.
- It drops TLP DWs until the next FIRST, so a cut tail is never spliced onto a
  prefix.

Two residuals remain:

- A PHY stop inside a word lets the one straddling host frame apply stale slot
  nibbles. A TLP that ended in the lost DWs can then complete with idle-word
  DWs in their place. Fixing that needs a host-side change.
- Pre-2.23 hosts ignore FIRST.

## Packet hazards in the original RTL

The first two are handled by the whole-packet reservation
([MSIX_FIFO_ADMISSION.md](MSIX_FIFO_ADMISSION.md)). The third is handled
by the drain described below. The fourth is contained rather than removed:
`ok_pcie` in `pcileech_pcie_tlp_a7.sv` is still cleared asynchronously. A host
subsystem reset now drains TX to a packet boundary first, so the clear lands
with no packet in flight. A link abort flushes the converter in the same reset,
so a half-sent packet is discarded rather than delivered.

1. **TX capacity was not reserved.** `tx_pass` checked instantaneous data and
   token full only at the inferred first DW. The packer wrote one or more
   cycles later, and its data and token enables were independent. A prior
   final beat could consume the last slot on the edge that admitted the next
   packet.
2. **RX could expose a prefix.** Earlier accepted beats stayed in the FIFO
   after a later full condition cleared `rx_admit`.
3. **Soft reset could remove a live TX tail.** The subsystem reset reached the
   source queues and mux while the converter stayed live.
4. **Asynchronous access withdrawal reached live data logic.** The
   asynchronously cleared `ok_pcie`/`acc_pcie` controls sourced valid and
   has-data, feeding a differently reset converter.

Nominal FIFO depths are TX data 1024 beats, TX token 16 and RX data 2048. They
do not prove usable depth, pipeline margin or free-space counts. The bound used
for reservation is the PCIe specification's largest TLP (1029 DWs), enforced by
capping every admitted packet at that size. It is not inferred from MPS, a
header, ECRC or an arbitrary spare-entry count in an opaque stream.

## Reset and drain rules

These rules are implemented in the 100T board RTL.

- **Link session abort.** The core user reset (PERST#, link down, core reset)
  and global `rst` abort the PCIe session. They flush the converter, the
  requester tracker, `dst64` and the gate together, and are never drained.
- **Host subsystem reset on a live link.** `pcileech_pcie_tx_drain` first raises
  `tx_stop`, so the TX mux selects no new packet and lets the selected one run
  to its last beat. Once the mux reports `tx_idle` with no pull outstanding, it
  raises the subsystem reset. `tx_stop` stays high until the reset is in
  effect, so no packet starts between the idle report and the reset. The
  request handshake is four-phase, so a stale idle is never taken for a new
  stop.
- **No timeout on a live link.** The selected packet is already complete in its
  source and only waits for the core to accept it. Cutting it on a timeout is
  the failure the drain exists to avoid. A watchdog may report failure or
  request a core or link reset, but it must not flush a partial live-link TLP.
- **Dead core clock.** With `clk_pcie` stopped, nothing can be transmitted and
  idle cannot be reported. The reset is then applied once no `clk_pcie` edge has
  been seen for `DEAD_CYCLES` `clk_sys` cycles (default 64), as a link-session
  abort.
- **Every TX source drains.** The drain covers cfg, BAR, static and the MSI-X
  transport sources, not just the host packet queue. Stopping all pull requests
  at once could strand an already selected packet and is not an acceptable
  quiesce policy.
- **COM session abort.** `pcileech_com_tx_resync` follows each TX flush, as
  described in the status block above. Resetting COM's RX FIFO makes a partial
  record disappear but does not mark the host's next bytes as a packet start,
  so a TX abort alone is not a new-boundary event.
- **Host output.** Once part of a frame has been emitted to USB, flushing FPGA
  queues cannot retract that prefix. Reset safety on that side relies on the
  host's FIRST handling (LeechCore 2.23+), not on queue-local store-and-forward.
