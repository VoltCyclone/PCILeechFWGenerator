# PCILeech Firmware Generator

## CI/CD Status

[![CI](https://github.com/voltcyclone/PCILeechFWGenerator/actions/workflows/consolidated-ci.yml/badge.svg?branch=main)](https://github.com/voltcyclone/PCILeechFWGenerator/actions/workflows/consolidated-ci.yml)
[![Security](https://github.com/voltcyclone/PCILeechFWGenerator/actions/workflows/security.yml/badge.svg?branch=main)](https://github.com/voltcyclone/PCILeechFWGenerator/actions/workflows/security.yml)
[![E2E](https://github.com/voltcyclone/PCILeechFWGenerator/actions/workflows/e2e-testing.yml/badge.svg?branch=main)](https://github.com/voltcyclone/PCILeechFWGenerator/actions/workflows/e2e-testing.yml)
[![Release](https://github.com/voltcyclone/PCILeechFWGenerator/actions/workflows/release.yml/badge.svg)](https://github.com/voltcyclone/PCILeechFWGenerator/actions/workflows/release.yml)

## Quality Metrics

[![codecov](https://codecov.io/gh/voltcyclone/PCILeechFWGenerator/graph/badge.svg?token=JVX3C1WL86)](https://codecov.io/gh/voltcyclone/PCILeechFWGenerator)
[![Python Version](https://img.shields.io/badge/python-3.11%2B-blue)](https://github.com/voltcyclone/PCILeechFWGenerator)
[![License](https://img.shields.io/badge/license-MIT-green)](LICENSE.txt)
[![Latest Release](https://img.shields.io/github/v/release/voltcyclone/PCILeechFWGenerator?include_prereleases)](https://github.com/voltcyclone/PCILeechFWGenerator/releases)
[![Downloads](https://img.shields.io/github/downloads/voltcyclone/PCILeechFWGenerator/total)](https://github.com/voltcyclone/PCILeechFWGenerator/releases)

[![Discord](https://img.shields.io/badge/Discord-Support-5865F2?logo=discord&logoColor=white)](https://discord.gg/dwQfMNsb7W)

Generate PCIe DMA firmware from real donor hardware. The tool reads a donor card's configuration over VFIO and builds a PCILeech FPGA bitstream from it.

> [!WARNING]
> This tool requires *real* hardware. Templates are built from the identifiers of an actual donor card, and placeholder values are rejected. Using your own donor device keeps your firmware unique.

## Build Pipeline

The generator runs three stages:

1. **Stage 1 (Host):** Collects PCIe device data via VFIO. This includes config space, BARs, the capability chain, MSI-X tables, and register behavior learned by tracing the native driver with `mmiotrace` (skip with `--no-mmio-learning`).
2. **Stage 2 (Container or local):** Renders SystemVerilog, TCL, and COE files from the collected data. The container has no VFIO access.
3. **Stage 3 (Host):** Runs Vivado through `build_all.tcl`, the only Stage 3 entry. Routed timing, clock coverage, DRC, and CDC checks must pass before the bitstream is written. Stage 3 is optional (`--templates-only` stops after Stage 2).

See [Host-Container Pipeline](https://pcileechfwgenerator.voltcyclone.info/host-container-pipeline) for details.

## Key Features

- **Donor analysis:** Real PCIe configuration and register maps from live hardware via VFIO.
- **Register learning:** Native driver behavior captured with `mmiotrace` during Stage 1.
- **Config-space shadow:** Full 4 KB shadow in BRAM with an explicit writemask policy.
- **MSI-X:** Served from the donor's own BAR, including multi-BAR layouts. Overview and limits are in [MSIX_STATUS.md](docs/development/MSIX_STATUS.md).
- **Offline capture:** Collect once, then render later without the donor (see below).
- **Interactive TUI:** Textual-based interface for device selection and builds.
- **Flashing:** USB flashing of a `.bin` image through `usbloader`.

## Quick Start

### Installation (Ubuntu 22.04+ / Debian 12+)

Modern Linux requires a virtual environment:

```bash
# Create and activate virtual environment
python3 -m venv ~/.pcileech-venv
source ~/.pcileech-venv/bin/activate

# Install with TUI support
pip install pcileechfwgenerator[tui]

# Verify installation
pcileech version
```

### Running with Root Access (Required for VFIO)

VFIO requires root. Use the venv's Python directly with sudo:

```bash
# Add this alias to ~/.bashrc for convenience:
echo "alias pcileech-sudo='sudo ~/.pcileech-venv/bin/python3 -m pcileechfwgenerator.pcileech_main'" >> ~/.bashrc
source ~/.bashrc

# Load VFIO modules
sudo modprobe vfio vfio-pci

# Now run commands with pcileech-sudo:
pcileech-sudo tui                                                    # Interactive TUI
pcileech-sudo build --bdf 0000:03:00.0 --board pcileech_35t325_x1   # CLI build
pcileech-sudo check --device 0000:03:00.0                            # VFIO diagnostics
```

For complete setup including IOMMU configuration, see the **[Installation Guide](https://pcileechfwgenerator.voltcyclone.info/installation)**.

### Requirements

- Python 3.11 or newer
- Linux with VFIO support (Stage 1)
- A donor PCIe card (any inexpensive NIC, sound, or capture card)

Optional:

- Podman, for isolated Stage 2 templating
- A PCILeech DMA board (for example `pcileech_75t484_x1`, `pcileech_35t325_x4`, or `pcileech_100t484_x1`)
- Vivado 2022.2 or newer for Stage 3

### CLI Commands

```bash
# Interactive TUI (recommended for first-time users)
pcileech-sudo tui

# Full 3-stage build
pcileech-sudo build --bdf 0000:03:00.0 --board pcileech_35t325_x1

# Stage 1 only (collect device data, then exit)
pcileech-sudo build --bdf 0000:03:00.0 --board pcileech_35t325_x1 --host-collect-only

# Run Stage 2 locally (skip the container)
pcileech-sudo build --bdf 0000:03:00.0 --board pcileech_35t325_x1 --local

# Full build with an explicit Vivado install
pcileech-sudo build --bdf 0000:03:00.0 --board pcileech_35t325_x1 \
    --vivado-path /tools/Xilinx/2025.1/Vivado --vivado-jobs 8 --vivado-timeout 7200

# Opt-in build flags (see `pcileech build --help`)
#   --writable-bar-fallback  emulate writable RAM for a served BAR with no learned register model
#                            (default is read-only; this is not donor behavior)
#   --ft601-legacy-phy       legacy FT601 PHY instead of the MMCM one (CaptainDMA 100T only)
#   --no-mmio-learning       skip the mmiotrace driver capture in Stage 1
#   --force-recapture        redo MMIO capture even if cached models exist

# Check VFIO configuration
pcileech-sudo check --device 0000:03:00.0

# Flash a firmware image (.bin, written through usbloader)
pcileech-sudo flash firmware.bin --board pcileech_35t325_x1

# Show version information (no sudo needed)
pcileech version
```

### Offline Captures

Saved donor captures need matching, versioned device-context and MSI-X files. Older or incomplete captures must be collected again into a fresh `--datastore`, because collection never overwrites an existing capture. Reuse checks the requested PCI address and the current device identity. See the [capture format and migration guide](docs/development/CAPTURE_FORMAT.md).

The CLI provides `capture collect`, `capture validate`, `capture import`, and `build --from-capture DIR --templates-only`. Complete captures include measured BAR lengths and can be rebuilt without the donor, on the machine that collected them. See the [offline workflow and its limits](docs/development/OFFLINE_CAPTURE.md).

> [!WARNING]
> **Builds from a saved capture are unverified.** The donor is not read during
> the build, so nothing confirms the capture still describes hardware you have.
> Every such build prints a large warning, asks you to accept the risk (or
> requires `--accept-offline-risk` when not run at a terminal), and writes
> `OFFLINE_BUILD_WARNING.txt` next to its artifacts. Captures are locked to the
> machine that collected them and are marked with `CAPTURE_WARNING.txt`; do not
> share them. Use the firmware only with hardware you own, against systems you
> own or are explicitly authorised to test.

> [!IMPORTANT]
> Use the venv. Running `pip install pcileechfwgenerator` into the system Python fails with `externally-managed-environment` on modern distros.

### Version Updates

CLI builds check for newer versions. Set `PCILEECH_DISABLE_UPDATE_CHECK=1` to disable the check, or run `pcileech version` to see the current version.

### Development from Repository

```bash
# Clone with the board submodule
git clone --recurse-submodules https://github.com/voltcyclone/PCILeechFWGenerator.git
cd PCILeechFWGenerator

python3 -m venv .venv && source .venv/bin/activate
pip install -e .

# From a checkout, run the root script
sudo -E python3 pcileech.py tui
```

- If you cloned without `--recurse-submodules`, run `git submodule update --init --recursive`. The `lib/voltcyclone-fpga` submodule holds the board definitions and synthesis sources.
- The container clones `voltcyclone-fpga` during the image build and needs no submodule.
- pip installs bundle the board files (installed under `pcileechfwgenerator/voltcyclone_fpga`), so they need no checkout. Only a source checkout needs `--recurse-submodules`.

## Troubleshooting

See the [Troubleshooting Guide](https://pcileechfwgenerator.voltcyclone.info/troubleshooting) for VFIO setup, installation problems, BAR detection, and locked IP cores.

### Common Issues

#### "externally-managed-environment" Error

Use a virtual environment:

```bash
python3 -m venv ~/.pcileech-venv
source ~/.pcileech-venv/bin/activate
pip install pcileechfwgenerator[tui]
```

#### "ModuleNotFoundError: No module named 'textual'"

Install with TUI support:

```bash
pip install pcileechfwgenerator[tui]
```

#### TUI build settings and older profiles

The TUI uses the shared CLI pipeline in the active Python environment. Select a real board and choose local or container templating. Both modes collect and run Vivado on the Linux host. Unsupported legacy profile settings fail before collection. See the [TUI build contract and migration guide](docs/development/TUI_BUILD_CONTRACT.md).

#### "Permission denied" / VFIO Errors

Use the venv's Python with sudo:

```bash
sudo ~/.pcileech-venv/bin/python3 -m pcileechfwgenerator.pcileech_main tui
```

#### Quick Diagnostic

```bash
pcileech-sudo check --device 0000:03:00.0 --interactive
```

## Documentation Links

- [Installation Guide](https://pcileechfwgenerator.voltcyclone.info/installation)
- [Quick Start Guide](https://pcileechfwgenerator.voltcyclone.info/quick-start)
- [Host-Container Pipeline](https://pcileechfwgenerator.voltcyclone.info/host-container-pipeline)
- [Container Builds](https://pcileechfwgenerator.voltcyclone.info/container-builds)
- [Troubleshooting Guide](https://pcileechfwgenerator.voltcyclone.info/troubleshooting)
- [Device Cloning Process](https://pcileechfwgenerator.voltcyclone.info/device-cloning)
- [Firmware Uniqueness](https://pcileechfwgenerator.voltcyclone.info/firmware-uniqueness)
- [Manual Donor Dump](https://pcileechfwgenerator.voltcyclone.info/manual-donor-dump)
- [PCILeech Configuration](https://pcileechfwgenerator.voltcyclone.info/pcileech-configuration)
- [Development Setup](https://pcileechfwgenerator.voltcyclone.info/development)
- [TUI Documentation](https://pcileechfwgenerator.voltcyclone.info/tui-readme)
- [Config Space Shadow](https://pcileechfwgenerator.voltcyclone.info/config-space-shadow)

The docs are managed in the [site repo](https://github.com/voltcyclone/pcileechfwgenerator-site) and served by Cloudflare.

## Output Files

Artifacts go to the datastore (default `pcileech_datastore/`):

```
pcileech_datastore/
├── device_context.json       # Stage 1: collected device data
├── msix_data.json            # Stage 1: MSI-X capability data
└── output/                   # Stage 2 and 3 artifacts, including a per-board directory
    ├── pcileech_top.sv       # Top-level SystemVerilog module
    ├── device_config.sv      # Device configuration module
    ├── build_all.tcl         # Stage 3 entry script
    └── *.bit                 # Bitstream (only after Stage 3)
```

Board `vivado_*.tcl` project scripts are not staged. `build_all.tcl` is the only Stage 3 entry.

## Cleanup and Safety

- Rebind donors to their original drivers through the TUI or CLI after collection.
- Keep generated firmware private. It contains real device identifiers.
- Build in an isolated environment, never on a production system.

## Acknowledgments

- **PCILeech Community**: For feedback and contributions
- @Simonrak for the writemask implementation

## License

This project is licensed under the MIT License - see the [LICENSE.txt](LICENSE.txt) file for details.

## Legal Notice

> [!IMPORTANT]
> This tool is intended for educational research and legitimate PCIe development only. Users are responsible for complying with all applicable laws. The authors assume no liability for misuse of this software.

- Use dedicated, separate hardware for builds.
- Follow responsible disclosure for any security research, and see [SECURITY.md](SECURITY.md) to report a vulnerability.
