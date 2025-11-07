# Development Guide

This guide covers development setup, testing, and contributing to the PCILeech Firmware Generator.

## Development Setup

### 1. Clone and Setup

**For Local Development** (requires voltcyclone-fpga submodule):

```bash
git clone --recurse-submodules https://github.com/voltcyclone/PCILeechFWGenerator.git
cd PCILeechFWGenerator
python3 -m venv .venv && source .venv/bin/activate
pip install -r requirements-dev.txt
pip install -e .
```

**For Container Development** (no submodule needed):

```bash
# Clone without submodules
git clone https://github.com/voltcyclone/PCILeechFWGenerator.git
cd PCILeechFWGenerator

# Build container (automatically clones voltcyclone-fpga)
podman build -t dma-fw .
```

**Note for local development:** The `--recurse-submodules` flag is required to initialize the `voltcyclone-fpga` submodule at `lib/voltcyclone-fpga`.

If you already cloned without submodules, initialize them with:

```bash
git submodule update --init --recursive
```

**Note for container builds:** The container automatically clones the `voltcyclone-fpga` repository during the build process, eliminating the need for git submodule management. See [Container Builds](site/docs/container-builds.md) for details.

### 2. Install Pre-commit Hooks

```bash
pip install pre-commit
pre-commit install
pre-commit install --hook-type commit-msg
```

## Development Workflow

### Running Tests

```bash
# Run all tests
make test

# Run unit tests only (fast)
make test-unit

# Run TUI tests
make test-tui

# Run fast tests only
make test-fast
```

### Template Validation

The project includes comprehensive template validation to ensure all Jinja2 templates have proper variable definitions.

```bash
# Validate templates (non-blocking)
make check-templates

# Validate with strict mode (fails on issues)
make check-templates-strict

# Generate suggested fixes
make check-templates-fix
```

### Manual Template Validation

```bash
# Basic validation
python scripts/validate_template_variables.py

# Strict mode for CI
python scripts/validate_template_variables.py --strict --format json

# Generate detailed fixes
python scripts/validate_template_variables.py --generate-fixes --verbose

# Summary format for quick checks
python scripts/validate_template_variables.py --format summary
```

### Code Quality

```bash
# Lint code
make lint

# Format code
make format

# Security scan
make security
```

### Building

```bash
# Build package
make build

# Full PyPI package build
make build-pypi

# Quick build for testing
make build-quick
```

## Template Development

### Template Variable Requirements

All Jinja2 templates must have properly defined variables. The template validation system:

1. **Analyzes templates** for undefined variables
2. **Checks conditional statements** like `{% if var is defined %}`
3. **Validates against requirements** defined in `TemplateContextValidator`
4. **Suggests fixes** for missing variables

### Adding New Templates

When adding new templates:

1. **Define variable requirements** in `src/templating/template_context_validator.py`
2. **Test template rendering** with proper context
3. **Run template validation** before committing:
   ```bash
   make check-templates-strict
   ```

### Template Patterns

```jinja2
{# Good: Use variables with proper defaults #}
{% if enable_feature %}
    // Feature enabled
{% endif %}

{# Avoid: Conditional existence checks #}
{% if enable_feature is defined %}
    // This pattern should be avoided
{% endif %}
```

## CI/CD Pipeline

### GitHub Actions Workflows

1. **Template Validation** (`.github/workflows/template-validation.yml`)
   - Validates all template variables
   - Checks template syntax
   - Generates reports for PRs

2. **Main CI** (`.github/workflows/ci.yml`)
   - Includes template validation step
   - Non-blocking to avoid build failures

### Pre-commit Hooks

The pre-commit configuration includes:
- Code formatting (Black, isort)
- Linting (flake8, mypy)
- Security scanning (bandit)
- **Template validation** (custom hook)

## Contributing

### Pull Request Process

1. **Create feature branch** from `main`
2. **Make changes** following code style
3. **Run validation** with `make check-templates-strict`
4. **Run tests** with `make test`
5. **Commit** with conventional commit format
6. **Push** and create pull request

### Template Changes

For template-related changes:
1. **Update variable requirements** if needed
2. **Test rendering** with real data
3. **Validate templates** pass strict checks
4. **Document** any new variables

### Code Style

- **Python**: Black formatting, PEP 8 compliance
- **Templates**: Consistent indentation, clear variable usage
- **Commits**: Conventional commits format
- **Documentation**: Clear, comprehensive

## Debugging

### Template Issues

```bash
# Analyze specific template
python scripts/validate_template_variables.py --verbose

# Check template syntax only
python -c "
from jinja2 import Environment, FileSystemLoader
env = Environment(loader=FileSystemLoader('src/templates'))
template = env.get_template('your_template.j2')
print('Template syntax is valid')
"
```

### Development Environment

```bash
# Check environment setup
make check-deps

# Install development dependencies
make install-dev

# Clean build artifacts
make clean
```

## Tools and Scripts

### Available Scripts

- `scripts/validate_template_variables.py` - Template validation
- `scripts/check_templates.sh` - Convenient template checking
- `scripts/analyze_imports.py` - Import analysis
- `scripts/generate_api_docs.py` - Documentation generation
- `scripts/iommu_viewer.py` - Lightweight IOMMU group and device viewer (useful for VFIO debugging)

### Make Targets

Run `make help` for all available targets:

```bash
make help
```

Key development targets:
- `check-templates` - Template validation
- `test` - Run test suite
- `lint` - Code linting
- `format` - Code formatting
- `clean` - Clean artifacts

## Tips

1. **Always validate templates** before committing
2. **Use strict mode** in CI to catch issues early
3. **Document new variables** in context validator
4. **Test with real data** when possible
5. **Follow conventional commits** for clear history
