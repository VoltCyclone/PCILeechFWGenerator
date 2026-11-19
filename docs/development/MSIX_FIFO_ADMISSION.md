# FIFO admission: whole-packet reservation

The CaptainDMA 100T reserves room for a whole packet in the existing FIFOs
before admitting it. This settles the FIFO capacity question in
[MSIX_PACKET_CONTRACT.md](MSIX_PACKET_CONTRACT.md).

## The problem

With the reset rules in the packet contract, packets are admitted whole or dropped whole at every reset, and
nothing is spliced across a reset. A packet can still be split when the FIFO
fills **inside** a packet that was already admitted.

| Path | FIFO (FIFO Generator 13.2, independent clocks) | Behavior without reservation when it fills mid-packet |
| --- | --- | --- |
| TX, host to PCIe (`src_fifo`) | data: block RAM, 1024 × 134; token: distributed RAM, 16 × 1 | Admission checks `full` only at the first DW. Later beats, which may include the last, are lost, but the token is still written. The reader then forwards a truncated packet, or reads on into the next packet. **The PCIe core receives a malformed or spliced TLP.** |
| TX token race | token FIFO | A packet is admitted with 15 tokens queued. The previous packet's token then fills the FIFO, so this packet's token is dropped. From then on, every packet waits for the next packet's token, until a reset. |
| RX, PCIe to host (`dst_fifo`) | block RAM, 2048 × 134 | Continuation beats are refused once the FIFO is full. The host sees a prefix and then a FIRST-led packet. LeechCore 2.23+ drops the prefix; older hosts append it to the next TLP. |

The data FIFO fills only when the reader is stalled: the PCIe core holds off
TX, or USB stops draining RX. That is rare, but it is a load condition, not a
fault, so it must not produce a malformed TLP.

## Rule

**Reserve room for a whole packet before admitting it.** The existing vendor
FIFOs stay as they are, and the guarantee comes from their documented
programmable-full flag.

1. **Admission.** A packet is admitted at its first beat only if the FIFO's
   `prog_full` is low. The threshold is a single constant:

   `threshold = usable_depth − MAX_BEATS − PIPE`

   - `usable_depth`: the FIFO's actual depth.
   - `MAX_BEATS`: the most beats one admitted packet can write.
   - `PIPE`: writes already committed when the flag was last updated. This
     covers the flag's update latency and the previous packet's final beat,
     still in the packer.

   On an independent-clock FIFO, `prog_full` counts writes at once and reads
   only after they cross into the write domain. It can therefore over-report
   occupancy but never under-report it. So if it is low at admission, at least
   `MAX_BEATS` slots are free. Only one packet is written at a time, so nothing
   else can take that room before this packet ends.
2. **`MAX_BEATS` comes from the PCIe specification, not from a guess about
   the host.** The largest legal TLP is 4 header DWs, 1024 payload DWs and 1
   digest DW: 1029 DWs, or 258 beats of 4 DWs.
3. **Cap.** The writer counts the DWs of each admitted packet. At DW 1029 it
   forces `last` and drops the rest of the source packet up to its own last.
   So no admitted packet can outgrow its reservation, even an illegal one. The
   cap only ever applies to a packet that already breaks the specification.
   For TX, the core drops a TLP whose length does not match its header
   (PG054, `tx_err_drop`). For RX, the core never delivers one larger than
   its MPS.
4. **Token.** The TX token FIFO uses its own `prog_full`, with room for this
   packet's token plus the pending one. This closes the race in the table
   above.
5. **The `full` checks stay** as a backstop, and the reset rules
   (first-beat admission, reset windows, drain) are unchanged.

### Numbers

As implemented, with `PIPE` = 2:

