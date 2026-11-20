# PCIe spec research for the requester tracker (O1–O4)

Normative background for [MSIX_REQUESTER_CONTRACT.md](MSIX_REQUESTER_CONTRACT.md).
It quotes the PCIe Base and PG054 text behind each rule the tracker enforces and
records where the sources leave a question open. It is specification research.
MSI-X messages are posted Memory Writes and
do not wait for the read completions the tracker follows (PG054 Table 3-44,
p. 139).

Four findings drove the contract's design:

| Priority | Risk | Resolution in the contract |
| --- | --- | --- |
| P1 | Freeing a tag on a poisoned partial completion lets later fragments match a new request (O4.7). | Poison propagates; a tag frees only when `remaining == 0` (contract §7). |
| P1 | Persistent tombstones after an FLR exhaust the tags and leave reads unavailable (O3.2). | Bounded drain interval releases quarantined tags (contract §6). |
| P2 | Accepted `s_axis_tx` data can wait inside the hard IP, so a timer started at accepted `tlast` can expire early (O1.6). | Timer targets the top of each window, and an NP admission cap bounds the wait (contract §8). |
| P2 | The spec does not settle CRS for a memory read (O4.3). | Terminate without a Malformed report; the reading is recorded as an assumption (contract §7). |

## Sources and confidence labels

| Key | Source | Notes |
| --- | --- | --- |
| **B3** | *PCI Express Base Specification Revision 3.0*, 10 Nov 2010 (full text, 860 pp.). Third-party mirror: <https://github.com/peterhu/eBooks/blob/master/PCI%20Express%C2%AE%20Base%20Specification%20Revision%203.0.pdf> | Not an official PCI-SIG distribution. Its revision history states 3.0 = 2.1 + 8 GT/s + errata + four listed ECNs, none of which touch the sections below. Page numbers are the printed page numbers. |
| **PG054** | AMD/Xilinx PG054 *7 Series FPGAs Integrated Block for PCI Express v3.3*, 23 Dec 2022. <https://www.xilinx.com/support/documents/ip_documentation/pcie_7x/v3_3/pg054-7series-pcie.pdf> | The core states compliance with Base 2.1. **Revision caveat:** the docs.amd.com HTML edition (v3.3, 2024-12-06) numbers tables differently ("Table 2" versus "Table 3-40/3-41" here). Table and page citations in these docs refer to the PDF edition named in this row. |
| **LNX** | Linux `include/uapi/linux/pci_regs.h` (master). <https://raw.githubusercontent.com/torvalds/linux/master/include/uapi/linux/pci_regs.h> | Field masks only: DEVCTL2 Completion Timeout Value = `0x000f`, Completion Timeout Disable = `0x0010`, DEVCAP2 Disable Supported = `0x00000010`. These agree with B3. |
| **INTEL** | Intel Arria 10 SR-IOV user guide, *PCI Express and PCI Capabilities*. <https://www.intel.com/content/www/us/en/docs/programmable/683686/20-4/pci-express-and-pci-capabilities.html> | Restates the DevCap2 range table. It agrees with B3. |

Labels used below:

- **normative-quoted**: verbatim from B3 (Base 3.0).
- **secondary-consistent**: vendor guide or derived reasoning that agrees with B3.
- **unverified**: memory or inference that has no quoted source.

**About Base 2.1 versus 3.0.** The Base 2.1 PDF was not available. B3 is
2.1 plus errata, the 8 GT/s additions and the four ECNs its history lists
(OBFF, ASPM Optionality, End-End TLP Changes for RCs, Protocol Multiplexing).
None of these is about completion timeout, completion formation, FLR or
DL_Down. The 2.1 wording is therefore expected to be identical, but that is
**unverified**. PG054 names Base 2.1 as its reference.

**About later revisions** (all **unverified**, from memory):

- Base 4.0 added 10-bit tags. Out of scope here (§5 of the contract).
- Base 6.0 renamed CRS to "Request Retry Status (RRS)".
- The register sections were renumbered: Device Capabilities 2 / Device Control 2
  became §7.5.3.15 / §7.5.3.16 in 4.0+.

No later change to the encodings or rules quoted below is known. That has not
been checked against later spec text.

---

## O1. Completion Timeout Value, Ranges, Disable (B3 §7.8.15–7.8.16)

### O1.1 Ranges (Device Capabilities 2 [3:0]): **normative-quoted**

B3 §7.8.15, Table 7-24, p. 650:

> Completion Timeout Ranges Supported – This field indicates device Function
> support for the optional Completion Timeout programmability mechanism. […]
> This field is applicable only to Root Ports, Endpoints that issue Requests on
> their own behalf, and PCI Express to PCI/PCI-X Bridges that take ownership of
> Requests issued on PCI Express. […]
> Four time value ranges are defined:
> Range A: 50 µs to 10 ms · Range B: 10 ms to 250 ms · Range C: 250 ms to 4 s ·
> Range D: 4 s to 64 s
> 0000b Completion Timeout programming not supported – the Function must
> implement a timeout value in the range 50 µs to 50 ms.
> 0001b Range A · 0010b Range B · 0011b Ranges A and B · 0110b Ranges B and C ·
> 0111b Ranges A, B, and C · 1110b Ranges B, C, and D · 1111b Ranges A, B, C, and D
> All other values are Reserved.
> It is strongly recommended that the Completion Timeout mechanism not expire in
> less than 10 ms.

