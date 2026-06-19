# AGENTS.md — PCILeech Firmware Generator

This file is a concentrated reference for AI coding agents working in the
`PCILeechFWGenerator` repository. It assumes you know nothing about the project
and need to make safe, useful changes quickly.

---

## Project overview

**PCILeechFWGenerator** generates authentic PCIe DMA firmware bitstreams by
cloning the PCIe configuration of a real donor device. The output is a Xilinx
FPGA bitstream for PCILeech-family boards (e.g., PCIeSquirrel, EnigmaX1,
ScreamerM2, ZDMA, CaptainDMA, GBOX, NeTV2, AC701/FT601, Acorn/FT2232H,
SP605/FT601).

- **Author:** Ramsey McGrath <ramsey@voltcyclone.info>
- **License:** MIT
- **Language:** Python 3.11+ (supports 3.11 and 3.12)
- **Operating system:** POSIX Linux only — VFIO is required for Stage 1 data
collection. macOS, Windows, and WSL2 are not supported.
- **Repository:** <https://github.com/voltcyclone/PCILeechFWGenerator>
- **Documentation site:** <https://pcileechfwgenerator.voltcyclone.info>

The tool is deliberately designed around **real donor hardware**. Synthetic donor
profiles or placeholder IDs (`0xDEAD`, `0xBEEF`, `0x1234`, `0xFFFF`, etc.) are
considered regressions, and the codebase actively rejects them.

Donor IDs are validated at multiple gates. Beyond the missing/zero check, builds
also reject **known synthetic placeholder pairs** (`KNOWN_PLACEHOLDER_IDS` in
`src/device_clone/constants.py`, enforced via `is_placeholder_donor_id`) such as
the fabricated Intel I210 default `0x8086:0x1533`, the Realtek `0x10EC:0x8168`
default, and the Xilinx FIFO defaults. A few of these pairs are also real device
IDs; cloning a genuine such device requires setting
`PCILEECH_ALLOW_PLACEHOLDER_IDS=1`, which bypasses **only** the placeholder-pair
check (never the missing/zero check) and must not be used for shippable builds.

---

## Three-stage pipeline

All functionality is organized around a host-container-host pipeline:

1. **Stage 1 — Host collect** (`src/host_collect/`, `src/cli/vfio*.py`)
   Reads the donor device's VID/PID, BARs, capability chain, MSI-X tables, and
   timing behavior from a Linux host via VFIO. Requires root.

2. **Stage 2 — Templating** (`src/templating/`, `src/templates/`)
   Renders Jinja2 templates into SystemVerilog, TCL, XDC, COE, and HEX files
   using the collected donor profile. This can run locally or inside an
   isolated Podman container. The container does **not** access VFIO.

3. **Stage 3 — Vivado build** (`src/vivado_handling/`)
   Runs Xilinx Vivado synthesis to produce a `.bit` bitstream. Requires a
   working Vivado install (WebPACK is sufficient for 7-series; UltraScale needs
   a paid license).

---

## Key configuration files

| File | Purpose |
|------|---------|
| `pyproject.toml` | Project metadata, dependencies, entry points, setuptools-scm, pylint config |
| `setup.cfg` | Legacy flake8 and PyScaffold metadata only |
| `pytest.ini` | pytest test paths, markers, coverage defaults, asyncio mode |
| `tox.ini` | tox environments for test, build, clean, docs |
| `Makefile` | Primary developer interface: install, test, lint, format, build, container, release |
| `.pre-commit-config.yaml` | black, isort, flake8, mypy, bandit, pydocstyle, prettier, custom template validation |
| `Containerfile` | Ubuntu 24.04 multi-stage image for Stage 2 container builds |
| `entrypoint.sh` | Container entrypoint; conditionally skips VFIO ops when running in host-context-only mode |
| `MANIFEST.in` | Curates files bundled into the sdist/wheel, including `lib/voltcyclone-fpga` |
| `cliff.toml` | `git-cliff` configuration; release notes and `CHANGELOG.md` are generated from conventional commits |
| `configs/fallbacks.yaml` | Last-resort defaults for non-sensitive template variables; critical IDs must never have fallbacks |
| `.coveragerc` | Coverage.py source mapping and exclusions |