| FIFO | Usable depth | `prog_full` threshold | Free when the flag is low |
| --- | --- | --- | --- |
| `src_fifo` data (`fifo_134_134_clk2_rxfifo`) | 1023 | 763 (75 %) | ≥ 1023 − 762 − 2 = 259 beats |
| `src_fifo` token (`fifo_1_1_clk2`) | 15 | 13 | ≥ 15 − 12 − 2 = 1 token |
| `dst_fifo` (`fifo_134_134_clk2`) | 2047 | 1787 (87 %) | ≥ 2047 − 1786 − 2 = 259 beats |

The capacity bench measures the budget directly. It counts true occupancy
from the model pins, and at every write with `prog_full` low it asserts that
occupancy is at most threshold − 1 + 2. Over 18 runs the data FIFOs' flag
was never more than 1 write behind. The token flag was always ahead, because
reads cross into `clk_sys` slowly.

The token FIFO drains into `pkt_count` on every `clk_pcie` cycle. It fills
only when 1-DW packets arrive faster than `clk_pcie`, so the token
reservation matters only at slow `clk_pcie`.

**Throughput does not change.** The threshold binds only when the FIFO is
already about three-quarters full, which means the reader is the bottleneck.
A packet refused there is refused whole, where it was previously cut.

## Alternatives considered

| Option | Why not |
| --- | --- |
| One-slot store-and-forward | It stops the reader overlapping the writer, so throughput drops sharply on long bursts. It also needs a new packet RAM. |
| Reserve by the TLP header's Length field | That trusts the host's header. A wrong Length would break the guarantee, and the packet contract forbids sizing from a header. |
| Custom gray-coded occupancy counters | This duplicates what FIFO Generator already provides and verifies, and adds a custom CDC crossing to dispose of. |
| Rely on the host-side contract alone | It is acceptable for RX with LeechCore 2.23+, but leaves the TX split, which can put a malformed TLP on the link, and the token race. |
| A bigger FIFO | It makes the fill less likely but guarantees nothing. |

## Implementation

- **IP:** `Programmable_Full_Type` is
  `Single_Programmable_Full_Threshold_Constant` on `fifo_134_134_clk2`,
  `fifo_134_134_clk2_rxfifo` and `fifo_1_1_clk2`.
  - Vivado 2026.1 locks these 2023.2.2 XCIs, so they are edited as text.
    The edit reproduces Vivado's own reconfiguration of the upgraded IP, value
    for value.
  - `Enable_Safety_Circuit` must stay `false`. Setting the threshold with
    `set_property -dict` in 2026.1 also turns it on for the block-RAM FIFOs.
    That changes the reset structure the reset windows were designed for.
  - Re-export the functional FIFO models for the vendor benches.
- **RTL:** in `src_fifo`, the admission term, the DW cap and the token
  threshold. In `dst_fifo`, the admission term and the DW cap. About 40 lines.
- **CDC:** no new crossing. `prog_full` is computed inside the IP, in the
  write domain. Confirm with `sim/cdc/check_cdc.tcl`; the CDC-15 count does
  not change for block-RAM FIFOs.
- **Timing:** rerun the 7-directive sweep.

## Verification

`sim/fifo_capacity` runs 18 cases and kills 5 mutants. The vendor-model benches
(`sim/subsys_reset` style, with the real FIFO Generator models) cover:

- a reader stalled for long periods, with back-to-back packets up to
  `MAX_BEATS`, at unequal and stopped clocks;
- oversize source packets, both TX and RX: capped, framed, and with no effect
  on the next packet;
- many short packets, so the token FIFO fills.

| Mutant | Must fail with | As run |
| --- | --- | --- |
| Admit on `!full` (the rule without reservation) | split packet | `tx_admit_on_full`, `rx_admit_on_full`: killed |
| Threshold off by `PIPE` | split packet under the flag-latency case | Replaced by the occupancy check above. The threshold is baked into the gate-level model, so it cannot be mutated in RTL. |
| No DW cap | split packet on an oversize packet | `tx_no_cap`, `rx_no_cap`: killed |
| Token admitted on `!full_tok` | lagging token, packet held | `tx_token_unreserved`: killed |
