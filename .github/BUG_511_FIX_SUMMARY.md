# Bug #511 Fix: Device IDs Not Being Used from Collected Hardware

## Problem Description

Users reported that when running the PCILeech firmware generator against a donor device (e.g., an audio controller), the generated Vivado project used **default device IDs** instead of the **actual collected device IDs** from the hardware.

### User Report
- Device: Audio controller (PCI device at 0000:00:1e.0)
- Expected: Vendor ID, Device ID, and Class Code from the audio controller
- Actual: Default/fallback values were used in Vivado project
- Impact: Generated firmware would not properly emulate the donor device

## Root Cause Analysis

The issue was in the **device collection and build pipeline**:

1. **Host Collection Issue**: The `HostCollector` only saved raw `config_space_hex` to `device_context.json` without extracting the device identification fields (vendor ID, device ID, class code, revision ID, subsystem IDs).

2. **Build Pipeline Issue**: When the build system loaded the host context, it had no device ID information available, so the template system fell back to default values (e.g., Realtek 0x10ec:0x8168 or generic 0x0000).

## Solution Implemented

### 1. Enhanced Host Collector (`src/host_collect/collector.py`)

Added device ID extraction during host collection:

```python
def _extract_device_ids(self, cfg: bytes) -> Dict[str, Any]:
    """Extract device identification fields from config space."""
    # Extracts:
    # - Vendor ID (offset 0x00-0x01)
    # - Device ID (offset 0x02-0x03)  
    # - Revision ID (offset 0x08)
    # - Class Code (offset 0x09-0x0B)
    # - Subsystem Vendor ID (offset 0x2C-0x2D)
    # - Subsystem Device ID (offset 0x2E-0x2F)
```

Modified `device_context.json` to include extracted device IDs:

```json
{
  "config_space_hex": "...",
  "vendor_id": 32902,
  "device_id": 2572,
  "class_code": 262912,
  "revision_id": 1,
  "subsystem_vendor_id": 32902,
  "subsystem_device_id": 29217
}
```

### 2. Fixed Build Pipeline (`src/build.py`)

Enhanced the host context branch to properly use collected device IDs:

```python
# Extract config space bytes for processing
config_space_hex = host_context.get("config_space_hex", "")
config_space_bytes = bytes.fromhex(config_space_hex) if config_space_hex else b""

# Build config_space_data with device IDs from host context
config_space_data = {
    "raw_config_space": config_space_bytes,
    "config_space_hex": config_space_hex,
    "vendor_id": format(host_context.get("vendor_id", 0), "04x"),
    "device_id": format(host_context.get("device_id", 0), "04x"),
    "class_code": format(host_context.get("class_code", 0), "06x"),
    "revision_id": format(host_context.get("revision_id", 0), "02x"),
    "device_info": {
        "vendor_id": host_context.get("vendor_id"),
        "device_id": host_context.get("device_id"),
        # ... other fields
    },
}
```

## Testing

### New Comprehensive Test Suite (`tests/test_device_id_propagation.py`)

Created 11 new tests covering:

1. **Device ID Extraction Tests**
   - Audio controller device ID extraction
   - Network controller device ID extraction
   - Short config space handling

2. **Host Collector Save Tests**
   - Verify device_context.json contains device IDs
   - Verify subsystem IDs are included

3. **Build Pipeline Tests**
   - Verify build uses collected device IDs
   - Verify config_space_data is properly populated

4. **Regression Tests**
   - Ensure default vendor ID (0x10ec) NOT used when real ID collected
   - Ensure default device ID (0x8168) NOT used when real ID collected
   - Ensure generic class code (0x000000) NOT used when real class collected

5. **Backward Compatibility Tests**
   - Old format device_context.json (without device IDs) correctly falls back to VFIO

### Updated Existing Tests (`tests/test_host_collector.py`)

Enhanced existing test to verify device IDs are extracted and saved.

### Test Results

```bash
$ python3 -m pytest tests/test_device_id_propagation.py tests/test_host_collector.py -v
===================================== 12 passed in 0.24s ======================================
```

All existing tests continue to pass:

```bash
$ python3 -m pytest tests/test_build_preloaded_config.py -v
====================================== 9 passed in 0.21s ======================================
```

## Verification

To verify the fix works:

1. Run host collection against a donor device:
   ```bash
   sudo -E python3 pcileech.py build --bdf 0000:00:1e.0 --board pcileech_enigma_x1
   ```

2. Check `pcileech_datastore/device_context.json`:
   ```json
   {
     "config_space_hex": "...",
     "vendor_id": 32902,  // Intel 0x8086
     "device_id": 2572,   // HD Audio 0x0a0c
     "class_code": 262912, // Audio 0x040300
     ...
   }
   ```

3. Open the Vivado project and verify device IDs match the donor device.

## Impact

- ✅ Device IDs from actual hardware are now properly propagated
- ✅ No more fallback to default values when real data is available
- ✅ Vivado projects now correctly use donor device identification
- ✅ All existing functionality remains intact
- ✅ Comprehensive test coverage prevents regression

## Files Changed

1. `src/host_collect/collector.py` - Added device ID extraction
2. `src/build.py` - Enhanced host context usage
3. `tests/test_device_id_propagation.py` - New comprehensive test suite
4. `tests/test_host_collector.py` - Enhanced existing test

## Related Issues

Fixes #511 - Bug 0143: Device IDs not populated from donor device