---

## Technology stack

### Core runtime dependencies

- `psutil>=5.9.0`
- `pydantic>=2.0.0`
- `aiofiles>=23.0.0`
- `jinja2>=3.1.0`
- `PyYAML>=6.0.0`
- `colorlog>=6.7.0`
- `typing_extensions>=4.0.0`

### Optional extras

- `[tui]` — `textual>=4.0.0`, `rich>=13.0.0`, `watchdog>=3.0.0`
- `[testing]` / `[test]` — pytest, pytest-cov, pytest-mock, pytest-xdist, pytest-asyncio, packaging
- `[dev]` — testing + TUI + black, isort, flake8, flake8-docstrings, flake8-import-order, mypy, pre-commit, build, twine, wheel, bandit, safety

### External tooling

- Xilinx Vivado 2022.2+ for Stage 3
- Podman/Docker for the optional Stage 2 container
- `git-cliff` for changelog generation
- `lspci`, `setpci`, kernel VFIO modules for Stage 1

---

## Code organization

The package `pcileechfwgenerator` maps to the `src/` directory via
`pyproject.toml` (`package-dir = {"pcileechfwgenerator" = "src"}`).

```text
src/
├── pcileech_main.py              # Installed-package CLI entry point
├── __init__.py                   # Curated public API
├── __version__.py                # Runtime version resolver (setuptools-scm)
├── pcileech.py (root)            # Unified source-checkout entry point
├── build.py                      # FirmwareBuilder and BuildConfiguration
├── cli/                          # Argument parsing, VFIO binder, diagnostics, container helpers, flash
├── host_collect/                 # Stage 1: VFIO-based donor extraction
├── device_clone/                 # Donor profile parsing, BAR sizing, MSI-X, config space, board config
├── pci_capability/               # PCIe capability list construction and analyzers
├── templating/                   # Stage 2: Jinja2 renderer, TCL builder, SV generators, context validator
├── templates/                    # Jinja2 sources (sv/*.j2, python/*.j2, _helpers.j2)
├── vivado_handling/              # Stage 3: Vivado runner, error reporter, IP patchers
├── file_management/              # Board discovery, datastore, donor dumps, option ROMs
├── behavioral/                   # Behavioral profilers for network, storage, media, USB devices
├── tui/                          # Textual-based interactive UI
│   ├── commands/                 # TUI command layer
│   ├── core/                     # Orchestrators, state managers, device/build operations
│   ├── dialogs/                  # Modal dialogs
│   ├── models/                   # Pydantic/dataclass models
│   ├── plugins/                  # Plugin system
│   ├── styles/                   # Textual CSS
│   ├── utils/                    # UI helpers
│   └── widgets/                  # Custom widgets
├── utils/                        # Logging, validation, unified context, post-build checks
├── donor_dump/                   # Kernel-module donor-dump tooling (C + Makefile)
└── scripts/                      # Driver scraping, kernel utilities, state-machine extraction
```

### Git submodules

- `lib/voltcyclone-fpga` — Board definitions, IP, constraints, synthesis templates
- `site` — Hosted documentation source
- `wiki` — GitHub wiki content

For local development, initialize submodules:

```bash
git submodule update --init --recursive
```

The container clones `voltcyclone-fpga` during the image build, so submodule
initialization is not needed for container usage.

---

## Entry points

Installed console scripts (all dispatch to the same main function):

- `pcileech`
- `pcileech-generate`
- `pcileech-build`
- `pcileech-tui`

Source-checkout entry point:

```bash
python3 pcileech.py <command>
```

Common commands:

```bash
pcileech version
pcileech tui
pcileech build --bdf 0000:03:00.0 --board pcileech_35t325_x1
pcileech build --bdf 0000:03:00.0 --board pcileech_35t325_x1 --local
pcileech build --bdf 0000:03:00.0 --board pcileech_35t325_x1 \
    --vivado-path /tools/Xilinx/2025.1/Vivado --vivado-jobs 8
pcileech check --device 0000:03:00.0
pcileech flash pcileech_datastore/output/*.bit --board pcileech_35t325_x1
```

