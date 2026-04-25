# Makefile for PCILeech Firmware Generator

.PHONY: help clean install install-dev test lint format build build-pypi upload-test upload-pypi release show-version changelog container container-rebuild docker-build build-container vfio-constants vfio-constants-clean check-templates check-templates-strict check-templates-fix check-templates-errors sv-lint

# Default target
help:
	@echo "PCILeech Firmware Generator - Available targets:"
	@echo ""
	@echo "Development:"
	@echo "  install      - Install package in development mode"
	@echo "  install-dev  - Install development dependencies"
	@echo "  test         - Run test suite"
	@echo "  test-tui     - Run TUI integration tests only"
	@echo "  test-unit    - Run unit tests only (no hardware/TUI)"
	@echo "  test-all     - Run all tests with coverage"
	@echo "  test-fast    - Run fast tests only"
	@echo "  check-templates - Validate template variables and syntax"
	@echo "  check-templates-strict - Validate templates with strict mode"
	@echo "  check-templates-errors - Treat template warnings as errors"
	@echo "  sv-lint      - Lint SystemVerilog templates for decl-after-stmt issues"
	@echo "  lint         - Run code linting"
	@echo "  format       - Format code with black and isort"
	@echo "  clean        - Clean build artifacts"
	@echo ""
	@echo "Building:"
	@echo "  build        - Build package distributions"
	@echo "  build-pypi   - Full PyPI package generation (recommended)"
	@echo "  build-quick  - Quick build without quality checks"
	@echo ""
	@echo "Publishing:"
	@echo "  upload-test  - Upload to Test PyPI"
	@echo "  upload-pypi  - Upload to PyPI"
	@echo "  release      - Full release process"
	@echo ""
	@echo "Container:"
	@echo "  container         - Build container image (pcileechfwgenerator:latest)"
	@echo "  container-rebuild - Force rebuild container (alias for container)"
	@echo "  docker-build      - Build container image (alias for container)"
	@echo ""
	@echo "Utilities:"
	@echo "  check-deps      - Check system dependencies"
	@echo "  security        - Run security scans"
	@echo "  vfio-constants  - Build and patch VFIO ioctl constants"
	@echo "  vfio-constants-clean - Clean VFIO build artifacts"
	@echo "  bar-viz         - Show BAR visualization tool usage"
	@echo ""
	@echo "Version Management:"
	@echo "  show-version                  - Show the version setuptools-scm currently resolves"
	@echo "  changelog                     - Regenerate CHANGELOG.md from git history (git-cliff)"
	@echo "  release VERSION=X.Y.Z         - Regenerate changelog, commit, tag v<VERSION>, push"
	@echo ""
	@echo "  Versioning is driven by git tags via setuptools-scm. The changelog"
	@echo "  is generated from conventional-commit messages by git-cliff. To cut"
	@echo "  a release, run 'make release VERSION=X.Y.Z' which tags the repo;"
	@echo "  release.yml takes over from there."

# Development targets
install:
	python3 -m pip install -e .

install-dev:
	python3 -m pip install -e ".[dev,test,tui]"

test:
	@find . -type d -name __pycache__ -exec rm -rf {} + 2>/dev/null || true
	@find . -type f -name "*.pyc" -delete 2>/dev/null || true
	pytest tests/ --cov=src --cov-report=term-missing --cache-clear

test-tui:
	pytest tests/test_tui_integration.py -v -m tui

test-unit:
	pytest tests/ -k "not tui" -m "not hardware" --cov=src --cov-report=term-missing

test-all:
	pytest tests/ -v --cov=src --cov-report=term-missing

test-fast:
	pytest tests/ -x -q -m "not slow and not hardware"

# Template validation targets
check-templates:
	@echo "Validating template variables and syntax..."
	./scripts/check_templates.sh

check-templates-strict:
	@echo "Validating templates with strict mode..."
	./scripts/check_templates.sh --strict

check-templates-fix:
	@echo "Validating templates and generating fixes..."
	./scripts/check_templates.sh --fix

check-templates-errors:
	@echo "Validating templates with warnings as errors..."
	./scripts/check_templates.sh --warnings-as-errors

# SystemVerilog linter target
sv-lint:
	@echo "Running SystemVerilog declaration-order linter..."
	python3 scripts/lint_sv_block_decls.py --strict

# BAR visualization tool
bar-viz:
	@echo "BAR Visualization Tool"
	@echo "Usage: python3 scripts/barviz.py -f <file> [options]"
	@echo ""
	@echo "Example: python3 scripts/barviz.py -f bar0.bin -m entropy"
	@echo "See scripts/barviz.py --help for more options"

lint:
	flake8 src/ tests/
	mypy src/

format:
	black src/ tests/
	isort src/ tests/

clean:
	rm -rf build/ dist/ *.egg-info/ .pytest_cache/ htmlcov/ .coverage
	find . -type d -name __pycache__ -exec rm -rf {} + 2>/dev/null || true
	find . -type f -name "*.pyc" -delete 2>/dev/null || true

