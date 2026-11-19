# Requester contract: FPGA-owned requester tags, completion timeout and DRP startup

The requester RTL and generator binding exist on the CaptainDMA 100T (overview:
[MSIX_STATUS.md](MSIX_STATUS.md)). The contract implements **option A: the FPGA
owns PCIe tags** for requests it tracks. A capability downgrade or transform is
not authorized. Read it with [MSIX_TIMEOUT_SUPPORT.md](MSIX_TIMEOUT_SUPPORT.md)
(source facts and DRP proof) and [MSIX_PACKET_CONTRACT.md](MSIX_PACKET_CONTRACT.md)
(stream integrity). Source names refer to `CaptainDMA/100t484-1/src` in the
board repo.

## 1. Decision and consequences

The FPGA replaces the host-chosen Tag of every tracked outbound request with a
tag it allocates, and restores the host's tag on the completions it delivers to
the host. Consequences:

- Tag lifetime, quarantine and reuse are decided by one owner, in hardware, with
  full knowledge of timeouts. A late completion can never match a newer request,
  because the FPGA never reissues a quarantined internal tag.
- No host-software protocol change is required. Host-visible failure is the
  absence of a completion (exactly today's observable behaviour on a lost
  request) plus new readable status counters (§9). A typed per-request failure
  record to the host is a possible later extension, not part of this contract.
- The Requester ID is **not** rewritten. Requests whose Requester ID is not this
  function's live ID are refused (§3), because the core drops completions for
  any other ID (PG054 *Unexpected Completions*) and they could never be tracked.

## 2. Placement and clock domain

Everything below is in `clk_pcie` (user_clk_out). PG054 *AXI Interface Frequency*
gives 62.5 MHz default for Gen1 x1 64-bit (125 MHz optional; 250 MHz unsupported
on -1 Artix-7). The build must read back the live user-clock property and bind
the generated `CLK_PCIE_HZ` parameter to it (§10); the timer must not assume.

| Block | Inserted at | Role |
| --- | --- | --- |
| `req_gate` | Between the merged 128-bit TX stream `tlps_tx` and `pcileech_tlps128_dst64` (`pcileech_pcie_a7.sv`) | Classify, admit/refuse, allocate, rewrite tag |
| `req_tx_obs` | On core `s_axis_tx` accepted words (`fire` at `pcileech_pcie_a7.sv`) | Mark RESERVED → ISSUED at accepted `tlast`, start timer |
| `req_rx_cpl` | Between `pcileech_tlps128_src64` output and `pcileech_tlps128_filter` | Parse completions, update progress, restore tag or drop |
| `req_table` | Shared by the three above | Entry state, timers, sweep |
| `req_err` | Drives `cfg_err_cpl_timeout/_unexpect`, header, `cfg_err_posted` (`pcileech_pcie_a7.sv`) | Lossless error queue |
| `drp_startup` | clk_sys, in `pcileech_pcie_a7_rst` and the DRP mux | Reset-held DRP RMW/verify (§11) |

All three merged TX sources (host raw, static repeating, BAR/cfg completer
responses) pass `req_gate`, so none can bypass admission. Placing `req_rx_cpl`
before the host filter/FIFO means real completions retire endpoint state even
when USB is stalled (MSIX_TIMEOUT_SUPPORT.md, behavior item 3).

`IfAXIS128` is a pull stream: `tready` in cycle *n* requests a beat delivered in
*n+1* (`pcileech_pcie_a7.sv`). `req_gate` must present the same contract
on both sides and add no beat loss or duplication; its own refusal discards are
the only intended removals.

### Device Control inputs (one-cycle register)

`pcileech_pcie_a7` registers four Device Control fields once in `clk_pcie`
before the requester sees them: Extended Tag Field Enable `cfg_dcommand[8]`,
Max_Read_Request_Size `cfg_dcommand[14:12]`, Completion Timeout Value
`cfg_dcommand2[3:0]` and Completion Timeout Disable `cfg_dcommand2[4]`.
Wired straight from the `PCIE_2_1` pins, the Extended Tag cone failed routed
setup at `n_quarantined` (−1.551 ns, 18 logic levels).

In this contract, "live" for these four fields means the value one `clk_pcie`
cycle after the core drives it. The lag adds one cycle to the gate's existing
pipeline latency. A request admitted in that cycle is indistinguishable from
one admitted a cycle earlier and still queued in the gate or core TX path.
Device Control 2 lets pending requests use the old or the new timeout setting.
Reset, FLR and hot reset clear Bus Master Enable, so no admission can use a
register value from before it caught up.

Bus Master Enable `cfg_command[2]` and the Requester ID stay unregistered.
`sim/r1/lint_pcie_a7.py` fails if any of the four fields is wired directly to
the core, or if Bus Master Enable is registered.

## 3. Request classification and admission (`req_gate`)

Header layout in the 128-bit beat: DW0 = `tdata[31:0]` with PCIe bit order
(`Fmt` = `[31:29]`, `Type` = `[28:24]`, `TD` = `[15]`, `Length` = `[9:0]`),
DW1 = `[63:32]` (Requester ID `[63:48]`, Tag `[47:40]`, Last BE `[39:36]`,
First BE `[35:32]`), address DWs at `[95:64]`/`[127:96]`. Confirmed against the
existing parser at `pcileech_tlps128_bar_controller.sv`.

| Class (first beat, `Fmt/Type`) | Disposition |
| --- | --- |
| MRd32 `000/00000`, MRd64 `001/00000` | **Tracked**, subject to the checks below |
| Posted: MWr32/64, Msg/MsgD (`Type 10rrr`) | Pass unmodified, untracked |
| Cpl/CplD/CplLk/CplDLk (completer responses from BAR/cfg/host) | Pass unmodified, untracked |
| MRdLk, IORd/IOWr, CfgRd/Wr0/1, FetchAdd/Swap/CAS, any `Fmt=100` prefix, any undefined encoding | **Refused** before transmit (`REFUSE_CLASS`) |

A tracked MRd is admitted only if **all** hold on the gate's input cycle; else
the whole packet is discarded through `tlast` and a counter increments:

1. It is a single beat with `first && tlast` (MRd carries no payload; 3DW and 4DW
   headers both fit in one beat). Otherwise `REFUSE_MALFORMED`.
