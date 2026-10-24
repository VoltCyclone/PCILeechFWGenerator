# Donor capture identity (schema version 1)

The capture contract ties `device_context.json` and `msix_data.json` to one collection. Both
host collectors write this format. The unified build command, config/context
loaders, MSI-X preloaders and prefilled BAR-model loader validate it before
using saved donor data.

## Identity contract

Both files contain the same `capture` object:

| Field | Meaning |
| --- | --- |
| `schema_version` | `1`; unsupported versions are rejected |
| `capture_id` | UUID generated for this collection |
| `collected_at` | Timezone-aware collection timestamp; writers use UTC |
| `bdf` | Lowercase `dddd:bb:ss.f` PCI address |
| `vendor_id`, `device_id` | Integer identifiers extracted from the raw PCI header |
| `revision_id`, `class_code` | Integer revision and class extracted from the header |
| `subsystem_vendor_id`, `subsystem_device_id` | Integer subsystem fields at header offsets 0x2c–0x2f |
| `config_space_size` | Actual byte count, currently 64–4096 bytes |
| `config_space_sha256` | SHA-256 of the captured config bytes |

Both files carry identical `config_space_hex` and normalized root `bdf`.
The device context also carries the extracted root identifiers. The MSI-X
sidecar always exists and contains an `msix_info` dictionary, which may be empty
when MSI-X is absent. Additional device information and BAR models can remain
in the device context.

Readers check the requested address, schema, metadata, length, checksum, raw
header identifiers and both files' capture identity. Conflicting identifiers
or config bytes in the supported nested context shapes are rejected too.
A zero-domain short address such as `03:00.0` normalizes to `0000:03:00.0`.

The unified host build checks a reused capture against the current device's
read-only 64-byte PCI header. Live-mode MSI-X preloaders do the same. Vendor,
device, class, revision and subsystem changes at the same address stop the
build; mutable command/status and BAR address registers do not invalidate it.
An unreadable current header stops reuse. Explicit host-context-only consumers
validate the saved pair without reading sysfs.

These checks establish consistency, not cryptographic authenticity or physical
device uniqueness. Two devices with identical header identifiers cannot be
distinguished by this contract. It does not detect every change to a device's
behavior or capabilities, or guarantee against a hotplug after the check.
Full offline-capture validation remains separate work; separate MMIO trace/model caches
are outside this snapshot contract.

## Publication and interrupted collection

A collector refuses a destination containing either capture file, including a
partial pair. It serializes both files to temporary files and flushes them before
publishing the MSI-X sidecar, then the device context. Exclusive hard links make
each publication atomic without replacing another writer's file. Directory
entries are flushed in order. Temporary files are removed during cleanup.

A process interruption can leave temporary files or an orphan sidecar. An orphan
is rejected, and collection does not silently replace it. A filesystem without
the required hard-link/fsync support reports a publication error. Keep the old
directory for inspection and collect into a fresh directory.

## Existing captures and migration

Unversioned captures, missing metadata, incomplete pairs and mismatches now
stop the build with a recollection message. Do not add invented identity metadata
to old files: their original device and capture association cannot be verified.

On the Linux donor host, select the actual donor and supported board, then use
a fresh datastore. For example, replacing the example address and board as needed:

```sh
sudo -E .venv/bin/python pcileech.py build \
  --bdf 0000:03:00.0 --board pcileech_35t325_x1 \
  --datastore ./captures/donor-new --host-collect-only
```

Use that same datastore for the subsequent unified build. Copy both capture
files together when transferring data to Stage 2. `DEVICE_CONTEXT_PATH` defaults
its sidecar to the sibling `msix_data.json`; an explicit `MSIX_DATA_PATH` must
belong to the same capture. Setting only `MSIX_DATA_PATH` is supported for the
local live-build preloader, which resolves the sibling device context.
Explicitly configured missing files are errors, not permission to fall back to
live collection. `--force-recapture` continues to govern MMIO learning; it does
not authorize overwriting capture files.

The older container entry point writes to `./output`; repeated collection there
also requires a fresh destination (for example, a fresh working directory).
Use the unified command's `--datastore` option when managing multiple captures.

## Verification

The original three regression tests failed before this change: wrong-donor
orchestration proceeded, an unidentified snapshot was accepted, and collection
omitted identity metadata. The test suite now exercises matching/mismatched
addresses, changed live identities, legacy/malformed files, conflicting sidecars,
both MSI-X consumers, BAR-model error propagation, both collectors, interrupted
publication and exclusive publication when another writer creates the destination.

Software tests run on macOS with in-memory PCI fixtures. Linux sysfs/VFIO,
container operation, Vivado synthesis and physical donor/FPGA behavior still
require their separate acceptance checks.
