# Core measurements

Results of the vendor-model and Vivado measurements behind the requester
(overview: [MSIX_STATUS.md](MSIX_STATUS.md)). The
[requester contract](MSIX_REQUESTER_CONTRACT.md) owns the policy decisions.
These measurements set implementation inputs and limits. They are model results
on the Gen1 x1 candidate cores.

## O6/O7: observed live properties

The read-only query opens existing projects with Vivado `open_project
-read_only`, calls `list_property` and `get_property`, and closes without saving.
It does not inspect static XCI/XML metadata. Vivado 2026.1 ran it.

| Live property | Existing `candidate-gen1` / `pcie_7x_0` | Existing PIPE project / `pipe_probe_core` |
| --- | --- | --- |
| Project part | `xc7a100tfgg484-1` | `xc7a100tfgg484-1` |
| `CONFIG.Device_Port_Type` | `PCI_Express_Endpoint_device` | same |
| `CONFIG.Link_Speed` / `Maximum_Link_Width` | `2.5_GT/s` / `X1` | same |
| `CONFIG.User_Clk_Freq` | `62.5` MHz | same |
| `CONFIG.Extended_Tag_Field` / `Extended_Tag_Default` | `false` / `false` | same |
| `CONFIG.Disable_Rx_Poisoned_Resp` | `false` | same |
| `CONFIG.RECRC_Check` / `RECRC_Check_Trim` | `0` / `false` | same |
| `CONFIG.AER_Enabled` | `true` | `false` |
| `CONFIG.Cpl_Timeout_Range` / `Cpl_Timeout_Disable_Sup` | `Range_B` / `false` | same |

Consequences:

- The observed clock and Extended Tag settings support a 62.5 MHz / 32-tag
  candidate. Final generation must bind its own live readback, not assume this
  observation applies to every generated core.
- Automatic poison response is enabled. The selected propagation policy still
  needs actual reporting verification in the final integration.
- Digest trimming and ECRC checking are off. Restoring a completion tag cannot
  silently preserve a stale ECRC. The contract's clear-TD/remove-digest path is
  required unless final generated settings deliberately establish another
  verified path.
- The PIPE control and candidate differ in AER. Results on one are not evidence
  of the other's error-reporting outcome.
- Both inspected projects retain range B and Disable Supported false. They are
  baseline projects, not the final donor-ABCD/DRP-startup implementation, so
  they do not establish donor capability fidelity.

## O5: error reporting

A copied testbench exercises the core's external error inputs and observes
endpoint status and root-port error-message reception, with a no-recovery error
as the positive control and an omitted stimulus that must fail a distinct
assertion. AER-on needs a separately generated model, because AER-off cannot
stand in for it.

Both the AER-off model and a separately generated AER-on variant passed all six
cases plus the expected missing-pulse rejection:

- With `no_recovery=0`, the AER-off model sent no error message and the AER-on
  model sent ERR_COR.
- With `no_recovery=1`, both sent ERR_NONFATAL.
- Posted and header48 variants changed neither outcome nor generated a Cpl
  without data.
- Device Status and the AER-on CTO/ANFE status bits were checked through host
  configuration reads.

The first probe showed the 128-bit AER Header Log reading zero. A
header-capture probe settled it: the core logs no Header Log for a
Completion Timeout, with the First Error Pointer pointing at CTO, which is
spec-correct because no TLP exists for a timeout. The tracker therefore drives no
header for timeouts (contract §9).

## O13 and hot reset

**First probe: inconclusive.** A copied testbench drove the endpoint's TX
interface and gated the root port's NP application delivery, with four NPH
credits selected. The 16 queued MRds all crossed PIPE while application delivery
stayed zero, so the fixture never withheld on-wire credits and the required
positive gate failed (`R1_NO_WITHHELD_REQUEST`). It proved neither a large nor a small accept-to-
transmit delay `D`. Earlier probes failed on instrumentation: the example's
idle watchdog and an inactive support-wrapper PIPE clock. The active PIPE clock
in this example is the core's `pcie_top_i.pipe_clk`.

**Revised delay probe: measured.** A revised probe measured a constant NP-credit round trip
`T_fc` of about 1136 ns in the vendor model. That gives
`D ≈ (j_ahead / C) · T_fc`, which the requester bounds with the `N_np_max`
admission cap (contract §8).

**Hot Reset (vendor model).** Hot
Reset asserts `user_reset_out`, holds it while `pl_received_hot_rst` is high,
drops `user_lnk_up` and resets config space (contract §6).
