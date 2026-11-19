# Completion-timeout support: source facts and DRP proof

Background for [MSIX_REQUESTER_CONTRACT.md](MSIX_REQUESTER_CONTRACT.md). It
records what the original board RTL did, the behavior a genuine requester
timeout needs, and the vendor-model results showing that the Disable Supported capability can
be established through the PG054 reset-held DRP update. No capability transform
is authorized: the generated endpoint must match the donor.

The original board exposed the control and error-reporting connection points
for a timeout engine but had no outstanding-request table and no completion
retirement. Wiring an expiry pulse or setting an advertisement bit alone cannot
provide genuine timeout support. The requester tracker supplies the table,
admission and tag ownership.

The hardest source-level issue was the raw host TLP contract. Tags and
Requester IDs arrive inside opaque host-programmed headers, and RTL neither
assigned nor arbitrated them. A timed-out tag reused by the host can receive a
late completion from its former request, and a local generation counter does
not distinguish those wire-identical transactions. The requester tracker resolves this by owning
the tags (contract §1).

## Source facts (original board RTL)

Paths are relative to `CaptainDMA/100t484-1/src` in the board repo. The table
describes the RTL before the requester tracker added `req_gate` and its companions.

| Topic | Current behavior and source |
| --- | --- |
| Raw host request ownership | `pcileech_fifo.sv` forwards accepted COM record `[63:32]` as a TLP DW and bit 10 as last. `pcileech_pcie_tlp_a7.sv` packs DWs without parsing/replacing Requester ID or Tag. Thus those header fields are selected by the COM producer, not a hardware allocator. Host software's allocation discipline is outside this document. |
| Other arbitrary requester source | `pcileech_pcie_cfg_a7.sv` sends host-programmed static TLP words and can repeat the programmed packet. It does not allocate/check a unique tag for each repetition. |
| Shared TX path | `pcileech_pcie_tlp_a7.sv` merges cfg response, BAR response, host raw and static sources. `pcileech_pcie_a7.sv` converts the merged stream to 64 bits. There is no requester table between it and the core. |
| Exact acceptance boundary | `pcileech_pcie_a7.sv` wires the converter directly to core `s_axis_tx`. Its `fire = tlp_tx.valid && tlp_tx.ready` is the accepted-word boundary. Internal `IfAXIS128.tready` is a next-cycle pull request, not the core acceptance event. |
| Current local identity | `pcileech_pcie_cfg_a7.sv` derives `pcie_id` from live bus/device/function outputs, in an internal packing order. A new parser must normalize that order rather than compare packed `pcie_id` blindly to wire Requester ID. |
| Inbound completions | `pcileech_pcie_a7.sv` accepts core RX unconditionally and packs the stream. `pcileech_pcie_tlp_a7.sv` recognizes Cpl/CplD only for filtering. It has no Requester-ID/tag lookup, outstanding byte count, completion status retirement or late-completion tombstone. |
| Host delivery | Inbound filtered packets go to the host RX FIFO (`pcileech_pcie_tlp_a7.sv`) and then COM mux (`pcileech_fifo.sv`). No timeout result is injected into this path. |
| BAR read state is completer state | `pcileech_tlps128_bar_controller.sv` retains the incoming request's Requester ID/Tag, splits replies and emits CplD byte counts. This services another requester's BAR read; it is not a table of this endpoint's outbound reads. Reusing it as a requester scoreboard would confuse direction and ownership. |
| Config responses are completer state too | `pcileech_tlps128_cfgspace_shadow.sv` builds a completion using the incoming config request's Requester ID/Tag and this endpoint as Completer ID. These TX packets must not open requester timers. |
| Timeout control available | Core `cfg_dcommand2` is wired to `ctx` (`pcileech_pcie_a7.sv`) and mirrored into host-readable status (`pcileech_pcie_cfg_a7.sv`). Search of scoped SV/SVH found no other consumer. |
| Error inputs inactive | `pcileech_pcie_a7.sv` ties timeout, unexpected-completion and completer-abort reporting inputs low; the completion-header and AER log metadata inputs are tied low too. No `cfg_err_cpl_rdy` connection exists in this board wrapper. The vendor error handshakes must be verified before replacing tie-offs. |
| Core transmission-drop status | `tx_err_drop` is wired into `ctx` (`pcileech_pcie_a7.sv`) and mirrored to host status (`pcileech_pcie_cfg_a7.sv`), not associated with an outstanding request. Acceptance by the AXIS port alone does not establish that the IP cannot subsequently reject/drop the packet. |