DevCap2[4], Completion Timeout Disable Supported (p. 650/651):

> A value of 1b indicates support for the Completion Timeout Disable mechanism.
> The Completion Timeout Disable mechanism is required for Endpoints that issue
> Requests on their own behalf and PCI […]

The text is cut off at the page break. The required-for-Endpoints wording is
quoted. The rest of the sentence is about bridges.

INTEL restates the same table (**secondary-consistent**).

### O1.2 Value encodings (Device Control 2 [3:0]): **normative-quoted**

B3 §7.8.16, Table 7-25, p. 655:

> A Function that does not support this optional capability must hardwire this
> field to 0000b and is required to implement a timeout value in the range 50 µs
> to 50 ms. Functions that support Completion Timeout programmability must
> support the values given below corresponding to the programmability ranges
> indicated in the Completion Timeout Ranges Supported field.
> Defined encodings:
> 0000b Default range: 50 µs to 50 ms
> It is strongly recommended that the Completion Timeout mechanism not expire in
> less than 10 ms.
> Values available if Range A (50 µs to 10 ms) programmability range is supported:
> 0001b 50 µs to 100 µs · 0010b 1 ms to 10 ms
> Values available if Range B (10 ms to 250 ms) […]: 0101b 16 ms to 55 ms ·
> 0110b 65 ms to 210 ms
> Values available if Range C (250 ms to 4 s) […]: 1001b 260 ms to 900 ms ·
> 1010b 1 s to 3.5 s
> Values available if the Range D (4 s to 64 s) […]: 1101b 4 s to 13 s ·
> 1110b 17 s to 64 s
> Values not defined above are Reserved. […]
> The default value for this field is 0000b.

**Result:** every window in contract §8 matches B3 exactly. Every chosen T lies
strictly inside its window, and the 0000b choice of 25 ms satisfies the ≥ 10 ms
recommendation.

### O1.3 Reserved or unsupported values: **normative-quoted** (definition); handling is **unverified**

- DevCtl2 says only "Values not defined above are Reserved." It gives no hardware
  rule for a reserved value being present.
- The general definition, B3 *Terms and Acronyms* (front matter), p. 34:

  > Reserved encodings for register and packet fields must not be used. Any
  > implementation dependence on a Reserved field value or encoding will result
  > in an implementation that is not PCI Express-compliant.