VFIO operations require root; use the venv Python with `sudo -E`:

```bash
sudo -E ~/.pcileech-venv/bin/python3 -m pcileechfwgenerator.pcileech_main tui
```

---

## Build and test commands

### Development setup

```bash
python3 -m venv .venv
source .venv/bin/activate
pip install -e ".[dev]"
pre-commit install
pre-commit install --hook-type commit-msg
```

### Running tests

```bash
# Full suite (with coverage)
make test

# Unit tests only, excluding hardware/TUI
make test-unit

# TUI integration tests
make test-tui

# Fast tests only
make test-fast

# Direct pytest examples
pytest tests/ -x -q
pytest tests/ -n auto
pytest tests/ -m "not slow and not hardware"
pytest tests/e2e/ -m "e2e and not slow"
```

pytest markers defined in `pytest.ini`:

- `unit`
- `integration`
- `e2e`
- `tui`
- `performance`
- `hardware` (skipped in CI)
- `slow`
- `requires_container_runtime`
- `requires_build_isolation`

Coverage is configured with `--cov=src`, `--cov-fail-under=10`, HTML output in
`htmlcov/`, and XML output in `coverage.xml`.

### Code quality

```bash
make lint      # flake8 + mypy
make format    # black + isort
make security  # bandit + safety
```

### Template validation

```bash
make check-templates              # non-blocking summary
make check-templates-strict       # fail on issues
make check-templates-fix          # generate suggested fixes
make check-templates-errors       # warnings as errors
python scripts/validate_template_variables.py --format summary
```

### SystemVerilog lint

```bash
make sv-lint
```

### Building and packaging

```bash
make build              # python -m build (sdist + wheel)
make build-pypi         # full PyPI package generation with VFIO constants
make build-quick        # quick build skipping quality checks
make test-build         # test package build in a clean venv
make container          # build Podman image
```

### Release

```bash
make release VERSION=0.14.16
```

This regenerates `CHANGELOG.md` with `git-cliff`, commits it if changed, tags
`v0.14.16`, and pushes. The `release.yml` GitHub Actions workflow then builds,
signs, generates release notes, and publishes to PyPI/TestPyPI.

Versioning is driven entirely by `setuptools-scm` from git tags; do **not** edit
`src/_version.py` (it is generated and gitignored).

---

## Code style guidelines

- **Formatter:** Black with `--line-length=88`.
- **Import sorting:** isort with `--profile=black --line-length=88`.
- **Linter:** flake8 with `max-line-length=88` and `extend_ignore = E203, W503`
  (Black-compatible).
- **Type checking:** mypy (`--ignore-missing-imports` in pre-commit; full mypy
  in `make lint`).
- **Docstrings:** Google style for modules, classes, and public functions.
- **Logging:** Use `logger = get_logger(__name__)` from `src/log_config`.
- **Commit messages:** Conventional commits (`feat:`, `fix:`, `docs:`,
  `refactor:`, `test:`, `chore:`). The changelog is generated from these.
- **Type hints:** Required for all public functions.

### Project-specific hard rules

1. **No placeholder donor IDs** in templates or template-rendering code. Real
   device identifiers must propagate from the collected donor profile.
2. **No `shell=True` in subprocess calls.** Always use argv-list form; Bandit
   will flag regressions.
3. **Use `log_*_safe` helpers** from `src/string_utils` for any log line that
   interpolates donor data, to avoid leaking identifiers into shared logs.
4. **Jinja2 SystemVerilog templates:** mind quote/backslash escaping inside SV
   string literals; always end `case` blocks with `default:`; declare `genvar`
   outside generate blocks.
5. **Pydantic models** are preferred for structured donor data; search
   `src/device_clone/` and `src/pci_capability/` before adding new dict-shaped
   values.

---

## Testing instructions

- Tests live in `tests/` and use pytest.
- There are ~173 `test_*.py` files across unit, integration, e2e, and TUI
  categories.