Parent extraction reads donor DevCap2 ranges and Disable Supported directly
(`src/device_clone/donor_capability_extractor.py`). The override emits
both properties and treats `CONFIG.Cpl_Timeout_*` as mandatory readback keys
(`src/vivado_handling/pcie_ip_donor_override.py`). These gates
stay strict.

## Behavior a requester timeout needs

The requester tracker implements these rules (contract §3 to §9). They are listed here with their
source basis.

1. **Classify and admit complete outbound requests.** Parse a validated header
   before the first word reaches the core, and classify posted, non-posted,
   completion and unsupported types. Track MRd32 and MRd64. Other non-posted
   classes need explicit semantics and tests, so they are refused rather than
   silently untracked. Reserve a table entry before core acceptance, and never
   let a duplicate key overwrite a live entry from any mux source or repeated
   static TLP. Move an entry to issued only when the core has accepted the whole
   request. Starting the timer at USB ingestion or an internal pull request is
   wrong under arbitrary queue and core stalls.
2. **Bound outstanding non-posted admission (O13).** A request accepted by the
   core but not yet on the wire waits for transmit NP flow-control credit. A
   delay probe measured a constant NP-credit round trip `T_fc` of about 1136 ns in the Gen1 x1 vendor
   model. The accept-to-transmit delay is `D ≈ (j_ahead / C) · T_fc`, where
   `j_ahead` is the number of non-posted requests queued ahead and `C` is the
   link partner's advertised NP credit. To keep `D` under the code-0001
   tolerance (about 40 µs), admission caps in-flight non-posted requests at
   `j_ahead < ~35·C` (worst case `C = 1`, fewer than about 35 outstanding). The
   gate refuses new requests at the cap instead of relying on the timer. The cap
   follows the advertised NP credit, not the tag width.
3. **Track completion progress.** A memory read can return several completions.
   Byte Count is the remaining data, Length includes DW padding, and zero
   encodings mean the maximum size. Do not retire on the first matching CplD,
   reset the timer on arbitrary matching traffic, or subtract `length * 4`
   blindly. Reject duplicate, overlapping or impossible progress instead of
   underflowing counters. The tracker sits before host filtering, so real
   completions retire endpoint state even when USB is stalled.
4. **Apply live timeout control and report reliably.** Consume `cfg_dcommand2[3:0]`
   and `[4]` in `clk_pcie`, with a timebase whose frequency and wrap are
   proven. Disabling the timeout does not free tags. Queue timeout and
   unexpected events until the core's error interface accepts them, because
   several entries can expire together and a one-cycle pulse is not lossless.
5. **Own retirement, late completions and reuse.** A timed-out request keeps its
   history so a late completion is classified as unexpected. An old and a new
   request with the same Requester ID and tag are indistinguishable on the
   wire, so the tag stays quarantined (contract §6). A native function or link
   reset invalidates tracking with the core's own session. A live-link
   subsystem reset must not erase outstanding history, because replies may
   still arrive.

## DRP proof in the vendor model

Standalone AMD PCIE_2_1 XSim models were run. No physical endpoint or donor
was involved.

With SYSRSTN held low, a read-modify-write of DRP word 0x019 changed 0x3d00 to
0x3f00, setting only bit 9. Readback passed, and a configuration-space read
after reset release showed DevCap2=0x1f. This shows the model's reset-held
attribute update and capability readback, not genuine timeout support.

The first fixture's DevCtl2 write test did not set Disable, but an ordinary
Bus Master Enable write did not take effect either. That fixture had no working
management-write positive control, so its negative Disable result is
**inconclusive** and is not evidence of an endpoint or silicon restriction.
The generated Gen1 x1 wrapper inverts the management byte-enable, write and
write-readonly signals, matching that fixture's polarity.

The vendor's accelerated PIPE example on the same Gen1 x1/-1 target provides a
working ordinary configuration path (`pipe_mode_sim=Enable_Pipe_Simulation`,
`pipe_sim=true`, `ENABLE_GT` not defined). The normal vendor test completed at
143.081 us: Command=3 received a successful Type0 completion and PIO readback
matched 0x01020304. This is
an ordinary linked configuration and PIO positive control. It is not a proof of
DRP-enabled DevCtl2 behavior.

### Focused linked Disable-control proof

The focused testbench gate answers the model control question. It uses the unchanged generated
PIPE endpoint/root-port core; only copied testbench stimulus is modified.

