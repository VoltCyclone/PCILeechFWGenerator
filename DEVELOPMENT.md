# Development Guide

This guide covers development setup, testing, and template work for the PCILeech Firmware Generator. Contribution rules are in [CONTRIBUTING.md](CONTRIBUTING.md).

## Setup

### Local Development

Local development needs the `voltcyclone-fpga` submodule at `lib/voltcyclone-fpga`.

```bash
git clone --recurse-submodules https://github.com/voltcyclone/PCILeechFWGenerator.git
cd PCILeechFWGenerator
python3 -m venv .venv && source .venv/bin/activate
pip install -e ".[dev]"
pre-commit install
pre-commit install --hook-type commit-msg
```

If you cloned without submodules, run:

```bash
git submodule update --init --recursive
```

### Container Development

The container clones `voltcyclone-fpga` during the image build, so no submodule is needed.

```bash
git clone https://github.com/voltcyclone/PCILeechFWGenerator.git
cd PCILeechFWGenerator
podman build -t pcileechfwgenerator:latest .
```

See [Container Builds](site/docs/container-builds.md) for details.

## Workflow

### Tests

```bash
make test        # full suite with coverage
make test-unit   # unit tests only
make test-tui    # TUI tests
make test-fast   # fast tests only
```

The suite holds about 4,400 tests. The `hardware` marker is skipped in CI.

### Code Quality

```bash
make lint        # flake8 + mypy
make format      # black + isort
make security    # bandit + dependency scan
make sv-lint     # SystemVerilog declaration-order lint
```

### Building

```bash
make build        # sdist + wheel
make build-pypi   # full PyPI package build
make build-quick  # skip quality checks
```

Run `make help` for every target.

## Templates

Every Jinja2 template variable must be defined. Variable requirements live in `TemplateContextValidator` (`src/templating/template_context_validator.py`).

### Validation

```bash
make check-templates          # non-blocking
make check-templates-strict   # fail on issues
make check-templates-fix      # suggest fixes

python scripts/validate_template_variables.py --strict --format json
python scripts/validate_template_variables.py --generate-fixes --verbose
python scripts/validate_template_variables.py --format summary
```

### Adding a Template

1. Define its variable requirements in `src/templating/template_context_validator.py`.
2. Render it with a real context.
3. Run `make check-templates-strict` before committing.

### Patterns

```jinja2
{# Good: use variables with proper defaults #}
{% if enable_feature %}
    // Feature enabled
{% endif %}

{# Avoid: conditional existence checks #}
{% if enable_feature is defined %}
    // This pattern should be avoided
{% endif %}
```

## CI

- `.github/workflows/consolidated-ci.yml`: template validation, CodeQL, unit tests (Python 3.11 and 3.12), coverage, packaging.
- `.github/workflows/security.yml`: dependency review, Bandit, pip-audit, Semgrep.
- `.github/workflows/e2e-testing.yml`: fast, build-isolation, and container-build end-to-end tests.
- `.github/workflows/release.yml`: builds and publishes on `v*` tags.

Pre-commit runs Black, isort, flake8, mypy, Bandit, pydocstyle, Prettier, the conventional-commit check, and the custom template-validation, safe-logging, and test-import hooks.

## Pull Requests

1. Branch from `main`.
2. Follow the code style and use conventional commits.
3. Run `make check-templates-strict` and `make test`.
4. Push and open a pull request.

For template changes, update variable requirements, test rendering with real data, and document new variables.

## Debugging

Check the syntax of a single template:

```bash
python -c "
from jinja2 import Environment, FileSystemLoader
env = Environment(loader=FileSystemLoader('src/templates'))
template = env.get_template('your_template.j2')
print('Template syntax is valid')
"
```

Other helpers:

```bash
make check-deps     # check system dependencies
make install-dev    # install development dependencies
make clean          # remove build artifacts
```

## Scripts

- `scripts/validate_template_variables.py`: template validation
- `scripts/check_templates.sh`: wrapper for template checks
- `scripts/analyze_imports.py`: import analysis
- `scripts/generate_api_docs.py`: API documentation
- `scripts/iommu_viewer.py`: IOMMU group viewer for VFIO debugging
- `scripts/lint_sv_block_decls.py`: SystemVerilog declaration-order linter
- `scripts/barviz.py`: BAR visualization

MSI-X and requester tracking are described in [docs/development/MSIX_STATUS.md](docs/development/MSIX_STATUS.md).