- Nothing in B3 says what hardware does when software writes a defined encoding
  outside the advertised ranges. Software is required not to do it ("must support
  the values … corresponding to the programmability ranges indicated").

**Conclusion:** the spec gives no mandated hardware behaviour for either case.
The contract's "treat as 0000b" (default 50 µs–50 ms) is an implementation
choice and does not violate the spec. Two alternatives:

- Make the field bits read-only for unsupported encodings. That is up to the hard
  core's DevCtl2 implementation, not the tracker.
- Keep the last legal value.

The contract should present "treat as 0000b" as a design choice, not as a spec
rule.

### O1.4 Changing the Value while Requests are outstanding: **normative-quoted**

B3 §7.8.16, p. 655:

> Software is permitted to change the value in this field at any time. For
> Requests already pending when the Completion Timeout Value is changed,
> hardware is permitted to use either the new or the old value for the
> outstanding Requests, and is permitted to base the start time for each Request
> either on when this value was changed or on when each request was issued.

Latching `limit_code` per entry at ISSUED is explicitly permitted. **Confirmed.**

### O1.5 Completion Timeout Disable (Device Control 2 [4]): **normative-quoted**, with one finding

B3 §7.8.16, p. 656:

> Completion Timeout Disable – When Set, this bit disables the Completion Timeout
> mechanism. […] Software is permitted to Set or Clear this bit at any time. When
> Set, the Completion Timeout detection mechanism is disabled.
> If there are outstanding Requests when the bit is cleared, it is permitted but
> not required for hardware to apply the completion timeout mechanism to the
> outstanding Requests. If this is done, it is permitted to base the start time
> for each Request on either the time this bit was cleared or the time each
> Request was issued.
> The default value for this bit is 0b.

**Finding.** The text gives the old/new freedom only for **clearing** the bit.
For **setting** it, the only sentence is the present-tense "When Set, the
Completion Timeout detection mechanism is disabled."

A per-entry latch alone would let a request issued with Disable = 0 expire
after software sets Disable = 1, which a strict reading forbids. The contract's
rule:

- An entry may expire only if `latched_disable == 0` **and** live
  `cfg_dcommand2[4] == 0`.
- An entry issued while Disable = 1 stays non-expiring after a clear. The spec
  permits that ("permitted but not required").

This adds one AND gate in the sweep and costs nothing else.

### O1.6 Related rules from §2.8 and §6.2: **normative-quoted**

§2.8, p. 151:

> PCI Express device Functions that issue Requests requiring Completions must
> implement the Completion Timeout mechanism. […] The Completion Timeout
> mechanism is activated for each Request that requires one or more Completions
> when the Request is transmitted.

The normative anchor is transmission. Equating it with the contract's ISSUED /
accepted `s_axis_tx` `tlast` is **not confirmed**. PG054 *Core Buffering and Flow
Control*, pp. 93–99, describes packets accepted into transmit buffers and
Non-Posted packets waiting for link-partner credits. AXI acceptance alone does
not prove that a request has been transmitted.

**Finding (O13).** A timer started at accepted `tlast` can expire while the
request is still queued in the hard IP, or leave too little of the window after
transmission. The tag stays owned while queued. The contract bounds the wait
with an NP admission cap and biases the timer to the top of each window
(contract §8); simulation measures the behavior, and silicon `T_fc` is still to
be confirmed.

§2.8, p. 151:

> A Memory Read Request for which there are multiple Completions must be
> considered completed only when all Completions have been received by the
> Requester. If some, but not all, requested data is returned before the
> Completion Timeout timer expires, the Requester is permitted to keep or to
> discard the data that was returned prior to timer expiration.

This matches retiring an entry only when `remaining == 0`. Partial data already
delivered to the host is permitted. **Confirmed.**

§6.2.3.2.4.4 *Requester with Completion Timeout*, p. 472:

> The Requester is permitted to attempt recovery zero, one, or multiple (finite)
> times, but must signal the error (if enabled) with an uncorrectable error
> Message if no further recovery attempt will be made. If the severity of the
> Completion Timeout is non-fatal, and the Requester elects to attempt recovery
> by issuing a new request, the Requester must first handle the current error
> case as an Advisory Non-Fatal Error.

PG054, `cfg_err_no_recovery` (Table 2-18):

> For a Completion Timeout, you can elect not to attempt the Request again. […]
> assertion causes the corresponding error to not be regarded as ANFE.

PG054 Table 3-40 gives the consequence:

- `no_recovery = 0`: ANFE, so ERR_COR only with AER and no message without it.
- `no_recovery = 1`: ERR_NONFATAL, or the AER-severity choice.

This is **secondary-consistent**.

**Decision (contract §9).** The contract sets `cfg_err_no_recovery = 0`
(ANFE). By the spec, ANFE is correct only when the Requester re-issues the
request, and the FPGA has no retry; recovery is delegated to host software,
which reissues reads after its own timeout. `no_recovery = 1` is the literal
reading, but it produces an ERR_NONFATAL the OS AER handler may react to, which
is more disruptive than the missing completion.

---

## O2. Completion formation for Memory Read (B3 §2.2.5, §2.2.9, §2.3.1.1)

### O2.1 Byte Count meaning and encoding: **normative-quoted**

§2.2.9, p. 97:

> Byte Count[11:0] – The remaining byte count for Request. The Byte Count value
> is specified as a binary number, with 0000 0000 0001b indicating 1 byte,
> 1111 1111 1111b indicating 4095 bytes, and 0000 0000 0000b indicating 4096
> bytes.

§2.3.1.1, p. 115:

> For each Memory Read Completion, the Byte Count field must indicate the
> remaining number of bytes required to complete the Request including the
> number of bytes returned with the Completion, except when the BCM bit is 1b.
> […] If a Memory Read Request is completed using multiple Completions, the Byte
> Count value for each successive Completion is the value indicated by the
> preceding Completion minus the number of bytes returned with the preceding
> Completion.

Both points are **confirmed**: 0 means 4096, and the check
`ByteCount == e.remaining` holds on every completion.

### O2.2 Length encoding: **normative-quoted**

§2.2.2, Table 2-4, p. 61: `00 0000 0000b → 1024 DW`. **Confirmed.**

### O2.3 Total byte count from Length and byte enables: **normative-quoted**

§2.3.1.1, Table 2-32, p. 117:

| 1st DW BE | Last DW BE | Total Byte Count |
| --- | --- | --- |
| 1xx1 | 0000 | 4 |
| 01x1 | 0000 | 3 |
| 1x10 | 0000 | 3 |
| 0011, 0110, 1100 | 0000 | 2 |
| 0001, 0010, 0100, 1000 | 0000 | 1 |
| **0000** | 0000 | **1** |
| xxx1 / xx10 / x100 / 1000 | 1xxx / 01xx / 001x / 0001 | Length×4 − (leading disabled in 1st BE) − (trailing disabled in Last BE), ranging from Length×4 down to Length×4 − 6 |

**Key result for the contract.** For Length = 1 the Byte Count is the **span**
from the lowest enabled byte to the highest enabled byte, inclusive. It is
**not** the number of set bits. For example, 1001b gives 4 and 0101b gives 3.

The contract's `remaining` for Length = 1 ("count of bits in a contiguous First
BE") is correct only for contiguous enables. Replace it with the span:
`msb_index(FirstBE) − lsb_index(FirstBE) + 1`, and 1 for 0000b. This lets the
contract support non-contiguous Length-1 reads instead of refusing them.

The multi-DW formula in contract §4 matches the table. It also covers
non-contiguous enables in QW-aligned Length-2 requests (see O2.6), because the
table only uses the leading and trailing disabled counts.

### O2.4 Lower Address: **normative-quoted**

§2.2.9, p. 98:

> For Memory Read Completions, the value in this field is the byte address for
> the first enabled byte of data returned with the Completion.

§2.3.1.1, p. 118:

> For the first (or only) Completion, the Completer can generate this field from
> the least significant 5 bits of the address of the Request concatenated with 2
> bits of byte-level address formed as shown in Table 2-33.
> For any subsequent Completions, the Lower Address field will always be zero
> except for Completions generated by a Root Complex with an RCB value of 64
> bytes. In this case the least significant 6 bits of the Lower Address field
> will always be zero and the most significant bit of the Lower Address field
> will toggle according to the alignment of the 64-byte data payload.

Table 2-33 maps 1st DW BE to Lower Address[1:0]:

| 1st DW BE | Lower Address[1:0] |
| --- | --- |
| 0000 | 00 |
| xxx1 | 00 |
| xx10 | 01 |
| x100 | 10 |
| 1000 | 11 |

**Confirmed:**

- `next_lower_addr = {addr[6:2], first_enabled_offset}`, with offset 0 for a
  zero-length read and for any BE with bit 0 set (including non-contiguous ones).
- `next_lower_addr += bytes_this (mod 128)` gives the right value for subsequent
  completions. Each subsequent completion starts on an RCB boundary, so the value
  is 0 or, for RCB 64, 0/64.

### O2.5 Splitting, RCB and ordering: **normative-quoted**

§2.3.1.1, p. 114–115:

> Read Completion Boundary (RCB) determines the naturally aligned address
> boundaries on which a Read Request may be serviced with multiple Completions.
> For a Root Complex, RCB is 64 bytes or 128 bytes […] For all other System
> Elements, RCB is 128 bytes.
> Completions for Requests which do not cross the naturally aligned address
> boundaries at integer multiples of RCB bytes must include all data specified
> in the Request.
> […] The first Completion must start with the address specified in the
> Request, and must end at one of the following: […] the entire Request […] an
> address boundary between the start and end of the Request at an integer
> multiple of RCB bytes. The final Completion must end with the address specified
> in the Request plus the length specified by the Request. All Completions
> between, but not including, the first and final Completions must be an integer
> multiple of RCB bytes in length.
> Receivers may optionally check for violations of RCB. If a Receiver
> implementing this check determines that a Completion violates this rule, it
> must handle the Completion as a Malformed TLP.
> **Multiple Memory Read Completions for a single Read Request must return data
> in increasing address order.**

The rule for completion contents, §2.3.1.1, p. 114:

> Individual Completions for Memory Read Requests may provide less than the full
> amount of data Requested so long as all Completions for a given Request when
> combined return exactly the amount of data Requested […] Completions must not
> include more data than permitted by Max_Payload_Size.

**Confirmed:** completions arrive in increasing address order. A peer that is not
a Root Complex uses RCB 128, and an RC uses 64 or 128. The contract's split check
is therefore correct for both RCB values. An optional RCB-alignment check is
permitted. If implemented, a violation must be treated as **Malformed**, not as
Unexpected. That would need `cfg_err_malformed`, which PG054 Table 2-18 provides.
The contract currently does not check RCB alignment, which is permitted
("optionally").

### O2.6 Byte-enable legality: **normative-quoted**

§2.2.5, p. 69–70:

> If the Length field for a Request indicates a length of greater than 1 DW,
> this field [1st DW BE] must not equal 0000b. […] [Last DW BE] If the Length
> field for a Request indicates a length of 1 DW, this field must equal 0000b. If
> the Length field for a Request indicates a length of greater than 1 DW, this
> field must not equal 0000b.
> Non-contiguous Byte Enables (enabled bytes separated by non-enabled bytes) are
> permitted in the 1st DW BE field for all Requests with length of 1 DW. […]
> **Non-contiguous Byte Enables are permitted in both Byte Enables fields for QW
> aligned Memory Requests with length of 2 DW (1 QW).**
> All non-QW aligned Memory Requests with length of 2 DW (1 QW) and Memory
> Requests with length of 3 DW or more must enable only bytes that are contiguous
> with the data between the first and last DW of the Request.

**Finding.** Non-contiguous enables are legal for a Length-1 read and also for
QW-aligned (`addr[2] == 0`) Length-2 memory reads. The multi-DW Byte Count
formula still holds for them, so the gate admits them (contract §4).

### O2.7 Other request rules that the gate's admission checks rely on: **normative-quoted**

- §2.2.7, p. 77: "For Memory Read Requests, Length must not exceed the value
  specified by Max_Read_Request_Size".
- §2.2.7, p. 78: "Requests must not specify an Address/Length combination which
  causes a Memory Space access to cross a 4-KB boundary."
- §2.2.4.1, p. 67: "For Addresses below 4 GB, Requesters must use the 32-bit
  format. The behavior of the receiver is not specified if a 64-bit format
  request addressing below 4 GB […] is received."

Checks 5 and 6 in contract §3 are **confirmed**.

### O2.8 Zero-length read: **normative-quoted**

§2.2.5, p. 70:

> If a Read Request of 1 DW specifies that no bytes are enabled to be read (1st
> DW BE[3:0] field = 0000b), the corresponding Completion must specify a Length
> of 1 DW, and include a data payload of 1 DW. The contents of the data payload
> within the Completion packet is unspecified and may be any value.

Table 2-32 gives Byte Count = 1 for BE 0000/0000, and Table 2-33 gives Lower
Address[1:0] = 00.

**Confirmed:** one CplD, Length 1, Byte Count 1, Lower Address = `{addr[6:2], 00}`.
The payload is garbage by definition. A non-SC status still produces a Cpl
without data, as for any read.

### O2.9 BCM: **normative-quoted**; the requester obligation is an Implementation Note, so it is not strictly normative

§2.2.9, p. 97:

> BCM – Byte Count Modified – this bit must not set by PCI Express Completers,
> and may only be set by PCI-X completers

Footnote 19: "Only PCI-X completers set the BCM bit to 1b."

Implementation Note *BCM Bit Usage*, p. 116:

> A PCI Express Memory Read Requester needs to correctly handle the case when a
> PCI-X Bridge/PCI-X Completer sets the BCM bit. When this occurs, the first Read
> Completion packet returned to the Requester will have the BCM bit set,
> indicating that the Byte Count field reports the size of just that first packet
> instead of the entire remaining byte count. The Requester should not conclude
> at this point that other packets of the Read Completion are missing. The BCM
> bit will never be set in subsequent packets of the Read Completion […]

**Finding.** A BCM = 1 completion cannot be treated as malformed: the
Implementation Note says the requester "needs to correctly handle" it. Root
Complex memory never sets BCM. A PCI-X completer is reachable only by
peer-to-peer reads through a PCIe-to-PCI-X bridge, which pcileech does not
target. The contract takes the documented deviation: drop the completion
without a `cfg_err` and count it (contract §7). Supporting BCM on the first
completion only (`bytes_this = 4*Length − LowerAddr[1:0]`, no
`ByteCount == remaining` check) would be the alternative if peer-to-peer reads
came into scope.

### O2.10 Header fields that must match: **normative-quoted**

§2.2.9, p. 99:

> Completion headers must supply the same values for the Requester ID, Tag, and
> Traffic Class as were supplied in the header of the corresponding Request.
> Completion headers must supply the same values for the Attribute as were
> supplied in the header of the corresponding Request, except as explicitly
> allowed when IDO is used.

§2.3.2 calls a mismatch in "Attributes, Traffic Class, Byte Count, Lower
Address" a match failure (see O4.5). The contract does not compare TC or Attr.
`hdr_log` already stores them, so add comparisons on TC and on Attr[1:0]
(RO, NS). Exclude the IDO bit.

---

## O3. DL_Down, link down, hot reset and FLR

### O3.1 DL_Down at the Endpoint (Upstream Port): **normative-quoted**

§2.9.1, p. 151–153:

> For a Port with DL_Down status, the Transaction Layer is not required to accept
> received TLPs from the Data Link Layer, provided that these TLPs have not been
> acknowledged by the Data Link Layer.
> […] For an Upstream Port, DL_Down status is handled as a reset by: returning
> all PCI Express-specific registers, state machines and externally observable
> state to the specified default or initial conditions (except for registers
> defined as sticky […]) • discarding all TLPs being processed

The Root Port side (Downstream Port), same section:

> initializing back to their default state any buffers or internal states
> associated with outstanding requests transmitted Downstream […] discarding all
> Completions submitted by the device core for Transmission

The Root Port side also says this about Non-Posted Requests "already being
processed … for which it may not be practical to return Completions":

> These cases are handled by the Requester using the Completion Timeout
> mechanism.

**Conclusion:**

- DL_Down is a reset of the Endpoint's transaction state. Freeing every entry on
  a link session abort matches "returning … state machines … to … initial
  conditions". **Confirmed (normative-quoted).**
- Neither end delivers completions that were queued at DL_Down: the RP discards
  completions submitted while it is DL_Down, and the EP discards TLPs being
  processed.
- The contract also states that "pre-abort completions cannot arrive
  afterwards", that is, after DL_Up. **No sentence in B3 states this directly.**
  It is **secondary-consistent**: an inference from the two discard rules, plus
  link retraining taking milliseconds, which is far longer than a completer's
  service time. There is a residual theoretical window in which the RC completer
  submits a completion after DL_Up for a request it received before DL_Down. I
  found no text that forbids it.
- Suggested contract wording: "all entries go to FREE (B3 §2.9.1, Upstream Port
  DL_Down handled as reset). Stale completions after DL_Up are not expected (both
  sides discard on DL_Down), but they are not normatively impossible. The tag
  allocator restarts at the lowest tag, so the residual risk is accepted and
  documented."