2. `TD == 0`. A host-computed digest would be invalidated by the tag rewrite.
   (`REFUSE_TD`)
3. Requester ID equals the live ID normalized to wire order `{bus, dev, fn}`.
   The packed `pcie_id` uses a different internal order
   (`pcileech_pcie_cfg_a7.sv`; byte-swapped use at
   `pcileech_tlps128_bar_controller.sv`). (`REFUSE_RID`)
4. Bus Master Enable `cfg_command[2] == 1`. (`REFUSE_BME`)
5. Request length in bytes ≤ Max_Read_Request_Size from live `cfg_dcommand[14:12]` (§2 register),
   and the byte range does not cross a 4 KB boundary. Length 0 encodes 1024 DW.
   Byte-enable rules: Length 1 ⇒ Last BE = 0; Length > 1 ⇒ First BE ≠ 0 and
   Last BE ≠ 0. A Length-1/First-BE-0 read is a legal zero-length read and is
   tracked. (`REFUSE_MALFORMED`)
6. MRd64 with address `[63:32] == 0` is refused as malformed (must use 3DW).
7. A free internal tag exists (§5). Otherwise `REFUSE_NO_TAG`. Refusal, not
   stall: stalling would block posted traffic behind a read indefinitely when
   tags are quarantined.
8. In-flight non-posted count `< N_np_max` (the O13 admission cap, §8).
   Otherwise `REFUSE_NP_CAP`. Like `REFUSE_NO_TAG` this is a refusal, not a stall,
   for the same reason. The count is of entries in RESERVED/ISSUED (not yet
   retired); `N_np_max` is sized from the live advertised NP credit so accept-to-
   transmit delay `D` stays under the tightest completion-timeout window.

On admission, `req_gate` writes the entry (§4) in state RESERVED and forwards
the beat with `Tag` replaced by the internal tag. No other header bit changes.

## 4. Entry contents

Indexed by internal tag. Width estimate ~72 bits; up to 256 entries.

| Field | Source |
| --- | --- |
| `state` | FREE, RESERVED, ISSUED, QUARANTINED (§6) |
| `host_tag[7:0]` | Original header Tag |
| `superseded` | Set when a newer admitted request reuses the same `host_tag` (§7) |
| `next_lower_addr[6:0]` | Expected Lower Address of the next completion |
| `remaining[12:0]` | Bytes still owed, 1..4096 |
| `t_issue` | Timebase snapshot at ISSUED |
| `limit_code[3:0]`, `disable` | Latched at ISSUED (§8) |
| `hdr_log` | Fields for `cfg_err_tlp_cpl_header` (§9) |

`remaining` at admission is the request's total Byte Count (B3 §2.3.1.1 Table 2-32):

- Length 1: the **span** `msb(FirstBE) − lsb(FirstBE) + 1`, or 1 for FirstBE = 0000b
  (zero-length read). This is not the number of enabled bytes: 1001b gives 4.
- Length > 1: `4*Length − leading_disabled(First BE) − trailing_disabled(Last BE)`.

`next_lower_addr` = `{addr[6:2], lsb_offset(FirstBE)}` (Table 2-33; 0 for FirstBE 0000b).

Non-contiguous byte enables are legal in First BE for Length 1, and in both BE fields
for QW-aligned (`addr[2] == 0`) Length-2 memory reads (B3 §2.2.5). Both are tracked
with the formulas above. Non-contiguous enables in any other request are
`REFUSE_MALFORMED`.

The entry also stores the request's TC and Attr[1:0] (RO, NS) for completion matching.

## 5. Tag allocation

- Usable tag space: 0–31 when Extended Tag Field Enable `cfg_dcommand[8] == 0`;
  0–255 when 1. Read live (§2 register) at each allocation. Clearing the enable never frees or
  reissues tags ≥ 32 already in use; it only restricts new allocations.
- Allocate **round-robin**: the first FREE tag in range at or after a pointer that
  advances past each allocated tag. A just-released tag is then the last one
  reused, which maximizes time separation from any straggling completion. Do not
  use lowest-FREE allocation, which reuses the most recently freed tag first.
  One allocation per cycle.
- The offer may be registered (board `CaptainDMA/100t484-1` does this for timing):
  the tag offered in cycle n is chosen from the FREE set of cycle n−1, so a tag
  that returns to FREE in cycle n is offerable from n+2, and `REFUSE_NO_TAG` can
  persist one cycle (16 ns) past a release. Because a tag leaves FREE only by
  admission, a registered offer is still FREE when taken, provided the tag taken
  in n−1 is excluded from the next offer. The range is re-applied when the offer
  is used, so clearing the enable withdraws an out-of-range offer at once.
- 10-bit tags are out of scope (Gen1 7-series core).
- A tag leaves FREE only by admission and returns to FREE only via §6 transitions.

## 6. Lifecycle

```
FREE --admit--> RESERVED --accepted tlast--> ISSUED --final Cpl (SC, incl. EP)--> FREE
                    |                          |  --UR/CA/reserved/CRS Cpl----> FREE
                    |                          |  --deadline (expiry enabled)-> QUARANTINED
                    |                          `--FLR-------------------------> QUARANTINED
                    |--discard (release)-----> FREE   (never on the wire)
                    `--accepted tlast after an FLR--> QUARANTINED
QUARANTINED --final or terminal Cpl (late)--> FREE
QUARANTINED --drain interval elapsed--------> FREE
any state --link session abort--> FREE
```

**Drain interval.** QUARANTINED is bounded; it is not
persistent. When an entry is quarantined, it records `t_q = now` and is released
when `now − t_q ≥ Q`:

- After a timeout, `Q` is the upper bound of the entry's latched CTO window
  (§8). That is one more full timeout interval.
- After an FLR, `Q` is the same, using the latched pre-FLR code. For an entry
  latched with Disable, `Q` = 64 s, the largest defined window. `t_q` is the FLR
  time for an entry that was ISSUED at the FLR, and the issue time for an entry
  that was RESERVED at the FLR and whose packet was accepted afterwards.

Basis and asymmetry:

- **FLR.** B3 §6.6.2 tells software not to reinitialize after an FLR until it has
  waited roughly the pre-FLR Completion Timeout. The Implementation Note on
  p. 513 makes avoiding stale completions software's responsibility. Releasing
  after that interval is therefore normatively supported.
- **Ordinary timeouts.** B3 gives no upper bound on how late a completion can
  arrive. The rule there is an **explicit assumption**: no completion arrives
  more than one further CTO window after expiry.
- **When the assumption is violated,** the backstop is round-robin allocation
  (§5) plus the full completion-match set (§7: Byte Count, Lower Address, TC,
  Attr). That backstop is probable, not proven. Read A/read B-shaped aliasing
  (MSIX_PCIE_SPEC_RESEARCH.md O4.7) is not detectable by header checks.

A persistent quarantine was rejected because 32 lost completions would block all
reads until a link reset. That conflicts with B3 §6.6.2, which requires the
function to be usable after an FLR.

- **RESERVED → ISSUED** on `s_axis_tx_tvalid && tready && tlast` of a packet whose
  first word decodes as MRd and whose Tag names a RESERVED entry. A tracked-class
  packet reaching the core with a non-RESERVED tag sets sticky `ERR_BYPASS`.
  That condition is an implementation fault and needs a test that it is unreachable.
- **`tx_err_drop`**: PG054 asserts it within two cycles after `tlast` of a discarded
  TLP, for link-down, payload > MPS, or a streaming-mode gap. Reads have no payload
  and streaming mode is not used, so for tracked requests it implies link-down, which
  is a session abort. The tracker only counts it. Entries are never freed on `tx_err_drop`.
  A dropped request that is not followed by a session abort times out normally,
  which is conservative.
- **Link session abort** = `rst_pcie_user` (PERST#, link down, core reset; covers
  global `rst` and `pcie_rst_core`, which also reset the core). All entries go to
  FREE and the error queue is cleared (B3 §2.9.1: an Upstream Port handles DL_Down
  as a reset and discards TLPs being processed). Both ends discard queued TLPs on
  DL_Down, so completions issued before the abort are not expected after DL_Up.
  No B3 text makes them impossible; this residual risk is accepted. PG054
  Table 2-7 documents `user_reset_out` for Hot Reset and Link Disable. **Core-level
  propagation (vendor model):**
  - Hot Reset asserts `user_reset_out` asynchronously, holds it while
    `pl_received_hot_rst` is high, drops `user_lnk_up`, and resets config space.
  - BME reads 0 after recovery, so the gate must refuse (`REFUSE_BME`) until the
    host re-enables it.
  - Reset deasserts only after PHY link-up (17 µs after release in-model); there is
    no fixed pulse width.

  Propagation from `user_reset_out` to the board's `rst_pcie_user` is upstream RTL.
- **Host subsystem reset `rst_subsys` on a live link does not touch the table.**
  Replies may still arrive. The tracker's reset is `rst_pcie_user || rst_clkpcie`
  only (`pcileech_pcie_a7.sv`).
- **Release** (proven pre-core discard): a RESERVED entry whose packet is
  discarded before core acceptance returns to FREE. The component doing that
  discard must identify the owned tag and drive the release. The current gate
  refuses before admission and has no release output; its shared link-session
  reset flushes both stream and table. `tx_err_drop` is never a release source.
  A release for an entry that is not RESERVED sets sticky `ERR_RELEASE` (an
  implementation fault, like `ERR_BYPASS`).
- **FLR** (`cfg_received_func_lvl_rst`): ISSUED → QUARANTINED with the drain
  interval above. An entry RESERVED at the FLR waits for its packet: a discard
  frees it, and an accepted tlast sends it straight to QUARANTINED (draining from
  that issue), because that request is on the wire and its tag must not be
  reused before its completion window has passed. A final completion in the FLR cycle
  wins (FREE); a sweep expiry in the FLR cycle is replaced by the FLR quarantine.
  A second FLR restarts the drain of entries the first one quarantined, which
  only delays release. No timeout is reported for them; B3 §6.6.2 permits clearing all
  transaction status. Completions arriving during or after the FLR for these
  entries are dropped and reported Unexpected (B3 §2.3.2 requires Unexpected
  after the FLR completes). Transactions Pending clears because QUARANTINED is
  excluded (§9).
- Tag exhaustion is visible in counters while it lasts. New host requests fail as
  `REFUSE_NO_TAG` until drain intervals release tags. A link reset is not
  required for recovery.

## 7. Completion handling (`req_rx_cpl`)

Parses the first beat of each Cpl/CplD/CplLk/CplDLk from `src64` (the core has
already dropped foreign Requester-ID completions). Completion DW1: Completer ID
`[63:48]`, Status `[47:45]`, BCM `[44]`, Byte Count `[43:32]` (0 ⇒ 4096).
DW2: Requester ID `[95:80]`, Tag `[79:72]`, Lower Address `[70:64]`.

"Matches" below uses `e` for the entry indexed by Tag. TC and Attr[1:0] equal the request's values (IDO is
excluded), `ByteCount == e.remaining` and `LowerAddr == e.next_lower_addr`
(B3 §2.2.9, §2.3.2). A failed match is reported as Unexpected: footnote 22
recommends that for Byte Count and Lower Address mismatches, and it is permitted
for the others.

| Condition | Table action | Host delivery | Error |
| --- | --- | --- | --- |
| Completion Type with unsupported Fmt, or inconsistent first-beat keep/last | none | drop packet | malformed, with original 128-bit header |
| `e.state` FREE or RESERVED | none | drop packet | unexpected |
| Status SC, CplD, matches, Length consistent (EP = 0 or 1) | progress (below) | restore `host_tag` unless `superseded`/QUARANTINED; EP preserved | none if ISSUED; unexpected if QUARANTINED |
| Status SC, BCM = 1 | none | drop | none for ISSUED, unexpected for QUARANTINED; counter `CPL_BCM_UNSUPPORTED` (documented deviation, below) |
| Status SC but match/Length fails, or CplLk/CplDLk, or Cpl without data for SC | none | drop | unexpected; `CPL_MISMATCH` counter |
| Status UR, CA, or reserved (Cpl, no data), TC/Attr match. Length ignored (undefined, §2.3.1.1) | → FREE | restore and deliver unless superseded/QUARANTINED | none via `cfg_err` (§6.2.3.2.5 forbids it; the core sets Received Master/Target Abort). Unexpected only if QUARANTINED |
| Status CRS (illegal for a memory read), TC/Attr match | → FREE | drop | none for ISSUED, unexpected for QUARANTINED; counter `CPL_CRS_ILLEGAL` (decision below) |

