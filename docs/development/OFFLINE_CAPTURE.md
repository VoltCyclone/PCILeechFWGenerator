# Validated offline capture workflow

Collect a donor once on Linux, then validate, import and render its saved data
without the donor being connected. Install the Python dependencies first; offline
commands do not install packages, require root or run the Linux/VFIO preflight.
The examples below use the source-checkout CLI.

## Separate stages

```sh
# Linux donor host: replace the address with the actual donor's address.
sudo -E .venv/bin/python pcileech.py capture collect \
  --bdf 0000:03:00.0 --output ./captures/donor-new

# Copy both JSON files together, then validate on the development machine.
.venv/bin/python pcileech.py capture validate --input ./captures/donor-new

# Optional: validate and publish an immutable copy in a fresh directory.
.venv/bin/python pcileech.py capture import --bdf 0000:03:00.0 \
  --input ./captures/donor-new --output ./captures/imported

# Render artifacts locally, without collection or Vivado.
.venv/bin/python pcileech.py build --from-capture ./captures/imported \
  --board pcileech_75t484_x1 --templates-only --local --output ./rendered
```

The capture supplies the BDF; an explicit `--bdf` must match. `capture import`
requires `--bdf` so an import always names the donor it expects. Offline builds use
local templating by default. Explicit `--container-mode container` forwards the
same validated capture through a read-only mount. Omit `--templates-only` only
when host Vivado synthesis is wanted and configured. The installed `pcileech`
command exposes the same subcommands; the Stage 2 module also accepts
`python -m pcileechfwgenerator.build --from-capture DIR --board BOARD --output OUT`.

The existing unified `build --host-collect-only` command also collects the new
resource extension. The TUI remains a live collection/build workflow.

## Snapshot schema

Both `device_context.json` and `msix_data.json` retain the matching schema-v1
[capture identity](CAPTURE_FORMAT.md). Offline use additionally requires this
object in `device_context.json` (the numbers below illustrate the shape only):

```json
{
  "offline_resources": {
    "schema_version": 1,
    "source": "linux_sysfs_resource",
    "bar_sizes": [4096, 0, 0, 0, 0, 0]
  }
}
```

`bar_sizes` contains exactly six integer byte lengths. Zero denotes an unused
slot, including the upper slot of a 64-bit BAR. Collection reads the first six
records of Linux's PCI `resource` file. Config-space address registers alone
cannot establish BAR lengths; collection does not write sizing masks or sample
MMIO content for this extension. Extra extension fields and unsupported versions
are rejected.

Validation requires a type-0 endpoint and exactly 256 or 4096 config bytes. It
checks the pair's identity/hash, raw identifiers, placeholder IDs, BAR lengths,
address widths/alignment and 64-bit pairing. MSI-X metadata must match the raw
standard capability, including count, flags, BIRs and offsets. The table and
pending-bit array must fit implemented memory BARs without overlapping. Invalid
or cyclic capability pointers and extra MSI-X payload fields are rejected.
Absent MSI-X is represented by `{}` or the collector's strictly typed all-zero
metadata. Live interrupt-table entries are not part of this portable schema.

These are consistency checks, not proof of authenticity, physical device
identity or working firmware. The config hash does not authenticate the resource
metadata. The schema records measured aperture lengths, not BAR backing memory,
register semantics, driver compatibility or IOMMU access permissions.

## Preservation and migration

Import preserves the original capture UUID, timestamp and content, using the
same exclusive, sidecar-first publication as collection. It never overwrites an
existing or partial pair. Output and capture directories must not overlap, and
donor-template export cannot write into the input capture.

Unversioned files, missing sidecars and older schema-v1 captures without measured
resource lengths require recollection into a fresh directory. Import does not
invent lengths, reassign identities or upgrade unsupported captures silently.
Synthetic fixtures live only in the test helpers and are not production donors.

Legacy `--host-context-only`/disabled-VFIO builds resolve the same canonical pair
and apply the same strict offline rules. `DEVICE_CONTEXT_PATH` and
`MSIX_DATA_PATH` may select either sibling; if both are supplied they must refer
to the canonical filenames in the same directory. Missing data stops the build.

## Current limits and evidence

Offline rendering disables live profiling, MMIO learning, sysfs/VFIO enrichment,
interrupt-table reads and PCI name lookup subprocesses. Profiling, force-recapture
and donor-template overrides conflict with offline input and are rejected.
`device_info.json` records the input identity and explicitly marks BAR content
as derived rather than sampled. The standalone generator's generation metadata
records the same provenance. Timing and other derived template behavior still
need a separate provenance audit.

The current dense renderer requires at least one memory BAR and supports at
most 16 MiB of total BAR content. Structurally valid larger captures can be
validated/imported, but rendering rejects them before output creation. Sparse
apertures and physical BAR/MSI-X capacity remain separate capacity work.

Existing generation validators can also reject structurally consistent layouts:
one test capture placed its table at `0x100` and PBA at `0x200`, inside the
generator's generic `0x0000`–`0x1000` device-control reservation. That reservation
policy remains a separate validation-policy investigation; this workflow does not relocate captured
MSI-X structures to make them fit. The successful rendering fixture uses table
offset `0x1000` and PBA offset `0x2000` in a 16 KiB BAR.

Software tests render through the real builder with direct hardware-path reads
and non-Git subprocesses blocked, verify input preservation, exercise malformed and
legacy captures, and check container arguments against the real Stage 2 parser.
They run on macOS with synthetic fixtures. Linux collection, a running container,
Vivado, Windows execution and FPGA/driver behavior have not been validated here.
Passing these tests does not establish hardware or VBS/IOMMU compatibility.