**Hot reset:** B3 handles a Switch's DL_Down by propagating hot reset, and an
Endpoint receiving hot reset is a conventional reset. PG054 Table 2-7, p. 16,
explicitly documents `user_reset_out` assertion for Hot Reset and Link Disable;
it also documents `user_lnk_up` deassertion in those states. The documented
behaviour is therefore established. O7 still needs to verify that the exact
generated core and integration propagate that reset to the tracker and clear
the intended state. Distinguish that integration check from an unknown vendor
interface rule.

### O3.2 FLR: **normative-quoted**

§6.6.2, p. 510–511:

> Function registers and Function-specific state machines must be set to their
> initialization values […]
> Note that the controls that enable the Function to initiate requests on PCI
> Express are cleared, including Bus Master Enable, MSI Enable, and the like
> […] The Link state must not be affected by an FLR.

This includes Device Control 2: CTV returns to 0000b and Disable returns to 0b.
Max_Payload_Size and RCB are in the exception list. **MRRS and Extended Tag
Enable are not**, so they reset to their defaults.

§6.6.2, p. 511:

> If software initiates an FLR when the Transactions Pending bit is 1b, then
> software must not initialize the Function until allowing adequate time for
> any associated Completions to arrive, or to achieve reasonable certainty that
> any remaining Completions will never arrive. For this purpose, it is
> recommended that software allow as much time as provided by the pre-FLR value
> for Completion Timeout on the device. […]
> Note that upon receipt of an FLR, a device Function may either clear all
> transaction status including Transactions Pending or set the Completion
> Timeout to its default value so that all pending transactions will time out
> during FLR execution. Regardless, the Transactions Pending bit must be clear
> upon completion of the FLR.

