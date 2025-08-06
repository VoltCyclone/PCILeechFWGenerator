# PCILeech Firmware Generator

[![CI](https://github.com/ramseymcgrath/PCILeechFWGenerator/workflows/CI/badge.svg)](https://github.com/ramseymcgrath/PCILeechFWGenerator/actions)
[![codecov](https://codecov.io/gh/ramseymcgrath/PCILeechFWGenerator/branch/main/graph/badge.svg)](https://codecov.io/gh/ramseymcgrath/PCILeechFWGenerator)
![](https://dcbadge.limes.pink/api/shield/429866199833247744)

Generate authentic PCIe DMA firmware from real donor hardware with a single command. This tool extracts donor configurations from a local device and generates unique PCILeech FPGA bitstreams (and optionally flashes a DMA card over USB-JTAG).

> [!WARNING]
> This tool requires *real* hardware. The templates are built using the device identifiers directly from a donor card and placeholder values are explicitly avoided. Using your own donor device ensures your firmware will be unique.

## ✨ Key Features

- **Donor Hardware Analysis**: Extract real PCIe device configurations and register maps from live hardware via VFIO
- **Dynamic Device Capabilities**: Generate realistic network, storage, media, and USB controller capabilities with pattern-based analysis
- **Full 4KB Config-Space Shadow**: Complete configuration space emulation with BRAM-based overlay memory
- **MSI-X Table Replication**: Exact replication of MSI-X tables from donor devices with interrupt delivery logic
- **Deterministic Variance Seeding**: Consistent hardware variance based on device serial number for unique firmware
- **Advanced SystemVerilog Generation**: Comprehensive PCIe device controller with modular template architecture
- **Active Device Interrupts**: MSI-X interrupt controller with timer-based and event-driven interrupt generation
- **Memory Overlay Mapping**: BAR dispatcher with configurable memory regions and custom PIO windows
- **Interactive TUI**: Modern Textual-based interface with real-time device monitoring and guided workflows
- **Containerized Build Pipeline**: Podman-based synthesis environment with automated VFIO setup
- **Automated Testing and Validation**: Comprehensive test suite with SystemVerilog assertions and Python unit tests
- **USB-JTAG Flashing**: Direct firmware deployment to DMA boards via integrated flash utilities

📚 **[Complete Documentation](https://pcileechfwgenerator.ramseymcgrath.com)** | 🔧 **[Troubleshooting Guide](https://pcileechfwgenerator.ramseymcgrath.com/troubleshooting)** | 🏗️ **[Device Cloning Guide](https://pcileechfwgenerator.ramseymcgrath.com/device-cloning)** | ⚡ **[Dynamic Capabilities](https://pcileechfwgenerator.ramseymcgrath.com/dynamic-device-capabilities)** | �️ **[Development Setup](https://pcileechfwgenerator.ramseymcgrath.com/development)**

## Quick Start

### Installation

```bash
# Install with TUI support (recommended)
pip install pcileechfwgenerator[tui]

# Load required kernel modules
sudo modprobe vfio vfio-pci
```

### Requirements

- **Python ≥ 3.9**
- **Donor PCIe card** (any inexpensive NIC, sound, or capture card)
- **Linux OS** (You need this)

### Optional Requirements

- **Podman** (_not Docker_ - required for proper PCIe device mounting) You use podman or run the python locally. *You must use linux for either option
- **DMA board** (pcileech_75t484_x1, pcileech_35t325_x4, or pcileech_100t484_x1) You don't need to flash your firmware with this tooling but you can.
- **Vivado Studio** (2022.2+ for synthesis and bitstream generation) You can use a locally generated Vivado project or insert the files into an existing one.


### Basic Usage

```bash
# Interactive TUI (recommended for first-time users)
sudo python3 pcileech.py tui

# CLI interface for scripted builds
sudo python3 pcileech.py build --bdf 0000:03:00.0 --board pcileech_35t325_x1

# CLI build with custom Vivado settings
sudo python3 pcileech.py build --bdf 0000:03:00.0 --board pcileech_35t325_x1 \
    --vivado-path /tools/Xilinx/2025.1/Vivado --vivado-jobs 8 --vivado-timeout 7200

# Check VFIO configuration
sudo python3 pcileech.py check --device 0000:03:00.0

# Flash firmware to device
sudo python3 pcileech.py flash output/firmware.bin

# Check for updates
./cli --check-version

# Skip automatic version check
./cli build --skip-version-check --bdf 0000:03:00.0 --board pcileech_35t325_x1
```

### Version Updates

The tool automatically checks for newer versions when you run it. You can:
- **Disable automatic checks**: Set `PCILEECH_DISABLE_UPDATE_CHECK=1` environment variable
- **Force a version check**: Run `./cli --check-version`
- **Skip check for one run**: Use `--skip-version-check` flag


### Development from Repository

```bash
git clone https://github.com/ramseymcgrath/PCILeechFWGenerator.git
cd PCILeechFWGenerator
python3 -m venv .venv && source .venv/bin/activate
pip install -r requirements.txt
sudo -E python3 pcileech.py tui
```

## Troubleshooting

Having issues? Check our comprehensive **[Troubleshooting Guide](https://pcileechfwgenerator.ramseymcgrath.com/troubleshooting)** which covers:

- **VFIO Setup Issues** - IOMMU configuration, module loading, device binding
- **Installation Problems** - Package dependencies, container setup
- **BAR Detection Issues** - Power state problems, device compatibility  
- **Device-Specific Issues** - Known problems with specific hardware

Quick diagnostic command:
```bash
# Check VFIO setup and device compatibility
sudo python3 pcileech.py check --device 0000:03:00.0 --interactive
``` 

## Direct Documentation Links

- **[Troubleshooting Guide](https://pcileechfwgenerator.ramseymcgrath.com/troubleshooting)** - Comprehensive troubleshooting and diagnostic guide
- **[Device Cloning Process](https://pcileechfwgenerator.ramseymcgrath.com/device-cloning)** - Complete guide to the cloning workflow
- **[Firmware Uniqueness](https://pcileechfwgenerator.ramseymcgrath.com/firmware-uniqueness)** - How authenticity is achieved
- **[Manual Donor Dump](https://pcileechfwgenerator.ramseymcgrath.com/manual-donor-dump)** - Step-by-step manual extraction
- **[Development Setup](https://pcileechfwgenerator.ramseymcgrath.com/development)** - Contributing and development guide
- **[TUI Documentation](https://pcileechfwgenerator.ramseymcgrath.com/tui-readme)** - Interactive interface guide
- **[Config space info](https://pcileechfwgenerator.ramseymcgrath.com/config-space-shadow)** - Config space shadow info

## Cleanup & Safety

- **Rebind donors**: Use TUI/CLI to rebind donor devices to original drivers
- **Keep firmware private**: Generated firmware contains real device identifiers
- **Use isolated build environments**: Never build on production systems
- **Container cleanup**: `podman rmi pcileechfwgenerator:latest`

> [!IMPORTANT]
> This tool is intended for educational research and legitimate PCIe development purposes only. Users are responsible for ensuring compliance with all applicable laws and regulations. The authors assume no liability for misuse of this software.

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
