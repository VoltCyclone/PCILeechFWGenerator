# Validated offline capture workflow

Collect a donor once on Linux, then validate, import and render its saved data
later without the donor being connected, **on the same machine** (see
[Collecting-host lock](#collecting-host-lock)). Install the Python dependencies
first; offline commands do not install packages, require root or run the
Linux/VFIO preflight. The examples below use the source-checkout CLI.

> [!WARNING]
> Builds from a saved capture are **unverified**: the donor is not read or
> checked during the build, so the firmware's identity rests entirely on the
> capture files. Every such build shows a prominent warning and requires you to
> accept the risk ([Unverified builds](#unverified-builds-warning-and-acceptance)).
> Only use the resulting firmware with hardware you own, against systems you own
> or are explicitly authorised to test.

## Separate stages

```sh
# Linux donor host: replace the address with the actual donor's address.
sudo -E .venv/bin/python pcileech.py capture collect \
  --bdf 0000:03:00.0 --output ./captures/donor-new

# Later, on the same machine (the donor may be removed), validate the capture.
.venv/bin/python pcileech.py capture validate --input ./captures/donor-new

# Optional: validate and publish an immutable copy in a fresh directory.
.venv/bin/python pcileech.py capture import --bdf 0000:03:00.0 \
  --input ./captures/donor-new --output ./captures/imported

# Render artifacts locally, without collection or Vivado. At a terminal this
# shows the unverified-build warning and asks y/N; in scripts pass
# --accept-offline-risk instead.
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

Rebuilding from an existing `--datastore` checks the donor at the captured
address. If it is present it must match the capture (a live, verified build). If
no device is present, the build continues as an unverified offline build from
the saved capture, with the same warning, acceptance and limits as
`--from-capture`. A different device at that address is always an error.

## Unverified builds: warning and acceptance

A build is *unverified* when it renders from a saved capture without reading the
donor in the same run: `--from-capture`, or a datastore rebuild whose donor is
absent. Every unverified build:

1. Prints a prominent `WARNING: UNVERIFIED OFFLINE FIRMWARE BUILD` banner.
2. At a terminal, asks `Continue with this unverified build? [y/N]`; only `y` or
   `yes` continues.
3. Without a terminal (scripts, CI, the TUI's build process) is refused unless
   `--accept-offline-risk` is passed. The flag is the explicit, recorded
   acceptance; it does not hide the banner.
4. Writes `OFFLINE_BUILD_WARNING.txt` into the output directory (see
   [Warning files](#warning-files)).

The acceptance given on the host is forwarded to the templating stage, including
the container stage. The Stage 2 module and `FirmwareBuilder` refuse an offline
configuration that has not been accepted. Live and verified builds are
unaffected.

## Collecting-host lock

Collection stores a host binding in `device_context.json`:

```json
{
  "host_binding": {
    "schema_version": 1,
    "method": "machine-id-sha256",
    "fingerprint": "<64 hex characters>"
  }
}
```

The fingerprint is a salted SHA-256 of `/etc/machine-id` (falling back to
`/var/lib/dbus/machine-id`), so captures never contain the raw machine ID. Any
offline use (`capture validate`, `capture import`, `--from-capture` and datastore
rebuilds without the donor) recomputes it and refuses a capture that has no
binding or was collected on another machine. Offline container builds mount
the host's `/etc/machine-id` read-only so the check sees the same host.

- Reinstalling the OS or regenerating the machine ID invalidates existing
  captures for offline use; recollect them.
- A host without a machine ID can still collect for live builds; those
  captures cannot be used offline.
- Captures collected before this lock existed have no binding and must be
  recollected for offline use.
- The lock deters casual copying of captures between machines. It is not
  tamper-proof: the files are plain JSON and the project is open source.

## Warning files

- `CAPTURE_WARNING.txt` is written next to every published capture (collection
  and import). It states that the directory holds a real donor's PCIe identity,
  must not be shared, and is locked to the collecting host. Capture readers
  ignore it, and an existing copy is never replaced.
- `OFFLINE_BUILD_WARNING.txt` is written into the output of every unverified
  build. It repeats the warning and records the trust level, build time, capture
  ID and collection time, capture address, config-space SHA-256 and a prefix of
  the collecting host's fingerprint. It contains no donor vendor or device IDs.

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
checks the pair's identity/hash, raw identifiers, placeholder IDs (honouring
`PCILEECH_ALLOW_PLACEHOLDER_IDS=1` like every other gate), BAR lengths,
address widths/alignment and 64-bit pairing. MSI-X metadata must match the raw
standard capability, including count, flags, BIRs and offsets. The table and
pending-bit array must fit implemented memory BARs without overlapping. Invalid
or cyclic capability pointers are rejected; extra collector fields in the MSI-X
metadata (such as `vector_count`) are ignored, not consumed.
Absent MSI-X is represented by `{}` or the collector's strictly typed all-zero
metadata. Live interrupt-table entries are not part of this portable schema.

These are consistency checks, not proof of authenticity, physical device
identity or working firmware. The config hash does not authenticate the resource
metadata. The schema records measured aperture lengths, not BAR backing memory,
register semantics, driver compatibility or IOMMU access permissions.

## Preservation and migration

Import preserves the original capture UUID, timestamp and content, using the
same exclusive, sidecar-first publication as collection. It never overwrites an
existing or partial pair. The output directory may be a subdirectory of the
capture (the default `<datastore>/output`) but must not be the capture directory
itself or contain it, and donor-template export cannot write into the input
capture.

Unversioned files, missing sidecars and older schema-v1 captures without measured
resource lengths require recollection into a fresh directory. Import does not
invent lengths, reassign identities or upgrade unsupported captures silently.
Synthetic fixtures live only in the test helpers and are not production donors.

Legacy `--host-context-only`/disabled-VFIO builds (`DEVICE_CONTEXT_PATH` /
`MSIX_DATA_PATH`) are the internal input path of the live container stage. They
validate the canonical pair with the strict loader but keep live behaviour: no
offline render limits, warning or acceptance. Outside a container they are
refused with a pointer to `--from-capture`, so they cannot bypass the offline
rules. If both variables are supplied they must name the canonical files in the
same directory. Missing data stops the build.

## Current limits and evidence

All builds currently ship zeroed extended configuration space (`0x100`–`0xFFF`);
see [Known issue: extended configuration space is always zero](EXTENDED_CONFIG_SPACE.md).

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
