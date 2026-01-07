# visualize_coe.py Demo

This directory contains demonstration scripts for the `visualize_coe.py` utility, which visualizes PCIe configuration space from Xilinx .coe (coefficient) files.

## Automatic Build Reports

**NEW**: The COE visualization is now automatically integrated into the build process! After a successful firmware build, a PCIe Configuration Space Report will be generated showing:
- Device/Vendor IDs injected into the firmware
- Subsystem IDs that were modified
- Complete PCIe configuration space layout

This works for both local and containerized builds. To disable the automatic report, set:
```bash
export PCILEECH_DISABLE_COE_REPORT=1
```

## Demo Files

- **demo_visualize_coe_auto.py** - Automated demonstration (recommended)
- **demo_visualize_coe.py** - Interactive demonstration with pause prompts

## Running the Demo

### Automated Demo (Recommended)
```bash
./demo_visualize_coe_auto.py
```

or

```bash
python3 demo_visualize_coe_auto.py
```

This will automatically run through all demo scenarios without user interaction.

### Interactive Demo
```bash
./demo_visualize_coe.py
```

This version pauses between each demo section, waiting for you to press Enter.

## What the Demo Shows

The demonstration creates sample .coe files and shows:

1. **Single File Visualization** - Display PCIe config space for a Xilinx device
2. **File Comparison** - Compare template (Xilinx) vs generated (Intel) showing ID injection
3. **Vendor Recognition** - Display an NVIDIA device showing vendor name lookup
4. **Usage Examples** - Command-line usage patterns

## Sample Output

### Single File Visualization
```
║ 0x00: Device/Vendor ID     │ 0x1541:0x8086      ║
║      └─ Device: 0x1541  Vendor: 0x8086 (Intel)                     ║
```

### Comparison Output
```
╔════════════════════════════════════════════════════════════════════╗
║                        Device IDs Injected                         ║
╠════════════════════════════════════════════════════════════════════╣
║ 0x00: Device/Vendor ID                                             ║
║      → 0x1541:0x8086 (donor device)                                ║
║ 0x2C: Subsystem IDs                                                ║
║      → 0x0001:0x8086                                               ║
╚════════════════════════════════════════════════════════════════════╝
```

## Features Demonstrated

✓ Parsing .coe memory initialization vectors  
✓ Decoding PCIe configuration space (Device/Vendor IDs, BARs, etc.)  
✓ Recognizing vendor names (Intel, AMD, NVIDIA, Xilinx, etc.)  
✓ Comparing files to show injected device IDs  
✓ Pretty-printed box drawing output  

## Real-World Usage

After running the demo, you can use the tool on real .coe files:

```bash
# Visualize a single file
./scripts/visualize_coe.py pcileech_datastore/35T/pcie_7x_0_config_rom_template.coe

# Compare template vs generated
./scripts/visualize_coe.py \
  pcileech_datastore/35T/pcie_7x_0_config_rom_template.coe \
  output/some_device/pcie_7x_0_config_rom.coe
```

## Understanding the Output

- **Device ID** - Upper 16 bits of DWORD at offset 0x00
- **Vendor ID** - Lower 16 bits of DWORD at offset 0x00  
- **Subsystem IDs** - DWORD at offset 0x2C
- **BARs** - Base Address Registers at offsets 0x10-0x24
- **Class/Revision** - Device class code and revision at offset 0x08

## Known Vendors

The tool recognizes these vendor IDs:
- 0x10EE - Xilinx
- 0x8086 - Intel
- 0x10DE - NVIDIA
- 0x1022 - AMD
- 0x1002 - AMD/ATI
- 0x10EC - Realtek
