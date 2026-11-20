# MSI-X and requester support

Overview of what the generated firmware serves and the current limits. The
linked documents hold the contracts.

## Features

- **Config space:** cloned config space served from the shadow BRAM, with an
  explicit writable policy (writemask) and a COE in shadow byte order.
- **MSI-X:** served from the donor's own BAR, including multi-BAR layouts.
  The donor IP override sets BAR apertures before the MSI-X BIR.
- **Requester tracking** on the CaptainDMA 100T. This covers tag allocation,
  completion timeout ranges from DevCap2, and Disable Supported through the DRP
  startup controller. The controller is pinned to the released board sources.
- **FT601 PHY:** MMCM clocking, with `--ft601-legacy-phy` as the opt-out.
- **Packet-safe FIFO admission:** whole-packet reservation in the TX and RX
  FIFOs.
- **Reset, abort and drain** across the PCIe subsystem and COM.
- **Stage 3 build:** a single path, `build_all.tcl`, behind hash-checked
  Stage 2 inputs. These gates must pass before the bitstream is written:
  - routed timing;
  - clock coverage (async board I/O counts only if it is a board false path);
  - DRC;
  - CDC: the board's pinned, endpoint-scoped checker where the board ships
    one, otherwise the blanket "no Critical" rule.
- **Board coverage:** the reset and CDC fixes are on the common-lineage boards:
  CaptainDMA 75T x1, EnigmaX1, PCIeSquirrel, ScreamerM2, GBOX, ZDMA 100T and
  ac701.

## Contracts and design records

- [MSIX_PACKET_CONTRACT.md](MSIX_PACKET_CONTRACT.md)
- [MSIX_FIFO_ADMISSION.md](MSIX_FIFO_ADMISSION.md)
- [MSIX_REQUESTER_CONTRACT.md](MSIX_REQUESTER_CONTRACT.md)
- [MSIX_FT601_CONTRACT.md](MSIX_FT601_CONTRACT.md)
- [MSIX_MULTIBAR_DESIGN.md](MSIX_MULTIBAR_DESIGN.md)
- [EXTENDED_CONFIG_SPACE.md](EXTENDED_CONFIG_SPACE.md)
- [OFFLINE_CAPTURE.md](OFFLINE_CAPTURE.md) and
  [CAPTURE_FORMAT.md](CAPTURE_FORMAT.md)
- [TUI_BUILD_CONTRACT.md](TUI_BUILD_CONTRACT.md)
- [MSIX_TIMEOUT_SUPPORT.md](MSIX_TIMEOUT_SUPPORT.md)
- [MSIX_PCIE_SPEC_RESEARCH.md](MSIX_PCIE_SPEC_RESEARCH.md)
- [MSIX_CORE_MEASUREMENTS.md](MSIX_CORE_MEASUREMENTS.md)

## Limits

- **FT601 timing assumptions:** OE_N release within 25.5 ns, TXE_N high for at
  least 2 cycles, clock duty and jitter, and board skew are assumed, not
  bounded by the datasheet (see [MSIX_FT601_CONTRACT.md](MSIX_FT601_CONTRACT.md)).
- **Timing margins are placement-sensitive.** Rerun the seven-directive sweep
  after any RTL change to the 100T. The target is at least 0.3 ns per internal
  clock.
- **Register behavior:** registers the capture cannot explain are not
  emulated. Config-space behavior learning, or a latched-status stopgap, does
  not cover them.
- **COM session:** a mid-word cut can complete one TLP with idle DWs. Fixing
  that needs a host-side change.
- **MSI-X producer:** no event source drives `irq_valid`, so the generated
  controller ties it low and never sends a message. The table, PBA and mask
  reads and writes work.
- **Board coverage:**
  - Requester tracking exists on the CaptainDMA 100T only.
  - The reset and CDC fixes are not ported to CaptainDMA 35T, NeTV2,
    acorn_ft2232h, pciescreamer or sp605.
  - Boards do not share a `common/src/` tree yet.
- **Pin scope:** `pcileech_fifo.sv` drives `pcie_rst_core` and the host DRP
  bridge, but it is not in the startup qualification's pinned source set.