# Building targets
build:
	python3 -m build

build-pypi:
	@echo "Running full PyPI package generation..."
	python3 scripts/generate_pypi_package.py --skip-upload

build-quick:
	@echo "Running quick PyPI package generation..."
	python3 scripts/generate_pypi_package.py --quick --skip-upload

# Publishing targets
upload-test:
	@echo "Building and uploading to Test PyPI..."
	python3 scripts/generate_pypi_package.py --test-pypi

upload-pypi:
	@echo "Building and uploading to PyPI..."
	python3 scripts/generate_pypi_package.py

# Cut a release: regenerate the changelog (git-cliff), commit, tag, push.
# setuptools-scm reads the tag at build time and the release.yml workflow
# publishes the wheel. Usage: make release VERSION=0.14.16
#
# Safety:
#  - Refuses to run with a dirty working tree (so the changelog commit
#    can't accidentally pick up unrelated edits).
#  - Tolerates an empty changelog diff (no new commits since last tag).
release:
	@if [ -z "$(VERSION)" ]; then \
		echo "Usage: make release VERSION=X.Y.Z"; exit 1; \
	fi
	@command -v git-cliff >/dev/null 2>&1 || { \
		echo "git-cliff is required: brew install git-cliff (or see https://git-cliff.org/docs/installation)"; \
		exit 1; \
	}
	@if [ -n "$$(git status --porcelain)" ]; then \
		echo "Working tree is not clean. Commit or stash changes first."; \
		git status --short; \
		exit 1; \
	fi
	git-cliff --tag v$(VERSION) -o CHANGELOG.md
	@if git diff --quiet -- CHANGELOG.md; then \
		echo "CHANGELOG.md unchanged — tagging v$(VERSION) directly"; \
	else \
		git add CHANGELOG.md && \
		git commit -m "docs(changelog): release v$(VERSION)"; \
	fi
	git tag v$(VERSION)
	git push origin HEAD --tags

# Regenerate CHANGELOG.md from the full git history.
changelog:
	@command -v git-cliff >/dev/null 2>&1 || { \
		echo "git-cliff is required: brew install git-cliff"; exit 1; \
	}
	git-cliff -o CHANGELOG.md

# Show the version setuptools-scm currently resolves to.
show-version:
	@python3 -m setuptools_scm

# Utility targets
check-deps:
	@echo "Checking system dependencies..."
	@python3 scripts/generate_pypi_package.py --skip-quality --skip-security --skip-upload --skip-install-test || true

security:
	@echo "Running security scans..."
	bandit -r src/
	safety check


# Container targets. The container image bundles VFIO constants, so
# `vfio-constants` is a prerequisite. ``container-rebuild``,
# ``docker-build``, and ``build-container`` are all aliases.
container: vfio-constants
	./scripts/build_container.sh

container-rebuild: container

docker-build: container

build-container: container

# Test package build
test-build:
	@echo "Testing PyPI package build..."
	python3 scripts/test_package_build.py

# Help for specific targets
help-build:
	@echo "Build targets:"
	@echo ""
	@echo "  build        - Basic build using python -m build"
	@echo "  build-pypi   - Full PyPI generation with all checks"
	@echo "  build-quick  - Quick build skipping quality checks"
	@echo ""
	@echo "Options for build-pypi:"
	@echo "  - Runs code quality checks (black, isort, flake8, mypy)"
	@echo "  - Runs security scans (bandit, safety)"
	@echo "  - Runs test suite with coverage"
	@echo "  - Validates package structure"
	@echo "  - Tests installation in virtual environment"
	@echo ""
	@echo "Use 'make build-quick' for faster iteration during development"

help-upload:
	@echo "Upload targets:"
	@echo ""
	@echo "  upload-test  - Upload to Test PyPI (https://test.pypi.org/)"
	@echo "  upload-pypi  - Upload to production PyPI (https://pypi.org/)"
	@echo ""
	@echo "Prerequisites:"
	@echo "  - Configure ~/.pypirc with your API tokens"
	@echo "  - Or set TWINE_USERNAME and TWINE_PASSWORD environment variables"
	@echo ""
	@echo "Test PyPI installation:"
	@echo "  pip install --index-url https://test.pypi.org/simple/ pcileechfwgenerator"
	@echo ""
	@echo "Production PyPI installation:"
	@echo "  pip install pcileechfwgenerator"

# VFIO Constants targets
vfio-constants:
	@echo "Building VFIO constants..."
	./build_vfio_constants.sh

vfio-constants-clean:
	@echo "Cleaning VFIO build artifacts..."
	rm -f vfio_helper vfio_helper.exe
	@echo "VFIO build artifacts cleaned"

# Integration target: build VFIO constants before the PyPI build.
# (The container target's vfio-constants prerequisite is declared at
# the canonical definition above.)
build-pypi: vfio-constants
	@echo "Running full PyPI package generation with VFIO constants..."
	python3 scripts/generate_pypi_package.py --skip-upload