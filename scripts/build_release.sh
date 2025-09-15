#!/bin/bash
# Build and release script for PCILeech Firmware Generator
# This script automates the complete build and release process

set -e  # Exit on any error

# Colors for output
RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
BLUE='\033[0;34m'
NC='\033[0m' # No Color

# Project root directory
PROJECT_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$PROJECT_ROOT"

# Functions
log_info() {
    echo -e "${BLUE}[INFO]${NC} $1"
}

log_success() {
    echo -e "${GREEN}[SUCCESS]${NC} $1"
}

log_warning() {
    echo -e "${YELLOW}[WARNING]${NC} $1"
}

log_error() {
    echo -e "${RED}[ERROR]${NC} $1"
}

check_command() {
    if ! command -v "$1" &> /dev/null; then
        log_error "Command '$1' not found. Please install it first."
        exit 1
    fi
}

# Check required tools
check_dependencies() {
    log_info "Checking dependencies..."
    check_command python3
    check_command pip
    check_command git
    
    # Check if we're in a git repository
    if ! git rev-parse --git-dir > /dev/null 2>&1; then
        log_error "Not in a git repository"
        exit 1
    fi
    
    log_success "All dependencies found"
}

# Clean previous builds
clean_build() {
    log_info "Cleaning previous builds..."
    rm -rf build/ dist/ *.egg-info/ .pytest_cache/ .coverage htmlcov/
    find . -type d -name __pycache__ -exec rm -rf {} + 2>/dev/null || true
    find . -type f -name "*.pyc" -delete 2>/dev/null || true
    log_success "Build artifacts cleaned"
}

# Install dependencies
install_dependencies() {
    log_info "Installing dependencies..."
    python3 -m pip install --upgrade pip setuptools wheel
    pip install -r requirements-dev.txt
    log_success "Dependencies installed"
}

# Run code quality checks
run_quality_checks() {
    log_info "Running code quality checks..."
    
    # Format check
    log_info "Checking code formatting with black..."
    black --check src/ tests/ || {
        log_warning "Code formatting issues found. Run 'black src/ tests/' to fix."
        return 1
    }
    
    # Import sorting check
    log_info "Checking import sorting with isort..."
    isort --check-only src/ tests/ || {
        log_warning "Import sorting issues found. Run 'isort src/ tests/' to fix."
        return 1
    }
    
    # Linting
    log_info "Running flake8 linting..."
    flake8 src/ tests/ --count --select=E9,F63,F7,F82 --show-source --statistics
    flake8 src/ tests/ --count --exit-zero --max-complexity=10 --max-line-length=88 --statistics
    
    # Type checking
    log_info "Running mypy type checking..."
    mypy src/ || {
        log_warning "Type checking issues found"
        return 1
    }
    
    # Security check
    log_info "Running security checks with bandit..."
    bandit -r src/ -f json -o bandit-report.json || true
    bandit -r src/
    
    log_success "Code quality checks passed"
}

# Run tests
run_tests() {
    log_info "Running test suite..."
    
    # Unit tests with coverage
    pytest tests/ --cov=src --cov-report=term-missing --cov-report=html --cov-report=xml
    
    # Check coverage threshold (80%)
    coverage report --fail-under=80 || {
        log_warning "Test coverage below 80%"
        return 1
    }
    
    log_success "All tests passed with sufficient coverage"
}