- `conftest.py` provides shared fixtures.
- `tests/mock_sysfs/` contains sysfs fixtures for hardware-free testing.
- Slow and hardware-dependent tests are marked and skipped by default in CI.
- E2E tests run in a separate GitHub Actions workflow
  (`.github/workflows/e2e-testing.yml`) covering fast e2e, build-isolation e2e,
  and container-build e2e.

When adding new functionality, add tests and run the relevant subset before
committing:

```bash
pytest tests/test_your_module.py -x
make test-unit
```

---

## CI/CD and deployment

### Workflows

- **`.github/workflows/consolidated-ci.yml`** — Template validation, CodeQL
  security analysis, unit tests (Python 3.11/3.12), coverage upload to Codecov,
  and packaging.
- **`.github/workflows/security.yml`** — Dependency review, Bandit, Safety,
  Semgrep, and SARIF uploads.
- **`.github/workflows/e2e-testing.yml`** — Fast e2e, build-isolation e2e, and
  container-build e2e.
- **`.github/workflows/release.yml`** — Triggered on `v*` tags; builds wheel/sdist,
  attests provenance, generates release notes from `cliff.toml`, and publishes
  to PyPI (stable) or TestPyPI (alpha/beta/rc).

### Versioning and release

- `setuptools-scm` derives the version from git tags using
  `version_scheme = "no-guess-dev"`.
- `git-cliff` generates `CHANGELOG.md` and release notes from conventional
  commits.
- Pre-releases containing `rc`, `beta`, or `alpha` go to TestPyPI; stable tags
  go to production PyPI.

---

## Security considerations

- The tool is intended for educational research and legitimate PCIe development
  only. Generated firmware contains real device identifiers and should be kept
  private.
- Never build firmware on production or sensitive systems; use isolated
  hardware.
- CI runs SAST and dependency scanning: Bandit, Safety, Semgrep, CodeQL.
- Do not introduce `shell=True`, hard-coded credentials, or synthetic donor
  data.
- Report security issues through GitHub Security Advisories, not public issues.
- See `SECURITY.md` and the legal notice in `README.md`.

---

## Agent-specific notes

### Files you must not hand-edit

- `src/_version.py` — generated by `setuptools-scm`.
- `lib/voltcyclone-fpga/` — upstream git submodule; changes must land in
  `VoltCyclone/voltcyclone-fpga` first, then the submodule pointer is bumped.
- `CHANGELOG.md` — generated by `git-cliff`.

### Common mistakes to avoid

- Forgetting `--recurse-submodules` when cloning, which causes "no boards
  available" errors.
- Running `sudo pcileech` without preserving the virtualenv path; use
  `sudo -E ~/.pcileech-venv/bin/python3 -m pcileechfwgenerator.pcileech_main`.
- Installing into the system Python on modern Linux; always use a venv.
- Adding fallbacks in `configs/fallbacks.yaml` for device IDs or timing values.

### Useful project tools

- `scripts/validate_template_variables.py` — template variable validation
- `scripts/check_templates.sh` — convenient template checking
- `scripts/analyze_imports.py` — import analysis
- `scripts/generate_api_docs.py` — documentation generation
- `scripts/iommu_viewer.py` — lightweight IOMMU group viewer for VFIO debugging
- `scripts/barviz.py` — BAR visualization
- `scripts/lint_sv_block_decls.py` — SystemVerilog declaration-order linter
- `.claude/skills/vivado-log-analyzer/` — analyze failed Vivado runs
- `.claude/skills/new-board-target/` — guide for adding a new FPGA board
- `.claude/agents/hardware-safety-reviewer.md` — domain-specific review for
  PCIe/template/Vivado changes

---

## Quick reference

```bash
# Install dev environment
python3 -m venv .venv && source .venv/bin/activate
pip install -e ".[dev]"
pre-commit install && pre-commit install --hook-type commit-msg

# Validate and test
make check-templates-strict
make test-unit
make lint

# Build
make build-pypi

# Release
make release VERSION=X.Y.Z
```
