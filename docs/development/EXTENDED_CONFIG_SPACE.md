# Known issue: extended configuration space is always zero

**Status:** open, affects every build (live, container and offline) on every
board. Tracked in the issue linked from the pull request that added this page.

## Symptom

The generated `pcileech_cfgspace.coe`, which initialises the config-space shadow
BRAM (`bram_pcie_cfgspace`), contains zeros for offsets `0x100`–`0xFFF`. The
donor's PCIe extended capabilities (AER, LTR, L1 PM Substates, ACS, ARI, DSN,
SR-IOV and so on) are never cloned, even when the capture contains them.

## Cause

Since the context consolidation in #281, `src/templating/sv_context_builder.py`
replaces `context["config_space"]` with a six-field summary (vendor, device,
class, revision, command, status). The template
`src/templates/sv/pcileech_cfgspace.coe.j2` (lines ~333–368) emits donor
extended space only when `config_space.raw_data` holds at least 4096 bytes, so
it always falls through to its "no extended config space data" branch.

`config_space_init.hex` does carry the full bytes, but no board build reads it:
every board's Vivado flow initialises the shadow from the COE.

## Impact

- **Enumeration is safe.** A zero header at `0x100` is a valid "no extended
  capabilities" terminator.
- **Fidelity is lost.** Drivers see no AER, LTR or ASPM L1SS, silently disable
  those features and report fewer errors. The clone is easy to tell apart from
  the donor by its empty extended space.
- **Placement matters.** All 14 board XCIs enable the core's own DSN capability
  at `0x100` and set `EXT_PCI_CFG_Space_Addr = 0x043`, so user extended space,
  and the shadow's responses, start at `0x10C`. Bytes the COE places at
  `0x100`–`0x10B` are shadowed by the hard DSN.

## Fixing it

The fix is not a one-liner. Passing `raw_data` through would copy the donor
chain verbatim to `0x100`, behind the core's DSN, leaving it unreachable or
misparsed. A correct fix needs to:

1. Keep the donor bytes in the template context (`raw_data`) instead of the
   six-field summary.
2. Relocate the donor's extended capability chain to start at `0x10C`, rewrite
   every `next` pointer, and link the core DSN's `next` to it (or drop the donor
   DSN, which the core already provides).
3. Decide per capability whether a static clone is safe. Capabilities that
   imply behaviour the FPGA does not implement (SR-IOV, ATS, PRI, PASID, Resizable
   BAR, DPC) should be omitted or neutralised rather than advertised.
4. Extend the writemask so cloned registers are read-only except where the spec
   requires RW/RW1C bits (for example AER status) that the shadow can honour.
5. Add rendering tests (COE bytes at `0x10C+` match the relocated chain, chain
   walk terminates, no overlap with the core DSN) and a hardware check
   (`lspci -vvv` shows the cloned capabilities; the device still enumerates and
   its driver binds).

Because this changes the firmware of every live build, it should land in its own
pull request with hardware validation on at least one 7-series board.
