---
name: vivado-log-analyzer
description: Use when a Vivado synthesis or implementation run fails, or when the user asks "why did the build fail / what's wrong with this vivado log". Surfaces only the actionable error/critical-warning lines from large Vivado outputs (vivado.log, synth_1/runme.log, impl_1/runme.log, *.rpt) so the diagnosis fits in context. Trigger when the user shares a Vivado log path, mentions "synthesis failed", "implementation failed", "timing failure", "BRAM exhausted", or pastes raw Vivado output.
---

# Vivado Log Analyzer

## When to invoke

- A Vivado build (synth / impl / bitstream) failed and the user wants the cause.
- The user pastes or points at one of: `vivado.log`, `*.runs/synth_1/runme.log`,
  `*.runs/impl_1/runme.log`, `*_timing_summary.rpt`,
  `*_utilization_*.rpt`, `*_drc_*.rpt`.
- Auto-trigger when a tool result contains `ERROR: [Synth`, `CRITICAL WARNING: [Place`,
  `Timing constraint not met`, or similar Vivado markers.

## What it does

Vivado logs are usually 5–50 MB and 95% noise. This skill runs
`scripts/analyze.py <log-path>` which extracts only:

- `ERROR:` and `CRITICAL WARNING:` lines (Vivado's "this is why it failed").
- Timing summary endpoints with negative slack.
- Resource utilization rows above 95% (BRAM, LUT, DSP, URAM exhaustion).
- DRC violations (UCIO-1, LUTLP-1, NSTD-1, etc.).
- IP licensing failures (`ERROR: [Common 17-69]`).
- Missing constraint file errors.

It groups by failure category and prints **at most ~80 lines** so you can hand
the result to the user or reason about it without burning context.

## How to use

```bash
python3 .claude/skills/vivado-log-analyzer/scripts/analyze.py <path-to-log>
```

`<path-to-log>` can be any of:

- A single `.log` or `.rpt` file
- A Vivado project's `*.runs/` directory (script will walk it)
- A build output directory (`output/build/...`)

If the user hasn't given a path, look first in `output/`, then in
`build/`, then in any `*.runs/synth_1/` or `*.runs/impl_1/` directory.

## After analysis

1. Print the categorized summary the script produced.
2. **Do not** re-read the raw log unless the summary is empty or the user asks
   for a specific line context. Re-reading defeats the purpose of the skill.
3. If timing is the failure category, point the user at
   `src/templating/timing_constraints/` and any board-specific `.xdc` files.
4. If resource exhaustion is the failure category, the fix is almost always in
   `src/templates/sv/` (template bloat) or in board selection
   (`src/file_management/board_discovery.py` — wrong FPGA part).
5. If IP licensing or "missing IP" errors appear, check the Containerfile and
   confirm the build is using a licensed Vivado image.

## Anti-patterns

- Do not paste the raw Vivado log back to the user. They already have it.
- Do not run this on logs smaller than ~500 lines — just read them directly.
- Do not try to "fix" timing failures by widening constraints without
  understanding the constraint origin (`src/templating/timing_constraints/`
  generates them; constraints are tied to donor profile).
