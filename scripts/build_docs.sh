#!/bin/bash
# Build documentation locally with API reference generation

set -e

# Colors for output
RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
NC='\033[0m' # No Color

# Script directory
SCRIPT_DIR="$( cd "$( dirname "${BASH_SOURCE[0]}" )" && pwd )"
PROJECT_ROOT="$(dirname "$SCRIPT_DIR")"

echo -e "${GREEN}PCILeech Firmware Generator - Documentation Builder${NC}"
echo "=================================================="

# Parse command line arguments
SERVE=false
CLEAN=false
PORT=8000

while [[ $# -gt 0 ]]; do
    case $1 in
        --serve|-s)
            SERVE=true
            shift
            ;;
        --clean|-c)
            CLEAN=true
            shift
            ;;
        --port|-p)
            PORT="$2"
            shift 2
            ;;
        --help|-h)
            echo "Usage: $0 [OPTIONS]"
            echo ""
            echo "Options:"
            echo "  -s, --serve     Start local documentation server after building"
            echo "  -c, --clean     Clean existing API documentation before building"
            echo "  -p, --port      Port for documentation server (default: 8000)"
            echo "  -h, --help      Show this help message"
            exit 0
            ;;
        *)
            echo -e "${RED}Unknown option: $1${NC}"
            echo "Use --help for usage information"
            exit 1
            ;;
    esac
done

cd "$PROJECT_ROOT"

# Check if we're in a virtual environment
if [[ -z "$VIRTUAL_ENV" ]]; then
    echo -e "${YELLOW}Warning: Not in a virtual environment${NC}"
    echo "Creating/activating virtual environment..."
    
    if [[ ! -d "venv" ]]; then
        python3 -m venv venv
    fi
    
    source venv/bin/activate
fi

# Install/upgrade documentation dependencies
echo -e "${GREEN}Installing documentation dependencies...${NC}"
pip install --quiet --upgrade pip

# Install main project in editable mode (needed for API docs)
echo -e "${GREEN}Installing project in editable mode...${NC}"
pip install -e . --quiet

# Install documentation tools
echo -e "${GREEN}Installing documentation tools...${NC}"
pip install --quiet \
    mkdocs \
    mkdocs-material \
    mkdocstrings[python] \
    mkdocs-git-revision-date-localized-plugin \
    mkdocs-git-authors-plugin \
    pymdown-extensions \
    sphinx \
    sphinx-autodoc-typehints \
    myst-parser

# Generate API documentation
echo -e "${GREEN}Generating API documentation...${NC}"

CLEAN_FLAG=""
if [ "$CLEAN" = true ]; then
    CLEAN_FLAG="--clean"
    echo "  Cleaning existing API docs..."
fi

python3 scripts/generate_api_docs.py \
    --source src \
    --output site/docs \
    --use-mkdocstrings \
    $CLEAN_FLAG

# Build MkDocs site
echo -e "${GREEN}Building MkDocs site...${NC}"
cd site

if [ "$SERVE" = true ]; then
    echo -e "${GREEN}Starting documentation server on http://localhost:${PORT}${NC}"
    echo "Press Ctrl+C to stop the server"
    mkdocs serve --dev-addr localhost:${PORT}
else
    mkdocs build --strict
    echo -e "${GREEN}✅ Documentation built successfully!${NC}"
    echo ""
    echo "Documentation is available at: ${PROJECT_ROOT}/site/site/"
    echo ""
    echo "To view the documentation locally, run:"
    echo "  $0 --serve"
    echo ""
    echo "Or open the HTML files directly:"
    echo "  open ${PROJECT_ROOT}/site/site/index.html"
fi