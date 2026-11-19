---
name: new-board-target
description: Use when the user wants to add support for a new FPGA board / dev kit target to PCILeechFWGenerator (e.g. "add support for board X", "register new board", "I want to target FPGA part Y"). Walks through the exact file touches needed, validation steps, and pitfalls specific to this codebase. Do NOT use for fixing build failures on an already-supported board, that's the vivado-log-analyzer skill.
disable-model-invocation: true
---

# Add a New Board Target

This is the canonical workflow for adding a new FPGA board to PCILeechFWGenerator. Follow it top-to-bottom, skipping steps almost always causes confusing synthesis failures down the line.

## 0. Confirm the upstream board exists

Boards are auto-discovered from the `voltcyclone-fpga` submodule
(`lib/voltcyclone-fpga/`). If the board directory doesn't exist there, you
must add it **upstream first**:

```bash
ls lib/voltcyclone-fpga/
# look for a directory matching the board (e.g. EnigmaX1, PCIeSquirrel, ZDMA)
```

If absent, open a PR against `VoltCyclone/voltcyclone-fpga` to add the board.
Do not hand-edit files under `lib/voltcyclone-fpga/` in this repo, that
directory is a git submodule and edits will not persist. (The PreToolUse hook
in `.claude/settings.json` will block such edits.)

## 1. Register the board in `BOARD_CONFIGS`

File: `src/file_management/board_discovery.py`

Add an entry under `BoardDiscovery.BOARD_CONFIGS` matching the upstream
directory name and FPGA part. Use existing entries (e.g. `pcileech_enigma_x1`,
`pcileech_75t484_x1`) as the template.

Key fields:
- `dir`: must match the directory name under `lib/voltcyclone-fpga/`.
- `fpga_part`: full Xilinx part (e.g. `xc7a75tfgg484-2`). Match the casing and
  speed-grade suffix used elsewhere, wrong speed grades pass synthesis but
  fail timing in mysterious ways.
- `max_lanes`: PCIe lane count the board exposes (1, 4, 8, 16).

If the board has a colloquial name (e.g. `75t`, `100t`), add a **legacy alias**
entry that points to the same directory, this keeps existing CLI invocations
working.

## 2. Verify discovery picks it up

```bash
.venv/bin/python -c "from pcileechfwgenerator.file_management.board_discovery import discover_all_boards; \
    import json; print(json.dumps(list(discover_all_boards().keys()), indent=2))"
```

The new board name should appear. If it doesn't, the discovery walker isn't
finding it, usually because `dir` doesn't match the upstream directory
exactly (case-sensitive).

## 3. Check for board-specific RTL / constraints

```bash
ls src/templates/sv/ | grep -i <board-family>     # e.g. 7series, ultrascale
ls lib/voltcyclone-fpga/<board-dir>/              # board sources and constraints
```

If the board uses a new FPGA family (e.g. first UltraScale+ target), you may
need to extend `FPGA_FAMILY_PATTERNS` in `src/device_clone/board_config.py`.
Existing patterns: `7series` (xc7a/k/v/z), `ultrascale`, `ultrascale_plus`.

## 4. Add tests

Relevant test files:

- `tests/test_board_part_regression.py`: FPGA part per board.
- `tests/test_board_source_generation.py`: board source staging.
- `tests/test_pcie_board_contract.py`: PCIe board contract.

Add the new board to the parametrized cases. Run:

```bash
.venv/bin/python -m pytest tests/test_board_part_regression.py tests/test_board_source_generation.py tests/test_pcie_board_contract.py -x -q
```

## 5. End-to-end smoke test

Render artifacts for the new board from a validated capture of a real donor:

```bash
.venv/bin/python pcileech.py build \
    --board <new-board-name> \
    --from-capture <capture-dir> \
    --templates-only --local --accept-offline-risk
```

`--templates-only` produces the Stage 2 outputs without invoking Vivado. If
this succeeds, you have at minimum a valid template materialization for the
board. Stage 3 runs `build_all.tcl` and its gates (routed timing, clock
coverage, DRC, CDC) on the host.

## 6. Update docs

- Mention the board in the README if it appears in the board examples.
- Do not edit `CHANGELOG.md`. `git-cliff` generates it from commit messages.
- Do NOT add the board to `AUTHORS` or any other contributor-style file.

## Common pitfalls

| Symptom | Likely cause |
|---|---|
| Board not found at runtime | `dir` in `BOARD_CONFIGS` doesn't match upstream directory name (case-sensitive). |
| Synthesis fails with "part not supported" | `fpga_part` typo or wrong speed grade. |
| Timing fails immediately on impl | New FPGA family without a matching entry in `FPGA_FAMILY_PATTERNS`. |
| Donor ID propagation tests fail | New board needs a default in donor-profile fallback (`configs/fallbacks.yaml`). |
| Build worked locally but fails in CI | Submodule pin in `.gitmodules` is older than your local checkout, bump the submodule SHA. |

## When NOT to use this skill

- Fixing an existing board that suddenly broke → use `vivado-log-analyzer`.
- Just changing FPGA part of an existing board → edit `BOARD_CONFIGS` directly,
  no need for the whole flow.
- Renaming a board → that's a breaking change; coordinate with a deprecation
  period, don't follow this skill.