The implementation checks TC/Attr on terminal completions as well as SC data.
Terminal Byte Count, Lower Address and Length are ignored. A terminal TC/Attr
mismatch is Unexpected and leaves the entry unchanged. A final SC payload can
include at most three trailing padding bytes beyond Byte Count.

Completion-family detection uses Type independently of Fmt. PG054 assigns
checking the required 3DW CplD header and reporting an illegal 4DW CplD to user
logic. The standalone receiver emits `malformed_valid` with the original full
header and drains the packet. This check precedes table ownership checks.
[PG054 Error Detection](https://docs.amd.com/r/en-US/pg054-7series-pcie/Error-Detection)

**Decisions:**

- **Reserved status** is handled as UR (B3 §2.3.2: "treated as if the Completion
  Status was Unsupported Request"). It is delivered unchanged; only the Tag is
  rewritten.
- **CRS for a memory read terminates the request.** B3 §2.3.2's termination
  clause covers status "other than Successful Completion or CRS (in response to
  Configuration Request only)", and the plain reading includes this case.
  Termination also avoids holding a tag for an illegal TLP. The optional
  Malformed report is **not** sent: Malformed TLP has Fatal default severity, and
  an ERR_FATAL could make the OS reset the link because of an optional report.
  Residual risk: if the intended reading is drop-and-timeout, a following late
  completion becomes Unexpected and the §5/§7 backstop applies.
- **BCM is a documented deviation.** Only PCI-X completers set BCM (B3 footnote
  19), never a Root Complex or an I350 peer. Peer-to-peer reads through a
  PCIe-to-PCI-X bridge are out of scope. The completion is dropped without a
  `cfg_err`, because it is spec-legal from such a completer. The entry times out
  normally.
- **Poisoned completions are propagated** (B3 §2.7.2.2, §6.2.3.2.4.3). An EP SC
  CplD progresses the entry like any other fragment and is delivered with EP
  intact. The tag is freed only when `remaining == 0`. Freeing it on EP is
  forbidden: the completer may still send later fragments, which could alias a
  new request (research O4.7). The core reports poison itself. O6/O7 must read
  back `DISABLE_RX_POISONED_RESP` and confirm that reporting.

Progress for a consistent SC CplD:

- `bytes_this = ByteCount` if `ByteCount ≤ 4*Length − LowerAddr[1:0]` (final);
  otherwise `4*Length − LowerAddr[1:0]` (partial, which must end on the completer's
  RCB). Partial completions must make `bytes_this > 0` and leave
  `remaining − bytes_this > 0`.
- `remaining -= bytes_this`; `next_lower_addr += bytes_this` (mod 128). When
  `remaining` reaches 0 → FREE.
- Ordering: "Multiple Memory Read Completions for a single Read Request must
  return data in increasing address order" (B3 §2.3.1.1). An out-of-order or
  duplicate segment fails the match and is dropped. Counters never underflow. An
  optional RCB-alignment check is not implemented (permitted).
- Zero-length read: one CplD, Length 1, Byte Count 1, Lower Address[1:0] = 00,
  payload unspecified (B3 §2.2.5, Tables 2-32/2-33).
- Poisoned (`EP` = 1, or `m_axis_rx_tuser[1]`): progress and deliver as decided
  above.
- ECRC: if the IP's Trim TLP Digest option is off, a TD = 1 completion carries
  a digest that becomes stale after tag restore. The build must read the live
  trim/ECRC-check properties. Either trim is on, or `req_rx_cpl` must clear TD
  and remove the digest DW atomically. Choose after the readback; do not deliver
  a stale digest silently.

Delivery: if delivered, rewrite only DW2 `Tag` to `host_tag`. Drops discard the
whole packet through `tlast`. `src64` cannot be backpressured (`tlp_rx.ready = 1`,
`pcileech_pcie_a7.sv`), so `req_rx_cpl` is a fixed-latency pipeline. The
lookup latency is matched by delaying the stream, never by stalling.

The current receiver decides and updates on the header's input edge, using the
table's combinational lookup, and registers the forwarded stream for one cycle.
Adjacent fragments see the preceding update. This relies on the core's payload
and digest framing checks. A same-edge final completion wins over timeout/FLR.
A partial completion at an FLR edge advances the quarantined entry and is
dropped with Unexpected. A request admitted on the same edge with a reused host
tag suppresses the old completion through the supersede lookup.

The measured baseline has receive ECRC checking off. Enabling checking requires
handling `rx_ecrc_err`, which arrives at EOF and is currently discarded by src64,
before this header-edge update/delivery scheme can be used. With digest trimming
on, the core removes ECRC and clears TD. With trimming off, the receiver clears
TD and removes the digest using the payload Length, including a digest-only
tail beat. [PG054 64-bit RX digest handling](https://docs.amd.com/r/en-US/pg054-7series-pcie/Packet-Data-Poisoning-and-TLP-Digest-on-the-64-Bit-Receive-AXI4-Stream-Interface),
[PG054 Receive Interface](https://docs.amd.com/r/en-US/pg054-7series-pcie/Receive-Interface)

**Superseded host tags.** If the host admits a new MRd with `host_tag = h` while
another non-FREE entry holds `h`, the older entry gets `superseded = 1`. Its
completions still progress and retire it but are not delivered. This stops a slow
reply to an abandoned host request from looking like the answer to the host's
newer request with the same tag. With host-assigned tags, that confusion is
possible today.

## 8. Timer and control rules

Completion Timeout Value encodings (`cfg_dcommand2[3:0]`) and windows are
confirmed against B3 §7.8.16 Table 7-25 (MSIX_PCIE_SPEC_RESEARCH.md O1.2).

**Timer start and target:**

- The timer starts at the accepted `s_axis_tx` `tlast` (ISSUED). This is an
  interim proxy for "when the Request is transmitted" (B3 §2.8).
- A request accepted by the core may wait inside it for Non-Posted credits for
  some delay `D` before transmission. The deadline measured from transmission is
  then `T − D`.
- So T is biased to the **top** of each window: `T = 0.9 × window_max`. That
  tolerates `D ≤ T − window_min` with no premature expiry. The previous
  mid-window targets left little room for `D`.
- **`D` is now bounded by admission control, not by a transmit-side proxy.** A
  delay probe measured, in the Gen1 x1 vendor model, a constant NP-credit round-trip
  `T_fc ≈ 1136 ns`, so `D ≈ (j_ahead / C) · T_fc` (`j_ahead` = non-posted requests
  queued ahead, `C` = advertised NP credit). `req_gate` therefore **caps in-flight
  non-posted requests at `N_np_max = floor((T₀₁ − window_min) / T_fc) · C`**, using
  the tightest code 0001 (`T − min = 40 µs`): `N_np_max ≈ 35·C`, worst case
  `C = 1 → < 35` outstanding. On reaching the cap, a new non-posted request gets
  `REFUSE_NP_CAP` (a distinct refusal from `REFUSE_NO_TAG`); it is not accepted
  into the core to be caught later by the timer. Size the cap from the live
  `fc_*`/InitFC advertised NP credit, not the tag-width limit.
- **Runtime implementation:** tie `fc_sel=101b` continuously
  (Transmit Credits Limit). Hold the gate's `np_cap` at zero through session
  abort/link-down and two qualified link-up clocks, then sample `fc_nph` once.
  Set `np_cap=min(256,35*max(C,1))`; zero means infinite credit for this selector,
  and treating it as one is conservative. X/Z samples keep admission closed
  and are retried. Preserve the sample across live-link subsystem reset and FLR:
  later limit values are cumulative UpdateFC counts, not the initial credit.
  Every outgoing NP source passes through the gate, so no pre-sample NP
  consumption can advance the initial limit. PG054 documents initial credits
  at `user_lnk_up` and a two-user-clock selector latency.
  [Flow Control Credit Information](https://docs.amd.com/r/en-US/pg054-7series-pcie/Flow-Control-Credit-Information),
  [Transmit Credit Flow Control Information](https://docs.amd.com/r/en-US/pg054-7series-pcie/Transmit-Credit-Flow-Control-Information).
  `N_np_max` is runtime state, not a top-level generic or generator plan field.
  `CONFIG.cfg_fc_if` must be enabled and read back true; the generator forces
  regeneration. The regenerated wrapper and synthesized DCP expose the FC ports on the 100T-1.
- `T_fc ≈ 1.1 µs` is the vendor-model UpdateFC cadence. `N_np_max` must be
  tightened if the real `T_fc` is larger. A transmit-side issue proxy is only
  needed if a single outstanding request (`j_ahead = 0`) can still breach code
  0001. In the model, `D` at `j_ahead = 0` is one `T_fc`, about 1.1 µs.

| Code | Window | T = 0.9 × max | Tolerated D (T − min) |
| --- | --- | --- | --- |
| 0000 | 50 µs–50 ms | 45 ms (≥ 10 ms recommendation met) | ≈ 45 ms |
| 0001 | 50–100 µs | 90 µs | 40 µs |
| 0010 | 1–10 ms | 9 ms | 8 ms |
| 0101 | 16–55 ms | 49.5 ms | 33.5 ms |
| 0110 | 65–210 ms | 189 ms | 124 ms |
| 1001 | 260–900 ms | 810 ms | 550 ms |
| 1010 | 1–3.5 s | 3.15 s | 2.15 s |
| 1101 | 4–13 s | 11.7 s | 7.7 s |
| 1110 | 17–64 s | 57.6 s | 40.6 s |
| other | reserved / unadvertised | design choice: apply the 0000b row | n/a |

B3 defines no hardware behaviour for reserved or unadvertised encodings; software
must not use them (B3 *Terms*: Reserved). Applying the 0000b window is an
implementation choice, not a spec rule.

- Timebase: one free-running tick counter in `clk_pcie`, tick period from
  `CLK_PCIE_HZ` (for 62.5 MHz, 64 cycles = 1.024 µs per tick). Use
  `elapsed = now − t_issue` in modular arithmetic. Counter width must exceed the
  largest T plus margin: 64 s / 1.024 µs ≈ 2^26, so ≥ 27 bits. Expire when
  `elapsed ≥ T_ticks`.
- Bound: actual expiry ≤ T + tick + sweep period. Sweep visits one entry per cycle:
  256 entries × 16 ns = 4.1 µs at 62.5 MHz. For code 0001,
  90 + 1.024 + 4.1 ≈ 95.1 µs < 100 µs, and 90 > 50 µs. The same counter times the
  drain interval (§6): a quarantine of at most 64 s fits the 27-bit modular
  counter, because it is measured from `t_q`, not from `t_issue`. Every row must satisfy `min < T` and `T + tick + sweep < max`,
  with `T` rounded **up** to whole ticks. Prove this in a test that enumerates the
  table, not by inspection.
- **Control changes while outstanding.**
  - `limit_code` is latched at ISSUED. B3 §7.8.16 permits using the old value,
    with the start time taken from issue.
  - Expiry requires `latched_disable == 0 && cfg_dcommand2[4] == 0` (§2 register). Setting
    Disable suppresses expiry of every entry while it is set ("When Set, the
    Completion Timeout detection mechanism is disabled").
  - An entry issued while Disable = 1 stays non-expiring after a clear, which is
    "permitted but not required".
  - Disable never frees or quarantines tags.
- Same-cycle final completion and deadline: the completion wins. The sweep and
  the completion update use one arbitration point per entry. A completion that
  finishes the request blocks expiry in the same cycle.

## 9. Error reporting and host-visible status

> **Board integration: count only.** The
> CaptainDMA `pcie_7x_0` core is generated with `err_reporting_if=false` and
> AER off, so it exposes no `cfg_err_*` path. Until enabling error reporting is
> decided per donor (it changes the advertised capabilities), `req_err` runs
> with `aer_enabled=0`, its `cfg_err_*` outputs are unconnected and the core's
> inputs stay tied off. Events are queued and counted only; the RO "error
> reports" counter then counts queue emissions, not core-accepted reports. The
> **To the core** rules below are the specification for when reporting is
> enabled. On this board only the counting path is active.

**To the core** (`req_err`), per PG054 *Error Reporting Signals* / *Error Types*:

- Report at most one error per cycle, only while `cfg_pmcsr_powerstate == 2'b00`
  (D0). Out of D0, count each event as `ERR_SUPPRESSED_NOT_D0` and discard it.
  The standalone implementation applies this discard policy to queued events
  as well as arrivals. Queued events leave through normal FIFO dequeue accounting.
  An already reported first AER header can remain retained until acknowledged.
- `cfg_err_cpl_rdy` qualifies non-posted UR/Completer-Abort responses.
  The timeout, unexpected and malformed channels here use one report per
  cycle without that ready qualifier. [PG054 Error Reporting Signals](https://docs.amd.com/r/en-US/pg054-7series-pcie/Error-Reporting-Signals)
- Unexpected completion: `cfg_err_cpl_unexpect` with `cfg_err_posted = 1`; no
  header required (PG054 Table 2, received Completion row).
- Completion timeout: `cfg_err_cpl_timeout`, `cfg_err_no_recovery = 0` (ANFE),
  `cfg_err_posted = 0`. Decision:
  - Use ANFE, because recovery is delegated to the host software. It reissues
    reads after its own timeout, which B3 §6.2.3.2.4.4 permits.
  - `no_recovery = 1` was rejected. It would make the timeout an ERR_NONFATAL,
    and the OS may then reset the device. That is more disruptive than the
    missing completion, which is the observable failure today.
  - `no_recovery` is a core input, not donor config space, so fidelity does not
    constrain it.
  - A header-capture probe shows the core logs **no** AER Header Log for a
  Completion Timeout. The First Error Pointer points to CTO but the Header Log
  stays zero, which is spec-correct because no TLP exists for a timeout. So the
  tracker need not drive a header for timeouts; a supplied
  `cfg_err_tlp_cpl_header`/`cfg_err_aer_headerlog` is not captured for CTO.
- `cfg_err_aer_headerlog` stays 0 unless the generated core has AER enabled. When
  AER is enabled the header-log path **works** (verified: a UR positive control
  captured the supplied header into Header Log DW0–2). For the
  **header-bearing** completion errors the tracker reports (unexpected, poisoned, malformed),
  drive `cfg_err_aer_headerlog` with the offending completion's header; the core
  captures DW0–2 of a 3DW header. The RX representation has DW0 in bits [31:0].
  The observed core header-log port places DW0 in [127:96], so req_err reverses
  the DWORD order and zeros log DW3 for known 3DW headers.
- `cfg_err_aer_headerlog_set` is a level indicating first-error ownership.
  PG054 requires retaining the first header until ownership is established.
  Subsequent reports proceed while the level stays high. A masked error may
  leave the level low, so retention uses the live Uncorrectable Error Mask
  bits for CTO [14], Unexpected Completion [16] and Malformed TLP [18].
  A masked event reports without waiting for header ownership. If a retained
  class becomes masked, its hold releases and the next eligible report can
  establish a new hold. No arbitrary acknowledgement timeout is used.
  [PG054 AER Requirements](https://docs.amd.com/r/en-US/pg054-7series-pcie/AER-Requirements),
  [AMD AER mask semantics](https://docs.amd.com/r/en-US/am028-versal-gen2-register-reference/VF8_PF4_AER_CAP_VF_UNCORR_ERR_MASK_OFF-MMI_PCIE_CTRL_USP-Register)
- The live mask input must match the core's current configuration, including
  host writes. A constant donor mask cannot establish that after software changes
  it. Coherent live mask data, or an exact-core capture protocol that removes this
  dependency, is an integration requirement.
- Events queue in a 512-entry FIFO with room for three enqueues on one edge.
  Same-edge tie order is timeout, malformed, unexpected. Four independently
  written RAM banks preserve order while one event dequeues per available cycle.
  RX unexpected/malformed events can recur without entry transitions, so a
  universal no-overflow claim based only on 256 entries is insufficient.
  The bench retains a full 256-timeout burst plus 256 legal-rate RX events across
  a 512-clock AER hold and drains all 513 reports including the first report.
  Longer unacknowledged holds or excessive input rates can exhaust capacity.
  Newest excess events are then discarded with a saturating overflow count and
  sticky `ERR_QUEUE_OVERFLOW`, preserving older queued data. The queue resets
  only on link session abort.

The header-log positive control used `no_recovery=1` and unmasked errors.
Exact-core ANFE (`no_recovery=0`), mask changes, ownership latency and repeated
error-report acceptance are not covered by it.

**Transactions Pending (O12, decided).** The tracker drives
`cfg_trn_pending = (count_RESERVED + count_ISSUED) != 0`, excluding QUARANTINED
(B3 §7.8.5; PG054: "You must assert this input if the user application has not
received a completion"). It replaces the host-writable `rw[215]` source
(`pcileech_pcie_cfg_a7.sv`); that bit is no longer connected.
Software that follows the FLR procedure depends on this bit, which is what makes
the FLR drain rule in §6 safe.
Since board `req_table` count stage (timing), the counts are registered one
`clk_pcie` cycle behind the entry state, and the published RESERVED count adds an
admission still in that stage. The bit and the gate's NP-cap check therefore see
the current count or a higher one: the bit rises in the same cycle as before and
may clear one cycle later, never earlier (checked every cycle in
`tb_req_table_equiv`).

**To the host:** clk_pcie counters and stickies, made readable through the
existing status region (`pcileech_fifo.sv` `ro` map; synchronized to clk_sys as
a coherent snapshot, not bit by bit). Saturating 16-bit counters: admitted,
completed, timed_out, unexpected, malformed_cpl, superseded_dropped, and each
REFUSE_* reason; live counts of RESERVED/ISSUED/QUARANTINED; stickies
`ERR_BYPASS`, `ERR_RELEASE`, `DRP_STARTUP_FAIL`. The ro-map additions are a shared-file change.
The error queue additionally exposes reported/suppressed/overflow counts,
current occupancy, high-water occupancy and the overflow sticky for that snapshot.

## 10. Parent (generator) obligations

1. Typed Pydantic `RequesterTrackingPlan` in `src/device_clone/`. It carries the
   donor DevCap2 ranges and Disable Supported, the Extended Tag Supported bit, the
   live `CLK_PCIE_HZ`, the trim-digest/ECRC readbacks and the DRP plan of §11.
2. The emitter sets the SV parameters from the plan only. No defaults and no
   placeholder values.
3. Readback validation separates the **IP initial state** (what Vivado accepts) from
   the **post-startup state** (what the controller establishes). The existing
   strict `CONFIG.Cpl_Timeout_*` readback stays fatal for every key the plan does
   not hand to the controller. A key moves to controller ownership only together
   with §11's verified DRP word. Exact donor final values are
   required; there is no silent transform.
4. The build manifest records the tracker/controller source hashes, the plan, and
   the model-evidence hashes (§12).

The implemented emitter stages a required donor policy and a load-only Tcl
procedure; both ordinary board setup and the portable host constructor invoke
it after donor override. It resolves the actual user clock and digest settings,
requires the flow-control interface, and records a complete typed plan.

**Qualified ownership:** `captaindma100t-gen1-abcd-drp64-v1` permits
only initial Disable Supported=false → donor-final=true. The typed qualification
pins controller/A7/top hashes and model-evidence hashes. Both the donor override
and plan application require the selected source paths and matching bytes,
100T-1/Gen1 x1/64-bit/62.5-MHz endpoint, Vivado 2026.1/IP 3.3, ABCD, watchdog 64,
and Extended Tag/AER/ECRC/trim off. Unqualified builds keep strict readback;
ranges and every other mismatch remain fatal. The source directory is frozen
when the plan loads so relative callers may change directory safely.

Resolved records retain initial and final Disable values, qualification scope
`controller_model_only` and `startup_verified=false`. The portable success
publisher validates the known qualification record and keeps
`drp_startup_verified=false`; a model run is not runtime observation.

## 11. DRP startup controller (`drp_startup`, clk_sys)

Purpose: establish DevCap2 Completion Timeout Disable Supported, which Vivado
accepts but reads back false for this endpoint, through
the PG054-documented reset-held DRP update. It does this before the core
enumerates.

- **Gate:** `rst_pcie_n` (`pcileech_pcie_a7.sv`) becomes
  `cnt_done && !rst_pcie_src && drp_ok`. `drp_ok` clears on every
  `rst_pcie_src` assertion, so the controller re-runs in every core reset episode.
  Re-running every time makes attribute persistence across resets irrelevant
  (still record the observed behaviour in the model test). The PG054 1500 ns
  minimum assertion continues to hold, because the gate only lengthens assertion.
- **Sequence**, with `sys_rst_n` low throughout:
  1. Read word `0x019`.
  2. Check `(read & PLAN_CHECK_MASK) == PLAN_BASE_EXPECT`, which detects a core
     whose generator-owned fields differ from the plan (see below).
  3. Write `(read & ~PLAN_MASK) | PLAN_VALUE`.
  4. Read back, and require equality with the whole written word.
  5. Set `drp_ok`.
- `PLAN_MASK`, `PLAN_VALUE`, `PLAN_CHECK_MASK` and `PLAN_BASE_EXPECT` come from
  §10. For Disable Supported, `PLAN_MASK = 0x0200`.
- **Field check, not a whole-word base.**
  A whole-word `PLAN_BASE_EXPECT` needs a vendor-model run of the exact core,
  which ordinary users cannot perform. The check therefore covers only fields
  the generator itself configures: `PLAN_CHECK_MASK = 0x3C00`, the Completion
  Timeout Ranges Supported field, with `PLAN_BASE_EXPECT` equal to the planned
  ranges placed at bits 13:10. The remaining bits are neither checked nor
  changed. The write still touches only `PLAN_MASK`, and the whole-word
  readback verify is unchanged.
- **Field positions.** AMD's DRP address map for the `PCIE_2_1`
  library element defines word 0x019 identically in UG477 (March 1, 2011),
  Table 5-45, p. 195, and PG054 v3.3 (June 8, 2016), Table 3-48:

  | Bits | Attribute |
  | --- | --- |
  | [7:0] | `CLASS_CODE[23:16]` (word 0x018 holds `CLASS_CODE[15:0]`) |
  | [8] | `CMD_INTX_IMPLEMENTED` |
  | [9] | `CPL_TIMEOUT_DISABLE_SUPPORTED` |
  | [13:10] | `CPL_TIMEOUT_RANGES_SUPPORTED[3:0]` |
  | [14] | `DEV_CAP2_ARI_FORWARDING_SUPPORTED` |
  | [15] | `DEV_CAP2_ATOMICOP_ROUTING_SUPPORTED` |

  Both vendor model runs agree with this map: `0x3d00` is ranges 1111, INTx 1,
  class base 0x00; `0x0905` is ranges 0010, INTx 1, class base 0x05 (the vendor
  example's memory-controller class). Setting bit 9 alone set DevCap2 Disable
  Supported in both. Document hashes (SHA-256): UG477
  `145219e9ac33d3868bab24b87bdbe5a91283d7bb881f3868b3c291d58546d4a9`, PG054
  `de9e91fa0f0da5cd5557bb32f287c5f898f551303e29690852fef8610c780076`.
- If the donor's planned value equals the IP's readback, the controller still
  runs the read/verify steps and skips the write.
- **Failure:** a DRP ready timeout (explicit bounded watchdog policy)
  or any mismatch keeps `rst_pcie_n` low, sets sticky `DRP_STARTUP_FAIL`, and
  exposes the read word. It retries only on the next reset-source assertion. The
  device does not enumerate with an unverified capability.
  As built (ro +0x068): bit 4 is the sticky `DRP_STARTUP_FAIL`; bit 1 reports
  only whether the latest attempt failed and clears when a retry starts;
  +0x06A keeps the saturating failed-attempt count.
- **Watchdog policy:** the emitter selects 64 `clk_sys` cycles,
  matching the standalone controller regression fixture. This is an engineering
  fail-closed bound exercised by the actual controller against range-B and
  newly generated ABCD vendor models, **not a vendor-specified
  maximum**. PG054's DRP handshake description and timing diagrams provide no
  numeric response-time maximum; `16..65535` is an RTL parameter constraint.
  Seven responses per positive campaign took five clocks at a 100-MHz DRP
  clock, over three reset episodes; the missing-write negative control held
  reset. This does not establish a universal latency bound or complete A7 reset
  integration. A slower response leaves the endpoint in reset with a failure.
  [PG054 DRP read/write interface](https://docs.amd.com/r/en-US/pg054-7series-pcie/Writing-and-Reading-the-DRP-Interface).
- **Bounded host transactions:** a host DRP
  transaction is bounded by the same ready timeout. Without `drp_rdy` the bridge
  is answered `0xFFFF` and the timeout is counted (ro +0x068 bits 15:8). Every
  timeout, host or startup, is followed by one more timeout window that absorbs a
  late `drp_rdy`, so it cannot answer the next request.
- **Arbitration:** `drp_startup` owns `pcie_drp_*` while active; the host bridge
  (`pcileech_fifo.sv`) is muxed in only while it is idle. A host
  request issued during startup stays pending (the bridge waits for `drp_rdy`)
  and is serviced afterwards; the mux switches only between transactions.
  Host DRP **writes** to word `0x019` are refused, because they could silently
  undo the planned capability. Reads stay allowed.

## 12. Verification

Standalone, self-checking benches live in the board's `sim/r1` (`run_r1.py`,
`lint_pcie_a7.py` and the `mut_*` mutants) and `sim/subsys_reset`.

1. `req_table` / `req_gate` / `req_rx_cpl` unit benches cover every row of
   MSIX_TIMEOUT_SUPPORT.md *Acceptance cases*, plus:
   - REFUSE_* reasons (including `REFUSE_NP_CAP` at the O13 admission cap, §8:
     admit `N_np_max` non-posted requests, assert the next is refused, retire one,
     assert admission resumes, and `N_np_max` tracks the advertised NP credit)
   - Extended Tag toggling
   - Exhaustion and recovery only by session abort
   - Superseded delivery suppression
   - Same-cycle completion/deadline
   - Every-entry simultaneous expiry with `cfg_err` held off by power state
   - Timer table enumeration (§8)
   - Counter wrap
   - Pull-stream equivalence (no loss/duplication for untracked traffic)
2. Each listed mutant fails a distinct test (MSIX_TIMEOUT_SUPPORT.md
   *Mutants*), plus:
   - Rewrite the Requester ID
   - Free on `tx_err_drop`
   - Clear the table on `rst_subsys`
   - Deliver a superseded completion
   - Latch the limit at admission instead of ISSUED
   - Release `rst_pcie_n` before the DRP verify
   - Use the latched Disable only, so set-after-issue still expires
   - Free on EP: the poisoned read A/read B split (research O4.7, poisoned
     first, middle and final fragments, and a missing fragment)
   - Use lowest-FREE allocation instead of round-robin
   - Omit the O13 `N_np_max` cap (admit unboundedly), or size it from the tag
     width instead of the advertised NP credit
   - Use a persistent quarantine with no drain release: exhaust 32 tags, then
     require reads to resume without a link reset, after both a timeout and an
     FLR
   - Count QUARANTINED in `cfg_trn_pending`, or drive it from `rw[215]`
   - Use the popcount instead of the span for Length-1 Byte Count
   - Skip the TC/Attr match
   - Report Malformed via `cfg_err` for CRS or BCM
   - Use mid-window T: a delayed-transmission bench with withheld NP credits
     must show no premature expiry for every code within its tolerated `D`
3. Vendor-model benches extend the PIPE fixture from the Disable-control proof:
   - `drp_startup` with full **ABCD** ranges on the exact generated core
   - Repeated core resets
   - Host DRP traffic during startup
   - A failing-verify mutant
   - MRd round trips through `req_gate`/`req_rx_cpl` with tag restore
   - A forced timeout observed through the core's `cfg_err_cpl_timeout`
     acceptance and DevSta/AER bits
4. Generator unit tests cover the plan, emitter and readback, including
   policy-off refusal staying fatal for unowned keys.

## 13. Design questions and resolutions

Spec sources: [MSIX_PCIE_SPEC_RESEARCH.md](MSIX_PCIE_SPEC_RESEARCH.md), from
Base 3.0 text. Equivalence with 2.1 is reasoned from the revision history but not
verified.

| # | Item | Resolution |
| --- | --- | --- |
| O1 | Timeout windows, reserved values, control changes | **Closed** in §8 (windows confirmed; live-Disable rule adopted) |
| O2 | Byte Count/ordering/BE/TC/Attr rules | **Closed** in §4/§7 |
| O3 | DL_Down/FLR treatment | **Closed** in §6, except hot-reset propagation, which moves to O7 |
| O4 | Completion status handling | **Closed** in §7 (reserved = UR, CRS terminates, poison propagates) |
| O5 | `cfg_err_cpl_timeout` header usage and posted flag; ANFE/ERR_COR outcome with AER on and off | **Closed in-model** (AER on/off outcomes; CTO logs no header, UR control captures one) |
| O6 | Trim TLP Digest / ECRC check / `DISABLE_RX_POISONED_RESP` live properties | Live Vivado property readback |
| O7 | Live user clock, Extended Tag Supported, AER presence; hot reset reaching `rst_pcie_user` | Hot reset to core `user_reset_out` verified in the vendor model |
| O8 | Host DRP write restriction on word 0x019 | **Closed**: accepted |
| O9 | `cfg_err_no_recovery` | **Closed**: 0 / ANFE (§9) |
| O10 | Poisoned completion lifecycle | **Closed**: propagate (§7) |
| O11 | BCM | **Closed**: documented deviation (§7) |
| O12 | `cfg_trn_pending` ownership | **Closed**: tracker-driven (§9) |
| O13 | Accept→transmit delay `D` | **Characterized in-model**: gate enforces explicit `N_np_max` (§8) |
| O14 | FLR / timeout tag recovery | **Closed** by the drain interval (§6); the ordinary-timeout part rests on a stated assumption |
| O15 | CRS termination | **Closed** as a decision (terminate, no Malformed report). The normative reading remains unconfirmed |

Measurements behind O5 to O7 and O13 are in
[MSIX_CORE_MEASUREMENTS.md](MSIX_CORE_MEASUREMENTS.md).
