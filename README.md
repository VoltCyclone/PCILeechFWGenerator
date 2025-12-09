# PCILeech Firmware Generator

## CI/CD Status

[![CI](https://github.com/voltcyclone/PCILeechFWGenerator/workflows/CI/badge.svg?branch=main)](https://github.com/voltcyclone/PCILeechFWGenerator/actions/workflows/ci.yml)
[![SystemVerilog Validation](https://img.shields.io/badge/SystemVerilog-passing-brightgreen)](https://github.com/voltcyclone/PCILeechFWGenerator/actions/workflows/ci.yml)
[![Unit Tests](https://img.shields.io/badge/Unit%20Tests-passing-brightgreen)](https://github.com/voltcyclone/PCILeechFWGenerator/actions/workflows/ci.yml)
[![TUI Tests](https://img.shields.io/badge/TUI%20Tests-passing-brightgreen)](https://github.com/voltcyclone/PCILeechFWGenerator/actions/workflows/ci.yml)
[![Integration Tests](https://img.shields.io/badge/Integration%20Tests-passing-brightgreen)](https://github.com/voltcyclone/PCILeechFWGenerator/actions/workflows/ci.yml)
[![Packaging](https://img.shields.io/badge/Packaging-passing-brightgreen)](https://github.com/voltcyclone/PCILeechFWGenerator/actions/workflows/ci.yml)
[![Documentation](https://img.shields.io/badge/Documentation-passing-brightgreen)](https://github.com/voltcyclone/PCILeechFWGenerator/actions/workflows/ci.yml)

## Quality Metrics

[![codecov](https://codecov.io/gh/ramseymcgrath/PCILeechFWGenerator/graph/badge.svg?token=JVX3C1WL86)](https://codecov.io/gh/ramseymcgrath/PCILeechFWGenerator)
[![Code Quality](https://img.shields.io/badge/code%20quality-A-brightgreen)](https://github.com/voltcyclone/PCILeechFWGenerator/actions)
[![Python Version](https://img.shields.io/badge/python-3.11%2B-blue)](https://github.com/voltcyclone/PCILeechFWGenerator)
[![License](https://img.shields.io/badge/license-MIT-green)](LICENSE.txt)
[![Latest Release](https://img.shields.io/github/v/release/ramseymcgrath/PCILeechFWGenerator?include_prereleases)](https://github.com/voltcyclone/PCILeechFWGenerator/releases)
[![Downloads](https://img.shields.io/github/downloads/ramseymcgrath/PCILeechFWGenerator/total)](https://github.com/voltcyclone/PCILeechFWGenerator/releases)

## Build Artifacts

[![Package Build](https://img.shields.io/badge/packages-available-brightgreen)](https://github.com/voltcyclone/PCILeechFWGenerator/actions/workflows/ci.yml)
[![Wheel](https://img.shields.io/badge/wheel-✓-green)](https://github.com/voltcyclone/PCILeechFWGenerator/actions/workflows/ci.yml)
[![Source Distribution](https://img.shields.io/badge/sdist-✓-green)](https://github.com/voltcyclone/PCILeechFWGenerator/actions/workflows/ci.yml)

![Discord](https://dcbadge.limes.pink/api/shield/429866199833247744)

Generate authentic PCIe DMA firmware from real donor hardware using a **3-stage host-container-host pipeline**. This tool extracts donor configurations from a local device via VFIO and generates unique PCILeech FPGA bitstreams.

> [!WARNING]
> This tool requires *real* hardware. The templates are built using the device identifiers directly from a donor card and placeholder values are explicitly avoided. Using your own donor device ensures your firmware will be unique.

## Build Pipeline

The generator uses a 3-stage pipeline:

1. **Stage 1 (Host)**: Collects PCIe device data via VFIO on the host
2. **Stage 2 (Container or Local)**: Generates firmware artifacts from collected data (no VFIO access in container)
3. **Stage 3 (Host)**: Runs Vivado synthesis on the host (optional)

The container does **NOT** access VFIO devices - it only performs templating using pre-collected data. See [Host-Container Pipeline](https://pcileechfwgenerator.voltcyclone.info/host-container-pipeline) for details.

## Key Features

- **Donor Hardware Analysis**: Extract real PCIe device configurations and register maps from live hardware via VFIO
- **Overlay-Only Architecture**: Generate device-specific `.coe` configuration files that integrate with upstream `pcileech-fpga` HDL modules
- **Dynamic Device Capabilities**: Generate realistic network, storage, media, and USB controller capabilities with pattern-based analysis
- **Full 4KB Config-Space Shadow**: Complete configuration space emulation with BRAM-based overlay memory
- **MSI-X Table Replication**: Exact replication of MSI-X tables from donor devices with interrupt delivery logic
- **Unified Context Building**: Centralized template context generation ensuring consistency across all output files
- **Active Device Interrupts**: MSI-X interrupt controller with timer-based and event-driven interrupt generation
- **Interactive TUI**: Modern Textual-based interface with real-time device monitoring and guided workflows
- **Containerized Build Pipeline**: Podman-based synthesis environment with automated VFIO setup
- **USB-JTAG Flashing**: Direct firmware deployment to DMA boards via integrated flash utilities

 **[Complete Documentation](https://pcileechfwgenerator.voltcyclone.info)** |  **[Host-Container Pipeline](https://pcileechfwgenerator.voltcyclone.info/host-container-pipeline)** |  **[Overlay Architecture](https://pcileechfwgenerator.voltcyclone.info/overlay-architecture)** |  **[Troubleshooting Guide](https://pcileechfwgenerator.voltcyclone.info/troubleshooting)** |  **[Device Cloning Guide](https://pcileechfwgenerator.voltcyclone.info/device-cloning)** | **[Dynamic Capabilities](https://pcileechfwgenerator.voltcyclone.info/dynamic-device-capabilities)** |  **[Development Setup](https://pcileechfwgenerator.voltcyclone.info/development)**

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

> ⚠️ **Don't skip the venv!** Running `pip install pcileechfwgenerator` directly will fail with `externally-managed-environment` on modern systems.

### Running with Root Access (Required for VFIO)

VFIO requires root. Use the venv's Python directly with sudo:

```bash
# Add this alias to ~/.bashrc for convenience:
echo "alias pcileech-sudo='sudo ~/.pcileech-venv/bin/python3 -m pcileechfwgenerator.pcileech'" >> ~/.bashrc
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

- **Python ≥ 3.11**
- **Donor PCIe card** (any inexpensive NIC, sound, or capture card)
- **Linux OS** with VFIO support (required for Stage 1 data collection)

### Optional Requirements

- **Podman** - For isolated Stage 2 templating (container does NOT access VFIO)
- **DMA board** - pcileech_75t484_x1, pcileech_35t325_x4, or pcileech_100t484_x1
- **Vivado** - 2022.2+ for bitstream synthesis (Stage 3)


### CLI Commands

```bash
# Interactive TUI (recommended for first-time users)
pcileech-sudo tui

# Full 3-stage build pipeline
pcileech-sudo build --bdf 0000:03:00.0 --board pcileech_35t325_x1

# Stage 1 only (collect device data, no templating)
pcileech-sudo build --bdf 0000:03:00.0 --board pcileech_35t325_x1 --host-collect-only

# Force local mode for Stage 2 (skip container)
pcileech-sudo build --bdf 0000:03:00.0 --board pcileech_35t325_x1 --local

# Full build with Vivado synthesis (Stage 3)
pcileech-sudo build --bdf 0000:03:00.0 --board pcileech_35t325_x1 \
    --vivado-path /tools/Xilinx/2025.1/Vivado --vivado-jobs 8 --vivado-timeout 7200

# Check VFIO configuration
pcileech-sudo check --device 0000:03:00.0

# Flash firmware to device (after Vivado synthesis produces .bit file)
pcileech-sudo flash pcileech_datastore/output/*.bit --board pcileech_35t325_x1

# Show version information (doesn't need sudo)
pcileech version
```

### Version Updates

The tool automatically checks for newer versions during CLI builds. You can:

- Disable automatic checks: set `PCILEECH_DISABLE_UPDATE_CHECK=1`
- Show current version: `pcileech version`


### Development from Repository

```bash
# Clone the repository
git clone https://github.com/voltcyclone/PCILeechFWGenerator.git
cd PCILeechFWGenerator

# Initialize submodule (only needed for local development, NOT for container builds)
git submodule update --init --recursive

python3 -m venv .venv && source .venv/bin/activate
pip install -e .

# From a dev checkout you can still run the root script
sudo -E python3 pcileech.py tui
```

> **Note for developers**: When working from a git checkout for local development, you must clone with `--recurse-submodules` or run `git submodule update --init --recursive` after cloning. The `lib/voltcyclone-fpga` submodule contains FPGA board definitions and synthesis templates.
>
> **Note for container users**: The container (used only for Stage 2 templating) automatically clones the `voltcyclone-fpga` repository during image build. The container does NOT access VFIO - it only generates firmware from pre-collected device data.
>
> **Note for pip users**: The voltcyclone-fpga submodule contents are bundled automatically in pip distributions, so no additional steps are needed.

## Troubleshooting

Having issues? Check our comprehensive **[Troubleshooting Guide](https://pcileechfwgenerator.voltcyclone.info/troubleshooting)** which covers:

- **VFIO Setup Issues** - IOMMU configuration, module loading, device binding
- **Installation Problems** - Virtual environment setup, Python 3.12+ issues
- **BAR Detection Issues** - Power state problems, device compatibility  
- **Locked IP Cores** - Vivado licensing and IP regeneration

### Common Issues

#### "externally-managed-environment" Error

You **must** use a virtual environment on modern Linux:

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

#### "Permission denied" / VFIO Errors

Use the venv's Python with sudo:

```bash
sudo ~/.pcileech-venv/bin/python3 -m pcileechfwgenerator.pcileech tui
```

#### Quick Diagnostic

```bash
pcileech-sudo check --device 0000:03:00.0 --interactive
```

## Direct Documentation Links

- **[Installation Guide](https://pcileechfwgenerator.voltcyclone.info/installation)** - Complete installation instructions
- **[Quick Start Guide](https://pcileechfwgenerator.voltcyclone.info/quick-start)** - Get started in minutes
- **[Host-Container Pipeline](https://pcileechfwgenerator.voltcyclone.info/host-container-pipeline)** - Understanding the 3-stage build flow
- **[Container Builds](https://pcileechfwgenerator.voltcyclone.info/container-builds)** - Container configuration and troubleshooting
- **[Troubleshooting Guide](https://pcileechfwgenerator.voltcyclone.info/troubleshooting)** - Comprehensive troubleshooting and diagnostic guide
- **[Device Cloning Process](https://pcileechfwgenerator.voltcyclone.info/device-cloning)** - Complete guide to the cloning workflow
- **[Firmware Uniqueness](https://pcileechfwgenerator.voltcyclone.info/firmware-uniqueness)** - How authenticity is achieved
- **[Manual Donor Dump](https://pcileechfwgenerator.voltcyclone.info/manual-donor-dump)** - Step-by-step manual extraction
- **[PCILeech Configuration](https://pcileechfwgenerator.voltcyclone.info/pcileech-configuration)** - Key configuration parameters explained
- **[Development Setup](https://pcileechfwgenerator.voltcyclone.info/development)** - Contributing and development guide
- **[TUI Documentation](https://pcileechfwgenerator.voltcyclone.info/tui-readme)** - Interactive interface guide
- **[Config space info](https://pcileechfwgenerator.voltcyclone.info/config-space-shadow)** - Config space shadow info

## Output Files

After a successful build, artifacts are placed in the datastore (default: `pcileech_datastore/`):

```
pcileech_datastore/
├── device_context.json       # Stage 1: Collected device data
├── msix_data.json            # Stage 1: MSI-X capability data
└── output/
    ├── pcileech_top.sv       # Top-level SystemVerilog module
    ├── device_config.sv      # Device configuration module
    ├── config_space_init.hex # Configuration space initialization (BRAM)
    ├── *.tcl                 # Vivado project/build scripts
    └── *.bit                 # Bitstream (only after Stage 3 Vivado synthesis)
```

## Cleanup & Safety

- **Rebind donors**: Use TUI/CLI to rebind donor devices to original drivers
- **Keep firmware private**: Generated firmware contains real device identifiers
- **Use isolated build environments**: Never build on production systems

> [!IMPORTANT]
> This tool is intended for educational research and legitimate PCIe development purposes only. Users are responsible for ensuring compliance with all applicable laws and regulations. The authors assume no liability for misuse of this software.

## Docs

Docs are managed in the [site repo](github.com/voltcyclone/pcileechfwgenerator-site) and served by cloudflare.

## Acknowledgments

- **PCILeech Community**: For feedback and contributions
- @Simonrak for the writemask implementation

## License

This project is licensed under the Apache License - see the [LICENSE](LICENSE) file for details.

## Legal Notice

*AGAIN* This tool is intended for educational research and legitimate PCIe development purposes only. Users are responsible for ensuring compliance with all applicable laws and regulations. The authors assume no liability for misuse of this software.

**Security Considerations:**

- Never build firmware on systems used for production or sensitive operations
- Use isolated build environments (Seperate dedicated hardware)
- Keep generated firmware private and secure
- Follow responsible disclosure practices for any security research
- Use the SECURITY.md template to raise security concerns
