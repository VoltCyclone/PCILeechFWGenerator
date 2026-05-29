# XML-format XCI donor-ID patching — Design

**Date:** 2026-05-29
**Status:** Approved (brainstorming complete)
**Branch:** `fix/xci-xml-donor-ids` (off `main` @ `b9a5f22`)
**Follows:** #622 / PR #624 (merged) — which added `patch_xci_donor_ids` for JSON-format XCIs only.

---

## Problem

`patch_xci_donor_ids` (in `src/vivado_handling/ip_lock_resolver.py`) rewrites the staged `pcie_7x_0.xci` so Vivado's PCIe IP baseline matches the donor before the project opens. It only matches **JSON-format** XCIs. Of 259 XCIs in the submodule, **41 are XML (IP-XACT/spirit) format**, including the `pcie_7x_0.xci` for **3 boards: pciescreamer, acorn_ft2232h, NeTV2**. For those boards the function matches zero fields and (since #624) logs a warning but leaves Xilinx defaults (`10EE`/`0666`/`020000`) in place — the original #622 bug persists for those boards.

## Goal

1. Patch donor IDs into **XML-format** XCIs as well as JSON, closing the gap for the 3 affected boards.
2. Return **richer per-file outcome info** so the caller knows exactly which files were patched, which were seen-but-unmatched, and which errored.
3. In `build.py`, surface unmatched **PCIe-core** files prominently: **prompt to continue when interactive (TTY); warn and auto-continue when non-interactive (CI/container)**. The build always proceeds after acknowledgement — it never silently ships wrong IDs without a visible, prominent message.

## Non-goals (YAGNI)

- Full IP-XACT modeling or schema validation.
- External libraries. Investigation found `ipyxact`/`ipCorePackager` target a newer IP-XACT revision than Vivado's 2009 dialect and model VLNV identity, not the `CONFIG.*`/`MODELPARAM` fields we patch; `hdlmake` only reads the module name. The repo declares **no** XML dependency today. We use only stdlib.
- Changing the JSON path's matching behavior.
- Re-serializing XCIs via a DOM (would churn namespaces/formatting and risk Vivado disliking the rewrite).

---

## Verified facts (from investigation)

- XML XCIs use the **same field-name suffixes and value format** as JSON. Example lines (real files):
  ```
  <spirit:configurableElementValue spirit:referenceId="PARAM_VALUE.Vendor_ID">10EE</spirit:configurableElementValue>
  <spirit:configurableElementValue spirit:referenceId="MODELPARAM_VALUE.ven_id">10EE</spirit:configurableElementValue>
  <spirit:configurableElementValue spirit:referenceId="MODELPARAM_VALUE.class_code">020000</spirit:configurableElementValue>
  <spirit:configurableElementValue spirit:referenceId="PARAM_VALUE.Class_Code_Base">02</spirit:configurableElementValue>
  ```
  User-config block uses `PARAM_VALUE.<LongName>`; generated block uses `MODELPARAM_VALUE.<short_name>`. NeTV2 has **15 patchable ID fields**.
- The codebase does **zero** XML parsing today; all XCI ops are `read_text` / `re.subn` / `write_text`. No XML deps declared.
- Existing interactive-prompt convention (`src/templating/tcl_builder.py:1116`): gate on `sys.stdin.isatty()`, then `input(...)`; raise/abort if not a TTY.
- **Only one production caller** of `patch_xci_donor_ids`: `build.py` (`_patch_fifo_with_donor_ids`, reads the `int` at lines 1958/1967/1973). Plus 3 tests in `tests/test_ip_lock_resolver.py`. All updated here.

---

## Design

### Component 1 — `patch_xci_donor_ids` in `ip_lock_resolver.py`

**New return type.** Replace the bare `int` with a small dataclass:

```python
@dataclass
class XciPatchSummary:
    patched: List[str]      # filenames actually rewritten
    unmatched: List[str]    # XCIs seen but zero ID fields matched (the gap)
    failed: List[str]       # filenames that errored (read/parse/write)
    total_files: int        # total *.xci files discovered

    @property
    def num_patched(self) -> int:
        return len(self.patched)

    def has_unmatched_core(self) -> bool:
        """True if any unmatched/failed file looks like the PCIe core."""
        return any(
            _is_pcie_core_xci(name)
            for name in (*self.unmatched, *self.failed)
        )
```

`_is_pcie_core_xci(name)` → `name.lower().startswith("pcie_7x")` (module-level helper; the PCIe core files in every board are named `pcie_7x_0.xci`).

**Per-file algorithm** (replaces the current single-regex loop):

1. Read text. On read error → append to `failed`, continue.
2. **Sniff** first non-whitespace char: `{` → JSON; `<` → XML; otherwise → unknown.
3. **If XML, validate** well-formedness with `xml.etree.ElementTree.fromstring(text)` inside a try. On `ET.ParseError`/any exception → append to `failed` (malformed), continue. (Validation only; we do NOT use the parsed tree to edit.)
4. Pick the regex template for the detected format and run the substitution loop over the **same** `subs` list already built (vid/did/svid/sid + optional class-code split). Count `total` matches.
   - JSON (unchanged): `("{key}"\s*:\s*\[\s*\{{\s*"value"\s*:\s*)"[0-9A-Fa-f]+"` → `\1"{val}"`
   - XML (new): `(referenceId="(?:PARAM_VALUE|MODELPARAM_VALUE)\.{key}"\s*>)[0-9A-Fa-f]+(?=</)` → `\1{val}`
     - Anchored on the exact `referenceId="…\.<key>"` so `class_code` can't match inside `Class_Code_Base`, and `ven_id` can't match inside `subsys_ven_id` (same key-anchoring discipline as the JSON path). Replaces only the element text between `>` and `</`.
   - Unknown format (neither `{` nor `<`): skip substitution, `total = 0`.
5. If `total > 0` and text changed → write, append to `patched`, log info.
   Elif `total == 0` → append to `unmatched`, log the existing warning (refined to mention both JSON and XML were attempted).
