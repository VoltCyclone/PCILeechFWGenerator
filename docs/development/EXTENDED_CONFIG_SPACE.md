# Extended configuration space cloning

The generated `pcileech_cfgspace.coe` initializes the config-space shadow BRAM
(`bram_pcie_cfgspace`). Offsets `0x100`-`0xFFF` carry the donor's PCIe extended
capabilities (AER, LTR, L1 PM Substates, ACS, ARI and so on), relocated behind
the core's own Device Serial Number capability.

## Layout

All 14 board XCIs enable the core's DSN capability at `0x100` and set
`EXT_PCI_CFG_Space_Addr = 0x043`. User extended space, and the shadow's
responses, therefore start at `0x10C`. A donor chain copied verbatim to `0x100`
would sit behind the hard DSN and be unreachable or misparsed.

`build_relocated_extended_config` in `src/device_clone/extended_config.py`
rebuilds the chain:

- It writes a DSN header at `0x100` whose next pointer is `0x10C`.
- It drops the donor's own DSN, because the core provides one.
- It copies the remaining donor capabilities contiguously from `0x10C`, each
  DWORD-aligned, and rewrites every next pointer. The last one gets 0.
- It drops any capability that no longer fits in 4 KB instead of truncating it.
- It clears the Next Function Number in an ARI capability, because the clone
  is single-function.
- It returns images of 256 bytes or fewer unchanged.

`src/device_clone/pcileech_context.py` applies it when the captured config
space is the full 4096 bytes. `src/templates/sv/pcileech_cfgspace.coe.j2` emits the relocated bytes
from `config_space.raw_data` when at least 4096 bytes are present. Otherwise it
writes a null header at `0x100` and zeros after it.

## Omitted capabilities

The generated core cannot honor these, so the clone omits them instead of
advertising behavior the FPGA lacks:

| ID | Capability |
| --- | --- |
| `0x0008`, `0x0009` | Multi-Function VC (a single-function clone must not advertise multi-function topology) |
| `0x0010` | SR-IOV |
| `0x000F` | ATS |
| `0x0013` | PRI |
| `0x001B` | PASID |
| `0x0015` | Resizable BAR |
| `0x001D` | DPC |

## Limits

- A zero header at `0x100` is a valid "no extended capabilities" terminator, so
  enumeration is safe even when a capture lacks extended space. The clone then
  looks different from the donor, and drivers disable AER, LTR and ASPM L1SS.
- Cloned registers are static. Only bits the writemask marks writable respond
  to host writes.
- Tests: `tests/test_extended_config.py`,
  `tests/test_extended_config_coe.py` and `tests/test_ext_cfg_pointers.py`.