§6.6.2, p. 512:

> While an FLR is in progress: […] If a Completion arrives, the Completion is
> permitted to be handled as an Unexpected Completion or to be silently
> discarded (following update of flow control credits) without logging or
> signaling it as an error.

§2.3.2, p. 120–121, the same rule plus the rule for after the FLR:

> Once the FLR has completed, received Completions corresponding to Requests
> issued prior to the FLR must be handled as Unexpected Completions, unless the
> Function has been re-enabled to issue Requests.

Implementation Note *Avoiding Data Corruption From Stale Completions*, p. 513:

> An FLR causes a Function to lose track of any outstanding non-posted Requests.
> Any corresponding Completions that later arrive are referred to as being
> "stale". […] any stale Completions that arrive afterwards may cause data
> corruption by being mistaken by the Function as belonging to Requests issued
> since the FLR.

Software is expected to wait on Transactions Pending before the FLR.

**Answers:**

- **May pre-FLR completions arrive after FLR?** Yes. The spec defines them
  ("stale") and makes their avoidance a software responsibility.
- **What must the requester do with them?**
  - During the FLR: Unexpected or silent discard, the implementation's choice.
  - After the FLR, before re-enable: **must** be Unexpected.
  - After re-enable: the spec gives no rule, because the function cannot tell
    them apart.

  Reporting a tombstoned completion as Unexpected during FLR is permitted.
  Retaining the tombstone prevents that tag from matching a new request while
  it is retained. This supports the discard/reporting rule, but does **not**
  establish compliance of the whole persistent-quarantine policy.