# Build package
build_package() {
    log_info "Building package distributions..."
    
    # Build wheel and source distribution
    python3 -m build
    
    # Check distributions
    twine check dist/*
    
    log_success "Package built successfully"
    
    # Show build artifacts
    log_info "Build artifacts:"
    ls -la dist/
}

# Test installation
test_installation() {
    log_info "Testing package installation..."
    
    # Create temporary virtual environment
    TEMP_VENV=$(mktemp -d)
    python3 -m venv "$TEMP_VENV"
    source "$TEMP_VENV/bin/activate"
    
    # Install the built package
    pip install dist/*.whl
    
    # Test imports
    python3 -c "import src; print(f'Package version: {src.__version__}')"
    python3 -c "from src.tui.main import PCILeechTUI; print('TUI import successful')"
    
    # Test console scripts
    pcileech-generate --help > /dev/null || echo "CLI help test completed"
    
    # Cleanup
    deactivate
    rm -rf "$TEMP_VENV"
    
    log_success "Package installation test passed"
}

# Create release notes
create_release_notes() {
    local version="$1"
    log_info "Creating release notes for version $version..."
    
    # Extract changelog section for this version
    awk "/^## \[$version\]/{flag=1; next} /^## \[/{flag=0} flag" CHANGELOG.md > release_notes.md
    
    if [ ! -s release_notes.md ]; then
        log_warning "No release notes found in CHANGELOG.md for version $version"
        echo "Release $version" > release_notes.md
        echo "" >> release_notes.md
        echo "See CHANGELOG.md for details." >> release_notes.md
    fi
    
    log_success "Release notes created"
}

# Upload to PyPI
upload_to_pypi() {
    local test_mode="$1"
    
    if [ "$test_mode" = "test" ]; then
        log_info "Uploading to Test PyPI..."
        twine upload --repository testpypi dist/*
        log_success "Uploaded to Test PyPI"
        log_info "Install with: pip install --index-url https://test.pypi.org/simple/ pcileechfwgenerator"
    else
        log_info "Uploading to PyPI..."
        twine upload dist/*
        log_success "Uploaded to PyPI"
        log_info "Install with: pip install pcileechfwgenerator"
    fi
}

# Create GitHub release
create_github_release() {
    local version="$1"
    local tag_name="v$version"
    
    log_info "Creating GitHub release $tag_name..."
    
    # Check if gh CLI is available
    if command -v gh &> /dev/null; then
        gh release create "$tag_name" \
            --title "Release $tag_name" \
            --notes-file release_notes.md \
            dist/*.whl dist/*.tar.gz
        log_success "GitHub release created"
    else
        log_warning "GitHub CLI (gh) not found. Please create release manually."
        log_info "Tag: $tag_name"
        log_info "Upload files: $(ls dist/)"
    fi
}

# Main function
main() {
    local command="${1:-build}"
    local version=""
    local test_pypi=false
    local skip_upload=false
    local skip_tests=false
    
    # Parse arguments
    while [[ $# -gt 0 ]]; do
        case $1 in
            build)
                command="build"
                shift
                ;;
            release)
                command="release"
                version="$2"
                shift 2
                ;;
            --test-pypi)
                test_pypi=true
                shift
                ;;
            --skip-upload)
                skip_upload=true
                shift
                ;;
            --skip-tests)
                skip_tests=true
                shift
                ;;
            --help|-h)
                echo "Usage: $0 [build|release <version>] [options]"
                echo ""
                echo "Commands:"
                echo "  build                 Build package (default)"
                echo "  release <version>     Build and release package"
                echo ""
                echo "Options:"
                echo "  --test-pypi          Upload to Test PyPI instead of PyPI"
                echo "  --skip-upload        Skip uploading to PyPI"
                echo "  --skip-tests         Skip running tests"
                echo "  --help, -h           Show this help message"
                exit 0
                ;;
            *)
                log_error "Unknown argument: $1"
                exit 1
                ;;
        esac
    done
    
    log_info "Starting $command process..."
    
    # Common steps
    check_dependencies
    clean_build
    install_dependencies
    
    if [ "$skip_tests" != true ]; then
        run_quality_checks
        run_tests
    fi
    
    build_package
    test_installation
    
    if [ "$command" = "release" ]; then
        if [ -z "$version" ]; then
            log_error "Version required for release command"
            exit 1
        fi
        
        create_release_notes "$version"
        
        if [ "$skip_upload" != true ]; then
            if [ "$test_pypi" = true ]; then
                upload_to_pypi "test"
            else
                upload_to_pypi "production"
            fi
        fi
        
        create_github_release "$version"
        
        log_success "Release $version completed successfully!"
    else
        log_success "Build completed successfully!"
        log_info "To release, run: $0 release <version>"
    fi
    
    # Cleanup
    rm -f release_notes.md bandit-report.json
}

# Run main function with all arguments
main "$@"