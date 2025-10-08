#!/usr/bin/env python3
"""Debug script to test cfg_shadow.sv.j2 template rendering."""

from jinja2 import Template
from pathlib import Path
import re

# Load the template
template_path = Path("src/templates/sv/cfg_shadow.sv.j2")
template_content = template_path.read_text()

# Create minimal context
context = {
    "header": "// Generated SystemVerilog file",
    "CONFIG_SPACE_SIZE": 4096,
    "OVERLAY_ENTRIES": 32,
    "EXT_CFG_CAP_PTR": 256,
    "EXT_CFG_XP_CAP_PTR": 256,
    "HASH_TABLE_SIZE": 256,
    "ENABLE_SPARSE_MAP": 1,
    "ENABLE_BIT_TYPES": 1,
    "DUAL_PORT": False,
    "OVERLAY_MAP": {
        0x004: [0xFFFFFFFF, 0x11111111],  # Legacy format
        0x008: [0x0000FFFF, 0x11112222, "Full entry"],  # Enhanced with bit types
        0x010: [0xFF00FF00, 0x11223344],  # Mixed bit types
    },
}

# Render template
template = Template(template_content)
result = template.render(**context)

# Save output for inspection
with open("debug_output.sv", "w") as f:
    f.write(result)

print("Template rendered successfully. Output saved to debug_output.sv")

# Check for overlay constants
if "OVR_IDX_004" in result:
    print("✓ Found OVR_IDX_004")
else:
    print("✗ Missing OVR_IDX_004")

# Extract the relevant section
lines = result.split("\n")
for i, line in enumerate(lines):
    if (
        "Auto-generated overlay constants" in line
        or "overlay constants" in line.lower()
    ):
        print(f"\nFound overlay section at line {i}:")
        for j in range(max(0, i - 5), min(len(lines), i + 20)):
            print(f"{j}: {lines[j]}")
        break

# Check begin/end balance
begin_count = len(re.findall(r"\bbegin\b", result))
end_count = len(re.findall(r"\bend\b(?!module|function|case|generate)", result))
print(f"\nBegin/end balance: {begin_count} begins, {end_count} ends")

# Find all unmatched begins
begin_lines = []
end_lines = []
for i, line in enumerate(lines):
    if re.search(r"\bbegin\b", line):
        begin_lines.append((i, line.strip()))
    if re.search(r"\bend\b(?!module|function|case|generate)", line):
        end_lines.append((i, line.strip()))

if len(begin_lines) != len(end_lines):
    print("\nUnmatched begin/end pairs:")
    print(f"Total begins: {len(begin_lines)}")
    print(f"Total ends: {len(end_lines)}")