6. Wrap the whole per-file body in try/except → `failed` on any unexpected error (preserves today's never-crash behavior).

Return `XciPatchSummary(...)`.

**Helper for the two regexes.** Factor the substitution loop into a small internal helper that takes `(text, subs, fmt) -> (new_text, total)` to keep the per-file loop readable and each format's pattern isolated/testable. Keep it module-private.

**Security note.** `ET.fromstring` on hostile XML is theoretically exposed to entity-expansion ("billion laughs"). These are trusted submodule files, not user input, so risk is negligible; the parse is wrapped so any exception is treated as "unverified format → `failed`" rather than crashing. (No new dependency; stdlib `xml.etree.ElementTree`.)

### Component 2 — `build.py` `_patch_fifo_with_donor_ids`

Replace the `n_xci = patch_xci_donor_ids(...)` / `if n_xci:` block (lines ~1958-1976) with handling for the new summary:

```python
summary = patch_xci_donor_ids(self.config.output_dir, donor, class_code=..., logger=..., prefix="BUILD")
if summary.num_patched:
    log_info_safe(... "Patched donor IDs into {count} XCI baseline file(s)" ...)

if summary.has_unmatched_core():
    self._handle_unpatched_pcie_core(summary, donor)   # new helper
elif summary.unmatched:
    log_warning_safe(... "Some non-core XCI files were left unpatched: {files}" ...)
```

New helper `_handle_unpatched_pcie_core(self, summary, donor)`:
- Build a prominent multi-line WARNING block: which `pcie_7x*` file(s) were unpatched, the donor IDs that would otherwise be wrong (`vendor=0x.. device=0x..`), and remediation ("file uses an unrecognized XCI format; donor IDs may not reach the bitstream").
- `if sys.stdin.isatty():` print the block, then `resp = input("Continue the build anyway? [y/N]: ")`. If `resp.strip().lower() not in ("y", "yes")` → raise to abort the build with a clear message.
- `else:` (non-TTY) → log the prominent warning via `log_warning_safe` and continue.
- Wrap the prompt in try/except so a prompt failure (e.g. `EOFError`) degrades to warn-and-continue, never an unhandled crash.

The XCI patch continues to run regardless of the PCIe-IP TCL-override outcome (preserved from #624: `extra_ip_cfg` initialized to `None`, override `except` falls through).

---

## Data flow

```
build._patch_fifo_with_donor_ids
  └─ patch_xci_donor_ids(output_dir/ip, donor, class_code)
       └─ for each *.xci:  sniff format → (XML? validate via ET) → regex subn → classify
       └─ returns XciPatchSummary(patched, unmatched, failed, total_files)
  └─ if summary.num_patched: log
  └─ if summary.has_unmatched_core():  _handle_unpatched_pcie_core
        TTY  → prompt [y/N]; "n" aborts build
        !TTY → prominent warning, continue
```

---

## File structure

| File | Change |
|------|--------|
| `src/vivado_handling/ip_lock_resolver.py` | Add `XciPatchSummary` dataclass, `_is_pcie_core_xci`, format-sniff + ET validation + XML regex; change `patch_xci_donor_ids` return type; private substitution helper. |
| `src/build.py` | Update `_patch_fifo_with_donor_ids` to consume the summary; add `_handle_unpatched_pcie_core` (TTY-prompt / non-TTY-warn). |
| `tests/test_ip_lock_resolver.py` | Update 3 existing tests for the new return type; add XML happy-path, XML `class_code=None`, malformed-XML→`failed`, real-fixture (NeTV2/pciescreamer) tests. |
| `tests/` (build) | Add tests for `_handle_unpatched_pcie_core`: non-TTY continues; TTY + "n" aborts; TTY + "y" continues. |

---

## Testing

**`patch_xci_donor_ids` (importlib pattern, per existing file):**
- JSON happy-path still works and returns `XciPatchSummary` with the file in `patched` (regression for the #624 behavior).
- XML happy-path: synthetic spirit-XML fixture with all ID fields → all patched, no `10EE`/`0666`/`020000` remain; file in `patched`.
- XML `class_code=None`: class-code fields untouched, ID fields patched.
- XML regex safety: `class_code` not matched inside `Class_Code_Base`; `ven_id` not inside `subsys_ven_id`.
- Malformed XML (`<not-closed`): lands in `failed`, no crash, no write.
- Unknown format (neither `{`/`<`): lands in `unmatched`.
- **Real fixture:** copy an actual `NeTV2` (or `pciescreamer`) `pcie_7x_0.xci` into a tmp `ip/` dir, run, assert all 15 fields patched and no Xilinx defaults remain — the highest-value test.

**`build._handle_unpatched_pcie_core`:**
- Non-TTY (monkeypatch `sys.stdin.isatty`→False): logs warning, returns normally (build continues).
- TTY + `input`→"n" (monkeypatch both): raises/aborts.
- TTY + `input`→"y": continues.
- `input` raises `EOFError`: degrades to continue (no crash).

**Full suite:** `tests/ --ignore=tests/e2e` stays green (baseline 2559 passed / 40 skipped + new tests).

---

## Risks & mitigations

- **Return-type break for callers:** only `build.py` consumes it; updated here. Grep confirmed no other production callers. Mitigation: change is deliberate and covered by tests.
- **Regex on XML fragility:** mitigated by (a) ET well-formedness validation before trusting the XML path, (b) exact `referenceId` anchoring, (c) real-fixture test. Same justification as the already-reviewed JSON regex on stable, machine-generated files.
- **Interactive prompt hanging CI:** mitigated by the `isatty()` gate (non-TTY never prompts) and EOFError fallback.
- **XML reserialization churn:** avoided entirely — ET is used only to validate; edits remain regex text substitutions.
