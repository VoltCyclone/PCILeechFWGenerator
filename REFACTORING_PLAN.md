# PCILeechFWGenerator Overlay Refactoring Plan

## Objective
Convert from a full firmware generator to a **minimal overlay generator** that produces only device-specific configuration files to be integrated with the upstream pcileech-fpga repository.

## Current State (Problems)
- ❌ Generating full SystemVerilog modules that duplicate pcileech-fpga upstream
- ❌ Templates for bar_controller, fifo, tlp processors exist both here and upstream
- ❌ Bloated codebase with unnecessary generation logic
- ❌ Not aligned with stated goal: "overlay config space from live device"

## Target State (Solution)
- ✅ Generate **ONLY** device-specific overlay files:
  - `pcileech_cfgspace.coe` (donor device config space)
  - `pcileech_cfgspace_writemask.coe` (write protection masks)
  - Device-specific parameters/defines
- ✅ Generate TCL scripts that reference upstream pcileech-fpga sources
- ✅ Copy/symlink upstream sources into build directory
- ✅ Minimal, focused codebase

---

## Phase 1: Cleanup - Remove Unnecessary SV Templates ✂️

### Templates to DELETE (exist in pcileech-fpga upstream):
```
src/templates/sv/
├── bar_controller.sv.j2                    ❌ DELETE
├── basic_bar_controller.sv.j2              ❌ DELETE
├── cfg_shadow.sv.j2                        ❌ DELETE
├── pcileech_fifo.sv.j2                     ❌ DELETE
├── pcileech_tlps128_bar_controller.sv.j2   ❌ DELETE
├── top_level_wrapper.sv.j2                 ❌ DELETE
├── main_module.sv.j2                       ❌ DELETE (if duplicates upstream)
├── advanced_controller.sv.j2               ❌ DELETE (use upstream logic)
└── clock_crossing.sv.j2                    ❌ DELETE (use upstream)
```

### Templates to KEEP (device-specific overlays):
```
src/templates/sv/
├── pcileech_cfgspace.coe.j2                ✅ KEEP (device-specific)
├── pcileech_header.svh.j2                  ✅ KEEP (device parameters)
├── device_config.sv.j2                     ✅ REVIEW (if truly device-specific)
├── pcie_endpoint_defines.sv.j2             ✅ KEEP (device parameters)
└── components/
    └── (device-specific parameter modules only)
```

---

## Phase 2: Refactor Core Modules 🔧

### 2.1 Update `sv_module_generator.py`
**Current**: Generates full SV modules
**New**: Generates only overlay configuration files

Changes:
- Remove `_generate_core_pcileech_modules()` - no longer needed
- Remove `_generate_advanced_controller()` - use upstream
- Keep `_generate_config_space_coe()` - this is device-specific
- Add `_generate_writemask_coe()` - device-specific write protection
- Simplify to focus on `.coe` file generation only

### 2.2 Update `systemverilog_generator.py`
**Current**: Orchestrates full module generation
**New**: Orchestrates overlay generation

Changes:
- Rename `generate_modules()` → `generate_overlay_files()`
- Remove module generation logic
- Focus on generating `.coe` files with donor device data
- Remove `generate_pcileech_modules()` - no longer generating modules
- Keep context building for donor device parameters

### 2.3 Update `tcl_builder.py`
**Current**: Generates TCL scripts for generated SV files
**New**: Generates TCL scripts that reference upstream pcileech-fpga

Changes:
- Update source file lists to point to `lib/voltcyclone-fpga/BOARD/src/`
- Add logic to copy/reference upstream sources
- Update project setup to include upstream IP cores
- Modify templates to reference correct paths

### 2.4 Update `pcileech_generator.py`
**Current**: Orchestrates full firmware generation
**New**: Orchestrates overlay generation + upstream integration

Changes:
- Remove SystemVerilog module generation calls
- Focus on generating `.coe` files
- Add logic to copy upstream pcileech-fpga sources
- Update save logic to output overlay files only
- Keep VFIO scanning and donor device profiling

### 2.5 Update `file_manager.py`
**Current**: Manages all generated files
**New**: Manages overlay files + upstream source integration

Changes:
- Remove SV module writing logic
- Add logic to copy upstream sources from submodule
- Create overlay-specific output structure
- Update validation to check overlay + upstream integration

---

## Phase 3: Update Templates 📝

### 3.1 TCL Templates
Update to reference upstream sources:

