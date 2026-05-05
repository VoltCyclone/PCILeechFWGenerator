# Issue #593 — Donor IDs not reaching FIFO; 75t484_x1 synthesis broken

Branch: `fix/issue-593-xilinx-defaults-fifo`
Goal: fix the two related symptoms reported in [#593](https://github.com/VoltCyclone/PCILeechFWGenerator/issues/593) — donor vendor/device/subsys/rev IDs reaching the upstream `pcileech_fifo.sv` runtime register file, and the 75t484_x1 board synthesizing cleanly.

## Background

The user reports two failures driven by the same underlying gap:

1. **Symptom A — board `75t` (EnigmaX1):** build succeeds, but the generated `pcileech_fifo.sv` still contains Xilinx defaults (`16'h10EE` vendor, `16'h0666` device, `8'h02` rev) at its `_pcie_core_config` reset and at the `rw[143:128]…rw[199:192]` reset block. Donor IDs land in `pcileech_cfgspace.coe` (BRAM) but never in the FIFO's runtime control register.
2. **Symptom B — board `pcileech_75t484_x1` (CaptainDMA/75t484_x1):** synthesis fails with `cannot resolve hierarchical name for the item 'pcie_cfg_subsys_vend_id'` at `pcileech_fifo.sv:311`, plus follow-on "part-select width could not be resolved to a constant" and `failed synthesizing module 'pcileech_fifo'` / `pcileech_75t484_x1_top`. Root cause: the upstream 75t484_x1 `pcileech_fifo.sv` writes to `dpcie.pcie_cfg_subsys_vend_id`, `pcie_cfg_subsys_id`, `pcie_cfg_vend_id`, `pcie_cfg_dev_id`, `pcie_cfg_rev_id` (lines 311–315), but the matching `pcileech_header.svh` `IfPCIeFifoCore` interface (lines 244–265) declares only `pcie_rst_core`, `pcie_rst_subsys`, and DRP signals. The fifo references interface members that do not exist — an upstream submodule self-inconsistency.

`pcie_cfg_subsys_vend_id` is grepped only in `lib/voltcyclone-fpga/`; our generator never emits it. `lib/voltcyclone-fpga` is a git submodule (`VoltCyclone/voltcyclone-fpga`), so any patch to its files belongs upstream **or** must be applied as a generation-time post-processing step on the staged copies in `pcileech_datastore/output/<board>/src/`. Direct modification of `lib/voltcyclone-fpga/...` will be wiped by the next submodule update and is out of scope here.

The donor-ID flow currently only injects values into `pcileech_cfgspace.coe.j2` (via `src/templating/sv_overlay_generator.py:57-124`). There is no Vivado IP `set_property CONFIG.Vendor_ID/Device_ID/...` step (grep finds these strings only under `tests/`) and no patch step on `pcileech_fifo.sv`. Both gaps need closing.

Source-file copy lives in `src/vivado_handling/pcileech_build_integration.py:207-286` (`_copy_source_files`) via `TemplateDiscovery.get_source_files(board_name)` (`src/file_management/template_discovery.py:228-269`). Insert any post-copy patching there.

## Conventions

- One commit per task. Conventional-commit prefix: `fix(build): ...` or `fix(template): ...` matching the area.
- Every task ships with a regression test under `tests/`. New tests should sit beside the closest existing siblings.
- Run `pytest tests/<area> -x` plus the task-specific subset before declaring done. Full repo suite is not required for a green light, but CI must pass before merge.
- Do **not** modify files under `lib/voltcyclone-fpga/` — those changes will be lost on submodule update. Patch staged outputs instead.
- Do **not** rework the donor-injection pipeline beyond what these tasks need. The cfgspace-overlay path stays as-is unless a task explicitly says otherwise.
- No drive-by board-config changes. If another board has a similar interface mismatch, leave a `# TODO(issue-593-followup):` and surface it in the commit body.

## Tasks

### T1 — Patch staged FIFO sources to inject donor IDs into the RW reset block (Symptom A)

**File to edit:** `src/vivado_handling/pcileech_build_integration.py` (or a new `src/vivado_handling/fifo_donor_patcher.py` invoked from there). Pick the new module if the patcher exceeds ~40 lines.

**Bug:** `_copy_source_files` copies `pcileech_fifo.sv` verbatim from `lib/voltcyclone-fpga/<board>/src/` to the staged output. Donor IDs are never written into the FIFO's reset values. The host therefore sees the upstream Xilinx defaults until/unless software writes to the RW register at runtime — which the PCILeech runtime does not currently do for these fields (the upstream comments mark them `(NOT IMPLEMENTED)`).

**Fix:** after copying `pcileech_fifo.sv` into the per-board output directory, run a deterministic textual rewrite that replaces the four hardcoded ID literals in the RW reset block with the donor's values. Anchor on the upstream comments to avoid false matches:

| Line tag (upstream) | Field | Replace with |
|---|---|---|
| `// +010: CFG_SUBSYS_VEND_ID` | `rw[143:128] <= 16'h10EE;` | `16'h<donor.subsys_vendor_id>` |
| `// +012: CFG_SUBSYS_ID`      | `rw[159:144] <= 16'h0007;` | `16'h<donor.subsys_id>` |
| `// +014: CFG_VEND_ID`        | `rw[175:160] <= 16'h10EE;` | `16'h<donor.vendor_id>` |
| `// +016: CFG_DEV_ID`         | `rw[191:176] <= 16'h0666;` | `16'h<donor.device_id>` |
| `// +018: CFG_REV_ID`         | `rw[199:192] <= 8'h02;`    | `8'h<donor.revision_id>` |

Also rewrite the `_pcie_core_config` packed initializer on line ~216 (`reg [79:0] _pcie_core_config = { 4'hf, 1'b1, 1'b1, 1'b0, 1'b0, 8'h02, 16'h0666, 16'h10EE, 16'h0007, 16'h10EE };`) using the same donor values, preserving the leading control bits. Build the literal from the donor dict and substitute the whole RHS.

Use regex with the comment as anchor (e.g. `r"rw\[143:128\]\s*<=\s*16'h[0-9A-Fa-f]{4};\s*//\s*\+010: CFG_SUBSYS_VEND_ID"`) so a future upstream edit that changes the hex value still matches. If any anchor fails to match, raise — do **not** silently skip. The build is broken if we can't patch.

Take donor IDs from the same source `sv_overlay_generator.generate_config_space_overlay` already consumes (the `device_config` / `config_space` dict). Pass it through from the build integration entry point.

**Acceptance:**
- New test `tests/vivado_handling/test_fifo_donor_patcher.py` with: a synthetic upstream `pcileech_fifo.sv` containing the five anchor lines and the `_pcie_core_config` initializer; a donor dict (`{vendor_id: 0x8086, device_id: 0x1533, subsys_vendor_id: 0x8086, subsys_id: 0x0001, revision_id: 0x03}`); assert all five lines and the initializer come out with the donor values and untouched anchor comments.
- Negative test: missing one anchor → patcher raises (chosen exception type, message names the missing anchor).
- Integration-style test that runs `_copy_source_files` against a fixture board dir, points it at a temp output, and greps the output `pcileech_fifo.sv` for the donor literal.
- Manual: regenerate firmware for a 75t board against any donor; confirm the staged `pcileech_fifo.sv` no longer contains `16'h10EE` or `16'h0666` (unless the donor IS Xilinx).

### T2 — Emit Vivado IP `CONFIG.Vendor_ID` / `Device_ID` / `Subsystem_*` / `Revision_ID` (Symptom A, hardware-visible)

**File to edit:** `src/templating/tcl_builder.py` (and whichever template emits `02_ip_config.tcl` — find via `grep -n "02_ip_config" src/`).

**Bug:** the PCIe 7-series IP block is what the host reads during enumeration. The generator never overrides its `CONFIG.Vendor_ID`/`Device_ID`/`Subsystem_Vendor_Id`/`Subsystem_Id`/`Revision_ID`, so even with the cfgspace shadow correctly populated, the IP itself reports Xilinx defaults during link bring-up and BAR enumeration on some flows. `grep -rn "CONFIG\.Device_ID" src/ tests/` returns hits only under `tests/`, confirming no production code path emits these.

**Fix:** in the IP-config TCL stage, add `set_property -dict [list CONFIG.Vendor_ID 0x<vid> CONFIG.Device_ID 0x<did> CONFIG.Subsystem_Vendor_Id 0x<svid> CONFIG.Subsystem_Id 0x<ssid> CONFIG.Revision_ID 0x<rev>] [get_ips <pcie_ip_name>]` after the existing IP creation. The IP name varies by board (`pcie_7x_0` for most A7 boards) — surface it from the existing board config rather than hardcoding.

If the IP-config TCL is not currently templated (i.e. it's a static file under `lib/voltcyclone-fpga/<board>/`), add a generation-time appendage written to a separate TCL fragment that the master script sources after the upstream IP-config step. Do not modify the vendored TCL in place.

**Acceptance:**
- Unit test that calls the TCL builder with a donor dict and asserts the rendered TCL contains the five `CONFIG.*` properties with the correct hex values.
- Test that asserts the generated TCL still parses (basic balanced-bracket / `set_property` syntax check is enough — full TCL eval is overkill).
- Manual: open the generated `02_ip_config.tcl` (or appended fragment), grep for `CONFIG.Vendor_ID`, confirm donor value.

### T3 — Make 75t484_x1 synthesize: drop fifo writes to undeclared interface fields (Symptom B)

**File to edit:** the same patcher introduced in T1, or a sibling step in `_copy_source_files`.

**Bug:** `lib/voltcyclone-fpga/CaptainDMA/75t484_x1/src/pcileech_fifo.sv:311-315` writes to `dpcie.pcie_cfg_subsys_vend_id`, `.pcie_cfg_subsys_id`, `.pcie_cfg_vend_id`, `.pcie_cfg_dev_id`, `.pcie_cfg_rev_id`. The corresponding `IfPCIeFifoCore` interface (`pcileech_header.svh:244-265`) declares only `pcie_rst_core`, `pcie_rst_subsys`, and DRP signals. Synthesis errors out on the first hierarchical reference; the cascading "part-select width could not be resolved" comes from `_pcie_core_config[0+:16]` losing its driver chain after the failed assigns.

Two viable fixes; pick **(b)**:

(a) Add the missing fields to the `IfPCIeFifoCore` interface in the staged `pcileech_header.svh`. Then we also need to wire them through `pcileech_pcie_a7.sv` (line 157 has the wiring commented out) and verify the IP block actually consumes them. Larger blast radius, more risk.

(b) **Comment out the five offending assigns** in the staged `pcileech_fifo.sv` for boards whose `pcileech_header.svh` does not declare those fields. The fields are marked `(NOT IMPLEMENTED)` in the upstream RW reset comments anyway — the design currently relies on the cfgspace BRAM (and, after T2, the IP `CONFIG.*` properties) for actual ID delivery. Removing these assigns restores parity with EnigmaX1, NeTV2, etc., which compile cleanly and ship the same way.

Implementation for (b):
1. Detect at patch time whether the board's `pcileech_header.svh` `IfPCIeFifoCore` interface contains `pcie_cfg_subsys_vend_id` (simple substring grep of the staged header).
2. If absent, rewrite the five `assign dpcie.pcie_cfg_<field> = …;` lines (anchored on `dpcie.pcie_cfg_`) into commented-out form: `// assign dpcie.pcie_cfg_<field> = …; // [issue-593] interface field not declared`.
3. Leave `pcie_rst_core` and `pcie_rst_subsys` assigns untouched — those exist in the interface.

This task layers on top of T1 (T1 already runs a patch pass over `pcileech_fifo.sv`); fold T3's rewrite into the same pass.

**Acceptance:**
- Unit test feeds a fixture upstream `pcileech_fifo.sv` (75t484_x1 shape) and a fixture `pcileech_header.svh` missing the cfg-id fields; asserts the five offending assigns are commented out and the two `pcie_rst_*` assigns are not touched.
- Same test with a header that DOES declare the fields → assigns are left alone.
- Integration test or manual repro: stage 75t484_x1 sources, run synthesis (or at least Vivado elaborate / `read_verilog` + `synth_design -rtl`), confirm no `cannot resolve hierarchical name` errors.

### T4 — Surface a clear error when board sources are internally inconsistent

**File to edit:** `src/vivado_handling/pcileech_build_integration.py` (or sibling validator module if one already enforces source-set sanity).

**Bug:** the user's first signal that anything was wrong was a Vivado synthesis error after a multi-minute build. The patcher in T3 already detects the inconsistency. Surface it earlier so the user knows the build is fine but a known-broken upstream board was repaired in-flight.

**Fix:** when T3's patcher commented anything out, log a single warning at the build's source-prep step naming the board, the file, and the action ("commented N undefined-interface assigns; fields are delivered via cfgspace shadow / IP CONFIG"). Do not raise — the build should succeed.

If a board's source set has the inconsistency in a form the patcher does **not** know how to fix (e.g. the same problem in a different file we don't know about), raise a clean error from the source-prep step with the file and line, before Vivado runs.

**Acceptance:**
- Unit test asserts the warning fires once when the patcher mutates anything, and not at all when it doesn't.
- Unit test for the unknown-inconsistency raise path: feed a malformed fifo with an *additional* unknown-field assign that the patcher's anchor regex doesn't cover; assert a build-time error names the file and line.

### T5 — Smoke regression for 75t and 75t484_x1 source generation

**File to add:** `tests/vivado_handling/test_board_source_generation.py` (or the closest existing sibling).

**Bug:** the codebase has no end-to-end test that exercises `_copy_source_files` for the boards reported in #593. Without one, future submodule updates can re-introduce the same class of issue silently.

**Fix:** add two parametrized smoke tests using a stub donor dict:

1. `_copy_source_files` for board `75t` (EnigmaX1) → output `pcileech_fifo.sv` contains the donor's vendor literal at the `CFG_VEND_ID` line and does NOT contain `16'h10EE` (assuming a non-Xilinx donor); `pcileech_header.svh` is byte-identical to upstream.
2. `_copy_source_files` for board `pcileech_75t484_x1` → output `pcileech_fifo.sv` has the five `dpcie.pcie_cfg_*` assigns commented; the donor's IDs appear in the RW reset; the file passes a basic SV lint (`pyverilog` parse if available, otherwise a balanced-`begin`/`end` and balanced-paren check).

These tests do not run Vivado; they validate the file-rewrite contract. They are the canary for upstream submodule drift.

**Acceptance:**
- Both tests pass on the post-T1/T2/T3/T4 tree.
- Both tests fail cleanly on the pre-fix tree (verify by stashing the patcher and re-running).

## Out of scope

- Reworking the donor-injection pipeline architecture (separate effort).
- Patching `lib/voltcyclone-fpga/` directly or upstreaming a fix (track separately; mention in the PR description so we can file an upstream issue).
- Auditing every other board in `lib/voltcyclone-fpga/CaptainDMA/` for similar interface/fifo mismatches — T3's check covers them defensively at build time, but we are not opening a board-by-board audit ticket here.
- Adding runtime support for the `(NOT IMPLEMENTED)` RW slots so software can rewrite vendor/device IDs on the fly — that's a feature, not a bug fix.

## Verification before PR

- `pytest tests/vivado_handling tests/templating -x`
- Manual build for board `75t` against a non-Xilinx donor; confirm staged `pcileech_fifo.sv` reflects donor IDs.
- Manual build for board `pcileech_75t484_x1` runs through synthesis without the `pcie_cfg_subsys_vend_id` errors.
- PR description names #593, links the upstream submodule inconsistency, and notes that an upstream patch is the durable fix.
