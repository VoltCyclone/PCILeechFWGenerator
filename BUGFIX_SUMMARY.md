# Bug Fix Summary - v0.14.0-beta.1 Issues

## Issues Reported by Users

Users encountered the following issues when running:
```bash
sudo .venv/bin/python3 pcileech.py build --bdf 0000:06:00.0 --board 35t
```

### 1. **Missing TUI Dependencies Warning (False Positive)**
**Error:**
```
❌ Critical packages missing:
   - textual: TUI functionality (install with: pip install textual)
   - rich: Rich text display (install with: pip install rich)
```

**Root Cause:** The dependency checker always required `textual` and `rich`, even when users were running CLI commands (not TUI).

**Fix:** Modified `pcileech.py::check_critical_imports()` to only require TUI dependencies when the `tui` command is being used. CLI commands now only require `psutil`.

**Changed File:** `pcileech.py` (lines 260-283)

---

### 2. **Invalid Board Shorthand**
**Error:**
```
error: argument --board: invalid choice: '35t' (choose from 'pcileech_35t325_x4', 'pcileech_35t325_x1', ...)
```

**Root Cause:** CLI required exact full board names. Users naturally tried shorthands like `35t` or `75t484`.

**Fix:** 
- Added `resolve_board_name()` function in `src/cli/cli.py` that supports:
  - Exact matches: `pcileech_35t325_x1`
  - Prefix matches: `35t325_x1` → `pcileech_35t325_x1`
  - Fuzzy matches: `35t` → `pcileech_35t325_x1` (when unambiguous)
  - Clear error messages when ambiguous or invalid

- Removed hardcoded `choices` from argparse to allow flexible input
- Updated both `build` and `flash` commands to use board resolution

**Changed Files:**
- `src/cli/cli.py` (added `resolve_board_name()`, updated build_sub, flash_sub)

**Example Usage:**
```bash
# All of these now work:
sudo pcileech build --bdf 0000:06:00.0 --board pcileech_35t325_x1  # Full name
sudo pcileech build --bdf 0000:06:00.0 --board 35t325_x1          # Without prefix
sudo pcileech build --bdf 0000:06:00.0 --board 35t                # Shorthand
```

---

### 3. **Missing voltcyclone-fpga Submodule in Container**
**Error (in container):**
```
[PCIL] Failed to build board configuration: voltcyclone-fpga submodule not found at /app/lib/voltcyclone-fpga
```

**Root Cause:** The `Containerfile` didn't copy the `lib/voltcyclone-fpga` directory into the container image.

**Fix:** Added `COPY lib/voltcyclone-fpga ./lib/voltcyclone-fpga` to the Containerfile build stage.

**Changed File:** `Containerfile` (line added after `COPY configs`)

---

### 4. **Missing SystemVerilog Module Validation Error**
**Error:**
```
[PCIL] PCILeech firmware generation failed: Missing required SystemVerilog modules: ['pcileech_tlps128_bar_controller']
```

**Root Cause:** Architecture change - the codebase moved from generating full `.sv` modules to generating `.coe` overlay files only. The BAR controller (`pcileech_tlps128_bar_controller.sv`) is now a static file from `lib/voltcyclone-fpga`, not a generated template.

However, the validation code in `pcileech_generator.py` still expected the old module to be generated.

**Fix:** Updated `_validate_generated_firmware()` in `src/device_clone/pcileech_generator.py` to:
- Check for expected overlay files (`pcileech_cfgspace.coe`) instead of full modules
- Remove validation for `pcileech_tlps128_bar_controller` (now static)
- Added documentation explaining the architecture change

**Changed File:** `src/device_clone/pcileech_generator.py` (lines 2020-2050)

---

## Documentation Updates

Updated `README.md` to show board shorthand examples:
```bash
# Or use shorthand:
sudo pcileech build --bdf 0000:03:00.0 --board 35t
```

---

## Testing Recommendations

Before release, verify:

1. **Board Resolution:**
   ```bash
   # Should work:
   sudo pcileech build --bdf <device> --board 35t
   sudo pcileech build --bdf <device> --board pcileech_35t325_x1
   
   # Should fail with clear error:
   sudo pcileech build --bdf <device> --board xyz
   ```

2. **TUI Dependency Check:**
   ```bash
   # Should NOT warn about textual/rich:
   sudo pcileech build --bdf <device> --board 35t
   
   # SHOULD warn if missing textual/rich:
   sudo pcileech tui
   ```

3. **Container Build:**
   ```bash
   # Should successfully copy voltcyclone-fpga submodule:
   sudo pcileech build --bdf <device> --board 35t
   # Check container logs for board discovery success
   ```

4. **Validation:**
   ```bash
   # Should NOT fail with "Missing required SystemVerilog modules":
   sudo pcileech build --bdf <device> --board 35t
   # Should complete successfully and generate .coe files
   ```

---

## Summary of Changed Files

1. `Containerfile` - Added voltcyclone-fpga submodule copy
2. `src/cli/cli.py` - Added board shorthand resolution
3. `pcileech.py` - Made TUI dependencies conditional
4. `src/device_clone/pcileech_generator.py` - Updated validation for overlay-only architecture
5. `README.md` - Documented board shorthand feature

---

## Backward Compatibility

✅ All changes are backward compatible:
- Full board names still work
- Exact board names (with prefix) still work
- New shorthand feature is additive
- CLI commands work as before

---

## Commit Message Suggestion

```
fix: resolve multiple v0.14.0-beta.1 user-reported issues

- Add board shorthand resolution (e.g., '35t' → 'pcileech_35t325_x1')
- Suppress TUI dependency warnings for CLI commands
- Fix missing voltcyclone-fpga submodule in container
- Update validation for overlay-only architecture (no more bar_controller module check)
- Document board shorthand feature in README

Fixes issues reported in #444
```