```tcl
# OLD (generated files):
add_files -norecurse {
    pcileech_fifo.sv
    pcileech_bar_controller.sv
    cfg_shadow.sv
}

# NEW (upstream + overlay):
add_files -norecurse {
    ../lib/voltcyclone-fpga/pciescreamer/src/pcileech_fifo.sv
    ../lib/voltcyclone-fpga/pciescreamer/src/pcileech_pcie_cfg_a7.sv
    ../lib/voltcyclone-fpga/pciescreamer/src/pcileech_pcie_cfgspace_shadow.sv
}

# Add device-specific overlay
add_files -norecurse {
    ip/pcileech_cfgspace.coe
    ip/pcileech_cfgspace_writemask.coe
}
```

### 3.2 Keep Only Essential SV Templates
- `pcileech_cfgspace.coe.j2` - Config space data
- `pcileech_header.svh.j2` - Device-specific parameters
- Device-specific defines/parameters only

---

## Phase 4: Update Tests 🧪

### Tests to UPDATE:
- `test_systemverilog_generator.py` - Test overlay generation, not modules
- `test_sv_module_generator.py` - Test .coe generation, not SV modules
- `test_tcl_builder.py` - Test upstream source references
- `test_pcileech_generator.py` - Test overlay + integration workflow
- `test_file_manager.py` - Test overlay file structure

### Tests to REMOVE:
- Tests for deleted SV module generation
- Tests for duplicate functionality

### Tests to ADD:
- Test upstream source discovery
- Test overlay + upstream integration
- Test .coe file validation
- Test end-to-end overlay workflow

---

## Phase 5: Documentation Updates 📚

### Documentation to UPDATE:
- README.md - Explain new overlay-only approach
- site/docs/architecture.md - Update architecture diagrams
- site/docs/template-reference.md - Remove deleted templates
- site/docs/usage.md - Update workflow examples

### New Documentation:
- site/docs/overlay-integration.md - How overlays work with upstream
- site/docs/upstream-sources.md - PCILeech-FPGA integration guide

---

## Output Structure (Final)

```
output/pciescreamer_8086_1234/
├── ip/
│   ├── pcileech_cfgspace.coe          ← Generated (donor-specific)
│   └── pcileech_cfgspace_writemask.coe ← Generated (donor-specific)
├── src/                                ← Copied from upstream
│   ├── pcileech_fifo.sv
│   ├── pcileech_pcie_cfg_a7.sv
│   ├── pcileech_pcie_cfgspace_shadow.sv
│   ├── pcileech_pcie_tlp_a7.sv
│   ├── pcileech_com.sv
│   ├── pcileech_ft601.sv
│   ├── pcileech_mux.sv
│   └── pcileech_pciescreamer_top.sv
├── constraints/                        ← Copied from upstream
│   └── pcileech_pciescreamer.xdc
├── tcl/
│   ├── vivado_generate_project.tcl    ← Generated (references upstream + overlay)
│   └── vivado_build.tcl               ← Generated (build automation)
└── metadata.json                       ← Generation metadata
```

---

## Benefits of This Approach ✨

1. **Minimal Generation** - Only generate what's truly device-specific
2. **Upstream Compatibility** - Always use latest pcileech-fpga code
3. **Reduced Maintenance** - No duplicate module implementations
4. **Clearer Purpose** - Tool does exactly what it says: overlays config space
5. **Easier Updates** - Just update submodule to get latest upstream
6. **Smaller Codebase** - Remove thousands of lines of unnecessary code

---

## Migration Strategy 🚀

### Step-by-step execution:
1. ✅ Create this plan document
2. ⏳ Delete unnecessary SV templates
3. ⏳ Refactor sv_module_generator.py
4. ⏳ Refactor systemverilog_generator.py  
5. ⏳ Update tcl_builder.py
6. ⏳ Update pcileech_generator.py
7. ⏳ Update file_manager.py
8. ⏳ Update TCL templates
9. ⏳ Update/fix all tests
10. ⏳ Update documentation
11. ⏳ Validate end-to-end workflow

### Rollback Strategy:
- This refactoring is on `git-refactor` branch
- Can revert entire branch if needed
- Keep commits atomic for selective reverts

---

## Success Criteria ✅

- [ ] No duplicate SV module generation (use upstream only)
- [ ] Only .coe files and parameters are generated
- [ ] TCL scripts correctly reference upstream sources
- [ ] All tests pass with new overlay-only approach
- [ ] End-to-end workflow: scan → generate overlay → build with upstream
- [ ] Documentation clearly explains overlay approach
- [ ] Codebase is significantly smaller and focused
