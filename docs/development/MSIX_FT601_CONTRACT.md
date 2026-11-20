# FT601 interface contract

The CaptainDMA 100T Gen1 board builds an MMCM FT601 PHY by default.
`--ft601-legacy-phy` selects the legacy PHY instead (`FT601_PHY_MMCM=0`).

## Implementation and limits

`pcileech_ft601_clk.sv` derives c0/c180 at 18°/198° and keeps the PHY in reset
until its clock/reset controller is ready. `pcileech_ft601_phy.sv` captures
DATA/flags on c0 and drives registered DATA/BE/strobes on c180. It aligns RX
validity and TX acceptance with the issued strobes and retains unaccepted TX
words in a 16-entry replay queue. The three-cycle RX-to-TX guard and queue
thresholds belong to this specific edge mapping.

The MMCM PHY keeps its synchronous reset asserted until the COM RX FIFO reports
write readiness after reset, latched for the session, so the first RX words are
not lost. The pad asynchronous reset is unchanged.

The FT601 I/O constraints use the datasheet limits without measured board
delays.

| Assumption | Basis | Limit |
| --- | --- | --- |
| FT601 releases DATA/BE within 25.5 ns of sampling OE_N high | The datasheet gives no OE-to-Hi-Z bound. The guard works at a 25 ns release and produces contention at 26 ns | The turnaround guard is not backed by a vendor bound |
| TXE_N remains high for at least two cycles after a refusal | Replay handles longer refusals; RTL detects acceptance after refusal with `tx_order_err` | Very short availability pulses are not covered. `tx_order_err` is a sticky status bit (`+022` bit 2); read it after a TX-heavy run |
| FT601 CLK meets MMCM duty/jitter limits | The datasheet gives only a typical 50% duty cycle and no jitter limit | Clock quality is assumed |
| Board clock/data/strobe delays fit routed margins | Timing analysis uses FPGA package delays and zero added board delay | Board skew is not budgeted |
| COM FIFOs obey the assumed response/reset behavior | Stream-level COM cases pass with structural FIFOs | Short or adjacent resets, TX reset continuity under traffic, and clock loss are not covered |

The clock/reset controller sequence is modeled. Silicon LOCKED/CLKINSTOPPED
latency is not bounded, and pad safety is not guaranteed throughout the
clock-loss detection interval. This is separate from the immediate asynchronous
pad release once `arst` asserts.

## Verified vendor facts

