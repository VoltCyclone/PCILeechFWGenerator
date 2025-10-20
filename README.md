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
[![Python Version](https://img.shields.io/badge/python-3.8%2B-blue)](https://github.com/voltcyclone/PCILeechFWGenerator)
[![License](https://img.shields.io/badge/license-MIT-green)](LICENSE.txt)
[![Latest Release](https://img.shields.io/github/v/release/ramseymcgrath/PCILeechFWGenerator?include_prereleases)](https://github.com/voltcyclone/PCILeechFWGenerator/releases)
[![Downloads](https://img.shields.io/github/downloads/ramseymcgrath/PCILeechFWGenerator/total)](https://github.com/voltcyclone/PCILeechFWGenerator/releases)

## Build Artifacts

[![Package Build](https://img.shields.io/badge/packages-available-brightgreen)](https://github.com/voltcyclone/PCILeechFWGenerator/actions/workflows/ci.yml)
[![Wheel](https://img.shields.io/badge/wheel-✓-green)](https://github.com/voltcyclone/PCILeechFWGenerator/actions/workflows/ci.yml)
[![Source Distribution](https://img.shields.io/badge/sdist-✓-green)](https://github.com/voltcyclone/PCILeechFWGenerator/actions/workflows/ci.yml)

![Discord](https://dcbadge.limes.pink/api/shield/429866199833247744)

Generate authentic PCIe DMA firmware from real donor hardware with a single command. This tool extracts donor configurations from a local device and generates unique PCILeech FPGA bitstreams (and optionally flashes a DMA card over USB-JTAG).

> [!WARNING]
> This tool requires *real* hardware. The templates are built using the device identifiers directly from a donor card and placeholder values are explicitly avoided. Using your own donor device ensures your firmware will be unique.

## Key Features

- **Donor Hardware Analysis**: Extract real PCIe device configurations and register maps from live hardware via VFIO
- **Dynamic Device Capabilities**: Generate realistic network, storage, media, and USB controller capabilities with pattern-based analysis
- **Full 4KB Config-Space Shadow**: Complete configuration space emulation with BRAM-based overlay memory
- **MSI-X Table Replication**: Exact replication of MSI-X tables from donor devices with interrupt delivery logic
- **Advanced SystemVerilog Generation**: Comprehensive PCIe device controller with modular template architecture
- **Active Device Interrupts**: MSI-X interrupt controller with timer-based and event-driven interrupt generation
- **Interactive TUI**: Modern Textual-based interface with real-time device monitoring and guided workflows
- **Containerized Build Pipeline**: Podman-based synthesis environment with automated VFIO setup
- **USB-JTAG Flashing**: Direct firmware deployment to DMA boards via integrated flash utilities

 **[Complete Documentation](https://pcileechfwgenerator.voltcyclone.info)** |  **[Troubleshooting Guide](https://pcileechfwgenerator.voltcyclone.info/troubleshooting)** |  **[Device Cloning Guide](https://pcileechfwgenerator.voltcyclone.info/device-cloning)** | **[Dynamic Capabilities](https://pcileechfwgenerator.voltcyclone.info/dynamic-device-capabilities)** |  **[Development Setup](https://pcileechfwgenerator.voltcyclone.info/development)**

## Quick Start

### Installation

```bash
# Install with TUI support (recommended)
pip install pcileechfwgenerator[tui]

# Load required kernel modules
sudo modprobe vfio vfio-pci
```

> **Python 3.12+ / Debian 12+ Users**: If you see `externally-managed-environment` errors, see [Installation on Python 3.12+](site/docs/installation-python312.md) for detailed instructions on using virtual environments with sudo.

### Requirements

- **Python ≥ 3.9**
- **Donor PCIe card** (any inexpensive NIC, sound, or capture card)
- **Linux OS** (You need this)

### Recommended Requirements

- **Podman** (recommended for containerized builds) - The build system uses Podman by default for consistency and reproducibility. If Podman isn't installed, you'll be prompted to run locally.

### Optional Requirements

- **DMA board** (pcileech_75t484_x1, pcileech_35t325_x4, or pcileech_100t484_x1) - You don't need to flash your firmware with this tooling but you can.
- **Vivado Studio** (2022.2+ for synthesis and bitstream generation) - You can use a locally generated Vivado project or insert the files into an existing one.


### Basic Usage

```bash
# Interactive TUI (recommended for first-time users)
sudo pcileech tui

# CLI interface for scripted builds
sudo pcileech build --bdf 0000:03:00.0 --board pcileech_35t325_x1

# CLI build with custom Vivado settings
sudo pcileech build --bdf 0000:03:00.0 --board pcileech_35t325_x1 \
    --vivado-path /tools/Xilinx/2025.1/Vivado --vivado-jobs 8 --vivado-timeout 7200

# Check VFIO configuration
sudo pcileech check --device 0000:03:00.0

# Flash firmware to device
sudo pcileech flash output/firmware.bin

# Show version information
pcileech version
```

### Version Updates

The tool automatically checks for newer versions during CLI builds. You can:

- Disable automatic checks: set `PCILEECH_DISABLE_UPDATE_CHECK=1`
- Show current version: `pcileech version`


### Development from Repository

```bash
git clone https://github.com/voltcyclone/PCILeechFWGenerator.git
cd PCILeechFWGenerator
python3 -m venv .venv && source .venv/bin/activate
pip install -e .

# From a dev checkout you can still run the root script
sudo -E python3 pcileech.py tui
```

## Troubleshooting

Having issues? Check our comprehensive **[Troubleshooting Guide](https://pcileechfwgenerator.voltcyclone.info/troubleshooting)** which covers:

- **VFIO Setup Issues** - IOMMU configuration, module loading, device binding
- **Installation Problems** - Package dependencies, container setup, Python 3.12+ externally-managed environments
- **BAR Detection Issues** - Power state problems, device compatibility  
- **Device-Specific Issues** - Known problems with specific hardware

Quick diagnostic command:

```bash
# Check VFIO setup and device compatibility
sudo python3 pcileech.py check --device 0000:03:00.0 --interactive
```

### Common Installation Issues

#### "externally-managed-environment" error on Python 3.12+/Debian 12+

See [Installation on Python 3.12+](site/docs/installation-python312.md) for detailed solutions. Quick fix:

```bash
# Create venv and install
python3 -m venv ~/.pcileech-venv
source ~/.pcileech-venv/bin/activate
pip install pcileechfwgenerator[tui]

# Run with venv's Python directly
sudo ~/.pcileech-venv/bin/python3 -m pcileechfwgenerator.pcileech tui
```

## Direct Documentation Links

- **[Troubleshooting Guide](https://pcileechfwgenerator.voltcyclone.info/troubleshooting)** - Comprehensive troubleshooting and diagnostic guide
- **[Device Cloning Process](https://pcileechfwgenerator.voltcyclone.info/device-cloning)** - Complete guide to the cloning workflow
- **[Firmware Uniqueness](https://pcileechfwgenerator.voltcyclone.info/firmware-uniqueness)** - How authenticity is achieved
- **[Manual Donor Dump](https://pcileechfwgenerator.voltcyclone.info/manual-donor-dump)** - Step-by-step manual extraction
- **[PCILeech Configuration](https://pcileechfwgenerator.voltcyclone.info/pcileech-configuration)** - Key configuration parameters explained
- **[Development Setup](https://pcileechfwgenerator.voltcyclone.info/development)** - Contributing and development guide
- **[TUI Documentation](https://pcileechfwgenerator.voltcyclone.info/tui-readme)** - Interactive interface guide
- **[Config space info](https://pcileechfwgenerator.voltcyclone.info/config-space-shadow)** - Config space shadow info

## Cleanup & Safety

- **Rebind donors**: Use TUI/CLI to rebind donor devices to original drivers
- **Keep firmware private**: Generated firmware contains real device identifiers
- **Use isolated build environments**: Never build on production systems
- **Container cleanup** (if needed): `podman rmi pcileechfwgenerator:latest`

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

---