- **Completion Timeout for requests outstanding at FLR?** The function may
  either clear all transaction status, which means no timeout, or let pending
  requests time out at the default value during the FLR. The contract's "no
  timeout reported" is option 1. **Confirmed (normative-quoted).**

**P1 finding (O14).** B3 §6.6.2, p. 512, also requires the Function to be
usable by its normal drivers after FLR. Consider 32 issued reads whose
completions never arrive. If FLR moved their tags to persistent QUARANTINED
entries, no tags would be left for new reads when Extended Tag Enable is clear,
and a missing completion cannot release a tombstone. The contract therefore
bounds quarantine with a drain interval (contract §6). After an FLR the
interval is the pre-FLR Completion Timeout, which §6.6.2 tells software to wait
out. After an ordinary timeout it rests on a stated assumption: no completion
arrives more than one further timeout window after expiry. Immediate freeing
and an invented quarantine duration were both rejected.

### O3.3 Transactions Pending: **normative-quoted**; this is a new contract gap

§7.8.5 Device Status [5], p. 621:

> Transactions Pending – Endpoints: When Set, this bit indicates that the
> Function has issued Non-Posted Requests that have not been completed. A
> Function reports this bit cleared only when all outstanding Non-Posted
> Requests have completed or have been terminated by the Completion Timeout
> mechanism. This bit must also be cleared upon the completion of an FLR.

PG054 Table 2-17 (**secondary-consistent**):

> `cfg_trn_pending` Input: User Transaction Pending: If asserted, sets the
> Transactions Pending bit in the Device Status Register. Note: You must assert
> this input if the user application has not received a completion to an
> upstream request.

The board originally drove `cfg_trn_pending` from a host-writable register bit
(`rw[215]` in `pcileech_pcie_cfg_a7.sv`), not from request state. The tracker now
drives `cfg_trn_pending = (count_RESERVED + count_ISSUED) != 0` and excludes
QUARANTINED, whose requests were terminated by timeout or cleared by FLR
(contract §9). Without this, software that follows the FLR algorithm would not
wait for real outstanding reads. RESERVED counts, which is conservative but
harmless.

### O3.4 Effect of FLR on tag-space and request limits (inference from O3.2): **secondary-consistent**

FLR clears BME, Extended Tag Field Enable and MRRS, and resets CTV/Disable to
their defaults. The contract already reads Extended Tag and MRRS live at each
allocation (§3, §5) and latches CTV at ISSUED. Requests already QUARANTINED by
the FLR are not affected.

---

## O4. Completion status handling at the requester (B3 §2.3.2, §6.2)

### O4.1 Status encodings: **normative-quoted**

§2.2.9, Table 2-29, p. 98:

| Code | Status |
| --- | --- |
| 000 | SC |
| 001 | UR |
| 010 | CRS |
| 100 | CA |
| all others | Reserved |

### O4.2 Reserved status: **normative-quoted**

§2.3.2, p. 121:

> Completions with a Reserved Completion Status value are treated as if the
> Completion Status was Unsupported Request (UR)

The tracker treats a reserved status exactly like UR: the entry goes to FREE, and the completion is
delivered unless the entry is superseded or QUARANTINED. Whether to deliver the
completion unchanged or with status rewritten to 001 is a host-API choice.
Unchanged keeps the "only Tag changes" rule. Requester-side logging is the same
as for UR (O4.4).

