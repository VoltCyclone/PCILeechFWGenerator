# Multi-BAR serving

The firmware serves the donor's register BAR and, when MSI-X lives elsewhere,
the donor's MSI-X BAR, each at its real PCI BAR index. Many NICs split the two.
For example, the Intel I350 has registers in a 64-bit BAR0 and the MSI-X table
and PBA in BAR3 (table at offset 0, PBA at `0x2000`, 10 vectors, 16 KiB).

## Model

`src/device_clone/served_bar.py` defines `ServedBar`, a validated record of one
served aperture:

- `index` (0-5), `size` (a power of two, at least 4 KiB for memory), and the
  captured `is_memory`, `is_64bit` and `prefetchable` bits.
- `role`: `registers`, `msix` or `registers+msix`.

`select_served_bars` picks the set from the capture:

- With no MSI-X capability, serve the primary register BAR alone.
- With MSI-X, the table and PBA must name the same implemented BAR. If that is
  the primary BAR, serve one BAR as `registers+msix`. Otherwise serve the
  primary as `registers` and the MSI-X BAR as `msix`.
- A table and PBA in different BARs, or a BIR with no unique implemented BAR,
  is an error. The generator never serves more than two BARs.

## Generation

- **PCIe IP:** `DonorPCIeIPConfig.served_bars` in
  `src/vivado_handling/pcie_ip_donor_override.py` enables each served BAR with
  its captured size and type, and disables the others. BAR apertures are set
  before the MSI-X BIR, so the BIR never points at a disabled BAR.
- **Controller:** `pcileech_tlps128_bar_controller.sv.j2` instantiates one
  owner per served BAR in `device_bar_slots`. The MSI-X subsystem takes the
  slot of its own BAR. The template raises an error when the MSI-X layout
  disagrees with the served descriptor.
- **Routing:** the PCIe core reports the matched BAR in
  `m_axis_rx_tuser[8:2]`. The board's `pcileech_pcie_a7.sv` latches it as
  `bar_hit`, so the generated controller selects the owner without any board
  RTL change.
- **Shared MSI-X BAR:** in `registers+msix` mode the MSI-X table and PBA ranges
  go to the MSI-X subsystem and everything else to the register owner.

Tests: `tests/test_multibar_serving.py`.

## Limits

- Registers outside the MSI-X table and PBA in a served BAR are modeled only
  when the capture explains them. Config-space behavior learning does not
  cover registers the capture cannot explain (see
  [MSIX_STATUS.md](MSIX_STATUS.md)).
- Whether the PCIe IP accepts very large donor BAR sizes is bounded by the IP,
  not by the generator. A refused size fails in the Stage 3 build.