| Check | Result |
| --- | --- |
| Actual endpoint reset held during programming | Whole DRP RMW finished at 450 ns, before the unchanged 4.995 us reset release |
| DRP word 0x019 | 0x0905 to 0x0b05; only bit 9 changes |
| Ordinary host Command write/read/restore | 0x00100000 to 0x00100004 to 0x00100000 |
| Ordinary host DevCap2 read | 0x00000012: Disable Supported is set; range remains B |
| Ordinary host DevCtl2 set/read | 0 to 0x10; actual core cfg_dcommand2[4] is 1 |
| Ordinary host DevCtl2 clear/read | 0x10 to 0; actual core cfg_dcommand2[4] is 0 |
| Skip-DRP-write mutant | BME control still passes, DevCap2 reads 2 and the named capability assertion rejects the test |

The positive completes at 101.945 us, and the discriminating mutant stops at
73.801 us.

This shows a documented mechanism working in the installed linked model,
beyond setting an advertisement bit. It does not prove requester timeout
behavior. The example has range B, not the donors' ABCD, and does not exercise
repeat reset, OS behavior or a production startup controller. A
serial-transceiver example also ran 100 us but ended before the ordinary write
sequence, so the PIPE example is used.

The startup design that follows from this proof holds the core in system reset,
uses an independent running DRP clock, read-modify-writes the planned attribute
word, verifies the entire word, and only then releases the core to enumerate. A
failure keeps reset asserted and exposes an error, so the core never enumerates
with an unverified donor mismatch. It is specified in contract §11, which also
covers arbitration with the host DRP bridge and the reset stretcher. The build
plan distinguishes the IP's initial state from the verified post-startup state
(contract §10), and strict donor readback stays mandatory for every key the
controller does not own.

## Acceptance cases

The requester benches in the board's `sim/r1` cover these cases.

| Case | Required result |
| --- | --- |
| Accepted TX versus stalled TX | No timer for unaccepted queued data; exactly one entry after the actual request issue boundary |
| Posted writes, Cpl/CplD and BAR/config replies | No requester timeout entry |
| Duplicate key across host/static sources | Original table state preserved; second request rejected before first core word |
| MRd32/MRd64 and byte order | Parsed identity/tag/address/length/byte enables match on-wire header fields |
| Split completion | First segment reduces correct remaining work; final valid coverage retires exactly once |
| Partial-byte/unaligned/maximum encoding | Useful bytes and special zero encodings handled without counter underflow or early retirement |
| Invalid completion progress/status | No false success from wrong RID/tag, duplicate payload, overlap, impossible Byte Count or malformed length |
| Concurrent final Cpl and deadline | Defined normative precedence; exactly one final outcome, no spurious double report |
| Disable/re-enable/value updates | Retain tags and completion tracking; expiry follows the approved control-change rules |
| Multiple expiries and error backpressure | Every error and its header remains stable until accepted; no lost pulses |
| Timeout, attempted same-tag reuse, late Cpl | Late data never completes the new request; reuse rejected until the proven safe release boundary |
| USB congestion or filtering | A real received completion retires endpoint state independently of host delivery; transport loss is separately reported |
| Core drop/reset/FLR/link loss versus soft reset | Correct session retirement; no stale completion matched after reset; soft reset does not erase live-link history |
| Unsupported non-posted request class | Explicitly refused before transmit; never silently untracked |

Mutants, each of which must fail a distinct test: arm on USB valid instead of core acceptance; retire on first CplD;
subtract padded Length bytes; overwrite a duplicate tag; free tags on Disable;
drop events when error-ready is low; match a late completion to a reused tag;
and clear the table on unrelated subsystem reset.

## Target mechanism

PG054 documents [DRP access](https://docs.amd.com/r/en-US/pg054-7series-pcie/Using-the-Dynamic-Reconfiguration-Port-Interface),
[reset-held update restrictions and read-modify-write](https://docs.amd.com/r/en-US/pg054-7series-pcie/Other-Considerations-for-the-DRP-Interface),
and the [attribute map](https://docs.amd.com/r/en-US/pg054-7series-pcie/DRP-Address-Map).
The mapped Disable Supported attribute is a documented mechanism. It is not
a definition of endpoint-specific config behavior. The startup controller
that uses it is specified in contract §11, and the PIPE model is described in
[PG054 PIPE mode simulations](https://docs.amd.com/r/en-US/pg054-7series-pcie/PIPE-Mode-Simulations).

Linux drivers that never read one bit do not remove the requirement to
implement the advertised behavior. Overview: [MSIX_STATUS.md](MSIX_STATUS.md).