### O4.3 CRS for a non-configuration request: **normative-quoted**

§2.3.2, p. 121:

> Completions with a Configuration Request Retry Status in response to a Request
> other than a Configuration Request are illegal. Receivers may optionally report
> these violations as Malformed TLPs. This is a reported error associated with the
> Receiving Port.

The termination interpretation is not settled by the text. The termination rule
(§2.3.2, p. 120) covers "Completions with a Completion Status other than
Successful Completion **or** Configuration Request Retry Status (in response to
Configuration Request only)". By the plain grammar, a CRS for a memory read
falls under "other than SC", which terminates the request. Because the TLP is
illegal the reading stays ambiguous (**unverified**: no clarifying text was
found). The contract (O15) terminates the entry and sends no Malformed report:
Malformed TLP has Fatal default severity, so an ERR_FATAL could make the OS
reset the link over an optional report. If the intended reading is
drop-and-timeout, a later completion becomes Unexpected and the contract's
allocation and match backstop applies. Reporting Unexpected is not what §2.3.2
names for this case, although it is permitted for match failures in general
(O4.5).

### O4.4 UR and CA for a memory read: **normative-quoted**

§2.3.2, p. 120:

> Completions with a Completion Status other than Successful Completion or
> Configuration Request Retry Status (in response to Configuration Request
> only) must cause the Requester to: Free Completion buffer space and other
> resources associated with the Request. Handle the error via a
> Requester-specific mechanism (see Section 6.2.3.2.5).

§2.3.2, p. 122:

> When a Read Completion […] is received with a Completion Status other than
> Successful Completion: No data is included with the Completion […] This
> Completion is the final Completion for the Request. The Requester must
> consider the Request terminated, and not expect additional Completions.
> Handling of partial Completions Received earlier is implementation specific.

§2.3.1.1, p. 114: "In this case, the value in the Length field is undefined, and
must be ignored by the Receiver."

§2.3.1.1, p. 118: Byte Count and Lower Address are set "as if the Completion
Status were Successful Completion".

§6.2.3.2.5, p. 473:

> If the Requester needs to report the error, the Requester must do so solely
> through a Requester-specific mechanism. […] The Requester is not permitted to
> report the error using PCI Express logging and error Message signaling.

§2.3.2, p. 122: "Completions with a Completion Status of Unsupported Request or
Completer Abort are reported using the conventional PCI reporting mechanisms
(see Section 7.5.1.2)". Status register, p. 592: Received Target Abort is set on
a CA completion, and Received Master Abort is set on a UR completion.

PG054 Table 3-26 says the core sets these bits itself: "Set if a Completion with
status Unsupported Request is received" (**secondary-consistent**). The tracker
therefore does nothing more for them.

**Confirmed:** UR/CA terminates the request (→ FREE) and is not reported through
`cfg_err`. The contract's "unexpected only if QUARANTINED" is right. A UR/CA
for a QUARANTINED entry no longer matches an outstanding request, so it is
Unexpected (O4.6).

Two refinements:

- The tracker must **ignore Length** on a UR/CA completion.
- It may check Byte Count and Lower Address against `remaining` and
  `next_lower_addr` "as if SC". A mismatch is then a match failure (O4.5).

### O4.5 Match failure, Malformed versus Unexpected: **normative-quoted**

§2.3.2, p. 120:

> If a received Completion matches the Transaction ID of an outstanding
> Request, but in some other way does not match the corresponding Request
> (e.g., a problem with Attributes, Traffic Class, Byte Count, Lower Address,
> etc), it is strongly recommended for the Receiver to handle the Completion as
> a Malformed TLP. However, if the Completion is otherwise properly formed, it
> is permitted for the Receiver to handle the Completion as an Unexpected
> Completion.

Footnote 22:

> For the case where only the Byte Count or Lower Address fields mismatch the
> expected values for a Memory Read Request, it is actually recommended for the
> Receiver to handle the Completion as an Unexpected Completion, since the
> mismatch might be caused by a previous Completion being misrouted.

**Confirmed:** report Byte Count and Lower Address mismatches as Unexpected,
which is the recommended choice. For TC/Attr mismatches (O2.10), Malformed is
strongly recommended and Unexpected is permitted. The contract's single
"unexpected" outcome is compliant.

### O4.6 Unexpected Completion and late completions: **normative-quoted** (definition); late-after-timeout is **secondary-consistent**

§2.3.2, p. 120:

> When a device receives a Completion which does not match the Transaction ID
> for any of the outstanding Requests issued by that device, the Completion is
> called an "Unexpected Completion." […] The agent receiving an Unexpected
> Completion must discard the Completion. An Unexpected Completion is a reported
> error associated with the Receiving Port.

§6.2.3.2.4.5, p. 473: a non-fatal Unexpected Completion "must [be handled] as an
Advisory Non-Fatal Error".

**After a timeout:** B3 does not say "late completion" in so many words.
Device Status, p. 621, says requests "terminated by the Completion Timeout
mechanism" are no longer outstanding. A late completion therefore matches no
outstanding Transaction ID and is by definition Unexpected.

PG054 *Completion Timeouts* states it outright (**secondary-consistent**):

> If a Completion is later received after a request times out, the user
> application must treat it as an Unexpected Completion.

