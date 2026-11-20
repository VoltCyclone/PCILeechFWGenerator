# Contributing to PCILeech Firmware Generator

[![PyPI version](https://badge.fury.io/py/pcileechfwgenerator.svg)](https://badge.fury.io/py/pcileechfwgenerator)
[![Python Support](https://img.shields.io/pypi/pyversions/pcileechfwgenerator.svg)](https://pypi.org/project/pcileechfwgenerator/)
[![License: MIT](https://img.shields.io/badge/License-MIT-yellow.svg)](https://opensource.org/licenses/MIT)

This document covers how to report issues, set up a development environment, and submit changes. Setup details and make targets are in [DEVELOPMENT.md](DEVELOPMENT.md).

## Code of Conduct

Participation is governed by the [Code of Conduct](CODE_OF_CONDUCT.md).

## Getting Started

### Prerequisites

- Python 3.11 or newer
- Git
- Linux (required for VFIO and PCIe operations)
- Podman or Docker (optional, for container Stage 2)
- Vivado (optional, for Stage 3)

### Setup

```bash
git clone --recurse-submodules https://github.com/<you>/PCILeechFWGenerator.git
cd PCILeechFWGenerator
python3 -m venv .venv && source .venv/bin/activate
pip install -e ".[dev]"
pre-commit install
pre-commit install --hook-type commit-msg
python -m pytest -x -q
```

## Reporting Issues

Use the issue templates. Run `help_ticket.sh` and include its output in bug reports.

- **Bug reports:** environment, exact command, full error output, expected versus actual behavior, and the hardware involved.
- **Feature requests:** the use case, the proposed solution, and alternatives considered.
- **Questions and open-ended ideas:** ask in the [support Discord](https://discord.gg/dwQfMNsb7W).
- **Security issues:** follow [SECURITY.md](SECURITY.md). Never open a public issue.

Do not include UUIDs, device serials, or donor dumps in public reports.

## Development Workflow

### Branches

- `main`: the stable branch, and the base for all pull requests.
- `feature/<name>`, `fix/<name>`, `docs/<name>`: working branches.

### Making Changes

1. Create a branch from `main`.
2. Make the change and add tests for new behavior.
3. Run the relevant tests, then the full suite:

   ```bash
   python -m pytest tests/test_specific_module.py
   python -m pytest -x -q
   python -m pytest -n auto
   python -m pytest -m "not slow and not hardware"
   ```

4. Commit with a conventional message.

### Commit Messages

Commits follow the conventional format. `git-cliff` builds `CHANGELOG.md` from them, so do not edit the changelog by hand.

```
<type>(<scope>): <description>

[optional body]
```

Types: `feat`, `fix`, `docs`, `style`, `refactor`, `test`, `chore`.

```
feat(tui): add real-time build progress monitoring
fix(build): resolve SystemVerilog generation error for network devices
docs(readme): update installation instructions for TUI
```

## Testing

Tests live in `tests/` and use pytest. Shared fixtures are in `tests/conftest.py`, end-to-end tests in `tests/e2e/`, and sysfs fixtures in `tests/mock_sysfs/`.

- Test individual functions and complete workflows.
- Mock external dependencies with `pytest-mock`.
- Cover error conditions and boundary cases.
- Do not use placeholder donor IDs such as `0xDEAD` or `0xBEEF` in generation paths.

## Code Style

- Black with line length 88, and isort with the Black profile.
- flake8 and mypy must pass (`make lint`).
- Type hints on all public functions.
- Google-style docstrings.
- `logger = get_logger(__name__)` from `src/log_config`, and the `log_*_safe` helpers for any log line that includes donor data.
- No `shell=True` in subprocess calls.

```bash
make format
make lint
```

Pre-commit runs Black, isort, flake8, mypy, Bandit, pydocstyle, Prettier, the conventional-commit check, and the template, safe-logging, and test-import hooks.

## Submitting Changes

1. Update documentation affected by the change.
2. Add tests.
3. Run the full test suite.
4. Open a pull request using the template in `.github/PULL_REQUEST_TEMPLATE.md`. Flag changes that touch firmware generation, VFIO, or the build pipeline in its risk section.

CI runs tests, lint, and security scans. A maintainer reviews the change, and at least one approval is required before merge.

## Releases

Versions follow semantic versioning and come from git tags through `setuptools-scm`. There is no version file to edit.

```bash
make release VERSION=X.Y.Z
```

The target regenerates `CHANGELOG.md` with `git-cliff`, commits it, tags `vX.Y.Z`, and pushes. The `release.yml` workflow then builds the sdist and wheel, attests provenance, generates release notes from `cliff.toml`, and publishes to PyPI. Tags containing `alpha`, `beta`, or `rc` go to TestPyPI.

## Getting Help

- [GitHub Issues](https://github.com/voltcyclone/PCILeechFWGenerator/issues) for bugs and concrete proposals
- [Discord](https://discord.gg/dwQfMNsb7W) for questions
- `docs/development/` for design notes, starting with [MSIX_STATUS.md](docs/development/MSIX_STATUS.md)

## Disclaimer

This tool is intended for educational research and legitimate PCIe development only. Users are responsible for complying with all applicable laws. The authors assume no liability for misuse of this software.