The [FTDI v1.06 datasheet](https://ftdichip.com/wp-content/uploads/2024/11/DS_FT600Q-FT601Q-IC-Datasheet.pdf)
was read for these facts. Section 4.3 describes active-low
RXF_N/TXE_N availability, OE_N-controlled slave DATA/BE drive, and RD_N/WR_N
access. Table 4.2 gives slave setup/hold 3.0/3.5 ns and master setup/hold
1.0/4.8 ns. At 100 MHz, this implies a 3.5..7.0 ns arrival window relative to
the preceding device clock edge, and output-delay constraints +1.0/-4.8 ns,
before board effects. These are timing limits, not an OE-to-Hi-Z specification.
The read transaction requirement includes draining up to 4 KB. This USB FIFO
transaction size is not a bound on an opaque host TLP frame.

The older [revision 1.05](https://www.ftdichip.cn/Support/Documents/DataSheets/ICs/DS_FT600Q-FT601Q%20IC%20Datasheet.pdf)
timing table agrees with the v1.06 values above. Its figures 4.6 to 4.8 place
OE_N assertion before RD_N and leave WR_N asserted briefly after TXE_N rises.
They help define test scenarios but give no numerical OE-to-Hi-Z bound. The
v1.05 mirror does not replace v1.06. FTDI's TN_191 was not readable, and
nothing from it is assumed.

## Required transfer and ownership contract

Define E_n as an FT601 rising clock edge at the device pins. A production
implementation must assign each sampled flag/data tuple and issued strobe to
its corresponding E_n. The I/O clock phase, capture stage and output stage
determine that mapping; a fabric FSM cycle number alone does not.

| Operation | Required order and acceptance condition |
| --- | --- |
| Idle | No read/write request; default pad ownership must be explicit |
| Enter RX | Release every FPGA DATA/BE driver before the slave can drive in response to OE_N |
| Read | Associate co-captured DATA, BE and RXF_N with the RD_N/OE_N values that reached the slave for that word; deliver each accepted word once |
| Leave RX | Stop requesting reads, retire accepted trailing words, deassert OE_N, then wait for a justified slave-release bound before enabling FPGA drive |
| Enter/write TX | Establish ownership and valid DATA/BE before an accepted WR_N edge; dequeue only the accepted word |
| TXE_N changes | Retain or replay every issued word until its acceptance is known; define the device's treatment of writes presented after availability changes |
| Reset/lock loss | Release pads and deassert OE_N/RD_N/WR_N even if the PHY clock has stopped; discard pending traffic only under the transport/session contract |
| Recovery | Synchronously release reset in each derived clock domain, wait for stable clock/lock and known ownership, then allow traffic |

These rows are acceptance requirements. The implementation's numerical guards
are conditional on the assumptions above.

The legacy PHY (`--ft601-legacy-phy`) violates the intended separation:
`pcileech_ft601.sv` derives T and OE_N from RXF_N and reset on the same clock edge; reset requests
drive rather than release the DATA/BE pads. With a stopped clock neither state
changes. Its equivalence tests preserve that behavior, so their passes do not
establish turnaround or reset safety.

## Asynchronous release with IOB packing

A standalone `pad_probe`
with a testbench, a synchronous-reset mutant, a physical wrapper, pin
constraints and a Vivado checker shows that asynchronous release coexists with
IOB packing.

All 32 data OFFs and 36 tristate FFs use FDPE with the same asynchronous PRE
and clock. T=1 releases the pad; data may preset to one while released. The
three control OFFs also preset inactive. A future production implementation
must synchronize reset deassertion and establish safe reset-source timing.

Results:

- A synchronous-reset mutant failed `ASYNC_RELEASE` with the clock stopped low.
- The proof passed **265 checks** with a local functional primitive model, then
  **265 checks** with the installed AMD FDPE model and `glbl` in Vivado 2026.1
  XSim. Cases include stopped-low/high clock, between-edge reset, reset-held
  input changes, release without an edge, 128 data patterns and Z drive.
- Vivado synthesis and place on `xc7a100tfgg484-1` placed **36 TFFs and 32
  paired data OFFs in OLOGIC**, with a common direct input-buffer PRE driver
  from `arst` and zero AVAL-250 violations. Data/TFF pairs were checked at each
  of 32 indices.
- The checker requires one common direct IBUF output whose input is `arst`.
  Ancestry alone could accept a gated reset. A gated-reset mutant reproduces the
  old false pass (`OLD_ANCESTRY_ACCEPTS=1`), and the checker rejects it for its
  LUT driver.

The wrapper uses the board's FT601 pin locations and a test-only counter. It has
no bus protocol and does not replace the routed full-board design.

## Open points

1. The read-prefetch/advance edge and TX acceptance around availability changes
   follow datasheet Figures 4.6 and 4.7 (v1.06). A read word is valid only where
   RXF_N and RD_N are both sampled low, and the first word is driven on OE_N
   alone. A write word is accepted only where WR_N and TXE_N are both low. The
   extra pipeline latency is modeled explicitly.
2. The datasheet gives no OE_N drive/Hi-Z time, no jitter limit and only a
   typical 50% duty cycle. The three-cycle turnaround guard is therefore not a
   proven bound. FPGA package delays are 81 to 162 ps (CLK on MRCC pin K18). No
   board trace data is published.
3. MMCM behavior on clock loss: LOCKED drops, and RST (at least 5 ns) is
   required after the clock returns. Lock takes up to 100 us. Only
   `MMCME2_ADV` exposes `CLKINSTOPPED`. LOCKED drop latency and CLKOUT behavior
   during loss are undocumented, so `c0`/`c180` stay untrusted while unlocked.
   A setup-only input capture has a +0.5 ns hold relationship (hold slack
   +3.823 ns), and `c0` to `c180` register-to-register timing at the full 36-pad
   T fan-out has +0.857 ns slack at zero board delay.
4. TX queue depth and acceptance delay derive from the chosen edge mapping and
   include COM requests already in flight.

The board's `sim/ft601/` bench scores accepted physical transfers and
contention for single words, bursts, refusal/replay, turnaround and stopped-clock
reset.