**Confirmed:** QUARANTINED + completion means drop and report Unexpected.

PG054 also says that completions received while the core is in a non-D0 power
state are consumed by the core as Unexpected (Table 3-11) and never reach
`req_rx_cpl`. Entries outstanding across a D-state change will therefore time out.

### O4.7 Poisoned completions for a memory read: **normative-quoted**

§2.7.2.2, p. 150:

> it may be desirable for the Requester to use data marked poisoned in
> Completions – such use is also not forbidden. The appropriate use of poisoned
> information is application specific.

§6.2.3.2.4.3, p. 472:

> Another example is a Requester that receives a poisoned Memory Read Completion
> TLP. If the Requester propagates the poisoned data internally or handles the
> error like it would for a Completion with UR/CA Status, it signals the error
> (if enabled) with an ERR_COR. If the Requester does not handle the poison in a
> manner that permits continued operation, it signals the error (if enabled)
> with ERR_NONFATAL.

§6.2.3.2, p. 470: if a completion is both unexpected and poisoned, "it is
recommended that Unexpected Completion be reported, and that Poisoned TLP
Received not be reported".

Status register, p. 592: Master Data Parity Error is set if Parity Error
Response = 1 and the Endpoint receives a Poisoned Completion.

PG054 (**secondary-consistent**):

- Status bits 8 and 15 are set by the core on a received poisoned completion.
- `cfg_err_poisoned` is "Normally, only used if attribute
  DISABLE_RX_POISONED_RESP=TRUE". By default the core reports received poisoned
  TLPs itself.
- `m_axis_rx_tuser[1]` (rx_err_fwd) marks the packet.

**Finding.** The poisoned completion still **matches** an outstanding request and
carries valid header fields: Byte Count, Lower Address and Length are meaningful.
The spec allows two compliant treatments:

- Use or propagate the poison. Progress the entry, and deliver to the host with
  EP preserved or with a host-visible flag. The ERR_COR / ANFE path applies.
- "Handle the error like it would for a Completion with UR/CA Status", which
  means terminating the request. This is also ERR_COR / ANFE.

The contract (O10) propagates the poison: the entry progresses like any other
fragment and the completion is delivered with EP intact. Dropping it without
progress would turn one data error into a Completion Timeout and hold the tag
until the deadline.

**Freeing on EP is unsafe for partial completions (O10).** EP marks poisoned data; an SC CplD carrying EP is not a
terminal UR/CA status. B3 §2.3.1.1's terminal-status rule does not require the
completer to stop sending the remaining fragments merely because one SC CplD
is poisoned. The following is a counterexample derived from the tag
allocator and matching rules, not a simulated RTL result:

1. Read A requests 256 bytes at `0x1000`, using internal tag 0. The first
   128-byte CplD has EP = 1, Byte Count = 256 and Lower Address = 0.
2. If the tracker frees tag 0 immediately, a new 128-byte read B at `0x2000`
   can obtain tag 0. Its expected Byte Count is 128 and Lower Address is 0.
3. A's delayed second 128-byte fragment has tag 0, Byte Count = 128 and Lower
   Address = 0. With matching TC/Attr it passes B's header checks and can be
   delivered as B's data. The header carries no full address to distinguish it.

Application-level failure and tag reclamation are therefore separate. With
poison propagated, each fragment is validated and progressed normally and the
tag frees only after the whole response has completed. An abandoned read would
need a drain/quarantine record and tag reclamation only after a validated final
completion, a terminal error completion, or a justified recovery boundary;
missing fragments must not trigger immediate reuse.

`DISABLE_RX_POISONED_RESP` is read back so the core's automatic poison
reporting can be confirmed (O6/O7). B3 §6.2.3.2.4.3 supports handling the
application error like UR/CA; it does not prove that immediate tag reuse is
safe.

---

## Decisions taken in the contract

Each open item raised by this research and where the requester contract settles
it.

| # | Item | Decision |
| --- | --- | --- |
| O1 | Timeout windows, reserved values, control changes | Window table confirmed; live Disable suppresses expiry (contract §8) |
| O2 | Byte Count, ordering, byte enables, TC/Attr | Table 2-32/2-33 formulas, non-contiguous enables admitted for Length 1 and QW-aligned Length 2, TC/Attr matched (contract §4, §7) |
| O3 | DL_Down, hot reset, FLR | Link abort frees all entries; FLR quarantines with a drain interval (contract §6) |
| O4 | Completion status handling | Reserved is UR; UR/CA free the entry; CRS terminates (O15); poison propagates (O10) |
| O9 | `cfg_err_no_recovery` | 0 (ANFE); recovery delegated to host software (contract §9) |
| O10 | Poisoned completion lifecycle | Propagate and progress; free only at `remaining == 0` (contract §7) |
| O11 | BCM | Documented deviation: drop and count (contract §7) |
| O12 | `cfg_trn_pending` ownership | Tracker-driven from RESERVED + ISSUED (contract §9) |
| O13 | Timeout start versus transmission | Timer at accepted `tlast`, biased high, with an NP admission cap (contract §8) |
| O14 | FLR recovery | Bounded drain interval (contract §6) |
| O15 | CRS for a memory read | Terminate, no Malformed report (contract §7) |

Verification of these rules lives in the board's `sim/r1` benches and mutants
(contract §12).
